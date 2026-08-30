"""Code-owned Packet A expectations.

This module is intentionally independent of the candidate JSON document. The
validator imports these values as authority; it never loads a golden document
from the path it is validating.
"""

EXPECTED_CANONICAL_SPECIFICATION_DIGEST = (
    "1efaa7bed03db3a551a2764af3f5cd7384ade9cba14d99fb9783bde838069e75"
)
EXPECTED_STRUCTURE_DIGEST = (
    "4a1274017c5045e349e20e90781bbacb0e257067ea0259d37b67dc9b481bd060"
)
EXPECTED_NARRATIVE_SEMANTIC_DIGEST = (
    "e6720c0479659cbe86a6720b2577b5d6567a2ac97ae8a32675da90c8d8779fda"
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
