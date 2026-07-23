from __future__ import annotations

import json
import os
import socket
import sys
import threading
from contextlib import suppress
from pathlib import Path

import anyio
import httpx
import uvicorn
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.desktop_setup import CoreProbe, probe_core
from allthecontext.instance_identity import ensure_instance_secret
from allthecontext.models import ClientCreate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _exercise_adapter(parameters: StdioServerParameters) -> None:
    async with (
        stdio_client(parameters) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        listed = await session.list_tools()
        assert {tool.name for tool in listed.tools} >= {
            "context_status",
            "bootstrap_context",
            "forget_context",
            "propose_memory",
        }
        propose_tool = next(tool for tool in listed.tools if tool.name == "propose_memory")
        assert {
            "explicit_user_statement",
            "entity_key",
            "attribute_key",
            "supersedes",
            "observed_at",
        } <= propose_tool.inputSchema["properties"].keys()
        result = await session.call_tool("context_status", {})
        assert result.isError is not True
        assert result.structuredContent is not None
        assert result.structuredContent["core_online"] is True
        proposed = await session.call_tool(
            "propose_memory",
            {
                "kind": "interaction_preference",
                "content": "Use direct answers in integration tests.",
                "scope": "general",
                "confidence": 1.0,
                "entity_key": "user",
                "attribute_key": "answer_style",
                "observed_at": "2026-07-23T16:00:00+00:00",
            },
        )
        assert proposed.isError is not True
        assert proposed.structuredContent is not None
        assert proposed.structuredContent["disposition"] == "applied"
        assert proposed.structuredContent["record_id"]
        forgotten = await session.call_tool(
            "forget_context",
            {
                "record_id": proposed.structuredContent["record_id"],
                "reason": "The integration-test user explicitly requested deletion.",
            },
        )
        assert forgotten.isError is not True
        assert forgotten.structuredContent is not None
        assert forgotten.structuredContent["disposition"] == "applied"
        assert (
            forgotten.structuredContent["record_id"]
            == proposed.structuredContent["record_id"]
        )
        assert forgotten.structuredContent["deleted_at"]


def _request_shutdown(base_url: str, admin_token: str) -> None:
    response = httpx.post(
        f"{base_url}/v1/admin/shutdown",
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=3.0,
    )
    response.raise_for_status()


async def _exercise_crash_recovery(
    parameters: StdioServerParameters,
    config: CoreConfig,
    admin_token: str,
) -> None:
    base_url = f"http://127.0.0.1:{config.port}"
    async with (
        stdio_client(parameters) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        first = await session.call_tool("context_status", {})
        assert first.isError is not True
        assert first.structuredContent is not None
        assert first.structuredContent["core_online"] is True

        await anyio.to_thread.run_sync(_request_shutdown, base_url, admin_token)
        for _ in range(100):
            if probe_core(config) is CoreProbe.UNREACHABLE:
                break
            await anyio.sleep(0.05)
        assert probe_core(config) is CoreProbe.UNREACHABLE

        second = await session.call_tool("context_status", {})
        assert second.isError is not True
        assert second.structuredContent is not None
        assert second.structuredContent["core_online"] is True


def test_real_stdio_mcp_handshake_and_tool_call(tmp_path: Path) -> None:
    port = _port()
    config = CoreConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "core.sqlite3",
        lock_path=tmp_path / "core.lock",
        port=port,
    )
    service = CoreService(config)
    principal, token = service.store.create_client(
        ClientCreate(
            name="MCP integration test",
            scopes=["context:read", "context:status", "context:propose", "context:ingest"],
        )
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(config, service=service),
            host="127.0.0.1",
            port=port,
            log_level="error",
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        thread.join(0.05)
    assert server.started

    environment = dict(os.environ)
    environment.update(
        {
            "ATC_TARGET_URL": f"http://127.0.0.1:{port}",
            "ATC_CLIENT_ID": principal.id,
            "ATC_CLIENT_TOKEN": token,
            "ATC_AUTO_START_CORE": "0",
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "allthecontext.mcp_adapter"],
        env=environment,
        cwd=str(Path(__file__).resolve().parents[2]),
    )
    try:
        anyio.run(_exercise_adapter, parameters)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_stdio_adapter_restarts_a_crashed_managed_core(tmp_path: Path) -> None:
    port = _port()
    config = CoreConfig(
        data_dir=tmp_path,
        database_path=tmp_path / "core.sqlite3",
        lock_path=tmp_path / "core.lock",
        port=port,
    )
    service = CoreService(config)
    principal, token = service.store.create_client(
        ClientCreate(
            name="Self-healing MCP integration test",
            scopes=["context:read", "context:status", "context:propose", "context:ingest"],
        )
    )
    _admin, admin_token = service.store.create_client(
        ClientCreate(name="MCP restart test administrator", scopes=["admin"])
    )
    ensure_instance_secret(config)

    base_url = f"http://127.0.0.1:{port}"
    environment = dict(os.environ)
    environment.update(
        {
            "ATC_CORE_DATA_DIR": str(config.data_dir),
            "ATC_CORE_HOST": "127.0.0.1",
            "ATC_CORE_PORT": str(port),
            "ATC_TARGET_URL": base_url,
            "ATC_CLIENT_ID": principal.id,
            "ATC_CLIENT_TOKEN": token,
            "ATC_AUTO_START_CORE": "1",
            "ATC_CORE_COMMAND": json.dumps(
                [sys.executable, "-m", "allthecontext.desktop", "--core"]
            ),
        }
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "allthecontext.mcp_adapter"],
        env=environment,
        cwd=str(Path(__file__).resolve().parents[2]),
    )

    try:
        anyio.run(_exercise_crash_recovery, parameters, config, admin_token)
    finally:
        with suppress(httpx.HTTPError):
            _request_shutdown(base_url, admin_token)
        for _ in range(100):
            if probe_core(config) is CoreProbe.UNREACHABLE:
                break
            threading.Event().wait(0.05)

    assert probe_core(config) is CoreProbe.UNREACHABLE
