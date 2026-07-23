from __future__ import annotations

import json
from dataclasses import fields, replace
from itertools import permutations

import pytest
from allthecontext.retrieval_contracts import (
    DiagnosticReasonCode,
    SetSelectionConstraints,
)
from allthecontext.set_selection import (
    DeterministicSetSelector,
    SetSelectionCandidate,
    selected_keys,
    total_budget_cost,
)


def _candidate(
    key: str,
    *,
    cost: int = 10,
    utility: int = 100,
    semantic: frozenset[str] = frozenset(),
    diversity: frozenset[str] = frozenset(),
    compatibility: frozenset[str] = frozenset(),
    incompatible_with: frozenset[str] = frozenset(),
    redundancy: frozenset[str] = frozenset(),
    conflicts: frozenset[str] = frozenset(),
    supports: frozenset[str] = frozenset(),
    mandatory: bool = False,
    policy: bool = True,
    temporal: bool = True,
    admissible: bool = True,
) -> SetSelectionCandidate:
    return SetSelectionCandidate(
        key=key,
        budget_cost=cost,
        base_utility=utility,
        semantic_facets=semantic,
        diversity_dimensions=diversity,
        compatibility_groups=compatibility,
        incompatible_with=incompatible_with,
        redundancy_groups=redundancy,
        conflict_groups=conflicts,
        supports=supports,
        mandatory_interaction_preference=mandatory,
        policy_authorized=policy,
        temporally_eligible=temporal,
        task_admissible=admissible,
    )


def test_marginal_utility_adds_semantic_diversity_and_support_under_budget() -> None:
    target = _candidate(
        "target",
        cost=100,
        utility=2_000,
        semantic=frozenset({"decision"}),
        diversity=frozenset({"fact"}),
        compatibility=frozenset({"current"}),
    )
    support = _candidate(
        "support",
        cost=50,
        semantic=frozenset({"evidence"}),
        diversity=frozenset({"source"}),
        compatibility=frozenset({"current"}),
        supports=frozenset({"target"}),
    )
    same_facet = _candidate(
        "same-facet",
        cost=50,
        utility=200,
        semantic=frozenset({"decision"}),
        diversity=frozenset({"fact"}),
        compatibility=frozenset({"current"}),
    )

    selection = DeterministicSetSelector().select(
        [same_facet, support, target], SetSelectionConstraints(limit=2, budget=150)
    )

    assert selected_keys(selection) == ("target", "support")
    assert total_budget_cost(selection.candidates) == 150


def test_feasible_mandatory_preferences_precede_higher_utility_optional_items() -> None:
    mandatory = [
        _candidate("preference-b", cost=40, utility=0, mandatory=True),
        _candidate("preference-a", cost=40, utility=0, mandatory=True),
    ]
    optional = _candidate("optional", cost=20, utility=10_000)

    selection = DeterministicSetSelector().select(
        [optional, *mandatory], SetSelectionConstraints(limit=3, budget=100)
    )

    assert selected_keys(selection) == ("preference-a", "preference-b", "optional")


def test_redundancy_conflict_and_compatibility_are_hard_set_constraints() -> None:
    winner = _candidate(
        "winner",
        utility=500,
        compatibility=frozenset({"current"}),
        redundancy=frozenset({"duplicate"}),
        conflicts=frozenset({"value"}),
        mandatory=True,
    )
    duplicate = _candidate(
        "duplicate",
        utility=400,
        compatibility=frozenset({"current"}),
        redundancy=frozenset({"duplicate"}),
    )
    conflict = _candidate(
        "conflict",
        utility=300,
        compatibility=frozenset({"current"}),
        conflicts=frozenset({"value"}),
    )
    incompatible_domain = _candidate(
        "old-domain", utility=10_000, compatibility=frozenset({"old"})
    )
    pair_blocked = _candidate(
        "pair-blocked",
        utility=10_000,
        incompatible_with=frozenset({"winner"}),
    )
    compatible = _candidate(
        "compatible", utility=100, compatibility=frozenset({"current"})
    )

    selection = DeterministicSetSelector().select(
        [pair_blocked, conflict, duplicate, incompatible_domain, compatible, winner],
        SetSelectionConstraints(limit=5, budget=100),
    )

    assert selected_keys(selection) == ("winner", "compatible")


def test_equal_conflict_candidates_resolve_by_opaque_key_not_input_order() -> None:
    alpha = _candidate("alpha", conflicts=frozenset({"value"}))
    beta = _candidate("beta", conflicts=frozenset({"value"}))
    neutral = _candidate("neutral", utility=50)
    constraints = SetSelectionConstraints(limit=3, budget=100)
    selector = DeterministicSetSelector()

    forward = selector.select([beta, neutral, alpha], constraints)
    reverse = selector.select([alpha, neutral, beta], constraints)

    assert selected_keys(forward) == ("alpha", "neutral")
    assert reverse == forward


def test_upstream_attestations_are_hard_and_filtered_before_signal_validation() -> None:
    valid = _candidate("valid")
    denied = replace(_candidate("denied", utility=10_000), policy_authorized=False)
    expired = replace(_candidate("expired", utility=10_000), temporally_eligible=False)
    inadmissible = replace(_candidate("inadmissible", utility=10_000), task_admissible=False)
    malformed_denied = replace(
        denied,
        budget_cost="not-an-integer",  # type: ignore[arg-type]
        semantic_facets=frozenset({"private-signal"}),
    )

    selection = DeterministicSetSelector().select(
        [malformed_denied, expired, valid, inadmissible],
        SetSelectionConstraints(limit=4, budget=100),
    )

    assert selected_keys(selection) == ("valid",)
    reasons = {diagnostic.reason for diagnostic in selection.diagnostics}
    assert DiagnosticReasonCode.POLICY_FILTERED in reasons


def test_supporting_evidence_requires_a_selected_compatible_target() -> None:
    target = _candidate("target", utility=1_000, compatibility=frozenset({"v2"}))
    compatible = _candidate(
        "compatible",
        supports=frozenset({"target"}),
        compatibility=frozenset({"v2"}),
    )
    incompatible = _candidate(
        "incompatible",
        utility=10_000,
        supports=frozenset({"target"}),
        compatibility=frozenset({"v1"}),
    )
    orphan = _candidate("orphan", utility=10_000, supports=frozenset({"missing"}))

    selection = DeterministicSetSelector().select(
        [orphan, incompatible, compatible, target],
        SetSelectionConstraints(limit=4, budget=100),
    )

    assert selected_keys(selection) == ("target", "compatible")


def test_input_order_repeated_runs_and_ties_are_deterministic() -> None:
    candidates = [
        _candidate("charlie", semantic=frozenset({"shared"})),
        _candidate("alpha", semantic=frozenset({"shared"})),
        _candidate("bravo", semantic=frozenset({"shared"})),
    ]
    constraints = SetSelectionConstraints(limit=2, budget=20)
    selector = DeterministicSetSelector()
    expected = selector.select(candidates, constraints)

    assert selected_keys(expected) == ("alpha", "bravo")
    assert all(
        selector.select(list(order), constraints) == expected
        for order in permutations(candidates)
    )
    assert [selector.select(candidates, constraints) for _ in range(10)] == [
        expected
    ] * 10


def test_safe_diagnostics_expose_closed_codes_and_aggregate_scalars_only() -> None:
    private_key = "opaque-private-candidate"
    selection = DeterministicSetSelector().select(
        [
            _candidate("selected", redundancy=frozenset({"copy"}), conflicts=frozenset({"c"})),
            _candidate(private_key, utility=0, redundancy=frozenset({"copy"})),
            _candidate("conflicting", utility=0, conflicts=frozenset({"c"})),
        ],
        SetSelectionConstraints(limit=3, budget=100),
    )

    assert {field.name for field in fields(selection.diagnostics[0])} == {
        "reason",
        "values",
    }
    rendered = json.dumps(
        [
            {
                "reason": diagnostic.reason,
                "values": [
                    {"metric": value.metric, "value": value.value}
                    for value in diagnostic.values
                ],
            }
            for diagnostic in selection.diagnostics
        ],
        sort_keys=True,
    )
    assert private_key not in rendered
    assert all(
        isinstance(value.value, bool | int | float)
        for diagnostic in selection.diagnostics
        for value in diagnostic.values
    )
    assert {diagnostic.reason for diagnostic in selection.diagnostics} == {
        DiagnosticReasonCode.SET_SELECTED,
        DiagnosticReasonCode.SET_DUPLICATE_SUPPRESSED,
        DiagnosticReasonCode.SET_CONFLICT_RESOLVED,
    }


@pytest.mark.parametrize(
    "candidate",
    [
        _candidate("bad-cost", cost=-1),
        _candidate("bad-key").__class__(
            key="",
            budget_cost=1,
            policy_authorized=True,
            temporally_eligible=True,
            task_admissible=True,
        ),
        _candidate("self-support", supports=frozenset({"self-support"})),
        _candidate("mandatory-evidence", mandatory=True, supports=frozenset({"target"})),
    ],
)
def test_invalid_eligible_metadata_fails_closed(candidate: SetSelectionCandidate) -> None:
    with pytest.raises(ValueError):
        DeterministicSetSelector().select(
            [candidate], SetSelectionConstraints(limit=1, budget=100)
        )
