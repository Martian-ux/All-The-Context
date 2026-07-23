from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from allthecontext.release_candidate import (
    CANDIDATE_FILE_NAME,
    CANDIDATE_PROVENANCE_FILE_NAME,
    ReleaseTarget,
    archive_name,
    assemble_candidate,
    direct_package_names,
    prepare_beta_channel,
    signed_manifest_name,
    validate_github_release_state,
    verify_beta_channel_site,
    verify_candidate,
    verify_release_asset_set,
)
from allthecontext.release_manifest import (
    ManifestError,
    create_manifest,
    public_key_fingerprint,
    public_key_value,
    sha256_file,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts import release_candidate as release_candidate_script
from scripts.build_release_assets import build_archive, write_metadata, write_subject_sbom
from scripts.release_candidate import canonical_python_version, validate_source_metadata

TEST_ONLY_SEED = bytes(range(32))
VERSION = "0.1.0-beta.1"
SOURCE_COMMIT = "a" * 40
TARGET = ReleaseTarget("linux", "x86_64")


def _bundle(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json",
                "verificationMaterial": {},
                "dsseEnvelope": {},
            }
        ),
        encoding="utf-8",
    )


def _candidate_files(tmp_path: Path) -> tuple[Path, Path]:
    release_dir = tmp_path / "release"
    release_dir.mkdir(parents=True)
    source = tmp_path / "all-the-context"
    source.write_bytes(b"portable app\n")
    ota = build_archive(
        source,
        release_dir,
        version=VERSION,
        platform_name=TARGET.platform,
        architecture=TARGET.architecture,
    )
    write_metadata(ota, version=VERSION)
    for suffix in ("provenance.sigstore.json", "sbom.sigstore.json"):
        _bundle(release_dir / f"{ota.name}.{suffix}")

    names = direct_package_names(VERSION, TARGET)
    direct_package = release_dir / names["direct_package"]
    direct_package.write_bytes(b"direct portable package\n")
    digest, size = sha256_file(direct_package)
    (release_dir / names["direct_package_checksum"]).write_text(
        f"{digest}  {direct_package.name}\n", encoding="ascii"
    )
    notice = release_dir / names["direct_package_notice"]
    notice.write_text("IMPORTANT: unsigned community build\n", encoding="utf-8")
    (release_dir / names["direct_package_report"]).write_text(
        json.dumps(
            {
                "architecture": TARGET.architecture,
                "format": "tar.gz",
                "notice": notice.name,
                "package": direct_package.name,
                "platform": TARGET.platform,
                "schema_version": 1,
                "sha256": digest,
                "size": size,
                "source": source.name,
                "trust": "unsigned-community",
                "version": VERSION,
            }
        ),
        encoding="utf-8",
    )
    write_subject_sbom(direct_package, version=VERSION)
    _bundle(release_dir / names["direct_package_provenance_bundle"])
    _bundle(release_dir / names["direct_package_sbom_bundle"])
    candidate = assemble_candidate(
        release_dir,
        version=VERSION,
        channel="beta",
        source_commit=SOURCE_COMMIT,
        targets=[TARGET],
        ota_targets=[TARGET],
    )
    return release_dir, candidate


def _keyring(tmp_path: Path) -> tuple[Path, Ed25519PrivateKey]:
    private = Ed25519PrivateKey.from_private_bytes(TEST_ONLY_SEED)
    public = public_key_value(private)
    path = tmp_path / "keys.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "keys": [
                    {
                        "algorithm": "Ed25519",
                        "channels": ["beta"],
                        "key_id": "test-only-beta",
                        "public_key": public,
                        "public_key_sha256": public_key_fingerprint(public),
                        "status": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path, private


def test_candidate_inventories_direct_package_and_separate_ota(tmp_path: Path) -> None:
    release_dir, candidate_path = _candidate_files(tmp_path)

    candidate = verify_candidate(candidate_path, release_dir, expected_targets=[TARGET])

    artifact = candidate["artifacts"][0]
    assert artifact["direct_package"]["name"].endswith("-unsigned.tar.gz")
    assert artifact["ota_archive"]["name"] == archive_name(VERSION, TARGET)
    assert artifact["direct_package"]["name"] != artifact["ota_archive"]["name"]
    assert candidate["unsigned_community_build"] is True


def test_candidate_rejects_changed_direct_package_and_untracked_files(tmp_path: Path) -> None:
    release_dir, candidate_path = _candidate_files(tmp_path)
    direct_package = release_dir / direct_package_names(VERSION, TARGET)["direct_package"]
    direct_package.write_bytes(b"tampered")
    with pytest.raises(ManifestError, match="digest and size"):
        verify_candidate(candidate_path, release_dir)

    release_dir, _ = _candidate_files(tmp_path / "second")
    (release_dir / "unexpected.bin").write_bytes(b"extra")
    with pytest.raises(ManifestError, match="untracked files"):
        assemble_candidate(
            release_dir,
            version=VERSION,
            channel="beta",
            source_commit=SOURCE_COMMIT,
            targets=[TARGET],
            ota_targets=[TARGET],
            output=release_dir / "other-candidate.json",
        )


def test_draft_asset_allowlist_rejects_every_untracked_file(tmp_path: Path) -> None:
    release_dir, candidate_path = _candidate_files(tmp_path)
    _bundle(release_dir / CANDIDATE_PROVENANCE_FILE_NAME)

    allowed = verify_release_asset_set(candidate_path, release_dir, stage="draft")
    assert {path.name for path in allowed} == {path.name for path in release_dir.iterdir()}

    (release_dir / "evil.exe").write_bytes(b"not candidate-described")
    with pytest.raises(ManifestError, match=r"extra=\['evil\.exe'\]"):
        verify_release_asset_set(candidate_path, release_dir, stage="draft")


def test_signed_beta_channel_is_exact_and_reproducibly_verified(tmp_path: Path) -> None:
    release_dir, candidate_path = _candidate_files(tmp_path)
    keyring_path, private = _keyring(tmp_path)
    artifact_path = release_dir / archive_name(VERSION, TARGET)
    repository = "example/all-the-context"
    tag = f"v{VERSION}"
    manifest = create_manifest(
        artifact=artifact_path,
        version=VERSION,
        channel="beta",
        platform_name="linux",
        architecture="x86_64",
        artifact_url=(
            f"https://github.com/{repository}/releases/download/{tag}/{artifact_path.name}"
        ),
        minimum_supported_version=VERSION,
        mandatory=False,
        release_notes_url=f"https://github.com/{repository}/releases/tag/{tag}",
        key_id="test-only-beta",
        private_key=private,
    )
    (release_dir / signed_manifest_name("beta", TARGET)).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _bundle(release_dir / CANDIDATE_PROVENANCE_FILE_NAME)
    verify_release_asset_set(candidate_path, release_dir, stage="promotion")

    provenance = release_dir / CANDIDATE_PROVENANCE_FILE_NAME
    provenance.unlink()
    with pytest.raises(ManifestError, match=r"missing=.*provenance"):
        verify_release_asset_set(candidate_path, release_dir, stage="promotion")
    _bundle(provenance)

    candidate_digest, _ = sha256_file(candidate_path)
    site = tmp_path / "site"

    index = prepare_beta_channel(
        release_dir,
        candidate_path=candidate_path,
        candidate_sha256=candidate_digest,
        keyring_path=keyring_path,
        repository=repository,
        source_commit=SOURCE_COMMIT,
        output_dir=site,
    )

    assert index["version"] == VERSION
    assert (site / "beta/linux/x86_64/manifest-v1.json").is_file()
    assert verify_beta_channel_site(site, keyring_path=keyring_path) == index
    with pytest.raises(ManifestError, match="digest"):
        prepare_beta_channel(
            release_dir,
            candidate_path=candidate_path,
            candidate_sha256="0" * 64,
            keyring_path=keyring_path,
            repository=repository,
            source_commit=SOURCE_COMMIT,
            output_dir=tmp_path / "bad-site",
        )
    (release_dir / "manifest-beta-macos-arm64-v1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ManifestError, match="unexpected"):
        prepare_beta_channel(
            release_dir,
            candidate_path=candidate_path,
            candidate_sha256=candidate_digest,
            keyring_path=keyring_path,
            repository=repository,
            source_commit=SOURCE_COMMIT,
            output_dir=tmp_path / "unexpected-site",
        )


def _version_tree(root: Path, *, python_version: str, dashboard_version: str) -> None:
    (root / "packages/allthecontext/src/allthecontext").mkdir(parents=True)
    (root / "apps/dashboard").mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "all-the-context"\nversion = "{python_version}"\n', encoding="utf-8"
    )
    (root / "packages/allthecontext/src/allthecontext/__init__.py").write_text(
        f'__version__ = "{python_version}"\n', encoding="utf-8"
    )
    (root / "apps/dashboard/package.json").write_text(
        json.dumps({"version": dashboard_version}), encoding="utf-8"
    )
    (root / "apps/dashboard/package-lock.json").write_text(
        json.dumps(
            {"version": dashboard_version, "packages": {"": {"version": dashboard_version}}}
        ),
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        f'[[package]]\nname = "all-the-context"\nversion = "{python_version}"\n',
        encoding="utf-8",
    )


def test_source_gate_accepts_python_canonical_beta_but_requires_raw_web_semver(
    tmp_path: Path,
) -> None:
    canonical = canonical_python_version(VERSION)
    assert canonical == "0.1.0b1"
    _version_tree(tmp_path, python_version=canonical, dashboard_version=VERSION)
    validate_source_metadata(
        tmp_path,
        version=VERSION,
        channel="beta",
        source_commit=SOURCE_COMMIT,
        checked_out_commit=SOURCE_COMMIT,
    )

    package = json.loads((tmp_path / "apps/dashboard/package.json").read_text(encoding="utf-8"))
    package["version"] = "0.1.0"
    (tmp_path / "apps/dashboard/package.json").write_text(json.dumps(package), encoding="utf-8")
    with pytest.raises(ManifestError, match="dashboard package"):
        validate_source_metadata(
            tmp_path,
            version=VERSION,
            channel="beta",
            source_commit=SOURCE_COMMIT,
            checked_out_commit=SOURCE_COMMIT,
        )


def test_validate_source_cli_reads_the_actual_checked_out_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _version_tree(
        tmp_path, python_version=canonical_python_version(VERSION), dashboard_version=VERSION
    )
    calls: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        del kwargs
        calls.append(command)
        return SimpleNamespace(stdout=f"{SOURCE_COMMIT}\n")

    monkeypatch.setattr(release_candidate_script.subprocess, "run", fake_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_candidate.py",
            "validate-source",
            "--project-root",
            str(tmp_path),
            "--version",
            VERSION,
            "--channel",
            "beta",
            "--source-commit",
            SOURCE_COMMIT,
        ],
    )

    assert release_candidate_script.main() == 0
    assert calls == [["git", "-C", str(tmp_path), "rev-parse", "HEAD"]]


def test_runner_gate_rejects_mislabeled_macos_architecture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(release_candidate_script.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(release_candidate_script.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(release_candidate_script.struct, "calcsize", lambda _format: 8)

    release_candidate_script.validate_runner_target("macos", "arm64")
    with pytest.raises(ManifestError, match="mislabeled"):
        release_candidate_script.validate_runner_target("macos", "x86_64")


def test_release_state_requires_exact_draft_or_immutable_beta() -> None:
    state = {
        "assets": [{"name": CANDIDATE_FILE_NAME}],
        "isDraft": True,
        "isImmutable": False,
        "isPrerelease": True,
        "tagName": f"v{VERSION}",
        "targetCommitish": SOURCE_COMMIT,
    }
    validate_github_release_state(
        state,
        tag=f"v{VERSION}",
        source_commit=SOURCE_COMMIT,
        draft=True,
        immutable=False,
        expected_asset_names={CANDIDATE_FILE_NAME},
    )
    state["assets"].append({"name": "evil.exe"})
    with pytest.raises(ManifestError, match=r"extra=\['evil\.exe'\]"):
        validate_github_release_state(
            state,
            tag=f"v{VERSION}",
            source_commit=SOURCE_COMMIT,
            draft=True,
            immutable=False,
            expected_asset_names={CANDIDATE_FILE_NAME},
        )
    state["assets"].pop()
    state["isImmutable"] = True
    with pytest.raises(ManifestError, match="state"):
        validate_github_release_state(
            state,
            tag=f"v{VERSION}",
            source_commit=SOURCE_COMMIT,
            draft=True,
            immutable=False,
        )


@pytest.mark.parametrize("field", ["sha256", "size", "trust", "format"])
def test_direct_package_report_tampering_is_rejected(tmp_path: Path, field: str) -> None:
    release_dir, candidate_path = _candidate_files(tmp_path)
    report = release_dir / direct_package_names(VERSION, TARGET)["direct_package_report"]
    value: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    value[field] = "wrong"
    report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ManifestError):
        verify_candidate(candidate_path, release_dir)
