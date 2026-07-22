from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.release_manifest import ManifestError, public_key_fingerprint
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.release_keyring import (
    audit_private_key_material,
    contains_private_key_block,
    import_reviewed_public_key,
    load_reviewable_public_key,
    reviewed_entry,
    validate_keyring_pair,
)

TEST_ONLY_SEED = bytes(reversed(range(32)))


def _public_key(path: Path) -> Path:
    public = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED).public_key()
    path.write_bytes(
        public.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return path


def _empty_keyrings(tmp_path: Path) -> tuple[Path, Path]:
    value = '{"schema_version": 1, "keys": []}\n'
    operator = tmp_path / "keys.json"
    packaged = tmp_path / "update_keys.json"
    operator.write_text(value, encoding="utf-8")
    packaged.write_text(value, encoding="utf-8")
    return operator, packaged


def test_reviewed_public_key_import_updates_both_trust_stores(tmp_path: Path) -> None:
    public_path = _public_key(tmp_path / "release.pub.pem")
    operator, packaged = _empty_keyrings(tmp_path)
    preliminary = reviewed_entry(
        public_path,
        key_id="release-test-2026",
        channels=["beta"],
        expected_fingerprint=(
            "sha256:141ddf2e77d4f690748cf74ecd390d44687d477b31b8931fa37abd02c35dbaba"
        ),
    )

    imported = import_reviewed_public_key(
        public_path,
        key_id="release-test-2026",
        channels=["beta"],
        expected_fingerprint=preliminary["public_key_sha256"],
        operator_path=operator,
        packaged_path=packaged,
    )

    assert imported == preliminary
    assert operator.read_bytes() == packaged.read_bytes()
    keyring = validate_keyring_pair(operator, packaged, required_channel="beta")
    assert keyring["keys"] == [preliminary]
    assert public_key_fingerprint(imported["public_key"]) == imported["public_key_sha256"]


def test_public_key_import_fails_closed_on_unreviewed_or_private_material(tmp_path: Path) -> None:
    public_path = _public_key(tmp_path / "release.pub.pem")
    with pytest.raises(ManifestError, match="fingerprint"):
        reviewed_entry(
            public_path,
            key_id="release-test-2026",
            channels=["beta"],
            expected_fingerprint=f"sha256:{'0' * 64}",
        )

    private_path = tmp_path / "forbidden-private.pem"
    private_path.write_bytes(
        Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED).private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    with pytest.raises(ManifestError, match="private key material is forbidden"):
        load_reviewable_public_key(private_path)
    with pytest.raises(ManifestError, match="tracked private-key material"):
        audit_private_key_material([private_path])


def test_private_key_audit_allows_policy_text_but_detects_complete_blocks() -> None:
    marker_reference = b'policy = b"-----BEGIN ENCRYPTED PRIVATE KEY-----"'
    complete_block = (
        b"-----BEGIN " b"ENCRYPTED PRIVATE KEY-----\n"
        b"dGVzdC1vbmx5LW5vdC1hLXJlYWwta2V5\n"
        b"-----END " b"ENCRYPTED PRIVATE KEY-----\n"
    )

    assert contains_private_key_block(marker_reference) is False
    assert contains_private_key_block(complete_block) is True


def test_keyring_pair_rejects_drift_and_fingerprint_tampering(tmp_path: Path) -> None:
    operator, packaged = _empty_keyrings(tmp_path)
    packaged.write_text(json.dumps({"schema_version": 1, "keys": []}), encoding="utf-8")
    with pytest.raises(ManifestError, match="byte-for-byte"):
        validate_keyring_pair(operator, packaged)

    packaged.write_bytes(operator.read_bytes())
    packaged.write_text('{"schema_version": 1, "keys": [{"bad": true}]}', encoding="utf-8")
    with pytest.raises(ManifestError):
        validate_keyring_pair(operator, packaged)
