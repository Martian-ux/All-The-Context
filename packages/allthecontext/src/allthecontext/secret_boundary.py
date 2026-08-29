"""Pre-ledger refusal rules for direct secret-like observations.

Patterns intentionally cover only high-confidence credential forms. A broad
entropy detector would silently discard ordinary memory text.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from uuid import UUID

from .models import CandidateInput

SECRET_DETECTOR_VERSION = "direct-secret-v4"
SECRET_REFUSAL_REASON = "direct_secret_like_content"
SECRET_REASON_REDACTION = "Explicit user privacy action"

_SECRET_ASSIGNMENT = re.compile(
    r"(?:api[_ -]?key|password|passphrase|private[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|client[_ -]?secret|authorization|"
    r"aws[_ -]?secret[_ -]?access[_ -]?key|secret[_ -]?access[_ -]?key|"
    r"connection[_ -]?string|shared[_ -]?access[_ -]?key|credential|secret)\b"
    r"\s*(?::|=|\bis\b|\bwas\b)",
    flags=re.IGNORECASE,
)
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----",
    flags=re.IGNORECASE,
)
_AUTH_SCHEME_VALUE = re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}", flags=re.IGNORECASE)
_TOKEN_SCHEME_VALUE = re.compile(
    r"\btoken\s+(?P<value>[A-Za-z0-9._~+/=-]{8,})", flags=re.IGNORECASE
)
_TOKEN_ASSIGNMENT = re.compile(
    r"\b(?:token|access[_ -]?token|refresh[_ -]?token|id[_ -]?token|"
    r"auth(?:entication)?[_ -]?token|api[_ -]?token|session[_ -]?token|"
    r"client[_ -]?token|oauth[_ -]?token)\b"
    r"\s*(?::|=|\bis\b|\bwas\b)\s*"
    r"(?P<value>[A-Za-z0-9._~+/=-]{8,})",
    flags=re.IGNORECASE,
)
_JWT_SHAPED = re.compile(
    r"(?<![A-Za-z0-9_-])(?:"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}|"
    r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\."
    r"[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
    r")(?![A-Za-z0-9_-])"
)
_PASETO_SHAPED = re.compile(
    r"(?<![A-Za-z0-9_])v[1-4]\.(?:local|public)\."
    r"[A-Za-z0-9_-]{16,}(?:\.[A-Za-z0-9_-]{2,})?(?![A-Za-z0-9_])",
    flags=re.IGNORECASE,
)
_WELL_KNOWN_TOKEN = re.compile(
    r"(?i)\b(?:"
    r"sk-[A-Za-z0-9_-]{20,}|"
    r"gsk_[A-Za-z0-9_-]{20,}|"
    r"ghp_[A-Za-z0-9]{36,}|"
    r"gh[ousr]_[A-Za-z0-9]{36,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|"
    r"xox[baprsce]-[A-Za-z0-9-]{10,}|"
    r"xapp-[A-Za-z0-9-]{10,}|"
    r"AIza[A-Za-z0-9_-]{20,}|"
    r"(?:AKIA|ASIA)[0-9A-Z]{16}|"
    r"npm_[A-Za-z0-9]{30,}|"
    r"pypi-[A-Za-z0-9_-]{20,}|"
    r"hf_[A-Za-z0-9]{20,}|"
    r"sntrys_[A-Za-z0-9_-]{20,}|"
    r"r8_[A-Za-z0-9_-]{20,}|"
    r"ya29\.[A-Za-z0-9_-]{20,}|"
    r"(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{20,}|"
    r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{20,}|"
    r"(?:secret|xai|vercel)_[A-Za-z0-9_-]{20,}|"
    r"dp\.st\.[A-Za-z0-9_-]{20,}|"
    r"lin_api_[A-Za-z0-9_-]{20,}|"
    r"dop_v1_[A-Za-z0-9_-]{20,}"
    r")\b"
)
_CREDENTIAL_URI = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*)://[^\s/@:?#]+:[^\s/@?#]+@")


def _credential_scan_text(value: str) -> str:
    """Expose compatibility and zero-width obfuscations to ASCII-oriented rules."""

    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(
        char for char in decomposed if unicodedata.category(char) not in {"Cf", "Mn", "Mc", "Me"}
    )


def _looks_like_contextual_token(value: str) -> bool:
    """Require token-like structure for generic labels, without entropy heuristics."""

    return len(value) >= 8 and (
        len(value) >= 24
        or any(char.isdigit() for char in value)
        or any(char in "._~+/=-" for char in value)
    )


def _contains_jwt_like_text(value: str) -> bool:
    """Recognize compact JWT/JWE forms only when their header is JSON-shaped."""

    for match in _JWT_SHAPED.finditer(value):
        header_segment = match.group(0).split(".", 1)[0]
        padded = header_segment + "=" * (-len(header_segment) % 4)
        try:
            header = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(header, Mapping):
            continue
        algorithm = header.get("alg")
        encryption = header.get("enc")
        token_type = header.get("typ")
        if (
            (isinstance(algorithm, str) and bool(algorithm))
            or (isinstance(encryption, str) and bool(encryption))
            or (
                isinstance(token_type, str)
                and token_type.casefold() in {"jwt", "at+jwt", "jws", "jwe"}
            )
        ):
            return True
    return False


def contains_secret_like_text(value: str) -> bool:
    """Return whether text resembles a directly supplied credential value."""

    normalized = _credential_scan_text(value)
    if any(
        pattern.search(normalized) is not None
        for pattern in (
            _SECRET_ASSIGNMENT,
            _PRIVATE_KEY_BLOCK,
            _AUTH_SCHEME_VALUE,
            _WELL_KNOWN_TOKEN,
            _CREDENTIAL_URI,
        )
    ):
        return True
    if _contains_jwt_like_text(normalized) or _PASETO_SHAPED.search(normalized) is not None:
        return True
    return any(
        _looks_like_contextual_token(match.group("value"))
        for pattern in (_TOKEN_SCHEME_VALUE, _TOKEN_ASSIGNMENT)
        for match in pattern.finditer(normalized)
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
