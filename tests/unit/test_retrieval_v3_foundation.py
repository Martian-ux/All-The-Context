from __future__ import annotations

import json
from copy import deepcopy

import pytest

from bench.retrieval_v3_foundation import (
    FROZEN_COMPARATOR_CONTRACT,
    GATE_DEFINITIONS,
    GateStatus,
    evaluate_gates,
    load_foundation_fixture,
    run,
    select_profiles,
)


def test_foundation_fixture_declares_all_required_sanitized_scenarios() -> None:
    fixture = load_foundation_fixture()
    categories = {query["category"] for query in fixture["queries"]}
    record_ids = {record["id"] for record in fixture["records"]}

    assert {
        "current_history",
        "temporal_filter",
        "semantic_coverage",
        "admissibility",
        "conflict",
        "near_duplicate",
        "permissions",
    } <= categories
    assert {
        "future-sentinel",
        "semantic-evidence",
        "admissible-deploy",
        "admissibility-false-positive",
        "conflict-east",
        "conflict-west",
    } <= record_ids
    assert set(fixture["declared_not_exercised"]) == {
        "as_of_history_resolution",
        "task_admissibility_evaluator",
        "compatible_evidence_set_policy",
        "retrieval_index_rebuild_after_restore",
    }
    assert fixture["temporal_cases"] == [
        {"case_id": "current", "query_id": "current_history", "status": "exercised"},
        {
            "case_id": "as_of",
            "as_of": "2025-01-01T00:00:05.500000+00:00",
            "expected": ["retention-old"],
            "status": "not_exercised",
        },
    ]
    assert fixture["compatible_evidence_sets"] == [
        {
            "query_id": "semantic_evidence",
            "candidate_ids": ["exact-cobalt", "semantic-evidence"],
            "status": "not_exercised",
        }
    ]


def test_foundation_profiles_keep_normal_ci_bounded_and_larger_runs_opt_in() -> None:
    assert select_profiles([], False) == (1_000, 10_000)
    with pytest.raises(ValueError, match="above 10k"):
        select_profiles([20_000], False)
    assert select_profiles([20_000], True) == (20_000,)
    assert select_profiles([50_000], True) == (50_000,)
    with pytest.raises(ValueError, match="above 50k"):
        select_profiles([50_001], True)


def test_bounded_foundation_harness_measures_comparator_and_lifecycle() -> None:
    report = run([100])
    metrics = report["profiles"]["100"]["metrics"]
    lifecycle = report["lifecycle"]
    scenarios = {item["scenario_id"]: item["status"] for item in lifecycle["scenarios"]}

    assert report["report_kind"] == "frozen_comparator_measurement"
    assert report["comparator"]["base_commit"] == (
        "70a4808cc5d9fc35f4a7b9a75bc3cbfbb0e9ce40"
    )
    assert report["comparator_contract_status"] == GateStatus.PASSED
    assert report["gate_results"] == []
    assert report["gate_results_status"] == GateStatus.NOT_EXERCISED
    assert len(report["gate_definitions"]) == len(GATE_DEFINITIONS)
    assert metrics["exact_recall_at_5"] == 1.0
    assert metrics["semantic_coverage_at_5"] == 1.0
    assert metrics["duplicate_redundancy"] == 0.0
    assert metrics["repeated_rankings_deterministic"] is True
    assert metrics["comparator_contract_match"] is True
    assert metrics["conflict_behavior_deterministic"] is True
    assert metrics["policy_violation_count"] == 0
    assert metrics["filter_violation_counts"] == {
        "future_validity": 0,
        "expired": 0,
        "superseded": 0,
        "deleted": 0,
    }
    assert metrics["canonical_record_count"] == 100
    assert metrics["fts_row_count"] == 100
    assert metrics["database_bytes"] > 0
    assert metrics["cold_latency"]["p95_ms"] >= 0
    assert metrics["warm_latency"]["p95_ms"] >= 0
    contract = json.loads(FROZEN_COMPARATOR_CONTRACT.read_text(encoding="utf-8"))
    assert report["comparator"] == contract["comparator"]
    assert report["fixtures"]["base_sha256"] == contract["base_fixture_sha256"]
    assert report["fixtures"]["foundation_sha256"] == contract["foundation_fixture_sha256"]
    assert metrics["ranking_fingerprint_sha256"] == contract["ranking_fingerprints_sha256"][
        "100"
    ]
    assert lifecycle["metrics"]["history_version_count"] == 2
    assert lifecycle["metrics"]["restart_ranking_parity"] is True
    assert lifecycle["metrics"]["portable_restore_valid"] is True
    assert lifecycle["metrics"]["resurrected_deleted_or_purged_count"] == 0
    assert scenarios["current_history"] == GateStatus.PASSED
    assert scenarios["export_import_restore"] == GateStatus.PASSED
    assert scenarios["as_of_history"] == GateStatus.NOT_EXERCISED
    assert scenarios["task_admissibility"] == GateStatus.NOT_EXERCISED
    assert scenarios["compatible_evidence_sets"] == GateStatus.NOT_EXERCISED
    assert scenarios["retrieval_index_rebuild_after_restore"] == GateStatus.NOT_EXERCISED
    rendered = json.dumps(report, sort_keys=True)
    assert "Sentinel deleted synthetic record" not in rendered
    assert "denied-sentinel" not in rendered
    assert "other-allowlist-sentinel" not in rendered


def _gate_report() -> dict[str, object]:
    return {
        "profiles": {
            "1000": {
                "metrics": {
                    "exact_recall_at_5": 1.0,
                    "admissibility_precision_at_5": 0.5,
                    "temporal_precision_at_5": 0.5,
                    "semantic_coverage_at_5": 1.0,
                    "duplicate_redundancy": 0.0,
                    "conflict_behavior_deterministic": True,
                    "policy_violation_count": 0,
                }
            }
        },
        "lifecycle": {"metrics": {"resurrected_deleted_or_purged_count": 0}},
    }


def test_machine_readable_gates_require_improvements_and_absolute_safety() -> None:
    comparator = _gate_report()
    candidate = deepcopy(comparator)
    candidate_metrics = candidate["profiles"]["1000"]["metrics"]
    candidate_metrics["admissibility_precision_at_5"] = 0.75
    candidate_metrics["temporal_precision_at_5"] = 0.75

    passed, results = evaluate_gates(candidate, comparator)

    assert passed is True
    assert len(results) == len(GATE_DEFINITIONS)
    assert {result["status"] for result in results} == {GateStatus.PASSED}


@pytest.mark.parametrize(
    ("metric", "value", "gate_id"),
    [
        ("exact_recall_at_5", 0.5, "exact_recall_at_5"),
        ("duplicate_redundancy", 0.1, "zero_duplicate_redundancy"),
        ("conflict_behavior_deterministic", False, "deterministic_conflict_behavior"),
        ("policy_violation_count", 1, "zero_policy_violations"),
    ],
)
def test_machine_readable_profile_gate_failures_are_explicit(
    metric: str, value: bool | int | float, gate_id: str
) -> None:
    comparator = _gate_report()
    candidate = deepcopy(comparator)
    candidate_metrics = candidate["profiles"]["1000"]["metrics"]
    candidate_metrics["admissibility_precision_at_5"] = 0.75
    candidate_metrics["temporal_precision_at_5"] = 0.75
    candidate_metrics[metric] = value

    passed, results = evaluate_gates(candidate, comparator)

    assert passed is False
    result = next(item for item in results if item["gate_id"] == gate_id)
    assert result["status"] == GateStatus.FAILED


def test_missing_future_feature_metrics_are_not_exercised_never_passed() -> None:
    comparator = _gate_report()
    candidate = {"profiles": {"1000": {"metrics": {}}}, "lifecycle": {"metrics": {}}}

    passed, results = evaluate_gates(candidate, comparator)

    assert passed is False
    assert {result["status"] for result in results} == {GateStatus.NOT_EXERCISED}
