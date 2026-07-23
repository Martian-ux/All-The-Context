from __future__ import annotations

import json
from pathlib import Path

from allthecontext.memory_lab_o01 import (
    O01_REPORT_SCHEMA,
    PRIMARY_CONDITIONS,
    SyntheticMemoryCondition,
    _environment_outcome,
    load_protocol,
    run_o01_file,
)

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "bench" / "memory_lab_o01_fixture.json"


def test_pre_action_input_separates_post_action_corrective_oracle() -> None:
    protocol = load_protocol(FIXTURE)
    hidden = next(
        regime.steps[1] for regime in protocol.regimes if regime.name == "online"
    )
    reference_spec = next(
        condition
        for condition in PRIMARY_CONDITIONS
        if condition.condition_id == "atc_governed_in_memory_reference"
    )
    condition = SyntheticMemoryCondition(reference_spec, protocol.budget)

    pre_action_read = condition.read(hidden.visible)
    assert pre_action_read.action is None
    outcome = _environment_outcome(hidden, "A0")
    assert outcome.accepted is False
    assert outcome.corrective_action == hidden.expected_action


def test_o01_is_deterministic_identifier_safe_and_equal_budget() -> None:
    first = run_o01_file(FIXTURE)
    second = run_o01_file(FIXTURE)

    assert first == second
    assert first["schema"] == O01_REPORT_SCHEMA
    assert first["repeats"] == 20
    assert all(
        condition["repeat_deterministic"]
        for condition in first["conditions"].values()
    )
    assert first["execution_scope"] == {
        "synthetic_opaque_data_only": True,
        "production_core_touched": False,
        "production_core_semantics_claimed": False,
        "external_code_models_or_network": False,
        "condition_state_isolated": True,
        "pre_action_oracle_exposed": False,
        "post_action_corrective_oracle_exposed": True,
        "wall_clock_access": False,
    }
    rendered = json.dumps(first, sort_keys=True)
    assert '"S0"' not in rendered
    assert '"S1"' not in rendered
    assert first["equal_budget"]["max_reads_per_step"] == 1
    assert first["equal_budget"]["max_writes_per_step"] == 2
    for condition in first["conditions"].values():
        for regime in condition["regimes"].values():
            observed = regime["budget_observed"]
            assert observed["max_write_attempts_per_step"] <= 2
            assert observed["max_read_attempts_per_step"] <= 1
            assert observed["max_selected_items"] <= 1


def test_o01_separates_write_read_utilization_action_and_recovery() -> None:
    report = run_o01_file(FIXTURE)
    required = {
        "write_admission_accuracy",
        "correct_later_read_coverage",
        "later_read_precision",
        "read_utilization_rate",
        "correct_next_action_count",
        "caos",
        "recovery_at_t",
        "post_shift_stale_activation_count",
    }
    for condition in report["conditions"].values():
        assert required <= condition["regimes"]["shifted"].keys()

    reference = report["conditions"]["atc_governed_in_memory_reference"]
    assert reference["label"] == "non-production governed in-memory reference"
    assert report["execution_scope"]["production_core_semantics_claimed"] is False
    append_online = report["conditions"]["append_log"]["regimes"]["online"]
    assert append_online["correct_later_read_coverage"] == 0.333333
    assert append_online["later_read_precision"] == 0.5
    assert reference["regimes"]["online"]["correct_later_read_coverage"] == 0.666667
    assert reference["regimes"]["online"]["later_read_precision"] == 1.0


def test_o01_frozen_ranking_instability_forces_hold() -> None:
    report = run_o01_file(FIXTURE)

    assert set(report["conditions"]) == {
        condition.condition_id for condition in PRIMARY_CONDITIONS
    }
    assert report["decision"]["state"] == "HOLD"
    assert "spearman_below_frozen_threshold" in report["decision"]["hold_reasons"]
    assert "rank_move_exceeds_frozen_threshold" not in report["decision"]["hold_reasons"]
    assert report["max_rank_move"] == 1.5
    assert min(report["tie_aware_spearman"].values()) < 0.7
    assert report["decision_average_ranks"]["off_policy"] == {
        "append_log": 2.0,
        "atc_governed_in_memory_reference": 2.0,
        "no_memory": 4.0,
        "stable_current_state": 2.0,
    }
    assert report["decision_tie_groups"]["off_policy"][0]["average_rank"] == 2.0
    assert report["tie_aware_spearman"] == {
        "off_policy_to_online": 0.333333,
        "off_policy_to_shifted": 0.816497,
        "online_to_shifted": 0.544331,
    }
    assert report["decision"]["frozen_criteria"] == {
        "rank_move_greater_than": 2,
        "spearman_less_than": 0.7,
        "recovery_must_be_stable": True,
        "shifted_caos_gap_greater_than": 0.05,
    }


def test_each_reference_rule_ablation_has_a_distinct_measured_effect() -> None:
    report = run_o01_file(FIXTURE)
    ablations = report["condition_rule_ablations"]

    assert {result["removed_rule"] for result in ablations.values()} == {
        "admission",
        "applicability",
        "currentness",
        "utilization",
    }
    assert all(result["repeat_deterministic"] for result in ablations.values())
    assert (
        ablations["reference_without_admission"][
            "write_admission_accuracy_delta_from_full_reference"
        ]
        > 0
    )
    assert (
        ablations["reference_without_applicability"][
            "write_admission_accuracy_delta_from_full_reference"
        ]
        > 0
    )
    assert (
        ablations["reference_without_currentness"][
            "shifted_stale_activation_delta_from_full_reference"
        ]
        > 0
    )
    assert any(
        delta > 0
        for delta in ablations["reference_without_utilization"][
            "regime_caos_delta_from_full_reference"
        ].values()
    )


def test_stable_control_resolves_epoch_and_feedback_ablation_is_reported() -> None:
    report = run_o01_file(FIXTURE)
    stable = report["conditions"]["stable_current_state"]["regimes"]["shifted"]

    assert stable["post_shift_stale_activation_count"] == 0
    no_feedback = report["condition_ablations"][
        "reference_without_post_action_feedback"
    ]
    assert no_feedback["removed_condition"] == (
        "supervised_post_action_corrective_oracle_feedback"
    )
    assert no_feedback["repeat_deterministic"] is True
    assert any(
        delta > 0
        for delta in no_feedback["regime_caos_delta_from_full_reference"].values()
    )
