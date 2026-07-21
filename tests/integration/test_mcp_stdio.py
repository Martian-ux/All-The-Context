from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import anyio
import uvicorn
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
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
            "propose_memory",
        }
        result = await session.call_tool("context_status", {})
        assert result.isError is not True
        assert result.structuredContent is not None
        assert result.structuredContent["core_online"] is True


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
