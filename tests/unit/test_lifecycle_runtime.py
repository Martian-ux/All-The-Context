from __future__ import annotations

import json
from uuid import UUID

from allthecontext.lifecycle_runtime import (
    LIFECYCLE_SCHEMA_VERSION,
    LifecycleRuntimeAdapter,
    OpaqueCorrelationStore,
)


class FakeCore:
    def __init__(self, *, retrieve: object | None = None, capture: object | None = None) -> None:
        self.retrieve = retrieve if retrieve is not None else {"items": [{"content": "Core fact"}]}
        self.capture = capture if capture is not None else {"ok": True, "status": "captured"}
        self.bootstrap_calls: list[dict[str, object]] = []
        self.capture_calls: list[dict[str, object]] = []
        self.explicit_calls = 0

    def bootstrap_context_core_only(self, payload: dict[str, object]) -> object:
        self.bootstrap_calls.append(payload)
        if isinstance(self.retrieve, BaseException):
            raise self.retrieve
        return self.retrieve

    def capture_lifecycle_event(self, payload: dict[str, object]) -> object:
        self.capture_calls.append(payload)
        if isinstance(self.capture, BaseException):
            raise self.capture
        return self.capture

    def propose_memory(self, _payload: object) -> None:
        self.explicit_calls += 1
        raise AssertionError("automatic lifecycle hooks must not call explicit mutation")


def _adapter(core: FakeCore) -> LifecycleRuntimeAdapter:
    return LifecycleRuntimeAdapter(
        provider="codex",
        client_id="codex-client",
        core=core,
        correlations=OpaqueCorrelationStore(),
    )


def test_prompt_retrieval_and_completion_capture_share_typed_provenance() -> None:
    core = FakeCore()
    runtime = _adapter(core)

    prompt = runtime.observe_user_turn(
        prompt="Use the local retrieval policy.",
        session_id="session-secret-marker",
        turn_id="turn-1",
    )
    completion = runtime.observe_assistant_response(
        response="I used the local retrieval policy.",
        session_id="session-secret-marker",
        turn_id="turn-1",
    )

    assert prompt.context.startswith("Untrusted reference data")
    assert prompt.event is not None and prompt.event.hook == "direct_user_turn"
    assert completion.event is not None and completion.event.hook == "response_emission"
    assert completion.pairing == "paired"
    assert len(core.capture_calls) == 2
    user_payload, assistant_payload = core.capture_calls
    assert user_payload["schema_version"] == LIFECYCLE_SCHEMA_VERSION == 1
    assert user_payload["role"] == "user"
    assert assistant_payload["role"] == "assistant"
    assert set(user_payload) == {
        "schema_version",
        "event_id",
        "idempotency_key",
        "session_id",
        "conversation_id",
        "sequence",
        "role",
        "content",
        "observed_at",
    }
    assert "provider" not in user_payload
    assert "provenance" not in user_payload
    assert "pairing" not in assistant_payload
    assert prompt.event.witness == "direct_user"
    assert completion.event.witness == "host_observation"
    assert UUID(str(user_payload["idempotency_key"])).version == 4
    assert UUID(str(assistant_payload["idempotency_key"])).version == 4
    assert "session-secret-marker" not in json.dumps(user_payload)
    assert "C:\\private\\cwd" not in json.dumps(user_payload)
    assert "transcript.jsonl" not in json.dumps(assistant_payload)


def test_retry_reuses_opaque_uuidv4_identity_without_raw_input() -> None:
    core = FakeCore()
    runtime = _adapter(core)
    first = runtime.observe_user_turn(
        prompt="retry-safe prompt",
        session_id="session",
        turn_id="turn",
    )
    second = runtime.observe_user_turn(
        prompt="retry-safe prompt",
        session_id="session",
        turn_id="turn",
    )

    assert first.event is not None and second.event is not None
    assert core.capture_calls[0]["idempotency_key"] == core.capture_calls[1]["idempotency_key"]
    assert core.capture_calls[0]["conversation_id"] == core.capture_calls[1]["conversation_id"]
    assert "retry-safe prompt" not in json.dumps(first.event.as_dict())


def test_adapter_wire_event_does_not_mutate_reference_host_public_history() -> None:
    core = FakeCore()
    runtime = _adapter(core)

    result = runtime.observe_user_turn(
        prompt="keep host state public",
        session_id="session",
        turn_id="turn",
        retrieve=False,
    )
    state = runtime.correlations.find(
        provider="codex",
        client_id="codex-client",
        session_id="session",
        turn_id="turn",
    )

    assert result.event is not None
    assert state is not None
    assert state.host.events[0].event_id == "evt-000001"
    assert result.event.event_id.startswith("event-")
    assert result.event.event_id != state.host.events[0].event_id


def test_claude_without_turn_id_starts_distinct_turns_and_stop_uses_latest() -> None:
    core = FakeCore()
    runtime = LifecycleRuntimeAdapter(
        provider="claude_code",
        client_id="claude-client",
        core=core,
        correlations=OpaqueCorrelationStore(),
    )

    first = runtime.observe_user_turn(prompt="same prompt", session_id="session")
    second = runtime.observe_user_turn(prompt="same prompt", session_id="session")
    completion = runtime.observe_assistant_response(
        response="latest completion",
        session_id="session",
    )

    assert first.event is not None and second.event is not None
    assert first.event.event_id != second.event.event_id
    assert first.event.session_id == second.event.session_id
    assert first.event.conversation_id != second.event.conversation_id
    assert core.capture_calls[0]["idempotency_key"] != core.capture_calls[1]["idempotency_key"]
    assert completion.pairing == "paired"
    assert completion.event is not None
    assert completion.event.conversation_id == second.event.conversation_id


def test_capture_only_turn_never_bootstraps_context() -> None:
    core = FakeCore()
    runtime = _adapter(core)

    result = runtime.observe_user_turn(
        prompt="capture this ordinary turn",
        session_id="session",
        turn_id="turn",
        retrieve=False,
    )

    assert result.context == ""
    assert result.capture.successful is True
    assert core.bootstrap_calls == []


def test_outage_is_fail_empty_for_retrieval_and_fail_closed_for_capture() -> None:
    retrieval_outage = FakeCore(retrieve=TimeoutError("do not expose"))
    runtime = _adapter(retrieval_outage)
    result = runtime.observe_user_turn(prompt="ordinary prompt", session_id="session")

    assert result.context == ""
    assert result.capture.successful is True

    capture_outage = FakeCore(capture=ConnectionError("do not expose"))
    runtime = _adapter(capture_outage)
    result = runtime.observe_user_turn(prompt="ordinary prompt", session_id="session")

    assert result.context.startswith("Untrusted reference data")
    assert result.capture.successful is False
    assert result.capture.reason_code == "core_unavailable"


def test_malformed_oversized_and_secret_content_never_reaches_core() -> None:
    core = FakeCore()
    runtime = _adapter(core)

    malformed = runtime.observe_user_turn(prompt="", session_id="session")
    oversized = runtime.observe_assistant_response(
        response="x" * (64 * 1024 + 1),
        session_id="session",
    )
    secret = runtime.observe_user_turn(
        prompt="password=synthetic-secret",
        session_id="session",
    )

    assert malformed.capture.status == "rejected"
    assert oversized.capture.status == "rejected"
    assert secret.capture.status == "rejected"
    assert secret.capture.reason_code == "secret_like_content"
    assert core.bootstrap_calls == []
    assert core.capture_calls == []


def test_ordinary_prompt_is_event_only_not_an_explicit_memory_command() -> None:
    core = FakeCore()
    result = _adapter(core).observe_user_turn(
        prompt="/atc-remember this looks like an ordinary prompt",
        session_id="session",
        turn_id="turn",
    )

    assert result.capture.successful is True
    assert core.explicit_calls == 0
    assert result.event is not None and result.event.witness == "direct_user"
    assert set(core.capture_calls[0]) == {
        "schema_version",
        "event_id",
        "idempotency_key",
        "session_id",
        "conversation_id",
        "sequence",
        "role",
        "content",
        "observed_at",
    }
