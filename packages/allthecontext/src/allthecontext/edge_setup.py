"""Portable enrollment contract shared by Core and the hosted Edge.

The bundle is intentionally a deployment secret: it contains the independent
credentials needed for Core-to-Edge replication.  It is copied once into the
hosting provider's secret store and never transported through an AI client.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

from .replication import canonical_json

BUNDLE_PREFIX = "atc-edge-v1."
_RECOVERY_NORMALIZER = re.compile(r"[^A-Z2-7]")


class EdgeSetupError(ValueError):
    """An Edge enrollment value is malformed or unsafe."""


@dataclass(frozen=True, slots=True)
class EdgeEnrollmentBundle:
    vault_id: str
    replication_secret: str
    replication_token: str
    owner_secret_hash: str
    version: int = 1

    def __post_init__(self) -> None:
        if self.version != 1:
            raise EdgeSetupError(f"unsupported Edge bundle version {self.version}")
        if not self.vault_id or len(self.vault_id) > 200:
            raise EdgeSetupError("vault_id is required")
        if len(self.replication_secret.encode("utf-8")) < 32:
            raise EdgeSetupError("replication secret must contain at least 32 bytes")
        if len(self.replication_token) < 32:
            raise EdgeSetupError("replication token must contain at least 32 characters")
        if len(self.owner_secret_hash) != 64:
            raise EdgeSetupError("owner secret hash must be a SHA-256 hex digest")
        try:
            bytes.fromhex(self.owner_secret_hash)
        except ValueError as exc:
            raise EdgeSetupError("owner secret hash must be hexadecimal") from exc

    def mapping(self) -> dict[str, object]:
        return {
            "owner_secret_hash": self.owner_secret_hash,
            "replication_secret": self.replication_secret,
            "replication_token": self.replication_token,
            "vault_id": self.vault_id,
            "version": self.version,
        }

    def encode(self) -> str:
        payload = canonical_json(self.mapping()).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        return f"{BUNDLE_PREFIX}{encoded}"

    @classmethod
    def decode(cls, encoded: str) -> EdgeEnrollmentBundle:
        if not encoded.startswith(BUNDLE_PREFIX):
            raise EdgeSetupError("Edge bundle has an unknown format")
        value = encoded[len(BUNDLE_PREFIX) :]
        if not value or len(value) > 8_000:
            raise EdgeSetupError("Edge bundle has an invalid size")
        try:
            padding = "=" * (-len(value) % 4)
            raw = base64.b64decode(value + padding, altchars=b"-_", validate=True)
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EdgeSetupError("Edge bundle is not valid encoded JSON") from exc
        if not isinstance(parsed, dict):
            raise EdgeSetupError("Edge bundle must contain an object")
        expected = {
            "owner_secret_hash",
            "replication_secret",
            "replication_token",
            "vault_id",
            "version",
        }
        if set(parsed) != expected:
            raise EdgeSetupError("Edge bundle fields do not match version 1")
        try:
            return cls(
                vault_id=str(parsed["vault_id"]),
                replication_secret=str(parsed["replication_secret"]),
                replication_token=str(parsed["replication_token"]),
                owner_secret_hash=str(parsed["owner_secret_hash"]).lower(),
                version=int(parsed["version"]),
            )
        except (TypeError, ValueError) as exc:
            raise EdgeSetupError("Edge bundle contains invalid values") from exc


def normalize_recovery_code(value: str) -> str:
    """Normalize the human recovery code without weakening its entropy."""

    return _RECOVERY_NORMALIZER.sub("", value.upper())


def hash_recovery_code(value: str) -> str:
    normalized = normalize_recovery_code(value)
    if len(normalized) < 24:
        raise EdgeSetupError("owner recovery code is too short")
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def generate_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    return "-".join(raw[index : index + 4] for index in range(0, len(raw), 4))


def generate_enrollment(vault_id: str) -> tuple[EdgeEnrollmentBundle, str]:
    recovery_code = generate_recovery_code()
    bundle = EdgeEnrollmentBundle(
        vault_id=vault_id,
        replication_secret=secrets.token_urlsafe(32),
        replication_token=secrets.token_urlsafe(32),
        owner_secret_hash=hash_recovery_code(recovery_code),
    )
    return bundle, recovery_code


def normalize_edge_url(value: str, *, allow_loopback_http: bool = True) -> str:
    """Return a canonical origin-only URL suitable for OAuth audience binding."""

    raw = value.strip()
    parsed = urlsplit(raw)
    if not parsed.scheme or not parsed.hostname:
        raise EdgeSetupError("Edge URL must be an absolute URL")
    if parsed.username is not None or parsed.password is not None:
        raise EdgeSetupError("Edge URL cannot contain user information")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise EdgeSetupError("Edge URL must contain only its origin, without a path or query")
    hostname = parsed.hostname.lower()
    loopback = hostname in {"127.0.0.1", "::1", "localhost"}
    if parsed.scheme != "https" and not (
        allow_loopback_http and parsed.scheme == "http" and loopback
    ):
        raise EdgeSetupError("Edge URL must use HTTPS except for loopback testing")
    try:
        port = parsed.port
    except ValueError as exc:
        raise EdgeSetupError("Edge URL contains an invalid port") from exc
    host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = (parsed.scheme == "https" and port == 443) or (
        parsed.scheme == "http" and port == 80
    )
    netloc = host if port is None or default_port else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def edge_instance_proof(
    replication_secret: str | bytes,
    *,
    public_url: str,
    vault_id: str,
    challenge: str,
) -> str:
    if not 16 <= len(challenge) <= 512:
        raise EdgeSetupError("challenge must contain between 16 and 512 characters")
    secret = (
        replication_secret.encode("utf-8")
        if isinstance(replication_secret, str)
        else bytes(replication_secret)
    )
    if len(secret) < 32:
        raise EdgeSetupError("replication secret must contain at least 32 bytes")
    origin = normalize_edge_url(public_url)
    message = f"all-the-context-edge\0{origin}\0{vault_id}\0{challenge}".encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def proof_matches(
    replication_secret: str | bytes,
    *,
    public_url: str,
    vault_id: str,
    challenge: str,
    supplied: str,
) -> bool:
    try:
        expected = edge_instance_proof(
            replication_secret,
            public_url=public_url,
            vault_id=vault_id,
            challenge=challenge,
        )
    except EdgeSetupError:
        return False
    return hmac.compare_digest(expected, supplied.lower())
