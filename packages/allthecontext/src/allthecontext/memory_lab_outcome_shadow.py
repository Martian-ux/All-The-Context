"""Bounded outcome receipts and conservative procedural proposals for Memory Lab.

This is a pure, in-memory research contract for ZF-017 through ZF-019.  It is
deliberately not imported by Core, Relay, MCP, dashboard, capture, scheduler,
or retrieval code.  The models contain only typed identifiers, versions,
bounded envelopes, and observable result codes; they never accept prompts,
raw context, tool arguments, private text, hidden reasoning, or model output.

The proposal path is shadow-only.  It can reject evidence or return an
advisory candidate procedure, but it has no promotion or authority operation.
"""

from __future__ import annotations

import json
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, cast

from .secret_boundary import contains_secret_like_text

SCHEMA_VERSION = 1
MAX_RECEIPTS = 512
MAX_ACTION_ENVELOPES = 16
MAX_DEPENDENCIES = 32
MAX_GUARDS = 16
MAX_REPAIR_TESTS = 16
MAX_TOKEN_LENGTH = 128


class Acknowledgement(StrEnum):
    ACKNOWLEDGED = "acknowledged"
    NOT_ACKNOWLEDGED = "not_acknowledged"
    NOT_OBSERVED = "not_observed"


class DeclaredUse(StrEnum):
    USED = "used"
    NOT_USED = "not_used"
    NOT_DECLARED = "not_declared"


class CompletionStatus(StrEnum):
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    ABANDONED = "abandoned"
    UNKNOWN = "unknown"


class OutcomeStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class EvidenceSource(StrEnum):
    HOST_ARTIFACT = "host_artifact"
    TOOL_GATEWAY = "tool_gateway"
    OUTCOME_ADAPTER = "outcome_adapter"
    CLIENT_DECLARATION = "client_declaration"


class VerificationStrength(StrEnum):
    NONE = "none"
    OBSERVED = "observed"
    STRONG = "strong"


class EnvelopeKind(StrEnum):
    TOOL = "tool"
    ACTION = "action"


class DependencyKind(StrEnum):
    MEMORY = "memory"
    SOURCE = "source"
    PROJECT = "project"
    PROJECTION = "projection"
    OUTCOME = "outcome"


class CorrectionDisposition(StrEnum):
    INVALIDATED = "invalidated"
    REPAIR_CONFIRMED = "repair_confirmed"


class InvalidationReason(StrEnum):
    CORRECTION = "correction"
    ORDINARY_DELETE = "ordinary_delete"
    TERMINAL_PURGE = "terminal_purge"
    PROJECTION_REBUILD = "projection_rebuild"


class AdmissionStatus(StrEnum):
    ACCEPTED = "accepted"
    IDEMPOTENT = "idempotent"
    REJECTED = "rejected"


class FailureCode(StrEnum):
    DUPLICATE_CONFLICT = "duplicate_conflict"
    RECEIPT_LIMIT = "receipt_limit"
    PURGED_DEPENDENCY = "purged_dependency"


class ProposalStatus(StrEnum):
    PROPOSED = "proposed"
    REJECTED = "rejected"


class ProposalReason(StrEnum):
    NO_OBSERVABLE_SUCCESS = "no_observable_success"
    RECURRENCE_OR_STRONG_VERIFICATION_REQUIRED = "recurrence_or_strong_verification_required"
    DUPLICATE_RECEIPT_INPUT = "duplicate_receipt_input"
    DUPLICATE_RECEIPT_CONFLICT = "duplicate_receipt_conflict"
    NON_INDEPENDENT_TASK_EVIDENCE = "non_independent_task_evidence"
    ACTION_SIGNATURE_DISAGREEMENT = "action_signature_disagreement"
    EXPLICIT_APPLICABILITY_REQUIRED = "explicit_applicability_required"
    NEGATIVE_GUARDS_REQUIRED = "negative_guards_required"
    REPAIR_TESTS_REQUIRED = "repair_tests_required"
    REPAIR_TEST_FAILED = "repair_test_failed"
    INFLUENCE_DEPENDENCIES_REQUIRED = "influence_dependencies_required"
    OUTCOME_DEPENDENCIES_REQUIRED = "outcome_dependencies_required"
    PURGE_CLOSURE_REQUIRED = "purge_closure_required"


_FORBIDDEN_FIELDS = frozenset(
    {
        "raw_context",
        "raw_prompt",
        "raw_response",
        "raw_supplied_context",
        "raw_tool_args",
        "hidden_reasoning",
        "chain_of_thought",
        "semantic_summary",
        "user_text",
        "credential",
        "secret",
        "model_self_report",
        "provider_claim",
    }
)


_MACHINE_TOKEN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,127})\Z", re.ASCII)


def _validate_token(value: object, name: str) -> None:
    """Require an ASCII machine token, not content-like or path-like text.

    Grammar: ``[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}``.  It intentionally keeps
    ASCII code punctuation, ISO-like timestamp ``T``/``:`` characters, and
    hyphen/underscore separators, while excluding whitespace, Unicode prose,
    slash/backslash paths, control characters, and ``..`` traversal shapes.
    """

    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TOKEN_LENGTH
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
        or ".." in value
        or _MACHINE_TOKEN_RE.fullmatch(value) is None
        or contains_secret_like_text(value)
    ):
        raise ValueError(f"{name} must match the ASCII machine-token grammar")


def _validate_enum(value: object, enum_type: type[StrEnum], name: str) -> None:
    if not isinstance(value, enum_type):
        raise ValueError(f"{name} must be an instance of {enum_type.__name__}")


def _validate_bool(value: object, name: str) -> None:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a bool")


def _validate_tuple(value: object, name: str) -> None:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be a tuple")


def _validate_version(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _validate_nonnegative_int(value: object, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _validate_unique_tokens(values: Sequence[str], name: str, limit: int) -> None:
    if len(values) > limit:
        raise ValueError(f"{name} exceeds the bounded limit")
    for value in values:
        _validate_token(value, name)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must be unique")


@dataclass(frozen=True, slots=True)
class DependencyRef:
    """Opaque versioned dependency; it carries no content or semantic text."""

    kind: DependencyKind
    dependency_id: str
    version: int

    def __post_init__(self) -> None:
        _validate_enum(self.kind, DependencyKind, "dependency kind")
        _validate_token(self.dependency_id, "dependency_id")
        _validate_version(self.version, "dependency version")


@dataclass(frozen=True, slots=True)
class ContextAssignment:
    """The exact project, projection, and memory versions assigned to a task."""

    assignment_id: str
    project_id: str
    project_version: int
    projection_id: str
    projection_version: int
    issue_receipt_id: str
    memory_versions: tuple[DependencyRef, ...] = ()
    source_dependencies: tuple[DependencyRef, ...] = ()
    applicability_key: str = ""
    time_bucket: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.assignment_id, str):
            raise ValueError("assignment_id must be a string")
        for value, name in (
            (self.assignment_id, "assignment_id"),
            (self.project_id, "project_id"),
            (self.projection_id, "projection_id"),
            (self.issue_receipt_id, "issue_receipt_id"),
            (self.applicability_key, "applicability_key"),
            (self.time_bucket, "time_bucket"),
        ):
            _validate_token(value, name)
        _validate_version(self.project_version, "project version")
        _validate_version(self.projection_version, "projection version")
        _validate_tuple(self.memory_versions, "memory_versions")
        _validate_tuple(self.source_dependencies, "source_dependencies")
        if len(self.memory_versions) + len(self.source_dependencies) > MAX_DEPENDENCIES:
            raise ValueError("assignment dependencies exceed the bounded limit")
        if any(not isinstance(item, DependencyRef) for item in self.memory_versions):
            raise ValueError("memory_versions must contain DependencyRef values")
        if any(not isinstance(item, DependencyRef) for item in self.source_dependencies):
            raise ValueError("source_dependencies must contain DependencyRef values")
        if any(item.kind is not DependencyKind.MEMORY for item in self.memory_versions):
            raise ValueError("memory_versions must contain memory dependencies")
        if any(item.kind is not DependencyKind.SOURCE for item in self.source_dependencies):
            raise ValueError("source_dependencies must contain source dependencies")
        if len(set(self.memory_versions)) != len(self.memory_versions):
            raise ValueError("memory_versions must be unique")
        if len(set(self.source_dependencies)) != len(self.source_dependencies):
            raise ValueError("source_dependencies must be unique")

    @property
    def dependency_refs(self) -> tuple[DependencyRef, ...]:
        return (
            DependencyRef(DependencyKind.PROJECT, self.project_id, self.project_version),
            DependencyRef(DependencyKind.PROJECTION, self.projection_id, self.projection_version),
            *self.memory_versions,
            *self.source_dependencies,
        )


@dataclass(frozen=True, slots=True)
class ActionEnvelope:
    """A bounded observable operation shape without arguments or payloads."""

    sequence: int
    kind: EnvelopeKind
    operation_code: str
    target_class: str
    status: OutcomeStatus = OutcomeStatus.UNKNOWN

    def __post_init__(self) -> None:
        _validate_enum(self.kind, EnvelopeKind, "envelope kind")
        _validate_enum(self.status, OutcomeStatus, "action status")
        if (
            isinstance(self.sequence, bool)
            or not isinstance(self.sequence, int)
            or self.sequence < 1
        ):
            raise ValueError("action sequence must be a positive integer")
        _validate_token(self.operation_code, "operation_code")
        _validate_token(self.target_class, "target_class")

    @property
    def signature_part(self) -> tuple[str, str, str]:
        return (self.kind.value, self.operation_code, self.target_class)


@dataclass(frozen=True, slots=True)
class ExternalResult:
    """An observable result classification, never a provider or model claim."""

    status: OutcomeStatus
    source: EvidenceSource
    verification: VerificationStrength
    result_code: str

    def __post_init__(self) -> None:
        _validate_enum(self.status, OutcomeStatus, "external result status")
        _validate_enum(self.source, EvidenceSource, "external result source")
        _validate_enum(self.verification, VerificationStrength, "verification strength")
        _validate_token(self.result_code, "result_code")
        if (
            self.verification is VerificationStrength.STRONG
            and self.source is EvidenceSource.CLIENT_DECLARATION
        ):
            raise ValueError("client declaration cannot be strong external verification")


@dataclass(frozen=True, slots=True)
class UserCorrection:
    """A typed correction marker; the correction text itself is intentionally absent."""

    correction_id: str
    target_dependency: DependencyRef
    disposition: CorrectionDisposition
    reason_code: str

    def __post_init__(self) -> None:
        _validate_enum(self.disposition, CorrectionDisposition, "correction disposition")
        if not isinstance(self.target_dependency, DependencyRef):
            raise ValueError("correction target must be a DependencyRef")
        _validate_token(self.correction_id, "correction_id")
        _validate_token(self.reason_code, "correction reason")


@dataclass(frozen=True, slots=True)
class OutcomeReceipt:
    """One immutable, privacy-bounded observable task receipt."""

    receipt_id: str
    receipt_version: int
    task_id: str
    task_kind: str
    assignment: ContextAssignment
    acknowledgement: Acknowledgement = Acknowledgement.NOT_OBSERVED
    declared_use: DeclaredUse = DeclaredUse.NOT_DECLARED
    action_envelopes: tuple[ActionEnvelope, ...] = ()
    completion: CompletionStatus = CompletionStatus.UNKNOWN
    external_result: ExternalResult | None = None
    user_correction: UserCorrection | None = None
    invalidation_dependencies: tuple[DependencyRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.assignment, ContextAssignment):
            raise ValueError("assignment must be a ContextAssignment")
        _validate_enum(self.acknowledgement, Acknowledgement, "acknowledgement")
        _validate_enum(self.declared_use, DeclaredUse, "declared use")
        _validate_enum(self.completion, CompletionStatus, "completion status")
        _validate_token(self.receipt_id, "receipt_id")
        _validate_version(self.receipt_version, "receipt version")
        _validate_token(self.task_id, "task_id")
        _validate_token(self.task_kind, "task_kind")
        _validate_tuple(self.action_envelopes, "action_envelopes")
        _validate_tuple(self.invalidation_dependencies, "invalidation_dependencies")
        if len(self.action_envelopes) > MAX_ACTION_ENVELOPES:
            raise ValueError("action envelopes exceed the bounded limit")
        if any(not isinstance(item, ActionEnvelope) for item in self.action_envelopes):
            raise ValueError("action_envelopes must contain ActionEnvelope values")
        sequences = tuple(item.sequence for item in self.action_envelopes)
        if sequences != tuple(range(1, len(sequences) + 1)):
            raise ValueError("action envelope sequences must be contiguous")
        if len(self.invalidation_dependencies) > MAX_DEPENDENCIES:
            raise ValueError("invalidation dependencies exceed the bounded limit")
        if any(not isinstance(item, DependencyRef) for item in self.invalidation_dependencies):
            raise ValueError("invalidation dependencies must contain DependencyRef values")
        if len(set(self.invalidation_dependencies)) != len(self.invalidation_dependencies):
            raise ValueError("invalidation dependencies must be unique")
        if self.external_result is not None and not isinstance(
            self.external_result, ExternalResult
        ):
            raise ValueError("external_result must be an ExternalResult")
        if self.user_correction is not None and not isinstance(
            self.user_correction, UserCorrection
        ):
            raise ValueError("user_correction must be a UserCorrection")
        all_dependencies = (*self.assignment.dependency_refs, *self.invalidation_dependencies)
        if len(set(all_dependencies)) != len(all_dependencies):
            raise ValueError("receipt dependencies must be unique")
        assigned_dependencies = set(self.assignment.dependency_refs)
        if (
            self.user_correction is not None
            and self.user_correction.target_dependency not in assigned_dependencies
        ):
            raise ValueError("correction target must be an assigned dependency")

    @property
    def dependency_refs(self) -> tuple[DependencyRef, ...]:
        return (*self.assignment.dependency_refs, *self.invalidation_dependencies)

    @property
    def outcome_dependency(self) -> DependencyRef:
        return DependencyRef(DependencyKind.OUTCOME, self.receipt_id, self.receipt_version)


@dataclass(frozen=True, slots=True)
class FailureReceipt:
    schema_version: int
    failure_code: FailureCode
    per_run_artifact_ref: str

    def __post_init__(self) -> None:
        _validate_version(self.schema_version, "failure schema version")
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError("failure schema version is unsupported")
        _validate_enum(self.failure_code, FailureCode, "failure code")
        _validate_token(self.per_run_artifact_ref, "per-run artifact reference")


@dataclass(frozen=True, slots=True)
class ReceiptAdmission:
    status: AdmissionStatus
    receipt: OutcomeReceipt | None = None
    failure: FailureReceipt | None = None

    def __post_init__(self) -> None:
        _validate_enum(self.status, AdmissionStatus, "admission status")
        if self.receipt is not None and not isinstance(self.receipt, OutcomeReceipt):
            raise ValueError("receipt must be an OutcomeReceipt")
        if self.failure is not None and not isinstance(self.failure, FailureReceipt):
            raise ValueError("failure must be a FailureReceipt")
        if self.status is AdmissionStatus.REJECTED:
            if self.receipt is not None or self.failure is None:
                raise ValueError("rejected admissions require only a failure")
        elif self.receipt is None or self.failure is not None:
            raise ValueError("accepted admissions require only a receipt")


@dataclass(frozen=True, slots=True)
class LifecycleMutation:
    dependency: DependencyRef
    reason: InvalidationReason
    affected_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.dependency, DependencyRef):
            raise ValueError("dependency must be a DependencyRef")
        _validate_enum(self.reason, InvalidationReason, "invalidation reason")
        _validate_nonnegative_int(self.affected_count, "affected count")


class OutcomeReceiptLedger:
    """Bounded in-memory receipt admission with correction and purge barriers."""

    def __init__(self, *, run_id: str, max_receipts: int = MAX_RECEIPTS) -> None:
        _validate_token(run_id, "run_id")
        if (
            isinstance(max_receipts, bool)
            or not isinstance(max_receipts, int)
            or not 1 <= max_receipts <= MAX_RECEIPTS
        ):
            raise ValueError("max_receipts is outside the bounded limit")
        self._run_id = run_id
        self._max_receipts = max_receipts
        self._receipts: list[OutcomeReceipt] = []
        self._by_id: dict[str, OutcomeReceipt] = {}
        self._invalidated: set[DependencyRef] = set()
        self._purged: set[DependencyRef] = set()
        self._failure_ordinal = 0

    @property
    def receipts(self) -> tuple[OutcomeReceipt, ...]:
        return tuple(self._receipts)

    @property
    def active_receipts(self) -> tuple[OutcomeReceipt, ...]:
        return tuple(
            receipt
            for receipt in self._receipts
            if not set(receipt.dependency_refs).intersection(self._invalidated)
        )

    def append(self, receipt: OutcomeReceipt) -> ReceiptAdmission:
        if not isinstance(receipt, OutcomeReceipt):
            raise ValueError("receipt must be an OutcomeReceipt")
        if any(dependency in self._purged for dependency in receipt.dependency_refs):
            return self._reject(FailureCode.PURGED_DEPENDENCY)
        prior = self._by_id.get(receipt.receipt_id)
        if prior is not None:
            if prior == receipt:
                return ReceiptAdmission(AdmissionStatus.IDEMPOTENT, receipt=prior)
            return self._reject(FailureCode.DUPLICATE_CONFLICT)
        if len(self._receipts) >= self._max_receipts:
            return self._reject(FailureCode.RECEIPT_LIMIT)
        self._receipts.append(receipt)
        self._by_id[receipt.receipt_id] = receipt
        return ReceiptAdmission(AdmissionStatus.ACCEPTED, receipt=receipt)

    def invalidate(
        self, dependency: DependencyRef, *, reason: InvalidationReason
    ) -> LifecycleMutation:
        if not isinstance(dependency, DependencyRef):
            raise ValueError("dependency must be a DependencyRef")
        _validate_enum(reason, InvalidationReason, "invalidation reason")
        if reason is InvalidationReason.TERMINAL_PURGE:
            raise ValueError("terminal purge must use purge()")
        self._invalidated.add(dependency)
        affected = sum(dependency in receipt.dependency_refs for receipt in self._receipts)
        return LifecycleMutation(dependency, reason, affected)

    def purge(self, dependency: DependencyRef) -> LifecycleMutation:
        """Terminally remove linked receipts and retain only an opaque barrier."""

        if not isinstance(dependency, DependencyRef):
            raise ValueError("dependency must be a DependencyRef")
        self._purged.add(dependency)
        removed = [receipt for receipt in self._receipts if dependency in receipt.dependency_refs]
        self._receipts = [
            receipt for receipt in self._receipts if dependency not in receipt.dependency_refs
        ]
        self._by_id = {receipt.receipt_id: receipt for receipt in self._receipts}
        self._invalidated.discard(dependency)
        return LifecycleMutation(dependency, InvalidationReason.TERMINAL_PURGE, len(removed))

    def inspectable_state(self) -> dict[str, int]:
        """Return counts only; purged IDs and receipt identifiers are not retained here."""

        return {
            "receipt_count": len(self._receipts),
            "active_receipt_count": len(self.active_receipts),
            "invalidated_dependency_count": len(self._invalidated),
            "purged_dependency_count": len(self._purged),
        }

    def _reject(self, code: FailureCode) -> ReceiptAdmission:
        self._failure_ordinal += 1
        return ReceiptAdmission(
            AdmissionStatus.REJECTED,
            failure=FailureReceipt(
                schema_version=SCHEMA_VERSION,
                failure_code=code,
                per_run_artifact_ref=f"{self._run_id}-failure-{self._failure_ordinal}",
            ),
        )


@dataclass(frozen=True, slots=True)
class ApplicabilityBoundary:
    """Explicit project/task boundary for a candidate procedure."""

    project_id: str
    task_kind: str
    applicability_key: str
    precondition_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_token(self.project_id, "applicability project_id")
        _validate_token(self.task_kind, "applicability task_kind")
        _validate_token(self.applicability_key, "applicability key")
        _validate_tuple(self.precondition_codes, "precondition_codes")
        _validate_unique_tokens(self.precondition_codes, "precondition_codes", MAX_GUARDS)


@dataclass(frozen=True, slots=True)
class RepairTest:
    """Content-free repair evidence required before a proposal can be issued."""

    test_id: str
    mutation: InvalidationReason
    passed: bool

    def __post_init__(self) -> None:
        _validate_enum(self.mutation, InvalidationReason, "repair mutation")
        _validate_bool(self.passed, "repair test passed")
        _validate_token(self.test_id, "repair test_id")


@dataclass(frozen=True, slots=True)
class PurgeClosure:
    """Explicit terminal cleanup coverage for every proposal dependency."""

    dependency_refs: tuple[DependencyRef, ...]
    closed: bool = False

    def __post_init__(self) -> None:
        _validate_tuple(self.dependency_refs, "purge closure dependencies")
        _validate_bool(self.closed, "purge closure closed")
        if len(self.dependency_refs) > MAX_DEPENDENCIES:
            raise ValueError("purge closure exceeds the bounded limit")
        if any(not isinstance(item, DependencyRef) for item in self.dependency_refs):
            raise ValueError("purge closure must contain DependencyRef values")
        if len(set(self.dependency_refs)) != len(self.dependency_refs):
            raise ValueError("purge closure dependencies must be unique")


@dataclass(frozen=True, slots=True)
class ProcedureProposal:
    """An advisory candidate; this model intentionally has no promotion state."""

    proposal_id: str
    applicability: ApplicabilityBoundary
    action_signature: tuple[tuple[str, str, str], ...]
    influence_dependencies: tuple[DependencyRef, ...]
    outcome_dependencies: tuple[DependencyRef, ...]
    negative_guards: tuple[str, ...]
    repair_tests: tuple[RepairTest, ...]
    purge_closure: PurgeClosure
    supporting_receipt_ids: tuple[str, ...]
    supporting_task_ids: tuple[str, ...]
    strong_external_receipt_ids: tuple[str, ...]
    recurrence_count: int
    strong_external_verification_count: int
    advisory_only: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.applicability, ApplicabilityBoundary):
            raise ValueError("applicability must be an ApplicabilityBoundary")
        _validate_bool(self.advisory_only, "advisory_only")
        if not self.advisory_only:
            raise ValueError("procedure proposals must remain advisory")
        _validate_token(self.proposal_id, "proposal_id")
        _validate_tuple(self.action_signature, "action_signature")
        _validate_tuple(self.influence_dependencies, "influence_dependencies")
        _validate_tuple(self.outcome_dependencies, "outcome_dependencies")
        _validate_tuple(self.negative_guards, "negative_guards")
        _validate_tuple(self.repair_tests, "repair_tests")
        _validate_tuple(self.supporting_receipt_ids, "supporting_receipt_ids")
        _validate_tuple(self.supporting_task_ids, "supporting_task_ids")
        _validate_tuple(self.strong_external_receipt_ids, "strong_external_receipt_ids")
        if len(self.influence_dependencies) > MAX_DEPENDENCIES:
            raise ValueError("influence_dependencies exceed the bounded limit")
        if len(self.outcome_dependencies) > MAX_DEPENDENCIES:
            raise ValueError("outcome_dependencies exceed the bounded limit")
        if len(self.repair_tests) > MAX_REPAIR_TESTS:
            raise ValueError("repair_tests exceed the bounded limit")
        if any(not isinstance(item, DependencyRef) for item in self.influence_dependencies):
            raise ValueError("influence_dependencies must contain DependencyRef values")
        if any(not isinstance(item, DependencyRef) for item in self.outcome_dependencies):
            raise ValueError("outcome_dependencies must contain DependencyRef values")
        if any(not isinstance(item, RepairTest) for item in self.repair_tests):
            raise ValueError("repair_tests must contain RepairTest values")
        if not self.action_signature or len(self.action_signature) > MAX_ACTION_ENVELOPES:
            raise ValueError("procedure action signature is outside the bounded limit")
        for part in self.action_signature:
            if not isinstance(part, tuple) or len(part) != 3:
                raise ValueError("procedure action signature parts are malformed")
            for value in part:
                _validate_token(value, "procedure action signature")
        if not self.influence_dependencies or not self.outcome_dependencies:
            raise ValueError("procedure dependencies are required")
        if any(item.kind is DependencyKind.OUTCOME for item in self.influence_dependencies):
            raise ValueError("influence_dependencies cannot contain outcome dependencies")
        if any(item.kind is not DependencyKind.OUTCOME for item in self.outcome_dependencies):
            raise ValueError("outcome_dependencies must contain outcome dependencies")
        if len(set(self.influence_dependencies)) != len(self.influence_dependencies):
            raise ValueError("influence_dependencies must be unique")
        if len(set(self.outcome_dependencies)) != len(self.outcome_dependencies):
            raise ValueError("outcome_dependencies must be unique")
        _validate_unique_tokens(self.supporting_receipt_ids, "supporting_receipt_ids", MAX_RECEIPTS)
        _validate_unique_tokens(self.supporting_task_ids, "supporting_task_ids", MAX_RECEIPTS)
        _validate_unique_tokens(
            self.strong_external_receipt_ids,
            "strong_external_receipt_ids",
            MAX_RECEIPTS,
        )
        if len(self.supporting_receipt_ids) != len(self.outcome_dependencies):
            raise ValueError("supporting receipts must match outcome dependencies")
        if (
            tuple(item.dependency_id for item in self.outcome_dependencies)
            != self.supporting_receipt_ids
        ):
            raise ValueError("supporting receipts must match outcome dependency order")
        if self.recurrence_count != len(self.supporting_receipt_ids):
            raise ValueError("recurrence count must match independent supporting receipts")
        if self.recurrence_count != len(self.supporting_task_ids):
            raise ValueError("recurrence count must match independent supporting tasks")
        _validate_nonnegative_int(self.recurrence_count, "recurrence count")
        if not 1 <= self.recurrence_count <= MAX_RECEIPTS:
            raise ValueError("recurrence count is outside the bounded limit")
        _validate_nonnegative_int(self.strong_external_verification_count, "verification count")
        if not 0 <= self.strong_external_verification_count <= MAX_RECEIPTS:
            raise ValueError("verification count is outside the bounded limit")
        if self.strong_external_verification_count > self.recurrence_count:
            raise ValueError("verification count cannot exceed recurrence count")
        if self.strong_external_verification_count != len(self.strong_external_receipt_ids):
            raise ValueError("verification count must match strong supporting receipts")
        if not set(self.strong_external_receipt_ids).issubset(self.supporting_receipt_ids):
            raise ValueError("strong supporting receipts must be proposal support")
        _validate_unique_tokens(self.negative_guards, "negative_guards", MAX_GUARDS)
        if not self.repair_tests:
            raise ValueError("repair tests are required and bounded")
        if len({item.test_id for item in self.repair_tests}) != len(self.repair_tests):
            raise ValueError("repair tests must be unique")
        if not isinstance(self.purge_closure, PurgeClosure):
            raise ValueError("purge_closure must be a PurgeClosure")
        if not self.purge_closure.closed:
            raise ValueError("purge closure must be closed")
        required_closure = frozenset((*self.influence_dependencies, *self.outcome_dependencies))
        if not required_closure.issubset(frozenset(self.purge_closure.dependency_refs)):
            raise ValueError("purge closure must cover proposal dependencies")


@dataclass(frozen=True, slots=True)
class LearningDecision:
    status: ProposalStatus
    reasons: tuple[ProposalReason, ...]
    proposal: ProcedureProposal | None
    eligible_receipt_count: int
    recurrence_count: int
    strong_external_verification_count: int
    shadow_only: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        _validate_enum(self.status, ProposalStatus, "proposal status")
        _validate_bool(self.shadow_only, "shadow_only")
        if not self.shadow_only:
            raise ValueError("learning decisions must remain shadow-only")
        _validate_tuple(self.reasons, "proposal reasons")
        if len(self.reasons) > MAX_GUARDS:
            raise ValueError("proposal reasons exceed the bounded limit")
        if any(not isinstance(item, ProposalReason) for item in self.reasons):
            raise ValueError("proposal reasons must contain ProposalReason values")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("proposal reasons must be unique")
        if self.proposal is not None and not isinstance(self.proposal, ProcedureProposal):
            raise ValueError("proposal must be a ProcedureProposal")
        if self.status is ProposalStatus.PROPOSED:
            if self.proposal is None or self.reasons:
                raise ValueError("proposed decisions require one proposal and no reasons")
        elif self.proposal is not None or not self.reasons:
            raise ValueError("rejected decisions require reasons and no proposal")
        for value, name in (
            (self.eligible_receipt_count, "eligible receipt count"),
            (self.recurrence_count, "recurrence count"),
            (self.strong_external_verification_count, "verification count"),
        ):
            _validate_nonnegative_int(value, name)
            if value > MAX_RECEIPTS:
                raise ValueError(f"{name} exceeds the bounded limit")


def propose_procedure(
    receipts: Sequence[OutcomeReceipt],
    *,
    proposal_id: str,
    applicability: ApplicabilityBoundary | None,
    negative_guards: Sequence[str],
    repair_tests: Sequence[RepairTest],
    purge_closure: PurgeClosure | None,
    invalidated_dependencies: Collection[DependencyRef] = frozenset(),
) -> LearningDecision:
    """Derive one deterministic shadow proposal from observable receipts.

    A proposal requires either two independent successful receipts with the
    same bounded action signature or one strong non-client external result.
    Every other gate is explicit and fail-closed.  The returned proposal is
    always advisory; this function cannot publish or promote it.
    """

    if not isinstance(receipts, Sequence) or isinstance(receipts, (str, bytes)):
        raise ValueError("receipts must be a sequence")
    if len(receipts) > MAX_RECEIPTS:
        raise ValueError("receipts exceed the bounded limit")
    if not isinstance(repair_tests, Sequence) or isinstance(repair_tests, (str, bytes)):
        raise ValueError("repair_tests must be a sequence")
    if len(repair_tests) > MAX_REPAIR_TESTS:
        raise ValueError("repair_tests exceed the bounded limit")
    if not isinstance(invalidated_dependencies, Collection):
        raise ValueError("invalidated_dependencies must be a collection")
    if len(invalidated_dependencies) > MAX_DEPENDENCIES:
        raise ValueError("invalidated dependencies exceed the bounded limit")
    if applicability is not None and not isinstance(applicability, ApplicabilityBoundary):
        raise ValueError("applicability must be an ApplicabilityBoundary")
    if purge_closure is not None and not isinstance(purge_closure, PurgeClosure):
        raise ValueError("purge_closure must be a PurgeClosure")
    _validate_token(proposal_id, "proposal_id")

    reasons: list[ProposalReason] = []
    invalidated_items = tuple(invalidated_dependencies)
    if any(not isinstance(item, DependencyRef) for item in invalidated_items):
        raise ValueError("invalidated dependencies must contain DependencyRef values")
    if len(set(invalidated_items)) != len(invalidated_items):
        raise ValueError("invalidated dependencies must be unique")
    invalidated = frozenset(invalidated_items)
    if applicability is None:
        reasons.append(ProposalReason.EXPLICIT_APPLICABILITY_REQUIRED)
    _validate_candidate_tokens(negative_guards, "negative guards", MAX_GUARDS)
    if not negative_guards:
        reasons.append(ProposalReason.NEGATIVE_GUARDS_REQUIRED)
    if not repair_tests:
        reasons.append(ProposalReason.REPAIR_TESTS_REQUIRED)
    else:
        if any(not isinstance(test, RepairTest) for test in repair_tests):
            raise ValueError("repair_tests must contain RepairTest values")
        if len({test.test_id for test in repair_tests}) != len(repair_tests):
            raise ValueError("repair_tests must be unique")
        if any(not test.passed for test in repair_tests):
            reasons.append(ProposalReason.REPAIR_TEST_FAILED)
    if purge_closure is None or not purge_closure.closed:
        reasons.append(ProposalReason.PURGE_CLOSURE_REQUIRED)

    by_receipt_id: dict[str, OutcomeReceipt] = {}
    duplicate_reasons: list[ProposalReason] = []
    for receipt in receipts:
        if not isinstance(receipt, OutcomeReceipt):
            raise ValueError("receipts must contain OutcomeReceipt values")
        prior = by_receipt_id.get(receipt.receipt_id)
        if prior is None:
            by_receipt_id[receipt.receipt_id] = receipt
        elif prior == receipt:
            duplicate_reasons.append(ProposalReason.DUPLICATE_RECEIPT_INPUT)
        else:
            duplicate_reasons.append(ProposalReason.DUPLICATE_RECEIPT_CONFLICT)
    if duplicate_reasons:
        return LearningDecision(
            ProposalStatus.REJECTED,
            tuple(dict.fromkeys(duplicate_reasons)),
            None,
            0,
            0,
            0,
        )

    ordered = tuple(sorted(by_receipt_id.values(), key=lambda item: item.receipt_id))
    eligible = tuple(
        receipt
        for receipt in ordered
        if applicability is not None
        and receipt.assignment.project_id == applicability.project_id
        and receipt.task_kind == applicability.task_kind
        and receipt.assignment.applicability_key == applicability.applicability_key
        and not set(receipt.dependency_refs).intersection(invalidated)
    )
    successes = tuple(receipt for receipt in eligible if _is_observable_success(receipt))
    signatures = {
        tuple(item.signature_part for item in receipt.action_envelopes) for receipt in successes
    }
    if len(signatures) > 1:
        reasons.append(ProposalReason.ACTION_SIGNATURE_DISAGREEMENT)
    signature = next(iter(sorted(signatures)), ())
    task_ids = {receipt.task_id for receipt in successes}
    recurrence_count = len(task_ids)
    if recurrence_count != len(successes):
        reasons.append(ProposalReason.NON_INDEPENDENT_TASK_EVIDENCE)
    strong_count = sum(
        1
        for receipt in successes
        if receipt.external_result is not None
        and receipt.external_result.verification is VerificationStrength.STRONG
    )
    if not successes:
        reasons.append(ProposalReason.NO_OBSERVABLE_SUCCESS)
    elif recurrence_count < 2 and strong_count == 0:
        reasons.append(ProposalReason.RECURRENCE_OR_STRONG_VERIFICATION_REQUIRED)

    influence_dependencies = _influence_dependencies(successes)
    outcome_dependencies = tuple(receipt.outcome_dependency for receipt in successes)
    if not influence_dependencies:
        reasons.append(ProposalReason.INFLUENCE_DEPENDENCIES_REQUIRED)
    if not outcome_dependencies:
        reasons.append(ProposalReason.OUTCOME_DEPENDENCIES_REQUIRED)
    expected_closure = frozenset((*influence_dependencies, *outcome_dependencies))
    if purge_closure is not None and (
        not expected_closure.issubset(frozenset(purge_closure.dependency_refs))
    ):
        reasons.append(ProposalReason.PURGE_CLOSURE_REQUIRED)

    unique_reasons = tuple(dict.fromkeys(reasons))
    if unique_reasons:
        return LearningDecision(
            ProposalStatus.REJECTED,
            unique_reasons,
            None,
            len(eligible),
            recurrence_count,
            strong_count,
        )

    if applicability is None or purge_closure is None:
        raise AssertionError(
            "successful proposal gates must have explicit applicability and closure"
        )

    proposal = ProcedureProposal(
        proposal_id=proposal_id,
        applicability=applicability,
        action_signature=signature,
        influence_dependencies=influence_dependencies,
        outcome_dependencies=outcome_dependencies,
        negative_guards=tuple(sorted(set(negative_guards))),
        repair_tests=tuple(sorted(repair_tests, key=lambda item: item.test_id)),
        purge_closure=purge_closure,
        supporting_receipt_ids=tuple(receipt.receipt_id for receipt in successes),
        supporting_task_ids=tuple(receipt.task_id for receipt in successes),
        strong_external_receipt_ids=tuple(
            receipt.receipt_id
            for receipt in successes
            if receipt.external_result is not None
            and receipt.external_result.verification is VerificationStrength.STRONG
        ),
        recurrence_count=recurrence_count,
        strong_external_verification_count=strong_count,
    )
    return LearningDecision(
        ProposalStatus.PROPOSED,
        (),
        proposal,
        len(eligible),
        recurrence_count,
        strong_count,
    )


def receipt_mapping(receipt: OutcomeReceipt) -> dict[str, Any]:
    """Return the JSON-shaped allowlisted receipt without private payload fields."""

    if not isinstance(receipt, OutcomeReceipt):
        raise ValueError("receipt must be an OutcomeReceipt")
    mapping = cast(dict[str, Any], _json_safe(asdict(receipt)))
    mapping["schema_version"] = SCHEMA_VERSION
    return mapping


def serialize_receipt(receipt: OutcomeReceipt) -> str:
    return json.dumps(receipt_mapping(receipt), sort_keys=True, separators=(",", ":"))


def validate_receipt_mapping(raw: Mapping[str, object]) -> None:
    """Check top-level receipt shape without echoing rejected data."""

    if not isinstance(raw, Mapping):
        raise ValueError("receipt must be a mapping")
    if any(not isinstance(key, str) for key in raw):
        raise ValueError("receipt keys must be strings")
    keys = set(raw)
    if _contains_forbidden_key(raw):
        raise ValueError("receipt contains a forbidden field")
    allowed = frozenset(
        {
            "receipt_id",
            "schema_version",
            "receipt_version",
            "task_id",
            "task_kind",
            "assignment",
            "acknowledgement",
            "declared_use",
            "action_envelopes",
            "completion",
            "external_result",
            "user_correction",
            "invalidation_dependencies",
        }
    )
    if keys != allowed:
        raise ValueError("receipt fields must match the allowlist")
    if type(raw.get("schema_version")) is not int or raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("receipt schema_version is unsupported")


def _is_observable_success(receipt: OutcomeReceipt) -> bool:
    result = receipt.external_result
    return (
        receipt.declared_use is DeclaredUse.USED
        and bool(receipt.action_envelopes)
        and all(item.status is OutcomeStatus.SUCCEEDED for item in receipt.action_envelopes)
        and receipt.completion is CompletionStatus.COMPLETED
        and result is not None
        and result.status is OutcomeStatus.SUCCEEDED
        and result.source is not EvidenceSource.CLIENT_DECLARATION
        and receipt.user_correction is None
    )


def _influence_dependencies(receipts: Sequence[OutcomeReceipt]) -> tuple[DependencyRef, ...]:
    dependencies = {
        dependency
        for receipt in receipts
        for dependency in receipt.assignment.dependency_refs
        if dependency.kind is not DependencyKind.OUTCOME
    }
    return tuple(
        sorted(dependencies, key=lambda item: (item.kind.value, item.dependency_id, item.version))
    )


def _validate_candidate_tokens(values: Sequence[str], name: str, limit: int) -> None:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        raise ValueError(f"{name} must be a sequence")
    _validate_unique_tokens(values, name, limit)


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).casefold() in _FORBIDDEN_FIELDS or _contains_forbidden_key(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def _json_safe(value: object) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(nested) for key, nested in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
