"""Small controlled in-process host for the experimental runtime contract.

This is ZF-009 evidence code, not a provider integration or SDK.  The host
reuses the existing v0 runtime host for ordering and typed envelopes.  Its
only additions are a Core compiler seam, an injected checkpoint sink, and a
safe way for sanitized fixtures to turn inert text into untrusted references.
It does not own lifecycle storage, retrieval, or canonical memory.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise
from typing import Literal, Protocol

from .client_runtime import (
    ClientLifecycleEnvelope,
    ClientRuntimeCapabilities,
    ClientRuntimeContractError,
    ContextDeliveryReceipt,
    ContextRequestPayload,
    DeterministicFakeClientRuntimeHost,
    GenerationReceipt,
    HookResult,
    HostTraceEntry,
    OrderingViolation,
    PayloadReference,
    ReferenceKind,
    RestartSessionTransitionPayload,
    UnsupportedHookReport,
)
from .models import BootstrapRequest, BootstrapResponse
from .security import ClientPrincipal

CapabilityLevel = Literal["L0", "L1", "L2", "L3"]
ReferenceHostTransport = Literal["in_process_reference", "ordinary_mcp"]
REFERENCE_HOST_MAX_LEVEL: CapabilityLevel = "L2"
_LEVEL_ORDER: dict[CapabilityLevel, int] = {"L0": 0, "L1": 1, "L2": 2, "L3": 3}
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+?=&%-]{0,255}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SECRET_LIKE = re.compile(
    r"(?i)(bearer\s+|basic\s+|\bsk-[a-z0-9]|\bgh[pousr]_[a-z0-9]|"
    r"\bAIza[a-z0-9]|secret\s*[:=]|password\s*[:=]|credential\s*[:=]|"
    r"token\s*[:=])"
)


class ReferenceHostError(ClientRuntimeContractError):
    """Raised when the controlled host cannot satisfy its bounded contract."""


class SecretLikePayloadRefused(ReferenceHostError):
    """Raised without putting secret-like content in a lifecycle envelope."""

    def __init__(self) -> None:
        super().__init__("secret-like payload refused before lifecycle persistence")
        self.reason_code = "direct_secret_like_content"


class MissingCorePrincipal(ReferenceHostError):
    """Raised when accepted L1+ compilation has no Core ClientPrincipal."""

    def __init__(self) -> None:
        super().__init__("accepted L1+ compilation requires a Core ClientPrincipal")
        self.reason_code = "missing_core_principal"


@dataclass(frozen=True, slots=True)
class CapabilityNegotiation:
    """Requested versus actually accepted capability, without provider claims."""

    requested_level: CapabilityLevel
    accepted_level: CapabilityLevel
    transport: ReferenceHostTransport
    capabilities: ClientRuntimeCapabilities
    reason: str

    @property
    def downgraded(self) -> bool:
        return self.requested_level != self.accepted_level


def negotiate_capabilities(
    requested_level: CapabilityLevel,
    *,
    transport: ReferenceHostTransport = "in_process_reference",
) -> CapabilityNegotiation:
    """Negotiate the real surface: reference host L0-L2, ordinary MCP L0."""

    _literal(requested_level, set(_LEVEL_ORDER), "requested capability level")
    _literal(transport, {"in_process_reference", "ordinary_mcp"}, "reference-host transport")
    if transport == "ordinary_mcp":
        accepted_level: CapabilityLevel = "L0"
        reason = "ordinary_mcp_is_l0"
    elif _LEVEL_ORDER[requested_level] > _LEVEL_ORDER[REFERENCE_HOST_MAX_LEVEL]:
        accepted_level = REFERENCE_HOST_MAX_LEVEL
        reason = "l3_consequence_enforcement_not_implemented"
    else:
        accepted_level = requested_level
        reason = "requested_level_supported_by_reference_host"
    return CapabilityNegotiation(
        requested_level=requested_level,
        accepted_level=accepted_level,
        transport=transport,
        capabilities=ClientRuntimeCapabilities.for_level(accepted_level),
        reason=reason,
    )


class CoreContextCompiler(Protocol):
    """The existing Core Retrieval V3/bootstrap seam."""

    def __call__(
        self,
        request: BootstrapRequest,
        principal: ClientPrincipal | None = None,
    ) -> BootstrapResponse: ...


CHECKPOINT_VERSION: Literal["reference-host-checkpoint-v0"] = "reference-host-checkpoint-v0"


@dataclass(frozen=True, slots=True)
class RuntimeCheckpoint:
    """Typed, caller-owned checkpoint state for the controlled reference host.

    ``idempotency_key`` is stable for the exact typed state.  A checkpoint sink
    must use it to make retries idempotent, including when it commits and then
    raises before the host can record the successful callback.
    """

    checkpoint_version: Literal["reference-host-checkpoint-v0"]
    sequence: int
    session_id: str
    client_id: str
    capability_level: CapabilityLevel
    events: tuple[ClientLifecycleEnvelope, ...]
    trace: tuple[HostTraceEntry, ...]
    pending_context: tuple[tuple[str, ClientLifecycleEnvelope], ...]
    delivered_context: tuple[tuple[str, ContextDeliveryReceipt], ...]
    started_generations: frozenset[str]
    integrity_digest: str
    idempotency_key: str

    @classmethod
    def create(
        cls,
        *,
        sequence: int,
        session_id: str,
        client_id: str,
        capability_level: CapabilityLevel,
        events: Sequence[ClientLifecycleEnvelope],
        trace: Sequence[HostTraceEntry],
        pending_context: Sequence[tuple[str, ClientLifecycleEnvelope]],
        delivered_context: Sequence[tuple[str, ContextDeliveryReceipt]],
        started_generations: Iterable[str],
    ) -> RuntimeCheckpoint:
        checkpoint = cls(
            checkpoint_version=CHECKPOINT_VERSION,
            sequence=sequence,
            session_id=session_id,
            client_id=client_id,
            capability_level=capability_level,
            events=tuple(events),
            trace=tuple(trace),
            pending_context=tuple(sorted(pending_context, key=lambda item: item[0])),
            delivered_context=tuple(sorted(delivered_context, key=lambda item: item[0])),
            started_generations=frozenset(started_generations),
            integrity_digest="",
            idempotency_key="",
        )
        digest = _checkpoint_digest(checkpoint)
        return replace(
            checkpoint,
            integrity_digest=digest,
            idempotency_key=f"checkpoint:{digest}",
        )

    def validate_integrity(self) -> None:
        """Detect corruption or mismatched state, not provide authentication."""

        if self.checkpoint_version != CHECKPOINT_VERSION:
            raise ReferenceHostError("checkpoint version is unsupported")
        expected_digest = _checkpoint_digest(self)
        if self.integrity_digest != expected_digest:
            raise ReferenceHostError("checkpoint integrity validation failed")
        if self.idempotency_key != f"checkpoint:{expected_digest}":
            raise ReferenceHostError("checkpoint idempotency key is invalid")


# The sink owns durable storage.  Its second argument is an explicit stable
# idempotency key: retrying the same logical checkpoint must not duplicate it.
CheckpointSink = Callable[[RuntimeCheckpoint, str], None]


def _delivery_as_dict(receipt: ContextDeliveryReceipt) -> dict[str, object]:
    return {
        "request_event_id": receipt.request_event_id,
        "generation_id": receipt.generation_id,
        "delivery_id": receipt.delivery_id,
        "delivery_sequence": receipt.delivery_sequence,
        "context_refs": [reference.as_dict() for reference in receipt.context_refs],
        "delivered_before_generation": receipt.delivered_before_generation,
    }


def _checkpoint_payload(checkpoint: RuntimeCheckpoint) -> dict[str, object]:
    """Build the deterministic payload covered by the checkpoint digest."""

    return {
        "checkpoint_version": checkpoint.checkpoint_version,
        "sequence": checkpoint.sequence,
        "session_id": checkpoint.session_id,
        "client_id": checkpoint.client_id,
        "capability_level": checkpoint.capability_level,
        "events": [event.as_dict() for event in checkpoint.events],
        "trace": [
            {
                "sequence": entry.sequence,
                "action": entry.action,
                "reference_id": entry.reference_id,
            }
            for entry in checkpoint.trace
        ],
        "pending_context": [
            {"generation_id": generation_id, "request": request.as_dict()}
            for generation_id, request in checkpoint.pending_context
        ],
        "delivered_context": [
            {"generation_id": generation_id, "receipt": _delivery_as_dict(receipt)}
            for generation_id, receipt in checkpoint.delivered_context
        ],
        "started_generations": sorted(checkpoint.started_generations),
    }


def _checkpoint_digest(checkpoint: RuntimeCheckpoint) -> str:
    """Hash typed checkpoint metadata for deterministic integrity validation."""

    canonical = json.dumps(
        _checkpoint_payload(checkpoint),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    # This detects accidental corruption or state mismatch; it is not trusted
    # authentication and must not be treated as proof of checkpoint provenance.
    return hashlib.sha256(canonical).hexdigest()


class ControlledReferenceHostV0(DeterministicFakeClientRuntimeHost):
    """A thin L0-L2 reference host over ``ClientRuntimeAdapterV0`` primitives."""

    def __init__(
        self,
        *,
        requested_level: CapabilityLevel = "L2",
        transport: ReferenceHostTransport = "in_process_reference",
        client_id: str = "reference-client-v0",
        session_id: str = "reference-session-v0",
        checkpoint_sink: CheckpointSink | None = None,
    ) -> None:
        self._negotiation = negotiate_capabilities(requested_level, transport=transport)
        super().__init__(
            self._negotiation.capabilities,
            client_id=client_id,
            session_id=session_id,
        )
        self._checkpoint_sink = checkpoint_sink
        self._last_checkpoint: RuntimeCheckpoint | None = None

    @classmethod
    def for_level(
        cls,
        level: CapabilityLevel,
        *,
        transport: ReferenceHostTransport = "in_process_reference",
        client_id: str = "reference-client-v0",
        session_id: str = "reference-session-v0",
        checkpoint_sink: CheckpointSink | None = None,
    ) -> ControlledReferenceHostV0:
        return cls(
            requested_level=level,
            transport=transport,
            client_id=client_id,
            session_id=session_id,
            checkpoint_sink=checkpoint_sink,
        )

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: RuntimeCheckpoint,
        *,
        current_session_id: str,
        requested_level: CapabilityLevel = "L2",
        transport: ReferenceHostTransport = "in_process_reference",
        client_id: str = "reference-client-v0",
        checkpoint_sink: CheckpointSink | None = None,
    ) -> ControlledReferenceHostV0:
        """Resume typed runtime state supplied by the external checkpoint seam."""

        host = cls.for_level(
            requested_level,
            transport=transport,
            client_id=client_id,
            session_id=current_session_id,
            checkpoint_sink=checkpoint_sink,
        )
        _validate_checkpoint(
            checkpoint,
            current_session_id=current_session_id,
            client_id=client_id,
            capability_level=host.negotiation.accepted_level,
        )
        host._events[:] = checkpoint.events
        host._trace[:] = checkpoint.trace
        host._pending_context.clear()
        host._pending_context.update(checkpoint.pending_context)
        host._delivered_context.clear()
        host._delivered_context.update(checkpoint.delivered_context)
        host._started_generations.clear()
        host._started_generations.update(checkpoint.started_generations)
        host._sequence = checkpoint.sequence
        host._session_id = checkpoint.session_id
        host._last_checkpoint = checkpoint
        return host

    @property
    def negotiation(self) -> CapabilityNegotiation:
        return self._negotiation

    def checkpoint(self) -> RuntimeCheckpoint | None:
        """Hand typed lifecycle state to the caller-owned persistence seam.

        The last successful snapshot is remembered only after the sink returns.
        If a sink commits and then raises, retrying produces the same snapshot
        and idempotency key so the sink can collapse the retry.
        """

        if self._checkpoint_sink is None:
            return None
        snapshot = self._make_checkpoint()
        if snapshot == self._last_checkpoint:
            return snapshot
        self._checkpoint_sink(snapshot, snapshot.idempotency_key)
        self._last_checkpoint = snapshot
        return snapshot

    def _make_checkpoint(self) -> RuntimeCheckpoint:
        return RuntimeCheckpoint.create(
            sequence=self._sequence,
            session_id=self._session_id,
            client_id=self._client_id,
            capability_level=self._negotiation.accepted_level,
            events=self.events,
            trace=self.trace,
            pending_context=tuple(sorted(self._pending_context.items(), key=lambda item: item[0])),
            delivered_context=tuple(
                sorted(self._delivered_context.items(), key=lambda item: item[0])
            ),
            started_generations=frozenset(self._started_generations),
        )

    def record_checkpoint(
        self,
        *,
        checkpoint_ref: PayloadReference,
        checkpoint_kind: Literal["compaction", "task_checkpoint"],
        checkpoint_state: Literal["created", "completed"] = "completed",
        task_id: str | None = None,
    ) -> HookResult:
        result = super().record_checkpoint(
            checkpoint_ref=checkpoint_ref,
            checkpoint_kind=checkpoint_kind,
            checkpoint_state=checkpoint_state,
            task_id=task_id,
        )
        return self._checkpoint_after_success(result)

    def record_session_transition(
        self,
        *,
        transition: Literal["restart", "session_transition"],
        previous_session_id: str | None = None,
        next_session_id: str | None = None,
    ) -> HookResult:
        result = super().record_session_transition(
            transition=transition,
            previous_session_id=previous_session_id,
            next_session_id=next_session_id,
        )
        return self._checkpoint_after_success(result)

    def record_completion_or_abandonment(
        self,
        *,
        terminal_state: Literal["completed", "abandoned"],
        reason_code: Literal[
            "completed",
            "user_abandoned",
            "host_shutdown",
            "error",
            "unknown",
        ] = "completed",
        task_id: str | None = None,
    ) -> HookResult:
        result = super().record_completion_or_abandonment(
            terminal_state=terminal_state,
            reason_code=reason_code,
            task_id=task_id,
        )
        return self._checkpoint_after_success(result)

    def _checkpoint_after_success(self, result: HookResult) -> HookResult:
        if isinstance(result, ClientLifecycleEnvelope):
            self.checkpoint()
        return result

    def reference_for_content(
        self,
        *,
        reference: str,
        kind: ReferenceKind,
        content: str,
    ) -> PayloadReference:
        """Commit to inert fixture text without retaining the text here."""

        _bounded_text(content)
        _bounded_identifier(reference, label="payload reference")
        if _SECRET_LIKE.search(content):
            raise SecretLikePayloadRefused()
        encoded = content.encode("utf-8")
        return PayloadReference(
            reference=reference,
            kind=kind,
            size_bytes=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
        )

    def observe_direct_user_content(
        self,
        *,
        reference: str,
        content: str,
        conversation_id: str | None = None,
        task_id: str | None = None,
    ) -> ClientLifecycleEnvelope | UnsupportedHookReport:
        return self.observe_direct_user_turn(
            self.reference_for_content(reference=reference, kind="user_turn", content=content),
            conversation_id=conversation_id,
            task_id=task_id,
        )

    def compile_before_generation(
        self,
        compiler: CoreContextCompiler,
        *,
        generation_id: str,
        requested_scopes: Sequence[str] = (),
        budget_chars: int = 4_000,
        conversation_id: str | None = None,
        task_id: str | None = None,
        workspace_id: str | None = None,
        project_id: str | None = None,
        query: str = "",
        principal: ClientPrincipal | None = None,
    ) -> (
        tuple[BootstrapResponse, ContextDeliveryReceipt, GenerationReceipt] | UnsupportedHookReport
    ):
        """Run request → existing Core compiler → delivery → generation."""

        if self.capabilities.supports("pre_generation_context_request") and not isinstance(
            principal, ClientPrincipal
        ):
            # L1+ compilation is authorization-first. Ordinary MCP/L0 still
            # returns UnsupportedHookReport and never reaches retrieval.
            raise MissingCorePrincipal()
        request = self.request_pre_generation_context(
            generation_id=generation_id,
            requested_scopes=requested_scopes,
            budget_chars=budget_chars,
            conversation_id=conversation_id,
            task_id=task_id,
            workspace_id=workspace_id,
            project_id=project_id,
        )
        if isinstance(request, UnsupportedHookReport):
            return request
        if not isinstance(request.payload, ContextRequestPayload):
            raise ReferenceHostError("context request did not use its typed payload")
        if request.payload.generation_id != generation_id:
            raise ReferenceHostError("context request targeted the wrong generation")
        response = compiler(
            BootstrapRequest(
                task_description=query,
                requested_scopes=list(request.payload.requested_scopes),
                current_project=request.project_id,
                character_budget=request.payload.budget_chars,
            ),
            principal,
        )
        if not isinstance(response, BootstrapResponse):
            raise ReferenceHostError("Core compiler did not return BootstrapResponse")
        if not response.items:
            # The v0 base contract requires at least one typed context reference.
            # Fail closed for empty Core context; do not invent a synthetic delivery.
            raise ClientRuntimeContractError(
                "empty Core context cannot be delivered by the v0 host"
            )
        references = tuple(
            self.reference_for_content(
                reference=item.id,
                kind="context_pack",
                content=item.content,
            )
            for item in response.items
        )
        delivery = self.deliver_context(request, references)
        if isinstance(delivery, UnsupportedHookReport):
            return delivery
        generation = self.begin_generation(generation_id=generation_id)
        if not generation.pre_generation_delivery:
            raise OrderingViolation("context was not delivered before generation")
        return response, delivery, generation


def _validate_checkpoint(
    checkpoint: RuntimeCheckpoint,
    *,
    current_session_id: str,
    client_id: str,
    capability_level: CapabilityLevel,
) -> None:
    """Validate checkpoint integrity and runtime ordering before state restore.

    This is deterministic integrity and chain validation over typed state.  It
    is not trusted authentication and does not establish client-to-principal
    binding, which belongs to a separate capability boundary.
    """

    if not isinstance(checkpoint, RuntimeCheckpoint):
        raise ReferenceHostError("checkpoint must use its typed snapshot")
    if type(checkpoint.checkpoint_version) is not str:
        raise ReferenceHostError("checkpoint version is not typed")
    if type(checkpoint.sequence) is not int or checkpoint.sequence < 0:
        raise ReferenceHostError("checkpoint sequence is invalid")
    if type(checkpoint.session_id) is not str or type(checkpoint.client_id) is not str:
        raise ReferenceHostError("checkpoint identity fields are not typed")
    _bounded_identifier(checkpoint.session_id, label="checkpoint session ID")
    _bounded_identifier(checkpoint.client_id, label="checkpoint client ID")
    if checkpoint.client_id != client_id:
        raise ReferenceHostError("checkpoint client identity does not match the host")
    if checkpoint.session_id != current_session_id:
        raise ReferenceHostError("checkpoint session does not match saved state")
    if (
        type(checkpoint.capability_level) is not str
        or checkpoint.capability_level not in _LEVEL_ORDER
    ):
        raise ReferenceHostError("checkpoint capability level is invalid")
    if checkpoint.capability_level != capability_level:
        raise ReferenceHostError("checkpoint capability does not match the host")
    if type(checkpoint.integrity_digest) is not str or type(checkpoint.idempotency_key) is not str:
        raise ReferenceHostError("checkpoint integrity metadata is not typed")
    if type(checkpoint.events) is not tuple:
        raise ReferenceHostError("checkpoint events must be a typed tuple")
    if type(checkpoint.trace) is not tuple:
        raise ReferenceHostError("checkpoint trace must be a typed tuple")
    if type(checkpoint.pending_context) is not tuple:
        raise ReferenceHostError("checkpoint pending context must be a typed tuple")
    if type(checkpoint.delivered_context) is not tuple:
        raise ReferenceHostError("checkpoint delivered context must be a typed tuple")
    if type(checkpoint.started_generations) is not frozenset:
        raise ReferenceHostError("checkpoint started generations must be a typed set")
    if any(not isinstance(event, ClientLifecycleEnvelope) for event in checkpoint.events):
        raise ReferenceHostError("checkpoint events must contain typed lifecycle envelopes")
    if any(not isinstance(entry, HostTraceEntry) for entry in checkpoint.trace):
        raise ReferenceHostError("checkpoint trace must contain typed entries")
    if any(
        type(entry) is not tuple
        or len(entry) != 2
        or type(entry[0]) is not str
        or not isinstance(entry[1], ClientLifecycleEnvelope)
        for entry in checkpoint.pending_context
    ):
        raise ReferenceHostError("checkpoint pending context is not typed")
    if any(
        type(entry) is not tuple
        or len(entry) != 2
        or type(entry[0]) is not str
        or not isinstance(entry[1], ContextDeliveryReceipt)
        for entry in checkpoint.delivered_context
    ):
        raise ReferenceHostError("checkpoint delivered context is not typed")
    if any(type(generation_id) is not str for generation_id in checkpoint.started_generations):
        raise ReferenceHostError("checkpoint generation IDs are not typed")

    # The digest detects accidental corruption or a mismatched typed snapshot;
    # it is integrity validation, not trusted authentication.
    checkpoint.validate_integrity()

    events = checkpoint.events
    event_ids = [event.event_id for event in events]
    if len(event_ids) != len(set(event_ids)):
        raise ReferenceHostError("checkpoint event IDs are not unique")
    if any(event.client_id != client_id for event in events):
        raise ReferenceHostError("checkpoint event client identity does not match the host")
    if any(left.sequence >= right.sequence for left, right in pairwise(events)):
        raise ReferenceHostError("checkpoint event sequence is not ordered")
    if any(event.sequence > checkpoint.sequence for event in events):
        raise ReferenceHostError("checkpoint event sequence exceeds runtime sequence")
    events_by_id = {event.event_id: event for event in events}

    active_session: str | None = None
    for event in events:
        if active_session is None:
            active_session = event.session_id
        if event.session_id != active_session:
            raise ReferenceHostError("checkpoint session chain is not ordered")
        if event.hook == "restart_session_transition":
            if not isinstance(event.payload, RestartSessionTransitionPayload):
                raise ReferenceHostError("checkpoint transition payload is not typed")
            if (
                event.payload.previous_session_id is not None
                and event.payload.previous_session_id != event.session_id
            ):
                raise ReferenceHostError("checkpoint transition previous session is invalid")
            if event.payload.next_session_id is not None:
                active_session = event.payload.next_session_id
    if active_session is not None and active_session != current_session_id:
        raise ReferenceHostError("checkpoint session does not match its envelope chain")

    pending: dict[str, ClientLifecycleEnvelope] = {}
    for generation_id, request in checkpoint.pending_context:
        if generation_id in pending:
            raise ReferenceHostError("checkpoint has duplicate pending generations")
        if request.event_id not in events_by_id or events_by_id[request.event_id] != request:
            raise ReferenceHostError("checkpoint pending request is not in the envelope chain")
        if request.hook != "pre_generation_context_request" or not isinstance(
            request.payload, ContextRequestPayload
        ):
            raise ReferenceHostError("checkpoint pending request has the wrong typed hook")
        if request.payload.generation_id != generation_id:
            raise ReferenceHostError("checkpoint pending request targets the wrong generation")
        pending[generation_id] = request

    delivered: dict[str, ContextDeliveryReceipt] = {}
    for generation_id, receipt in checkpoint.delivered_context:
        if generation_id in delivered:
            raise ReferenceHostError("checkpoint has duplicate delivered generations")
        pending_request = pending.get(generation_id)
        if pending_request is None or receipt.request_event_id != pending_request.event_id:
            raise ReferenceHostError("checkpoint delivery does not match a pending request")
        if receipt.delivery_sequence <= pending_request.sequence:
            raise ReferenceHostError("checkpoint delivery sequence is not after its request")
        if receipt.delivery_sequence > checkpoint.sequence:
            raise ReferenceHostError("checkpoint delivery sequence exceeds runtime sequence")
        delivered[generation_id] = receipt

    started = checkpoint.started_generations
    if capability_level == "L0":
        if pending or delivered:
            raise ReferenceHostError("L0 checkpoint cannot contain context delivery state")
    elif not set(started).issubset(pending) or not set(started).issubset(delivered):
        raise ReferenceHostError("checkpoint started generation lacks delivered context state")

    trace = checkpoint.trace
    if any(left.sequence >= right.sequence for left, right in pairwise(trace)):
        raise ReferenceHostError("checkpoint trace sequence is not ordered")
    if any(entry.sequence > checkpoint.sequence for entry in trace):
        raise ReferenceHostError("checkpoint trace sequence exceeds runtime sequence")
    trace_started = [entry.reference_id for entry in trace if entry.action == "generation_started"]
    if len(trace_started) != len(set(trace_started)) or set(trace_started) != set(started):
        raise ReferenceHostError("checkpoint generation trace does not match runtime state")
    trace_deliveries = [
        entry.reference_id for entry in trace if entry.action == "context_delivered"
    ]
    delivery_ids = [receipt.delivery_id for receipt in delivered.values()]
    if len(trace_deliveries) != len(set(trace_deliveries)) or set(trace_deliveries) != set(
        delivery_ids
    ):
        raise ReferenceHostError("checkpoint delivery trace does not match runtime state")
    for entry in trace:
        if entry.action == "context_delivered":
            trace_receipt = next(
                (item for item in delivered.values() if item.delivery_id == entry.reference_id),
                None,
            )
            if trace_receipt is None or trace_receipt.delivery_sequence != entry.sequence:
                raise ReferenceHostError("checkpoint delivery trace sequence is invalid")


def _literal(value: object, allowed: set[str], label: str) -> None:
    if type(value) is not str or value not in allowed:
        raise ClientRuntimeContractError(f"{label} is not a supported literal")


def _bounded_identifier(value: str, *, label: str) -> None:
    if type(value) is not str or not value or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ClientRuntimeContractError(f"{label} is outside its bound")


def _bounded_text(value: str) -> None:
    if type(value) is not str or not value or len(value) > 128_000 or _CONTROL.search(value):
        raise ClientRuntimeContractError("payload text is outside its bound")


ReferenceHostV0 = ControlledReferenceHostV0
ReferenceHost = ControlledReferenceHostV0

__all__ = [
    "CHECKPOINT_VERSION",
    "REFERENCE_HOST_MAX_LEVEL",
    "CapabilityNegotiation",
    "CheckpointSink",
    "ControlledReferenceHostV0",
    "CoreContextCompiler",
    "MissingCorePrincipal",
    "ReferenceHost",
    "ReferenceHostError",
    "ReferenceHostTransport",
    "ReferenceHostV0",
    "RuntimeCheckpoint",
    "SecretLikePayloadRefused",
    "negotiate_capabilities",
]
