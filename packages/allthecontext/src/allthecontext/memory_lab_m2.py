"""Isolated deterministic prototype for the Memory Lab M2 experiment.

This module is research-only and is not wired into Core, retrieval, or MCP
surfaces.  It deliberately accepts no harness oracle.  The compiler can see
only declared obligations and already-classified record metadata.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum

MAX_CANDIDATES = 12
MAX_OBLIGATIONS = 4
MAX_BUDGET_CHARS = 8_192
_RECEIPT_DOMAIN = b"atc-memory-lab-m2-receipt-v1\0"


class ContextRole(StrEnum):
    """Closed epistemic and operational roles visible to the compiler."""

    EVIDENCE = "evidence"
    CURRENT_CLAIM = "current_claim"
    CONSTRAINT = "constraint"
    HYPOTHESIS = "hypothesis"
    PROCEDURE = "procedure"
    WARNING = "warning"
    WORKING_DEPENDENCY = "working_dependency"


class CompilationStatus(StrEnum):
    """Closed terminal states for one compilation attempt."""

    ISSUED = "issued"
    ABSTAINED = "abstained"
    RETRY_GENERATION_CHANGE = "retry_generation_change"


class CompilationReason(StrEnum):
    """Content-free reason vocabulary used by field-minimized synthetic receipts."""

    PROJECTION_SEALED = "projection_sealed"
    OBLIGATION_COVERED = "obligation_covered"
    DELETION_REJECTED_OBLIGATION = "deletion_rejected_obligation"
    DELETION_REJECTED_DEPENDENCY = "deletion_rejected_dependency"
    MINIMALITY_CONFIRMED = "minimality_confirmed"
    BUDGET_INFEASIBLE = "budget_infeasible"
    OBLIGATION_INFEASIBLE = "obligation_infeasible"
    CUMULATIVE_DISCLOSURE_LIMIT = "cumulative_disclosure_limit"
    GENERATION_CHANGED = "generation_changed"
    CURRENT_VERSION_REREAD = "current_version_reread"


@dataclass(frozen=True, slots=True)
class ContextRecord:
    """One symbolic record presented to the research compiler."""

    key: str
    version: int
    content: str
    role: ContextRole
    coverage_ids: frozenset[str] = field(default_factory=frozenset)
    dependency_ids: frozenset[str] = field(default_factory=frozenset)
    relevance: int = 0
    authorized: bool = False
    temporally_current: bool = False
    applicable: bool = False
    allowed_fields: tuple[str, ...] = ("content",)

    def __post_init__(self) -> None:
        if not self.key or len(self.key) > 128:
            raise ValueError("record keys must be bounded non-empty strings")
        if self.version < 1:
            raise ValueError("record versions must be positive")
        if len(self.content) > MAX_BUDGET_CHARS:
            raise ValueError("record content exceeds the experiment hard cap")
        if self.relevance < 0:
            raise ValueError("record relevance must be non-negative")
        _validate_labels(self.coverage_ids, "coverage")
        _validate_labels(self.dependency_ids, "dependency")
        if self.key in self.dependency_ids:
            raise ValueError("record dependencies cannot be self-referential")
        if self.allowed_fields != ("content",):
            raise ValueError("the M2 prototype permits only the content field")

    @property
    def char_cost(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class ContextObligation:
    """A declared compiler obligation, not a harness success label."""

    obligation_id: str
    accepted_roles: frozenset[ContextRole]

    def __post_init__(self) -> None:
        if not self.obligation_id or len(self.obligation_id) > 128:
            raise ValueError("obligation ids must be bounded non-empty strings")
        if not self.accepted_roles:
            raise ValueError("obligations must accept at least one role")


@dataclass(frozen=True, slots=True)
class ContextNeed:
    """Fixed request and budget inputs supplied before compilation."""

    request_commitment: str
    obligations: tuple[ContextObligation, ...]
    character_budget: int
    page_size: int = 2
    prior_disclosure_chars: int = 0
    cumulative_disclosure_limit: int | None = None

    def __post_init__(self) -> None:
        if not self.request_commitment:
            raise ValueError("request commitment is required")
        if not 1 <= len(self.obligations) <= MAX_OBLIGATIONS:
            raise ValueError(f"one to {MAX_OBLIGATIONS} obligations are required")
        ids = tuple(item.obligation_id for item in self.obligations)
        if len(ids) != len(set(ids)):
            raise ValueError("obligation ids must be unique")
        if not 0 <= self.character_budget <= MAX_BUDGET_CHARS:
            raise ValueError("character budget is outside the experiment cap")
        if self.page_size < 1:
            raise ValueError("page size must be positive")
        if self.prior_disclosure_chars < 0:
            raise ValueError("prior disclosure must be non-negative")
        if self.cumulative_disclosure_limit is not None and self.cumulative_disclosure_limit < 0:
            raise ValueError("cumulative disclosure limit must be non-negative")


@dataclass(frozen=True, slots=True)
class SealedProjection:
    """Authorization/currentness/applicability-filtered immutable projection."""

    items: tuple[ContextRecord, ...]
    commitment: str


@dataclass(frozen=True, slots=True)
class DeletionTestReceipt:
    """One content-free synthetic test of deleting a selected item.

    The unkeyed digest is a linkable, dictionary-attackable commitment. It is
    suitable only for this opaque synthetic fixture, not as a production
    privacy mechanism.
    """

    item_commitment: str
    rejected_reason: CompilationReason


@dataclass(frozen=True, slots=True)
class CompilationReceipt:
    """Field-minimized synthetic observable receipt for one compilation.

    SHA-256 fields here are deterministic commitments, not redactions. A
    production design would require a separately reviewed keyed or blinded
    construction to resist dictionary attacks and cross-receipt linkability.
    """

    status: CompilationStatus
    sealed_projection_commitment: str
    selected_item_commitments: tuple[str, ...]
    deletion_tests: tuple[DeletionTestReceipt, ...]
    reason_code_multiset: tuple[tuple[str, int], ...]
    disclosure_chars: int
    semantic_output_digest: str
    cursor_page_shape: tuple[int, ...]
    timing_class: str
    learning_state_digest: str


@dataclass(frozen=True, slots=True)
class CompilationResult:
    """Internal selected records plus the synthetic experimental receipt."""

    selected: tuple[ContextRecord, ...]
    receipt: CompilationReceipt


def seal_projection(records: Sequence[ContextRecord]) -> SealedProjection:
    """Seal current authorized-and-applicable records before relevance work."""

    if len(records) > MAX_CANDIDATES:
        raise ValueError(f"M2 candidate sets are capped at {MAX_CANDIDATES}")
    keys = tuple(record.key for record in records)
    if len(keys) != len(set(keys)):
        raise ValueError("candidate keys must be unique")
    admitted = tuple(
        sorted(
            (
                record
                for record in records
                if record.authorized is True
                and record.temporally_current is True
                and record.applicable is True
            ),
            key=lambda item: item.key,
        )
    )
    commitment = _digest(
        "projection",
        *(f"{item.key}:{item.version}:{_digest('content', item.content)}" for item in admitted),
    )
    return SealedProjection(admitted, commitment)


class SealedProjectionMinimalCompiler:
    """Compile and confirm a sufficient one-deletion-minimal working set."""

    def compile(
        self,
        records: Sequence[ContextRecord],
        need: ContextNeed,
        *,
        reread_records: Sequence[ContextRecord] | None = None,
    ) -> CompilationResult:
        """Seal, compile, delete-test, current-reread, and issue deterministically."""

        projection = seal_projection(records)
        semantic_selection = self._initial_sufficient_set(
            projection.items,
            need,
            character_budget=None,
        )
        if not semantic_selection:
            return self._terminal(
                projection,
                (),
                need,
                CompilationStatus.ABSTAINED,
                (CompilationReason.OBLIGATION_INFEASIBLE,),
                (),
            )
        selected = list(
            self._initial_sufficient_set(
                projection.items,
                need,
                character_budget=need.character_budget,
            )
        )
        if not selected:
            return self._terminal(
                projection,
                (),
                need,
                CompilationStatus.ABSTAINED,
                (CompilationReason.BUDGET_INFEASIBLE,),
                (),
            )

        deletion_tests: list[DeletionTestReceipt] = []
        reasons: list[CompilationReason] = [
            CompilationReason.PROJECTION_SEALED,
            CompilationReason.OBLIGATION_COVERED,
        ]
        for candidate in tuple(selected):
            reduced = tuple(item for item in selected if item.key != candidate.key)
            failure = _sufficiency_failure(reduced, need.obligations)
            if failure is None:
                raise AssertionError("minimum-cardinality selection retained a removable item")
            reason = (
                CompilationReason.DELETION_REJECTED_DEPENDENCY
                if failure == "dependency"
                else CompilationReason.DELETION_REJECTED_OBLIGATION
            )
            deletion_tests.append(DeletionTestReceipt(_item_commitment(candidate), reason))
            reasons.append(reason)
        reasons.append(CompilationReason.MINIMALITY_CONFIRMED)

        selected_tuple = tuple(selected)
        disclosure_chars = sum(item.char_cost for item in selected_tuple)
        if disclosure_chars > need.character_budget:
            raise AssertionError("budget-feasible selection exceeded its character budget")
        if (
            need.cumulative_disclosure_limit is not None
            and need.prior_disclosure_chars + disclosure_chars > need.cumulative_disclosure_limit
        ):
            return self._terminal(
                projection,
                (),
                need,
                CompilationStatus.ABSTAINED,
                (CompilationReason.CUMULATIVE_DISCLOSURE_LIMIT,),
                (),
            )

        current = tuple(records if reread_records is None else reread_records)
        if not self._current_reread_matches(selected_tuple, current):
            return self._terminal(
                projection,
                (),
                need,
                CompilationStatus.RETRY_GENERATION_CHANGE,
                (CompilationReason.GENERATION_CHANGED,),
                (),
            )
        reasons.append(CompilationReason.CURRENT_VERSION_REREAD)
        return self._terminal(
            projection,
            selected_tuple,
            need,
            CompilationStatus.ISSUED,
            tuple(reasons),
            tuple(deletion_tests),
        )

    @staticmethod
    def _initial_sufficient_set(
        candidates: Sequence[ContextRecord],
        need: ContextNeed,
        *,
        character_budget: int | None,
    ) -> tuple[ContextRecord, ...]:
        """Choose an exact minimum-cardinality semantically sufficient set.

        Budget feasibility is deliberately evaluated by the next compiler
        state so semantic and budget terminal reasons remain distinguishable.
        """

        feasible: list[tuple[ContextRecord, ...]] = []
        for size in range(1, len(candidates) + 1):
            for subset in itertools.combinations(candidates, size):
                if (
                    character_budget is not None
                    and sum(item.char_cost for item in subset) > character_budget
                ):
                    continue
                if _sufficiency_failure(subset, need.obligations) is None:
                    feasible.append(subset)
            if feasible:
                break
        if not feasible:
            return ()
        return min(
            feasible,
            key=lambda subset: (
                sum(item.char_cost for item in subset),
                -sum(item.relevance for item in subset),
                tuple(item.key for item in subset),
            ),
        )

    @staticmethod
    def _current_reread_matches(
        selected: Sequence[ContextRecord],
        current_records: Sequence[ContextRecord],
    ) -> bool:
        current = {item.key: item for item in current_records}
        for item in selected:
            reread = current.get(item.key)
            if (
                reread is None
                or reread.version != item.version
                or reread.content != item.content
                or reread.authorized is not True
                or reread.temporally_current is not True
                or reread.applicable is not True
            ):
                return False
        return True

    @staticmethod
    def _terminal(
        projection: SealedProjection,
        selected: tuple[ContextRecord, ...],
        need: ContextNeed,
        status: CompilationStatus,
        reasons: tuple[CompilationReason, ...],
        deletion_tests: tuple[DeletionTestReceipt, ...],
    ) -> CompilationResult:
        ordered = tuple(sorted(selected, key=lambda item: item.key))
        disclosure_chars = sum(item.char_cost for item in ordered)
        page_shape = tuple(
            len(ordered[offset : offset + need.page_size])
            for offset in range(0, len(ordered), need.page_size)
        )
        reason_multiset = tuple(
            sorted((reason.value, count) for reason, count in Counter(reasons).items())
        )
        sealed_count = len(projection.items)
        subset_checks = (2**sealed_count) - 1
        timing_class = _logical_timing_class(sealed_count + (2 * subset_checks) + len(ordered))
        selected_commitments = tuple(_item_commitment(item) for item in ordered)
        semantic_digest = _digest(
            "semantic-output",
            *(item.content for item in ordered),
        )
        learning_digest = _digest(
            "learning-state",
            need.request_commitment,
            *(
                f"{item.version}:{commitment}"
                for item, commitment in zip(ordered, selected_commitments, strict=True)
            ),
        )
        return CompilationResult(
            ordered,
            CompilationReceipt(
                status=status,
                sealed_projection_commitment=projection.commitment,
                selected_item_commitments=selected_commitments,
                deletion_tests=deletion_tests,
                reason_code_multiset=reason_multiset,
                disclosure_chars=disclosure_chars,
                semantic_output_digest=semantic_digest,
                cursor_page_shape=page_shape,
                timing_class=timing_class,
                learning_state_digest=learning_digest,
            ),
        )


def selected_keys(result: CompilationResult) -> tuple[str, ...]:
    """Return internal symbolic keys for harness evaluation only."""

    return tuple(item.key for item in result.selected)


def receipt_observables(receipt: CompilationReceipt) -> tuple[object, ...]:
    """Return every paired-vault observable field in a fixed order."""

    return (
        receipt.status,
        receipt.sealed_projection_commitment,
        receipt.selected_item_commitments,
        receipt.deletion_tests,
        receipt.disclosure_chars,
        receipt.semantic_output_digest,
        receipt.reason_code_multiset,
        receipt.cursor_page_shape,
        receipt.timing_class,
        receipt.learning_state_digest,
    )


def serialize_receipt(receipt: CompilationReceipt) -> str:
    """Canonically serialize every externally visible receipt field."""

    return json.dumps(asdict(receipt), sort_keys=True, separators=(",", ":"))


def _sufficiency_failure(
    selected: Sequence[ContextRecord],
    obligations: Sequence[ContextObligation],
) -> str | None:
    selected_keys = {item.key for item in selected}
    if any(not item.dependency_ids <= selected_keys for item in selected):
        return "dependency"
    for obligation in obligations:
        if not any(
            obligation.obligation_id in item.coverage_ids and item.role in obligation.accepted_roles
            for item in selected
        ):
            return "obligation"
    return None


def _item_commitment(item: ContextRecord) -> str:
    return _digest("selected-item", item.key, str(item.version))


def _logical_timing_class(operation_count: int) -> str:
    if operation_count <= 32:
        return "logical_xs"
    if operation_count <= 256:
        return "logical_s"
    if operation_count <= 2_048:
        return "logical_m"
    return "logical_l"


def _digest(domain: str, *parts: str) -> str:
    digest = hashlib.sha256()
    digest.update(_RECEIPT_DOMAIN)
    digest.update(domain.encode("utf-8"))
    for part in parts:
        digest.update(b"\0")
        digest.update(part.encode("utf-8"))
    return digest.hexdigest()


def _validate_labels(labels: Iterable[str], name: str) -> None:
    values = tuple(labels)
    if len(values) > 32 or any(not value or len(value) > 128 for value in values):
        raise ValueError(f"{name} labels must be bounded non-empty strings")
