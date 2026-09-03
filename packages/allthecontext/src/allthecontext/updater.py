"""Fail-closed native update checking, staging, handoff, and recovery.

Release metadata is untrusted until :mod:`allthecontext.release_manifest` has
verified its exact schema, trust key, signature, and version policy.  This
module deliberately keeps transport and installation behind small protocols so
the transaction can be exercised without a network or a real installation.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import stat
import struct
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, suppress
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Literal, Protocol, cast
from urllib.parse import urljoin, urlsplit

from platformdirs import user_data_path

from . import __version__
from .desktop_runtime import RuntimeCommand
from .release_manifest import (
    ManifestError,
    ReleaseVersion,
    load_keyring,
    sha256_file,
    verify_manifest,
)
from .windows_update_helper import (
    MAX_JOURNAL_BYTES,
    MAX_RETIREMENT_TOMBSTONE_BYTES,
    POST_COMMIT_DEGRADED_ERROR,
    RETIREMENT_TOMBSTONE_DIRECTORY,
    RETIREMENT_TOMBSTONE_SCHEMA_VERSION,
    HelperError,
    HelperPhase,
    UpdateJournal,
    bind_handoff_state,
    bind_recovery_authority,
    completed_transaction_is_authoritative,
    journal_handoff_identity,
    launch_recovery_helper,
    recovery_authority_retirement_status,
    register_recovery,
    request_rollback,
    retire_recovery_authority,
    transaction_outcome,
    validate_recovery_authority,
)
from .windows_update_helper import (
    _atomic_json as _helper_atomic_json,
)
from .windows_update_helper import (
    _plain_directory_chain_if_present as _helper_plain_directory_chain_if_present,
)
from .windows_update_helper import (
    _plain_file_stat as _helper_plain_file_stat,
)
from .windows_update_helper import (
    _plain_file_stat_if_present as _helper_plain_file_stat_if_present,
)
from .windows_update_helper import (
    _prepare_plain_directory_chain as _helper_prepare_plain_directory_chain,
)
from .windows_update_helper import (
    _transaction_entry_has_recovery_evidence as _helper_transaction_entry_has_recovery_evidence,
)

CURRENT_VERSION = __version__
MAX_MANIFEST_BYTES = 128 * 1024
MAX_PREFERENCES_BYTES = 16 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 20.0
MAX_REDIRECTS = 1
CHECK_INTERVAL = timedelta(hours=24)
MAX_CLEANUP_ENTRIES = 32
MAX_CLEANUP_DEPTH = 32
RECOVERY_EVIDENCE_INCOMPLETE_ERROR = (
    "Persisted update recovery evidence was incomplete; manual recovery is required"
)
DEFAULT_BETA_MANIFEST_URL = (
    "https://martian-ux.github.io/All-The-Context/beta/windows/x86_64/manifest-v1.json"
)
WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

Channel = Literal["stable", "beta"]


class UpdateError(RuntimeError):
    """A safe, operator-facing update failure without sensitive detail."""


class _PersistedMetadataError(UpdateError):
    """A bounded metadata failure with a conservative replacement decision."""

    def __init__(self, message: str, *, safe_to_replace: bool, missing: bool = False) -> None:
        self.safe_to_replace = safe_to_replace
        self.missing = missing
        super().__init__(message)


class _BoundedJsonError(UpdateError):
    """Expected bounded JSON boundary failure, distinct from programming errors."""


class _RetirementEvidenceError(UpdateError):
    """Terminal retirement evidence failed closed and must not be replaced."""


@dataclass(frozen=True, slots=True)
class _BoundedJson:
    raw: bytes
    value: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _CleanupAction:
    path: Path
    parent_expected: os.stat_result
    target_expected: os.stat_result
    remove_directory: bool


@dataclass(slots=True)
class _CleanupBudget:
    entries_used: int = 0

    def reserve(self, depth: int) -> bool:
        if depth > MAX_CLEANUP_DEPTH or self.entries_used >= MAX_CLEANUP_ENTRIES:
            return False
        self.entries_used += 1
        return True


class UpdateEndpointHttpError(UpdateError):
    """An update endpoint returned a bounded, nonsecret HTTP status."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Update endpoint returned HTTP {status_code}")


class UpdateBusyError(UpdateError):
    """Another check, download, or install owns the transaction."""


_PUBLIC_ERROR_MESSAGES = frozenset(
    {
        "Automatic update checks do not support this operating system",
        "Automatic update checks require a 64-bit application runtime",
        "Automatic update checks do not support this CPU architecture",
        "Persisted update metadata could not be saved safely",
        "The verified update artifact exceeds the safety limit",
        "The verified update artifact changed while it was checked",
        "The verified update artifact is unreadable",
        "Update endpoint has an invalid network port",
        "Update endpoint must be HTTPS without embedded credentials",
        "Release download redirect was refused",
        "Update endpoint redirect was refused",
        "Update endpoint returned an invalid Content-Length",
        "Update metadata exceeds the size limit",
        "Update metadata could not be decoded safely",
        "Update metadata must be a JSON object",
        "Release artifact declares an unsupported size",
        "Release download length differs from signed metadata",
        "Update download was cancelled",
        "Release download exceeded its signed size",
        "Release download was truncated",
        "Insufficient disk space to stage and recover this update",
        "Insufficient disk space",
        "Installed files are locked",
        "Installer process crashed",
        "Manual installation is required for this verified update",
        "The Windows release artifact is not a valid ZIP archive",
        "Release archive contains an unsafe path",
        "Release archive expands beyond the safety limit",
        "Release archive contains multiple Windows setup programs",
        "Release archive does not contain AllTheContextSetup.exe",
        "Release artifact exceeded the safety limit at handoff",
        "The installed update source changed during copy",
        "Verified release archive evidence is missing at handoff",
        "The verified release archive changed during handoff",
        "The verified release archive changed during extraction",
        "The Windows recovery transaction could not be prepared",
        "The independent Windows recovery journal is unavailable",
        "The independent Windows rollback could not be requested",
        "Core database is unavailable for the required pre-update backup",
        "Pre-update database backup failed integrity verification",
        "Persisted update state cannot be changed safely",
        "Persisted update preferences cannot be changed safely",
        "Interrupted updater staging could not be cleaned safely",
        "The interrupted update operation was safely cancelled",
        "The update did not become healthy; the previous app and vault were restored",
        "The Windows update recovery journal was invalid",
        "Persisted update recovery metadata was invalid and was reset safely",
        "Persisted update recovery metadata was unsafe; recovery evidence was preserved",
        RECOVERY_EVIDENCE_INCOMPLETE_ERROR,
        "Update recovery completed but cleanup could not be completed safely; retry recovery",
        POST_COMMIT_DEGRADED_ERROR,
        "The new version failed its health check and was rolled back",
        "The new version did not become healthy and automatic rollback failed",
        "Signed update metadata targets a different platform",
        "Signed update metadata targets a different architecture",
        "Verified update metadata declares an unsupported artifact size",
        "Verified update transaction identity is unavailable",
        "Verified update metadata identity is unavailable",
        "Verified update metadata could not be re-checked; check again",
        "Verified update metadata changed; check again",
        "Verified update state no longer matches its metadata",
        "Update channel must be stable or beta",
        "There is no available update to defer",
        "This compatibility or security update cannot be deferred",
        "A verified same-version candidate is required before acceptance",
        "No verified candidate is available for acceptance",
        "Only an exact same-version signed candidate can be accepted",
        "Verified candidate version metadata is invalid",
        "Verified same-version metadata could not be re-checked; check again",
        "A verified available update is required before download",
        "The updater staging directory is not a trusted plain directory",
        "The previous staged artifact is not a plain file",
        "Release artifact checksum does not match signed metadata",
        "A completely verified update must be ready before saving",
        "Verified update artifact is no longer available; download again",
        "Saved update artifact exceeded its signed size",
        "Saved update artifact failed signed checksum verification",
        "A completely verified update must be ready before install",
        "Verified update artifact identity changed; check again",
        "Release artifact changed after preflight",
        "The Core port is invalid for update recovery",
        "Updater staging cleanup could not be completed safely",
        "Persisted update state was corrupt and was reset safely",
        "Persisted update state could not be read safely",
        "Persisted update state was invalid; recovery evidence was preserved",
        "Persisted update state could not be read safely; recovery evidence was preserved",
        "Persisted update paths were invalid and were reset safely",
        "No HTTPS metadata endpoint is configured for this update channel",
        "Update endpoint returned HTTP 404",
        "Persisted updater error was sanitized",
        "Signed update metadata signature verification failed",
        "The signing key for update metadata is revoked",
        "Update metadata has no uniquely trusted signing key",
        "Signed update metadata is a downgrade or not newer than the installed version",
        "Signed update metadata violates the requested channel",
        "Update signing key metadata is invalid",
        "Update metadata contains an invalid release version",
        "Update metadata contains an unsafe URL",
        "Update metadata verification failed",
        "Update check failed safely",
        "Update download verification failed safely",
        "Update download setup failed safely",
        "Update download failed safely",
        "Update installation verification failed safely",
        "Update installation failed safely",
    }
)


def _public_manifest_error(error: ManifestError) -> str:
    """Project untrusted manifest/keyring failures into stable public text."""

    detail = str(error).casefold()
    if "signature" in detail:
        return "Signed update metadata signature verification failed"
    if "revoked" in detail:
        return "The signing key for update metadata is revoked"
    if "uniquely trusted" in detail:
        return "Update metadata has no uniquely trusted signing key"
    if "downgrade" in detail or "newer" in detail:
        return "Signed update metadata is a downgrade or not newer than the installed version"
    if "platform" in detail:
        return "Signed update metadata targets a different platform"
    if "architecture" in detail:
        return "Signed update metadata targets a different architecture"
    if "channel" in detail:
        return "Signed update metadata violates the requested channel"
    if "keyring" in detail or "key " in detail or "key metadata" in detail:
        return "Update signing key metadata is invalid"
    if "version" in detail:
        return "Update metadata contains an invalid release version"
    if "url" in detail or "https" in detail:
        return "Update metadata contains an unsafe URL"
    return "Update metadata verification failed"


def _public_error_message(error: BaseException, *, fallback: str) -> str:
    """Return only allowlisted or classified updater text to public state."""

    if isinstance(error, ManifestError):
        return _public_manifest_error(error)
    if isinstance(error, UpdateEndpointHttpError):
        status = error.status_code
        if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 999:
            return f"Update endpoint returned HTTP {status}"
        return fallback
    message = str(error)
    if message in _PUBLIC_ERROR_MESSAGES:
        return message
    http_prefix = "Update endpoint returned HTTP "
    status_text = message[len(http_prefix) :]
    if len(status_text) == 3 and status_text.isdigit():
        status = int(status_text)
        if 100 <= status <= 999:
            return f"Update endpoint returned HTTP {status}"
    return fallback


def _sanitize_persisted_error(value: str | None) -> str | None:
    if value is None:
        return None
    if value in _PUBLIC_ERROR_MESSAGES:
        return value
    prefix = "Update endpoint returned HTTP "
    suffix = value[len(prefix) :] if value.startswith(prefix) else ""
    if len(suffix) == 3 and suffix.isdigit() and 100 <= int(suffix) <= 999:
        return value
    return "Persisted updater error was sanitized"


def _safe_public_url(value: str | None) -> str | None:
    if value is None or len(value) > 2048:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return None
    return value


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


class UpdatePhase(StrEnum):
    IDLE = "idle"
    DISABLED = "disabled"
    CHECKING = "checking"
    CURRENT = "current"
    UNPUBLISHED = "unpublished"
    AVAILABLE = "available"
    DEFERRED = "deferred"
    DOWNLOADING = "downloading"
    READY = "ready"
    INSTALLING = "installing"
    RESTART_REQUIRED = "restart_required"
    INSTALLED = "installed"
    ROLLED_BACK = "rolled_back"
    MANUAL_REQUIRED = "manual_required"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class UpdatePreferences:
    enabled: bool = True
    channel: Channel = "stable"
    deferred_version: str | None = None


@dataclass(slots=True)
class UpdateState:
    phase: UpdatePhase = UpdatePhase.IDLE
    current_version: str = CURRENT_VERSION
    offered_version: str | None = None
    mandatory: bool = False
    release_notes_url: str | None = None
    downloaded_path: str | None = None
    backup_path: str | None = None
    last_checked_at: str | None = None
    last_error: str | None = None
    operation_id: str | None = None
    transaction_path: str | None = None
    recovery_attempts: int = 0
    manifest_identity: str | None = None
    handoff_identity: str | None = None
    pending_handoff_identity: str | None = None
    completed_handoff_identity: str | None = None


@dataclass(frozen=True, slots=True)
class PreparedArtifact:
    path: Path
    filename: str
    size: int


@dataclass(frozen=True, slots=True)
class InstallPlan:
    artifact: Path
    target_version: str
    current_version: str
    operation_id: str
    operation_dir: Path
    transaction_dir: Path
    database_path: Path
    database_backup_path: Path
    state_path: Path
    core_host: str
    core_port: int
    artifact_sha256: str | None = None
    artifact_size: int | None = None


@dataclass(frozen=True, slots=True)
class UpdateConfig:
    data_dir: Path
    keyring_path: Path
    manifest_urls: Mapping[Channel, str]
    current_version: str = CURRENT_VERSION
    platform_name: str = field(default_factory=lambda: current_platform()[0])
    architecture: str = field(default_factory=lambda: current_platform()[1])

    @classmethod
    def default(cls) -> UpdateConfig:
        data_dir = Path(user_data_path("AllTheContext", "AllTheContext", roaming=False))
        package_keyring = Path(__file__).resolve().with_name("update_keys.json")
        platform_name, architecture = current_platform()
        urls: dict[Channel, str] = {}
        if (
            _packaged_update_runtime(platform_name)
            and platform_name == "windows"
            and architecture == "x86_64"
        ):
            try:
                keyring = load_keyring(package_keyring)
            except (ManifestError, OSError, ValueError, TypeError, json.JSONDecodeError):
                keyring = {"keys": []}
            keys = keyring.get("keys")
            if isinstance(keys, list) and any(
                isinstance(key, dict)
                and key.get("status") == "active"
                and isinstance(key.get("channels"), list)
                and "beta" in key["channels"]
                for key in keys
            ):
                urls["beta"] = DEFAULT_BETA_MANIFEST_URL
        stable = os.environ.get("ATC_UPDATE_STABLE_URL")
        beta = os.environ.get("ATC_UPDATE_BETA_URL")
        if stable:
            urls["stable"] = stable
        if beta:
            urls["beta"] = beta
        return cls(
            data_dir / "updates",
            package_keyring,
            urls,
            platform_name=platform_name,
            architecture=architecture,
        )


def _packaged_update_runtime(platform_name: str) -> bool:
    if bool(getattr(sys, "frozen", False)):
        return True
    if platform_name != "windows":
        return False
    try:
        executable = Path(sys.executable).resolve()
        helper = executable.with_name("AllTheContextUpdater.exe")
        return (
            executable.name.casefold() == "allthecontext.exe"
            and executable.is_file()
            and helper.is_file()
        )
    except (OSError, RuntimeError, ValueError):
        return False


def current_platform() -> tuple[str, str]:
    system = platform.system()
    try:
        platform_name = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}[system]
    except KeyError as exc:
        raise UpdateError("Automatic update checks do not support this operating system") from exc
    if struct.calcsize("P") * 8 != 64:
        raise UpdateError("Automatic update checks require a 64-bit application runtime")
    machine = platform.machine().casefold()
    if machine in {"arm64", "aarch64"}:
        architecture = "arm64"
    elif machine in {"amd64", "x86_64"}:
        architecture = "x86_64"
    else:
        raise UpdateError("Automatic update checks do not support this CPU architecture")
    return platform_name, architecture


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _is_link_or_reparse(value: os.stat_result | Any) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(
        getattr(value, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
    )


def _plain_file_stat(path: Path, message: str) -> os.stat_result:
    try:
        value = _helper_plain_file_stat(path, "metadata_untrusted")
    except (HelperError, OSError) as exc:
        raise UpdateError(message) from exc
    return value


def _same_file(expected: os.stat_result, observed: os.stat_result) -> bool:
    try:
        same = os.path.samestat(expected, observed)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return bool(
        same
        and stat.S_ISREG(observed.st_mode)
        and getattr(observed, "st_nlink", 1) == 1
        and expected.st_size == observed.st_size
        and getattr(expected, "st_mtime_ns", None) == getattr(observed, "st_mtime_ns", None)
    )


def _metadata_error(
    message: str, *, safe_to_replace: bool = False, missing: bool = False
) -> _PersistedMetadataError:
    return _PersistedMetadataError(
        message,
        safe_to_replace=safe_to_replace,
        missing=missing,
    )


def _decode_bounded_json(raw: bytes, maximum_bytes: int, *, label: str) -> _BoundedJson:
    """Decode one bounded JSON object without exposing parser or payload detail."""

    if not isinstance(raw, bytes):
        raise _BoundedJsonError(f"{label} could not be decoded safely")
    if len(raw) > maximum_bytes:
        raise _BoundedJsonError(f"{label} exceeds the size limit")
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise _BoundedJsonError(f"{label} could not be decoded safely") from exc
    try:
        value = json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise _BoundedJsonError(f"{label} could not be decoded safely") from exc
    if not isinstance(value, dict):
        raise _BoundedJsonError(f"{label} must be a JSON object")
    return _BoundedJson(raw, cast(dict[str, Any], value))


def _read_bounded_json(path: Path, maximum_bytes: int, *, label: str) -> _BoundedJson:
    """Read one stable, plain-file JSON object with a limit+1 sentinel read.

    The recovery helper owns the platform-specific plain-path primitives.  The
    updater adds the file-descriptor identity and post-read checks here so a
    growth or replacement race cannot turn an authority read into a stable
    value merely because the initial ``lstat`` looked safe.
    """

    try:
        before = _helper_plain_file_stat_if_present(path, "metadata_untrusted")
    except HelperError as exc:
        raise _metadata_error(f"{label} is not a trusted plain file") from exc
    if before is None:
        raise _metadata_error(f"{label} is unavailable", safe_to_replace=True, missing=True)
    if before.st_size > maximum_bytes:
        raise _metadata_error(f"{label} exceeds the size limit", safe_to_replace=True)

    try:
        with path.open("rb") as stream:
            raw = stream.read(maximum_bytes + 1)
            if len(raw) > maximum_bytes:
                raise _metadata_error(f"{label} exceeds the size limit")
            opened = os.fstat(stream.fileno())
            if not _same_file(before, opened):
                raise _metadata_error(f"{label} changed while it was read")
            observed = os.fstat(stream.fileno())
            if not _same_file(before, observed):
                raise _metadata_error(f"{label} changed while it was read")
    except _PersistedMetadataError:
        raise
    except OSError as exc:
        raise _metadata_error(f"{label} could not be read safely") from exc

    try:
        after = _helper_plain_file_stat(path, "metadata_untrusted")
    except HelperError as exc:
        raise _metadata_error(f"{label} changed while it was read") from exc
    if not _same_file(before, after):
        raise _metadata_error(f"{label} changed while it was read")
    try:
        return _decode_bounded_json(raw, maximum_bytes, label=label)
    except _BoundedJsonError as exc:
        raise _metadata_error(str(exc), safe_to_replace=True) from exc


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    """Persist updater metadata without replacing an unsafe target or parent."""

    try:
        _helper_atomic_json(path, dict(value), boundary_code="metadata_write_untrusted")
    except HelperError as exc:
        raise UpdateError("Persisted update metadata could not be saved safely") from exc
    except OSError as exc:
        raise UpdateError("Persisted update metadata could not be saved safely") from exc


def _prepare_plain_directory(path: Path, message: str) -> None:
    try:
        _helper_prepare_plain_directory_chain(path, "metadata_write_untrusted")
    except (HelperError, OSError) as exc:
        raise UpdateError(message) from exc


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.normcase(os.path.abspath(os.fspath(left))) == os.path.normcase(
            os.path.abspath(os.fspath(right))
        )
    except (OSError, TypeError, ValueError):
        return False


def _within_path(path: Path, root: Path) -> bool:
    try:
        path_value = os.path.normcase(os.path.abspath(os.fspath(path)))
        root_value = os.path.normcase(os.path.abspath(os.fspath(root)))
        return os.path.commonpath((path_value, root_value)) == root_value
    except (OSError, TypeError, ValueError):
        return False


def _unlink_plain_file(path: Path, message: str) -> None:
    try:
        parent_before = _plain_directory_stat_if_present(path.parent)
        if parent_before is None:
            return
        target_before = _helper_plain_file_stat_if_present(path, "metadata_untrusted")
        if target_before is None:
            parent_after = _plain_directory_stat_if_present(path.parent)
            if parent_after is None or not _same_directory(parent_before, parent_after):
                raise HelperError("metadata_untrusted")
            return
        if not _unlink_owned_entry(
            path,
            parent_expected=parent_before,
            target_expected=target_before,
        ):
            raise HelperError("metadata_untrusted")
    except (HelperError, OSError) as exc:
        raise UpdateError(message) from exc


def _plain_directory_stat_if_present(path: Path) -> os.stat_result | None:
    try:
        if not _helper_plain_directory_chain_if_present(path, "metadata_untrusted"):
            return None
        value = path.lstat()
    except FileNotFoundError:
        return None
    except (HelperError, OSError):
        raise
    if _is_link_or_reparse(value) or not stat.S_ISDIR(value.st_mode):
        raise HelperError("metadata_untrusted")
    return value


def _same_directory(expected: os.stat_result, observed: os.stat_result) -> bool:
    try:
        same = os.path.samestat(expected, observed)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return bool(same and stat.S_ISDIR(observed.st_mode) and not _is_link_or_reparse(observed))


def _same_unlink_entry(expected: os.stat_result, observed: os.stat_result) -> bool:
    try:
        same = os.path.samestat(expected, observed)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return bool(
        same
        and stat.S_IFMT(expected.st_mode) == stat.S_IFMT(observed.st_mode)
        and _is_link_or_reparse(expected) == _is_link_or_reparse(observed)
        and getattr(expected, "st_nlink", 1) == getattr(observed, "st_nlink", 1)
        and expected.st_size == observed.st_size
        and getattr(expected, "st_mtime_ns", None) == getattr(observed, "st_mtime_ns", None)
    )


def _unlink_owned_entry(
    path: Path,
    *,
    parent_expected: os.stat_result,
    target_expected: os.stat_result,
) -> bool:
    """Unlink one entry only while its plain parent and identity remain stable."""

    try:
        parent_before = _plain_directory_stat_if_present(path.parent)
        target_before = path.lstat()
        if (
            parent_before is None
            or not _same_directory(parent_expected, parent_before)
            or not _same_unlink_entry(target_expected, target_before)
        ):
            return False
        parent_after = _plain_directory_stat_if_present(path.parent)
        target_after = path.lstat()
        if (
            parent_after is None
            or not _same_directory(parent_expected, parent_after)
            or not _same_unlink_entry(target_expected, target_after)
        ):
            return False
        parent_final = _plain_directory_stat_if_present(path.parent)
        target_final = path.lstat()
        if (
            parent_final is None
            or not _same_directory(parent_expected, parent_final)
            or not _same_unlink_entry(target_expected, target_final)
        ):
            return False
        path.unlink()
        return True
    except (HelperError, OSError):
        return False


def _rmdir_owned_directory(
    path: Path,
    *,
    parent_expected: os.stat_result,
    directory_expected: os.stat_result,
) -> bool:
    """Remove one empty directory only while its parent and identity are stable."""

    try:
        parent_before = _plain_directory_stat_if_present(path.parent)
        directory_before = _plain_directory_stat_if_present(path)
        if (
            parent_before is None
            or not _same_directory(parent_expected, parent_before)
            or directory_before is None
            or not _same_directory(directory_expected, directory_before)
        ):
            return False
        parent_after = _plain_directory_stat_if_present(path.parent)
        directory_after = _plain_directory_stat_if_present(path)
        if (
            parent_after is None
            or not _same_directory(parent_expected, parent_after)
            or directory_after is None
            or not _same_directory(directory_expected, directory_after)
        ):
            return False
        parent_final = _plain_directory_stat_if_present(path.parent)
        directory_final = _plain_directory_stat_if_present(path)
        if (
            parent_final is None
            or not _same_directory(parent_expected, parent_final)
            or directory_final is None
            or not _same_directory(directory_expected, directory_final)
        ):
            return False
        path.rmdir()
        return True
    except (HelperError, OSError):
        return False


def _plan_owned_tree(
    path: Path,
    *,
    expected: os.stat_result | None,
    budget: _CleanupBudget,
    actions: list[_CleanupAction],
    root_depth: int,
    count_root: bool,
) -> bool:
    """Plan a bounded post-order cleanup without mutating the filesystem."""

    try:
        current = _plain_directory_stat_if_present(path)
        if current is None:
            return True
        if expected is not None and not _same_directory(expected, current):
            return False
        if _is_link_or_reparse(current) or not stat.S_ISDIR(current.st_mode):
            return False
        if count_root and not budget.reserve(root_depth):
            return False
        parent_stat = _plain_directory_stat_if_present(path.parent)
        if parent_stat is None:
            return False
        stack: list[tuple[Path, os.stat_result, os.stat_result, int, Iterator[Path]]] = [
            (path, current, parent_stat, root_depth, iter(path.iterdir()))
        ]
        while stack:
            directory, directory_stat, parent_stat, depth, children = stack[-1]
            current_directory = _plain_directory_stat_if_present(directory)
            if current_directory is None or not _same_directory(directory_stat, current_directory):
                return False
            try:
                child = next(children)
            except StopIteration:
                actions.append(
                    _CleanupAction(
                        directory,
                        parent_stat,
                        directory_stat,
                        remove_directory=True,
                    )
                )
                stack.pop()
                continue
            child_stat = child.lstat()
            child_depth = depth + 1
            if _is_link_or_reparse(child_stat) or (
                stat.S_ISREG(child_stat.st_mode) and getattr(child_stat, "st_nlink", 1) == 1
            ):
                if not budget.reserve(child_depth):
                    return False
                actions.append(
                    _CleanupAction(
                        child,
                        directory_stat,
                        child_stat,
                        remove_directory=False,
                    )
                )
            elif stat.S_ISDIR(child_stat.st_mode):
                if not budget.reserve(child_depth):
                    return False
                child_parent_stat = directory_stat
                stack.append(
                    (
                        child,
                        child_stat,
                        child_parent_stat,
                        child_depth,
                        iter(child.iterdir()),
                    )
                )
            else:
                return False
        return True
    except (HelperError, OSError, RecursionError):
        return False


def _cleanup_action_matches(action: _CleanupAction) -> bool:
    try:
        parent = _plain_directory_stat_if_present(action.path.parent)
        if parent is None or not _same_directory(action.parent_expected, parent):
            return False
        if action.remove_directory:
            target = _plain_directory_stat_if_present(action.path)
            return target is not None and _same_directory(action.target_expected, target)
        return _same_unlink_entry(action.target_expected, action.path.lstat())
    except (HelperError, OSError, RecursionError):
        return False


def _apply_cleanup_actions(actions: list[_CleanupAction]) -> bool:
    """Apply a preflighted post-order cleanup plan with final identity checks."""

    try:
        if not all(_cleanup_action_matches(action) for action in actions):
            return False
        for action in actions:
            if action.remove_directory:
                if not _rmdir_owned_directory(
                    action.path,
                    parent_expected=action.parent_expected,
                    directory_expected=action.target_expected,
                ):
                    return False
            elif not _unlink_owned_entry(
                action.path,
                parent_expected=action.parent_expected,
                target_expected=action.target_expected,
            ):
                return False
        return True
    except (HelperError, OSError, RecursionError):
        return False


def _remove_owned_tree(
    path: Path,
    *,
    expected: os.stat_result | None = None,
) -> bool:
    """Remove a private tree under global entry and depth budgets."""

    budget = _CleanupBudget()
    actions: list[_CleanupAction] = []
    if not _plan_owned_tree(
        path,
        expected=expected,
        budget=budget,
        actions=actions,
        root_depth=0,
        count_root=False,
    ):
        return False
    return _apply_cleanup_actions(actions)


def _hash_stable_file(path: Path, *, maximum_bytes: int) -> tuple[str, int]:
    before = _plain_file_stat(path, "The verified update artifact is not a plain file")
    if before.st_size > maximum_bytes:
        raise UpdateError("The verified update artifact exceeds the safety limit")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            if not _same_file(before, os.fstat(stream.fileno())):
                raise UpdateError("The verified update artifact changed while it was checked")
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(chunk)
                if size > maximum_bytes:
                    raise UpdateError("The verified update artifact exceeds the safety limit")
                digest.update(chunk)
            if not _same_file(before, os.fstat(stream.fileno())):
                raise UpdateError("The verified update artifact changed while it was checked")
    except OSError as exc:
        raise UpdateError("The verified update artifact is unreadable") from exc
    after = _plain_file_stat(path, "The verified update artifact changed while it was checked")
    if not _same_file(before, after):
        raise UpdateError("The verified update artifact changed while it was checked")
    return digest.hexdigest(), size


class UpdateTransport(Protocol):
    def get_bytes(self, url: str, *, maximum_bytes: int) -> bytes: ...

    def stream(
        self,
        url: str,
        target: Path,
        *,
        expected_bytes: int,
        cancelled: Callable[[], bool],
    ) -> tuple[str, int]: ...


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class HttpsTransport:
    """HTTPS-only transport with bounded bodies and a pinned release-asset redirect."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect())

    @staticmethod
    def _request(url: str, *, redirected_release_asset: bool = False) -> urllib.request.Request:
        parsed = urlsplit(url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise UpdateError("Update endpoint has an invalid network port") from exc
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise UpdateError("Update endpoint must be HTTPS without embedded credentials")
        if redirected_release_asset:
            if (
                parsed.hostname != "release-assets.githubusercontent.com"
                or port not in {None, 443}
                or not parsed.path.startswith("/github-production-release-asset/")
                or not parsed.query
                or parsed.fragment
            ):
                raise UpdateError("Release download redirect was refused")
            return urllib.request.Request(url, headers={"User-Agent": "AllTheContext-Updater/1"})
        lowered_path = parsed.path.casefold()
        if (
            parsed.query
            or parsed.fragment
            or "/main/" in lowered_path
            or "/latest/" in lowered_path
        ):
            raise UpdateError(
                "Update endpoint must be immutable and cannot reference main or latest"
            )
        return urllib.request.Request(url, headers={"User-Agent": "AllTheContext-Updater/1"})

    @staticmethod
    def _release_asset_redirect(source_url: str, location: str | None) -> str:
        source = urlsplit(source_url)
        try:
            source_port = source.port
        except ValueError as exc:
            raise UpdateError("Release download redirect was refused") from exc
        parts = source.path.split("/")
        if (
            source.scheme != "https"
            or source.hostname != "github.com"
            or source_port not in {None, 443}
            or source.query
            or source.fragment
            or len(parts) != 7
            or parts[0] != ""
            or parts[3:5] != ["releases", "download"]
            or any(not part for part in (parts[1], parts[2], parts[5], parts[6]))
            or parts[5].casefold() == "latest"
            or not location
        ):
            raise UpdateError("Release download redirect was refused")
        redirected = urljoin(source_url, location)
        try:
            HttpsTransport._request(redirected, redirected_release_asset=True)
        except UpdateError as exc:
            raise UpdateError("Release download redirect was refused") from exc
        return redirected

    def _open(self, url: str, *, allow_release_redirect: bool = False) -> Any:
        current_url = url
        redirect_count = 0
        while True:
            try:
                response = self._opener.open(
                    self._request(
                        current_url,
                        redirected_release_asset=redirect_count > 0,
                    ),
                    timeout=CONNECT_TIMEOUT_SECONDS,
                )
                # urllib uses the socket timeout for subsequent reads as well.
                raw = getattr(response, "fp", None)
                socket = getattr(getattr(raw, "raw", None), "_sock", None)
                if socket is not None:
                    socket.settimeout(READ_TIMEOUT_SECONDS)
                return response
            except urllib.error.HTTPError as exc:
                if 300 <= exc.code < 400:
                    if (
                        not allow_release_redirect
                        or exc.code not in {302, 307, 308}
                        or redirect_count >= MAX_REDIRECTS
                    ):
                        raise UpdateError("Update endpoint redirect was refused") from exc
                    headers = exc.headers
                    current_url = self._release_asset_redirect(
                        url, headers.get("Location") if headers is not None else None
                    )
                    redirect_count += 1
                    continue
                raise UpdateEndpointHttpError(exc.code) from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise UpdateError(
                    "Update endpoint could not be reached within the time limit"
                ) from exc

    @staticmethod
    def _content_length(headers: Any) -> int | None:
        declared = headers.get("Content-Length")
        if declared is None:
            return None
        try:
            value = int(declared)
        except (TypeError, ValueError, OverflowError) as exc:
            raise UpdateError("Update endpoint returned an invalid Content-Length") from exc
        if value < 0:
            raise UpdateError("Update endpoint returned an invalid Content-Length")
        return value

    def get_bytes(self, url: str, *, maximum_bytes: int) -> bytes:
        with self._open(url) as response:
            declared = self._content_length(response.headers)
            if declared is not None and declared > maximum_bytes:
                raise UpdateError("Update metadata exceeds the size limit")
            value = response.read(maximum_bytes + 1)
        if len(value) > maximum_bytes:
            raise UpdateError("Update metadata exceeds the size limit")
        return cast(bytes, value)

    def stream(
        self,
        url: str,
        target: Path,
        *,
        expected_bytes: int,
        cancelled: Callable[[], bool],
    ) -> tuple[str, int]:
        if expected_bytes <= 0 or expected_bytes > MAX_ARTIFACT_BYTES:
            raise UpdateError("Release artifact declares an unsupported size")
        digest = hashlib.sha256()
        received = 0
        try:
            with (
                self._open(url, allow_release_redirect=True) as response,
                target.open("xb") as output,
            ):
                declared = self._content_length(response.headers)
                if declared is not None and declared != expected_bytes:
                    raise UpdateError("Release download length differs from signed metadata")
                while True:
                    if cancelled():
                        raise UpdateError("Update download was cancelled")
                    chunk = response.read(min(1024 * 1024, expected_bytes - received + 1))
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected_bytes:
                        raise UpdateError("Release download exceeded its signed size")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if received != expected_bytes:
                raise UpdateError("Release download was truncated")
            return digest.hexdigest(), received
        except BaseException:
            with suppress(OSError, UpdateError):
                _unlink_plain_file(target, "The partial release artifact is not a plain file")
            raise


class Installer(Protocol):
    @property
    def supported(self) -> bool: ...

    @property
    def unsupported_reason(self) -> str: ...

    def preflight(self, artifact: Path, required_bytes: int) -> None: ...

    def handoff(self, plan: InstallPlan) -> None: ...

    def rollback(self, state: UpdateState) -> None: ...

    def recovery_outcome(self, state: UpdateState) -> str | None: ...


class HealthProbe(Protocol):
    def healthy(self) -> bool: ...


class LoopbackHealthProbe:
    """Bounded post-restart proof that the local Core is serving again."""

    def __init__(self, host: str = "127.0.0.1", port: int = 7337) -> None:
        self.url = f"http://{host}:{port}/health"

    def healthy(self) -> bool:
        try:
            request = urllib.request.Request(self.url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=3) as response:
                if cast(int, response.status) != 200:
                    return False
                value = json.loads(response.read(4097).decode("utf-8"))
            return bool(value == {"status": "ok", "component": "core"})
        except (
            OSError,
            ValueError,
            UnicodeError,
            RecursionError,
            urllib.error.URLError,
            json.JSONDecodeError,
        ):
            return False


class PlatformInstaller:
    """Native handoff for the artifact forms the project can safely apply."""

    def __init__(
        self,
        *,
        system: str | None = None,
        frozen: bool | None = None,
        application_path: Path | None = None,
        helper_path: Path | None = None,
        mcp_path: Path | None = None,
        recovery_path: Path | None = None,
    ) -> None:
        self.system = system or platform.system()
        self.frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        runtime = RuntimeCommand.current()
        self.application_path = (application_path or runtime.executable).resolve()
        self.helper_path = helper_path or runtime.update_executable
        self.mcp_path = mcp_path or self.application_path.with_name("AllTheContextMCP.exe")
        self.recovery_path = recovery_path or self.application_path.with_name(
            "AllTheContextRecovery.exe"
        )
        self.stable_update_helper_path = self.application_path.with_name("AllTheContextUpdater.exe")

    @property
    def supported(self) -> bool:
        return bool(
            self.system == "Windows"
            and self.frozen
            and self.application_path.is_file()
            and self.helper_path is not None
            and self.helper_path.is_file()
            and self.stable_update_helper_path.is_file()
            and self.recovery_path.is_file()
        )

    @property
    def unsupported_reason(self) -> str:
        if self.system == "Windows":
            if not self.frozen:
                return "Automatic Windows updates require the installed desktop application"
            if not self.recovery_path.is_file():
                return (
                    "The installed Windows recovery/admin helper is unavailable; reinstall the "
                    "current desktop package before applying updates"
                )
            return (
                "The installed Windows update helper is unavailable; reinstall the current "
                "desktop package before applying updates"
            )
        if self.system == "Darwin":
            return (
                "The verified macOS update requires a manual app replacement; this community "
                "build is not notarized"
            )
        if self.system == "Linux":
            return (
                "The verified Linux update requires a distribution-specific manual package install"
            )
        return "This platform has no safe automatic installer handoff"

    def preflight(self, artifact: Path, required_bytes: int) -> None:
        free = shutil.disk_usage(artifact.parent).free
        # Keep enough room for archive, extraction, and a retained recovery copy.
        if free < required_bytes * 3:
            raise UpdateError("Insufficient disk space to stage and recover this update")
        if self.supported and not zipfile.is_zipfile(artifact):
            raise UpdateError("The Windows release artifact is not a valid ZIP archive")

    @staticmethod
    def _extract_windows_setup(archive: Path | BinaryIO, target: Path) -> Path:
        target.mkdir(parents=True, exist_ok=False)
        setup: Path | None = None
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            expanded = 0
            for entry in entries:
                if "\\" in entry.filename or ":" in entry.filename:
                    raise UpdateError("Release archive contains an unsafe path")
                name = PurePosixPath(entry.filename)
                if name.is_absolute() or ".." in name.parts or entry.is_dir():
                    if entry.is_dir():
                        continue
                    raise UpdateError("Release archive contains an unsafe path")
                expanded += entry.file_size
                if expanded > MAX_ARTIFACT_BYTES * 2:
                    raise UpdateError("Release archive expands beyond the safety limit")
                destination = target.joinpath(*name.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, destination.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                if destination.name.casefold() == "allthecontextsetup.exe":
                    if setup is not None:
                        raise UpdateError(
                            "Release archive contains multiple Windows setup programs"
                        )
                    setup = destination
        if setup is None:
            raise UpdateError("Release archive does not contain AllTheContextSetup.exe")
        return setup

    @staticmethod
    def _hash_archive_stream(stream: BinaryIO) -> tuple[str, int]:
        stream.seek(0)
        digest = hashlib.sha256()
        size = 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_ARTIFACT_BYTES:
                raise UpdateError("Release artifact exceeded the safety limit at handoff")
            digest.update(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _copy_verified(source: Path, target: Path) -> tuple[str, int]:
        _prepare_plain_directory(
            target.parent,
            "The installed update target directory is not a trusted plain directory",
        )
        target_parent = _plain_directory_stat_if_present(target.parent)
        if target_parent is None:
            raise UpdateError("The installed update target directory is unavailable")
        temporary = target.with_name(f"{target.name}.atc-new")
        _unlink_plain_file(temporary, "The temporary installed update is not a plain file")
        source_stat = _plain_file_stat(source, "The installed update source is not trusted")
        try:
            with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
                current_parent = _plain_directory_stat_if_present(target.parent)
                if current_parent is None or not _same_directory(target_parent, current_parent):
                    raise UpdateError("The installed update target directory changed during copy")
                if not _same_file(source_stat, os.fstat(input_stream.fileno())):
                    raise UpdateError("The installed update source changed during copy")
                shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
                if not _same_file(source_stat, os.fstat(input_stream.fileno())):
                    raise UpdateError("The installed update source changed during copy")
                output_stream.flush()
                os.fsync(output_stream.fileno())
            if not _same_file(
                source_stat,
                _plain_file_stat(source, "The installed update source changed during copy"),
            ):
                raise UpdateError("The installed update source changed during copy")
            digest, size = sha256_file(temporary)
            current_parent = _plain_directory_stat_if_present(target.parent)
            if current_parent is None or not _same_directory(target_parent, current_parent):
                raise UpdateError("The installed update target directory changed during copy")
            _helper_plain_file_stat_if_present(
                target, "The installed update target is not a plain file"
            )
            temporary.replace(target)
            return digest, size
        except BaseException:
            with suppress(UpdateError):
                _unlink_plain_file(
                    temporary,
                    "The temporary installed update is not a plain file",
                )
            raise

    def handoff(self, plan: InstallPlan) -> None:
        if not self.supported:
            raise UpdateError(self.unsupported_reason)
        assert self.helper_path is not None
        if (
            not isinstance(plan.artifact_sha256, str)
            or len(plan.artifact_sha256) != 64
            or any(character not in "0123456789abcdef" for character in plan.artifact_sha256)
            or isinstance(plan.artifact_size, bool)
            or not isinstance(plan.artifact_size, int)
            or plan.artifact_size <= 0
            or plan.artifact_size > MAX_ARTIFACT_BYTES
        ):
            raise UpdateError("Verified release archive evidence is missing at handoff")
        try:
            _plain_file_stat(
                plan.artifact,
                "The verified release archive is unavailable at handoff",
            )
            with plan.artifact.open("rb") as archive_stream:
                opened_stat = os.fstat(archive_stream.fileno())
                archive_path_stat = _plain_file_stat(
                    plan.artifact,
                    "The verified release archive is unavailable at handoff",
                )
                if not _same_file(archive_path_stat, opened_stat):
                    raise UpdateError("The verified release archive changed during handoff")
                digest, size = self._hash_archive_stream(archive_stream)
                if digest != plan.artifact_sha256 or size != plan.artifact_size:
                    raise UpdateError(
                        "Release artifact checksum does not match signed metadata at handoff"
                    )
                plan.transaction_dir.mkdir(parents=True, exist_ok=False)
                setup = self._extract_windows_setup(
                    archive_stream,
                    plan.operation_dir / "extracted",
                )
                final_digest, final_size = self._hash_archive_stream(archive_stream)
                if not _same_file(opened_stat, os.fstat(archive_stream.fileno())) or not _same_file(
                    opened_stat,
                    _plain_file_stat(
                        plan.artifact,
                        "The verified release archive changed during extraction",
                    ),
                ):
                    raise UpdateError("The verified release archive changed during extraction")
                if final_digest != plan.artifact_sha256 or final_size != plan.artifact_size:
                    raise UpdateError("The verified release archive changed during extraction")
            replacement = plan.transaction_dir / "replacement" / "AllTheContextSetup.exe"
            replacement_digest, replacement_size = self._copy_verified(setup, replacement)
            rollback_application = plan.transaction_dir / "rollback" / "AllTheContext.exe"
            rollback_digest, rollback_size = self._copy_verified(
                self.application_path, rollback_application
            )
            rollback_mcp: Path | None = None
            rollback_mcp_digest: str | None = None
            rollback_mcp_size: int | None = None
            if self.mcp_path.is_file():
                rollback_mcp = plan.transaction_dir / "rollback" / "AllTheContextMCP.exe"
                rollback_mcp_digest, rollback_mcp_size = self._copy_verified(
                    self.mcp_path, rollback_mcp
                )
            rollback_recovery: Path | None = None
            rollback_recovery_digest: str | None = None
            rollback_recovery_size: int | None = None
            if self.recovery_path.is_file():
                rollback_recovery = plan.transaction_dir / "rollback" / "AllTheContextRecovery.exe"
                rollback_recovery_digest, rollback_recovery_size = self._copy_verified(
                    self.recovery_path, rollback_recovery
                )
            rollback_update_helper = plan.transaction_dir / "rollback" / "AllTheContextUpdater.exe"
            rollback_update_digest, rollback_update_size = self._copy_verified(
                self.stable_update_helper_path, rollback_update_helper
            )
            copied_helper = plan.transaction_dir / "AllTheContextUpdater.exe"
            recovery_helper_digest, recovery_helper_size = self._copy_verified(
                self.helper_path,
                copied_helper,
            )
            backup_digest, backup_size = sha256_file(plan.database_backup_path)
            journal_path = plan.transaction_dir / "journal.json"
            now = _utc_now()
            journal = UpdateJournal(
                operation_id=plan.operation_id,
                phase=HelperPhase.PREPARED,
                current_version=plan.current_version,
                target_version=plan.target_version,
                parent_pid=os.getpid(),
                application_path=str(self.application_path),
                replacement_path=str(replacement),
                replacement_sha256=replacement_digest,
                replacement_size=replacement_size,
                rollback_application_path=str(rollback_application),
                rollback_application_sha256=rollback_digest,
                rollback_application_size=rollback_size,
                mcp_path=str(self.mcp_path),
                rollback_mcp_path=str(rollback_mcp) if rollback_mcp else None,
                rollback_mcp_sha256=rollback_mcp_digest,
                rollback_mcp_size=rollback_mcp_size,
                recovery_path=str(self.recovery_path),
                rollback_recovery_path=str(rollback_recovery) if rollback_recovery else None,
                rollback_recovery_sha256=rollback_recovery_digest,
                rollback_recovery_size=rollback_recovery_size,
                stable_update_helper_path=str(self.stable_update_helper_path),
                rollback_update_helper_path=str(rollback_update_helper),
                rollback_update_helper_sha256=rollback_update_digest,
                rollback_update_helper_size=rollback_update_size,
                database_path=str(plan.database_path),
                database_backup_path=str(plan.database_backup_path),
                database_backup_sha256=backup_digest,
                database_backup_size=backup_size,
                state_path=str(plan.state_path),
                helper_path=str(copied_helper),
                core_host=plan.core_host,
                core_port=plan.core_port,
                recovery_helper_sha256=recovery_helper_digest,
                recovery_helper_size=recovery_helper_size,
                created_at=now,
                updated_at=now,
            )
            journal.validate(journal_path)
            journal.save(journal_path)
            bind_recovery_authority(journal, journal_path)
            bind_handoff_state(journal, journal_path)
            register_recovery(copied_helper, journal_path, plan.operation_id)
            launch_recovery_helper(copied_helper, journal_path)
        except (HelperError, OSError, zipfile.BadZipFile) as exc:
            raise UpdateError("The Windows recovery transaction could not be prepared") from exc

    def rollback(self, state: UpdateState) -> None:
        if state.transaction_path is None:
            raise UpdateError("The independent Windows recovery journal is unavailable")
        try:
            request_rollback(Path(state.transaction_path))
        except (HelperError, OSError) as exc:
            raise UpdateError("The independent Windows rollback could not be requested") from exc

    def recovery_outcome(self, state: UpdateState) -> str | None:
        if state.transaction_path is None:
            return None
        return transaction_outcome(Path(state.transaction_path))


class DatabaseBackup(Protocol):
    def create(self, source: Path, target: Path) -> None: ...


class SQLiteBackup:
    """Consistent, verified SQLite backup taken before native cutover."""

    def create(self, source: Path, target: Path) -> None:
        if not source.is_file():
            raise UpdateError("Core database is unavailable for the required pre-update backup")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".atc-new")
        _unlink_plain_file(temporary, "The temporary database backup is not a plain file")
        try:
            source_connection = sqlite3.connect(source, timeout=10)
            destination = sqlite3.connect(temporary)
            try:
                source_connection.execute("PRAGMA busy_timeout=10000")
                source_connection.backup(destination)
                result = destination.execute("PRAGMA quick_check").fetchone()
                if result is None or result[0] != "ok":
                    raise UpdateError("Pre-update database backup failed integrity verification")
            finally:
                destination.close()
                source_connection.close()
            temporary.replace(target)
        except BaseException:
            with suppress(UpdateError):
                _unlink_plain_file(
                    temporary,
                    "The temporary database backup is not a plain file",
                )
            raise


class UpdateManager:
    """One serialized, durable update transaction."""

    def __init__(
        self,
        config: UpdateConfig,
        *,
        database_path: Path,
        transport: UpdateTransport | None = None,
        installer: Installer | None = None,
        backup: DatabaseBackup | None = None,
        health_probe: HealthProbe | None = None,
    ) -> None:
        self.config = config
        self.database_path = database_path
        self.transport = transport or HttpsTransport()
        self.installer = installer or PlatformInstaller()
        self.backup = backup or SQLiteBackup()
        self.health_probe = health_probe or LoopbackHealthProbe()
        self._operation_gate = threading.Lock()
        self._operation_lock = threading.RLock()
        self._cancel = threading.Event()
        _prepare_plain_directory(
            self.config.data_dir,
            "The updater data directory is not a trusted plain directory",
        )
        self.preferences_path = self.config.data_dir / "preferences.json"
        self.state_path = self.config.data_dir / "state.json"
        self._preferences_write_allowed = True
        self._state_write_allowed = True
        with self._operation_lock:
            self.preferences = self._load_preferences()
            self.state = self._load_state()
            self.state.current_version = config.current_version
            self._validate_internal_state()
            if self._state_write_allowed and self.state.completed_handoff_identity is not None:
                # A failed retirement remains a valid terminal state with a
                # tombstone that the next startup can retry.
                self._clear_completed_recovery_evidence()
            if self._state_write_allowed and self._transaction_evidence_requires_preservation():
                self._state_write_allowed = False
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = (
                    "Persisted update recovery metadata was unsafe; recovery evidence was preserved"
                )
            self._normalize_unpublished_channel_state()
            self._recover_interrupted()
            if self._state_write_allowed:
                cleanup_ok = self._prune_retirement_tombstones()
                cleanup_ok = (
                    self._prune_directory(
                        self.config.data_dir / "staging", keep=self.state.operation_id
                    )
                    if cleanup_ok
                    else False
                )
                active_transaction = (
                    Path(self.state.transaction_path).parent.name
                    if self.state.transaction_path is not None
                    else None
                )
                if active_transaction is None and self.state.completed_handoff_identity is not None:
                    active_transaction = self.state.operation_id
                if cleanup_ok:
                    cleanup_ok = self._prune_directory(
                        self.config.data_dir / "transactions", keep=active_transaction
                    )
                if cleanup_ok:
                    cleanup_ok = self._prune_retirement_tombstones()
                if cleanup_ok:
                    cleanup_ok = self._prune_directory(self.config.data_dir / "exports", keep=None)
                if cleanup_ok:
                    self._save()
                else:
                    self._state_write_allowed = False
                    self.state.phase = UpdatePhase.ERROR
                    self.state.last_error = "Updater staging cleanup could not be completed safely"

    @staticmethod
    def _render_json(value: Mapping[str, Any]) -> str:
        return json.dumps(value, sort_keys=True, indent=2) + "\n"

    @staticmethod
    def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
        _atomic_json(path, value)

    def _load_preferences(self) -> UpdatePreferences:
        default_preferences = self._default_preferences()
        try:
            value = _read_bounded_json(
                self.preferences_path,
                MAX_PREFERENCES_BYTES,
                label="Persisted update preferences",
            ).value
            if not isinstance(value, dict) or not isinstance(value.get("enabled", True), bool):
                raise ValueError("invalid preferences")
            channel = value.get("channel")
            if channel not in {"stable", "beta"}:
                raise ValueError("invalid channel")
            deferred = value.get("deferred_version")
            if deferred is not None:
                if not isinstance(deferred, str):
                    raise ValueError("invalid deferred version")
                ReleaseVersion.parse(deferred)
            selected_channel = cast(Channel, channel)
            if (
                selected_channel not in self.config.manifest_urls
                and default_preferences.channel in self.config.manifest_urls
            ):
                selected_channel = default_preferences.channel
                deferred = None
            return UpdatePreferences(
                enabled=value.get("enabled", True),
                channel=selected_channel,
                deferred_version=deferred,
            )
        except _PersistedMetadataError as exc:
            self._preferences_write_allowed = exc.safe_to_replace
            return default_preferences
        except (ValueError, TypeError, KeyError):
            return default_preferences

    def _default_preferences(self) -> UpdatePreferences:
        version = ReleaseVersion.parse(self.config.current_version)
        if version.stability == 0 and "beta" in self.config.manifest_urls:
            return UpdatePreferences(channel="beta")
        return UpdatePreferences()

    def _recovery_evidence_present(self) -> bool:
        """Conservatively retain state authority when a recovery tree exists."""

        retirements = self.config.data_dir / RETIREMENT_TOMBSTONE_DIRECTORY
        try:
            if _helper_plain_directory_chain_if_present(retirements, "metadata_untrusted") and any(
                retirements.iterdir()
            ):
                return True
        except (HelperError, OSError, RecursionError):
            return True
        transactions = self.config.data_dir / "transactions"
        try:
            if not _helper_plain_directory_chain_if_present(transactions, "metadata_untrusted"):
                return False
            return any(
                _helper_transaction_entry_has_recovery_evidence(entry)
                for entry in transactions.iterdir()
            )
        except (HelperError, OSError, RecursionError):
            return True

    def _recovery_authority_present(self) -> bool:
        return (
            self.state.transaction_path is not None
            or self.state.handoff_identity is not None
            or self.state.pending_handoff_identity is not None
            or self.state.phase in {UpdatePhase.INSTALLING, UpdatePhase.RESTART_REQUIRED}
        )

    def _transaction_evidence_requires_preservation(self) -> bool:
        """Detect transaction evidence that is not covered by a completed handoff."""

        transactions = self.config.data_dir / "transactions"
        try:
            if not _helper_plain_directory_chain_if_present(transactions, "metadata_untrusted"):
                return False
            entries = list(transactions.iterdir())
        except (HelperError, OSError, RecursionError):
            return True
        evidence_entries = [
            entry for entry in entries if _helper_transaction_entry_has_recovery_evidence(entry)
        ]
        if not evidence_entries:
            return False

        allowed: Path | None = None
        if self.state.transaction_path is not None:
            allowed = Path(self.state.transaction_path).parent
        elif (
            self.state.operation_id is not None
            and self.state.phase in {UpdatePhase.INSTALLED, UpdatePhase.ROLLED_BACK}
            and (
                self._retirement_tombstone_state_matches()
                or completed_transaction_is_authoritative(
                    self.state_path,
                    {
                        **asdict(self.state),
                        "phase": self.state.phase.value,
                    },
                    self.state.phase.value,
                    validate_storage=False,
                )
            )
        ):
            operation = self.state.operation_id
            allowed = transactions / operation
        if allowed is None:
            return True
        return any(not _same_path(entry, allowed) for entry in evidence_entries)

    def _transaction_evidence_persisted(self) -> bool:
        """Treat an existing transaction file or partial transaction tree as authority."""

        transaction = self.state.transaction_path
        if transaction is None:
            return False
        path = Path(transaction)
        try:
            if _helper_plain_file_stat_if_present(path, "metadata_untrusted") is not None:
                return True
            if not _helper_plain_directory_chain_if_present(path.parent, "metadata_untrusted"):
                return False
            return _helper_transaction_entry_has_recovery_evidence(path.parent)
        except (HelperError, OSError, RecursionError):
            return True

    def _retirement_root(self) -> Path:
        return self.config.data_dir / RETIREMENT_TOMBSTONE_DIRECTORY

    def _retirement_tombstone_candidates(self, operation: str) -> list[Path]:
        root = self._retirement_root()
        if not _helper_plain_directory_chain_if_present(root, "metadata_untrusted"):
            return []
        prefix = f"{operation}-"
        return [
            entry
            for entry in root.iterdir()
            if entry.name.startswith(prefix) and entry.name.endswith(".json")
        ]

    @staticmethod
    def _retirement_tombstone_payload(value: Mapping[str, Any]) -> dict[str, Any] | None:
        fields = {
            "schema_version",
            "operation_id",
            "outcome",
            "terminal_phase",
            "handoff_identity",
            "terminal_authority_mac",
            "journal_sha256",
        }
        if set(value) != fields:
            return None
        operation = value.get("operation_id")
        outcome = value.get("outcome")
        terminal_phase = value.get("terminal_phase")
        if (
            value.get("schema_version") != RETIREMENT_TOMBSTONE_SCHEMA_VERSION
            or not isinstance(operation, str)
            or len(operation) != 24
            or any(character not in "0123456789abcdef" for character in operation)
            or outcome not in {"installed", "rolled_back"}
            or terminal_phase not in {HelperPhase.COMMITTED.value, HelperPhase.ROLLED_BACK.value}
            or not isinstance(value.get("handoff_identity"), str)
            or len(cast(str, value["handoff_identity"])) != 64
            or any(
                character not in "0123456789abcdef"
                for character in cast(str, value["handoff_identity"])
            )
            or not isinstance(value.get("terminal_authority_mac"), str)
            or len(cast(str, value["terminal_authority_mac"])) != 64
            or any(
                character not in "0123456789abcdef"
                for character in cast(str, value["terminal_authority_mac"])
            )
            or not isinstance(value.get("journal_sha256"), str)
            or len(cast(str, value["journal_sha256"])) != 64
            or any(
                character not in "0123456789abcdef"
                for character in cast(str, value["journal_sha256"])
            )
        ):
            return None
        return dict(value)

    def _load_retirement_tombstone(self, path: Path) -> tuple[dict[str, Any], bytes] | None:
        try:
            bounded = _read_bounded_json(
                path,
                MAX_RETIREMENT_TOMBSTONE_BYTES,
                label="Updater retirement evidence",
            )
        except (_PersistedMetadataError, RecursionError, TypeError, ValueError):
            return None
        payload = self._retirement_tombstone_payload(bounded.value)
        if payload is None:
            return None
        prefix = f"{payload['operation_id']}-"
        suffix = ".json"
        if not path.name.startswith(prefix) or not path.name.endswith(suffix):
            return None
        filename_digest = path.name[len(prefix) : -len(suffix)]
        if (
            len(filename_digest) != 64
            or any(character not in "0123456789abcdef" for character in filename_digest)
            or hashlib.sha256(bounded.raw).hexdigest() != filename_digest
            or bounded.raw != self._render_json(payload).encode("utf-8")
        ):
            return None
        return payload, bounded.raw

    def _retirement_tombstone_state_matches(self) -> bool:
        operation = self.state.operation_id
        identity = self.state.completed_handoff_identity
        if operation is None or self.state.phase not in {
            UpdatePhase.INSTALLED,
            UpdatePhase.ROLLED_BACK,
        }:
            return False
        try:
            candidates = self._retirement_tombstone_candidates(operation)
            if len(candidates) != 1:
                return False
            loaded = self._load_retirement_tombstone(candidates[0])
            if loaded is None:
                return False
            payload, _ = loaded
            return bool(
                (identity is None or payload["handoff_identity"] == identity)
                and payload["outcome"]
                == ("installed" if self.state.phase is UpdatePhase.INSTALLED else "rolled_back")
                and payload["terminal_phase"]
                == (
                    HelperPhase.COMMITTED.value
                    if self.state.phase is UpdatePhase.INSTALLED
                    else HelperPhase.ROLLED_BACK.value
                )
            )
        except (HelperError, OSError, RecursionError, TypeError, ValueError):
            return False

    def _build_retirement_tombstone(self) -> tuple[Path, dict[str, Any]]:
        identity = self.state.completed_handoff_identity
        operation = self.state.operation_id
        if (
            identity is None
            or operation is None
            or self.state.phase
            not in {
                UpdatePhase.INSTALLED,
                UpdatePhase.ROLLED_BACK,
            }
        ):
            raise _RetirementEvidenceError("terminal retirement state is incomplete")
        journal_path = self.config.data_dir / "transactions" / operation / "journal.json"
        try:
            bounded = _read_bounded_json(
                journal_path,
                MAX_JOURNAL_BYTES,
                label="Updater terminal recovery journal",
            )
            journal = UpdateJournal.load(journal_path, validate_storage=False)
            expected_phase = (
                HelperPhase.COMMITTED
                if self.state.phase is UpdatePhase.INSTALLED
                else HelperPhase.ROLLED_BACK
            )
            if (
                journal.operation_id != operation
                or journal.phase is not expected_phase
                or journal_handoff_identity(journal) != identity
                or not _same_path(Path(journal.state_path), self.state_path)
                or not _valid_digest(journal.terminal_authority_mac)
                or recovery_authority_retirement_status(
                    operation,
                    identity,
                    expected_phase.value,
                    cast(str, journal.terminal_authority_mac),
                )
                not in {"valid", "missing"}
            ):
                raise _RetirementEvidenceError("terminal retirement evidence is invalid")
        except _RetirementEvidenceError:
            raise
        except (HelperError, OSError, RecursionError, TypeError, ValueError, UpdateError) as exc:
            raise _RetirementEvidenceError("terminal retirement evidence is unavailable") from exc
        payload = {
            "schema_version": RETIREMENT_TOMBSTONE_SCHEMA_VERSION,
            "operation_id": operation,
            "outcome": "installed" if self.state.phase is UpdatePhase.INSTALLED else "rolled_back",
            "terminal_phase": journal.phase.value,
            "handoff_identity": identity,
            "terminal_authority_mac": journal.terminal_authority_mac,
            "journal_sha256": hashlib.sha256(bounded.raw).hexdigest(),
        }
        digest = hashlib.sha256(self._render_json(payload).encode("utf-8")).hexdigest()
        return self._retirement_root() / f"{operation}-{digest}.json", payload

    def _validate_retirement_tombstone(self, path: Path, payload: Mapping[str, Any]) -> bool:
        operation = cast(str, payload["operation_id"])
        identity = cast(str, payload["handoff_identity"])
        terminal_phase = cast(str, payload["terminal_phase"])
        status = recovery_authority_retirement_status(
            operation,
            identity,
            terminal_phase,
            cast(str, payload["terminal_authority_mac"]),
        )
        if status not in {"valid", "missing"}:
            return False
        journal_path = self.config.data_dir / "transactions" / operation / "journal.json"
        try:
            journal_stat = _helper_plain_file_stat_if_present(journal_path, "metadata_untrusted")
            if journal_stat is None:
                return True
            bounded = _read_bounded_json(
                journal_path,
                MAX_JOURNAL_BYTES,
                label="Updater terminal recovery journal",
            )
            if hashlib.sha256(bounded.raw).hexdigest() != payload["journal_sha256"]:
                return False
            journal = UpdateJournal.load(journal_path, validate_storage=False)
            return bool(
                journal.operation_id == operation
                and journal.phase.value == terminal_phase
                and journal_handoff_identity(journal) == identity
                and journal.terminal_authority_mac == payload["terminal_authority_mac"]
                and (
                    status == "missing"
                    or recovery_authority_retirement_status(
                        operation,
                        identity,
                        terminal_phase,
                        cast(str, journal.terminal_authority_mac),
                    )
                    == "valid"
                )
            )
        except (HelperError, OSError, RecursionError, TypeError, ValueError, UpdateError):
            return False

    def _ensure_retirement_tombstone(self) -> tuple[Path, dict[str, Any]]:
        operation = self.state.operation_id
        identity = self.state.completed_handoff_identity
        if operation is None or identity is None:
            raise _RetirementEvidenceError("terminal retirement state is incomplete")
        candidates = self._retirement_tombstone_candidates(operation)
        if len(candidates) > 1:
            raise _RetirementEvidenceError("multiple terminal retirement records exist")
        if candidates:
            loaded = self._load_retirement_tombstone(candidates[0])
            if loaded is None:
                raise _RetirementEvidenceError("terminal retirement record is invalid")
            payload, _ = loaded
            expected_outcome = (
                "installed" if self.state.phase is UpdatePhase.INSTALLED else "rolled_back"
            )
            expected_phase = (
                HelperPhase.COMMITTED.value
                if expected_outcome == "installed"
                else HelperPhase.ROLLED_BACK.value
            )
            if (
                payload["operation_id"] != operation
                or payload["outcome"] != expected_outcome
                or payload["terminal_phase"] != expected_phase
                or payload["handoff_identity"] != identity
                or not self._validate_retirement_tombstone(candidates[0], payload)
            ):
                raise _RetirementEvidenceError("terminal retirement record does not match state")
            return candidates[0], payload
        path, payload = self._build_retirement_tombstone()
        _atomic_json(path, payload)
        loaded = self._load_retirement_tombstone(path)
        if loaded is None or not self._validate_retirement_tombstone(path, loaded[0]):
            raise _RetirementEvidenceError("terminal retirement record could not be verified")
        return path, loaded[0]

    @staticmethod
    def _remove_retirement_tombstone(path: Path) -> bool:
        try:
            parent = _plain_directory_stat_if_present(path.parent)
            if parent is None:
                return True
            target = _helper_plain_file_stat_if_present(path, "metadata_untrusted")
            if target is None:
                return True
            return _unlink_owned_entry(
                path,
                parent_expected=parent,
                target_expected=target,
            )
        except (HelperError, OSError, RecursionError):
            return False

    def _retirement_cleanup_failed(self, *, invalid: bool = False) -> bool:
        if invalid:
            self._state_write_allowed = False
            self.state.phase = UpdatePhase.ERROR
            self.state.last_error = RECOVERY_EVIDENCE_INCOMPLETE_ERROR
            return False
        self.state.last_error = "Updater staging cleanup could not be completed safely"
        try:
            self._save()
        except UpdateError:
            self._state_write_allowed = False
        return False

    def _clear_completed_recovery_evidence(self) -> bool:
        """Retire terminal evidence through a crash-replayable tombstone."""

        identity = self.state.completed_handoff_identity
        operation = self.state.operation_id
        if identity is None or operation is None or self.state.transaction_path is not None:
            return True
        try:
            tombstone_path, tombstone = self._ensure_retirement_tombstone()
        except _RetirementEvidenceError:
            return self._retirement_cleanup_failed(invalid=True)
        except (HelperError, OSError, RecursionError, TypeError, ValueError, UpdateError):
            return self._retirement_cleanup_failed()

        operation_dir = self.config.data_dir / "staging" / operation
        transaction_dir = self.config.data_dir / "transactions" / operation
        cleanup_ok = True
        try:
            operation_expected = _plain_directory_stat_if_present(operation_dir)
            if operation_expected is not None and not _remove_owned_tree(
                operation_dir, expected=operation_expected
            ):
                cleanup_ok = False
        except (HelperError, OSError, RecursionError):
            cleanup_ok = False
        try:
            transaction_expected = _plain_directory_stat_if_present(transaction_dir)
            if transaction_expected is not None and not _remove_owned_tree(
                transaction_dir, expected=transaction_expected
            ):
                cleanup_ok = False
        except (HelperError, OSError, RecursionError):
            cleanup_ok = False

        authority_status = recovery_authority_retirement_status(
            operation,
            cast(str, tombstone["handoff_identity"]),
            cast(str, tombstone["terminal_phase"]),
            cast(str, tombstone["terminal_authority_mac"]),
        )
        authority_ok = authority_status == "missing"
        if authority_status == "valid":
            authority_ok = retire_recovery_authority(operation)
        elif authority_status not in {"valid", "missing"}:
            return self._retirement_cleanup_failed(invalid=True)
        if not cleanup_ok or not authority_ok:
            return self._retirement_cleanup_failed()

        self.state.completed_handoff_identity = None
        try:
            self._save()
        except UpdateError:
            self.state.completed_handoff_identity = identity
            return self._retirement_cleanup_failed()
        # State is cleared before this unlink.  A crash here leaves only an
        # authenticated, already-retired tombstone for the next startup prune.
        self._remove_retirement_tombstone(tombstone_path)
        return True

    def _prune_retirement_tombstones(self) -> bool:
        """Reap only authenticated, already-retired orphan tombstones."""

        root = self._retirement_root()
        try:
            if not _helper_plain_directory_chain_if_present(root, "metadata_untrusted"):
                return True
            entries = list(root.iterdir())
        except (HelperError, OSError, RecursionError):
            return False
        pending_operation = (
            self.state.operation_id if self.state.completed_handoff_identity is not None else None
        )
        for entry in entries:
            if pending_operation is not None and entry.name.startswith(f"{pending_operation}-"):
                continue
            loaded = self._load_retirement_tombstone(entry)
            if loaded is None:
                return False
            payload, _ = loaded
            operation = cast(str, payload["operation_id"])
            transaction_dir = self.config.data_dir / "transactions" / operation
            try:
                if _plain_directory_stat_if_present(transaction_dir) is not None:
                    continue
            except (HelperError, OSError, RecursionError):
                return False
            status = recovery_authority_retirement_status(
                operation,
                cast(str, payload["handoff_identity"]),
                cast(str, payload["terminal_phase"]),
                cast(str, payload["terminal_authority_mac"]),
            )
            if status == "valid":
                if not retire_recovery_authority(operation):
                    continue
            elif status != "missing":
                return False
            if not self._remove_retirement_tombstone(entry):
                return False
        return True

    def _load_state(self) -> UpdateState:
        try:
            value = _read_bounded_json(
                self.state_path,
                MAX_STATE_BYTES,
                label="Persisted update state",
            ).value
            if not isinstance(value, dict):
                raise ValueError("invalid state")
            value["phase"] = UpdatePhase(value["phase"])
            allowed = set(UpdateState.__dataclass_fields__)
            state = UpdateState(**{key: item for key, item in value.items() if key in allowed})
            optional_strings = (
                state.offered_version,
                state.release_notes_url,
                state.downloaded_path,
                state.backup_path,
                state.last_checked_at,
                state.last_error,
                state.operation_id,
                state.transaction_path,
                state.manifest_identity,
                state.handoff_identity,
                state.pending_handoff_identity,
                state.completed_handoff_identity,
            )
            if any(item is not None and not isinstance(item, str) for item in optional_strings):
                raise ValueError("invalid state string")
            state.last_error = _sanitize_persisted_error(state.last_error)
            if not isinstance(state.current_version, str):
                raise ValueError("invalid current version")
            ReleaseVersion.parse(state.current_version)
            if state.offered_version is not None:
                ReleaseVersion.parse(state.offered_version)
            if not isinstance(state.mandatory, bool):
                raise ValueError("invalid mandatory flag")
            if (
                isinstance(state.recovery_attempts, bool)
                or not isinstance(state.recovery_attempts, int)
                or state.recovery_attempts < 0
            ):
                raise ValueError("invalid recovery attempts")
            if state.operation_id is not None and (
                len(state.operation_id) != 24
                or any(character not in "0123456789abcdef" for character in state.operation_id)
            ):
                raise ValueError("invalid operation ID")
            if state.manifest_identity is not None and (
                len(state.manifest_identity) != 64
                or any(character not in "0123456789abcdef" for character in state.manifest_identity)
            ):
                raise ValueError("invalid manifest identity")
            for identity in (
                state.handoff_identity,
                state.pending_handoff_identity,
                state.completed_handoff_identity,
            ):
                if identity is not None and (
                    len(identity) != 64
                    or any(character not in "0123456789abcdef" for character in identity)
                ):
                    raise ValueError("invalid handoff identity")
            return state
        except _PersistedMetadataError as exc:
            if self._recovery_evidence_present():
                self._state_write_allowed = False
                return UpdateState(
                    phase=UpdatePhase.ERROR,
                    current_version=self.config.current_version,
                    last_error=(
                        "Persisted update state could not be read safely; recovery evidence "
                        "was preserved"
                    ),
                )
            self._state_write_allowed = exc.safe_to_replace
            if exc.missing:
                return UpdateState(current_version=self.config.current_version)
            if exc.safe_to_replace:
                return UpdateState(
                    phase=UpdatePhase.ERROR,
                    current_version=self.config.current_version,
                    last_error="Persisted update state was corrupt and was reset safely",
                )
            return UpdateState(
                phase=UpdatePhase.ERROR,
                current_version=self.config.current_version,
                last_error="Persisted update state could not be read safely",
            )
        except (ValueError, TypeError, KeyError):
            if self._recovery_evidence_present():
                self._state_write_allowed = False
                return UpdateState(
                    phase=UpdatePhase.ERROR,
                    current_version=self.config.current_version,
                    last_error=(
                        "Persisted update state was invalid; recovery evidence was preserved"
                    ),
                )
            return UpdateState(
                phase=UpdatePhase.ERROR,
                current_version=self.config.current_version,
                last_error="Persisted update state was corrupt and was reset safely",
            )

    def _active_recovery_metadata_complete(self) -> bool:
        if not self._recovery_authority_present():
            return True
        required: list[str | None] = [
            self.state.offered_version,
            self.state.release_notes_url,
            self.state.backup_path,
            self.state.operation_id,
            self.state.transaction_path,
            self.state.manifest_identity,
        ]
        if self.state.phase not in {UpdatePhase.INSTALLED, UpdatePhase.ROLLED_BACK}:
            required.append(self.state.downloaded_path)
        return all(value is not None for value in required)

    def _active_recovery_evidence_complete(self) -> bool:
        """Require every active recovery reference to resolve to its owned evidence."""

        if not self._recovery_authority_present():
            return not self._transaction_evidence_requires_preservation()
        operation = self.state.operation_id
        if operation is None:
            return False
        try:
            operation_dir = self.config.data_dir / "staging" / operation
            expected_artifact = operation_dir / "artifact.zip"
            backup_root = self.config.data_dir / "backups"
            backup_path = Path(self.state.backup_path or "")
            transaction_root = self.config.data_dir / "transactions"
            expected_transaction = transaction_root / operation / "journal.json"
            if self.state.phase not in {
                UpdatePhase.INSTALLED,
                UpdatePhase.ROLLED_BACK,
            } and not _same_path(Path(self.state.downloaded_path or ""), expected_artifact):
                return False
            if not _same_path(backup_path.parent, backup_root):
                return False
            if not _same_path(Path(self.state.transaction_path or ""), expected_transaction):
                return False
            operation_stat = _plain_directory_stat_if_present(operation_dir)
            transaction_dir_stat = _plain_directory_stat_if_present(expected_transaction.parent)
            artifact_stat = _helper_plain_file_stat_if_present(
                expected_artifact,
                "metadata_untrusted",
            )
            backup_stat = _helper_plain_file_stat_if_present(backup_path, "metadata_untrusted")
            transaction_stat = _helper_plain_file_stat_if_present(
                expected_transaction,
                "metadata_untrusted",
            )
            if transaction_stat is None or transaction_stat.st_size <= 0:
                return False
            journal = UpdateJournal.load(expected_transaction, validate_storage=False)
            validate_recovery_authority(
                journal,
                expected_transaction,
                require_terminal=self.state.phase
                in {UpdatePhase.INSTALLED, UpdatePhase.ROLLED_BACK},
            )
            journal_identity = journal_handoff_identity(journal)
            if (
                journal.operation_id != operation
                or self.config.current_version
                not in {journal.current_version, journal.target_version}
                or self.state.offered_version != journal.target_version
                or not _same_path(Path(journal.state_path), self.state_path)
                or not _same_path(Path(journal.database_path), self.database_path)
                or not _same_path(Path(journal.database_backup_path), backup_path)
                or not _same_path(Path(journal.helper_path).parent, expected_transaction.parent)
                or self.state.completed_handoff_identity is not None
                or (
                    self.state.handoff_identity is None
                    and self.state.pending_handoff_identity is None
                )
                or self.state.handoff_identity not in {None, journal_identity}
                or self.state.pending_handoff_identity not in {None, journal_identity}
                or (
                    self.state.phase
                    in {
                        UpdatePhase.INSTALLING,
                        UpdatePhase.RESTART_REQUIRED,
                        UpdatePhase.ERROR,
                    }
                    and journal.phase in {HelperPhase.COMMITTED, HelperPhase.ROLLED_BACK}
                )
                or (
                    self.state.phase is UpdatePhase.INSTALLED
                    and journal.phase is not HelperPhase.COMMITTED
                )
                or (
                    self.state.phase is UpdatePhase.ROLLED_BACK
                    and journal.phase is not HelperPhase.ROLLED_BACK
                )
            ):
                return False
            if self.state.phase in {UpdatePhase.INSTALLED, UpdatePhase.ROLLED_BACK} and (
                transaction_outcome(expected_transaction, validate_storage=False)
                != ("installed" if self.state.phase is UpdatePhase.INSTALLED else "rolled_back")
            ):
                return False
        except (HelperError, OSError, RecursionError, TypeError, ValueError):
            return False
        return bool(
            operation_stat is not None
            and transaction_dir_stat is not None
            and artifact_stat is not None
            and artifact_stat.st_size > 0
            and backup_stat is not None
            and backup_stat.st_size > 0
            and transaction_stat is not None
            and transaction_stat.st_size > 0
        )

    def _validate_internal_state(self) -> None:
        invalid = False
        recovery_authority_fields_present = (
            self.state.transaction_path is not None
            or self.state.handoff_identity is not None
            or self.state.pending_handoff_identity is not None
        )
        active_recovery = recovery_authority_fields_present or self.state.phase in {
            UpdatePhase.INSTALLING,
            UpdatePhase.RESTART_REQUIRED,
        }

        def preserve_recovery_authority(message: str) -> None:
            self._state_write_allowed = False
            self.state.phase = UpdatePhase.ERROR
            self.state.last_error = message

        if not self._active_recovery_metadata_complete():
            preserve_recovery_authority(RECOVERY_EVIDENCE_INCOMPLETE_ERROR)
            return
        if not self._active_recovery_evidence_complete():
            preserve_recovery_authority(RECOVERY_EVIDENCE_INCOMPLETE_ERROR)
            return
        if recovery_authority_fields_present and self.state.phase not in {
            UpdatePhase.INSTALLING,
            UpdatePhase.RESTART_REQUIRED,
            UpdatePhase.INSTALLED,
            UpdatePhase.ROLLED_BACK,
        }:
            preserve_recovery_authority(
                "Persisted update recovery metadata was unsafe; recovery evidence was preserved"
            )
            return

        if (
            self.state.release_notes_url is not None
            and _safe_public_url(self.state.release_notes_url) is None
        ):
            if active_recovery:
                preserve_recovery_authority(RECOVERY_EVIDENCE_INCOMPLETE_ERROR)
                return
            self.state.release_notes_url = None
            invalid = True

        operation = self.state.operation_id
        expected_artifact = (
            self.config.data_dir / "staging" / operation / "artifact.zip"
            if operation is not None
            else None
        )
        if self.state.downloaded_path is not None:
            downloaded_path_valid = expected_artifact is not None and _same_path(
                Path(self.state.downloaded_path), expected_artifact
            )
            if downloaded_path_valid and expected_artifact is not None:
                try:
                    _helper_plain_file_stat_if_present(expected_artifact, "metadata_untrusted")
                except HelperError:
                    downloaded_path_valid = False
            if not downloaded_path_valid:
                if active_recovery:
                    preserve_recovery_authority(RECOVERY_EVIDENCE_INCOMPLETE_ERROR)
                    return
                self.state.downloaded_path = None
                invalid = True
        if self.state.backup_path is not None:
            backup_root = self.config.data_dir / "backups"
            backup_path = Path(self.state.backup_path)
            backup_valid = _within_path(backup_path, backup_root)
            if backup_valid:
                try:
                    _helper_plain_file_stat_if_present(backup_path, "metadata_untrusted")
                except HelperError:
                    backup_valid = False
            if not backup_valid:
                if active_recovery:
                    preserve_recovery_authority(RECOVERY_EVIDENCE_INCOMPLETE_ERROR)
                    return
                self.state.backup_path = None
                invalid = True
        if self.state.transaction_path is not None:
            transaction_root = self.config.data_dir / "transactions"
            expected_transaction = (
                transaction_root / operation / "journal.json" if operation is not None else None
            )
            transaction_valid = expected_transaction is not None and _same_path(
                Path(self.state.transaction_path), expected_transaction
            )
            if transaction_valid and expected_transaction is not None:
                try:
                    _helper_plain_file_stat_if_present(expected_transaction, "metadata_untrusted")
                except HelperError:
                    transaction_valid = False
            if not transaction_valid:
                if active_recovery:
                    preserve_recovery_authority(RECOVERY_EVIDENCE_INCOMPLETE_ERROR)
                    return
                self.state.transaction_path = None
                self.state.handoff_identity = None
                self.state.pending_handoff_identity = None
                self.state.completed_handoff_identity = None
                invalid = True
        elif (
            self.state.handoff_identity is not None
            or self.state.pending_handoff_identity is not None
        ):
            if active_recovery:
                preserve_recovery_authority(RECOVERY_EVIDENCE_INCOMPLETE_ERROR)
                return
            self.state.handoff_identity = None
            self.state.pending_handoff_identity = None
            invalid = True
        elif self.state.completed_handoff_identity is not None:
            if self.state.phase not in {UpdatePhase.INSTALLED, UpdatePhase.ROLLED_BACK}:
                preserve_recovery_authority(
                    "Persisted update recovery metadata was unsafe; recovery evidence was preserved"
                )
                return
        if invalid:
            self.state.phase = UpdatePhase.ERROR
            self.state.last_error = "Persisted update paths were invalid and were reset safely"

    def _uses_canonical_beta_channel(self) -> bool:
        return (
            self.preferences.channel == "beta"
            and self.config.manifest_urls.get("beta") == DEFAULT_BETA_MANIFEST_URL
        )

    def _set_unpublished_channel_state(self) -> bool:
        if not self._clean_operation():
            self._state_write_allowed = False
            self.state.phase = UpdatePhase.ERROR
            self.state.last_error = "Updater staging cleanup could not be completed safely"
            return False
        self.state.phase = UpdatePhase.UNPUBLISHED
        self.state.offered_version = None
        self.state.mandatory = False
        self.state.release_notes_url = None
        self.state.operation_id = None
        self.state.manifest_identity = None
        self.state.transaction_path = None
        self.state.handoff_identity = None
        self.state.pending_handoff_identity = None
        self.state.completed_handoff_identity = None
        self.state.last_error = None
        return True

    def _normalize_unpublished_channel_state(self) -> None:
        if (
            self._state_write_allowed
            and self._uses_canonical_beta_channel()
            and self.state.phase == UpdatePhase.ERROR
            and self.state.last_error == "Update endpoint returned HTTP 404"
        ):
            self._set_unpublished_channel_state()

    def _save(self) -> None:
        if not self._state_write_allowed:
            raise UpdateError("Persisted update state cannot be changed safely")
        value = asdict(self.state)
        value["phase"] = self.state.phase.value
        self._atomic_json(self.state_path, value)

    def _require_metadata_writes_allowed(self, *, preferences: bool = False) -> None:
        if not self._state_write_allowed:
            raise UpdateError("Persisted update state cannot be changed safely")
        if preferences and not self._preferences_write_allowed:
            raise UpdateError("Persisted update preferences cannot be changed safely")

    def _require_no_active_handoff(self) -> None:
        if self._recovery_authority_present():
            raise UpdateBusyError("The Windows recovery helper owns the active update")

    @contextmanager
    def _exclusive(self) -> Iterator[None]:
        if not self._operation_gate.acquire(blocking=False):
            raise UpdateBusyError("Another update operation is already in progress")
        try:
            with self._operation_lock:
                yield
        finally:
            self._operation_gate.release()

    def _recover_interrupted(self) -> None:
        if self.state.phase in {UpdatePhase.CHECKING, UpdatePhase.DOWNLOADING}:
            if not self._clean_operation():
                self._state_write_allowed = False
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = "Interrupted updater staging could not be cleaned safely"
                return
            self.state.phase = UpdatePhase.CANCELLED
            self.state.last_error = "The interrupted update operation was safely cancelled"
            self._save()

    def _recovery_cleanup_failed(self) -> dict[str, Any]:
        """Keep the active handoff authoritative for a safe recovery retry."""

        self.state.phase = UpdatePhase.RESTART_REQUIRED
        self.state.last_error = (
            "Update recovery completed but cleanup could not be completed safely; retry recovery"
        )
        # The helper may already have published a terminal state. Never write
        # RESTART_REQUIRED over that publication: the helper owns the terminal
        # journal/state transition and can safely finish pointer cleanup on a
        # later replay.
        return self.public_status()

    def _finish_recovery(self, outcome: str, completed_identity: str) -> dict[str, Any]:
        if not self._clean_operation():
            return self._recovery_cleanup_failed()
        self.state.phase = (
            UpdatePhase.INSTALLED if outcome == "installed" else UpdatePhase.ROLLED_BACK
        )
        self.state.last_error = (
            None
            if outcome == "installed"
            else "The update did not become healthy; the previous app and vault were restored"
        )
        self.state.transaction_path = None
        self.state.handoff_identity = None
        self.state.pending_handoff_identity = None
        self.state.completed_handoff_identity = completed_identity
        self._save()
        if not self._clear_completed_recovery_evidence():
            return self.public_status()
        return self.public_status()

    def recover_after_restart(self) -> dict[str, Any]:
        """Resolve a persisted handoff exactly once after the replacement starts."""

        with self._exclusive():
            if self.state.phase not in {
                UpdatePhase.INSTALLING,
                UpdatePhase.RESTART_REQUIRED,
                UpdatePhase.INSTALLED,
                UpdatePhase.ROLLED_BACK,
            } or (
                self.state.phase in {UpdatePhase.INSTALLED, UpdatePhase.ROLLED_BACK}
                and self.state.transaction_path is None
            ):
                return self.public_status()
            self._require_metadata_writes_allowed()
            if not self._active_recovery_evidence_complete():
                self._state_write_allowed = False
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = RECOVERY_EVIDENCE_INCOMPLETE_ERROR
                return self.public_status()
            transaction = self.state.transaction_path
            if transaction is None:
                self._state_write_allowed = False
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = RECOVERY_EVIDENCE_INCOMPLETE_ERROR
                return self.public_status()
            journal_outcome = transaction_outcome(Path(transaction), validate_storage=False)
            try:
                reported_outcome = self.installer.recovery_outcome(self.state)
            except (HelperError, OSError, ValueError):
                reported_outcome = "failed"
            if journal_outcome == "failed":
                self._state_write_allowed = False
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = RECOVERY_EVIDENCE_INCOMPLETE_ERROR
                return self.public_status()
            if reported_outcome in {"installed", "rolled_back"} and (
                reported_outcome != journal_outcome
            ):
                self._state_write_allowed = False
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = RECOVERY_EVIDENCE_INCOMPLETE_ERROR
                return self.public_status()
            if journal_outcome in {"installed", "rolled_back"}:
                recovery_outcome = journal_outcome
            elif reported_outcome == "pending":
                return self.public_status()
            elif reported_outcome not in {None, "pending"}:
                self._state_write_allowed = False
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = RECOVERY_EVIDENCE_INCOMPLETE_ERROR
                return self.public_status()
            else:
                recovery_outcome = None
            if recovery_outcome is None:
                offered = self.state.offered_version
                try:
                    version_advanced = offered is not None and (
                        ReleaseVersion.parse(self.config.current_version)
                        >= ReleaseVersion.parse(offered)
                    )
                except ManifestError:
                    self._state_write_allowed = False
                    self.state.phase = UpdatePhase.ERROR
                    self.state.last_error = RECOVERY_EVIDENCE_INCOMPLETE_ERROR
                    return self.public_status()
                if version_advanced and self.health_probe.healthy():
                    recovery_outcome = transaction_outcome(
                        Path(transaction), validate_storage=False
                    )
                    if recovery_outcome not in {
                        "installed",
                        "rolled_back",
                    }:
                        return self.public_status()
                else:
                    try:
                        self.installer.rollback(self.state)
                    except UpdateError:
                        self.state.phase = UpdatePhase.ERROR
                        self.state.last_error = (
                            "The new version did not become healthy and automatic rollback failed"
                        )
                        self._save()
                        return self.public_status()
                    recovery_outcome = transaction_outcome(
                        Path(transaction), validate_storage=False
                    )
                    if recovery_outcome != "rolled_back":
                        return self.public_status()
                if recovery_outcome not in {"installed", "rolled_back"}:
                    return self.public_status()
            try:
                journal = UpdateJournal.load(Path(transaction), validate_storage=False)
                completed_identity = journal_handoff_identity(journal)
            except (HelperError, OSError, RecursionError, TypeError, ValueError):
                self._state_write_allowed = False
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = RECOVERY_EVIDENCE_INCOMPLETE_ERROR
                return self.public_status()
            return self._finish_recovery(recovery_outcome, completed_identity)

    def _operation_directory(self) -> Path:
        operation = self.state.operation_id or "pending"
        return self.config.data_dir / "staging" / operation

    def _validate_manifest_target(self, manifest: Mapping[str, Any]) -> None:
        if manifest["platform"] != self.config.platform_name:
            raise UpdateError("Signed update metadata targets a different platform")
        if manifest["architecture"] != self.config.architecture:
            raise UpdateError("Signed update metadata targets a different architecture")

    @staticmethod
    def _validate_manifest_artifact_size(manifest: Mapping[str, Any]) -> None:
        size = manifest["size"]
        if (
            isinstance(size, bool)
            or not isinstance(size, int)
            or size <= 0
            or size > MAX_ARTIFACT_BYTES
        ):
            raise UpdateError("Verified update metadata declares an unsupported artifact size")

    def _revalidate_persisted_manifest(self) -> dict[str, Any]:
        """Revalidate every persisted manifest field before consuming authority."""

        operation = self.state.operation_id
        if (
            not isinstance(operation, str)
            or len(operation) != 24
            or any(character not in "0123456789abcdef" for character in operation)
        ):
            raise UpdateError("Verified update transaction identity is unavailable")
        if self.state.manifest_identity is None:
            raise UpdateError("Verified update metadata identity is unavailable")

        manifest_path = self._operation_directory() / "manifest.json"
        try:
            bounded = _read_bounded_json(
                manifest_path,
                MAX_MANIFEST_BYTES,
                label="Verified update metadata",
            )
        except _PersistedMetadataError as exc:
            raise UpdateError(
                "Verified update metadata could not be re-checked; check again"
            ) from exc
        if hashlib.sha256(bounded.raw).hexdigest() != self.state.manifest_identity:
            raise UpdateError("Verified update metadata changed; check again")
        manifest = bounded.value
        try:
            verify_manifest(
                manifest,
                load_keyring(self.config.keyring_path),
                current_version=self.config.current_version,
                expected_channel=self.preferences.channel,
            )
            self._validate_manifest_target(manifest)
            self._validate_manifest_artifact_size(manifest)
            if (
                manifest["version"] != self.state.offered_version
                or manifest["mandatory"] != self.state.mandatory
                or manifest["release_notes_url"] != self.state.release_notes_url
            ):
                raise UpdateError("Verified update state no longer matches its metadata")
        except UpdateError:
            raise
        except (ManifestError, OSError, TypeError, UnicodeError, ValueError, RecursionError) as exc:
            raise UpdateError(
                "Verified update metadata could not be re-checked; check again"
            ) from exc
        return manifest

    def _clean_operation(self) -> bool:
        operation = self._operation_directory()
        try:
            operation_stat = _plain_directory_stat_if_present(operation)
            if operation_stat is not None and not _remove_owned_tree(
                operation, expected=operation_stat
            ):
                return False
        except (HelperError, OSError, RecursionError):
            return False
        self.state.downloaded_path = None
        return True

    @staticmethod
    def _prune_directory(root: Path, *, keep: str | None) -> bool:
        """Remove private orphan entries under one global tree budget."""

        try:
            root_stat = _plain_directory_stat_if_present(root)
        except (HelperError, OSError, RecursionError):
            return False
        if root_stat is None:
            return True
        budget = _CleanupBudget()
        actions: list[_CleanupAction] = []
        try:
            for entry in root.iterdir():
                current_root = _plain_directory_stat_if_present(root)
                if current_root is None or not _same_directory(root_stat, current_root):
                    return False
                if keep is not None and entry.name == keep:
                    continue
                entry_stat = entry.lstat()
                if _is_link_or_reparse(entry_stat) or (
                    stat.S_ISREG(entry_stat.st_mode) and getattr(entry_stat, "st_nlink", 1) == 1
                ):
                    if not budget.reserve(1):
                        return False
                    actions.append(
                        _CleanupAction(
                            entry,
                            current_root,
                            entry_stat,
                            remove_directory=False,
                        )
                    )
                elif stat.S_ISDIR(entry_stat.st_mode):
                    if not _plan_owned_tree(
                        entry,
                        expected=entry_stat,
                        budget=budget,
                        actions=actions,
                        root_depth=1,
                        count_root=True,
                    ):
                        return False
                else:
                    return False
            current_root = _plain_directory_stat_if_present(root)
            if current_root is None or not _same_directory(root_stat, current_root):
                return False
        except (HelperError, OSError, RecursionError):
            return False
        return _apply_cleanup_actions(actions)

    def public_status(self) -> dict[str, Any]:
        with self._operation_lock:
            result = asdict(self.state)
            result["phase"] = self.state.phase.value
            result["release_notes_url"] = _safe_public_url(self.state.release_notes_url)
            result.update(
                {
                    "enabled": self.preferences.enabled,
                    "channel": self.preferences.channel,
                    "deferred_version": self.preferences.deferred_version,
                    "automatic_install_supported": self.installer.supported,
                    "verified_artifact_available": self.state.downloaded_path is not None
                    and self.state.phase in {UpdatePhase.READY, UpdatePhase.MANUAL_REQUIRED},
                    "installer_detail": (
                        "Packaged update can restart into the verified installer"
                        if self.installer.supported
                        else _public_error_message(
                            UpdateError(self.installer.unsupported_reason),
                            fallback="Manual installation is required for this verified update",
                        )
                    ),
                    "configured": self.preferences.channel in self.config.manifest_urls,
                    "available_channels": sorted(self.config.manifest_urls),
                }
            )
            # Private staging and backup paths are intentionally not exposed.
            result.pop("downloaded_path", None)
            result.pop("backup_path", None)
            result.pop("operation_id", None)
            result.pop("transaction_path", None)
            result.pop("manifest_identity", None)
            result.pop("handoff_identity", None)
            result.pop("pending_handoff_identity", None)
            result.pop("completed_handoff_identity", None)
            return result

    def configure(self, *, enabled: bool, channel: Channel) -> dict[str, Any]:
        if channel not in {"stable", "beta"}:
            raise UpdateError("Update channel must be stable or beta")
        with self._exclusive():
            self._require_no_active_handoff()
            self._require_metadata_writes_allowed(preferences=True)
            channel_changed = channel != self.preferences.channel
            if (
                self.state.completed_handoff_identity is not None
                and not self._clear_completed_recovery_evidence()
            ):
                raise UpdateError("Updater staging cleanup could not be completed safely")
            next_preferences = UpdatePreferences(enabled, channel, None)
            cleanup_failed = channel_changed and (
                not self._clean_operation()
                or not self._prune_directory(self.config.data_dir / "staging", keep=None)
            )
            if cleanup_failed:
                self._state_write_allowed = False
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = "Updater staging cleanup could not be completed safely"
                raise UpdateError("Updater staging cleanup could not be completed safely")
            self._atomic_json(self.preferences_path, asdict(next_preferences))
            self.preferences = next_preferences
            if channel_changed:
                self.state.offered_version = None
                self.state.mandatory = False
                self.state.release_notes_url = None
                self.state.operation_id = None
                self.state.manifest_identity = None
                self.state.transaction_path = None
                self.state.handoff_identity = None
                self.state.pending_handoff_identity = None
                self.state.completed_handoff_identity = None
            if not enabled:
                self._cancel.set()
                self.state.phase = UpdatePhase.DISABLED
                self.state.last_error = None
            elif channel_changed or self.state.phase == UpdatePhase.DISABLED:
                self.state.phase = UpdatePhase.IDLE
                self.state.last_error = None
            self._save()
            return self.public_status()

    def defer(self) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            self._require_metadata_writes_allowed(preferences=True)
            if self.state.offered_version is None:
                raise UpdateError("There is no available update to defer")
            if self.state.mandatory:
                raise UpdateError("This compatibility or security update cannot be deferred")
            if (
                self.state.completed_handoff_identity is not None
                and not self._clear_completed_recovery_evidence()
            ):
                raise UpdateError("Updater staging cleanup could not be completed safely")
            next_preferences = UpdatePreferences(
                self.preferences.enabled, self.preferences.channel, self.state.offered_version
            )
            self._atomic_json(self.preferences_path, asdict(next_preferences))
            self.preferences = next_preferences
            self.state.phase = UpdatePhase.DEFERRED
            self._save()
            return self.public_status()

    def clear_error(self) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            self._require_metadata_writes_allowed()
            self.state.last_error = None
            if self.state.phase in {UpdatePhase.ERROR, UpdatePhase.CANCELLED}:
                self.state.phase = UpdatePhase.IDLE
            self._save()
            return self.public_status()

    def cancel(self) -> dict[str, Any]:
        self._cancel.set()
        with self._operation_gate, self._operation_lock:
            return self.public_status()

    def scheduled_check(self) -> dict[str, Any]:
        with self._operation_lock:
            last = _parse_time(self.state.last_checked_at)
            if not self.preferences.enabled or (
                last is not None and datetime.now(UTC) - last < CHECK_INTERVAL
            ):
                return self.public_status()
        return self.check(respect_defer=True)

    def check(self, *, respect_defer: bool = False) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            self._require_metadata_writes_allowed()
            if (
                self.state.completed_handoff_identity is not None
                and not self._clear_completed_recovery_evidence()
            ):
                return self.public_status()
            if not self.preferences.enabled:
                self.state.phase = UpdatePhase.DISABLED
                self._save()
                return self.public_status()
            url = self.config.manifest_urls.get(self.preferences.channel)
            if url is None:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = (
                    "No HTTPS metadata endpoint is configured for this update channel"
                )
                self._save()
                return self.public_status()
            self._cancel.clear()
            self.state.phase = UpdatePhase.CHECKING
            self.state.last_error = None
            self._save()
            try:
                raw = self.transport.get_bytes(url, maximum_bytes=MAX_MANIFEST_BYTES)
                bounded = _decode_bounded_json(
                    raw,
                    MAX_MANIFEST_BYTES,
                    label="Update metadata",
                )
                manifest = bounded.value
                keyring = load_keyring(self.config.keyring_path)
                verify_manifest(
                    manifest,
                    keyring,
                    current_version=self.config.current_version,
                    expected_channel=self.preferences.channel,
                )
                self._validate_manifest_target(manifest)
                self._validate_manifest_artifact_size(manifest)
                offered = cast(str, manifest["version"])
                operation_id = hashlib.sha256(raw).hexdigest()[:24]
                persisted_manifest = self._render_json(manifest).encode("utf-8")
                operation_dir = self.config.data_dir / "staging" / operation_id
                self._atomic_json(operation_dir / "manifest.json", manifest)
                self.state.last_checked_at = _utc_now()
                self.state.offered_version = offered
                self.state.mandatory = cast(bool, manifest["mandatory"])
                self.state.release_notes_url = cast(str, manifest["release_notes_url"])
                self.state.operation_id = operation_id
                self.state.manifest_identity = hashlib.sha256(persisted_manifest).hexdigest()
                self.state.downloaded_path = None
                if ReleaseVersion.parse(offered) == ReleaseVersion.parse(
                    self.config.current_version
                ):
                    self.state.phase = UpdatePhase.CURRENT
                elif (
                    respect_defer
                    and not self.state.mandatory
                    and offered == self.preferences.deferred_version
                ):
                    self.state.phase = UpdatePhase.DEFERRED
                else:
                    self.state.phase = UpdatePhase.AVAILABLE
                self._save()
                self._prune_directory(self.config.data_dir / "staging", keep=operation_id)
                return self.public_status()
            except UpdateEndpointHttpError as exc:
                if exc.status_code == 404 and self._uses_canonical_beta_channel():
                    self._set_unpublished_channel_state()
                    self.state.last_checked_at = _utc_now()
                else:
                    self.state.phase = UpdatePhase.ERROR
                    self.state.last_error = _public_error_message(
                        exc, fallback="Update check failed safely"
                    )
                    self.state.last_checked_at = _utc_now()
                self._save()
                return self.public_status()
            except (
                ManifestError,
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                json.JSONDecodeError,
                UpdateError,
            ) as exc:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = _public_error_message(
                    exc, fallback="Update check failed safely"
                )
                self.state.last_checked_at = _utc_now()
                self._save()
                return self.public_status()

    def accept_exact_candidate(self) -> dict[str, Any]:
        """Reopen a verified same-version candidate for acceptance smoke.

        Ordinary channel checks report :attr:`UpdatePhase.CURRENT` when the
        signed offer equals the installed version. Beta1 acceptance and
        packaged rollback proof still need that already-verified exact
        candidate to proceed through download, transactional replacement,
        health verification, and rollback without fabricating a newer release.
        This step performs no network I/O and never weakens signature, hash,
        platform, channel, or key checks.
        """

        with self._exclusive():
            self._require_no_active_handoff()
            self._require_metadata_writes_allowed()
            if self.state.phase != UpdatePhase.CURRENT:
                raise UpdateError("A verified same-version candidate is required before acceptance")
            offered = self.state.offered_version
            if offered is None:
                raise UpdateError("No verified candidate is available for acceptance")
            try:
                if ReleaseVersion.parse(offered) != ReleaseVersion.parse(
                    self.config.current_version
                ):
                    raise UpdateError("Only an exact same-version signed candidate can be accepted")
            except ManifestError as exc:
                raise UpdateError("Verified candidate version metadata is invalid") from exc
            try:
                self._revalidate_persisted_manifest()
            except UpdateError as exc:
                raise UpdateError(
                    "Verified same-version metadata could not be re-checked; check again"
                ) from exc
            self.state.phase = UpdatePhase.AVAILABLE
            self.state.last_error = None
            self._save()
            return self.public_status()

    def download(self) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            self._require_metadata_writes_allowed()
            if self.state.phase not in {UpdatePhase.AVAILABLE, UpdatePhase.CANCELLED}:
                raise UpdateError("A verified available update is required before download")
            try:
                manifest = self._revalidate_persisted_manifest()
            except UpdateError as exc:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = _public_error_message(
                    exc, fallback="Update download verification failed safely"
                )
                self.state.downloaded_path = None
                self._save()
                return self.public_status()
            target = self._operation_directory() / "artifact.zip"
            try:
                _prepare_plain_directory(
                    target.parent,
                    "The updater staging directory is not a trusted plain directory",
                )
                _unlink_plain_file(target, "The previous staged artifact is not a plain file")
            except UpdateError as exc:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = _public_error_message(
                    exc, fallback="Update download setup failed safely"
                )
                self.state.downloaded_path = None
                self._save()
                return self.public_status()
            self._cancel.clear()
            self.state.phase = UpdatePhase.DOWNLOADING
            self.state.last_error = None
            self._save()
            try:
                digest, received = self.transport.stream(
                    cast(str, manifest["url"]),
                    target,
                    expected_bytes=cast(int, manifest["size"]),
                    cancelled=self._cancel.is_set,
                )
                if received != manifest["size"] or digest != manifest["sha256"]:
                    raise UpdateError("Release artifact checksum does not match signed metadata")
                self.state.downloaded_path = str(target)
                self.state.phase = (
                    UpdatePhase.READY if self.installer.supported else UpdatePhase.MANUAL_REQUIRED
                )
                if not self.installer.supported:
                    self.state.last_error = _public_error_message(
                        UpdateError(self.installer.unsupported_reason),
                        fallback="Manual installation is required for this verified update",
                    )
                self._save()
                return self.public_status()
            except (OSError, UpdateError) as exc:
                with suppress(OSError, UpdateError):
                    _unlink_plain_file(target, "The partial release artifact is not a plain file")
                self.state.downloaded_path = None
                self.state.phase = (
                    UpdatePhase.CANCELLED if "cancel" in str(exc).casefold() else UpdatePhase.ERROR
                )
                self.state.last_error = _public_error_message(
                    exc, fallback="Update download failed safely"
                )
                self._save()
                return self.public_status()

    def prepare_artifact_export(self) -> PreparedArtifact:
        """Copy a freshly re-verified staged artifact for one authenticated response."""

        with self._exclusive():
            self._require_no_active_handoff()
            if (
                self.state.phase
                not in {
                    UpdatePhase.READY,
                    UpdatePhase.MANUAL_REQUIRED,
                }
                or self.state.downloaded_path is None
            ):
                raise UpdateError("A completely verified update must be ready before saving")
            try:
                manifest = self._revalidate_persisted_manifest()
                source = Path(self.state.downloaded_path)
                expected = self._operation_directory() / "artifact.zip"
                if not _same_path(source, expected):
                    raise UpdateError(
                        "Verified update artifact is no longer available; download again"
                    )
                source_digest, source_size = _hash_stable_file(
                    source,
                    maximum_bytes=MAX_ARTIFACT_BYTES,
                )
                if source_size != manifest["size"] or source_digest != manifest["sha256"]:
                    raise UpdateError("Saved update artifact failed signed checksum verification")
            except (ManifestError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise UpdateError(
                    "Verified update artifact could not be re-verified; check again"
                ) from exc

            export_root = self.config.data_dir / "exports"
            _prepare_plain_directory(
                export_root,
                "The updater export directory is not a trusted plain directory",
            )
            descriptor, export_name = tempfile.mkstemp(suffix=".zip", dir=export_root)
            export_path = Path(export_name)
            try:
                digest = hashlib.sha256()
                copied = 0
                with (
                    source.open("rb") as input_stream,
                    os.fdopen(descriptor, "wb") as output_stream,
                ):
                    while chunk := input_stream.read(1024 * 1024):
                        copied += len(chunk)
                        if copied > cast(int, manifest["size"]):
                            raise UpdateError("Saved update artifact exceeded its signed size")
                        digest.update(chunk)
                        output_stream.write(chunk)
                    output_stream.flush()
                    os.fsync(output_stream.fileno())
                if copied != manifest["size"] or digest.hexdigest() != manifest["sha256"]:
                    raise UpdateError("Saved update artifact failed signed checksum verification")
            except BaseException:
                with suppress(OSError):
                    os.close(descriptor)
                with suppress(UpdateError):
                    _unlink_plain_file(
                        export_path,
                        "The temporary exported artifact is not a plain file",
                    )
                raise
            filename = (
                f"all-the-context-{manifest['version']}-{manifest['platform']}-"
                f"{manifest['architecture']}.zip"
            )
            return PreparedArtifact(export_path, filename, copied)

    def install(self) -> dict[str, Any]:
        with self._exclusive():
            self._require_no_active_handoff()
            self._require_metadata_writes_allowed()
            if self.state.phase != UpdatePhase.READY or self.state.downloaded_path is None:
                raise UpdateError("A completely verified update must be ready before install")
            artifact = Path(self.state.downloaded_path)
            try:
                manifest = self._revalidate_persisted_manifest()
                expected_artifact = self._operation_directory() / "artifact.zip"
                if not _same_path(artifact, expected_artifact):
                    raise UpdateError("Verified update artifact identity changed; check again")
                artifact_digest, artifact_size = _hash_stable_file(
                    artifact,
                    maximum_bytes=MAX_ARTIFACT_BYTES,
                )
                if artifact_size != manifest["size"] or artifact_digest != manifest["sha256"]:
                    raise UpdateError("Release artifact checksum does not match signed metadata")
            except (
                ManifestError,
                OSError,
                TypeError,
                UnicodeError,
                ValueError,
                UpdateError,
            ) as exc:
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = _public_error_message(
                    exc, fallback="Update installation verification failed safely"
                )
                self.state.downloaded_path = None
                self.state.transaction_path = None
                self.state.handoff_identity = None
                self.state.pending_handoff_identity = None
                self.state.completed_handoff_identity = None
                self._save()
                return self.public_status()
            self._cancel.clear()
            self.state.phase = UpdatePhase.INSTALLING
            self.state.last_error = None
            self._save()
            try:
                self.installer.preflight(artifact, cast(int, manifest["size"]))
                final_digest, final_size = _hash_stable_file(
                    artifact,
                    maximum_bytes=MAX_ARTIFACT_BYTES,
                )
                if final_size != manifest["size"] or final_digest != manifest["sha256"]:
                    raise UpdateError("Release artifact changed after preflight")
                backup_path = (
                    self.config.data_dir
                    / "backups"
                    / (f"core-{self.config.current_version}-before-{manifest['version']}.sqlite3")
                )
                self.backup.create(self.database_path, backup_path)
                self.state.backup_path = str(backup_path)
                self.state.recovery_attempts += 1
                operation_id = self.state.operation_id
                if operation_id is None:
                    raise UpdateError("Verified update transaction identity is unavailable")
                transaction_dir = self.config.data_dir / "transactions" / operation_id
                self.state.transaction_path = str(transaction_dir / "journal.json")
                self.state.handoff_identity = None
                self.state.pending_handoff_identity = None
                self.state.completed_handoff_identity = None
                self.state.phase = UpdatePhase.RESTART_REQUIRED
                self._save()
                core_host = os.environ.get("ATC_CORE_HOST", "127.0.0.1")
                try:
                    core_port = int(os.environ.get("ATC_CORE_PORT", "7337"))
                except ValueError as exc:
                    raise UpdateError("The Core port is invalid for update recovery") from exc
                self.installer.handoff(
                    InstallPlan(
                        artifact=artifact,
                        target_version=cast(str, manifest["version"]),
                        current_version=self.config.current_version,
                        operation_id=operation_id,
                        operation_dir=self._operation_directory(),
                        transaction_dir=transaction_dir,
                        database_path=self.database_path,
                        database_backup_path=backup_path,
                        state_path=self.state_path,
                        core_host=core_host,
                        core_port=core_port,
                        artifact_sha256=cast(str, manifest["sha256"]),
                        artifact_size=cast(int, manifest["size"]),
                    )
                )
                return self.public_status()
            except (OSError, ValueError, UpdateError) as exc:
                failure_message = _public_error_message(
                    exc, fallback="Update installation failed safely"
                )
                if self._transaction_evidence_persisted():
                    transaction_path = self.state.transaction_path
                    operation_id = self.state.operation_id
                    reloaded = self._load_state()
                    if (
                        reloaded.transaction_path != transaction_path
                        or reloaded.operation_id != operation_id
                        or not self._state_write_allowed
                    ):
                        self._state_write_allowed = False
                        return self.public_status()
                    self.state = reloaded
                    self.state.current_version = self.config.current_version
                else:
                    self.state.downloaded_path = None
                    self.state.transaction_path = None
                    self.state.handoff_identity = None
                    self.state.pending_handoff_identity = None
                    self.state.completed_handoff_identity = None
                self.state.phase = UpdatePhase.ERROR
                self.state.last_error = failure_message
                self._save()
                return self.public_status()
