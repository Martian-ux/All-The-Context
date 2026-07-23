"""Exercise frozen first-run setup, installed MCP, retrieval, and graceful shutdown."""

from __future__ import annotations

import atexit
import json
import os
import platform
import plistlib
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
from allthecontext import __version__
from allthecontext.release_manifest import sha256_file
from allthecontext.windows_update_helper import HelperPhase, UpdateJournal
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


def wait_for_core(base_url: str, token: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if api_request(f"{base_url}/v1/context/status", token).get("core_online") is True:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError("transactional updater did not restart Core within twenty seconds")


def prepare_packaged_update_transaction(
    *,
    data_dir: Path,
    installed_app: Path,
    release_app: Path,
    operation_id: str,
    core_port: int,
    target_version: str,
) -> tuple[Path, Path]:
    updates = data_dir / "updates"
    transaction_dir = updates / "transactions" / operation_id
    rollback_dir = transaction_dir / "rollback"
    replacement_dir = transaction_dir / "replacement"
    rollback_dir.mkdir(parents=True)
    replacement_dir.mkdir()
    transaction_helper = transaction_dir / "AllTheContextUpdater.exe"
    stable_helper = installed_app.with_name("AllTheContextUpdater.exe")
    stable_mcp = installed_app.with_name("AllTheContextMCP.exe")
    if not stable_helper.is_file() or not stable_mcp.is_file():
        raise RuntimeError("installed update or MCP helper is missing")
    shutil.copy2(stable_helper, transaction_helper)
    replacement = replacement_dir / "AllTheContextSetup.exe"
    rollback_app = rollback_dir / "AllTheContext.exe"
    rollback_mcp = rollback_dir / "AllTheContextMCP.exe"
    rollback_update_helper = rollback_dir / "AllTheContextUpdater.exe"
    shutil.copy2(release_app, replacement)
    shutil.copy2(installed_app, rollback_app)
    shutil.copy2(stable_mcp, rollback_mcp)
    shutil.copy2(stable_helper, rollback_update_helper)

    database = data_dir / "core.sqlite3"
    backup = updates / "backups" / f"packaged-smoke-{operation_id}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(database)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("packaged update backup was not valid")
    finally:
        destination.close()
        source.close()

    journal_path = transaction_dir / "journal.json"
    state_path = updates / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "restart_required",
            "current_version": __version__,
            "offered_version": target_version,
            "downloaded_path": None,
            "backup_path": str(backup),
            "last_error": None,
            "operation_id": operation_id,
            "transaction_path": str(journal_path),
            "recovery_attempts": int(state.get("recovery_attempts", 0)) + 1,
        }
    )
    state_temporary = state_path.with_name(f"{state_path.name}.{operation_id}.atc-new")
    state_temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    state_temporary.replace(state_path)

    replacement_digest, replacement_size = sha256_file(replacement)
    rollback_digest, rollback_size = sha256_file(rollback_app)
    rollback_mcp_digest, rollback_mcp_size = sha256_file(rollback_mcp)
    rollback_update_digest, rollback_update_size = sha256_file(rollback_update_helper)
    backup_digest, backup_size = sha256_file(backup)
    now = "2026-07-22T12:00:00+00:00"
    journal = UpdateJournal(
        operation_id=operation_id,
        phase=HelperPhase.PREPARED,
        current_version=__version__,
        target_version=target_version,
        parent_pid=0,
        application_path=str(installed_app),
        replacement_path=str(replacement),
        replacement_sha256=replacement_digest,
        replacement_size=replacement_size,
        rollback_application_path=str(rollback_app),
        rollback_application_sha256=rollback_digest,
        rollback_application_size=rollback_size,
        mcp_path=str(stable_mcp),
        rollback_mcp_path=str(rollback_mcp),
        rollback_mcp_sha256=rollback_mcp_digest,
        rollback_mcp_size=rollback_mcp_size,
        stable_update_helper_path=str(stable_helper),
        rollback_update_helper_path=str(rollback_update_helper),
        rollback_update_helper_sha256=rollback_update_digest,
        rollback_update_helper_size=rollback_update_size,
        database_path=str(database),
        database_backup_path=str(backup),
        database_backup_sha256=backup_digest,
        database_backup_size=backup_size,
        state_path=str(state_path),
        helper_path=str(transaction_helper),
        core_host="127.0.0.1",
        core_port=core_port,
        created_at=now,
        updated_at=now,
    )
    journal.save(journal_path)
    return transaction_helper, journal_path


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
            "ATC_PACKAGED_SMOKE": "1",
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
                "ATC_SMOKE_PROGRAMS_DIR": str(work / "shell" / "Programs"),
                "ATC_SMOKE_DESKTOP_DIR": str(work / "shell" / "Desktop"),
                "ATC_SMOKE_UNINSTALL_KEY": (
                    f"Software\\AllTheContext\\Smoke\\packaged-{os.getpid()}"
                ),
                "ATC_SMOKE_UPDATE_RUNONCE_KEY": (
                    f"Software\\AllTheContext\\Smoke\\packaged-update-{os.getpid()}"
                ),
                "ATC_SMOKE_STARTUP_WINDOWS_KEY": (
                    f"Software\\AllTheContext\\Smoke\\packaged-startup-{os.getpid()}"
                ),
            }
        )
    elif system == "Darwin":
        environment["ATC_INSTALL_DIR"] = str(work / "Applications" / "All The Context.app")
        environment["ATC_SMOKE_LAUNCH_AGENTS_DIR"] = str(work / "LaunchAgents")
    else:
        environment["XDG_CONFIG_HOME"] = str(work / "config")

    base_url = f"http://127.0.0.1:{port}"
    cleanup_admin_token = ""

    def cleanup_failed_smoke() -> None:
        if cleanup_admin_token:
            with suppress(Exception):
                stop_core(base_url, cleanup_admin_token)
        if system == "Windows":
            import winreg

            for key_name in (
                environment["ATC_SMOKE_UNINSTALL_KEY"],
                environment["ATC_SMOKE_UPDATE_RUNONCE_KEY"],
                environment["ATC_SMOKE_STARTUP_WINDOWS_KEY"],
            ):
                with suppress(OSError):
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)
        deadline = time.monotonic() + 10
        while work.exists() and time.monotonic() < deadline:
            try:
                shutil.rmtree(work)
            except OSError:
                time.sleep(0.1)
            else:
                return

    atexit.register(cleanup_failed_smoke)

    subprocess.run(
        [
            str(executable),
            "--headless-setup",
            str(report_path),
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
    startup_report = report.get("startup")
    expected_startup = {
        "Windows": "HKCU Run",
        "Darwin": "LaunchAgent",
    }.get(system, "XDG autostart")
    if not isinstance(startup_report, dict) or startup_report.get("mechanism") != expected_startup:
        raise SystemExit(f"packaged startup adapter was not installed: {startup_report}")

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
    if system == "Darwin":
        installed_bundles = [
            candidate
            for candidate in installed_app.parents
            if candidate.suffix.casefold() == ".app"
        ]
        if len(installed_bundles) != 1:
            raise SystemExit("installed macOS executable is not inside one stable app bundle")
        seal = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(installed_bundles[0])],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if seal.returncode != 0 and "not signed at all" not in seal.stderr.casefold():
            raise SystemExit("installed macOS app bundle has an invalid structural code seal")
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
    cleanup_admin_token = admin_token

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
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            environment["ATC_SMOKE_STARTUP_WINDOWS_KEY"],
        ) as startup_key:
            startup_command, _kind = winreg.QueryValueEx(startup_key, "All The Context Core")
        if str(installed_app) not in startup_command or "--core" not in startup_command:
            raise SystemExit("isolated Windows startup command did not use the installed app")
    elif system == "Darwin":
        launch_agent = work / "LaunchAgents" / "com.allthecontext.core.plist"
        with launch_agent.open("rb") as stream:
            launch_payload = plistlib.load(stream)
        if launch_payload.get("ProgramArguments") != [str(installed_app), "--core"]:
            raise SystemExit("isolated LaunchAgent did not use the installed app bundle")
    else:
        startup_entry = work / "config" / "autostart" / "all-the-context.desktop"
        startup_content = startup_entry.read_text(encoding="utf-8")
        if str(installed_app) not in startup_content or "--core" not in startup_content:
            raise SystemExit("isolated XDG startup entry did not use the portable app")

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
        updates = api_request(f"{base_url}/v1/admin/updates", admin_token)
        expected_automatic = system == "Windows"
        if updates.get("automatic_install_supported") is not expected_automatic:
            raise SystemExit(f"packaged updater capability was incorrect: {updates}")
        if (
            system != "Windows"
            and "manual" not in str(updates.get("installer_detail", "")).casefold()
        ):
            raise SystemExit(f"packaged updater did not explain its manual boundary: {updates}")

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

    packaged_update_result = "not_applicable"
    if system == "Windows":
        crash_helper, crash_journal = prepare_packaged_update_transaction(
            data_dir=data_dir,
            installed_app=installed_app,
            release_app=executable,
            operation_id="d" * 24,
            core_port=port,
            target_version=__version__,
        )
        interrupted_environment = dict(environment)
        interrupted_environment["ATC_UPDATE_FAULT_AFTER_PHASE"] = "binary_replaced"
        interrupted = subprocess.run(
            [str(crash_helper), "--journal", str(crash_journal)],
            env=interrupted_environment,
            check=False,
            timeout=180,
        )
        if interrupted.returncode != 86:
            raise SystemExit(
                f"packaged updater did not stop at the injected crash point: "
                f"{interrupted.returncode}"
            )
        if json.loads(crash_journal.read_text(encoding="utf-8")).get("phase") != (
            "binary_replaced"
        ):
            raise SystemExit("packaged updater did not persist the interrupted cutover")
        subprocess.run(
            [str(crash_helper), "--journal", str(crash_journal)],
            env=environment,
            check=True,
            timeout=180,
        )
        wait_for_core(base_url, token)
        if json.loads(crash_journal.read_text(encoding="utf-8")).get("phase") != "committed":
            raise SystemExit("packaged updater did not commit after crash recovery")
        stop_core(base_url, admin_token)

        rollback_helper, rollback_journal = prepare_packaged_update_transaction(
            data_dir=data_dir,
            installed_app=installed_app,
            release_app=executable,
            operation_id="e" * 24,
            core_port=port,
            target_version=__version__,
        )
        rollback_environment = dict(environment)
        rollback_environment.update(
            {
                "ATC_UPDATE_FORCE_HEALTH_FAILURE": "1",
                "ATC_UPDATE_SMOKE_MUTATE_DB": "1",
            }
        )
        rolled_back = subprocess.run(
            [str(rollback_helper), "--journal", str(rollback_journal)],
            env=rollback_environment,
            check=False,
            timeout=180,
        )
        if rolled_back.returncode != 2:
            raise SystemExit(
                f"packaged updater did not report the exercised rollback: {rolled_back.returncode}"
            )
        wait_for_core(base_url, token)
        rollback_status = json.loads(rollback_journal.read_text(encoding="utf-8"))
        if rollback_status.get("phase") != "rolled_back":
            raise SystemExit(f"packaged updater did not roll back: {rollback_status}")
        restored_files = (
            (
                Path(str(rollback_status["application_path"])),
                str(rollback_status["rollback_application_sha256"]),
                int(rollback_status["rollback_application_size"]),
            ),
            (
                Path(str(rollback_status["mcp_path"])),
                str(rollback_status["rollback_mcp_sha256"]),
                int(rollback_status["rollback_mcp_size"]),
            ),
            (
                Path(str(rollback_status["stable_update_helper_path"])),
                str(rollback_status["rollback_update_helper_sha256"]),
                int(rollback_status["rollback_update_helper_size"]),
            ),
        )
        if any(sha256_file(path) != (digest, size) for path, digest, size in restored_files):
            raise SystemExit("packaged updater did not restore every installed binary")
        connection = sqlite3.connect(data_dir / "core.sqlite3")
        try:
            leaked_migration = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='packaged_update_smoke'"
            ).fetchone()
            if leaked_migration is not None:
                raise SystemExit("packaged updater did not restore the pre-update database")
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise SystemExit("packaged updater rollback database is not valid")
        finally:
            connection.close()
        stop_core(base_url, admin_token)
        packaged_update_result = "passed"

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
        with suppress(FileNotFoundError):
            winreg.DeleteKey(
                winreg.HKEY_CURRENT_USER,
                environment["ATC_SMOKE_UPDATE_RUNONCE_KEY"],
            )

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
    atexit.unregister(cleanup_failed_smoke)
    print(
        json.dumps(
            {
                "setup": "passed",
                "browser_handoff": "passed",
                "stable_mcp_command": True,
                "mcp_handshake": "passed",
                "mcp_core_restart": "passed",
                "installed_reopen": "passed",
                "per_user_startup": "passed",
                "ota_automatic_install": system == "Windows",
                "ota_transaction_recovery": packaged_update_result,
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
