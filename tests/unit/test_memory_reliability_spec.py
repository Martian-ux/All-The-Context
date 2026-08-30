from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from bench.validate_memory_reliability_spec import (
    SpecificationValidationError,
    validate_spec,
    with_recomputed_digest,
)

ROOT = Path(__file__).parents[2]
SPEC_PATH = ROOT / "bench" / "memory_reliability_spec.json"
FIXTURE_PATH = ROOT / "bench" / "memory_reliability_fixtures.json"
PROGRAM_PATH = ROOT / "docs" / "research" / "ATC_MEMORY_EVALUATION_PROGRAM.md"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _walk_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(str(key))
            keys.update(_walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(_walk_keys(child))
    return keys


def _ids(values: list[dict[str, Any]]) -> list[str]:
    return [str(value["id"]) for value in values]


def _scenario(fixture: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    scenarios = fixture["scenarios"]
    assert isinstance(scenarios, list)
    return next(value for value in scenarios if value["id"] == scenario_id)


def test_packet_a_specification_freeze_is_content_bound_and_non_displacing() -> None:
    spec = _load(SPEC_PATH)
    validate_spec(spec)


def test_packet_a_contract_exposes_safety_denominator_and_structured_cells() -> None:
    spec = _load(SPEC_PATH)
    packet = spec["packet_a"]

    assert packet["arm_vocabulary"][1]["id"] == "STATIC_TASK_NOTE"
    assert packet["arm_vocabulary"][1]["unavailable_status"] == "SUPPORTED"
    assert (
        packet["cell_status_contract"]["response_statuses"]
        == packet["cell_status_contract"]["allowed_statuses"]
    )
    assert (
        packet["opportunity_contract"]["non_abstention_formula"]
        == "count(SUPPORTED response statuses) / E_w"
    )
    assert packet["hard_safety_exposure_contract"]["exposure_denominator"] == "S_h"
    assert packet["hard_safety_exposure_contract"]["required_nonzero_coverage"] is True
    assert packet["power_simulation"]["nonrecoverable_infrastructure_loss_allowance"] == 0.15
    assert packet["power_simulation"]["provisional_confirmatory_n_is_non_authoritative"] is True
    assert all(isinstance(item, dict) for item in packet["required_ablations"])
    assert all(isinstance(item, dict) for item in packet["mutation_cells"])
    assert all(isinstance(item, dict) for item in packet["matched_hybrid_cells"])


def test_packet_a_validation_fails_closed_on_semantic_mutations_even_with_rebound_digest() -> None:
    spec = _load(SPEC_PATH)

    mutations: list[tuple[str, Any]] = []

    circular = deepcopy(spec)
    circular["packet_a"]["opportunity_contract"]["outcome_dependent_exclusion"] = True
    mutations.append(("circular denominator", circular))

    after_outcome = deepcopy(spec)
    after_outcome["packet_a"]["estimands"][0]["denominator"] = "post_outcome_scored_events"
    mutations.append(("after-outcome denominator", after_outcome))

    missing_cell = deepcopy(spec)
    missing_cell["packet_a"]["arm_vocabulary"].pop()
    mutations.append(("missing arm cell", missing_cell))

    unknown_response_status = deepcopy(spec)
    unknown_response_status["packet_a"]["cell_status_contract"]["response_statuses"].append(
        "MADE_UP_STATUS"
    )
    mutations.append(("unknown response status", unknown_response_status))

    bad_permission = deepcopy(spec)
    bad_permission["packet_a"]["permission_contract"]["forbidden"].remove("network_access")
    mutations.append(("bad permission boundary", bad_permission))

    bad_safety = deepcopy(spec)
    bad_safety["packet_a"]["hard_safety_policy"]["failure_cannot_be_averaged_away"] = False
    mutations.append(("bad safety policy", bad_safety))

    missing_exposure = deepcopy(spec)
    del missing_exposure["packet_a"]["hard_safety_exposure_contract"]["coverage_floor"]
    mutations.append(("missing hard-safety exposure field", missing_exposure))

    indeterminate_exposure = deepcopy(spec)
    indeterminate_exposure["packet_a"]["hard_safety_exposure_contract"][
        "indeterminate_exposure_disposition"
    ] = "ALLOW_ZERO_FAILURE_CLAIM"
    mutations.append(("indeterminate exposure disposition", indeterminate_exposure))

    ambiguous_source = deepcopy(spec)
    ambiguous_source["packet_a"]["fixture_repository_contract"][
        "source_state_must_be_manifest_bound"
    ] = False
    mutations.append(("ambiguous source state", ambiguous_source))

    missing_provenance = deepcopy(spec)
    missing_provenance["packet_a"]["provenance"]["canonical_inputs"].pop()
    mutations.append(("missing provenance source", missing_provenance))

    provisional_authority = deepcopy(spec)
    provisional_authority["packet_a"]["power_simulation"][
        "provisional_confirmatory_n_is_non_authoritative"
    ] = False
    mutations.append(("authoritative provisional N", provisional_authority))

    missing_estimand_field = deepcopy(spec)
    del missing_estimand_field["packet_a"]["estimands"][0]["unit"]
    mutations.append(("non-operational estimand", missing_estimand_field))

    generic_hybrid = deepcopy(spec)
    generic_hybrid["packet_a"]["matched_hybrid_cells"][0]["control_cell_id"] = (
        "CELL_HYBRID_ATC_GOVERNED"
    )
    mutations.append(("ambiguous matched hybrid", generic_hybrid))

    unknown_category = deepcopy(spec)
    unknown_category["packet_a"]["cell_status_contract"]["allowed_statuses"].append(
        "MADE_UP_STATUS"
    )
    mutations.append(("unknown category", unknown_category))

    digest_drift = deepcopy(spec)
    digest_drift["packet_a"]["freeze_date"] = "2099-01-01"
    mutations.append(("digest drift", digest_drift))

    execution_claim = deepcopy(spec)
    execution_claim["packet_a"]["execution_boundary"]["confirmatory_results_exist"] = True
    mutations.append(("execution claim", execution_claim))

    for _label, candidate in mutations:
        candidate = with_recomputed_digest(candidate)
        with pytest.raises(SpecificationValidationError):
            validate_spec(candidate, require_golden_digest=False, validate_narrative=False)

    non_finite = deepcopy(spec)
    non_finite["packet_a"]["power_simulation"]["power_target"] = float("nan")
    with pytest.raises(SpecificationValidationError):
        validate_spec(non_finite, require_golden_digest=False, validate_narrative=False)


def test_packet_a_digest_drift_is_rejected_separately_from_semantic_validation() -> None:
    spec = _load(SPEC_PATH)
    spec["packet_a"]["freeze_date"] = "2099-01-01"
    with pytest.raises(SpecificationValidationError):
        validate_spec(spec)


def test_spec_is_explicitly_non_executable_and_fixes_first_five_order() -> None:
    spec = _load(SPEC_PATH)

    assert spec["schema_version"] == 1
    assert spec["status"] == "specification_only_no_harness_or_adapter_implemented"
    assert spec["adapter_boundary"]["status"] == "conceptual_contract_for_future_abi"
    assert spec["first_five_execution_order"] == ["E01", "E02", "E03", "E04", "E05"]

    experiments = spec["experiments"]
    assert isinstance(experiments, list)
    assert [experiment["order"] for experiment in experiments] == list(
        range(1, len(experiments) + 1)
    )
    assert [experiment["id"] for experiment in experiments[:5]] == [
        "E01",
        "E02",
        "E03",
        "E04",
        "E05",
    ]
    assert experiments[4]["execution_mode"] == "deterministic_exhaustive_faults"
    assert experiments[-1]["execution_mode"] == "consented_local_product_pilot"


def test_comparison_matrix_keeps_simple_competitor_hybrid_and_atc_cells_distinct() -> None:
    spec = _load(SPEC_PATH)
    groups = spec["system_groups"]

    simple = _ids(groups["simple_baselines"])
    competitors = _ids(groups["individual_competitors"])
    hybrids = _ids(groups["hybrids"])
    ablations = _ids(groups["atc_research_ablations"])
    all_ids = simple + competitors + hybrids + ablations

    assert len(all_ids) == len(set(all_ids))
    assert {
        "simple_no_memory",
        "simple_long_context",
        "simple_static_profile",
        "simple_append_log_search",
        "simple_atc_retrieval_v3",
    } == set(simple)
    assert competitors == [
        "competitor_mem0",
        "competitor_graphiti",
        "competitor_hindsight",
        "competitor_letta",
        "competitor_langmem",
    ]
    assert all(
        competitor["adapter_cell"] == "individual_unwrapped"
        and competitor["unsupported_operations_must_be_reported"] is True
        for competitor in groups["individual_competitors"]
    )
    assert set(hybrids) == {"hybrid_best_non_atc", "hybrid_atc_governed"}
    assert "atc_full_research_stack" in ablations

    first = spec["experiments"][0]
    assert first["required_system_groups"] == [
        "simple_baselines",
        "individual_competitors",
        "hybrids",
    ]


def test_logical_fixtures_are_symbolic_bounded_and_cover_every_capability_twice() -> None:
    spec = _load(SPEC_PATH)
    fixture = _load(FIXTURE_PATH)
    scenarios = fixture["scenarios"]

    assert fixture["schema_version"] == 1
    assert fixture["status"] == "logical_specification_fixture_not_executable_harness"
    assert fixture["content_policy"]["synthetic"] is True
    assert fixture["content_policy"]["real_personal_context"] is False
    assert len(scenarios) == 18
    assert len(_ids(scenarios)) == len(set(_ids(scenarios)))

    forbidden_raw_keys = set(fixture["content_policy"]["raw_text_fields_forbidden"])
    assert forbidden_raw_keys.isdisjoint(_walk_keys(fixture))

    coverage: Counter[str] = Counter()
    declared = set(spec["capabilities"])
    expected_oracle_fields = set(fixture["scenario_oracle_fields"])
    for scenario in scenarios:
        capabilities = set(scenario["capabilities"])
        assert capabilities
        assert capabilities <= declared
        coverage.update(capabilities)

        events = scenario["events"]
        assert 1 <= len(events) <= 16
        assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
        assert [event["at"] for event in events] == sorted(event["at"] for event in events)
        assert all(event["principal"] in fixture["principals"] for event in events)
        assert all(event["source_class"] in fixture["source_classes"] for event in events)

        assert scenario["checkpoints"]
        assert all(
            checkpoint["after_seq"] <= events[-1]["seq"]
            and checkpoint["client"] in fixture["clients"]
            and checkpoint["target"] in fixture["targets"]
            for checkpoint in scenario["checkpoints"]
        )
        assert set(scenario["oracle"]) == expected_oracle_fields
        assert all(isinstance(value, list) for value in scenario["oracle"].values())

    assert set(coverage) == declared
    assert all(count >= 2 for count in coverage.values())


def test_every_referenced_local_fixture_exists_and_unknown_gold_is_not_adapter_visible() -> None:
    spec = _load(SPEC_PATH)
    fixture = _load(FIXTURE_PATH)
    available = set(_ids(fixture["scenarios"]))
    referenced = {
        fixture_id
        for experiment in spec["experiments"]
        for fixture_id in experiment.get("fixture_ids", [])
    }

    assert referenced <= available
    assert spec["fixture"]["adapter_visible_gold"] is False
    assert {
        "gold_labels",
        "forbidden_sets",
        "promotion_thresholds",
        "future_events",
        "other_condition_outputs",
    } == set(spec["adapter_boundary"]["adapter_forbidden_inputs"])


def test_privacy_fixture_freezes_authorization_and_import_invariance_oracle() -> None:
    fixture = _load(FIXTURE_PATH)
    scenario = _scenario(fixture, "privacy_authorization_invariance")
    checkpoints = scenario["checkpoints"]
    oracle = scenario["oracle"]

    assert len(checkpoints) == 2
    assert checkpoints[0]["task_class"] == checkpoints[1]["task_class"]
    assert "include_unauthorized_in_index" in checkpoints[0]["faults"]
    assert checkpoints[1]["faults"] == []
    assert any(
        event["source_class"] == "untrusted_import" and event["operation"] == "import_data"
        for event in scenario["events"]
    )
    assert (
        "TOKEN_EQUAL_OBSERVABLE_TRANSCRIPT_ACROSS_PRIVACY_CHECKPOINTS" in oracle["required_actions"]
    )
    assert "TOKEN_PRIVATE_CANARY_7QZ" in oracle["forbidden_influence"]
    assert "IMPORT_INSTRUCTION_SHAPED:CONFIGURATION" in oracle["forbidden_influence"]


def test_forgetting_fixture_distinguishes_all_lifecycle_operations() -> None:
    fixture = _load(FIXTURE_PATH)
    scenario = _scenario(fixture, "forgetting_operations_are_distinct")
    operations = {event["operation"] for event in scenario["events"]}

    assert {
        "evict_working_context",
        "apply_ranking_decay",
        "retire_procedure",
        "soft_delete",
        "restore",
        "purge",
    } <= operations
    assert "CLAIM_FORGET_TEST:TOKEN_KEEP_AS_TRUTH" in scenario["oracle"]["required_current"]
    assert "CLAIM_PURGE_FINAL:*" in scenario["oracle"]["forbidden_influence"]
    assert scenario["oracle"]["reachable_private_artifacts_after_completion"] == []


def test_correction_and_consequence_oracles_invalidate_every_stale_surface() -> None:
    fixture = _load(FIXTURE_PATH)
    convergence = _scenario(fixture, "correction_converges_all_surfaces")
    consequence = _scenario(fixture, "consequence_correction_before_consume")

    assert set(convergence["oracle"]["invalidated_artifacts"]) == {
        "WORKING_COLOR_V1",
        "SUMMARY_COLOR_V1",
        "RELATION_COLOR_V1",
        "CAPSULE_COLOR_V1",
    }
    consequence_operations = [event["operation"] for event in consequence["events"]]
    assert (
        consequence_operations.index("prepare_token")
        < consequence_operations.index("correct_contract")
        < consequence_operations.index("consume_token")
    )
    assert consequence["oracle"]["accepted_tokens"] == []
    assert consequence["oracle"]["rejected_tokens"] == ["TOKEN_SEND_V1:REVOKED"]
    assert "TOKEN_CROSS_PROTECTED_CHECKPOINT" in consequence["oracle"]["forbidden_actions"]


def test_outcome_closure_fixture_freezes_dependency_and_zero_residue_oracle() -> None:
    fixture = _load(FIXTURE_PATH)
    scenario = _scenario(fixture, "purge_rebuild_removes_private_lineage")
    derivation = next(
        event for event in scenario["events"] if event["operation"] == "derive_artifacts"
    )
    operations = [event["operation"] for event in scenario["events"]]

    assert (
        operations.index("derive_artifacts")
        < operations.index("purge")
        < operations.index("rebuild")
    )
    assert set(derivation["attributes"]["artifacts"]) == set(
        scenario["oracle"]["invalidated_artifacts"]
    )
    assert scenario["oracle"]["reachable_private_artifacts_after_completion"] == []
    assert "TOKEN_PRIVATE_LINEAGE_X" in scenario["oracle"]["forbidden_influence"]
    assert {
        "orphan_page_scan",
        "temporary_file_scan",
        "snapshot_scan",
        "backup_boundary_scan",
    } <= set(scenario["checkpoints"][0]["faults"])


def test_promotion_gates_compare_to_strongest_simpler_condition_with_intervals() -> None:
    spec = _load(SPEC_PATH)
    gates = {gate["id"]: gate for gate in spec["promotion_gates"]}

    assert {
        "universal_safety",
        "working_portability",
        "semantic_temporal",
        "episodic_procedural",
        "relational",
        "recall_to_action",
        "correction",
        "forgetting_privacy",
        "consequence_closure",
        "outcome_closure",
    } == set(gates)
    for gate_id in (
        "working_portability",
        "semantic_temporal",
        "episodic_procedural",
        "relational",
        "recall_to_action",
        "forgetting_privacy",
    ):
        gate = gates[gate_id]
        comparison = gate.get("paired_improvement") or gate.get("noninferiority")
        assert comparison["comparator"] == "strongest_simpler_eligible_condition"
        assert any("confidence_interval" in key for key in comparison)

    for gate_id in ("universal_safety", "consequence_closure", "outcome_closure"):
        assert (
            gates[gate_id]["confidence_reporting"]
            == "exact_one_sided_95_percent_binomial_upper_bound"
        )


def test_statistical_cost_and_latency_contracts_prevent_budget_driven_wins() -> None:
    spec = _load(SPEC_PATH)
    statistics = spec["statistics"]
    budgets = spec["budgets"]
    local_budget_ids = {budget["id"] for budget in budgets["local"]}

    assert statistics["stochastic"]["paired_episode_snapshots_and_seeds"] is True
    assert statistics["stochastic"]["cluster_unit"] == "episode"
    assert statistics["stochastic"]["multiplicity"] == "holm_within_confirmatory_family"
    assert statistics["stochastic"]["confirmatory_sample_size"] == {
        "method": "simulation_from_smallest_effect_worth_detecting",
        "familywise_alpha": 0.05,
        "minimum_power": 0.8,
        "preferred_primary_caos_power": 0.9,
    }
    assert (
        statistics["deterministic"]["safety_interval"]
        == "exact_one_sided_95_percent_clopper_pearson"
    )
    assert {
        "ingest_p95_ms",
        "query_compile_p95_ms",
        "checkpoint_export_p95_ms",
        "checkpoint_import_p95_ms",
        "correction_invalidation_p95_ms",
        "token_consume_p99_ms",
        "deterministic_rebuild_seconds",
    } == local_budget_ids
    assert budgets["cost_promotion"]["same_reader_controller_and_reasoning_effort"] is True
    assert budgets["cost_promotion"]["maximum_end_to_end_cost_premium"] == 0.25
    assert budgets["cost_promotion"]["minimum_caos_gain_if_cost_premium_positive"] == 0.05


def test_program_cites_primary_benchmarks_and_rejects_retrieval_only_success() -> None:
    program = PROGRAM_PATH.read_text(encoding="utf-8")

    for url in (
        "https://github.com/xiaowu0162/LongMemEval",
        "https://github.com/xiaowu0162/LongMemEval-V2",
        "https://github.com/HUST-AI-HYZ/MemoryAgentBench",
        "https://memoryarena.github.io/",
        "https://arxiv.org/abs/2602.16313",
    ):
        assert url in program
    assert "Current Authorized Outcome Success (CAOS)" in program
    assert "The component outcomes are always reported separately" in program
    assert "Passing the local suite is necessary and insufficient for promotion" in program
    assert "does not define or implement the future adapter ABI" in program
