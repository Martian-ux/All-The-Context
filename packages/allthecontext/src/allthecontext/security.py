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

# Capability grant (not an operation scope): only ATC-configured same-device
# principals with this class may attest that text was an explicit user statement.
# Authentication and context:propose alone are insufficient (A-09 / B-102).
WITNESS_EXPLICIT_USER_STATEMENT = "witness:explicit_user_statement"
CLAUDE_CODE_USER_WRITE_SCOPES = frozenset({"context:propose", WITNESS_EXPLICIT_USER_STATEMENT})


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

    def may_attest_explicit_user_statement(self) -> bool:
        """Return whether Core trusts this principal as an explicit-statement witness."""
        return (
            WITNESS_EXPLICIT_USER_STATEMENT in self.scopes
            or "admin" in self.scopes
            or "*" in self.scopes
        )


def principal_may_attest_explicit_user_statement(
    principal: ClientPrincipal | None,
) -> bool:
    return principal is not None and principal.may_attest_explicit_user_statement()


def principal_may_submit_claude_code_user_mutation(
    principal: ClientPrincipal | None,
) -> bool:
    """Return whether a principal is the exact opt-in Claude Code write identity.

    This deliberately excludes ``context:read``, administrator, wildcard, and
    unrelated scopes. The write identity is therefore separate from the
    existing Claude Code read principal and cannot be used as a read shortcut.
    """

    return principal is not None and principal.scopes == CLAUDE_CODE_USER_WRITE_SCOPES


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
