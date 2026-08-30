"""Code-owned Packet A expectations.

This module is intentionally independent of the candidate JSON document. The
validator imports these values as authority; it never loads a golden document
from the path it is validating.
"""

EXPECTED_CANONICAL_SPECIFICATION_DIGEST = (
    "c0e7c3d604ff5c7fa42fcab35b3c2e7f107de575c50a76d647e0bbde05b7a8e2"
)
EXPECTED_NARRATIVE_SEMANTIC_DIGEST = (
    "57abd5119f4c4e9c1358a5fae9e61ebcc99bd9101a1d40782e874592fe9823a1"
)
