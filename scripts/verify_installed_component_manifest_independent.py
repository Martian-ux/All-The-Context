"""Independently verify the future Windows installed-component archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, NoReturn, cast

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
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 3
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
MAX_PE_OPTIONAL_HEADER_BYTES = 64 * 1024
MAX_CERTIFICATE_TABLE_BYTES = 16 * 1024 * 1024
MAX_ZIP_COMMENT_BYTES = 65_535
MAX_ZIP_TAIL_BYTES = 22 + MAX_ZIP_COMMENT_BYTES
MAX_ZIP_CENTRAL_DIRECTORY_BYTES = 256 * 1024
MAX_ZIP_MEMBER_NAME_BYTES = 65_535
READ_CHUNK_SIZE = 1024 * 1024
ARCHIVE_MEMBER_PREFIX = "installed-component-package/"
ARCHIVE_MEMBER_BASENAMES = frozenset(
    {"AllTheContextSetup.exe", MANIFEST_FILE_NAME, CHECKSUM_FILE_NAME}
)
ARCHIVE_MEMBER_NAMES = frozenset(
    ARCHIVE_MEMBER_PREFIX + name for name in ARCHIVE_MEMBER_BASENAMES
)
AMD64_MACHINE = 0x8664
PE32_PLUS_MAGIC = 0x20B
EXPECTED_ZIP_COMPRESSION = zipfile.ZIP_DEFLATED


class IndependentManifestVerificationError(ValueError):
    """Raised when an installed-component archive cannot be trusted."""


class _ContentFreeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> NoReturn:
        self.exit(2, f"{self.prog}: error: verification arguments are invalid\n")


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
    digest: str
    size: int
    identity: tuple[int, int]


@dataclass(frozen=True)
class _ValidatedPath:
    path: Path
    identity: tuple[int, int]


@dataclass(frozen=True)
class _StreamMeasurement:
    digest: str
    size: int
    identity: tuple[int, int]


@dataclass(frozen=True)
class _ExecutableMeasurement(_StreamMeasurement):
    authenticode: str


@dataclass(frozen=True)
class _PrimitiveZipRecord:
    name_bytes: bytes
    name: str
    flags: int
    compression: int
    crc: int
    compressed_size: int
    uncompressed_size: int
    local_header_offset: int
    version_needed: int
    version_made_by: int
    disk_number: int
    external_attributes: int


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
        raise IndependentManifestVerificationError("cannot inspect verification input") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return value.is_symlink() or bool(getattr(information, "st_file_attributes", 0) & reparse_flag)


def _validate_root(root: Path) -> tuple[Path, Path]:
    lexical = _absolute(root)
    if _is_link_or_reparse(lexical):
        _failure("source root cannot be a symlink or reparse point")
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IndependentManifestVerificationError("source root is unavailable") from exc
    if not resolved.is_dir():
        _failure("source root must be a directory")
    return lexical, resolved


def _reject_linked_path(lexical: Path, root: Path) -> None:
    try:
        relative = lexical.relative_to(root)
    except ValueError as exc:
        raise IndependentManifestVerificationError(
            "verification input escapes the source root"
        ) from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.exists() and _is_link_or_reparse(current):
            _failure("verification input uses a symlink or reparse point")


def _regular_file(
    value: Path, *, root: Path, label: str, maximum_size: int | None = None
) -> Path:
    lexical_root, resolved_root = _validate_root(root)
    lexical = _absolute(value)
    _reject_linked_path(lexical, lexical_root)
    try:
        resolved = lexical.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise IndependentManifestVerificationError(f"{label} is missing") from exc
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
    if information.st_nlink != 1:
        _failure(f"{label} must not be a hardlink")
    if maximum_size is not None and information.st_size > maximum_size:
        _failure(f"{label} exceeds the maximum allowed size")
    return resolved


def _identity(snapshot: _FileSnapshot) -> tuple[int, int]:
    return snapshot.device, snapshot.inode


def _assert_regular_snapshot(snapshot: _FileSnapshot, *, label: str) -> None:
    if not stat.S_ISREG(snapshot.mode):
        _failure(f"{label} must be a regular file")
    if snapshot.links != 1:
        _failure(f"{label} must not be a hardlink")


def _hash_stream(
    stream: BinaryIO, *, label: str, maximum_size: int
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            remaining = maximum_size + 1 - size
            if remaining <= 0:
                _failure(f"{label} exceeds the maximum allowed size")
            chunk = stream.read(min(READ_CHUNK_SIZE, remaining))
            if chunk == b"":
                break
            if not isinstance(chunk, bytes):
                _failure(f"{label} returned a non-binary read")
            size += len(chunk)
            if size > maximum_size:
                _failure(f"{label} exceeds the maximum allowed size")
            digest.update(chunk)
    except IndependentManifestVerificationError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise IndependentManifestVerificationError(f"could not read {label}") from exc
    return digest.hexdigest(), size


def _measure_once(
    path: Path,
    *,
    label: str,
    maximum_size: int,
    expected_identity: tuple[int, int] | None = None,
) -> _StreamMeasurement:
    try:
        before_path = _snapshot(path.stat(follow_symlinks=False))
        _assert_regular_snapshot(before_path, label=label)
        if expected_identity is not None and _identity(before_path) != expected_identity:
            _failure(f"{label} changed after path validation")
        if before_path.size > maximum_size:
            _failure(f"{label} exceeds the maximum allowed size")
        with path.open("rb") as stream:
            before_handle = _snapshot(os.fstat(stream.fileno()))
            _assert_regular_snapshot(before_handle, label=label)
            if (
                _identity(before_handle) != _identity(before_path)
                or before_handle.size != before_path.size
                or (
                    expected_identity is not None
                    and _identity(before_handle) != expected_identity
                )
            ):
                _failure(f"{label} changed before it was read")
            digest, size = _hash_stream(stream, label=label, maximum_size=maximum_size)
            after_handle = _snapshot(os.fstat(stream.fileno()))
        after_path = _snapshot(path.stat(follow_symlinks=False))
    except IndependentManifestVerificationError:
        raise
    except (OSError, RuntimeError, ValueError, TypeError) as exc:
        raise IndependentManifestVerificationError(f"could not read {label}") from exc
    _assert_regular_snapshot(after_handle, label=label)
    if before_handle != after_handle or before_path != after_path:
        _failure(f"{label} changed while it was read")
    final_identity = _identity(after_path)
    if final_identity != _identity(before_path) or (
        expected_identity is not None and final_identity != expected_identity
    ):
        _failure(f"{label} changed while it was read")
    if size != before_path.size:
        _failure(f"{label} changed while it was read")
    return _StreamMeasurement(digest, size, final_identity)


def _stable_measurement(
    path: Path,
    *,
    label: str,
    maximum_size: int,
    expected_identity: tuple[int, int] | None = None,
) -> _StreamMeasurement:
    first = _measure_once(
        path,
        label=label,
        maximum_size=maximum_size,
        expected_identity=expected_identity,
    )
    second = _measure_once(
        path,
        label=label,
        maximum_size=maximum_size,
        expected_identity=first.identity if expected_identity is None else expected_identity,
    )
    if (first.digest, first.size, first.identity) != (
        second.digest,
        second.size,
        second.identity,
    ):
        _failure(f"{label} changed between stable reads")
    return first


def _stable_file(
    path: Path, *, label: str, expected_identity: tuple[int, int] | None = None
) -> _StableFile:
    measurement = _stable_measurement(
        path,
        label=label,
        maximum_size=MAX_EXECUTABLE_BYTES,
        expected_identity=expected_identity,
    )
    return _StableFile(
        path=path,
        digest=measurement.digest,
        size=measurement.size,
        identity=measurement.identity,
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


def _reject_nonfinite_json(_value: str) -> NoReturn:
    _failure("manifest contains a non-finite JSON value")


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
    if size > MAX_EXECUTABLE_BYTES:
        _failure(f"{label} exceeds the maximum allowed size")
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
        if (
            not isinstance(role, str)
            or not isinstance(filename, str)
            or role != expected_role
            or filename != expected_name
            or role in seen_roles
        ):
            _failure("installed-component ordering or role is invalid")
        seen_roles.add(role)
        folded_name = filename.casefold()
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
            or not isinstance(auth["status"], str)
            or auth["status"] not in AUTHENTICODE_STATUSES
        ):
            _failure("installed-component Authenticode status is invalid")


def _expected_header(
    *,
    version: str,
    source_commit: str,
    platform: str,
    architecture: str,
) -> tuple[str, str, str, str]:
    values = (version, source_commit, platform, architecture)
    return values


def _validate_expected_header(
    payload: Mapping[str, Any], expected: tuple[str, str, str, str]
) -> None:
    actual = (
        cast(str, payload["version"]),
        cast(str, payload["source_commit"]),
        cast(str, payload["platform"]),
        cast(str, payload["architecture"]),
    )
    if actual != expected:
        _failure("installed-component manifest header does not match verification inputs")


def _read_range(
    stream: BinaryIO,
    *,
    offset: int,
    size: int,
    file_size: int,
    label: str,
    maximum_size: int = MAX_PE_OPTIONAL_HEADER_BYTES,
) -> bytes:
    if offset < 0 or size < 0 or size > maximum_size:
        _failure(f"{label} has an invalid bounded range")
    if offset > file_size or size > file_size - offset:
        _failure(f"{label} has a truncated bounded range")
    try:
        stream.seek(offset)
        raw = stream.read(size)
    except (OSError, ValueError, TypeError) as exc:
        raise IndependentManifestVerificationError(f"could not inspect {label}") from exc
    if not isinstance(raw, bytes) or len(raw) != size:
        _failure(f"{label} has a truncated bounded range")
    return raw


def _authenticode_status_stream(
    stream: BinaryIO, *, file_size: int, label: str
) -> str:
    dos_header = _read_range(stream, offset=0, size=64, file_size=file_size, label=label)
    if dos_header[:2] != b"MZ":
        _failure(f"{label} is not a valid PE executable")
    pe_offset = int.from_bytes(dos_header[60:64], "little")
    if pe_offset < 64:
        _failure(f"{label} has an invalid PE header offset")
    pe_header = _read_range(stream, offset=pe_offset, size=24, file_size=file_size, label=label)
    if pe_header[:4] != b"PE\0\0":
        _failure(f"{label} is missing its PE signature")
    if int.from_bytes(pe_header[4:6], "little") != AMD64_MACHINE:
        _failure(f"{label} is not an AMD64 PE executable")
    optional_size = int.from_bytes(pe_header[20:22], "little")
    if optional_size < 2 or optional_size > MAX_PE_OPTIONAL_HEADER_BYTES:
        _failure(f"{label} has an invalid PE optional header")
    optional_start = pe_offset + 24
    optional = _read_range(
        stream,
        offset=optional_start,
        size=optional_size,
        file_size=file_size,
        label=label,
    )
    magic = int.from_bytes(optional[:2], "little")
    if magic != PE32_PLUS_MAGIC:
        _failure(f"{label} is not a PE32+ executable")
    directory_count_offset, directory_offset = 108, 112
    if len(optional) < directory_count_offset + 4:
        _failure(f"{label} lacks PE data-directory metadata")
    directory_count = int.from_bytes(
        optional[directory_count_offset : directory_count_offset + 4], "little"
    )
    if directory_count <= 4:
        return "not-present"
    certificate_entry = directory_offset + (4 * 8)
    if len(optional) < certificate_entry + 8:
        _failure(f"{label} has a truncated certificate table")
    location = int.from_bytes(optional[certificate_entry : certificate_entry + 4], "little")
    certificate_size = int.from_bytes(
        optional[certificate_entry + 4 : certificate_entry + 8], "little"
    )
    if location == 0 and certificate_size == 0:
        return "not-present"
    if (
        location <= 0
        or certificate_size <= 0
        or certificate_size > MAX_CERTIFICATE_TABLE_BYTES
        or location > file_size
        or certificate_size > file_size - location
    ):
        _failure(f"{label} has an invalid certificate table")
    # The status is intentionally presence-only. Seek across the bounded table to
    # ensure the advertised range is addressable without retaining certificate bytes.
    try:
        stream.seek(location + certificate_size)
    except (OSError, ValueError, TypeError) as exc:
        raise IndependentManifestVerificationError(f"could not inspect {label}") from exc
    return "present-unverified"


def _validate_source_paths(
    *,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    archive_path: Path,
) -> tuple[_StableFile, dict[str, _ValidatedPath], tuple[int, int]]:
    if set(component_paths) != COMPONENT_ROLES:
        _failure("verification requires exactly four executable inputs")
    archive = _regular_file(
        archive_path,
        root=source_root,
        label="release archive",
        maximum_size=MAX_ARCHIVE_BYTES,
    )
    direct = _regular_file(
        direct_package_path,
        root=source_root,
        label="direct package",
        maximum_size=MAX_EXECUTABLE_BYTES,
    )
    components: dict[str, _ValidatedPath] = {}
    identities: dict[tuple[int, int], str] = {}
    direct_identity: tuple[int, int] | None = None
    archive_identity: tuple[int, int] | None = None
    for label, path in (("release archive", archive), ("direct package", direct)):
        identity = _file_identity(path, label=label)
        if identity in identities:
            _failure(f"verification contains duplicate inputs: {identities[identity]} and {label}")
        identities[identity] = label
        if label == "release archive":
            archive_identity = identity
        if label == "direct package":
            direct_identity = identity
    for role, _expected_name in COMPONENTS:
        path = _regular_file(
            component_paths[role],
            root=source_root,
            label=f"{role} executable",
            maximum_size=MAX_EXECUTABLE_BYTES,
        )
        if path.name.casefold() not in SOURCE_BASENAMES[role]:
            _failure(f"{role} executable has an unexpected source filename")
        identity = _file_identity(path, label=f"{role} executable")
        if identity in identities:
            _failure(
                f"verification contains duplicate inputs: {identities[identity]} and {role}"
        )
        identities[identity] = role
        components[role] = _ValidatedPath(path, identity)
    if direct_identity is None or archive_identity is None:
        _failure("verification input identity is unavailable")
    return (
        _stable_file(direct, label="direct package", expected_identity=direct_identity),
        components,
        archive_identity,
    )


def _file_identity(path: Path, *, label: str) -> tuple[int, int]:
    try:
        information = path.stat(follow_symlinks=False)
    except (OSError, RuntimeError) as exc:
        raise IndependentManifestVerificationError(f"{label} changed before verification") from exc
    if information.st_nlink != 1:
        _failure(f"{label} must not be a hardlink")
    return int(information.st_dev), int(information.st_ino)


def _validate_archive_member_name(name: str) -> None:
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name:
        _failure("release ZIP contains an unsafe path")
    if name.startswith("/") or ":" in name or not name.startswith(ARCHIVE_MEMBER_PREFIX):
        _failure("release ZIP contains an escaping path")
    raw_parts = name.split("/")
    if any(part in {"", ".", ".."} for part in raw_parts):
        _failure("release ZIP contains an escaping path")
    path = PurePosixPath(name)
    if path.is_absolute():
        _failure("release ZIP contains an escaping path")


def _validate_zip_info_metadata(infos: list[zipfile.ZipInfo]) -> None:
    total_size = 0
    for info in infos:
        if info.flag_bits != 0 or info.compress_type != EXPECTED_ZIP_COMPRESSION:
            _failure("release ZIP compression metadata is not canonical")
        if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
            _failure("release ZIP member exceeds the maximum allowed size")
        if info.compress_size < 0:
            _failure("release ZIP member has invalid compressed size")
        total_size += info.file_size
        if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
            _failure("release ZIP exceeds the maximum uncompressed size")
        if info.file_size > 0 and (
            info.compress_size == 0
            or info.file_size > info.compress_size * MAX_COMPRESSION_RATIO
        ):
            _failure("release ZIP member exceeds the maximum compression ratio")


def _decode_primitive_zip_name(raw_name: bytes) -> str:
    if b"\x00" in raw_name:
        _failure("release ZIP contains an unsafe path")
    try:
        name = raw_name.decode("ascii")
    except UnicodeDecodeError as exc:
        raise IndependentManifestVerificationError(
            "release ZIP member filename is not decodable"
        ) from exc
    _validate_archive_member_name(name)
    if raw_name != name.encode("ascii"):
        _failure("release ZIP member filename is not canonical")
    return name


def _inspect_zip_envelope(
    stream: BinaryIO, *, file_size: int
) -> tuple[_PrimitiveZipRecord, ...]:
    """Bound and inspect the ZIP envelope before invoking ZipFile inventory."""

    if file_size < 22:
        _failure("release archive is not a complete ZIP envelope")
    first_local = _read_range(
        stream,
        offset=0,
        size=4,
        file_size=file_size,
        label="release archive",
        maximum_size=4,
    )
    if first_local != b"PK\x03\x04":
        _failure("release ZIP must begin with a local file header")

    tail_size = min(file_size, MAX_ZIP_TAIL_BYTES)
    tail_start = file_size - tail_size
    tail = _read_range(
        stream,
        offset=tail_start,
        size=tail_size,
        file_size=file_size,
        label="release archive",
        maximum_size=MAX_ZIP_TAIL_BYTES,
    )
    eocd_positions: list[int] = []
    for position in range(0, len(tail) - 21):
        if tail[position : position + 4] != b"PK\x05\x06":
            continue
        comment_size = int.from_bytes(tail[position + 20 : position + 22], "little")
        if position + 22 + comment_size == len(tail):
            eocd_positions.append(position)
    if len(eocd_positions) != 1:
        _failure("release ZIP end record is not canonical")
    eocd_position = eocd_positions[0]
    eocd_offset = tail_start + eocd_position
    eocd = tail[eocd_position : eocd_position + 22]
    if int.from_bytes(eocd[20:22], "little") != 0:
        _failure("release ZIP comments are not supported")
    if eocd_offset >= 20:
        locator = _read_range(
            stream,
            offset=eocd_offset - 20,
            size=20,
            file_size=file_size,
            label="release archive",
            maximum_size=20,
        )
        if locator[:4] == b"PK\x06\x07":
            _failure("release ZIP64 envelopes are not supported")

    disk_number = int.from_bytes(eocd[4:6], "little")
    central_disk = int.from_bytes(eocd[6:8], "little")
    entries_on_disk = int.from_bytes(eocd[8:10], "little")
    entry_count = int.from_bytes(eocd[10:12], "little")
    central_size = int.from_bytes(eocd[12:16], "little")
    central_offset = int.from_bytes(eocd[16:20], "little")
    if (
        disk_number != 0
        or central_disk != 0
        or entries_on_disk != entry_count
        or entries_on_disk == 0xFFFF
        or entry_count == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    ):
        _failure("release ZIP multi-disk or ZIP64 envelopes are not supported")
    if entry_count != MAX_ARCHIVE_MEMBERS:
        _failure("Windows installed-component archive must contain exactly three files")
    if central_size > MAX_ZIP_CENTRAL_DIRECTORY_BYTES:
        _failure("release ZIP central directory exceeds the maximum allowed size")
    if central_offset <= 0 or central_offset + central_size != eocd_offset:
        _failure("release ZIP central directory is not canonical")

    records: list[_PrimitiveZipRecord] = []
    cursor = central_offset
    total_size = 0
    for _ in range(entry_count):
        header = _read_range(
            stream,
            offset=cursor,
            size=46,
            file_size=file_size,
            label="release archive",
            maximum_size=46,
        )
        if header[:4] != b"PK\x01\x02":
            _failure("release ZIP central directory is invalid")
        version_made_by = int.from_bytes(header[4:6], "little")
        version_needed = int.from_bytes(header[6:8], "little")
        flags = int.from_bytes(header[8:10], "little")
        compression = int.from_bytes(header[10:12], "little")
        crc = int.from_bytes(header[16:20], "little")
        compressed_size = int.from_bytes(header[20:24], "little")
        uncompressed_size = int.from_bytes(header[24:28], "little")
        name_size = int.from_bytes(header[28:30], "little")
        extra_size = int.from_bytes(header[30:32], "little")
        comment_size = int.from_bytes(header[32:34], "little")
        disk_start = int.from_bytes(header[34:36], "little")
        external_attributes = int.from_bytes(header[38:42], "little")
        local_header_offset = int.from_bytes(header[42:46], "little")
        if name_size > MAX_ZIP_MEMBER_NAME_BYTES:
            _failure("release ZIP member filename is too long")
        if (
            version_made_by != ((3 << 8) | 20)
            or version_needed != 20
            or flags != 0
            or compression != EXPECTED_ZIP_COMPRESSION
            or compressed_size == 0xFFFFFFFF
            or uncompressed_size == 0xFFFFFFFF
            or disk_start != 0
            or extra_size != 0
            or comment_size != 0
        ):
            _failure("release ZIP envelope metadata is not canonical")
        raw_name = _read_range(
            stream,
            offset=cursor + 46,
            size=name_size,
            file_size=file_size,
            label="release archive",
            maximum_size=MAX_ZIP_MEMBER_NAME_BYTES,
        )
        name = _decode_primitive_zip_name(raw_name)
        records.append(
            _PrimitiveZipRecord(
                name_bytes=raw_name,
                name=name,
                flags=flags,
                compression=compression,
                crc=crc,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                local_header_offset=local_header_offset,
                version_needed=version_needed,
                version_made_by=version_made_by,
                disk_number=disk_start,
                external_attributes=external_attributes,
            )
        )
        cursor += 46 + name_size
        if cursor > eocd_offset:
            _failure("release ZIP central directory is truncated")
        total_size += uncompressed_size
        if total_size > MAX_TOTAL_UNCOMPRESSED_BYTES:
            _failure("release ZIP exceeds the maximum uncompressed size")
        if uncompressed_size > MAX_MEMBER_BYTES:
            _failure("release ZIP member exceeds the maximum allowed size")
        if uncompressed_size > 0 and (
            compressed_size == 0
            or uncompressed_size > compressed_size * MAX_COMPRESSION_RATIO
        ):
            _failure("release ZIP member exceeds the maximum compression ratio")
    if cursor != eocd_offset:
        _failure("release ZIP central directory size is not canonical")
    if len({record.name for record in records}) != MAX_ARCHIVE_MEMBERS:
        _failure("release ZIP contains duplicate entries")
    if {record.name for record in records} != ARCHIVE_MEMBER_NAMES:
        _failure("release ZIP contains an unexpected member set")

    intervals: list[tuple[int, int]] = []
    for record in records:
        local_offset = record.local_header_offset
        if local_offset >= central_offset:
            _failure("release ZIP local header is outside the file data")
        local = _read_range(
            stream,
            offset=local_offset,
            size=30,
            file_size=file_size,
            label="release archive",
            maximum_size=30,
        )
        if local[:4] != b"PK\x03\x04":
            _failure("release ZIP local header is invalid")
        local_version = int.from_bytes(local[4:6], "little")
        local_flags = int.from_bytes(local[6:8], "little")
        local_compression = int.from_bytes(local[8:10], "little")
        local_crc = int.from_bytes(local[14:18], "little")
        local_compressed_size = int.from_bytes(local[18:22], "little")
        local_uncompressed_size = int.from_bytes(local[22:26], "little")
        local_name_size = int.from_bytes(local[26:28], "little")
        local_extra_size = int.from_bytes(local[28:30], "little")
        if (
            local_version != record.version_needed
            or local_flags != record.flags
            or local_compression != record.compression
            or local_crc != record.crc
            or local_compressed_size != record.compressed_size
            or local_uncompressed_size != record.uncompressed_size
            or local_name_size != len(record.name_bytes)
            or local_extra_size != 0
        ):
            _failure("release ZIP local and central headers differ")
        local_name = _read_range(
            stream,
            offset=local_offset + 30,
            size=local_name_size,
            file_size=file_size,
            label="release archive",
            maximum_size=MAX_ZIP_MEMBER_NAME_BYTES,
        )
        if local_name != record.name_bytes:
            _failure("release ZIP local and central filenames differ")
        data_start = local_offset + 30 + local_name_size
        data_end = data_start + record.compressed_size
        if data_end > central_offset:
            _failure("release ZIP member data overlaps the central directory")
        intervals.append((local_offset, data_end))
    intervals.sort()
    if not intervals or intervals[0][0] != 0:
        _failure("release ZIP local data does not begin at byte zero")
    for previous, current in pairwise(intervals):
        if previous[1] != current[0]:
            _failure("release ZIP local data is not contiguous")
    if intervals[-1][1] != central_offset:
        _failure("release ZIP local data is not canonical")
    return tuple(records)


def _read_zip_member(
    bundle: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    label: str,
    maximum_size: int,
) -> bytes:
    try:
        with bundle.open(info, "r") as stream:
            chunks: list[bytes] = []
            size = 0
            while True:
                remaining = maximum_size + 1 - size
                if remaining <= 0:
                    _failure(f"ZIP member exceeds the maximum allowed size: {label}")
                chunk = stream.read(min(READ_CHUNK_SIZE, remaining))
                if chunk == b"":
                    break
                if not isinstance(chunk, bytes):
                    _failure(f"could not read ZIP member: {label}")
                chunks.append(chunk)
                size += len(chunk)
                if size > maximum_size:
                    _failure(f"ZIP member exceeds the maximum allowed size: {label}")
    except Exception as exc:
        if isinstance(exc, IndependentManifestVerificationError):
            raise
        raise IndependentManifestVerificationError(f"could not read ZIP member: {label}") from exc
    if size != info.file_size:
        _failure(f"ZIP member size changed while it was read: {label}")
    return b"".join(chunks)


def _hash_zip_member(
    bundle: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    label: str,
    maximum_size: int,
) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with bundle.open(info, "r") as stream:
            while True:
                remaining = maximum_size + 1 - size
                if remaining <= 0:
                    _failure(f"ZIP member exceeds the maximum allowed size: {label}")
                chunk = stream.read(min(READ_CHUNK_SIZE, remaining))
                if chunk == b"":
                    break
                if not isinstance(chunk, bytes):
                    _failure(f"could not read ZIP member: {label}")
                digest.update(chunk)
                size += len(chunk)
                if size > maximum_size:
                    _failure(f"ZIP member exceeds the maximum allowed size: {label}")
    except Exception as exc:
        if isinstance(exc, IndependentManifestVerificationError):
            raise
        raise IndependentManifestVerificationError(f"could not read ZIP member: {label}") from exc
    if size != info.file_size:
        _failure(f"ZIP member size changed while it was read: {label}")
    return digest.hexdigest(), size


def _assert_open_file_matches(
    stream: BinaryIO,
    *,
    before_path: _FileSnapshot,
    expected_identity: tuple[int, int],
    label: str,
) -> _FileSnapshot:
    before_handle = _snapshot(os.fstat(stream.fileno()))
    _assert_regular_snapshot(before_handle, label=label)
    if (
        _identity(before_path) != expected_identity
        or _identity(before_handle) != expected_identity
        or before_handle.size != before_path.size
    ):
        _failure(f"{label} changed before it was read")
    return before_handle


def _read_archive_contents(
    archive: Path, *, expected_identity: tuple[int, int]
) -> tuple[bytes, bytes, str, int, str]:
    try:
        before_path = _snapshot(archive.stat(follow_symlinks=False))
        _assert_regular_snapshot(before_path, label="release archive")
        if before_path.size > MAX_ARCHIVE_BYTES:
            _failure("release archive exceeds the maximum allowed size")
        with archive.open("rb") as stream:
            before_handle = _assert_open_file_matches(
                stream,
                before_path=before_path,
                expected_identity=expected_identity,
                label="release archive",
            )
            first_digest, first_size = _hash_stream(
                stream, label="release archive", maximum_size=MAX_ARCHIVE_BYTES
            )
            stream.seek(0)
            try:
                primitive_records = _inspect_zip_envelope(
                    stream, file_size=before_path.size
                )
                primitive_by_name = {record.name: record for record in primitive_records}
                stream.seek(0)
                with zipfile.ZipFile(stream, "r") as bundle:
                    infos = bundle.infolist()
                    if len(infos) != MAX_ARCHIVE_MEMBERS:
                        _failure(
                            "Windows installed-component archive must contain exactly three files"
                        )
                    names: set[str] = set()
                    for info in infos:
                        if getattr(info, "orig_filename", info.filename) != info.filename:
                            _failure("release ZIP member filename was normalized")
                        _validate_archive_member_name(info.filename)
                        primitive = primitive_by_name.get(info.filename)
                        if primitive is None:
                            _failure("release ZIP member inventory differs from its envelope")
                        if (
                            info.flag_bits != primitive.flags
                            or info.compress_type != primitive.compression
                            or primitive.crc != info.CRC
                            or info.compress_size != primitive.compressed_size
                            or info.file_size != primitive.uncompressed_size
                            or info.header_offset != primitive.local_header_offset
                            or info.create_system != 3
                            or info.create_version != 20
                            or info.extract_version != primitive.version_needed
                            or info.extra != b""
                            or info.comment != b""
                        ):
                            _failure("release ZIP inventory differs from its envelope")
                        folded = info.filename.casefold()
                        if folded in names:
                            _failure("release ZIP contains duplicate entries")
                        names.add(folded)
                    by_name: dict[str, zipfile.ZipInfo] = {}
                    for info in infos:
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
                    if set(by_name) != ARCHIVE_MEMBER_NAMES:
                        _failure("release ZIP contains an unexpected member set")
                    _validate_zip_info_metadata(infos)
                    manifest_info = by_name[ARCHIVE_MEMBER_PREFIX + MANIFEST_FILE_NAME]
                    checksum_info = by_name[ARCHIVE_MEMBER_PREFIX + CHECKSUM_FILE_NAME]
                    package_info = by_name[ARCHIVE_MEMBER_PREFIX + "AllTheContextSetup.exe"]
                    if manifest_info.file_size > MAX_MANIFEST_BYTES:
                        _failure("installed-component manifest is too large")
                    if checksum_info.file_size > MAX_CHECKSUM_BYTES:
                        _failure("installed-component checksum is too large")
                    raw_manifest = _read_zip_member(
                        bundle,
                        manifest_info,
                        label=MANIFEST_FILE_NAME,
                        maximum_size=MAX_MANIFEST_BYTES,
                    )
                    raw_checksum = _read_zip_member(
                        bundle,
                        checksum_info,
                        label=CHECKSUM_FILE_NAME,
                        maximum_size=MAX_CHECKSUM_BYTES,
                    )
                    package_digest, package_size = _hash_zip_member(
                        bundle,
                        package_info,
                        label="AllTheContextSetup.exe",
                        maximum_size=MAX_MEMBER_BYTES,
                    )
            except IndependentManifestVerificationError:
                raise
            except (
                OSError,
                RuntimeError,
                ValueError,
                KeyError,
                IndexError,
                zipfile.BadZipFile,
            ) as exc:
                raise IndependentManifestVerificationError(
                    "release archive is not a valid ZIP"
                ) from exc
            stream.seek(0)
            second_digest, second_size = _hash_stream(
                stream, label="release archive", maximum_size=MAX_ARCHIVE_BYTES
            )
            after_handle = _snapshot(os.fstat(stream.fileno()))
        after_path = _snapshot(archive.stat(follow_symlinks=False))
    except IndependentManifestVerificationError:
        raise
    except (
        OSError,
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        zipfile.BadZipFile,
    ) as exc:
        raise IndependentManifestVerificationError("release archive is not a valid ZIP") from exc
    _assert_regular_snapshot(after_handle, label="release archive")
    if before_handle != after_handle or before_path != after_path:
        _failure("release archive changed while it was read")
    if _identity(after_path) != expected_identity:
        _failure("release archive changed while it was read")
    if (first_digest, first_size) != (second_digest, second_size):
        _failure("release archive changed between stable reads")
    return raw_manifest, raw_checksum, package_digest, package_size, package_info.filename


def _stable_executable(
    path: Path, *, label: str, expected_identity: tuple[int, int]
) -> _ExecutableMeasurement:
    def measure() -> _ExecutableMeasurement:
        try:
            before_path = _snapshot(path.stat(follow_symlinks=False))
            _assert_regular_snapshot(before_path, label=label)
            if before_path.size > MAX_EXECUTABLE_BYTES:
                _failure(f"{label} exceeds the maximum allowed size")
            if _identity(before_path) != expected_identity:
                _failure(f"{label} changed after path validation")
            with path.open("rb") as stream:
                before_handle = _assert_open_file_matches(
                    stream,
                    before_path=before_path,
                    expected_identity=expected_identity,
                    label=label,
                )
                status = _authenticode_status_stream(
                    stream, file_size=before_path.size, label=label
                )
                stream.seek(0)
                digest, size = _hash_stream(
                    stream, label=label, maximum_size=MAX_EXECUTABLE_BYTES
                )
                after_handle = _snapshot(os.fstat(stream.fileno()))
            after_path = _snapshot(path.stat(follow_symlinks=False))
        except IndependentManifestVerificationError:
            raise
        except (OSError, RuntimeError, ValueError, TypeError) as exc:
            raise IndependentManifestVerificationError(f"could not read {label}") from exc
        _assert_regular_snapshot(after_handle, label=label)
        if before_handle != after_handle or before_path != after_path:
            _failure(f"{label} changed while it was read")
        if _identity(after_path) != expected_identity or size != before_path.size:
            _failure(f"{label} changed while it was read")
        return _ExecutableMeasurement(digest, size, expected_identity, status)

    first = measure()
    second = measure()
    if first != second:
        _failure(f"{label} changed between stable reads")
    return first


def _verify_archive(
    *,
    archive_path: Path,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    version: str,
    source_commit: str,
    platform: str,
    architecture: str,
) -> dict[str, Any]:
    """Verify a candidate archive and its four installed executable inputs."""

    expected = _expected_header(
        version=version,
        source_commit=source_commit,
        platform=platform,
        architecture=architecture,
    )
    direct, components, archive_identity = _validate_source_paths(
        direct_package_path=direct_package_path,
        component_paths=component_paths,
        source_root=source_root,
        archive_path=archive_path,
    )
    archive = _regular_file(
        archive_path,
        root=source_root,
        label="release archive",
        maximum_size=MAX_ARCHIVE_BYTES,
    )
    if _file_identity(archive, label="release archive") != archive_identity:
        _failure("release archive changed after path validation")
    raw_manifest, raw_checksum, package_digest, package_size, package_name = (
        _read_archive_contents(archive, expected_identity=archive_identity)
    )

    manifest_digest = hashlib.sha256(raw_manifest).hexdigest()
    expected_checksum = f"{manifest_digest}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    if raw_checksum != expected_checksum:
        _failure("installed-component checksum does not match the manifest")
    payload = _load_manifest(raw_manifest)
    _validate_manifest_shape(payload)
    _validate_expected_header(payload, expected)

    package_value = cast(dict[str, Any], payload["package"])
    package_descriptor = _descriptor(
        {key: package_value[key] for key in ("filename", "sha256", "size")},
        label="archive package",
    )
    direct_descriptor = _descriptor(package_value["direct_package"], label="direct package")
    expected_archive_name = f"all-the-context-{version}-{platform}-{architecture}.zip"
    expected_direct_name = f"all-the-context-{version}-{platform}-{architecture}-unsigned.exe"
    if archive.name != expected_archive_name:
        _failure("release archive filename does not match verification inputs")
    if package_name != ARCHIVE_MEMBER_PREFIX + package_descriptor["filename"]:
        _failure("release archive package filename does not match the manifest")
    if package_digest != package_descriptor["sha256"] or package_size != package_descriptor["size"]:
        _failure("release archive package does not match the manifest")
    if direct_descriptor["filename"] != expected_direct_name:
        _failure("direct package filename does not match verification inputs")
    if (
        direct.path.name != expected_direct_name
        or direct.path.name != direct_descriptor["filename"]
    ):
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
        validated = components[role]
        descriptor = _descriptor(
            {key: item[key] for key in ("filename", "sha256", "size")},
            label=f"{role} executable",
        )
        source = _stable_executable(
            validated.path,
            label=f"{role} executable",
            expected_identity=validated.identity,
        )
        if source.identity != validated.identity:
            _failure(f"{role} executable changed after path validation")
        if validated.path.name.casefold() not in SOURCE_BASENAMES[role]:
            _failure(f"{role} executable has an unexpected source filename")
        if source.digest != descriptor["sha256"] or source.size != descriptor["size"]:
            _failure(f"{role} executable does not match the manifest")
        status = cast(dict[str, str], item["authenticode"])["status"]
        if source.authenticode != status:
            _failure(f"{role} Authenticode status changed")
        if role == "main" and (source.digest, source.size) != (
            package_descriptor["sha256"],
            package_descriptor["size"],
        ):
            _failure("main executable does not match archive package digest or size")
    return payload


def verify_archive(
    *,
    archive_path: Path,
    direct_package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    version: str,
    source_commit: str,
    platform: str,
    architecture: str,
) -> dict[str, Any]:
    """Verify a candidate archive with mandatory expected release identity."""

    try:
        return _verify_archive(
            archive_path=archive_path,
            direct_package_path=direct_package_path,
            component_paths=component_paths,
            source_root=source_root,
            version=version,
            source_commit=source_commit,
            platform=platform,
            architecture=architecture,
        )
    except IndependentManifestVerificationError:
        raise
    except Exception as exc:
        raise IndependentManifestVerificationError("verification failed closed") from exc


def _component_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--direct-package", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--architecture", required=True)
    for role, _filename in COMPONENTS:
        parser.add_argument(f"--{role}", type=Path, required=True)


def main() -> int:
    parser = _ContentFreeArgumentParser(
        prog="verify-installed-component-manifest",
        description=__doc__,
    )
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
    except Exception:
        parser.error("verification failed closed")
    print("verified installed-component archive: pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
