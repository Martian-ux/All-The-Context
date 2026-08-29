"""Provider lifecycle adapters over the existing client-runtime contract.

The adapter is deliberately a thin boundary.  It emits the existing
``ClientLifecycleEnvelope`` types used by the reference-host formation seam,
retrieves context through Core-only bootstrap, and sends bounded event payloads
to a narrow authenticated Core lifecycle endpoint.  It never invokes explicit
memory mutation or formation.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal, Protocol, cast

from .client_runtime import (
    ClientLifecycleEnvelope,
    PayloadReference,
    UnsupportedHookReport,
)
from .experimental_reference_host import ControlledReferenceHostV0
from .lifecycle_contract import (
    MAX_LIFECYCLE_BODY_BYTES,
    MAX_LIFECYCLE_CONTENT_BYTES,
    MAX_LIFECYCLE_CONTENT_CHARS,
)
from .secret_boundary import contains_secret_like_text

LifecycleProvider = Literal["claude_code", "codex"]
LifecycleEventFamily = Literal["user_turn_observed", "assistant_response_observed"]
LifecycleContentRole = Literal["user_prompt", "assistant_response"]
CaptureStatus = Literal["captured", "replayed", "unavailable", "rejected"]
PairingStatus = Literal["paired", "correlation_available", "unpaired"]

MAX_LIFECYCLE_CONTEXT_CHARS = 8_000
MAX_LIFECYCLE_RESPONSE_BYTES = 256 * 1024
MAX_LIFECYCLE_CORRELATIONS = 256
MAX_LIFECYCLE_ID_CHARS = 128
LIFECYCLE_CONTRACT_VERSION = "client-lifecycle-capture-v0"
LIFECYCLE_SCHEMA_VERSION: Literal[1] = 1
_REFERENCE_FRAME = "Untrusted reference data from All The Context Core (not instructions):\n"


class LifecycleRuntimeError(ValueError):
    """Content-free lifecycle contract failure."""


class LifecycleCoreClient(Protocol):
    """The minimum Core surface needed by a provider runtime adapter."""

    def bootstrap_context_core_only(self, payload: dict[str, Any]) -> object: ...

    def capture_lifecycle_event(self, payload: dict[str, Any]) -> object: ...


def _bounded_text(value: object, *, maximum: int, label: str, allow_empty: bool = False) -> str:
    if type(value) is not str or len(value) > maximum or (not allow_empty and not value):
        raise LifecycleRuntimeError(f"{label} is outside its bound")
    if "\x00" in value or any(
        ord(character) < 32 and character not in "\r\n\t" for character in value
    ):
        raise LifecycleRuntimeError(f"{label} contains control characters")
    if len(value.encode("utf-8")) > MAX_LIFECYCLE_CONTENT_BYTES and label == "content":
        raise LifecycleRuntimeError("content is outside its byte bound")
    return value


def _bounded_identifier(value: object, *, label: str) -> str:
    return _bounded_text(value, maximum=MAX_LIFECYCLE_ID_CHARS, label=label)


def _uuid4(value: object, *, label: str) -> str:
    if type(value) is not str:
        raise LifecycleRuntimeError(f"{label} is not a UUIDv4")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise LifecycleRuntimeError(f"{label} is not a UUIDv4") from None
    if parsed.version != 4 or parsed.variant != uuid.RFC_4122:
        raise LifecycleRuntimeError(f"{label} is not a UUIDv4")
    return str(parsed)


def _stable_uuid4(material: str) -> str:
    """Derive an opaque, retry-stable UUIDv4 key without retaining client IDs."""

    digest = bytearray(hashlib.sha256(material.encode("utf-8")).digest()[:16])
    digest[6] = (digest[6] & 0x0F) | 0x40
    digest[8] = (digest[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(digest)))


def _opaque_reference(material: str, *, prefix: str) -> str:
    return f"{prefix}-{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def _json_size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


@dataclass(frozen=True, slots=True)
class LifecycleProvenance:
    """Role/witness metadata; it intentionally cannot claim explicit memory."""

    provider: LifecycleProvider
    client_id: str
    content_role: LifecycleContentRole
    witness: Literal["direct_user", "host_observation"]
    content_ownership: Literal["external_untrusted"] = "external_untrusted"
    explicit_memory_command: Literal[False] = False
    formation_policy: Literal["event_only"] = "event_only"

    def __post_init__(self) -> None:
        if self.provider not in {"claude_code", "codex"}:
            raise LifecycleRuntimeError("unknown lifecycle provider")
        _bounded_identifier(self.client_id, label="client ID")
        expected_witness = (
            "direct_user" if self.content_role == "user_prompt" else "host_observation"
        )
        if self.witness != expected_witness:
            raise LifecycleRuntimeError("lifecycle witness does not match content role")
        if self.content_ownership != "external_untrusted":
            raise LifecycleRuntimeError("lifecycle content must remain external and untrusted")
        if self.explicit_memory_command is not False or self.formation_policy != "event_only":
            raise LifecycleRuntimeError("automatic lifecycle events cannot claim memory mutation")

@dataclass(frozen=True, slots=True)
class LifecycleCaptureRequest:
    """Narrow Core request containing one bounded event payload."""

    provider: LifecycleProvider
    event_family: LifecycleEventFamily
    correlation_id: str
    idempotency_key: str
    event: ClientLifecycleEnvelope
    content: str
    provenance: LifecycleProvenance
    pairing: PairingStatus = "unpaired"
    paired_event_id: str | None = None
    schema_version: Literal[1] = LIFECYCLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.provider not in {"claude_code", "codex"}:
            raise LifecycleRuntimeError("unknown lifecycle provider")
        if self.event_family not in {"user_turn_observed", "assistant_response_observed"}:
            raise LifecycleRuntimeError("unknown lifecycle event family")
        _bounded_identifier(self.correlation_id, label="correlation ID")
        _uuid4(self.idempotency_key, label="idempotency key")
        if not isinstance(self.event, ClientLifecycleEnvelope):
            raise LifecycleRuntimeError("lifecycle event must use ClientLifecycleEnvelope")
        if self.event.conversation_id is None:
            raise LifecycleRuntimeError("lifecycle event requires an opaque conversation ID")
        expected_hook = (
            "direct_user_turn"
            if self.event_family == "user_turn_observed"
            else "response_emission"
        )
        if self.event.hook != expected_hook:
            raise LifecycleRuntimeError("lifecycle event family does not match envelope hook")
        _bounded_text(
            self.content,
            maximum=MAX_LIFECYCLE_CONTENT_CHARS,
            label="content",
        )
        if self.provenance.provider != self.provider:
            raise LifecycleRuntimeError("provenance provider does not match request")
        if self.provenance.content_role != (
            "user_prompt" if self.event_family == "user_turn_observed" else "assistant_response"
        ):
            raise LifecycleRuntimeError("provenance role does not match request")
        if self.pairing not in {"paired", "correlation_available", "unpaired"}:
            raise LifecycleRuntimeError("invalid lifecycle pairing status")
        if self.paired_event_id is not None:
            _bounded_identifier(self.paired_event_id, label="paired event ID")
        if type(self.schema_version) is not int or self.schema_version != LIFECYCLE_SCHEMA_VERSION:
            raise LifecycleRuntimeError("unsupported lifecycle capture contract")

    def as_dict(self) -> dict[str, object]:
        """Return only the authenticated Core wire shape.

        Provider, client, source, witness, ACL, sensitivity, and provenance
        are intentionally not caller-declared on the wire.  Core derives them
        from the authenticated principal and its registered client.
        """

        payload = {
            "schema_version": self.schema_version,
            "event_id": self.event.event_id,
            "idempotency_key": self.idempotency_key,
            "session_id": self.event.session_id,
            "conversation_id": self.event.conversation_id,
            "sequence": self.event.sequence,
            "role": "user" if self.event_family == "user_turn_observed" else "assistant",
            "content": self.content,
            "observed_at": self.event.observed_at,
        }
        if _json_size(payload) > MAX_LIFECYCLE_BODY_BYTES:
            raise LifecycleRuntimeError("lifecycle capture request is too large")
        return payload


@dataclass(frozen=True, slots=True)
class LifecycleCaptureResponse:
    """Only explicit Core receipts count as capture success."""

    status: CaptureStatus
    reason_code: str = ""

    def __post_init__(self) -> None:
        if self.status not in {"captured", "replayed", "unavailable", "rejected"}:
            raise LifecycleRuntimeError("invalid lifecycle capture status")
        if self.reason_code:
            _bounded_identifier(self.reason_code, label="capture reason")

    @property
    def successful(self) -> bool:
        return self.status in {"captured", "replayed"}

    @classmethod
    def from_core(cls, value: object) -> LifecycleCaptureResponse:
        if not isinstance(value, Mapping) or value.get("ok") is not True:
            return cls("unavailable", "invalid_core_receipt")
        status = value.get("status")
        if status in {"captured", "replayed"}:
            return cls(cast(CaptureStatus, status))
        return cls("unavailable", "invalid_core_receipt")


@dataclass(frozen=True, slots=True)
class LifecycleRuntimeResult:
    """Content-free hook result; raw prompt/response text is never retained here."""

    context: str = ""
    capture: LifecycleCaptureResponse = LifecycleCaptureResponse("unavailable", "not_attempted")
    event: ClientLifecycleEnvelope | None = None
    pairing: PairingStatus = "unpaired"


@dataclass(slots=True)
class _CorrelationState:
    correlation_id: str
    session_ref: str
    host: ControlledReferenceHostV0
    user_event: ClientLifecycleEnvelope | None = None
    assistant_event: ClientLifecycleEnvelope | None = None
    user_capture: LifecycleCaptureResponse | None = None
    user_content_sha256: str | None = None
    assistant_content_sha256: str | None = None
    stable_turn_identity: bool = False


class OpaqueCorrelationStore:
    """Bounded in-memory correlation; only one-way keys and opaque IDs are kept."""

    def __init__(self, *, capacity: int = MAX_LIFECYCLE_CORRELATIONS) -> None:
        if type(capacity) is not int or not 1 <= capacity <= MAX_LIFECYCLE_CORRELATIONS:
            raise ValueError("correlation capacity is outside its bound")
        self._capacity = capacity
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _CorrelationState] = OrderedDict()
        self._active_by_session: OrderedDict[str, str] = OrderedDict()

    def begin(
        self,
        *,
        provider: LifecycleProvider,
        client_id: str,
        session_id: str,
        turn_id: str | None,
    ) -> tuple[str, _CorrelationState]:
        with self._lock:
            session_key = _correlation_key(provider, client_id, session_id, None)
            if turn_id is None:
                key = _correlation_key(
                    provider,
                    client_id,
                    session_id,
                    f"opaque-turn-{uuid.uuid4()}",
                )
            else:
                key = _correlation_key(provider, client_id, session_id, turn_id)
            state = self._entries.get(key)
            if state is None:
                state = _new_state(
                    provider,
                    client_id,
                    key,
                    session_key=session_key,
                    stable_turn_identity=turn_id is not None,
                )
            self._entries[key] = state
            self._entries.move_to_end(key)
            self._active_by_session[session_key] = key
            self._active_by_session.move_to_end(session_key)
            self._trim()
        return key, state

    def find(
        self,
        *,
        provider: LifecycleProvider,
        client_id: str,
        session_id: str,
        turn_id: str | None,
    ) -> _CorrelationState | None:
        key = _correlation_key(provider, client_id, session_id, turn_id)
        with self._lock:
            state = self._entries.get(key)
            if state is not None:
                self._entries.move_to_end(key)
            return state

    def _trim(self) -> None:
        while len(self._entries) > self._capacity:
            old_key, _old_state = self._entries.popitem(last=False)
            for session_key, active_key in tuple(self._active_by_session.items()):
                if active_key == old_key:
                    del self._active_by_session[session_key]


def _correlation_key(
    provider: LifecycleProvider,
    client_id: str,
    session_id: str,
    turn_id: str | None,
) -> str:
    _bounded_identifier(client_id, label="client ID")
    _bounded_identifier(session_id, label="session ID")
    if turn_id is not None:
        _bounded_identifier(turn_id, label="turn ID")
    material = "\0".join((provider, client_id, session_id, turn_id or "active"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _new_state(
    provider: LifecycleProvider,
    client_id: str,
    key: str,
    *,
    session_key: str | None = None,
    stable_turn_identity: bool = False,
) -> _CorrelationState:
    del provider
    session_ref = _opaque_reference(
        f"session\0{session_key or key}",
        prefix="session",
    )
    correlation_id = _opaque_reference(f"conversation\0{key}", prefix="conversation")
    host = ControlledReferenceHostV0.for_level(
        "L2",
        client_id=client_id,
        session_id=session_ref,
    )
    return _CorrelationState(
        correlation_id=correlation_id,
        session_ref=session_ref,
        host=host,
        stable_turn_identity=stable_turn_identity,
    )


def _opaque_event_id(
    state: _CorrelationState,
    *,
    role: LifecycleContentRole,
) -> str:
    """Give each logical role in a correlation an opaque stable event ID."""

    return _opaque_reference(f"event\0{state.correlation_id}\0{role}", prefix="event")


def _replace_latest_event(
    state: _CorrelationState,
    event: ClientLifecycleEnvelope,
    *,
    role: LifecycleContentRole,
) -> ClientLifecycleEnvelope:
    """Replace the reference-host sequence ID with an adapter-owned opaque ID."""

    replacement = replace(
        event,
        event_id=_opaque_event_id(state, role=role),
        conversation_id=event.conversation_id or state.correlation_id,
    )
    # The reference host remains the existing formation seam.  Its
    # deterministic test ID is not suitable as a cross-state Core event key,
    # so keep this adapter-owned envelope separate from host internals.
    return replacement


def _content_reference(state: _CorrelationState, *, role: str, content: str) -> PayloadReference:
    material = f"{state.correlation_id}\0{role}"
    return state.host.reference_for_content(
        reference=_opaque_reference(material, prefix="payload"),
        kind="user_turn" if role == "user_prompt" else "response",
        content=content,
    )


def _event_idempotency(state: _CorrelationState, *, role: str) -> str:
    if not state.stable_turn_identity:
        # Claude Code does not provide a stable per-turn identifier.  A later
        # callback with the same prompt/session may be a new turn, so it must
        # not be deduplicated as an exactly-once retry.
        return str(uuid.uuid4())
    return _stable_uuid4(f"{state.correlation_id}\0{role}")


def _content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _bounded_context_items(response: object) -> tuple[tuple[str, PayloadReference], ...]:
    if not isinstance(response, Mapping) or not isinstance(response.get("items"), list):
        return ()
    remaining = MAX_LIFECYCLE_CONTEXT_CHARS - len(_REFERENCE_FRAME)
    if remaining <= 0:
        return ()
    selected: list[tuple[str, PayloadReference]] = []
    for index, item in enumerate(response["items"]):
        if not isinstance(item, Mapping) or type(item.get("content")) is not str:
            continue
        content = cast(str, item["content"])
        if not content or contains_secret_like_text(content):
            continue
        separator = 2 if selected else 0
        available = remaining - separator
        if available <= 0:
            break
        bounded = content[:available]
        ref = PayloadReference(
            reference=_opaque_reference(f"context\0{index}\0{bounded}", prefix="context"),
            kind="context_pack",
            size_bytes=len(bounded.encode("utf-8")),
            sha256=hashlib.sha256(bounded.encode("utf-8")).hexdigest(),
        )
        selected.append((bounded, ref))
        remaining -= separator + len(bounded)
        if len(bounded) != len(content):
            break
    return tuple(selected)


def _format_context(items: tuple[tuple[str, PayloadReference], ...]) -> str:
    if not items:
        return ""
    return _REFERENCE_FRAME + "\n\n".join(content for content, _ref in items)


def _empty_context_reference(state: _CorrelationState) -> PayloadReference:
    return PayloadReference(
        reference=_opaque_reference(f"{state.correlation_id}\0empty_context", prefix="context"),
        kind="context_pack",
        size_bytes=0,
        sha256=hashlib.sha256(b"").hexdigest(),
    )


class LifecycleRuntimeAdapter:
    """Automatic retrieval and observation for one configured client."""

    def __init__(
        self,
        *,
        provider: LifecycleProvider,
        client_id: str,
        core: object | None,
        correlations: OpaqueCorrelationStore | None = None,
    ) -> None:
        if provider not in {"claude_code", "codex"}:
            raise ValueError("unknown lifecycle provider")
        _bounded_identifier(client_id, label="client ID")
        self.provider = provider
        self.client_id = client_id
        self.core = core
        self.correlations = correlations or OpaqueCorrelationStore()

    def observe_user_turn(
        self,
        *,
        prompt: str,
        session_id: str,
        turn_id: str | None = None,
        retrieve: bool = True,
    ) -> LifecycleRuntimeResult:
        try:
            _bounded_text(prompt, maximum=MAX_LIFECYCLE_CONTENT_CHARS, label="content")
            _bounded_identifier(session_id, label="session ID")
            if turn_id is not None:
                _bounded_identifier(turn_id, label="turn ID")
        except LifecycleRuntimeError:
            return LifecycleRuntimeResult(
                capture=LifecycleCaptureResponse("rejected", "malformed_input")
            )
        if contains_secret_like_text(prompt):
            return LifecycleRuntimeResult(
                capture=LifecycleCaptureResponse("rejected", "secret_like_content")
            )
        _key, state = self.correlations.begin(
            provider=self.provider,
            client_id=self.client_id,
            session_id=session_id,
            turn_id=turn_id,
        )
        try:
            content_digest = _content_sha256(prompt)
            retrying = state.user_event is not None
            event: ClientLifecycleEnvelope
            if state.user_event is not None:
                if state.user_content_sha256 != content_digest:
                    return LifecycleRuntimeResult(
                        capture=LifecycleCaptureResponse("rejected", "retry_payload_conflict")
                    )
                event = state.user_event
            else:
                candidate = state.host.observe_direct_user_content(
                    reference=_opaque_reference(
                        f"{state.correlation_id}\0user_prompt", prefix="payload"
                    ),
                    content=prompt,
                    conversation_id=state.correlation_id,
                )
                if isinstance(candidate, UnsupportedHookReport):
                    return LifecycleRuntimeResult(
                        capture=LifecycleCaptureResponse("unavailable", "hook_unsupported")
                    )
                event = _replace_latest_event(state, candidate, role="user_prompt")
                state.user_content_sha256 = content_digest
            request = LifecycleCaptureRequest(
                provider=self.provider,
                event_family="user_turn_observed",
                correlation_id=state.correlation_id,
                idempotency_key=_event_idempotency(state, role="user_prompt"),
                event=event,
                content=prompt,
                provenance=LifecycleProvenance(
                    provider=self.provider,
                    client_id=self.client_id,
                    content_role="user_prompt",
                    witness="direct_user",
                ),
            )
            capture = self._capture(request)
            with self.correlations._lock:
                state.user_event = event
                state.user_capture = capture
            context = ""
            if retrieve:
                context = (
                    self._retrieve_for_retry(prompt)
                    if retrying
                    else self._retrieve_and_deliver(state, prompt)
                )
            return LifecycleRuntimeResult(
                context=context,
                capture=capture,
                event=event,
                pairing="unpaired",
            )
        except Exception:
            return LifecycleRuntimeResult(
                capture=LifecycleCaptureResponse("unavailable", "runtime_error")
            )

    def observe_assistant_response(
        self,
        *,
        response: str,
        session_id: str,
        turn_id: str | None = None,
    ) -> LifecycleRuntimeResult:
        try:
            _bounded_text(response, maximum=MAX_LIFECYCLE_CONTENT_CHARS, label="content")
            _bounded_identifier(session_id, label="session ID")
            if turn_id is not None:
                _bounded_identifier(turn_id, label="turn ID")
        except LifecycleRuntimeError:
            return LifecycleRuntimeResult(
                capture=LifecycleCaptureResponse("rejected", "malformed_input")
            )
        if contains_secret_like_text(response):
            return LifecycleRuntimeResult(
                capture=LifecycleCaptureResponse("rejected", "secret_like_content")
            )
        if turn_id is None:
            # Claude Code supplies only a session identifier.  Never attach a
            # completion to the session's latest prompt: that would turn an
            # unreliable session-only guess into a claimed turn pairing.
            key = _correlation_key(
                self.provider,
                self.client_id,
                session_id,
                f"unpaired-response-{uuid.uuid4()}",
            )
            state = _new_state(
                self.provider,
                self.client_id,
                key,
                session_key=_correlation_key(self.provider, self.client_id, session_id, None),
                stable_turn_identity=False,
            )
            pairing: PairingStatus = "unpaired"
        else:
            found_state = self.correlations.find(
                provider=self.provider,
                client_id=self.client_id,
                session_id=session_id,
                turn_id=turn_id,
            )
            if found_state is None:
                key = _correlation_key(self.provider, self.client_id, session_id, turn_id)
                state = _new_state(
                    self.provider,
                    self.client_id,
                    key,
                    session_key=_correlation_key(self.provider, self.client_id, session_id, None),
                    stable_turn_identity=True,
                )
                pairing = "correlation_available"
            else:
                state = found_state
                pairing = "paired" if state.user_event is not None else "correlation_available"
        try:
            content_digest = _content_sha256(response)
            event: ClientLifecycleEnvelope
            if state.stable_turn_identity and state.assistant_event is not None:
                if state.assistant_content_sha256 != content_digest:
                    return LifecycleRuntimeResult(
                        capture=LifecycleCaptureResponse("rejected", "retry_payload_conflict")
                    )
                event = state.assistant_event
            else:
                candidate = state.host.observe_response_emission(
                    _content_reference(state, role="assistant_response", content=response),
                    emission_state="completed",
                )
                if isinstance(candidate, UnsupportedHookReport):
                    return LifecycleRuntimeResult(
                        capture=LifecycleCaptureResponse("unavailable", "hook_unsupported")
                    )
                event = _replace_latest_event(state, candidate, role="assistant_response")
                state.assistant_content_sha256 = content_digest
            request = LifecycleCaptureRequest(
                provider=self.provider,
                event_family="assistant_response_observed",
                correlation_id=state.correlation_id,
                idempotency_key=_event_idempotency(state, role="assistant_response"),
                event=event,
                content=response,
                provenance=LifecycleProvenance(
                    provider=self.provider,
                    client_id=self.client_id,
                    content_role="assistant_response",
                    witness="host_observation",
                ),
                pairing=pairing,
                paired_event_id=(
                    state.user_event.event_id
                    if pairing == "paired" and state.user_event is not None
                    else None
                ),
            )
            capture = self._capture(request)
            if state.stable_turn_identity:
                with self.correlations._lock:
                    state.assistant_event = event
            return LifecycleRuntimeResult(capture=capture, event=event, pairing=pairing)
        except Exception:
            return LifecycleRuntimeResult(
                capture=LifecycleCaptureResponse("unavailable", "runtime_error")
            )

    def _retrieve_and_deliver(self, state: _CorrelationState, prompt: str) -> str:
        request = state.host.request_pre_generation_context(
            generation_id=_opaque_reference(
                f"{state.correlation_id}\0generation", prefix="generation"
            ),
            conversation_id=state.correlation_id,
        )
        if isinstance(request, UnsupportedHookReport):
            return ""
        response: object = {}
        bootstrap = getattr(self.core, "bootstrap_context_core_only", None)
        if callable(bootstrap):
            try:
                response = bootstrap({"query": prompt, "budget_chars": MAX_LIFECYCLE_CONTEXT_CHARS})
            except Exception:
                response = {}
        items = _bounded_context_items(response)
        try:
            refs = tuple(ref for _content, ref in items) or (_empty_context_reference(state),)
            state.host.deliver_context(request, refs)
            generation_id = getattr(request.payload, "generation_id", None)
            if type(generation_id) is not str:
                return ""
            state.host.begin_generation(generation_id=generation_id)
        except Exception:
            # Retrieval is fail-empty and must never block the provider turn.
            pass
        return _format_context(items)

    def _retrieve_for_retry(self, prompt: str) -> str:
        bootstrap = getattr(self.core, "bootstrap_context_core_only", None)
        if not callable(bootstrap):
            return ""
        try:
            response = bootstrap(
                {"query": prompt, "budget_chars": MAX_LIFECYCLE_CONTEXT_CHARS}
            )
        except Exception:
            return ""
        return _format_context(_bounded_context_items(response))

    def _capture(self, request: LifecycleCaptureRequest) -> LifecycleCaptureResponse:
        if contains_secret_like_text(request.content):
            return LifecycleCaptureResponse("rejected", "secret_like_content")
        submit = getattr(self.core, "capture_lifecycle_event", None)
        if not callable(submit):
            return LifecycleCaptureResponse("unavailable", "capture_contract_unavailable")
        try:
            payload = request.as_dict()
            response = submit(payload)
        except Exception:
            return LifecycleCaptureResponse("unavailable", "core_unavailable")
        return LifecycleCaptureResponse.from_core(response)


__all__ = [
    "LIFECYCLE_CONTRACT_VERSION",
    "LIFECYCLE_SCHEMA_VERSION",
    "MAX_LIFECYCLE_BODY_BYTES",
    "MAX_LIFECYCLE_CONTENT_BYTES",
    "MAX_LIFECYCLE_CONTENT_CHARS",
    "MAX_LIFECYCLE_CONTEXT_CHARS",
    "MAX_LIFECYCLE_RESPONSE_BYTES",
    "CaptureStatus",
    "LifecycleCaptureRequest",
    "LifecycleCaptureResponse",
    "LifecycleCoreClient",
    "LifecycleEventFamily",
    "LifecycleProvenance",
    "LifecycleRuntimeAdapter",
    "LifecycleRuntimeError",
    "LifecycleRuntimeResult",
    "OpaqueCorrelationStore",
]
