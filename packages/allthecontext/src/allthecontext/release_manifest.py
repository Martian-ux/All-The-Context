"""Versioned OTA manifest creation and verification.

This module defines release metadata only.  It intentionally does not download,
install, or execute updates.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

SCHEMA_VERSION = 1
CHANNELS = frozenset({"stable", "beta"})
PLATFORMS = frozenset({"windows", "macos", "linux"})
ARCHITECTURES = frozenset({"x86_64", "arm64"})
SHA256 = re.compile(r"[0-9a-f]{64}")
SHA256_FINGERPRINT = re.compile(r"sha256:[0-9a-f]{64}")
KEY_ID = re.compile(r"[a-z0-9][a-z0-9._-]{2,63}")
VERSION = re.compile(
    r"(?P<major>0|[1-9][0-9]*)\.(?P<minor>0|[1-9][0-9]*)\.(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<label>beta)\.(?P<number>[1-9][0-9]*))?"
)


class ManifestError(ValueError):
    """The release metadata is malformed or violates update policy."""


@dataclass(frozen=True, order=True)
class ReleaseVersion:
    major: int
    minor: int
    patch: int
    stability: int
    prerelease: int

    @classmethod
    def parse(cls, value: str) -> ReleaseVersion:
        match = VERSION.fullmatch(value)
        if match is None:
            raise ManifestError(f"invalid release version: {value!r}")
        label = match.group("label")
        return cls(
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            1 if label is None else 0,
            0 if label is None else int(match.group("number")),
        )


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ManifestError("invalid base64url value")
    try:
        return base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise ManifestError("invalid base64url value") from exc


def canonical_payload(manifest: dict[str, Any]) -> bytes:
    """Return the deterministic UTF-8 bytes covered by the signature."""

    unsigned = {key: value for key, value in manifest.items() if key != "signature"}
    return json.dumps(
        unsigned,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return digest.hexdigest(), size


def _require_https_url(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ManifestError(f"{field} must be a string")
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ManifestError(f"{field} must be an HTTPS URL without embedded credentials")
    if parsed.fragment:
        raise ManifestError(f"{field} must not contain a fragment")
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "version",
        "channel",
        "platform",
        "architecture",
        "url",
        "sha256",
        "size",
        "minimum_supported_version",
        "mandatory",
        "release_notes_url",
        "key_id",
        "signature",
    }
    if set(manifest) != required:
        missing = sorted(required - set(manifest))
        extra = sorted(set(manifest) - required)
        raise ManifestError(f"manifest fields differ (missing={missing}, extra={extra})")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ManifestError("unsupported manifest schema version")
    version_value = manifest["version"]
    minimum_value = manifest["minimum_supported_version"]
    if not isinstance(version_value, str) or not isinstance(minimum_value, str):
        raise ManifestError("version fields must be strings")
    version = ReleaseVersion.parse(version_value)
    minimum = ReleaseVersion.parse(minimum_value)
    channel = manifest["channel"]
    if channel not in CHANNELS:
        raise ManifestError("channel must be stable or beta")
    if channel == "stable" and version.stability != 1:
        raise ManifestError("stable channel cannot publish a prerelease version")
    if channel == "beta" and version.stability != 0:
        raise ManifestError("beta channel requires a beta.N prerelease version")
    if minimum > version:
        raise ManifestError("minimum supported version cannot exceed release version")
    if manifest["platform"] not in PLATFORMS:
        raise ManifestError("unsupported platform")
    if manifest["architecture"] not in ARCHITECTURES:
        raise ManifestError("unsupported architecture")
    artifact_url = _require_https_url(manifest["url"], "url")
    parsed_artifact = urlsplit(artifact_url)
    lowered_path = parsed_artifact.path.casefold()
    if parsed_artifact.query or "/latest/" in lowered_path or "/main/" in lowered_path:
        raise ManifestError("artifact URL must be immutable and cannot reference latest or main")
    if version_value.casefold() not in lowered_path:
        raise ManifestError("artifact URL path must include the exact release version")
    _require_https_url(manifest["release_notes_url"], "release_notes_url")
    digest = manifest["sha256"]
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        raise ManifestError("sha256 must be 64 lowercase hexadecimal characters")
    size = manifest["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ManifestError("size must be a positive integer")
    if not isinstance(manifest["mandatory"], bool):
        raise ManifestError("mandatory must be a boolean")
    key_id = manifest["key_id"]
    if not isinstance(key_id, str) or KEY_ID.fullmatch(key_id) is None:
        raise ManifestError("invalid key ID")
    signature = manifest["signature"]
    if not isinstance(signature, str) or len(_base64url_decode(signature)) != 64:
        raise ManifestError("signature must be a base64url-encoded Ed25519 signature")


def load_private_key(path: Path, *, password: bytes | None = None) -> Ed25519PrivateKey:
    try:
        value = serialization.load_pem_private_key(path.read_bytes(), password=password)
    except (TypeError, ValueError) as exc:
        raise ManifestError(
            "private key is not a valid PEM Ed25519 key for the supplied password"
        ) from exc
    if not isinstance(value, Ed25519PrivateKey):
        raise ManifestError("private key is not Ed25519")
    return value


def create_manifest(
    *,
    artifact: Path,
    version: str,
    channel: Literal["stable", "beta"],
    platform_name: Literal["windows", "macos", "linux"],
    architecture: Literal["x86_64", "arm64"],
    artifact_url: str,
    minimum_supported_version: str,
    mandatory: bool,
    release_notes_url: str,
    key_id: str,
    private_key: Ed25519PrivateKey,
) -> dict[str, Any]:
    digest, size = sha256_file(artifact)
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "version": version,
        "channel": channel,
        "platform": platform_name,
        "architecture": architecture,
        "url": artifact_url,
        "sha256": digest,
        "size": size,
        "minimum_supported_version": minimum_supported_version,
        "mandatory": mandatory,
        "release_notes_url": release_notes_url,
        "key_id": key_id,
        "signature": _base64url_encode(bytes(64)),
    }
    validate_manifest(manifest)
    manifest["signature"] = _base64url_encode(private_key.sign(canonical_payload(manifest)))
    return manifest


def load_keyring(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ManifestError("keyring must be a JSON object")
    validate_keyring(value)
    return cast(dict[str, Any], value)


def public_key_fingerprint(public_value: str) -> str:
    """Return the review fingerprint for a raw base64url Ed25519 public key."""

    public_bytes = _base64url_decode(public_value)
    if len(public_bytes) != 32:
        raise ManifestError("Ed25519 public key must contain exactly 32 bytes")
    return f"sha256:{hashlib.sha256(public_bytes).hexdigest()}"


def validate_keyring(keyring: dict[str, Any]) -> None:
    """Validate the complete, deliberately small OTA trust store."""

    if set(keyring) != {"schema_version", "keys"}:
        raise ManifestError("keyring fields differ from the version 1 schema")
    if keyring.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError("unsupported keyring schema")
    keys = keyring.get("keys")
    if not isinstance(keys, list):
        raise ManifestError("keyring keys must be a list")
    if len(keys) > 32:
        raise ManifestError("keyring contains too many keys")
    seen_ids: set[str] = set()
    seen_public_keys: set[str] = set()
    required = {
        "key_id",
        "algorithm",
        "public_key",
        "public_key_sha256",
        "channels",
        "status",
    }
    for entry in keys:
        if not isinstance(entry, dict) or set(entry) != required:
            raise ManifestError("keyring entry fields differ from the version 1 schema")
        key_id = entry.get("key_id")
        if not isinstance(key_id, str) or KEY_ID.fullmatch(key_id) is None:
            raise ManifestError("invalid keyring key ID")
        if key_id in seen_ids:
            raise ManifestError("keyring key IDs must be unique")
        seen_ids.add(key_id)
        if entry.get("algorithm") != "Ed25519":
            raise ManifestError("keyring algorithm must be Ed25519")
        public_value = entry.get("public_key")
        if not isinstance(public_value, str):
            raise ManifestError("keyring public key must be a string")
        fingerprint = public_key_fingerprint(public_value)
        if public_value in seen_public_keys:
            raise ManifestError("keyring public keys must be unique")
        seen_public_keys.add(public_value)
        declared_fingerprint = entry.get("public_key_sha256")
        if (
            not isinstance(declared_fingerprint, str)
            or SHA256_FINGERPRINT.fullmatch(declared_fingerprint) is None
            or declared_fingerprint != fingerprint
        ):
            raise ManifestError("keyring public-key fingerprint does not match")
        channels = entry.get("channels")
        if (
            not isinstance(channels, list)
            or not channels
            or any(not isinstance(channel, str) or channel not in CHANNELS for channel in channels)
            or len(set(channels)) != len(channels)
        ):
            raise ManifestError("keyring channels must be a non-empty unique channel list")
        if entry.get("status") not in {"active", "revoked"}:
            raise ManifestError("keyring status must be active or revoked")


def verify_manifest(
    manifest: dict[str, Any],
    keyring: dict[str, Any],
    *,
    current_version: str | None = None,
    expected_channel: str | None = None,
) -> None:
    validate_manifest(manifest)
    validate_keyring(keyring)
    if expected_channel is not None and manifest["channel"] != expected_channel:
        raise ManifestError("manifest channel does not match the requested channel")
    keys = keyring.get("keys")
    if not isinstance(keys, list):
        raise ManifestError("keyring keys must be a list")
    matching = [
        key for key in keys if isinstance(key, dict) and key.get("key_id") == manifest["key_id"]
    ]
    if len(matching) != 1:
        raise ManifestError("manifest key ID is not uniquely trusted")
    key = matching[0]
    if key.get("algorithm") != "Ed25519" or key.get("status") != "active":
        raise ManifestError("manifest key is revoked or unsupported")
    channels = key.get("channels")
    if not isinstance(channels, list) or manifest["channel"] not in channels:
        raise ManifestError("manifest key is not trusted for this channel")
    public_value = key.get("public_key")
    if not isinstance(public_value, str):
        raise ManifestError("trusted public key is missing")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(_base64url_decode(public_value))
        public_key.verify(_base64url_decode(manifest["signature"]), canonical_payload(manifest))
    except (ValueError, TypeError) as exc:
        raise ManifestError("invalid trusted Ed25519 public key") from exc
    except InvalidSignature as exc:
        raise ManifestError("manifest signature verification failed") from exc
    if current_version is not None:
        current = ReleaseVersion.parse(current_version)
        offered = ReleaseVersion.parse(cast(str, manifest["version"]))
        if offered < current:
            raise ManifestError("downgrade is forbidden")
        minimum = ReleaseVersion.parse(cast(str, manifest["minimum_supported_version"]))
        if current < minimum:
            raise ManifestError("current version requires a manual supported upgrade path")


def public_key_value(private_key: Ed25519PrivateKey) -> str:
    public = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _base64url_encode(public)


def encoded_public_key(public_key: Ed25519PublicKey) -> str:
    """Encode an already-public Ed25519 key for the OTA keyring."""

    public = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return _base64url_encode(public)
