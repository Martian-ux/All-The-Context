"""Independent fail-closed validation for the Packet A specification.

This module is intentionally read-only.  It validates a candidate document
against an independently authored contract rather than deriving expectations
from the candidate itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from copy import deepcopy
from pathlib import Path
from typing import Any

try:
    from bench.packet_a_contract import (
        EXPECTED_BASE_CELL_COUNT,
        EXPECTED_CANONICAL_SPECIFICATION_DIGEST,
        EXPECTED_COMPARISON_ARM_IDS,
        EXPECTED_COMPARISON_CELL_IDS,
        EXPECTED_CONTRACT_SOURCE_SHA256,
        EXPECTED_NARRATIVE_SEMANTIC_DIGEST,
        EXPECTED_PROVISIONAL_MINIMUM_PAIRED_EPISODE_COUNT,
        EXPECTED_PROVISIONAL_REPETITIONS_PER_BASE_CELL,
        EXPECTED_ROOT_CAOS_COMPONENTS,
        EXPECTED_S_H_STATUS_IDS,
        EXPECTED_STRUCTURE_DIGEST,
        EXPECTED_VALIDATOR_VERSION,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from packet_a_contract import (  # type: ignore[no-redef]
        EXPECTED_BASE_CELL_COUNT,
        EXPECTED_CANONICAL_SPECIFICATION_DIGEST,
        EXPECTED_COMPARISON_ARM_IDS,
        EXPECTED_COMPARISON_CELL_IDS,
        EXPECTED_CONTRACT_SOURCE_SHA256,
        EXPECTED_NARRATIVE_SEMANTIC_DIGEST,
        EXPECTED_PROVISIONAL_MINIMUM_PAIRED_EPISODE_COUNT,
        EXPECTED_PROVISIONAL_REPETITIONS_PER_BASE_CELL,
        EXPECTED_ROOT_CAOS_COMPONENTS,
        EXPECTED_S_H_STATUS_IDS,
        EXPECTED_STRUCTURE_DIGEST,
        EXPECTED_VALIDATOR_VERSION,
    )

ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = ROOT / "bench" / "memory_reliability_spec.json"
FREEZE_DOCUMENT_PATH = (
    ROOT / "docs" / "research" / "ATC_PACKET_A_SPECIFICATION_FREEZE_2026-08-30.md"
)
CONTRACT_SOURCE_PATH = ROOT / "bench" / "packet_a_contract.py"
CONCRETE_PATH_TYPE = type(Path())
_CONTRACT_SOURCE_DIGEST_PATTERNS = (
    (
        re.compile(
            rb"(?m)^EXPECTED_CANONICAL_SPECIFICATION_DIGEST = \(\r?\n"
            rb'    "[0-9a-f]{64}"\r?\n\)'
        ),
        b'EXPECTED_CANONICAL_SPECIFICATION_DIGEST = ("<DERIVED_DIGEST>")',
    ),
    (
        re.compile(rb'(?m)^EXPECTED_STRUCTURE_DIGEST = "[0-9a-f]{64}"\r?$'),
        b'EXPECTED_STRUCTURE_DIGEST = "<DERIVED_DIGEST>"',
    ),
    (
        re.compile(
            rb"(?m)^EXPECTED_NARRATIVE_SEMANTIC_DIGEST = \(\r?\n"
            rb'    "[0-9a-f]{64}"\r?\n\)'
        ),
        b'EXPECTED_NARRATIVE_SEMANTIC_DIGEST = ("<DERIVED_DIGEST>")',
    ),
    (
        re.compile(rb'(?m)^EXPECTED_CONTRACT_SOURCE_SHA256 = "[0-9a-f]{64}"\r?$'),
        b'EXPECTED_CONTRACT_SOURCE_SHA256 = "<CONTRACT_SOURCE_SHA256>"',
    ),
)

# Public validator inputs are bounded before parsing, decoding, or hashing.
# These ceilings are deliberately generous for the frozen documents while
# keeping accidental or adversarial resource use finite.
MAX_INPUT_BYTES = 2_000_000
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 50_000
MAX_JSON_MEMBERS = 50_000
MAX_JSON_STRING_CHARS = 100_000
MAX_JSON_TOTAL_STRING_CHARS = 500_000
MAX_JSON_NUMBER_DIGITS = 128

# Code-owned authority: the candidate's self-digest is never used as the
# expected value, including when callers use the test-only compatibility flag
# ``require_golden_digest=False``.
GOLDEN_SPECIFICATION_DIGEST = EXPECTED_CANONICAL_SPECIFICATION_DIGEST
VALIDATOR_VERSION = EXPECTED_VALIDATOR_VERSION

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
EXPECTED_S_H_STATUS_MAPPING = {
    "EXPOSED": {
        "s_h_denominator": "INCLUDE",
        "failure_numerator": "COUNT_OBSERVED_FAILURE",
        "denominator_exclusion": "NONE",
        "disposition": "OUTCOME_RECEIPT_REQUIRED",
    },
    "NOT_APPLICABLE": {
        "s_h_denominator": "EXCLUDE",
        "failure_numerator": "NO_CREDIT",
        "denominator_exclusion": "PREDECLARED_NON_APPLICABILITY",
        "disposition": "REASON_AND_CAPABILITY_BOUNDARY_REQUIRED_NO_ZERO_FAILURE_CLAIM",
    },
    "MISSING": {
        "s_h_denominator": "EXCLUDE",
        "failure_numerator": "NO_CREDIT",
        "denominator_exclusion": "INCOMPLETE_EXPOSURE_NOT_A_POST_OUTCOME_EXCLUSION",
        "disposition": "FAIL_CLOSED_NO_ZERO_FAILURE_CLAIM",
    },
    "INDETERMINATE": {
        "s_h_denominator": "EXCLUDE",
        "failure_numerator": "NO_CREDIT",
        "denominator_exclusion": "INCOMPLETE_EXPOSURE_NOT_A_POST_OUTCOME_EXCLUSION",
        "disposition": "FAIL_CLOSED_NO_ZERO_FAILURE_CLAIM",
    },
    "UNEXERCISED": {
        "s_h_denominator": "EXCLUDE",
        "failure_numerator": "NO_CREDIT",
        "denominator_exclusion": "INCOMPLETE_EXPOSURE_NOT_A_POST_OUTCOME_EXCLUSION",
        "disposition": "FAIL_CLOSED_NO_ZERO_FAILURE_CLAIM",
    },
}
EXPECTED_M1_EVIDENCE_POLICY = {
    "explicit_user_evidence": {
        "source_class": "USER",
        "required_witness_grant": "CONFIGURED_SAME_DEVICE_WITNESS_GRANT",
        "grant_fields": [
            "authenticated_principal_id",
            "same_device_opaque_handle",
            "exact_project_id",
            "exact_episode_id",
            "exact_turn_id",
            "grant_generation",
            "expires_at_bounded_time_class",
        ],
        "grant_configured_before_observation": True,
        "default_without_grant": "TENTATIVE_UNTRUSTED_OBSERVATION",
        "may_close_transition": False,
        "may_establish_safety_or_success": False,
        "core_verification_required_for_independent_status": True,
    },
    "ordinary_evidence": {
        "default_witness": "untrusted_observation",
        "default_verification": "TENTATIVE",
        "default_credit": "NO_TRANSITION_CREDIT",
        "default_is_not_truth_or_safety": True,
    },
    "relay_provider_paths": {
        "path_actors": [
            "RELAY",
            "CLIENT",
            "MODEL",
            "TOOL",
            "PROVIDER",
            "CONNECTOR",
            "IMPORTED_TEXT",
        ],
        "allowed_role": "SOURCE_ONLY_UNTRUSTED_OBSERVATION",
        "may_relabel": False,
        "may_issue": False,
        "may_be_transition_witness": False,
        "may_upgrade_ordinary_or_user_evidence": False,
    },
}
EXPECTED_M1_ACL_SENSITIVITY_POLICY = {
    "sensitivity_classes": {
        "S0": {
            "meaning": "public specification or aggregate research result",
            "permitted_content": ["closed_codes", "aggregate_counts", "digests"],
        },
        "S1": {
            "meaning": "opaque lifecycle metadata",
            "permitted_content": [
                "opaque_ids",
                "versions",
                "generations",
                "enums",
                "bounded_times",
            ],
        },
        "S2": {
            "meaning": "authorized project/workspace metadata",
            "permitted_content": [
                "project_scoped_ids",
                "source_revisions",
                "typed_refs",
                "bounded_codes",
            ],
        },
        "S3": {
            "meaning": "restricted security or participant metadata",
            "permitted_content": ["security_codes", "participant_handles", "restricted_acl_refs"],
        },
    },
    "acl_rule": (
        "narrowest_of_core_authorized_principal_isolated_harness_and_consented_report_recipient"
    ),
    "acl_filtering": {
        "applied_by": "CORE",
        "applied_before_exposure": True,
        "exact_project_scope_required": True,
        "exact_principal_view_required": True,
        "same_device_grant_required_for_explicit_user_evidence": True,
        "unknown_principal_or_project": "DEFAULT_DENY",
        "relay_provider_cannot_widen_or_relabel": True,
        "external_copy_requires_known_destination_and_deletion_path": True,
    },
    "artifact_acl_defaults": {
        "M1_TRANSACTION": {
            "sensitivity": ["S1", "S2"],
            "acl": ["CORE", "EXACT_PRINCIPAL_VIEW", "ISOLATED_HARNESS"],
        },
        "OUTCOME_RECEIPT": {
            "sensitivity": ["S1", "S2"],
            "acl": ["CORE", "ISOLATED_HARNESS", "AUTHORIZED_RESEARCH_READER"],
        },
        "WORKING_CHECKPOINT": {
            "sensitivity": ["S2"],
            "acl": ["CORE", "EXACT_PROJECT_PRINCIPAL"],
        },
        "RECONCILIATION_ARTIFACT": {
            "sensitivity": ["S2"],
            "acl": ["CORE", "EXACT_PROJECT_PRINCIPAL"],
        },
        "M3_CLOSURE_REPORT": {
            "sensitivity": ["S1", "S2"],
            "acl": ["CORE", "ISOLATED_LAB"],
        },
        "CACHE_OR_REPORT": {
            "sensitivity": ["S0", "S1", "S2"],
            "acl": ["CORE", "CONSENTED_REPORT_RECIPIENT"],
        },
    },
}
EXPECTED_M1_RECEIPT_TOPOLOGY = {
    "mandatory_episode_bindings": [
        "episode_id_exact",
        "task_id_exact",
        "task_family_id_exact",
        "fixture_repository_id_exact",
        "source_state_receipt_id_exact",
        "immutable_source_state_ref_exact",
        "source_inventory_sha256_exact",
        "mutation_schedule_id_exact",
        "oracle_id_and_version_exact",
        "client_model_stratum_id_exact",
        "episode_seed_exact",
        "arm_id_exact",
        "cell_id_exact",
        "project_id_exact",
        "policy_generation_exact",
        "principal_view_generation_exact",
        "dependency_binding_exact",
    ],
    "task_receipt_schema": {
        "required_fields": [
            "task_receipt_id",
            "episode_id",
            "task_id",
            "task_family_id",
            "fixture_repository_id",
            "immutable_source_state_ref",
            "source_inventory_sha256",
            "mutation_schedule_id",
            "oracle_id_and_version",
            "client_model_stratum_id",
            "episode_seed",
        ],
        "binds_to": "mandatory_episode_bindings",
    },
    "source_state_receipt_schema": {
        "required_fields": [
            "source_state_receipt_id",
            "episode_id",
            "repository_id",
            "immutable_commit_or_ref",
            "file_inventory",
            "file_inventory_sha256",
            "source_state_sha256",
            "source_state_generation",
        ],
        "binds_to": "task_receipt_schema",
    },
    "reserve_receipt_schema": {
        "required_fields": [
            "replacement_receipt_id",
            "episode_id",
            "replaced_episode_id",
            "reserve_episode_id",
            "trigger_status",
            "independent_diagnosis_receipt_id",
            "last_valid_state_receipt_id",
            "same_family_repository_stratum",
            "preserved_binding_digest",
            "predeclared_before_execution",
        ],
        "binds_to": "mandatory_episode_bindings",
    },
    "last_valid_state_receipt_schema": {
        "required_fields": [
            "last_valid_state_receipt_id",
            "episode_id",
            "task_id",
            "source_state_receipt_id",
            "arm_id",
            "last_valid_state",
            "last_valid_state_digest",
            "last_valid_state_step",
            "retained_until",
            "attrition_disposition",
        ],
        "binds_to": "mandatory_episode_bindings",
    },
    "outcome_receipt_bindings": [
        "task_receipt_id_exact",
        "source_state_receipt_id_exact",
        "use_id_exact",
        "action_envelope_id_exact",
        "oracle_id_and_version_exact",
        "episode_id_exact",
        "arm_id_exact",
        "cell_id_exact",
        "project_id_exact",
        "policy_generation_exact",
        "principal_view_generation_exact",
        "dependency_binding_exact",
    ],
}
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
EXPECTED_CELL_MECHANISMS = {
    "MUT_BRANCH_OR_SOURCE_REVISION_CHANGE": "BRANCH_OR_SOURCE_REVISION_CHANGE",
    "MUT_CORRECTED_REQUIREMENTS": "CORRECTED_REQUIREMENTS",
    "MUT_DEPENDENCY_CHANGE": "DEPENDENCY_CHANGE",
    "MUT_ABANDONED_APPROACH": "ABANDONED_APPROACH",
    "MUT_ORDINARY_DELETION": "ORDINARY_DELETION",
    "MUT_TERMINAL_PURGE": "TERMINAL_PURGE",
    "MUT_PROJECT_AMBIGUITY": "PROJECT_AMBIGUITY",
    "MUT_EXTERNALLY_MODIFIED_FILES": "EXTERNALLY_MODIFIED_FILES",
    "MUT_STALE_CHECKPOINT_SUPERFICIALLY_PLAUSIBLE": "STALE_CHECKPOINT_SUPERFICIALLY_PLAUSIBLE",
    "ABL_WORKING_CHECKPOINTS": "working_checkpoints",
    "ABL_EPISODIC_OUTCOME_RECORDS": "episodic_outcome_records",
    "ABL_TEMPORAL_RELATIONAL_PROJECTIONS": "temporal_relational_projections",
    "ABL_PROCEDURE_DISTILLATION_AND_RETRIEVAL": "procedure_distillation_and_retrieval",
    "ABL_TYPED_EVENT_ACTIVATION": "typed_event_activation",
    "ABL_CONSEQUENCE_CONTRACTS_AND_CHECKPOINT_TOKENS": (
        "consequence_contracts_and_checkpoint_tokens"
    ),
    "ABL_OUTCOME_DEPENDENCY_CLOSURE": "outcome_dependency_closure",
    "ABL_FULL_ATC_RESEARCH_STACK": "all_preregistered_winning_atc_mechanisms",
    "ABL_CHECKPOINT_WITHOUT_RECONCILIATION": "checkpoint_without_reconciliation",
    "ABL_RECONCILIATION_WITHOUT_M1_BINDING": "reconciliation_without_m1_binding",
    "ABL_M1_WITHOUT_DEPENDENCY_OR_INVALIDATION_CLOSURE": (
        "m1_without_dependency_or_invalidation_closure"
    ),
    "ABL_SEMANTIC_ACKNOWLEDGEMENT_CHALLENGE_VS_CONTENT_FREE_PLACEBO": (
        "semantic_acknowledgement_challenge_vs_content_free_placebo"
    ),
    "ABL_PROSPECTIVE_MEMORY_WITHOUT_NEGATIVE_GUARDS": "prospective_memory_without_negative_guards",
    "ABL_PROSPECTIVE_MEMORY_WITHOUT_CURRENT_VERSION_REREAD": (
        "prospective_memory_without_current_version_reread"
    ),
    "ABL_PROSPECTIVE_MEMORY_WITHOUT_DEPENDENCY_CLOSURE": (
        "prospective_memory_without_dependency_closure"
    ),
    "ABL_PROSPECTIVE_MEMORY_WITHOUT_ACTION_CEILING": "prospective_memory_without_action_ceiling",
    "ABL_CONDITIONAL_FAILURE_MEMORY_WITHOUT_DISCONFIRMATION": (
        "conditional_failure_memory_without_disconfirmation"
    ),
    "ABL_STATIC_WARRANTY_WITHOUT_LOCAL_USE_TIME_VERIFICATION": (
        "static_warranty_without_local_use_time_verification"
    ),
    "ABL_M3_OPTIMIZED_REBUILD_VS_INDEPENDENT_FULL_REBUILD": (
        "m3_optimized_rebuild_vs_independent_full_rebuild"
    ),
    "ABL_CONTINUITY_DEBT_AGGREGATE_VS_CATEGORY_VECTOR": (
        "continuity_debt_aggregate_vs_category_vector"
    ),
    "ABL_PROCEDURES_WITHOUT_APPLICABILITY_ROLLBACK_OR_PURGE_CLOSURE": (
        "procedures_without_applicability_rollback_or_purge_closure"
    ),
    "CELL_HYBRID_ATC_GOVERNED": "hybrid_atc_governed",
    "CELL_HYBRID_CHECKPOINT_RECONCILIATION": "checkpoint_reconciliation",
    "CELL_HYBRID_M1": "m1_observable_use_ledger",
    "CELL_HYBRID_M3": "m3_dependency_complete_closure",
}
EXPECTED_UNSUPPORTED_CELL_METADATA = [
    {
        "id": arm_id,
        "reason_code": "UNSUPPORTED_UNTIL_LATER_MANIFEST_CAPABILITY_CHECK",
        "denominator_disposition": "RETAIN_IN_E_w_NO_CREDIT",
        "capability_boundary": "NO_CONFIRMATORY_CREDIT_UNTIL_CAPABILITY_IS_VERIFIED",
    }
    for arm_id in EXPECTED_ARM_IDS[2:]
]
_UNSAFE_TEXT = re.compile(
    r"(?:[\x00-\x08\x0b\x0c\x0e-\x1f\r\n]|<script|BEGIN [A-Z ]+ PRIVATE KEY|"
    r"(?:^|[\s;&|])(?:rm|del|format|powershell|bash|cmd(?:\.exe)?)\b|"
    r"(?:api[_-]?key|authorization:|session[_-]?cookie))",
    flags=re.IGNORECASE,
)
EXPECTED_PROVENANCE = [
    (
        "docs/research/POST_BETA_CONTINUITY_AND_MEMORY_PROPOSAL_2026-08-29.md",
        "Packet A section 6 and non-displacing boundary",
        "f4f18ef1ed814f7d08c5e10d73a743c305889c627b495d972b0b03caec9ee245",
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
    (
        "bench/packet_a_contract.py",
        "code-owned Packet A authority constants and structure contract",
        EXPECTED_CONTRACT_SOURCE_SHA256,
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
    "task_manifest_contract",
    "client_model_build_strata",
    "arm_vocabulary",
    "comparison_cell_vocabulary",
    "comparison_arm_vocabulary",
    "unsupported_cell_metadata",
    "cell_contract",
    "required_ablations",
    "mutation_cells",
    "matched_hybrid_cells",
    "episode_contract",
    "permission_contract",
    "budget_contract",
    "secret_refusal",
    "m1_contract",
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
    "future_receipt_requirements",
    "failure_and_replacement_contract",
    "trust_contract",
    "lifecycle_parity_contract",
    "mechanism_contract",
    "not_frozen_by_packet_a",
    "execution_boundary",
    "provenance",
    "validation_contract",
    "content_binding",
}

EXPECTED_ROOT_KEYS = {
    "schema_version",
    "specification_id",
    "status",
    "documentation",
    "fixture",
    "claim_under_test",
    "primary_endpoint",
    "capabilities",
    "stages",
    "system_groups",
    "adapter_boundary",
    "experiments",
    "metric_families",
    "budgets",
    "contamination_controls",
    "statistics",
    "failure_taxonomy",
    "promotion_gates",
    "decision_states",
    "first_five_execution_order",
    "packet_a",
}

# These are field-level semantic authorities for the inherited root contract.
# They deliberately enumerate vocabularies, experiment identities, and budget
# inputs instead of comparing the candidate document to a copied JSON blob.
EXPECTED_ROOT_CAPABILITIES = [
    "working",
    "episodic",
    "semantic",
    "procedural",
    "relational",
    "temporal",
    "correction",
    "forgetting",
    "privacy",
    "cross_agent_portability",
    "recall_to_action",
    "consequence_closure",
    "outcome_closure",
]
EXPECTED_ROOT_STAGES = [
    "capture",
    "canonicalize",
    "consolidate_project",
    "retrieve",
    "compile",
    "read_reason",
    "act",
    "verify_outcome",
    "correct_forget",
    "invalidate_rebuild",
]
EXPECTED_SIMPLE_BASELINE_IDS = [
    "simple_no_memory",
    "simple_long_context",
    "simple_static_profile",
    "simple_append_log_search",
    "simple_atc_retrieval_v3",
]
EXPECTED_COMPETITOR_SYSTEMS = [
    ("competitor_mem0", "Mem0"),
    ("competitor_graphiti", "Graphiti"),
    ("competitor_hindsight", "Hindsight"),
    ("competitor_letta", "Letta"),
    ("competitor_langmem", "LangMem"),
]
EXPECTED_HYBRID_IDS = ["hybrid_best_non_atc", "hybrid_atc_governed"]
EXPECTED_ABLATION_IDS_AND_MECHANISMS = [
    ("atc_plus_working_checkpoints", "working_checkpoints"),
    ("atc_plus_episodic_outcomes", "episodic_outcome_records"),
    ("atc_plus_temporal_relational", "temporal_relational_projections"),
    ("atc_plus_procedures", "procedure_distillation_and_retrieval"),
    ("atc_plus_event_activation", "typed_event_activation"),
    (
        "atc_plus_consequence_closure",
        "consequence_contracts_and_checkpoint_tokens",
    ),
    ("atc_plus_outcome_closure", "outcome_dependency_closure"),
    ("atc_full_research_stack", "all_preregistered_winning_atc_mechanisms"),
]
EXPECTED_ADAPTER_LOGICAL_OPERATIONS = [
    "reset",
    "present_event",
    "checkpoint",
    "observe_outcome",
    "correct",
    "forget",
    "export_state",
    "import_state",
    "inventory_dependencies",
    "close",
]
EXPECTED_ADAPTER_HARNESS_OWNED = [
    "episode_order",
    "principal",
    "frozen_clock",
    "budgets",
    "faults",
    "outcome_oracle",
    "condition_randomization",
]
EXPECTED_ADAPTER_FORBIDDEN_INPUTS = [
    "gold_labels",
    "forbidden_sets",
    "promotion_thresholds",
    "future_events",
    "other_condition_outputs",
]
EXPECTED_ADAPTER_DECLARATION_FIELDS = [
    "system_version",
    "source_revision",
    "network_and_provider_calls",
    "models_and_parameters",
    "cache_and_persistence_locations",
    "supported_operations",
    "reset_cleanup_behavior",
    "data_egress",
    "correction_semantics",
    "purge_test_boundary",
    "common_harness_emulation",
]
EXPECTED_METRIC_FAMILIES = {
    "capture": [
        "capture_precision",
        "capture_recall",
        "false_write_rate",
        "witness_accuracy",
        "source_span_completeness",
    ],
    "state": [
        "current_state_accuracy",
        "temporal_accuracy",
        "correction_convergence",
        "conflict_accuracy",
        "abstention_accuracy",
    ],
    "retrieval_compilation": [
        "recall_at_k",
        "mrr",
        "ndcg",
        "set_sufficiency",
        "prerequisite_recall",
        "exception_recall",
        "stale_inclusion",
        "contradiction",
        "redundancy",
    ],
    "continuity_learning": [
        "working_resume_accuracy",
        "portability_semantic_parity",
        "repeated_failure_reduction",
        "false_procedure_transfer",
        "task_success",
    ],
    "action": [
        "current_authorized_outcome_success",
        "recall_to_action_conversion",
        "retrieval_to_action_gap",
        "task_progress",
    ],
    "closure": [
        "false_activation",
        "invalid_token_acceptance",
        "stale_checkpoint_escape",
        "dependency_completeness",
        "rebuild_determinism",
        "purge_residue",
    ],
    "privacy_efficiency": [
        "fields_disclosed",
        "tokens_disclosed",
        "cumulative_disclosure",
        "model_calls",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "storage_bytes",
        "monetary_cost",
        "latency_p50",
        "latency_p95",
        "latency_p99",
    ],
}
EXPECTED_CONTAMINATION_CONTROLS = [
    "freeze_public_development_private_confirmatory_and_fault_partitions_by_digest",
    "use_opaque_symbolic_values_and_per_run_canaries",
    "exclude_labels_forbidden_ids_future_events_and_gates_from_adapter_inputs",
    "keep_unpresented_benchmark_data_outside_adapter_search_roots",
    "reset_state_caches_namespaces_files_and_provider_threads_between_conditions",
    "test_cross_principal_canary_absence_after_reset",
    "treat_imported_instruction_shaped_content_as_data",
    "keep_policy_and_budget_configuration_outside_imported_content",
    "record_model_build_prompt_digest_parameters_and_reasoning_effort",
    "use_no_memory_condition_to_estimate_task_leakage",
    "prefer_programmatic_or_environment_oracles",
    "blind_soft_judges_to_condition_name",
    "keep_all_attempted_runs_including_crashes_and_unsafe_outputs",
]
EXPECTED_FAILURE_TAXONOMY = [
    "CAPTURE_MISS",
    "CAPTURE_FALSE_WRITE",
    "WITNESS_COLLAPSE",
    "CANONICAL_WRONG_CURRENT",
    "CANONICAL_FALSE_INFERENCE",
    "EPISODE_OUTCOME_CONFUSION",
    "TEMPORAL_BOUNDARY",
    "RELATION_CLOSURE_MISS",
    "RETRIEVAL_MISS",
    "RETRIEVAL_STALE",
    "SET_INSUFFICIENT",
    "EXCESS_DISCLOSURE",
    "UNAUTHORIZED_INFLUENCE",
    "WORKING_STATE_DRIFT",
    "PORTABILITY_SEMANTIC_DRIFT",
    "READER_MISUSE",
    "ACTION_NONUSE",
    "PROCEDURE_FALSE_TRANSFER",
    "SELF_REINFORCEMENT",
    "CORRECTION_NONCONVERGENCE",
    "FORGETTING_SEMANTIC_COLLAPSE",
    "STALE_CHECKPOINT_ESCAPE",
    "DEPENDENCY_OMISSION",
    "PURGE_RESIDUE",
    "EVALUATOR_ERROR",
    "BUDGET_ESCAPE",
    "CONTAMINATION",
]


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
    _require(actual == expected, "object keys differ")


def _strict_equal(actual: Any, expected: Any) -> bool:
    """Compare JSON values without Python's bool/int equality coercion."""

    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return set(actual) == set(expected) and all(
            _strict_equal(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _strict_equal(left, right) for left, right in zip(actual, expected, strict=True)
        )
    return actual == expected


def _require_value(value: Any, expected: Any, path: str) -> None:
    _require(_strict_equal(value, expected), f"{path} differs from the frozen value")


def _require_safe_concrete_path(value: Any, label: str) -> None:
    """Reject virtual, path-like, and pathlib-subclass inputs before dispatch."""

    _require(type(value) is CONCRETE_PATH_TYPE, f"{label} must be a concrete pathlib path")


def _file_identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _compute_contract_source_digest(document: bytes) -> str:
    """Hash contract source after removing its derived digest literals."""

    _require(type(document) is bytes, "contract source must be bytes")
    _require(len(document) <= MAX_INPUT_BYTES, "contract source exceeds byte limit")
    normalized = document
    for pattern, replacement in _CONTRACT_SOURCE_DIGEST_PATTERNS:
        normalized, count = pattern.subn(replacement, normalized)
        _require(count == 1, "contract source digest marker is not unique")
    return hashlib.sha256(normalized).hexdigest()


def _read_contract_source_digest() -> str:
    return _compute_contract_source_digest(_read_bounded_file(CONTRACT_SOURCE_PATH))


def _read_bounded_file(path: Path, *, maximum_bytes: int = MAX_INPUT_BYTES) -> bytes:
    """Read a regular file with bounded, identity-checked, content-free failure."""

    _require_safe_concrete_path(path, "input path")
    try:
        before = path.stat()
        _require(stat.S_ISREG(before.st_mode), "input is not a regular file")
        _require(before.st_size <= maximum_bytes, "input exceeds byte limit")
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            _require(_file_identity(opened) == _file_identity(before), "input changed during read")
            _require(opened.st_size == before.st_size, "input changed during read")
            _require(opened.st_mtime_ns == before.st_mtime_ns, "input changed during read")
            data = handle.read(maximum_bytes + 1)
            after = os.fstat(handle.fileno())
        _require(_file_identity(after) == _file_identity(opened), "input changed during read")
        _require(after.st_mtime_ns == opened.st_mtime_ns, "input changed during read")
        _require(len(data) <= maximum_bytes, "input exceeds byte limit")
        _require(len(data) == after.st_size, "input changed during read")
        final = path.stat()
        _require(_file_identity(final) == _file_identity(after), "input changed during read")
        _require(final.st_size == after.st_size, "input changed during read")
        _require(final.st_mtime_ns == after.st_mtime_ns, "input changed during read")
        return data
    except SpecificationValidationError:
        raise
    except (OSError, ValueError):
        pass
    raise SpecificationValidationError("input read failed")


def _validate_json_limits(value: Any) -> None:
    """Reject non-JSON, cyclic, or oversized values without unbounded recursion."""

    nodes = 0
    members = 0
    string_chars = 0
    active_containers: set[int] = set()
    seen_containers: set[int] = set()
    pending: list[tuple[Any, int, bool]] = [(value, 0, False)]
    while pending:
        current, depth, exiting = pending.pop()
        current_type = type(current)
        if exiting:
            active_containers.remove(id(current))
            continue
        nodes += 1
        _require(nodes <= MAX_JSON_NODES, "document exceeds JSON node limit")
        _require(depth <= MAX_JSON_DEPTH, "document exceeds JSON depth limit")
        if current_type is str:
            string_chars += len(current)
            _require(len(current) <= MAX_JSON_STRING_CHARS, "document exceeds string limit")
            _require(
                string_chars <= MAX_JSON_TOTAL_STRING_CHARS,
                "document exceeds total string limit",
            )
        elif current_type is dict:
            container_id = id(current)
            _require(container_id not in active_containers, "document contains a cycle")
            _require(
                container_id not in seen_containers,
                "document contains a shared container reference",
            )
            seen_containers.add(container_id)
            active_containers.add(container_id)
            members += len(current)
            _require(members <= MAX_JSON_MEMBERS, "document exceeds member limit")
            pending.append((current, depth, True))
            for key, child in current.items():
                _require(type(key) is str, "document contains a non-text object key")
                string_chars += len(key)
                _require(len(key) <= MAX_JSON_STRING_CHARS, "document exceeds string limit")
                _require(
                    string_chars <= MAX_JSON_TOTAL_STRING_CHARS,
                    "document exceeds total string limit",
                )
                pending.append((child, depth + 1, False))
        elif current_type is list:
            container_id = id(current)
            _require(container_id not in active_containers, "document contains a cycle")
            _require(
                container_id not in seen_containers,
                "document contains a shared container reference",
            )
            seen_containers.add(container_id)
            active_containers.add(container_id)
            pending.append((current, depth, True))
            for child in current:
                pending.append((child, depth + 1, False))
        elif current_type is int:
            try:
                digits = len(str(abs(current)))
            except ValueError:
                number_conversion_failed = True
            else:
                number_conversion_failed = False
            if number_conversion_failed:
                raise SpecificationValidationError("document exceeds number digit limit")
            _require(digits <= MAX_JSON_NUMBER_DIGITS, "document exceeds number digit limit")
        elif current_type is float:
            _require(math.isfinite(current), "document contains a non-finite number")
        elif current_type is bool or current is None:
            continue
        else:
            raise SpecificationValidationError("document contains an unsupported JSON value")


def _structural_projection(value: Any, path: tuple[str | int, ...] = ()) -> Any:
    """Build the code-owned structure projection used before field access.

    Objects are sorted by key so harmless JSON object reordering remains valid.
    Arrays retain order, and scalar values are retained with explicit type tags;
    this makes list identity/order and every frozen container boundary exact while
    the specification digest remains the separate self-binding authority.
    """

    value_type = type(value)
    if value_type is dict:
        children = []
        for key in sorted(value):
            if path == ("packet_a", "content_binding") and key == "specification_digest":
                continue
            children.append([key, _structural_projection(value[key], (*path, key))])
        return ["object", children]
    if value_type is list:
        return [
            "array",
            [_structural_projection(child, (*path, index)) for index, child in enumerate(value)],
        ]
    if value_type is str:
        return ["string", value]
    if value_type is bool:
        return ["boolean", value]
    if value_type is int:
        return ["integer", value]
    if value_type is float:
        return ["number", value]
    if value is None:
        return ["null"]
    raise SpecificationValidationError("document structure contains an unsupported value")


def _compute_structure_digest(value: Any) -> str:
    """Hash the frozen schema/container contract without exposing candidate data."""

    try:
        projection = _structural_projection(value)
        encoded = json.dumps(
            projection,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (MemoryError, RecursionError, TypeError, UnicodeError, ValueError):
        serialization_failed = True
    else:
        serialization_failed = False
    if serialization_failed:
        raise SpecificationValidationError("document structure digest failed")
    return hashlib.sha256(encoded).hexdigest()


def _validate_document_structure(spec: dict[str, Any]) -> None:
    """Reject any nested schema, container, order, or identity drift first."""

    _require_value(
        _compute_structure_digest(spec),
        EXPECTED_STRUCTURE_DIGEST,
        "document structure contract",
    )


def _bounded_deepcopy(value: Any) -> Any:
    try:
        copied = deepcopy(value)
    except (MemoryError, RecursionError, TypeError, ValueError):
        copy_failed = True
    else:
        copy_failed = False
    if copy_failed:
        raise SpecificationValidationError("document copy failed")
    return copied


def _require_safe_bounded_text(value: Any, path: str, maximum: int) -> None:
    _require(isinstance(value, str), f"{path} must be text")
    _require(0 < len(value) <= maximum, f"{path} is outside its bounded text policy")
    _require(_UNSAFE_TEXT.search(value) is None, f"{path} contains unsafe content")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def _reject_nonfinite_json_constant(value: str) -> Any:
    raise SpecificationValidationError("non-finite JSON constant is forbidden")


def _parse_bounded_int(value: str) -> int:
    _require(
        len(value.lstrip("-")) <= MAX_JSON_NUMBER_DIGITS,
        "document exceeds number digit limit",
    )
    return int(value)


def _parse_bounded_float(value: str) -> float:
    _require(
        len(value.lstrip("-")) <= MAX_JSON_NUMBER_DIGITS,
        "document exceeds number digit limit",
    )
    parsed = float(value)
    _require(math.isfinite(parsed), "document contains a non-finite number")
    return parsed


def _parse_bounded_json_bytes(document: bytes) -> Any:
    """Parse bounded JSON with one fail-closed policy for all JSON fragments."""

    _require(type(document) is bytes, "invalid JSON document")
    _require(len(document) <= MAX_INPUT_BYTES, "input exceeds byte limit")
    try:
        value = json.loads(
            document.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite_json_constant,
            parse_int=_parse_bounded_int,
            parse_float=_parse_bounded_float,
        )
    except SpecificationValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        parse_failed = True
    else:
        parse_failed = False
    if parse_failed:
        raise SpecificationValidationError("invalid JSON document")
    _validate_json_limits(value)
    return value


def load_json_document(path: Path) -> dict[str, Any]:
    """Load a JSON document without silently accepting duplicate or non-finite data."""

    _require_safe_concrete_path(path, "input path")
    value = _parse_bounded_json_bytes(_read_bounded_file(path))
    _require(isinstance(value, dict), "specification root must be an object")
    return value


def _validate_root_contract(spec: dict[str, Any]) -> None:
    """Validate inherited root semantics independently of the content digest."""

    _require_value(
        spec["status"],
        "specification_only_no_harness_or_adapter_implemented",
        "status",
    )
    _require_value(
        spec["documentation"],
        "docs/research/ATC_MEMORY_EVALUATION_PROGRAM.md",
        "documentation",
    )
    _require_value(
        spec["claim_under_test"],
        (
            "ATC improves current authorized outcomes and longitudinal continuity beyond retrieval "
            "under fixed quality, privacy, latency, context, and cost budgets."
        ),
        "claim_under_test",
    )

    fixture = spec["fixture"]
    _require_keys(
        fixture,
        {"path", "schema_version", "content_policy", "adapter_visible_gold"},
        "fixture",
    )
    for field, expected in {
        "path": "bench/memory_reliability_fixtures.json",
        "schema_version": 1,
        "content_policy": "synthetic_symbolic_no_real_personal_context",
        "adapter_visible_gold": False,
    }.items():
        _require_value(fixture[field], expected, f"fixture.{field}")

    _require_value(spec["capabilities"], EXPECTED_ROOT_CAPABILITIES, "capabilities")
    _require_value(spec["stages"], EXPECTED_ROOT_STAGES, "stages")

    groups = spec["system_groups"]
    _require_keys(
        groups,
        {"simple_baselines", "individual_competitors", "hybrids", "atc_research_ablations"},
        "system_groups",
    )
    expected_simple_descriptions = [
        "Current task only; estimates pretraining and task leakage.",
        (
            "Full authorized history under a frozen deterministic truncation policy and the same "
            "context cap."
        ),
        "Frozen compact user, project, or task profile.",
        (
            "Append-only logical event log with exact and lexical search but no canonical "
            "current-state model."
        ),
        "Current ATC authorization-first retrieval and deterministic set compilation.",
    ]
    simple = groups["simple_baselines"]
    _require(len(simple) == len(EXPECTED_SIMPLE_BASELINE_IDS), "simple baseline count differs")
    for index, (item, expected_id, expected_description) in enumerate(
        zip(simple, EXPECTED_SIMPLE_BASELINE_IDS, expected_simple_descriptions, strict=True)
    ):
        _require_keys(item, {"id", "description"}, f"system_groups.simple_baselines[{index}]")
        _require_value(item["id"], expected_id, f"simple baseline {index} id")
        _require_value(
            item["description"], expected_description, f"simple baseline {index} description"
        )

    competitors = groups["individual_competitors"]
    _require(len(competitors) == len(EXPECTED_COMPETITOR_SYSTEMS), "competitor count differs")
    for index, (item, (expected_id, expected_system)) in enumerate(
        zip(competitors, EXPECTED_COMPETITOR_SYSTEMS, strict=True)
    ):
        path = f"system_groups.individual_competitors[{index}]"
        _require_keys(
            item,
            {"id", "system", "adapter_cell", "unsupported_operations_must_be_reported"},
            path,
        )
        _require_value(item["id"], expected_id, f"{path}.id")
        _require_value(item["system"], expected_system, f"{path}.system")
        _require_value(item["adapter_cell"], "individual_unwrapped", f"{path}.adapter_cell")
        _require_value(
            item["unsupported_operations_must_be_reported"],
            True,
            f"{path}.unsupported_operations_must_be_reported",
        )

    expected_hybrid_descriptions = [
        (
            "Frozen development-set recipe using winning non-ATC mechanisms without ATC-specific "
            "authority or closure."
        ),
        (
            "Best non-ATC mechanisms behind Core authorization, temporal resolution, canonical "
            "reread, dependency manifests, and deterministic closure."
        ),
    ]
    hybrids = groups["hybrids"]
    _require(len(hybrids) == len(EXPECTED_HYBRID_IDS), "hybrid count differs")
    for index, (item, expected_id, expected_description) in enumerate(
        zip(hybrids, EXPECTED_HYBRID_IDS, expected_hybrid_descriptions, strict=True)
    ):
        path = f"system_groups.hybrids[{index}]"
        _require_keys(item, {"id", "description"}, path)
        _require_value(item["id"], expected_id, f"{path}.id")
        _require_value(item["description"], expected_description, f"{path}.description")

    ablations = groups["atc_research_ablations"]
    _require(
        len(ablations) == len(EXPECTED_ABLATION_IDS_AND_MECHANISMS),
        "research ablation count differs",
    )
    for index, (item, (expected_id, expected_mechanism)) in enumerate(
        zip(ablations, EXPECTED_ABLATION_IDS_AND_MECHANISMS, strict=True)
    ):
        path = f"system_groups.atc_research_ablations[{index}]"
        _require_keys(item, {"id", "mechanism"}, path)
        _require_value(item["id"], expected_id, f"{path}.id")
        _require_value(item["mechanism"], expected_mechanism, f"{path}.mechanism")

    adapter = spec["adapter_boundary"]
    _require_keys(
        adapter,
        {
            "status",
            "logical_operations",
            "harness_owned",
            "adapter_forbidden_inputs",
            "required_declaration_fields",
            "common_emulation_cannot_earn_adapter_capability_credit",
        },
        "adapter_boundary",
    )
    for field, expected in {
        "status": "conceptual_contract_for_future_abi",
        "logical_operations": EXPECTED_ADAPTER_LOGICAL_OPERATIONS,
        "harness_owned": EXPECTED_ADAPTER_HARNESS_OWNED,
        "adapter_forbidden_inputs": EXPECTED_ADAPTER_FORBIDDEN_INPUTS,
        "required_declaration_fields": EXPECTED_ADAPTER_DECLARATION_FIELDS,
        "common_emulation_cannot_earn_adapter_capability_credit": True,
    }.items():
        _require_value(adapter[field], expected, f"adapter_boundary.{field}")

    expected_experiments: dict[str, dict[str, Any]] = {
        "E01": {
            "order": 1,
            "name": "State, authority, correction, and forgetting",
            "execution_mode": "deterministic_local",
            "capabilities": [
                "semantic",
                "temporal",
                "correction",
                "forgetting",
                "privacy",
                "outcome_closure",
            ],
            "fixture_ids": [
                "semantic_current_state",
                "temporal_as_of_and_known_at",
                "correction_converges_all_surfaces",
                "forgetting_operations_are_distinct",
                "privacy_authorization_invariance",
                "purge_rebuild_removes_private_lineage",
            ],
            "required_system_groups": ["simple_baselines", "individual_competitors", "hybrids"],
            "primary_metrics": [
                "exact_current_authorized_state",
                "unauthorized_influence_count",
                "purge_residue_count",
            ],
            "go_gate_ids": [
                "universal_safety",
                "semantic_temporal",
                "correction",
                "forgetting_privacy",
                "outcome_closure",
            ],
        },
        "E02": {
            "order": 2,
            "name": "Working continuity and cross-agent portability",
            "execution_mode": "deterministic_then_stochastic",
            "capabilities": [
                "working",
                "correction",
                "cross_agent_portability",
                "recall_to_action",
            ],
            "fixture_ids": [
                "working_resume_after_compaction",
                "working_resume_after_correction",
                "portable_checkpoint_target_change",
            ],
            "required_system_ids": [
                "simple_long_context",
                "simple_static_profile",
                "simple_append_log_search",
                "competitor_letta",
                "competitor_langmem",
                "hybrid_best_non_atc",
                "hybrid_atc_governed",
                "atc_plus_working_checkpoints",
            ],
            "primary_metrics": [
                "exact_resume_state",
                "correct_next_action",
                "stale_resume_rate",
                "portability_semantic_parity",
            ],
            "go_gate_ids": ["universal_safety", "working_portability", "recall_to_action"],
        },
        "E03": {
            "order": 3,
            "name": "Episodic and procedural learning",
            "execution_mode": "deterministic_then_stochastic",
            "capabilities": ["episodic", "procedural", "recall_to_action", "outcome_closure"],
            "fixture_ids": [
                "episode_attempt_is_not_success",
                "procedure_reduces_repeat_failure",
                "procedure_precondition_blocks_false_transfer",
            ],
            "required_system_ids": [
                "simple_long_context",
                "simple_append_log_search",
                "competitor_hindsight",
                "competitor_letta",
                "hybrid_best_non_atc",
                "hybrid_atc_governed",
                "atc_plus_episodic_outcomes",
                "atc_plus_procedures",
            ],
            "primary_metrics": [
                "repeated_failure_reduction",
                "false_procedure_transfer_rate",
                "task_success",
            ],
            "go_gate_ids": [
                "universal_safety",
                "episodic_procedural",
                "recall_to_action",
                "outcome_closure",
            ],
        },
        "E04": {
            "order": 4,
            "name": "Relational, temporal, and recall-to-action",
            "execution_mode": "deterministic_then_stochastic",
            "capabilities": ["relational", "temporal", "semantic", "recall_to_action", "privacy"],
            "fixture_ids": [
                "relation_prerequisite_set",
                "relation_scoped_exception",
                "semantic_disconnect_action",
                "temporal_as_of_and_known_at",
            ],
            "required_system_ids": [
                "simple_append_log_search",
                "simple_atc_retrieval_v3",
                "competitor_graphiti",
                "competitor_hindsight",
                "hybrid_best_non_atc",
                "hybrid_atc_governed",
                "atc_plus_temporal_relational",
            ],
            "primary_metrics": [
                "set_sufficiency",
                "current_authorized_outcome_success",
                "retrieval_to_action_gap",
                "selected_context_tokens",
            ],
            "go_gate_ids": [
                "universal_safety",
                "semantic_temporal",
                "relational",
                "recall_to_action",
                "forgetting_privacy",
            ],
        },
        "E05": {
            "order": 5,
            "name": "Consequence and outcome closure",
            "execution_mode": "deterministic_exhaustive_faults",
            "capabilities": ["correction", "consequence_closure", "outcome_closure", "privacy"],
            "fixture_ids": [
                "consequence_correction_before_consume",
                "consequence_target_drift",
                "consequence_disconnect_resume",
                "purge_rebuild_removes_private_lineage",
            ],
            "required_system_ids": [
                "hybrid_atc_governed",
                "atc_plus_consequence_closure",
                "atc_plus_outcome_closure",
            ],
            "primary_metrics": [
                "stale_protected_checkpoint_escape_count",
                "invalid_token_acceptance_count",
                "missed_dependency_count",
                "purge_residue_count",
            ],
            "go_gate_ids": [
                "universal_safety",
                "correction",
                "consequence_closure",
                "outcome_closure",
            ],
        },
        "E06": {
            "order": 6,
            "name": "LongMemEval cleaned",
            "execution_mode": "official_benchmark_plus_atc_receipts",
            "upstream": "https://github.com/xiaowu0162/LongMemEval",
            "official_metrics_preserved": ["question_accuracy"],
            "added_metrics": [
                "source_span_precision",
                "current_state_correctness",
                "unauthorized_influence",
                "disclosure",
                "stage_attribution",
                "correction_replay",
            ],
        },
        "E07": {
            "order": 7,
            "name": "MemoryAgentBench incremental competencies",
            "execution_mode": "official_benchmark_plus_atc_receipts",
            "upstream": "https://github.com/HUST-AI-HYZ/MemoryAgentBench",
            "official_metrics_preserved": ["per_competency_results"],
            "added_metrics": [
                "current_state_accuracy",
                "false_write_rate",
                "false_forgetting_rate",
                "stage_attribution",
            ],
        },
        "E08": {
            "order": 8,
            "name": "LongMemEval-V2 context gathering",
            "execution_mode": "official_benchmark_plus_atc_receipts",
            "upstream": "https://github.com/xiaowu0162/LongMemEval-V2",
            "official_metrics_preserved": ["answer_accuracy", "query_latency"],
            "added_metrics": [
                "evidence_sufficiency",
                "workflow_to_action_transfer",
                "disclosure",
                "model_tool_and_monetary_cost",
            ],
        },
        "E09": {
            "order": 9,
            "name": "MemoryArena agentic outcomes",
            "execution_mode": "official_benchmark_plus_atc_receipts",
            "upstream": "https://memoryarena.github.io/",
            "official_metrics_preserved": ["task_progress_score", "task_success_rate"],
            "added_metrics": [
                "current_authorized_outcome_success",
                "prerequisite_coverage",
                "current_state_use",
                "false_transfer",
                "intervention_ablation",
            ],
        },
        "E10": {
            "order": 10,
            "name": "Multi-target consequence behavior",
            "execution_mode": "stochastic_fixed_target_roster",
            "prerequisite_experiment": "E05",
            "hard_effects": "synthetic_host_predicates_only",
            "conditions": [
                "identical_text",
                "best_static_template",
                "sequential_compilation",
                "joint_compilation",
            ],
        },
        "E11": {
            "order": 11,
            "name": "Opt-in longitudinal pilot",
            "execution_mode": "consented_local_product_pilot",
            "prerequisite_experiments": [
                "E01",
                "E02",
                "E03",
                "E04",
                "E05",
                "E06",
                "E07",
                "E08",
                "E09",
                "E10",
            ],
            "metrics": [
                "user_reported_usefulness",
                "correction_trust",
                "repeated_restatement_reduction",
                "user_burden",
            ],
        },
    }
    experiments = spec["experiments"]
    _require(len(experiments) == len(expected_experiments), "experiment count differs")
    for index, experiment in enumerate(experiments):
        path = f"experiments[{index}]"
        experiment_id = experiment.get("id")
        _require(experiment_id in expected_experiments, f"{path}.id is not a declared experiment")
        expected = {"id": experiment_id, **expected_experiments[experiment_id]}
        _require_keys(experiment, set(expected), path)
        for field, value in expected.items():
            _require_value(experiment[field], value, f"{path}.{field}")

    _require_keys(spec["metric_families"], set(EXPECTED_METRIC_FAMILIES), "metric_families")
    for family, metrics in EXPECTED_METRIC_FAMILIES.items():
        _require_value(spec["metric_families"][family], metrics, f"metric_families.{family}")

    budgets = spec["budgets"]
    _require_keys(
        budgets,
        {"reference_profile_required_fields", "local", "context", "cost_promotion"},
        "budgets",
    )
    _require_value(
        budgets["reference_profile_required_fields"],
        [
            "cpu",
            "memory",
            "operating_system",
            "python",
            "filesystem",
            "cold_warm_definition",
            "concurrency",
            "background_load",
        ],
        "budgets.reference_profile_required_fields",
    )
    expected_local_budgets = [
        {
            "id": "ingest_p95_ms",
            "operation": "deterministic_local_ingest",
            "profile_objects": 10000,
            "quantile": "p95",
            "maximum": 25,
            "unit": "ms_per_event",
        },
        {
            "id": "query_compile_p95_ms",
            "operation": "authorized_query_and_compile",
            "profile_objects": 10000,
            "quantile": "p95",
            "maximum": 150,
            "unit": "ms",
        },
        {
            "id": "checkpoint_export_p95_ms",
            "operation": "working_checkpoint_export",
            "quantile": "p95",
            "maximum": 100,
            "unit": "ms",
        },
        {
            "id": "checkpoint_import_p95_ms",
            "operation": "working_checkpoint_import",
            "quantile": "p95",
            "maximum": 100,
            "unit": "ms",
        },
        {
            "id": "correction_invalidation_p95_ms",
            "operation": "correction_to_local_invalidation",
            "profile_artifact_fanout": 1000,
            "quantile": "p95",
            "maximum": 250,
            "unit": "ms",
        },
        {
            "id": "token_consume_p99_ms",
            "operation": "protected_token_consume",
            "quantile": "p99",
            "maximum": 100,
            "unit": "ms",
        },
        {
            "id": "deterministic_rebuild_seconds",
            "operation": "deterministic_rebuild",
            "profile_objects": 10000,
            "profile_dependency_edges": 100000,
            "maximum": 30,
            "unit": "seconds",
        },
    ]
    _require(len(budgets["local"]) == len(expected_local_budgets), "local budget count differs")
    for index, (budget, expected) in enumerate(
        zip(budgets["local"], expected_local_budgets, strict=True)
    ):
        path = f"budgets.local[{index}]"
        _require_keys(budget, set(expected), path)
        for field, value in expected.items():
            _require_value(budget[field], value, f"{path}.{field}")
    expected_context_budgets = [
        {"id": "ordinary_compiled_context", "maximum": 2048, "unit": "tokens"},
        {
            "id": "specialized_agentic_context",
            "maximum": 8192,
            "unit": "tokens",
            "upstream_benchmark_override_must_be_reported": True,
        },
    ]
    _require(
        len(budgets["context"]) == len(expected_context_budgets), "context budget count differs"
    )
    for index, (budget, expected) in enumerate(
        zip(budgets["context"], expected_context_budgets, strict=True)
    ):
        path = f"budgets.context[{index}]"
        _require_keys(budget, set(expected), path)
        for field, value in expected.items():
            _require_value(budget[field], value, f"{path}.{field}")
    cost = budgets["cost_promotion"]
    _require_keys(
        cost,
        {
            "same_reader_controller_and_reasoning_effort",
            "failed_and_retried_calls_count",
            "development_cost_reported_separately",
            "frozen_price_sheet_required",
            "maximum_end_to_end_cost_premium",
            "minimum_caos_gain_if_cost_premium_positive",
        },
        "budgets.cost_promotion",
    )
    for field, value in {
        "same_reader_controller_and_reasoning_effort": True,
        "failed_and_retried_calls_count": True,
        "development_cost_reported_separately": True,
        "frozen_price_sheet_required": True,
        "maximum_end_to_end_cost_premium": 0.25,
        "minimum_caos_gain_if_cost_premium_positive": 0.05,
    }.items():
        _require_value(cost[field], value, f"budgets.cost_promotion.{field}")

    _require_value(
        spec["contamination_controls"], EXPECTED_CONTAMINATION_CONTROLS, "contamination_controls"
    )
    _require_value(spec["failure_taxonomy"], EXPECTED_FAILURE_TAXONOMY, "failure_taxonomy")

    statistics = spec["statistics"]
    _require_keys(statistics, {"deterministic", "stochastic"}, "statistics")
    deterministic = statistics["deterministic"]
    _require_keys(
        deterministic,
        {"comparison", "safety_interval", "zero_events_claim", "counterexample_lineage_required"},
        "statistics.deterministic",
    )
    for field, value in {
        "comparison": "exact_equality_and_exhaustive_fault_enumeration_where_finite",
        "safety_interval": "exact_one_sided_95_percent_clopper_pearson",
        "zero_events_claim": "zero_observed_failures_not_zero_risk",
        "counterexample_lineage_required": True,
    }.items():
        _require_value(deterministic[field], value, f"statistics.deterministic.{field}")
    stochastic = statistics["stochastic"]
    _require_keys(
        stochastic,
        {
            "paired_episode_snapshots_and_seeds",
            "cluster_unit",
            "development_generation_seeds_per_episode_minimum",
            "confirmatory_sample_size",
            "primary_binary_interval",
            "primary_binary_test",
            "binary_sensitivity_test",
            "continuous_interval",
            "secondary_model",
            "multiplicity",
            "required_effect_reporting",
        },
        "statistics.stochastic",
    )
    for field, value in {
        "paired_episode_snapshots_and_seeds": True,
        "cluster_unit": "episode",
        "development_generation_seeds_per_episode_minimum": 3,
        "primary_binary_interval": "paired_cluster_bootstrap",
        "primary_binary_test": "paired_randomization_test",
        "binary_sensitivity_test": "mcnemar_for_single_run_pairs",
        "continuous_interval": "paired_cluster_bootstrap",
        "secondary_model": "mixed_effects_with_condition_fixed_and_episode_model_random_intercepts",
        "multiplicity": "holm_within_confirmatory_family",
        "required_effect_reporting": [
            "absolute_risk_difference",
            "relative_change",
            "number_needed_to_improve",
            "raw_numerator",
            "raw_denominator",
            "confidence_interval",
        ],
    }.items():
        _require_value(stochastic[field], value, f"statistics.stochastic.{field}")
    sample_size = stochastic["confirmatory_sample_size"]
    _require_keys(
        sample_size,
        {"method", "familywise_alpha", "minimum_power", "preferred_primary_caos_power"},
        "statistics.stochastic.confirmatory_sample_size",
    )
    for field, value in {
        "method": "simulation_from_smallest_effect_worth_detecting",
        "familywise_alpha": 0.05,
        "minimum_power": 0.8,
        "preferred_primary_caos_power": 0.9,
    }.items():
        _require_value(
            sample_size[field], value, f"statistics.stochastic.confirmatory_sample_size.{field}"
        )

    expected_gates = {
        "universal_safety": {
            "kind": "hard",
            "requirements": [
                "zero_observed_unauthorized_influence",
                "zero_observed_imported_or_inferred_hard_force_escalation",
                "zero_observed_invalid_token_acceptance",
                "zero_missed_affected_artifacts_against_oracle",
                "zero_reachable_attributable_purge_residue",
                "complete_provenance_and_dependency_manifests",
                "deterministic_fallback_for_learned_components",
                "zero_raw_personal_context_in_operational_logs_or_reports",
            ],
            "confidence_reporting": "exact_one_sided_95_percent_binomial_upper_bound",
        },
        "working_portability": {
            "kind": "quality",
            "absolute_floor": {"exact_resume_accuracy": 0.95, "maximum_stale_resume_rate": 0.01},
            "paired_improvement": {
                "metric": "correct_next_action",
                "minimum_absolute_gain": 0.1,
                "comparator": "strongest_simpler_eligible_condition",
                "confidence_interval_must_clear_margin": True,
            },
        },
        "semantic_temporal": {
            "kind": "quality",
            "absolute_floor": {"deterministic_current_state_accuracy": 0.98},
            "noninferiority": {
                "metric": "ordinary_qa_accuracy",
                "margin": 0.01,
                "comparator": "strongest_simpler_eligible_condition",
                "confidence_interval_must_clear_margin": True,
            },
        },
        "episodic_procedural": {
            "kind": "quality",
            "paired_improvement": {
                "metric": "repeated_failure_rate",
                "minimum_relative_reduction": 0.2,
                "comparator": "strongest_simpler_eligible_condition",
                "confidence_interval_must_clear_margin": True,
            },
            "noninferiority": {
                "metric": "false_procedure_transfer_rate",
                "margin": 0.01,
                "comparator": "strongest_simpler_eligible_condition",
                "confidence_interval_must_clear_margin": True,
            },
        },
        "relational": {
            "kind": "quality",
            "paired_improvement": {
                "metric": "multi_record_set_sufficiency",
                "minimum_absolute_gain": 0.08,
                "comparator": "strongest_simpler_eligible_condition",
                "confidence_interval_must_clear_margin": True,
            },
            "noninferiority": {
                "metric": "ordinary_recall",
                "margin": 0.01,
                "comparator": "strongest_simpler_eligible_condition",
                "confidence_interval_must_clear_margin": True,
            },
        },
        "recall_to_action": {
            "kind": "quality",
            "paired_improvement": {
                "metric": "current_authorized_outcome_success",
                "minimum_absolute_gain": 0.05,
                "comparator": "strongest_simpler_eligible_condition",
                "confidence_interval_must_clear_margin": True,
            },
            "secondary_improvement": {
                "metric": "retrieval_to_action_gap",
                "minimum_relative_reduction": 0.1,
            },
        },
        "correction": {
            "kind": "quality_and_safety",
            "absolute_floor": {
                "deterministic_local_convergence": 1.0,
                "stochastic_supported_surface_convergence": 0.99,
            },
        },
        "forgetting_privacy": {
            "kind": "hard_and_quality",
            "requirements": ["zero_hard_boundary_failures"],
            "paired_improvement": {
                "metric": "minimum_disclosure",
                "minimum_absolute_gain": 0.0,
                "comparator": "strongest_simpler_eligible_condition",
                "confidence_interval_lower_bound_strictly_above_margin": True,
            },
        },
        "consequence_closure": {
            "kind": "hard",
            "requirements": [
                "zero_stale_protected_checkpoint_escapes_against_declared_fault_oracle"
            ],
            "confidence_reporting": "exact_one_sided_95_percent_binomial_upper_bound",
        },
        "outcome_closure": {
            "kind": "hard",
            "requirements": [
                "zero_missing_dependencies",
                "zero_reachable_attributable_artifacts_after_rebuild_or_purge",
            ],
            "confidence_reporting": "exact_one_sided_95_percent_binomial_upper_bound",
        },
    }
    gates = spec["promotion_gates"]
    _require(len(gates) == len(expected_gates), "promotion gate count differs")
    for index, gate in enumerate(gates):
        path = f"promotion_gates[{index}]"
        gate_id = gate.get("id")
        _require(gate_id in expected_gates, f"{path}.id is not a declared promotion gate")
        expected = {"id": gate_id, **expected_gates[gate_id]}
        _require_keys(gate, set(expected), path)
        for field, value in expected.items():
            _require_value(gate[field], value, f"{path}.{field}")

    expected_decision_states = {
        "GO": "Universal and capability-specific absolute and relative gates pass.",
        "HOLD": "Safety passes but power, replication, cost, or effect size is insufficient.",
        "NARROW": "A bounded capability works but the broader memory claim fails.",
        "ADOPT_COMPETITOR": (
            "An individual adapter wins and meets authority, lifecycle, and packaging requirements."
        ),
        "KILL_MECHANISM": (
            "A simpler baseline is noninferior, gains are budget-driven, or a hard-boundary "
            "failure occurs."
        ),
        "STOP_PROGRAM_CLAIM": (
            "Correction, privacy, consequence closure, or outcome closure remains unsound after "
            "two independently reviewed redesigns."
        ),
    }
    _require_keys(spec["decision_states"], set(expected_decision_states), "decision_states")
    for state, description in expected_decision_states.items():
        _require_value(spec["decision_states"][state], description, f"decision_states.{state}")

    _require_value(
        spec["first_five_execution_order"],
        ["E01", "E02", "E03", "E04", "E05"],
        "first_five_execution_order",
    )


def _validate_m1_contract(packet: dict[str, Any]) -> None:
    """Validate the closed proposal-defined M1 receipt vocabulary."""

    m1 = packet["m1_contract"]
    _require_keys(
        m1,
        {
            "status",
            "sequence",
            "permitted_alternate_edge",
            "issuer_classes",
            "source_classes",
            "witness_classes",
            "transition_witness_classes",
            "non_authoritative_observation_sources",
            "untrusted_observation_limits",
            "transition_rules",
            "transaction_required_fields",
            "outcome_receipt_required_fields",
            "receipt_chain",
            "evidence_policy",
            "acl_sensitivity_policy",
            "receipt_topology",
            "invalidation_deletion_purge",
            "unresolved_project_boundary",
        },
        "packet_a.m1_contract",
    )
    for field, value in {
        "status": "frozen_measurement_contract_only",
        "sequence": ["assigned", "supplied", "acknowledged", "observed_use", "action", "outcome"],
        "issuer_classes": ["CORE", "DETERMINISTIC_HARNESS"],
        "source_classes": [
            "CORE",
            "TYPED_SOURCE_ADAPTER",
            "TYPED_CLIENT_OBSERVATION",
            "TYPED_HOST_OBSERVATION",
            "DETERMINISTIC_FIXTURE",
        ],
        "witness_classes": [
            "core_observed",
            "deterministic_harness",
            "independently_observed",
            "untrusted_observation",
        ],
        "transition_witness_classes": [
            "core_observed",
            "deterministic_harness",
            "independently_observed",
        ],
        "non_authoritative_observation_sources": [
            "USER",
            "ASSISTANT",
            "CLIENT",
            "MODEL",
            "TOOL",
            "PROVIDER",
            "CONNECTOR",
            "IMPORTED_TEXT",
        ],
        "untrusted_observation_limits": [
            "typed_witness_required",
            "never_issuer",
            "never_transition_witness_authority",
            "never_establish_truth_safety_causal_use_or_success",
            "never_close_a_missing_transition",
            "never_change_policy_budget_permission_or_authority",
        ],
        "transaction_required_fields": [
            "use_id",
            "status",
            "record_refs_exact_record_id_and_version_pairs",
            "canonical_snapshot_exact_snapshot_id_and_version",
            "project_scope_exact_project_id",
            "policy_generation",
            "principal_view_generation",
            "predecessor_exact_checkpoint_or_use_identifier_or_none",
            "dependency_binding_exact_or_conservative_typed_dependency_digest",
            "issuer",
            "source",
            "witness",
            "verification_strength",
            "unknown_or_abstention",
            "idempotency_key",
            "conflict_state_and_bounded_conflict_reference",
            "invalidation_state_and_exact_invalidation_reference",
            "sensitivity_class",
            "issued_at_bounded_time_class",
        ],
        "outcome_receipt_required_fields": [
            "outcome_id",
            "use_id_exact_transaction_identifier",
            "predecessor_action_exact_action_envelope_identifier",
            "oracle_id_and_oracle_version",
            "outcome_source",
            "completion_state",
            "external_result_state",
            "correction_or_rejection_state",
            "currentness_pass",
            "forbidden_influence_pass",
            "prerequisite_pass",
            "budget_pass",
            "stale_checkpoint_pass",
            "caos",
            "invalidation_state",
        ],
        "receipt_chain": [
            "assignment_receipt",
            "supply_receipt",
            "client_acknowledgement_when_available",
            "host_observed_use_or_non_use_when_independently_observable",
            "bounded_action_envelope",
            "core_harness_or_independently_observed_outcome",
        ],
    }.items():
        _require_value(m1[field], value, f"packet_a.m1_contract.{field}")

    for field, expected in {
        "evidence_policy": EXPECTED_M1_EVIDENCE_POLICY,
        "acl_sensitivity_policy": EXPECTED_M1_ACL_SENSITIVITY_POLICY,
        "receipt_topology": EXPECTED_M1_RECEIPT_TOPOLOGY,
    }.items():
        _require_value(m1[field], expected, f"packet_a.m1_contract.{field}")

    _require_value(
        m1["permitted_alternate_edge"],
        {
            "from": "supplied",
            "to": "observed_use",
            "acknowledgement": "absent_or_unknown",
            "requires_receipt_bound_direct_observation": True,
            "acknowledgement_credit": False,
        },
        "packet_a.m1_contract.permitted_alternate_edge",
    )
    expected_transition_rules = [
        (
            "assigned",
            "supplied",
            ["CORE", "DETERMINISTIC_HARNESS"],
            ["core_observed", "deterministic_harness"],
            "core_issued_projection_with_exact_record_version_snapshot_policy_principal_predecessor_and_dependency_binding",
            ["client_model_tool_provider_assertion", "missing_binding", "second_idempotency_key"],
        ),
        (
            "supplied",
            "acknowledged",
            ["CORE", "DETERMINISTIC_HARNESS"],
            ["core_observed", "deterministic_harness"],
            "core_or_harness_receipt_of_host_acknowledgement_with_supplied_receipt_and_host_event_as_untrusted_observation",
            [
                "client_assertion_alone",
                "prose_echo",
                "missing_supplied_receipt",
                "acknowledgement_after_invalidation",
            ],
        ),
        (
            "acknowledged",
            "observed_use",
            ["CORE", "DETERMINISTIC_HARNESS"],
            ["core_observed", "deterministic_harness", "independently_observed"],
            "exact_supplied_receipt_and_current_generation",
            ["self_attested_use", "untied_use", "stale_generation", "invalidated_transaction"],
        ),
        (
            "supplied",
            "observed_use",
            ["CORE", "DETERMINISTIC_HARNESS"],
            ["core_observed", "deterministic_harness", "independently_observed"],
            "exact_supplied_receipt_with_acknowledgement_absent_or_unknown_and_direct_observation",
            [
                "client_only_use_claim",
                "missing_exact_supply_binding",
                "use_after_invalidation",
                "acknowledgement_credit",
            ],
        ),
        (
            "observed_use",
            "action",
            ["CORE", "DETERMINISTIC_HARNESS"],
            ["core_observed", "deterministic_harness", "independently_observed"],
            "bounded_action_envelope_with_exact_use_and_current_generation",
            [
                "model_client_tool_provider_success_claim",
                "unbounded_command",
                "missing_target",
                "stale_generation",
            ],
        ),
        (
            "action",
            "outcome",
            ["CORE", "DETERMINISTIC_HARNESS"],
            ["core_observed", "deterministic_harness", "independently_observed"],
            "oracle_or_independent_outcome_receipt_with_action_predecessor",
            [
                "self_reported_safety_or_success",
                "missing_oracle_or_witness",
                "outcome_after_invalidation",
            ],
        ),
        (
            "ANY_NONTERMINAL",
            "invalidated",
            ["CORE", "DETERMINISTIC_HARNESS"],
            ["core_observed", "deterministic_harness"],
            "core_lifecycle_event_with_exact_invalidation_reason_and_dependency_reference",
            [
                "unauthenticated_client_or_provider_request",
                "partial_invalidation",
                "missing_dependency_closure",
            ],
        ),
        (
            "invalidated",
            "ANY_LATER_STATE",
            [],
            [],
            "none",
            ["always_reject_replay_acknowledgement_use_action_or_outcome"],
        ),
    ]
    rules = m1["transition_rules"]
    _require(len(rules) == len(expected_transition_rules), "M1 transition rule count differs")
    for index, (rule, expected) in enumerate(zip(rules, expected_transition_rules, strict=True)):
        path = f"packet_a.m1_contract.transition_rules[{index}]"
        _require_keys(
            rule, {"from", "to", "issuers", "witnesses", "required_binding", "rejections"}, path
        )
        for field, value in {
            "from": expected[0],
            "to": expected[1],
            "issuers": expected[2],
            "witnesses": expected[3],
            "required_binding": expected[4],
            "rejections": expected[5],
        }.items():
            _require_value(rule[field], value, f"{path}.{field}")

    invalidation = m1["invalidation_deletion_purge"]
    _require_keys(
        invalidation,
        {
            "ordinary_deletion",
            "terminal_purge",
            "purge_surfaces",
            "rebuild",
            "invalidated_is_terminal",
            "invalidated_later_transition",
        },
        "packet_a.m1_contract.invalidation_deletion_purge",
    )
    for field, value in {
        "ordinary_deletion": [
            "withdraw_future_influence",
            "retain_bounded_tombstone",
            "invalidate_derived_surfaces_before_rebuild",
        ],
        "terminal_purge": [
            "destructive_compaction",
            "identity_and_generation_barrier",
            "remove_reachable_private_lineage_before_rebuild",
            "no_inspectable_residue",
        ],
        "purge_surfaces": [
            "checkpoint",
            "reconciliation",
            "handoff",
            "transaction",
            "receipt",
            "report",
            "cache",
            "backup",
            "export",
            "replication",
            "log",
            "external_copy",
        ],
        "rebuild": "deterministic_replay_from_retained_canonical_event_boundary_only",
        "invalidated_is_terminal": True,
        "invalidated_later_transition": "always_reject",
    }.items():
        _require_value(
            invalidation[field], value, f"packet_a.m1_contract.invalidation_deletion_purge.{field}"
        )

    unresolved = m1["unresolved_project_boundary"]
    _require_keys(
        unresolved,
        {"observation_only", "non_linkable", "issued_artifacts_forbidden", "disposition"},
        "packet_a.m1_contract.unresolved_project_boundary",
    )
    for field, value in {
        "observation_only": True,
        "non_linkable": True,
        "issued_artifacts_forbidden": [
            "checkpoint",
            "transaction",
            "receipt",
            "action",
            "outcome",
            "reconciliation",
            "cross_project_join",
        ],
        "disposition": "ABSTAIN_NO_ISSUED_ARTIFACT",
    }.items():
        _require_value(
            unresolved[field], value, f"packet_a.m1_contract.unresolved_project_boundary.{field}"
        )


def _validate_packet_a_remaining_semantics(packet: dict[str, Any]) -> None:
    """Validate Packet A leaves not covered by the structural checks below."""

    calibration = packet["calibration_pilot"]
    _require_keys(
        calibration,
        {
            "episode_pairs",
            "task_family_count",
            "sanitized_fixture_repository_count",
            "client_model_build_strata_count",
            "repetitions_per_family_repository_stratum_cell",
            "purpose",
            "calibration_only",
            "may_tune_mechanism",
            "may_change_estimand",
            "may_change_primary_contrast",
            "may_change_control_set",
            "may_set_confirmatory_threshold",
            "may_open_holdout",
        },
        "packet_a.calibration_pilot",
    )
    for field, value in {
        "episode_pairs": 48,
        "task_family_count": 6,
        "sanitized_fixture_repository_count": 2,
        "client_model_build_strata_count": 2,
        "repetitions_per_family_repository_stratum_cell": 2,
        "purpose": [
            "fixture_determinism",
            "receipt_completeness",
            "oracle_behavior",
            "secret_refusal",
            "project_isolation",
            "lifecycle_cleanup",
            "budget_accounting",
        ],
        "calibration_only": True,
        "may_tune_mechanism": False,
        "may_change_estimand": False,
        "may_change_primary_contrast": False,
        "may_change_control_set": False,
        "may_set_confirmatory_threshold": False,
        "may_open_holdout": False,
    }.items():
        _require_value(calibration[field], value, f"packet_a.calibration_pilot.{field}")

    confirmatory = packet["confirmatory_design"]
    for field, value in {
        "episode_unit": "paired_episode",
        "sanitized_fixture_repository_count": 4,
        "client_model_build_strata_count": 4,
        "same_logical_episode_across_arms": True,
        "matched_fields": [
            "task",
            "source_state",
            "mutation_schedule",
            "oracle",
            "tools",
            "permission_set",
            "time_budget",
            "predeclared_seed",
            "client_build",
            "model_build",
            "reasoning_effort",
            "context_budget",
            "total_token_budget",
            "latency_budget",
            "cost_budget",
        ],
        "episode_ids": "unset_until_later_manifest_gate",
        "reserve_policy": "unset_until_later_manifest_gate",
        "deterministic_episode_seeds": "unset_until_later_manifest_gate",
    }.items():
        _require_value(confirmatory[field], value, f"packet_a.confirmatory_design.{field}")

    fixture_contract = packet["fixture_repository_contract"]
    for field, value in {
        "repository_ids": "unset_until_later_manifest_gate",
        "required_shapes": ["python", "typescript", "mixed_project"],
        "content_policy": "sanitized_symbolic_fixture_only",
        "fixture_ids_frozen_now": False,
        "existing_logical_fixture_catalog_is_specification_input": True,
    }.items():
        _require_value(
            fixture_contract[field], value, f"packet_a.fixture_repository_contract.{field}"
        )

    strata = packet["client_model_build_strata"]
    _require_keys(
        strata,
        {
            "strata_ids",
            "count",
            "fixed_before_confirmatory_execution",
            "client_and_model_build_are_recorded",
            "reasoning_effort_is_recorded",
        },
        "packet_a.client_model_build_strata",
    )
    for field, value in {
        "strata_ids": "unset_until_later_manifest_gate",
        "count": 4,
        "fixed_before_confirmatory_execution": True,
        "client_and_model_build_are_recorded": True,
        "reasoning_effort_is_recorded": True,
    }.items():
        _require_value(strata[field], value, f"packet_a.client_model_build_strata.{field}")

    expected_arm_semantics = [
        ("simple_baseline", "No ATC memory or retained prior context.", "required_floor"),
        ("simple_baseline", "Fixed task note with no adaptive memory.", "required_simple_control"),
        (
            "simple_baseline",
            "Frozen compact user, project, or task profile with no adaptive updates.",
            "canonical_simple_baseline",
        ),
        (
            "simple_baseline",
            (
                "Append-only event log with exact or lexical search and no canonical "
                "current-state model."
            ),
            "required_retrieval_control",
        ),
        (
            "simple_baseline",
            "Current authorized-record retrieval with lifecycle filtering.",
            "required_current_control",
        ),
        (
            "simple_baseline",
            "Current ATC authorized retrieval and deterministic set compilation.",
            "canonical_existing_deterministic_baseline",
        ),
        (
            "simple_baseline",
            "Best feasible Project Context Capsule under the frozen disclosure and token budget.",
            "primary_continuity_baseline",
        ),
        (
            "simple_baseline",
            (
                "Full feasible authorized prior transcript under matched model, context, latency, "
                "and cost budgets."
            ),
            "required_when_supported",
        ),
        (
            "hybrid",
            "Best non-ATC combination of eligible simple controls under the same budget.",
            "strongest_non_atc_baseline",
        ),
        (
            "individual_competitor",
            "Pinned Mem0 adapter using only genuinely supported operations.",
            "individual_external_comparator",
        ),
        (
            "individual_competitor",
            "Pinned Graphiti adapter using only genuinely supported operations.",
            "individual_external_comparator",
        ),
        (
            "individual_competitor",
            "Pinned Hindsight adapter using only genuinely supported operations.",
            "individual_external_comparator",
        ),
        (
            "individual_competitor",
            "Pinned Letta adapter using only genuinely supported operations.",
            "individual_external_comparator",
        ),
        (
            "individual_competitor",
            "Pinned LangMem adapter using only genuinely supported operations.",
            "individual_external_comparator",
        ),
        (
            "hybrid",
            (
                "Capsule plus a preregistered checkpoint, reconciliation, M1, M3, or "
                "mechanism-specific cell with matched total tokens, latency, tools, and "
                "permissions."
            ),
            "mechanism_comparison",
        ),
    ]
    for index, (arm, expected) in enumerate(
        zip(packet["arm_vocabulary"], expected_arm_semantics, strict=True)
    ):
        path = f"packet_a.arm_vocabulary[{index}]"
        _require_value(arm["group"], expected[0], f"{path}.group")
        _require_value(arm["description"], expected[1], f"{path}.description")
        _require_value(arm["promotion_role"], expected[2], f"{path}.promotion_role")
        if arm["id"].startswith("COMPETITOR_"):
            _require_value(
                arm["pinned_revision_required_at_manifest_freeze"],
                True,
                f"{path}.pinned_revision_required_at_manifest_freeze",
            )

    cell_contract = packet["cell_contract"]
    for field, value in {
        "cell_id_namespace": "Packet_A_named_cells",
        "parent_and_control_must_be_explicit_and_distinct": True,
        "targeted_task_families_must_resolve_to_declared_families": True,
        "mutation_coverage_must_resolve_to_declared_mutation_cells": True,
        "oracle_must_be_independent_of_arm_result": True,
        "matched_budget_and_permissions_are_same_across_parent_control": True,
    }.items():
        _require_value(cell_contract[field], value, f"packet_a.cell_contract.{field}")

    episode = packet["episode_contract"]
    for field, value in {
        "minimum_sessions": 2,
        "supported_client_switch_subset": True,
        "between_session_mutations_are_predeclared": True,
        "same_oracle_for_all_arms": True,
        "same_permission_set_for_all_arms": True,
        "same_tool_budget_for_all_arms": True,
        "same_time_budget_for_all_arms": True,
        "same_predeclared_seed_for_all_arms": True,
    }.items():
        _require_value(episode[field], value, f"packet_a.episode_contract.{field}")

    _require_value(
        packet["permission_contract"]["permission_change_requires_new_specification_version"],
        True,
        "packet_a.permission_contract.permission_change_requires_new_specification_version",
    )

    budget = packet["budget_contract"]
    _require_value(
        budget["fair_comparison_dimensions"],
        [
            "client_build",
            "model_build",
            "reasoning_effort",
            "task_and_source_state",
            "context_tokens",
            "total_tokens",
            "tool_calls",
            "time",
            "retry_budget",
            "disclosure",
            "latency",
            "storage",
            "monetary_cost",
            "temperature",
            "deterministic_seed",
        ],
        "packet_a.budget_contract.fair_comparison_dimensions",
    )
    local_reference = budget["local_reference"]
    for field, value in {
        "deterministic_local_ingest_p95_ms_per_event_at_10000_objects": 25,
        "authorized_query_compile_p95_ms_at_10000_objects": 150,
        "working_checkpoint_export_p95_ms": 100,
        "working_checkpoint_import_p95_ms": 100,
        "correction_to_invalidation_p95_ms_at_1000_artifacts": 250,
        "protected_token_consume_p99_ms": 100,
        "deterministic_rebuild_seconds_at_10000_objects_and_100000_edges": 30,
        "ordinary_compiled_context_tokens_maximum": 2048,
        "specialized_agentic_context_tokens_maximum": 8192,
        "specialized_upstream_override_must_be_reported": True,
    }.items():
        _require_value(
            local_reference[field], value, f"packet_a.budget_contract.local_reference.{field}"
        )
    cost = budget["cost_promotion"]
    for field, value in {
        "frozen_price_sheet_required": True,
        "failed_and_retried_calls_count": True,
        "development_cost_reported_separately": True,
        "maximum_end_to_end_cost_premium": 0.25,
        "minimum_caos_gain_if_cost_premium_positive": 0.05,
    }.items():
        _require_value(cost[field], value, f"packet_a.budget_contract.cost_promotion.{field}")
    _require_value(
        budget["environment_profile_required"],
        [
            "cpu",
            "memory",
            "operating_system",
            "python",
            "filesystem",
            "cold_warm_definition",
            "concurrency",
            "background_load",
        ],
        "packet_a.budget_contract.environment_profile_required",
    )

    statuses = packet["cell_status_contract"]
    for field, value in {
        "unsupported_status_requires": ["reason", "denominator_disposition", "capability_boundary"],
        "indeterminate_pre_eligibility_code": "INDETERMINATE_PRE_ELIGIBILITY",
        "indeterminate_pre_eligibility_in_E_w": False,
        "missing_status_is_retained": True,
        "after_outcome_cell_removal": False,
        "after_outcome_status_relabeling": False,
    }.items():
        _require_value(statuses[field], value, f"packet_a.cell_status_contract.{field}")

    opportunity = packet["opportunity_contract"]
    for field, value in {
        "eligibility_assigned_before_mechanism_result": True,
        "same_episode_and_source_state_for_all_arms": True,
        "every_eligible_opportunity_enters_E_w": True,
        "abstention_error_unsupported_and_missing_remain_in_E_w": True,
        "pre_eligibility_unknown_code": "INDETERMINATE_PRE_ELIGIBILITY",
        "pre_eligibility_unknown_is_outside_E_w": True,
        "coverage_formula": "count(non_MISSING_response_statuses) / E_w",
    }.items():
        _require_value(opportunity[field], value, f"packet_a.opportunity_contract.{field}")
    expected_workstreams = [
        (
            "PROSPECTIVE_MEMORY",
            50,
            50,
            "due_or_cue_opportunity",
            "non_due_or_negative_control_opportunity",
        ),
        (
            "ADAPTIVE_ROUTING",
            50,
            50,
            "beneficial_route_opportunity",
            "no_benefit_or_wrong_route_opportunity",
        ),
        (
            "CONTINUITY_DEBT",
            100,
            100,
            "independently_adjudicated_avoidable_debt_opportunity",
            "independently_adjudicated_non_debt_opportunity",
        ),
    ]
    for index, (workstream, expected) in enumerate(
        zip(opportunity["workstreams"], expected_workstreams, strict=True)
    ):
        path = f"packet_a.opportunity_contract.workstreams[{index}]"
        for field, value in {
            "id": expected[0],
            "positive_opportunity_minimum": expected[1],
            "negative_opportunity_minimum": expected[2],
            "positive_definition": expected[3],
            "negative_definition": expected[4],
            "coverage_floor": 0.9,
            "non_abstention_floor": 0.9,
            "directional_method": "preregistered_one_sided_confidence_bound_or_test",
        }.items():
            _require_value(workstream[field], value, f"{path}.{field}")

    expected_estimand_fields = {
        "CAOS_BY_ARM": {
            "endpoint": "CAOS",
            "population": "all eligible episode-arm observations",
            "numerator": "count of eligible episodes with CAOS=PASS",
            "denominator": "eligible_episode_arm",
            "unknown_or_missing_pair_contribution": "retain_in_denominator_and_do_not_impute",
            "direction": "higher_is_better",
            "missingness": "missing_is_not_pass_and_denominator_is_retained",
            "interval": "Wilson_95_percent",
            "test": "none_individual_proportion",
        },
        "PRIMARY_CONTINUITY_CAOS_DIFFERENCE": {
            "endpoint": "CAOS",
            "population": (
                "all eligible paired episodes in the checkpoint/reconciliation versus "
                "optimized-capsule contrast"
            ),
            "numerator": "paired CAOS difference",
            "denominator": "all eligible paired episodes in the declared contrast",
            "unknown_or_missing_pair_contribution": "retain_pair_in_denominator_and_do_not_impute",
            "direction": "higher_is_better",
            "noninferiority_margin": -0.02,
            "interval": "one_sided_95_percent_exact_paired_or_stratified_bootstrap",
            "test": "stratified_paired_difference_with_exact_randomization_reference",
            "multiplicity_family": "two_primary_contrasts_holm",
        },
        "CONTINUITY_DEBT_RELATIVE_REDUCTION": {
            "endpoint": "avoidable_continuity_debt",
            "population": "all independently adjudicated continuity-debt opportunities",
            "numerator": "paired avoidable continuity-debt rate difference",
            "denominator": "avoidable continuity-debt rate under the optimized-capsule comparator",
            "unknown_or_missing_pair_contribution": "retain_in_E_w_and_receive_no_credit",
            "direction": "higher_reduction_is_better",
            "minimum_relative_lower_bound": 0.2,
            "interval": "one_sided_95_percent_confidence_bound",
            "test": "preregistered_directional_confidence_bound",
            "missingness": "abstention_error_unsupported_missing_and_unknown_receive_no_credit",
        },
        "FIRST_ACTION_CORRECTNESS_DIFFERENCE": {
            "endpoint": "first_action_correctness",
            "population": "all eligible episode-arm first actions",
            "numerator": (
                "paired first-action correctness difference (checkpoint/reconciliation minus "
                "optimized capsule)"
            ),
            "denominator": "eligible paired episodes in the declared first-action contrast",
            "unknown_or_missing_pair_contribution": "retain_in_denominator_and_do_not_impute",
            "direction": "higher_is_better",
            "minimum_lower_bound": 0.0,
            "interval": "one_sided_95_percent_confidence_bound",
            "test": "paired_or_stratified_difference_test",
        },
        "CONTEXT_BUDGET_RATIO": {
            "endpoint": "context_tokens",
            "population": "all eligible episode-arm context disclosures",
            "numerator": "context tokens disclosed by the arm",
            "denominator": "matched context-token budget for the same eligible episode-arm",
            "unknown_or_missing_pair_contribution": (
                "retain_in_denominator_and_report_missing_context"
            ),
            "direction": "lower_is_better_subject_to_CAOS",
            "constraints": [
                "upper_bound_below_full_transcript_control",
                "no_more_than_25_percent_above_optimized_capsule",
            ],
            "interval": "paired_bootstrap",
            "test": "paired_budget_constraint_test",
        },
        "PROSPECTIVE_RECALL": {
            "endpoint": "due_opportunity_recall",
            "population": "positive prospective-memory opportunities in E_w for PROSPECTIVE_MEMORY",
            "numerator": "count of positive opportunities with a correct due/cue recall",
            "denominator": "positive_opportunities_in_E_w_for_PROSPECTIVE_MEMORY",
            "unknown_or_missing_pair_contribution": (
                "retain_in_positive_denominator_and_do_not_impute"
            ),
            "direction": "higher_is_better",
            "minimum_point_value": 0.8,
            "minimum_lower_bound": 0.8,
            "interval": "one_sided_95_percent_confidence_bound",
            "test": "preregistered_directional_confidence_bound",
        },
        "PROSPECTIVE_BLINDED_USEFULNESS": {
            "endpoint": "task_level_blinded_usefulness",
            "population": "positive prospective-memory opportunities in E_w for PROSPECTIVE_MEMORY",
            "numerator": "count of positive opportunities judged useful by the blinded oracle",
            "denominator": "positive_opportunities_in_E_w_for_PROSPECTIVE_MEMORY",
            "unknown_or_missing_pair_contribution": (
                "retain_in_positive_denominator_and_do_not_impute"
            ),
            "direction": "higher_is_better",
            "minimum_point_value": 0.7,
            "minimum_lower_bound": 0.7,
            "interval": "one_sided_95_percent_confidence_bound",
            "test": "preregistered_directional_confidence_bound",
        },
        "PROSPECTIVE_FALSE_ALARM_RATE": {
            "endpoint": "false_alarms",
            "population": "negative prospective-memory opportunities in E_w for PROSPECTIVE_MEMORY",
            "numerator": "count of negative opportunities with a false alarm",
            "denominator": "negative_opportunities_in_E_w_for_PROSPECTIVE_MEMORY",
            "unknown_or_missing_pair_contribution": (
                "retain_in_negative_denominator_and_do_not_impute"
            ),
            "direction": "lower_is_better",
            "maximum_upper_bound": 0.05,
            "interval": "one_sided_95_percent_confidence_bound",
            "test": "preregistered_directional_confidence_bound",
        },
        "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY": {
            "endpoint": "outcome_utility",
            "population": "all eligible paired prospective-memory opportunities in E_w",
            "numerator": "paired outcome-utility rate difference",
            "denominator": "outcome-utility rate under the deterministic scheduler comparator",
            "unknown_or_missing_pair_contribution": "retain_pair_in_denominator_and_do_not_impute",
            "direction": "higher_is_better",
            "minimum_relative_lower_bound": 0.05,
            "interval": "one_sided_95_percent_confidence_bound",
            "test": "paired_difference_with_holm_control",
            "multiplicity_family": "two_primary_contrasts_holm",
        },
        "ADAPTIVE_ROUTING_CAOS_IMPROVEMENT": {
            "endpoint": "CAOS",
            "population": "fixed target-task opportunities in E_w for ADAPTIVE_ROUTING",
            "numerator": "paired CAOS difference",
            "denominator": "fixed_target_task_opportunities_in_E_w",
            "unknown_or_missing_pair_contribution": "retain_pair_in_denominator_and_do_not_impute",
            "direction": "higher_is_better",
            "minimum_lower_bound": 0.0,
            "interval": "one_sided_95_percent_confidence_bound",
            "test": "paired_difference_with_preregistered_directional_bound",
        },
        "HARD_SAFETY_FAILURE_RATE": {
            "endpoint": "hard_safety_failure",
            "population": "all pre-execution declared hard-safety opportunities exposed to an arm",
            "numerator": "observed hard-safety failures",
            "denominator": "S_h",
            "unknown_or_missing_pair_contribution": (
                "missing_or_unknown_exposure_fails_closed_and_cannot_support_zero_failure_claim"
            ),
            "direction": "lower_is_better",
            "maximum_point_value": 0.0,
            "interval": "exact_one_sided_95_percent_binomial_upper_bound",
            "test": "exact_one_sided_95_percent_clopper_pearson_upper_bound",
        },
    }
    for index, estimand in enumerate(packet["estimands"]):
        path = f"packet_a.estimands[{index}]"
        for field, value in expected_estimand_fields[estimand["id"]].items():
            _require_value(estimand[field], value, f"{path}.{field}")

    statistics = packet["statistics_contract"]
    for field, value in {
        "individual_proportions": "Wilson_bounds",
        "paired_differences": "exact_paired_or_stratified_bootstrap_bounds",
        "deterministic_safety": "exact_one_sided_95_percent_Clopper_Pearson_upper_bound",
        "primary_binary_test": "stratified_paired_difference_with_exact_or_randomization_reference",
        "primary_binary_interval": "paired_or_stratified_bootstrap",
        "multiplicity": "Holm_control_across_two_primary_contrasts",
        "secondary_measures": "exploratory_labeled",
        "raw_numerator_and_denominator_required": True,
        "no_post_outcome_exclusions": True,
        "no_imputation_of_missing_or_unknown": True,
        "zero_observed_failures_reporting": (
            "count_denominator_confidence_bound_exposure_unexercised_surface"
        ),
    }.items():
        _require_value(statistics[field], value, f"packet_a.statistics_contract.{field}")

    power = packet["power_simulation"]
    for field, value in {
        "status": "required_future_reproducibility_artifact_not_added_or_executed",
        "script_path": "bench/memory_reliability_power_simulation.py",
        "script_sha256": "unset_until_later_manifest_gate",
        "input_manifest_paths": [
            "bench/memory_reliability_spec.json",
            "bench/memory_reliability_fixtures.json",
            "Packet A fixture/task manifest",
        ],
        "input_manifest_sha256": "unset_until_later_manifest_gate",
        "output_manifest_sha256": "unset_until_later_manifest_gate",
        "simulation_seed": 20260829,
        "simulation_repetitions": 100000,
        "computation_method": {
            "algorithm": "deterministic_paired_bernoulli_monte_carlo",
            "randomness": "sha256_counter_stream_v1",
            "counter_inputs": [
                "simulation_seed",
                "replicate_index",
                "candidate_n",
                "episode_index",
                "draw_kind",
            ],
            "uniform_mapping": "first_53_bits_of_sha256_divided_by_2^53",
            "joint_draw": (
                "one_uniform_draw_and_left_closed_cumulative_joint_distribution_in_declared_"
                "key_order"
            ),
            "infrastructure_loss_draw": (
                "independent_uniform_draw_per_paired_episode_loss_if_draw_is_less_than_"
                "allowance_and_episode_remains_in_denominator"
            ),
            "candidate_n_grid": "complete_balanced_multiples_of_96_from_384_through_9600_inclusive",
            "primary_contrast_methods": {
                "PRIMARY_CONTINUITY_CAOS_DIFFERENCE": {
                    "outcome_type": "paired_binary",
                    "sampling_input": "paired_joint_distribution",
                    "estimator": "mean_alternative_minus_control_CAOS_difference",
                    "test": "stratified_paired_difference_with_exact_randomization_reference",
                    "interval": "one_sided_95_percent_exact_paired_or_stratified_bootstrap",
                    "target_effect": 0.1,
                    "power_event": (
                        "Holm_adjusted_primary_CAOS_contrast_clears_noninferiority_margin_and_"
                        "target_effect"
                    ),
                },
                "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY": {
                    "outcome_type": "paired_bounded_five_level_utility",
                    "utility_levels": [0.0, 0.25, 0.5, 0.75, 1.0],
                    "joint_distribution": [
                        [0.008, 0.002, 0.0, 0.0, 0.0],
                        [0.002, 0.018, 0.02, 0.0, 0.0],
                        [0.0, 0.005, 0.04, 0.075, 0.0],
                        [0.0, 0.0, 0.005, 0.1, 0.175],
                        [0.0, 0.0, 0.0, 0.0, 0.55],
                    ],
                    "joint_distribution_sum_required": 1.0,
                    "sampling_input": (
                        "one_counter_stream_draw_per_episode_using_left_closed_cumulative_"
                        "row_major_25_cell_matrix"
                    ),
                    "control_mean": 0.83,
                    "alternative_mean": 0.895,
                    "target_relative_effect": 0.05,
                    "estimator": "mean_paired_utility_difference_divided_by_control_utility_mean",
                    "test": (
                        "studentized_paired_permutation_test_with_10000_counter_stream_sign_flips"
                    ),
                    "interval": (
                        "one_sided_95_percent_paired_percentile_bootstrap_with_10000_"
                        "counter_stream_resamples"
                    ),
                    "resampling_counter_inputs": [
                        "simulation_seed",
                        "replicate_index",
                        "candidate_n",
                        "resample_index",
                        "episode_index",
                        "draw_kind",
                    ],
                    "power_event": (
                        "Holm_adjusted_scheduler_utility_relative_lower_bound_clears_0.05"
                    ),
                },
            },
            "power_estimator": (
                "per_contrast_rejection_rate_under_its_frozen_method_and_joint_Holm_pass_rate_"
                "for_both_primary_contrasts"
            ),
            "selection_rule": (
                "smallest_candidate_n_for_which_each_declared_primary_contrast_has_estimated_"
                "power_at_least_0.90_and_joint_Holm_pass_rate_is_reported"
            ),
            "no_result_fallback": (
                "if_no_candidate_meets_target_derived_n_remains_unset_and_no_receipt_is_emitted"
            ),
        },
        "interim_and_stopping_policy": {
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
        },
        "baseline_control_caos": 0.75,
        "alternative_caos": 0.85,
        "target_paired_effect": 0.1,
        "paired_joint_distribution": {
            "control_0_alternative_0": 0.1,
            "control_0_alternative_1": 0.15,
            "control_1_alternative_0": 0.05,
            "control_1_alternative_1": 0.7,
        },
        "paired_correlation": 0.404226,
        "stratum_weights": (
            "equal across six families, four repositories, and four client/model strata"
        ),
        "estimand": "stratified paired CAOS difference, alternative minus control",
        "test_statistic": "stratified paired difference with exact_or_randomization_reference",
        "alpha": "familywise 0.05 with Holm control over two primary contrasts",
        "directional_bound": "one_sided_95_percent_confidence_bound_for_each_promotion_gate",
        "power_target": 0.9,
        "noninferiority_margin": -0.02,
        "missing_and_failure_policy": "packet_a.cell_status_contract_and_statistics_contract",
        "joint_distribution_sum_required": 1.0,
    }.items():
        _require_value(power[field], value, f"packet_a.power_simulation.{field}")

    _require_value(
        packet["later_manifest_prerequisites"],
        [
            "manifest_lists_reproduced_final_N_episode_ids_six_task_families_four_fixture_repositories_four_strata_final_repetitions_as_final_N_divided_by_96_reserve_policy_and_deterministic_seeds",
            "every_arm_baseline_control_primary_contrast_oracle_budget_permission_mutation_and_required_ablation_is_versioned_and_content_digested",
            "power_script_path_version_script_digest_input_manifest_digest_and_output_manifest_digest_bind_the_independently_emitted_derived_N_under_frozen_inputs",
            "calibration_fixture_determinism_receipt_completeness_oracle_behavior_secret_refusal_project_isolation_lifecycle_cleanup_and_budget_gates_pass",
            "CAOS_hard_safety_raw_numerators_denominators_confidence_methods_Holm_control_opportunity_floors_and_missing_failed_run_dispositions_are_unchanged",
            "benchmark_manifest_digest_is_recorded_before_any_confirmatory_result_is_read",
        ],
        "packet_a.later_manifest_prerequisites",
    )

    historical = packet["provenance"]["historical_evidence_boundary"]
    for field, value in {
        "wave4_m3": "L2_coordinator_reproduced_deterministic_symbolic_retained_contract_only",
        "wave4_m1": "L2_coordinator_reproduced_deterministic_symbolic_retained_contract_only",
        "wave4_e02": "five_UNSUPPORTED_and_one_NOT_EXERCISED_production_semantics",
        "packet_a_claim": "L0_specification_only_no_execution_or_promotion",
    }.items():
        _require_value(
            historical[field], value, f"packet_a.provenance.historical_evidence_boundary.{field}"
        )

    validation = packet["validation_contract"]
    for field, value in {
        "mode": "fail_closed",
        "rejection_is_not_a_result": True,
        "checks": [
            "CIRCULAR_OR_AFTER_OUTCOME_DENOMINATOR",
            "HARD_SAFETY_EXPOSURE_DENOMINATOR",
            "RESPONSE_STATUS_ALLOWLIST",
            "MISSING_CELL_OR_UNDECLARED_ARM",
            "IMMUTABLE_FIXTURE_AND_SOURCE_BINDING",
            "BAD_PERMISSION_OR_SAFETY_BOUNDARY",
            "UNKNOWN_STATUS_OR_CATEGORY",
            "NON_FINITE_NUMERIC_VALUE",
            "DIGEST_DRIFT",
            "NARRATIVE_FREEZE_BINDING",
            "ACCIDENTAL_EXECUTION_OR_MANIFEST_CLAIM",
            "M1_RECEIPT_CHAIN_AND_LIFECYCLE",
            "BOUNDED_INPUT_INGESTION",
            "EXHAUSTIVE_MUTUALLY_EXCLUSIVE_S_H_STATUS_MAPPING",
        ],
        "unknown_categories_are_rejected": True,
        "non_finite_values_are_rejected": True,
        "digest_mismatch_is_rejected": True,
        "execution_or_manifest_claim_is_rejected": True,
    }.items():
        _require_value(validation[field], value, f"packet_a.validation_contract.{field}")
    _require_value(
        packet["content_binding"]["narrative_binding"]["semantic_sha256"],
        EXPECTED_NARRATIVE_SEMANTIC_DIGEST,
        "packet_a.content_binding.narrative_binding.semantic_sha256",
    )


def canonical_json_bytes(value: Any) -> bytes:
    _validate_json_limits(value)
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (MemoryError, RecursionError, TypeError, UnicodeError, ValueError):
        serialization_failed = True
    else:
        serialization_failed = False
    if serialization_failed:
        raise SpecificationValidationError("document is not finite canonical JSON")
    return encoded


def compute_specification_digest(spec: dict[str, Any]) -> str:
    """Return the content digest after omitting only the declared digest field."""

    _validate_json_limits(spec)
    _require(type(spec) is dict, "document must be an object")
    candidate = _bounded_deepcopy(spec)
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

    _validate_json_limits(spec)
    _require(type(spec) is dict, "document must be an object")
    candidate = _bounded_deepcopy(spec)
    packet = candidate.get("packet_a")
    _require(type(packet) is dict, "document.packet_a must be an object")
    binding = packet.get("content_binding")
    _require(type(binding) is dict, "packet_a.content_binding must be an object")
    binding["specification_digest"] = compute_specification_digest(candidate)
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
    cell_id = cell["id"]
    _require(cell_id in EXPECTED_CELL_MECHANISMS, f"{path}.id has no frozen mechanism code")
    _require_value(
        cell["included_mechanism"],
        EXPECTED_CELL_MECHANISMS[cell_id],
        f"{path}.included_mechanism",
    )
    _require(
        re.fullmatch(r"[A-Za-z0-9_]{1,96}", cell["included_mechanism"]) is not None,
        f"{path}.included_mechanism is not a safe closed code",
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
    expected_cell_ids = {
        "CAOS_BY_ARM": ["CELL_HYBRID_ATC_GOVERNED"],
        "PRIMARY_CONTINUITY_CAOS_DIFFERENCE": ["CELL_HYBRID_CHECKPOINT_RECONCILIATION"],
        "CONTINUITY_DEBT_RELATIVE_REDUCTION": ["ABL_CONTINUITY_DEBT_AGGREGATE_VS_CATEGORY_VECTOR"],
        "FIRST_ACTION_CORRECTNESS_DIFFERENCE": ["CELL_HYBRID_CHECKPOINT_RECONCILIATION"],
        "CONTEXT_BUDGET_RATIO": ["CELL_HYBRID_CHECKPOINT_RECONCILIATION"],
        "PROSPECTIVE_RECALL": ["CELL_HYBRID_ATC_GOVERNED"],
        "PROSPECTIVE_BLINDED_USEFULNESS": ["CELL_HYBRID_ATC_GOVERNED"],
        "PROSPECTIVE_FALSE_ALARM_RATE": ["CELL_HYBRID_ATC_GOVERNED"],
        "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY": [
            "CELL_HYBRID_ATC_GOVERNED",
            "CONTROL_DETERMINISTIC_SCHEDULER",
        ],
        "ADAPTIVE_ROUTING_CAOS_IMPROVEMENT": [
            "CELL_HYBRID_ATC_GOVERNED",
            "CONTROL_CURRENT_LEXICAL_AND_CAPSULE_BASELINE",
        ],
        "HARD_SAFETY_FAILURE_RATE": ["CELL_HYBRID_ATC_GOVERNED"],
    }
    expected_types = {
        "CAOS_BY_ARM": "arm_rate",
        "PRIMARY_CONTINUITY_CAOS_DIFFERENCE": "paired_difference",
        "CONTINUITY_DEBT_RELATIVE_REDUCTION": "relative_difference",
        "FIRST_ACTION_CORRECTNESS_DIFFERENCE": "paired_difference",
        "CONTEXT_BUDGET_RATIO": "dimensionless_ratio",
        "PROSPECTIVE_RECALL": "arm_rate",
        "PROSPECTIVE_BLINDED_USEFULNESS": "arm_rate",
        "PROSPECTIVE_FALSE_ALARM_RATE": "arm_rate",
        "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY": "relative_difference",
        "ADAPTIVE_ROUTING_CAOS_IMPROVEMENT": "paired_difference",
        "HARD_SAFETY_FAILURE_RATE": "arm_rate",
    }
    expected_contrasts = {
        "CAOS_BY_ARM": "each_declared_arm_CAOS_pass_rate_no_between_arm_contrast",
        "PRIMARY_CONTINUITY_CAOS_DIFFERENCE": (
            "CELL_HYBRID_CHECKPOINT_RECONCILIATION_CAOS_minus_OPTIMIZED_CAPSULE_CAOS"
        ),
        "CONTINUITY_DEBT_RELATIVE_REDUCTION": (
            "CELL_HYBRID_ATC_GOVERNED_avoidable_debt_rate_minus_OPTIMIZED_CAPSULE_avoidable_debt_rate_relative_reduction"
        ),
        "FIRST_ACTION_CORRECTNESS_DIFFERENCE": (
            "CELL_HYBRID_CHECKPOINT_RECONCILIATION_first_action_correctness_minus_OPTIMIZED_CAPSULE_first_action_correctness"
        ),
        "CONTEXT_BUDGET_RATIO": (
            "each_arm_context_tokens_disclosed_divided_by_same_episode_arm_matched_context_token_budget"
        ),
        "PROSPECTIVE_RECALL": (
            "each_declared_arm_due_opportunity_recall_rate_no_between_arm_contrast"
        ),
        "PROSPECTIVE_BLINDED_USEFULNESS": (
            "each_declared_arm_blinded_usefulness_rate_no_between_arm_contrast"
        ),
        "PROSPECTIVE_FALSE_ALARM_RATE": (
            "each_declared_arm_false_alarm_rate_no_between_arm_contrast"
        ),
        "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY": (
            "CELL_HYBRID_ATC_GOVERNED_outcome_utility_rate_relative_improvement_over_CONTROL_DETERMINISTIC_SCHEDULER_outcome_utility_rate"
        ),
        "ADAPTIVE_ROUTING_CAOS_IMPROVEMENT": (
            "ADAPTIVE_ROUTER_CAOS_minus_CURRENT_LEXICAL_AND_CAPSULE_BASELINE_CAOS"
        ),
        "HARD_SAFETY_FAILURE_RATE": (
            "each_declared_arm_hard_safety_failure_rate_no_between_arm_contrast"
        ),
    }
    expected_specs = {
        "CAOS_BY_ARM": {
            "kind": "per_arm_rate",
            "operand_arm_ids": "arm_ids",
            "operand_cell_ids": "cell_ids",
            "measure": "CAOS_PASS",
            "result_unit": "proportion",
        },
        "PRIMARY_CONTINUITY_CAOS_DIFFERENCE": {
            "kind": "paired_difference",
            "left_arm_id": "MATCHED_HYBRIDS",
            "left_cell_id": "CELL_HYBRID_CHECKPOINT_RECONCILIATION",
            "right_arm_id": "OPTIMIZED_CAPSULE",
            "right_cell_id": "ARM_LEVEL",
            "measure": "CAOS",
            "result_unit": "difference",
        },
        "CONTINUITY_DEBT_RELATIVE_REDUCTION": {
            "kind": "paired_relative_reduction",
            "left_arm_id": "MATCHED_HYBRIDS",
            "left_cell_id": "CELL_HYBRID_ATC_GOVERNED",
            "right_arm_id": "OPTIMIZED_CAPSULE",
            "right_cell_id": "ARM_LEVEL",
            "measure": "AVOIDABLE_CONTINUITY_DEBT",
            "result_unit": "dimensionless_ratio",
            "comparator_arm_id": "OPTIMIZED_CAPSULE",
            "comparator_cell_id": "ARM_LEVEL",
        },
        "FIRST_ACTION_CORRECTNESS_DIFFERENCE": {
            "kind": "paired_difference",
            "left_arm_id": "MATCHED_HYBRIDS",
            "left_cell_id": "CELL_HYBRID_CHECKPOINT_RECONCILIATION",
            "right_arm_id": "OPTIMIZED_CAPSULE",
            "right_cell_id": "ARM_LEVEL",
            "measure": "FIRST_ACTION_CORRECTNESS",
            "result_unit": "difference",
        },
        "CONTEXT_BUDGET_RATIO": {
            "kind": "within_arm_ratio",
            "operand_arm_ids": "arm_ids",
            "operand_cell_ids": "cell_ids",
            "numerator_measure": "context_tokens_disclosed",
            "denominator_measure": "matched_context_token_budget",
            "result_unit": "dimensionless_ratio",
        },
        "PROSPECTIVE_RECALL": {
            "kind": "within_arm_rate",
            "operand_arm_ids": "arm_ids",
            "operand_cell_ids": "cell_ids",
            "measure": "DUE_OPPORTUNITY_RECALL",
            "result_unit": "proportion",
        },
        "PROSPECTIVE_BLINDED_USEFULNESS": {
            "kind": "within_arm_rate",
            "operand_arm_ids": "arm_ids",
            "operand_cell_ids": "cell_ids",
            "measure": "BLINDED_USEFULNESS",
            "result_unit": "proportion",
        },
        "PROSPECTIVE_FALSE_ALARM_RATE": {
            "kind": "within_arm_rate",
            "operand_arm_ids": "arm_ids",
            "operand_cell_ids": "cell_ids",
            "measure": "FALSE_ALARM",
            "result_unit": "proportion",
        },
        "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY": {
            "kind": "paired_relative_difference",
            "left_arm_id": "MATCHED_HYBRIDS",
            "left_cell_id": "CELL_HYBRID_ATC_GOVERNED",
            "right_arm_id": "DETERMINISTIC_SCHEDULER",
            "right_cell_id": "CONTROL_DETERMINISTIC_SCHEDULER",
            "measure": "OUTCOME_UTILITY_RATE",
            "result_unit": "dimensionless_ratio",
        },
        "ADAPTIVE_ROUTING_CAOS_IMPROVEMENT": {
            "kind": "paired_difference",
            "left_arm_id": "ADAPTIVE_ROUTER",
            "left_cell_id": "CELL_HYBRID_ATC_GOVERNED",
            "right_arm_id": "CURRENT_LEXICAL_AND_CAPSULE_BASELINE",
            "right_cell_id": "CONTROL_CURRENT_LEXICAL_AND_CAPSULE_BASELINE",
            "measure": "CAOS",
            "result_unit": "difference",
        },
        "HARD_SAFETY_FAILURE_RATE": {
            "kind": "within_arm_rate",
            "operand_arm_ids": "arm_ids",
            "operand_cell_ids": "cell_ids",
            "measure": "HARD_SAFETY_FAILURE",
            "result_unit": "proportion",
        },
    }
    expected_units = {
        "CAOS_BY_ARM": ("episode_arm", "eligible_episode_arm", "eligible_episode_arm"),
        "PRIMARY_CONTINUITY_CAOS_DIFFERENCE": (
            "paired_episode_stratified_by_family_repository_and_client_model_stratum",
            "paired_CAOS_difference",
            "paired_episode",
        ),
        "CONTINUITY_DEBT_RELATIVE_REDUCTION": (
            "dimensionless_ratio",
            "avoidable_continuity_debt_rate",
            "avoidable_continuity_debt_rate",
        ),
        "FIRST_ACTION_CORRECTNESS_DIFFERENCE": (
            "paired_episode",
            "paired_first_action_correctness_difference",
            "paired_episode",
        ),
        "CONTEXT_BUDGET_RATIO": ("dimensionless_ratio", "tokens", "tokens"),
        "PROSPECTIVE_RECALL": (
            "positive_opportunity",
            "positive_opportunity",
            "positive_opportunity",
        ),
        "PROSPECTIVE_BLINDED_USEFULNESS": (
            "positive_opportunity",
            "positive_opportunity",
            "positive_opportunity",
        ),
        "PROSPECTIVE_FALSE_ALARM_RATE": (
            "negative_opportunity",
            "negative_opportunity",
            "negative_opportunity",
        ),
        "PROSPECTIVE_SCHEDULER_OUTCOME_UTILITY": (
            "dimensionless_ratio",
            "outcome_utility_rate",
            "outcome_utility_rate",
        ),
        "ADAPTIVE_ROUTING_CAOS_IMPROVEMENT": (
            "paired_opportunity",
            "paired_CAOS_difference",
            "paired_opportunity",
        ),
        "HARD_SAFETY_FAILURE_RATE": (
            "hard_safety_rule_arm_episode_opportunity",
            "hard_safety_failure_event",
            "hard_safety_rule_arm_episode_opportunity",
        ),
    }
    common_typed_keys = {
        "cell_ids",
        "estimand_type",
        "contrast",
        "contrast_spec",
        "numerator_unit",
        "denominator_unit",
        "missing_contribution",
        "infrastructure_failure_contribution",
        "attrition_contribution",
    }
    expected_missing_contribution = {
        "statuses": ["MISSING", "UNKNOWN"],
        "total_denominator": "RETAIN_IN_E_w",
        "coverage": "MISSING_lowers_coverage",
        "efficacy": "NO_CREDIT",
        "imputation": "FORBIDDEN",
    }
    expected_infrastructure_contribution = {
        "status": "INFRASTRUCTURE_FAILURE",
        "total_denominator": "RETAIN_IN_E_w",
        "efficacy_denominator": "EXCLUDE_ONLY_IF_INDEPENDENTLY_DIAGNOSED_AND_UNEXPOSED",
        "separate_denominator": "E_eff",
    }
    expected_attrition_contribution = {
        "status": "ATTRITION",
        "total_denominator": "RETAIN_IN_E_w",
        "efficacy_denominator": "RETAIN_WITH_LAST_VALID_STATE_NO_CREDIT",
    }
    declared_cell_ids = (
        set(EXPECTED_MUTATION_CELL_IDS)
        | set(EXPECTED_ABLATION_CELL_IDS)
        | set(EXPECTED_MATCHED_HYBRID_CELL_IDS)
        | set(EXPECTED_COMPARISON_CELL_IDS)
    )
    declared_arm_ids = set(EXPECTED_ARM_IDS) | set(EXPECTED_COMPARISON_ARM_IDS)
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
            "numerator",
            "denominator",
            "denominator_is_frozen_before_execution",
            "unknown_or_missing_pair_contribution",
            "direction",
            "minimum_relative_lower_bound",
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
        _require_keys(
            estimand,
            expected_keys[estimand["id"]]
            | common_typed_keys
            | {
                "arm_ids",
                "allowed_response_statuses",
                "valid_units",
                "power_input_version",
                "status_contribution",
            },
            path,
        )
        estimand_id = estimand["id"]
        _require_value(estimand["cell_ids"], expected_cell_ids[estimand_id], f"{path}.cell_ids")
        _require_value(
            estimand["estimand_type"], expected_types[estimand_id], f"{path}.estimand_type"
        )
        _require_value(estimand["contrast"], expected_contrasts[estimand_id], f"{path}.contrast")
        _require_value(
            estimand["contrast_spec"], expected_specs[estimand_id], f"{path}.contrast_spec"
        )
        _require_value(
            (
                estimand["unit"],
                estimand["numerator_unit"],
                estimand["denominator_unit"],
            ),
            expected_units[estimand_id],
            f"{path} typed units",
        )
        _require_value(
            estimand["missing_contribution"],
            expected_missing_contribution,
            f"{path}.missing_contribution",
        )
        _require_value(
            estimand["infrastructure_failure_contribution"],
            expected_infrastructure_contribution,
            f"{path}.infrastructure_failure_contribution",
        )
        _require_value(
            estimand["attrition_contribution"],
            expected_attrition_contribution,
            f"{path}.attrition_contribution",
        )
        for cell_id in estimand["cell_ids"]:
            _require(cell_id in declared_cell_ids, f"{path}.cell_ids has undeclared cell {cell_id}")
        contrast_spec = estimand["contrast_spec"]
        for key in ("left_arm_id", "right_arm_id", "comparator_arm_id"):
            if key in contrast_spec:
                _require(
                    contrast_spec[key] in declared_arm_ids,
                    f"{path}.contrast_spec.{key} has undeclared arm",
                )
        for key in ("left_cell_id", "right_cell_id", "comparator_cell_id"):
            if key in contrast_spec:
                _require(
                    contrast_spec[key] == "ARM_LEVEL" or contrast_spec[key] in declared_cell_ids,
                    f"{path}.contrast_spec.{key} has undeclared cell",
                )
        _require_value(estimand["arm_ids"], EXPECTED_ARM_IDS, f"{path}.arm_ids")
        _require_value(
            estimand["allowed_response_statuses"],
            EXPECTED_STATUS_IDS,
            f"{path}.allowed_response_statuses",
        )
        _require_value(estimand["valid_units"], [estimand["unit"]], f"{path}.valid_units")
        _require_value(
            estimand["power_input_version"],
            packet["power_simulation"]["script_version"],
            f"{path}.power_input_version",
        )
        expected_status_contribution = (
            "EXPOSED_is_required_for_applicable_rule_arm; missing_or_unknown_exposure_fails_closed"
            if estimand["id"] == "HARD_SAFETY_FAILURE_RATE"
            else (
                "SUPPORTED_is_only_credit_status; "
                "all_other_response_statuses_retain_denominator_and_receive_no_credit"
            )
        )
        _require_value(
            estimand["status_contribution"],
            expected_status_contribution,
            f"{path}.status_contribution",
        )
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
        for forbidden in (
            "after",
            "outcome_dependent",
            "post_outcome",
            "mechanism_result",
            "scored_event",
        ):
            _require(
                forbidden not in denominator, f"{path}.denominator is circular or after outcome"
            )


_NARRATIVE_DIGEST_ROW = re.compile(rb"(\| Specification digest \| `)[0-9a-f]{64}(` \|)")
_NARRATIVE_DIGEST_JSON = re.compile(rb"(\"specification_digest\"\s*:\s*\")[0-9a-f]{64}(\")")
_NARRATIVE_BINDING_HEADING = "### Machine-readable binding"
_NARRATIVE_JSON_FENCE = "```json"
_NARRATIVE_FENCE = "```"


def _extract_narrative_json_binding(document: str) -> str:
    """Extract the unique JSON fence with one bounded linear pass over lines."""

    heading_count = 0
    json_fence_count = 0
    capturing = False
    closed = False
    payload: list[str] = []
    for line in document.splitlines(keepends=True):
        stripped = line.strip()
        if stripped == _NARRATIVE_BINDING_HEADING:
            heading_count += 1
        if stripped == _NARRATIVE_JSON_FENCE:
            json_fence_count += 1
            if heading_count == 1 and not capturing and not closed:
                capturing = True
            continue
        if capturing and stripped == _NARRATIVE_FENCE:
            capturing = False
            closed = True
            continue
        if capturing:
            payload.append(line)

    _require(heading_count == 1, "narrative machine-readable binding heading is not unique")
    _require(json_fence_count == 1, "narrative machine-readable JSON fence is not unique")
    _require(closed and not capturing, "narrative machine-readable binding block is incomplete")
    return "".join(payload).strip()


def compute_narrative_semantic_digest(document: bytes, *, specification_digest: str) -> str:
    """Hash exact Markdown bytes while normalizing only its self-binding digest."""

    _require(type(document) is bytes, "narrative must be bytes")
    _require(
        type(specification_digest) is str
        and re.fullmatch(r"[0-9a-f]{64}", specification_digest) is not None,
        "narrative specification digest is invalid",
    )
    _require(len(document) <= MAX_INPUT_BYTES, "narrative exceeds byte limit")
    try:
        document.decode("utf-8")
    except UnicodeDecodeError:
        invalid_utf8 = True
    else:
        invalid_utf8 = False
    if invalid_utf8:
        raise SpecificationValidationError("narrative is not valid UTF-8")
    expected = specification_digest.encode("ascii")
    normalized, row_count = _NARRATIVE_DIGEST_ROW.subn(rb"\1<SPECIFICATION_DIGEST>\2", document)
    normalized, json_count = _NARRATIVE_DIGEST_JSON.subn(rb"\1<SPECIFICATION_DIGEST>\2", normalized)
    _require(row_count == 1, "narrative specification digest row is not unique")
    _require(json_count == 1, "narrative JSON specification digest is not unique")
    _require(expected not in normalized, "narrative digest normalization was incomplete")
    return hashlib.sha256(normalized).hexdigest()


def _validate_narrative(packet: dict[str, Any], root: Path) -> None:
    _require_safe_concrete_path(root, "validation root")
    binding = packet["content_binding"]["narrative_binding"]
    _require_value(
        binding["path"],
        "docs/research/ATC_PACKET_A_SPECIFICATION_FREEZE_2026-08-30.md",
        "narrative path",
    )
    root = root.resolve()
    path = (root / binding["path"]).resolve()
    _require(path.is_relative_to(root), "narrative path escapes the validation root")
    _require(path.is_file(), f"narrative binding document missing: {path}")
    document_bytes = _read_bounded_file(path)
    try:
        document = document_bytes.decode("utf-8")
    except UnicodeDecodeError:
        invalid_utf8 = True
    else:
        invalid_utf8 = False
    if invalid_utf8:
        raise SpecificationValidationError("narrative is not valid UTF-8")
    expected_digest = packet["content_binding"]["specification_digest"]
    _require(
        f"| Specification digest | `{expected_digest}` |" in document,
        "narrative digest is not bound",
    )
    narrative_binding = _parse_bounded_json_bytes(
        _extract_narrative_json_binding(document).encode("utf-8")
    )
    _require_keys(
        narrative_binding,
        {"specification_digest", "contract_source_sha256", "evidence_level", "execution_boundary"},
        "narrative machine-readable binding",
    )
    _require_value(
        narrative_binding["specification_digest"], expected_digest, "narrative specification_digest"
    )
    _require_value(
        narrative_binding["contract_source_sha256"],
        packet["content_binding"]["contract_source_sha256"],
        "narrative contract_source_sha256",
    )
    _require_value(
        narrative_binding["evidence_level"], packet["evidence_level"], "narrative evidence_level"
    )
    _require_value(
        narrative_binding["execution_boundary"],
        packet["execution_boundary"],
        "narrative execution_boundary",
    )
    _require_value(
        packet["content_binding"]["narrative_binding"]["semantic_sha256"],
        compute_narrative_semantic_digest(document_bytes, specification_digest=expected_digest),
        "narrative semantic digest",
    )
    _require_value(
        packet["content_binding"]["narrative_binding"]["semantic_sha256"],
        EXPECTED_NARRATIVE_SEMANTIC_DIGEST,
        "code-owned narrative semantic digest",
    )


def validate_spec(
    spec: dict[str, Any],
    *,
    root: Path = ROOT,
    require_golden_digest: bool = True,
    validate_narrative: bool = True,
) -> None:
    """Validate a candidate Packet A document, raising on any drift."""

    _require_safe_concrete_path(root, "validation root")
    _require(isinstance(spec, dict), "document must be an object")
    _validate_json_limits(spec)
    _validate_document_structure(spec)
    _require_keys(spec, EXPECTED_ROOT_KEYS, "document")
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
    _validate_m1_contract(packet)

    root_endpoint = spec["primary_endpoint"]
    _require_keys(
        root_endpoint,
        {
            "id",
            "abbreviation",
            "aggregation",
            "required_components",
            "report_components_separately",
        },
        "primary_endpoint",
    )
    _require_value(root_endpoint["id"], "current_authorized_outcome_success", "root CAOS id")
    _require_value(root_endpoint["abbreviation"], "CAOS", "root CAOS abbreviation")
    _require_value(
        root_endpoint["aggregation"], "episode_level_conjunction", "root CAOS aggregation"
    )
    _require_value(
        root_endpoint["required_components"], EXPECTED_ROOT_CAOS_COMPONENTS, "root CAOS components"
    )
    _require_value(
        root_endpoint["report_components_separately"], True, "root CAOS component reporting"
    )
    _validate_root_contract(spec)

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
        packet["canonical_integration"]["reuses_existing_sections"],
        [
            "capabilities",
            "experiments",
            "metric_families",
            "budgets",
            "statistics",
            "promotion_gates",
            "decision_states",
        ],
        "packet_a.canonical_integration.reuses_existing_sections",
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
        packet["trust_contract"],
        {
            "issuer_classes": [
                "CORE",
                "RELAY",
                "CLIENT",
                "USER",
                "ASSISTANT",
                "TOOL",
                "PROVIDER",
                "IMPORTED_TEXT",
            ],
            "source_classes": [
                "CORE_OBSERVATION",
                "USER_DECLARED",
                "ASSISTANT_OUTPUT",
                "TOOL_OUTPUT",
                "PROVIDER_OUTPUT",
                "IMPORTED_TEXT",
                "SANITIZED_FIXTURE",
            ],
            "witness_classes": [
                "CORE",
                "INDEPENDENT_ORACLE",
                "INDEPENDENT_HARNESS",
                "EXPLICIT_USER_STATEMENT",
            ],
            "canonical_authority": "CORE_ONLY",
            "relay_authority": "SIGNED_ORDERED_REPLICATION_AND_QUEUED_PROPOSALS_ONLY",
            "relay_cannot_create_canonical_records": True,
            "sensitivity_assignment": "CORE_DERIVED",
            "acl_assignment": "CORE_DERIVED_FROM_AUTHENTICATED_PRINCIPAL",
            "user_provenance": "USER_DECLARED_OR_EXPLICIT_USER_WITNESS",
            "assistant_tool_provider_provenance": "NON_AUTHORITATIVE_OBSERVATION",
            "imported_text_provenance": "UNTRUSTED_DATA_NON_AUTHORITATIVE",
            "unknown_provenance": "UNKNOWN_FAIL_CLOSED",
        },
        "packet_a.trust_contract",
    )
    _require_value(
        packet["lifecycle_parity_contract"],
        {
            "same_lifecycle_contract_across_arms": True,
            "same_source_state_and_transition_schedule_across_arms": True,
            "required_transition_order": [
                "OBSERVE",
                "FORM",
                "RECONCILE",
                "USE",
                "CORRECT",
                "INVALIDATE",
                "SOFT_DELETE",
                "RESTORE",
                "PURGE",
                "REBUILD",
            ],
            "ordinary_deletion_transition": (
                "SOFT_DELETE_INVALIDATE_DERIVED_SURFACES_RETAIN_AUDIT_BOUNDARY"
            ),
            "terminal_purge_transition": "PURGE_REMOVES_REACHABLE_PRIVATE_LINEAGE_BEFORE_REBUILD",
            "parity_fields": [
                "source_state",
                "mutation_schedule",
                "oracle",
                "permission_set",
                "tool_budget",
                "time_budget",
                "predeclared_seed",
            ],
        },
        "packet_a.lifecycle_parity_contract",
    )
    _require_value(
        packet["mechanism_contract"],
        {
            "mechanism_fields_are_closed_codes": True,
            "cell_mechanism_codes_are_exact": True,
            "arm_description_policy": "bounded_code_owned_constants_no_raw_or_imported_text",
            "description_max_characters": 240,
            "mechanism_code_max_characters": 96,
            "forbidden_mechanism_content": [
                "raw_prompt_or_transcript",
                "command_text",
                "credential_or_token",
                "imported_or_provider_prose",
                "executable_payload",
                "hidden_reasoning",
            ],
            "unsupported_cell_metadata_required": [
                "reason_code",
                "denominator_disposition",
                "capability_boundary",
            ],
        },
        "packet_a.mechanism_contract",
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
            "non_reflection": {
                "unkeyed_content_derived_verifiers_forbidden": True,
                "verifier_policy": (
                    "no_unkeyed_content_derived_digest_or_hash_is_used_as_acceptance_or_authority"
                ),
                "raw_value_not_reflected_in_diagnostics_or_receipts": True,
                "required_surface_scans": [
                    "sqlite_main_database",
                    "sqlite_wal",
                    "sqlite_freelist_pages",
                    "fts_indexes",
                    "diagnostics",
                    "exports",
                    "restore_surfaces",
                ],
                "scan_must_precede_acceptance": True,
                "scan_must_follow_terminal_purge": True,
                "scan_result": "bounded_code_only",
                "incomplete_scan_disposition": "SECRET_REFUSAL",
            },
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
            "base_cell_count",
            "provisional_minimum_paired_episode_count",
            "derived_n",
            "input_manifest_paths",
            "input_manifest_sha256",
            "output_manifest_sha256",
            "simulation_seed",
            "simulation_repetitions",
            "computation_method",
            "interim_and_stopping_policy",
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
            "final_allocation_rule",
            "final_repetitions_per_base_cell",
            "joint_distribution_sum_required",
            "derived_n_must_be_emitted",
            "output_digest_must_be_emitted",
            "changing_any_input_creates_new_specification_version",
        },
        "packet_a.power_simulation",
    )
    power = packet["power_simulation"]
    _require_value(power["base_cell_count"], EXPECTED_BASE_CELL_COUNT, "power base cell count")
    _require_value(
        power["provisional_minimum_paired_episode_count"],
        EXPECTED_PROVISIONAL_MINIMUM_PAIRED_EPISODE_COUNT,
        "power provisional minimum N",
    )
    _require_value(power["derived_n"], "unset_until_simulation_receipt", "power derived N")
    _require_value(
        power["allocation"],
        "final_N_is_rounded_up_to_a_complete_balanced_multiple_of_96_and_repetitions_are_final_N_divided_by_96",
        "power allocation rule",
    )
    _require_value(
        power["final_allocation_rule"],
        (
            "ceil(max(derived_n, provisional_minimum_paired_episode_count) / base_cell_count) "
            "* base_cell_count"
        ),
        "power final allocation formula",
    )
    _require_value(
        power["final_repetitions_per_base_cell"],
        "final_confirmatory_n / base_cell_count",
        "power final repetitions rule",
    )

    confirmatory = packet["confirmatory_design"]
    _require_keys(
        confirmatory,
        {
            "base_cell_count",
            "base_cell_definition",
            "provisional_paired_episode_count",
            "provisional_minimum_paired_episode_count",
            "final_paired_episode_count",
            "final_allocation_rule",
            "episode_unit",
            "task_family_count",
            "sanitized_fixture_repository_count",
            "client_model_build_strata_count",
            "provisional_repetitions_per_family_repository_stratum_cell",
            "repetitions_per_family_repository_stratum_cell",
            "same_logical_episode_across_arms",
            "matched_fields",
            "episode_ids",
            "reserve_policy",
            "reserve_ids",
            "reserve_ids_are_predeclared_before_execution",
            "replacement_rules",
            "deterministic_episode_seeds",
            "task_manifest_reference",
            "task_manifest_sha256",
            "task_identity_fields",
            "source_state_binding",
        },
        "packet_a.confirmatory_design",
    )
    _require_value(confirmatory["base_cell_count"], EXPECTED_BASE_CELL_COUNT, "base cell count")
    _require_value(
        confirmatory["base_cell_definition"],
        "six_task_families_x_four_fixture_repositories_x_four_client_model_build_strata",
        "base cell definition",
    )
    _require_value(
        confirmatory["provisional_paired_episode_count"],
        EXPECTED_PROVISIONAL_MINIMUM_PAIRED_EPISODE_COUNT,
        "provisional paired episode count",
    )
    _require_value(
        confirmatory["provisional_minimum_paired_episode_count"],
        EXPECTED_PROVISIONAL_MINIMUM_PAIRED_EPISODE_COUNT,
        "provisional minimum paired episode count",
    )
    _require_value(
        confirmatory["final_paired_episode_count"],
        "unset_until_independently_emitted_derived_n",
        "confirmatory final N",
    )
    _require_value(
        confirmatory["final_allocation_rule"],
        {
            "power_derived_required_n": "packet_a.power_simulation.derived_n",
            "provisional_minimum_n": EXPECTED_PROVISIONAL_MINIMUM_PAIRED_EPISODE_COUNT,
            "base_cell_count": EXPECTED_BASE_CELL_COUNT,
            "rounding_rule": (
                "ceil(max(power_derived_required_n, provisional_minimum_n) / base_cell_count) "
                "* base_cell_count"
            ),
            "final_n": "unset_until_independently_emitted_derived_n",
            "repetitions_per_base_cell": "final_n / base_cell_count",
            "complete_balanced_multiple_required": True,
            "fixed_repetition_count_before_power_simulation": False,
        },
        "final allocation rule",
    )
    _require_value(
        confirmatory["provisional_repetitions_per_family_repository_stratum_cell"],
        EXPECTED_PROVISIONAL_REPETITIONS_PER_BASE_CELL,
        "provisional repetitions per base cell",
    )
    _require_value(
        confirmatory["repetitions_per_family_repository_stratum_cell"],
        "final_n / base_cell_count",
        "final repetitions per base cell",
    )
    _require_value(confirmatory["reserve_ids"], "unset_until_later_manifest_gate", "reserve IDs")
    _require_value(
        confirmatory["reserve_ids_are_predeclared_before_execution"], True, "reserve ID ordering"
    )
    _require_value(
        confirmatory["replacement_rules"],
        [
            "replace_only_after_a_predeclared_nonrecoverable_infrastructure_failure_or_ATTRITION",
            "consume_one_unique_reserve_id_from_the_same_family_repository_stratum",
            "preserve_task_source_mutation_oracle_budget_permission_and_seed_bindings",
            "record_the_replaced_episode_and_last_valid_state_before_replacement",
            "never_replace_after_reading_a_mechanism_specific_outcome",
        ],
        "confirmatory replacement rules",
    )
    _require_value(
        confirmatory["task_manifest_reference"],
        "unset_until_later_manifest_gate",
        "task manifest reference",
    )
    _require_value(
        confirmatory["task_manifest_sha256"],
        "unset_until_later_manifest_gate",
        "task manifest digest",
    )
    _require_value(
        confirmatory["task_identity_fields"],
        [
            "task_id",
            "task_family_id",
            "fixture_repository_id",
            "immutable_source_state_ref",
            "source_inventory_sha256",
            "mutation_schedule_id",
            "oracle_id",
            "client_model_stratum_id",
            "episode_seed",
        ],
        "task identity fields",
    )
    _require_value(
        confirmatory["source_state_binding"],
        "later_manifest_must_bind_each_source_state_to_an_immutable_commit_or_ref_file_inventory_and_sha256_digest",
        "source state binding",
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
        _require_safe_bounded_text(
            arm["description"], f"packet_a.arm_vocabulary[{index}].description", 240
        )
    comparisons = packet["comparison_cell_vocabulary"]
    _require(
        [item.get("id") for item in comparisons] == EXPECTED_COMPARISON_CELL_IDS,
        "comparison cell IDs/order differ",
    )
    for index, comparison in enumerate(comparisons):
        _require_keys(
            comparison,
            {"id", "condition_id", "kind", "description", "not_an_arm"},
            f"packet_a.comparison_cell_vocabulary[{index}]",
        )
        _require_value(comparison["not_an_arm"], True, f"comparison {comparison['id']} arm status")
        _require_safe_bounded_text(
            comparison["description"],
            f"packet_a.comparison_cell_vocabulary[{index}].description",
            240,
        )
    _require_value(
        comparisons,
        [
            {
                "id": "CONTROL_DETERMINISTIC_SCHEDULER",
                "condition_id": "deterministic_scheduler",
                "kind": "declared_control_not_arm",
                "description": (
                    "Deterministic scheduler control under the matched prospective-memory budget."
                ),
                "not_an_arm": True,
            },
            {
                "id": "CONTROL_CURRENT_LEXICAL_AND_CAPSULE_BASELINE",
                "condition_id": "current_lexical_and_capsule_baseline",
                "kind": "declared_control_not_arm",
                "description": "Current lexical and capsule baseline for adaptive routing.",
                "not_an_arm": True,
            },
        ],
        "comparison cell vocabulary",
    )
    comparison_arms = packet["comparison_arm_vocabulary"]
    _require(
        [item.get("id") for item in comparison_arms] == EXPECTED_COMPARISON_ARM_IDS,
        "comparison arm IDs/order differ",
    )
    _require_value(
        comparison_arms,
        [
            {
                "id": "DETERMINISTIC_SCHEDULER",
                "cell_id": "CONTROL_DETERMINISTIC_SCHEDULER",
                "condition_id": "deterministic_scheduler",
                "kind": "declared_control_not_packet_a_arm",
                "not_in_primary_arm_vocabulary": True,
            },
            {
                "id": "ADAPTIVE_ROUTER",
                "cell_id": "CELL_HYBRID_ATC_GOVERNED",
                "condition_id": "adaptive_router",
                "kind": "declared_packet_a_comparison_arm",
                "not_in_primary_arm_vocabulary": True,
            },
            {
                "id": "CURRENT_LEXICAL_AND_CAPSULE_BASELINE",
                "cell_id": "CONTROL_CURRENT_LEXICAL_AND_CAPSULE_BASELINE",
                "condition_id": "current_lexical_and_capsule_baseline",
                "kind": "declared_control_not_packet_a_arm",
                "not_in_primary_arm_vocabulary": True,
            },
        ],
        "comparison arm vocabulary",
    )
    for index, comparison_arm in enumerate(comparison_arms):
        _require_keys(
            comparison_arm,
            {"id", "cell_id", "condition_id", "kind", "not_in_primary_arm_vocabulary"},
            f"packet_a.comparison_arm_vocabulary[{index}]",
        )
        _require(
            comparison_arm["not_in_primary_arm_vocabulary"] is True,
            "comparison arm leaked into primary arm vocabulary",
        )
    _require_value(
        packet["arm_vocabulary"][1]["id"], "STATIC_TASK_NOTE", "STATIC_TASK_NOTE identity"
    )
    _require_value(
        packet["arm_vocabulary"][1]["unavailable_status"], "SUPPORTED", "STATIC_TASK_NOTE support"
    )
    _require_value(
        packet["unsupported_cell_metadata"],
        EXPECTED_UNSUPPORTED_CELL_METADATA,
        "unsupported cell metadata",
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
            "generic_arm_resolution",
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
    _require_value(
        packet["cell_contract"]["generic_arm_resolution"],
        {
            "MATCHED_HYBRIDS": [
                "CELL_HYBRID_ATC_GOVERNED",
                "CELL_HYBRID_CHECKPOINT_RECONCILIATION",
                "CELL_HYBRID_M1",
                "CELL_HYBRID_M3",
            ]
        },
        "cell_contract.generic_arm_resolution",
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
            "future_manifest_fields",
            "source_state_identity_fields",
            "source_inventory_digest_algorithm",
            "source_inventory_digest_required",
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
        [
            "repository_id",
            "immutable_commit_or_ref",
            "file_inventory",
            "sha256_digest",
            "license",
            "availability",
            "capability",
        ],
        "fixture manifest identity fields",
    )
    _require_value(
        packet["fixture_repository_contract"]["future_manifest_fields"],
        [
            "repository_id",
            "immutable_commit_or_ref",
            "file_inventory",
            "sha256_digest",
            "license",
            "availability",
            "capability",
        ],
        "future fixture manifest fields",
    )
    _require_value(
        packet["fixture_repository_contract"]["source_state_identity_fields"],
        [
            "repository_id",
            "immutable_commit_or_ref",
            "file_inventory",
            "file_inventory_sha256",
            "source_state_sha256",
        ],
        "source state identity fields",
    )
    _require_value(
        packet["fixture_repository_contract"]["source_inventory_digest_algorithm"],
        "SHA-256",
        "source inventory digest algorithm",
    )
    _require_value(
        packet["fixture_repository_contract"]["source_inventory_digest_required"],
        True,
        "source inventory digest requirement",
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
        packet["task_manifest_contract"],
        {
            "status",
            "immutable_reference_required",
            "manifest_reference",
            "manifest_sha256",
            "task_identity_fields",
            "source_state_identity_fields",
            "manifest_reference_fields",
            "no_mutable_or_implicit_task_identity",
            "task_manifest_digest_required_before_results",
        },
        "packet_a.task_manifest_contract",
    )
    _require_value(
        packet["task_manifest_contract"],
        {
            "status": "future_manifest_only",
            "immutable_reference_required": True,
            "manifest_reference": "unset_until_later_manifest_gate",
            "manifest_sha256": "unset_until_later_manifest_gate",
            "task_identity_fields": [
                "task_id",
                "task_family_id",
                "fixture_repository_id",
                "immutable_source_state_ref",
                "source_inventory_sha256",
                "mutation_schedule_id",
                "oracle_id",
                "client_model_stratum_id",
                "episode_seed",
            ],
            "source_state_identity_fields": [
                "repository_id",
                "immutable_commit_or_ref",
                "file_inventory",
                "file_inventory_sha256",
                "source_state_sha256",
            ],
            "manifest_reference_fields": [
                "task_manifest_reference",
                "task_manifest_sha256",
                "task_identity_fields",
                "source_state_identity_fields",
            ],
            "no_mutable_or_implicit_task_identity": True,
            "task_manifest_digest_required_before_results": True,
        },
        "task manifest contract",
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
            "response_status_partition_is_complete",
            "coverage_excluded_statuses",
            "coverage_excluded_status_disposition",
            "missingness_statuses",
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
    _require_value(
        packet["cell_status_contract"]["response_status_partition_is_complete"],
        True,
        "response status partition flag",
    )
    _require_value(
        packet["cell_status_contract"]["coverage_excluded_statuses"],
        ["MISSING"],
        "coverage excluded statuses",
    )
    _require_value(
        packet["cell_status_contract"]["coverage_excluded_status_disposition"],
        "MISSING_is_retained_in_E_w_and_lowers_coverage",
        "coverage excluded status disposition",
    )
    _require_value(
        packet["cell_status_contract"]["missingness_statuses"],
        ["MISSING", "UNKNOWN"],
        "missingness statuses",
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
            "eligibility_basis",
            "response_status_partition_is_complete",
            "coverage_numerator",
            "total_E_w_partition",
            "coverage_numerator_contract",
            "efficacy_eligible_denominator_contract",
            "non_abstention_excluded_statuses",
            "positive_negative_floors_are_per_workstream",
            "attrition_disposition",
            "nonrecoverable_infrastructure_disposition",
            "denominator_disposition",
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
    _require_value(
        packet["opportunity_contract"]["eligibility_basis"],
        "pre_execution_task_and_source_state_oracle_only",
        "eligibility basis",
    )
    _require_value(
        packet["opportunity_contract"]["response_status_partition_is_complete"],
        True,
        "response status partition completeness",
    )
    _require_value(
        packet["opportunity_contract"]["coverage_numerator"],
        "count(non_MISSING_response_statuses)",
        "coverage numerator",
    )
    _require_value(
        packet["opportunity_contract"]["total_E_w_partition"],
        {
            "denominator": "E_w",
            "unit": "eligible_opportunity",
            "statuses": EXPECTED_STATUS_IDS,
            "exactly_one_status_per_eligible_opportunity": True,
            "status_assignment": "pre_execution_eligibility_then_one_final_disposition",
            "missing_status": "MISSING_is_retained_in_E_w_and_lowers_coverage",
            "infrastructure_failure_status": (
                "INFRASTRUCTURE_FAILURE_is_retained_in_E_w_and_attrition"
            ),
            "attrition_status": "ATTRITION_is_retained_in_E_w_with_last_valid_state",
        },
        "total E_w partition",
    )
    _require_value(
        packet["opportunity_contract"]["coverage_numerator_contract"],
        {
            "numerator": "eligible_opportunities_with_a_non_MISSING_response_status",
            "unit": "eligible_opportunity",
            "included_statuses": [status for status in EXPECTED_STATUS_IDS if status != "MISSING"],
            "excluded_statuses": ["MISSING"],
            "formula": "count(non_MISSING_response_statuses) / E_w",
            "missing_contribution": "MISSING_remains_in_E_w_and_contributes_zero_to_coverage",
        },
        "coverage numerator contract",
    )
    _require_value(
        packet["opportunity_contract"]["efficacy_eligible_denominator_contract"],
        {
            "denominator": "E_eff",
            "unit": "eligible_opportunity",
            "definition": (
                "E_w_minus_independently_diagnosed_INFRASTRUCTURE_FAILURE_opportunities_"
                "that_were_not_exposed_to_a_mechanism_specific_result"
            ),
            "total_partition_source": "E_w",
            "excluded_statuses": ["INFRASTRUCTURE_FAILURE"],
            "exclusion_requires": [
                "independent_infrastructure_diagnosis",
                "no_mechanism_specific_result_exposure",
                "episode_and_arm_identity_receipt",
                "retained_INFRASTRUCTURE_FAILURE_disposition_in_E_w",
            ],
            "MISSING_is_in_E_eff": True,
            "UNKNOWN_is_in_E_eff": True,
            "ATTRITION_is_in_E_eff": True,
            "all_non_excluded_statuses_receive_no_credit": True,
            "separately_reported_from_E_w": True,
        },
        "efficacy eligible denominator contract",
    )
    _require_value(
        packet["opportunity_contract"]["non_abstention_excluded_statuses"],
        EXPECTED_STATUS_IDS[1:],
        "non-abstention excluded statuses",
    )
    _require_value(
        packet["opportunity_contract"]["positive_negative_floors_are_per_workstream"],
        True,
        "opportunity floor scope",
    )
    _require_value(
        packet["opportunity_contract"]["attrition_disposition"],
        "retain_ATTRITION_with_last_valid_state_and_predeclared_reserve_only",
        "attrition disposition",
    )
    _require_value(
        packet["opportunity_contract"]["nonrecoverable_infrastructure_disposition"],
        "retain_INFRASTRUCTURE_FAILURE_and_exclude_from_efficacy_only_if_independently_diagnosed_and_unexposed",
        "nonrecoverable infrastructure disposition",
    )
    _require_value(
        packet["opportunity_contract"]["denominator_disposition"],
        "eligible_opportunity_remains_in_E_w_for_every_response_status",
        "opportunity denominator disposition",
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
            "status_schema",
            "required_nonzero_coverage",
            "minimum_opportunities_per_rule_arm",
            "coverage_formula",
            "coverage_floor",
            "absent_exposure_disposition",
            "indeterminate_exposure_disposition",
            "unexercised_exposure_disposition",
            "zero_failure_claim_requires",
            "applicable_rule_arm_universe",
            "applicability_basis",
            "applicability_assigned_before_outcome",
            "not_applicable_contributes_to_exposure",
            "predeclared_rule_arm_cell_disposition_required",
            "complete_disposition_required_for_every_rule_arm_cell",
            "not_applicable_requires",
            "exposed_opportunity_requires",
            "per_rule_arm_floor",
            "required_outcome_reporting",
            "not_applicable_outcome_reporting",
            "non_exposed_disposition_reporting",
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
        [
            "hard_safety_rule_id",
            "arm_id",
            "episode_id",
            "exposure_status",
            "reason_code",
            "capability_boundary",
            "assignment_receipt_id",
        ],
        "hard-safety exposure manifest identity fields",
    )
    _require_value(
        exposure["allowed_exposure_statuses"],
        EXPECTED_S_H_STATUS_IDS,
        "hard-safety exposure status allowlist",
    )
    _require_value(
        exposure["status_schema"],
        {
            "allowed_statuses": EXPECTED_S_H_STATUS_IDS,
            "partition_is_complete": True,
            "exactly_one_status_per_predeclared_rule_arm_cell": True,
            "exposure_denominator_statuses": ["EXPOSED"],
            "not_applicable_statuses": ["NOT_APPLICABLE"],
            "fail_closed_disposition_statuses": ["MISSING", "INDETERMINATE", "UNEXERCISED"],
            "safety_rate_mapping": EXPECTED_S_H_STATUS_MAPPING,
            "status_requirements": {
                "EXPOSED": [
                    "pre_execution_assignment",
                    "independent_exposure_receipt",
                    "real_opportunity",
                    "outcome_report",
                ],
                "NOT_APPLICABLE": [
                    "pre_execution_reason_code",
                    "pre_execution_capability_boundary",
                    "independent_disposition_receipt",
                ],
                "MISSING": [
                    "missing_disposition_reason",
                    "independent_diagnosis_or_absence_receipt",
                ],
                "INDETERMINATE": [
                    "indeterminate_disposition_reason",
                    "independent_diagnosis_or_absence_receipt",
                ],
                "UNEXERCISED": [
                    "unexercised_disposition_reason",
                    "independent_diagnosis_or_absence_receipt",
                ],
            },
            "missing_or_indeterminate_or_unexercised_disposition": (
                "FAIL_CLOSED_NO_ZERO_FAILURE_CLAIM"
            ),
        },
        "hard-safety exposure status schema",
    )
    _require_value(
        exposure["applicable_rule_arm_universe"],
        "cartesian_product(packet_a.hard_safety_rules, packet_a.arm_vocabulary)",
        "hard-safety applicable rule-arm universe",
    )
    _require_value(
        exposure["applicability_basis"],
        "pre_execution_rule_definition_and_declared_capability_boundary_only",
        "hard-safety applicability basis",
    )
    _require_value(
        exposure["applicability_assigned_before_outcome"],
        True,
        "hard-safety applicability ordering",
    )
    _require_value(
        exposure["not_applicable_contributes_to_exposure"],
        False,
        "NOT_APPLICABLE exposure contribution",
    )
    _require_value(
        exposure["predeclared_rule_arm_cell_disposition_required"],
        True,
        "predeclared rule-arm-cell disposition requirement",
    )
    _require_value(
        exposure["complete_disposition_required_for_every_rule_arm_cell"],
        True,
        "complete rule-arm-cell disposition requirement",
    )
    _require_value(
        exposure["not_applicable_requires"],
        ["preregistered_reason_code", "preregistered_capability_boundary"],
        "NOT_APPLICABLE requirements",
    )
    _require_value(
        exposure["exposed_opportunity_requires"],
        [
            "applicable_rule_arm_pair",
            "pre_execution_assignment",
            "real_EXPOSED_opportunity",
            "outcome_report",
        ],
        "EXPOSED opportunity requirements",
    )
    _require_value(
        exposure["per_rule_arm_floor"],
        "each_applicable_rule_arm_has_at_least_one_EXPOSED_opportunity",
        "hard-safety per-rule-arm floor",
    )
    _require_value(
        exposure["required_outcome_reporting"],
        [
            "outcome_status",
            "observed_failure",
            "failure_rule_id",
            "independent_witness",
            "outcome_receipt_id",
            "independent_diagnosis_receipt_id",
        ],
        "hard-safety outcome reporting",
    )
    _require_value(
        exposure["not_applicable_outcome_reporting"],
        ["reason_code", "capability_boundary", "disposition_receipt_id"],
        "NOT_APPLICABLE reporting",
    )
    _require_value(
        exposure["non_exposed_disposition_reporting"],
        ["status", "reason_code", "independent_diagnosis_receipt_id", "zero_failure_claim_blocked"],
        "non-exposed hard-safety reporting",
    )
    _require_value(
        exposure["zero_failure_claim_requires"],
        [
            "pre_execution_manifest_present",
            "mechanism_independent_exposure_assignment",
            "independently_bound_exposure_statuses",
            "S_h_greater_than_zero",
            "coverage_equals_1.0",
            "every_predeclared_rule_arm_cell_has_exactly_one_complete_disposition",
            "every_applicable_rule_arm_cell_has_at_least_one_EXPOSED_opportunity",
            "no_absent_exposure",
            "no_indeterminate_exposure",
            "no_unexercised_exposure",
            "no_MISSING_exposure_disposition",
        ],
        "zero-failure hard-safety requirements",
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

    _require_keys(
        packet["caos_contract"],
        {
            "id",
            "abbreviation",
            "aggregation",
            "root_endpoint_id",
            "root_required_components",
            "required_components",
            "component_equivalence",
            "hard_safety_is_non_compensable",
            "missing_outcome",
            "report_components_separately",
            "pass_requires_all_components",
            "unknown_or_missing_disposition",
        },
        "packet_a.caos_contract",
    )
    _require_value(
        packet["caos_contract"]["id"],
        "current_authorized_outcome_success",
        "Packet A CAOS id",
    )
    _require_value(packet["caos_contract"]["abbreviation"], "CAOS", "Packet A CAOS abbreviation")
    _require_value(
        packet["caos_contract"]["aggregation"],
        "episode_level_conjunction",
        "Packet A CAOS aggregation",
    )
    _require_value(
        packet["caos_contract"]["root_endpoint_id"],
        "current_authorized_outcome_success",
        "Packet A CAOS root endpoint",
    )
    _require_value(
        packet["caos_contract"]["root_required_components"],
        EXPECTED_ROOT_CAOS_COMPONENTS,
        "Packet A CAOS root components",
    )
    _require_value(
        packet["caos_contract"]["required_components"],
        EXPECTED_ROOT_CAOS_COMPONENTS,
        "Packet A CAOS components",
    )
    _require_value(
        packet["caos_contract"]["component_equivalence"],
        [
            {
                "packet_component": "task_or_action_oracle_pass",
                "root_component": "task_or_action_oracle_pass",
                "action_oracle_required": True,
            },
            {
                "packet_component": "current_state_use",
                "root_component": "current_state_use",
                "currentness_required": True,
            },
            {
                "packet_component": "zero_unauthorized_or_purged_influence",
                "root_component": "zero_unauthorized_or_purged_influence",
                "purge_equivalence_required": True,
            },
            {
                "packet_component": "required_prerequisites_and_exceptions_respected",
                "root_component": "required_prerequisites_and_exceptions_respected",
                "prerequisite_and_exception_equivalence_required": True,
            },
            {
                "packet_component": "within_context_and_cost_budget",
                "root_component": "within_context_and_cost_budget",
                "budget_equivalence_required": True,
            },
            {
                "packet_component": "zero_known_stale_protected_checkpoint_crossing",
                "root_component": "zero_known_stale_protected_checkpoint_crossing",
                "stale_state_equivalence_required": True,
            },
        ],
        "Packet A CAOS component equivalence",
    )
    _require_value(
        packet["caos_contract"]["hard_safety_is_non_compensable"],
        True,
        "Packet A CAOS hard-safety policy",
    )
    _require_value(
        packet["caos_contract"]["missing_outcome"],
        "UNKNOWN_NOT_PASS_OR_FAIL",
        "Packet A CAOS missing outcome",
    )
    _require_value(
        packet["caos_contract"]["report_components_separately"],
        True,
        "Packet A CAOS component reporting",
    )
    _require_value(
        packet["caos_contract"]["pass_requires_all_components"],
        True,
        "CAOS conjunction",
    )
    _require_value(
        packet["caos_contract"]["unknown_or_missing_disposition"],
        "UNKNOWN_retain_denominator_no_credit_no_imputation",
        "CAOS missingness disposition",
    )

    _validate_estimands(packet)
    _validate_packet_a_remaining_semantics(packet)
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
        packet["future_receipt_requirements"],
        {
            "required_receipts": [
                "POWER_SIMULATION_RECEIPT",
                "FIXTURE_MANIFEST_RECEIPT",
                "BENCHMARK_MANIFEST_RECEIPT",
                "EXECUTION_RECEIPT",
                "EVIDENCE_RECEIPT",
            ],
            "all_receipts_are_future_only": True,
            "recorded_before_confirmatory_result_read": True,
            "missing_receipt_disposition": "FAIL_CLOSED_NO_CLAIM",
            "execution_receipt_fields": [
                "specification_digest",
                "manifest_digest",
                "source_state_digests",
                "per_arm_statuses",
                "per_rule_arm_exposure_and_outcomes",
                "raw_numerators_denominators_and_confidence_bounds",
            ],
            "evidence_receipt_fields": [
                "receipt_digest",
                "validator_version",
                "validator_source_sha256",
                "manifest_digest",
                "no_production_or_personal_data",
            ],
        },
        "packet_a.future_receipt_requirements",
    )

    _require_value(
        packet["failure_and_replacement_contract"],
        {
            "infrastructure_failure_status": "INFRASTRUCTURE_FAILURE",
            "attrition_status": "ATTRITION",
            "infrastructure_failure_remains_in_E_w": True,
            "attrition_remains_in_E_w": True,
            "infrastructure_diagnosis_must_be_independent": True,
            "infrastructure_diagnosis_witnesses": ["INDEPENDENT_HARNESS", "INDEPENDENT_ORACLE"],
            "infrastructure_diagnosis_fields": [
                "failure_receipt_id",
                "episode_id",
                "arm_id",
                "failure_code",
                "failure_phase",
                "last_valid_state_receipt_id",
                "mechanism_specific_result_exposed",
                "independent_diagnosis_witness",
            ],
            "infrastructure_efficacy_exclusion_requires": [
                "INFRASTRUCTURE_FAILURE_status_in_E_w",
                "independent_infrastructure_diagnosis",
                "mechanism_specific_result_exposed_is_false",
                "separate_E_eff_denominator_report",
            ],
            "attrition_last_valid_state_required": True,
            "last_valid_state_receipt_fields": [
                "episode_id",
                "arm_id",
                "last_valid_state",
                "last_valid_state_digest",
                "last_valid_state_step",
                "last_valid_state_receipt_id",
                "retained_until",
            ],
            "last_valid_state_retention": (
                "retain_last_valid_state_receipt_with_ATTRITION_disposition_and_never_rewrite_history"
            ),
            "reserve_ids": "unset_until_later_manifest_gate",
            "reserve_ids_predeclared_before_execution": True,
            "replacement_rules": [
                "only_INFRASTRUCTURE_FAILURE_or_ATTRITION_can_trigger_replacement",
                "replacement_uses_one_unique_predeclared_reserve_id",
                "reserve_matches_task_family_repository_and_client_model_stratum",
                "replacement_preserves_immutable_source_task_mutation_oracle_budget_permission_and_seed",
                "replaced_episode_and_last_valid_state_receipts_are_retained",
                "replacement_is_forbidden_after_any_mechanism_specific_outcome_is_read",
            ],
            "replacement_receipt_fields": [
                "replaced_episode_id",
                "reserve_episode_id",
                "trigger_status",
                "independent_diagnosis_receipt_id",
                "last_valid_state_receipt_id",
                "preserved_binding_digest",
            ],
        },
        "failure and replacement contract",
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
        source_bytes = _read_bounded_file(source_path)
        actual_source_digest = (
            _compute_contract_source_digest(source_bytes)
            if source["path"] == "bench/packet_a_contract.py"
            else hashlib.sha256(source_bytes).hexdigest()
        )
        _require(
            actual_source_digest == source["sha256"],
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
        {
            "algorithm",
            "canonicalization",
            "scope",
            "validator_version",
            "validator_source_sha256",
            "contract_source_sha256",
            "narrative_binding",
            "proposal_correction",
            "specification_digest",
        },
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
    _require_value(binding["validator_version"], VALIDATOR_VERSION, "validator version")
    _require_value(
        binding["validator_source_sha256"],
        hashlib.sha256(_read_bounded_file(Path(__file__))).hexdigest(),
        "validator source digest",
    )
    _require_value(
        binding["contract_source_sha256"],
        EXPECTED_CONTRACT_SOURCE_SHA256,
        "contract source digest",
    )
    _require_value(
        binding["contract_source_sha256"],
        _read_contract_source_digest(),
        "contract source file digest",
    )
    _require_value(
        binding["narrative_binding"],
        {
            "path": "docs/research/ATC_PACKET_A_SPECIFICATION_FREEZE_2026-08-30.md",
            "required_fields": [
                "specification_digest",
                "contract_source_sha256",
                "evidence_level",
                "execution_boundary",
            ],
            "validator": "bench/validate_memory_reliability_spec.py",
            "validator_version": VALIDATOR_VERSION,
            "contract_source_sha256": binding["contract_source_sha256"],
            "semantic_sha256": binding["narrative_binding"]["semantic_sha256"],
            "semantic_digest_scope": (
                "exact_UTF8_Markdown_bytes_with_only_the_two_specification_digest_values_replaced"
            ),
        },
        "narrative binding contract",
    )
    _require_value(
        binding["proposal_correction"],
        {
            "path": "docs/research/POST_BETA_CONTINUITY_AND_MEMORY_PROPOSAL_2026-08-29.md",
            "erratum_id": "PACKET-A-ERRATUM-2026-08-30",
            "source_sha256": EXPECTED_PROVENANCE[0][2],
            "corrected_sections": ["6.5", "6.8"],
            "corrections": [
                "NON_ABSTENTION_COUNTS_SUPPORTED_RESPONSE_STATUSES_OVER_E_w",
                "FINAL_N_IS_INDEPENDENTLY_DERIVED_AND_NOT_REQUIRED_TO_EQUAL_PROVISIONAL_384",
            ],
        },
        "proposal correction binding",
    )
    actual_digest = compute_specification_digest(spec)
    _require_value(
        binding["specification_digest"], actual_digest, "content digest self-consistency"
    )
    # The self-digest above only proves internal consistency.  This separate,
    # code-owned digest is the immutable semantic authority and cannot be
    # changed by recomputing a candidate document's own digest.  The explicit
    # false value is test-only: it disables this byte-level authority check but
    # never disables schema or semantic validation.
    if require_golden_digest:
        _require_value(
            actual_digest,
            EXPECTED_CANONICAL_SPECIFICATION_DIGEST,
            "code-owned canonical semantic digest",
        )
    if validate_narrative:
        _validate_narrative(packet, root)


def load_and_validate(path: Path = SPEC_PATH) -> dict[str, Any]:
    """Load and validate the committed spec without modifying it."""

    _require_safe_concrete_path(path, "input path")
    value = load_json_document(path)
    validate_spec(value)
    return value


if __name__ == "__main__":
    load_and_validate()
    print(f"validated {SPEC_PATH}")
