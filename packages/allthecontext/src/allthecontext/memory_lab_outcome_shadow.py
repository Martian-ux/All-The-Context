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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, cast

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
    ACTION_SIGNATURE_DISAGREEMENT = "action_signature_disagreement"
    EXPLICIT_APPLICABILITY_REQUIRED = "explicit_applicability_required"
    NEGATIVE_GUARDS_REQUIRED = "negative_guards_required"
    REPAIR_TESTS_REQUIRED = "repair_tests_required"
    REPAIR_TEST_FAILED = "repair_test_failed"
    SOURCE_DEPENDENCIES_REQUIRED = "source_dependencies_required"
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


def _validate_token(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TOKEN_LENGTH
        or value != value.strip()
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"{name} must be a bounded non-empty token")


def _validate_version(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


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
        if len(self.memory_versions) + len(self.source_dependencies) > MAX_DEPENDENCIES:
            raise ValueError("assignment dependencies exceed the bounded limit")
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
        _validate_token(self.receipt_id, "receipt_id")
        _validate_version(self.receipt_version, "receipt version")
        _validate_token(self.task_id, "task_id")
        _validate_token(self.task_kind, "task_kind")
        if len(self.action_envelopes) > MAX_ACTION_ENVELOPES:
            raise ValueError("action envelopes exceed the bounded limit")
        sequences = tuple(item.sequence for item in self.action_envelopes)
        if sequences != tuple(range(1, len(sequences) + 1)):
            raise ValueError("action envelope sequences must be contiguous")
        if len(self.invalidation_dependencies) > MAX_DEPENDENCIES:
            raise ValueError("invalidation dependencies exceed the bounded limit")
        if len(set(self.invalidation_dependencies)) != len(self.invalidation_dependencies):
            raise ValueError("invalidation dependencies must be unique")
        all_dependencies = set(self.assignment.dependency_refs)
        if (
            self.user_correction is not None
            and self.user_correction.target_dependency not in all_dependencies
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


@dataclass(frozen=True, slots=True)
class ReceiptAdmission:
    status: AdmissionStatus
    receipt: OutcomeReceipt | None = None
    failure: FailureReceipt | None = None


@dataclass(frozen=True, slots=True)
class LifecycleMutation:
    dependency: DependencyRef
    reason: InvalidationReason
    affected_count: int


class OutcomeReceiptLedger:
    """Bounded in-memory receipt admission with correction and purge barriers."""

    def __init__(self, *, run_id: str, max_receipts: int = MAX_RECEIPTS) -> None:
        _validate_token(run_id, "run_id")
        if not 1 <= max_receipts <= MAX_RECEIPTS:
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
        if reason is InvalidationReason.TERMINAL_PURGE:
            raise ValueError("terminal purge must use purge()")
        self._invalidated.add(dependency)
        affected = sum(dependency in receipt.dependency_refs for receipt in self._receipts)
        return LifecycleMutation(dependency, reason, affected)

    def purge(self, dependency: DependencyRef) -> LifecycleMutation:
        """Terminally remove linked receipts and retain only an opaque barrier."""

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
    precondition_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_token(self.project_id, "applicability project_id")
        _validate_token(self.task_kind, "applicability task_kind")
        _validate_unique_tokens(self.precondition_codes, "precondition_codes", MAX_GUARDS)


@dataclass(frozen=True, slots=True)
class RepairTest:
    """Content-free repair evidence required before a proposal can be issued."""

    test_id: str
    mutation: InvalidationReason
    passed: bool

    def __post_init__(self) -> None:
        _validate_token(self.test_id, "repair test_id")


@dataclass(frozen=True, slots=True)
class PurgeClosure:
    """Explicit terminal cleanup coverage for every proposal dependency."""

    dependency_refs: tuple[DependencyRef, ...]
    closed: bool = False

    def __post_init__(self) -> None:
        if len(self.dependency_refs) > MAX_DEPENDENCIES:
            raise ValueError("purge closure exceeds the bounded limit")
        if len(set(self.dependency_refs)) != len(self.dependency_refs):
            raise ValueError("purge closure dependencies must be unique")


@dataclass(frozen=True, slots=True)
class ProcedureProposal:
    """An advisory candidate; this model intentionally has no promotion state."""

    proposal_id: str
    applicability: ApplicabilityBoundary
    action_signature: tuple[tuple[str, str, str], ...]
    source_dependencies: tuple[DependencyRef, ...]
    outcome_dependencies: tuple[DependencyRef, ...]
    negative_guards: tuple[str, ...]
    repair_tests: tuple[RepairTest, ...]
    purge_closure: PurgeClosure
    supporting_receipt_ids: tuple[str, ...]
    recurrence_count: int
    strong_external_verification_count: int
    advisory_only: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        _validate_token(self.proposal_id, "proposal_id")
        if not self.action_signature or len(self.action_signature) > MAX_ACTION_ENVELOPES:
            raise ValueError("procedure action signature is outside the bounded limit")
        for part in self.action_signature:
            if len(part) != 3:
                raise ValueError("procedure action signature parts are malformed")
            for value in part:
                _validate_token(value, "procedure action signature")
        if not self.source_dependencies or not self.outcome_dependencies:
            raise ValueError("procedure dependencies are required")
        if not 1 <= self.recurrence_count <= MAX_RECEIPTS:
            raise ValueError("recurrence count is outside the bounded limit")
        if not 0 <= self.strong_external_verification_count <= MAX_RECEIPTS:
            raise ValueError("verification count is outside the bounded limit")
        _validate_unique_tokens(self.negative_guards, "negative_guards", MAX_GUARDS)
        if not self.repair_tests or len(self.repair_tests) > MAX_REPAIR_TESTS:
            raise ValueError("repair tests are required and bounded")
        if not self.purge_closure.closed:
            raise ValueError("purge closure must be closed")


@dataclass(frozen=True, slots=True)
class LearningDecision:
    status: ProposalStatus
    reasons: tuple[ProposalReason, ...]
    proposal: ProcedureProposal | None
    eligible_receipt_count: int
    recurrence_count: int
    strong_external_verification_count: int
    shadow_only: bool = field(default=True, init=False)


def propose_procedure(
    receipts: Sequence[OutcomeReceipt],
    *,
    proposal_id: str,
    applicability: ApplicabilityBoundary | None,
    negative_guards: Sequence[str],
    repair_tests: Sequence[RepairTest],
    purge_closure: PurgeClosure | None,
    invalidated_dependencies: Iterable[DependencyRef] = (),
) -> LearningDecision:
    """Derive one deterministic shadow proposal from observable receipts.

    A proposal requires either two independent successful receipts with the
    same bounded action signature or one strong non-client external result.
    Every other gate is explicit and fail-closed.  The returned proposal is
    always advisory; this function cannot publish or promote it.
    """

    reasons: list[ProposalReason] = []
    invalidated = frozenset(invalidated_dependencies)
    if len(invalidated) > MAX_DEPENDENCIES:
        raise ValueError("invalidated dependencies exceed the bounded limit")
    if applicability is None:
        reasons.append(ProposalReason.EXPLICIT_APPLICABILITY_REQUIRED)
    _validate_candidate_tokens(negative_guards, "negative guards", MAX_GUARDS)
    if not negative_guards:
        reasons.append(ProposalReason.NEGATIVE_GUARDS_REQUIRED)
    if not repair_tests:
        reasons.append(ProposalReason.REPAIR_TESTS_REQUIRED)
    elif any(not test.passed for test in repair_tests):
        reasons.append(ProposalReason.REPAIR_TEST_FAILED)
    if purge_closure is None or not purge_closure.closed:
        reasons.append(ProposalReason.PURGE_CLOSURE_REQUIRED)

    ordered = tuple(sorted(receipts, key=lambda item: item.receipt_id))
    if len(ordered) > MAX_RECEIPTS:
        raise ValueError("receipts exceed the bounded limit")
    eligible = tuple(
        receipt
        for receipt in ordered
        if applicability is not None
        and receipt.assignment.project_id == applicability.project_id
        and receipt.task_kind == applicability.task_kind
        and not set(receipt.dependency_refs).intersection(invalidated)
    )
    successes = tuple(
        receipt
        for receipt in eligible
        if _is_observable_success(receipt)
    )
    signatures = {
        tuple(item.signature_part for item in receipt.action_envelopes)
        for receipt in successes
    }
    if len(signatures) > 1:
        reasons.append(ProposalReason.ACTION_SIGNATURE_DISAGREEMENT)
    signature = next(iter(signatures), ())
    recurrence_count = len(successes)
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

    source_dependencies = _source_dependencies(successes)
    outcome_dependencies = tuple(receipt.outcome_dependency for receipt in successes)
    if not source_dependencies:
        reasons.append(ProposalReason.SOURCE_DEPENDENCIES_REQUIRED)
    if not outcome_dependencies:
        reasons.append(ProposalReason.OUTCOME_DEPENDENCIES_REQUIRED)
    expected_closure = frozenset((*source_dependencies, *outcome_dependencies))
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
        source_dependencies=source_dependencies,
        outcome_dependencies=outcome_dependencies,
        negative_guards=tuple(sorted(set(negative_guards))),
        repair_tests=tuple(sorted(repair_tests, key=lambda item: item.test_id)),
        purge_closure=purge_closure,
        supporting_receipt_ids=tuple(receipt.receipt_id for receipt in successes),
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

    mapping = cast(dict[str, Any], _json_safe(asdict(receipt)))
    mapping["schema_version"] = SCHEMA_VERSION
    return mapping


def serialize_receipt(receipt: OutcomeReceipt) -> str:
    return json.dumps(receipt_mapping(receipt), sort_keys=True, separators=(",", ":"))


def validate_receipt_mapping(raw: Mapping[str, object]) -> None:
    """Check top-level receipt shape without echoing rejected data."""

    if not isinstance(raw, Mapping):
        raise ValueError("receipt must be a mapping")
    keys = {str(key) for key in raw}
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
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("receipt schema_version is unsupported")


def _is_observable_success(receipt: OutcomeReceipt) -> bool:
    result = receipt.external_result
    return (
        receipt.declared_use is DeclaredUse.USED
        and bool(receipt.action_envelopes)
        and receipt.completion is CompletionStatus.COMPLETED
        and result is not None
        and result.status is OutcomeStatus.SUCCEEDED
        and result.source is not EvidenceSource.CLIENT_DECLARATION
        and receipt.user_correction is None
    )


def _source_dependencies(receipts: Sequence[OutcomeReceipt]) -> tuple[DependencyRef, ...]:
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
    if len(values) > limit:
        raise ValueError(f"{name} exceed the bounded limit")
    for value in values:
        _validate_token(value, name)


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
