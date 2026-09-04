"""Audit an operator-run Windows clean-machine journey and emit one receipt.

This command never launches the installer or any product executable.  The
operator runs the downloaded package and records the path-free lifecycle
evidence, then this audit binds that evidence to the verified release
candidate, installed component bytes, and native-build provenance.  A receipt
is written only when every required acceptance check is present and passing.
"""

# The script is intentionally runnable from a clean checkout without an
# editable install; keep this import bootstrap before package imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "allthecontext" / "src"))

from allthecontext.acceptance_receipt import validate_receipt
from allthecontext.installed_component_manifest import (
    CHECKSUM_FILE_NAME as COMPONENT_CHECKSUM_FILE_NAME,
)
from allthecontext.installed_component_manifest import (
    MANIFEST_FILE_NAME,
)
from allthecontext.release_candidate import verify_candidate
from allthecontext.release_manifest import ManifestError, sha256_file
from allthecontext.windows_acceptance import (
    WINDOWS_COMPONENTS,
    validate_windows_acceptance,
)
from installed_component_manifest import verify_archive
from native_build_provenance import (
    CHECKSUM_FILE_NAME as PROVENANCE_CHECKSUM_FILE_NAME,
)
from native_build_provenance import (
    PROVENANCE_FILE_NAME,
    verify_provenance,
)


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ManifestError(f"{label} is unavailable")
    return path.resolve(strict=True)


def _directory(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise ManifestError(f"{label} is unavailable")
    return path.resolve(strict=True)


def _descriptor(path: Path, label: str) -> dict[str, Any]:
    checked = _regular_file(path, label)
    digest, size = sha256_file(checked)
    if size <= 0:
        raise ManifestError(f"{label} is empty")
    return {"name": checked.name, "sha256": digest, "size": size}


def _descriptor_bytes(raw: bytes, name: str, label: str) -> dict[str, Any]:
    if not raw:
        raise ManifestError(f"{label} is empty")
    return {"name": name, "sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}


def _candidate_descriptor(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"name", "sha256", "size"}:
        raise ManifestError(f"candidate {label} descriptor is malformed")
    name = value.get("name")
    digest = value.get("sha256")
    size = value.get("size")
    if not isinstance(name, str) or not isinstance(digest, str):
        raise ManifestError(f"candidate {label} descriptor is malformed")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ManifestError(f"candidate {label} descriptor is malformed")
    return {"name": name, "sha256": digest, "size": size}


def _windows_candidate_artifact(candidate: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, list):
        raise ManifestError("candidate artifact inventory is malformed")
    windows = [
        item
        for item in artifacts
        if isinstance(item, Mapping)
        and item.get("platform") == "windows"
        and item.get("architecture") == "x86_64"
    ]
    if len(windows) != 1:
        raise ManifestError("candidate must contain exactly one windows/x86_64 artifact")
    artifact = windows[0]
    if artifact.get("ota_manifest_eligible") is not True:
        raise ManifestError("the Windows candidate artifact is not OTA eligible")
    return dict(artifact)


def _component_paths(component_dir: Path) -> dict[str, Path]:
    if component_dir.is_symlink() or not component_dir.is_dir():
        raise ManifestError("installed component directory is unavailable")
    paths: dict[str, Path] = {}
    for role, filename in WINDOWS_COMPONENTS:
        paths[role] = _regular_file(component_dir / filename, f"installed {role} component")
    return paths


def _common_source_root(paths: Mapping[str, Path]) -> Path:
    try:
        root = Path(os.path.commonpath([str(path) for path in paths.values()]))
    except ValueError as exc:
        raise ManifestError("acceptance inputs do not share a verification root") from exc
    if root.is_file():
        return root.parent
    return root


def _archive_metadata(archive: Path) -> tuple[bytes, bytes]:
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            manifest_infos = [
                info
                for info in bundle.infolist()
                if Path(info.filename).name.casefold() == MANIFEST_FILE_NAME.casefold()
            ]
            checksum_infos = [
                info
                for info in bundle.infolist()
                if Path(info.filename).name.casefold() == COMPONENT_CHECKSUM_FILE_NAME.casefold()
            ]
            if len(manifest_infos) != 1 or len(checksum_infos) != 1:
                raise ManifestError("installed component archive metadata is ambiguous")
            return bundle.read(manifest_infos[0]), bundle.read(checksum_infos[0])
    except zipfile.BadZipFile as exc:
        raise ManifestError("installed component archive is not a valid ZIP") from exc


def _component_evidence(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    components = payload.get("components")
    if not isinstance(components, list) or len(components) != len(WINDOWS_COMPONENTS):
        raise ManifestError("installed component manifest has no complete component set")
    result: list[dict[str, Any]] = []
    for item, (role, filename) in zip(components, WINDOWS_COMPONENTS, strict=True):
        if not isinstance(item, Mapping):
            raise ManifestError("installed component manifest component is malformed")
        if item.get("role") != role or item.get("filename") != filename:
            raise ManifestError("installed component manifest component identity changed")
        result.append(
            {
                "role": role,
                "filename": filename,
                "sha256": item.get("sha256"),
                "size": item.get("size"),
            }
        )
    return result


def _provenance_components(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    components = payload.get("components")
    if not isinstance(components, list) or len(components) != len(WINDOWS_COMPONENTS):
        raise ManifestError("native build provenance has no complete component set")
    result: list[dict[str, Any]] = []
    for item, (role, filename) in zip(components, WINDOWS_COMPONENTS, strict=True):
        if not isinstance(item, Mapping):
            raise ManifestError("native build provenance component is malformed")
        if item.get("role") != role or item.get("filename") != filename:
            raise ManifestError("native build provenance component identity changed")
        result.append(
            {
                "role": role,
                "filename": filename,
                "sha256": item.get("sha256"),
                "size": item.get("size"),
            }
        )
    return result


def _require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise ManifestError(f"Windows acceptance {label} does not match verified bytes")


def _load_evidence(path: Path, *, source_commit: str, version: str) -> dict[str, Any]:
    checked = _regular_file(path, "clean-machine evidence")
    try:
        value = json.loads(checked.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("clean-machine evidence is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != {"windows_acceptance"}:
        raise ManifestError("clean-machine evidence must contain only windows_acceptance")
    acceptance = value.get("windows_acceptance")
    if not isinstance(acceptance, Mapping):
        raise ManifestError("clean-machine evidence windows_acceptance is malformed")
    return validate_windows_acceptance(
        acceptance,
        require_pass=True,
        expected_source_commit=source_commit,
        expected_version=version,
    )


def audit(arguments: argparse.Namespace) -> Path:
    if platform.system() != "Windows":
        raise ManifestError("Windows clean-machine audit must run on Windows")
    release_dir = _directory(arguments.release_dir, "release directory")
    candidate_path = release_dir / "release-candidate-v1.json"
    _regular_file(candidate_path, "release candidate")
    candidate = verify_candidate(candidate_path, release_dir)
    source_commit = cast(str, candidate["source_commit"])
    version = cast(str, candidate["version"])
    candidate_digest, _ = sha256_file(candidate_path)

    artifact = _windows_candidate_artifact(candidate)
    direct_descriptor = _candidate_descriptor(artifact.get("direct_package"), "direct package")
    archive_descriptor = _candidate_descriptor(artifact.get("ota_archive"), "OTA archive")
    direct_path = _regular_file(release_dir / direct_descriptor["name"], "direct package")
    archive_path = _regular_file(release_dir / archive_descriptor["name"], "OTA archive")
    if _descriptor(direct_path, "direct package") != direct_descriptor:
        raise ManifestError("direct package bytes do not match the candidate")
    if _descriptor(archive_path, "OTA archive") != archive_descriptor:
        raise ManifestError("OTA archive bytes do not match the candidate")

    component_paths = _component_paths(arguments.component_dir)
    source_root = _common_source_root(
        {"archive": archive_path, "direct": direct_path, **component_paths}
    )
    manifest_payload = verify_archive(
        archive_path=archive_path,
        direct_package_path=direct_path,
        component_paths=component_paths,
        source_root=source_root,
        version=version,
        source_commit=source_commit,
        platform="windows",
        architecture="x86_64",
    )
    provenance_path = _regular_file(arguments.provenance, "native build provenance")
    provenance_payload = verify_provenance(
        provenance_path,
        component_paths,
        version=version,
        source_commit=source_commit,
    )
    if provenance_path.name != PROVENANCE_FILE_NAME:
        raise ManifestError("native build provenance filename is not canonical")
    provenance_checksum = _regular_file(
        provenance_path.with_name(PROVENANCE_CHECKSUM_FILE_NAME),
        "native build provenance checksum",
    )
    manifest_raw, manifest_checksum_raw = _archive_metadata(archive_path)
    evidence = _load_evidence(arguments.evidence, source_commit=source_commit, version=version)
    expected_artifacts = {
        "direct_package": direct_descriptor,
        "installed_component_archive": archive_descriptor,
        "installed_component_manifest": _descriptor_bytes(
            manifest_raw, MANIFEST_FILE_NAME, "installed component manifest"
        ),
        "installed_component_checksum": _descriptor_bytes(
            manifest_checksum_raw, COMPONENT_CHECKSUM_FILE_NAME, "installed component checksum"
        ),
        "native_build_provenance": _descriptor(provenance_path, "native build provenance"),
        "native_build_provenance_checksum": _descriptor(
            provenance_checksum, "native build provenance checksum"
        ),
    }
    _require_equal(evidence["artifacts"], expected_artifacts, "artifact bindings")
    manifest_components = _component_evidence(manifest_payload)
    provenance_components = _provenance_components(provenance_payload)
    _require_equal(manifest_components, provenance_components, "component provenance")
    _require_equal(evidence["components"], manifest_components, "component bindings")

    receipt: dict[str, Any] = {
        "schema_version": 1,
        "receipt_id": f"windows-clean-{arguments.gate_id.casefold()}",
        "gate_id": arguments.gate_id,
        "evidence_kind": "exact_downloaded_artifact",
        "status": "pass",
        "source_commit": source_commit,
        "candidate_sha256": candidate_digest,
        "content_free": True,
        "limitations": [],
        "attempts": [{"attempt": 1, "status": "pass"}],
        # Only candidate inventory members belong in this map.  The nested
        # acceptance record carries the inner archive/provenance descriptors;
        # those are checked against bytes above and are not candidate assets.
        "artifact_digests": {
            direct_descriptor["name"]: direct_descriptor["sha256"],
            archive_descriptor["name"]: archive_descriptor["sha256"],
        },
        "notes": "Windows clean-machine lifecycle and artifact audit passed.",
        "windows_acceptance": evidence,
    }
    validated = validate_receipt(receipt)
    output = arguments.receipt_out
    if output.exists():
        raise ManifestError("refusing to replace an existing acceptance receipt")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(validated, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate-id", choices=("BETA-P01", "BETA-S03"), required=True)
    parser.add_argument("--release-dir", type=Path, required=True)
    parser.add_argument("--component-dir", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    return parser


def main() -> int:
    try:
        output = audit(_parser().parse_args())
    except (ManifestError, OSError, ValueError, zipfile.BadZipFile):
        # Keep failure output content-free; detailed operator diagnostics stay
        # in the source-controlled test logs, never in a receipt or console.
        print("windows clean-machine acceptance audit failed", file=sys.stderr)
        return 1
    print(output.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
