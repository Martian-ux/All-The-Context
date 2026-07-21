"""Credential hashing and authorization helpers.

No credentials or raw context are emitted to logs by this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass

_ITERATIONS = 310_000


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, _ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        _ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_token(token: str, encoded: str) -> bool:
    try:
        algorithm, iterations, encoded_salt, encoded_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
        expected = base64.urlsafe_b64decode(encoded_digest.encode("ascii"))
        actual = hashlib.pbkdf2_hmac("sha256", token.encode("utf-8"), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True, slots=True)
class ClientPrincipal:
    id: str
    name: str
    scopes: frozenset[str]
    auto_approve: bool = False


def record_is_allowed(
    principal: ClientPrincipal | None,
    record_scopes: set[str],
    allowed_clients: set[str],
    denied_clients: set[str],
) -> bool:
    if principal is None:
        return True
    if principal.id in denied_clients:
        return False
    if allowed_clients and principal.id not in allowed_clients:
        return False
    # Record scopes select categories at query time. Coarse operation scopes are
    # checked by the transport; per-record clients are controlled by allow/deny.
    # Do not accidentally compare values such as "project:atlas" to "context:read".
    _ = record_scopes
    return True
