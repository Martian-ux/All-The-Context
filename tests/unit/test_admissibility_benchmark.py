from __future__ import annotations

import json
from pathlib import Path

from bench.admissibility_benchmark import FIXTURES, run


def _fixture() -> dict[str, object]:
    loaded = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_fixture_is_sanitized_and_covers_admissibility_false_positives() -> None:
    fixture = _fixture()
    scenarios = fixture["scenarios"]
    assert isinstance(scenarios, list)
    candidates = [
        candidate
        for scenario in scenarios
        for candidate in scenario["candidates"]
    ]
    roles = {str(candidate["fixture_role"]) for candidate in candidates}

    assert {
        "project_scope_mismatch",
        "kind_mismatch",
        "active_conflict",
        "scope_kind_conflict_mismatch",
        "sparse_relevant",
        "underspecified_relevant",
        "unauthorized_control",
        "temporally_ineligible_control",
    } <= roles
    assert all("content" not in candidate for candidate in candidates)
    assert all("query" not in scenario and "task" not in scenario for scenario in scenarios)


def test_benchmark_proves_precision_gain_without_recall_at_5_loss() -> None:
    report = run()
    metrics = report["metrics"]
    acceptance = report["acceptance"]
    diagnostics = report["diagnostics"]
    assert isinstance(metrics, dict)
    assert isinstance(acceptance, dict)
    assert isinstance(diagnostics, dict)

    assert metrics["baseline_admissibility_precision_at_5"] == 0.5
    assert metrics["gated_admissibility_precision_at_5"] == 0.833333
    assert metrics["precision_at_5_delta"] == 0.333333
    assert metrics["baseline_relevant_recall_at_5"] == 1.0
    assert metrics["gated_relevant_recall_at_5"] == 1.0
    assert metrics["recall_at_5_delta"] == 0.0
    assert metrics["baseline_false_positive_count_at_5"] == 5
    assert metrics["gated_false_positive_count_at_5"] == 1
    assert metrics["false_positive_count_at_5_delta"] == -4
    assert acceptance == {
        "admissibility_precision_improved": True,
        "relevant_recall_at_5_not_worse": True,
        "false_positives_reduced": True,
        "repeated_evaluation_deterministic": True,
        "input_order_deterministic": True,
    }
    assert diagnostics["evaluated_count"] == 10
    assert diagnostics["admitted_count"] == 6
    assert diagnostics["rejected_count"] == 4
    assert diagnostics["fail_open_count"] == 3


def test_benchmark_report_contains_no_candidate_keys_or_raw_terms(tmp_path: Path) -> None:
    report = run()
    assert run() == report
    rendered = json.dumps(report, sort_keys=True)
    fixture = _fixture()
    scenarios = fixture["scenarios"]
    assert isinstance(scenarios, list)
    keys = [
        str(candidate["key"])
        for scenario in scenarios
        for candidate in scenario["candidates"]
    ]

    assert all(key not in rendered for key in keys)
    diagnostics = report["diagnostics"]
    assert isinstance(diagnostics, dict)
    for name, value in diagnostics.items():
        if name == "reason_counts":
            assert isinstance(value, dict)
            assert all(isinstance(reason, str) for reason in value)
            assert all(isinstance(count, int) for count in value.values())
        else:
            assert isinstance(value, int | float | bool)

    copied_fixture = tmp_path / "fixture.json"
    copied_fixture.write_text(FIXTURES.read_text(encoding="utf-8"), encoding="utf-8")
    assert run(copied_fixture)["metrics"] == report["metrics"]
