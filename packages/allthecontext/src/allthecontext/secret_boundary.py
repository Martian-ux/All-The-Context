"""Pre-ledger refusal rules for direct secret-like observations.

Patterns intentionally cover only high-confidence credential forms. A broad
entropy detector would silently discard ordinary memory text.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from uuid import UUID

from .models import CandidateInput

SECRET_DETECTOR_VERSION = "direct-secret-v2"
SECRET_REFUSAL_REASON = "direct_secret_like_content"
SECRET_REASON_REDACTION = "Explicit user privacy action"

_SECRET_ASSIGNMENT = re.compile(
    r"(?:api[_ -]?key|password|passphrase|private[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|client[_ -]?secret|authorization|"
    r"aws[_ -]?secret[_ -]?access[_ -]?key|secret[_ -]?access[_ -]?key|"
    r"connection[_ -]?string|shared[_ -]?access[_ -]?key|secret)\b"
    r"\s*(?::|=|\bis\b|\bwas\b)",
    flags=re.IGNORECASE,
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    flags=re.IGNORECASE,
)
_BEARER_VALUE = re.compile(r"\bbearer\s+[A-Za-z0-9._~+/=-]{8,}", flags=re.IGNORECASE)
_WELL_KNOWN_TOKEN = re.compile(
    r"(?i)\b(?:"
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"ghp_[A-Za-z0-9]{36,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}"
    r")\b"
)
_CREDENTIAL_URI = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*)://[^\s/@:?#]+:[^\s/@?#]+@")


def contains_secret_like_text(value: str) -> bool:
    """Return whether text resembles a directly supplied credential value."""

    return any(
        pattern.search(value) is not None
        for pattern in (
            _SECRET_ASSIGNMENT,
            _PRIVATE_KEY_BLOCK,
            _BEARER_VALUE,
            _WELL_KNOWN_TOKEN,
            _CREDENTIAL_URI,
        )
    )


def contains_secret_like_value(value: object) -> bool:
    """Recursively inspect direct payload values without retaining a fingerprint."""

    if isinstance(value, str):
        return contains_secret_like_text(value)
    if isinstance(value, Mapping):
        return any(
            contains_secret_like_text(f"{key}:") or contains_secret_like_value(item)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return any(contains_secret_like_value(item) for item in value)
    return False


def contains_direct_secret(candidate: CandidateInput) -> bool:
    """Inspect every caller-controlled candidate field, including retry metadata."""

    return contains_secret_like_value(candidate.model_dump(mode="json"))


def opaque_operation_id(value: str | None) -> str | None:
    """Accept only random UUIDv4 operation IDs for durable refusal replay."""

    if value is None:
        return None
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    if parsed.version != 4:
        return None
    return str(parsed)


def redact_secret_reason(reason: str | None) -> str:
    """Replace secret-like durable reasons with a fixed content-free statement."""

    if reason is None or not reason.strip() or contains_secret_like_text(reason):
        return SECRET_REASON_REDACTION
    return reason
