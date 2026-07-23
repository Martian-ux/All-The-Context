from __future__ import annotations

import hashlib
import inspect
import json

import pytest
from allthecontext.memory_reliability_lab import (
    FULL_SPEC_SCENARIO_COUNT,
    LONGITUDINAL_ADAPTER_ABI,
    LONGITUDINAL_REPORT_SCHEMA,
    AtcGovernedReferenceAdapter,
    ConditionManifest,
    EpisodeBudget,
    LogicalEvent,
    report_contains_fixture_symbols,
)

from bench.memory_reliability_lab_e01 import FIXTURES, load_fixture, run_fixture

FIXTURE_SHA256 = "19f52ad16b398248866b5bfac930380ee71071a3414a4678a316bd161f762086"


def test_fixture_is_frozen_symbolic_partial_e01_with_required_coverage() -> None:
    scenarios, budget = load_fixture()
    fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))
    capabilities = {
        capability for scenario in scenarios for capability in scenario.capabilities
    }

    assert hashlib.sha256(FIXTURES.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert fixture["content_policy"] == {
        "synthetic": True,
        "symbolic_values_only": True,
        "real_personal_context": False,
        "imported_content_is_untrusted_data": True,
    }
    assert fixture["clock"]["wall_clock_access"] is False
    assert len(scenarios) == 6
    assert len(scenarios) < FULL_SPEC_SCENARIO_COUNT
    assert budget == EpisodeBudget(max_items=2, max_token_units=2)
    assert {
        "semantic",
        "authority",
        "correction",
        "forgetting",
        "privacy",
        "outcome_closure",
        "epistemic_role",
        "task_applicability",
        "cross_project_domain",
        "harmful_memory",
    } <= capabilities
    assert {"text", "prompt", "message", "response"}.isdisjoint(
        {
            key
            for scenario in fixture["scenarios"]
            for event in scenario["events"]
            for key in event
        }
    )


def test_partial_e01_compares_equal_budget_controls_and_reference_model() -> None:
    report = run_fixture()
    conditions = report["conditions"]

    assert report["schema"] == LONGITUDINAL_REPORT_SCHEMA
    assert report["adapter_abi"] == LONGITUDINAL_ADAPTER_ABI
    assert report["fixture_sha256"] == FIXTURE_SHA256
    assert report["execution_scope"] == {
        "kind": "smallest_executable_longitudinal_slice",
        "executed_scenario_count": 6,
        "full_spec_scenario_count": 18,
        "full_spec_executed": False,
        "production_core_touched": False,
        "production_schema_added": False,
        "external_systems_exercised": False,
        "wall_clock_timing_measured": False,
        "production_core_semantics": "production_core_semantics_not_exercised",
        "governed_condition_identity": "new_in_memory_reference_model_not_current_atc",
    }
    assert {
        "simple_no_memory",
        "simple_append_log_search",
        "atc_governed_reference",
        "ablation_without_authority",
        "ablation_without_currentness_invalidation",
        "ablation_without_applicability",
        "ablation_without_purge_closure",
    } == set(conditions)

    budgets = {json.dumps(result["budget"], sort_keys=True) for result in conditions.values()}
    event_counts = {
        int(result["accounting"]["presented_event_count"]) for result in conditions.values()
    }
    checkpoint_counts = {
        int(result["accounting"]["checkpoint_count"]) for result in conditions.values()
    }
    assert budgets == {json.dumps(report["equal_budget"], sort_keys=True)}
    assert event_counts == {46}
    assert checkpoint_counts == {12}
    assert all(result["accounting"]["model_calls"] == 0 for result in conditions.values())
    assert all(result["accounting"]["network_calls"] == 0 for result in conditions.values())
    assert all(result["metrics"]["budget_escape_count"] == 0 for result in conditions.values())
    assert all(result["metrics"]["repeat_deterministic"] is True for result in conditions.values())


def test_reference_model_passes_bounded_oracles_but_is_not_labeled_current_atc() -> None:
    report = run_fixture()
    metrics = report["conditions"]["atc_governed_reference"]["metrics"]
    manifest = report["conditions"]["atc_governed_reference"]["manifest"]

    assert metrics["exact_current_authorized_state_success_count"] == 6
    assert metrics["exact_current_authorized_state_success_rate"] == 1.0
    assert metrics["forbidden_influence_count"] == 0
    assert metrics["authorized_but_inapplicable_count"] == 0
    assert metrics["cross_project_domain_leakage_count"] == 0
    assert metrics["harmful_memory_non_abstention_count"] == 0
    assert metrics["purge_residue_count"] == 0
    assert metrics["correction_convergence_rate"] == 1.0
    assert metrics["forgetting_semantics_rate"] == 1.0
    assert metrics["harmful_abstention_accuracy"] == 1.0
    assert manifest["condition_id"] == "atc_governed_reference"
    assert manifest["touches_production_core"] is False
    condition_ids = set(report["conditions"])
    assert "current-atc" not in condition_ids
    assert "current_atc" not in condition_ids


def test_controls_expose_retrieval_equivalence_and_harmful_memory_failures() -> None:
    report = run_fixture()
    no_memory = report["conditions"]["simple_no_memory"]["metrics"]
    append_log = report["conditions"]["simple_append_log_search"]["metrics"]

    assert no_memory["exact_current_authorized_state_success_count"] == 1
    assert no_memory["exact_current_authorized_state_success_rate"] == 0.166667
    assert no_memory["harmful_abstention_accuracy"] == 1.0
    assert append_log["exact_current_authorized_state_success_count"] == 0
    assert append_log["forbidden_influence_count"] == 8
    assert append_log["authorized_but_inapplicable_count"] == 3
    assert append_log["cross_project_domain_leakage_count"] == 1
    assert append_log["harmful_memory_non_abstention_count"] == 1
    assert append_log["purge_residue_count"] == 1
    assert append_log["correction_convergence_rate"] == 0.0
    assert append_log["forgetting_semantics_rate"] == 0.0
    assert append_log["failure_code_counts"] == {
        "CORRECTION_NONCONVERGENCE": 1,
        "CROSS_PROJECT_DOMAIN_LEAKAGE": 1,
        "EPISTEMIC_ROLE_MISUSE": 1,
        "FORGETTING_SEMANTIC_COLLAPSE": 2,
        "HARMFUL_MEMORY_NON_ABSTENTION": 1,
        "PROCEDURE_FALSE_TRANSFER": 1,
        "PURGE_RESIDUE": 1,
        "RETRIEVAL_MISS": 2,
        "WITNESS_COLLAPSE": 1,
    }
    assert report["bounded_hypotheses"] == {
        "append_log_equivalent_to_governed_reference": "falsified_in_bounded_slice",
        "append_log_matches_governed_harmful_abstention": "falsified_in_bounded_slice",
        "no_memory_equivalent_to_governed_reference": "falsified_in_bounded_slice",
    }


def test_rule_removal_ablations_attribute_each_reference_gain() -> None:
    report = run_fixture()

    assert report["rule_ablation_results"] == {
        "ablation_without_applicability": {
            "removed_rule": "applicability",
            "exact_success_rate_delta_from_reference": 0.5,
            "forbidden_influence_delta_from_reference": 4,
            "purge_residue_delta_from_reference": 0,
            "harmful_non_abstention_delta_from_reference": 1,
        },
        "ablation_without_authority": {
            "removed_rule": "authority",
            "exact_success_rate_delta_from_reference": 0.166667,
            "forbidden_influence_delta_from_reference": 1,
            "purge_residue_delta_from_reference": 0,
            "harmful_non_abstention_delta_from_reference": 0,
        },
        "ablation_without_currentness_invalidation": {
            "removed_rule": "currentness_invalidation",
            "exact_success_rate_delta_from_reference": 0.333333,
            "forbidden_influence_delta_from_reference": 2,
            "purge_residue_delta_from_reference": 0,
            "harmful_non_abstention_delta_from_reference": 0,
        },
        "ablation_without_purge_closure": {
            "removed_rule": "purge_closure",
            "exact_success_rate_delta_from_reference": 0.166667,
            "forbidden_influence_delta_from_reference": 0,
            "purge_residue_delta_from_reference": 1,
            "harmful_non_abstention_delta_from_reference": 0,
        },
    }
    assert report["validity_risks"] == [
        "fixture_oracle_and_reference_rule_codesign",
        "small_hand_authored_symbolic_slice",
        "no_production_core_execution",
        "no_external_system_execution",
    ]


def test_reports_are_identifier_free_and_oracles_do_not_cross_adapter_boundary() -> None:
    scenarios, _budget = load_fixture()
    report = run_fixture()
    rendered = json.dumps(report, sort_keys=True)

    assert report_contains_fixture_symbols(report, scenarios) is False
    for scenario in scenarios:
        assert scenario.scenario_id not in rendered
        assert scenario.checkpoint.checkpoint_id not in rendered
        assert not hasattr(scenario.checkpoint, "required_values")
        assert not hasattr(scenario.checkpoint, "forbidden")
        for event in scenario.events:
            assert event.object_id not in rendered
            if event.value is not None:
                assert event.value not in rendered


def test_reference_module_has_no_production_core_dependency() -> None:
    source = inspect.getsource(
        __import__(
            "allthecontext.memory_reliability_lab",
            fromlist=["memory_reliability_lab"],
        )
    )

    assert "CoreStore" not in source
    assert "RetrievalEngine" not in source


def _event(
    seq: int,
    operation: str,
    object_id: str,
    *,
    principal: str = "P_ALPHA",
    source_class: str = "host_attested_user_turn",
    role: str | None = None,
) -> LogicalEvent:
    memory_operation = operation in {"set_claim", "set_procedure"}
    return LogicalEvent(
        seq=seq,
        at=f"2035-03-01T00:{seq:02d}:00Z",
        principal=principal,
        source_class=source_class,
        operation=operation,
        object_id=object_id,
        topic="TOKEN_CONTROL" if memory_operation else None,
        role=role if memory_operation else None,
        value="TOKEN_VALUE" if memory_operation else None,
    )


def test_authority_rule_rejects_foreign_and_untrusted_control_operations() -> None:
    adapter = AtcGovernedReferenceAdapter()
    adapter.reset("RUN_CONTROL_AUTHORITY", "P_ALPHA", "2035-03-01T00:00:00Z")
    adapter.present(_event(1, "set_claim", "CLAIM_CONTROL", role="CLAIM"))
    adapter.present(
        _event(2, "soft_delete", "CLAIM_CONTROL", principal="P_BETA")
    )
    adapter.present(
        _event(
            3,
            "purge",
            "CLAIM_CONTROL",
            source_class="untrusted_import",
        )
    )
    assert adapter.inventory()["CLAIM_CONTROL"] == "CURRENT"

    adapter.present(_event(4, "soft_delete", "CLAIM_CONTROL"))
    adapter.present(_event(5, "restore", "CLAIM_CONTROL", principal="P_BETA"))
    adapter.present(
        _event(
            6,
            "restore",
            "CLAIM_CONTROL",
            source_class="untrusted_import",
        )
    )
    assert adapter.inventory()["CLAIM_CONTROL"] == "DELETED"

    adapter.present(
        _event(7, "set_procedure", "PROCEDURE_CONTROL", role="PROCEDURE")
    )
    adapter.present(
        _event(8, "retire_procedure", "PROCEDURE_CONTROL", principal="P_BETA")
    )
    adapter.present(
        _event(
            9,
            "retire_procedure",
            "PROCEDURE_CONTROL",
            source_class="untrusted_import",
        )
    )
    assert adapter.inventory()["PROCEDURE_CONTROL"] == "CURRENT"


def test_purge_is_terminal_against_restore_and_same_id_recreation() -> None:
    adapter = AtcGovernedReferenceAdapter()
    adapter.reset("RUN_TERMINAL_PURGE", "P_ALPHA", "2035-03-01T00:00:00Z")
    adapter.present(_event(1, "set_claim", "CLAIM_TERMINAL", role="CLAIM"))
    adapter.present(_event(2, "purge", "CLAIM_TERMINAL"))
    adapter.present(_event(3, "restore", "CLAIM_TERMINAL"))
    adapter.present(_event(4, "set_claim", "CLAIM_TERMINAL", role="CLAIM"))

    assert "CLAIM_TERMINAL" not in adapter.inventory()


def test_contracts_fail_closed_on_unsafe_manifest_or_nondeterministic_protocol() -> None:
    with pytest.raises(ValueError, match="offline and noncanonical"):
        ConditionManifest(
            condition_id="unsafe",
            name="Unsafe",
            lifecycle_model="writer",
            writes_canonical_state=True,
        )
    with pytest.raises(ValueError, match="unknown governed reference rules"):
        AtcGovernedReferenceAdapter(enabled_rules=frozenset({"unknown"}))
