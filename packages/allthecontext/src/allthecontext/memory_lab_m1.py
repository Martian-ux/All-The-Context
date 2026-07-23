"""Research-only observable memory-use ledger for Memory Lab Wave 4 M1.

The ledger records bounded host-observable facts.  It is deliberately isolated
from Core and never accepts prompts, supplied context, model reasoning, or
client self-report as causal evidence.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

SCHEMA_VERSION = 1
MAX_EVENTS = 10_000
MAX_IDENTIFIER_LENGTH = 128


class Stage(StrEnum):
    ASSIGNED = "assigned"
    SUPPLIED = "supplied"
    ACKNOWLEDGED = "acknowledged"
    OBSERVED_USE = "observed_use"
    ACTION = "action"
    OUTCOME = "outcome"
    INVALIDATED = "invalidated"


class ObservableSource(StrEnum):
    CORE = "core"
    CLIENT_TRANSPORT = "client_transport"
    HOST_ARTIFACT = "host_artifact"
    TOOL_GATEWAY = "tool_gateway"
    OUTCOME_ADAPTER = "outcome_adapter"
    LIFECYCLE = "lifecycle"


class OutcomeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class InvalidationReason(StrEnum):
    CORRECTION = "correction"
    SCOPE_NARROWING = "scope_narrowing"
    PERMISSION_REVOCATION = "permission_revocation"
    ORDINARY_DELETE = "ordinary_delete"
    TERMINAL_PURGE = "terminal_purge"
    POLICY_GENERATION_CHANGE = "policy_generation_change"


class AdmissionStatus(StrEnum):
    ACCEPTED = "accepted"
    IDEMPOTENT = "idempotent"
    REJECTED = "rejected"


class FailureClass(StrEnum):
    FORBIDDEN_FIELD = "forbidden_field"
    UNKNOWN_FIELD = "unknown_field"
    INVALID_FIELD = "invalid_field"
    UNKNOWN_RECORD = "unknown_record"
    UNKNOWN_TRANSACTION = "unknown_transaction"
    UNKNOWN_PARENT = "unknown_parent"
    BINDING_MISMATCH = "binding_mismatch"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    IMPOSSIBLE_TRANSITION = "impossible_transition"
    FABRICATED_OUTCOME = "fabricated_outcome"
    INVALIDATED_TRANSACTION = "invalidated_transaction"
    PURGED_IDENTITY = "purged_identity"


_EVENT_FIELDS = frozenset(
    {
        "event_id",
        "transaction_id",
        "stage",
        "canonical_record_id",
        "canonical_record_version",
        "canonical_snapshot_id",
        "issue_receipt_id",
        "policy_generation",
        "principal_capability_view_id",
        "causal_predecessor_event_ids",
        "event_time_bucket",
        "observable_source_type",
        "action_type_enum",
        "outcome_status_enum",
        "invalidation_reason_enum",
    }
)
_REQUIRED_FIELDS = frozenset(
    {
        "event_id",
        "transaction_id",
        "stage",
        "canonical_record_id",
        "canonical_record_version",
        "canonical_snapshot_id",
        "issue_receipt_id",
        "policy_generation",
        "principal_capability_view_id",
        "causal_predecessor_event_ids",
        "event_time_bucket",
        "observable_source_type",
    }
)
_FORBIDDEN_FIELDS = frozenset(
    {
        "raw_context",
        "raw_prompt",
        "raw_response",
        "raw_supplied_context",
        "hidden_reasoning",
        "chain_of_thought",
        "credential",
        "secret",
        "user_text",
        "semantic_summary",
        "stable_content_hash",
        "exact_high_resolution_timestamp",
        "device_fingerprint",
        "cross_transaction_tracking_id",
        "model_self_report",
    }
)


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    record_id: str
    version: int
    lineage_id: str
    identity_generation: int = 1


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    event_id: str
    transaction_id: str
    stage: Stage
    canonical_record_id: str
    canonical_record_version: int
    canonical_snapshot_id: str
    issue_receipt_id: str
    policy_generation: int
    principal_capability_view_id: str
    causal_predecessor_event_ids: tuple[str, ...]
    event_time_bucket: str
    observable_source_type: ObservableSource
    action_type_enum: str | None = None
    outcome_status_enum: OutcomeStatus | None = None
    invalidation_reason_enum: InvalidationReason | None = None


@dataclass(frozen=True, slots=True)
class FailureReceipt:
    schema_version: int
    case_id: str
    invariant_id: str
    phase_enum: str
    failure_class_enum: FailureClass
    expected_state_enum: str
    actual_state_enum: str
    per_run_artifact_ref: str
    count: int = 1


@dataclass(frozen=True, slots=True)
class Admission:
    status: AdmissionStatus
    event: LedgerEvent | None = None
    failure: FailureReceipt | None = None


@dataclass(frozen=True, slots=True)
class TransactionView:
    transaction_id: str
    stages: tuple[Stage, ...]
    acknowledgement: str
    observed_use: str
    terminal_state: str
    invalidation_reason: InvalidationReason | None
    evidence_grade: str


@dataclass(frozen=True, slots=True)
class AggregateView:
    assigned: int
    supplied: int
    acknowledged: int
    observed_use: int
    action: int
    outcome: int
    invalidated: int
    purge_count: int


class ObservableUseLedger:
    """Append-only event admission plus deterministic derived reconstruction."""

    def __init__(self, *, run_id: str) -> None:
        _validate_identifier(run_id, "run_id")
        self._run_id = run_id
        self._records: dict[tuple[str, int], CanonicalRecord] = {}
        self._minimum_identity_generation = 1
        self._events: list[LedgerEvent] = []
        self._by_event_id: dict[str, LedgerEvent] = {}
        self._purge_count = 0
        self._failure_ordinal = 0

    @property
    def events(self) -> tuple[LedgerEvent, ...]:
        return tuple(self._events)

    def register_record(self, record: CanonicalRecord) -> None:
        _validate_identifier(record.record_id, "record_id")
        _validate_identifier(record.lineage_id, "lineage_id")
        if record.version < 1:
            raise ValueError("record version must be positive")
        if record.identity_generation < self._minimum_identity_generation:
            raise ValueError("purged identity generation cannot be reused")
        key = (record.record_id, record.version)
        prior = self._records.get(key)
        if prior is not None and prior != record:
            raise ValueError("record identity/version conflict")
        self._records[key] = record

    def append(
        self,
        raw: Mapping[str, Any],
        *,
        case_id: str = "M1-HARNESS",
        invariant_id: str = "M1-I01",
    ) -> Admission:
        """Validate and append one observable event without echoing rejected data."""

        failure = self._schema_failure(raw, case_id, invariant_id)
        if failure is not None:
            return failure
        try:
            event = _parse_event(raw)
        except (TypeError, ValueError):
            return self._reject(
                FailureClass.INVALID_FIELD, case_id, invariant_id, "valid_event", "invalid"
            )

        if len(self._events) >= MAX_EVENTS:
            return self._reject(
                FailureClass.INVALID_FIELD,
                case_id,
                invariant_id,
                "bounded_event_count",
                "limit_exceeded",
            )

        binding_key = (event.canonical_record_id, event.canonical_record_version)
        record = self._records.get(binding_key)
        if record is None:
            return self._reject(
                FailureClass.UNKNOWN_RECORD,
                case_id,
                invariant_id,
                "known_record_version",
                "unknown",
            )
        prior = self._by_event_id.get(event.event_id)
        if prior is not None:
            if prior == event:
                return Admission(AdmissionStatus.IDEMPOTENT, event=prior)
            return self._reject(
                FailureClass.DUPLICATE_CONFLICT,
                case_id,
                "M1-I02",
                "identical_retry",
                "conflicting_retry",
            )

        transaction = self._transaction_events(event.transaction_id)
        if not transaction and event.stage is not Stage.ASSIGNED:
            return self._reject(
                FailureClass.UNKNOWN_TRANSACTION,
                case_id,
                invariant_id,
                "assigned_transaction",
                "unknown",
            )
        if transaction:
            binding = transaction[0]
            if _binding(event) != _binding(binding):
                return self._reject(
                    FailureClass.BINDING_MISMATCH,
                    case_id,
                    "M1-I01",
                    "exact_transaction_binding",
                    "mismatch",
                )
            if any(item.stage is Stage.INVALIDATED for item in transaction):
                return self._reject(
                    FailureClass.INVALIDATED_TRANSACTION,
                    case_id,
                    "M1-I04",
                    "open_transaction",
                    "invalidated",
                )

        parents = tuple(self._by_event_id.get(item) for item in event.causal_predecessor_event_ids)
        if any(parent is None for parent in parents):
            return self._reject(
                FailureClass.UNKNOWN_PARENT,
                case_id,
                "M1-I03",
                "known_parents",
                "unknown_parent",
            )
        typed_parents = tuple(parent for parent in parents if parent is not None)
        if any(parent.transaction_id != event.transaction_id for parent in typed_parents):
            return self._reject(
                FailureClass.BINDING_MISMATCH,
                case_id,
                "M1-I01",
                "same_transaction_parents",
                "foreign_parent",
            )
        transition_failure = self._transition_failure(event, transaction, typed_parents)
        if transition_failure is not None:
            return self._reject(
                transition_failure,
                case_id,
                "M1-I03" if transition_failure is not FailureClass.FABRICATED_OUTCOME else "M1-I06",
                "possible_observable_transition",
                "impossible",
            )

        self._events.append(event)
        self._by_event_id[event.event_id] = event
        return Admission(AdmissionStatus.ACCEPTED, event=event)

    def invalidate_record(
        self,
        *,
        record_id: str,
        version: int,
        reason: InvalidationReason,
        event_time_bucket: str,
        principal_capability_view_ids: frozenset[str] | None = None,
    ) -> tuple[Admission, ...]:
        """Append explicit invalidations for every affected open transaction."""

        key = (record_id, version)
        record = self._records.get(key)
        if record is None:
            raise ValueError("cannot invalidate an unknown record version")
        open_transactions: dict[str, LedgerEvent] = {}
        for event in self._events:
            if (
                event.canonical_record_id == record_id
                and event.canonical_record_version == version
            ):
                open_transactions[event.transaction_id] = event
        results: list[Admission] = []
        for transaction_id in sorted(open_transactions):
            transaction = self._transaction_events(transaction_id)
            if any(item.stage is Stage.INVALIDATED for item in transaction):
                continue
            binding = transaction[0]
            if (
                principal_capability_view_ids is not None
                and binding.principal_capability_view_id
                not in principal_capability_view_ids
            ):
                continue
            parent = transaction[-1]
            results.append(
                self.append(
                    {
                        **_base_mapping(binding),
                        "event_id": f"inv-{reason.value}-{len(self._events) + 1}",
                        "stage": Stage.INVALIDATED.value,
                        "causal_predecessor_event_ids": [parent.event_id],
                        "event_time_bucket": event_time_bucket,
                        "observable_source_type": ObservableSource.LIFECYCLE.value,
                        "invalidation_reason_enum": reason.value,
                    },
                    case_id="M1-LIFECYCLE",
                    invariant_id="M1-I04",
                )
            )
        if reason is InvalidationReason.TERMINAL_PURGE:
            affected_keys = {
                candidate_key
                for candidate_key, candidate in self._records.items()
                if candidate.lineage_id == record.lineage_id
            }
            self._minimum_identity_generation = max(
                self._minimum_identity_generation,
                *(
                    self._records[item].identity_generation + 1
                    for item in affected_keys
                ),
            )
            retained_events = [
                event
                for event in self._events
                if (event.canonical_record_id, event.canonical_record_version)
                not in affected_keys
            ]
            self._events = retained_events
            self._by_event_id = {event.event_id: event for event in retained_events}
            for affected_key in affected_keys:
                self._records.pop(affected_key, None)
            self._purge_count += 1
            return tuple(
                Admission(item.status, failure=item.failure) for item in results
            )
        return tuple(results)

    def advance_policy_generation(
        self, *, old_generation: int, event_time_bucket: str
    ) -> tuple[Admission, ...]:
        affected = sorted(
            {
                (event.canonical_record_id, event.canonical_record_version)
                for event in self._events
                if event.policy_generation == old_generation
            }
        )
        results: list[Admission] = []
        for record_id, version in affected:
            results.extend(
                self.invalidate_record(
                    record_id=record_id,
                    version=version,
                    reason=InvalidationReason.POLICY_GENERATION_CHANGE,
                    event_time_bucket=event_time_bucket,
                )
            )
        return tuple(results)

    def transaction_view(self, transaction_id: str) -> TransactionView:
        events = self._transaction_events(transaction_id)
        if not events:
            raise KeyError(transaction_id)
        stages = tuple(event.stage for event in events)
        invalidation = next(
            (
                event.invalidation_reason_enum
                for event in reversed(events)
                if event.stage is Stage.INVALIDATED
            ),
            None,
        )
        grade = "assigned"
        for candidate in (
            Stage.SUPPLIED,
            Stage.ACKNOWLEDGED,
            Stage.OBSERVED_USE,
            Stage.ACTION,
            Stage.OUTCOME,
        ):
            if candidate in stages:
                grade = candidate.value
        if invalidation is not None:
            grade = Stage.INVALIDATED.value
        return TransactionView(
            transaction_id=transaction_id,
            stages=stages,
            acknowledgement="observed" if Stage.ACKNOWLEDGED in stages else "not_observed",
            observed_use="observed" if Stage.OBSERVED_USE in stages else "not_observed",
            terminal_state="invalidated" if invalidation is not None else "open",
            invalidation_reason=invalidation,
            evidence_grade=grade,
        )

    def rebuild_aggregates(self) -> AggregateView:
        """Rebuild all derived counts from accepted events and invalidation dependencies."""

        active_events: list[LedgerEvent] = []
        invalidated_transactions = {
            event.transaction_id
            for event in self._events
            if event.stage is Stage.INVALIDATED
        }
        for event in self._events:
            if (
                event.transaction_id in invalidated_transactions
                and event.stage
                in {Stage.OBSERVED_USE, Stage.ACTION, Stage.OUTCOME}
            ):
                continue
            active_events.append(event)
        counts = Counter(event.stage for event in active_events)
        return AggregateView(
            assigned=counts[Stage.ASSIGNED],
            supplied=counts[Stage.SUPPLIED],
            acknowledged=counts[Stage.ACKNOWLEDGED],
            observed_use=counts[Stage.OBSERVED_USE],
            action=counts[Stage.ACTION],
            outcome=counts[Stage.OUTCOME],
            invalidated=counts[Stage.INVALIDATED],
            purge_count=self._purge_count,
        )

    def normalized_receipts(self) -> tuple[dict[str, Any], ...]:
        """Return privacy-bounded receipts with per-run identifiers normalized."""

        transaction_positions: dict[str, str] = {}
        event_positions: dict[str, str] = {}
        issue_positions: dict[str, str] = {}
        receipts: list[dict[str, Any]] = []
        for event in self._events:
            transaction_positions.setdefault(
                event.transaction_id, f"transaction-{len(transaction_positions) + 1}"
            )
            event_positions.setdefault(event.event_id, f"event-{len(event_positions) + 1}")
            issue_positions.setdefault(
                event.issue_receipt_id, f"issue-{len(issue_positions) + 1}"
            )
            receipts.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "per_run_receipt_id": f"{self._run_id}-receipt-{len(receipts) + 1}",
                    "event_id": event_positions[event.event_id],
                    "transaction_id": transaction_positions[event.transaction_id],
                    "stage": event.stage.value,
                    "canonical_record_id": event.canonical_record_id,
                    "canonical_record_version": event.canonical_record_version,
                    "issue_receipt_id": issue_positions[event.issue_receipt_id],
                    "policy_generation": event.policy_generation,
                    "principal_capability_view_id": event.principal_capability_view_id,
                    "causal_predecessor_event_ids": [
                        event_positions[parent]
                        for parent in event.causal_predecessor_event_ids
                    ],
                    "event_time_bucket": event.event_time_bucket,
                    "observable_source_type": event.observable_source_type.value,
                    "action_type_enum": event.action_type_enum,
                    "outcome_status_enum": (
                        None
                        if event.outcome_status_enum is None
                        else event.outcome_status_enum.value
                    ),
                    "invalidation_reason_enum": (
                        None
                        if event.invalidation_reason_enum is None
                        else event.invalidation_reason_enum.value
                    ),
                }
            )
        return tuple(receipts)

    def replay(self) -> ObservableUseLedger:
        clone = ObservableUseLedger(run_id=self._run_id)
        for record in self._records.values():
            clone.register_record(record)
        for event in self._events:
            result = clone.append(asdict(event))
            if result.status is not AdmissionStatus.ACCEPTED:
                raise AssertionError("accepted ledger failed deterministic replay")
        clone._minimum_identity_generation = self._minimum_identity_generation
        clone._purge_count = self._purge_count
        return clone

    def inspectable_state(self) -> dict[str, Any]:
        """Return every declared internal state surface for privacy assertions."""

        return {
            "active_records": [
                asdict(record)
                for _, record in sorted(self._records.items())
            ],
            "active_events": [asdict(event) for event in self._events],
            "minimum_identity_generation": self._minimum_identity_generation,
            "purge_count": self._purge_count,
        }

    def _schema_failure(
        self,
        raw: Mapping[str, Any],
        case_id: str,
        invariant_id: str,
    ) -> Admission | None:
        keys = frozenset(raw)
        if keys & _FORBIDDEN_FIELDS:
            return self._reject(
                FailureClass.FORBIDDEN_FIELD,
                case_id,
                "M1-I07",
                "allowlisted_fields",
                "forbidden_field_name",
            )
        if keys - _EVENT_FIELDS:
            return self._reject(
                FailureClass.UNKNOWN_FIELD,
                case_id,
                "M1-I07",
                "allowlisted_fields",
                "unknown_field_name",
            )
        if keys < _REQUIRED_FIELDS:
            return self._reject(
                FailureClass.INVALID_FIELD,
                case_id,
                invariant_id,
                "required_fields",
                "missing_field",
            )
        return None

    def _transition_failure(
        self,
        event: LedgerEvent,
        transaction: Sequence[LedgerEvent],
        parents: Sequence[LedgerEvent],
    ) -> FailureClass | None:
        stages = {item.stage for item in transaction}
        parent_stages = {item.stage for item in parents}
        if event.stage is Stage.ASSIGNED:
            return (
                None
                if (
                    not transaction
                    and not parents
                    and event.observable_source_type is ObservableSource.CORE
                )
                else FailureClass.IMPOSSIBLE_TRANSITION
            )
        if event.stage in stages:
            return FailureClass.IMPOSSIBLE_TRANSITION
        requirements: dict[Stage, tuple[Stage, ObservableSource]] = {
            Stage.SUPPLIED: (Stage.ASSIGNED, ObservableSource.CORE),
            Stage.ACKNOWLEDGED: (Stage.SUPPLIED, ObservableSource.CLIENT_TRANSPORT),
            Stage.OBSERVED_USE: (Stage.SUPPLIED, ObservableSource.HOST_ARTIFACT),
            Stage.ACTION: (Stage.OBSERVED_USE, ObservableSource.TOOL_GATEWAY),
            Stage.OUTCOME: (Stage.ACTION, ObservableSource.OUTCOME_ADAPTER),
        }
        if event.stage is Stage.INVALIDATED:
            if (
                not parents
                or event.invalidation_reason_enum is None
                or event.observable_source_type is not ObservableSource.LIFECYCLE
            ):
                return FailureClass.IMPOSSIBLE_TRANSITION
            return None
        required_stage, required_source = requirements[event.stage]
        if (
            required_stage not in stages
            or required_stage not in parent_stages
            or event.observable_source_type is not required_source
        ):
            return (
                FailureClass.FABRICATED_OUTCOME
                if event.stage is Stage.OUTCOME
                else FailureClass.IMPOSSIBLE_TRANSITION
            )
        if event.stage is Stage.ACTION and not event.action_type_enum:
            return FailureClass.IMPOSSIBLE_TRANSITION
        if event.stage is Stage.OUTCOME and event.outcome_status_enum is None:
            return FailureClass.FABRICATED_OUTCOME
        return None

    def _transaction_events(self, transaction_id: str) -> tuple[LedgerEvent, ...]:
        return tuple(
            event for event in self._events if event.transaction_id == transaction_id
        )

    def _reject(
        self,
        failure: FailureClass,
        case_id: str,
        invariant_id: str,
        expected: str,
        actual: str,
    ) -> Admission:
        self._failure_ordinal += 1
        return Admission(
            AdmissionStatus.REJECTED,
            failure=FailureReceipt(
                schema_version=SCHEMA_VERSION,
                case_id=case_id,
                invariant_id=invariant_id,
                phase_enum="admission",
                failure_class_enum=failure,
                expected_state_enum=expected,
                actual_state_enum=actual,
                per_run_artifact_ref=(
                    f"{self._run_id}-failure-{self._failure_ordinal}"
                ),
            ),
        )

def serialize_receipts(receipts: Sequence[Mapping[str, Any]]) -> str:
    return json.dumps(receipts, sort_keys=True, separators=(",", ":"))


def serialize_failure(receipt: FailureReceipt) -> str:
    return json.dumps(asdict(receipt), sort_keys=True, separators=(",", ":"))


def _parse_event(raw: Mapping[str, Any]) -> LedgerEvent:
    parents = raw["causal_predecessor_event_ids"]
    if not isinstance(parents, (list, tuple)) or any(
        not isinstance(item, str) for item in parents
    ):
        raise TypeError("parents must be a string sequence")
    event = LedgerEvent(
        event_id=str(raw["event_id"]),
        transaction_id=str(raw["transaction_id"]),
        stage=Stage(raw["stage"]),
        canonical_record_id=str(raw["canonical_record_id"]),
        canonical_record_version=int(raw["canonical_record_version"]),
        canonical_snapshot_id=str(raw["canonical_snapshot_id"]),
        issue_receipt_id=str(raw["issue_receipt_id"]),
        policy_generation=int(raw["policy_generation"]),
        principal_capability_view_id=str(raw["principal_capability_view_id"]),
        causal_predecessor_event_ids=tuple(parents),
        event_time_bucket=str(raw["event_time_bucket"]),
        observable_source_type=ObservableSource(raw["observable_source_type"]),
        action_type_enum=_optional_string(raw.get("action_type_enum")),
        outcome_status_enum=(
            None
            if raw.get("outcome_status_enum") is None
            else OutcomeStatus(raw["outcome_status_enum"])
        ),
        invalidation_reason_enum=(
            None
            if raw.get("invalidation_reason_enum") is None
            else InvalidationReason(raw["invalidation_reason_enum"])
        ),
    )
    for name in (
        event.event_id,
        event.transaction_id,
        event.canonical_record_id,
        event.canonical_snapshot_id,
        event.issue_receipt_id,
        event.principal_capability_view_id,
        event.event_time_bucket,
    ):
        _validate_identifier(name, "event identifier")
    if event.canonical_record_version < 1 or event.policy_generation < 1:
        raise ValueError("versions and generations must be positive")
    return event


def _binding(event: LedgerEvent) -> tuple[object, ...]:
    return (
        event.canonical_record_id,
        event.canonical_record_version,
        event.canonical_snapshot_id,
        event.issue_receipt_id,
        event.policy_generation,
        event.principal_capability_view_id,
    )


def _base_mapping(event: LedgerEvent) -> dict[str, Any]:
    return {
        "transaction_id": event.transaction_id,
        "canonical_record_id": event.canonical_record_id,
        "canonical_record_version": event.canonical_record_version,
        "canonical_snapshot_id": event.canonical_snapshot_id,
        "issue_receipt_id": event.issue_receipt_id,
        "policy_generation": event.policy_generation,
        "principal_capability_view_id": event.principal_capability_view_id,
    }


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValueError("optional enum must be a bounded non-empty string")
    return value


def _validate_identifier(value: str, name: str) -> None:
    if not value or len(value) > MAX_IDENTIFIER_LENGTH:
        raise ValueError(f"{name} must be a bounded non-empty string")
