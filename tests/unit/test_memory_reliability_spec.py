from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

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


def _canonical_json_bytes(value: object) -> bytes:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AssertionError("Packet A must contain only finite JSON values") from exc
    return encoded.encode("utf-8")


def _packet_a_digest(spec: dict[str, Any]) -> str:
    candidate = deepcopy(spec)
    binding = candidate["packet_a"]["content_binding"]
    binding.pop("specification_digest")
    return hashlib.sha256(_canonical_json_bytes(candidate)).hexdigest()


def _assert_finite_json(value: object) -> None:
    if isinstance(value, float):
        assert math.isfinite(value)
    elif isinstance(value, dict):
        for child in value.values():
            _assert_finite_json(child)
    elif isinstance(value, list):
        for child in value:
            _assert_finite_json(child)


def _assert_packet_a_contract(spec: dict[str, Any]) -> None:
    packet = spec["packet_a"]
    _assert_finite_json(packet)

    assert packet["schema_version"] == 1
    assert packet["specification_id"] == "atc-memory-reliability-packet-a-v1"
    assert packet["freeze_date"] == "2026-08-30"
    assert packet["status"] == "frozen_specification_only"
    assert packet["evidence_level"] == "L0"
    assert packet["authority"] == "research_contract_only"
    assert packet["canonical_integration"]["extends_specification_id"] == spec["specification_id"]
    assert packet["canonical_integration"]["does_not_create_parallel_fixture_or_runtime"] is True

    non_displacing = packet["non_displacing"]
    assert all(
        non_displacing[key] is value
        for key, value in {
            "research_lane": True,
            "active_frontier_remains_blocking": True,
            "product_dag_remains_authoritative": True,
            "product_prerequisite": False,
            "reorders_product_dag": False,
            "production_schema_authorized": False,
            "production_data_collection_authorized": False,
            "external_access_authorized": False,
            "execution_authorized": False,
            "promotion_authorized": False,
            "benchmark_manifest_frozen": False,
        }.items()
    )

    task_families = packet["task_families"]
    assert [(item["id"], item["label"]) for item in task_families] == [
        ("BUG_FIX", "bug fix"),
        ("REFACTOR", "refactor"),
        ("RELEASE_PREPARATION", "release preparation"),
        ("DOCUMENTATION_CONFIGURATION", "documentation/configuration"),
        ("INCIDENT_INVESTIGATION", "incident investigation"),
        ("CROSS_CLIENT_PROJECT_HANDOFF", "cross-client project handoff"),
    ]
    assert packet["confirmatory_design"]["task_family_count"] == len(task_families) == 6
    assert packet["calibration_pilot"]["task_family_count"] == len(task_families)

    arms = packet["arm_vocabulary"]
    arm_ids = [item["id"] for item in arms]
    condition_ids = [item["condition_id"] for item in arms]
    assert len(arm_ids) == len(set(arm_ids)) == 15
    assert len(condition_ids) == len(set(condition_ids)) == 15
    assert arm_ids == [
        "NO_MEMORY",
        "STATIC_TASK_NOTE",
        "STATIC_PROFILE",
        "APPEND_LOG_SEARCH",
        "CURRENT_RETRIEVAL",
        "SIMPLE_ATC_RETRIEVAL_V3",
        "OPTIMIZED_CAPSULE",
        "LONG_CONTEXT_CONTROL",
        "BEST_NON_ATC_HYBRID",
        "COMPETITOR_MEM0",
        "COMPETITOR_GRAPHITI",
        "COMPETITOR_HINDSIGHT",
        "COMPETITOR_LETTA",
        "COMPETITOR_LANGMEM",
        "MATCHED_HYBRIDS",
    ]
    assert arms[0]["unavailable_status"] == "SUPPORTED"
    assert all(item["unavailable_status"] == "UNSUPPORTED" for item in arms[1:])

    required_ablations = packet["required_ablations"]
    assert len(required_ablations) == len(set(required_ablations))
    assert {
        "checkpoint_without_reconciliation",
        "reconciliation_without_m1_binding",
        "m1_without_dependency_or_invalidation_closure",
        "semantic_acknowledgement_challenge_vs_content_free_placebo",
        "prospective_memory_without_negative_guards",
        "prospective_memory_without_current_version_reread",
        "prospective_memory_without_dependency_closure",
        "prospective_memory_without_action_ceiling",
        "m3_optimized_rebuild_vs_independent_full_rebuild",
        "continuity_debt_aggregate_vs_category_vector",
        "procedures_without_applicability_rollback_or_purge_closure",
    } <= set(required_ablations)
    assert set(packet["mutation_classes"]) == {
        "BRANCH_OR_SOURCE_REVISION_CHANGE",
        "CORRECTED_REQUIREMENTS",
        "DEPENDENCY_CHANGE",
        "ABANDONED_APPROACH",
        "ORDINARY_DELETION",
        "TERMINAL_PURGE",
        "PROJECT_AMBIGUITY",
        "EXTERNALLY_MODIFIED_FILES",
        "STALE_CHECKPOINT_SUPERFICIALLY_PLAUSIBLE",
    }

    permissions = packet["permission_contract"]
    assert permissions["same_across_arms"] is True
    assert permissions["authorization_precedes_relevance"] is True
    assert permissions["unknown_permission_state"] == "FAIL_CLOSED"
    assert permissions["unresolved_project_state"] == "ABSTAIN_NO_ISSUED_ARTIFACT"
    assert {
        "network_access",
        "provider_access",
        "credentials",
        "real_personal_context",
        "production_core_access",
        "operator_core_access",
        "external_effects",
        "gold_labels",
        "future_events",
        "other_condition_outputs",
    } <= set(permissions["forbidden"])

    secret_refusal = packet["secret_refusal"]
    assert secret_refusal["status"] == "frozen_before_assignment_or_storage"
    assert secret_refusal["refusal_code"] == "SECRET_REFUSAL"
    assert secret_refusal["not_a_failed_memory_episode"] is True
    assert secret_refusal["raw_value_not_retained_or_echoed"] is True

    safety = packet["hard_safety_policy"]
    assert safety["failure_is_non_compensable"] is True
    assert safety["failure_stops_affected_promotion"] is True
    assert safety["failure_cannot_be_averaged_away"] is True
    assert packet["hard_safety_rules"]

    cell_status = packet["cell_status_contract"]
    statuses = set(cell_status["allowed_statuses"])
    assert cell_status["indeterminate_pre_eligibility_code"] not in statuses
    assert cell_status["indeterminate_pre_eligibility_in_E_w"] is False
    assert cell_status["missing_status_is_retained"] is True
    assert cell_status["after_outcome_cell_removal"] is False
    assert cell_status["after_outcome_status_relabeling"] is False
    assert set(cell_status["non_credit_statuses"]) <= statuses

    opportunity = packet["opportunity_contract"]
    assert opportunity["eligible_opportunity_denominator"] == "E_w"
    assert opportunity["denominator_is_frozen_before_execution"] is True
    assert opportunity["denominator_is_mechanism_independent"] is True
    assert opportunity["eligibility_assigned_before_mechanism_result"] is True
    assert opportunity["every_eligible_opportunity_enters_E_w"] is True
    assert opportunity["outcome_dependent_exclusion"] is False
    assert opportunity["after_outcome_denominator_reconstruction"] is False
    assert opportunity["circular_denominator_reference"] is False
    assert opportunity["mechanism_defined_scored_event_denominator"] is False
    assert opportunity["pre_eligibility_unknown_is_outside_E_w"] is True
    assert opportunity["coverage_formula"] == "recorded_eligible_opportunity_statuses / E_w"
    assert opportunity["non_abstention_formula"] == (
        "(E_w - abstentions - errors - unsupported) / E_w"
    )
    for workstream in opportunity["workstreams"]:
        assert workstream["coverage_floor"] == 0.9
        assert workstream["non_abstention_floor"] == 0.9
        assert workstream["positive_opportunity_minimum"] > 0
        assert workstream["negative_opportunity_minimum"] > 0

    for estimand in packet["estimands"]:
        denominator = str(estimand["denominator"]).lower()
        assert "after" not in denominator
        assert "outcome" not in denominator
        assert "mechanism_result" not in denominator
        assert "scored_event" not in denominator
        assert estimand["direction"]
        assert estimand["interval"]

    power = packet["power_simulation"]
    assert power["status"] == "required_future_reproducibility_artifact_not_added_or_executed"
    assert power["script_path"] == "bench/memory_reliability_power_simulation.py"
    assert power["script_version"] == "packet-a-power-v1"
    assert power["simulation_seed"] == 20260829
    assert power["simulation_repetitions"] == 100000
    assert power["baseline_control_caos"] == 0.75
    assert power["alternative_caos"] == 0.85
    assert power["target_paired_effect"] == 0.1
    assert sum(power["paired_joint_distribution"].values()) == 1.0
    assert power["paired_correlation"] == 0.404226
    assert power["provisional_confirmatory_n"] == 384
    assert power["final_confirmatory_n"].startswith("unset_")

    assert len(packet["later_manifest_prerequisites"]) == 6
    assert packet["not_frozen_by_packet_a"] == [
        "confirmatory_fixture_ids",
        "benchmark_manifest",
        "final_confirmatory_N",
        "confirmatory_results",
        "promotion_decision",
        "product_schema",
        "production_or_live_behavior",
        "production_data_collection",
        "external_access",
        "release_or_support_claim",
    ]

    execution = packet["execution_boundary"]
    assert all(
        execution[key] is False
        for key in (
            "packet_a_executed",
            "benchmark_manifest_exists",
            "confirmatory_results_exist",
            "production_behavior_changed",
            "model_or_provider_run_performed",
            "l2_or_l3_packet_a_evidence_claimed",
        )
    )
    assert execution["wave4_l2_provenance_is_historical_input_only"] is True

    binding = packet["content_binding"]
    assert binding["algorithm"] == "SHA-256"
    assert binding["scope"] == "complete_machine_readable_specification"
    assert binding["specification_digest"] == _packet_a_digest(spec)

    for source in packet["provenance"]["canonical_inputs"]:
        source_path = ROOT / source["path"]
        assert source_path.is_file()
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source["sha256"]
    assert packet["provenance"]["secret_refusal_preserved"] is True
    assert packet["provenance"]["imported_text_remains_untrusted_data"] is True


def test_packet_a_specification_freeze_is_content_bound_and_non_displacing() -> None:
    spec = _load(SPEC_PATH)
    _assert_packet_a_contract(spec)


def test_packet_a_validation_fails_closed_on_drift_and_unsafe_or_circular_inputs() -> None:
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

    bad_permission = deepcopy(spec)
    bad_permission["packet_a"]["permission_contract"]["forbidden"].remove("network_access")
    mutations.append(("bad permission boundary", bad_permission))

    bad_safety = deepcopy(spec)
    bad_safety["packet_a"]["hard_safety_policy"]["failure_cannot_be_averaged_away"] = False
    mutations.append(("bad safety policy", bad_safety))

    unknown_category = deepcopy(spec)
    unknown_category["packet_a"]["cell_status_contract"]["allowed_statuses"].append(
        "MADE_UP_STATUS"
    )
    mutations.append(("unknown category", unknown_category))

    non_finite = deepcopy(spec)
    non_finite["packet_a"]["power_simulation"]["power_target"] = float("nan")
    mutations.append(("non-finite value", non_finite))

    digest_drift = deepcopy(spec)
    digest_drift["packet_a"]["freeze_date"] = "2099-01-01"
    mutations.append(("digest drift", digest_drift))

    execution_claim = deepcopy(spec)
    execution_claim["packet_a"]["execution_boundary"]["confirmatory_results_exist"] = True
    mutations.append(("execution claim", execution_claim))

    for _label, candidate in mutations:
        with pytest.raises(AssertionError):
            _assert_packet_a_contract(candidate)


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
