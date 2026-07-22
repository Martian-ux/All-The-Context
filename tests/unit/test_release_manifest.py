from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from allthecontext.release_manifest import (
    ManifestError,
    canonical_payload,
    create_manifest,
    public_key_fingerprint,
    public_key_value,
    verify_manifest,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import release_manifest as release_manifest_script
from scripts.release_manifest import (
    load_encrypted_private_key_interactive,
    require_private_key_outside_repository,
)

TEST_ONLY_SEED = bytes(range(32))
ROOT = Path(__file__).resolve().parents[2]


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
                "public_key_sha256": public_key_fingerprint(public_key_value(private_key)),
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


def test_packaged_update_keyring_matches_operator_keyring() -> None:
    operator = json.loads((ROOT / "release" / "keys.json").read_text(encoding="utf-8"))
    packaged = json.loads(
        (
            ROOT / "packages" / "allthecontext" / "src" / "allthecontext" / "update_keys.json"
        ).read_text(encoding="utf-8")
    )
    assert packaged == operator


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


def test_offline_signing_key_must_resolve_outside_checkout(tmp_path: Path) -> None:
    repository = tmp_path / "checkout"
    repository.mkdir()
    inside = repository / "release-private.pem"
    outside = tmp_path / "offline-private.pem"
    inside.write_text("test-only", encoding="utf-8")
    outside.write_text("test-only", encoding="utf-8")

    with pytest.raises(ManifestError, match="outside"):
        require_private_key_outside_repository(inside, repository)
    assert require_private_key_outside_repository(outside, repository) == outside.resolve()


def test_offline_signing_loads_password_protected_key_with_no_echo_prompt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    password = "test-only-password"
    private = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    encrypted = tmp_path / "encrypted-private.pem"
    encrypted.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(password.encode()),
        )
    )
    prompts: list[str] = []
    monkeypatch.setattr(release_manifest_script.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        release_manifest_script.getpass,
        "getpass",
        lambda prompt: prompts.append(prompt) or password,
    )

    loaded = load_encrypted_private_key_interactive(encrypted)

    assert public_key_value(loaded) == public_key_value(private)
    assert prompts == ["Offline release key password: "]


def test_offline_signing_rejects_plaintext_key_and_noninteractive_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    plaintext = tmp_path / "plaintext-private.pem"
    plaintext.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    with pytest.raises(ManifestError, match="encrypted PKCS8"):
        load_encrypted_private_key_interactive(plaintext)

    encrypted = tmp_path / "encrypted-private.pem"
    encrypted.write_bytes(
        private.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(b"test-only-password"),
        )
    )
    monkeypatch.setattr(release_manifest_script.sys.stdin, "isatty", lambda: False)
    with pytest.raises(ManifestError, match="interactive terminal"):
        load_encrypted_private_key_interactive(encrypted)
