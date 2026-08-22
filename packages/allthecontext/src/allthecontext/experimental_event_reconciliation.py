"""Pure Wave 1 capture/lifecycle metadata reconciliation.

The boundary accepts existing contracts and retains only bounded identifiers,
commitments, and payload references; it never writes, replays, checkpoints, or
mints observation/current identifiers or copies source text.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, cast

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

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?i)(bearer\s+|basic\s+|\bsk-[a-z0-9]|\bgh[pousr]_[a-z0-9]|"
    r"\bAIza[a-z0-9]|(?:api[_ -]?key|access[_ -]?token|password|"
    r"credential|secret|token)\s*[:=])"
)


class ReconciliationErrorCode(StrEnum):
    """Content-free failure vocabulary."""

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


class ReconciliationViolation(ValueError):
    """A bounded error whose message contains only a stable code."""

    def __init__(self, code: ReconciliationErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


class EventOrigin(StrEnum):
    CAPTURE = "capture"
    CLIENT_LIFECYCLE = "client_lifecycle"
    CAPTURE_AND_CLIENT_LIFECYCLE = "capture_and_client_lifecycle"


class SensitivityClass(StrEnum):
    ORDINARY = "ordinary"
    SENSITIVE = "sensitive"
    RESTRICTED = "restricted"


def _scan_secret(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    return "".join(
        char for char in normalized if unicodedata.category(char) not in {"Cf", "Mn", "Mc", "Me"}
    )


def _reference(value: object, *, maximum: int = MAX_GENERIC_REFERENCE_CHARS) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_FIELD)
    if _CONTROL.search(value) or _SECRET.search(_scan_secret(value)):
        raise ReconciliationViolation(ReconciliationErrorCode.SECRET_LIKE_METADATA)
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
            raise ReconciliationViolation(ReconciliationErrorCode.INVALID_TIMESTAMP) from None
    else:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_TIMESTAMP)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_TIMESTAMP)
    return parsed.astimezone(UTC)


def _sequence(value: object, *, allow_zero: bool = False) -> int | None:
    if value is None:
        return None
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= MAX_CAPTURE_INTEGER:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_FIELD)
    return value


def _artifact_refs(value: object) -> tuple[str, ...]:
    if type(value) is not tuple or len(value) > MAX_ARTIFACT_REFERENCES:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_FIELD)
    refs = tuple(_reference(item) for item in value)
    if len(refs) != len(set(refs)):
        raise ReconciliationViolation(ReconciliationErrorCode.DUPLICATE_REFERENCE)
    return refs


@dataclass(frozen=True, slots=True)
class DependencyWithdrawal:
    """One authorized correction/deletion/expiry/purge dependency withdrawal."""

    dependency_ref: str
    cause: InvalidationCause
    action: InvalidationAction = InvalidationAction.WITHDRAW_AND_REBUILD
    authorized: bool = True
    provider_item_id: str | None = None

    def __post_init__(self) -> None:
        _reference(self.dependency_ref)
        if type(self.cause) is not InvalidationCause or self.cause not in {
            InvalidationCause.CORRECTION,
            InvalidationCause.ORDINARY_DELETE,
            InvalidationCause.RETENTION_EXPIRY,
            InvalidationCause.TERMINAL_PURGE,
        }:
            raise ReconciliationViolation(ReconciliationErrorCode.INVALID_WITHDRAWAL)
        if type(self.action) is not InvalidationAction:
            raise ReconciliationViolation(ReconciliationErrorCode.INVALID_WITHDRAWAL)
        if type(self.authorized) is not bool:
            raise ReconciliationViolation(ReconciliationErrorCode.INVALID_WITHDRAWAL)
        if self.provider_item_id is not None:
            _reference(self.provider_item_id)
        if self.cause is InvalidationCause.TERMINAL_PURGE:
            if self.action is not InvalidationAction.ERASE:
                raise ReconciliationViolation(ReconciliationErrorCode.PURGE_REQUIRES_ERASE)
        elif self.action is InvalidationAction.ERASE:
            raise ReconciliationViolation(ReconciliationErrorCode.INVALID_WITHDRAWAL)


def _withdrawals(value: object) -> tuple[DependencyWithdrawal, ...]:
    if type(value) is not tuple or len(value) > MAX_DEPENDENCY_WITHDRAWALS:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_WITHDRAWAL)
    if any(type(item) is not DependencyWithdrawal for item in value):
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_WITHDRAWAL)
    refs = tuple(item.dependency_ref for item in value)
    if len(refs) != len(set(refs)):
        raise ReconciliationViolation(ReconciliationErrorCode.DUPLICATE_REFERENCE)
    return value


@dataclass(frozen=True, slots=True)
class EventReconciliationInput:
    """Immutable, content-free metadata supplied to a later formation step."""

    event_id: str
    origin: EventOrigin
    witness_class: WitnessClass
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
    payload_commitment: str | None = None
    payload_size_bytes: int = 0
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
    retention_class: str = RetentionClass.SOURCE_LIFETIME.value
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
            raise ReconciliationViolation(ReconciliationErrorCode.INVALID_FIELD)
        artifact_refs = _artifact_refs(self.artifact_refs)
        withdrawals = _withdrawals(self.dependency_withdrawals)
        object.__setattr__(self, "artifact_refs", artifact_refs)
        object.__setattr__(self, "dependency_withdrawals", withdrawals)

    def as_dict(self) -> dict[str, object]:
        """Return metadata only; neither raw capture nor lifecycle content appears."""

        data = asdict(self)
        data.pop("retention")
        data["artifact_refs"] = list(self.artifact_refs)
        data["event_time"] = self.event_time.isoformat() if self.event_time else None
        data["observed_time"] = self.observed_time.isoformat() if self.observed_time else None
        data["authorization"] = {
            "allowed_principals": sorted(self.authorization.allowed_principals or ()),
            "allowed_scopes": sorted(self.authorization.allowed_scopes or ()),
        }
        data["dependency_withdrawals"] = [
            (item.dependency_ref, item.cause.value, item.action.value, item.provider_item_id)
            for item in self.dependency_withdrawals
        ]
        return data


_PAYLOAD_TYPES: tuple[type[object], ...] = (
    ContextRequestPayload,
    ContextRequestPayload,
    DirectUserTurnPayload,
    ToolObservableResultPayload,
    ResponseEmissionPayload,
    CompactionTaskCheckpointPayload,
    RestartSessionTransitionPayload,
    CompletionAbandonmentPayload,
    ConsequenceCheckpointPayload,
)


def _validate_lifecycle(envelope: object) -> ClientLifecycleEnvelope:
    if type(envelope) is not ClientLifecycleEnvelope:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    value = cast(ClientLifecycleEnvelope, envelope)
    if type(value.hook) is not str or value.hook not in ALL_LIFECYCLE_HOOKS:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    expected = _PAYLOAD_TYPES[ALL_LIFECYCLE_HOOKS.index(value.hook)]
    if type(value.payload) is not expected:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    if type(value.contract_version) is not str or value.contract_version != CONTRACT_VERSION:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    if type(value.content_ownership) is not str or value.content_ownership != "external_untrusted":
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    if type(value.retention_class) is not str or value.retention_class not in {
        "ephemeral",
        "bounded",
        "checkpoint",
    }:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    if value.hook == "direct_user_turn" and value.witness != "direct_user":
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    if value.hook != "direct_user_turn" and value.witness == "direct_user":
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    return value


def _payload_reference(value: object) -> PayloadReference:
    if type(value) is not PayloadReference:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    reference = cast(PayloadReference, value)
    if reference.untrusted is not True:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    _reference(reference.reference)
    if type(reference.kind) is not str or not reference.kind:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD)
    return reference


def _lifecycle_artifacts(envelope: ClientLifecycleEnvelope) -> tuple[str, ...]:
    attribute = {
        "direct_user_turn": "turn_ref",
        "tool_observable_result": "result_ref",
        "response_emission": "response_ref",
        "compaction_task_checkpoint": "checkpoint_ref",
        "consequence_checkpoint": "evidence_ref",
    }.get(envelope.hook)
    if attribute is None:
        return ()
    reference = getattr(envelope.payload, attribute)
    return () if reference is None else (_payload_reference(reference).reference,)


def _capture_metadata(
    event: object,
) -> tuple[CaptureEvent, str, str, int]:
    if type(event) is not CaptureEvent:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_CAPTURE_EVENT)
    capture = cast(CaptureEvent, event)
    if type(capture.operation) is not str or capture.operation not in {"upsert", "delete"}:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_CAPTURE_OPERATION)
    if type(capture.generation) is not int or not 0 <= capture.generation <= MAX_CAPTURE_INTEGER:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_CAPTURE_GENERATION)
    normalizer = getattr(capture, "normalized", None)
    if not callable(normalizer):
        raise ReconciliationViolation(ReconciliationErrorCode.CAPTURE_NORMALIZATION_REJECTED)
    try:
        normalized = normalizer()
    except Exception:
        raise ReconciliationViolation(
            ReconciliationErrorCode.CAPTURE_NORMALIZATION_REJECTED
        ) from None
    if (
        type(normalized) is not tuple
        or len(normalized) != 2
        or type(normalized[0]) is not str
        or type(normalized[1]) is not str
        or _SHA256.fullmatch(normalized[1]) is None
    ):
        raise ReconciliationViolation(ReconciliationErrorCode.CAPTURE_NORMALIZATION_REJECTED)
    payload_size = len(normalized[0].encode("utf-8"))
    if payload_size > MAX_PAYLOAD_BYTES:
        raise ReconciliationViolation(ReconciliationErrorCode.CAPTURE_NORMALIZATION_REJECTED)
    return capture, capture.provider_event_id, normalized[1], payload_size


def _lifecycle_retention(value: str) -> RetentionPolicy:
    mapping = {
        "ephemeral": RetentionClass.SESSION,
        "bounded": RetentionClass.SOURCE_LIFETIME,
        "checkpoint": RetentionClass.USER_CONTROLLED,
    }
    try:
        return RetentionPolicy(mapping[value])
    except (KeyError, TypeError):
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_RETENTION) from None


def _merge_context_ref(current: str | None, incoming: str | None) -> str | None:
    if current is not None and incoming is not None and current != incoming:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LINEAGE)
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
    """Normalize one existing capture event, lifecycle event, or composed pair."""

    if capture_event is None and lifecycle_envelope is None:
        raise ReconciliationViolation(ReconciliationErrorCode.EMPTY_INPUT)
    if capture_event is not None and type(capture_event) is not CaptureEvent:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_CAPTURE_EVENT)
    if lifecycle_envelope is not None and type(lifecycle_envelope) is not ClientLifecycleEnvelope:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE)
    if type(artifact_refs) is not tuple:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_FIELD)
    withdrawals = _withdrawals(dependency_withdrawals)
    normalized_capture: tuple[CaptureEvent, str, str, int] | None = None
    if capture_event is not None:
        normalized_capture = _capture_metadata(capture_event)
    normalized_lifecycle = (
        _validate_lifecycle(lifecycle_envelope) if lifecycle_envelope is not None else None
    )

    bounded_source_id = _optional_reference(source_id)
    bounded_account = _optional_reference(account_ref)
    bounded_client = _optional_reference(client_ref)
    bounded_conversation = _optional_reference(conversation_ref)
    bounded_task = _optional_reference(task_ref)
    bounded_workspace = _optional_reference(workspace_ref)
    bounded_project = _optional_reference(project_ref)
    if normalized_capture is not None:
        capture, source_event_id, commitment, payload_size = normalized_capture
        bounded_source_event_id = _reference(source_event_id)
        bounded_item = _reference(capture.provider_item_id)
        bounded_source_id = _reference(bounded_source_id)
        if source_cursor is not None:
            source_cursor = _reference(source_cursor, maximum=MAX_CURSOR_CHARS)
        _sequence(source_sequence)
        idempotency_key = _reference(idempotency_key)
        if capture.operation == "delete":
            matches = tuple(
                item
                for item in withdrawals
                if item.cause is InvalidationCause.ORDINARY_DELETE
                and (
                    item.provider_item_id == bounded_item
                    or (item.provider_item_id is None and item.dependency_ref == bounded_item)
                )
                and item.authorized
            )
            if not matches:
                raise ReconciliationViolation(ReconciliationErrorCode.DELETE_WITHDRAWAL_REQUIRED)
        if any(
            item.cause is InvalidationCause.ORDINARY_DELETE
            and item.provider_item_id is not None
            and item.provider_item_id != bounded_item
            for item in withdrawals
        ):
            raise ReconciliationViolation(ReconciliationErrorCode.DELETE_WITHDRAWAL_MISMATCH)
    else:
        bounded_source_event_id = None
        bounded_item = None
        commitment = None
        payload_size = 0
        if source_id is not None or source_cursor is not None or source_sequence is not None:
            raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LINEAGE)
        if idempotency_key is not None:
            raise ReconciliationViolation(ReconciliationErrorCode.INVALID_LINEAGE)

    lifecycle = normalized_lifecycle
    retention_label: str
    if lifecycle is not None:
        bounded_lifecycle_event = _reference(lifecycle.event_id)
        bounded_client = _merge_context_ref(bounded_client, lifecycle.client_id)
        bounded_conversation = _merge_context_ref(bounded_conversation, lifecycle.conversation_id)
        bounded_task = _merge_context_ref(bounded_task, lifecycle.task_id)
        bounded_workspace = _merge_context_ref(bounded_workspace, lifecycle.workspace_id)
        bounded_project = _merge_context_ref(bounded_project, lifecycle.project_id)
        lifecycle_refs = _lifecycle_artifacts(lifecycle)
        lifecycle_observed = lifecycle.observed_at
        if observed_time is None and lifecycle_observed is not None:
            observed_time = lifecycle_observed
    else:
        bounded_lifecycle_event = None
        lifecycle_refs = ()

    provided_refs = _artifact_refs(artifact_refs)
    combined_refs = list(provided_refs)
    refs_to_add = lifecycle_refs + ((bounded_item,) if bounded_item is not None else ())
    for ref in refs_to_add:
        if ref in combined_refs:
            raise ReconciliationViolation(ReconciliationErrorCode.DUPLICATE_REFERENCE)
        combined_refs.append(ref)
    if len(combined_refs) > MAX_ARTIFACT_REFERENCES:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_FIELD)

    if sensitivity is None or type(sensitivity) is not SensitivityClass:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_SENSITIVITY)
    if authorization is None:
        authorization = AuthorizationApplicability()
    elif type(authorization) is not AuthorizationApplicability:
        raise ReconciliationViolation(ReconciliationErrorCode.INVALID_FIELD)
    if retention is None:
        retention = (
            _lifecycle_retention(lifecycle.retention_class)
            if lifecycle is not None
            else RetentionPolicy(RetentionClass.SOURCE_LIFETIME)
        )
    else:
        if type(retention) is not RetentionPolicy:
            raise ReconciliationViolation(ReconciliationErrorCode.INVALID_RETENTION)
    if lifecycle is not None:
        retention_label = lifecycle.retention_class
    else:
        retention_label = retention.retention_class.value

    event_id = bounded_source_event_id or bounded_lifecycle_event
    if event_id is None:
        raise ReconciliationViolation(ReconciliationErrorCode.EMPTY_INPUT)
    origin = (
        EventOrigin.CAPTURE_AND_CLIENT_LIFECYCLE
        if normalized_capture is not None and lifecycle is not None
        else EventOrigin.CAPTURE
        if normalized_capture is not None
        else EventOrigin.CLIENT_LIFECYCLE
    )
    witness = (
        WitnessClass.AUTHORITATIVE_SOURCE
        if normalized_capture is not None and lifecycle is None
        else WitnessClass.DIRECT_USER
        if lifecycle is not None and lifecycle.witness == "direct_user"
        else WitnessClass.HOST_ARTIFACT
    )
    sequence = lifecycle.sequence if lifecycle is not None else source_sequence
    capture_version = CAPTURE_CONTRACT_VERSION if normalized_capture is not None else None
    lifecycle_version = lifecycle.contract_version if lifecycle is not None else None
    producer = producer_version or (
        "capture-client-runtime-v0"
        if normalized_capture is not None and lifecycle is not None
        else capture_version or lifecycle_version or RECONCILIATION_SCHEMA_VERSION
    )
    _reference(producer, maximum=MAX_PRODUCER_VERSION_CHARS)
    return EventReconciliationInput(
        event_id=event_id,
        origin=origin,
        witness_class=witness,
        source_id=bounded_source_id,
        source_event_id=bounded_source_event_id,
        source_generation=(normalized_capture[0].generation if normalized_capture else None),
        provider_item_id=bounded_item,
        lifecycle_event_id=bounded_lifecycle_event,
        sequence=sequence,
        source_sequence=source_sequence,
        source_order_key=normalized_capture[0].order_key if normalized_capture else None,
        source_cursor=source_cursor,
        idempotency_key=idempotency_key,
        payload_commitment=commitment,
        payload_size_bytes=payload_size,
        account_ref=bounded_account,
        client_ref=bounded_client,
        conversation_ref=bounded_conversation,
        task_ref=bounded_task,
        workspace_ref=bounded_workspace,
        project_ref=bounded_project,
        artifact_refs=tuple(combined_refs),
        event_time=_timestamp(event_time),
        observed_time=_timestamp(observed_time),
        retention=retention,
        retention_class=retention_label,
        sensitivity=sensitivity,
        authorization=authorization,
        content_ownership="external_untrusted",
        schema_version=RECONCILIATION_SCHEMA_VERSION,
        producer_version=producer,
        capture_contract_version=capture_version,
        lifecycle_contract_version=lifecycle_version,
        dependency_withdrawals=withdrawals,
    )


def normalize_capture_event(
    event: CaptureEvent,
    **kwargs: Any,
) -> EventReconciliationInput:
    return reconcile_event(capture_event=event, **kwargs)


def normalize_lifecycle_event(
    envelope: ClientLifecycleEnvelope,
    **kwargs: Any,
) -> EventReconciliationInput:
    return reconcile_event(lifecycle_envelope=envelope, **kwargs)


ReconciliationInput = EventReconciliationInput
