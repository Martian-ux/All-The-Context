"""Deterministic, policy-bound admissibility scoring for retrieval candidates.

This module deliberately accepts no record content or raw query/task terms. Callers
must supply bounded numeric evidence computed only after authorization and temporal
eligibility have been established. Structural failures are omitted before validation,
scoring, aggregation, or optional shadow evaluation.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import pairwise
from typing import Protocol


class ConflictState(StrEnum):
    """Upstream conflict classification; this module does not infer conflicts."""

    CLEAR = "clear"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"
    POSSIBLE = "possible"
    ACTIVE = "active"


class AdmissibilityReason(StrEnum):
    """Stable, content-free reason codes suitable for aggregate diagnostics."""

    ADMIT_SCORE = "admit.score"
    ADMIT_FAIL_OPEN_SPARSE_EVIDENCE = "admit.fail_open.sparse_evidence"
    ADMIT_FAIL_OPEN_UNDERSPECIFIED_TASK = "admit.fail_open.underspecified_task"
    REJECT_SCORE_BELOW_THRESHOLD = "reject.score_below_threshold"
    REJECT_LOW_TASK_QUERY_COVERAGE = "reject.low_task_query_coverage"
    REJECT_SCOPE_PROJECT_MISMATCH = "reject.scope_project_mismatch"
    REJECT_KIND_MISMATCH = "reject.kind_mismatch"
    REJECT_LOW_CONFIDENCE_EXPLICITNESS = "reject.low_confidence_explicitness"
    REJECT_CONFLICT = "reject.conflict"
    SHADOW_AGREE = "shadow.agree"
    SHADOW_DISAGREE_WOULD_ADMIT = "shadow.disagree_would_admit"
    SHADOW_DISAGREE_WOULD_REJECT = "shadow.disagree_would_reject"
    SHADOW_INVALID_OUTPUT = "shadow.invalid_output"
    SHADOW_ERROR = "shadow.error"


@dataclass(frozen=True, slots=True)
class FactorWeights:
    """Inspectable relative weights for the five bounded factors."""

    task_query_coverage: float = 0.25
    scope_project_fit: float = 0.20
    kind_compatibility: float = 0.15
    confidence_explicitness: float = 0.10
    conflict_state: float = 0.30

    def items(self) -> tuple[tuple[str, float], ...]:
        return (
            ("task_query_coverage", self.task_query_coverage),
            ("scope_project_fit", self.scope_project_fit),
            ("kind_compatibility", self.kind_compatibility),
            ("confidence_explicitness", self.confidence_explicitness),
            ("conflict_state", self.conflict_state),
        )


def _default_conflict_scores() -> tuple[tuple[ConflictState, float], ...]:
    return (
        (ConflictState.CLEAR, 1.0),
        (ConflictState.RESOLVED, 0.8),
        (ConflictState.UNKNOWN, 0.5),
        (ConflictState.POSSIBLE, 0.2),
        (ConflictState.ACTIVE, 0.0),
    )


@dataclass(frozen=True, slots=True)
class AdmissibilityConfig:
    """Bounded, deterministic scoring and conservative rejection policy."""

    weights: FactorWeights = field(default_factory=FactorWeights)
    rejection_threshold: float = 0.60
    minimum_evidence_factors: int = 3
    minimum_task_specificity: float = 0.25
    confidence_share: float = 0.70
    low_factor_reason_floor: float = 0.40
    conflict_scores: tuple[tuple[ConflictState, float], ...] = field(
        default_factory=_default_conflict_scores
    )

    def __post_init__(self) -> None:
        for name, value in self.weights.items():
            _validate_unit_interval(value, f"weights.{name}")
        if not any(value > 0.0 for _name, value in self.weights.items()):
            raise ValueError("at least one factor weight must be positive")
        _validate_unit_interval(self.rejection_threshold, "rejection_threshold")
        _validate_unit_interval(self.minimum_task_specificity, "minimum_task_specificity")
        _validate_unit_interval(self.confidence_share, "confidence_share")
        _validate_unit_interval(self.low_factor_reason_floor, "low_factor_reason_floor")
        if not 1 <= self.minimum_evidence_factors <= len(self.weights.items()):
            raise ValueError("minimum_evidence_factors must be between one and five")
        configured_states = [state for state, _score in self.conflict_scores]
        if len(configured_states) != len(set(configured_states)):
            raise ValueError("conflict_scores must contain each state once")
        if set(configured_states) != set(ConflictState):
            raise ValueError("conflict_scores must configure every conflict state")
        for state, score in self.conflict_scores:
            _validate_unit_interval(score, f"conflict_scores.{state.value}")


@dataclass(frozen=True, slots=True)
class AdmissibilitySignals:
    """Precomputed evidence only; values are normalized to the closed interval [0, 1]."""

    task_query_coverage: float | None = None
    scope_project_fit: float | None = None
    kind_compatibility: float | None = None
    confidence: float | None = None
    explicitness: float | None = None
    conflict_state: ConflictState | None = None


@dataclass(frozen=True, slots=True)
class AdmissibilityCandidate:
    """Opaque candidate key, structural attestations, and sanitized evidence."""

    key: str
    candidate_authorized: bool
    candidate_temporally_eligible: bool
    evidence_authorized: bool
    evidence_temporally_eligible: bool
    signals: AdmissibilitySignals = field(default_factory=AdmissibilitySignals)

    @property
    def boundary_verified(self) -> bool:
        """Whether both the candidate and all contributing evidence are eligible."""
        return (
            self.candidate_authorized
            and self.candidate_temporally_eligible
            and self.evidence_authorized
            and self.evidence_temporally_eligible
        )


@dataclass(frozen=True, slots=True)
class AdmissibilityContext:
    """Sanitized task/query specificity; no raw task or query text is accepted."""

    query_specificity: float | None = None
    task_specificity: float | None = None


@dataclass(frozen=True, slots=True)
class FactorValues:
    """The normalized factors used by the deterministic gate."""

    task_query_coverage: float | None
    scope_project_fit: float | None
    kind_compatibility: float | None
    confidence_explicitness: float | None
    conflict_state: float | None

    def items(self) -> tuple[tuple[str, float | None], ...]:
        return (
            ("task_query_coverage", self.task_query_coverage),
            ("scope_project_fit", self.scope_project_fit),
            ("kind_compatibility", self.kind_compatibility),
            ("confidence_explicitness", self.confidence_explicitness),
            ("conflict_state", self.conflict_state),
        )


@dataclass(frozen=True, slots=True)
class ShadowFeatures:
    """Content-free features exposed to an optional learned shadow gate."""

    factors: FactorValues
    production_score: float
    evidence_factor_count: int
    task_underspecified: bool
    production_admitted: bool
    production_fail_open: bool


@dataclass(frozen=True, slots=True)
class LearnedGatePrediction:
    """A shadow-only prediction with no production authority."""

    would_admit: bool
    score: float | None = None


class LearnedAdmissibilityGate(Protocol):
    """Optional learned gate; implementations receive no keys or raw text."""

    def assess(self, features: ShadowFeatures) -> LearnedGatePrediction: ...


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    """Safe comparison metadata that cannot alter a production decision."""

    reason_code: AdmissibilityReason
    would_admit: bool | None
    score: float | None
    disagreed: bool
    error: bool


@dataclass(frozen=True, slots=True)
class AdmissibilityDecision:
    """Independent per-candidate production decision and safe explanation."""

    key: str
    admitted: bool
    score: float
    factors: FactorValues
    evidence_factor_count: int
    fail_open: bool
    reason_codes: tuple[AdmissibilityReason, ...]
    shadow: ShadowComparison | None = None


@dataclass(frozen=True, slots=True)
class AdmissibilityDiagnostics:
    """Only safe reason codes and numeric/boolean aggregates."""

    evaluated_count: int
    admitted_count: int
    rejected_count: int
    fail_open_count: int
    shadow_evaluated_count: int
    shadow_disagreement_count: int
    shadow_error_count: int
    minimum_score: float
    mean_score: float
    maximum_score: float
    had_rejections: bool
    reason_counts: tuple[tuple[AdmissibilityReason, int], ...]


@dataclass(frozen=True, slots=True)
class AdmissibilityBatch:
    """Canonical key-ordered decisions plus content-free aggregate diagnostics."""

    decisions: tuple[AdmissibilityDecision, ...]
    diagnostics: AdmissibilityDiagnostics


_LOW_FACTOR_REASONS: dict[str, AdmissibilityReason] = {
    "task_query_coverage": AdmissibilityReason.REJECT_LOW_TASK_QUERY_COVERAGE,
    "scope_project_fit": AdmissibilityReason.REJECT_SCOPE_PROJECT_MISMATCH,
    "kind_compatibility": AdmissibilityReason.REJECT_KIND_MISMATCH,
    "confidence_explicitness": AdmissibilityReason.REJECT_LOW_CONFIDENCE_EXPLICITNESS,
    "conflict_state": AdmissibilityReason.REJECT_CONFLICT,
}


def _validate_unit_interval(value: float, name: str) -> None:
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a finite number between zero and one")


class DeterministicAdmissibilityGate:
    """Score independent candidates without selecting or reordering a result set."""

    def __init__(
        self,
        config: AdmissibilityConfig | None = None,
        *,
        learned_shadow: LearnedAdmissibilityGate | None = None,
    ) -> None:
        self.config = config or AdmissibilityConfig()
        self.learned_shadow = learned_shadow

    def evaluate_many(
        self,
        candidates: Sequence[AdmissibilityCandidate],
        context: AdmissibilityContext,
    ) -> AdmissibilityBatch:
        """Evaluate structurally safe candidates in canonical, input-order-stable order."""
        eligible = sorted(
            (candidate for candidate in candidates if candidate.boundary_verified),
            key=lambda candidate: candidate.key,
        )
        if not eligible:
            return AdmissibilityBatch((), _diagnostics(()))
        for previous, current in pairwise(eligible):
            if previous.key == current.key:
                raise ValueError("eligible candidate keys must be unique")
        _validate_context(context)
        decisions = tuple(self._evaluate(candidate, context) for candidate in eligible)
        return AdmissibilityBatch(decisions, _diagnostics(decisions))

    def _evaluate(
        self,
        candidate: AdmissibilityCandidate,
        context: AdmissibilityContext,
    ) -> AdmissibilityDecision:
        factors = self._factors(candidate.signals)
        weights = dict(self.config.weights.items())
        weighted = tuple(
            (value, weights[name])
            for name, value in factors.items()
            if value is not None and weights[name] > 0.0
        )
        evidence_factor_count = len(weighted)
        denominator = math.fsum(weight for _value, weight in weighted)
        score = (
            0.0
            if denominator == 0.0
            else math.fsum(value * weight for value, weight in weighted) / denominator
        )
        score = round(score, 6)
        specificities = tuple(
            value
            for value in (context.query_specificity, context.task_specificity)
            if value is not None
        )
        task_underspecified = (
            not specificities or max(specificities) < self.config.minimum_task_specificity
        )
        sparse = evidence_factor_count < self.config.minimum_evidence_factors
        fail_open_reasons: list[AdmissibilityReason] = []
        if task_underspecified:
            fail_open_reasons.append(AdmissibilityReason.ADMIT_FAIL_OPEN_UNDERSPECIFIED_TASK)
        if sparse:
            fail_open_reasons.append(AdmissibilityReason.ADMIT_FAIL_OPEN_SPARSE_EVIDENCE)
        fail_open = bool(fail_open_reasons)
        if fail_open:
            admitted = True
            reasons = tuple(fail_open_reasons)
        elif score >= self.config.rejection_threshold:
            admitted = True
            reasons = (AdmissibilityReason.ADMIT_SCORE,)
        else:
            admitted = False
            low_reasons = tuple(
                _LOW_FACTOR_REASONS[name]
                for name, value in factors.items()
                if value is not None and value < self.config.low_factor_reason_floor
            )
            reasons = (AdmissibilityReason.REJECT_SCORE_BELOW_THRESHOLD, *low_reasons)
        decision = AdmissibilityDecision(
            key=candidate.key,
            admitted=admitted,
            score=score,
            factors=factors,
            evidence_factor_count=evidence_factor_count,
            fail_open=fail_open,
            reason_codes=reasons,
        )
        return self._with_shadow(decision, task_underspecified)

    def _factors(self, signals: AdmissibilitySignals) -> FactorValues:
        for name, value in (
            ("task_query_coverage", signals.task_query_coverage),
            ("scope_project_fit", signals.scope_project_fit),
            ("kind_compatibility", signals.kind_compatibility),
            ("confidence", signals.confidence),
            ("explicitness", signals.explicitness),
        ):
            if value is not None:
                _validate_unit_interval(value, name)
        quality: float | None
        if signals.confidence is None:
            quality = signals.explicitness
        elif signals.explicitness is None:
            quality = signals.confidence
        else:
            quality = signals.confidence * self.config.confidence_share + signals.explicitness * (
                1.0 - self.config.confidence_share
            )
        if signals.conflict_state is not None and not isinstance(
            signals.conflict_state, ConflictState
        ):
            raise ValueError("conflict_state must be a ConflictState")
        conflict_scores = dict(self.config.conflict_scores)
        conflict = None
        if signals.conflict_state is not None:
            conflict = conflict_scores[signals.conflict_state]
        return FactorValues(
            task_query_coverage=signals.task_query_coverage,
            scope_project_fit=signals.scope_project_fit,
            kind_compatibility=signals.kind_compatibility,
            confidence_explicitness=None if quality is None else round(quality, 6),
            conflict_state=conflict,
        )

    def _with_shadow(
        self,
        decision: AdmissibilityDecision,
        task_underspecified: bool,
    ) -> AdmissibilityDecision:
        if self.learned_shadow is None:
            return decision
        features = ShadowFeatures(
            factors=decision.factors,
            production_score=decision.score,
            evidence_factor_count=decision.evidence_factor_count,
            task_underspecified=task_underspecified,
            production_admitted=decision.admitted,
            production_fail_open=decision.fail_open,
        )
        try:
            prediction = self.learned_shadow.assess(features)
        except Exception:
            shadow = ShadowComparison(
                reason_code=AdmissibilityReason.SHADOW_ERROR,
                would_admit=None,
                score=None,
                disagreed=False,
                error=True,
            )
        else:
            valid = isinstance(prediction, LearnedGatePrediction) and isinstance(
                prediction.would_admit, bool
            )
            if valid and prediction.score is not None:
                valid = math.isfinite(prediction.score) and 0.0 <= prediction.score <= 1.0
            if not valid:
                shadow = ShadowComparison(
                    reason_code=AdmissibilityReason.SHADOW_INVALID_OUTPUT,
                    would_admit=None,
                    score=None,
                    disagreed=False,
                    error=True,
                )
                return AdmissibilityDecision(
                    key=decision.key,
                    admitted=decision.admitted,
                    score=decision.score,
                    factors=decision.factors,
                    evidence_factor_count=decision.evidence_factor_count,
                    fail_open=decision.fail_open,
                    reason_codes=decision.reason_codes,
                    shadow=shadow,
                )
            disagreed = prediction.would_admit != decision.admitted
            if not disagreed:
                reason = AdmissibilityReason.SHADOW_AGREE
            elif prediction.would_admit:
                reason = AdmissibilityReason.SHADOW_DISAGREE_WOULD_ADMIT
            else:
                reason = AdmissibilityReason.SHADOW_DISAGREE_WOULD_REJECT
            shadow = ShadowComparison(
                reason_code=reason,
                would_admit=prediction.would_admit,
                score=None if prediction.score is None else round(prediction.score, 6),
                disagreed=disagreed,
                error=False,
            )
        return AdmissibilityDecision(
            key=decision.key,
            admitted=decision.admitted,
            score=decision.score,
            factors=decision.factors,
            evidence_factor_count=decision.evidence_factor_count,
            fail_open=decision.fail_open,
            reason_codes=decision.reason_codes,
            shadow=shadow,
        )


def _validate_context(context: AdmissibilityContext) -> None:
    if context.query_specificity is not None:
        _validate_unit_interval(context.query_specificity, "query_specificity")
    if context.task_specificity is not None:
        _validate_unit_interval(context.task_specificity, "task_specificity")


def _diagnostics(decisions: Sequence[AdmissibilityDecision]) -> AdmissibilityDiagnostics:
    counts: Counter[AdmissibilityReason] = Counter()
    for decision in decisions:
        counts.update(decision.reason_codes)
        if decision.shadow is not None:
            counts[decision.shadow.reason_code] += 1
    scores = [decision.score for decision in decisions]
    rejected_count = sum(not decision.admitted for decision in decisions)
    shadow = [decision.shadow for decision in decisions if decision.shadow is not None]
    return AdmissibilityDiagnostics(
        evaluated_count=len(decisions),
        admitted_count=sum(decision.admitted for decision in decisions),
        rejected_count=rejected_count,
        fail_open_count=sum(decision.fail_open for decision in decisions),
        shadow_evaluated_count=len(shadow),
        shadow_disagreement_count=sum(item.disagreed for item in shadow),
        shadow_error_count=sum(item.error for item in shadow),
        minimum_score=0.0 if not scores else min(scores),
        mean_score=0.0 if not scores else round(math.fsum(scores) / len(scores), 6),
        maximum_score=0.0 if not scores else max(scores),
        had_rejections=bool(rejected_count),
        reason_counts=tuple(sorted(counts.items(), key=lambda item: item[0].value)),
    )
