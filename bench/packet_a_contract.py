"""Code-owned Packet A expectations.

This module is intentionally independent of the candidate JSON document. The
validator imports these values as authority; it never loads a golden document
from the path it is validating.
"""

EXPECTED_CANONICAL_SPECIFICATION_DIGEST = (
    "1a6c3bee632d06254d458a52ba3041c9bafc122d525cf9cd71face8cb14eb7a5"
)
EXPECTED_STRUCTURE_DIGEST = "f6bd0240059bd2ee9182f9bbbd87cce0a0808d45aa3c252825481deaade5a7e2"
EXPECTED_NARRATIVE_SEMANTIC_DIGEST = (
    "8bd97bb50b86d30b492ae498aeaf721450103b237a29cd452950b02bdba04917"
)
EXPECTED_VALIDATOR_VERSION = "packet-a-validator-v4"
EXPECTED_BASE_CELL_COUNT = 96
EXPECTED_PROVISIONAL_MINIMUM_PAIRED_EPISODE_COUNT = 384
EXPECTED_PROVISIONAL_REPETITIONS_PER_BASE_CELL = 4

EXPECTED_COMPARISON_CELL_IDS = [
    "CONTROL_DETERMINISTIC_SCHEDULER",
    "CONTROL_CURRENT_LEXICAL_AND_CAPSULE_BASELINE",
]
EXPECTED_COMPARISON_ARM_IDS = [
    "DETERMINISTIC_SCHEDULER",
    "ADAPTIVE_ROUTER",
    "CURRENT_LEXICAL_AND_CAPSULE_BASELINE",
]

EXPECTED_S_H_STATUS_IDS = [
    "EXPOSED",
    "NOT_APPLICABLE",
    "MISSING",
    "INDETERMINATE",
    "UNEXERCISED",
]

EXPECTED_ROOT_CAOS_COMPONENTS = [
    "task_or_action_oracle_pass",
    "current_state_use",
    "zero_unauthorized_or_purged_influence",
    "required_prerequisites_and_exceptions_respected",
    "within_context_and_cost_budget",
    "zero_known_stale_protected_checkpoint_crossing",
]
