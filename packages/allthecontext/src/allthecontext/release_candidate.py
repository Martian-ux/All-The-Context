"""Fail-closed assembly and verification for native release candidates."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from .exact_source_gate import load_matrix_evidence
from .release_manifest import (
    ARCHITECTURES,
    CHANNELS,
    PLATFORMS,
    SHA256,
    ManifestError,
    ReleaseVersion,
    load_keyring,
    sha256_file,
    verify_manifest,
)

CANDIDATE_SCHEMA_VERSION = 1
CANDIDATE_FILE_NAME = "release-candidate-v1.json"
CANDIDATE_PROVENANCE_FILE_NAME = f"{CANDIDATE_FILE_NAME}.provenance.sigstore.json"
CHANNEL_INDEX_FILE_NAME = "index-v1.json"
MATRIX_EVIDENCE_FILE_NAME = "matrix-evidence.json"
COMPONENT_INVENTORY_FILE_NAME = "component-inventory-v1.json"
COMPONENT_INVENTORY_CHECKSUM_FILE_NAME = f"{COMPONENT_INVENTORY_FILE_NAME}.sha256"
NOTICES_FILE_NAME = "NOTICES.txt"
ACCEPTANCE_RECEIPT_BUNDLE_FILE_NAME = "acceptance-receipt-bundle-v1.json"
PUBLICATION_GATE_RECORD_FILE_NAME = "publication-gate-record.json"
SOURCE_EVIDENCE_FIELDS = (
    "matrix_evidence",
    "component_inventory",
    "component_inventory_checksum",
    "notices",
)
SOURCE_EVIDENCE_FILE_NAMES = {
    "matrix_evidence": MATRIX_EVIDENCE_FILE_NAME,
    "component_inventory": COMPONENT_INVENTORY_FILE_NAME,
    "component_inventory_checksum": COMPONENT_INVENTORY_CHECKSUM_FILE_NAME,
    "notices": NOTICES_FILE_NAME,
}
DECISION_ASSET_NAMES = frozenset(
    {
        ACCEPTANCE_RECEIPT_BUNDLE_FILE_NAME,
        PUBLICATION_GATE_RECORD_FILE_NAME,
    }
)
COMMIT = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
SAFE_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")
MAX_JSON_BYTES = 16 * 1024 * 1024
DIRECT_PACKAGE_SUFFIXES = {
    "windows": ".exe",
    "macos": ".dmg",
    "linux": ".tar.gz",
}
ASSET_STAGES = frozenset({"draft", "signed", "promotion"})


@dataclass(frozen=True, order=True)
class ReleaseTarget:
    platform: str
    architecture: str

    @classmethod
    def parse(cls, value: str) -> ReleaseTarget:
        parts = value.split(":")
        if len(parts) != 2 or parts[0] not in PLATFORMS or parts[1] not in ARCHITECTURES:
            raise ManifestError(f"invalid release target: {value!r}")
        return cls(parts[0], parts[1])


def _read_json_object(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ManifestError(f"release metadata is too large: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"release metadata is not valid UTF-8 JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ManifestError(f"release metadata must be a JSON object: {path.name}")
    return cast(dict[str, Any], value)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        raise ManifestError(f"refusing to replace existing release metadata: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _descriptor(path: Path) -> dict[str, Any]:
    digest, size = sha256_file(path)
    if size <= 0:
        raise ManifestError(f"release file is empty: {path.name}")
    return {"name": path.name, "sha256": digest, "size": size}


def _descriptor_path(
    directory: Path,
    descriptor: object,
    field: str,
    *,
    seen_names: set[str] | None = None,
) -> Path:
    if not isinstance(descriptor, dict) or set(descriptor) != {"name", "sha256", "size"}:
        raise ManifestError(f"candidate {field} descriptor is malformed")
    name = descriptor.get("name")
    digest = descriptor.get("sha256")
    size = descriptor.get("size")
    if not isinstance(name, str) or SAFE_FILE_NAME.fullmatch(name) is None:
        raise ManifestError(f"candidate {field} name is unsafe")
    if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
        raise ManifestError(f"candidate {field} digest is malformed")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ManifestError(f"candidate {field} size is malformed")
    if seen_names is not None:
        if name in seen_names or name.casefold() in {item.casefold() for item in seen_names}:
            raise ManifestError(f"candidate descriptor file is duplicated: {name}")
        seen_names.add(name)
    path = directory / name
    if not path.is_file() or path.is_symlink():
        raise ManifestError(f"candidate {field} file is missing or unsafe: {name}")
    actual_digest, actual_size = sha256_file(path)
    if actual_digest != digest or actual_size != size:
        raise ManifestError(f"candidate {field} does not match its digest and size: {name}")
    return path


def _validate_checksum(archive: Path, checksum: Path) -> None:
    digest, _ = sha256_file(archive)
    expected = f"{digest}  {archive.name}\n"
    try:
        value = checksum.read_text(encoding="ascii")
    except UnicodeDecodeError as exc:
        raise ManifestError("checksum sidecar must contain ASCII") from exc
    if value != expected:
        raise ManifestError(f"checksum sidecar does not exactly match {archive.name}")


def _validate_direct_package_report(
    direct_package: Path,
    notice: Path,
    report: Path,
    *,
    version: str,
    target: ReleaseTarget,
) -> None:
    value = _read_json_object(report)
    required = {
        "schema_version",
        "version",
        "platform",
        "architecture",
        "trust",
        "format",
        "package",
        "notice",
        "source",
        "sha256",
        "size",
        "recovery_surface",
        "recovery_console_helper",
    }
    expected_recovery_surface = {
        "windows": "embedded-console-helper",
        "macos": "bundled-console-helper",
        "linux": "console-main-binary",
    }[target.platform]
    expected_recovery_helper = {
        "windows": "AllTheContextRecovery.exe",
        "macos": "all-the-context-recovery",
        "linux": "all-the-context",
    }[target.platform]
    digest, size = sha256_file(direct_package)
    source = value.get("source")
    if (
        set(value) != required
        or value.get("schema_version") != 1
        or value.get("version") != version
        or value.get("platform") != target.platform
        or value.get("architecture") != target.architecture
        or value.get("trust") != "unsigned-community"
        or value.get("format") != DIRECT_PACKAGE_SUFFIXES[target.platform].lstrip(".")
        or value.get("package") != direct_package.name
        or value.get("notice") != notice.name
        or value.get("sha256") != digest
        or value.get("size") != size
        or value.get("recovery_surface") != expected_recovery_surface
        or value.get("recovery_console_helper") != expected_recovery_helper
        or not isinstance(source, str)
        or SAFE_FILE_NAME.fullmatch(source) is None
    ):
        raise ManifestError("direct native package report does not match its package")
    try:
        notice_text = notice.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError("unsigned package notice must be UTF-8") from exc
    if "unsigned" not in notice_text.casefold() or len(notice_text) > 64 * 1024:
        raise ManifestError("native package notice must clearly disclose unsigned status")


def _zip_sha256(archive: Path) -> dict[str, tuple[str, int]]:
    result: dict[str, tuple[str, int]] = {}
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            for info in bundle.infolist():
                if info.is_dir():
                    continue
                if info.filename in result or info.filename.casefold() in {
                    name.casefold() for name in result
                }:
                    raise ManifestError("release ZIP contains duplicate case-insensitive paths")
                digest = hashlib.sha256()
                size = 0
                with bundle.open(info, "r") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        size += len(chunk)
                        digest.update(chunk)
                if size != info.file_size:
                    raise ManifestError("release ZIP entry size does not match its directory")
                result[info.filename] = (digest.hexdigest(), size)
    except zipfile.BadZipFile as exc:
        raise ManifestError("release artifact is not a valid ZIP archive") from exc
    if not result:
        raise ManifestError("release ZIP contains no files")
    return result


def _validate_spdx(subject: Path, sbom: Path, version: str, *, analyzed_archive: bool) -> None:
    value = _read_json_object(sbom)
    if value.get("spdxVersion") != "SPDX-2.3" or value.get("dataLicense") != "CC0-1.0":
        raise ManifestError("release SBOM is not an SPDX 2.3 document")
    packages = value.get("packages")
    if not isinstance(packages, list) or len(packages) != 1 or not isinstance(packages[0], dict):
        raise ManifestError("release SBOM must describe exactly one package")
    package = packages[0]
    archive_digest, _ = sha256_file(subject)
    if (
        package.get("name") != "all-the-context"
        or package.get("versionInfo") != version
        or package.get("packageFileName") != subject.name
        or package.get("filesAnalyzed") is not analyzed_archive
        or package.get("checksums") != [{"algorithm": "SHA256", "checksumValue": archive_digest}]
    ):
        raise ManifestError("release SBOM package identity does not match the archive")
    files = value.get("files")
    if not isinstance(files, list) or (not analyzed_archive and files):
        raise ManifestError("release SBOM file inventory is missing")
    if not analyzed_archive:
        return
    declared: dict[str, tuple[str, int]] = {}
    for item in files:
        if not isinstance(item, dict):
            raise ManifestError("release SBOM file entry is malformed")
        file_name = item.get("fileName")
        checksums = item.get("checksums")
        comment = item.get("comment")
        if (
            not isinstance(file_name, str)
            or not file_name.startswith("./")
            or not isinstance(checksums, list)
            or not isinstance(comment, str)
        ):
            raise ManifestError("release SBOM file identity is malformed")
        sha256_values = [
            checksum.get("checksumValue")
            for checksum in checksums
            if isinstance(checksum, dict) and checksum.get("algorithm") == "SHA256"
        ]
        match = re.fullmatch(r"Archived file; size=([1-9][0-9]*|0) bytes", comment)
        if len(sha256_values) != 1 or match is None:
            raise ManifestError("release SBOM file digest or size is malformed")
        relative = file_name[2:]
        if relative in declared:
            raise ManifestError("release SBOM contains duplicate file names")
        declared[relative] = (cast(str, sha256_values[0]), int(match.group(1)))
    if declared != _zip_sha256(subject):
        raise ManifestError("release SBOM file inventory does not match the archive")


def _validate_sigstore_bundle(path: Path) -> None:
    value = _read_json_object(path)
    media_type = value.get("mediaType")
    if (
        not isinstance(media_type, str)
        or not media_type.startswith("application/vnd.dev.sigstore.bundle")
        or not isinstance(value.get("verificationMaterial"), dict)
        or not isinstance(value.get("dsseEnvelope"), dict)
    ):
        raise ManifestError(f"attestation bundle is not a Sigstore DSSE bundle: {path.name}")


def archive_name(version: str, target: ReleaseTarget) -> str:
    return f"all-the-context-{version}-{target.platform}-{target.architecture}.zip"


def direct_package_names(version: str, target: ReleaseTarget) -> dict[str, str]:
    base = f"all-the-context-{version}-{target.platform}-{target.architecture}-unsigned"
    package = f"{base}{DIRECT_PACKAGE_SUFFIXES[target.platform]}"
    return {
        "direct_package": package,
        "direct_package_checksum": f"{package}.sha256",
        "direct_package_notice": f"{base}.IMPORTANT-UNSIGNED.txt",
        "direct_package_report": f"{base}.package.json",
        "direct_package_sbom": f"{package}.spdx.json",
        "direct_package_provenance_bundle": f"{package}.provenance.sigstore.json",
        "direct_package_sbom_bundle": f"{package}.sbom.sigstore.json",
    }


def attestation_names(archive: str) -> tuple[str, str]:
    return f"{archive}.provenance.sigstore.json", f"{archive}.sbom.sigstore.json"


def attach_attestation_bundles(
    release_dir: Path,
    *,
    provenance_bundle: Path,
    sbom_bundle: Path,
    subject: Path | None = None,
) -> tuple[Path, Path]:
    if subject is None:
        archives = list(release_dir.glob("*.zip"))
        if len(archives) != 1:
            raise ManifestError("attestation attachment requires exactly one release ZIP")
        subject = archives[0]
    if subject.parent.resolve() != release_dir.resolve() or not subject.is_file():
        raise ManifestError("attestation subject must be a release-directory file")
    provenance_name, sbom_name = attestation_names(subject.name)
    outputs = (release_dir / provenance_name, release_dir / sbom_name)
    for source, destination in zip((provenance_bundle, sbom_bundle), outputs, strict=True):
        if destination.exists():
            raise ManifestError(f"refusing to replace attestation bundle: {destination.name}")
        if not source.is_file() or source.is_symlink():
            raise ManifestError("attestation action did not produce a regular bundle file")
        shutil.copyfile(source, destination)
        _validate_sigstore_bundle(destination)
    return outputs


INVENTORY_ALLOWED_KEYS = frozenset(
    {
        "schema_version",
        "version",
        "source_commit",
        "project_version",
        "locks",
        "component_count",
        "components",
    }
)
INVENTORY_COMPONENT_ALLOWED_KEYS = frozenset(
    {
        "ecosystem",
        "name",
        "version",
        "license",
        "locked",
        "source_kind",
        "scope",
    }
)
INVENTORY_LOCK_ALLOWED_KEYS = frozenset({"sha256"})
INVENTORY_ECOSYSTEMS = frozenset({"python", "npm"})
INVENTORY_SCOPES = frozenset({"runtime", "build", "dev"})
CANONICAL_INVENTORY_LOCK_NAMES = frozenset({"uv.lock", "apps/dashboard/package-lock.json"})


def _validate_component_inventory_pair(
    inventory_path: Path,
    checksum_path: Path,
    *,
    source_commit: str,
    version: str,
) -> None:
    """Require inventory checksum binding and schema matching the builder."""

    if not checksum_path.is_file() or checksum_path.is_symlink():
        raise ManifestError("component inventory checksum sidecar is required")
    _validate_checksum(inventory_path, checksum_path)
    inventory = _read_json_object(inventory_path)
    if set(inventory) != INVENTORY_ALLOWED_KEYS:
        raise ManifestError("component inventory fields or schema are invalid")
    schema_version = inventory.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ManifestError("component inventory schema_version must be integer 1")
    if inventory.get("source_commit") != source_commit:
        raise ManifestError("component inventory source_commit does not match the candidate")
    if inventory.get("version") != version:
        raise ManifestError("component inventory version does not match the candidate")
    project_version = inventory.get("project_version")
    if not isinstance(project_version, str) or not project_version.strip():
        raise ManifestError("component inventory project_version is invalid")
    components = inventory.get("components")
    count = inventory.get("component_count")
    if not isinstance(components, list):
        raise ManifestError("component inventory components must be a list")
    if isinstance(count, bool) or not isinstance(count, int) or count != len(components):
        raise ManifestError("component inventory component_count does not match components")
    if count < 1:
        raise ManifestError("component inventory must list at least one locked component")
    locks = inventory.get("locks")
    if not isinstance(locks, dict) or not locks:
        raise ManifestError("component inventory must declare reviewed lock digests")
    if set(locks) != CANONICAL_INVENTORY_LOCK_NAMES:
        raise ManifestError("component inventory locks must declare the reviewed lock files")
    for lock_name, lock_value in locks.items():
        if not isinstance(lock_name, str) or not isinstance(lock_value, dict):
            raise ManifestError("component inventory locks entry is malformed")
        if set(lock_value) != INVENTORY_LOCK_ALLOWED_KEYS:
            raise ManifestError(f"component inventory lock entry is malformed: {lock_name}")
        digest = lock_value.get("sha256")
        if not isinstance(digest, str) or SHA256.fullmatch(digest) is None:
            raise ManifestError("component inventory lock digest is malformed")
    seen_components: set[tuple[str, str, str]] = set()
    for item in components:
        if not isinstance(item, dict) or set(item) != INVENTORY_COMPONENT_ALLOWED_KEYS:
            raise ManifestError("component inventory component entry is malformed")
        ecosystem = item.get("ecosystem")
        name = item.get("name")
        component_version = item.get("version")
        license_id = item.get("license")
        locked = item.get("locked")
        source_kind = item.get("source_kind")
        scope = item.get("scope")
        if ecosystem not in INVENTORY_ECOSYSTEMS:
            raise ManifestError("component inventory ecosystem is invalid")
        if not isinstance(name, str) or not name.strip():
            raise ManifestError("component inventory component name is invalid")
        if not isinstance(component_version, str) or not component_version.strip():
            raise ManifestError("component inventory component version is invalid")
        if not isinstance(license_id, str) or not license_id.strip() or len(license_id) > 120:
            raise ManifestError("component inventory license is invalid")
        if locked is not True:
            raise ManifestError("component inventory components must be locked=true")
        if not isinstance(source_kind, str) or not source_kind.strip():
            raise ManifestError("component inventory source_kind is invalid")
        if scope not in INVENTORY_SCOPES:
            raise ManifestError("component inventory scope is invalid")
        identity = (str(ecosystem), name.casefold(), component_version)
        if identity in seen_components:
            raise ManifestError(
                f"component inventory contains duplicate component: {name}@{component_version}"
            )
        seen_components.add(identity)


def _validate_source_evidence_contents(
    release_dir: Path,
    *,
    source_commit: str,
    version: str,
) -> None:
    """Semantically validate bound source-evidence files (not just digests)."""

    load_matrix_evidence(release_dir / MATRIX_EVIDENCE_FILE_NAME, source_commit=source_commit)
    _validate_component_inventory_pair(
        release_dir / COMPONENT_INVENTORY_FILE_NAME,
        release_dir / COMPONENT_INVENTORY_CHECKSUM_FILE_NAME,
        source_commit=source_commit,
        version=version,
    )
    notices = release_dir / NOTICES_FILE_NAME
    try:
        notices_text = notices.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError("component notices must be UTF-8") from exc
    if not notices_text.strip() or len(notices_text) > 2 * 1024 * 1024:
        raise ManifestError("component notices file is empty or unreasonably large")
    if source_commit not in notices_text:
        raise ManifestError("component notices must reference the exact source commit")


def _source_evidence_descriptors(
    release_dir: Path,
    *,
    source_commit: str,
    version: str,
) -> dict[str, dict[str, Any]]:
    _validate_source_evidence_contents(release_dir, source_commit=source_commit, version=version)
    descriptors: dict[str, dict[str, Any]] = {}
    for field, file_name in SOURCE_EVIDENCE_FILE_NAMES.items():
        path = release_dir / file_name
        if not path.is_file() or path.is_symlink():
            raise ManifestError(f"release candidate source evidence is missing: {file_name}")
        descriptors[field] = _descriptor(path)
        if descriptors[field]["name"] != file_name:
            raise ManifestError(f"source evidence name mismatch: {file_name}")
    return descriptors


def assemble_candidate(
    release_dir: Path,
    *,
    version: str,
    channel: str,
    source_commit: str,
    targets: Iterable[ReleaseTarget],
    ota_targets: Iterable[ReleaseTarget] = (),
    output: Path | None = None,
) -> Path:
    parsed_version = ReleaseVersion.parse(version)
    if channel not in CHANNELS:
        raise ManifestError("release candidate channel is invalid")
    if (channel == "beta") != (parsed_version.stability == 0):
        raise ManifestError("release candidate version does not match its channel")
    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("release candidate source commit must be a full lowercase SHA")
    unique_targets = sorted(set(targets))
    eligible_ota_targets = set(ota_targets)
    if not unique_targets:
        raise ManifestError("release candidate must contain at least one target")
    if not eligible_ota_targets or not eligible_ota_targets.issubset(unique_targets):
        raise ManifestError("release candidate requires a non-empty OTA target subset")
    source_evidence = _source_evidence_descriptors(
        release_dir, source_commit=source_commit, version=version
    )
    artifacts: list[dict[str, Any]] = []
    expected_files: set[str] = set(SOURCE_EVIDENCE_FILE_NAMES.values())
    for target in unique_targets:
        name = archive_name(version, target)
        provenance_name, sbom_bundle_name = attestation_names(name)
        direct_names = direct_package_names(version, target)
        paths = {
            "ota_archive": release_dir / name,
            "ota_checksum": release_dir / f"{name}.sha256",
            "ota_sbom": release_dir / f"{name}.spdx.json",
            "ota_provenance_bundle": release_dir / provenance_name,
            "ota_sbom_bundle": release_dir / sbom_bundle_name,
            **{field: release_dir / file_name for field, file_name in direct_names.items()},
        }
        for field, path in paths.items():
            if not path.is_file() or path.is_symlink():
                raise ManifestError(f"release candidate {field} is missing: {path.name}")
            expected_files.add(path.name)
        _validate_checksum(paths["ota_archive"], paths["ota_checksum"])
        _validate_spdx(paths["ota_archive"], paths["ota_sbom"], version, analyzed_archive=True)
        _validate_sigstore_bundle(paths["ota_provenance_bundle"])
        _validate_sigstore_bundle(paths["ota_sbom_bundle"])
        _validate_checksum(paths["direct_package"], paths["direct_package_checksum"])
        _validate_spdx(
            paths["direct_package"],
            paths["direct_package_sbom"],
            version,
            analyzed_archive=False,
        )
        _validate_sigstore_bundle(paths["direct_package_provenance_bundle"])
        _validate_sigstore_bundle(paths["direct_package_sbom_bundle"])
        _validate_direct_package_report(
            paths["direct_package"],
            paths["direct_package_notice"],
            paths["direct_package_report"],
            version=version,
            target=target,
        )
        artifacts.append(
            {
                "architecture": target.architecture,
                "ota_manifest_eligible": target in eligible_ota_targets,
                "platform": target.platform,
                **{field: _descriptor(path) for field, path in paths.items()},
            }
        )
    actual_files = {path.name for path in release_dir.iterdir() if path.is_file()}
    if actual_files != expected_files:
        raise ManifestError(
            "release assembly directory contains missing or untracked files "
            f"(missing={sorted(expected_files - actual_files)}, "
            f"extra={sorted(actual_files - expected_files)})"
        )
    candidate: dict[str, Any] = {
        "artifacts": artifacts,
        "channel": channel,
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "source_commit": source_commit,
        "source_evidence": source_evidence,
        "tag": f"v{version}",
        "unsigned_community_build": True,
        "version": version,
    }
    destination = output or release_dir / CANDIDATE_FILE_NAME
    _write_json(destination, candidate)
    digest, _ = sha256_file(destination)
    checksum_path = destination.with_name(f"{destination.name}.sha256")
    checksum_path.write_text(f"{digest}  {destination.name}\n", encoding="ascii", newline="\n")
    verify_candidate(
        destination,
        release_dir,
        expected_targets=unique_targets,
        expected_ota_targets=eligible_ota_targets,
    )
    return destination


def verify_candidate(
    candidate_path: Path,
    release_dir: Path,
    *,
    expected_sha256: str | None = None,
    expected_targets: Iterable[ReleaseTarget] | None = None,
    expected_ota_targets: Iterable[ReleaseTarget] | None = None,
) -> dict[str, Any]:
    candidate_digest, _ = sha256_file(candidate_path)
    if expected_sha256 is not None and candidate_digest != expected_sha256:
        raise ManifestError("release candidate inventory digest does not match operator input")
    candidate = _read_json_object(candidate_path)
    required = {
        "schema_version",
        "version",
        "channel",
        "tag",
        "source_commit",
        "source_evidence",
        "unsigned_community_build",
        "artifacts",
    }
    schema_version = candidate.get("schema_version")
    if (
        set(candidate) != required
        or isinstance(schema_version, bool)
        or schema_version != (CANDIDATE_SCHEMA_VERSION)
    ):
        raise ManifestError("release candidate inventory fields or schema are invalid")
    version = candidate.get("version")
    channel = candidate.get("channel")
    if not isinstance(version, str) or not isinstance(channel, str):
        raise ManifestError("release candidate version and channel must be strings")
    parsed_version = ReleaseVersion.parse(version)
    if channel not in CHANNELS or (channel == "beta") != (parsed_version.stability == 0):
        raise ManifestError("release candidate version does not match its channel")
    if candidate.get("tag") != f"v{version}":
        raise ManifestError("release candidate tag does not match its version")
    source_commit = candidate.get("source_commit")
    if not isinstance(source_commit, str) or COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("release candidate source commit is invalid")
    if candidate.get("unsigned_community_build") is not True:
        raise ManifestError("release candidate must disclose unsigned community build status")
    source_evidence = candidate.get("source_evidence")
    if not isinstance(source_evidence, dict) or set(source_evidence) != set(SOURCE_EVIDENCE_FIELDS):
        raise ManifestError("release candidate source_evidence is malformed")
    seen_files: set[str] = set()
    for field in SOURCE_EVIDENCE_FIELDS:
        path = _descriptor_path(
            release_dir,
            source_evidence.get(field),
            f"source_evidence.{field}",
            seen_names=seen_files,
        )
        expected_name = SOURCE_EVIDENCE_FILE_NAMES[field]
        if path.name != expected_name:
            raise ManifestError(f"source evidence file name is wrong for {field}")
    # Recompute semantic relationships; digest match alone is not enough.
    _validate_source_evidence_contents(release_dir, source_commit=source_commit, version=version)
    artifact_values = candidate.get("artifacts")
    if not isinstance(artifact_values, list) or not artifact_values:
        raise ManifestError("release candidate artifact inventory is empty")
    targets: list[ReleaseTarget] = []
    fields = {
        "platform",
        "architecture",
        "direct_package",
        "direct_package_checksum",
        "direct_package_notice",
        "direct_package_report",
        "direct_package_sbom",
        "direct_package_provenance_bundle",
        "direct_package_sbom_bundle",
        "ota_archive",
        "ota_checksum",
        "ota_sbom",
        "ota_provenance_bundle",
        "ota_sbom_bundle",
        "ota_manifest_eligible",
    }
    for artifact in artifact_values:
        if not isinstance(artifact, dict) or set(artifact) != fields:
            raise ManifestError("release candidate artifact entry is malformed")
        platform_name = artifact.get("platform")
        architecture = artifact.get("architecture")
        if not isinstance(platform_name, str) or not isinstance(architecture, str):
            raise ManifestError("release candidate target values must be strings")
        target = ReleaseTarget.parse(f"{platform_name}:{architecture}")
        if target in targets:
            raise ManifestError("release candidate targets must be unique")
        targets.append(target)
        if not isinstance(artifact.get("ota_manifest_eligible"), bool):
            raise ManifestError("release candidate OTA eligibility must be a boolean")
        paths = {
            field: _descriptor_path(
                release_dir,
                artifact.get(field),
                field,
                seen_names=seen_files,
            )
            for field in fields - {"platform", "architecture", "ota_manifest_eligible"}
        }
        expected_archive = archive_name(version, target)
        expected_provenance, expected_sbom_bundle = attestation_names(expected_archive)
        direct_names = direct_package_names(version, target)
        expected_names = {
            "ota_archive": expected_archive,
            "ota_checksum": f"{expected_archive}.sha256",
            "ota_sbom": f"{expected_archive}.spdx.json",
            "ota_provenance_bundle": expected_provenance,
            "ota_sbom_bundle": expected_sbom_bundle,
            **direct_names,
        }
        if any(paths[field].name != name for field, name in expected_names.items()):
            raise ManifestError("release candidate file names do not match their target")
        _validate_checksum(paths["ota_archive"], paths["ota_checksum"])
        _validate_spdx(paths["ota_archive"], paths["ota_sbom"], version, analyzed_archive=True)
        _validate_sigstore_bundle(paths["ota_provenance_bundle"])
        _validate_sigstore_bundle(paths["ota_sbom_bundle"])
        _validate_checksum(paths["direct_package"], paths["direct_package_checksum"])
        _validate_spdx(
            paths["direct_package"],
            paths["direct_package_sbom"],
            version,
            analyzed_archive=False,
        )
        _validate_sigstore_bundle(paths["direct_package_provenance_bundle"])
        _validate_sigstore_bundle(paths["direct_package_sbom_bundle"])
        _validate_direct_package_report(
            paths["direct_package"],
            paths["direct_package_notice"],
            paths["direct_package_report"],
            version=version,
            target=target,
        )
    if targets != sorted(targets):
        raise ManifestError("release candidate targets must use deterministic ordering")
    if expected_targets is not None and targets != sorted(set(expected_targets)):
        raise ManifestError("release candidate targets do not match the required matrix")
    eligible_targets = {
        ReleaseTarget(cast(str, artifact["platform"]), cast(str, artifact["architecture"]))
        for artifact in artifact_values
        if artifact["ota_manifest_eligible"] is True
    }
    if not eligible_targets:
        raise ManifestError("release candidate has no OTA-eligible target")
    if expected_ota_targets is not None and eligible_targets != set(expected_ota_targets):
        raise ManifestError("release candidate OTA targets do not match the approved subset")
    checksum_path = candidate_path.with_name(f"{candidate_path.name}.sha256")
    if not checksum_path.is_file() or checksum_path.is_symlink():
        raise ManifestError("release candidate checksum sidecar is required")
    _validate_checksum(candidate_path, checksum_path)
    return candidate


def validate_github_release_state(
    state: Mapping[str, Any],
    *,
    tag: str,
    source_commit: str,
    draft: bool,
    immutable: bool,
    expected_asset_names: Iterable[str] | None = None,
    expected_asset_descriptors: Mapping[str, tuple[str, int]] | None = None,
) -> None:
    """Validate normalized GitHub CLI or REST release state used by release gates."""

    normalized = normalize_github_release_state(state)

    if (
        normalized.get("tagName") != tag
        or normalized.get("targetCommitish") != source_commit
        or normalized.get("isDraft") is not draft
        or normalized.get("isImmutable") is not immutable
        or normalized.get("isPrerelease") is not True
    ):
        raise ManifestError(
            "GitHub beta release state does not match the reviewed promotion inputs"
        )
    assets = normalized.get("assets")
    if not isinstance(assets, list) or not assets:
        raise ManifestError("GitHub beta release has no attached assets")
    actual_asset_names: list[str] = []
    for asset in assets:
        name = asset.get("name") if isinstance(asset, Mapping) else None
        if not isinstance(name, str) or SAFE_FILE_NAME.fullmatch(name) is None:
            raise ManifestError("GitHub beta release contains an invalid asset name")
        actual_asset_names.append(name)
    if len(actual_asset_names) != len({name.casefold() for name in actual_asset_names}):
        raise ManifestError("GitHub beta release contains duplicate asset names")
    if expected_asset_names is not None:
        expected = set(expected_asset_names)
        actual = set(actual_asset_names)
        if actual != expected:
            raise ManifestError(
                "GitHub beta release asset set differs from the controlled inventory "
                f"(missing={sorted(expected - actual)}, extra={sorted(actual - expected)})"
            )
    if expected_asset_descriptors is not None:
        actual_assets = {cast(str, asset["name"]): asset for asset in assets}
        expected_names = set(expected_asset_descriptors)
        if set(actual_assets) != expected_names:
            raise ManifestError("GitHub beta release asset descriptors do not match inventory")
        for name, (expected_digest, expected_size) in expected_asset_descriptors.items():
            asset = actual_assets[name]
            size = asset.get("size")
            digest = asset.get("digest")
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or size != expected_size
                or digest != f"sha256:{expected_digest}"
            ):
                raise ManifestError(
                    f"GitHub beta release asset digest or size differs from inventory: {name}"
                )


def normalize_github_release_state(
    state: Mapping[str, Any],
    *,
    require_release_id: bool = False,
    require_asset_api_metadata: bool = False,
) -> dict[str, Any]:
    """Normalize GitHub REST or ``gh release view --json`` release metadata."""

    rest_shape = "tag_name" in state or "target_commitish" in state
    release_id = state.get("id") if rest_shape else state.get("releaseId")
    if release_id is not None and (
        isinstance(release_id, bool) or not isinstance(release_id, int) or release_id < 1
    ):
        raise ManifestError("GitHub release ID is invalid")
    if require_release_id and release_id is None:
        raise ManifestError("GitHub release ID is required for an unpublished draft")
    assets = state.get("assets")
    if not isinstance(assets, list):
        raise ManifestError("GitHub beta release assets are malformed")
    normalized_assets: list[dict[str, Any]] = []
    for asset in assets:
        if not isinstance(asset, Mapping):
            raise ManifestError("GitHub beta release asset metadata is malformed")
        name = asset.get("name")
        if not isinstance(name, str) or SAFE_FILE_NAME.fullmatch(name) is None:
            raise ManifestError("GitHub beta release contains an invalid asset name")
        asset_id = asset.get("id")
        numeric_asset_id = (
            not isinstance(asset_id, bool) and isinstance(asset_id, int) and asset_id >= 1
        )
        opaque_cli_asset_id = (
            not rest_shape
            and isinstance(asset_id, str)
            and 1 <= len(asset_id) <= 256
            and asset_id.isascii()
            and asset_id.isprintable()
        )
        size = asset.get("size")
        digest = asset.get("digest")
        if asset_id is not None and not numeric_asset_id and not opaque_cli_asset_id:
            raise ManifestError("GitHub release asset ID is invalid")
        if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
            raise ManifestError("GitHub release asset size is invalid")
        if digest is not None and (
            not isinstance(digest, str)
            or not digest.startswith("sha256:")
            or SHA256.fullmatch(digest.removeprefix("sha256:")) is None
        ):
            raise ManifestError("GitHub release asset digest is invalid")
        if require_asset_api_metadata and (
            not numeric_asset_id or size is None or not isinstance(digest, str)
        ):
            raise ManifestError(
                "GitHub release asset API metadata requires numeric REST ID, size, and SHA-256"
            )
        normalized_assets.append(
            {
                # ``gh release view --json assets`` returns an opaque GraphQL node ID.
                # Never reinterpret that value as the numeric REST ID used by mutation URLs.
                "id": asset_id if numeric_asset_id else None,
                "name": name,
                "size": size,
                "digest": digest,
            }
        )
    if len(normalized_assets) != len(
        {cast(str, asset["name"]).casefold() for asset in normalized_assets}
    ):
        raise ManifestError("GitHub beta release contains duplicate asset names")
    normalized = {
        "releaseId": release_id,
        "tagName": state.get("tag_name") if rest_shape else state.get("tagName"),
        "targetCommitish": (
            state.get("target_commitish") if rest_shape else state.get("targetCommitish")
        ),
        "isDraft": state.get("draft") if rest_shape else state.get("isDraft"),
        "isImmutable": state.get("immutable") if rest_shape else state.get("isImmutable"),
        "isPrerelease": state.get("prerelease") if rest_shape else state.get("isPrerelease"),
        "assets": normalized_assets,
    }
    return normalized


def select_github_release_from_api_listing(value: object, *, tag: str) -> dict[str, Any]:
    """Select one draft/published release by tag from paginated REST list output."""

    if not isinstance(value, list):
        raise ManifestError("GitHub release listing must be an array")
    releases: list[Mapping[str, Any]] = []
    for item in value:
        if isinstance(item, list):
            if any(not isinstance(release, Mapping) for release in item):
                raise ManifestError("GitHub release listing page is malformed")
            releases.extend(cast(list[Mapping[str, Any]], item))
        elif isinstance(item, Mapping):
            releases.append(item)
        else:
            raise ManifestError("GitHub release listing entry is malformed")
    if any(
        not isinstance(release.get("tag_name"), str) or not release.get("tag_name")
        for release in releases
    ):
        raise ManifestError("GitHub release listing contains an invalid tag name")
    matches = [release for release in releases if release.get("tag_name") == tag]
    if len(matches) != 1:
        raise ManifestError(
            f"GitHub release listing requires exactly one release for {tag!r}; found {len(matches)}"
        )
    return normalize_github_release_state(
        matches[0],
        require_release_id=True,
        require_asset_api_metadata=True,
    )


def signed_manifest_name(channel: str, target: ReleaseTarget) -> str:
    if channel not in CHANNELS:
        raise ManifestError("unknown signed-manifest channel")
    return f"manifest-{channel}-{target.platform}-{target.architecture}-v1.json"


def expected_release_asset_names(candidate: Mapping[str, Any], *, stage: str) -> set[str]:
    """Return the exact GitHub Release asset set for a controlled stage.

    Stages:
    - ``draft``: packages, candidate inventory, provenance, source evidence
    - ``signed``: draft plus offline-signed OTA manifests
    - ``promotion``: signed plus acceptance receipt bundle and publication record
    """

    if stage not in ASSET_STAGES:
        raise ManifestError("release asset stage must be draft, signed, or promotion")
    artifacts = candidate.get("artifacts")
    channel = candidate.get("channel")
    source_evidence = candidate.get("source_evidence")
    if not isinstance(artifacts, list) or not isinstance(channel, str):
        raise ManifestError("release candidate cannot define an asset set")
    names = {
        CANDIDATE_FILE_NAME,
        f"{CANDIDATE_FILE_NAME}.sha256",
        CANDIDATE_PROVENANCE_FILE_NAME,
    }
    if not isinstance(source_evidence, dict):
        raise ManifestError("release candidate source_evidence is required for asset sets")
    for field in SOURCE_EVIDENCE_FIELDS:
        descriptor = source_evidence.get(field)
        if not isinstance(descriptor, dict) or not isinstance(descriptor.get("name"), str):
            raise ManifestError(f"release candidate source_evidence.{field} is malformed")
        names.add(cast(str, descriptor["name"]))
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ManifestError("release candidate artifact entry is malformed")
        for field, descriptor in artifact.items():
            if field in {"platform", "architecture", "ota_manifest_eligible"}:
                continue
            if not isinstance(descriptor, dict) or not isinstance(descriptor.get("name"), str):
                raise ManifestError("release candidate file descriptor is malformed")
            names.add(cast(str, descriptor["name"]))
        if stage in {"signed", "promotion"} and artifact.get("ota_manifest_eligible") is True:
            target = ReleaseTarget(
                cast(str, artifact["platform"]), cast(str, artifact["architecture"])
            )
            names.add(signed_manifest_name(channel, target))
    if stage == "promotion":
        names.update(DECISION_ASSET_NAMES)
    return names


def verify_release_asset_set(
    candidate_path: Path,
    release_dir: Path,
    *,
    stage: str,
    expected_sha256: str | None = None,
    expected_targets: Iterable[ReleaseTarget] | None = None,
    expected_ota_targets: Iterable[ReleaseTarget] | None = None,
) -> list[Path]:
    """Verify and return the exact, explicitly uploadable release files."""

    canonical_candidate = release_dir / CANDIDATE_FILE_NAME
    if candidate_path.resolve() != canonical_candidate.resolve():
        raise ManifestError("release asset verification requires the canonical candidate path")
    candidate = verify_candidate(
        candidate_path,
        release_dir,
        expected_sha256=expected_sha256,
        expected_targets=expected_targets,
        expected_ota_targets=expected_ota_targets,
    )
    expected_names = expected_release_asset_names(candidate, stage=stage)
    entries = list(release_dir.iterdir())
    if any(path.is_symlink() or not path.is_file() for path in entries):
        raise ManifestError("release asset directory must contain only regular files")
    actual_names = {path.name for path in entries}
    if len(actual_names) != len({name.casefold() for name in actual_names}):
        raise ManifestError("release assets contain case-insensitive name collisions")
    if actual_names != expected_names:
        raise ManifestError(
            "release asset set differs from the controlled inventory "
            f"(missing={sorted(expected_names - actual_names)}, "
            f"extra={sorted(actual_names - expected_names)})"
        )
    _validate_sigstore_bundle(release_dir / CANDIDATE_PROVENANCE_FILE_NAME)
    return [release_dir / name for name in sorted(expected_names)]


def _require_empty_output(output_dir: Path) -> None:
    if output_dir.exists():
        if output_dir.is_symlink() or not output_dir.is_dir() or any(output_dir.iterdir()):
            raise ManifestError("channel output directory must be absent or empty")
    else:
        output_dir.mkdir(parents=True)


def prepare_beta_channel(
    release_dir: Path,
    *,
    candidate_path: Path,
    candidate_sha256: str,
    keyring_path: Path,
    repository: str,
    source_commit: str,
    output_dir: Path,
) -> dict[str, Any]:
    if REPOSITORY.fullmatch(repository) is None:
        raise ManifestError("GitHub repository must be OWNER/REPOSITORY")
    candidate = verify_candidate(
        candidate_path,
        release_dir,
        expected_sha256=candidate_sha256,
    )
    if candidate["channel"] != "beta" or candidate["source_commit"] != source_commit:
        raise ManifestError("beta promotion inputs do not match the candidate inventory")
    _require_empty_output(output_dir)
    keyring = load_keyring(keyring_path)
    version = cast(str, candidate["version"])
    tag = cast(str, candidate["tag"])
    manifest_entries: list[dict[str, Any]] = []
    eligible_artifacts = [
        artifact
        for artifact in cast(list[dict[str, Any]], candidate["artifacts"])
        if artifact["ota_manifest_eligible"] is True
    ]
    expected_manifest_names = {
        signed_manifest_name(
            "beta",
            ReleaseTarget(cast(str, artifact["platform"]), cast(str, artifact["architecture"])),
        )
        for artifact in eligible_artifacts
    }
    actual_manifest_names = {path.name for path in release_dir.glob("manifest-*-v1.json")}
    if actual_manifest_names != expected_manifest_names:
        raise ManifestError(
            "signed OTA manifest set differs from the approved targets "
            f"(missing={sorted(expected_manifest_names - actual_manifest_names)}, "
            f"unexpected={sorted(actual_manifest_names - expected_manifest_names)})"
        )
    for artifact in eligible_artifacts:
        target = ReleaseTarget(cast(str, artifact["platform"]), cast(str, artifact["architecture"]))
        manifest_path = release_dir / signed_manifest_name("beta", target)
        manifest = _read_json_object(manifest_path)
        verify_manifest(manifest, keyring, expected_channel="beta")
        archive = cast(dict[str, Any], artifact["ota_archive"])
        expected_url = f"https://github.com/{repository}/releases/download/{tag}/{archive['name']}"
        expected_notes = f"https://github.com/{repository}/releases/tag/{tag}"
        if (
            manifest.get("version") != version
            or manifest.get("platform") != target.platform
            or manifest.get("architecture") != target.architecture
            or manifest.get("url") != expected_url
            or manifest.get("release_notes_url") != expected_notes
            or manifest.get("sha256") != archive["sha256"]
            or manifest.get("size") != archive["size"]
        ):
            raise ManifestError(f"signed manifest does not match candidate: {manifest_path.name}")
        destination = (
            output_dir / "beta" / target.platform / target.architecture / "manifest-v1.json"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(manifest_path, destination)
        digest, _ = sha256_file(destination)
        manifest_entries.append(
            {
                "architecture": target.architecture,
                "path": destination.relative_to(output_dir).as_posix(),
                "platform": target.platform,
                "sha256": digest,
            }
        )
    copied_candidate = output_dir / "beta" / CANDIDATE_FILE_NAME
    copied_candidate.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate_path, copied_candidate)
    (output_dir / "beta" / f"{CANDIDATE_FILE_NAME}.sha256").write_text(
        f"{candidate_sha256}  {CANDIDATE_FILE_NAME}\n",
        encoding="ascii",
        newline="\n",
    )
    channel_index: dict[str, Any] = {
        "candidate_sha256": candidate_sha256,
        "channel": "beta",
        "manifests": manifest_entries,
        "schema_version": 1,
        "source_commit": source_commit,
        "version": version,
    }
    _write_json(output_dir / "beta" / CHANNEL_INDEX_FILE_NAME, channel_index)
    (output_dir / ".nojekyll").write_bytes(b"")
    (output_dir / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>All The Context updates</title>'
        "<h1>All The Context update metadata</h1>"
        "<p>This site contains signed machine-readable beta update manifests.</p>\n",
        encoding="utf-8",
        newline="\n",
    )
    verify_beta_channel_site(output_dir, keyring_path=keyring_path)
    return channel_index


def verify_beta_channel_site(site_dir: Path, *, keyring_path: Path) -> dict[str, Any]:
    index = _read_json_object(site_dir / "beta" / CHANNEL_INDEX_FILE_NAME)
    required = {
        "schema_version",
        "channel",
        "version",
        "source_commit",
        "candidate_sha256",
        "manifests",
    }
    if (
        set(index) != required
        or index.get("schema_version") != 1
        or index.get("channel") != "beta"
        or not isinstance(index.get("version"), str)
        or ReleaseVersion.parse(cast(str, index["version"])).stability != 0
        or not isinstance(index.get("source_commit"), str)
        or COMMIT.fullmatch(cast(str, index["source_commit"])) is None
        or not isinstance(index.get("candidate_sha256"), str)
        or SHA256.fullmatch(cast(str, index["candidate_sha256"])) is None
    ):
        raise ManifestError("beta channel index is malformed")
    candidate = site_dir / "beta" / CANDIDATE_FILE_NAME
    candidate_digest, _ = sha256_file(candidate)
    if candidate_digest != index["candidate_sha256"]:
        raise ManifestError("published candidate inventory digest is wrong")
    keyring = load_keyring(keyring_path)
    entries = index.get("manifests")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("beta channel manifest index is empty")
    seen_targets: set[ReleaseTarget] = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "platform",
            "architecture",
            "path",
            "sha256",
        }:
            raise ManifestError("beta channel manifest entry is malformed")
        target = ReleaseTarget.parse(f"{entry['platform']}:{entry['architecture']}")
        if target in seen_targets:
            raise ManifestError("beta channel contains duplicate targets")
        seen_targets.add(target)
        expected_path = f"beta/{target.platform}/{target.architecture}/manifest-v1.json"
        if entry.get("path") != expected_path:
            raise ManifestError("beta channel manifest path is not canonical")
        path = site_dir / Path(expected_path)
        digest, _ = sha256_file(path)
        if digest != entry.get("sha256"):
            raise ManifestError("beta channel manifest digest is wrong")
        verify_manifest(_read_json_object(path), keyring, expected_channel="beta")
    return index
