"""Code-owned Packet A expectations.

This module is intentionally independent of the candidate JSON document. The
validator imports these values as authority; it never loads a golden document
from the path it is validating.
"""

EXPECTED_CANONICAL_SPECIFICATION_DIGEST = (
    "74869640c0bc0c863e6840ceaafd5a09aca4b2c535dbbc00f03dfa1d6f3ef43b"
)
EXPECTED_NARRATIVE_SEMANTIC_DIGEST = (
    "0aa72c45a12ddf26f183627826eab008d80ffd67408c4b5197e19405a416f0da"
)

EXPECTED_VALIDATOR_VERSION = "packet-a-validator-v3"
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
