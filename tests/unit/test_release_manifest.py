from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from allthecontext.release_manifest import (
    ManifestError,
    canonical_payload,
    create_manifest,
    public_key_value,
    verify_manifest,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

TEST_ONLY_SEED = bytes(range(32))


def _release(tmp_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    artifact = tmp_path / "all-the-context-0.2.0-windows-x86_64.zip"
    artifact.write_bytes(b"deterministic test-only release artifact\n")
    private_key = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    manifest = create_manifest(
        artifact=artifact,
        version="0.2.0",
        channel="stable",
        platform_name="windows",
        architecture="x86_64",
        artifact_url=(
            "https://github.com/example/all-the-context/releases/download/"
            "v0.2.0/all-the-context-0.2.0-windows-x86_64.zip"
        ),
        minimum_supported_version="0.1.0",
        mandatory=False,
        release_notes_url="https://github.com/example/all-the-context/releases/tag/v0.2.0",
        key_id="test-only-2026",
        private_key=private_key,
    )
    keyring = {
        "schema_version": 1,
        "keys": [
            {
                "key_id": "test-only-2026",
                "algorithm": "Ed25519",
                "public_key": public_key_value(private_key),
                "channels": ["stable", "beta"],
                "status": "active",
            }
        ],
    }
    return manifest, keyring


def test_manifest_is_deterministic_and_verifies(tmp_path: Path) -> None:
    manifest, keyring = _release(tmp_path)
    repeated, _ = _release(tmp_path)
    assert json.dumps(manifest, sort_keys=True) == json.dumps(repeated, sort_keys=True)
    verify_manifest(manifest, keyring, current_version="0.1.0", expected_channel="stable")


def test_tamper_revocation_and_downgrade_are_rejected(tmp_path: Path) -> None:
    manifest, keyring = _release(tmp_path)
    tampered = {**manifest, "mandatory": True}
    with pytest.raises(ManifestError, match="signature"):
        verify_manifest(tampered, keyring)
    revoked = json.loads(json.dumps(keyring))
    revoked["keys"][0]["status"] = "revoked"
    with pytest.raises(ManifestError, match="revoked"):
        verify_manifest(manifest, revoked)
    with pytest.raises(ManifestError, match="downgrade"):
        verify_manifest(manifest, keyring, current_version="0.3.0")
    requires_manual = {**manifest, "minimum_supported_version": "0.1.1"}
    private_key = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    requires_manual["signature"] = (
        base64.urlsafe_b64encode(private_key.sign(canonical_payload(requires_manual)))
        .rstrip(b"=")
        .decode("ascii")
    )
    with pytest.raises(ManifestError, match="manual supported"):
        verify_manifest(requires_manual, keyring, current_version="0.1.0")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/all-the-context/releases/latest/download/app.zip",
        "https://raw.githubusercontent.com/example/all-the-context/main/app.zip",
        "http://downloads.example.test/v0.2.0/app.zip",
    ],
)
def test_mutable_or_insecure_artifact_urls_are_rejected(tmp_path: Path, url: str) -> None:
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"artifact")
    with pytest.raises(ManifestError):
        create_manifest(
            artifact=artifact,
            version="0.2.0",
            channel="stable",
            platform_name="linux",
            architecture="x86_64",
            artifact_url=url,
            minimum_supported_version="0.1.0",
            mandatory=False,
            release_notes_url="https://example.test/releases/v0.2.0",
            key_id="test-only-2026",
            private_key=Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED),
        )
