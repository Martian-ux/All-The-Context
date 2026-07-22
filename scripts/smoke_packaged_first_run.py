"""Exercise frozen first-run setup, installed MCP, retrieval, and graceful shutdown."""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from contextlib import suppress
from pathlib import Path
from typing import Any, TextIO

import anyio
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from smoke_desktop_artifact import ROOT, artifact_executable


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def api_request(url: str, token: str, *, method: str = "GET") -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected API response from {url}")
    return value


def stop_core(base_url: str, admin_token: str) -> None:
    with suppress(OSError, urllib.error.URLError):
        api_request(f"{base_url}/v1/admin/shutdown", admin_token, method="POST")
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/health", timeout=0.2):
                pass
        except (OSError, urllib.error.URLError):
            return
        time.sleep(0.1)
    raise RuntimeError("installed Core did not shut down within ten seconds")


async def exercise_mcp(parameters: StdioServerParameters, errlog: TextIO) -> None:
    async with (
        stdio_client(parameters, errlog=errlog) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        required = {"context_status", "bootstrap_context", "propose_memory"}
        if not names.issuperset(required):
            raise RuntimeError(f"packaged MCP tools are missing: {sorted(required - names)}")
        status = await session.call_tool("context_status", {})
        if status.isError is True or not status.structuredContent:
            raise RuntimeError(f"packaged MCP status failed: {status}")
        if status.structuredContent.get("core_online") is not True:
            raise RuntimeError(f"packaged MCP did not reach Core: {status.structuredContent}")


def main() -> int:
    system = os.environ.get("ATC_SMOKE_PLATFORM") or platform.system()
    executable = artifact_executable(system)
    if not executable.is_file():
        raise SystemExit(f"desktop artifact is missing: {executable}")

    temp_parent = ROOT / "tmp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="packaged-first-run-", dir=temp_parent))
    data_dir = work / "data"
    codex_home = work / "codex"
    report_path = work / "setup-report.json"
    port = available_port()
    environment = dict(os.environ)
    environment.update(
        {
            "ATC_CORE_DATA_DIR": str(data_dir),
            "ATC_CORE_PORT": str(port),
            "ATC_CORE_HOST": "127.0.0.1",
            "CODEX_HOME": str(codex_home),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
        }
    )
    if system == "Windows":
        environment["ATC_INSTALL_DIR"] = str(work / "installed")
        environment.update(
            {
                "ATC_PACKAGED_SMOKE": "1",
                "ATC_SMOKE_PROGRAMS_DIR": str(work / "shell" / "Programs"),
                "ATC_SMOKE_DESKTOP_DIR": str(work / "shell" / "Desktop"),
                "ATC_SMOKE_UNINSTALL_KEY": (
                    f"Software\\AllTheContext\\Smoke\\packaged-{os.getpid()}"
                ),
            }
        )

    subprocess.run(
        [
            str(executable),
            "--headless-setup",
            str(report_path),
            "--no-startup",
            "--no-claude",
            "--vault-name",
            "Packaged smoke vault",
        ],
        env=environment,
        check=True,
        timeout=90,
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("core_url") != f"http://127.0.0.1:{port}":
        raise SystemExit(f"unexpected setup report: {report}")

    config_path = codex_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    managed = config["mcp_servers"]["all_the_context"]
    command = Path(managed["command"])
    arguments = list(managed["args"])
    command_override = os.environ.get("ATC_SMOKE_MCP_COMMAND")
    if command_override:
        command = Path(command_override).expanduser().resolve()
        arguments = []
    client_environment = {str(key): str(value) for key, value in managed["env"].items()}
    core_command = json.loads(client_environment.get("ATC_CORE_COMMAND", "null"))
    if not isinstance(core_command, list) or len(core_command) < 2 or core_command[-1] != "--core":
        raise SystemExit(f"configured Core recovery command is invalid: {core_command}")
    installed_app = Path(str(core_command[0]))
    if not installed_app.is_file():
        raise SystemExit(f"installed desktop app is not stable: {installed_app}")
    if not command.is_file():
        raise SystemExit(f"configured MCP command is not stable: {command}")
    token = client_environment.get("ATC_CLIENT_TOKEN", "")
    if not token:
        raise SystemExit("isolated fallback setup did not configure an MCP credential")
    desktop_client_id = str(report.get("client_id", ""))
    credential_path = data_dir / "credentials.development.json"
    credential_map = json.loads(credential_path.read_text(encoding="utf-8"))
    admin_token = str(credential_map.get(f"client:{desktop_client_id}", ""))
    if not admin_token:
        raise SystemExit("isolated setup did not retain its desktop administrator credential")

    if system == "Windows":
        expected_shortcuts = (
            work / "shell" / "Programs" / "All The Context" / "All The Context.lnk",
            work / "shell" / "Programs" / "All The Context" / "Uninstall All The Context.lnk",
            work / "shell" / "Desktop" / "All The Context.lnk",
        )
        if not all(path.is_file() for path in expected_shortcuts):
            raise SystemExit("isolated Windows launchers were not registered")
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            environment["ATC_SMOKE_UNINSTALL_KEY"],
        ):
            pass

    base_url = f"http://127.0.0.1:{port}"
    try:
        dashboard_url = str(report.get("dashboard_url", ""))
        if "atc_token" in dashboard_url or "/v1/browser/connect?ticket=" not in dashboard_url:
            raise SystemExit(f"unsafe or invalid dashboard handoff URL: {dashboard_url}")
        with urllib.request.urlopen(dashboard_url, timeout=3) as response:
            if response.status != 200:
                raise SystemExit(f"browser handoff did not reach dashboard: {response.status}")
            handoff_html = response.read().decode("utf-8")
        session_match = re.search(
            r'sessionStorage\.setItem\("atc\.browserSession","([^"]+)"\)',
            handoff_html,
        )
        if session_match is None or admin_token in handoff_html:
            raise SystemExit("browser handoff exposed no safe opaque session")
        browser_request = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/context/status",
            headers={
                "Authorization": f"Browser {session_match.group(1)}",
                "X-ATC-Dashboard": "1",
            },
        )
        with urllib.request.urlopen(browser_request, timeout=3) as response:
            if json.loads(response.read().decode("utf-8")).get("core_online") is not True:
                raise SystemExit("browser session did not authenticate to Core")

        status = api_request(f"{base_url}/v1/context/status", token)
        if status.get("core_online") is not True:
            raise SystemExit(f"installed Core status was not ready: {status}")

        mcp_environment = dict(environment)
        mcp_environment.update(client_environment)
        parameters = StdioServerParameters(
            command=str(command),
            args=arguments,
            env=mcp_environment,
            cwd=str(command.parent),
        )
        mcp_log_path = work / "mcp-stderr.log"
        with mcp_log_path.open("w", encoding="utf-8") as mcp_log:
            anyio.run(exercise_mcp, parameters, mcp_log)
        mcp_stderr = mcp_log_path.read_text(encoding="utf-8", errors="replace")
        if "Traceback" in mcp_stderr:
            raise RuntimeError(f"packaged MCP wrote a traceback to stderr:\n{mcp_stderr}")
    finally:
        stop_core(base_url, admin_token)

    # The already-configured packaged adapter must recover Core without the
    # user opening the desktop app again.
    restart_log_path = work / "mcp-restart-stderr.log"
    with restart_log_path.open("w", encoding="utf-8") as restart_log:
        anyio.run(exercise_mcp, parameters, restart_log)
    if "Traceback" in restart_log_path.read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError("packaged MCP Core restart wrote a traceback")
    stop_core(base_url, admin_token)

    # Reopen the stable installed copy and run the idempotent setup/upgrade
    # path; the same vault and desktop authority must survive.
    reopen_report_path = work / "reopen-report.json"
    subprocess.run(
        [
            str(installed_app),
            "--headless-setup",
            str(reopen_report_path),
            "--no-startup",
            "--no-claude",
            "--vault-name",
            "Packaged smoke vault",
        ],
        env=environment,
        check=True,
        timeout=90,
    )
    reopen_report = json.loads(reopen_report_path.read_text(encoding="utf-8"))
    if reopen_report.get("vault_id") != report.get("vault_id") or reopen_report.get(
        "client_id"
    ) != report.get("client_id"):
        raise SystemExit(f"installed reopen changed vault authority: {reopen_report}")
    if api_request(f"{base_url}/v1/context/status", token).get("core_online") is not True:
        raise SystemExit("reopened installed Core was not ready")
    stop_core(base_url, admin_token)

    uninstall_result = "not_applicable"
    if system == "Windows":
        uninstall_report_path = work / "uninstall-report.json"
        subprocess.run(
            [
                str(installed_app),
                "--packaged-smoke-uninstall",
                str(uninstall_report_path),
            ],
            # Match the WorkingDirectory used by the real Start Menu
            # uninstall shortcut. The detached cleanup helper must move out
            # of this directory before trying to remove it.
            cwd=installed_app.parent,
            env=environment,
            check=True,
            timeout=90,
        )
        uninstall_report = json.loads(uninstall_report_path.read_text(encoding="utf-8"))
        if uninstall_report != {"uninstalled": True, "vault_preserved": True}:
            raise SystemExit(f"unexpected packaged uninstall report: {uninstall_report}")

        install_dir = Path(environment["ATC_INSTALL_DIR"])
        delete_deadline = time.monotonic() + 15
        while install_dir.exists() and time.monotonic() < delete_deadline:
            time.sleep(0.1)
        if install_dir.exists():
            raise SystemExit(f"packaged uninstaller left application files: {install_dir}")
        if not (data_dir / "core.sqlite3").is_file():
            raise SystemExit("packaged uninstaller removed the retained local vault")
        if any(path.exists() for path in expected_shortcuts):
            raise SystemExit("packaged uninstaller left isolated Windows shortcuts")

        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                environment["ATC_SMOKE_UNINSTALL_KEY"],
            ):
                pass
        except FileNotFoundError:
            pass
        else:
            raise SystemExit("packaged uninstaller left its Apps & Features registration")

        cleaned_config = config_path.read_text(encoding="utf-8")
        if "all_the_context" in cleaned_config or token in cleaned_config:
            raise SystemExit("packaged uninstaller left the Codex MCP connection")
        cleaned_credentials = json.loads(credential_path.read_text(encoding="utf-8"))
        client_id = client_environment["ATC_CLIENT_ID"]
        if f"client:{client_id}" in cleaned_credentials:
            raise SystemExit("packaged uninstaller left the Codex credential")
        connection = sqlite3.connect(data_dir / "core.sqlite3")
        try:
            revoked = connection.execute(
                "SELECT revoked_at FROM client_registrations WHERE id=?",
                (client_id,),
            ).fetchone()
        finally:
            # sqlite3.Connection's context manager commits or rolls back but
            # does not close.  An open connection prevents deletion on
            # Windows and would make the smoke mistake its own handle for an
            # application shutdown leak.
            connection.close()
        if revoked is None or revoked[0] is None:
            raise SystemExit("packaged uninstaller did not revoke the Codex principal")
        uninstall_result = "passed"

    cleanup_deadline = time.monotonic() + 10
    while True:
        try:
            shutil.rmtree(work)
            break
        except PermissionError:
            if time.monotonic() >= cleanup_deadline:
                raise SystemExit(f"temporary smoke data remained locked: {work}") from None
            time.sleep(0.1)
    print(
        json.dumps(
            {
                "setup": "passed",
                "browser_handoff": "passed",
                "stable_mcp_command": True,
                "mcp_handshake": "passed",
                "mcp_core_restart": "passed",
                "installed_reopen": "passed",
                "core_shutdown": "passed",
                "packaged_uninstall": uninstall_result,
                "temporary_data_removed": True,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
