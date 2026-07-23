from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from allthecontext.memory_lab_o01 import (
    O01_REPORT_SCHEMA,
    PRIMARY_CONDITIONS,
    VisibleOutcome,
    VisibleStep,
    load_protocol,
    run_o01_file,
)

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "bench" / "memory_lab_o01_fixture.json"


def test_visible_condition_input_cannot_contain_oracle_action() -> None:
    assert "expected_action" not in {field.name for field in fields(VisibleStep)}
    assert "expected_action" not in {field.name for field in fields(VisibleOutcome)}
    protocol = load_protocol(FIXTURE)
    assert all(
        not hasattr(step.visible, "expected_action")
        for regime in protocol.regimes
        for step in regime.steps
    )


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
        "oracle_action_exposed_to_conditions": False,
        "post_action_feedback_is_explicit_visible_environment_outcome": True,
        "wall_clock_access": False,
    }
    rendered = json.dumps(first, sort_keys=True)
    assert '"S0"' not in rendered
    assert '"S1"' not in rendered
    assert "expected_action" not in rendered
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
        "later_read_correct_rate",
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


def test_o01_frozen_ranking_instability_forces_hold() -> None:
    report = run_o01_file(FIXTURE)

    assert set(report["conditions"]) == {
        condition.condition_id for condition in PRIMARY_CONDITIONS
    }
    assert report["decision"]["state"] == "HOLD"
    assert "spearman_below_frozen_threshold" in report["decision"]["hold_reasons"]
    assert min(report["spearman"].values()) < 0.7
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
        "explicit_post_action_environment_feedback"
    )
    assert no_feedback["repeat_deterministic"] is True
    assert any(
        delta > 0
        for delta in no_feedback["regime_caos_delta_from_full_reference"].values()
    )
