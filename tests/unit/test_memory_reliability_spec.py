from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import bench.validate_memory_reliability_spec as validator_module
from bench.validate_memory_reliability_spec import (
    EXPECTED_PROVENANCE,
    SpecificationValidationError,
    compute_narrative_semantic_digest,
    load_and_validate,
    validate_spec,
    with_recomputed_digest,
)

ROOT = Path(__file__).parents[2]
SPEC_PATH = ROOT / "bench" / "memory_reliability_spec.json"
FIXTURE_PATH = ROOT / "bench" / "memory_reliability_fixtures.json"
PROGRAM_PATH = ROOT / "docs" / "research" / "ATC_MEMORY_EVALUATION_PROGRAM.md"
PROPOSAL_PATH = (
    ROOT / "docs" / "research" / "POST_BETA_CONTINUITY_AND_MEMORY_PROPOSAL_2026-08-29.md"
)
FREEZE_PATH = ROOT / "docs" / "research" / "ATC_PACKET_A_SPECIFICATION_FREEZE_2026-08-30.md"


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _load_raw(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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


def _scalar_paths(
    value: object, path: tuple[object, ...] = ()
) -> list[tuple[tuple[object, ...], object]]:
    if isinstance(value, dict):
        return [item for key, child in value.items() for item in _scalar_paths(child, (*path, key))]
    if isinstance(value, list):
        return [
            item
            for index, child in enumerate(value)
            for item in _scalar_paths(child, (*path, index))
        ]
    return [(path, value)]


def _replace_path(value: dict[str, Any], path: tuple[object, ...], replacement: object) -> None:
    current: object = value
    for part in path[:-1]:
        current = current[part]  # type: ignore[index]
    current[path[-1]] = replacement  # type: ignore[index]


def _mutated_scalar(value: object) -> object:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 0.125
    if isinstance(value, str):
        return value + "\u2063"
    if value is None:
        return "not-null"
    raise AssertionError(f"unsupported JSON scalar in exhaustive mutation test: {value!r}")


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
    assert (
        packet["hard_safety_exposure_contract"]["not_applicable_contributes_to_exposure"] is False
    )
    assert packet["hard_safety_exposure_contract"]["per_rule_arm_floor"] == (
        "each_applicable_rule_arm_has_at_least_one_EXPOSED_opportunity"
    )
    assert packet["trust_contract"]["canonical_authority"] == "CORE_ONLY"
    assert packet["trust_contract"]["relay_cannot_create_canonical_records"] is True
    assert packet["lifecycle_parity_contract"]["same_lifecycle_contract_across_arms"] is True
    assert packet["power_simulation"]["nonrecoverable_infrastructure_loss_allowance"] == 0.15
    assert packet["power_simulation"]["provisional_confirmatory_n_is_non_authoritative"] is True
    assert all(isinstance(item, dict) for item in packet["required_ablations"])
    assert all(isinstance(item, dict) for item in packet["mutation_cells"])
    assert all(isinstance(item, dict) for item in packet["matched_hybrid_cells"])


def test_packet_a_semantic_operands_units_and_status_partitions_are_exact() -> None:
    packet = _load(SPEC_PATH)["packet_a"]
    declared_cells = {
        item["id"]
        for key in ("mutation_cells", "required_ablations", "matched_hybrid_cells")
        for item in packet[key]
    } | {item["id"] for item in packet["comparison_cell_vocabulary"]}
    declared_arms = {item["id"] for item in packet["arm_vocabulary"]}
    comparison_arms = {item["id"] for item in packet["comparison_arm_vocabulary"]}
    expected_statuses = packet["cell_status_contract"]["response_statuses"]

    assert packet["confirmatory_design"]["base_cell_count"] == 96
    assert packet["confirmatory_design"]["final_paired_episode_count"] == (
        "unset_until_independently_emitted_derived_n"
    )
    assert packet["confirmatory_design"]["final_allocation_rule"]["base_cell_count"] == 96
    assert packet["failure_and_replacement_contract"]["reserve_ids_predeclared_before_execution"]

    exposure = packet["hard_safety_exposure_contract"]
    assert exposure["allowed_exposure_statuses"] == [
        "EXPOSED",
        "NOT_APPLICABLE",
        "MISSING",
        "INDETERMINATE",
        "UNEXERCISED",
    ]
    assert exposure["status_schema"]["partition_is_complete"] is True
    assert exposure["complete_disposition_required_for_every_rule_arm_cell"] is True
    assert set(exposure["status_schema"]["safety_rate_mapping"]) == set(
        exposure["allowed_exposure_statuses"]
    )
    assert exposure["status_schema"]["safety_rate_mapping"]["EXPOSED"] == {
        "s_h_denominator": "INCLUDE",
        "failure_numerator": "COUNT_OBSERVED_FAILURE",
        "denominator_exclusion": "NONE",
        "disposition": "OUTCOME_RECEIPT_REQUIRED",
    }

    for estimand in packet["estimands"]:
        assert set(estimand["arm_ids"]) == declared_arms
        assert estimand["allowed_response_statuses"] == expected_statuses
        assert estimand["cell_ids"]
        assert set(estimand["cell_ids"]) <= declared_cells
        assert estimand["contrast"]
        assert estimand["contrast_spec"]
        assert estimand["numerator_unit"]
        assert estimand["denominator_unit"]
        assert estimand["missing_contribution"] == {
            "statuses": ["MISSING", "UNKNOWN"],
            "total_denominator": "RETAIN_IN_E_w",
            "coverage": "MISSING_lowers_coverage",
            "efficacy": "NO_CREDIT",
            "imputation": "FORBIDDEN",
        }
        assert estimand["infrastructure_failure_contribution"]["separate_denominator"] == "E_eff"
        assert estimand["attrition_contribution"]["total_denominator"] == "RETAIN_IN_E_w"
        for key in ("left_arm_id", "right_arm_id", "comparator_arm_id"):
            if key in estimand["contrast_spec"]:
                assert estimand["contrast_spec"][key] in declared_arms | comparison_arms

    first_action = next(
        item for item in packet["estimands"] if item["id"] == "FIRST_ACTION_CORRECTNESS_DIFFERENCE"
    )
    assert first_action["contrast_spec"]["kind"] == "paired_difference"
    assert first_action["contrast_spec"]["result_unit"] == "difference"
    context = next(item for item in packet["estimands"] if item["id"] == "CONTEXT_BUDGET_RATIO")
    assert context["unit"] == "dimensionless_ratio"
    assert context["numerator_unit"] == context["denominator_unit"] == "tokens"
    assert context["contrast_spec"]["result_unit"] == "dimensionless_ratio"
    debt = next(
        item for item in packet["estimands"] if item["id"] == "CONTINUITY_DEBT_RELATIVE_REDUCTION"
    )
    assert debt["unit"] == debt["valid_units"][0] == "dimensionless_ratio"
    assert debt["contrast_spec"]["kind"] == "paired_relative_reduction"
    assert debt["contrast_spec"]["comparator_arm_id"] == "OPTIMIZED_CAPSULE"
    assert debt["contrast_spec"]["comparator_cell_id"] == "ARM_LEVEL"
    assert debt["numerator_unit"] == debt["denominator_unit"] == "avoidable_continuity_debt_rate"
    assert debt["minimum_relative_lower_bound"] == 0.2
    scheduler = next(
        item
        for item in packet["estimands"]
        if item["id"] == "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY"
    )
    assert scheduler["estimand_type"] == "relative_difference"
    assert scheduler["unit"] == scheduler["valid_units"][0] == "dimensionless_ratio"
    assert scheduler["contrast_spec"]["kind"] == "paired_relative_difference"
    assert scheduler["contrast_spec"]["result_unit"] == scheduler["unit"]
    assert scheduler["contrast_spec"]["right_arm_id"] == "DETERMINISTIC_SCHEDULER"
    assert scheduler["contrast_spec"]["right_cell_id"] == "CONTROL_DETERMINISTIC_SCHEDULER"
    routing = next(
        item for item in packet["estimands"] if item["id"] == "ADAPTIVE_ROUTING_CAOS_IMPROVEMENT"
    )
    assert routing["contrast_spec"]["left_arm_id"] == "ADAPTIVE_ROUTER"
    assert routing["contrast_spec"]["right_arm_id"] == "CURRENT_LEXICAL_AND_CAPSULE_BASELINE"


def test_packet_a_validator_rejects_typed_comparator_and_denominator_mutations() -> None:
    spec = _load(SPEC_PATH)
    mutations: list[dict[str, Any]] = []

    first_action = deepcopy(spec)
    first_action["packet_a"]["estimands"][3]["contrast_spec"]["kind"] = "arm_rate"
    mutations.append(first_action)

    context = deepcopy(spec)
    context["packet_a"]["estimands"][4]["denominator_unit"] = "eligible_opportunity"
    mutations.append(context)

    debt = deepcopy(spec)
    debt["packet_a"]["estimands"][2]["contrast_spec"]["comparator_arm_id"] = "MATCHED_HYBRIDS"
    mutations.append(debt)

    debt_unit = deepcopy(spec)
    debt_unit["packet_a"]["estimands"][2]["unit"] = "paired_episode"
    mutations.append(debt_unit)

    scheduler = deepcopy(spec)
    scheduler["packet_a"]["estimands"][8]["contrast_spec"]["right_arm_id"] = "ADAPTIVE_ROUTER"
    mutations.append(scheduler)

    scheduler_type = deepcopy(spec)
    scheduler_type["packet_a"]["estimands"][8]["estimand_type"] = "paired_difference"
    mutations.append(scheduler_type)

    routing = deepcopy(spec)
    routing["packet_a"]["estimands"][9]["contrast_spec"]["left_arm_id"] = "MATCHED_HYBRIDS"
    mutations.append(routing)

    for candidate in mutations:
        with pytest.raises(SpecificationValidationError):
            validate_spec(
                with_recomputed_digest(candidate),
                require_golden_digest=False,
                validate_narrative=False,
            )


def test_packet_a_validator_rejects_cross_field_contract_mutations() -> None:
    spec = _load(SPEC_PATH)
    mutations: list[dict[str, Any]] = []

    base_cell = deepcopy(spec)
    base_cell["packet_a"]["confirmatory_design"]["base_cell_count"] = 95
    mutations.append(base_cell)

    allocation = deepcopy(spec)
    allocation["packet_a"]["power_simulation"]["final_allocation_rule"] = "final_N = 384"
    mutations.append(allocation)

    coverage = deepcopy(spec)
    coverage["packet_a"]["opportunity_contract"]["coverage_numerator_contract"][
        "excluded_statuses"
    ] = ["UNKNOWN"]
    mutations.append(coverage)

    efficacy = deepcopy(spec)
    efficacy["packet_a"]["opportunity_contract"]["efficacy_eligible_denominator_contract"][
        "MISSING_is_in_E_eff"
    ] = False
    mutations.append(efficacy)

    status_schema = deepcopy(spec)
    status_schema["packet_a"]["hard_safety_exposure_contract"]["status_schema"][
        "allowed_statuses"
    ] = ["EXPOSED", "NOT_APPLICABLE"]
    mutations.append(status_schema)

    safety_mapping = deepcopy(spec)
    safety_mapping["packet_a"]["hard_safety_exposure_contract"]["status_schema"][
        "safety_rate_mapping"
    ]["MISSING"]["s_h_denominator"] = "INCLUDE"
    mutations.append(safety_mapping)

    failure = deepcopy(spec)
    failure["packet_a"]["failure_and_replacement_contract"][
        "infrastructure_diagnosis_must_be_independent"
    ] = False
    mutations.append(failure)

    caos = deepcopy(spec)
    caos["packet_a"]["caos_contract"]["component_equivalence"][2]["purge_equivalence_required"] = (
        False
    )
    mutations.append(caos)

    for candidate in mutations:
        with pytest.raises(SpecificationValidationError):
            validate_spec(
                with_recomputed_digest(candidate),
                require_golden_digest=False,
                validate_narrative=False,
            )


def test_packet_a_code_owned_digest_rejects_every_semantic_leaf_after_rebound() -> None:
    spec = _load(SPEC_PATH)
    scalar_paths = _scalar_paths(spec)
    assert len(scalar_paths) > 300

    for path, original in scalar_paths:
        if path == ("packet_a", "content_binding", "specification_digest"):
            continue
        candidate = deepcopy(spec)
        _replace_path(candidate, path, _mutated_scalar(original))
        candidate = with_recomputed_digest(candidate)
        with pytest.raises(SpecificationValidationError):
            validate_spec(candidate, require_golden_digest=False, validate_narrative=False)


def test_packet_a_self_digest_is_not_authority_but_is_repaired_for_nonsemantic_reencoding() -> None:
    spec = _load(SPEC_PATH)
    spec["packet_a"]["content_binding"]["specification_digest"] = "0" * 64
    repaired = with_recomputed_digest(spec)
    validate_spec(repaired, require_golden_digest=False, validate_narrative=False)


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

    unsafe_mechanism = deepcopy(spec)
    unsafe_mechanism["packet_a"]["mutation_cells"][0]["included_mechanism"] = "rm -rf fixture"
    mutations.append(("unsafe mechanism text", unsafe_mechanism))

    unsafe_witness = deepcopy(spec)
    unsafe_witness["packet_a"]["trust_contract"]["witness_classes"][0] = "model asserted success"
    mutations.append(("unsafe witness provenance", unsafe_witness))

    na_exposure = deepcopy(spec)
    na_exposure["packet_a"]["hard_safety_exposure_contract"][
        "not_applicable_contributes_to_exposure"
    ] = True
    mutations.append(("NOT_APPLICABLE exposure credit", na_exposure))

    nested_unknown = deepcopy(spec)
    nested_unknown["packet_a"]["caos_contract"]["unreviewed_field"] = "unexpected"
    mutations.append(("nested unknown field", nested_unknown))

    nested_missing = deepcopy(spec)
    del nested_missing["packet_a"]["caos_contract"]["missing_outcome"]
    mutations.append(("nested missing field", nested_missing))

    path_trick = deepcopy(spec)
    path_trick["packet_a"]["content_binding"]["narrative_binding"]["path"] = "../outside.md"
    mutations.append(("narrative path escape", path_trick))

    for _label, candidate in mutations:
        candidate = with_recomputed_digest(candidate)
        with pytest.raises(SpecificationValidationError):
            validate_spec(candidate, require_golden_digest=False, validate_narrative=False)

    non_finite = deepcopy(spec)
    non_finite["packet_a"]["power_simulation"]["power_target"] = float("nan")
    with pytest.raises(SpecificationValidationError):
        validate_spec(non_finite, require_golden_digest=False, validate_narrative=False)


def test_packet_a_m1_rejects_forged_issuer_witness_and_receipt_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _load(SPEC_PATH)
    mutations: list[dict[str, Any]] = []

    forged_issuer = deepcopy(spec)
    forged_issuer["packet_a"]["m1_contract"]["issuer_classes"].append("CLIENT")
    mutations.append(forged_issuer)

    forged_witness = deepcopy(spec)
    forged_witness["packet_a"]["m1_contract"]["transition_rules"][5]["witnesses"] = [
        "untrusted_observation"
    ]
    mutations.append(forged_witness)

    missing_receipt_binding = deepcopy(spec)
    missing_receipt_binding["packet_a"]["m1_contract"]["outcome_receipt_required_fields"].remove(
        "predecessor_action_exact_action_envelope_identifier"
    )
    mutations.append(missing_receipt_binding)

    weakened_purge = deepcopy(spec)
    weakened_purge["packet_a"]["m1_contract"]["invalidation_deletion_purge"][
        "purge_surfaces"
    ].remove("external_copy")
    mutations.append(weakened_purge)

    widened_user_evidence = deepcopy(spec)
    widened_user_evidence["packet_a"]["m1_contract"]["evidence_policy"]["relay_provider_paths"][
        "may_relabel"
    ] = True
    mutations.append(widened_user_evidence)

    weakened_acl = deepcopy(spec)
    weakened_acl["packet_a"]["m1_contract"]["acl_sensitivity_policy"]["sensitivity_classes"]["S3"][
        "permitted_content"
    ].append("raw_participant_content")
    mutations.append(weakened_acl)

    incomplete_topology = deepcopy(spec)
    incomplete_topology["packet_a"]["m1_contract"]["receipt_topology"]["reserve_receipt_schema"][
        "required_fields"
    ].remove("preserved_binding_digest")
    mutations.append(incomplete_topology)

    incomplete_secret_scan = deepcopy(spec)
    incomplete_secret_scan["packet_a"]["secret_refusal"]["non_reflection"][
        "required_surface_scans"
    ].remove("sqlite_wal")
    mutations.append(incomplete_secret_scan)

    for candidate in mutations:
        candidate = with_recomputed_digest(candidate)
        monkeypatch.setattr(
            validator_module,
            "EXPECTED_CANONICAL_SPECIFICATION_DIGEST",
            candidate["packet_a"]["content_binding"]["specification_digest"],
        )
        with pytest.raises(SpecificationValidationError):
            validate_spec(candidate, validate_narrative=False)


def test_packet_a_json_loader_enforces_bounded_input_limits(tmp_path: Path) -> None:
    oversized_path = tmp_path / "oversized.json"
    oversized_path.write_bytes(b"{" + b" " * validator_module.MAX_INPUT_BYTES + b"}")
    with pytest.raises(SpecificationValidationError, match="byte limit"):
        validator_module.load_json_document(oversized_path)

    deep_path = tmp_path / "deep.json"
    deep_path.write_text(
        "[" * (validator_module.MAX_JSON_DEPTH + 1)
        + "0"
        + "]" * (validator_module.MAX_JSON_DEPTH + 1),
        encoding="utf-8",
    )
    with pytest.raises(SpecificationValidationError, match="depth limit"):
        validator_module.load_json_document(deep_path)

    very_deep_path = tmp_path / "very-deep.json"
    very_deep_level = validator_module.MAX_JSON_DEPTH * 20
    very_deep_path.write_text(
        "[" * very_deep_level + "0" + "]" * very_deep_level,
        encoding="utf-8",
    )
    with pytest.raises(SpecificationValidationError):
        validator_module.load_json_document(very_deep_path)

    long_string_path = tmp_path / "long-string.json"
    long_string_path.write_text(
        '{"value":"' + "x" * (validator_module.MAX_JSON_STRING_CHARS + 1) + '"}',
        encoding="utf-8",
    )
    with pytest.raises(SpecificationValidationError, match="string limit"):
        validator_module.load_json_document(long_string_path)

    long_number_path = tmp_path / "long-number.json"
    long_number_path.write_text(
        '{"value":' + "9" * (validator_module.MAX_JSON_NUMBER_DIGITS + 1) + "}",
        encoding="utf-8",
    )
    with pytest.raises(SpecificationValidationError, match="number digit limit"):
        validator_module.load_json_document(long_number_path)

    many_nodes_path = tmp_path / "many-nodes.json"
    many_nodes_path.write_text(
        json.dumps({"values": [0] * (validator_module.MAX_JSON_NODES + 1)}),
        encoding="utf-8",
    )
    with pytest.raises(SpecificationValidationError, match="node limit"):
        validator_module.load_json_document(many_nodes_path)

    malformed_path = tmp_path / "malformed.json"
    malformed_path.write_text('{"value":', encoding="utf-8")
    with pytest.raises(SpecificationValidationError, match="invalid JSON"):
        validator_module.load_json_document(malformed_path)

    with pytest.raises(SpecificationValidationError, match="byte limit"):
        compute_narrative_semantic_digest(
            b"x" * (validator_module.MAX_INPUT_BYTES + 1), specification_digest="0" * 64
        )


def test_packet_a_digest_drift_is_rejected_separately_from_semantic_validation() -> None:
    spec = _load(SPEC_PATH)
    spec["packet_a"]["freeze_date"] = "2099-01-01"
    with pytest.raises(SpecificationValidationError):
        validate_spec(spec)


def test_packet_a_json_loader_rejects_duplicate_keys_and_nonfinite_numbers(tmp_path: Path) -> None:
    duplicate_path = tmp_path / "duplicate.json"
    duplicate_path.write_text(
        _load_raw(SPEC_PATH).replace(
            '"schema_version": 1,', '"schema_version": 1,\n  "schema_version": 1,', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(SpecificationValidationError, match="duplicate"):
        load_and_validate(duplicate_path)

    nonfinite_path = tmp_path / "nonfinite.json"
    nonfinite_path.write_text(
        _load_raw(SPEC_PATH).replace(
            '"provisional_confirmatory_n": 384', '"provisional_confirmatory_n": NaN', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(SpecificationValidationError, match="non-finite"):
        load_and_validate(nonfinite_path)


def test_packet_a_rejects_numeric_coercion_and_allows_only_key_reordering() -> None:
    spec = _load(SPEC_PATH)
    numeric_coercion = deepcopy(spec)
    numeric_coercion["packet_a"]["power_simulation"]["simulation_seed"] = True
    with pytest.raises(SpecificationValidationError):
        validate_spec(
            with_recomputed_digest(numeric_coercion),
            require_golden_digest=False,
            validate_narrative=False,
        )

    reordered = json.loads(json.dumps(spec, ensure_ascii=False, sort_keys=False))
    validate_spec(reordered, require_golden_digest=False, validate_narrative=False)


def test_packet_a_narrative_and_proposal_correction_are_content_bound() -> None:
    spec = _load(SPEC_PATH)
    digest = spec["packet_a"]["content_binding"]["specification_digest"]
    narrative = FREEZE_PATH.read_bytes()
    assert (
        compute_narrative_semantic_digest(narrative, specification_digest=digest)
        == spec["packet_a"]["content_binding"]["narrative_binding"]["semantic_sha256"]
    )
    assert (
        compute_narrative_semantic_digest(
            narrative.replace(b"non-displacing", b"non-displacing ", 1),
            specification_digest=digest,
        )
        != spec["packet_a"]["content_binding"]["narrative_binding"]["semantic_sha256"]
    )
    assert (
        compute_narrative_semantic_digest(
            narrative.replace(b"\n", b"\r\n"), specification_digest=digest
        )
        != spec["packet_a"]["content_binding"]["narrative_binding"]["semantic_sha256"]
    )

    proposal = PROPOSAL_PATH.read_text(encoding="utf-8")
    assert "`non_abstention = count(SUPPORTED response statuses) / E_w`" in proposal
    assert "must reproduce 384" not in proposal.lower()
    assert (
        spec["packet_a"]["content_binding"]["proposal_correction"]["source_sha256"]
        == (EXPECTED_PROVENANCE[0][2])
    )


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
