from __future__ import annotations

import json

from bench.set_selection_benchmark import FIXTURES, run


def _fixture() -> dict[str, object]:
    loaded = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_fixture_is_sanitized_and_covers_required_set_behavior() -> None:
    fixture = _fixture()
    scenarios = fixture["scenarios"]
    assert isinstance(scenarios, list)
    roles = {str(scenario["fixture_role"]) for scenario in scenarios}
    candidates = [
        candidate
        for scenario in scenarios
        for candidate in scenario["candidates"]
    ]

    assert roles == {
        "mandatory_preferences_with_budget",
        "semantic_diversity_and_support",
        "deterministic_conflict_resolution",
        "explicit_compatibility",
        "upstream_attestation_boundary",
        "marginal_budget_utility",
    }
    assert all("content" not in candidate for candidate in candidates)
    assert all("query" not in scenario and "task" not in scenario for scenario in scenarios)


def test_bounded_benchmark_passes_every_set_selection_acceptance_gate() -> None:
    report = run()
    metrics = report["metrics"]
    acceptance = report["acceptance"]
    assert isinstance(metrics, dict)
    assert isinstance(acceptance, dict)

    assert all(acceptance.values())
    assert metrics["scenario_count"] == 6
    assert metrics["expected_selection_recall"] == 1.0
    assert metrics["unexpected_selected_count"] == 0
    assert metrics["missed_expected_count"] == 0
    assert metrics["mandatory_preference_recall"] == 1.0
    assert metrics["selected_semantic_coverage"] >= metrics[
        "integrated_baseline_semantic_coverage"
    ]
    assert metrics["duplicate_redundancy_count"] == 0
    assert metrics["conflict_violation_count"] == 0
    assert metrics["compatibility_violation_count"] == 0
    assert metrics["support_relationship_violation_count"] == 0
    assert metrics["budget_violation_count"] == 0
    assert metrics["upstream_boundary_violation_count"] == 0
    assert metrics["repeated_run_deterministic"] is True
    assert metrics["input_order_deterministic"] is True


def test_benchmark_report_contains_no_candidate_keys_or_set_labels() -> None:
    report = run()
    assert run() == report
    rendered = json.dumps(report, sort_keys=True)
    fixture = _fixture()
    scenarios = fixture["scenarios"]
    assert isinstance(scenarios, list)
    candidates = [
        candidate
        for scenario in scenarios
        for candidate in scenario["candidates"]
    ]
    forbidden = {
        str(candidate["key"])
        for candidate in candidates
    }
    forbidden.update(
        str(label)
        for candidate in candidates
        for field in (
            "semantic_facets",
            "diversity_dimensions",
            "compatibility_groups",
            "redundancy_groups",
            "conflict_groups",
        )
        for label in candidate.get(field, [])
    )

    assert all(value not in rendered for value in forbidden)
    assert all(isinstance(value, bool | int | float) for value in report["metrics"].values())
    assert all(isinstance(value, bool) for value in report["acceptance"].values())
