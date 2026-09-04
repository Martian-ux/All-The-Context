from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from allthecontext.build_identity import make_build_identity
from allthecontext.exact_source_gate import (
    CANONICAL_CI_WORKFLOW_NAME,
    CANONICAL_CI_WORKFLOW_PATH,
    REQUIRED_CI_JOBS,
)
from allthecontext.release_candidate import (
    CANDIDATE_FILE_NAME,
    CANDIDATE_PROVENANCE_FILE_NAME,
    COMPONENT_INVENTORY_CHECKSUM_FILE_NAME,
    COMPONENT_INVENTORY_FILE_NAME,
    MATRIX_EVIDENCE_FILE_NAME,
    NOTICES_FILE_NAME,
    ReleaseTarget,
    archive_name,
    assemble_candidate,
    direct_package_names,
    normalize_github_release_state,
    prepare_beta_channel,
    select_github_release_from_api_listing,
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
from scripts.release_candidate import (
    canonical_python_version,
    validate_public_readiness_docs,
    validate_source_metadata,
)

TEST_ONLY_SEED = bytes(range(32))
VERSION = "0.1.0-beta.1"
SOURCE_COMMIT = "a" * 40
TARGET = ReleaseTarget("linux", "x86_64")
WINDOWS_TARGET = ReleaseTarget("windows", "x86_64")
MACOS_TARGET = ReleaseTarget("macos", "arm64")
DIRECT_PACKAGE_FIELDS = {
    "windows": {
        "format": "exe",
        "recovery_console_helper": "AllTheContextRecovery.exe",
        "recovery_surface": "embedded-console-helper",
    },
    "macos": {
        "format": "dmg",
        "recovery_console_helper": "all-the-context-recovery",
        "recovery_surface": "bundled-console-helper",
    },
    "linux": {
        "format": "tar.gz",
        "recovery_console_helper": "all-the-context",
        "recovery_surface": "console-main-binary",
    },
}


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


def _source_evidence(release_dir: Path) -> None:
    inventory = release_dir / COMPONENT_INVENTORY_FILE_NAME
    inventory.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": VERSION,
                "source_commit": SOURCE_COMMIT,
                "project_version": VERSION,
                "locks": {
                    "uv.lock": {"sha256": "a" * 64},
                    "apps/dashboard/package-lock.json": {"sha256": "b" * 64},
                },
                "component_count": 1,
                "components": [
                    {
                        "ecosystem": "python",
                        "name": "all-the-context",
                        "version": VERSION,
                        "license": "MIT",
                        "locked": True,
                        "source_kind": "path",
                        "scope": "runtime",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    digest, _ = sha256_file(inventory)
    (release_dir / COMPONENT_INVENTORY_CHECKSUM_FILE_NAME).write_text(
        f"{digest}  {inventory.name}\n", encoding="ascii", newline="\n"
    )
    (release_dir / MATRIX_EVIDENCE_FILE_NAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_commit": SOURCE_COMMIT,
                "workflow_path": CANONICAL_CI_WORKFLOW_PATH,
                "workflow_name": CANONICAL_CI_WORKFLOW_NAME,
                "workflow_run_id": 42,
                "run_status": "completed",
                "run_conclusion": "success",
                "job_records": [
                    {
                        "name": name,
                        "run_id": 42,
                        "head_sha": SOURCE_COMMIT,
                        "status": "completed",
                        "conclusion": "success",
                    }
                    for name in REQUIRED_CI_JOBS
                ],
                "required_jobs": list(REQUIRED_CI_JOBS),
                "ok": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (release_dir / NOTICES_FILE_NAME).write_text(
        "All The Context component inventory notices\n"
        f"Source commit: {SOURCE_COMMIT}\n"
        f"Release version: {VERSION}\n",
        encoding="utf-8",
    )


def _write_target_artifacts(release_dir: Path, tmp_path: Path, target: ReleaseTarget) -> None:
    source = tmp_path / f"all-the-context-{target.platform}-{target.architecture}"
    source.write_bytes(b"portable app\n")
    ota = build_archive(
        source,
        release_dir,
        version=VERSION,
        platform_name=target.platform,
        architecture=target.architecture,
    )
    write_metadata(ota, version=VERSION)
    for suffix in ("provenance.sigstore.json", "sbom.sigstore.json"):
        _bundle(release_dir / f"{ota.name}.{suffix}")

    names = direct_package_names(VERSION, target)
    direct_package = release_dir / names["direct_package"]
    direct_package.write_bytes(b"direct portable package\n")
    digest, size = sha256_file(direct_package)
    (release_dir / names["direct_package_checksum"]).write_text(
        f"{digest}  {direct_package.name}\n", encoding="ascii"
    )
    notice = release_dir / names["direct_package_notice"]
    notice.write_text("IMPORTANT: unsigned community build\n", encoding="utf-8")
    fields = DIRECT_PACKAGE_FIELDS[target.platform]
    identity = make_build_identity(
        version=VERSION,
        platform_name=target.platform,
        architecture=target.architecture,
        source_commit=SOURCE_COMMIT,
    )
    (release_dir / names["direct_package_report"]).write_text(
        json.dumps(
            {
                "architecture": target.architecture,
                "build_identity": identity.as_dict(),
                "build_identity_sha256": identity.sha256,
                "channel": identity.channel,
                "format": fields["format"],
                "notice": notice.name,
                "package": direct_package.name,
                "platform": target.platform,
                "recovery_console_helper": fields["recovery_console_helper"],
                "recovery_surface": fields["recovery_surface"],
                "schema_version": 1,
                "sha256": digest,
                "size": size,
                "source_commit": identity.source_commit,
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


def _candidate_files(
    tmp_path: Path,
    *,
    targets: list[ReleaseTarget] | None = None,
    ota_targets: list[ReleaseTarget] | None = None,
) -> tuple[Path, Path]:
    selected = targets or [TARGET]
    selected_ota = ota_targets or selected
    release_dir = tmp_path / "release"
    release_dir.mkdir(parents=True)
    _source_evidence(release_dir)
    for target in selected:
        _write_target_artifacts(release_dir, tmp_path, target)
    candidate = assemble_candidate(
        release_dir,
        version=VERSION,
        channel="beta",
        source_commit=SOURCE_COMMIT,
        targets=selected,
        ota_targets=selected_ota,
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


def test_public_release_notes_exclude_macos_support_and_assets(tmp_path: Path) -> None:
    release_dir, candidate_path = _candidate_files(
        tmp_path,
        targets=[WINDOWS_TARGET, TARGET],
        ota_targets=[WINDOWS_TARGET],
    )
    output = tmp_path / "release-notes.md"

    release_candidate_script._write_notes(candidate_path, release_dir, output)

    notes = output.read_text(encoding="utf-8")
    assert "Supported release targets: Windows 11 x86-64 and Ubuntu 24.04 LTS x86-64" in notes
    assert "Windows x86-64 is the only OTA target; Linux is direct install only" in notes
    assert "macOS is not supported and no macOS package is included" in notes
    assert "one-click setup" in notes
    assert "portable archive" in notes
    assert "eligible" in notes
    assert "withheld" in notes
    assert ".dmg" not in notes
    assert "open-and-launch" not in notes
    assert "notarization" not in notes
    assert archive_name(VERSION, WINDOWS_TARGET) in notes
    assert archive_name(VERSION, TARGET) in notes
    assert direct_package_names(VERSION, WINDOWS_TARGET)["direct_package"] in notes
    assert direct_package_names(VERSION, TARGET)["direct_package"] in notes


def test_public_release_notes_reject_macos_assets(tmp_path: Path) -> None:
    release_dir, candidate_path = _candidate_files(
        tmp_path,
        targets=[MACOS_TARGET],
        ota_targets=[MACOS_TARGET],
    )
    output = tmp_path / "release-notes.md"

    with pytest.raises(ManifestError, match="do not support macos packages or DMG"):
        release_candidate_script._write_notes(candidate_path, release_dir, output)

    assert not output.exists()


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
        source_commit=SOURCE_COMMIT,
    )
    (release_dir / signed_manifest_name("beta", TARGET)).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _bundle(release_dir / CANDIDATE_PROVENANCE_FILE_NAME)
    verify_release_asset_set(candidate_path, release_dir, stage="signed")
    (release_dir / "acceptance-receipt-bundle-v1.json").write_text("{}\n", encoding="utf-8")
    (release_dir / "publication-gate-record.json").write_text("{}\n", encoding="utf-8")
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
    published_index = site / "beta" / "index-v1.json"
    index_value = json.loads(published_index.read_text(encoding="utf-8"))
    index_value["source_commit"] = "b" * 40
    published_index.write_text(json.dumps(index_value), encoding="utf-8")
    with pytest.raises(ManifestError, match="candidate identity"):
        verify_beta_channel_site(site, keyring_path=keyring_path)
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
    manifest_path = release_dir / signed_manifest_name("beta", TARGET)
    manifest_without_source = dict(manifest)
    manifest_without_source.pop("source_commit")
    manifest_path.write_text(json.dumps(manifest_without_source), encoding="utf-8")
    with pytest.raises(ManifestError, match="source commit is required"):
        prepare_beta_channel(
            release_dir,
            candidate_path=candidate_path,
            candidate_sha256=candidate_digest,
            keyring_path=keyring_path,
            repository=repository,
            source_commit=SOURCE_COMMIT,
            output_dir=tmp_path / "missing-source-site",
        )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
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
    (root / "docs/operations").mkdir(parents=True)
    (root / "SUPPORT.md").write_text(
        "Triage at https://github.com/Martian-ux/All-The-Context/issues/new. "
        "Security uses https://github.com/Martian-ux/All-The-Context/security/advisories/new.\n",
        encoding="utf-8",
    )
    (root / "docs/KNOWN_ISSUES.md").write_text(
        "Severity P3. Impact. Workaround. Owner. Post-V1.\n",
        encoding="utf-8",
    )
    (root / "SECURITY.md").write_text(
        "Private vulnerability reporting. Removed routes return HTTP 404. "
        "Credential setup does not silently use plaintext.\n",
        encoding="utf-8",
    )
    (root / "docs/operations/RUNBOOK.md").write_text(
        "Backup before restore.\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "[support](SUPPORT.md) [known issues](docs/KNOWN_ISSUES.md) "
        "[security](SECURITY.md) [recovery runbook](docs/operations/RUNBOOK.md)\n",
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


def test_source_gate_requires_linked_public_readiness_documents(tmp_path: Path) -> None:
    _version_tree(
        tmp_path,
        python_version=canonical_python_version(VERSION),
        dashboard_version=VERSION,
    )
    validate_public_readiness_docs(tmp_path)

    (tmp_path / "SUPPORT.md").unlink()
    with pytest.raises(ManifestError, match=r"release readiness document is missing: SUPPORT\.md"):
        validate_public_readiness_docs(tmp_path)

    (tmp_path / "SUPPORT.md").write_text(
        "Triage at https://github.com/Martian-ux/All-The-Context/issues/new. "
        "Security uses https://github.com/Martian-ux/All-The-Context/security/advisories/new.\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("[support](SUPPORT.md)\n", encoding="utf-8")
    with pytest.raises(ManifestError, match="README release-readiness links are incomplete"):
        validate_public_readiness_docs(tmp_path)


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


def test_unpublished_draft_resolves_uniquely_from_paginated_api_listing() -> None:
    state = {
        "id": 360008392,
        "assets": [
            {
                "id": 41,
                "name": CANDIDATE_FILE_NAME,
                "size": 123,
                "digest": f"sha256:{'c' * 64}",
            }
        ],
        "draft": True,
        "immutable": False,
        "prerelease": True,
        "tag_name": f"v{VERSION}",
        "target_commitish": SOURCE_COMMIT,
    }

    normalized = select_github_release_from_api_listing([[state]], tag=f"v{VERSION}")

    assert normalized == {
        "releaseId": 360008392,
        "assets": [
            {
                "id": 41,
                "name": CANDIDATE_FILE_NAME,
                "size": 123,
                "digest": f"sha256:{'c' * 64}",
            }
        ],
        "isDraft": True,
        "isImmutable": False,
        "isPrerelease": True,
        "tagName": f"v{VERSION}",
        "targetCommitish": SOURCE_COMMIT,
    }
    validate_github_release_state(
        normalized,
        tag=f"v{VERSION}",
        source_commit=SOURCE_COMMIT,
        draft=True,
        immutable=False,
        expected_asset_descriptors={CANDIDATE_FILE_NAME: ("c" * 64, 123)},
    )


def test_draft_listing_rejects_duplicate_tag_and_incomplete_asset_metadata() -> None:
    state = {
        "id": 1,
        "assets": [
            {
                "id": 2,
                "name": CANDIDATE_FILE_NAME,
                "size": 10,
                "digest": f"sha256:{'d' * 64}",
            }
        ],
        "draft": True,
        "immutable": False,
        "prerelease": True,
        "tag_name": f"v{VERSION}",
        "target_commitish": SOURCE_COMMIT,
    }
    with pytest.raises(ManifestError, match="exactly one"):
        select_github_release_from_api_listing([state, state], tag=f"v{VERSION}")

    incomplete = dict(state)
    incomplete["assets"] = [{"id": 2, "name": CANDIDATE_FILE_NAME, "size": 10}]
    with pytest.raises(ManifestError, match="ID, size, and SHA-256"):
        select_github_release_from_api_listing([incomplete], tag=f"v{VERSION}")


def test_release_asset_descriptor_mismatch_is_rejected() -> None:
    state = normalize_github_release_state(
        {
            "assets": [
                {
                    "name": CANDIDATE_FILE_NAME,
                    "size": 123,
                    "digest": f"sha256:{'e' * 64}",
                }
            ],
            "draft": True,
            "immutable": False,
            "prerelease": True,
            "tag_name": f"v{VERSION}",
            "target_commitish": SOURCE_COMMIT,
        }
    )
    with pytest.raises(ManifestError, match="digest or size"):
        validate_github_release_state(
            state,
            tag=f"v{VERSION}",
            source_commit=SOURCE_COMMIT,
            draft=True,
            immutable=False,
            expected_asset_descriptors={CANDIDATE_FILE_NAME: ("f" * 64, 123)},
        )


def test_published_cli_state_accepts_opaque_graphql_asset_ids() -> None:
    state = {
        "assets": [
            {
                "id": "RA_kwDOTesF6s4fQ0j8",
                "name": CANDIDATE_FILE_NAME,
                "size": 123,
                "digest": f"sha256:{'e' * 64}",
            }
        ],
        "isDraft": False,
        "isImmutable": True,
        "isPrerelease": True,
        "tagName": f"v{VERSION}",
        "targetCommitish": SOURCE_COMMIT,
    }

    normalized = normalize_github_release_state(state)

    assert normalized["assets"][0]["id"] is None
    validate_github_release_state(
        normalized,
        tag=f"v{VERSION}",
        source_commit=SOURCE_COMMIT,
        draft=False,
        immutable=True,
        expected_asset_descriptors={CANDIDATE_FILE_NAME: ("e" * 64, 123)},
    )


def test_opaque_graphql_asset_id_is_not_accepted_as_rest_api_metadata() -> None:
    state = {
        "assets": [
            {
                "id": "RA_kwDOTesF6s4fQ0j8",
                "name": CANDIDATE_FILE_NAME,
                "size": 123,
                "digest": f"sha256:{'e' * 64}",
            }
        ],
        "isDraft": False,
        "isImmutable": True,
        "isPrerelease": True,
        "tagName": f"v{VERSION}",
        "targetCommitish": SOURCE_COMMIT,
    }

    with pytest.raises(ManifestError, match="numeric REST ID"):
        normalize_github_release_state(state, require_asset_api_metadata=True)


def test_release_cli_resolves_draft_id_and_lists_safe_asset_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    listing = tmp_path / "release-list.json"
    state = tmp_path / "release-state.json"
    listing.write_text(
        json.dumps(
            [
                [
                    {
                        "id": 360008392,
                        "assets": [
                            {
                                "id": 7,
                                "name": CANDIDATE_FILE_NAME,
                                "size": 1,
                                "digest": f"sha256:{'a' * 64}",
                            }
                        ],
                        "draft": True,
                        "immutable": False,
                        "prerelease": True,
                        "tag_name": f"v{VERSION}",
                        "target_commitish": SOURCE_COMMIT,
                    }
                ]
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_candidate.py",
            "resolve-release",
            "--input",
            str(listing),
            "--tag",
            f"v{VERSION}",
            "--source-commit",
            SOURCE_COMMIT,
            "--draft",
            "true",
            "--immutable",
            "false",
            "--output",
            str(state),
        ],
    )
    assert release_candidate_script.main() == 0
    assert capsys.readouterr().out == "360008392\n"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_candidate.py",
            "list-release-assets",
            "--input",
            str(state),
        ],
    )
    assert release_candidate_script.main() == 0
    assert capsys.readouterr().out == f"7\t{CANDIDATE_FILE_NAME}\n"


@pytest.mark.parametrize("field", ["sha256", "size", "trust", "format"])
def test_direct_package_report_tampering_is_rejected(tmp_path: Path, field: str) -> None:
    release_dir, candidate_path = _candidate_files(tmp_path)
    report = release_dir / direct_package_names(VERSION, TARGET)["direct_package_report"]
    value: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    value[field] = "wrong"
    report.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ManifestError):
        verify_candidate(candidate_path, release_dir)


@pytest.mark.parametrize(
    "field", ["channel", "source_commit", "build_identity", "build_identity_sha256"]
)
def test_direct_package_report_requires_complete_source_identity(
    tmp_path: Path, field: str
) -> None:
    release_dir, candidate_path = _candidate_files(tmp_path)
    report = release_dir / direct_package_names(VERSION, TARGET)["direct_package_report"]
    value: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    value.pop(field)
    report.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(ManifestError):
        verify_candidate(candidate_path, release_dir)
