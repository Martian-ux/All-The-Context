from __future__ import annotations

import json
import os
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import bench.packet_a_power_reference as power_reference
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
    with pytest.raises(SpecificationValidationError, match="invalid JSON") as malformed_error:
        validator_module.load_json_document(malformed_path)
    assert str(malformed_error.value) == "invalid JSON document"

    with pytest.raises(SpecificationValidationError, match="byte limit"):
        compute_narrative_semantic_digest(
            b"x" * (validator_module.MAX_INPUT_BYTES + 1), specification_digest="0" * 64
        )


def test_packet_a_embedded_json_parser_rejects_content_free_malformed_fragments() -> None:
    expected_keys = {"specification_digest", "evidence_level", "execution_boundary"}

    duplicate = b'{"specification_digest":"x","specification_digest":"y"}'
    with pytest.raises(SpecificationValidationError, match="duplicate JSON key") as duplicate_error:
        validator_module._parse_bounded_json_bytes(duplicate)
    assert "specification_digest" not in str(duplicate_error.value)

    for nonfinite in (b"NaN", b"Infinity", b"-Infinity"):
        with pytest.raises(SpecificationValidationError, match="non-finite") as nonfinite_error:
            validator_module._parse_bounded_json_bytes(b'{"value":' + nonfinite + b"}")
        assert nonfinite.decode() not in str(nonfinite_error.value)

    very_deep = (
        b'{"specification_digest":"x","evidence_level":"L0",'
        b'"execution_boundary":{},"discarded":' + b"[" * 1100 + b"0" + b"]" * 1100 + b"}"
    )
    with pytest.raises(SpecificationValidationError) as deep_error:
        validator_module._parse_bounded_json_bytes(very_deep)
    assert "discarded" not in str(deep_error.value)

    unknown_secret_like = (
        b'{"specification_digest":"x","evidence_level":"L0",'
        b'"execution_boundary":{},"api_key":"PRIVATE_SENTINEL"}'
    )
    parsed = validator_module._parse_bounded_json_bytes(unknown_secret_like)
    with pytest.raises(SpecificationValidationError, match="object keys differ") as key_error:
        validator_module._require_keys(parsed, expected_keys, "narrative machine-readable binding")
    assert "api_key" not in str(key_error.value)
    assert "PRIVATE_SENTINEL" not in str(key_error.value)


def test_packet_a_public_digest_helpers_preflight_cycles_types_and_limits() -> None:
    deep: dict[str, Any] = {}
    current = deep
    for _ in range(1100):
        child: dict[str, Any] = {}
        current["discarded"] = child
        current = child

    cyclic: dict[str, Any] = {}
    cyclic["self"] = cyclic
    for invalid in (deep, cyclic):
        for helper in (
            validator_module.canonical_json_bytes,
            validator_module.compute_specification_digest,
            validator_module.with_recomputed_digest,
        ):
            with pytest.raises(SpecificationValidationError) as error:
                helper(invalid)
            assert "discarded" not in str(error.value)
            assert "self" not in str(error.value)

    oversized_collection = {"values": [0] * (validator_module.MAX_JSON_NODES + 1)}
    oversized_string = {"value": "x" * (validator_module.MAX_JSON_STRING_CHARS + 1)}
    oversized_number = {"value": 10**validator_module.MAX_JSON_NUMBER_DIGITS}
    for invalid in (oversized_collection, oversized_string, oversized_number):
        with pytest.raises(SpecificationValidationError):
            validator_module.canonical_json_bytes(invalid)

    assert validator_module.canonical_json_bytes(True) == b"true"
    assert validator_module.canonical_json_bytes(1) == b"1"
    assert validator_module.canonical_json_bytes(True) != validator_module.canonical_json_bytes(1)

    class DictSubclass(dict[str, Any]):
        pass

    class ListSubclass(list[int]):
        pass

    class StringSubclass(str):
        pass

    class IntSubclass(int):
        pass

    class FloatSubclass(float):
        pass

    class BytesSubclass(bytes):
        pass

    for subclass in (
        DictSubclass(),
        ListSubclass(),
        StringSubclass("safe"),
        IntSubclass(1),
        FloatSubclass(1.0),
    ):
        with pytest.raises(SpecificationValidationError) as error:
            validator_module.canonical_json_bytes(subclass)
        assert "Subclass" not in str(error.value)

    with pytest.raises(SpecificationValidationError):
        compute_narrative_semantic_digest(BytesSubclass(b""), specification_digest="0" * 64)
    with pytest.raises(SpecificationValidationError):
        compute_narrative_semantic_digest(b"", specification_digest=StringSubclass("0" * 64))


def test_packet_a_recursive_structure_contract_rejects_container_drift() -> None:
    spec = _load(SPEC_PATH)

    def assert_rejected(candidate: dict[str, Any]) -> None:
        with pytest.raises(SpecificationValidationError) as caught:
            validate_spec(
                with_recomputed_digest(candidate),
                require_golden_digest=False,
                validate_narrative=False,
            )
        assert str(caught.value) == "document structure contract differs from the frozen value"

    unknown_key_cases = [
        ("packet_a", "episode_contract"),
        ("packet_a", "budget_contract", "local_reference"),
        ("packet_a", "opportunity_contract", "workstreams", 0),
        ("packet_a", "power_simulation", "computation_method"),
    ]
    for path in unknown_key_cases:
        candidate = deepcopy(spec)
        target: Any = candidate
        for part in path:
            target = target[part]
        target["unexpected_nested_key"] = "untrusted"
        assert_rejected(candidate)

    missing_key_cases = [
        (("packet_a", "calibration_pilot"), "task_family_count"),
        (("packet_a", "episode_contract"), "minimum_sessions"),
        (("packet_a", "budget_contract"), "local_reference"),
        (("packet_a", "power_simulation", "interim_and_stopping_policy"), "early_stopping"),
    ]
    for path, key in missing_key_cases:
        candidate = deepcopy(spec)
        target: Any = candidate
        for part in path:
            target = target[part]
        target.pop(key)
        assert_rejected(candidate)

    wrong_shape_cases = [
        (("packet_a", "episode_contract"), []),
        (("packet_a", "power_simulation", "computation_method"), "wrong-shape"),
        (("experiments", 0), {"id": "E01"}),
        (("promotion_gates", 0), "wrong-shape"),
    ]
    for path, replacement in wrong_shape_cases:
        candidate = deepcopy(spec)
        _replace_path(candidate, path, replacement)
        assert_rejected(candidate)

    for list_path in (
        ("experiments",),
        ("promotion_gates",),
        ("packet_a", "task_families"),
        ("packet_a", "estimands"),
    ):
        candidate = deepcopy(spec)
        target: Any = candidate
        for part in list_path:
            target = target[part]
        target.append(deepcopy(target[-1]))
        assert_rejected(candidate)

    for list_path in (
        ("experiments",),
        ("promotion_gates",),
        ("packet_a", "task_families"),
        ("packet_a", "estimands"),
    ):
        candidate = deepcopy(spec)
        target: Any = candidate
        for part in list_path:
            target = target[part]
        target.reverse()
        assert_rejected(candidate)

    identity_drift = deepcopy(spec)
    identity_drift["experiments"][0]["id"] = "E02"
    assert_rejected(identity_drift)


def test_packet_a_public_boundaries_reject_aliases_and_invalid_utf8_content_free() -> None:
    shared: dict[str, Any] = {"value": "bounded"}
    aliased = {"left": shared, "right": shared}
    for helper in (
        validator_module.canonical_json_bytes,
        validator_module.compute_specification_digest,
        validator_module.with_recomputed_digest,
    ):
        with pytest.raises(SpecificationValidationError) as caught:
            helper(aliased)
        assert str(caught.value) == "document contains a shared container reference"
        assert caught.value.__cause__ is None

    candidate = _load(SPEC_PATH)
    shared_packet_object = candidate["packet_a"]["episode_contract"]
    candidate["packet_a"]["budget_contract"]["local_reference"] = shared_packet_object
    with pytest.raises(SpecificationValidationError) as caught:
        validate_spec(candidate, require_golden_digest=False, validate_narrative=False)
    assert str(caught.value) == "document contains a shared container reference"
    assert caught.value.__cause__ is None

    with pytest.raises(SpecificationValidationError) as caught:
        compute_narrative_semantic_digest(b"\xff", specification_digest="0" * 64)
    assert str(caught.value) == "narrative is not valid UTF-8"
    assert caught.value.__cause__ is None


def test_packet_a_narrative_binding_parser_is_unique_and_linear() -> None:
    document = FREEZE_PATH.read_text(encoding="utf-8")
    extracted = validator_module._extract_narrative_json_binding(document)
    parsed = json.loads(extracted)
    assert (
        parsed["specification_digest"]
        == _load(SPEC_PATH)["packet_a"]["content_binding"]["specification_digest"]
    )

    repeated = "\n".join("### Machine-readable binding\n```json\n{}\n```" for _ in range(400))
    with pytest.raises(SpecificationValidationError) as caught:
        validator_module._extract_narrative_json_binding(repeated)
    assert str(caught.value) == "narrative machine-readable binding heading is not unique"
    assert caught.value.__cause__ is None

    with pytest.raises(SpecificationValidationError) as caught:
        validator_module._parse_bounded_json_bytes(b'{"secret_sentinel":')
    assert str(caught.value) == "invalid JSON document"
    assert caught.value.__cause__ is None

    with pytest.raises(SpecificationValidationError) as caught:
        validator_module.canonical_json_bytes("\ud800")
    assert str(caught.value) == "document is not finite canonical JSON"
    assert caught.value.__cause__ is None

    with pytest.raises(SpecificationValidationError) as caught:
        validator_module.load_json_document(Path("missing_secret_sentinel.json"))
    assert str(caught.value) == "input read failed"
    assert caught.value.__cause__ is None


def test_packet_a_contract_source_digest_is_independent_and_bound() -> None:
    spec = _load(SPEC_PATH)
    expected = validator_module.EXPECTED_CONTRACT_SOURCE_SHA256
    assert validator_module._read_contract_source_digest() == expected
    assert (
        b"EXPECTED_CONTRACT_SOURCE_SHA256"
        not in (ROOT / "bench" / "packet_a_contract.py").read_bytes()
    )
    assert spec["packet_a"]["content_binding"]["contract_source_sha256"] == expected
    assert any(
        source["path"] == "bench/packet_a_contract.py" and source["sha256"] == expected
        for source in spec["packet_a"]["provenance"]["canonical_inputs"]
    )

    contract_source = (ROOT / "bench" / "packet_a_contract.py").read_bytes()
    tampered_source = contract_source.replace(
        b"EXPECTED_BASE_CELL_COUNT = 96", b"EXPECTED_BASE_CELL_COUNT = 97"
    )
    for digest in (
        validator_module.EXPECTED_CANONICAL_SPECIFICATION_DIGEST.encode(),
        validator_module.EXPECTED_STRUCTURE_DIGEST.encode(),
        validator_module.EXPECTED_NARRATIVE_SEMANTIC_DIGEST.encode(),
    ):
        tampered_source = tampered_source.replace(digest, b"0" * 64, 1)
    assert validator_module._compute_contract_source_digest(tampered_source) != expected

    reference_expected = validator_module.EXPECTED_POWER_REFERENCE_SOURCE_SHA256
    reference_source = (ROOT / "bench" / "packet_a_power_reference.py").read_bytes()
    assert validator_module.hashlib.sha256(reference_source).hexdigest() == reference_expected
    assert spec["packet_a"]["content_binding"]["power_reference_source_sha256"] == (
        reference_expected
    )
    assert any(
        source["path"] == "bench/packet_a_power_reference.py"
        and source["sha256"] == reference_expected
        for source in spec["packet_a"]["provenance"]["canonical_inputs"]
    )
    assert (
        validator_module.hashlib.sha256(
            reference_source.replace(b"POWER_REFERENCE_VERSION", b"POWER_REFERENCE_VERSIOX", 1)
        ).hexdigest()
        != reference_expected
    )


def test_packet_a_file_entrypoints_reject_virtual_and_subclassed_paths() -> None:
    concrete_path_type = type(Path())

    class HostilePathSubclass(concrete_path_type):
        def __fspath__(self) -> str:
            raise AssertionError("HOSTILE_FSPATH_SENTINEL")

        def stat(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("HOSTILE_STAT_SENTINEL")

    class HostilePathLike:
        def __fspath__(self) -> str:
            raise AssertionError("HOSTILE_FSPATH_SENTINEL")

    hostile_inputs: list[Any] = [
        HostilePathSubclass(str(SPEC_PATH)),
        HostilePathLike(),
        str(SPEC_PATH),
    ]
    spec = _load(SPEC_PATH)
    entrypoints = (
        validator_module._read_bounded_file,
        validator_module.load_json_document,
        validator_module.load_and_validate,
    )
    for hostile in hostile_inputs:
        for entrypoint in entrypoints:
            with pytest.raises(SpecificationValidationError) as caught:
                entrypoint(hostile)
            assert str(caught.value) == "input path must be a concrete pathlib path"
            assert caught.value.__cause__ is None
            assert caught.value.__context__ is None
            assert "HOSTILE" not in str(caught.value)

        with pytest.raises(SpecificationValidationError) as caught:
            validate_spec(spec, root=hostile, validate_narrative=False)
        assert str(caught.value) == "validation root must be a concrete pathlib path"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

        with pytest.raises(SpecificationValidationError) as caught:
            validator_module._validate_narrative(spec["packet_a"], hostile)
        assert str(caught.value) == "validation root must be a concrete pathlib path"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None


def test_packet_a_read_limits_and_filesystem_identity_are_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload_path = tmp_path / "payload.bin"
    payload_path.write_bytes(b"bounded")
    invalid_limits: tuple[Any, ...] = (
        True,
        False,
        0,
        -1,
        validator_module.MAX_INPUT_BYTES + 1,
        10**10000,
        1.0,
        object(),
    )
    for maximum_bytes in invalid_limits:
        with pytest.raises(SpecificationValidationError) as caught:
            validator_module._read_bounded_file(payload_path, maximum_bytes=maximum_bytes)
        assert str(caught.value) == "input size limit is invalid"
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None

    with pytest.raises(SpecificationValidationError, match="input exceeds byte limit"):
        validator_module._read_bounded_file(payload_path, maximum_bytes=1)

    hardlink_path = tmp_path / "hardlink.bin"
    os.link(payload_path, hardlink_path)
    with pytest.raises(SpecificationValidationError) as hardlink_error:
        validator_module._read_bounded_file(hardlink_path)
    assert str(hardlink_error.value) == "input file identity is not unique"
    hardlink_path.unlink()

    symlink_path = tmp_path / "symlink.bin"
    try:
        symlink_path.symlink_to(payload_path)
    except OSError:
        pass
    else:
        with pytest.raises(SpecificationValidationError) as symlink_error:
            validator_module._read_bounded_file(symlink_path)
        assert str(symlink_error.value) == "input path uses a link or reparse point"

    linked_parent = tmp_path / "linked-parent"
    try:
        linked_parent.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pass
    else:
        with pytest.raises(SpecificationValidationError) as parent_link_error:
            validator_module._read_bounded_file(linked_parent / payload_path.name)
        assert str(parent_link_error.value) == "input path uses a link or reparse point"

    directory_path = tmp_path / "directory-input"
    directory_path.mkdir()
    with pytest.raises(SpecificationValidationError) as special_file_error:
        validator_module._read_bounded_file(directory_path)
    assert str(special_file_error.value) == "input is not a regular file"

    original_path_chain = validator_module._path_chain
    path_chain_calls = 0
    swap_call = 2

    def swapped_path_chain(path: Path) -> Any:
        nonlocal path_chain_calls
        result = original_path_chain(path)
        path_chain_calls += 1
        if path_chain_calls == swap_call:
            absolute, chain, metadata = result
            changed_chain = list(chain)
            changed_chain[-1] = (0,) * len(changed_chain[-1])
            return absolute, tuple(changed_chain), metadata
        return result

    monkeypatch.setattr(validator_module, "_path_chain", swapped_path_chain)
    with pytest.raises(SpecificationValidationError) as swap_error:
        validator_module._read_bounded_file(payload_path)
    assert str(swap_error.value) == "input changed during read"
    assert swap_error.value.__cause__ is None
    assert swap_error.value.__context__ is None

    path_chain_calls = 0
    swap_call = 3
    with pytest.raises(SpecificationValidationError) as after_read_swap_error:
        validator_module._read_bounded_file(payload_path)
    assert str(after_read_swap_error.value) == "input changed during read"
    assert after_read_swap_error.value.__cause__ is None
    assert after_read_swap_error.value.__context__ is None


def _traceback_local_reprs(error: BaseException) -> list[str]:
    values: list[str] = []
    seen: set[int] = set()
    traceback = error.__traceback__
    while traceback is not None:
        for name, value in traceback.tb_frame.f_locals.items():
            values.append(name)
            pending = [value]
            while pending:
                current = pending.pop()
                current_id = id(current)
                if current_id in seen:
                    continue
                seen.add(current_id)
                try:
                    values.append(repr(current))
                except Exception:
                    values.append("<unrepresentable>")
                if isinstance(current, dict):
                    pending.extend(current.keys())
                    pending.extend(current.values())
                elif isinstance(current, (list, tuple, set, frozenset)):
                    pending.extend(current)
        traceback = traceback.tb_next
    return values


def _capture_malformed_traceback() -> BaseException:
    payload = b'{"TRACEBACK_JSON_CANARY":'
    try:
        validator_module._parse_bounded_json_bytes(payload)
    except BaseException as error:
        del payload
        return error
    raise AssertionError("malformed input unexpectedly parsed")


def _capture_duplicate_traceback() -> BaseException:
    payload = b'{"DUPLICATE_KEY_CANARY":1,"DUPLICATE_KEY_CANARY":2}'
    try:
        validator_module._parse_bounded_json_bytes(payload)
    except BaseException as error:
        del payload
        return error
    raise AssertionError("duplicate input unexpectedly parsed")


def _capture_oversize_traceback() -> BaseException:
    payload = b"OVERSIZE_BYTES_CANARY" + b"x" * validator_module.MAX_INPUT_BYTES
    try:
        validator_module._parse_bounded_json_bytes(payload)
    except BaseException as error:
        del payload
        return error
    raise AssertionError("oversize input unexpectedly parsed")


def _capture_missing_path_traceback() -> BaseException:
    path = Path("MISSING_PATH_CANARY.json")
    try:
        validator_module.load_json_document(path)
    except BaseException as error:
        del path
        return error
    raise AssertionError("missing path unexpectedly loaded")


def _capture_invalid_utf8_traceback() -> BaseException:
    payload = b"INVALID_UTF8_CANARY\xff"
    try:
        compute_narrative_semantic_digest(payload, specification_digest="0" * 64)
    except BaseException as error:
        del payload
        return error
    raise AssertionError("invalid UTF-8 unexpectedly parsed")


def _capture_candidate_traceback() -> BaseException:
    candidate = _load(SPEC_PATH)
    candidate["packet_a"]["power_simulation"]["derived_n"] = "TRACEBACK_CANDIDATE_CANARY"
    try:
        validate_spec(candidate, require_golden_digest=False, validate_narrative=False)
    except BaseException as error:
        del candidate
        return error
    raise AssertionError("invalid candidate unexpectedly validated")


def _capture_missing_narrative_traceback() -> BaseException:
    candidate = _load(SPEC_PATH)
    root = Path("NARRATIVE_ROOT_CANARY")
    try:
        validator_module._validate_narrative(candidate["packet_a"], root)
    except BaseException as error:
        del candidate
        del root
        return error
    raise AssertionError("missing narrative unexpectedly validated")


def test_packet_a_public_failures_have_no_raw_traceback_locals_or_paths() -> None:
    failures = (
        _capture_malformed_traceback(),
        _capture_duplicate_traceback(),
        _capture_oversize_traceback(),
        _capture_missing_path_traceback(),
        _capture_invalid_utf8_traceback(),
        _capture_candidate_traceback(),
        _capture_missing_narrative_traceback(),
    )
    canaries = (
        "TRACEBACK_JSON_CANARY",
        "DUPLICATE_KEY_CANARY",
        "OVERSIZE_BYTES_CANARY",
        "MISSING_PATH_CANARY",
        "INVALID_UTF8_CANARY",
        "TRACEBACK_CANDIDATE_CANARY",
        "NARRATIVE_ROOT_CANARY",
    )
    for error, canary in zip(failures, canaries, strict=True):
        assert canary not in str(error)
        assert error.__cause__ is None
        assert error.__context__ is None
        assert all(canary not in text for text in _traceback_local_reprs(error))


def test_packet_a_file_and_encoding_failures_have_no_exception_graph_leak() -> None:
    malformed = b'{"JSON_DOC_SENTINEL":'
    with pytest.raises(SpecificationValidationError) as caught:
        validator_module._parse_bounded_json_bytes(malformed)
    assert str(caught.value) == "invalid JSON document"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "JSON_DOC_SENTINEL" not in str(caught.value)

    with pytest.raises(SpecificationValidationError) as caught:
        validator_module.canonical_json_bytes("\ud800")
    assert str(caught.value) == "document is not finite canonical JSON"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None

    with pytest.raises(SpecificationValidationError) as caught:
        validator_module.load_json_document(Path("missing_FILE_NOT_FOUND_SENTINEL.json"))
    assert str(caught.value) == "input read failed"
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "FILE_NOT_FOUND_SENTINEL" not in str(caught.value)


def test_packet_a_power_method_and_closed_stopping_policy_are_frozen() -> None:
    spec = _load(SPEC_PATH)
    power = spec["packet_a"]["power_simulation"]
    assert power["computation_method"]["algorithm"] == ("deterministic_counter_stream_monte_carlo")
    assert power["computation_method"]["candidate_n_grid"] == (
        "complete_balanced_multiples_of_96_from_384_through_9600_inclusive"
    )
    methods = power["computation_method"]["primary_contrast_methods"]
    caos_method = methods["PRIMARY_CONTINUITY_CAOS_DIFFERENCE"]
    utility_method = methods["PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY"]
    assert caos_method["outcome_type"] == "paired_binary"
    assert utility_method["outcome_type"] == "paired_bounded_five_level_utility"
    assert utility_method["test"] != caos_method["test"]
    assert sum(sum(row) for row in utility_method["joint_distribution"]) == 1.0
    assert utility_method["control_mean"] == 0.83
    assert utility_method["alternative_mean"] == 0.895
    assert utility_method["target_relative_effect"] == 0.05
    assert power["interim_and_stopping_policy"] == {
        "interim_looks": "none",
        "interim_peeking": "prohibited",
        "replicate_count_fixed_before_run": 100000,
        "candidate_grid_fixed_before_run": True,
        "early_stopping": "forbidden",
        "futility_stopping": "forbidden",
        "harm_stopping": "forbidden",
        "optional_stopping": "forbidden",
        "stopping_exceptions": "none",
        "adaptive_sampling": False,
        "peeking_or_reallocation": "forbidden",
        "stop_only_after": "all_candidate_n_values_and_all_100000_replicates_are_evaluated",
        "result_status": "specification_only_no_power_result_claim",
    }

    candidate = deepcopy(spec)
    candidate["packet_a"]["power_simulation"]["interim_and_stopping_policy"]["early_stopping"] = (
        "allowed"
    )
    with pytest.raises(SpecificationValidationError):
        validate_spec(
            with_recomputed_digest(candidate),
            require_golden_digest=False,
            validate_narrative=False,
        )

    for contrast_id, field, replacement in (
        (
            "PRIMARY_CONTINUITY_CAOS_DIFFERENCE",
            "test",
            "studentized_paired_permutation_test_with_10000_counter_stream_sign_flips",
        ),
        (
            "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY",
            "outcome_type",
            "paired_binary",
        ),
    ):
        candidate = deepcopy(spec)
        candidate["packet_a"]["power_simulation"]["computation_method"]["primary_contrast_methods"][
            contrast_id
        ][field] = replacement
        with pytest.raises(SpecificationValidationError):
            validate_spec(
                with_recomputed_digest(candidate),
                require_golden_digest=False,
                validate_narrative=False,
            )


def test_packet_a_power_reference_has_golden_vectors_axes_and_exact_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = _load(SPEC_PATH)
    method = spec["packet_a"]["power_simulation"]["computation_method"]["reference_method_contract"]
    assert method == validator_module.EXPECTED_POWER_COMPUTATION_METHOD
    for vector in method["golden_counter_vectors"]:
        arguments = (
            vector["simulation_seed"],
            vector["replicate_index"],
            vector["candidate_n"],
            vector["resample_index"],
            vector["episode_index"],
            vector["draw_kind"],
        )
        assert power_reference.counter_digest(*arguments) == vector["digest"]
        assert power_reference.counter_uniform(*arguments) == vector["uniform"]

    utility = method["primary_contrast_methods"]["PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY"]
    assert utility["matrix_axes"] == {
        "rows": "control_utility",
        "columns": "alternative_utility",
        "order": [0.0, 0.25, 0.5, 0.75, 1.0],
    }
    matrix = utility_matrix = utility["joint_distribution"]
    levels = utility["matrix_axes"]["order"]
    control_mean = sum(sum(row) * levels[index] for index, row in enumerate(matrix))
    alternative_mean = sum(
        sum(matrix[row][column] for row in range(len(matrix))) * levels[column]
        for column in range(len(levels))
    )
    assert control_mean == 0.83
    assert alternative_mean == 0.895
    assert tuple(tuple(row) for row in utility_matrix) == power_reference.UTILITY_JOINT_MATRIX

    assert power_reference.episode_cell_index(0, 384) == 0
    assert power_reference.episode_cell_index(95, 384) == 95
    assert power_reference.episode_cell_index(96, 384) == 0
    assert power_reference.cell_coordinates(95) == (5, 3, 3)
    assert power_reference.candidate_n_grid() == tuple(range(384, 9601, 96))
    assert power_reference.holm_adjusted_p_values((0.01, 0.01)) == (0.02, 0.02)

    evaluated_candidates: list[int] = []

    def fake_candidate_power(seed: int, candidate_n: int) -> tuple[float, float, float]:
        del seed
        evaluated_candidates.append(candidate_n)
        return (0.9, 0.9, 0.9) if candidate_n == 384 else (0.0, 0.0, 0.0)

    monkeypatch.setattr(power_reference, "estimate_candidate_power", fake_candidate_power)
    assert power_reference.select_derived_n(20260829) == 384
    assert evaluated_candidates == list(power_reference.candidate_n_grid())

    caos_observations = (
        power_reference.PairObservation(0, power_reference.VALID_STATUS, 0, 1),
        power_reference.PairObservation(0, power_reference.VALID_STATUS, 1, 0),
        power_reference.PairObservation(1, power_reference.VALID_STATUS, 0, 0),
    )
    assert power_reference.exact_paired_binary_pvalue(caos_observations) == 0.75
    utility_observations = (
        power_reference.PairObservation(0, power_reference.VALID_STATUS, 0.5, 0.75),
        power_reference.PairObservation(1, power_reference.LOST_STATUS, 0.75, 1.0),
    )
    assert power_reference.relative_utility_effect(utility_observations) == 0.5
    missing_observations = (
        power_reference.PairObservation(0, power_reference.MISSING_STATUS, 0.5, 0.75),
    )
    assert power_reference.relative_utility_effect(missing_observations) is None

    transposed = deepcopy(spec)
    utility_contract = transposed["packet_a"]["power_simulation"]["computation_method"][
        "reference_method_contract"
    ]["primary_contrast_methods"]["PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY"]
    transposed_matrix = utility_contract["matrix_axes"]
    assert transposed_matrix["rows"] == "control_utility"
    joint_distribution = utility_contract
    transposed_distribution = [
        [joint_distribution["joint_distribution"][row][column] for row in range(5)]
        for column in range(5)
    ]
    transposed["packet_a"]["power_simulation"]["computation_method"]["reference_method_contract"][
        "primary_contrast_methods"
    ]["PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY"]["joint_distribution"] = transposed_distribution
    with pytest.raises(SpecificationValidationError):
        validate_spec(
            with_recomputed_digest(transposed),
            require_golden_digest=False,
            validate_narrative=False,
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


def test_packet_a_rebound_narrative_digest_cannot_authorize_semantic_drift() -> None:
    spec = _load(SPEC_PATH)
    candidate = deepcopy(spec)
    candidate["packet_a"]["content_binding"]["narrative_binding"]["semantic_sha256"] = "0" * 64
    with pytest.raises(SpecificationValidationError):
        validate_spec(
            with_recomputed_digest(candidate),
            require_golden_digest=False,
            validate_narrative=False,
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
