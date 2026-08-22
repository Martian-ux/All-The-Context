"""Pure Wave 1 reconciliation of bounded capture/lifecycle metadata.

Exactly one existing event is accepted at a time. This seam retains bounded
identifiers, ordering material, timestamps, commitments, and references only;
it never retains source text or mutates source, cursor, observation, or Core.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, NoReturn, cast

from .capture import MAX_CAPTURE_INTEGER, MAX_CURSOR_CHARS, CaptureEvent
from .client_runtime import (
    ALL_LIFECYCLE_HOOKS,
    CONTRACT_VERSION,
    ClientLifecycleEnvelope,
    CompactionTaskCheckpointPayload,
    CompletionAbandonmentPayload,
    ConsequenceCheckpointPayload,
    ContextRequestPayload,
    DirectUserTurnPayload,
    PayloadReference,
    ResponseEmissionPayload,
    RestartSessionTransitionPayload,
    ToolObservableResultPayload,
)
from .experimental_event_observation import (
    AuthorizationApplicability,
    RetentionClass,
    RetentionPolicy,
    WitnessClass,
)
from .experimental_projection_contract import InvalidationAction, InvalidationCause

MAX_GENERIC_REFERENCE_CHARS = 256
MAX_ARTIFACT_REFERENCES = 32
MAX_DEPENDENCY_WITHDRAWALS = 32
MAX_PRODUCER_VERSION_CHARS = 128
MAX_PAYLOAD_BYTES = 64 * 1024
CAPTURE_CONTRACT_VERSION = "capture-event-v0"
RECONCILIATION_SCHEMA_VERSION = "event-reconciliation-v0"
_REFERENCE_KINDS = {
    "context_pack",
    "user_turn",
    "tool_result",
    "response",
    "working_checkpoint",
    "outcome",
    "attestation",
    "external_artifact",
}
_HOOK_REFERENCE_KINDS = {
    "direct_user_turn": {"user_turn"},
    "tool_observable_result": {"tool_result", "external_artifact"},
    "response_emission": {"response"},
    "compaction_task_checkpoint": {"working_checkpoint"},
    "consequence_checkpoint": {
        "context_pack",
        "user_turn",
        "tool_result",
        "response",
        "outcome",
        "external_artifact",
    },
}
_PAYLOAD_REFERENCE_FIELDS = {
    "direct_user_turn": "turn_ref",
    "tool_observable_result": "result_ref",
    "response_emission": "response_ref",
    "compaction_task_checkpoint": "checkpoint_ref",
    "consequence_checkpoint": "evidence_ref",
}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?i)(bearer\s+|basic\s+|\bsk-[a-z0-9]|\bgh[pousr]_[a-z0-9]|\bAIza[a-z0-9]|"
    r"(?:api[_ -]?key|access[_ -]?token|password|credential|secret|token)\s*[:=])"
)


class ReconciliationErrorCode(StrEnum):
    INVALID_FIELD, EMPTY_INPUT = "invalid_field", "empty_input"
    INVALID_CAPTURE_EVENT, INVALID_CAPTURE_OPERATION = (
        "invalid_capture_event",
        "invalid_capture_operation",
    )
    INVALID_CAPTURE_GENERATION, CAPTURE_NORMALIZATION_REJECTED = (
        "invalid_capture_generation",
        "capture_normalization_rejected",
    )
    INVALID_LIFECYCLE_ENVELOPE, INVALID_LIFECYCLE_PAYLOAD = (
        "invalid_lifecycle_envelope",
        "invalid_lifecycle_payload",
    )
    INVALID_LINEAGE, INVALID_TIMESTAMP = "invalid_lineage", "invalid_timestamp"
    INVALID_RETENTION, INVALID_SENSITIVITY = "invalid_retention", "invalid_sensitivity"
    SECRET_LIKE_METADATA, INVALID_WITHDRAWAL = "secret_like_metadata", "invalid_withdrawal"
    PURGE_REQUIRES_ERASE, DELETE_WITHDRAWAL_REQUIRED = (
        "purge_requires_erase",
        "delete_withdrawal_required",
    )
    DELETE_WITHDRAWAL_MISMATCH, DUPLICATE_REFERENCE = (
        "delete_withdrawal_mismatch",
        "duplicate_reference",
    )
    UNLINKED_COMPOSITION = "unlinked_composition"


class ReconciliationViolation(ValueError):
    """A bounded error whose message contains only a stable code."""

    def __init__(self, code: ReconciliationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class EventOrigin(StrEnum):
    CAPTURE = "capture"
    CLIENT_LIFECYCLE = "client_lifecycle"


class SensitivityClass(StrEnum):
    ORDINARY = "ordinary"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


def _fail(code: ReconciliationErrorCode) -> NoReturn:
    raise ReconciliationViolation(code)


def _reference(value: object, *, maximum: int = MAX_GENERIC_REFERENCE_CHARS) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > maximum
        or value != value.strip()
        or _CONTROL.search(value)
    ):
        _fail(ReconciliationErrorCode.INVALID_FIELD)
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = "".join(
        c for c in normalized if unicodedata.category(c) not in {"Cf", "Mn", "Mc", "Me"}
    )
    if _SECRET.search(normalized):
        _fail(ReconciliationErrorCode.SECRET_LIKE_METADATA)
    return value


def _optional_reference(value: object, *, maximum: int = MAX_GENERIC_REFERENCE_CHARS) -> str | None:
    return None if value is None else _reference(value, maximum=maximum)


def _timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif type(value) is str and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            _fail(ReconciliationErrorCode.INVALID_TIMESTAMP)
    else:
        _fail(ReconciliationErrorCode.INVALID_TIMESTAMP)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(ReconciliationErrorCode.INVALID_TIMESTAMP)
    return parsed.astimezone(UTC)


def _required_times(event_time: object, observed_time: object) -> tuple[datetime, datetime]:
    event, observed = _timestamp(event_time), _timestamp(observed_time)
    if event is None or observed is None:
        _fail(ReconciliationErrorCode.INVALID_TIMESTAMP)
    return event, observed


def _sequence(value: object, *, allow_zero: bool = False) -> int | None:
    minimum = 0 if allow_zero else 1
    if value is None:
        return None
    if type(value) is not int or not minimum <= value <= MAX_CAPTURE_INTEGER:
        _fail(ReconciliationErrorCode.INVALID_FIELD)
    return value


def _refs(value: object) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > MAX_ARTIFACT_REFERENCES:
        _fail(ReconciliationErrorCode.INVALID_FIELD)
    refs = tuple(_reference(item) for item in value)
    if len(refs) != len(set(refs)):
        _fail(ReconciliationErrorCode.DUPLICATE_REFERENCE)
    return refs


def _sha(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        _fail(ReconciliationErrorCode.INVALID_FIELD)
    return value


def _size(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int or not 0 <= value <= MAX_PAYLOAD_BYTES:
        _fail(ReconciliationErrorCode.INVALID_FIELD)
    return value


def _retention(value: object) -> RetentionPolicy:
    if type(value) is not RetentionPolicy:
        _fail(ReconciliationErrorCode.INVALID_RETENTION)
    policy = cast(RetentionPolicy, value)
    if type(policy.retention_class) is not RetentionClass:
        _fail(ReconciliationErrorCode.INVALID_RETENTION)
    expires = _timestamp(policy.expires_at)
    if policy.retention_class is RetentionClass.EXPLICIT_EXPIRY and expires is None:
        _fail(ReconciliationErrorCode.INVALID_RETENTION)
    return cast(Any, RetentionPolicy)(policy.retention_class, expires)


def _authorization(value: object) -> AuthorizationApplicability:
    if type(value) is not AuthorizationApplicability:
        _fail(ReconciliationErrorCode.INVALID_FIELD)
    auth = cast(AuthorizationApplicability, value)
    labels = (
        auth.allowed_principals,
        auth.allowed_scopes,
        auth.denied_principals,
        auth.denied_scopes,
    )
    if any(
        value is not None and (type(value) is not frozenset or len(value) > 64) for value in labels
    ):
        _fail(ReconciliationErrorCode.INVALID_FIELD)
    for values in labels:
        for item in values or ():
            _reference(item, maximum=128)
    allowed_principals, allowed_scopes, denied_principals, denied_scopes = labels
    if denied_principals is None or denied_scopes is None:
        _fail(ReconciliationErrorCode.INVALID_FIELD)
    if (allowed_principals and allowed_principals & denied_principals) or (
        allowed_scopes and allowed_scopes & denied_scopes
    ):
        _fail(ReconciliationErrorCode.INVALID_FIELD)
    return cast(Any, AuthorizationApplicability)(*labels)


@dataclass(frozen=True, slots=True)
class DependencyWithdrawal:
    """One bounded, authorized correction/deletion/expiry/purge withdrawal."""

    dependency_ref: str
    cause: InvalidationCause
    authorization_ref: str
    action: InvalidationAction = InvalidationAction.WITHDRAW_AND_REBUILD
    provider_item_id: str | None = None

    def __post_init__(self) -> None:
        _reference(self.dependency_ref)
        _reference(self.authorization_ref)
        if (
            type(self.cause) is not InvalidationCause
            or self.cause
            not in {
                InvalidationCause.CORRECTION,
                InvalidationCause.ORDINARY_DELETE,
                InvalidationCause.RETENTION_EXPIRY,
                InvalidationCause.TERMINAL_PURGE,
            }
            or type(self.action) is not InvalidationAction
        ):
            _fail(ReconciliationErrorCode.INVALID_WITHDRAWAL)
        _optional_reference(self.provider_item_id)
        if (
            self.cause is InvalidationCause.TERMINAL_PURGE
            and self.action is not InvalidationAction.ERASE
        ):
            _fail(ReconciliationErrorCode.PURGE_REQUIRES_ERASE)
        if (
            self.cause is not InvalidationCause.TERMINAL_PURGE
            and self.action is InvalidationAction.ERASE
        ):
            _fail(ReconciliationErrorCode.INVALID_WITHDRAWAL)


def _withdrawals(value: object) -> tuple[DependencyWithdrawal, ...]:
    if type(value) is not tuple or len(value) > MAX_DEPENDENCY_WITHDRAWALS:
        _fail(ReconciliationErrorCode.INVALID_WITHDRAWAL)
    items = tuple(value)
    if any(type(item) is not DependencyWithdrawal for item in items):
        _fail(ReconciliationErrorCode.INVALID_WITHDRAWAL)
    for item in items:
        item.__post_init__()
    refs = tuple(item.dependency_ref for item in items)
    if len(refs) != len(set(refs)):
        _fail(ReconciliationErrorCode.DUPLICATE_REFERENCE)
    return items


def _validate_origin_fields(value: EventReconciliationInput, artifacts: tuple[str, ...]) -> None:
    required = (
        value.source_id,
        value.source_event_id,
        value.source_generation,
        value.provider_item_id,
        value.source_order_key,
        value.idempotency_key,
    )
    source_fields = (
        *required,
        value.source_sequence,
        value.source_cursor,
    )
    if value.origin is EventOrigin.CAPTURE:
        valid = (
            all(item is not None for item in required)
            and value.witness_class is WitnessClass.AUTHORITATIVE_SOURCE
            and type(value.capture_operation) is str
            and value.capture_operation in {"upsert", "delete"}
            and value.lifecycle_hook is None
            and value.session_ref is None
            and value.capture_contract_version == CAPTURE_CONTRACT_VERSION
            and value.lifecycle_event_id is None
            and value.lifecycle_contract_version is None
            and value.payload_reference is None
            and value.payload_reference_kind is None
            and value.payload_commitment is not None
            and type(value.payload_size_bytes) is int
            and value.event_id == value.source_event_id
            and value.provider_item_id in artifacts
            and value.idempotency_material
            == (
                value.idempotency_key,
                value.source_event_id,
                value.source_order_key,
            )
        )
    else:
        payload_valid = (
            value.payload_reference is None
            and all(
                item is None
                for item in (
                    value.payload_reference_kind,
                    value.payload_commitment,
                    value.payload_size_bytes,
                )
            )
        ) or (
            value.payload_reference is not None
            and value.payload_reference in artifacts
            and type(value.lifecycle_hook) is str
            and value.payload_reference_kind
            in _HOOK_REFERENCE_KINDS.get(value.lifecycle_hook, set())
        )
        valid = (
            value.lifecycle_event_id is not None
            and value.client_ref is not None
            and value.sequence is not None
            and type(value.lifecycle_hook) is str
            and value.lifecycle_hook in ALL_LIFECYCLE_HOOKS
            and value.session_ref is not None
            and value.capture_operation is None
            and payload_valid
            and value.lifecycle_contract_version == CONTRACT_VERSION
            and value.capture_contract_version is None
            and not any(item is not None for item in source_fields)
            and value.event_id == value.lifecycle_event_id
            and value.idempotency_material
            == (
                value.client_ref,
                value.lifecycle_event_id,
                value.sequence,
            )
        )
    if not valid:
        _fail(ReconciliationErrorCode.INVALID_LINEAGE)


@dataclass(frozen=True, slots=True)
class EventReconciliationInput:
    """Immutable, content-free metadata supplied to a later formation step."""

    event_id: str
    origin: EventOrigin
    witness_class: WitnessClass
    capture_operation: str | None = None
    lifecycle_hook: str | None = None
    session_ref: str | None = None
    source_id: str | None = None
    source_event_id: str | None = None
    source_generation: int | None = None
    provider_item_id: str | None = None
    lifecycle_event_id: str | None = None
    sequence: int | None = None
    source_sequence: int | None = None
    source_order_key: str | None = None
    source_cursor: str | None = None
    idempotency_key: str | None = None
    idempotency_material: tuple[str | int, ...] = ()
    payload_reference: str | None = None
    payload_reference_kind: str | None = None
    payload_commitment: str | None = None
    payload_size_bytes: int | None = None
    account_ref: str | None = None
    client_ref: str | None = None
    conversation_ref: str | None = None
    task_ref: str | None = None
    workspace_ref: str | None = None
    project_ref: str | None = None
    artifact_refs: tuple[str, ...] = ()
    event_time: datetime | None = None
    observed_time: datetime | None = None
    retention: RetentionPolicy = field(
        default_factory=lambda: RetentionPolicy(RetentionClass.SOURCE_LIFETIME)
    )
    sensitivity: SensitivityClass = SensitivityClass.ORDINARY
    authorization: AuthorizationApplicability = field(default_factory=AuthorizationApplicability)
    content_ownership: str = "external_untrusted"
    schema_version: str = RECONCILIATION_SCHEMA_VERSION
    producer_version: str = RECONCILIATION_SCHEMA_VERSION
    capture_contract_version: str | None = None
    lifecycle_contract_version: str | None = None
    dependency_withdrawals: tuple[DependencyWithdrawal, ...] = ()

    def __post_init__(self) -> None:
        _reference(self.event_id)
        if type(self.origin) is not EventOrigin or type(self.witness_class) is not WitnessClass:
            _fail(ReconciliationErrorCode.INVALID_FIELD)
        for value in (
            self.session_ref,
            self.source_id,
            self.source_event_id,
            self.provider_item_id,
            self.lifecycle_event_id,
            self.account_ref,
            self.client_ref,
            self.conversation_ref,
            self.task_ref,
            self.workspace_ref,
            self.project_ref,
        ):
            _optional_reference(value)
        _optional_reference(self.source_order_key)
        _optional_reference(self.source_cursor, maximum=MAX_CURSOR_CHARS)
        _sequence(self.source_generation, allow_zero=True)
        _sequence(self.sequence)
        _sequence(self.source_sequence)
        if self.origin is EventOrigin.CAPTURE and self.sequence != self.source_sequence:
            _fail(ReconciliationErrorCode.INVALID_LINEAGE)
        _optional_reference(self.idempotency_key)
        if type(self.idempotency_material) is not tuple or len(self.idempotency_material) != 3:
            _fail(ReconciliationErrorCode.INVALID_FIELD)
        for item in self.idempotency_material:
            if type(item) is str:
                _reference(item)
            elif type(item) is int:
                _sequence(item)
            else:
                _fail(ReconciliationErrorCode.INVALID_FIELD)
        _optional_reference(self.payload_reference)
        if self.payload_reference is None:
            if self.payload_reference_kind is not None:
                _fail(ReconciliationErrorCode.INVALID_FIELD)
        elif (
            type(self.payload_reference_kind) is not str
            or self.payload_reference_kind not in _REFERENCE_KINDS
        ):
            _fail(ReconciliationErrorCode.INVALID_FIELD)
        _sha(self.payload_commitment)
        _size(self.payload_size_bytes)
        artifacts = _refs(self.artifact_refs)
        event_time, observed_time = _required_times(self.event_time, self.observed_time)
        object.__setattr__(self, "event_time", event_time)
        object.__setattr__(self, "observed_time", observed_time)
        object.__setattr__(self, "retention", _retention(self.retention))
        if type(self.sensitivity) is not SensitivityClass:
            _fail(ReconciliationErrorCode.INVALID_SENSITIVITY)
        object.__setattr__(self, "authorization", _authorization(self.authorization))
        if (
            type(self.content_ownership) is not str
            or self.content_ownership != "external_untrusted"
        ):
            _fail(ReconciliationErrorCode.INVALID_FIELD)
        if self.schema_version != RECONCILIATION_SCHEMA_VERSION:
            _fail(ReconciliationErrorCode.INVALID_FIELD)
        _reference(self.producer_version, maximum=MAX_PRODUCER_VERSION_CHARS)
        for version in (self.capture_contract_version, self.lifecycle_contract_version):
            _optional_reference(version, maximum=MAX_PRODUCER_VERSION_CHARS)
        withdrawals = _withdrawals(self.dependency_withdrawals)
        object.__setattr__(self, "artifact_refs", artifacts)
        object.__setattr__(self, "dependency_withdrawals", withdrawals)
        _validate_origin_fields(self, artifacts)

    def as_dict(self) -> dict[str, object]:
        data = asdict(self)
        data.update(
            artifact_refs=list(self.artifact_refs),
            event_time=cast(datetime, self.event_time).isoformat(),
            observed_time=cast(datetime, self.observed_time).isoformat(),
            retention={
                "retention_class": self.retention.retention_class.value,
                "expires_at": self.retention.expires_at.isoformat()
                if self.retention.expires_at
                else None,
            },
            authorization={
                "allowed_principals": sorted(self.authorization.allowed_principals or ()),
                "allowed_scopes": sorted(self.authorization.allowed_scopes or ()),
                "denied_principals": sorted(self.authorization.denied_principals),
                "denied_scopes": sorted(self.authorization.denied_scopes),
            },
            dependency_withdrawals=[
                {
                    "dependency_ref": item.dependency_ref,
                    "cause": item.cause.value,
                    "action": item.action.value,
                    "authorization_ref": item.authorization_ref,
                    "provider_item_id": item.provider_item_id,
                }
                for item in self.dependency_withdrawals
            ],
            idempotency_material=list(self.idempotency_material),
        )
        return data


_PAYLOAD_TYPES = {
    "manual_context_request": ContextRequestPayload,
    "pre_generation_context_request": ContextRequestPayload,
    "direct_user_turn": DirectUserTurnPayload,
    "tool_observable_result": ToolObservableResultPayload,
    "response_emission": ResponseEmissionPayload,
    "compaction_task_checkpoint": CompactionTaskCheckpointPayload,
    "restart_session_transition": RestartSessionTransitionPayload,
    "completion_abandonment": CompletionAbandonmentPayload,
    "consequence_checkpoint": ConsequenceCheckpointPayload,
}


def _payload_reference(value: object, expected: set[str]) -> PayloadReference:
    if type(value) is not PayloadReference:
        _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    reference = cast(PayloadReference, value)
    try:
        _reference(reference.reference)
        if (
            type(reference.kind) is not str
            or reference.kind not in expected
            or reference.untrusted is not True
        ):
            _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
        _size(reference.size_bytes)
        _sha(reference.sha256)
    except ReconciliationViolation:
        _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    return reference


def _revalidated_lifecycle_envelope(
    envelope: ClientLifecycleEnvelope,
) -> ClientLifecycleEnvelope:
    payload = envelope.payload
    attribute = _PAYLOAD_REFERENCE_FIELDS.get(envelope.hook)
    try:
        reference = None if attribute is None else getattr(payload, attribute)
    except Exception:
        _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    if reference is not None and type(reference) is not PayloadReference:
        _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    try:
        validated_payload = replace(cast(Any, payload))
    except Exception:
        _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    try:
        diagnostics = tuple(replace(item) for item in envelope.diagnostics)
        return replace(envelope, payload=validated_payload, diagnostics=diagnostics)
    except Exception:
        _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)


def _validate_lifecycle(
    value: object,
) -> tuple[ClientLifecycleEnvelope, datetime | None, PayloadReference | None]:
    if type(value) is not ClientLifecycleEnvelope:
        _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    envelope = cast(ClientLifecycleEnvelope, value)
    if type(envelope.hook) is not str or envelope.hook not in ALL_LIFECYCLE_HOOKS:
        _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    if type(envelope.payload) is not _PAYLOAD_TYPES[envelope.hook]:
        _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    if envelope.observed_at is not None:
        if type(envelope.observed_at) is not str:
            _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
        _timestamp(envelope.observed_at)
    envelope = _revalidated_lifecycle_envelope(envelope)
    for item in (envelope.event_id, envelope.session_id, envelope.client_id):
        _reference(item)
    for item in (
        envelope.conversation_id,
        envelope.task_id,
        envelope.workspace_id,
        envelope.project_id,
    ):
        _optional_reference(item)
    observed = None if envelope.observed_at is None else _timestamp(envelope.observed_at)
    reference = None
    if envelope.hook in _PAYLOAD_REFERENCE_FIELDS:
        attribute = _PAYLOAD_REFERENCE_FIELDS[envelope.hook]
        reference = _payload_reference(
            getattr(envelope.payload, attribute), _HOOK_REFERENCE_KINDS[envelope.hook]
        )
    elif envelope.hook == "consequence_checkpoint":
        payload = cast(ConsequenceCheckpointPayload, envelope.payload)
        if payload.status == "observed":
            reference = _payload_reference(
                payload.evidence_ref, _HOOK_REFERENCE_KINDS[envelope.hook]
            )
        elif payload.status != "not_observed" or payload.evidence_ref is not None:
            _fail(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    return envelope, observed, reference


def _capture_metadata(value: object) -> tuple[CaptureEvent, str, str, int]:
    if type(value) is not CaptureEvent:
        _fail(ReconciliationErrorCode.INVALID_CAPTURE_EVENT)
    capture = cast(CaptureEvent, value)
    for item in (capture.provider_event_id, capture.provider_item_id, capture.order_key):
        _reference(item)
    if type(capture.operation) is not str or capture.operation not in {"upsert", "delete"}:
        _fail(ReconciliationErrorCode.INVALID_CAPTURE_OPERATION)
    if type(capture.generation) is not int or not 0 <= capture.generation <= MAX_CAPTURE_INTEGER:
        _fail(ReconciliationErrorCode.INVALID_CAPTURE_GENERATION)
    normalizer = getattr(capture, "normalized", None)
    if not callable(normalizer):
        _fail(ReconciliationErrorCode.CAPTURE_NORMALIZATION_REJECTED)
    try:
        normalized = normalizer()
    except Exception:
        _fail(ReconciliationErrorCode.CAPTURE_NORMALIZATION_REJECTED)
    if (
        type(normalized) is not tuple
        or len(normalized) != 2
        or type(normalized[0]) is not str
        or type(normalized[1]) is not str
        or _SHA256.fullmatch(normalized[1]) is None
    ):
        _fail(ReconciliationErrorCode.CAPTURE_NORMALIZATION_REJECTED)
    size = len(normalized[0].encode("utf-8"))
    if size > MAX_PAYLOAD_BYTES:
        _fail(ReconciliationErrorCode.CAPTURE_NORMALIZATION_REJECTED)
    return capture, capture.provider_event_id, normalized[1], size


def _lifecycle_retention(value: str) -> RetentionPolicy:
    try:
        return RetentionPolicy(
            {
                "ephemeral": RetentionClass.SESSION,
                "bounded": RetentionClass.SOURCE_LIFETIME,
                "checkpoint": RetentionClass.USER_CONTROLLED,
            }[value]
        )
    except (KeyError, TypeError):
        _fail(ReconciliationErrorCode.INVALID_RETENTION)


def _merge(current: str | None, incoming: str | None) -> str | None:
    if current is not None and incoming is not None and current != incoming:
        _fail(ReconciliationErrorCode.INVALID_LINEAGE)
    return current if current is not None else incoming


def reconcile_event(
    *,
    capture_event: CaptureEvent | None = None,
    lifecycle_envelope: ClientLifecycleEnvelope | None = None,
    source_id: str | None = None,
    source_cursor: str | None = None,
    source_sequence: int | None = None,
    idempotency_key: str | None = None,
    account_ref: str | None = None,
    client_ref: str | None = None,
    conversation_ref: str | None = None,
    task_ref: str | None = None,
    workspace_ref: str | None = None,
    project_ref: str | None = None,
    artifact_refs: tuple[str, ...] = (),
    event_time: datetime | str | None = None,
    observed_time: datetime | str | None = None,
    retention: RetentionPolicy | None = None,
    sensitivity: SensitivityClass = SensitivityClass.ORDINARY,
    authorization: AuthorizationApplicability | None = None,
    producer_version: str | None = None,
    dependency_withdrawals: tuple[DependencyWithdrawal, ...] = (),
) -> EventReconciliationInput:
    """Normalize one existing capture event or lifecycle envelope."""
    if capture_event is None and lifecycle_envelope is None:
        _fail(ReconciliationErrorCode.EMPTY_INPUT)
    if capture_event is not None and lifecycle_envelope is not None:
        _fail(ReconciliationErrorCode.UNLINKED_COMPOSITION)
    refs, withdrawals = _refs(artifact_refs), _withdrawals(dependency_withdrawals)
    common: dict[str, object] = {
        "account_ref": _optional_reference(account_ref),
        "client_ref": _optional_reference(client_ref),
        "conversation_ref": _optional_reference(conversation_ref),
        "task_ref": _optional_reference(task_ref),
        "workspace_ref": _optional_reference(workspace_ref),
        "project_ref": _optional_reference(project_ref),
        "artifact_refs": refs,
        "sensitivity": sensitivity,
        "authorization": AuthorizationApplicability()
        if authorization is None
        else _authorization(authorization),
        "dependency_withdrawals": withdrawals,
    }
    if type(sensitivity) is not SensitivityClass:
        _fail(ReconciliationErrorCode.INVALID_SENSITIVITY)
    if capture_event is not None:
        capture, source_event, commitment, size = _capture_metadata(capture_event)
        source = _reference(source_id)
        item, order = _reference(capture.provider_item_id), _reference(capture.order_key)
        cursor, source_seq = (
            _optional_reference(source_cursor, maximum=MAX_CURSOR_CHARS),
            _sequence(source_sequence),
        )
        idem = _reference(idempotency_key)
        times = _required_times(event_time, observed_time)
        if capture.operation == "delete" and not any(
            item.cause is InvalidationCause.ORDINARY_DELETE
            and item.provider_item_id == capture.provider_item_id
            for item in withdrawals
        ):
            _fail(ReconciliationErrorCode.DELETE_WITHDRAWAL_REQUIRED)
        if any(
            item.cause is InvalidationCause.ORDINARY_DELETE
            and item.provider_item_id not in {None, capture.provider_item_id}
            for item in withdrawals
        ):
            _fail(ReconciliationErrorCode.DELETE_WITHDRAWAL_MISMATCH)
        retention_value = (
            _retention(retention)
            if retention is not None
            else RetentionPolicy(RetentionClass.SOURCE_LIFETIME)
        )
        common.update(
            event_id=source_event,
            origin=EventOrigin.CAPTURE,
            witness_class=WitnessClass.AUTHORITATIVE_SOURCE,
            capture_operation=capture.operation,
            source_id=source,
            source_event_id=source_event,
            source_generation=capture.generation,
            provider_item_id=item,
            sequence=source_seq,
            source_sequence=source_seq,
            source_order_key=order,
            source_cursor=cursor,
            idempotency_key=idem,
            idempotency_material=(idem, source_event, order),
            payload_commitment=commitment,
            payload_size_bytes=size,
            artifact_refs=(*refs, item),
            event_time=times[0],
            observed_time=times[1],
            retention=retention_value,
            producer_version=producer_version or CAPTURE_CONTRACT_VERSION,
            capture_contract_version=CAPTURE_CONTRACT_VERSION,
        )
    else:
        if any(
            value is not None
            for value in (source_id, source_cursor, source_sequence, idempotency_key)
        ):
            _fail(ReconciliationErrorCode.INVALID_LINEAGE)
        envelope, envelope_time, reference = _validate_lifecycle(lifecycle_envelope)
        client = _merge(cast(str | None, common["client_ref"]), _reference(envelope.client_id))
        context = {
            name: _merge(
                cast(str | None, common[name]),
                _optional_reference(getattr(envelope, name[:-4] + "_id")),
            )
            for name in ("conversation_ref", "task_ref", "workspace_ref", "project_ref")
        }
        if envelope_time is None:
            times = _required_times(event_time, observed_time)
        else:
            supplied = (_timestamp(event_time), _timestamp(observed_time))
            if any(value is not None and value != envelope_time for value in supplied):
                _fail(ReconciliationErrorCode.INVALID_TIMESTAMP)
            times = (envelope_time, envelope_time)
        payload_ref = None if reference is None else reference.reference
        lifecycle_refs = refs if payload_ref is None else (*refs, payload_ref)
        derived = _lifecycle_retention(envelope.retention_class)
        retained = derived if retention is None else _retention(retention)
        if (
            retained.retention_class is not derived.retention_class
            or retained.expires_at is not None
        ):
            _fail(ReconciliationErrorCode.INVALID_RETENTION)
        common.update(
            event_id=_reference(envelope.event_id),
            origin=EventOrigin.CLIENT_LIFECYCLE,
            witness_class={
                "direct_user": WitnessClass.DIRECT_USER,
                "host_observation": WitnessClass.HOST_ARTIFACT,
                "model_provider_self_attestation": WitnessClass.UNTRUSTED_IMPORTED_TEXT,
                "system_observation": WitnessClass.SYSTEM_DERIVATION,
            }[envelope.witness],
            lifecycle_hook=envelope.hook,
            session_ref=envelope.session_id,
            client_ref=client,
            conversation_ref=context["conversation_ref"],
            task_ref=context["task_ref"],
            workspace_ref=context["workspace_ref"],
            project_ref=context["project_ref"],
            lifecycle_event_id=envelope.event_id,
            sequence=envelope.sequence,
            idempotency_material=(envelope.client_id, envelope.event_id, envelope.sequence),
            payload_reference=payload_ref,
            payload_reference_kind=None if reference is None else reference.kind,
            payload_commitment=None if reference is None else reference.sha256,
            payload_size_bytes=None if reference is None else reference.size_bytes,
            artifact_refs=lifecycle_refs,
            event_time=times[0],
            observed_time=times[1],
            retention=retained,
            producer_version=producer_version or CONTRACT_VERSION,
            lifecycle_contract_version=CONTRACT_VERSION,
        )
    _reference(cast(str, common["producer_version"]), maximum=MAX_PRODUCER_VERSION_CHARS)
    return EventReconciliationInput(**cast(Any, common))


def normalize_capture_event(event: CaptureEvent, **kwargs: Any) -> EventReconciliationInput:
    return reconcile_event(capture_event=event, **kwargs)


def normalize_lifecycle_event(
    envelope: ClientLifecycleEnvelope, **kwargs: Any
) -> EventReconciliationInput:
    return reconcile_event(lifecycle_envelope=envelope, **kwargs)


ReconciliationInput = EventReconciliationInput
