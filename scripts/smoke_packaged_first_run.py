"""Exercise frozen first-run setup, installed MCP, retrieval, and graceful shutdown."""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
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

    subprocess.run(
        [
            str(executable),
            "--headless-setup",
            str(report_path),
            "--no-startup",
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
    if not command.is_file():
        raise SystemExit(f"configured MCP command is not stable: {command}")
    token = client_environment.get("ATC_CLIENT_TOKEN", "")
    if not token:
        raise SystemExit("isolated fallback setup did not configure an MCP credential")

    stopped = False
    try:
        status = api_request(f"http://127.0.0.1:{port}/v1/context/status", token)
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
        with suppress(OSError, urllib.error.URLError):
            api_request(f"http://127.0.0.1:{port}/v1/admin/shutdown", token, method="POST")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2):
                    pass
            except (OSError, urllib.error.URLError):
                stopped = True
                break
            time.sleep(0.1)
    if not stopped:
        raise SystemExit("installed Core did not shut down within ten seconds")

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
                "stable_mcp_command": True,
                "mcp_handshake": "passed",
                "core_shutdown": "passed",
                "temporary_data_removed": True,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
