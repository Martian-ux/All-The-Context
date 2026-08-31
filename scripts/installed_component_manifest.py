"""Create and verify deterministic provenance for installed Windows executables."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, NoReturn, cast

from allthecontext.release_manifest import ReleaseVersion

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_TYPE = "installed-component"
MANIFEST_FILE_NAME = "installed-component-manifest-v1.json"
CHECKSUM_FILE_NAME = f"{MANIFEST_FILE_NAME}.sha256"
WINDOWS_PLATFORM = "windows"
WINDOWS_ARCHITECTURE = "x86_64"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SAFE_PACKAGE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.exe")
AUTHENTICODE_STATUSES = frozenset({"not-present", "present-unverified"})
COMPONENTS: tuple[tuple[str, str], ...] = (
    ("main", "AllTheContext.exe"),
    ("mcp", "AllTheContextMCP.exe"),
    ("recovery", "AllTheContextRecovery.exe"),
    ("updater", "AllTheContextUpdater.exe"),
)
COMPONENT_ROLES = frozenset(role for role, _filename in COMPONENTS)
SOURCE_BASENAMES: dict[str, frozenset[str]] = {
    "main": frozenset({"allthecontext.exe", "allthecontextsetup.exe"}),
    "mcp": frozenset({"allthecontextmcp.exe"}),
    "recovery": frozenset({"allthecontextrecovery.exe"}),
    "updater": frozenset({"allthecontextupdater.exe"}),
}


class InstalledComponentManifestError(ValueError):
    """Raised when installed-component provenance cannot be trusted."""


@dataclass(frozen=True)
class _FileSnapshot:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> _FileSnapshot:
        return cls(
            device=int(value.st_dev),
            inode=int(value.st_ino),
            mode=int(value.st_mode),
            links=int(value.st_nlink),
            size=int(value.st_size),
            modified_ns=int(value.st_mtime_ns),
            changed_ns=int(value.st_ctime_ns),
        )


@dataclass(frozen=True)
class _FileMeasurement:
    digest: str
    size: int
    snapshot: _FileSnapshot


def _is_reparse_or_link(value: Path) -> bool:
    try:
        information = value.stat(follow_symlinks=False)
    except OSError as exc:
        raise InstalledComponentManifestError(
            f"cannot inspect installed-component input: {value}"
        ) from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return value.is_symlink() or bool(getattr(information, "st_file_attributes", 0) & reparse_flag)


def _absolute(value: Path) -> Path:
    return Path(os.path.abspath(str(value.expanduser())))


def _validate_root(root: Path) -> tuple[Path, Path]:
    lexical = _absolute(root)
    if _is_reparse_or_link(lexical):
        raise InstalledComponentManifestError("source root cannot be a symlink or reparse point")
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise InstalledComponentManifestError(f"source root is unavailable: {lexical}") from exc
    if not resolved.is_dir():
        raise InstalledComponentManifestError("source root must be a directory")
    return lexical, resolved


def _reject_linked_path(lexical: Path, root: Path) -> None:
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise InstalledComponentManifestError(
            f"installed-component input escapes the source root: {lexical.name}"
        ) from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.exists() and _is_reparse_or_link(current):
            raise InstalledComponentManifestError(
                f"installed-component input uses a symlink or reparse point: {current.name}"
            )


def _regular_file(value: Path, *, root: Path, label: str) -> Path:
    lexical_root, resolved_root = _validate_root(root)
    lexical = _absolute(value)
    _reject_linked_path(lexical, lexical_root)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise InstalledComponentManifestError(f"{label} is missing: {lexical}") from exc
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise InstalledComponentManifestError(f"{label} escapes the source root") from exc
    if _is_reparse_or_link(lexical) or _is_reparse_or_link(resolved):
        raise InstalledComponentManifestError(f"{label} is a symlink or reparse point")
    try:
        information = resolved.stat(follow_symlinks=False)
    except OSError as exc:
        raise InstalledComponentManifestError(f"{label} cannot be inspected") from exc
    if not stat.S_ISREG(information.st_mode):
        raise InstalledComponentManifestError(f"{label} must be a regular file")
    return resolved


def _snapshot(value: os.stat_result) -> _FileSnapshot:
    return _FileSnapshot.from_stat(value)


def _hash_open_file(stream: BinaryIO, *, label: str) -> _FileMeasurement:
    try:
        before = _snapshot(os.fstat(stream.fileno()))
        digest = hashlib.sha256()
        size = 0
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
        after = _snapshot(os.fstat(stream.fileno()))
    except OSError as exc:
        raise InstalledComponentManifestError(f"could not hash {label}") from exc
    if before != after or size != before.size:
        raise InstalledComponentManifestError(f"{label} changed while it was hashed")
    return _FileMeasurement(digest.hexdigest(), size, after)


def _hash_file(value: Path, *, label: str) -> _FileMeasurement:
    try:
        before_close = _snapshot(value.stat(follow_symlinks=False))
        with value.open("rb") as stream:
            measurement = _hash_open_file(stream, label=label)
        after_close = _snapshot(value.stat(follow_symlinks=False))
    except OSError as exc:
        raise InstalledComponentManifestError(f"could not read {label}") from exc
    if before_close != after_close:
        raise InstalledComponentManifestError(f"{label} changed after it was hashed")
    return _FileMeasurement(measurement.digest, measurement.size, after_close)


def _stable_measurements(paths: Mapping[str, Path]) -> dict[str, _FileMeasurement]:
    first = {label: _hash_file(path, label=label) for label, path in paths.items()}
    second = {label: _hash_file(path, label=label) for label, path in paths.items()}
    for label, measurement in first.items():
        if measurement != second[label]:
            raise InstalledComponentManifestError(f"{label} changed after it was hashed")
    return first


def _safe_name(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SAFE_PACKAGE_NAME.fullmatch(value) is None:
        raise InstalledComponentManifestError(f"{label} has an unsafe executable filename")
    return value


def authenticode_status(value: Path) -> str:
    """Return only evidence available without claiming a publisher signature.

    ``present-unverified`` means that the PE certificate table exists. It does
    not claim that the certificate is valid, trusted, or identifies a publisher.
    """

    try:
        file_size = value.stat().st_size
        with value.open("rb") as stream:
            dos_header = stream.read(64)
            if len(dos_header) != 64 or dos_header[:2] != b"MZ":
                raise InstalledComponentManifestError(f"{value.name} is not a valid PE executable")
            pe_offset = int.from_bytes(dos_header[60:64], "little")
            if pe_offset < 64 or pe_offset > file_size - 24:
                raise InstalledComponentManifestError(f"{value.name} has an invalid PE header")
            stream.seek(pe_offset)
            pe_header = stream.read(24)
            if len(pe_header) != 24 or pe_header[:4] != b"PE\0\0":
                raise InstalledComponentManifestError(f"{value.name} is missing its PE signature")
            optional_size = int.from_bytes(pe_header[20:22], "little")
            if optional_size < 2 or optional_size > 64 * 1024:
                raise InstalledComponentManifestError(
                    f"{value.name} has an invalid PE optional header"
                )
            optional = stream.read(optional_size)
    except OSError as exc:
        raise InstalledComponentManifestError(
            f"could not inspect Authenticode state: {value.name}"
        ) from exc

    magic = int.from_bytes(optional[:2], "little")
    if magic == 0x10B:
        directory_count_offset, directory_offset = 92, 96
    elif magic == 0x20B:
        directory_count_offset, directory_offset = 108, 112
    else:
        raise InstalledComponentManifestError(f"{value.name} has an unsupported PE format")
    if len(optional) < directory_count_offset + 4:
        raise InstalledComponentManifestError(f"{value.name} lacks PE data-directory metadata")
    directory_count = int.from_bytes(
        optional[directory_count_offset : directory_count_offset + 4], "little"
    )
    if directory_count <= 4:
        return "not-present"
    certificate_entry = directory_offset + (4 * 8)
    if len(optional) < certificate_entry + 8:
        raise InstalledComponentManifestError(f"{value.name} has a truncated certificate table")
    location = int.from_bytes(optional[certificate_entry : certificate_entry + 4], "little")
    size = int.from_bytes(optional[certificate_entry + 4 : certificate_entry + 8], "little")
    if location == 0 and size == 0:
        return "not-present"
    if location <= 0 or size <= 0 or location + size > file_size:
        raise InstalledComponentManifestError(f"{value.name} has an invalid certificate table")
    return "present-unverified"


def _validate_header(*, version: str, source_commit: str, platform: str, architecture: str) -> None:
    try:
        ReleaseVersion.parse(version)
    except ValueError as exc:
        raise InstalledComponentManifestError(f"invalid product version: {version!r}") from exc
    if COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise InstalledComponentManifestError("source commit must be a full lowercase SHA-1")
    if platform != WINDOWS_PLATFORM or architecture != WINDOWS_ARCHITECTURE:
        raise InstalledComponentManifestError(
            "installed-component manifests currently support Windows x86_64 only"
        )


def _expected_header(
    *,
    version: str | None,
    source_commit: str | None,
    platform: str | None,
    architecture: str | None,
) -> tuple[str, str, str, str] | None:
    values = (version, source_commit, platform, architecture)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise InstalledComponentManifestError(
            "verification requires all four expected manifest header fields"
        )
    return cast(tuple[str, str, str, str], values)


def _validate_expected_header(
    payload: Mapping[str, Any], expected: tuple[str, str, str, str] | None
) -> None:
    if expected is None:
        return
    actual = (
        cast(str, payload["version"]),
        cast(str, payload["source_commit"]),
        cast(str, payload["platform"]),
        cast(str, payload["architecture"]),
    )
    if actual != expected:
        raise InstalledComponentManifestError(
            "installed-component manifest header does not match verification inputs"
        )


def _component_paths(
    values: Mapping[str, Path],
    *,
    root: Path,
    package_path: Path,
    direct_package_path: Path,
) -> tuple[Path, Path, dict[str, Path]]:
    if set(values) != COMPONENT_ROLES:
        raise InstalledComponentManifestError(
            "manifest must name exactly main, mcp, recovery, and updater"
        )
    package = _regular_file(package_path, root=root, label="package")
    direct_package = _regular_file(direct_package_path, root=root, label="direct package")
    if package.name != "AllTheContextSetup.exe":
        raise InstalledComponentManifestError("package must be named AllTheContextSetup.exe")
    _safe_name(direct_package.name, label="direct package")
    resolved: dict[str, Path] = {}
    identities: dict[tuple[int, int], str] = {}
    for label, path in (("package", package), ("direct package", direct_package)):
        identity = (path.stat().st_dev, path.stat().st_ino)
        if identity in identities:
            raise InstalledComponentManifestError(
                f"duplicate executable input: {identities[identity]} and {label}"
            )
        identities[identity] = label
    for role, _expected_name in COMPONENTS:
        path = _regular_file(values[role], root=root, label=f"{role} executable")
        if path.name.casefold() not in SOURCE_BASENAMES[role]:
            raise InstalledComponentManifestError(
                f"{role} executable has an unexpected source filename"
            )
        identity = (path.stat().st_dev, path.stat().st_ino)
        previous = identities.get(identity)
        if previous is not None:
            raise InstalledComponentManifestError(
                f"duplicate executable input: {previous} and {role}"
            )
        identities[identity] = role
        resolved[role] = path
    names = [expected_name.casefold() for _role, expected_name in COMPONENTS]
    if len(names) != len(set(names)):
        raise InstalledComponentManifestError("component executable filenames are duplicated")
    if direct_package.name.casefold() in names:
        raise InstalledComponentManifestError(
            "direct package filename duplicates an installed component"
        )
    return package, direct_package, resolved


def build_manifest(
    *,
    package_path: Path,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    version: str,
    source_commit: str,
    platform: str = WINDOWS_PLATFORM,
    architecture: str = WINDOWS_ARCHITECTURE,
) -> dict[str, Any]:
    """Build a manifest after proving all inputs are stable regular files."""

    _validate_header(
        version=version,
        source_commit=source_commit,
        platform=platform,
        architecture=architecture,
    )
    package, direct_package, components = _component_paths(
        component_paths,
        root=source_root,
        package_path=package_path,
        direct_package_path=direct_package_path,
    )
    inputs = {
        "package": package,
        "direct package": direct_package,
        **{f"{role} executable": path for role, path in components.items()},
    }
    measurements = _stable_measurements(inputs)
    package_measurement = measurements["package"]
    direct_measurement = measurements["direct package"]
    if (package_measurement.digest, package_measurement.size) != (
        direct_measurement.digest,
        direct_measurement.size,
    ):
        raise InstalledComponentManifestError("archive package does not match the direct package")
    main_measurement = measurements["main executable"]
    if (main_measurement.digest, main_measurement.size) != (
        package_measurement.digest,
        package_measurement.size,
    ):
        raise InstalledComponentManifestError(
            "main executable does not match archive package digest or size"
        )
    component_values: list[dict[str, Any]] = []
    for role, expected_name in COMPONENTS:
        path = components[role]
        measurement = measurements[f"{role} executable"]
        component_values.append(
            {
                "authenticode": {"status": authenticode_status(path)},
                "filename": expected_name,
                "role": role,
                "sha256": measurement.digest,
                "size": measurement.size,
            }
        )
    for label, path in inputs.items():
        try:
            if _snapshot(path.stat(follow_symlinks=False)) != measurements[label].snapshot:
                raise InstalledComponentManifestError(f"{label} changed after it was hashed")
        except OSError as exc:
            raise InstalledComponentManifestError(f"{label} changed after it was hashed") from exc
    return {
        "architecture": architecture,
        "component_count": len(component_values),
        "components": component_values,
        "manifest_type": MANIFEST_TYPE,
        "package": {
            "direct_package": {
                "filename": direct_package.name,
                "sha256": direct_measurement.digest,
                "size": direct_measurement.size,
            },
            "filename": package.name,
            "sha256": package_measurement.digest,
            "size": package_measurement.size,
        },
        "platform": platform,
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "source_commit": source_commit,
        "version": version,
    }


def canonical_json(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_new(path: Path, payload: bytes, *, label: str) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        raise InstalledComponentManifestError(
            f"refusing to replace existing {label}: {path.name}"
        ) from exc
    except OSError as exc:
        raise InstalledComponentManifestError(f"could not write {label}: {path}") from exc


def _prepare_output_dir(output_dir: Path, *, source_root: Path, allowed: set[str]) -> Path:
    lexical_root, resolved_root = _validate_root(source_root)
    lexical = _absolute(output_dir)
    try:
        lexical.relative_to(lexical_root)
    except ValueError as exc:
        raise InstalledComponentManifestError("manifest output escapes the source root") from exc
    _reject_linked_path(lexical, lexical_root)
    try:
        resolved = lexical.resolve(strict=False)
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise InstalledComponentManifestError("manifest output escapes the source root") from exc
    if lexical.exists():
        if _is_reparse_or_link(lexical) or not lexical.is_dir():
            raise InstalledComponentManifestError("manifest output must be a regular directory")
        existing = {entry.name for entry in lexical.iterdir()}
        if not existing <= allowed:
            raise InstalledComponentManifestError("manifest output contains unexpected files")
    else:
        try:
            lexical.mkdir(parents=True)
        except OSError as exc:
            raise InstalledComponentManifestError(
                "could not create manifest output directory"
            ) from exc
    return lexical


def create_manifest(
    *,
    output_dir: Path,
    package_path: Path,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    version: str,
    source_commit: str,
    platform: str = WINDOWS_PLATFORM,
    architecture: str = WINDOWS_ARCHITECTURE,
) -> tuple[Path, Path]:
    if _absolute(package_path).parent != _absolute(output_dir):
        raise InstalledComponentManifestError("archive package must be staged beside the manifest")
    output = _prepare_output_dir(
        output_dir,
        source_root=source_root,
        allowed={package_path.name, MANIFEST_FILE_NAME, CHECKSUM_FILE_NAME},
    )
    manifest = build_manifest(
        package_path=package_path,
        direct_package_path=direct_package_path,
        component_paths=component_paths,
        source_root=source_root,
        version=version,
        source_commit=source_commit,
        platform=platform,
        architecture=architecture,
    )
    manifest_path = output / MANIFEST_FILE_NAME
    checksum_path = output / CHECKSUM_FILE_NAME
    raw = canonical_json(manifest)
    digest = hashlib.sha256(raw).hexdigest()
    _write_new(manifest_path, raw, label="installed-component manifest")
    try:
        _write_new(
            checksum_path,
            f"{digest}  {MANIFEST_FILE_NAME}\n".encode("ascii"),
            label="installed-component checksum",
        )
    except BaseException:
        manifest_path.unlink(missing_ok=True)
        raise
    return manifest_path, checksum_path


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InstalledComponentManifestError("manifest contains duplicate JSON fields")
        result[key] = value
    return result


def _reject_non_finite_json_number(value: str) -> NoReturn:
    raise InstalledComponentManifestError(
        "installed-component manifest contains a non-finite JSON number"
    )


def _parse_finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise InstalledComponentManifestError(
            "installed-component manifest contains a non-finite JSON number"
        )
    return parsed


def _load_manifest_bytes(raw: bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json_number,
            parse_float=_parse_finite_json_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstalledComponentManifestError(
            "installed-component manifest is not UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise InstalledComponentManifestError("installed-component manifest must be a JSON object")
    payload = cast(dict[str, Any], value)
    if canonical_json(payload) != raw:
        raise InstalledComponentManifestError("installed-component manifest is not canonical JSON")
    return payload


def _descriptor(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"filename", "sha256", "size"}:
        raise InstalledComponentManifestError(f"{label} descriptor is malformed")
    filename = _safe_name(value.get("filename"), label=label)
    digest = value.get("sha256")
    size = value.get("size")
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        raise InstalledComponentManifestError(f"{label} digest is malformed")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise InstalledComponentManifestError(f"{label} size is malformed")
    return {"filename": filename, "sha256": digest, "size": size}


def _validate_shape(value: Mapping[str, Any]) -> None:
    required = {
        "architecture",
        "component_count",
        "components",
        "manifest_type",
        "package",
        "platform",
        "schema_version",
        "source_commit",
        "version",
    }
    schema_version = value.get("schema_version")
    if (
        set(value) != required
        or isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version != MANIFEST_SCHEMA_VERSION
    ):
        raise InstalledComponentManifestError(
            "installed-component manifest fields or schema are invalid"
        )
    _validate_header(
        version=value.get("version") if isinstance(value.get("version"), str) else "",
        source_commit=value.get("source_commit")
        if isinstance(value.get("source_commit"), str)
        else "",
        platform=value.get("platform") if isinstance(value.get("platform"), str) else "",
        architecture=value.get("architecture")
        if isinstance(value.get("architecture"), str)
        else "",
    )
    if value.get("manifest_type") != MANIFEST_TYPE:
        raise InstalledComponentManifestError("installed-component manifest type is invalid")
    package_value = value.get("package")
    if not isinstance(package_value, dict) or set(package_value) != {
        "direct_package",
        "filename",
        "sha256",
        "size",
    }:
        raise InstalledComponentManifestError("installed-component package descriptor is malformed")
    package = _descriptor(
        {key: package_value[key] for key in ("filename", "sha256", "size")},
        label="archive package",
    )
    if package["filename"] != "AllTheContextSetup.exe":
        raise InstalledComponentManifestError("archive package filename is invalid")
    direct = _descriptor(package_value.get("direct_package"), label="direct package")
    if direct["filename"].casefold() == package["filename"].casefold():
        raise InstalledComponentManifestError("archive and direct package names must differ")
    component_count = value.get("component_count")
    if type(component_count) is not int or component_count != len(COMPONENTS):
        raise InstalledComponentManifestError("installed-component count is invalid")
    components = value.get("components")
    if not isinstance(components, list) or len(components) != len(COMPONENTS):
        raise InstalledComponentManifestError("installed-component list is invalid")
    seen_roles: set[str] = set()
    seen_names: set[str] = set()
    for item, (expected_role, expected_name) in zip(components, COMPONENTS, strict=True):
        if not isinstance(item, dict) or set(item) != {
            "authenticode",
            "filename",
            "role",
            "sha256",
            "size",
        }:
            raise InstalledComponentManifestError("installed-component entry is malformed")
        role = item.get("role")
        filename = item.get("filename")
        if role != expected_role or filename != expected_name or role in seen_roles:
            raise InstalledComponentManifestError("installed-component ordering or role is invalid")
        seen_roles.add(cast(str, role))
        folded_name = cast(str, filename).casefold()
        if folded_name in seen_names:
            raise InstalledComponentManifestError("installed-component filenames are duplicated")
        seen_names.add(folded_name)
        _descriptor(
            {key: item[key] for key in ("filename", "sha256", "size")},
            label=f"{expected_role} executable",
        )
        auth = item.get("authenticode")
        if (
            not isinstance(auth, dict)
            or set(auth) != {"status"}
            or auth.get("status") not in AUTHENTICODE_STATUSES
        ):
            raise InstalledComponentManifestError(
                "installed-component Authenticode status is invalid"
            )


def _check_measurement(
    path: Path,
    expected: Mapping[str, Any],
    *,
    label: str,
    allowed_filenames: frozenset[str] | None = None,
) -> _FileMeasurement:
    measurement = _hash_file(path, label=label)
    filename_matches = (
        path.name.casefold() in allowed_filenames
        if allowed_filenames is not None
        else path.name == expected["filename"]
    )
    if (
        not filename_matches
        or measurement.digest != expected["sha256"]
        or measurement.size != expected["size"]
    ):
        raise InstalledComponentManifestError(f"{label} does not match the manifest")
    return measurement


def _validate_component_paths_for_verify(
    values: Mapping[str, Path],
    *,
    root: Path,
    reserved_identities: set[tuple[int, int]] | None = None,
) -> dict[str, Path]:
    if set(values) != COMPONENT_ROLES:
        raise InstalledComponentManifestError(
            "verification requires exactly four executable inputs"
        )
    resolved: dict[str, Path] = {}
    identities = set(reserved_identities or ())
    for role, _expected_name in COMPONENTS:
        path = _regular_file(values[role], root=root, label=f"{role} executable")
        if path.name.casefold() not in SOURCE_BASENAMES[role]:
            raise InstalledComponentManifestError(
                f"{role} executable has an unexpected source filename"
            )
        identity = (path.stat().st_dev, path.stat().st_ino)
        if identity in identities:
            raise InstalledComponentManifestError(
                "verification contains duplicate executable inputs"
            )
        identities.add(identity)
        resolved[role] = path
    return resolved


def _verify_payload(
    payload: Mapping[str, Any],
    *,
    package_measurement: _FileMeasurement | None,
    package_name: str | None,
    package_path: Path | None,
    expected_header: tuple[str, str, str, str] | None,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
) -> None:
    _validate_shape(payload)
    _validate_expected_header(payload, expected_header)
    package_value = cast(dict[str, Any], payload["package"])
    package_descriptor = _descriptor(
        {key: package_value[key] for key in ("filename", "sha256", "size")},
        label="archive package",
    )
    direct_descriptor = _descriptor(package_value["direct_package"], label="direct package")
    direct_path = _regular_file(direct_package_path, root=source_root, label="direct package")
    direct_measurement = _check_measurement(direct_path, direct_descriptor, label="direct package")
    if (
        direct_measurement.digest != package_descriptor["sha256"]
        or direct_measurement.size != package_descriptor["size"]
    ):
        raise InstalledComponentManifestError("direct package does not match archive package")
    reserved_identities = {
        (
            int(direct_path.stat(follow_symlinks=False).st_dev),
            int(direct_path.stat(follow_symlinks=False).st_ino),
        )
    }
    if package_path is not None:
        package_identity = package_path.stat(follow_symlinks=False)
        reserved_identities.add((int(package_identity.st_dev), int(package_identity.st_ino)))
    manifest_components = cast(list[dict[str, Any]], payload["components"])
    main_value = manifest_components[0]
    if (
        main_value["sha256"] != package_descriptor["sha256"]
        or main_value["size"] != package_descriptor["size"]
    ):
        raise InstalledComponentManifestError(
            "main executable does not match archive package digest or size"
        )
    components = _validate_component_paths_for_verify(
        component_paths,
        root=source_root,
        reserved_identities=reserved_identities,
    )
    for item, (role, _expected_name) in zip(manifest_components, COMPONENTS, strict=True):
        path = components[role]
        _check_measurement(
            path,
            item,
            label=f"{role} executable",
            allowed_filenames=SOURCE_BASENAMES[role],
        )
        expected_status = cast(dict[str, str], item["authenticode"])["status"]
        if authenticode_status(path) != expected_status:
            raise InstalledComponentManifestError(f"{role} Authenticode status changed")
    if package_measurement is not None and (
        package_name != package_descriptor["filename"]
        or package_measurement.digest != package_descriptor["sha256"]
        or package_measurement.size != package_descriptor["size"]
    ):
        raise InstalledComponentManifestError("archive package does not match the manifest")


def _verify_checksum_bytes(raw_manifest: bytes, raw_checksum: bytes, *, name: str) -> None:
    digest = hashlib.sha256(raw_manifest).hexdigest()
    try:
        expected = f"{digest}  {name}\n".encode("ascii")
    except UnicodeEncodeError as exc:
        raise InstalledComponentManifestError("manifest filename is not ASCII") from exc
    if raw_checksum != expected:
        raise InstalledComponentManifestError(
            "installed-component checksum does not match the manifest"
        )


def verify_manifest(
    *,
    manifest_path: Path,
    package_path: Path,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    version: str | None = None,
    source_commit: str | None = None,
    platform: str | None = None,
    architecture: str | None = None,
) -> dict[str, Any]:
    manifest = _regular_file(manifest_path, root=source_root, label="installed-component manifest")
    if manifest.name != MANIFEST_FILE_NAME:
        raise InstalledComponentManifestError("manifest filename is not canonical")
    checksum = manifest.with_name(CHECKSUM_FILE_NAME)
    checksum = _regular_file(checksum, root=source_root, label="installed-component checksum")
    try:
        raw_manifest = manifest.read_bytes()
        raw_checksum = checksum.read_bytes()
    except OSError as exc:
        raise InstalledComponentManifestError(
            "could not read installed-component metadata"
        ) from exc
    _verify_checksum_bytes(raw_manifest, raw_checksum, name=MANIFEST_FILE_NAME)
    payload = _load_manifest_bytes(raw_manifest)
    _validate_shape(payload)
    expected_header = _expected_header(
        version=version,
        source_commit=source_commit,
        platform=platform,
        architecture=architecture,
    )
    package = _regular_file(package_path, root=source_root, label="archive package")
    package_measurement = _check_measurement(
        package,
        cast(dict[str, Any], payload.get("package", {})),
        label="archive package",
    )
    _verify_payload(
        payload,
        package_measurement=package_measurement,
        package_name=package.name,
        package_path=package,
        expected_header=expected_header,
        direct_package_path=direct_package_path,
        component_paths=component_paths,
        source_root=source_root,
    )
    return payload


def _zip_member_path(name: str) -> PurePosixPath:
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or re.match(r"^[A-Za-z]:", name) is not None
    ):
        raise InstalledComponentManifestError("release ZIP contains an unsafe path")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InstalledComponentManifestError("release ZIP contains an escaping path")
    return path


def verify_archive(
    *,
    archive_path: Path,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    version: str | None = None,
    source_commit: str | None = None,
    platform: str | None = None,
    architecture: str | None = None,
) -> dict[str, Any]:
    archive = _regular_file(archive_path, root=source_root, label="release archive")
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            infos = bundle.infolist()
            if len(infos) != 3:
                raise InstalledComponentManifestError(
                    "Windows installed-component archive must contain exactly three files"
                )
            names: set[str] = set()
            for info in infos:
                path = _zip_member_path(info.filename)
                folded = path.as_posix().casefold()
                if folded in names or info.is_dir():
                    raise InstalledComponentManifestError(
                        "release ZIP contains duplicate or directory entries"
                    )
                names.add(folded)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise InstalledComponentManifestError("release ZIP contains a symlink entry")
            by_basename: dict[str, zipfile.ZipInfo] = {}
            for info in infos:
                basename = PurePosixPath(info.filename).name.casefold()
                if basename in by_basename:
                    raise InstalledComponentManifestError(
                        "release ZIP contains duplicate component basenames"
                    )
                by_basename[basename] = info
            manifest_info = by_basename.get(MANIFEST_FILE_NAME.casefold())
            checksum_info = by_basename.get(CHECKSUM_FILE_NAME.casefold())
            package_info = by_basename.get("AllTheContextSetup.exe".casefold())
            if manifest_info is None or checksum_info is None or package_info is None:
                raise InstalledComponentManifestError(
                    "release ZIP lacks installed-component metadata"
                )
            with bundle.open(manifest_info, "r") as stream:
                raw_manifest = stream.read()
            with bundle.open(checksum_info, "r") as stream:
                raw_checksum = stream.read()
            _verify_checksum_bytes(raw_manifest, raw_checksum, name=MANIFEST_FILE_NAME)
            payload = _load_manifest_bytes(raw_manifest)
            _validate_shape(payload)
            expected_header = _expected_header(
                version=version,
                source_commit=source_commit,
                platform=platform,
                architecture=architecture,
            )
            package_digest = hashlib.sha256()
            package_size = 0
            with bundle.open(package_info, "r") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    package_size += len(chunk)
                    package_digest.update(chunk)
            package_measurement = _FileMeasurement(
                package_digest.hexdigest(),
                package_size,
                _FileSnapshot(0, 0, 0, 0, package_size, 0, 0),
            )
            package_value = cast(dict[str, Any], payload.get("package", {}))
            package_descriptor = _descriptor(
                {key: package_value.get(key) for key in ("filename", "sha256", "size")},
                label="archive package",
            )
            if (
                package_info.filename.rsplit("/", 1)[-1] != package_descriptor["filename"]
                or package_digest.hexdigest() != package_descriptor["sha256"]
                or package_size != package_descriptor["size"]
            ):
                raise InstalledComponentManifestError(
                    "release archive package does not match the manifest"
                )
    except zipfile.BadZipFile as exc:
        raise InstalledComponentManifestError("release archive is not a valid ZIP") from exc
    _verify_payload(
        payload,
        package_measurement=package_measurement,
        package_name=package_info.filename.rsplit("/", 1)[-1],
        package_path=None,
        expected_header=expected_header,
        direct_package_path=direct_package_path,
        component_paths=component_paths,
        source_root=source_root,
    )
    return payload


def _component_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--platform", default=WINDOWS_PLATFORM)
    parser.add_argument("--architecture", default=WINDOWS_ARCHITECTURE)
    for role, _filename in COMPONENTS:
        parser.add_argument(f"--{role}", type=Path, required=True)


def _component_values(arguments: argparse.Namespace) -> dict[str, Path]:
    return {role: cast(Path, getattr(arguments, role)) for role, _filename in COMPONENTS}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create_parser = commands.add_parser("create")
    _component_arguments(create_parser)
    create_parser.add_argument("--output-dir", type=Path, required=True)
    create_parser.add_argument("--package", type=Path, required=True)
    create_parser.add_argument("--direct-package", type=Path, required=True)
    verify_parser = commands.add_parser("verify")
    _component_arguments(verify_parser)
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--package", type=Path, required=True)
    verify_parser.add_argument("--direct-package", type=Path, required=True)
    archive_parser = commands.add_parser("verify-archive")
    _component_arguments(archive_parser)
    archive_parser.add_argument("--archive", type=Path, required=True)
    archive_parser.add_argument("--direct-package", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        components = _component_values(arguments)
        if arguments.command == "create":
            manifest, checksum = create_manifest(
                output_dir=arguments.output_dir,
                package_path=arguments.package,
                direct_package_path=arguments.direct_package,
                component_paths=components,
                source_root=arguments.source_root,
                version=arguments.version,
                source_commit=arguments.source_commit,
                platform=arguments.platform,
                architecture=arguments.architecture,
            )
            print(manifest)
            print(checksum)
        elif arguments.command == "verify":
            verify_manifest(
                manifest_path=arguments.manifest,
                package_path=arguments.package,
                direct_package_path=arguments.direct_package,
                component_paths=components,
                source_root=arguments.source_root,
                version=arguments.version,
                source_commit=arguments.source_commit,
                platform=arguments.platform,
                architecture=arguments.architecture,
            )
            print(arguments.manifest)
        else:
            verify_archive(
                archive_path=arguments.archive,
                direct_package_path=arguments.direct_package,
                component_paths=components,
                source_root=arguments.source_root,
                version=arguments.version,
                source_commit=arguments.source_commit,
                platform=arguments.platform,
                architecture=arguments.architecture,
            )
            print(arguments.archive)
    except InstalledComponentManifestError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
