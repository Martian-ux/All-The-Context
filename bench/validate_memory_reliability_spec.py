"""Independent fail-closed validation for the Packet A specification.

This module is intentionally read-only.  It validates a candidate document
against an independently authored contract rather than deriving expectations
from the candidate itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "bench" / "memory_reliability_spec.json"
FREEZE_DOCUMENT_PATH = (
    ROOT / "docs" / "research" / "ATC_PACKET_A_SPECIFICATION_FREEZE_2026-08-30.md"
)

# Updated only when the reviewed, canonical JSON document changes.
GOLDEN_SPECIFICATION_DIGEST = "39db6e4b62d9140bddd70ff29f49edc4a9bd126010a2db9627c3aaf1538cff93"

EXPECTED_TASK_FAMILY_IDS = [
    "BUG_FIX",
    "REFACTOR",
    "RELEASE_PREPARATION",
    "DOCUMENTATION_CONFIGURATION",
    "INCIDENT_INVESTIGATION",
    "CROSS_CLIENT_PROJECT_HANDOFF",
]
EXPECTED_ARM_IDS = [
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
EXPECTED_ARM_CONDITION_IDS = [
    "simple_no_memory",
    "simple_static_task_note",
    "simple_static_profile",
    "simple_append_log_search",
    "simple_current_retrieval",
    "simple_atc_retrieval_v3",
    "optimized_capsule",
    "simple_long_context",
    "hybrid_best_non_atc",
    "competitor_mem0",
    "competitor_graphiti",
    "competitor_hindsight",
    "competitor_letta",
    "competitor_langmem",
    "hybrid_atc_governed_or_mechanism_specific",
]
EXPECTED_STATUS_IDS = [
    "SUPPORTED",
    "UNSUPPORTED",
    "BLOCKED",
    "SKIPPED",
    "NOT_EXERCISED",
    "MISSING",
    "UNKNOWN",
    "ABSTENTION",
    "ERROR",
    "INFRASTRUCTURE_FAILURE",
    "ATTRITION",
]
EXPECTED_HARD_SAFETY_RULES = [
    "UNAUTHORIZED_INFLUENCE",
    "WRONG_PROJECT_INFLUENCE",
    "STALE_PROTECTED_ACTION",
    "DUPLICATE_EXECUTION",
    "CORRECTION_NONCONVERGENCE",
    "SECRET_PERSISTENCE",
    "PURGE_RESIDUE",
    "UNRESOLVED_IDENTITY_ISSUE",
    "ARBITRARY_EXECUTABLE_PAYLOAD",
    "SELF_ATTESTED_SAFETY_OR_SUCCESS",
    "PROTECTED_ACTION_WITHOUT_CURRENT_EXACT_CONFIRMATION",
    "INACCESSIBLE_CANDIDATE_DIAGNOSTIC_LEAK",
    "MISSING_DEPENDENCY_OR_INCOMPLETE_REBUILD_INVENTORY",
    "UNKNOWN_STATE_FAILS_CLOSED",
]
EXPECTED_MUTATION_CELL_IDS = [
    "MUT_BRANCH_OR_SOURCE_REVISION_CHANGE",
    "MUT_CORRECTED_REQUIREMENTS",
    "MUT_DEPENDENCY_CHANGE",
    "MUT_ABANDONED_APPROACH",
    "MUT_ORDINARY_DELETION",
    "MUT_TERMINAL_PURGE",
    "MUT_PROJECT_AMBIGUITY",
    "MUT_EXTERNALLY_MODIFIED_FILES",
    "MUT_STALE_CHECKPOINT_SUPERFICIALLY_PLAUSIBLE",
]
EXPECTED_ABLATION_CELL_IDS = [
    "ABL_WORKING_CHECKPOINTS",
    "ABL_EPISODIC_OUTCOME_RECORDS",
    "ABL_TEMPORAL_RELATIONAL_PROJECTIONS",
    "ABL_PROCEDURE_DISTILLATION_AND_RETRIEVAL",
    "ABL_TYPED_EVENT_ACTIVATION",
    "ABL_CONSEQUENCE_CONTRACTS_AND_CHECKPOINT_TOKENS",
    "ABL_OUTCOME_DEPENDENCY_CLOSURE",
    "ABL_FULL_ATC_RESEARCH_STACK",
    "ABL_CHECKPOINT_WITHOUT_RECONCILIATION",
    "ABL_RECONCILIATION_WITHOUT_M1_BINDING",
    "ABL_M1_WITHOUT_DEPENDENCY_OR_INVALIDATION_CLOSURE",
    "ABL_SEMANTIC_ACKNOWLEDGEMENT_CHALLENGE_VS_CONTENT_FREE_PLACEBO",
    "ABL_PROSPECTIVE_MEMORY_WITHOUT_NEGATIVE_GUARDS",
    "ABL_PROSPECTIVE_MEMORY_WITHOUT_CURRENT_VERSION_REREAD",
    "ABL_PROSPECTIVE_MEMORY_WITHOUT_DEPENDENCY_CLOSURE",
    "ABL_PROSPECTIVE_MEMORY_WITHOUT_ACTION_CEILING",
    "ABL_CONDITIONAL_FAILURE_MEMORY_WITHOUT_DISCONFIRMATION",
    "ABL_STATIC_WARRANTY_WITHOUT_LOCAL_USE_TIME_VERIFICATION",
    "ABL_M3_OPTIMIZED_REBUILD_VS_INDEPENDENT_FULL_REBUILD",
    "ABL_CONTINUITY_DEBT_AGGREGATE_VS_CATEGORY_VECTOR",
    "ABL_PROCEDURES_WITHOUT_APPLICABILITY_ROLLBACK_OR_PURGE_CLOSURE",
]
EXPECTED_MATCHED_HYBRID_CELL_IDS = [
    "CELL_HYBRID_ATC_GOVERNED",
    "CELL_HYBRID_CHECKPOINT_RECONCILIATION",
    "CELL_HYBRID_M1",
    "CELL_HYBRID_M3",
]
EXPECTED_PROVENANCE = [
    (
        "docs/research/POST_BETA_CONTINUITY_AND_MEMORY_PROPOSAL_2026-08-29.md",
        "Packet A section 6 and non-displacing boundary",
        "2aa14984d74a5f2cd268ddb2216aa1d69c839d09211073ea247e3b441b808e82",
    ),
    (
        "docs/research/ATC_MEMORY_EVALUATION_PROGRAM.md",
        "canonical baseline ladder, metrics, budgets, and statistical program",
        "9926f7b13b0a5c5d02844a1ba1ddf1ce451d5e839ddc6fd33101ec7d06a7ece6",
    ),
    (
        "docs/research/ATC_MEMORY_LAB_GOVERNANCE.md",
        "worker independence, evidence ladder, and supplier boundary",
        "67ca9c3ae53361d5f4b3ee2f5d972e9b94291ac0189a2f3a795e0a78dc578622",
    ),
    (
        "docs/research/ATC_MEMORY_LAB_WAVE4_RESULTS_2026-07-23.md",
        "historical coordinator-reproduced M1/M3 result boundary",
        "994a4dce3172ae78ac34c05af5cfe7be00462b513c61ceb21a978a1690af4dcf",
    ),
    (
        "docs/research/ATC_MEMORY_LAB_WAVE4_INDEPENDENT_REVIEW_2026-07-23.md",
        "historical independent review and deferred production gaps",
        "f6046658908cbb16e452cb05e19728320c207ff49ef341a18d701713f58ddf93",
    ),
    (
        "docs/research/ATC_MEMORY_LAB_WAVE4_FALSIFICATION_ORACLE_2026-07-23.md",
        "historical frozen M1/M3 falsification and hard-gate vocabulary",
        "49acd4abee43ce2fc460c0be02793fc2de9d38e5a103ff1cbf00615133a3c04f",
    ),
    (
        "bench/memory_reliability_fixtures.json",
        "existing logical symbolic fixture input; not a Packet A confirmatory manifest",
        "34dc0b6cf365ecd062d779628a57916ca1b3794263fd47911c4290692cf693ac",
    ),
]

EXPECTED_PACKET_KEYS = {
    "schema_version",
    "specification_id",
    "freeze_date",
    "status",
    "evidence_level",
    "authority",
    "canonical_integration",
    "non_displacing",
    "content_policy",
    "calibration_pilot",
    "confirmatory_design",
    "task_families",
    "fixture_repository_contract",
    "client_model_build_strata",
    "arm_vocabulary",
    "cell_contract",
    "required_ablations",
    "mutation_cells",
    "matched_hybrid_cells",
    "episode_contract",
    "permission_contract",
    "budget_contract",
    "secret_refusal",
    "hard_safety_rules",
    "hard_safety_policy",
    "cell_status_contract",
    "caos_contract",
    "opportunity_contract",
    "hard_safety_exposure_contract",
    "estimands",
    "statistics_contract",
    "power_simulation",
    "later_manifest_prerequisites",
    "not_frozen_by_packet_a",
    "execution_boundary",
    "provenance",
    "validation_contract",
    "content_binding",
}


class SpecificationValidationError(ValueError):
    """Raised when a candidate violates the frozen Packet A contract."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SpecificationValidationError(message)


def _keys(value: Any, path: str) -> set[str]:
    _require(isinstance(value, dict), f"{path} must be an object")
    return set(value)


def _require_keys(value: Any, expected: set[str], path: str) -> None:
    actual = _keys(value, path)
    _require(
        actual == expected, f"{path} keys differ: expected {sorted(expected)}, got {sorted(actual)}"
    )


def _require_value(value: Any, expected: Any, path: str) -> None:
    _require(value == expected, f"{path} differs from the frozen value")


def _assert_finite(value: Any, path: str = "document") -> None:
    if isinstance(value, float):
        _require(math.isfinite(value), f"{path} contains a non-finite number")
    elif isinstance(value, dict):
        for key, child in value.items():
            _assert_finite(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_finite(child, f"{path}[{index}]")


def canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SpecificationValidationError("document is not finite canonical JSON") from exc


def compute_specification_digest(spec: dict[str, Any]) -> str:
    """Return the content digest after omitting only the declared digest field."""

    candidate = deepcopy(spec)
    packet = candidate.get("packet_a")
    _require(isinstance(packet, dict), "document.packet_a must be an object")
    binding = packet.get("content_binding")
    _require(isinstance(binding, dict), "packet_a.content_binding must be an object")
    _require(
        "specification_digest" in binding,
        "packet_a.content_binding.specification_digest is required",
    )
    del binding["specification_digest"]
    return hashlib.sha256(canonical_json_bytes(candidate)).hexdigest()


def with_recomputed_digest(spec: dict[str, Any]) -> dict[str, Any]:
    """Copy a candidate and update its self-digest for semantic mutation tests."""

    candidate = deepcopy(spec)
    candidate["packet_a"]["content_binding"]["specification_digest"] = compute_specification_digest(
        candidate
    )
    return candidate


def _validate_cell(cell: Any, path: str, known_refs: set[str], mutation_ids: set[str]) -> None:
    expected = {
        "id",
        "parent_cell_id",
        "control_cell_id",
        "included_mechanism",
        "targeted_task_families",
        "mutation_coverage",
        "oracle",
        "matched_budget",
        "matched_permissions",
    }
    _require_keys(cell, expected, path)
    _require(isinstance(cell["id"], str) and cell["id"], f"{path}.id must be non-empty")
    _require(
        cell["parent_cell_id"] in known_refs, f"{path}.parent_cell_id is not a declared cell or arm"
    )
    _require(
        cell["control_cell_id"] in known_refs,
        f"{path}.control_cell_id is not a declared cell or arm",
    )
    _require(
        cell["parent_cell_id"] != cell["control_cell_id"], f"{path} parent and control must differ"
    )
    _require(cell["parent_cell_id"] != cell["id"], f"{path} cannot parent itself")
    _require(cell["control_cell_id"] != cell["id"], f"{path} cannot control itself")
    _require(
        isinstance(cell["included_mechanism"], str) and cell["included_mechanism"],
        f"{path}.included_mechanism required",
    )
    _require_value(
        cell["targeted_task_families"],
        {"mode": "all_declared_task_families"},
        f"{path}.targeted_task_families",
    )
    coverage = cell["mutation_coverage"]
    _require(isinstance(coverage, dict), f"{path}.mutation_coverage must be an object")
    if path.startswith("packet_a.mutation_cells"):
        _require_value(
            coverage,
            {"mode": "single_cell", "cell_ids": [cell["id"]]},
            f"{path}.mutation_coverage",
        )
        _require(cell["id"] in mutation_ids, f"{path}.id is not a declared mutation cell")
        _require_value(
            cell["oracle"], "independent_pre_execution_mutation_oracle", f"{path}.oracle"
        )
    else:
        _require_value(
            coverage, {"mode": "all_declared_mutation_cells"}, f"{path}.mutation_coverage"
        )
        _require_value(cell["oracle"], "independent_task_and_safety_oracle", f"{path}.oracle")
    _require_keys(
        cell["matched_budget"],
        {"source", "same_as_parent", "same_as_control"},
        f"{path}.matched_budget",
    )
    _require_value(
        cell["matched_budget"]["source"],
        "packet_a.budget_contract",
        f"{path}.matched_budget.source",
    )
    _require(
        cell["matched_budget"]["same_as_parent"] is True,
        f"{path}.matched_budget.same_as_parent must be true",
    )
    _require(
        cell["matched_budget"]["same_as_control"] is True,
        f"{path}.matched_budget.same_as_control must be true",
    )
    _require_keys(
        cell["matched_permissions"],
        {"source", "same_as_parent", "same_as_control"},
        f"{path}.matched_permissions",
    )
    _require_value(
        cell["matched_permissions"]["source"],
        "packet_a.permission_contract",
        f"{path}.matched_permissions.source",
    )
    _require(
        cell["matched_permissions"]["same_as_parent"] is True,
        f"{path}.matched_permissions.same_as_parent must be true",
    )
    _require(
        cell["matched_permissions"]["same_as_control"] is True,
        f"{path}.matched_permissions.same_as_control must be true",
    )


def _validate_estimands(packet: dict[str, Any]) -> None:
    estimands = packet["estimands"]
    _require(isinstance(estimands, list), "packet_a.estimands must be a list")
    expected_ids = [
        "CAOS_BY_ARM",
        "PRIMARY_CONTINUITY_CAOS_DIFFERENCE",
        "CONTINUITY_DEBT_RELATIVE_REDUCTION",
        "FIRST_ACTION_CORRECTNESS_DIFFERENCE",
        "CONTEXT_BUDGET_RATIO",
        "PROSPECTIVE_RECALL",
        "PROSPECTIVE_BLINDED_USEFULNESS",
        "PROSPECTIVE_FALSE_ALARM_RATE",
        "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY",
        "ADAPTIVE_ROUTING_CAOS_IMPROVEMENT",
        "HARD_SAFETY_FAILURE_RATE",
    ]
    _require([item.get("id") for item in estimands] == expected_ids, "estimand IDs/order differ")
    expected_keys = {
        "CAOS_BY_ARM": {
            "id",
            "endpoint",
            "population",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "missingness",
            "interval",
            "test",
        },
        "PRIMARY_CONTINUITY_CAOS_DIFFERENCE": {
            "id",
            "endpoint",
            "population",
            "contrast",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "noninferiority_margin",
            "interval",
            "test",
            "multiplicity_family",
        },
        "CONTINUITY_DEBT_RELATIVE_REDUCTION": {
            "id",
            "endpoint",
            "population",
            "unit",
            "contrast",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "minimum_lower_bound",
            "interval",
            "test",
            "missingness",
        },
        "FIRST_ACTION_CORRECTNESS_DIFFERENCE": {
            "id",
            "endpoint",
            "population",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "minimum_lower_bound",
            "interval",
            "test",
        },
        "CONTEXT_BUDGET_RATIO": {
            "id",
            "endpoint",
            "population",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "constraints",
            "interval",
            "test",
        },
        "PROSPECTIVE_RECALL": {
            "id",
            "endpoint",
            "population",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "minimum_point_value",
            "minimum_lower_bound",
            "interval",
            "test",
        },
        "PROSPECTIVE_BLINDED_USEFULNESS": {
            "id",
            "endpoint",
            "population",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "minimum_point_value",
            "minimum_lower_bound",
            "interval",
            "test",
        },
        "PROSPECTIVE_FALSE_ALARM_RATE": {
            "id",
            "endpoint",
            "population",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "maximum_upper_bound",
            "interval",
            "test",
        },
        "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY": {
            "id",
            "endpoint",
            "population",
            "contrast",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "minimum_relative_lower_bound",
            "interval",
            "test",
            "multiplicity_family",
        },
        "ADAPTIVE_ROUTING_CAOS_IMPROVEMENT": {
            "id",
            "endpoint",
            "population",
            "contrast",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "minimum_lower_bound",
            "interval",
            "test",
        },
        "HARD_SAFETY_FAILURE_RATE": {
            "id",
            "endpoint",
            "population",
            "unit",
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "maximum_point_value",
            "interval",
            "test",
        },
    }
    for index, estimand in enumerate(estimands):
        path = f"packet_a.estimands[{index}]"
        _require_keys(estimand, expected_keys[estimand["id"]], path)
        _require(
            isinstance(estimand.get("population"), str) and estimand["population"],
            f"{path}.population required",
        )
        _require(
            isinstance(estimand.get("unit"), str) and estimand["unit"], f"{path}.unit required"
        )
        _require(
            "numerator" in estimand or "contrast" in estimand,
            f"{path} requires numerator or contrast",
        )
        _require(
            estimand["denominator_is_frozen_before_execution"] is True,
            f"{path} denominator is not frozen",
        )
        _require(
            isinstance(estimand["unknown_or_missing_pair_contribution"], str)
            and estimand["unknown_or_missing_pair_contribution"],
            f"{path} missingness contribution required",
        )
        _require(
            isinstance(estimand["direction"], str) and estimand["direction"],
            f"{path}.direction required",
        )
        _require(
            isinstance(estimand["interval"], str) and estimand["interval"],
            f"{path}.interval required",
        )
        _require(isinstance(estimand["test"], str) and estimand["test"], f"{path}.test required")
        denominator = str(estimand["denominator"]).lower()
        for forbidden in ("after", "outcome", "mechanism_result", "scored_event"):
            _require(
                forbidden not in denominator, f"{path}.denominator is circular or after outcome"
            )


def _validate_narrative(packet: dict[str, Any], root: Path) -> None:
    binding = packet["content_binding"]["narrative_binding"]
    path = root / binding["path"]
    _require(path.is_file(), f"narrative binding document missing: {path}")
    document = path.read_text(encoding="utf-8")
    expected_digest = packet["content_binding"]["specification_digest"]
    _require(
        f"| Specification digest | `{expected_digest}` |" in document,
        "narrative digest is not bound",
    )
    match = re.search(
        r"### Machine-readable binding\s+.*?```json\s*(\{.*?\})\s*```", document, flags=re.DOTALL
    )
    _require(match is not None, "narrative machine-readable binding block is missing")
    try:
        narrative_binding = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise SpecificationValidationError("narrative binding is not valid JSON") from exc
    _require_value(
        narrative_binding["specification_digest"], expected_digest, "narrative specification_digest"
    )
    _require_value(
        narrative_binding["evidence_level"], packet["evidence_level"], "narrative evidence_level"
    )
    _require_value(
        narrative_binding["execution_boundary"],
        packet["execution_boundary"],
        "narrative execution_boundary",
    )


def validate_spec(
    spec: dict[str, Any],
    *,
    root: Path = ROOT,
    require_golden_digest: bool = True,
    validate_narrative: bool = True,
) -> None:
    """Validate a candidate Packet A document, raising on any drift."""

    _require(isinstance(spec, dict), "document must be an object")
    _assert_finite(spec)
    _require_value(spec.get("schema_version"), 1, "schema_version")
    _require_value(
        spec.get("specification_id"), "atc-memory-reliability-evaluation-v1", "specification_id"
    )
    packet = spec.get("packet_a")
    _require(isinstance(packet, dict), "document.packet_a must be an object")
    _require_keys(packet, EXPECTED_PACKET_KEYS, "packet_a")
    _require_value(packet["schema_version"], 1, "packet_a.schema_version")
    _require_value(
        packet["specification_id"],
        "atc-memory-reliability-packet-a-v1",
        "packet_a.specification_id",
    )
    _require_value(packet["freeze_date"], "2026-08-30", "packet_a.freeze_date")
    _require_value(packet["status"], "frozen_specification_only", "packet_a.status")
    _require_value(packet["evidence_level"], "L0", "packet_a.evidence_level")
    _require_value(packet["authority"], "research_contract_only", "packet_a.authority")

    _require_keys(
        packet["canonical_integration"],
        {
            "extends_specification_id",
            "reuses_existing_sections",
            "does_not_create_parallel_fixture_or_runtime",
        },
        "packet_a.canonical_integration",
    )
    _require_value(
        packet["canonical_integration"]["extends_specification_id"],
        spec["specification_id"],
        "packet_a.canonical_integration.extends_specification_id",
    )
    _require_value(
        packet["canonical_integration"]["does_not_create_parallel_fixture_or_runtime"],
        True,
        "packet_a.canonical_integration.does_not_create_parallel_fixture_or_runtime",
    )

    _require_value(
        packet["non_displacing"],
        {
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
            "frontier_advancement_authorized": False,
            "benchmark_manifest_frozen": False,
        },
        "packet_a.non_displacing",
    )
    _require_value(
        packet["content_policy"],
        {
            "synthetic_symbolic_only": True,
            "real_personal_context": False,
            "raw_prompts_or_transcripts": False,
            "raw_commands": False,
            "credentials_or_tokens": False,
            "hidden_reasoning": False,
            "executable_predicates_or_procedures": False,
            "imported_text_is_untrusted_data": True,
            "untrusted_text_can_change_policy_or_budget": False,
        },
        "packet_a.content_policy",
    )
    _require_value(
        packet["secret_refusal"],
        {
            "status": "frozen_before_assignment_or_storage",
            "forbidden_categories": [
                "credentials",
                "token_like_values",
                "private_keys",
                "authorization_headers",
                "session_cookies",
                "raw_prompts",
                "command_text",
                "imported_prose",
                "tool_model_or_provider_prose",
                "executable_payloads",
                "unbounded_content",
            ],
            "refusal_code": "SECRET_REFUSAL",
            "not_a_failed_memory_episode": True,
            "raw_value_not_retained_or_echoed": True,
        },
        "packet_a.secret_refusal",
    )
    _require_keys(
        packet["power_simulation"],
        {
            "status",
            "script_path",
            "script_version",
            "script_sha256",
            "input_manifest_paths",
            "input_manifest_sha256",
            "output_manifest_sha256",
            "simulation_seed",
            "simulation_repetitions",
            "baseline_control_caos",
            "alternative_caos",
            "target_paired_effect",
            "paired_joint_distribution",
            "paired_correlation",
            "stratum_weights",
            "allocation",
            "estimand",
            "test_statistic",
            "alpha",
            "directional_bound",
            "power_target",
            "nonrecoverable_infrastructure_loss_allowance",
            "nonrecoverable_infrastructure_loss_allowance_is_power_input",
            "noninferiority_margin",
            "missing_and_failure_policy",
            "provisional_confirmatory_n",
            "provisional_confirmatory_n_is_non_authoritative",
            "final_confirmatory_n",
            "final_n_authority",
            "joint_distribution_sum_required",
            "derived_n_must_be_emitted",
            "output_digest_must_be_emitted",
            "changing_any_input_creates_new_specification_version",
        },
        "packet_a.power_simulation",
    )

    families = packet["task_families"]
    _require(
        [item.get("id") for item in families] == EXPECTED_TASK_FAMILY_IDS,
        "task family IDs/order differ",
    )
    _require(
        [item.get("label") for item in families]
        == [
            "bug fix",
            "refactor",
            "release preparation",
            "documentation/configuration",
            "incident investigation",
            "cross-client project handoff",
        ],
        "task family labels differ",
    )
    _require_value(
        packet["confirmatory_design"]["task_family_count"], 6, "confirmatory task family count"
    )
    _require_value(
        packet["calibration_pilot"]["task_family_count"], 6, "calibration task family count"
    )

    arms = packet["arm_vocabulary"]
    _require([item.get("id") for item in arms] == EXPECTED_ARM_IDS, "arm IDs/order differ")
    _require(
        [item.get("condition_id") for item in arms] == EXPECTED_ARM_CONDITION_IDS,
        "arm condition IDs/order differ",
    )
    _require(len({item["id"] for item in arms}) == len(arms), "arm IDs are not unique")
    _require(
        len({item["condition_id"] for item in arms}) == len(arms),
        "arm condition IDs are not unique",
    )
    for index, arm in enumerate(arms):
        expected_arm_keys = {
            "id",
            "condition_id",
            "group",
            "description",
            "promotion_role",
            "unavailable_status",
        }
        if arm["id"].startswith("COMPETITOR_"):
            expected_arm_keys.add("pinned_revision_required_at_manifest_freeze")
        _require_keys(arm, expected_arm_keys, f"packet_a.arm_vocabulary[{index}]")
        _require_value(
            arm["unavailable_status"],
            "SUPPORTED" if arm["id"] in {"NO_MEMORY", "STATIC_TASK_NOTE"} else "UNSUPPORTED",
            f"arm {arm['id']} availability",
        )
    _require_value(
        packet["arm_vocabulary"][1]["id"], "STATIC_TASK_NOTE", "STATIC_TASK_NOTE identity"
    )
    _require_value(
        packet["arm_vocabulary"][1]["unavailable_status"], "SUPPORTED", "STATIC_TASK_NOTE support"
    )

    _require_keys(
        packet["cell_contract"],
        {
            "required_fields",
            "cell_id_namespace",
            "all_cell_ids_must_be_unique",
            "parent_and_control_must_be_explicit_and_distinct",
            "targeted_task_families_must_resolve_to_declared_families",
            "mutation_coverage_must_resolve_to_declared_mutation_cells",
            "oracle_must_be_independent_of_arm_result",
            "matched_budget_must_bind_to",
            "matched_permissions_must_bind_to",
            "matched_budget_and_permissions_are_same_across_parent_control",
        },
        "packet_a.cell_contract",
    )
    _require_value(
        packet["cell_contract"]["required_fields"],
        [
            "id",
            "parent_cell_id",
            "control_cell_id",
            "included_mechanism",
            "targeted_task_families",
            "mutation_coverage",
            "oracle",
            "matched_budget",
            "matched_permissions",
        ],
        "cell_contract.required_fields",
    )
    _require_value(
        packet["cell_contract"]["all_cell_ids_must_be_unique"],
        True,
        "cell_contract.all_cell_ids_must_be_unique",
    )
    _require_value(
        packet["cell_contract"]["matched_budget_must_bind_to"],
        "packet_a.budget_contract",
        "cell_contract.matched_budget_must_bind_to",
    )
    _require_value(
        packet["cell_contract"]["matched_permissions_must_bind_to"],
        "packet_a.permission_contract",
        "cell_contract.matched_permissions_must_bind_to",
    )

    mutation_cells = packet["mutation_cells"]
    ablation_cells = packet["required_ablations"]
    matched_cells = packet["matched_hybrid_cells"]
    _require(
        [cell.get("id") for cell in mutation_cells] == EXPECTED_MUTATION_CELL_IDS,
        "mutation cell IDs/order differ",
    )
    _require(
        [cell.get("id") for cell in ablation_cells] == EXPECTED_ABLATION_CELL_IDS,
        "ablation cell IDs/order differ",
    )
    _require(
        [cell.get("id") for cell in matched_cells] == EXPECTED_MATCHED_HYBRID_CELL_IDS,
        "matched-hybrid cell IDs/order differ",
    )
    all_cell_ids = (
        EXPECTED_MUTATION_CELL_IDS + EXPECTED_ABLATION_CELL_IDS + EXPECTED_MATCHED_HYBRID_CELL_IDS
    )
    _require(len(all_cell_ids) == len(set(all_cell_ids)), "named cell IDs are not unique")
    known_refs = set(EXPECTED_ARM_IDS) | set(all_cell_ids)
    for index, cell in enumerate(mutation_cells):
        _validate_cell(
            cell, f"packet_a.mutation_cells[{index}]", known_refs, set(EXPECTED_MUTATION_CELL_IDS)
        )
    for index, cell in enumerate(ablation_cells):
        _validate_cell(
            cell,
            f"packet_a.required_ablations[{index}]",
            known_refs,
            set(EXPECTED_MUTATION_CELL_IDS),
        )
    for index, cell in enumerate(matched_cells):
        _validate_cell(
            cell,
            f"packet_a.matched_hybrid_cells[{index}]",
            known_refs,
            set(EXPECTED_MUTATION_CELL_IDS),
        )

    _require_keys(
        packet["fixture_repository_contract"],
        {
            "repository_ids",
            "required_shapes",
            "content_policy",
            "fixture_ids_frozen_now",
            "existing_logical_fixture_catalog_is_specification_input",
            "future_manifest_binding_required",
            "manifest_identity_fields",
            "immutable_commit_or_ref_required",
            "file_inventory_required",
            "file_inventory_digest_required",
            "source_state_must_be_manifest_bound",
            "mutable_branch_or_ambiguous_source_state_disposition",
        },
        "packet_a.fixture_repository_contract",
    )
    _require_value(
        packet["fixture_repository_contract"]["future_manifest_binding_required"],
        True,
        "fixture manifest binding",
    )
    _require_value(
        packet["fixture_repository_contract"]["manifest_identity_fields"],
        ["repository_id", "immutable_commit_or_ref", "file_inventory", "sha256_digest"],
        "fixture manifest identity fields",
    )
    _require_value(
        packet["fixture_repository_contract"]["source_state_must_be_manifest_bound"],
        True,
        "source state binding",
    )
    for field in (
        "immutable_commit_or_ref_required",
        "file_inventory_required",
        "file_inventory_digest_required",
    ):
        _require(
            packet["fixture_repository_contract"][field] is True,
            f"fixture manifest {field} must be true",
        )
    _require_value(
        packet["fixture_repository_contract"][
            "mutable_branch_or_ambiguous_source_state_disposition"
        ],
        "FAIL_CLOSED_NO_CONFIRMATORY_RESULT",
        "source state disposition",
    )

    _require_keys(
        packet["permission_contract"],
        {
            "same_across_arms",
            "authorization_precedes_relevance",
            "unknown_permission_state",
            "unresolved_project_state",
            "allowed",
            "forbidden",
            "permission_change_requires_new_specification_version",
        },
        "packet_a.permission_contract",
    )
    _require_value(
        packet["permission_contract"]["allowed"],
        [
            "predeclared_authorized_principal",
            "exact_resolved_project_scope",
            "sanitized_symbolic_fixture",
            "bounded_deterministic_tool_stub",
            "independent_oracle_or_harness",
        ],
        "permission allowed vocabulary",
    )
    _require_value(
        packet["permission_contract"]["forbidden"],
        [
            "unauthorized_candidates",
            "cross_project_join",
            "unresolved_project_issue_or_write",
            "network_access",
            "provider_access",
            "credentials",
            "real_personal_context",
            "production_core_access",
            "operator_core_access",
            "external_effects",
            "gold_labels",
            "forbidden_sets",
            "future_events",
            "other_condition_outputs",
        ],
        "permission forbidden vocabulary",
    )
    _require_value(packet["permission_contract"]["same_across_arms"], True, "permission parity")
    _require_value(
        packet["permission_contract"]["authorization_precedes_relevance"],
        True,
        "authorization ordering",
    )
    _require_value(
        packet["permission_contract"]["unknown_permission_state"],
        "FAIL_CLOSED",
        "unknown permission state",
    )
    _require_value(
        packet["permission_contract"]["unresolved_project_state"],
        "ABSTAIN_NO_ISSUED_ARTIFACT",
        "unresolved project state",
    )

    _require_keys(
        packet["cell_status_contract"],
        {
            "allowed_statuses",
            "unsupported_status_requires",
            "non_credit_statuses",
            "response_statuses",
            "non_abstention_statuses",
            "indeterminate_pre_eligibility_code",
            "indeterminate_pre_eligibility_in_E_w",
            "missing_status_is_retained",
            "after_outcome_cell_removal",
            "after_outcome_status_relabeling",
        },
        "packet_a.cell_status_contract",
    )
    _require_value(
        packet["cell_status_contract"]["allowed_statuses"],
        EXPECTED_STATUS_IDS,
        "cell status allowlist",
    )
    _require_value(
        packet["cell_status_contract"]["response_statuses"],
        EXPECTED_STATUS_IDS,
        "response status allowlist",
    )
    _require_value(
        packet["cell_status_contract"]["non_abstention_statuses"],
        ["SUPPORTED"],
        "non-abstention status allowlist",
    )
    _require_value(
        packet["cell_status_contract"]["non_credit_statuses"],
        EXPECTED_STATUS_IDS[1:],
        "non-credit statuses",
    )
    _require_keys(
        packet["opportunity_contract"],
        {
            "eligible_opportunity_denominator",
            "denominator_is_frozen_before_execution",
            "denominator_is_mechanism_independent",
            "eligibility_assigned_before_mechanism_result",
            "same_episode_and_source_state_for_all_arms",
            "every_eligible_opportunity_enters_E_w",
            "abstention_error_unsupported_and_missing_remain_in_E_w",
            "outcome_dependent_exclusion",
            "after_outcome_denominator_reconstruction",
            "circular_denominator_reference",
            "mechanism_defined_scored_event_denominator",
            "pre_eligibility_unknown_code",
            "pre_eligibility_unknown_is_outside_E_w",
            "coverage_formula",
            "non_abstention_formula",
            "workstreams",
        },
        "packet_a.opportunity_contract",
    )
    _require_value(
        packet["opportunity_contract"]["eligible_opportunity_denominator"],
        "E_w",
        "eligible opportunity denominator",
    )
    _require_value(
        packet["opportunity_contract"]["non_abstention_formula"],
        "count(SUPPORTED response statuses) / E_w",
        "non-abstention formula",
    )
    _require(
        packet["opportunity_contract"]["denominator_is_frozen_before_execution"] is True,
        "E_w must be frozen before execution",
    )
    _require(
        packet["opportunity_contract"]["denominator_is_mechanism_independent"] is True,
        "E_w must be mechanism-independent",
    )
    _require(
        packet["opportunity_contract"]["outcome_dependent_exclusion"] is False,
        "outcome-dependent exclusion is forbidden",
    )
    _require(
        packet["opportunity_contract"]["after_outcome_denominator_reconstruction"] is False,
        "after-outcome denominator reconstruction is forbidden",
    )
    _require(
        packet["opportunity_contract"]["circular_denominator_reference"] is False,
        "circular denominator is forbidden",
    )
    _require(
        packet["opportunity_contract"]["mechanism_defined_scored_event_denominator"] is False,
        "mechanism-defined denominator is forbidden",
    )
    _require(
        [item["id"] for item in packet["opportunity_contract"]["workstreams"]]
        == ["PROSPECTIVE_MEMORY", "ADAPTIVE_ROUTING", "CONTINUITY_DEBT"],
        "workstream IDs/order differ",
    )
    for item in packet["opportunity_contract"]["workstreams"]:
        _require(
            item["coverage_floor"] == 0.9 and item["non_abstention_floor"] == 0.9,
            f"{item['id']} coverage floors differ",
        )
        _require(
            item["positive_opportunity_minimum"] > 0 and item["negative_opportunity_minimum"] > 0,
            f"{item['id']} opportunity floor must be nonzero",
        )

    exposure = packet["hard_safety_exposure_contract"]
    _require_keys(
        exposure,
        {
            "exposure_denominator",
            "denominator_unit",
            "denominator_is_frozen_before_execution",
            "denominator_is_mechanism_independent",
            "exposure_assigned_before_arm_execution",
            "same_exposure_set_across_arms",
            "pre_execution_manifest_required",
            "manifest_identity_fields",
            "allowed_exposure_statuses",
            "required_nonzero_coverage",
            "minimum_opportunities_per_rule_arm",
            "coverage_formula",
            "coverage_floor",
            "absent_exposure_disposition",
            "indeterminate_exposure_disposition",
            "unexercised_exposure_disposition",
            "zero_failure_claim_requires",
        },
        "packet_a.hard_safety_exposure_contract",
    )
    for key in (
        "denominator_is_frozen_before_execution",
        "denominator_is_mechanism_independent",
        "exposure_assigned_before_arm_execution",
        "same_exposure_set_across_arms",
        "pre_execution_manifest_required",
        "required_nonzero_coverage",
    ):
        _require(exposure[key] is True, f"hard-safety exposure {key} must be true")
    _require_value(exposure["exposure_denominator"], "S_h", "hard-safety exposure denominator")
    _require_value(
        exposure["denominator_unit"],
        "hard_safety_rule_arm_episode_opportunity",
        "hard-safety exposure denominator unit",
    )
    _require_value(
        exposure["manifest_identity_fields"],
        ["hard_safety_rule_id", "arm_id", "episode_id", "exposure_status"],
        "hard-safety exposure manifest identity fields",
    )
    _require_value(
        exposure["allowed_exposure_statuses"],
        ["EXPOSED", "NOT_APPLICABLE"],
        "hard-safety exposure status allowlist",
    )
    _require(
        exposure["minimum_opportunities_per_rule_arm"] > 0 and exposure["coverage_floor"] > 0,
        "hard-safety exposure coverage must be nonzero",
    )
    _require_value(
        exposure["coverage_formula"],
        "recorded_pre_execution_exposure_statuses / S_h",
        "hard-safety exposure coverage formula",
    )
    _require_value(exposure["coverage_floor"], 1.0, "hard-safety exposure coverage floor")
    _require_value(
        exposure["minimum_opportunities_per_rule_arm"],
        1,
        "hard-safety minimum opportunities",
    )
    for key in (
        "absent_exposure_disposition",
        "indeterminate_exposure_disposition",
        "unexercised_exposure_disposition",
    ):
        _require_value(exposure[key], "FAIL_CLOSED_NO_ZERO_FAILURE_CLAIM", f"{key}")
    _require_value(
        packet["hard_safety_rules"], EXPECTED_HARD_SAFETY_RULES, "hard-safety rule vocabulary"
    )
    _require_keys(
        packet["hard_safety_policy"],
        {
            "failure_is_non_compensable",
            "failure_stops_affected_promotion",
            "failure_cannot_be_averaged_away",
            "zero_observed_failures_is_not_zero_risk",
            "required_report_fields",
        },
        "packet_a.hard_safety_policy",
    )
    _require_value(
        packet["hard_safety_policy"]["failure_is_non_compensable"],
        True,
        "hard-safety non-compensability",
    )
    _require_value(
        packet["hard_safety_policy"]["failure_stops_affected_promotion"],
        True,
        "hard-safety promotion stop",
    )
    _require_value(
        packet["hard_safety_policy"]["failure_cannot_be_averaged_away"],
        True,
        "hard-safety averaging prohibition",
    )
    _require_value(
        packet["hard_safety_policy"]["zero_observed_failures_is_not_zero_risk"],
        True,
        "zero-failure risk interpretation",
    )
    _require_value(
        packet["hard_safety_policy"]["required_report_fields"],
        ["observed_count", "denominator", "confidence_bound", "exposure", "unexercised_surface"],
        "hard-safety report fields",
    )

    _validate_estimands(packet)
    power = packet["power_simulation"]
    _require_value(power["provisional_confirmatory_n"], 384, "provisional N")
    _require_value(
        power["provisional_confirmatory_n_is_non_authoritative"], True, "provisional N authority"
    )
    _require_value(
        power["final_confirmatory_n"],
        "unset_until_independently_emitted_derived_n",
        "final N status",
    )
    _require_value(
        power["final_n_authority"],
        "later_manifest_must_bind_to_independently_emitted_derived_n",
        "final N authority",
    )
    _require_value(
        power["nonrecoverable_infrastructure_loss_allowance"],
        0.15,
        "infrastructure-loss power allowance",
    )
    _require_value(
        power["nonrecoverable_infrastructure_loss_allowance_is_power_input"],
        True,
        "infrastructure-loss power input",
    )
    _require_value(power["derived_n_must_be_emitted"], True, "derived N emission")
    _require_value(power["output_digest_must_be_emitted"], True, "power output digest emission")
    _require_value(
        power["changing_any_input_creates_new_specification_version"],
        True,
        "power input versioning",
    )

    _require_value(
        packet["not_frozen_by_packet_a"],
        [
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
        ],
        "Packet A non-frozen list",
    )
    _require_value(
        packet["execution_boundary"],
        {
            "packet_a_executed": False,
            "benchmark_manifest_exists": False,
            "confirmatory_results_exist": False,
            "production_behavior_changed": False,
            "model_or_provider_run_performed": False,
            "l2_or_l3_packet_a_evidence_claimed": False,
            "wave4_l2_provenance_is_historical_input_only": True,
        },
        "execution boundary",
    )
    _require_keys(
        packet["provenance"],
        {
            "canonical_inputs",
            "historical_evidence_boundary",
            "secret_refusal_preserved",
            "imported_text_remains_untrusted_data",
        },
        "packet_a.provenance",
    )
    _require_value(
        packet["provenance"]["canonical_inputs"],
        [
            {"path": path, "role": role, "sha256": digest}
            for path, role, digest in EXPECTED_PROVENANCE
        ],
        "provenance sources",
    )
    for source in packet["provenance"]["canonical_inputs"]:
        source_path = root / source["path"]
        _require(source_path.is_file(), f"provenance source missing: {source_path}")
        _require(
            hashlib.sha256(source_path.read_bytes()).hexdigest() == source["sha256"],
            f"provenance digest drift: {source['path']}",
        )
    _require(
        packet["provenance"]["secret_refusal_preserved"] is True,
        "secret refusal provenance is not preserved",
    )
    _require(
        packet["provenance"]["imported_text_remains_untrusted_data"] is True,
        "untrusted imported text policy is not preserved",
    )

    binding = packet["content_binding"]
    _require_keys(
        binding,
        {"algorithm", "canonicalization", "scope", "narrative_binding", "specification_digest"},
        "packet_a.content_binding",
    )
    _require_value(binding["algorithm"], "SHA-256", "content digest algorithm")
    _require_value(
        binding["canonicalization"],
        (
            "UTF-8 JSON sorted keys and compact separators, excluding "
            "packet_a.content_binding.specification_digest"
        ),
        "content digest canonicalization",
    )
    _require_value(
        binding["scope"], "complete_machine_readable_specification", "content digest scope"
    )
    _require_value(
        binding["narrative_binding"],
        {
            "path": "docs/research/ATC_PACKET_A_SPECIFICATION_FREEZE_2026-08-30.md",
            "required_fields": ["specification_digest", "evidence_level", "execution_boundary"],
            "validator": "bench/validate_memory_reliability_spec.py",
        },
        "narrative binding contract",
    )
    actual_digest = compute_specification_digest(spec)
    _require_value(
        binding["specification_digest"], actual_digest, "content digest self-consistency"
    )
    if require_golden_digest:
        _require_value(actual_digest, GOLDEN_SPECIFICATION_DIGEST, "golden content digest")
        if validate_narrative:
            _validate_narrative(packet, root)


def load_and_validate(path: Path = SPEC_PATH) -> dict[str, Any]:
    """Load and validate the committed spec without modifying it."""

    value = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(value, dict), "specification root must be an object")
    validate_spec(value)
    return value


if __name__ == "__main__":
    load_and_validate()
    print(f"validated {SPEC_PATH}")
