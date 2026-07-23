from __future__ import annotations

from bench.retrieval_v3_combined import run
from bench.retrieval_v3_foundation import GateStatus


def test_integrated_candidate_passes_bounded_frozen_comparator_gate() -> None:
    report = run([100])
    metrics = report["profiles"]["100"]["metrics"]

    assert report["passed"] is True
    assert report["gate_results_status"] == GateStatus.PASSED
    assert {result["status"] for result in report["gate_results"]} == {GateStatus.PASSED}
    assert metrics["exact_recall_at_5"] == 1.0
    assert metrics["admissibility_precision_at_5"] == 1.0
    assert metrics["temporal_precision_at_5"] == 1.0
    assert metrics["semantic_coverage_at_5"] == 1.0
    assert metrics["duplicate_redundancy"] == 0.0
    assert metrics["policy_violation_count"] == 0
    assert metrics["warm_latency"]["p95_ms"] < 150.0
    assert report["lifecycle"]["metrics"]["resurrected_deleted_or_purged_count"] == 0
