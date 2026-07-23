"""Run the sanitized, deterministic retrieval-admissibility fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from allthecontext.admissibility import (
    AdmissibilityCandidate,
    AdmissibilityConfig,
    AdmissibilityContext,
    AdmissibilityReason,
    AdmissibilitySignals,
    ConflictState,
    DeterministicAdmissibilityGate,
)

FIXTURES = Path(__file__).with_name("admissibility_fixtures.json")


def _load_fixture(path: Path = FIXTURES) -> dict[str, Any]:
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or parsed.get("schema_version") != 1:
        raise ValueError("admissibility fixture must use schema version one")
    if not isinstance(parsed.get("scenarios"), list) or not parsed["scenarios"]:
        raise ValueError("admissibility fixture must contain scenarios")
    return parsed


def _optional_float(data: Mapping[str, Any], name: str) -> float | None:
    value = data.get(name)
    return None if value is None else float(value)


def _candidate(data: Mapping[str, Any]) -> AdmissibilityCandidate:
    raw_signals = data.get("signals")
    if not isinstance(raw_signals, dict):
        raise ValueError("candidate signals must be an object")
    raw_conflict = raw_signals.get("conflict_state")
    conflict = None if raw_conflict is None else ConflictState(str(raw_conflict))
    return AdmissibilityCandidate(
        key=str(data["key"]),
        candidate_authorized=bool(data["candidate_authorized"]),
        candidate_temporally_eligible=bool(data["candidate_temporally_eligible"]),
        evidence_authorized=bool(data["evidence_authorized"]),
        evidence_temporally_eligible=bool(data["evidence_temporally_eligible"]),
        signals=AdmissibilitySignals(
            task_query_coverage=_optional_float(raw_signals, "task_query_coverage"),
            scope_project_fit=_optional_float(raw_signals, "scope_project_fit"),
            kind_compatibility=_optional_float(raw_signals, "kind_compatibility"),
            confidence=_optional_float(raw_signals, "confidence"),
            explicitness=_optional_float(raw_signals, "explicitness"),
            conflict_state=conflict,
        ),
    )


def _context(data: Mapping[str, Any]) -> AdmissibilityContext:
    return AdmissibilityContext(
        query_specificity=_optional_float(data, "query_specificity"),
        task_specificity=_optional_float(data, "task_specificity"),
    )


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 6)


def _config_report(config: AdmissibilityConfig) -> dict[str, object]:
    return {
        "weights": dict(config.weights.items()),
        "rejection_threshold": config.rejection_threshold,
        "minimum_evidence_factors": config.minimum_evidence_factors,
        "minimum_task_specificity": config.minimum_task_specificity,
        "confidence_share": config.confidence_share,
        "low_factor_reason_floor": config.low_factor_reason_floor,
        "conflict_scores": {state.value: score for state, score in config.conflict_scores},
    }


def run(path: Path = FIXTURES) -> dict[str, object]:
    """Compare the deterministic gate with fail-open admission of every safe candidate."""
    fixture = _load_fixture(path)
    config = AdmissibilityConfig()
    gate = DeterministicAdmissibilityGate(config)
    baseline_returned = 0
    baseline_relevant = 0
    gated_returned = 0
    gated_relevant = 0
    total_relevant = 0
    evaluated_count = 0
    admitted_count = 0
    rejected_count = 0
    fail_open_count = 0
    scores: list[float] = []
    reason_counts: Counter[AdmissibilityReason] = Counter()
    repeated_evaluation_deterministic = True
    input_order_deterministic = True
    scenarios = fixture["scenarios"]
    assert isinstance(scenarios, list)
    for raw_scenario in scenarios:
        if not isinstance(raw_scenario, dict):
            raise ValueError("scenario must be an object")
        raw_context = raw_scenario.get("context")
        raw_candidates = raw_scenario.get("candidates")
        if not isinstance(raw_context, dict) or not isinstance(raw_candidates, list):
            raise ValueError("scenario context and candidates are required")
        candidates = [_candidate(item) for item in raw_candidates if isinstance(item, dict)]
        if len(candidates) != len(raw_candidates):
            raise ValueError("candidate must be an object")
        relevance = {
            str(item["key"]): bool(item["relevant"])
            for item in raw_candidates
            if isinstance(item, dict)
        }
        structurally_safe = [candidate for candidate in candidates if candidate.boundary_verified]
        context = _context(raw_context)
        batch = gate.evaluate_many(candidates, context)
        repeated_evaluation_deterministic = (
            repeated_evaluation_deterministic and batch == gate.evaluate_many(candidates, context)
        )
        input_order_deterministic = input_order_deterministic and batch == gate.evaluate_many(
            list(reversed(candidates)), context
        )
        admitted = {decision.key for decision in batch.decisions if decision.admitted}
        baseline_top_five = structurally_safe[:5]
        gated_candidates = [
            candidate for candidate in structurally_safe if candidate.key in admitted
        ]
        gated_top_five = gated_candidates[:5]
        scenario_relevant = sum(relevance[candidate.key] for candidate in structurally_safe)
        total_relevant += scenario_relevant
        baseline_returned += len(baseline_top_five)
        baseline_relevant += sum(relevance[candidate.key] for candidate in baseline_top_five)
        gated_returned += len(gated_top_five)
        gated_relevant += sum(relevance[candidate.key] for candidate in gated_top_five)
        evaluated_count += batch.diagnostics.evaluated_count
        admitted_count += batch.diagnostics.admitted_count
        rejected_count += batch.diagnostics.rejected_count
        fail_open_count += batch.diagnostics.fail_open_count
        scores.extend(decision.score for decision in batch.decisions)
        reason_counts.update(dict(batch.diagnostics.reason_counts))

    baseline_precision = _ratio(baseline_relevant, baseline_returned)
    gated_precision = _ratio(gated_relevant, gated_returned)
    baseline_recall = _ratio(baseline_relevant, total_relevant)
    gated_recall = _ratio(gated_relevant, total_relevant)
    baseline_false_positives = baseline_returned - baseline_relevant
    gated_false_positives = gated_returned - gated_relevant
    diagnostics: dict[str, object] = {
        "evaluated_count": evaluated_count,
        "admitted_count": admitted_count,
        "rejected_count": rejected_count,
        "fail_open_count": fail_open_count,
        "minimum_score": 0.0 if not scores else min(scores),
        "mean_score": 0.0 if not scores else round(math.fsum(scores) / len(scores), 6),
        "maximum_score": 0.0 if not scores else max(scores),
        "had_rejections": bool(rejected_count),
        "reason_counts": {
            reason.value: count
            for reason, count in sorted(reason_counts.items(), key=lambda item: item[0].value)
        },
    }
    return {
        "schema_version": 1,
        "fixture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "config": _config_report(config),
        "metrics": {
            "baseline_admissibility_precision_at_5": baseline_precision,
            "gated_admissibility_precision_at_5": gated_precision,
            "precision_at_5_delta": round(gated_precision - baseline_precision, 6),
            "baseline_relevant_recall_at_5": baseline_recall,
            "gated_relevant_recall_at_5": gated_recall,
            "recall_at_5_delta": round(gated_recall - baseline_recall, 6),
            "baseline_false_positive_count_at_5": baseline_false_positives,
            "gated_false_positive_count_at_5": gated_false_positives,
            "false_positive_count_at_5_delta": (gated_false_positives - baseline_false_positives),
        },
        "diagnostics": diagnostics,
        "acceptance": {
            "admissibility_precision_improved": gated_precision > baseline_precision,
            "relevant_recall_at_5_not_worse": gated_recall >= baseline_recall,
            "false_positives_reduced": gated_false_positives < baseline_false_positives,
            "repeated_evaluation_deterministic": repeated_evaluation_deterministic,
            "input_order_deterministic": input_order_deterministic,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=FIXTURES)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    report = run(arguments.fixture)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {arguments.output}")
    acceptance = report["acceptance"]
    assert isinstance(acceptance, dict)
    passed = all(bool(value) for value in acceptance.values())
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
