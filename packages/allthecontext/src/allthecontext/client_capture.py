"""Core-owned continuous capture for client runtime integrations.

This route accepts only bounded event content and evidence role. Core derives
source identity, lifecycle witness, sensitivity, ACL, and observation
provenance before the existing formation and memory policy seams are used.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from .capture import CaptureError, CaptureEvent
from .client_runtime import (
    ClientLifecycleEnvelope,
    DirectUserTurnPayload,
    EvidenceWitness,
    LifecycleHook,
    PayloadReference,
    ReferenceKind,
    ResponseEmissionPayload,
    ToolObservableResultPayload,
)
from .experimental_event_observation import (
    AuthorizationApplicability,
    ContentInterpretation,
    EventLineage,
    EventObservationInput,
    EvidenceClass,
    FormationRefusalCode,
    FormationStatus,
    ItemLineage,
    PayloadKind,
    SourceLineage,
    form_observation,
)
from .experimental_event_reconciliation import (
    SensitivityClass,
    normalize_lifecycle_event,
)
from .memory_policy import classify_sensitivity, extract_live_user_claim
from .models import CaptureEventRequest, CaptureRole, Sensitivity
from .secret_boundary import SECRET_REFUSAL_REASON, contains_secret_like_value
from .security import ClientPrincipal
from .storage import CoreStore

CAPTURE_ROUTE = "/v1/lifecycle/events"
_ROLE_TO_KIND = {
    "user": "captured_user_turn",
    "assistant": "captured_assistant_response",
    "tool": "captured_tool_result",
    "imported": "captured_imported_text",
}
_ROLE_TO_REFERENCE_KIND: dict[CaptureRole, ReferenceKind] = {
    "user": "user_turn",
    "assistant": "response",
    "tool": "tool_result",
    "imported": "external_artifact",
}
_ROLE_TO_WITNESS: dict[CaptureRole, EvidenceWitness] = {
    "user": "direct_user",
    "assistant": "model_provider_self_attestation",
    "tool": "host_observation",
    "imported": "system_observation",
}
_ROLE_TO_EVIDENCE = {
    "user": EvidenceClass.DIRECT_ASSERTION,
    "assistant": EvidenceClass.OBSERVED_ARTIFACT,
    "tool": EvidenceClass.OBSERVED_ARTIFACT,
    "imported": EvidenceClass.SOURCE_ITEM,
}
_ROLE_TO_HOOK: dict[CaptureRole, LifecycleHook] = {
    "user": "direct_user_turn",
    "assistant": "response_emission",
    "tool": "tool_observable_result",
    "imported": "tool_observable_result",
}


def _source_id(principal_id: str) -> str:
    return "client-capture-" + hashlib.sha256(principal_id.encode("utf-8")).hexdigest()


def _content_reference(event_id: str, content: str) -> str:
    digest = hashlib.sha256(f"{event_id}\0{content}".encode()).hexdigest()
    return f"client-capture-content-{digest}"


def _observed_at(value: str | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CaptureError("capture_contract_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CaptureError("capture_contract_invalid")
    return parsed.astimezone(UTC)


def _sensitivity_class(value: Sensitivity) -> SensitivityClass:
    return {
        Sensitivity.NORMAL: SensitivityClass.ORDINARY,
        Sensitivity.SENSITIVE: SensitivityClass.SENSITIVE,
        Sensitivity.HIGHLY_SENSITIVE: SensitivityClass.RESTRICTED,
    }[value]


def _payload_reference(request: CaptureEventRequest) -> PayloadReference:
    content = request.content
    reference = _content_reference(request.event_id, content)
    kind = _ROLE_TO_REFERENCE_KIND[request.role]
    return PayloadReference(
        reference=reference,
        kind=kind,
        size_bytes=len(content.encode("utf-8")),
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def _lifecycle_envelope(
    request: CaptureEventRequest,
    principal: ClientPrincipal,
    observed_at: datetime,
) -> ClientLifecycleEnvelope:
    reference = _payload_reference(request)
    if request.role == "user":
        payload: Any = DirectUserTurnPayload(reference)
    elif request.role == "assistant":
        payload = ResponseEmissionPayload(reference)
    else:
        payload = ToolObservableResultPayload(
            tool_name="captured-tool",
            result_ref=reference,
            result_kind="observable_result" if request.role == "imported" else "tool_result",
        )
    hook = _ROLE_TO_HOOK[request.role]
    witness = _ROLE_TO_WITNESS[request.role]
    return ClientLifecycleEnvelope(
        event_id=request.event_id,
        sequence=request.sequence,
        hook=hook,
        session_id=request.session_id,
        client_id=principal.id,
        payload=payload,
        conversation_id=request.conversation_id,
        witness=witness,
        retention_class="bounded",
        observed_at=observed_at.isoformat(),
    )


def _formation_proposal(
    request: CaptureEventRequest,
    principal: ClientPrincipal,
    source_id: str,
    envelope: ClientLifecycleEnvelope,
    observed_at: datetime,
    sensitivity: Sensitivity,
) -> tuple[str, str, Any]:
    try:
        normalized = normalize_lifecycle_event(
            envelope,
            event_time=observed_at,
            observed_time=observed_at,
            sensitivity=_sensitivity_class(sensitivity),
            authorization=AuthorizationApplicability(
                allowed_principals=(
                    frozenset({principal.id}) if sensitivity != Sensitivity.NORMAL else None
                )
            ),
        )
        formation = form_observation(
            EventObservationInput(
                source=SourceLineage(source_id=source_id),
                event=EventLineage(
                    event_id=normalized.event_id,
                    source_id=source_id,
                    sequence=request.sequence,
                ),
                item=ItemLineage(item_id=request.event_id, source_id=source_id),
                witness_class=normalized.witness_class,
                evidence_class=_ROLE_TO_EVIDENCE[request.role],
                retention=normalized.retention,
                authorization=normalized.authorization,
                observed_at=observed_at,
                content=request.content,
                payload_kind=PayloadKind.BOUNDED_INLINE,
                content_interpretation=(
                    ContentInterpretation.INERT_UNTRUSTED_DATA
                    if request.role in {"assistant", "imported"}
                    else ContentInterpretation.EVIDENCE_DATA
                ),
            ),
            as_of=observed_at,
            refusal_ref=f"capture-refusal-{hashlib.sha256(request.idempotency_key.encode()).hexdigest()}",
        )
    except (TypeError, ValueError):
        raise CaptureError("capture_contract_invalid") from None
    if formation.status is FormationStatus.REFUSED:
        assert formation.refusal is not None
        reason = formation.refusal.reason_code
        if reason is FormationRefusalCode.SECRET_LIKE_CONTENT:
            raise CaptureError("capture_payload_rejected")
        raise CaptureError("capture_contract_invalid")
    if formation.proposal is None or formation.proposal.content is None:
        raise CaptureError("capture_contract_invalid")
    return (
        normalized.witness_class.value,
        normalized.lifecycle_hook or "unknown",
        formation.proposal,
    )


class CoreCaptureService:
    """Authenticated Core boundary for continuous client-runtime evidence."""

    def __init__(self, store: CoreStore) -> None:
        self.store = store

    def capture(self, request: CaptureEventRequest, principal: ClientPrincipal) -> dict[str, Any]:
        if not isinstance(principal, ClientPrincipal):
            raise CaptureError("capture_contract_invalid")
        if contains_secret_like_value(request.content):
            receipt = self.store.refuse_direct_value(
                request.content,
                route=CAPTURE_ROUTE,
                operation_id=request.idempotency_key,
                client=principal,
            )
            return {
                "ok": False,
                "status": "refused",
                "reason": SECRET_REFUSAL_REASON,
                "refusal_id": receipt.id if receipt is not None else None,
                "replayed": bool(receipt and receipt.replayed),
            }

        observed_at = _observed_at(request.observed_at)
        sensitivity = classify_sensitivity(request.content)
        source_id = _source_id(principal.id)
        envelope = _lifecycle_envelope(request, principal, observed_at)
        witness, hook, _ = _formation_proposal(
            request,
            principal,
            source_id,
            envelope,
            observed_at,
            sensitivity,
        )
        event_payload: dict[str, Any] = {
            "conversation_id": request.conversation_id,
            "content": request.content,
            "hook": hook,
            "role": request.role,
            "session_id": request.session_id,
            "witness": witness,
        }
        event = CaptureEvent(
            provider_event_id=request.event_id,
            provider_item_id=request.event_id,
            order_key=str(request.sequence),
            payload=event_payload,
        )
        candidate = self.store.build_client_capture_candidate(
            event=event,
            request=request,
            sensitivity=sensitivity,
            principal=principal,
            source_id=source_id,
            observed_at=observed_at.isoformat(),
        )
        formed_candidate = None
        if request.role == "user":
            claim = extract_live_user_claim(request.content)
            if claim is not None:
                formed_candidate = self.store.build_live_user_candidate(
                    request=request,
                    claim=claim,
                    sensitivity=sensitivity,
                    principal=principal,
                    source_id=source_id,
                    observed_at=observed_at.isoformat(),
                )
        created, replayed, durable_event_id = self.store.record_client_capture(
            event=event,
            candidate=candidate,
            principal=principal,
            formed_candidate=formed_candidate,
        )
        return {
            "ok": True,
            "status": "replayed" if replayed else "captured",
            "capture_event_id": durable_event_id,
            "observation_id": created.id,
        }


__all__ = ["CAPTURE_ROUTE", "CoreCaptureService"]
