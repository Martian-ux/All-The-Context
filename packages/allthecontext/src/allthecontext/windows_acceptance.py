"""Content-free evidence contract for the Windows clean-machine acceptance lane.

The acceptance runner is deliberately separate from the desktop runtime.  It
can therefore audit an operator-run journey without giving a receipt any
authority to install, update, uninstall, or retain local user data.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .release_manifest import ManifestError, ReleaseVersion

WINDOWS_ACCEPTANCE_SCHEMA_VERSION = 1
WINDOWS_ACCEPTANCE_GATES = frozenset({"BETA-P01", "BETA-S03"})
WINDOWS_COMPONENTS: tuple[tuple[str, str], ...] = (
    ("main", "AllTheContext.exe"),
    ("mcp", "AllTheContextMCP.exe"),
    ("recovery", "AllTheContextRecovery.exe"),
    ("updater", "AllTheContextUpdater.exe"),
)
WINDOWS_ACCEPTANCE_ARTIFACTS = (
    "direct_package",
    "installed_component_archive",
    "installed_component_manifest",
    "installed_component_checksum",
    "native_build_provenance",
    "native_build_provenance_checksum",
)
WINDOWS_ACCEPTANCE_CHECKS = (
    "ordinary_install",
    "first_run_setup",
    "restart",
    "login_startup",
    "update_interruption",
    "update_recovery",
    "rollback",
    "uninstall",
    "state_preserved",
    "state_removed",
    "no_leftover_binaries",
    "no_leftover_shortcuts",
    "no_leftover_tasks",
    "no_leftover_runonce",
    "protected_os_store",
    "setup_rollback",
)
WINDOWS_LEFTOVER_COUNTERS = (
    "installed_binaries",
    "shortcuts",
    "scheduled_tasks",
    "runonce_entries",
    "orphaned_client_secrets",
)

COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}")

WINDOWS_ACCEPTANCE_ALLOWED_KEYS = frozenset(
    {
        "schema_version",
        "platform",
        "architecture",
        "source_commit",
        "version",
        "clean_machine",
        "developer_tooling",
        "prerequisites",
        "permissions",
        "defender",
        "core",
        "artifacts",
        "components",
        "checks",
        "leftovers",
    }
)
WINDOWS_PREREQUISITE_KEYS = frozenset(
    {"status", "os_family", "architecture", "developer_tooling_absent"}
)
WINDOWS_PERMISSION_KEYS = frozenset({"status", "scope", "elevation"})
WINDOWS_DEFENDER_KEYS = frozenset(
    {
        "status",
        "artifact_scan",
        "real_time_protection_enabled",
        "target_files_present",
        "new_detections",
        "quarantine_events",
        "signature_version",
    }
)
WINDOWS_CORE_KEYS = frozenset({"status", "host", "loopback_only", "public_listener_count"})
WINDOWS_DESCRIPTOR_KEYS = frozenset({"name", "sha256", "size"})
WINDOWS_COMPONENT_KEYS = frozenset({"role", "filename", "sha256", "size"})
WINDOWS_CHECK_STATUSES = frozenset({"pass", "fail", "not_run", "unavailable"})


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a JSON object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ManifestError(f"{label} has unknown fields: {', '.join(unknown)}")


def _require_status(value: object, label: str) -> str:
    if not isinstance(value, str) or value not in WINDOWS_CHECK_STATUSES:
        raise ManifestError(f"{label} status is invalid")
    return value


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        raise ManifestError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _require_descriptor(value: object, label: str) -> dict[str, Any]:
    descriptor = _require_mapping(value, label)
    _reject_unknown(descriptor, WINDOWS_DESCRIPTOR_KEYS, label)
    if set(descriptor) != WINDOWS_DESCRIPTOR_KEYS:
        raise ManifestError(f"{label} is missing fields")
    name = descriptor.get("name")
    if not isinstance(name, str) or SAFE_FILENAME.fullmatch(name) is None:
        raise ManifestError(f"{label} name is unsafe")
    digest = _require_sha256(descriptor.get("sha256"), f"{label} sha256")
    size = descriptor.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ManifestError(f"{label} size must be a positive integer")
    return {"name": name, "sha256": digest, "size": size}


def _validate_prerequisites(value: object) -> dict[str, Any]:
    record = _require_mapping(value, "Windows prerequisites")
    _reject_unknown(record, WINDOWS_PREREQUISITE_KEYS, "Windows prerequisites")
    if set(record) != WINDOWS_PREREQUISITE_KEYS:
        raise ManifestError("Windows prerequisites are missing fields")
    if _require_status(record.get("status"), "Windows prerequisites") != "pass":
        raise ManifestError("Windows clean-machine acceptance requires passing prerequisites")
    if record.get("os_family") != "windows" or record.get("architecture") != "x86_64":
        raise ManifestError("Windows prerequisites must identify windows/x86_64")
    if record.get("developer_tooling_absent") is not True:
        raise ManifestError("Windows prerequisites must confirm developer tooling is absent")
    return record


def _validate_permissions(value: object) -> dict[str, Any]:
    record = _require_mapping(value, "Windows permissions")
    _reject_unknown(record, WINDOWS_PERMISSION_KEYS, "Windows permissions")
    if set(record) != WINDOWS_PERMISSION_KEYS:
        raise ManifestError("Windows permissions are missing fields")
    if _require_status(record.get("status"), "Windows permissions") != "pass":
        raise ManifestError("Windows clean-machine acceptance requires passing permissions")
    if record.get("scope") != "current_user" or record.get("elevation") != "not_required":
        raise ManifestError("Windows acceptance must be a non-elevated current-user install")
    return record


def _validate_defender(value: object) -> dict[str, Any]:
    record = _require_mapping(value, "Windows Defender evidence")
    _reject_unknown(record, WINDOWS_DEFENDER_KEYS, "Windows Defender evidence")
    if set(record) != WINDOWS_DEFENDER_KEYS:
        raise ManifestError("Windows Defender evidence is missing fields")
    if _require_status(record.get("status"), "Windows Defender evidence") != "pass":
        raise ManifestError("Windows clean-machine acceptance requires passing Defender evidence")
    if record.get("artifact_scan") != "completed":
        raise ManifestError("Windows Defender evidence must record a completed artifact scan")
    if record.get("real_time_protection_enabled") is not True:
        raise ManifestError("Windows Defender real-time protection must be enabled")
    if record.get("target_files_present") is not True:
        raise ManifestError("Windows Defender evidence must cover the target files")
    for key in ("new_detections", "quarantine_events"):
        count = record.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ManifestError(f"Windows Defender {key} must be a non-negative integer")
        if count != 0:
            raise ManifestError("Windows clean-machine acceptance requires no Defender detections")
    signature_version = record.get("signature_version")
    if not isinstance(signature_version, str) or not signature_version.strip():
        raise ManifestError("Windows Defender signature_version is required")
    return record


def _validate_core(value: object) -> dict[str, Any]:
    record = _require_mapping(value, "Windows Core evidence")
    _reject_unknown(record, WINDOWS_CORE_KEYS, "Windows Core evidence")
    if set(record) != WINDOWS_CORE_KEYS:
        raise ManifestError("Windows Core evidence is missing fields")
    if _require_status(record.get("status"), "Windows Core evidence") != "pass":
        raise ManifestError("Windows clean-machine acceptance requires a passing Core check")
    if record.get("host") != "127.0.0.1" or record.get("loopback_only") is not True:
        raise ManifestError("Windows Core must be bound to loopback")
    listener_count = record.get("public_listener_count")
    if (
        isinstance(listener_count, bool)
        or not isinstance(listener_count, int)
        or listener_count != 0
    ):
        raise ManifestError("Windows Core evidence must show zero public listeners")
    return record


def _validate_artifacts(value: object) -> dict[str, dict[str, Any]]:
    record = _require_mapping(value, "Windows acceptance artifacts")
    if set(record) != set(WINDOWS_ACCEPTANCE_ARTIFACTS):
        raise ManifestError(
            "Windows acceptance artifacts must contain the package and provenance set"
        )
    validated: dict[str, dict[str, Any]] = {}
    names: set[str] = set()
    for key in WINDOWS_ACCEPTANCE_ARTIFACTS:
        descriptor = _require_descriptor(record.get(key), f"Windows artifact {key}")
        folded = descriptor["name"].casefold()
        if folded in names:
            raise ManifestError("Windows acceptance artifact names must be unique")
        names.add(folded)
        validated[key] = descriptor
    return validated


def _validate_components(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(WINDOWS_COMPONENTS):
        raise ManifestError("Windows acceptance must contain exactly four components")
    validated: list[dict[str, Any]] = []
    names: set[str] = set()
    for item, (expected_role, expected_filename) in zip(value, WINDOWS_COMPONENTS, strict=True):
        component = _require_mapping(item, "Windows acceptance component")
        _reject_unknown(component, WINDOWS_COMPONENT_KEYS, "Windows acceptance component")
        if set(component) != WINDOWS_COMPONENT_KEYS:
            raise ManifestError("Windows acceptance component is missing fields")
        if component.get("role") != expected_role or component.get("filename") != expected_filename:
            raise ManifestError("Windows acceptance component ordering or identity is invalid")
        folded = expected_filename.casefold()
        if folded in names:
            raise ManifestError("Windows acceptance component names must be unique")
        names.add(folded)
        _require_sha256(component.get("sha256"), f"Windows component {expected_role} sha256")
        size = component.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise ManifestError(f"Windows component {expected_role} size is invalid")
        validated.append(dict(component))
    return validated


def _validate_checks(value: object, *, require_pass: bool) -> dict[str, str]:
    record = _require_mapping(value, "Windows acceptance checks")
    if set(record) != set(WINDOWS_ACCEPTANCE_CHECKS):
        raise ManifestError("Windows acceptance checks must cover the complete lifecycle")
    validated: dict[str, str] = {}
    for key in WINDOWS_ACCEPTANCE_CHECKS:
        status = _require_status(record.get(key), f"Windows acceptance check {key}")
        if require_pass and status != "pass":
            raise ManifestError(f"Windows acceptance check {key} did not pass")
        validated[key] = status
    return validated


def _validate_leftovers(value: object, *, require_zero: bool) -> dict[str, int]:
    record = _require_mapping(value, "Windows acceptance leftovers")
    if set(record) != set(WINDOWS_LEFTOVER_COUNTERS):
        raise ManifestError("Windows acceptance leftovers must cover every cleanup surface")
    validated: dict[str, int] = {}
    for key in WINDOWS_LEFTOVER_COUNTERS:
        count = record.get(key)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ManifestError(f"Windows acceptance leftover count {key} is invalid")
        if require_zero and count != 0:
            raise ManifestError(f"Windows acceptance found leftover {key}")
        validated[key] = count
    return validated


def validate_windows_acceptance(
    value: Mapping[str, Any],
    *,
    require_pass: bool = False,
    expected_source_commit: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    """Validate a complete, path-free Windows clean-machine evidence record."""

    record = dict(value)
    _reject_unknown(record, WINDOWS_ACCEPTANCE_ALLOWED_KEYS, "Windows acceptance")
    required = set(WINDOWS_ACCEPTANCE_ALLOWED_KEYS)
    if set(record) != required:
        raise ManifestError("Windows acceptance is missing fields")
    if record.get("schema_version") != WINDOWS_ACCEPTANCE_SCHEMA_VERSION or isinstance(
        record.get("schema_version"), bool
    ):
        raise ManifestError("Windows acceptance schema_version must be integer 1")
    if record.get("platform") != "windows" or record.get("architecture") != "x86_64":
        raise ManifestError("Windows acceptance target must be windows/x86_64")
    source_commit = record.get("source_commit")
    if not isinstance(source_commit, str) or COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ManifestError("Windows acceptance source_commit must be a full lowercase SHA")
    if expected_source_commit is not None and source_commit != expected_source_commit:
        raise ManifestError("Windows acceptance source_commit does not match the candidate")
    version = record.get("version")
    if not isinstance(version, str):
        raise ManifestError("Windows acceptance version is invalid")
    ReleaseVersion.parse(version)
    if expected_version is not None and version != expected_version:
        raise ManifestError("Windows acceptance version does not match the candidate")
    if record.get("clean_machine") is not True:
        raise ManifestError("Windows acceptance must explicitly identify a clean machine")
    if record.get("developer_tooling") is not False:
        raise ManifestError("Windows acceptance must confirm developer tooling was absent")

    validated = {
        "schema_version": WINDOWS_ACCEPTANCE_SCHEMA_VERSION,
        "platform": "windows",
        "architecture": "x86_64",
        "source_commit": source_commit,
        "version": version,
        "clean_machine": True,
        "developer_tooling": False,
        "prerequisites": _validate_prerequisites(record.get("prerequisites")),
        "permissions": _validate_permissions(record.get("permissions")),
        "defender": _validate_defender(record.get("defender")),
        "core": _validate_core(record.get("core")),
        "artifacts": _validate_artifacts(record.get("artifacts")),
        "components": _validate_components(record.get("components")),
        "checks": _validate_checks(record.get("checks"), require_pass=require_pass),
        "leftovers": _validate_leftovers(record.get("leftovers"), require_zero=require_pass),
    }
    return validated


def require_candidate_artifact_bindings(
    value: Mapping[str, Any],
    artifact_digests: Mapping[str, str],
) -> None:
    """Bind the outer candidate assets to the nested clean-machine record."""

    artifacts = value.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise ManifestError("Windows acceptance artifacts are unavailable")
    for key in ("direct_package", "installed_component_archive"):
        descriptor = artifacts.get(key)
        if not isinstance(descriptor, Mapping):
            raise ManifestError(f"Windows acceptance {key} binding is unavailable")
        name = descriptor.get("name")
        digest = descriptor.get("sha256")
        if not isinstance(name, str) or artifact_digests.get(name) != digest:
            raise ManifestError(f"Windows acceptance {key} is not bound to the candidate")
