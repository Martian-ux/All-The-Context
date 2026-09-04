from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import pytest
from allthecontext import __version__, application_install
from allthecontext.build_identity import (
    BuildIdentity,
    BuildIdentityError,
    make_build_identity,
)
from allthecontext.release_manifest import (
    ManifestError,
    create_manifest,
    public_key_fingerprint,
    public_key_value,
    verify_manifest,
)
from allthecontext.updater import UpdateConfig, UpdateManager
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.build_desktop import helper_arguments
from scripts.package_desktop import build_platform_package

ROOT = Path(__file__).resolve().parents[2]
SOURCE_COMMIT = "a" * 40


def test_canonical_build_identity_agrees_with_project_and_runtime_metadata() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    identity = make_build_identity(
        version=__version__,
        source_commit=SOURCE_COMMIT,
        platform_name="windows",
        architecture="x86_64",
    )

    assert project["project"]["version"] == identity.version == __version__
    assert identity.channel == "beta"
    assert set(identity.as_dict()) == {
        "schema_version",
        "version",
        "channel",
        "platform",
        "architecture",
        "source_commit",
    }
    assert identity.sha256 == make_build_identity(
        version=__version__,
        source_commit=SOURCE_COMMIT,
        platform_name="windows",
        architecture="x86_64",
    ).sha256


def test_build_identity_rejects_contradictory_or_stale_metadata() -> None:
    identity = make_build_identity(
        version=__version__,
        source_commit=SOURCE_COMMIT,
        platform_name="linux",
        architecture="x86_64",
    )
    stale = identity.as_dict()
    stale["version"] = "0.1.0-beta.3"
    stale["channel"] = "stable"
    with pytest.raises(BuildIdentityError, match="version and channel"):
        BuildIdentity.from_mapping(stale)

    missing_commit = identity.as_dict()
    missing_commit.pop("source_commit")
    with pytest.raises(BuildIdentityError, match="fields differ"):
        BuildIdentity.from_mapping(missing_commit)


def test_windows_packaging_arguments_carry_identity_and_version_resource() -> None:
    identity = Path("build") / "identity" / "build-identity-v1.json"
    version_file = Path("build") / "identity" / "AllTheContextMCP-version.txt"
    arguments = helper_arguments(
        "Windows",
        identity_path=identity,
        version_file=version_file,
    )

    assert "--add-data" in arguments
    assert f"{identity}{os.pathsep}allthecontext" in arguments
    assert arguments[arguments.index("--version-file") + 1] == str(version_file)


def test_direct_package_report_publishes_the_same_identity(tmp_path: Path) -> None:
    executable = tmp_path / "all-the-context"
    executable.write_bytes(b"identity-bearing-package")
    _package, _checksum, _notice, report = build_platform_package(
        executable,
        tmp_path / "out",
        version=__version__,
        platform_name="linux",
        architecture="x86_64",
        source_commit=SOURCE_COMMIT,
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["build_identity"] == {
        "schema_version": 1,
        "version": __version__,
        "channel": "beta",
        "platform": "linux",
        "architecture": "x86_64",
        "source_commit": SOURCE_COMMIT,
    }
    assert payload["build_identity_sha256"] == make_build_identity(
        version=__version__,
        source_commit=SOURCE_COMMIT,
        platform_name="linux",
        architecture="x86_64",
    ).sha256


def test_stale_windows_registration_is_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    identity = make_build_identity(
        version=__version__,
        source_commit=SOURCE_COMMIT,
        platform_name="windows",
        architecture="x86_64",
    )

    class Key:
        def Close(self) -> None:
            return None

    class Registry:
        HKEY_CURRENT_USER = object()

        def __init__(self) -> None:
            self.values = {
                "DisplayVersion": "0.1.0-beta.3",
                "ATCReleaseChannel": "beta",
                "ATCSourceCommit": SOURCE_COMMIT,
                "ATCBuildIdentitySha256": identity.sha256,
            }

        def OpenKey(self, _root: object, _path: str) -> Key:
            return Key()

        def QueryValueEx(self, _key: Key, name: str) -> tuple[object, int]:
            return self.values[name], 1

    registry = Registry()
    monkeypatch.setattr(application_install.platform, "system", lambda: "Windows")
    monkeypatch.setattr(application_install, "windows_registry", lambda: registry)
    monkeypatch.setattr(application_install, "runtime_build_identity", lambda: identity)

    assert application_install.application_entrypoints_need_refresh() is True
    registry.values["DisplayVersion"] = identity.version
    assert application_install.application_entrypoints_need_refresh() is False


def test_packaged_update_verification_requires_the_source_commit() -> None:
    artifact = ROOT / "README.md"
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    keyring = {
        "schema_version": 1,
        "keys": [
            {
                "key_id": "identity-test-key",
                "algorithm": "Ed25519",
                "public_key": public_key_value(private_key),
                "public_key_sha256": public_key_fingerprint(public_key_value(private_key)),
                "channels": ["stable"],
                "status": "active",
            }
        ],
    }
    manifest = create_manifest(
        artifact=artifact,
        version="0.2.0",
        channel="stable",
        platform_name="windows",
        architecture="x86_64",
        artifact_url="https://updates.example.test/v0.2.0/package.zip",
        minimum_supported_version="0.1.0",
        mandatory=False,
        release_notes_url="https://updates.example.test/v0.2.0",
        key_id="identity-test-key",
        private_key=private_key,
    )

    with pytest.raises(ManifestError, match="source commit is required"):
        verify_manifest(manifest, keyring, require_source_commit=True)


def test_updater_state_exposes_the_verified_offered_identity(tmp_path: Path) -> None:
    artifact = tmp_path / "all-the-context-0.2.0.zip"
    artifact.write_bytes(b"signed update")
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public_value = public_key_value(private_key)
    keyring = {
        "schema_version": 1,
        "keys": [
            {
                "key_id": "identity-test-key",
                "algorithm": "Ed25519",
                "public_key": public_value,
                "public_key_sha256": public_key_fingerprint(public_value),
                "channels": ["stable"],
                "status": "active",
            }
        ],
    }
    keyring_path = tmp_path / "keys.json"
    keyring_path.write_text(json.dumps(keyring), encoding="utf-8")
    manifest = create_manifest(
        artifact=artifact,
        version="0.2.0",
        channel="stable",
        platform_name="windows",
        architecture="x86_64",
        artifact_url="https://updates.example.test/v0.2.0/all-the-context-0.2.0.zip",
        minimum_supported_version="0.1.0",
        mandatory=False,
        release_notes_url="https://updates.example.test/v0.2.0",
        key_id="identity-test-key",
        private_key=private_key,
        source_commit=SOURCE_COMMIT,
    )

    class Transport:
        def get_bytes(self, _url: str, *, maximum_bytes: int) -> bytes:
            assert maximum_bytes > 0
            return json.dumps(manifest).encode("utf-8")

    manager = UpdateManager(
        UpdateConfig(
            tmp_path / "updates",
            keyring_path,
            {"stable": "https://updates.example.test/manifest.json"},
            current_version="0.1.0",
            current_source_commit=SOURCE_COMMIT,
            platform_name="windows",
            architecture="x86_64",
        ),
        database_path=tmp_path / "core.sqlite3",
        transport=Transport(),
    )

    status = manager.check()
    assert status["offered_version"] == "0.2.0"
    assert status["current_source_commit"] == SOURCE_COMMIT
    assert status["offered_source_commit"] == SOURCE_COMMIT
