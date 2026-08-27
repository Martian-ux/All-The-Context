"""Native Codex UserPromptSubmit/Stop lifecycle hook runtime.

This module is intentionally limited to lifecycle observation capture.  Read
retrieval stays on the separately configured read-principal hook. Explicit
memory mutations stay on approved MCP tools; these lifecycle hooks never call
them and never request tool approval.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.mcpserver.tools import Tool
from pydantic import Field, StrictStr

from .claude_code_hook import _is_managed_loopback_core
from .credentials import KeyringCredentialStore
from .http_client import ContextHttpClient
from .lifecycle_runtime import (
    LifecycleCaptureResponse,
    LifecycleRuntimeAdapter,
    LifecycleRuntimeResult,
    OpaqueCorrelationStore,
)
from .mcp_adapter import _ensure_local_core, _strict_tool

CODEX_USER_PROMPT_SUBMIT = "UserPromptSubmit"
CODEX_STOP = "Stop"
CODEX_HOOK_INPUT_MAX_BYTES = 256 * 1024
CODEX_HOOK_CORE_TIMEOUT_SECONDS = 2.0
CODEX_HOOK_MAX_RESPONSE_BYTES = 256 * 1024
CODEX_SESSION_ID_MAX_CHARS = 128
CODEX_TURN_ID_MAX_CHARS = 128
CODEX_PROMPT_MAX_CHARS = 64 * 1024
CODEX_RESPONSE_MAX_CHARS = 64 * 1024
_LIFECYCLE_CORRELATIONS = OpaqueCorrelationStore()


def _empty_user_prompt_output() -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": CODEX_USER_PROMPT_SUBMIT,
            "additionalContext": "",
        }
    }


def _empty_stop_output() -> dict[str, Any]:
    return {}


def _reject_constant(_value: str) -> Any:
    raise ValueError("non-standard JSON constant")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _bounded_field(
    payload: Mapping[str, object],
    name: str,
    *,
    maximum: int,
    required: bool = True,
) -> str | None:
    value = payload.get(name)
    if value is None and not required:
        return None
    if type(value) is not str or not value or len(value) > maximum or "\x00" in value:
        return None
    if any(ord(character) < 32 and character not in "\r\n\t" for character in value):
        return None
    return value


def _hook_client() -> ContextHttpClient | None:
    """Build a client only after strict direct loopback Core proof."""

    target = os.environ.get("ATC_TARGET_URL", "http://127.0.0.1:7337")
    if not _is_managed_loopback_core(target):
        return None
    client_id = os.environ.get("ATC_CLIENT_ID", "")
    if not client_id:
        return None
    try:
        _ensure_local_core(
            target,
            wait_seconds=CODEX_HOOK_CORE_TIMEOUT_SECONDS,
            require_verified=True,
            ignore_environment_proxy=True,
        )
    except Exception:
        return None
    token = os.environ.get("ATC_CLIENT_TOKEN", "")
    if not token:
        try:
            token = KeyringCredentialStore().get(f"client:{client_id}") or ""
        except Exception:
            return None
    if not token:
        return None
    return ContextHttpClient(
        target,
        client_id,
        token,
        timeout_seconds=CODEX_HOOK_CORE_TIMEOUT_SECONDS,
        max_response_bytes=CODEX_HOOK_MAX_RESPONSE_BYTES,
        trust_env=False,
    )


def handle_codex_event(
    payload: Mapping[str, object],
    *,
    core: object | None = None,
    adapter: LifecycleRuntimeAdapter | None = None,
) -> tuple[dict[str, Any], LifecycleRuntimeResult]:
    """Handle one validated Codex lifecycle event without raising to the client."""

    event_name = payload.get("hook_event_name")
    if event_name not in {CODEX_USER_PROMPT_SUBMIT, CODEX_STOP}:
        return {}, LifecycleRuntimeResult()
    session_id = _bounded_field(payload, "session_id", maximum=CODEX_SESSION_ID_MAX_CHARS)
    if session_id is None:
        result = LifecycleRuntimeResult(
            capture=LifecycleCaptureResponse("rejected", "malformed_input")
        )
        return (
            (
                _empty_user_prompt_output()
                if event_name == CODEX_USER_PROMPT_SUBMIT
                else _empty_stop_output()
            ),
            result,
        )
    turn_id = _bounded_field(
        payload,
        "turn_id",
        maximum=CODEX_TURN_ID_MAX_CHARS,
        required=False,
    )
    if event_name == CODEX_USER_PROMPT_SUBMIT:
        prompt = _bounded_field(payload, "prompt", maximum=CODEX_PROMPT_MAX_CHARS)
        if prompt is None:
            result = LifecycleRuntimeResult(
                capture=LifecycleCaptureResponse("rejected", "malformed_input")
            )
            return _empty_user_prompt_output(), result
        runtime = adapter or LifecycleRuntimeAdapter(
            provider="codex",
            client_id=os.environ.get("ATC_CLIENT_ID", "codex-lifecycle-hook"),
            core=core if core is not None else _hook_client(),
            correlations=_LIFECYCLE_CORRELATIONS,
        )
        result = runtime.observe_user_turn(
            prompt=prompt,
            session_id=session_id,
            turn_id=turn_id,
            retrieve=False,
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": CODEX_USER_PROMPT_SUBMIT,
                "additionalContext": result.context,
            }
        }, result

    if "last_assistant_message" not in payload:
        return _empty_stop_output(), LifecycleRuntimeResult()
    response = _bounded_field(
        payload,
        "last_assistant_message",
        maximum=CODEX_RESPONSE_MAX_CHARS,
        required=False,
    )
    if response is None:
        return _empty_stop_output(), LifecycleRuntimeResult(
            capture=LifecycleCaptureResponse("rejected", "malformed_input")
        )
    runtime = adapter or LifecycleRuntimeAdapter(
        provider="codex",
        client_id=os.environ.get("ATC_CLIENT_ID", "codex-lifecycle-hook"),
        core=core if core is not None else _hook_client(),
        correlations=_LIFECYCLE_CORRELATIONS,
    )
    result = runtime.observe_assistant_response(
        response=response,
        session_id=session_id,
        turn_id=turn_id,
    )
    return _empty_stop_output(), result


def codex_user_prompt_submit(
    prompt: Annotated[StrictStr, Field(max_length=CODEX_PROMPT_MAX_CHARS)],
    session_id: Annotated[StrictStr, Field(max_length=CODEX_SESSION_ID_MAX_CHARS)],
    turn_id: Annotated[
        StrictStr | None,
        Field(default=None, max_length=CODEX_TURN_ID_MAX_CHARS),
    ] = None,
) -> dict[str, Any]:
    """Codex MCP-tool hook for direct user-turn capture."""

    output, _result = handle_codex_event(
        {
            "hook_event_name": CODEX_USER_PROMPT_SUBMIT,
            "prompt": prompt,
            "session_id": session_id,
            "turn_id": turn_id,
        }
    )
    return output


def codex_stop(
    last_assistant_message: Annotated[
        StrictStr | None,
        Field(default=None, max_length=CODEX_RESPONSE_MAX_CHARS),
    ],
    session_id: Annotated[StrictStr, Field(max_length=CODEX_SESSION_ID_MAX_CHARS)],
    turn_id: Annotated[
        StrictStr | None,
        Field(default=None, max_length=CODEX_TURN_ID_MAX_CHARS),
    ] = None,
) -> dict[str, Any]:
    """Codex MCP-tool hook for completion observation without memory mutation."""

    if last_assistant_message is None:
        return _empty_stop_output()
    output, _result = handle_codex_event(
        {
            "hook_event_name": CODEX_STOP,
            "last_assistant_message": last_assistant_message,
            "session_id": session_id,
            "turn_id": turn_id,
        }
    )
    return output


def build_codex_hook_mcp() -> MCPServer:
    """Build the dedicated lifecycle-only MCP hook server."""

    user_tool: Tool = _strict_tool(
        codex_user_prompt_submit,
        name="codex_user_prompt_submit",
        structured_output=False,
        hide_input_in_errors=True,
    )
    stop_tool: Tool = _strict_tool(
        codex_stop,
        name="codex_stop",
        structured_output=False,
        hide_input_in_errors=True,
    )
    return MCPServer(
        "All The Context Codex Lifecycle Hook",
        instructions=(
            "Codex UserPromptSubmit and Stop capture hooks only. Do not bootstrap context from "
            "this capture-principal server; a separately configured read-principal hook supplies "
            "untrusted reference data before generation. Captured events are evidence; Core "
            "decides any later formation and canonical-memory change."
        ),
        tools=[user_tool, stop_tool],
    )


def _read_bounded_stdin() -> Mapping[str, object] | None:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    raw = stream.read(CODEX_HOOK_INPUT_MAX_BYTES + 1)
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, bytes) or len(raw) > CODEX_HOOK_INPUT_MAX_BYTES:
        return None
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def main() -> None:
    """Run as a Codex native command hook; stdout is always bounded JSON only."""

    payload = _read_bounded_stdin()
    output: dict[str, Any] = {}
    if payload is not None:
        try:
            output, _result = handle_codex_event(payload)
        except Exception:
            output = {}
    try:
        sys.stdout.write(json.dumps(output, ensure_ascii=False, separators=(",", ":")) + "\n")
    except (BrokenPipeError, OSError):
        return


__all__ = [
    "CODEX_HOOK_INPUT_MAX_BYTES",
    "CODEX_HOOK_MAX_RESPONSE_BYTES",
    "CODEX_STOP",
    "CODEX_USER_PROMPT_SUBMIT",
    "build_codex_hook_mcp",
    "codex_stop",
    "codex_user_prompt_submit",
    "handle_codex_event",
    "main",
]
