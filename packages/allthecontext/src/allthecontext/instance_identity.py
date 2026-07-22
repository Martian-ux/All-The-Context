"""Per-installation proof used before the desktop sends privileged credentials."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

from filelock import FileLock

from .config import CoreConfig

IDENTITY_FILENAME = "instance-identity.json"
PROOF_CONTEXT = "all-the-context-core-v1"


def _identity_path(config: CoreConfig) -> Path:
    return config.data_dir / IDENTITY_FILENAME


def ensure_instance_secret(config: CoreConfig) -> str:
    """Return a stable random secret stored inside this user's Core data directory."""
    config.prepare()
    path = _identity_path(config)
    with FileLock(str(path.with_suffix(path.suffix + ".lock")), timeout=5):
        if path.is_file():
            return read_instance_secret(config)
        secret = secrets.token_urlsafe(32)
        payload = json.dumps({"version": 1, "secret": secret}, sort_keys=True) + "\n"
        temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
        try:
            temporary.write_text(payload, encoding="utf-8", newline="\n")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return read_instance_secret(config)


def read_instance_secret(config: CoreConfig) -> str:
    path = _identity_path(config)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("Core instance identity is missing or invalid") from exc
    secret = value.get("secret") if isinstance(value, dict) else None
    version = value.get("version") if isinstance(value, dict) else None
    if version != 1 or not isinstance(secret, str) or len(secret) < 32:
        raise RuntimeError("Core instance identity is missing or invalid")
    return secret


def instance_proof(config: CoreConfig, challenge: str, secret: str | None = None) -> str:
    if not challenge or len(challenge) > 256:
        raise ValueError("instance challenge must contain between 1 and 256 characters")
    active_secret = secret or read_instance_secret(config)
    message = (f"{PROOF_CONTEXT}\0{config.host.casefold()}\0{config.port}\0{challenge}").encode()
    return hmac.new(active_secret.encode(), message, hashlib.sha256).hexdigest()


def proof_matches(config: CoreConfig, challenge: str, proof: str) -> bool:
    try:
        expected = instance_proof(config, challenge)
    except (RuntimeError, ValueError):
        return False
    return hmac.compare_digest(expected, proof)
