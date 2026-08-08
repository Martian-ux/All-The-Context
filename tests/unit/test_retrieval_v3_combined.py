from __future__ import annotations

import json
import math

import pytest

from bench.retrieval_v3_combined import _evaluate_operational_acceptance, run
from bench.retrieval_v3_foundation import GateStatus


def failure_diagnostic(report: dict[str, object]) -> str:
    """Render the bounded gate evidence needed to diagnose a hosted failure."""
    return json.dumps(
        {
            "gate_results": report["gate_results"],
            "lifecycle": report["lifecycle"],
            "operational_acceptance": report["operational_acceptance"],
            "profiles": report["profiles"],
        },
        indent=2,
        sort_keys=True,
    )


def test_integrated_candidate_passes_bounded_functional_gate() -> None:
    report = run([100])
    metrics = report["profiles"]["100"]["metrics"]

    assert report["gate_results_status"] == GateStatus.PASSED, failure_diagnostic(report)
    assert {result["status"] for result in report["gate_results"]} == {GateStatus.PASSED}
    assert report["operational_acceptance"]["all_profile_gates_passed"] is True
    assert report["operational_acceptance"]["as_of_resolution_exercised"] is True
    assert report["operational_acceptance"]["restart_restore_lifecycle_passed"] is True
    assert report["operational_acceptance"]["zero_resurrection"] is True
    assert report["passed"] is all(report["operational_acceptance"].values())
    assert metrics["exact_recall_at_5"] == 1.0
    assert metrics["admissibility_precision_at_5"] == 1.0
    assert metrics["temporal_precision_at_5"] == 1.0
    assert metrics["semantic_coverage_at_5"] == 1.0
    assert metrics["duplicate_redundancy"] == 0.0
    assert metrics["policy_violation_count"] == 0
    assert metrics["warm_latency"]["p95_ms"] >= 0.0
    assert report["lifecycle"]["metrics"]["resurrected_deleted_or_purged_count"] == 0


def _measured(*warm_p95_values: object) -> list[dict[str, object]]:
    return [
        {
            "metrics": {
                "warm_latency": {"p95_ms": value},
                "as_of_expected_ids_present": True,
            }
        }
        for value in warm_p95_values
    ]


def _lifecycle() -> dict[str, object]:
    return {
        "metrics": {
            "as_of_expected_ids_present": True,
            "restart_as_of_ranking_parity": True,
            "restore_rebuild_valid": True,
            "resurrected_deleted_or_purged_count": 0,
        }
    }


@pytest.mark.parametrize(
    ("warm_p95", "expected"),
    [
        (149.999, True),
        (150.0, False),
        (150.000001, False),
        (-0.001, False),
        (math.nan, False),
        (math.inf, False),
        (None, False),
        (True, False),
        ("10.0", False),
    ],
)
def test_operational_latency_gate_is_exact_and_fails_closed(
    warm_p95: object, expected: bool
) -> None:
    operational = _evaluate_operational_acceptance(
        _measured(warm_p95), _lifecycle(), profile_gates_passed=True
    )

    assert operational["warm_p95_under_150_ms"] is expected
    assert all(
        value is True for name, value in operational.items() if name != "warm_p95_under_150_ms"
    )


def test_operational_latency_gate_requires_every_measured_profile() -> None:
    operational = _evaluate_operational_acceptance(
        _measured(10.0, 150.0), _lifecycle(), profile_gates_passed=True
    )

    assert operational["warm_p95_under_150_ms"] is False


def test_operational_acceptance_rejects_missing_evidence() -> None:
    assert not all(_evaluate_operational_acceptance([], {}, profile_gates_passed=True).values())
