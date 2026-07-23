"""Run the bounded, sanitized deterministic set-selection benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from allthecontext.retrieval_contracts import SetSelectionConstraints
from allthecontext.set_selection import (
    DeterministicSetSelector,
    SetSelectionCandidate,
    selected_keys,
    total_budget_cost,
)

FIXTURES = Path(__file__).with_name("set_selection_fixtures.json")


def _load_fixture(path: Path = FIXTURES) -> dict[str, Any]:
    parsed: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or parsed.get("schema_version") != 1:
        raise ValueError("set-selection fixture must use schema version one")
    if not isinstance(parsed.get("scenarios"), list) or not parsed["scenarios"]:
        raise ValueError("set-selection fixture must contain scenarios")
    return parsed


def _labels(data: Mapping[str, Any], name: str) -> frozenset[str]:
    raw = data.get(name, [])
    if not isinstance(raw, list) or any(not isinstance(value, str) for value in raw):
        raise ValueError("set-selection fixture labels must be string lists")
    return frozenset(raw)


def _candidate(data: Mapping[str, Any]) -> SetSelectionCandidate:
    return SetSelectionCandidate(
        key=str(data["key"]),
        budget_cost=int(data["budget_cost"]),
        base_utility=int(data.get("base_utility", 0)),
        semantic_facets=_labels(data, "semantic_facets"),
        diversity_dimensions=_labels(data, "diversity_dimensions"),
        compatibility_groups=_labels(data, "compatibility_groups"),
        incompatible_with=_labels(data, "incompatible_with"),
        redundancy_groups=_labels(data, "redundancy_groups"),
        conflict_groups=_labels(data, "conflict_groups"),
        supports=_labels(data, "supports"),
        mandatory_interaction_preference=bool(data.get("mandatory_interaction_preference", False)),
        policy_authorized=bool(data.get("policy_authorized", True)),
        temporally_eligible=bool(data.get("temporally_eligible", True)),
        task_admissible=bool(data.get("task_admissible", True)),
    )


def _pair_violations(selected: Sequence[SetSelectionCandidate], attribute: str) -> int:
    violations = 0
    for index, left in enumerate(selected):
        left_values = getattr(left, attribute)
        assert isinstance(left_values, frozenset)
        for right in selected[index + 1 :]:
            right_values = getattr(right, attribute)
            assert isinstance(right_values, frozenset)
            violations += bool(left_values.intersection(right_values))
    return violations


def _compatibility_violations(selected: Sequence[SetSelectionCandidate]) -> int:
    violations = 0
    for index, left in enumerate(selected):
        for right in selected[index + 1 :]:
            explicitly_incompatible = (
                right.key in left.incompatible_with or left.key in right.incompatible_with
            )
            disjoint_domains = bool(
                left.compatibility_groups
                and right.compatibility_groups
                and left.compatibility_groups.isdisjoint(right.compatibility_groups)
            )
            violations += explicitly_incompatible or disjoint_domains
    return violations


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 6)


def run(path: Path = FIXTURES) -> dict[str, object]:
    """Evaluate deterministic set behavior without returning candidate identifiers."""

    fixture = _load_fixture(path)
    selector = DeterministicSetSelector()
    scenario_count = 0
    candidate_count = 0
    selected_count = 0
    expected_count = 0
    expected_selected_count = 0
    unexpected_selected_count = 0
    missed_expected_count = 0
    mandatory_count = 0
    selected_mandatory_count = 0
    baseline_facet_hits = 0
    selected_facet_hits = 0
    target_facet_count = 0
    duplicate_violations = 0
    conflict_violations = 0
    compatibility_violations = 0
    support_violations = 0
    budget_violations = 0
    upstream_boundary_violations = 0
    repeated_deterministic = True
    input_order_deterministic = True

    scenarios = fixture["scenarios"]
    assert isinstance(scenarios, list)
    for raw_scenario in scenarios:
        if not isinstance(raw_scenario, dict):
            raise ValueError("set-selection scenario must be an object")
        raw_candidates = raw_scenario.get("candidates")
        raw_constraints = raw_scenario.get("constraints")
        raw_targets = raw_scenario.get("target_semantic_facets")
        if (
            not isinstance(raw_candidates, list)
            or not isinstance(raw_constraints, dict)
            or not isinstance(raw_targets, list)
            or any(not isinstance(value, str) for value in raw_targets)
        ):
            raise ValueError("set-selection scenario fields are required")
        candidates = [
            _candidate(candidate) for candidate in raw_candidates if isinstance(candidate, dict)
        ]
        if len(candidates) != len(raw_candidates):
            raise ValueError("set-selection candidates must be objects")
        constraints = SetSelectionConstraints(
            limit=int(raw_constraints["limit"]),
            budget=int(raw_constraints["budget"]),
        )
        selection = selector.select(candidates, constraints)
        selected = selection.candidates
        keys = selected_keys(selection)
        expected = {
            str(candidate["key"])
            for candidate in raw_candidates
            if isinstance(candidate, dict) and candidate.get("expected_selected") is True
        }
        baseline = [
            candidate
            for candidate, raw_candidate in zip(candidates, raw_candidates, strict=True)
            if isinstance(raw_candidate, dict)
            and raw_candidate.get("integrated_baseline_selected") is True
        ]
        targets = set(raw_targets)
        baseline_facets = set().union(*(candidate.semantic_facets for candidate in baseline))
        selected_facets = set().union(*(candidate.semantic_facets for candidate in selected))
        repeats = [selector.select(candidates, constraints) for _ in range(10)]
        order_variants = [list(reversed(candidates))]
        order_variants.extend(
            candidates[offset:] + candidates[:offset] for offset in range(1, len(candidates))
        )

        scenario_count += 1
        candidate_count += len(candidates)
        selected_count += len(selected)
        expected_count += len(expected)
        expected_selected_count += len(set(keys).intersection(expected))
        unexpected_selected_count += len(set(keys).difference(expected))
        missed_expected_count += len(expected.difference(keys))
        eligible_mandatory = [
            candidate
            for candidate in candidates
            if candidate.upstream_eligible and candidate.mandatory_interaction_preference
        ]
        mandatory_count += len(eligible_mandatory)
        selected_mandatory_count += sum(
            candidate.mandatory_interaction_preference for candidate in selected
        )
        baseline_facet_hits += len(baseline_facets.intersection(targets))
        selected_facet_hits += len(selected_facets.intersection(targets))
        target_facet_count += len(targets)
        duplicate_violations += _pair_violations(selected, "redundancy_groups")
        conflict_violations += _pair_violations(selected, "conflict_groups")
        compatibility_violations += _compatibility_violations(selected)
        selected_key_set = set(keys)
        support_violations += sum(
            bool(candidate.supports) and candidate.supports.isdisjoint(selected_key_set)
            for candidate in selected
        )
        budget_violations += total_budget_cost(selected) > int(raw_constraints["budget"])
        upstream_boundary_violations += sum(
            not candidate.upstream_eligible for candidate in selected
        )
        repeated_deterministic &= all(repeat == selection for repeat in repeats)
        input_order_deterministic &= all(
            selector.select(order, constraints) == selection for order in order_variants
        )

    baseline_coverage = _ratio(baseline_facet_hits, target_facet_count)
    selected_coverage = _ratio(selected_facet_hits, target_facet_count)
    metrics: dict[str, bool | int | float] = {
        "scenario_count": scenario_count,
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "expected_selection_recall": _ratio(expected_selected_count, expected_count),
        "unexpected_selected_count": unexpected_selected_count,
        "missed_expected_count": missed_expected_count,
        "mandatory_preference_recall": _ratio(selected_mandatory_count, mandatory_count),
        "integrated_baseline_semantic_coverage": baseline_coverage,
        "selected_semantic_coverage": selected_coverage,
        "duplicate_redundancy_count": duplicate_violations,
        "conflict_violation_count": conflict_violations,
        "compatibility_violation_count": compatibility_violations,
        "support_relationship_violation_count": support_violations,
        "budget_violation_count": budget_violations,
        "upstream_boundary_violation_count": upstream_boundary_violations,
        "repeated_run_deterministic": repeated_deterministic,
        "input_order_deterministic": input_order_deterministic,
    }
    acceptance = {
        "expected_selection_exact": (unexpected_selected_count == 0 and missed_expected_count == 0),
        "mandatory_preferences_preserved": metrics["mandatory_preference_recall"] == 1.0,
        "semantic_coverage_at_least_integrated_baseline": (selected_coverage >= baseline_coverage),
        "zero_duplicate_redundancy": duplicate_violations == 0,
        "zero_conflict_violations": conflict_violations == 0,
        "zero_compatibility_violations": compatibility_violations == 0,
        "support_relationships_preserved": support_violations == 0,
        "character_budgets_respected": budget_violations == 0,
        "upstream_attestations_never_weakened": upstream_boundary_violations == 0,
        "repeated_run_deterministic": repeated_deterministic,
        "input_order_deterministic": input_order_deterministic,
    }
    return {
        "schema_version": 1,
        "fixture_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "metrics": metrics,
        "acceptance": acceptance,
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
    return 0 if all(bool(value) for value in acceptance.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
