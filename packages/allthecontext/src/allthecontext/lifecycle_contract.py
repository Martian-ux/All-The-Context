"""Shared bounds for the authenticated client lifecycle contract."""

from __future__ import annotations

# This is the single provider-facing content bound.  The character bound is
# the JSON/schema limit; the byte bound protects UTF-8 transport and Core's
# downstream formation seam.
MAX_LIFECYCLE_CONTENT_CHARS = 16_384
MAX_LIFECYCLE_CONTENT_BYTES = 64 * 1024

# Lifecycle JSON includes opaque identifiers and timestamps in addition to
# content.  Keep one request-body limit shared by the adapter, HTTP client,
# native Codex stdin bridge, and Core's route boundary.
MAX_LIFECYCLE_BODY_BYTES = 128 * 1024


__all__ = [
    "MAX_LIFECYCLE_BODY_BYTES",
    "MAX_LIFECYCLE_CONTENT_BYTES",
    "MAX_LIFECYCLE_CONTENT_CHARS",
]
