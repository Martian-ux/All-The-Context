"""Small controlled in-process host for the experimental runtime contract.

This is ZF-009 evidence code, not a provider integration or SDK.  The host
reuses the existing v0 runtime host for ordering and typed envelopes.  Its
only additions are a Core compiler seam, an injected checkpoint sink, and a
safe way for sanitized fixtures to turn inert text into untrusted references.
It does not own lifecycle storage, retrieval, or canonical memory.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
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
    OrderingViolation,
    PayloadReference,
    ReferenceKind,
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


class CapabilityNegotiationError(ReferenceHostError):
    """Raised when a requested level would overstate the host or transport."""


class SecretLikePayloadRefused(ReferenceHostError):
    """Raised without putting secret-like content in a lifecycle envelope."""

    def __init__(self, *, reference: str) -> None:
        super().__init__("secret-like payload refused before lifecycle persistence")
        self.reference = reference
        self.reason_code = "direct_secret_like_content"


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


CheckpointSink = Callable[[tuple[ClientLifecycleEnvelope, ...], str], None]


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
        events: Sequence[ClientLifecycleEnvelope],
        *,
        current_session_id: str,
        requested_level: CapabilityLevel = "L2",
        transport: ReferenceHostTransport = "in_process_reference",
        client_id: str = "reference-client-v0",
        checkpoint_sink: CheckpointSink | None = None,
    ) -> ControlledReferenceHostV0:
        """Resume from typed envelopes supplied by an external checkpoint seam."""

        host = cls.for_level(
            requested_level,
            transport=transport,
            client_id=client_id,
            session_id=current_session_id,
            checkpoint_sink=checkpoint_sink,
        )
        restored = tuple(events)
        if any(not isinstance(event, ClientLifecycleEnvelope) for event in restored):
            raise ReferenceHostError("checkpoint must contain typed lifecycle envelopes")
        if restored and any(event.client_id != client_id for event in restored):
            raise ReferenceHostError("checkpoint client identity does not match the host")
        if restored and any(left.sequence >= right.sequence for left, right in pairwise(restored)):
            raise ReferenceHostError("checkpoint event sequence is not ordered")
        host._events[:] = restored
        host._sequence = restored[-1].sequence if restored else 0
        host._session_id = current_session_id
        return host

    @property
    def negotiation(self) -> CapabilityNegotiation:
        return self._negotiation

    def checkpoint(self) -> None:
        """Hand typed lifecycle state to the caller-owned persistence seam."""

        if self._checkpoint_sink is not None:
            self._checkpoint_sink(self.events, self.current_session_id)

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
            raise SecretLikePayloadRefused(reference=reference)
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
    "REFERENCE_HOST_MAX_LEVEL",
    "CapabilityNegotiation",
    "CapabilityNegotiationError",
    "CheckpointSink",
    "ControlledReferenceHostV0",
    "CoreContextCompiler",
    "ReferenceHost",
    "ReferenceHostError",
    "ReferenceHostTransport",
    "ReferenceHostV0",
    "SecretLikePayloadRefused",
    "negotiate_capabilities",
]
