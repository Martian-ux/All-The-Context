"""Public-key authenticated, one-time hosted Edge claim protocol."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CLAIM_PREFIX = "atc-edge-claim-v1."
CLAIM_LIFETIME_SECONDS = 24 * 3600


class EdgeClaimError(ValueError):
    pass


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(value: str, *, expected: int) -> bytes:
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise EdgeClaimError("claim key encoding is invalid") from exc
    if len(raw) != expected:
        raise EdgeClaimError("claim key length is invalid")
    return raw


def _decode_any(value: str) -> bytes:
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        raise EdgeClaimError("claim value encoding is invalid") from exc


@dataclass(frozen=True, slots=True)
class EdgeClaimBundle:
    claim_id: str
    vault_id: str
    owner_secret_hash: str
    signing_public_key: str
    encryption_public_key: str
    expires_at: int

    def encode(self) -> str:
        raw = json.dumps(
            self.__dict__
            if hasattr(self, "__dict__")
            else {
                "claim_id": self.claim_id,
                "vault_id": self.vault_id,
                "owner_secret_hash": self.owner_secret_hash,
                "signing_public_key": self.signing_public_key,
                "encryption_public_key": self.encryption_public_key,
                "expires_at": self.expires_at,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return CLAIM_PREFIX + _b64(raw)

    @classmethod
    def decode(cls, value: str) -> EdgeClaimBundle:
        if not value.startswith(CLAIM_PREFIX) or len(value) > 8_000:
            raise EdgeClaimError("unknown Edge claim format")
        try:
            raw = base64.b64decode(
                value[len(CLAIM_PREFIX) :] + "=" * (-len(value[len(CLAIM_PREFIX) :]) % 4),
                altchars=b"-_",
                validate=True,
            )
            parsed = json.loads(raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise EdgeClaimError("Edge claim is invalid") from exc
        if not isinstance(parsed, dict) or set(parsed) != {
            "claim_id",
            "vault_id",
            "owner_secret_hash",
            "signing_public_key",
            "encryption_public_key",
            "expires_at",
        }:
            raise EdgeClaimError("Edge claim fields are invalid")
        bundle = cls(**parsed)
        if len(bundle.claim_id) < 32 or len(bundle.vault_id) > 200:
            raise EdgeClaimError("Edge claim identity is invalid")
        if len(bundle.owner_secret_hash) != 64:
            raise EdgeClaimError("Edge owner hash is invalid")
        _decode(bundle.signing_public_key, expected=32)
        _decode(bundle.encryption_public_key, expected=32)
        return bundle


@dataclass(frozen=True, slots=True)
class EdgeClaimPrivate:
    signing_private_key: str
    encryption_private_key: str


def generate_claim(
    vault_id: str, owner_secret_hash: str
) -> tuple[EdgeClaimBundle, EdgeClaimPrivate]:
    signing = Ed25519PrivateKey.generate()
    encryption = X25519PrivateKey.generate()
    raw = serialization.Encoding.Raw
    return (
        EdgeClaimBundle(
            claim_id=secrets.token_urlsafe(32),
            vault_id=vault_id,
            owner_secret_hash=owner_secret_hash,
            signing_public_key=_b64(
                signing.public_key().public_bytes(raw, serialization.PublicFormat.Raw)
            ),
            encryption_public_key=_b64(
                encryption.public_key().public_bytes(raw, serialization.PublicFormat.Raw)
            ),
            expires_at=int(time.time()) + CLAIM_LIFETIME_SECONDS,
        ),
        EdgeClaimPrivate(
            signing_private_key=_b64(
                signing.private_bytes(
                    raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
                )
            ),
            encryption_private_key=_b64(
                encryption.private_bytes(
                    raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
                )
            ),
        ),
    )


def claim_message(claim_id: str, challenge: str, public_url: str) -> bytes:
    return f"all-the-context/edge-claim/v1\0{claim_id}\0{challenge}\0{public_url}".encode()


def sign_claim(
    private: EdgeClaimPrivate, bundle: EdgeClaimBundle, challenge: str, public_url: str
) -> str:
    key = Ed25519PrivateKey.from_private_bytes(_decode(private.signing_private_key, expected=32))
    return _b64(key.sign(claim_message(bundle.claim_id, challenge, public_url)))


def decrypt_claim(
    bundle: EdgeClaimBundle, private: EdgeClaimPrivate, envelope: dict[str, str]
) -> dict[str, str]:
    key_private = X25519PrivateKey.from_private_bytes(
        _decode(private.encryption_private_key, expected=32)
    )
    shared = key_private.exchange(
        X25519PublicKey.from_public_bytes(_decode(envelope["ephemeral_public_key"], expected=32))
    )
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"atc-edge-claim-v1").derive(
        shared
    )
    raw = AESGCM(key).decrypt(
        _decode(envelope["nonce"], expected=12),
        _decode_any(envelope["ciphertext"]),
        bundle.claim_id.encode(),
    )
    parsed = json.loads(raw)
    return {
        "replication_secret": str(parsed["replication_secret"]),
        "replication_token": str(parsed["replication_token"]),
    }


def encrypt_forward_request(public_key: str, plaintext: bytes, associated_data: bytes) -> str:
    """Seal an in-flight request so the hosted Edge can never read its query."""

    ephemeral = X25519PrivateKey.generate()
    shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(_decode(public_key, expected=32)))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"atc-edge-forward-v1").derive(
        shared
    )
    nonce = secrets.token_bytes(12)
    envelope = {
        "ephemeral_public_key": _b64(
            ephemeral.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ),
        "nonce": _b64(nonce),
        "ciphertext": _b64(AESGCM(key).encrypt(nonce, plaintext, associated_data)),
    }
    return json.dumps(envelope, sort_keys=True, separators=(",", ":"))


def decrypt_forward_request(private_key: str, envelope_json: str, associated_data: bytes) -> bytes:
    """Open a request envelope only inside the authoritative Core process."""

    try:
        envelope = json.loads(envelope_json)
        private = X25519PrivateKey.from_private_bytes(_decode(private_key, expected=32))
        shared = private.exchange(
            X25519PublicKey.from_public_bytes(
                _decode(str(envelope["ephemeral_public_key"]), expected=32)
            )
        )
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"atc-edge-forward-v1"
        ).derive(shared)
        return AESGCM(key).decrypt(
            _decode(str(envelope["nonce"]), expected=12),
            _decode_any(str(envelope["ciphertext"])),
            associated_data,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EdgeClaimError("forwarding envelope is invalid") from exc


class EdgeClaimStore:
    def __init__(self, database_path: Path, bundle: EdgeClaimBundle, public_url: str) -> None:
        self.database_path = database_path
        self.bundle = bundle
        self.public_url = public_url
        self._lock = RLock()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA secure_delete=ON")
        return connection

    def challenge(self) -> str:
        if self.acknowledged() or time.time() > self.bundle.expires_at:
            raise EdgeClaimError("Edge claim is unavailable")
        challenge = secrets.token_urlsafe(32)
        with self._lock, self._connection() as connection:
            connection.execute(
                "DELETE FROM edge_claim_challenges WHERE expires_at<?", (time.time(),)
            )
            connection.execute(
                "INSERT INTO edge_claim_challenges"
                "(challenge_hash,claim_id,expires_at) VALUES(?,?,?)",
                (
                    hashlib.sha256(challenge.encode()).hexdigest(),
                    self.bundle.claim_id,
                    time.time() + 300,
                ),
            )
        return challenge

    def complete(self, challenge: str, signature: str) -> dict[str, str]:
        now = time.time()
        if now > self.bundle.expires_at or self.acknowledged():
            raise EdgeClaimError("Edge claim is unavailable")
        try:
            Ed25519PublicKey.from_public_bytes(
                _decode(self.bundle.signing_public_key, expected=32)
            ).verify(
                _decode(signature, expected=64),
                claim_message(self.bundle.claim_id, challenge, self.public_url),
            )
        except (ValueError, EdgeClaimError, InvalidSignature) as exc:
            raise EdgeClaimError("Edge claim proof is invalid") from exc
        digest = hashlib.sha256(challenge.encode()).hexdigest()
        with self._lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            used = connection.execute(
                "UPDATE edge_claim_challenges SET used_at=? WHERE challenge_hash=? "
                "AND claim_id=? AND used_at IS NULL AND expires_at>=?",
                (now, digest, self.bundle.claim_id, now),
            )
            if used.rowcount != 1:
                raise EdgeClaimError("Edge claim challenge is expired or replayed")
            row = connection.execute(
                "SELECT * FROM edge_claim_runtime WHERE singleton=1"
            ).fetchone()
            if row is None:
                replication_secret = secrets.token_urlsafe(32)
                replication_token = secrets.token_urlsafe(32)
                connection.execute(
                    "INSERT INTO edge_claim_runtime VALUES(1,?,?,?,?,NULL)",
                    (self.bundle.claim_id, replication_secret, replication_token, now),
                )
            else:
                replication_secret = str(row["replication_secret"])
                replication_token = str(row["replication_token"])
        ephemeral = X25519PrivateKey.generate()
        shared = ephemeral.exchange(
            X25519PublicKey.from_public_bytes(
                _decode(self.bundle.encryption_public_key, expected=32)
            )
        )
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=None, info=b"atc-edge-claim-v1"
        ).derive(shared)
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(
            {"replication_secret": replication_secret, "replication_token": replication_token},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        ciphertext = AESGCM(key).encrypt(nonce, plaintext, self.bundle.claim_id.encode())
        return {
            "ephemeral_public_key": _b64(
                ephemeral.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )
            ),
            "nonce": _b64(nonce),
            "ciphertext": _b64(ciphertext),
        }

    def decrypt(self, private: EdgeClaimPrivate, envelope: dict[str, str]) -> dict[str, str]:
        return decrypt_claim(self.bundle, private, envelope)

    def credentials(self) -> tuple[str, str] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM edge_claim_runtime WHERE singleton=1"
            ).fetchone()
        return (
            None if row is None else (str(row["replication_secret"]), str(row["replication_token"]))
        )

    def acknowledge(self) -> None:
        with self._connection() as connection:
            connection.execute(
                "UPDATE edge_claim_runtime SET acknowledged_at=? WHERE singleton=1",
                (time.time(),),
            )
            connection.execute("DELETE FROM edge_claim_challenges")

    def acknowledged(self) -> bool:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT acknowledged_at FROM edge_claim_runtime WHERE singleton=1"
            ).fetchone()
        return row is not None and row[0] is not None
