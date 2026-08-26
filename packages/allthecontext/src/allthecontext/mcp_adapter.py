"""Official MCP SDK adapter forwarding typed tools to Core or Relay HTTP."""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
import uvicorn
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.tools import Tool
from mcp.types import ToolAnnotations
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from allthecontext.config import CoreConfig
from allthecontext.credentials import KeyringCredentialStore
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.desktop_setup import CoreProbe, launch_core, probe_core
from allthecontext.http_client import ContextApiError, ContextHttpClient

MANAGED_CORE_STARTUP_SECONDS = 30.0


def _configured_core_runtime() -> RuntimeCommand:
    serialized = os.environ.get("ATC_CORE_COMMAND")
    if not serialized:
        return RuntimeCommand.current()
    try:
        command = json.loads(serialized)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ATC_CORE_COMMAND must be a JSON command array") from exc
    if (
        not isinstance(command, list)
        or len(command) < 2
        or any(not isinstance(item, str) or not item for item in command)
        or command[-1] != "--core"
    ):
        raise RuntimeError("ATC_CORE_COMMAND must end with the --core application mode")
    return RuntimeCommand(Path(command[0]), tuple(command[1:-1]))


def _ensure_local_core(
    target: str,
    *,
    wait_seconds: float = MANAGED_CORE_STARTUP_SECONDS,
) -> None:
    """Restart the user's verified local Core for managed MCP connections."""

    if os.environ.get("ATC_AUTO_START_CORE") != "1":
        return
    parsed = urlsplit(target)
    try:
        target_port = parsed.port
    except ValueError as exc:
        raise RuntimeError("Managed local Core URL contains an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or target_port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError(
            "Automatic Core restart is restricted to a plain 127.0.0.1 HTTP endpoint"
        )

    config = replace(CoreConfig.default(), host="127.0.0.1", port=target_port)
    state = probe_core(config)
    if state is CoreProbe.VERIFIED:
        return
    if state is CoreProbe.UNVERIFIED:
        raise RuntimeError(
            f"Port {target_port} is occupied by a service that is not this All The Context Core"
        )
    launch_core(
        _configured_core_runtime(),
        config,
        wait_seconds=wait_seconds,
    )


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
    _ensure_local_core(target)
    return ContextHttpClient(target, client_id, token)


def _safe(call: Callable[[], Any]) -> dict[str, Any]:
    try:
        result = call()
        if isinstance(result, dict):
            return result
        return {"ok": True, "result": result}
    except ContextApiError as exc:
        return exc.as_dict()


def _automatic_proposal_key(_payload: dict[str, Any] | None = None) -> str:
    """Create an opaque operation ID with no relationship to proposal content."""

    return str(uuid.uuid4())


def _strict_tool(fn: Callable[..., Any], **kwargs: Any) -> Tool:
    """Build a v2 tool while retaining the adapter's closed-input contract."""

    tool = Tool.from_function(fn, **kwargs)
    tool.parameters["additionalProperties"] = False
    tool.fn_metadata.arg_model.model_config["extra"] = "forbid"
    tool.fn_metadata.arg_model.model_rebuild(force=True)
    return tool


def _safe_root_project_hint(value: object) -> str | None:
    """Accept one client display label while refusing root paths and URIs."""

    if type(value) is not str:
        return None
    normalized = " ".join(value.split()).strip()
    if (
        not normalized
        or len(normalized) > 160
        or "/" in normalized
        or "\\" in normalized
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        return None
    return normalized


async def _single_root_project_hint(ctx: Context) -> str | None:
    """Read only one advertised MCP root name; never read or forward its URI."""

    try:
        capabilities = ctx.client_capabilities
        if capabilities is None or capabilities.roots is None or not ctx.session.can_send_request:
            return None
        roots_result = None
        with anyio.move_on_after(1.0) as cancel_scope:
            roots_result = await ctx.session.list_roots()
        if cancel_scope.cancel_called or roots_result is None or len(roots_result.roots) != 1:
            return None
        return _safe_root_project_hint(roots_result.roots[0].name)
    except Exception:
        # Roots are an optional MCP capability. A missing, malformed, or
        # unresponsive backchannel never blocks ordinary context retrieval.
        return None


def build_mcp() -> MCPServer:
    """Build the transport-independent tool registry."""

    instructions = (
        "Use this context service automatically; do not ask the user to manage it. At the "
        "start of every substantive task where preferences, projects, people, constraints, "
        "or prior decisions could matter, call bootstrap_context before answering or acting, "
        "without asking the user to open or manage All The Context. Core automatically activates "
        "the sole authorized project or a project uniquely named by the task or explicit host "
        "signal; it abstains instead of guessing across projects. "
        "Then use search_context or get_context_item when more detail is needed. When the user "
        "states or corrects durable personal context or makes a lasting decision, call "
        "propose_memory before the task ends. Set explicit_user_statement=true only when the "
        "content was directly stated by the user in the current interaction; leave it false "
        "(the default) for inference, summaries, and imported or provider text. Core evaluates "
        "submitted observations automatically under the user's configured memory policy; "
        "submission does not create a review task. Call forget_context only when the user "
        "explicitly asks to forget or delete a specific context record; never infer that "
        "request. Never represent inaccessible sources as covered and never submit secrets, "
        "hidden reasoning, provider instructions, or guesses as established facts."
    )
    registered_tools: list[Tool] = []

    def tool(**kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            registered_tools.append(_strict_tool(fn, **kwargs))
            return fn

        return register

    @tool()
    async def bootstrap_context(
        ctx: Context,
        task_description: str = "",
        requested_scopes: list[str] | None = None,
        character_budget: int = 8000,
        current_project: str | None = None,
    ) -> dict[str, Any]:
        """Compile context and automatically activate one unambiguous authorized project.

        Do not ask the user to open ATC. Supply current_project only when the host or
        task already exposes a project name or returned opaque project ID; otherwise
        Core resolves the task label or sole authorized project and safely abstains.
        """
        host_project_hint = (
            await _single_root_project_hint(ctx) if current_project is None else None
        )
        return _safe(
            lambda: _client().bootstrap_context(
                {
                    "query": task_description,
                    "requested_scopes": requested_scopes or [],
                    "budget_chars": character_budget,
                    "current_project": current_project,
                    "host_project_hint": host_project_hint,
                }
            )
        )

    @tool()
    def search_context(
        query: str,
        scopes: list[str] | None = None,
        kinds: list[str] | None = None,
        as_of: str | None = None,
        current_project: str | None = None,
        limit: int = 20,
        cursor: int = 0,
    ) -> dict[str, Any]:
        """Search current context now or at an offset-aware historical instant."""
        return _safe(
            lambda: _client().search_context(
                {
                    "query": query,
                    "scopes": scopes or [],
                    "kinds": kinds or [],
                    "as_of": as_of,
                    "current_project": current_project,
                    "limit": limit,
                    "offset": cursor,
                }
            )
        )

    @tool()
    def get_context_item(record_id: str) -> dict[str, Any]:
        """Get one current context record and its permitted provenance."""
        return _safe(lambda: _client().get_context_item(record_id))

    @tool()
    def context_status() -> dict[str, Any]:
        """Report context mode, Core/Relay availability, and replication freshness."""
        return _safe(lambda: _client().context_status())

    @tool()
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

    @tool()
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

    @tool()
    def finish_ingestion(session_id: str, coverage_report: dict[str, Any]) -> dict[str, Any]:
        """Finish an ingestion session with truthful available/unavailable coverage."""
        return _safe(
            lambda: _client().finish_ingestion(
                {"session_id": session_id, "coverage": coverage_report}
            )
        )

    @tool()
    def propose_memory(
        kind: str,
        content: str,
        scope: str,
        confidence: float,
        sensitivity: str = "normal",
        source_reference: str | None = None,
        evidence: str | None = None,
        explicit_user_statement: bool = False,
        entity_key: str | None = None,
        attribute_key: str | None = None,
        supersedes: str | None = None,
        observed_at: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit an observation for automatic Core evaluation.

        explicit_user_statement defaults to false. A configured witness-capable
        client (for example ATC-configured Codex or Claude Desktop) must set it
        true only for content the user directly stated in the current interaction.
        Never set it true for inference, summaries, or imported/provider text.
        """
        if (entity_key is None) != (attribute_key is None):
            raise ValueError("entity_key and attribute_key must be supplied together")
        payload: dict[str, Any] = {
            "kind": kind,
            "content": content,
            "scopes": [scope],
            "confidence": confidence,
            "sensitivity": sensitivity,
            "source_reference": source_reference,
            "evidence": evidence,
            "explicit_user_statement": explicit_user_statement,
            "entity_key": entity_key,
            "attribute_key": attribute_key,
            "supersedes": supersedes,
            "observed_at": observed_at,
        }
        payload["idempotency_key"] = idempotency_key or _automatic_proposal_key()
        return _safe(lambda: _client().propose_memory(payload))

    @tool()
    def report_context_error(
        record_id: str,
        description: str,
        suggested_correction: str | None = None,
    ) -> dict[str, Any]:
        """Report stale context; Core automatically evaluates any explicit correction."""
        return _safe(
            lambda: _client().report_context_error(
                {
                    "record_id": record_id,
                    "description": description,
                    "suggested_correction": suggested_correction,
                }
            )
        )

    @tool(
        annotations=ToolAnnotations.model_validate(
            {
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": True,
                "openWorldHint": False,
            }
        )
    )
    def forget_context(record_id: str, reason: str) -> dict[str, Any]:
        """Call only on an explicit user request to reversibly delete one context record."""
        return _safe(
            lambda: _client().forget_context(
                {
                    "record_id": record_id,
                    "reason": reason,
                }
            )
        )

    return MCPServer("All The Context", instructions=instructions, tools=registered_tools)


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


async def _run_stdio(server: MCPServer) -> None:
    """Run STDIO through the SDK's UTF-8, descriptor-isolated v2 entry point."""

    await server.run_stdio_async()


def _server_for_profile() -> MCPServer:
    """Select the explicitly gated adapter profile without changing ordinary MCP."""

    profile = os.environ.get("ATC_MCP_PROFILE", "")
    if not profile:
        return build_mcp()
    if profile == "claude_code_hook":
        from allthecontext.claude_code_hook import build_claude_code_hook_mcp

        return build_claude_code_hook_mcp()
    raise RuntimeError(f"Unsupported ATC_MCP_PROFILE: {profile}")


def main() -> None:
    """Run the lightweight local STDIO forwarding adapter."""
    anyio.run(_run_stdio, _server_for_profile())


def http_main() -> None:
    """Run a bearer-protected Streamable HTTP forwarding adapter."""
    server = _server_for_profile()
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
        routes=[
            Mount(
                "/",
                app=BearerGate(
                    server.streamable_http_app(
                        stateless_http=True,
                        json_response=True,
                        host=host,
                    ),
                    access_token,
                ),
            )
        ],
        lifespan=lifespan,
    )
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
