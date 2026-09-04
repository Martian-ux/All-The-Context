"""Run a bounded, content-free Microsoft Defender scan of exact Windows bytes.

The command deliberately uses only the supported ``MpCmdRun.exe`` custom-scan
operation.  It never changes Defender settings, emits raw Defender output, or
records local paths.  A receipt is passable only when the Defender status is
ready, the exact package and four manifest-bound components remain present and
byte-identical, and Defender history did not acquire a relevant detection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform as platform_module
import re
import shutil
import stat
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NoReturn, Protocol, cast

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "allthecontext" / "src"))

try:
    from scripts import installed_component_manifest as manifest_module
except (ImportError, ModuleNotFoundError):  # Direct ``python scripts/...`` execution.
    import installed_component_manifest as manifest_module


RECEIPT_SCHEMA_VERSION = 1
RECEIPT_TYPE = "windows-defender-scan"
RECEIPT_FILE_NAME = "windows-defender-scan-receipt-v1.json"
CHECKSUM_FILE_NAME = f"{RECEIPT_FILE_NAME}.sha256"
WINDOWS_PLATFORM = "windows"
WINDOWS_ARCHITECTURE = "x86_64"
MAX_HISTORY_ENTRIES = 4_096
MAX_HISTORY_OUTPUT = 8 * 1024 * 1024
MAX_SCAN_OUTPUT = 512 * 1024
MAX_RECEIPT_AGE = timedelta(hours=24)
MAX_CLOCK_SKEW = timedelta(minutes=5)
DEFAULT_SIGNATURE_MAX_AGE = timedelta(days=7)
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(-beta\.[1-9][0-9]*)?"
)
VERSIONED_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){1,5}$")
UTC_PATTERN = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?Z$"
)

COMPONENTS = manifest_module.COMPONENTS
COMPONENT_ROLES = manifest_module.COMPONENT_ROLES
MANIFEST_FILE_NAME = manifest_module.MANIFEST_FILE_NAME
SOURCE_BASENAMES = manifest_module.SOURCE_BASENAMES

REASON_CODES = frozenset(
    {
        "unsupported_platform",
        "defender_unavailable",
        "defender_malformed",
        "defender_disabled",
        "signature_stale",
        "signature_clock_invalid",
        "history_unavailable",
        "history_malformed",
        "scan_timeout",
        "scan_failed",
        "history_detected_before_scan",
        "defender_detection_history",
        "history_changed_during_scan",
        "target_missing_after_scan",
        "target_changed_after_scan",
        "target_reparse_after_scan",
        "defender_status_changed",
        "post_scan_unavailable",
    }
)


class WindowsDefenderScanError(ValueError):
    """Raised when a candidate or receipt cannot be trusted."""


class _OperationalFailure(WindowsDefenderScanError):
    def __init__(self, code: str, *, unavailable: bool = False) -> None:
        if code not in REASON_CODES:
            raise ValueError(f"unknown Defender scan failure: {code}")
        super().__init__(code)
        self.code = code
        self.unavailable = unavailable


class _BackendFailure(_OperationalFailure):
    pass


@dataclass(frozen=True)
class _Snapshot:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> _Snapshot:
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
class _Measurement:
    digest: str
    size: int
    snapshot: _Snapshot


@dataclass(frozen=True)
class _Target:
    role: str
    path: Path
    filename: str
    measurement: _Measurement


@dataclass(frozen=True)
class _DefenderStatus:
    engine_version: str
    platform_version: str
    service_version: str
    signature_version: str
    signature_updated_at: str
    signature_age_seconds: int
    antivirus_enabled: bool
    real_time_protection_enabled: bool
    ioav_protection_enabled: bool
    running_mode: str
    readiness: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "engine_version": self.engine_version,
            "platform_version": self.platform_version,
            "service_version": self.service_version,
            "signature_version": self.signature_version,
            "signature_updated_at": self.signature_updated_at,
            "signature_age_seconds": self.signature_age_seconds,
            "antivirus_enabled": self.antivirus_enabled,
            "real_time_protection_enabled": self.real_time_protection_enabled,
            "ioav_protection_enabled": self.ioav_protection_enabled,
            "running_mode": self.running_mode,
            "readiness": self.readiness,
        }


@dataclass(frozen=True)
class _HistoryEntry:
    fingerprint: str
    target_indexes: tuple[int, ...]
    quarantine_or_deletion: bool


@dataclass(frozen=True)
class _HistorySnapshot:
    entries: tuple[_HistoryEntry, ...]


class DefenderBackend(Protocol):
    """Narrow injectable boundary used by the scanner and adversarial tests."""

    def status(self) -> Mapping[str, Any]: ...

    def history(self) -> object: ...

    def custom_scan(self, path: Path) -> int: ...


class DefenderClient:
    """Invoke only read-only Defender queries and the supported custom scan."""

    _STATUS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$items = @(Get-MpComputerStatus -ErrorAction Stop)
if ($items.Count -ne 1) { throw 'status unavailable' }
$item = $items[0]
$required = @(
  'AMEngineVersion', 'AMProductVersion', 'AMServiceVersion',
  'AntivirusSignatureVersion', 'AntivirusSignatureLastUpdated',
  'AntivirusEnabled', 'RealTimeProtectionEnabled', 'IoavProtectionEnabled',
  'AMRunningMode'
)
foreach ($name in $required) {
  if (-not ($item.PSObject.Properties.Name -contains $name)) { throw 'status malformed' }
}
[ordered]@{
  engine_version = [string]$item.AMEngineVersion
  platform_version = [string]$item.AMProductVersion
  service_version = [string]$item.AMServiceVersion
  signature_version = [string]$item.AntivirusSignatureVersion
  signature_updated_at = $item.AntivirusSignatureLastUpdated.ToUniversalTime().ToString(
    'yyyy-MM-ddTHH:mm:ss.ffffffZ'
  )
  antivirus_enabled = [bool]$item.AntivirusEnabled
  real_time_protection_enabled = [bool]$item.RealTimeProtectionEnabled
  ioav_protection_enabled = [bool]$item.IoavProtectionEnabled
  running_mode = [string]$item.AMRunningMode
} | ConvertTo-Json -Compress
"""
    _HISTORY_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$records = @(Get-MpThreatDetection -ErrorAction Stop | ForEach-Object {
  [ordered]@{
    DetectionID = $_.DetectionID
    ThreatID = $_.ThreatID
    InitialDetectionTime = $_.InitialDetectionTime
    LastThreatStatusChangeTime = $_.LastThreatStatusChangeTime
    ActionSuccess = $_.ActionSuccess
    CurrentThreatExecutionStatusID = $_.CurrentThreatExecutionStatusID
    Resources = @($_.Resources)
  }
})
[ordered]@{ records = $records } | ConvertTo-Json -Compress -Depth 6
"""

    def __init__(
        self,
        *,
        mp_cmd_run: Path | None = None,
        powershell: Path | None = None,
        timeout_seconds: float = 300.0,
        runner: Callable[..., Any] | None = None,
    ) -> None:
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise WindowsDefenderScanError("timeout is invalid")
        self._mp_cmd_run = mp_cmd_run
        self._powershell = powershell
        self._timeout_seconds = timeout_seconds
        self._runner = runner or subprocess.run

    @staticmethod
    def _resolve_executable(explicit: Path | None, name: str) -> Path:
        if explicit is not None:
            if explicit.name.casefold() != name.casefold():
                raise _BackendFailure("defender_unavailable", unavailable=True)
            try:
                information = explicit.stat(follow_symlinks=False)
                if not stat.S_ISREG(information.st_mode) or explicit.is_symlink():
                    raise OSError("not a regular executable")
            except OSError as exc:
                raise _BackendFailure("defender_unavailable", unavailable=True) from exc
            return explicit
        candidates: list[Path] = []
        if name.casefold() == "mpcmdrun.exe":
            program_data = os.environ.get("PROGRAMDATA")
            if program_data:
                platform_root = Path(program_data) / "Microsoft" / "Windows Defender" / "Platform"
                with suppress(OSError):
                    candidates.extend(platform_root.glob("*/MpCmdRun.exe"))
            program_files = os.environ.get("PROGRAMFILES")
            if program_files:
                candidates.append(Path(program_files) / "Windows Defender" / "MpCmdRun.exe")
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
        for candidate in sorted(candidates, key=lambda value: str(value).casefold(), reverse=True):
            try:
                information = candidate.stat(follow_symlinks=False)
                if stat.S_ISREG(information.st_mode) and not candidate.is_symlink():
                    return candidate
            except OSError:
                continue
        raise _BackendFailure("defender_unavailable", unavailable=True)

    def _powershell_json(self, script: str, *, limit: int) -> object:
        executable = self._resolve_executable(self._powershell, "powershell.exe")
        try:
            completed = self._runner(
                [
                    str(executable),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    script,
                ],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise _BackendFailure("scan_timeout") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise _BackendFailure("defender_unavailable", unavailable=True) from exc
        if completed.returncode != 0 or not isinstance(completed.stdout, str):
            raise _BackendFailure("defender_unavailable", unavailable=True)
        if len(completed.stdout.encode("utf-8", errors="replace")) > limit:
            raise _BackendFailure("defender_malformed")
        try:
            return json.loads(
                completed.stdout,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_nonfinite,
                parse_float=_parse_finite_float,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise _BackendFailure("defender_malformed") from exc

    def status(self) -> Mapping[str, Any]:
        result = self._powershell_json(self._STATUS_SCRIPT, limit=MAX_SCAN_OUTPUT)
        if not isinstance(result, dict):
            raise _BackendFailure("defender_malformed")
        return cast(Mapping[str, Any], result)

    def history(self) -> object:
        result = self._powershell_json(self._HISTORY_SCRIPT, limit=MAX_HISTORY_OUTPUT)
        if isinstance(result, dict) and set(result) == {"records"}:
            records = result["records"]
            if not isinstance(records, list):
                raise _BackendFailure("history_malformed")
            return records
        return result

    def custom_scan(self, path: Path) -> int:
        executable = self._resolve_executable(self._mp_cmd_run, "mpcmdrun.exe")
        try:
            completed = self._runner(
                [
                    str(executable),
                    "-Scan",
                    "-ScanType",
                    "3",
                    "-File",
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise _BackendFailure("scan_timeout") from exc
        except (OSError, subprocess.SubprocessError) as exc:
            raise _BackendFailure("defender_unavailable", unavailable=True) from exc
        stdout = getattr(completed, "stdout", "")
        stderr = getattr(completed, "stderr", "")
        if (
            isinstance(stdout, str)
            and len(stdout.encode("utf-8", errors="replace")) > MAX_SCAN_OUTPUT
        ):
            raise _BackendFailure("defender_malformed")
        if (
            isinstance(stderr, str)
            and len(stderr.encode("utf-8", errors="replace")) > MAX_SCAN_OUTPUT
        ):
            raise _BackendFailure("defender_malformed")
        if not isinstance(completed.returncode, int) or isinstance(completed.returncode, bool):
            raise _BackendFailure("defender_malformed")
        return completed.returncode


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON field")
        value[key] = item
    return value


def _reject_nonfinite(_value: str) -> NoReturn:
    raise ValueError("non-finite JSON number")


def _parse_finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _format_utc(value: datetime) -> str:
    normalized = value.astimezone(UTC)
    text = normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _parse_timestamp(value: object, *, code: str) -> datetime:
    if not isinstance(value, str) or UTC_PATTERN.fullmatch(value) is None:
        raise _OperationalFailure(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise _OperationalFailure(code) from exc
    if parsed.tzinfo is None:
        raise _OperationalFailure(code)
    return parsed.astimezone(UTC)


def _version(value: object, *, label: str) -> str:
    if not isinstance(value, str) or VERSIONED_PATTERN.fullmatch(value) is None:
        raise _OperationalFailure("defender_malformed")
    return value


def _safe_path(path: Path, *, root: Path, label: str) -> Path:
    try:
        return manifest_module._regular_file(path, root=root, label=label)
    except (OSError, manifest_module.InstalledComponentManifestError) as exc:
        raise WindowsDefenderScanError("candidate input is not a regular non-reparse file") from exc


def _snapshot(path: Path) -> _Snapshot:
    try:
        information = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise WindowsDefenderScanError("candidate input is unavailable") from exc
    if not stat.S_ISREG(information.st_mode):
        raise WindowsDefenderScanError("candidate input is not a regular file")
    return _Snapshot.from_stat(information)


def _hash_file(path: Path) -> _Measurement:
    try:
        before = _snapshot(path)
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            opened = _Snapshot.from_stat(os.fstat(stream.fileno()))
            if not _same_file_state(opened, before):
                raise WindowsDefenderScanError("candidate input changed while it was opened")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
            after_open = _Snapshot.from_stat(os.fstat(stream.fileno()))
        after = _snapshot(path)
    except OSError as exc:
        raise WindowsDefenderScanError("candidate input could not be hashed") from exc
    if (
        not _same_file_state(before, after_open)
        or not _same_file_state(before, after)
        or size != before.size
    ):
        raise WindowsDefenderScanError("candidate input changed while it was hashed")
    return _Measurement(digest.hexdigest(), size, after)


def _same_file_state(left: _Snapshot, right: _Snapshot) -> bool:
    """Compare content-relevant identity; Windows open handles report mode differently."""

    return (
        left.device,
        left.inode,
        left.links,
        left.size,
        left.modified_ns,
    ) == (
        right.device,
        right.inode,
        right.links,
        right.size,
        right.modified_ns,
    )


def _stable_hash(path: Path) -> _Measurement:
    first = _hash_file(path)
    second = _hash_file(path)
    if (
        first.digest != second.digest
        or first.size != second.size
        or not _same_file_state(first.snapshot, second.snapshot)
    ):
        raise WindowsDefenderScanError("candidate input changed after it was hashed")
    return first


def _descriptor(filename: str, measurement: _Measurement) -> dict[str, Any]:
    return {"filename": filename, "sha256": measurement.digest, "size": measurement.size}


def _defender_state_equal(left: _DefenderStatus, right: _DefenderStatus) -> bool:
    """Compare stable Defender identity while allowing age to advance."""

    left_payload = left.as_dict()
    right_payload = right.as_dict()
    left_payload.pop("signature_age_seconds")
    right_payload.pop("signature_age_seconds")
    return left_payload == right_payload


def _status_from_payload(value: Mapping[str, Any]) -> _DefenderStatus:
    """Rehydrate a validated receipt snapshot for current-state comparison."""

    return _DefenderStatus(
        engine_version=cast(str, value["engine_version"]),
        platform_version=cast(str, value["platform_version"]),
        service_version=cast(str, value["service_version"]),
        signature_version=cast(str, value["signature_version"]),
        signature_updated_at=cast(str, value["signature_updated_at"]),
        signature_age_seconds=cast(int, value["signature_age_seconds"]),
        antivirus_enabled=cast(bool, value["antivirus_enabled"]),
        real_time_protection_enabled=cast(bool, value["real_time_protection_enabled"]),
        ioav_protection_enabled=cast(bool, value["ioav_protection_enabled"]),
        running_mode=cast(str, value["running_mode"]),
        readiness=cast(str, value["readiness"]),
    )


def _validate_header(*, version: str, source_commit: str) -> None:
    if VERSION_PATTERN.fullmatch(version) is None:
        raise WindowsDefenderScanError("product version is invalid")
    if COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise WindowsDefenderScanError("source commit is invalid")


def _candidate_targets(
    *,
    package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    manifest_path: Path | None,
    archive_package_path: Path | None,
    version: str,
    source_commit: str,
) -> tuple[dict[str, Any], tuple[_Target, ...], dict[str, Any] | None]:
    _validate_header(version=version, source_commit=source_commit)
    if set(component_paths) != COMPONENT_ROLES:
        raise WindowsDefenderScanError("candidate must provide exactly four components")
    if (manifest_path is None) != (archive_package_path is None):
        raise WindowsDefenderScanError("manifest and archive package must be supplied together")
    package = _safe_path(package_path, root=source_root, label="package")
    expected_package_name = f"all-the-context-{version}-windows-x86_64-unsigned.exe"
    if package.name != expected_package_name:
        raise WindowsDefenderScanError("package filename is not the expected candidate name")

    targets: list[_Target] = []
    identities: dict[tuple[int, int], str] = {}
    package_measurement = _stable_hash(package)
    package_identity = (package_measurement.snapshot.device, package_measurement.snapshot.inode)
    identities[package_identity] = "package"
    targets.append(_Target("package", package, package.name, package_measurement))

    for role, expected_filename in COMPONENTS:
        path = _safe_path(
            component_paths[role],
            root=source_root,
            label=f"{role} executable",
        )
        if path.name.casefold() not in SOURCE_BASENAMES[role]:
            raise WindowsDefenderScanError("component filename is not bound to its role")
        measurement = _stable_hash(path)
        identity = (measurement.snapshot.device, measurement.snapshot.inode)
        if identity in identities:
            raise WindowsDefenderScanError("candidate contains duplicate component inputs")
        identities[identity] = role
        targets.append(_Target(role, path, expected_filename, measurement))
    if (
        targets[0].measurement.digest != targets[1].measurement.digest
        or targets[0].measurement.size != targets[1].measurement.size
    ):
        raise WindowsDefenderScanError("package and main component are not byte-identical")

    manifest_descriptor: dict[str, Any] | None = None
    if manifest_path is not None:
        if archive_package_path is None:
            raise WindowsDefenderScanError("manifest verification requires archive package")
        try:
            manifest_payload = manifest_module.verify_manifest(
                manifest_path=manifest_path,
                package_path=archive_package_path,
                direct_package_path=package,
                component_paths={role: component_paths[role] for role, _name in COMPONENTS},
                source_root=source_root,
                version=version,
                source_commit=source_commit,
                platform=WINDOWS_PLATFORM,
                architecture=WINDOWS_ARCHITECTURE,
            )
            manifest_file = _safe_path(
                manifest_path,
                root=source_root,
                label="installed-component manifest",
            )
            manifest_measurement = _stable_hash(manifest_file)
        except (OSError, manifest_module.InstalledComponentManifestError) as exc:
            raise WindowsDefenderScanError(
                "installed-component manifest does not bind candidate"
            ) from exc
        manifest_descriptor = {
            "filename": MANIFEST_FILE_NAME,
            "sha256": manifest_measurement.digest,
            "size": manifest_measurement.size,
        }
        # ``verify_manifest`` is authoritative for all four component SHA-256
        # values and for the direct package identity.  Keep the normalized
        # payload out of the receipt; the receipt stores only exact identities.
        del manifest_payload
    return (
        {
            "filename": package.name,
            "sha256": package_measurement.digest,
            "size": package_measurement.size,
        },
        tuple(targets),
        manifest_descriptor,
    )


def _parse_status(raw: object, *, now: datetime, max_age: timedelta) -> _DefenderStatus:
    required = {
        "engine_version",
        "platform_version",
        "service_version",
        "signature_version",
        "signature_updated_at",
        "antivirus_enabled",
        "real_time_protection_enabled",
        "ioav_protection_enabled",
        "running_mode",
    }
    if not isinstance(raw, Mapping) or set(raw) != required:
        raise _OperationalFailure("defender_malformed")
    engine = _version(raw["engine_version"], label="engine version")
    platform_version = _version(raw["platform_version"], label="platform version")
    service = _version(raw["service_version"], label="service version")
    signature = _version(raw["signature_version"], label="signature version")
    updated = _parse_timestamp(raw["signature_updated_at"], code="signature_clock_invalid")
    age = now - updated
    if age < -MAX_CLOCK_SKEW:
        raise _OperationalFailure("signature_clock_invalid")
    age_seconds = max(0, int(age.total_seconds()))
    enabled_fields = (
        raw["antivirus_enabled"],
        raw["real_time_protection_enabled"],
        raw["ioav_protection_enabled"],
    )
    if any(type(value) is not bool for value in enabled_fields) or not isinstance(
        raw["running_mode"], str
    ):
        raise _OperationalFailure("defender_malformed")
    running_mode = cast(str, raw["running_mode"])
    readiness = "ready"
    if not all(enabled_fields) or running_mode.casefold() != "normal":
        readiness = "disabled"
    elif age > max_age:
        readiness = "stale"
    return _DefenderStatus(
        engine_version=engine,
        platform_version=platform_version,
        service_version=service,
        signature_version=signature,
        signature_updated_at=_format_utc(updated),
        signature_age_seconds=age_seconds,
        antivirus_enabled=cast(bool, raw["antivirus_enabled"]),
        real_time_protection_enabled=cast(bool, raw["real_time_protection_enabled"]),
        ioav_protection_enabled=cast(bool, raw["ioav_protection_enabled"]),
        running_mode=running_mode,
        readiness=readiness,
    )


def _history_field(entry: Mapping[str, Any], *names: str) -> Any:
    folded = {key.casefold(): value for key, value in entry.items() if isinstance(key, str)}
    for name in names:
        if name.casefold() in folded:
            return folded[name.casefold()]
    return None


def _normalize_history_path(value: str) -> str:
    text = value.strip()
    if text.casefold().startswith("file:"):
        text = text[5:]
    if text.startswith("\\\\?\\"):
        text = text[4:]
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


def _history_snapshot(raw: object, targets: Sequence[_Target]) -> _HistorySnapshot:
    if not isinstance(raw, list) or len(raw) > MAX_HISTORY_ENTRIES:
        raise _OperationalFailure("history_malformed")
    entries: list[_HistoryEntry] = []
    seen: set[str] = set()
    target_paths = tuple(_normalize_history_path(str(target.path)) for target in targets)
    for item in raw:
        if not isinstance(item, dict):
            raise _OperationalFailure("history_malformed")
        resources = _history_field(item, "Resources", "Resource")
        if isinstance(resources, str):
            resources = [resources]
        if (
            not isinstance(resources, list)
            or not resources
            or not all(
                isinstance(resource, str) and 0 < len(resource) <= 4096 for resource in resources
            )
        ):
            raise _OperationalFailure("history_malformed")
        normalized_resources = tuple(_normalize_history_path(resource) for resource in resources)
        matches = tuple(
            index
            for index, target_path in enumerate(target_paths)
            if target_path in normalized_resources
        )
        detection_id = _history_field(item, "DetectionID", "Id", "ID")
        threat_id = _history_field(item, "ThreatID")
        initial = _history_field(item, "InitialDetectionTime")
        changed = _history_field(item, "LastThreatStatusChangeTime")
        action_success = _history_field(item, "ActionSuccess")
        execution_status = _history_field(item, "CurrentThreatExecutionStatusID")
        if detection_id is None and threat_id is None and initial is None and changed is None:
            raise _OperationalFailure("history_malformed")
        if action_success is not None and type(action_success) is not bool:
            raise _OperationalFailure("history_malformed")
        fingerprint_value = {
            "detection_id": detection_id,
            "threat_id": threat_id,
            "initial": initial,
            "changed": changed,
            "action_success": action_success,
            "execution_status": execution_status,
            "resources": normalized_resources,
        }
        try:
            fingerprint = hashlib.sha256(
                json.dumps(
                    fingerprint_value,
                    allow_nan=False,
                    ensure_ascii=True,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError) as exc:
            raise _OperationalFailure("history_malformed") from exc
        if fingerprint in seen:
            raise _OperationalFailure("history_malformed")
        seen.add(fingerprint)
        action_text = " ".join(
            str(value).casefold()
            for value in (
                _history_field(item, "Action", "ThreatStatus", "Status"),
                execution_status,
            )
            if value is not None
        )
        action_detected = any(
            token in action_text for token in ("quarant", "delet", "remediat", "blocked")
        )
        entries.append(_HistoryEntry(fingerprint, matches, action_detected))
    return _HistorySnapshot(tuple(entries))


def _history_payload(
    before: _HistorySnapshot,
    after: _HistorySnapshot,
    *,
    target_count: int,
    post_presence: str,
) -> tuple[dict[str, Any], list[str]]:
    before_by_id = {entry.fingerprint: entry for entry in before.entries}
    after_by_id = {entry.fingerprint: entry for entry in after.entries}
    new_entries = [entry for key, entry in after_by_id.items() if key not in before_by_id]
    before_matches = sum(len(entry.target_indexes) for entry in before.entries)
    after_matches = sum(len(entry.target_indexes) for entry in after.entries)
    new_matches = sum(len(entry.target_indexes) for entry in new_entries)
    action_matches = sum(
        len(entry.target_indexes) for entry in after.entries if entry.quarantine_or_deletion
    )
    reasons: list[str] = []
    if before_matches:
        reasons.append("history_detected_before_scan")
    if new_matches:
        reasons.append("defender_detection_history")
    if new_entries and not new_matches:
        reasons.append("history_changed_during_scan")
    if post_presence in {"missing", "changed", "reparse"}:
        reasons.append(
            "target_missing_after_scan"
            if post_presence == "missing"
            else (
                "target_changed_after_scan"
                if post_presence == "changed"
                else "target_reparse_after_scan"
            )
        )
    return (
        {
            "before_entry_count": len(before.entries),
            "after_entry_count": len(after.entries),
            "new_entry_count": len(new_entries),
            "preexisting_target_detection_count": before_matches,
            "target_detection_count": after_matches,
            "quarantine_or_deletion_detection_count": max(
                action_matches, 1 if post_presence == "missing" else 0
            ),
            "status": "clear" if not reasons else "detected",
            "target_count": target_count,
        },
        reasons,
    )


def _component_descriptors(targets: Sequence[_Target]) -> list[dict[str, Any]]:
    return [
        {
            "role": target.role,
            **_descriptor(target.filename, target.measurement),
        }
        for target in targets
        if target.role != "package"
    ]


def _base_receipt(
    *,
    receipt_id: str,
    version: str,
    source_commit: str,
    package: dict[str, Any],
    targets: Sequence[_Target],
    manifest: dict[str, Any] | None,
    now: datetime,
) -> dict[str, Any]:
    return {
        "architecture": WINDOWS_ARCHITECTURE,
        "components": _component_descriptors(targets),
        "content_free": True,
        "created_at": _format_utc(now),
        "defender": None,
        "history": None,
        "invocation": {
            "completed": False,
            "result": "not_run",
            "scan_type": "custom",
            "settings_changed": False,
            "target_count": len(targets),
            "tool": "MpCmdRun.exe",
        },
        "manifest": manifest,
        "outcome": "inconclusive",
        "package": package,
        "post_scan": {
            "presence": "not_run",
            "rehashed": False,
            "stable": False,
            "target_count": len(targets),
        },
        "receipt_id": receipt_id,
        "reason_codes": [],
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "source_commit": source_commit,
        "status": "fail",
        "platform": WINDOWS_PLATFORM,
        "receipt_type": RECEIPT_TYPE,
        "version": version,
    }


def _set_failure(receipt: dict[str, Any], failure: _OperationalFailure) -> None:
    reasons = cast(list[str], receipt["reason_codes"])
    if failure.code not in reasons:
        reasons.append(failure.code)
    receipt["status"] = "unavailable" if failure.unavailable else "fail"
    receipt["outcome"] = (
        "detection"
        if failure.code
        in {
            "history_detected_before_scan",
            "defender_detection_history",
            "target_missing_after_scan",
            "target_changed_after_scan",
            "target_reparse_after_scan",
        }
        else "inconclusive"
    )


def _measure_post_scan(targets: Sequence[_Target]) -> tuple[str, bool, bool]:
    rehashed = True
    for target in targets:
        try:
            current = _stable_hash(target.path)
        except WindowsDefenderScanError:
            rehashed = False
            try:
                if os.path.lexists(str(target.path)):
                    information = target.path.stat(follow_symlinks=False)
                    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                    if target.path.is_symlink() or bool(
                        getattr(information, "st_file_attributes", 0) & reparse_flag
                    ):
                        return "reparse", False, False
                    return "changed", False, False
            except OSError:
                pass
            return "missing", False, False
        if current != target.measurement:
            return "changed", rehashed, False
    return "all_present", rehashed, True


def scan_candidate(
    *,
    package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    version: str,
    source_commit: str,
    manifest_path: Path | None = None,
    archive_package_path: Path | None = None,
    defender: DefenderBackend | None = None,
    now: Callable[[], datetime] = _utc_now,
    signature_max_age: timedelta = DEFAULT_SIGNATURE_MAX_AGE,
    platform_name: str | None = None,
) -> dict[str, Any]:
    """Scan exact candidate files and return a normalized content-free receipt."""

    package, targets, manifest = _candidate_targets(
        package_path=package_path,
        component_paths=component_paths,
        source_root=source_root,
        manifest_path=manifest_path,
        archive_package_path=archive_package_path,
        version=version,
        source_commit=source_commit,
    )
    current = now().astimezone(UTC)
    receipt = _base_receipt(
        receipt_id=f"wd-{uuid.uuid4().hex}",
        version=version,
        source_commit=source_commit,
        package=package,
        targets=targets,
        manifest=manifest,
        now=current,
    )
    if (platform_name or platform_module.system()).casefold() != "windows":
        _set_failure(receipt, _OperationalFailure("unsupported_platform", unavailable=True))
        return validate_payload(receipt)
    backend = defender or DefenderClient()
    try:
        before_status = _parse_status(backend.status(), now=current, max_age=signature_max_age)
        receipt["defender"] = {
            "before": before_status.as_dict(),
            "after": None,
            "stable": False,
            "status": before_status.readiness,
        }
        if before_status.readiness != "ready":
            code = "signature_stale" if before_status.readiness == "stale" else "defender_disabled"
            _set_failure(receipt, _OperationalFailure(code))
            return validate_payload(receipt)
    except _OperationalFailure as failure:
        _set_failure(receipt, failure)
        return validate_payload(receipt)
    except (subprocess.TimeoutExpired, TimeoutError):
        _set_failure(receipt, _BackendFailure("scan_timeout"))
        return validate_payload(receipt)
    except (OSError, subprocess.SubprocessError):
        _set_failure(receipt, _BackendFailure("defender_unavailable", unavailable=True))
        return validate_payload(receipt)

    before_history: _HistorySnapshot | None = None
    after_history: _HistorySnapshot | None = None
    try:
        before_history = _history_snapshot(backend.history(), targets)
    except _OperationalFailure as failure:
        _set_failure(
            receipt,
            _BackendFailure("history_unavailable", unavailable=True)
            if failure.code not in {"history_malformed", "scan_timeout"}
            else failure,
        )
        return validate_payload(receipt)
    except (subprocess.TimeoutExpired, TimeoutError):
        _set_failure(receipt, _BackendFailure("scan_timeout"))
        return validate_payload(receipt)
    except (OSError, subprocess.SubprocessError):
        _set_failure(receipt, _BackendFailure("history_unavailable", unavailable=True))
        return validate_payload(receipt)

    preexisting_matches = any(entry.target_indexes for entry in before_history.entries)
    if preexisting_matches:
        _set_failure(receipt, _OperationalFailure("history_detected_before_scan"))
        return validate_payload(receipt)

    completed = True
    scan_failed = False
    for target in targets:
        try:
            current_measurement = _stable_hash(target.path)
        except WindowsDefenderScanError:
            _set_failure(receipt, _OperationalFailure("post_scan_unavailable"))
            completed = False
            break
        if current_measurement != target.measurement:
            _set_failure(receipt, _OperationalFailure("target_changed_after_scan"))
            completed = False
            break
        try:
            return_code = backend.custom_scan(target.path)
        except _OperationalFailure as failure:
            _set_failure(receipt, failure)
            completed = False
            break
        except (subprocess.TimeoutExpired, TimeoutError):
            _set_failure(receipt, _BackendFailure("scan_timeout"))
            completed = False
            break
        except (OSError, subprocess.SubprocessError):
            _set_failure(receipt, _BackendFailure("defender_unavailable", unavailable=True))
            completed = False
            break
        if type(return_code) is not int:
            _set_failure(receipt, _BackendFailure("defender_malformed"))
            completed = False
            break
        if return_code != 0:
            scan_failed = True

    presence, rehashed, stable = _measure_post_scan(targets)
    post_scan = cast(dict[str, Any], receipt["post_scan"])
    post_scan.update({"presence": presence, "rehashed": rehashed, "stable": stable})
    if presence == "missing":
        _set_failure(receipt, _OperationalFailure("target_missing_after_scan"))
    elif presence == "changed":
        _set_failure(receipt, _OperationalFailure("target_changed_after_scan"))
    elif presence == "reparse":
        _set_failure(receipt, _OperationalFailure("target_reparse_after_scan"))
    elif not rehashed:
        _set_failure(receipt, _OperationalFailure("post_scan_unavailable"))

    try:
        after_history = _history_snapshot(backend.history(), targets)
    except _OperationalFailure as failure:
        _set_failure(
            receipt,
            _BackendFailure("history_unavailable", unavailable=True)
            if failure.code not in {"history_malformed", "scan_timeout"}
            else failure,
        )
    except (subprocess.TimeoutExpired, TimeoutError):
        _set_failure(receipt, _BackendFailure("scan_timeout"))
    except (OSError, subprocess.SubprocessError):
        _set_failure(receipt, _BackendFailure("history_unavailable", unavailable=True))
    if before_history is not None and after_history is not None:
        history_payload, history_reasons = _history_payload(
            before_history,
            after_history,
            target_count=len(targets),
            post_presence=presence,
        )
        receipt["history"] = history_payload
        for reason in history_reasons:
            _set_failure(receipt, _OperationalFailure(reason))

    try:
        after_status = _parse_status(
            backend.status(), now=now().astimezone(UTC), max_age=signature_max_age
        )
        defender_payload = cast(dict[str, Any], receipt["defender"])
        defender_payload["after"] = after_status.as_dict()
        defender_payload["stable"] = _defender_state_equal(before_status, after_status)
        defender_payload["status"] = after_status.readiness
        if after_status.readiness != "ready":
            _set_failure(
                receipt,
                _OperationalFailure(
                    "signature_stale" if after_status.readiness == "stale" else "defender_disabled"
                ),
            )
        elif not defender_payload["stable"]:
            _set_failure(receipt, _OperationalFailure("defender_status_changed"))
    except _OperationalFailure as failure:
        _set_failure(receipt, failure)
    except (subprocess.TimeoutExpired, TimeoutError):
        _set_failure(receipt, _BackendFailure("scan_timeout"))
    except (OSError, subprocess.SubprocessError):
        _set_failure(receipt, _BackendFailure("defender_unavailable", unavailable=True))

    invocation = cast(dict[str, Any], receipt["invocation"])
    invocation["completed"] = completed
    invocation["result"] = "nonzero" if scan_failed else ("success" if completed else "incomplete")
    if not completed and not scan_failed and not cast(list[str], receipt["reason_codes"]):
        _set_failure(receipt, _OperationalFailure("scan_failed"))
    if completed and scan_failed:
        _set_failure(receipt, _OperationalFailure("scan_failed"))

    if manifest_path is not None and archive_package_path is not None and presence == "all_present":
        try:
            _package_after, _targets_after, manifest_after = _candidate_targets(
                package_path=package_path,
                component_paths=component_paths,
                source_root=source_root,
                manifest_path=manifest_path,
                archive_package_path=archive_package_path,
                version=version,
                source_commit=source_commit,
            )
            if manifest_after != manifest:
                _set_failure(receipt, _OperationalFailure("target_changed_after_scan"))
        except WindowsDefenderScanError:
            _set_failure(receipt, _OperationalFailure("target_changed_after_scan"))

    reasons = cast(list[str], receipt["reason_codes"])
    if not reasons and completed and stable:
        receipt["status"] = "pass"
        receipt["outcome"] = "clean"
    return validate_payload(receipt)


def _assert_content_free(value: object, *, path: str = "$") -> None:
    forbidden_tokens = (
        "path",
        "raw",
        "stdout",
        "stderr",
        "secret",
        "token",
        "password",
        "credential",
        "username",
        "account",
        "context",
        "threat_name",
        "resource",
        "command",
        "payload",
        "bytes",
    )
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise WindowsDefenderScanError("receipt contains a non-string field")
            folded = key.casefold()
            if any(token in folded for token in forbidden_tokens):
                raise WindowsDefenderScanError("receipt is not content-free")
            _assert_content_free(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_content_free(child, path=f"{path}[{index}]")
    elif isinstance(value, str):
        if len(value) > 8 * 1024:
            raise WindowsDefenderScanError("receipt string is unreasonably large")
        if re.search(r"(?i)(?:[A-Z]:\\Users\\|/Users/[^/\s]+/|/home/[^/\s]+/)", value):
            raise WindowsDefenderScanError("receipt contains a private path")
        if re.search(r"(?i)(?:sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{36}|AKIA[0-9A-Z]{16})", value):
            raise WindowsDefenderScanError("receipt contains a secret-like value")


def _require_exact_keys(value: object, expected: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != expected:
        raise WindowsDefenderScanError(f"receipt {label} is malformed")
    return cast(dict[str, Any], value)


def _validate_file_descriptor(value: object, *, label: str) -> dict[str, Any]:
    descriptor = _require_exact_keys(value, {"filename", "sha256", "size"}, label)
    filename = descriptor["filename"]
    if (
        not isinstance(filename, str)
        or manifest_module.SAFE_PACKAGE_NAME.fullmatch(filename) is None
        or "/" in filename
        or "\\" in filename
    ):
        raise WindowsDefenderScanError(f"receipt {label} filename is malformed")
    digest = descriptor["sha256"]
    size = descriptor["size"]
    if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
        raise WindowsDefenderScanError(f"receipt {label} digest is malformed")
    if type(size) is not int or size <= 0:
        raise WindowsDefenderScanError(f"receipt {label} size is malformed")
    return descriptor


def _validate_defender_snapshot(value: object, *, label: str) -> dict[str, Any]:
    snapshot = _require_exact_keys(
        value,
        {
            "engine_version",
            "platform_version",
            "service_version",
            "signature_version",
            "signature_updated_at",
            "signature_age_seconds",
            "antivirus_enabled",
            "real_time_protection_enabled",
            "ioav_protection_enabled",
            "running_mode",
            "readiness",
        },
        label,
    )
    for key in ("engine_version", "platform_version", "service_version", "signature_version"):
        _version(snapshot[key], label=key)
    _parse_timestamp(snapshot["signature_updated_at"], code="defender_malformed")
    if type(snapshot["signature_age_seconds"]) is not int or snapshot["signature_age_seconds"] < 0:
        raise WindowsDefenderScanError("receipt Defender age is malformed")
    for key in ("antivirus_enabled", "real_time_protection_enabled", "ioav_protection_enabled"):
        if type(snapshot[key]) is not bool:
            raise WindowsDefenderScanError("receipt Defender status is malformed")
    if not isinstance(snapshot["running_mode"], str) or snapshot["readiness"] not in {
        "ready",
        "disabled",
        "stale",
    }:
        raise WindowsDefenderScanError("receipt Defender status is malformed")
    return snapshot


def validate_payload(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a complete receipt without accepting maintainer assertions."""

    required = {
        "architecture",
        "components",
        "content_free",
        "created_at",
        "defender",
        "history",
        "invocation",
        "manifest",
        "outcome",
        "package",
        "platform",
        "post_scan",
        "receipt_id",
        "receipt_type",
        "reason_codes",
        "schema_version",
        "source_commit",
        "status",
        "version",
    }
    if set(value) != required:
        raise WindowsDefenderScanError("receipt fields or schema are invalid")
    _assert_content_free(value)
    if (
        value["schema_version"] != RECEIPT_SCHEMA_VERSION
        or type(value["schema_version"]) is not int
    ):
        raise WindowsDefenderScanError("receipt schema is invalid")
    if value["receipt_type"] != RECEIPT_TYPE or value["platform"] != WINDOWS_PLATFORM:
        raise WindowsDefenderScanError("receipt type or platform is invalid")
    if value["architecture"] != WINDOWS_ARCHITECTURE or value["content_free"] is not True:
        raise WindowsDefenderScanError("receipt target or content boundary is invalid")
    if (
        not isinstance(value["receipt_id"], str)
        or re.fullmatch(r"wd-[0-9a-f]{32}", value["receipt_id"]) is None
    ):
        raise WindowsDefenderScanError("receipt identifier is invalid")
    if not isinstance(value["version"], str) or VERSION_PATTERN.fullmatch(value["version"]) is None:
        raise WindowsDefenderScanError("receipt version is invalid")
    if (
        not isinstance(value["source_commit"], str)
        or COMMIT_PATTERN.fullmatch(value["source_commit"]) is None
    ):
        raise WindowsDefenderScanError("receipt source commit is invalid")
    _parse_timestamp(value["created_at"], code="defender_malformed")
    if value["status"] not in {"pass", "fail", "unavailable", "not_run"}:
        raise WindowsDefenderScanError("receipt status is invalid")
    if value["outcome"] not in {"clean", "detection", "inconclusive"}:
        raise WindowsDefenderScanError("receipt outcome is invalid")
    reasons = value["reason_codes"]
    if value["status"] != "pass" and (
        not isinstance(reasons, list)
        or not reasons
        or any(not isinstance(reason, str) or reason not in REASON_CODES for reason in reasons)
    ):
        raise WindowsDefenderScanError("receipt failure reasons are invalid")
    if value["status"] == "pass" and reasons != []:
        raise WindowsDefenderScanError("passing receipt contains failure reasons")

    _validate_file_descriptor(value["package"], label="package")
    components = value["components"]
    if not isinstance(components, list) or len(components) != len(COMPONENTS):
        raise WindowsDefenderScanError("receipt component list is invalid")
    for item, (expected_role, expected_filename) in zip(components, COMPONENTS, strict=True):
        entry = _require_exact_keys(item, {"role", "filename", "sha256", "size"}, "component")
        if entry["role"] != expected_role or entry["filename"] != expected_filename:
            raise WindowsDefenderScanError("receipt component ordering is invalid")
        _validate_file_descriptor(
            {key: entry[key] for key in ("filename", "sha256", "size")}, label="component"
        )
    manifest = value["manifest"]
    if manifest is not None:
        descriptor = _require_exact_keys(manifest, {"filename", "sha256", "size"}, "manifest")
        if descriptor["filename"] != MANIFEST_FILE_NAME:
            raise WindowsDefenderScanError("receipt manifest filename is invalid")
        if (
            not isinstance(descriptor["sha256"], str)
            or SHA256_PATTERN.fullmatch(descriptor["sha256"]) is None
            or type(descriptor["size"]) is not int
            or descriptor["size"] <= 0
        ):
            raise WindowsDefenderScanError("receipt manifest descriptor is malformed")

    invocation = _require_exact_keys(
        value["invocation"],
        {"completed", "result", "scan_type", "settings_changed", "target_count", "tool"},
        "invocation",
    )
    if (
        type(invocation["completed"]) is not bool
        or invocation["scan_type"] != "custom"
        or type(invocation["settings_changed"]) is not bool
        or invocation["target_count"] != 5
        or invocation["tool"] != "MpCmdRun.exe"
        or invocation["result"] not in {"not_run", "success", "nonzero", "incomplete"}
    ):
        raise WindowsDefenderScanError("receipt invocation is malformed")

    post_scan = _require_exact_keys(
        value["post_scan"], {"presence", "rehashed", "stable", "target_count"}, "post-scan state"
    )
    if (
        post_scan["presence"]
        not in {"all_present", "missing", "changed", "reparse", "unavailable", "not_run"}
        or type(post_scan["rehashed"]) is not bool
        or type(post_scan["stable"]) is not bool
        or post_scan["target_count"] != 5
    ):
        raise WindowsDefenderScanError("receipt post-scan state is malformed")

    defender = value["defender"]
    if defender is not None:
        defender_payload = _require_exact_keys(
            defender, {"before", "after", "stable", "status"}, "Defender"
        )
        if defender_payload["before"] is not None:
            _validate_defender_snapshot(defender_payload["before"], label="Defender before")
        if defender_payload["after"] is not None:
            _validate_defender_snapshot(defender_payload["after"], label="Defender after")
        if type(defender_payload["stable"]) is not bool or defender_payload["status"] not in {
            "ready",
            "disabled",
            "stale",
            "malformed",
            "unavailable",
            "changed",
        }:
            raise WindowsDefenderScanError("receipt Defender envelope is malformed")
    history = value["history"]
    if history is not None:
        history_payload = _require_exact_keys(
            history,
            {
                "before_entry_count",
                "after_entry_count",
                "new_entry_count",
                "preexisting_target_detection_count",
                "target_detection_count",
                "quarantine_or_deletion_detection_count",
                "status",
                "target_count",
            },
            "history",
        )
        for key in (
            "before_entry_count",
            "after_entry_count",
            "new_entry_count",
            "preexisting_target_detection_count",
            "target_detection_count",
            "quarantine_or_deletion_detection_count",
        ):
            if type(history_payload[key]) is not int or history_payload[key] < 0:
                raise WindowsDefenderScanError("receipt history count is malformed")
        if (
            history_payload["status"] not in {"clear", "detected"}
            or history_payload["target_count"] != 5
        ):
            raise WindowsDefenderScanError("receipt history is malformed")

    if value["status"] == "pass":
        if value["outcome"] != "clean" or defender is None or history is None:
            raise WindowsDefenderScanError("receipt pass prerequisites are incomplete")
        defender_payload = cast(dict[str, Any], defender)
        if not isinstance(defender_payload["before"], dict) or not isinstance(
            defender_payload["after"], dict
        ):
            raise WindowsDefenderScanError("receipt pass Defender snapshots are incomplete")
        before = cast(dict[str, Any], defender_payload["before"])
        after = cast(dict[str, Any], defender_payload["after"])
        if (
            defender_payload["status"] != "ready"
            or defender_payload["stable"] is not True
            or before["readiness"] != "ready"
            or after["readiness"] != "ready"
            or cast(dict[str, Any], invocation)["completed"] is not True
            or cast(dict[str, Any], invocation)["result"] != "success"
            or cast(dict[str, Any], invocation)["settings_changed"] is not False
            or cast(dict[str, Any], post_scan)["presence"] != "all_present"
            or cast(dict[str, Any], post_scan)["rehashed"] is not True
            or cast(dict[str, Any], post_scan)["stable"] is not True
            or cast(dict[str, Any], history)["status"] != "clear"
            or cast(dict[str, Any], history)["new_entry_count"] != 0
            or cast(dict[str, Any], history)["target_detection_count"] != 0
        ):
            raise WindowsDefenderScanError("receipt pass predicates are false")
    elif value["outcome"] == "clean":
        raise WindowsDefenderScanError("non-passing receipt cannot claim clean outcome")
    return dict(value)


def canonical_json(value: Mapping[str, Any]) -> bytes:
    try:
        return (
            json.dumps(value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise WindowsDefenderScanError("receipt cannot be canonicalized") from exc


def write_receipt(path: Path, payload: Mapping[str, Any]) -> tuple[Path, Path]:
    if path.name != RECEIPT_FILE_NAME:
        raise WindowsDefenderScanError("receipt filename is not canonical")
    normalized = validate_payload(payload)
    raw = canonical_json(normalized)
    checksum = hashlib.sha256(raw).hexdigest()
    checksum_path = path.with_name(CHECKSUM_FILE_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or checksum_path.exists():
        raise WindowsDefenderScanError("refusing to replace receipt output")
    try:
        with path.open("xb") as stream:
            stream.write(raw)
        with checksum_path.open("xb") as stream:
            stream.write(f"{checksum}  {RECEIPT_FILE_NAME}\n".encode("ascii"))
    except (FileExistsError, OSError) as exc:
        raise WindowsDefenderScanError("could not write receipt") from exc
    return path, checksum_path


def _load_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
            parse_float=_parse_finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise WindowsDefenderScanError("receipt is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or canonical_json(value) != raw:
        raise WindowsDefenderScanError("receipt is not canonical JSON")
    return cast(dict[str, Any], value)


def load_receipt(path: Path) -> dict[str, Any]:
    if path.name != RECEIPT_FILE_NAME:
        raise WindowsDefenderScanError("receipt filename is not canonical")
    checksum_path = path.with_name(CHECKSUM_FILE_NAME)
    try:
        raw = path.read_bytes()
        checksum = checksum_path.read_bytes()
    except OSError as exc:
        raise WindowsDefenderScanError("receipt output is unavailable") from exc
    expected = f"{hashlib.sha256(raw).hexdigest()}  {RECEIPT_FILE_NAME}\n".encode("ascii")
    if checksum != expected:
        raise WindowsDefenderScanError("receipt checksum does not match")
    return validate_payload(_load_json(raw))


def verify_receipt(
    *,
    receipt_path: Path,
    package_path: Path,
    component_paths: Mapping[str, Path],
    source_root: Path,
    version: str | None = None,
    source_commit: str | None = None,
    manifest_path: Path | None = None,
    archive_package_path: Path | None = None,
    defender: DefenderBackend | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> dict[str, Any]:
    """Verify receipt authenticity boundary, current bytes, and fresh Defender state."""

    receipt = load_receipt(receipt_path)
    if receipt["status"] != "pass":
        raise WindowsDefenderScanError("receipt is not passing evidence")
    if version is not None and receipt["version"] != version:
        raise WindowsDefenderScanError("receipt version does not match verification input")
    if source_commit is not None and receipt["source_commit"] != source_commit:
        raise WindowsDefenderScanError("receipt source commit does not match verification input")
    created_at = _parse_timestamp(receipt["created_at"], code="defender_malformed")
    current = now().astimezone(UTC)
    if current < created_at - MAX_CLOCK_SKEW or current - created_at > MAX_RECEIPT_AGE:
        raise WindowsDefenderScanError("receipt is stale")
    package, targets, manifest = _candidate_targets(
        package_path=package_path,
        component_paths=component_paths,
        source_root=source_root,
        manifest_path=manifest_path,
        archive_package_path=archive_package_path,
        version=cast(str, receipt["version"]),
        source_commit=cast(str, receipt["source_commit"]),
    )
    if receipt["package"] != package or receipt["components"] != _component_descriptors(targets):
        raise WindowsDefenderScanError("receipt identities do not match current candidate")
    if receipt["manifest"] != manifest:
        raise WindowsDefenderScanError("receipt manifest identity does not match current candidate")
    presence, rehashed, stable = _measure_post_scan(targets)
    if presence != "all_present" or not rehashed or not stable:
        raise WindowsDefenderScanError("candidate changed after receipt")
    backend = defender or DefenderClient()
    try:
        current_status = _parse_status(
            backend.status(), now=current, max_age=DEFAULT_SIGNATURE_MAX_AGE
        )
    except _OperationalFailure as failure:
        raise WindowsDefenderScanError(
            "current Defender status cannot validate receipt"
        ) from failure
    defender_payload = cast(dict[str, Any], receipt["defender"])
    recorded_after = cast(dict[str, Any], defender_payload["after"])
    if (
        not _defender_state_equal(_status_from_payload(recorded_after), current_status)
        or current_status.readiness != "ready"
    ):
        raise WindowsDefenderScanError("receipt Defender state is stale or changed")
    return receipt


def _add_candidate_arguments(parser: argparse.ArgumentParser, *, receipt: bool = False) -> None:
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--main", type=Path, required=True)
    parser.add_argument("--mcp", type=Path, required=True)
    parser.add_argument("--recovery", type=Path, required=True)
    parser.add_argument("--updater", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--archive-package", type=Path)
    if receipt:
        parser.add_argument("--receipt", type=Path, required=True)
    else:
        parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)


def _component_arguments(arguments: argparse.Namespace) -> dict[str, Path]:
    return {
        "main": arguments.main,
        "mcp": arguments.mcp,
        "recovery": arguments.recovery,
        "updater": arguments.updater,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan")
    _add_candidate_arguments(scan)
    scan.add_argument("--mp-cmd-run", type=Path)
    scan.add_argument("--powershell", type=Path)
    scan.add_argument("--timeout-seconds", type=float, default=300.0)
    scan.add_argument("--signature-max-age-hours", type=float, default=168.0)
    verify = commands.add_parser("verify")
    _add_candidate_arguments(verify, receipt=True)
    verify.add_argument("--powershell", type=Path)
    verify.add_argument("--timeout-seconds", type=float, default=300.0)
    return parser


def main() -> int:
    try:
        arguments = _parser().parse_args()
        if arguments.command == "scan":
            max_age = timedelta(hours=arguments.signature_max_age_hours)
            if arguments.signature_max_age_hours <= 0 or not math.isfinite(
                arguments.signature_max_age_hours
            ):
                raise WindowsDefenderScanError("signature age is invalid")
            receipt = scan_candidate(
                package_path=arguments.package,
                component_paths=_component_arguments(arguments),
                source_root=arguments.source_root,
                version=arguments.version,
                source_commit=arguments.source_commit,
                manifest_path=arguments.manifest,
                archive_package_path=arguments.archive_package,
                defender=DefenderClient(
                    mp_cmd_run=arguments.mp_cmd_run,
                    powershell=arguments.powershell,
                    timeout_seconds=arguments.timeout_seconds,
                ),
                signature_max_age=max_age,
            )
            write_receipt(arguments.output, receipt)
            print(f"{RECEIPT_FILE_NAME} status={receipt['status']}")
            return 0 if receipt["status"] == "pass" else 1
        receipt = verify_receipt(
            receipt_path=arguments.receipt,
            package_path=arguments.package,
            component_paths=_component_arguments(arguments),
            source_root=arguments.source_root,
            version=arguments.version,
            source_commit=arguments.source_commit,
            manifest_path=arguments.manifest,
            archive_package_path=arguments.archive_package,
            defender=DefenderClient(
                powershell=arguments.powershell,
                timeout_seconds=arguments.timeout_seconds,
            ),
        )
        print(f"{RECEIPT_FILE_NAME} status={receipt['status']}")
        return 0
    except (WindowsDefenderScanError, OSError, ValueError):
        print("Windows Defender scan error: operation rejected", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
