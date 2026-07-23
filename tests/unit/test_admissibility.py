from __future__ import annotations

from dataclasses import replace

import pytest
from allthecontext.admissibility import (
    AdmissibilityBatch,
    AdmissibilityCandidate,
    AdmissibilityConfig,
    AdmissibilityContext,
    AdmissibilityReason,
    AdmissibilitySignals,
    ConflictState,
    DeterministicAdmissibilityGate,
    FactorWeights,
    LearnedGatePrediction,
    ShadowFeatures,
)


def _candidate(
    key: str,
    signals: AdmissibilitySignals,
    *,
    candidate_authorized: bool = True,
    candidate_temporally_eligible: bool = True,
    evidence_authorized: bool = True,
    evidence_temporally_eligible: bool = True,
) -> AdmissibilityCandidate:
    return AdmissibilityCandidate(
        key=key,
        candidate_authorized=candidate_authorized,
        candidate_temporally_eligible=candidate_temporally_eligible,
        evidence_authorized=evidence_authorized,
        evidence_temporally_eligible=evidence_temporally_eligible,
        signals=signals,
    )


def _complete_signals(
    *,
    coverage: float = 0.9,
    scope: float = 0.9,
    kind: float = 0.9,
    confidence: float = 0.9,
    explicitness: float = 1.0,
    conflict: ConflictState = ConflictState.CLEAR,
) -> AdmissibilitySignals:
    return AdmissibilitySignals(
        task_query_coverage=coverage,
        scope_project_fit=scope,
        kind_compatibility=kind,
        confidence=confidence,
        explicitness=explicitness,
        conflict_state=conflict,
    )


def test_scores_and_decisions_are_stable_under_input_order() -> None:
    gate = DeterministicAdmissibilityGate()
    context = AdmissibilityContext(query_specificity=0.8, task_specificity=0.9)
    candidates = [
        _candidate("z-relevant", _complete_signals()),
        _candidate(
            "a-wrong-project",
            _complete_signals(coverage=0.35, scope=0.0, kind=0.7, confidence=0.8),
        ),
    ]

    forward = gate.evaluate_many(candidates, context)
    reverse = gate.evaluate_many(list(reversed(candidates)), context)

    assert forward == reverse
    assert [decision.key for decision in forward.decisions] == [
        "a-wrong-project",
        "z-relevant",
    ]
    assert [decision.admitted for decision in forward.decisions] == [False, True]
    rejected = forward.decisions[0]
    assert rejected.score == 0.5785
    assert AdmissibilityReason.REJECT_SCOPE_PROJECT_MISMATCH in rejected.reason_codes


class RecordingShadow:
    def __init__(self) -> None:
        self.features: list[ShadowFeatures] = []

    def assess(self, features: ShadowFeatures) -> LearnedGatePrediction:
        self.features.append(features)
        return LearnedGatePrediction(would_admit=features.production_admitted, score=0.5)


def test_structural_failures_cannot_affect_outputs_aggregates_or_shadow() -> None:
    context = AdmissibilityContext(query_specificity=0.8, task_specificity=0.8)
    safe = _candidate("safe", _complete_signals())
    invalid_signals = AdmissibilitySignals(
        task_query_coverage=9.0,
        scope_project_fit=-4.0,
        kind_compatibility=3.0,
        confidence=2.0,
        explicitness=-1.0,
        conflict_state=ConflictState.ACTIVE,
    )
    unsafe = [
        _candidate("denied-record-id", invalid_signals, candidate_authorized=False),
        _candidate("expired-record-id", invalid_signals, candidate_temporally_eligible=False),
        _candidate("unsafe-evidence-id", invalid_signals, evidence_authorized=False),
        _candidate(
            "safe",
            invalid_signals,
            evidence_temporally_eligible=False,
        ),
    ]
    clean_shadow = RecordingShadow()
    polluted_shadow = RecordingShadow()

    clean = DeterministicAdmissibilityGate(learned_shadow=clean_shadow).evaluate_many(
        [safe], context
    )
    polluted = DeterministicAdmissibilityGate(learned_shadow=polluted_shadow).evaluate_many(
        [*unsafe, safe], context
    )

    assert polluted == clean
    assert len(clean_shadow.features) == len(polluted_shadow.features) == 1
    assert polluted.diagnostics.evaluated_count == 1
    rendered = repr(polluted)
    assert "denied-record-id" not in rendered
    assert "expired-record-id" not in rendered
    assert "unsafe-evidence-id" not in rendered


def test_safe_candidate_evidence_must_be_bounded() -> None:
    candidate = _candidate(
        "invalid-safe-candidate",
        replace(_complete_signals(), task_query_coverage=1.01),
    )

    with pytest.raises(ValueError, match="task_query_coverage"):
        DeterministicAdmissibilityGate().evaluate_many(
            [candidate], AdmissibilityContext(query_specificity=1.0)
        )


def test_sparse_evidence_fails_open_even_when_observed_score_is_low() -> None:
    sparse = _candidate(
        "sparse-relevant",
        AdmissibilitySignals(task_query_coverage=0.0, scope_project_fit=0.0),
    )

    decision = (
        DeterministicAdmissibilityGate()
        .evaluate_many([sparse], AdmissibilityContext(query_specificity=0.9, task_specificity=0.8))
        .decisions[0]
    )

    assert decision.admitted is True
    assert decision.fail_open is True
    assert decision.score == 0.0
    assert decision.reason_codes == (AdmissibilityReason.ADMIT_FAIL_OPEN_SPARSE_EVIDENCE,)


@pytest.mark.parametrize(
    "context",
    [
        AdmissibilityContext(),
        AdmissibilityContext(query_specificity=0.0, task_specificity=0.0),
    ],
)
def test_empty_or_underspecified_task_context_fails_open(
    context: AdmissibilityContext,
) -> None:
    weak = _candidate(
        "possible-relevant",
        _complete_signals(
            coverage=0.0,
            scope=0.0,
            kind=0.0,
            confidence=0.0,
            explicitness=0.0,
            conflict=ConflictState.ACTIVE,
        ),
    )

    decision = DeterministicAdmissibilityGate().evaluate_many([weak], context).decisions[0]

    assert decision.admitted is True
    assert decision.fail_open is True
    assert decision.reason_codes == (AdmissibilityReason.ADMIT_FAIL_OPEN_UNDERSPECIFIED_TASK,)


def test_conservative_threshold_admits_equality_and_sparse_evidence() -> None:
    config = AdmissibilityConfig(
        weights=FactorWeights(
            task_query_coverage=1.0,
            scope_project_fit=0.0,
            kind_compatibility=0.0,
            confidence_explicitness=0.0,
            conflict_state=0.0,
        ),
        rejection_threshold=0.6,
        minimum_evidence_factors=1,
    )
    gate = DeterministicAdmissibilityGate(config)
    context = AdmissibilityContext(query_specificity=1.0)
    boundary = _candidate("threshold-boundary", AdmissibilitySignals(task_query_coverage=0.6))
    sparse_config = replace(config, minimum_evidence_factors=2, rejection_threshold=1.0)

    boundary_decision = gate.evaluate_many([boundary], context).decisions[0]
    sparse_decision = (
        DeterministicAdmissibilityGate(sparse_config)
        .evaluate_many([boundary], context)
        .decisions[0]
    )

    assert boundary_decision.admitted is True
    assert boundary_decision.fail_open is False
    assert boundary_decision.reason_codes == (AdmissibilityReason.ADMIT_SCORE,)
    assert sparse_decision.admitted is True
    assert sparse_decision.fail_open is True


class OpposingShadow:
    def assess(self, features: ShadowFeatures) -> LearnedGatePrediction:
        return LearnedGatePrediction(
            would_admit=not features.production_admitted,
            score=1.0 - features.production_score,
        )


class FailingShadow:
    def assess(self, features: ShadowFeatures) -> LearnedGatePrediction:
        del features
        raise RuntimeError("sensitive implementation detail")


class InvalidShadow:
    def assess(self, features: ShadowFeatures) -> LearnedGatePrediction:
        del features
        return LearnedGatePrediction(would_admit=False, score=2.0)


def _production_projection(
    batch: AdmissibilityBatch,
) -> tuple[tuple[str, bool, float, tuple[AdmissibilityReason, ...]], ...]:
    return tuple(
        (decision.key, decision.admitted, decision.score, decision.reason_codes)
        for decision in batch.decisions
    )


def test_learned_gate_is_shadow_only_and_cannot_filter_or_reorder() -> None:
    candidates = [
        _candidate("admit", _complete_signals()),
        _candidate(
            "reject",
            _complete_signals(
                coverage=0.2,
                scope=0.1,
                kind=0.1,
                confidence=0.2,
                conflict=ConflictState.ACTIVE,
            ),
        ),
    ]
    context = AdmissibilityContext(query_specificity=0.9)
    production = DeterministicAdmissibilityGate().evaluate_many(candidates, context)
    shadowed = DeterministicAdmissibilityGate(learned_shadow=OpposingShadow()).evaluate_many(
        candidates, context
    )

    assert _production_projection(shadowed) == _production_projection(production)
    assert shadowed.diagnostics.shadow_evaluated_count == 2
    assert shadowed.diagnostics.shadow_disagreement_count == 2
    assert shadowed.diagnostics.shadow_error_count == 0


def test_shadow_errors_are_sanitized_and_have_zero_authority() -> None:
    candidate = _candidate("safe", _complete_signals())
    context = AdmissibilityContext(task_specificity=0.9)
    production = DeterministicAdmissibilityGate().evaluate_many([candidate], context)

    shadowed = DeterministicAdmissibilityGate(learned_shadow=FailingShadow()).evaluate_many(
        [candidate], context
    )

    assert _production_projection(shadowed) == _production_projection(production)
    assert shadowed.decisions[0].shadow is not None
    assert shadowed.decisions[0].shadow.reason_code == AdmissibilityReason.SHADOW_ERROR
    assert "sensitive implementation detail" not in repr(shadowed)
    assert shadowed.diagnostics.shadow_error_count == 1


def test_invalid_shadow_output_is_sanitized_and_has_zero_authority() -> None:
    candidate = _candidate("safe", _complete_signals())
    context = AdmissibilityContext(task_specificity=0.9)
    production = DeterministicAdmissibilityGate().evaluate_many([candidate], context)

    shadowed = DeterministicAdmissibilityGate(learned_shadow=InvalidShadow()).evaluate_many(
        [candidate], context
    )

    assert _production_projection(shadowed) == _production_projection(production)
    assert shadowed.decisions[0].shadow is not None
    assert shadowed.decisions[0].shadow.reason_code == AdmissibilityReason.SHADOW_INVALID_OUTPUT
    assert shadowed.decisions[0].shadow.score is None
    assert shadowed.diagnostics.shadow_error_count == 1


def test_config_rejects_unbounded_or_incomplete_settings() -> None:
    with pytest.raises(ValueError, match="rejection_threshold"):
        AdmissibilityConfig(rejection_threshold=1.1)
    with pytest.raises(ValueError, match="between one and five"):
        AdmissibilityConfig(minimum_evidence_factors=0)
    with pytest.raises(ValueError, match="every conflict state"):
        AdmissibilityConfig(conflict_scores=((ConflictState.CLEAR, 1.0),))
