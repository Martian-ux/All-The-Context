"""Prepare a content-free, fail-closed Windows helper reassessment bundle.

This script verifies already-produced release inputs without executing them or
contacting a service. The output is preparation evidence only: it does not claim
that a candidate is safe, signed, cleared, submitted, or accepted.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import sys
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Never, cast

if __package__:
    from scripts import installed_component_manifest as manifest_module
else:  # Direct ``python scripts/...`` execution must use the sibling module.
    import installed_component_manifest as manifest_module

MANIFEST_FILE_NAME = manifest_module.MANIFEST_FILE_NAME
MANIFEST_CHECKSUM_FILE_NAME = manifest_module.CHECKSUM_FILE_NAME
BUNDLE_FILE_NAME = "windows-security-submission-v1.json"
BUNDLE_CHECKSUM_FILE_NAME = f"{BUNDLE_FILE_NAME}.sha256"
BUNDLE_SCHEMA_VERSION = 1
BUNDLE_TYPE = "windows-security-submission"
WINDOWS_PLATFORM = manifest_module.WINDOWS_PLATFORM
WINDOWS_ARCHITECTURE = manifest_module.WINDOWS_ARCHITECTURE
COMPONENTS = manifest_module.COMPONENTS
COMPONENT_ROLES = manifest_module.COMPONENT_ROLES
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")

OPERATOR_INSTRUCTIONS = (
    "HOLD: do not restore, execute, or add an antivirus exclusion for this component.",
    "Use only the exact helper digest, size, and installed-component manifest "
    "binding recorded here.",
    "Record only sanitized vendor identifiers in the detection placeholders; never "
    "add logs, paths, credentials, tokens, or user context.",
    "A prepared bundle is not a submission, clearance, malware determination, "
    "publisher-trust decision, or release acceptance.",
)


class WindowsSecuritySubmissionError(ValueError):
    """Raised when a content-free security submission bundle cannot be prepared."""


@dataclass(frozen=True)
class _DirectoryIdentity:
    device: int
    inode: int
    file_type: int
    file_attributes: int


@dataclass(frozen=True)
class _OutputFileIdentity:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int
    file_attributes: int


@dataclass(frozen=True)
class _InputIdentity:
    device: int
    inode: int
    file_type: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int
    file_attributes: int


@dataclass(frozen=True)
class _BoundInput:
    label: str
    path: Path
    ancestors: tuple[tuple[Path, _DirectoryIdentity], ...]
    identity: _InputIdentity


@dataclass(frozen=True)
class _InputBinding:
    source_root: Path
    source_root_identity: _DirectoryIdentity
    inputs: tuple[_BoundInput, ...]


class _ContentFreeArgumentParser(argparse.ArgumentParser):
    """Reject malformed CLI input without reflecting user-controlled text."""

    def error(self, _message: str) -> Never:
        raise SystemExit(2)


def _safe_call(label: str, function: Callable[[], Any]) -> Any:
    """Map canonical-manifest failures to path-free errors for operator output."""

    try:
        return function()
    except (manifest_module.InstalledComponentManifestError, OSError) as exc:
        raise WindowsSecuritySubmissionError(f"{label} cannot be verified") from exc


def _input_identity(path: Path, *, label: str, directory: bool) -> _InputIdentity:
    try:
        information = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise WindowsSecuritySubmissionError(f"{label} cannot be verified") from exc
    attributes = int(getattr(information, "st_file_attributes", 0))
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(information.st_mode)
        or bool(attributes & reparse_flag)
        or (directory and not stat.S_ISDIR(information.st_mode))
        or (not directory and not stat.S_ISREG(information.st_mode))
    ):
        raise WindowsSecuritySubmissionError(f"{label} cannot be verified")
    links = int(information.st_nlink)
    if not directory and links != 1:
        raise WindowsSecuritySubmissionError(f"{label} cannot be verified")
    return _InputIdentity(
        device=int(information.st_dev),
        inode=int(information.st_ino),
        file_type=stat.S_IFMT(information.st_mode),
        links=links,
        size=int(information.st_size),
        modified_ns=int(information.st_mtime_ns),
        changed_ns=int(information.st_ctime_ns),
        file_attributes=attributes,
    )


def _input_directory_identity(path: Path, *, label: str) -> _DirectoryIdentity:
    """Bind a directory entry without treating expected child writes as swaps."""

    try:
        information = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise WindowsSecuritySubmissionError(f"{label} cannot be verified") from exc
    attributes = int(getattr(information, "st_file_attributes", 0))
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        stat.S_ISLNK(information.st_mode)
        or bool(attributes & reparse_flag)
        or not stat.S_ISDIR(information.st_mode)
    ):
        raise WindowsSecuritySubmissionError(f"{label} cannot be verified")
    return _DirectoryIdentity(
        device=int(information.st_dev),
        inode=int(information.st_ino),
        file_type=stat.S_IFMT(information.st_mode),
        file_attributes=attributes,
    )


def _capture_input_binding(
    *, source_root: Path, inputs: Mapping[str, Path]
) -> _InputBinding:
    try:
        lexical_root, _resolved_root = manifest_module._validate_root(source_root)
    except (manifest_module.InstalledComponentManifestError, OSError) as exc:
        raise WindowsSecuritySubmissionError("source root cannot be verified") from exc
    root_identity = _input_directory_identity(lexical_root, label="source root")
    bound: list[_BoundInput] = []
    for label, value in inputs.items():
        lexical = manifest_module._absolute(value)
        try:
            manifest_module._reject_linked_path(lexical, lexical_root)
            relative = lexical.relative_to(lexical_root)
        except (ValueError, manifest_module.InstalledComponentManifestError) as exc:
            raise WindowsSecuritySubmissionError(f"{label} cannot be verified") from exc
        ancestors: list[tuple[Path, _DirectoryIdentity]] = [(lexical_root, root_identity)]
        current = lexical_root
        for part in relative.parts[:-1]:
            current /= part
            ancestors.append(
                (
                    current,
                    _input_directory_identity(current, label=f"{label} directory"),
                )
            )
        identity = _input_identity(lexical, label=label, directory=False)
        bound.append(_BoundInput(label, lexical, tuple(ancestors), identity))
    return _InputBinding(lexical_root, root_identity, tuple(bound))


def _revalidate_input_binding(binding: _InputBinding) -> None:
    if _input_directory_identity(binding.source_root, label="source root") != (
        binding.source_root_identity
    ):
        raise WindowsSecuritySubmissionError("source root changed while it was verified")
    for item in binding.inputs:
        for path, expected in item.ancestors:
            if _input_directory_identity(path, label=item.label) != expected:
                raise WindowsSecuritySubmissionError(
                    f"{item.label} path ancestry changed while it was verified"
                )
        if _input_identity(item.path, label=item.label, directory=False) != item.identity:
            raise WindowsSecuritySubmissionError(f"{item.label} changed while it was verified")


def _manifest_component(payload: Mapping[str, Any], role: str) -> dict[str, Any]:
    components = cast(list[dict[str, Any]], payload["components"])
    component = next((item for item in components if item["role"] == role), None)
    if component is None:
        raise WindowsSecuritySubmissionError("requested helper role is absent from the manifest")
    return component


def _directory_identity(path: Path) -> _DirectoryIdentity:
    try:
        information = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise WindowsSecuritySubmissionError("output directory cannot be verified") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = int(getattr(information, "st_file_attributes", 0))
    if (
        path.is_symlink()
        or bool(attributes & reparse_flag)
        or not stat.S_ISDIR(information.st_mode)
    ):
        raise WindowsSecuritySubmissionError("output directory cannot be verified")
    return _DirectoryIdentity(
        device=int(information.st_dev),
        inode=int(information.st_ino),
        file_type=stat.S_IFMT(information.st_mode),
        file_attributes=attributes,
    )


def _output_file_identity(path: Path) -> _OutputFileIdentity:
    try:
        information = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise WindowsSecuritySubmissionError("output file cannot be verified") from exc
    return _output_identity_from_stat(information)


def _output_stream_identity(stream: Any) -> _OutputFileIdentity:
    try:
        information = os.fstat(stream.fileno())
    except OSError as exc:
        raise WindowsSecuritySubmissionError("output file cannot be verified") from exc
    return _output_identity_from_stat(information)


def _output_identity_from_stat(information: os.stat_result) -> _OutputFileIdentity:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = int(getattr(information, "st_file_attributes", 0))
    if (
        bool(attributes & reparse_flag)
        or stat.S_ISLNK(information.st_mode)
        or not stat.S_ISREG(information.st_mode)
        or int(information.st_nlink) != 1
    ):
        raise WindowsSecuritySubmissionError("output file cannot be verified")
    return _OutputFileIdentity(
        device=int(information.st_dev),
        inode=int(information.st_ino),
        mode=int(information.st_mode),
        links=int(information.st_nlink),
        size=int(information.st_size),
        modified_ns=int(information.st_mtime_ns),
        changed_ns=int(information.st_ctime_ns),
        file_attributes=attributes,
    )


def _same_output_object(left: _OutputFileIdentity, right: _OutputFileIdentity) -> bool:
    """Compare handle/path ownership fields that remain stable across close."""

    return (
        left.device,
        left.inode,
        stat.S_IFMT(left.mode),
        left.links,
        left.file_attributes,
    ) == (
        right.device,
        right.inode,
        stat.S_IFMT(right.mode),
        right.links,
        right.file_attributes,
    )


def _output_entry_names(path: Path) -> set[str]:
    try:
        entries = list(path.iterdir())
    except OSError as exc:
        raise WindowsSecuritySubmissionError("output directory cannot be verified") from exc
    for entry in entries:
        _output_file_identity(entry)
    return {entry.name for entry in entries}


def _assert_owned_output_directory(
    output: Path,
    identity: _DirectoryIdentity,
    *,
    expected_names: set[str],
) -> None:
    before = _directory_identity(output)
    if before != identity:
        raise WindowsSecuritySubmissionError("output directory identity changed")
    names = _output_entry_names(output)
    after = _directory_identity(output)
    if after != identity or names != expected_names:
        raise WindowsSecuritySubmissionError("output directory identity or contents changed")


def _create_owned_output_directory(
    output_dir: Path, *, source_root: Path
) -> tuple[Path, _DirectoryIdentity]:
    try:
        lexical_root, resolved_root = manifest_module._validate_root(source_root)
        lexical = manifest_module._absolute(output_dir)
        relative = lexical.relative_to(lexical_root)
        if not relative.parts:
            raise WindowsSecuritySubmissionError("output directory must be newly created")
        manifest_module._reject_linked_path(lexical.parent, lexical_root)
    except (ValueError, manifest_module.InstalledComponentManifestError) as exc:
        raise WindowsSecuritySubmissionError("output directory cannot be verified") from exc
    try:
        parent_identity = _directory_identity(lexical.parent)
        parent_information = lexical.parent.stat(follow_symlinks=False)
    except OSError as exc:
        raise WindowsSecuritySubmissionError("output directory cannot be verified") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    if (
        lexical.parent.is_symlink()
        or bool(getattr(parent_information, "st_file_attributes", 0) & reparse_flag)
        or not stat.S_ISDIR(parent_information.st_mode)
        or os.path.lexists(str(lexical))
    ):
        raise WindowsSecuritySubmissionError("output directory cannot be verified")
    try:
        lexical.mkdir()
    except OSError as exc:
        raise WindowsSecuritySubmissionError("output directory cannot be verified") from exc
    identity = _directory_identity(lexical)
    try:
        resolved = lexical.resolve(strict=True)
        resolved.relative_to(resolved_root)
        manifest_module._reject_linked_path(lexical, lexical_root)
        if _directory_identity(lexical.parent) != parent_identity:
            raise WindowsSecuritySubmissionError("output parent identity changed")
    except (OSError, ValueError, manifest_module.InstalledComponentManifestError) as exc:
        raise WindowsSecuritySubmissionError("output directory cannot be verified") from exc
    _assert_owned_output_directory(output=lexical, identity=identity, expected_names=set())
    return lexical, identity


def _component_paths(
    *,
    main_path: Path,
    mcp_path: Path,
    recovery_path: Path,
    updater_path: Path,
) -> dict[str, Path]:
    return {
        "main": main_path,
        "mcp": mcp_path,
        "recovery": recovery_path,
        "updater": updater_path,
    }


def _verify_inputs(
    *,
    archive_path: Path,
    package_path: Path,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    manifest_path: Path,
    manifest_checksum_path: Path,
    source_root: Path,
    version: str,
    source_commit: str,
) -> tuple[Path, Path, dict[str, Any], _InputBinding, dict[str, Any]]:
    """Return metadata only after both phases share one stable input binding."""

    binding = _capture_input_binding(
        source_root=source_root,
        inputs={
            "release archive": archive_path,
            "archive package": package_path,
            "direct package": direct_package_path,
            **{f"{role} executable": path for role, path in component_paths.items()},
            "installed-component manifest": manifest_path,
            "installed-component checksum": manifest_checksum_path,
        },
    )

    manifest = _safe_call(
        "manifest",
        lambda: manifest_module._regular_file(
            manifest_path,
            root=source_root,
            label="installed-component manifest",
        ),
    )
    if manifest.name != MANIFEST_FILE_NAME:
        raise WindowsSecuritySubmissionError("manifest filename is not canonical")
    checksum = _safe_call(
        "manifest checksum",
        lambda: manifest_module._regular_file(
            manifest_checksum_path,
            root=source_root,
            label="installed-component checksum",
        ),
    )
    if checksum != manifest.with_name(MANIFEST_CHECKSUM_FILE_NAME):
        raise WindowsSecuritySubmissionError("manifest checksum path is not canonical")

    manifest_payload = _safe_call(
        "manifest verification",
        lambda: manifest_module.verify_manifest(
            manifest_path=manifest,
            package_path=package_path,
            direct_package_path=direct_package_path,
            component_paths=component_paths,
            source_root=source_root,
            version=version,
            source_commit=source_commit,
            platform=WINDOWS_PLATFORM,
            architecture=WINDOWS_ARCHITECTURE,
        ),
    )
    _revalidate_input_binding(binding)
    archive_payload = _safe_call(
        "archive verification",
        lambda: manifest_module.verify_archive(
            archive_path=archive_path,
            direct_package_path=direct_package_path,
            component_paths=component_paths,
            source_root=source_root,
            version=version,
            source_commit=source_commit,
            platform=WINDOWS_PLATFORM,
            architecture=WINDOWS_ARCHITECTURE,
        ),
    )
    _revalidate_input_binding(binding)
    if archive_payload != manifest_payload:
        raise WindowsSecuritySubmissionError("archive and manifest verification differ")
    measurements = _safe_call(
        "verified input metadata",
        lambda: manifest_module._stable_measurements(
            {
                "release archive": archive_path,
                "archive package": package_path,
                "direct package": direct_package_path,
                **{
                    f"{role} executable": path
                    for role, path in component_paths.items()
                },
                "manifest": manifest,
                "manifest checksum": checksum,
            }
        ),
    )
    _revalidate_input_binding(binding)
    return manifest, checksum, manifest_payload, binding, measurements


def _build_bundle_with_binding(
    *,
    archive_path: Path,
    package_path: Path,
    direct_package_path: Path,
    main_path: Path,
    mcp_path: Path,
    recovery_path: Path,
    updater_path: Path,
    manifest_path: Path,
    manifest_checksum_path: Path,
    source_root: Path,
    role: str,
    source_commit: str,
    version: str,
) -> tuple[dict[str, Any], _InputBinding]:
    """Build a content-free bundle after proving every release input."""

    if not isinstance(role, str) or role not in COMPONENT_ROLES:
        raise WindowsSecuritySubmissionError("helper role is invalid")
    if not isinstance(source_commit, str) or COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise WindowsSecuritySubmissionError("source commit must be a full lowercase SHA-1")
    if not isinstance(version, str):
        raise WindowsSecuritySubmissionError("product version is invalid")
    try:
        manifest_module.ReleaseVersion.parse(version)
    except ValueError as exc:
        raise WindowsSecuritySubmissionError("product version is invalid") from exc

    component_paths = _component_paths(
        main_path=main_path,
        mcp_path=mcp_path,
        recovery_path=recovery_path,
        updater_path=updater_path,
    )
    _manifest, _checksum, manifest_payload, input_binding, measurements = _verify_inputs(
        archive_path=archive_path,
        package_path=package_path,
        direct_package_path=direct_package_path,
        component_paths=component_paths,
        manifest_path=manifest_path,
        manifest_checksum_path=manifest_checksum_path,
        source_root=source_root,
        version=version,
        source_commit=source_commit,
    )
    component = _manifest_component(manifest_payload, role)
    selected_measurement = measurements[f"{role} executable"]
    selected_path = component_paths[role]
    if (
        selected_path.name.casefold() not in manifest_module.SOURCE_BASENAMES[role]
        or component["sha256"] != selected_measurement.digest
        or component["size"] != selected_measurement.size
    ):
        raise WindowsSecuritySubmissionError(
            "requested helper differs from the verified component measurement"
        )
    candidate = {
        "authenticode_status": component["authenticode"]["status"],
        "filename": selected_path.name,
        "role": component["role"],
        "sha256": selected_measurement.digest,
        "size": selected_measurement.size,
    }
    binding = {
        "architecture": manifest_payload["architecture"],
        "archive": {
            "filename": archive_path.name,
            "sha256": measurements["release archive"].digest,
            "size": measurements["release archive"].size,
        },
        "component": candidate,
        "direct_package": {
            "filename": direct_package_path.name,
            "sha256": measurements["direct package"].digest,
            "size": measurements["direct package"].size,
        },
        "manifest": {
            "checksum": {
                "filename": MANIFEST_CHECKSUM_FILE_NAME,
                "sha256": measurements["manifest checksum"].digest,
                "size": measurements["manifest checksum"].size,
            },
            "filename": MANIFEST_FILE_NAME,
            "sha256": measurements["manifest"].digest,
            "size": measurements["manifest"].size,
        },
        "package": {
            "filename": package_path.name,
            "sha256": measurements["archive package"].digest,
            "size": measurements["archive package"].size,
        },
        "platform": manifest_payload["platform"],
        "source_commit": manifest_payload["source_commit"],
        "version": manifest_payload["version"],
    }
    payload = {
        "architecture": WINDOWS_ARCHITECTURE,
        "bundle_type": BUNDLE_TYPE,
        "candidate": candidate,
        "detection": {
            "event_id": None,
            "observed_at": None,
            "product": None,
            "reassessment_id": None,
            "severity": None,
            "submission_id": None,
            "status": "not-provided",
            "threat_name": None,
            "vendor": None,
        },
        "manifest_binding": binding,
        "operator_instructions": list(OPERATOR_INSTRUCTIONS),
        "platform": WINDOWS_PLATFORM,
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "source_commit": source_commit,
        "status": "hold",
        "trust": {
            "clearance": "not-claimed",
            "malware_status": "not-determined",
            "publisher_status": "not-asserted",
            "submission_status": "not-submitted",
        },
        "version": manifest_payload["version"],
    }
    return payload, input_binding


def build_bundle(
    *,
    archive_path: Path,
    package_path: Path,
    direct_package_path: Path,
    main_path: Path,
    mcp_path: Path,
    recovery_path: Path,
    updater_path: Path,
    manifest_path: Path,
    manifest_checksum_path: Path,
    source_root: Path,
    role: str,
    source_commit: str,
    version: str,
) -> dict[str, Any]:
    """Build a content-free bundle after proving every release input."""

    payload, _binding = _build_bundle_with_binding(
        archive_path=archive_path,
        package_path=package_path,
        direct_package_path=direct_package_path,
        main_path=main_path,
        mcp_path=mcp_path,
        recovery_path=recovery_path,
        updater_path=updater_path,
        manifest_path=manifest_path,
        manifest_checksum_path=manifest_checksum_path,
        source_root=source_root,
        role=role,
        source_commit=source_commit,
        version=version,
    )
    return payload


def _write_owned_new(
    output: Path,
    identity: _DirectoryIdentity,
    filename: str,
    content: bytes,
    *,
    label: str,
    expected_names: set[str],
) -> _OutputFileIdentity:
    _assert_owned_output_directory(output, identity, expected_names=expected_names)
    path = output / filename
    if os.path.lexists(str(path)):
        raise WindowsSecuritySubmissionError(f"refusing to replace existing {label}")
    file_identity: _OutputFileIdentity | None = None
    stream: Any | None = None
    try:
        stream = path.open("xb")
        # Bind the exclusively created handle before the pathname can be trusted.
        file_identity = _output_stream_identity(stream)
        _write_all(stream, content)
        file_identity = _output_stream_identity(stream)
        stream.close()
        stream = None
        path_identity = _output_file_identity(path)
        if not _same_output_object(path_identity, file_identity):
            raise WindowsSecuritySubmissionError("output file identity changed")
        file_identity = path_identity
        _assert_owned_output_directory(output, identity, expected_names={*expected_names, filename})
        return file_identity
    except FileExistsError as exc:
        raise WindowsSecuritySubmissionError(f"refusing to replace existing {label}") from exc
    except BaseException as exc:
        if stream is not None:
            with suppress(OSError):
                stream.close()
        if isinstance(exc, OSError):
            raise WindowsSecuritySubmissionError(f"could not write {label}") from exc
        raise


def _write_all(stream: Any, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = stream.write(content[offset:])
        if not isinstance(written, int) or written <= 0:
            raise OSError("short output write")
        offset += written
    stream.flush()
    os.fsync(stream.fileno())


def _verify_owned_output(
    output: Path,
    identity: _DirectoryIdentity,
    filename: str,
    expected_identity: _OutputFileIdentity,
    expected_content: bytes,
    *,
    label: str,
    expected_names: set[str],
) -> None:
    """Re-read exact output bytes while binding the handle and pathname."""

    _assert_owned_output_directory(output, identity, expected_names=expected_names)
    path = output / filename
    try:
        with path.open("rb") as stream:
            opened_identity = _output_stream_identity(stream)
            if (
                not _same_output_object(opened_identity, expected_identity)
                or opened_identity.size != len(expected_content)
            ):
                raise WindowsSecuritySubmissionError(f"{label} changed after it was written")
            actual = stream.read(len(expected_content) + 1)
            if actual != expected_content:
                raise WindowsSecuritySubmissionError(f"{label} changed after it was written")
            if _output_stream_identity(stream) != opened_identity:
                raise WindowsSecuritySubmissionError(f"{label} changed after it was written")
    except OSError as exc:
        raise WindowsSecuritySubmissionError(f"{label} cannot be revalidated") from exc
    path_identity = _output_file_identity(path)
    if (
        not _same_output_object(path_identity, expected_identity)
        or path_identity.size != len(expected_content)
    ):
        raise WindowsSecuritySubmissionError(f"{label} changed after it was written")
    _assert_owned_output_directory(output, identity, expected_names=expected_names)


def create_bundle(
    *,
    output_dir: Path,
    archive_path: Path,
    package_path: Path,
    direct_package_path: Path,
    main_path: Path,
    mcp_path: Path,
    recovery_path: Path,
    updater_path: Path,
    manifest_path: Path,
    manifest_checksum_path: Path,
    source_root: Path,
    role: str,
    source_commit: str,
    version: str,
) -> tuple[Path, Path]:
    """Write a new bundle and detached checksum without replacing existing files."""

    payload, input_binding = _build_bundle_with_binding(
        archive_path=archive_path,
        package_path=package_path,
        direct_package_path=direct_package_path,
        main_path=main_path,
        mcp_path=mcp_path,
        recovery_path=recovery_path,
        updater_path=updater_path,
        manifest_path=manifest_path,
        manifest_checksum_path=manifest_checksum_path,
        source_root=source_root,
        role=role,
        source_commit=source_commit,
        version=version,
    )
    raw_bundle = manifest_module.canonical_json(payload)
    _revalidate_input_binding(input_binding)
    output, output_identity = _create_owned_output_directory(
        output_dir, source_root=source_root
    )
    bundle_path = output / BUNDLE_FILE_NAME
    checksum_path = output / BUNDLE_CHECKSUM_FILE_NAME
    try:
        _revalidate_input_binding(input_binding)
        bundle_identity = _write_owned_new(
            output,
            output_identity,
            BUNDLE_FILE_NAME,
            raw_bundle,
            label="security submission bundle",
            expected_names=set(),
        )
        _verify_owned_output(
            output,
            output_identity,
            BUNDLE_FILE_NAME,
            bundle_identity,
            raw_bundle,
            label="security submission bundle",
            expected_names={BUNDLE_FILE_NAME},
        )
        digest = hashlib.sha256(raw_bundle).hexdigest()
        raw_checksum = f"{digest}  {BUNDLE_FILE_NAME}\n".encode("ascii")
        _revalidate_input_binding(input_binding)
        checksum_identity = _write_owned_new(
            output,
            output_identity,
            BUNDLE_CHECKSUM_FILE_NAME,
            raw_checksum,
            label="security submission checksum",
            expected_names={BUNDLE_FILE_NAME},
        )
        complete_names = {BUNDLE_FILE_NAME, BUNDLE_CHECKSUM_FILE_NAME}
        _verify_owned_output(
            output,
            output_identity,
            BUNDLE_FILE_NAME,
            bundle_identity,
            raw_bundle,
            label="security submission bundle",
            expected_names=complete_names,
        )
        _verify_owned_output(
            output,
            output_identity,
            BUNDLE_CHECKSUM_FILE_NAME,
            checksum_identity,
            raw_checksum,
            label="security submission checksum",
            expected_names=complete_names,
        )
        _revalidate_input_binding(input_binding)
        _verify_owned_output(
            output,
            output_identity,
            BUNDLE_FILE_NAME,
            bundle_identity,
            raw_bundle,
            label="security submission bundle",
            expected_names=complete_names,
        )
        _verify_owned_output(
            output,
            output_identity,
            BUNDLE_CHECKSUM_FILE_NAME,
            checksum_identity,
            raw_checksum,
            label="security submission checksum",
            expected_names=complete_names,
        )
    except BaseException:
        # A pathname can be replaced after any identity check and before an
        # unlink. Retain failed operation-owned output rather than risk deleting
        # an object that this process did not create.
        raise
    return bundle_path, checksum_path


def _parser() -> argparse.ArgumentParser:
    parser = _ContentFreeArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--direct-package", type=Path, required=True)
    parser.add_argument("--main", type=Path, required=True)
    parser.add_argument("--mcp", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--updater", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-checksum", type=Path, required=True)
    parser.add_argument("--role", choices=sorted(COMPONENT_ROLES), required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main() -> int:
    try:
        arguments = _parser().parse_args()
        create_bundle(
            output_dir=arguments.output_dir,
            archive_path=arguments.archive,
            package_path=arguments.package,
            direct_package_path=arguments.direct_package,
            main_path=arguments.main,
            mcp_path=arguments.mcp,
            recovery_path=arguments.recovery,
            updater_path=arguments.updater,
            manifest_path=arguments.manifest,
            manifest_checksum_path=arguments.manifest_checksum,
            source_root=arguments.source_root,
            role=arguments.role,
            source_commit=arguments.source_commit,
            version=arguments.version,
        )
    except (WindowsSecuritySubmissionError, OSError):
        print("windows security submission error: operation rejected", file=sys.stderr)
        return 1
    print(BUNDLE_FILE_NAME)
    print(BUNDLE_CHECKSUM_FILE_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
