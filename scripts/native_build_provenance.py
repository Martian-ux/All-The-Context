"""Create and verify a deterministic, content-free native build receipt.

The receipt is deliberately independent of the application runtime.  It binds
the four Windows executables to a source commit, reviewed lock digests, and a
fixed two-build comparison contract.  It contains no absolute paths,
timestamps, host names, or signing claims, so the JSON and checksum can be
published as metadata without publishing the build workspace.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

SCHEMA_VERSION = 1
PROVENANCE_TYPE = "native-build"
PROVENANCE_FILE_NAME = "native-build-provenance-v1.json"
CHECKSUM_FILE_NAME = f"{PROVENANCE_FILE_NAME}.sha256"
CONTRACT_ID = "native-windows-clean-double-build-v1"
PINNED_PYTHON_VERSION = "3.12.10"
PINNED_PYINSTALLER_VERSION = "6.21.0"
PINNED_UV_VERSION = "0.11.32"
LOCK_FILE_NAMES = ("uv.lock", "apps/dashboard/package-lock.json")
WINDOWS_PLATFORM = "windows"
WINDOWS_ARCHITECTURE = "x86_64"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-beta\.[1-9][0-9]*)?"
)

# ``filename`` is the installed identity used by the existing component
# manifest. ``build_filename`` is the concrete path emitted by PyInstaller.
COMPONENTS: tuple[tuple[str, str, str], ...] = (
    ("main", "AllTheContext.exe", "AllTheContextSetup.exe"),
    ("mcp", "AllTheContextMCP.exe", "AllTheContextMCP.exe"),
    ("recovery", "AllTheContextRecovery.exe", "AllTheContextRecovery.exe"),
    ("updater", "AllTheContextUpdater.exe", "AllTheContextUpdater.exe"),
)
COMPONENT_ROLES = frozenset(role for role, _filename, _build_filename in COMPONENTS)


class NativeBuildProvenanceError(ValueError):
    """Raised when native build identity or reproducibility is not trustworthy."""


@dataclass(frozen=True)
class ComponentDigest:
    role: str
    filename: str
    build_filename: str
    sha256: str
    size: int


@dataclass(frozen=True)
class BuildSnapshot:
    label: str
    components: tuple[ComponentDigest, ...]


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


def _same_file_state(left: _FileSnapshot, right: _FileSnapshot) -> bool:
    """Compare byte-relevant identity fields across path and open-handle stats."""

    return (
        left.device,
        left.inode,
        stat.S_IFMT(left.mode),
        left.links,
        left.size,
        left.modified_ns,
        left.changed_ns,
        left.attributes,
    ) == (
        right.device,
        right.inode,
        stat.S_IFMT(right.mode),
        right.links,
        right.size,
        right.modified_ns,
        right.changed_ns,
        right.attributes,
    )


def _same_file_identity(left: _FileSnapshot, right: _FileSnapshot) -> bool:
    """Compare fields that must agree between a pathname and its open handle."""

    return (
        left.device,
        left.inode,
        stat.S_IFMT(left.mode),
        left.links,
        left.size,
        left.attributes,
    ) == (
        right.device,
        right.inode,
        stat.S_IFMT(right.mode),
        right.links,
        right.size,
        right.attributes,
    )


def _failure(message: str) -> NoReturn:
    raise NativeBuildProvenanceError(message)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(str(path.expanduser())))


def _is_link_or_reparse(path: Path) -> bool:
    try:
        information = path.stat(follow_symlinks=False)
    except (OSError, RuntimeError) as exc:
        raise NativeBuildProvenanceError("native component cannot be inspected") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(getattr(information, "st_file_attributes", 0) & reparse_flag)


def _regular_file(path: Path, *, label: str) -> Path:
    lexical = _absolute(path)
    if _is_link_or_reparse(lexical):
        _failure(f"{label} is a symlink or reparse point")
    try:
        resolved = lexical.resolve(strict=True)
        information = resolved.stat(follow_symlinks=False)
    except (OSError, RuntimeError) as exc:
        raise NativeBuildProvenanceError(f"{label} is unavailable") from exc
    if _is_link_or_reparse(resolved) or not stat.S_ISREG(information.st_mode):
        _failure(f"{label} must be a regular file")
    if int(information.st_nlink) != 1:
        _failure(f"{label} must not be a hardlink")
    return resolved


def _file_snapshot(path: Path, *, label: str) -> _FileSnapshot:
    try:
        return _FileSnapshot.from_stat(path.stat(follow_symlinks=False))
    except (OSError, RuntimeError) as exc:
        raise NativeBuildProvenanceError(f"{label} changed while it was hashed") from exc


def _hash_file(path: Path, *, label: str) -> tuple[str, int]:
    before_path = _file_snapshot(path, label=label)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            before_handle = _FileSnapshot.from_stat(os.fstat(stream.fileno()))
            if not _same_file_identity(before_handle, before_path):
                _failure(f"{label} changed before it was hashed")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
            after_handle = _FileSnapshot.from_stat(os.fstat(stream.fileno()))
    except NativeBuildProvenanceError:
        raise
    except (OSError, RuntimeError) as exc:
        raise NativeBuildProvenanceError(f"could not read {label}") from exc
    after_path = _file_snapshot(path, label=label)
    if (
        not _same_file_state(before_handle, after_handle)
        or not _same_file_state(before_path, after_path)
        or size != before_path.size
    ):
        _failure(f"{label} changed while it was hashed")
    return digest.hexdigest(), size


def _stable_hash(path: Path, *, label: str) -> tuple[str, int]:
    first = _hash_file(path, label=label)
    second = _hash_file(path, label=label)
    if first != second:
        _failure(f"{label} changed between stable reads")
    return first


def _component_descriptor(component: ComponentDigest) -> dict[str, Any]:
    return {
        "build_filename": component.build_filename,
        "filename": component.filename,
        "role": component.role,
        "sha256": component.sha256,
        "size": component.size,
    }


def collect_snapshot(
    component_paths: Mapping[str, Path], *, label: str = "clean-build"
) -> BuildSnapshot:
    """Hash exactly the four native outputs in the contract's fixed order."""

    if set(component_paths) != COMPONENT_ROLES:
        _failure("native build must provide exactly four executable components")
    identities: dict[tuple[int, int], str] = {}
    resolved: list[tuple[str, str, str, Path]] = []
    for role, filename, build_filename in COMPONENTS:
        path = _regular_file(component_paths[role], label=f"{role} executable")
        if path.name != build_filename:
            _failure(f"{role} executable filename is not canonical")
        information = _file_snapshot(path, label=f"{role} executable")
        identity = (information.device, information.inode)
        previous = identities.get(identity)
        if previous is not None:
            _failure(f"native build contains duplicate executable inputs: {previous} and {role}")
        identities[identity] = role
        resolved.append((role, filename, build_filename, path))
    components: list[ComponentDigest] = []
    for role, filename, build_filename, path in resolved:
        digest, size = _stable_hash(path, label=f"{role} executable")
        components.append(ComponentDigest(role, filename, build_filename, digest, size))
    return BuildSnapshot(label, tuple(components))


def _validate_string(value: object, *, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _failure(f"native build provenance {label} is malformed")
    return value


def _validate_component(value: object, *, expected: tuple[str, str, str]) -> ComponentDigest:
    if not isinstance(value, dict) or set(value) != {
        "build_filename",
        "filename",
        "role",
        "sha256",
        "size",
    }:
        _failure("native build provenance component is malformed")
    role, filename, build_filename = expected
    if value.get("role") != role or value.get("filename") != filename:
        _failure("native build provenance component ordering or identity is invalid")
    if value.get("build_filename") != build_filename:
        _failure("native build provenance build filename is invalid")
    digest = _validate_string(value.get("sha256"), pattern=SHA256_PATTERN, label="component digest")
    size = value.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        _failure("native build provenance component size is malformed")
    # These are fixed relative names, not filesystem paths. This explicit
    # check keeps the no-path-leakage rule visible if the catalog changes.
    for name in (filename, build_filename):
        if Path(name).is_absolute() or "/" in name or "\\" in name or ":" in name:
            _failure("native build provenance component filename is not relative")
    return ComponentDigest(role, filename, build_filename, digest, size)


def _validate_components(value: object) -> tuple[ComponentDigest, ...]:
    if not isinstance(value, list) or len(value) != len(COMPONENTS):
        _failure("native build provenance component list is invalid")
    return tuple(
        _validate_component(item, expected=expected)
        for item, expected in zip(value, COMPONENTS, strict=True)
    )


def _validate_snapshot(value: object, *, expected_label: str) -> BuildSnapshot:
    if not isinstance(value, dict) or set(value) != {"components", "label"}:
        _failure("native build provenance build snapshot is malformed")
    if value.get("label") != expected_label:
        _failure("native build provenance build ordering is invalid")
    return BuildSnapshot(expected_label, _validate_components(value.get("components")))


def _validate_toolchain(value: object) -> dict[str, str]:
    expected = {
        "python": PINNED_PYTHON_VERSION,
        "pyinstaller": PINNED_PYINSTALLER_VERSION,
        "uv": PINNED_UV_VERSION,
    }
    if not isinstance(value, dict) or set(value) != set(expected):
        _failure("native build provenance toolchain is unbound")
    if value != expected:
        _failure("native build provenance toolchain is not pinned")
    return cast(dict[str, str], value)


def _validate_locks(value: object) -> dict[str, dict[str, str]]:
    if not isinstance(value, dict) or set(value) != set(LOCK_FILE_NAMES):
        _failure("native build provenance lock inventory is unbound")
    result: dict[str, dict[str, str]] = {}
    for name in LOCK_FILE_NAMES:
        entry = value.get(name)
        if not isinstance(entry, dict) or set(entry) != {"sha256"}:
            _failure("native build provenance lock inventory is malformed")
        digest = _validate_string(entry.get("sha256"), pattern=SHA256_PATTERN, label="lock digest")
        result[name] = {"sha256": digest}
    return result


def _component_tuples(snapshot: BuildSnapshot) -> tuple[tuple[str, str, str, int], ...]:
    return tuple((item.role, item.filename, item.sha256, item.size) for item in snapshot.components)


def validate_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the complete receipt and recompute its matching claim."""

    required = {
        "architecture",
        "builds",
        "components",
        "contract",
        "platform",
        "provenance_type",
        "schema_version",
        "source_commit",
        "version",
    }
    schema_version = value.get("schema_version")
    if (
        set(value) != required
        or isinstance(schema_version, bool)
        or schema_version != SCHEMA_VERSION
    ):
        _failure("native build provenance fields or schema are invalid")
    if value.get("provenance_type") != PROVENANCE_TYPE:
        _failure("native build provenance type is invalid")
    if (
        value.get("platform") != WINDOWS_PLATFORM
        or value.get("architecture") != WINDOWS_ARCHITECTURE
    ):
        _failure("native build provenance target is invalid")
    _validate_string(value.get("version"), pattern=VERSION_PATTERN, label="version")
    _validate_string(value.get("source_commit"), pattern=COMMIT_PATTERN, label="source commit")

    contract = value.get("contract")
    required_contract = {
        "build_count",
        "comparison",
        "id",
        "locks",
        "matching",
        "path_policy",
        "toolchain",
    }
    if not isinstance(contract, dict) or set(contract) != required_contract:
        _failure("native build provenance contract is malformed")
    if (
        contract.get("build_count") != 2
        or isinstance(contract.get("build_count"), bool)
        or contract.get("comparison") != "sha256-and-size"
        or contract.get("id") != CONTRACT_ID
        or contract.get("matching") is not True
        or contract.get("path_policy") != "relative-component-filenames-only"
    ):
        _failure("native build provenance contract is not the pinned double-build policy")
    locks = _validate_locks(contract.get("locks"))
    toolchain = _validate_toolchain(contract.get("toolchain"))

    builds_value = value.get("builds")
    if not isinstance(builds_value, list) or len(builds_value) != 2:
        _failure("native build provenance must contain exactly two clean builds")
    first = _validate_snapshot(builds_value[0], expected_label="clean-build-1")
    second = _validate_snapshot(builds_value[1], expected_label="clean-build-2")
    if _component_tuples(first) != _component_tuples(second):
        _failure("native build provenance clean builds are not byte-identical")
    components = _validate_components(value.get("components"))
    if _component_tuples(first) != _component_tuples(BuildSnapshot("final", components)):
        _failure("native build provenance final components do not match clean builds")

    # Return a normalized copy so callers never accidentally trust mutable or
    # non-canonical mapping subclasses supplied by tests or integrations.
    return {
        "architecture": WINDOWS_ARCHITECTURE,
        "builds": [
            {
                "components": [_component_descriptor(item) for item in first.components],
                "label": first.label,
            },
            {
                "components": [_component_descriptor(item) for item in second.components],
                "label": second.label,
            },
        ],
        "components": [_component_descriptor(item) for item in components],
        "contract": {
            "build_count": 2,
            "comparison": "sha256-and-size",
            "id": CONTRACT_ID,
            "locks": locks,
            "matching": True,
            "path_policy": "relative-component-filenames-only",
            "toolchain": toolchain,
        },
        "platform": WINDOWS_PLATFORM,
        "provenance_type": PROVENANCE_TYPE,
        "schema_version": SCHEMA_VERSION,
        "source_commit": cast(str, value["source_commit"]),
        "version": cast(str, value["version"]),
    }


def build_payload(
    *,
    version: str,
    source_commit: str,
    toolchain: Mapping[str, str],
    locks: Mapping[str, Mapping[str, str]],
    first: BuildSnapshot,
    second: BuildSnapshot,
) -> dict[str, Any]:
    """Build a receipt only after validating the complete two-build relation."""

    normalized_toolchain = _validate_toolchain(dict(toolchain))
    normalized_locks = _validate_locks(
        {name: dict(locks[name]) for name in LOCK_FILE_NAMES}
        if set(locks) == set(LOCK_FILE_NAMES)
        else dict(locks)
    )
    payload = {
        "architecture": WINDOWS_ARCHITECTURE,
        "builds": [
            {
                "components": [_component_descriptor(item) for item in first.components],
                "label": first.label,
            },
            {
                "components": [_component_descriptor(item) for item in second.components],
                "label": second.label,
            },
        ],
        "components": [_component_descriptor(item) for item in second.components],
        "contract": {
            "build_count": 2,
            "comparison": "sha256-and-size",
            "id": CONTRACT_ID,
            "locks": normalized_locks,
            "matching": True,
            "path_policy": "relative-component-filenames-only",
            "toolchain": normalized_toolchain,
        },
        "platform": WINDOWS_PLATFORM,
        "provenance_type": PROVENANCE_TYPE,
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
        "version": version,
    }
    return validate_payload(payload)


def canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise NativeBuildProvenanceError("native build provenance cannot be canonicalized") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in pairs:
        if key in result:
            _failure("native build provenance contains duplicate JSON fields")
        result[key] = item
    return result


def _reject_nonfinite(_value: str) -> NoReturn:
    _failure("native build provenance contains a non-finite JSON number")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        _failure("native build provenance contains a non-finite JSON number")
    return parsed


def write_provenance(path: Path, payload: Mapping[str, Any]) -> tuple[Path, Path]:
    if path.name != PROVENANCE_FILE_NAME:
        _failure("native build provenance filename is not canonical")
    normalized = validate_payload(payload)
    raw = canonical_json(normalized)
    checksum = hashlib.sha256(raw).hexdigest()
    checksum_path = path.with_name(CHECKSUM_FILE_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or checksum_path.exists():
        _failure("refusing to replace native build provenance output")
    try:
        with path.open("xb") as stream:
            stream.write(raw)
        with checksum_path.open("xb") as stream:
            stream.write(f"{checksum}  {PROVENANCE_FILE_NAME}\n".encode("ascii"))
    except (FileExistsError, OSError) as exc:
        raise NativeBuildProvenanceError("could not write native build provenance") from exc
    return path, checksum_path


def load_provenance(path: Path) -> dict[str, Any]:
    if path.name != PROVENANCE_FILE_NAME:
        _failure("native build provenance filename is not canonical")
    checksum_path = path.with_name(CHECKSUM_FILE_NAME)
    try:
        raw = path.read_bytes()
        checksum = checksum_path.read_bytes()
    except OSError as exc:
        raise NativeBuildProvenanceError("native build provenance output is unavailable") from exc
    expected = f"{hashlib.sha256(raw).hexdigest()}  {PROVENANCE_FILE_NAME}\n".encode("ascii")
    if checksum != expected:
        _failure("native build provenance checksum does not match")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
            parse_float=_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeBuildProvenanceError(
            "native build provenance is not canonical UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        _failure("native build provenance is not canonical JSON")
    return validate_payload(cast(dict[str, Any], value))


def verify_provenance(
    path: Path,
    component_paths: Mapping[str, Path],
    *,
    version: str | None = None,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Verify metadata and the current executable bytes without executing them."""

    payload = load_provenance(path)
    if version is not None and payload["version"] != version:
        _failure("native build provenance version does not match verification input")
    if source_commit is not None and payload["source_commit"] != source_commit:
        _failure("native build provenance source commit does not match verification input")
    current = collect_snapshot(component_paths, label="verified-components")
    expected = _validate_components(payload["components"])
    if _component_tuples(current) != _component_tuples(BuildSnapshot("verified", expected)):
        _failure("native executable bytes do not match native build provenance")
    return payload
