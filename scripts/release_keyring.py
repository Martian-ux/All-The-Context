"""Inspect and import reviewed Ed25519 *public* release keys.

This utility intentionally has no key-generation or private-key import command.
The signing key remains on an operator-controlled offline system.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from allthecontext.release_manifest import (
    CHANNELS,
    KEY_ID,
    ManifestError,
    encoded_public_key,
    load_keyring,
    public_key_fingerprint,
    validate_keyring,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPERATOR_KEYRING = REPOSITORY_ROOT / "release" / "keys.json"
DEFAULT_PACKAGED_KEYRING = (
    REPOSITORY_ROOT / "packages" / "allthecontext" / "src" / "allthecontext" / "update_keys.json"
)
PRIVATE_KEY_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN ENCRYPTED PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
    b"-----BEGIN EC PRIVATE KEY-----",
    b"-----BEGIN DSA PRIVATE KEY-----",
)
FORBIDDEN_PRIVATE_KEY_NAMES = frozenset({"id_ed25519", "id_ecdsa", "id_rsa", "id_dsa"})
FORBIDDEN_PRIVATE_KEY_SUFFIXES = frozenset({".key", ".p12", ".pfx"})


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def load_reviewable_public_key(path: Path) -> Ed25519PublicKey:
    """Load only a PEM or OpenSSH public key container.

    Bare 32-byte/base64 values are deliberately rejected because an Ed25519
    private seed has the same length and could otherwise be imported by mistake.
    """

    value = path.read_bytes()
    if not value or len(value) > 64 * 1024:
        raise ManifestError("public key file is empty or unreasonably large")
    if any(marker in value for marker in PRIVATE_KEY_MARKERS):
        raise ManifestError("private key material is forbidden; provide only the public key")
    loaders = (serialization.load_pem_public_key, serialization.load_ssh_public_key)
    loaded: object | None = None
    for loader in loaders:
        try:
            loaded = loader(value)
            break
        except (TypeError, ValueError):
            continue
    if not isinstance(loaded, Ed25519PublicKey):
        raise ManifestError("public key must be an Ed25519 PEM or OpenSSH public key")
    return loaded


def reviewed_entry(
    public_key_path: Path,
    *,
    key_id: str,
    channels: Sequence[str],
    expected_fingerprint: str,
) -> dict[str, Any]:
    if KEY_ID.fullmatch(key_id) is None:
        raise ManifestError("invalid release key ID")
    if not channels or any(channel not in CHANNELS for channel in channels):
        raise ManifestError("at least one valid update channel is required")
    if len(set(channels)) != len(channels):
        raise ManifestError("release key channels must be unique")
    public_value = encoded_public_key(load_reviewable_public_key(public_key_path))
    fingerprint = public_key_fingerprint(public_value)
    if expected_fingerprint != fingerprint:
        raise ManifestError("reviewed public-key fingerprint does not match the supplied key")
    return {
        "algorithm": "Ed25519",
        "channels": sorted(channels),
        "key_id": key_id,
        "public_key": public_value,
        "public_key_sha256": fingerprint,
        "status": "active",
    }


def _write_pair_atomically(
    operator_path: Path,
    packaged_path: Path,
    value: dict[str, Any],
) -> None:
    """Replace both tracked copies, restoring the first on an ordinary failure."""

    if not operator_path.is_file() or not packaged_path.is_file():
        raise ManifestError("both tracked keyring files must already exist")
    previous_operator = operator_path.read_bytes()
    previous_packaged = packaged_path.read_bytes()
    encoded = _canonical_json(value)
    suffix = uuid.uuid4().hex
    operator_temp = operator_path.with_name(f".{operator_path.name}.{suffix}.tmp")
    packaged_temp = packaged_path.with_name(f".{packaged_path.name}.{suffix}.tmp")
    rollback_temp = operator_path.with_name(f".{operator_path.name}.{suffix}.rollback")
    try:
        operator_temp.write_bytes(encoded)
        packaged_temp.write_bytes(encoded)
        os.replace(operator_temp, operator_path)
        try:
            os.replace(packaged_temp, packaged_path)
        except OSError:
            rollback_temp.write_bytes(previous_operator)
            os.replace(rollback_temp, operator_path)
            raise
    finally:
        for temporary in (operator_temp, packaged_temp, rollback_temp):
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()
    if operator_path.read_bytes() != encoded or packaged_path.read_bytes() != encoded:
        # Restore both copies if an unusual filesystem filter changed the bytes.
        operator_path.write_bytes(previous_operator)
        packaged_path.write_bytes(previous_packaged)
        raise ManifestError("keyring replacement did not preserve exact reviewed bytes")


def import_reviewed_public_key(
    public_key_path: Path,
    *,
    key_id: str,
    channels: Sequence[str],
    expected_fingerprint: str,
    operator_path: Path = DEFAULT_OPERATOR_KEYRING,
    packaged_path: Path = DEFAULT_PACKAGED_KEYRING,
) -> dict[str, Any]:
    operator = validate_keyring_pair(operator_path, packaged_path)
    entry = reviewed_entry(
        public_key_path,
        key_id=key_id,
        channels=channels,
        expected_fingerprint=expected_fingerprint,
    )
    keys = operator["keys"]
    if any(item.get("key_id") == key_id for item in keys):
        raise ManifestError("release key ID already exists and cannot be reused")
    if any(item.get("public_key") == entry["public_key"] for item in keys):
        raise ManifestError("release public key already exists under another ID")
    updated = {"schema_version": 1, "keys": [*keys, entry]}
    validate_keyring(updated)
    _write_pair_atomically(operator_path, packaged_path, updated)
    return entry


def validate_keyring_pair(
    operator_path: Path,
    packaged_path: Path,
    *,
    required_channel: str | None = None,
) -> dict[str, Any]:
    if operator_path.read_bytes() != packaged_path.read_bytes():
        raise ManifestError("operator and packaged keyrings are not byte-for-byte equivalent")
    operator = load_keyring(operator_path)
    packaged = load_keyring(packaged_path)
    if operator != packaged:
        raise ManifestError("operator and packaged keyrings are not equivalent JSON")
    if required_channel is not None:
        if required_channel not in CHANNELS:
            raise ManifestError("unknown required keyring channel")
        if not any(
            key["status"] == "active" and required_channel in key["channels"]
            for key in operator["keys"]
        ):
            raise ManifestError(f"no active release key is trusted for {required_channel}")
    return operator


def tracked_paths(repository_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [repository_root / os.fsdecode(value) for value in result.stdout.split(b"\0") if value]


def contains_private_key_block(value: bytes) -> bool:
    """Detect complete PEM/OpenSSH private blocks without flagging policy text."""

    for begin_marker in PRIVATE_KEY_MARKERS:
        end_marker = begin_marker.replace(b"BEGIN", b"END", 1)
        begin = value.find(begin_marker)
        if begin >= 0 and value.find(end_marker, begin + len(begin_marker)) >= 0:
            return True
    return False


def audit_private_key_material(paths: Sequence[Path]) -> None:
    violations: list[str] = []
    for path in paths:
        name = path.name.casefold()
        if name in FORBIDDEN_PRIVATE_KEY_NAMES or path.suffix.casefold() in (
            FORBIDDEN_PRIVATE_KEY_SUFFIXES
        ):
            violations.append(path.as_posix())
            continue
        try:
            value = path.read_bytes()
        except OSError as exc:
            raise ManifestError(f"could not audit tracked path: {path.name}") from exc
        if contains_private_key_block(value):
            violations.append(path.as_posix())
    if violations:
        names = ", ".join(sorted(violations))
        raise ManifestError(f"tracked private-key material is forbidden: {names}")


def _parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="print the public value and review fingerprint")
    inspect.add_argument("--public-key", type=Path, required=True)
    import_key = commands.add_parser("import", help="import a separately reviewed public key")
    import_key.add_argument("--public-key", type=Path, required=True)
    import_key.add_argument("--key-id", required=True)
    import_key.add_argument("--channel", action="append", choices=sorted(CHANNELS), required=True)
    import_key.add_argument("--expected-fingerprint", required=True)
    import_key.add_argument("--operator-keyring", type=Path, default=DEFAULT_OPERATOR_KEYRING)
    import_key.add_argument("--packaged-keyring", type=Path, default=DEFAULT_PACKAGED_KEYRING)
    validate = commands.add_parser("validate", help="validate both public trust stores")
    validate.add_argument("--operator-keyring", type=Path, default=DEFAULT_OPERATOR_KEYRING)
    validate.add_argument("--packaged-keyring", type=Path, default=DEFAULT_PACKAGED_KEYRING)
    validate.add_argument("--require-channel", choices=sorted(CHANNELS))
    audit = commands.add_parser("audit", help="reject tracked private-key material")
    audit.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    return root


def main() -> int:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "inspect":
            public_value = encoded_public_key(load_reviewable_public_key(arguments.public_key))
            print(
                json.dumps(
                    {
                        "algorithm": "Ed25519",
                        "public_key": public_value,
                        "public_key_sha256": public_key_fingerprint(public_value),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        elif arguments.command == "import":
            entry = import_reviewed_public_key(
                arguments.public_key,
                key_id=arguments.key_id,
                channels=arguments.channel,
                expected_fingerprint=arguments.expected_fingerprint,
                operator_path=arguments.operator_keyring,
                packaged_path=arguments.packaged_keyring,
            )
            print(f"imported reviewed public key {entry['key_id']} ({entry['public_key_sha256']})")
        elif arguments.command == "validate":
            keyring = validate_keyring_pair(
                arguments.operator_keyring,
                arguments.packaged_keyring,
                required_channel=arguments.require_channel,
            )
            print(f"validated {len(keyring['keys'])} public release key(s)")
        else:
            paths = tracked_paths(arguments.repository_root.resolve())
            audit_private_key_material(paths)
            print(f"audited {len(paths)} tracked files; no private-key material detected")
        return 0
    except (ManifestError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(f"release keyring error: {exc}") from exc


if __name__ == "__main__":
    raise SystemExit(main())
