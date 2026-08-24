"""Small Packet G direct-user formation mapper over existing Core contracts.

This is local composition evidence for one conservative ZF-010 class, not a
product runtime. It maps only accepted in-process Packet G L1+
``direct_user_turn`` envelopes through ``normalize_lifecycle_event``,
``form_observation``, and authenticated ``add_candidate``. It does not own
canonical records, infer kind, scan event logs, or compile context.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, cast

from .client_runtime import (
    MAX_REFERENCE_BYTES,
    ClientLifecycleEnvelope,
    DirectUserTurnPayload,
    UnsupportedHookReport,
)
from .experimental_event_observation import (
    MAX_CONTENT_CHARS,
    MAX_REFERENCE_CHARS,
    AuthorizationApplicability,
    ContentInterpretation,
    EventLineage,
    EventObservationInput,
    EvidenceClass,
    FormationRefusalCode,
    FormationStatus,
    ItemLineage,
    PayloadKind,
    RetentionClass,
    SourceLineage,
    WitnessClass,
    form_observation,
)
from .experimental_event_observation import (
    ObservationDisposition as FormationDisposition,
)
from .experimental_event_reconciliation import normalize_lifecycle_event
from .experimental_reference_host import ControlledReferenceHostV0
from .models import CandidateInput, CandidateOut, SecretRefusalOut
from .secret_boundary import contains_direct_secret, contains_secret_like_text
from .security import ClientPrincipal
from .storage import CoreStore

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_ACCEPTED_LEVELS = frozenset({"L1", "L2"})
DIRECT_USER_FORMATION_KINDS = frozenset({"interaction_preference", "correction", "context_forget"})
_KINDS_REQUIRING_SUPERSEDES = frozenset({"correction", "context_forget"})
FORMATION_ROUTE = "reference-host-direct-user-formation"


class DirectUserFormationError(ValueError):
    """Content-free contract failure before Core candidate admission."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class DirectUserFormationRefusalCode(StrEnum):
    SECRET_LIKE_CONTENT = "secret_like_content"
    RETENTION_EXPIRED = "retention_expired"
    CONTENT_OVER_BOUND = "content_over_bound"


@dataclass(frozen=True, slots=True)
class DirectUserFormationRefusal:
    """Content-free mapper refusal; secret receipts never include payload text."""

    reason_code: str
    secret_receipt: SecretRefusalOut | None = None


@dataclass(frozen=True, slots=True)
class DirectUserFormationResult:
    """Exactly one of an admitted candidate or a content-free refusal."""

    status: Literal["formed", "refused"]
    candidate: CandidateOut | None = None
    refusal: DirectUserFormationRefusal | None = None

    def __post_init__(self) -> None:
        if self.status == "formed":
            if self.candidate is None or self.refusal is not None:
                raise DirectUserFormationError("invalid_field")
            return
        if self.status != "refused" or self.candidate is not None or self.refusal is None:
            raise DirectUserFormationError("invalid_field")


def form_direct_user_turn(
    store: CoreStore,
    host: ControlledReferenceHostV0,
    envelope: object,
    *,
    principal: ClientPrincipal | None,
    content: str,
    kind: str,
    supersedes: str | None = None,
    scopes: Sequence[str] = (),
    allowed_clients: Sequence[str] = (),
    denied_clients: Sequence[str] = (),
    entity_key: str | None = None,
    attribute_key: str | None = None,
) -> DirectUserFormationResult:
    """Admit one caller-declared direct-user claim, correction, or forget."""

    _require_store(store)
    _require_accepted_host(host)
    accepted = _require_accepted_direct_user_envelope(host, envelope)
    durable = _require_durable_principal(store, principal, accepted)
    declared_kind = _require_declared_kind(kind, supersedes)
    _require_optional_slot(entity_key, attribute_key)
    _require_content_commitment(accepted, content)
    authorization = _caller_authorization(allowed_clients, denied_clients)
    observed_at = _observed_at(accepted)
    normalized = normalize_lifecycle_event(
        accepted,
        client_ref=durable.id,
        conversation_ref=accepted.conversation_id,
        task_ref=accepted.task_id,
        workspace_ref=accepted.workspace_id,
        project_ref=accepted.project_id,
        event_time=observed_at,
        observed_time=observed_at,
        authorization=authorization,
    )
    if (
        accepted.retention_class == "ephemeral"
        or normalized.retention.retention_class is RetentionClass.SESSION
    ):
        raise DirectUserFormationError("ephemeral_retention")
    source_id = f"lifecycle:{durable.id}"
    payload = accepted.payload
    if type(payload) is not DirectUserTurnPayload:
        raise DirectUserFormationError("unsupported_hook")
    turn_ref = payload.turn_ref
    formation = form_observation(
        EventObservationInput(
            source=SourceLineage(source_id=source_id),
            event=EventLineage(
                event_id=normalized.event_id,
                source_id=source_id,
                sequence=cast(int, normalized.sequence),
            ),
            item=ItemLineage(item_id=turn_ref.reference, source_id=source_id),
            witness_class=WitnessClass.DIRECT_USER,
            evidence_class=EvidenceClass.DIRECT_ASSERTION,
            retention=normalized.retention,
            authorization=normalized.authorization,
            observed_at=cast(datetime, normalized.observed_time),
            content=content,
            payload_kind=PayloadKind.BOUNDED_INLINE,
            content_interpretation=ContentInterpretation.EVIDENCE_DATA,
            disposition=FormationDisposition.TENTATIVE,
        ),
        as_of=normalized.observed_time,
        refusal_ref=f"formation-{accepted.event_id}",
    )
    if formation.status is FormationStatus.REFUSED and formation.refusal is not None:
        return _content_free_refusal(
            store,
            durable,
            content=content,
            reason=_formation_refusal_code(formation.refusal.reason_code),
            idempotency_key=_idempotency_key(normalized.idempotency_material),
        )
    if not formation.accepted or formation.proposal is None or formation.proposal.content is None:
        raise DirectUserFormationError("invalid_field")
    candidate = CandidateInput(
        kind=declared_kind,
        content=formation.proposal.content,
        entity_key=entity_key,
        attribute_key=attribute_key,
        scopes=list(scopes),
        source_id=None,
        source_reference=turn_ref.reference,
        supersedes=supersedes,
        explicit_user_statement=True,
        confidence=1.0,
        allowed_clients=list(allowed_clients),
        denied_clients=list(denied_clients),
        idempotency_key=_idempotency_key(normalized.idempotency_material),
    )
    if contains_direct_secret(candidate):
        receipt = store.refuse_direct_candidate(
            candidate,
            route=FORMATION_ROUTE,
            client=durable,
        )
        return DirectUserFormationResult(
            status="refused",
            refusal=DirectUserFormationRefusal(
                reason_code=DirectUserFormationRefusalCode.SECRET_LIKE_CONTENT.value,
                secret_receipt=receipt,
            ),
        )
    created = store.add_candidate(candidate, client=durable)
    return DirectUserFormationResult(status="formed", candidate=created)


def _require_store(store: object) -> None:
    if not isinstance(store, CoreStore):
        raise DirectUserFormationError("invalid_field")


def _require_accepted_host(host: object) -> None:
    if not isinstance(host, ControlledReferenceHostV0):
        raise DirectUserFormationError("envelope_not_accepted")
    negotiation = host.negotiation
    if negotiation.transport == "ordinary_mcp":
        raise DirectUserFormationError("ordinary_mcp_is_l0")
    if negotiation.transport != "in_process_reference":
        raise DirectUserFormationError("envelope_not_accepted")
    if negotiation.accepted_level not in _ACCEPTED_LEVELS:
        raise DirectUserFormationError("capability_not_accepted")


def _require_accepted_direct_user_envelope(
    host: ControlledReferenceHostV0,
    envelope: object,
) -> ClientLifecycleEnvelope:
    if isinstance(envelope, UnsupportedHookReport):
        raise DirectUserFormationError("unsupported_hook")
    if type(envelope) is not ClientLifecycleEnvelope:
        raise DirectUserFormationError("envelope_not_accepted")
    accepted = envelope
    if not any(item is accepted for item in host.events):
        raise DirectUserFormationError("envelope_not_accepted")
    if accepted.hook != "direct_user_turn":
        raise DirectUserFormationError("unsupported_hook")
    if accepted.witness != "direct_user":
        raise DirectUserFormationError("unsupported_hook")
    if type(accepted.payload) is not DirectUserTurnPayload:
        raise DirectUserFormationError("unsupported_hook")
    return accepted


def _require_durable_principal(
    store: CoreStore,
    principal: ClientPrincipal | None,
    envelope: ClientLifecycleEnvelope,
) -> ClientPrincipal:
    if not isinstance(principal, ClientPrincipal):
        raise DirectUserFormationError("missing_core_principal")
    if envelope.client_id != principal.id:
        raise DirectUserFormationError("principal_client_mismatch")
    registered = next(
        (item for item in store.list_clients() if item["id"] == principal.id),
        None,
    )
    if registered is None or bool(registered.get("revoked")):
        raise DirectUserFormationError("missing_core_principal")
    return principal


def _require_declared_kind(kind: object, supersedes: object) -> str:
    if type(kind) is not str:
        raise DirectUserFormationError("undeclared_kind")
    declared = kind.strip()
    if declared not in DIRECT_USER_FORMATION_KINDS:
        raise DirectUserFormationError("undeclared_kind")
    if declared in _KINDS_REQUIRING_SUPERSEDES and (
        type(supersedes) is not str or not supersedes.strip()
    ):
        raise DirectUserFormationError("missing_supersedes")
    return declared


def _require_optional_slot(entity_key: object, attribute_key: object) -> None:
    if (entity_key is None) != (attribute_key is None):
        raise DirectUserFormationError("invalid_field")
    for value in (entity_key, attribute_key):
        if value is not None and (type(value) is not str or not value.strip()):
            raise DirectUserFormationError("invalid_field")


def _require_content_commitment(envelope: ClientLifecycleEnvelope, content: object) -> None:
    if type(content) is not str:
        raise DirectUserFormationError("invalid_field")
    if not content.strip() or _CONTROL.search(content) is not None:
        raise DirectUserFormationError("invalid_field")
    encoded = content.encode("utf-8")
    if len(content) > MAX_CONTENT_CHARS or len(encoded) > MAX_REFERENCE_BYTES:
        raise DirectUserFormationError(DirectUserFormationRefusalCode.CONTENT_OVER_BOUND.value)
    payload = envelope.payload
    if type(payload) is not DirectUserTurnPayload:
        raise DirectUserFormationError("unsupported_hook")
    turn_ref = payload.turn_ref
    if (
        type(turn_ref.reference) is not str
        or not turn_ref.reference
        or len(turn_ref.reference) > MAX_REFERENCE_CHARS
        or turn_ref.kind != "user_turn"
        or turn_ref.untrusted is not True
        or turn_ref.size_bytes != len(encoded)
        or turn_ref.sha256 != hashlib.sha256(encoded).hexdigest()
    ):
        raise DirectUserFormationError("commitment_mismatch")


def _caller_authorization(
    allowed_clients: Sequence[str],
    denied_clients: Sequence[str],
) -> AuthorizationApplicability:
    if isinstance(allowed_clients, (str, bytes)) or isinstance(denied_clients, (str, bytes)):
        raise DirectUserFormationError("invalid_field")
    allowed = tuple(allowed_clients)
    denied = tuple(denied_clients)
    if any(type(item) is not str or not item.strip() for item in (*allowed, *denied)):
        raise DirectUserFormationError("invalid_field")
    return AuthorizationApplicability(
        allowed_principals=frozenset(allowed) if allowed else None,
        denied_principals=frozenset(denied),
    )


def _observed_at(envelope: ClientLifecycleEnvelope) -> datetime:
    if envelope.observed_at is None:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(envelope.observed_at.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DirectUserFormationError("invalid_field")
    return parsed.astimezone(UTC)


def _idempotency_key(material: tuple[str | int, ...]) -> str:
    if len(material) != 3:
        raise DirectUserFormationError("invalid_field")
    client_id, event_id, sequence = material
    return f"direct-user:{client_id}:{event_id}:{sequence}"


def _formation_refusal_code(code: FormationRefusalCode) -> str:
    if code is FormationRefusalCode.SECRET_LIKE_CONTENT:
        return DirectUserFormationRefusalCode.SECRET_LIKE_CONTENT.value
    if code is FormationRefusalCode.RETENTION_EXPIRED:
        return DirectUserFormationRefusalCode.RETENTION_EXPIRED.value
    raise DirectUserFormationError("invalid_field")


def _content_free_refusal(
    store: CoreStore,
    principal: ClientPrincipal,
    *,
    content: str,
    reason: str,
    idempotency_key: str,
) -> DirectUserFormationResult:
    receipt = None
    if (
        reason == DirectUserFormationRefusalCode.SECRET_LIKE_CONTENT.value
        or contains_secret_like_text(content)
    ):
        receipt = store.refuse_direct_value(
            content,
            route=FORMATION_ROUTE,
            operation_id=idempotency_key,
            client=principal,
        )
    return DirectUserFormationResult(
        status="refused",
        refusal=DirectUserFormationRefusal(reason_code=reason, secret_receipt=receipt),
    )


__all__ = [
    "DIRECT_USER_FORMATION_KINDS",
    "DirectUserFormationError",
    "DirectUserFormationRefusal",
    "DirectUserFormationRefusalCode",
    "DirectUserFormationResult",
    "form_direct_user_turn",
]
