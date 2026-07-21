"""Official MCP SDK adapter forwarding typed tools to Core or Relay HTTP."""

from __future__ import annotations

import contextlib
import hashlib
import os
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import suppress
from io import TextIOWrapper
from typing import Any

import anyio
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.stdio import stdio_server
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from allthecontext.credentials import KeyringCredentialStore
from allthecontext.http_client import ContextApiError, ContextHttpClient


def _client() -> ContextHttpClient:
    target = os.environ.get("ATC_TARGET_URL", "http://127.0.0.1:7337")
    client_id = os.environ.get("ATC_CLIENT_ID", "")
    token = os.environ.get("ATC_CLIENT_TOKEN", "")
    if client_id and not token:
        token = KeyringCredentialStore().get(f"client:{client_id}") or ""
    if not client_id or not token:
        raise RuntimeError(
            "ATC_CLIENT_ID is required and its token must be in the OS credential store "
            "or ATC_CLIENT_TOKEN"
        )
    return ContextHttpClient(target, client_id, token)


def _safe(call: Callable[[], Any]) -> dict[str, Any]:
    try:
        result = call()
        if isinstance(result, dict):
            return result
        return {"ok": True, "result": result}
    except ContextApiError as exc:
        return exc.as_dict()


def build_mcp() -> FastMCP:
    """Build the transport-independent tool registry."""

    server = FastMCP(
        "All The Context",
        instructions=(
            "Retrieve approved user context and propose durable changes. "
            "Never represent inaccessible sources as covered and never submit secrets, "
            "hidden reasoning, provider instructions, or guesses as established facts."
        ),
        stateless_http=True,
        json_response=True,
    )

    @server.tool()
    def bootstrap_context(
        task_description: str = "",
        requested_scopes: list[str] | None = None,
        character_budget: int = 8000,
        current_project: str | None = None,
    ) -> dict[str, Any]:
        """Compile approved context for a task within permissions and a character budget."""
        return _safe(
            lambda: _client().bootstrap_context(
                {
                    "query": task_description,
                    "requested_scopes": requested_scopes or [],
                    "budget_chars": character_budget,
                    "current_project": current_project,
                }
            )
        )

    @server.tool()
    def search_context(
        query: str,
        scopes: list[str] | None = None,
        kinds: list[str] | None = None,
        limit: int = 20,
        cursor: int = 0,
    ) -> dict[str, Any]:
        """Search approved, currently valid context using structured filters and FTS."""
        return _safe(
            lambda: _client().search_context(
                {
                    "query": query,
                    "scopes": scopes or [],
                    "kinds": kinds or [],
                    "limit": limit,
                    "offset": cursor,
                }
            )
        )

    @server.tool()
    def get_context_item(record_id: str) -> dict[str, Any]:
        """Get one approved context record and its permitted provenance."""
        return _safe(lambda: _client().get_context_item(record_id))

    @server.tool()
    def context_status() -> dict[str, Any]:
        """Report context mode, Core/Relay availability, and replication freshness."""
        return _safe(lambda: _client().context_status())

    @server.tool()
    def begin_ingestion(
        mode: str,
        accessible_sources: list[str],
        unavailable_sources: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Begin a resumable ingestion session and declare exact source coverage."""
        return _safe(
            lambda: _client().begin_ingestion(
                {
                    "mode": mode,
                    "accessible_sources": accessible_sources,
                    "unavailable_sources": unavailable_sources,
                    "idempotency_key": idempotency_key,
                }
            )
        )

    @server.tool()
    def submit_context_batch(
        session_id: str,
        idempotency_key: str,
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Submit one bounded idempotent candidate batch; retry with the same key safely."""
        return _safe(
            lambda: _client().submit_context_batch(
                {
                    "session_id": session_id,
                    "idempotency_key": idempotency_key,
                    "candidates": candidates,
                }
            )
        )

    @server.tool()
    def finish_ingestion(session_id: str, coverage_report: dict[str, Any]) -> dict[str, Any]:
        """Finish an ingestion session with truthful available/unavailable coverage."""
        return _safe(
            lambda: _client().finish_ingestion(
                {"session_id": session_id, "coverage": coverage_report}
            )
        )

    @server.tool()
    def propose_memory(
        kind: str,
        content: str,
        scope: str,
        confidence: float,
        sensitivity: str = "normal",
        source_reference: str | None = None,
        evidence: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Propose durable user context for policy review; never writes canonical memory."""
        return _safe(
            lambda: _client().propose_memory(
                {
                    "kind": kind,
                    "content": content,
                    "scopes": [scope],
                    "confidence": confidence,
                    "sensitivity": sensitivity,
                    "source_reference": source_reference,
                    "evidence": evidence,
                    "idempotency_key": idempotency_key
                    or hashlib.sha256(f"{kind}\0{scope}\0{content}".encode()).hexdigest(),
                }
            )
        )

    @server.tool()
    def report_context_error(
        record_id: str,
        description: str,
        suggested_correction: str | None = None,
    ) -> dict[str, Any]:
        """Report incorrect or stale context as a reviewable correction signal."""
        return _safe(
            lambda: _client().report_context_error(
                {
                    "record_id": record_id,
                    "content": description,
                    "evidence": suggested_correction,
                }
            )
        )

    # FastMCP v1 generates permissive Pydantic argument models by default.
    # Advertise and enforce closed tool inputs so model typos fail loudly.
    for tool in server._tool_manager.list_tools():
        tool.parameters["additionalProperties"] = False
        tool.fn_metadata.arg_model.model_config["extra"] = "forbid"
        tool.fn_metadata.arg_model.model_rebuild(force=True)

    return server


class BearerGate:
    """Protect a single-client Streamable HTTP adapter at its outer boundary."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            supplied = headers.get(b"authorization", b"")
            import hmac

            if not hmac.compare_digest(supplied, b"Bearer " + self.token):
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


async def _run_stdio(server: FastMCP) -> None:
    """Run STDIO without allowing temporary UTF-8 wrappers to close process handles."""
    if sys.stdin is None or sys.stdout is None:
        raise RuntimeError("STDIO MCP requires process standard input and output streams")
    stdin_wrapper = TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    stdout_wrapper = TextIOWrapper(sys.stdout.buffer, encoding="utf-8", write_through=True)
    try:
        stdin = anyio.wrap_file(stdin_wrapper)
        stdout = anyio.wrap_file(stdout_wrapper)
        async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
            await server._mcp_server.run(
                read_stream,
                write_stream,
                server._mcp_server.create_initialization_options(),
            )
    finally:
        with suppress(OSError, ValueError):
            stdout_wrapper.flush()
        with suppress(OSError, ValueError):
            stdin_wrapper.detach()
        with suppress(OSError, ValueError):
            stdout_wrapper.detach()


def main() -> None:
    """Run the lightweight local STDIO forwarding adapter."""
    anyio.run(_run_stdio, build_mcp())


def http_main() -> None:
    """Run a bearer-protected Streamable HTTP forwarding adapter."""
    server = build_mcp()
    access_token = os.environ.get("ATC_MCP_ACCESS_TOKEN", "")
    if not access_token:
        raise RuntimeError("ATC_MCP_ACCESS_TOKEN is required for HTTP MCP")
    host = os.environ.get("ATC_MCP_HOST", "127.0.0.1")
    port = int(os.environ.get("ATC_MCP_PORT", "7339"))

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        async with server.session_manager.run():
            yield

    app = Starlette(
        routes=[Mount("/", app=BearerGate(server.streamable_http_app(), access_token))],
        lifespan=lifespan,
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
