"""Code-owned Packet A expectations.

This module is intentionally independent of the candidate JSON document. The
validator imports these values as authority; it never loads a golden document
from the path it is validating.
"""

EXPECTED_CANONICAL_SPECIFICATION_DIGEST = (
    "cb050608e87fd150141a8678bed586bd7bcf90a1d7256e7f0b430551031b259e"
)
EXPECTED_STRUCTURE_DIGEST = "ef77eae164d15263c69fd4ca127e35a34c475b9ccbcd52603969700f6ba07d9b"
EXPECTED_NARRATIVE_SEMANTIC_DIGEST = (
    "999d287d4ee6158e2f92d53543e014fee845694c3797ce1cd02ca24113cba57d"
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
