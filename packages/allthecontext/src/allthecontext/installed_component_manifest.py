"""Validate the four executable components of a Windows installation.

The release workflow creates this metadata, but the updater cannot import the
workflow script from a frozen application.  Keep this small verifier in the
runtime package so the recovery helper can validate installed bytes without
trusting setup's report or a diagnostics response.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, NoReturn, cast

from .release_manifest import ManifestError, ReleaseVersion

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_TYPE = "installed-component"
MANIFEST_FILE_NAME = "installed-component-manifest-v1.json"
CHECKSUM_FILE_NAME = f"{MANIFEST_FILE_NAME}.sha256"
WINDOWS_PLATFORM = "windows"
WINDOWS_ARCHITECTURE = "x86_64"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_EXECUTABLE_BYTES = 512 * 1024 * 1024
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\.exe")
AUTHENTICODE_STATUSES = frozenset({"not-present", "present-unverified"})
COMPONENTS: tuple[tuple[str, str], ...] = (
    ("main", "AllTheContext.exe"),
    ("mcp", "AllTheContextMCP.exe"),
    ("recovery", "AllTheContextRecovery.exe"),
    ("updater", "AllTheContextUpdater.exe"),
)


class InstalledComponentManifestError(ValueError):
    """Raised when installed-component provenance cannot be trusted."""


def _failure(message: str) -> NoReturn:
    raise InstalledComponentManifestError(message)


def canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        rendered = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
    except (RecursionError, TypeError, ValueError) as exc:
        raise InstalledComponentManifestError(
            "installed-component manifest is not canonical"
        ) from exc
    return f"{rendered}\n".encode()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            _failure("installed-component manifest contains duplicate JSON fields")
        value[key] = item
    return value


def _reject_nonfinite(_value: str) -> NoReturn:
    _failure("installed-component manifest contains a non-finite JSON number")


def _load(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        _failure("installed-component manifest is too large")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except InstalledComponentManifestError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise InstalledComponentManifestError(
            "installed-component manifest is not UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        _failure("installed-component manifest must be a JSON object")
    payload = cast(dict[str, Any], value)
    if canonical_json(payload) != raw:
        _failure("installed-component manifest is not canonical JSON")
    return payload


def _descriptor(value: object, *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"filename", "sha256", "size"}:
        _failure(f"{label} descriptor is malformed")
    filename = value["filename"]
    digest = value["sha256"]
    size = value["size"]
    if not isinstance(filename, str) or SAFE_FILENAME.fullmatch(filename) is None:
        _failure(f"{label} filename is malformed")
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        _failure(f"{label} digest is malformed")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        _failure(f"{label} size is malformed")
    if size > MAX_EXECUTABLE_BYTES:
        _failure(f"{label} exceeds the maximum allowed size")
    return {"filename": filename, "sha256": digest, "size": size}


def _validate_shape(payload: Mapping[str, Any]) -> None:
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
    if set(payload) != required:
        _failure("installed-component manifest fields or schema are invalid")
    if payload["schema_version"] != MANIFEST_SCHEMA_VERSION or isinstance(
        payload["schema_version"], bool
    ):
        _failure("installed-component manifest fields or schema are invalid")
    if payload["manifest_type"] != MANIFEST_TYPE:
        _failure("installed-component manifest type is invalid")
    version = payload["version"]
    commit = payload["source_commit"]
    if (
        not isinstance(version, str)
        or not isinstance(commit, str)
        or not COMMIT_PATTERN.fullmatch(commit)
        or payload["platform"] != WINDOWS_PLATFORM
        or payload["architecture"] != WINDOWS_ARCHITECTURE
    ):
        _failure("installed-component manifest header is invalid")
    try:
        ReleaseVersion.parse(version)
    except ManifestError as exc:
        raise InstalledComponentManifestError(
            "installed-component manifest version is invalid"
        ) from exc

    package = payload["package"]
    if not isinstance(package, dict) or set(package) != {
        "direct_package",
        "filename",
        "sha256",
        "size",
    }:
        _failure("installed-component package descriptor is malformed")
    package_descriptor = _descriptor(
        {key: package[key] for key in ("filename", "sha256", "size")},
        label="archive package",
    )
    if package_descriptor["filename"] != "AllTheContextSetup.exe":
        _failure("archive package filename is invalid")
    direct = _descriptor(package["direct_package"], label="direct package")
    if direct["filename"].casefold() == package_descriptor["filename"].casefold():
        _failure("archive and direct package names must differ")
    if (direct["sha256"], direct["size"]) != (
        package_descriptor["sha256"],
        package_descriptor["size"],
    ):
        _failure("direct package does not match archive package")

    components = payload["components"]
    if (
        isinstance(payload["component_count"], bool)
        or not isinstance(payload["component_count"], int)
        or payload["component_count"] != len(COMPONENTS)
        or not isinstance(components, list)
        or len(components) != len(COMPONENTS)
    ):
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
            or filename.casefold() in seen_names
        ):
            _failure("installed-component ordering or role is invalid")
        seen_roles.add(role)
        seen_names.add(filename.casefold())
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
    main = cast(dict[str, Any], components[0])
    if (main["sha256"], main["size"]) != (
        package_descriptor["sha256"],
        package_descriptor["size"],
    ):
        _failure("main executable does not match archive package")


def validate_manifest_bytes(
    raw_manifest: bytes,
    raw_checksum: bytes,
    *,
    expected_version: str,
    expected_package_sha256: str,
    expected_package_size: int,
    expected_source_commit: str | None = None,
) -> dict[str, Any]:
    """Validate metadata copied from the signed Windows release archive."""

    manifest_digest = hashlib.sha256(raw_manifest).hexdigest()
    expected_checksum = f"{manifest_digest}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    if raw_checksum != expected_checksum:
        _failure("installed-component checksum does not match the manifest")
    payload = _load(raw_manifest)
    _validate_shape(payload)
    if payload["version"] != expected_version:
        _failure("installed-component manifest version does not match the update")
    if expected_source_commit is not None and payload["source_commit"] != expected_source_commit:
        _failure("installed-component manifest source commit does not match the update")
    package = cast(dict[str, Any], payload["package"])
    if package["sha256"] != expected_package_sha256 or package["size"] != expected_package_size:
        _failure("archive package does not match the installed-component manifest")
    return payload


def component_descriptors(payload: Mapping[str, Any]) -> dict[str, tuple[str, int]]:
    """Return the exact four role-to-digest bindings after shape validation."""

    _validate_shape(payload)
    components = cast(list[dict[str, Any]], payload["components"])
    return {
        role: (cast(str, item["sha256"]), cast(int, item["size"]))
        for item, (role, _filename) in zip(components, COMPONENTS, strict=True)
    }
