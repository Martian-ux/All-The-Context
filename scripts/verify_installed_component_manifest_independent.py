"""Independently verify the future Windows installed-component archive."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, cast

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_TYPE = "installed-component"
MANIFEST_FILE_NAME = "installed-component-manifest-v1.json"
CHECKSUM_FILE_NAME = f"{MANIFEST_FILE_NAME}.sha256"
WINDOWS_PLATFORM = "windows"
WINDOWS_ARCHITECTURE = "x86_64"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-beta\.[1-9][0-9]*)?"
)
SAFE_EXECUTABLE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.exe")
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
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_CHECKSUM_BYTES = 256
READ_CHUNK_SIZE = 1024 * 1024


class IndependentManifestVerificationError(ValueError):
    """Raised when an installed-component archive cannot be trusted."""


@dataclass(frozen=True)
class _FileSnapshot:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int
    attributes: int

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
            attributes=int(getattr(value, "st_file_attributes", 0)),
        )


@dataclass(frozen=True)
class _StableFile:
    path: Path
    raw: bytes
    digest: str
    size: int
    identity: tuple[int, int]


def _failure(message: str) -> NoReturn:
    raise IndependentManifestVerificationError(message)


def _absolute(value: Path) -> Path:
    return Path(os.path.abspath(str(value.expanduser())))


def _snapshot(value: os.stat_result) -> _FileSnapshot:
    return _FileSnapshot.from_stat(value)


def _is_link_or_reparse(value: Path) -> bool:
    try:
        information = value.stat(follow_symlinks=False)
    except (OSError, RuntimeError) as exc:
        raise IndependentManifestVerificationError(
            f"cannot inspect verification input: {value}"
        ) from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return value.is_symlink() or bool(getattr(information, "st_file_attributes", 0) & reparse_flag)


def _validate_root(root: Path) -> tuple[Path, Path]:
    lexical = _absolute(root)
    if _is_link_or_reparse(lexical):
        _failure("source root cannot be a symlink or reparse point")
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IndependentManifestVerificationError(
            f"source root is unavailable: {lexical}"
        ) from exc
    if not resolved.is_dir():
        _failure("source root must be a directory")
    return lexical, resolved


def _reject_linked_path(lexical: Path, root: Path) -> None:
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise IndependentManifestVerificationError(
            f"verification input escapes the source root: {lexical.name}"
        ) from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.exists() and _is_link_or_reparse(current):
            _failure(f"verification input uses a symlink or reparse point: {current.name}")


def _regular_file(value: Path, *, root: Path, label: str) -> Path:
    lexical_root, resolved_root = _validate_root(root)
    lexical = _absolute(value)
    _reject_linked_path(lexical, lexical_root)
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IndependentManifestVerificationError(f"{label} is missing: {lexical}") from exc
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise IndependentManifestVerificationError(f"{label} escapes the source root") from exc
    if _is_link_or_reparse(lexical) or _is_link_or_reparse(resolved):
        _failure(f"{label} is a symlink or reparse point")
    try:
        information = resolved.stat(follow_symlinks=False)
    except (OSError, RuntimeError) as exc:
        raise IndependentManifestVerificationError(f"{label} cannot be inspected") from exc
    if not stat.S_ISREG(information.st_mode):
        _failure(f"{label} must be a regular file")
    return resolved


def _read_once(path: Path, *, label: str) -> bytes:
    try:
        before_path = _snapshot(path.stat(follow_symlinks=False))
        if path.is_symlink() or before_path.attributes & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            _failure(f"{label} is a symlink or reparse point")
        if not stat.S_ISREG(before_path.mode):
            _failure(f"{label} must be a regular file")
        with path.open("rb") as stream:
            before_handle = _snapshot(os.fstat(stream.fileno()))
            if (
                (before_handle.device, before_handle.inode)
                != (before_path.device, before_path.inode)
                or before_handle.size != before_path.size
            ):
                _failure(f"{label} changed before it was read")
            if not stat.S_ISREG(before_handle.mode):
                _failure(f"{label} must be a regular file")
            chunks: list[bytes] = []
            size = 0
            for chunk in iter(lambda: stream.read(READ_CHUNK_SIZE), b""):
                if not isinstance(chunk, bytes):
                    _failure(f"{label} returned a non-binary read")
                chunks.append(chunk)
                size += len(chunk)
            after_handle = _snapshot(os.fstat(stream.fileno()))
        after_path = _snapshot(path.stat(follow_symlinks=False))
    except IndependentManifestVerificationError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise IndependentManifestVerificationError(f"could not read {label}") from exc
    if before_handle != after_handle or before_path != after_path:
        _failure(f"{label} changed while it was read")
    if path.is_symlink() or after_path.attributes & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    ):
        _failure(f"{label} is a symlink or reparse point")
    if not stat.S_ISREG(after_path.mode):
        _failure(f"{label} must be a regular file")
    if size != before_path.size:
        _failure(f"{label} changed while it was read")
    return b"".join(chunks)


def _stable_read(path: Path, *, label: str) -> bytes:
    first = _read_once(path, label=label)
    second = _read_once(path, label=label)
    if first != second:
        _failure(f"{label} changed between stable reads")
    return first


def _stable_file(path: Path, *, label: str) -> _StableFile:
    raw = _stable_read(path, label=label)
    try:
        information = path.stat(follow_symlinks=False)
    except (OSError, RuntimeError) as exc:
        raise IndependentManifestVerificationError(f"{label} changed after it was read") from exc
    return _StableFile(
        path=path,
        raw=raw,
        digest=hashlib.sha256(raw).hexdigest(),
        size=len(raw),
        identity=(int(information.st_dev), int(information.st_ino)),
    )


def canonical_json(value: Mapping[str, Any]) -> bytes:
    """Serialize the manifest using the archive's exact canonical JSON form."""

    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise IndependentManifestVerificationError("manifest cannot be canonicalized") from exc
    return f"{rendered}\n".encode()


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            _failure("manifest contains duplicate JSON fields")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str) -> NoReturn:
    _failure(f"manifest contains non-finite JSON value: {value}")


def _load_manifest(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        _failure("installed-component manifest is too large")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except IndependentManifestVerificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise IndependentManifestVerificationError(
            "installed-component manifest is not UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        _failure("installed-component manifest must be a JSON object")
    payload = cast(dict[str, Any], value)
    try:
        canonical = canonical_json(payload)
    except IndependentManifestVerificationError:
        raise
    if canonical != raw:
        _failure("installed-component manifest is not canonical JSON")
    return payload


def _safe_executable_name(value: object, *, label: str) -> str:
    if not isinstance(value, str) or SAFE_EXECUTABLE_NAME.fullmatch(value) is None:
        _failure(f"{label} has an unsafe executable filename")
    return value


def _descriptor(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"filename", "sha256", "size"}:
        _failure(f"{label} descriptor is malformed")
    filename = _safe_executable_name(value["filename"], label=label)
    digest = value["sha256"]
    size = value["size"]
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        _failure(f"{label} digest is malformed")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        _failure(f"{label} size is malformed")
    return {"filename": filename, "sha256": digest, "size": size}


def _validate_header(
    *, version: object, source_commit: object, platform: object, architecture: object
) -> None:
    if not isinstance(version, str) or VERSION_PATTERN.fullmatch(version) is None:
        _failure("invalid product version")
    if not isinstance(source_commit, str) or COMMIT_PATTERN.fullmatch(source_commit) is None:
        _failure("source commit must be a full lowercase SHA-1")
    if platform != WINDOWS_PLATFORM or architecture != WINDOWS_ARCHITECTURE:
        _failure("installed-component manifests currently support Windows x86_64 only")


def _validate_manifest_shape(value: Mapping[str, Any]) -> None:
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
    if set(value) != required:
        _failure("installed-component manifest fields or schema are invalid")
    if (
        not isinstance(value["schema_version"], int)
        or isinstance(value["schema_version"], bool)
        or value["schema_version"] != MANIFEST_SCHEMA_VERSION
    ):
        _failure("installed-component manifest fields or schema are invalid")
    _validate_header(
        version=value["version"],
        source_commit=value["source_commit"],
        platform=value["platform"],
        architecture=value["architecture"],
    )
    if value["manifest_type"] != MANIFEST_TYPE:
        _failure("installed-component manifest type is invalid")

    package_value = value["package"]
    if not isinstance(package_value, dict) or set(package_value) != {
        "direct_package",
        "filename",
        "sha256",
        "size",
    }:
        _failure("installed-component package descriptor is malformed")
    package = _descriptor(
        {key: package_value[key] for key in ("filename", "sha256", "size")},
        label="archive package",
    )
    if package["filename"] != "AllTheContextSetup.exe":
        _failure("archive package filename is invalid")
    direct = _descriptor(package_value["direct_package"], label="direct package")
    if direct["filename"].casefold() == package["filename"].casefold():
        _failure("archive and direct package names must differ")

    count = value["component_count"]
    if not isinstance(count, int) or isinstance(count, bool) or count != len(COMPONENTS):
        _failure("installed-component count is invalid")
    components = value["components"]
    if not isinstance(components, list) or len(components) != len(COMPONENTS):
        _failure("installed-component list is invalid")
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
            _failure("installed-component entry is malformed")
        role = item["role"]
        filename = item["filename"]
        if role != expected_role or filename != expected_name or role in seen_roles:
            _failure("installed-component ordering or role is invalid")
        seen_roles.add(cast(str, role))
        folded_name = cast(str, filename).casefold()
        if folded_name in seen_names:
            _failure("installed-component filenames are duplicated")
        seen_names.add(folded_name)
        _descriptor(
            {key: item[key] for key in ("filename", "sha256", "size")},
            label=f"{expected_role} executable",
        )
        auth = item["authenticode"]
        if (
            not isinstance(auth, dict)
            or set(auth) != {"status"}
            or auth["status"] not in AUTHENTICODE_STATUSES
        ):
            _failure("installed-component Authenticode status is invalid")


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
        _failure("verification requires all four expected manifest header fields")
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
        _failure("installed-component manifest header does not match verification inputs")


def _authenticode_status(raw: bytes, *, name: str) -> str:
    if len(raw) < 64 or raw[:2] != b"MZ":
        _failure(f"{name} is not a valid PE executable")
    pe_offset = int.from_bytes(raw[60:64], "little")
    if pe_offset < 64 or pe_offset > len(raw) - 24:
        _failure(f"{name} has an invalid PE header")
    pe_header = raw[pe_offset : pe_offset + 24]
    if len(pe_header) != 24 or pe_header[:4] != b"PE\0\0":
        _failure(f"{name} is missing its PE signature")
    optional_size = int.from_bytes(pe_header[20:22], "little")
    optional_start = pe_offset + 24
    optional_end = optional_start + optional_size
    if optional_size < 2 or optional_size > 64 * 1024 or optional_end > len(raw):
        _failure(f"{name} has an invalid PE optional header")
    optional = raw[optional_start:optional_end]
    magic = int.from_bytes(optional[:2], "little")
    if magic == 0x10B:
        directory_count_offset, directory_offset = 92, 96
    elif magic == 0x20B:
        directory_count_offset, directory_offset = 108, 112
    else:
        _failure(f"{name} has an unsupported PE format")
    if len(optional) < directory_count_offset + 4:
        _failure(f"{name} lacks PE data-directory metadata")
    directory_count = int.from_bytes(
        optional[directory_count_offset : directory_count_offset + 4], "little"
    )
    if directory_count <= 4:
        return "not-present"
    certificate_entry = directory_offset + (4 * 8)
    if len(optional) < certificate_entry + 8:
        _failure(f"{name} has a truncated certificate table")
    location = int.from_bytes(optional[certificate_entry : certificate_entry + 4], "little")
    size = int.from_bytes(optional[certificate_entry + 4 : certificate_entry + 8], "little")
    if location == 0 and size == 0:
        return "not-present"
    if location <= 0 or size <= 0 or location + size > len(raw):
        _failure(f"{name} has an invalid certificate table")
    return "present-unverified"


def _validate_source_paths(
    *,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    archive_path: Path,
) -> tuple[_StableFile, dict[str, Path]]:
    if set(component_paths) != COMPONENT_ROLES:
        _failure("verification requires exactly four executable inputs")
    archive = _regular_file(archive_path, root=source_root, label="release archive")
    direct = _regular_file(direct_package_path, root=source_root, label="direct package")
    components: dict[str, Path] = {}
    identities: dict[tuple[int, int], str] = {}
    for label, path in (("release archive", archive), ("direct package", direct)):
        identity = _file_identity(path, label=label)
        if identity in identities:
            _failure(f"verification contains duplicate inputs: {identities[identity]} and {label}")
        identities[identity] = label
    for role, _expected_name in COMPONENTS:
        path = _regular_file(component_paths[role], root=source_root, label=f"{role} executable")
        if path.name.casefold() not in SOURCE_BASENAMES[role]:
            _failure(f"{role} executable has an unexpected source filename")
        identity = _file_identity(path, label=f"{role} executable")
        if identity in identities:
            _failure(
                f"verification contains duplicate inputs: {identities[identity]} and {role}"
            )
        identities[identity] = role
        components[role] = path
    return _stable_file(direct, label="direct package"), components


def _file_identity(path: Path, *, label: str) -> tuple[int, int]:
    try:
        information = path.stat(follow_symlinks=False)
    except (OSError, RuntimeError) as exc:
        raise IndependentManifestVerificationError(f"{label} changed before verification") from exc
    return int(information.st_dev), int(information.st_ino)


def _validate_archive_member_name(name: str) -> None:
    if not name or "\\" in name or "\x00" in name:
        _failure("release ZIP contains an unsafe path")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        _failure("release ZIP contains an escaping path")
    raw_parts = name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        _failure("release ZIP contains an escaping path")
    path = PurePosixPath(name)
    if path.is_absolute():
        _failure("release ZIP contains an escaping path")


def _read_zip_member(bundle: zipfile.ZipFile, info: zipfile.ZipInfo, *, label: str) -> bytes:
    try:
        with bundle.open(info, "r") as stream:
            chunks: list[bytes] = []
            size = 0
            for chunk in iter(lambda: stream.read(READ_CHUNK_SIZE), b""):
                chunks.append(chunk)
                size += len(chunk)
    except Exception as exc:
        raise IndependentManifestVerificationError(f"could not read ZIP member: {label}") from exc
    if size != info.file_size:
        _failure(f"ZIP member size changed while it was read: {label}")
    return b"".join(chunks)


def _hash_zip_member(
    bundle: zipfile.ZipFile, info: zipfile.ZipInfo, *, label: str
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with bundle.open(info, "r") as stream:
            for chunk in iter(lambda: stream.read(READ_CHUNK_SIZE), b""):
                digest.update(chunk)
                size += len(chunk)
    except Exception as exc:
        raise IndependentManifestVerificationError(f"could not read ZIP member: {label}") from exc
    if size != info.file_size:
        _failure(f"ZIP member size changed while it was read: {label}")
    return digest.hexdigest(), size


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
    """Verify a candidate archive and its four installed executable inputs."""

    direct, components = _validate_source_paths(
        direct_package_path=direct_package_path,
        component_paths=component_paths,
        source_root=source_root,
        archive_path=archive_path,
    )
    archive = _regular_file(archive_path, root=source_root, label="release archive")
    archive_raw = _stable_read(archive, label="release archive")
    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive_raw), "r")
    except Exception as exc:
        raise IndependentManifestVerificationError("release archive is not a valid ZIP") from exc
    with bundle:
        try:
            infos = bundle.infolist()
        except Exception as exc:
            raise IndependentManifestVerificationError(
                "release archive is not a valid ZIP"
            ) from exc
        names: set[str] = set()
        by_name: dict[str, zipfile.ZipInfo] = {}
        for info in infos:
            _validate_archive_member_name(info.filename)
            folded = info.filename.casefold()
            if folded in names:
                _failure("release ZIP contains duplicate entries")
            names.add(folded)
            if info.is_dir():
                _failure("release ZIP contains a directory entry")
            if info.flag_bits & 0x1:
                _failure("release ZIP contains an encrypted entry")
            mode = (info.external_attr >> 16) & 0o170000
            if mode == stat.S_IFLNK:
                _failure("release ZIP contains a symlink entry")
            if mode not in {0, stat.S_IFREG}:
                _failure("release ZIP contains a special-file entry")
            by_name[info.filename] = info
        if len(infos) != 3:
            _failure("Windows installed-component archive must contain exactly three files")
        expected_names = {
            "AllTheContextSetup.exe",
            MANIFEST_FILE_NAME,
            CHECKSUM_FILE_NAME,
        }
        if set(by_name) != expected_names:
            _failure("release ZIP contains an unexpected member set")
        manifest_info = by_name[MANIFEST_FILE_NAME]
        checksum_info = by_name[CHECKSUM_FILE_NAME]
        package_info = by_name["AllTheContextSetup.exe"]
        if manifest_info.file_size > MAX_MANIFEST_BYTES:
            _failure("installed-component manifest is too large")
        if checksum_info.file_size > MAX_CHECKSUM_BYTES:
            _failure("installed-component checksum is too large")
        raw_manifest = _read_zip_member(bundle, manifest_info, label=MANIFEST_FILE_NAME)
        raw_checksum = _read_zip_member(bundle, checksum_info, label=CHECKSUM_FILE_NAME)
        package_digest, package_size = _hash_zip_member(
            bundle, package_info, label="AllTheContextSetup.exe"
        )

    manifest_digest = hashlib.sha256(raw_manifest).hexdigest()
    expected_checksum = f"{manifest_digest}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    if raw_checksum != expected_checksum:
        _failure("installed-component checksum does not match the manifest")
    payload = _load_manifest(raw_manifest)
    _validate_manifest_shape(payload)
    _validate_expected_header(
        payload,
        _expected_header(
            version=version,
            source_commit=source_commit,
            platform=platform,
            architecture=architecture,
        ),
    )

    package_value = cast(dict[str, Any], payload["package"])
    package_descriptor = _descriptor(
        {key: package_value[key] for key in ("filename", "sha256", "size")},
        label="archive package",
    )
    direct_descriptor = _descriptor(package_value["direct_package"], label="direct package")
    if package_info.filename != package_descriptor["filename"]:
        _failure("release archive package filename does not match the manifest")
    if package_digest != package_descriptor["sha256"] or package_size != package_descriptor["size"]:
        _failure("release archive package does not match the manifest")
    if direct.path.name != direct_descriptor["filename"]:
        _failure("direct package filename does not match the manifest")
    if direct.digest != direct_descriptor["sha256"] or direct.size != direct_descriptor["size"]:
        _failure("direct package does not match the manifest")
    if (direct.digest, direct.size) != (package_descriptor["sha256"], package_descriptor["size"]):
        _failure("direct package does not match archive package")

    manifest_components = cast(list[dict[str, Any]], payload["components"])
    main_descriptor = manifest_components[0]
    if (
        main_descriptor["sha256"] != package_descriptor["sha256"]
        or main_descriptor["size"] != package_descriptor["size"]
    ):
        _failure("main executable does not match archive package digest or size")

    for item, (role, _expected_name) in zip(manifest_components, COMPONENTS, strict=True):
        source = _stable_file(components[role], label=f"{role} executable")
        descriptor = _descriptor(
            {key: item[key] for key in ("filename", "sha256", "size")},
            label=f"{role} executable",
        )
        if source.path.name.casefold() not in SOURCE_BASENAMES[role]:
            _failure(f"{role} executable has an unexpected source filename")
        if source.digest != descriptor["sha256"] or source.size != descriptor["size"]:
            _failure(f"{role} executable does not match the manifest")
        status = cast(dict[str, str], item["authenticode"])["status"]
        if _authenticode_status(source.raw, name=source.path.name) != status:
            _failure(f"{role} Authenticode status changed")
        if role == "main" and (source.digest, source.size) != (
            package_descriptor["sha256"],
            package_descriptor["size"],
        ):
            _failure("main executable does not match archive package digest or size")
    return payload


def _component_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--direct-package", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--platform", default=WINDOWS_PLATFORM)
    parser.add_argument("--architecture", default=WINDOWS_ARCHITECTURE)
    for role, _filename in COMPONENTS:
        parser.add_argument(f"--{role}", type=Path, required=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        nargs="?",
        choices=("verify-archive",),
        default="verify-archive",
        help="verification operation (optional compatibility spelling)",
    )
    _component_arguments(parser)
    arguments = parser.parse_args()
    try:
        verify_archive(
            archive_path=arguments.archive,
            direct_package_path=arguments.direct_package,
            component_paths={
                role: cast(Path, getattr(arguments, role)) for role, _filename in COMPONENTS
            },
            source_root=arguments.source_root,
            version=arguments.version,
            source_commit=arguments.source_commit,
            platform=arguments.platform,
            architecture=arguments.architecture,
        )
    except IndependentManifestVerificationError as exc:
        parser.error(str(exc))
    print(arguments.archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
