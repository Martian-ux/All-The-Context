"""Code-owned Packet A expectations.

This module is intentionally independent of the candidate JSON document. The
validator imports these values as authority; it never loads a golden document
from the path it is validating.
"""

EXPECTED_CANONICAL_SPECIFICATION_DIGEST = (
    "e1df3122b147c3ff8956cfc1157899ca7146c55fbc7db51b2ff509dd5fe1d9fc"
)
EXPECTED_STRUCTURE_DIGEST = "28d1cd5d6bd79e7cb9f2da5807e4e7a0898555a235434ec7379285a4d9ba860a"
EXPECTED_NARRATIVE_SEMANTIC_DIGEST = (
    "21d1baa3a20d80472e3870eeb48f9e273db24923ebe7e0e8093ddd9233c3c850"
)
EXPECTED_CONTRACT_SOURCE_SHA256 = "a57514b4102abe954516e530cf6a8507465afb4406e78399d10bb0b0670953ca"

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
