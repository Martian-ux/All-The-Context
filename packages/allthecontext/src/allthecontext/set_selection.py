"""Deterministic metadata-only set selection for retrieval candidates.

The selector consumes candidates only after policy, temporal, and task-
admissibility evaluation.  It never upgrades a failed upstream attestation and
does not accept record content, query text, or arbitrary diagnostic metadata.
Set-level signals are opaque, bounded labels computed by an authorized caller.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .retrieval_contracts import (
        SafeRetrievalDiagnostic,
        SetSelection,
        SetSelectionConstraints,
    )

MAX_SET_CANDIDATES = 10_000
MAX_SIGNAL_LABELS = 256
MAX_LABEL_CHARS = 256
MAX_BUDGET_COST = 10_000_000
MAX_BASE_UTILITY = 10_000_000


@dataclass(frozen=True, slots=True)
class SetSelectionCandidate:
    """Opaque candidate metadata supplied after all upstream hard gates.

    ``compatibility_groups`` are opt-in compatibility domains: two labeled
    candidates are compatible only when their groups overlap. Unlabeled
    candidates are neutral. A declaration in either candidate's
    ``incompatible_with`` set is a hard incompatibility.

    Redundancy and conflict groups are explicit upstream classifications. A
    selected set contains at most one candidate from each such group.
    Supporting evidence is selectable only after at least one key named by
    ``supports`` has been selected.
    """

    key: str
    budget_cost: int
    base_utility: int = 0
    semantic_facets: frozenset[str] = field(default_factory=frozenset)
    diversity_dimensions: frozenset[str] = field(default_factory=frozenset)
    compatibility_groups: frozenset[str] = field(default_factory=frozenset)
    incompatible_with: frozenset[str] = field(default_factory=frozenset)
    redundancy_groups: frozenset[str] = field(default_factory=frozenset)
    conflict_groups: frozenset[str] = field(default_factory=frozenset)
    supports: frozenset[str] = field(default_factory=frozenset)
    mandatory_interaction_preference: bool = False
    policy_authorized: bool = False
    temporally_eligible: bool = False
    task_admissible: bool = False

    @property
    def upstream_eligible(self) -> bool:
        """Require exact affirmative attestations from every upstream gate."""

        return (
            self.policy_authorized is True
            and self.temporally_eligible is True
            and self.task_admissible is True
        )


@dataclass(frozen=True, slots=True)
class SetSelectionConfig:
    """Inspectable integer weights for deterministic marginal utility."""

    semantic_facet_utility: int = 1_000
    diversity_dimension_utility: int = 100
    supporting_evidence_utility: int = 200
    maximum_candidates: int = MAX_SET_CANDIDATES

    def __post_init__(self) -> None:
        for value in (
            self.semantic_facet_utility,
            self.diversity_dimension_utility,
            self.supporting_evidence_utility,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("set-selection utility weights must be non-negative integers")
        if (
            isinstance(self.maximum_candidates, bool)
            or not isinstance(self.maximum_candidates, int)
            or not 1 <= self.maximum_candidates <= MAX_SET_CANDIDATES
        ):
            raise ValueError(
                f"maximum_candidates must be between one and {MAX_SET_CANDIDATES}"
            )


@dataclass(frozen=True, slots=True)
class _MarginalUtility:
    candidate: SetSelectionCandidate
    benefit: int
    semantic_gain: int
    diversity_gain: int
    support_gain: int

    @property
    def density(self) -> Fraction:
        """Return an exact benefit-per-character value for stable comparison."""

        return Fraction(self.benefit, max(1, self.candidate.budget_cost))


class DeterministicSetSelector:
    """Greedily maximize exact marginal utility under limit and character budget.

    Feasible mandatory interaction preferences are considered before optional
    candidates at every step. Within that tier, ordering is by exact marginal
    utility per budget character, then total marginal utility, semantic gain,
    diversity gain, supporting-evidence gain, lower cost, and opaque key.
    Integer utility and :class:`fractions.Fraction` avoid platform-dependent
    floating-point tie behavior.
    """

    def __init__(self, config: SetSelectionConfig | None = None) -> None:
        self.config = config or SetSelectionConfig()

    def select(
        self,
        candidates: Sequence[SetSelectionCandidate],
        constraints: SetSelectionConstraints,
    ) -> SetSelection[SetSelectionCandidate]:
        """Return an immutable compatible set and content-free diagnostics."""

        from .retrieval_contracts import SetSelection

        if len(candidates) > self.config.maximum_candidates:
            raise ValueError("candidate count exceeds the configured hard cap")
        eligible_unsorted = tuple(
            candidate for candidate in candidates if candidate.upstream_eligible
        )
        self._validate_eligible(eligible_unsorted)
        eligible = tuple(sorted(eligible_unsorted, key=lambda candidate: candidate.key))
        selected: list[SetSelectionCandidate] = []
        remaining = list(eligible)
        used = 0

        while remaining and len(selected) < constraints.limit:
            feasible = [
                candidate
                for candidate in remaining
                if self._feasible(candidate, selected, used, constraints.budget)
            ]
            if not feasible:
                break
            mandatory = [
                candidate
                for candidate in feasible
                if candidate.mandatory_interaction_preference
            ]
            pool = mandatory or feasible
            marginals = [self._marginal(candidate, selected) for candidate in pool]
            if not mandatory:
                marginals = [marginal for marginal in marginals if marginal.benefit > 0]
                if not marginals:
                    break
            chosen = min(marginals, key=self._ranking_key).candidate
            selected.append(chosen)
            remaining.remove(chosen)
            used += chosen.budget_cost

        diagnostics = self._diagnostics(candidates, eligible, selected)
        return SetSelection(tuple(selected), diagnostics)

    @staticmethod
    def _validate_eligible(candidates: Sequence[SetSelectionCandidate]) -> None:
        keys: set[str] = set()
        for candidate in candidates:
            if (
                not isinstance(candidate.key, str)
                or not candidate.key
                or len(candidate.key) > MAX_LABEL_CHARS
            ):
                raise ValueError("eligible candidate keys must be bounded non-empty strings")
            if candidate.key in keys:
                raise ValueError("eligible candidate keys must be unique")
            keys.add(candidate.key)
            if (
                isinstance(candidate.budget_cost, bool)
                or not isinstance(candidate.budget_cost, int)
                or not 0 <= candidate.budget_cost <= MAX_BUDGET_COST
            ):
                raise ValueError("eligible candidate budget costs must be bounded integers")
            if (
                isinstance(candidate.base_utility, bool)
                or not isinstance(candidate.base_utility, int)
                or not 0 <= candidate.base_utility <= MAX_BASE_UTILITY
            ):
                raise ValueError("eligible candidate base utility must be a bounded integer")
            if not isinstance(candidate.mandatory_interaction_preference, bool):
                raise ValueError("mandatory preference attestations must be boolean")
            if candidate.mandatory_interaction_preference and candidate.supports:
                raise ValueError("mandatory interaction preferences cannot be evidence")
            for labels in (
                candidate.semantic_facets,
                candidate.diversity_dimensions,
                candidate.compatibility_groups,
                candidate.incompatible_with,
                candidate.redundancy_groups,
                candidate.conflict_groups,
                candidate.supports,
            ):
                _validate_labels(labels)
            if candidate.key in candidate.incompatible_with or candidate.key in candidate.supports:
                raise ValueError("eligible candidate relationships cannot be self-referential")

    @staticmethod
    def _feasible(
        candidate: SetSelectionCandidate,
        selected: Sequence[SetSelectionCandidate],
        used: int,
        budget: int | None,
    ) -> bool:
        if budget is not None and used + candidate.budget_cost > budget:
            return False
        selected_keys = {item.key for item in selected}
        if candidate.supports and not candidate.supports.intersection(selected_keys):
            return False
        for item in selected:
            if candidate.redundancy_groups.intersection(item.redundancy_groups):
                return False
            if candidate.conflict_groups.intersection(item.conflict_groups):
                return False
            if not _compatible(candidate, item):
                return False
        return True

    def _marginal(
        self,
        candidate: SetSelectionCandidate,
        selected: Sequence[SetSelectionCandidate],
    ) -> _MarginalUtility:
        semantic = set().union(*(item.semantic_facets for item in selected))
        diversity = set().union(*(item.diversity_dimensions for item in selected))
        selected_keys = {item.key for item in selected}
        semantic_gain = len(candidate.semantic_facets.difference(semantic))
        diversity_gain = len(candidate.diversity_dimensions.difference(diversity))
        support_gain = len(candidate.supports.intersection(selected_keys))
        benefit = (
            candidate.base_utility
            + semantic_gain * self.config.semantic_facet_utility
            + diversity_gain * self.config.diversity_dimension_utility
            + support_gain * self.config.supporting_evidence_utility
        )
        return _MarginalUtility(
            candidate=candidate,
            benefit=benefit,
            semantic_gain=semantic_gain,
            diversity_gain=diversity_gain,
            support_gain=support_gain,
        )

    @staticmethod
    def _ranking_key(
        marginal: _MarginalUtility,
    ) -> tuple[Fraction, int, int, int, int, int, str]:
        candidate = marginal.candidate
        return (
            -marginal.density,
            -marginal.benefit,
            -marginal.semantic_gain,
            -marginal.diversity_gain,
            -marginal.support_gain,
            candidate.budget_cost,
            candidate.key,
        )

    @staticmethod
    def _diagnostics(
        candidates: Sequence[SetSelectionCandidate],
        eligible: Sequence[SetSelectionCandidate],
        selected: Sequence[SetSelectionCandidate],
    ) -> tuple[SafeRetrievalDiagnostic, ...]:
        from .retrieval_contracts import (
            DiagnosticMetricCode,
            DiagnosticReasonCode,
            DiagnosticValue,
            SafeRetrievalDiagnostic,
        )

        selected_keys = {candidate.key for candidate in selected}
        omitted = [candidate for candidate in eligible if candidate.key not in selected_keys]
        duplicate_count = sum(
            any(
                candidate.redundancy_groups.intersection(item.redundancy_groups)
                for item in selected
            )
            for candidate in omitted
        )
        conflict_count = sum(
            any(
                candidate.conflict_groups.intersection(item.conflict_groups)
                for item in selected
            )
            for candidate in omitted
        )
        filtered_count = len(candidates) - len(eligible)
        diagnostics = [
            SafeRetrievalDiagnostic(
                DiagnosticReasonCode.SET_SELECTED,
                (
                    DiagnosticValue(DiagnosticMetricCode.CANDIDATE_COUNT, len(candidates)),
                    DiagnosticValue(DiagnosticMetricCode.SELECTED_COUNT, len(selected)),
                    DiagnosticValue(
                        DiagnosticMetricCode.FILTERED_COUNT,
                        len(candidates) - len(selected),
                    ),
                    DiagnosticValue(DiagnosticMetricCode.DETERMINISTIC, True),
                ),
            )
        ]
        if filtered_count:
            diagnostics.append(
                SafeRetrievalDiagnostic(
                    DiagnosticReasonCode.POLICY_FILTERED,
                    (
                        DiagnosticValue(
                            DiagnosticMetricCode.FILTERED_COUNT, filtered_count
                        ),
                    ),
                )
            )
        if duplicate_count:
            diagnostics.append(
                SafeRetrievalDiagnostic(
                    DiagnosticReasonCode.SET_DUPLICATE_SUPPRESSED,
                    (
                        DiagnosticValue(
                            DiagnosticMetricCode.DUPLICATE_COUNT, duplicate_count
                        ),
                    ),
                )
            )
        if conflict_count:
            diagnostics.append(
                SafeRetrievalDiagnostic(
                    DiagnosticReasonCode.SET_CONFLICT_RESOLVED,
                    (
                        DiagnosticValue(DiagnosticMetricCode.CONFLICT_COUNT, conflict_count),
                    ),
                )
            )
        return tuple(diagnostics)


def _validate_labels(labels: object) -> None:
    if not isinstance(labels, frozenset) or len(labels) > MAX_SIGNAL_LABELS:
        raise ValueError("set-selection labels must be bounded frozensets")
    if any(
        not isinstance(label, str) or not label or len(label) > MAX_LABEL_CHARS
        for label in labels
    ):
        raise ValueError("set-selection labels must be bounded non-empty strings")


def _compatible(left: SetSelectionCandidate, right: SetSelectionCandidate) -> bool:
    if right.key in left.incompatible_with or left.key in right.incompatible_with:
        return False
    if left.compatibility_groups and right.compatibility_groups:
        return bool(left.compatibility_groups.intersection(right.compatibility_groups))
    return True


def selected_keys(selection: SetSelection[SetSelectionCandidate]) -> tuple[str, ...]:
    """Return opaque selected keys without adding them to safe diagnostics."""

    return tuple(candidate.key for candidate in selection.candidates)


def total_budget_cost(candidates: Iterable[SetSelectionCandidate]) -> int:
    """Return the exact caller-declared character cost of a selected set."""

    return sum(candidate.budget_cost for candidate in candidates)
