from __future__ import annotations

import io
import json

import anyio
import pytest
from allthecontext import codex_hook as hook
from allthecontext.lifecycle_runtime import LifecycleRuntimeAdapter, OpaqueCorrelationStore


class Core:
    def __init__(self) -> None:
        self.bootstrap_calls: list[dict[str, object]] = []
        self.capture_calls: list[dict[str, object]] = []
        self.explicit_calls = 0

    def bootstrap_context_core_only(self, payload: dict[str, object]) -> object:
        self.bootstrap_calls.append(payload)
        return {"items": [{"content": "read-principal context"}]}

    def capture_lifecycle_event(self, payload: dict[str, object]) -> object:
        self.capture_calls.append(payload)
        return {"ok": True, "status": "captured"}

    def propose_memory(self, _payload: object) -> None:
        self.explicit_calls += 1


def _adapter(core: Core) -> LifecycleRuntimeAdapter:
    return LifecycleRuntimeAdapter(
        provider="codex",
        client_id="capture-client",
        core=core,
        correlations=OpaqueCorrelationStore(),
    )


def test_codex_native_events_capture_only_and_pair_prompt_with_stop() -> None:
    core = Core()
    adapter = _adapter(core)

    user_output, user_result = hook.handle_codex_event(
        {
            "hook_event_name": hook.CODEX_USER_PROMPT_SUBMIT,
            "prompt": "ordinary prompt",
            "session_id": "session",
            "turn_id": "turn",
            "cwd": r"C:\private\cwd-must-not-forward",
            "transcript_path": r"C:\private\transcript.jsonl",
        },
        adapter=adapter,
    )
    stop_output, stop_result = hook.handle_codex_event(
        {
            "hook_event_name": hook.CODEX_STOP,
            "last_assistant_message": "rendered assistant response",
            "session_id": "session",
            "turn_id": "turn",
            "cwd": r"C:\private\cwd-must-not-forward",
            "transcript_path": r"C:\private\transcript.jsonl",
        },
        adapter=adapter,
    )

    assert user_output["hookSpecificOutput"]["additionalContext"] == ""
    assert stop_output == {}
    assert user_result.event is not None and user_result.event.witness == "direct_user"
    assert stop_result.event is not None and stop_result.event.witness == "host_observation"
    assert stop_result.pairing == "paired"
    assert core.bootstrap_calls == []
    assert [payload["role"] for payload in core.capture_calls] == ["user", "assistant"]
    serialized = json.dumps(core.capture_calls)
    assert "private\\cwd" not in serialized
    assert "transcript.jsonl" not in serialized
    assert "ordinary prompt" in serialized
    assert "rendered assistant response" in serialized
    assert core.explicit_calls == 0


def test_codex_default_runtime_keeps_in_process_prompt_stop_correlation() -> None:
    core = Core()
    session_id = "default-runtime-session"
    turn_id = "default-runtime-turn"

    _user_output, _user_result = hook.handle_codex_event(
        {
            "hook_event_name": hook.CODEX_USER_PROMPT_SUBMIT,
            "prompt": "ordinary prompt",
            "session_id": session_id,
            "turn_id": turn_id,
        },
        core=core,
    )
    _stop_output, stop_result = hook.handle_codex_event(
        {
            "hook_event_name": hook.CODEX_STOP,
            "last_assistant_message": "rendered assistant response",
            "session_id": session_id,
            "turn_id": turn_id,
        },
        core=core,
    )

    assert stop_result.pairing == "paired"
    assert [payload["role"] for payload in core.capture_calls] == ["user", "assistant"]


def test_codex_mcp_profile_is_lifecycle_only_and_strict() -> None:
    async def list_tools() -> dict[str, object]:
        server = hook.build_codex_hook_mcp()
        return {tool.name: tool for tool in await server.list_tools()}

    tools = anyio.run(list_tools)
    assert set(tools) == {"codex_user_prompt_submit", "codex_stop"}
    user_tool = tools["codex_user_prompt_submit"]
    stop_tool = tools["codex_stop"]
    assert not {"cwd", "transcript_path", "log_path", "provenance"} & set(
        user_tool.input_schema["properties"]
    )
    assert not {"cwd", "transcript_path", "log_path", "provenance"} & set(
        stop_tool.input_schema["properties"]
    )
    assert user_tool.input_schema["additionalProperties"] is False
    assert set(user_tool.input_schema["properties"]) == {"prompt", "session_id", "turn_id"}


@pytest.mark.parametrize(
    "payload",
    [
        {"hook_event_name": hook.CODEX_USER_PROMPT_SUBMIT, "session_id": "s"},
        {
            "hook_event_name": hook.CODEX_USER_PROMPT_SUBMIT,
            "prompt": "x" * (hook.CODEX_PROMPT_MAX_CHARS + 1),
            "session_id": "s",
        },
        {
            "hook_event_name": hook.CODEX_STOP,
            "last_assistant_message": "x" * (hook.CODEX_RESPONSE_MAX_CHARS + 1),
            "session_id": "s",
        },
    ],
)
def test_codex_malformed_or_oversized_event_is_content_free(payload: dict[str, object]) -> None:
    core = Core()
    output, result = hook.handle_codex_event(payload, core=core)

    assert output in (
        {},
        {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ""}},
    )
    assert result.capture.status == "rejected"
    assert core.bootstrap_calls == []
    assert core.capture_calls == []


def test_codex_command_runtime_stdout_has_no_raw_input_or_diagnostics(monkeypatch, capsys) -> None:
    core = Core()
    monkeypatch.setattr(hook, "_hook_client", lambda: core)
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "hook_event_name": hook.CODEX_USER_PROMPT_SUBMIT,
                    "prompt": "prompt-not-output",
                    "session_id": "session-not-output",
                    "turn_id": "turn",
                    "cwd": "cwd-not-output",
                    "transcript_path": "transcript-not-output",
                }
            )
        ),
    )

    hook.main()
    output = capsys.readouterr().out
    assert "prompt-not-output" not in output
    assert "session-not-output" not in output
    assert "cwd-not-output" not in output
    assert "transcript-not-output" not in output
    assert "read-principal context" not in output


def test_codex_oversized_stdin_fails_closed_without_echo(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        hook.sys,
        "stdin",
        io.StringIO("x" * (hook.CODEX_HOOK_INPUT_MAX_BYTES + 1)),
    )

    hook.main()
    assert capsys.readouterr().out == "{}\n"
