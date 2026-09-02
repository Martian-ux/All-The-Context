"""Independent, journaled Windows application and database cutover helper.

The helper is packaged as a separate executable so it can outlive the Core
binary it replaces.  It accepts only a journal rooted below the per-user Core
data directory and never accepts arbitrary command lines or filesystem roots.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from filelock import FileLock, Timeout
from platformdirs import user_data_path

from . import __version__
from .platform_compat import windows_dll, windows_registry
from .release_manifest import ManifestError, ReleaseVersion, load_keyring, verify_manifest

JOURNAL_SCHEMA_VERSION = 2
MAX_JOURNAL_BYTES = 64 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_STAGING_MANIFEST_BYTES = 128 * 1024
MAX_STAGING_ARTIFACT_BYTES = 2 * 1024 * 1024 * 1024
STARTUP_RECOVERY_DIAGNOSTIC_SCHEMA_VERSION = 1
STARTUP_RECOVERY_DIAGNOSTIC_NAME = "startup-recovery.json"
MAX_STARTUP_RECOVERY_DIAGNOSTIC_BYTES = 16 * 1024
STARTUP_RECOVERY_DIAGNOSTIC_STATUSES = frozenset({"blocked", "cleared"})
STARTUP_RECOVERY_PHASES = frozenset(
    {
        "idle",
        "disabled",
        "checking",
        "current",
        "unpublished",
        "available",
        "deferred",
        "downloading",
        "ready",
        "installing",
        "restart_required",
        "installed",
        "rolled_back",
        "manual_required",
        "error",
        "cancelled",
    }
)
STARTUP_RECOVERY_TRANSACTION_PHASES = frozenset({"restart_required", "installed", "rolled_back"})
STARTUP_RECOVERY_DIAGNOSTIC_CODES = frozenset(
    {
        "none",
        "metadata_too_large",
        "metadata_unreadable",
        "metadata_invalid",
        "startup_state_untrusted",
        "startup_state_invalid",
        "startup_state_active_without_transaction",
        "startup_state_missing_with_transaction",
        "startup_state_reset_failed",
        "pre_cutover_install_reset",
        "pre_cutover_evidence_missing",
        "unbound_preparation_reset",
        "journal_unreadable",
        "journal_invalid",
        "startup_state_mismatch",
        "helper_launch_failed",
        "diagnostic_unreadable",
        "diagnostic_invalid",
    }
)
STARTUP_STATE_FIELDS = frozenset(
    {
        "phase",
        "current_version",
        "offered_version",
        "mandatory",
        "release_notes_url",
        "downloaded_path",
        "backup_path",
        "last_checked_at",
        "last_error",
        "operation_id",
        "transaction_path",
        "recovery_attempts",
        "manifest_identity",
        "handoff_identity",
        "pending_handoff_identity",
        "completed_handoff_identity",
    }
)
PROCESS_TIMEOUT_SECONDS = 90
PARENT_EXIT_TIMEOUT_SECONDS = 60
WINDOWS_RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
SMOKE_FLAG = "ATC_PACKAGED_SMOKE"
WINDOWS_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class HelperPhase(StrEnum):
    PREPARED = "prepared"
    WAITING_FOR_PARENT = "waiting_for_parent"
    ABORT_REQUESTED = "abort_requested"
    CUTOVER_STARTED = "cutover_started"
    BINARY_REPLACED = "binary_replaced"
    DIAGNOSTICS_PASSED = "diagnostics_passed"
    HEALTH_PASSED = "health_passed"
    ROLLBACK_REQUESTED = "rollback_requested"
    ROLLING_BACK = "rolling_back"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"


ACTIVE_PHASES = {
    HelperPhase.PREPARED,
    HelperPhase.WAITING_FOR_PARENT,
    HelperPhase.ABORT_REQUESTED,
    HelperPhase.CUTOVER_STARTED,
    HelperPhase.BINARY_REPLACED,
    HelperPhase.DIAGNOSTICS_PASSED,
    HelperPhase.HEALTH_PASSED,
    HelperPhase.ROLLBACK_REQUESTED,
    HelperPhase.ROLLING_BACK,
}
TERMINAL_PHASES = {HelperPhase.COMMITTED, HelperPhase.ROLLED_BACK}


class HelperError(RuntimeError):
    """A fixed-code helper failure safe to persist without private detail."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _decode_json(raw: bytes) -> object:
    """Decode one already-bounded JSON payload into an untrusted value.

    Keep parser exception handling next to the parser invocation.  In
    particular, CPython can raise ``ValueError`` for its integer digit limit
    and ``RecursionError`` for deeply nested JSON; neither is an I/O error and
    neither should escape a bounded metadata boundary.  Do not broaden this
    to ``Exception`` or ``BaseException``: unexpected implementation failures
    and process-control exceptions must retain their normal behavior.
    """

    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise HelperError("metadata_unreadable") from exc
    try:
        return json.loads(text)
    except (ValueError, RecursionError) as exc:
        raise HelperError("metadata_unreadable") from exc


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(
    path: Path,
    value: dict[str, Any],
    *,
    boundary_code: str = "metadata_untrusted",
) -> None:
    if not _plain_directory_chain_if_present(path.parent, boundary_code):
        path.parent.mkdir(parents=True, exist_ok=True)
    _plain_directory_chain(path.parent, boundary_code)
    parent_before = _plain_directory_stat(path.parent, boundary_code)
    _plain_file_stat_if_present(path, boundary_code)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".atc-new", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        parent_after = _plain_directory_stat(path.parent, boundary_code)
        if not _same_directory(parent_before, parent_after):
            raise HelperError(boundary_code)
        _plain_file_stat(temporary, boundary_code)
        _plain_file_stat_if_present(path, boundary_code)
        parent_after = _plain_directory_stat(path.parent, boundary_code)
        if not _same_directory(parent_before, parent_after):
            raise HelperError(boundary_code)
        temporary.replace(path)
    except BaseException:
        with suppress(HelperError, OSError):
            _unlink_plain_file_if_present(temporary, boundary_code)
        raise


def _read_json(
    path: Path,
    maximum_bytes: int,
    *,
    boundary_code: str = "metadata_unreadable",
) -> dict[str, Any]:
    try:
        before = _plain_file_stat(path, boundary_code)
        if before.st_size > maximum_bytes:
            raise HelperError("metadata_too_large")
        with path.open("rb") as stream:
            raw = stream.read(maximum_bytes + 1)
            if len(raw) > maximum_bytes:
                raise HelperError("metadata_too_large")
            opened = os.fstat(stream.fileno())
            if not _same_file(before, opened):
                raise HelperError(boundary_code)
            observed = os.fstat(stream.fileno())
            if not _same_file(before, observed):
                raise HelperError(boundary_code)
        after = _plain_file_stat(path, boundary_code)
        if not _same_file(before, after):
            raise HelperError(boundary_code)
    except HelperError:
        raise
    except OSError as exc:
        raise HelperError("metadata_unreadable") from exc
    value = _decode_json(raw)
    if not isinstance(value, dict):
        raise HelperError("metadata_invalid")
    return cast(dict[str, Any], value)


def journal_failure_diagnostic(path: Path) -> str:
    """Return bounded, non-sensitive updater state for operational failures."""
    try:
        value = _read_json(path, MAX_JOURNAL_BYTES)
    except HelperError as error:
        return json.dumps({"journal_status": error.code}, sort_keys=True)
    last_error_code = value.get("last_error_code")
    if last_error_code is not None and (
        not isinstance(last_error_code, str) or len(last_error_code) > 64
    ):
        last_error_code = "invalid"
    phase = value.get("phase")
    if not isinstance(phase, str) or len(phase) > 64:
        phase = "invalid"
    schema_version = value.get("schema_version")
    if (
        isinstance(schema_version, bool)
        or not isinstance(schema_version, int)
        or schema_version < 0
    ):
        schema_version = "invalid"
    return json.dumps(
        {
            "last_error_code": last_error_code,
            "phase": phase,
            "schema_version": schema_version,
        },
        sort_keys=True,
    )


def _startup_recovery_diagnostic_path(state_path: Path) -> Path:
    return state_path.with_name(STARTUP_RECOVERY_DIAGNOSTIC_NAME)


def _write_startup_recovery_diagnostic(
    state_path: Path,
    *,
    status: str,
    code: str,
    phase: str | None = None,
) -> None:
    """Persist only bounded startup-recovery facts, never state or local paths."""

    safe_phase = phase if isinstance(phase, str) and phase in STARTUP_RECOVERY_PHASES else None
    safe_code = (
        code
        if isinstance(code, str) and code in STARTUP_RECOVERY_DIAGNOSTIC_CODES
        else "startup_state_invalid"
    )
    safe_status = (
        status
        if isinstance(status, str) and status in STARTUP_RECOVERY_DIAGNOSTIC_STATUSES
        else "blocked"
    )
    payload = {
        "schema_version": STARTUP_RECOVERY_DIAGNOSTIC_SCHEMA_VERSION,
        "status": safe_status,
        "code": safe_code,
        "phase": safe_phase,
        "updated_at": _utc_now(),
    }
    try:
        _atomic_json(_startup_recovery_diagnostic_path(state_path), payload)
    except Exception:
        # Startup containment remains fail-closed when diagnostics cannot be written.
        return


def _startup_recovery_diagnostic_exists(path: Path) -> bool:
    """Probe the optional marker without allowing it to affect startup control flow."""

    try:
        return _plain_file_stat_if_present(path, "startup_diagnostic_untrusted") is not None
    except Exception:
        # The marker is informational only. An unsafe or racing marker must never
        # become an unhandled startup failure or startup authority.
        return False


def startup_recovery_diagnostic(path: Path) -> dict[str, str] | None:
    """Read a sanitized startup diagnostic for the packaged recovery doctor."""

    try:
        if _plain_file_stat_if_present(path, "startup_diagnostic_untrusted") is None:
            return None
        value = _read_json(path, MAX_STARTUP_RECOVERY_DIAGNOSTIC_BYTES)
    except Exception:
        return {"status": "unreadable", "code": "diagnostic_unreadable"}
    status = value.get("status")
    code = value.get("code")
    phase = value.get("phase")
    if (
        isinstance(value.get("schema_version"), bool)
        or value.get("schema_version") != STARTUP_RECOVERY_DIAGNOSTIC_SCHEMA_VERSION
        or not isinstance(status, str)
        or status not in STARTUP_RECOVERY_DIAGNOSTIC_STATUSES
        or not isinstance(code, str)
        or code not in STARTUP_RECOVERY_DIAGNOSTIC_CODES
    ):
        return {"status": "unreadable", "code": "diagnostic_invalid"}
    result = {"status": status, "code": code}
    if isinstance(phase, str) and phase in STARTUP_RECOVERY_PHASES:
        result["phase"] = phase
    return result


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        _plain_file_stat(path, "recovery_file_unreadable")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
    except (HelperError, OSError) as exc:
        raise HelperError("recovery_file_unreadable") from exc
    return digest.hexdigest(), size


def _plain_file_stat(path: Path, code: str) -> os.stat_result:
    """Return a single-link regular file without following reparse paths."""

    _plain_directory_chain(path.parent, code)
    try:
        value = path.lstat()
    except OSError as exc:
        raise HelperError(code) from exc
    if (
        bool(getattr(value, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT)
        or stat.S_ISLNK(value.st_mode)
        or not stat.S_ISREG(value.st_mode)
        or getattr(value, "st_nlink", 1) != 1
    ):
        raise HelperError(code)
    return value


def _plain_directory_stat(path: Path, code: str) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError as exc:
        raise HelperError(code) from exc
    if bool(getattr(value, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT) or not stat.S_ISDIR(
        value.st_mode
    ):
        raise HelperError(code)
    return value


def _plain_directory_chain(path: Path, code: str) -> None:
    """Reject reparse points in every parent of a recovery-owned directory."""

    for directory in reversed((path, *path.parents)):
        _plain_directory_stat(directory, code)


def _plain_directory_chain_if_present(path: Path, code: str) -> bool:
    """Validate existing parents without following a missing path component."""

    for directory in reversed((path, *path.parents)):
        try:
            value = directory.lstat()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise HelperError(code) from exc
        has_reparse = bool(getattr(value, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT)
        if has_reparse or not stat.S_ISDIR(value.st_mode):
            raise HelperError(code)
    return True


def _plain_file_stat_if_present(path: Path, code: str) -> os.stat_result | None:
    if not _plain_directory_chain_if_present(path.parent, code):
        return None
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise HelperError(code) from exc
    if (
        bool(getattr(value, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT)
        or stat.S_ISLNK(value.st_mode)
        or not stat.S_ISREG(value.st_mode)
        or getattr(value, "st_nlink", 1) != 1
    ):
        raise HelperError(code)
    return value


def _unlink_plain_file_if_present(path: Path, code: str) -> None:
    parent_before = _plain_directory_stat(path.parent, code)
    target_before = _plain_file_stat_if_present(path, code)
    if target_before is None:
        return
    parent_after = _plain_directory_stat(path.parent, code)
    target_after = _plain_file_stat(path, code)
    if not _same_directory(parent_before, parent_after) or not _same_file(
        target_before, target_after
    ):
        raise HelperError(code)
    parent_after = _plain_directory_stat(path.parent, code)
    if not _same_directory(parent_before, parent_after):
        raise HelperError(code)
    path.unlink()


def _update_keyring_path() -> Path:
    """Return the bundled keyring used by the normal updater."""

    return Path(__file__).resolve().with_name("update_keys.json")


def _update_architecture() -> str | None:
    machine = platform.machine().casefold()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return None


def _pre_cutover_staging_evidence(operation_id: str, state: dict[str, Any]) -> bool:
    data_dir = _data_directory()
    updates_dir = data_dir / "updates"
    staging_dir = updates_dir / "staging"
    operation_dir = staging_dir / operation_id
    expected_artifact = operation_dir / "artifact.zip"
    manifest = operation_dir / "manifest.json"
    downloaded_path = state.get("downloaded_path")
    manifest_identity = state.get("manifest_identity")
    if not isinstance(downloaded_path, str) or not _valid_digest(manifest_identity):
        return False
    try:
        if os.path.normcase(os.path.abspath(downloaded_path)) != os.path.normcase(
            os.path.abspath(expected_artifact)
        ):
            return False
        _plain_directory_chain(operation_dir, "startup_state_untrusted")
        manifest_stat = _plain_file_stat(manifest, "startup_state_untrusted")
        artifact_stat = _plain_file_stat(expected_artifact, "startup_state_untrusted")
        if (
            manifest_stat.st_size > MAX_STAGING_MANIFEST_BYTES
            or artifact_stat.st_size <= 0
            or artifact_stat.st_size > MAX_STAGING_ARTIFACT_BYTES
        ):
            return False
        if not _verified(manifest, cast(str, manifest_identity), manifest_stat.st_size):
            return False
        value = _read_json(manifest, MAX_STAGING_MANIFEST_BYTES)
        verify_manifest(
            value,
            load_keyring(_update_keyring_path()),
            current_version=__version__,
        )
        if (
            value["platform"] != "windows"
            or value["architecture"] != _update_architecture()
            or value["version"] != state.get("offered_version")
            or value["mandatory"] != state.get("mandatory")
            or value["release_notes_url"] != state.get("release_notes_url")
        ):
            return False
        if not _verified(
            expected_artifact,
            cast(str, value["sha256"]),
            cast(int, value["size"]),
        ):
            return False
    except (HelperError, ManifestError, OSError, TypeError, UnicodeError, ValueError):
        return False
    return True


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


def _same_directory(expected: os.stat_result, observed: os.stat_result) -> bool:
    try:
        same = os.path.samestat(expected, observed)
    except (AttributeError, OSError, TypeError, ValueError):
        return False
    return bool(same and stat.S_ISDIR(observed.st_mode)) and not bool(
        getattr(observed, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT
    )


def _valid_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _valid_operation_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 24
        and all(character in "0123456789abcdef" for character in value)
    )


def _data_directory() -> Path:
    configured = os.environ.get("ATC_CORE_DATA_DIR")
    if configured:
        return Path(os.path.abspath(os.fspath(Path(configured).expanduser())))
    return Path(
        os.path.abspath(os.fspath(user_data_path("AllTheContext", "AllTheContext", roaming=False)))
    )


def _install_directory() -> Path:
    configured = os.environ.get("ATC_INSTALL_DIR")
    if configured:
        return Path(os.path.abspath(os.fspath(Path(configured).expanduser())))
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return (
            Path(os.path.abspath(os.fspath(Path(local_app_data).expanduser())))
            / "Programs"
            / "All The Context"
        )
    data_path = _data_directory()
    return data_path.parent / "Programs" / "All The Context"


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(os.fspath(_absolute_path(left))) == os.path.normcase(
        os.fspath(_absolute_path(right))
    )


def _prepare_plain_directory_chain(path: Path, code: str) -> None:
    """Create only a missing tail below a verified plain ancestor."""

    absolute = _absolute_path(path)
    missing: list[Path] = []
    current = absolute
    while True:
        try:
            value = current.lstat()
        except FileNotFoundError:
            missing.append(current)
            parent = current.parent
            if parent == current:
                raise HelperError(code) from None
            current = parent
            continue
        except OSError as exc:
            raise HelperError(code) from exc
        has_reparse = bool(getattr(value, "st_file_attributes", 0) & WINDOWS_REPARSE_POINT)
        if has_reparse or not stat.S_ISDIR(value.st_mode):
            raise HelperError(code)
        _plain_directory_chain(current, code)
        break

    for directory in reversed(missing):
        try:
            directory.mkdir()
        except FileExistsError:
            pass
        except OSError as exc:
            raise HelperError(code) from exc
        _plain_directory_stat(directory, code)
    _plain_directory_chain(absolute, code)


def _validate_write_target(target: Path, root: Path, code: str) -> None:
    target_absolute = _absolute_path(target)
    root_absolute = _absolute_path(root)
    if not _within(target_absolute, root_absolute):
        raise HelperError(code)
    _prepare_plain_directory_chain(root_absolute, code)
    _prepare_plain_directory_chain(target_absolute.parent, code)
    _plain_file_stat_if_present(target_absolute, code)


def _validate_install_targets(journal: UpdateJournal, code: str) -> Path:
    root = _install_directory()
    for target in (
        journal.application_path,
        journal.mcp_path,
        journal.recovery_path,
        journal.stable_update_helper_path,
    ):
        _validate_write_target(Path(target), root, code)
    return root


def _within(path: Path, root: Path) -> bool:
    path_value = os.path.normcase(os.fspath(_absolute_path(path)))
    root_value = os.path.normcase(os.fspath(_absolute_path(root)))
    try:
        return os.path.commonpath((path_value, root_value)) == root_value
    except (OSError, ValueError):
        return False


def _validate_plain_file_path(path: Path, code: str, *, required: bool = False) -> None:
    _plain_directory_chain(path.parent, code)
    if required:
        _plain_file_stat(path, code)
    else:
        _plain_file_stat_if_present(path, code)


def _validate_journal_storage_paths(
    journal: UpdateJournal, journal_path: Path, boundary_code: str
) -> None:
    data_dir = _data_directory()
    updates_dir = data_dir / "updates"
    transaction_dir = updates_dir / "transactions" / journal.operation_id
    backup_root = updates_dir / "backups"
    _plain_directory_chain(transaction_dir, boundary_code)
    _plain_directory_chain(backup_root, boundary_code)
    for path in (
        Path(journal.replacement_path),
        Path(journal.rollback_application_path),
        Path(journal.rollback_update_helper_path),
        *([Path(journal.rollback_mcp_path)] if journal.rollback_mcp_path else []),
        *([Path(journal.rollback_recovery_path)] if journal.rollback_recovery_path else []),
        Path(journal.database_path),
        Path(journal.database_backup_path),
        Path(journal.state_path),
        Path(journal.helper_path),
    ):
        _validate_plain_file_path(path, boundary_code, required=True)
    # A newly prepared transaction validates this path before the first save;
    # UpdateJournal.load() performs the required existing-file check.
    _validate_plain_file_path(journal_path, boundary_code)
    database = Path(journal.database_path)
    for suffix in ("-wal", "-shm", "-journal"):
        _validate_plain_file_path(database.with_name(f"{database.name}{suffix}"), boundary_code)


def _validate_startup_state(value: dict[str, Any]) -> str:
    if set(value) != STARTUP_STATE_FIELDS:
        raise HelperError("startup_state_invalid")
    phase = value.get("phase")
    if not isinstance(phase, str) or phase not in STARTUP_RECOVERY_PHASES:
        raise HelperError("startup_state_invalid")
    current_version = value.get("current_version")
    if not isinstance(current_version, str):
        raise HelperError("startup_state_invalid")
    try:
        ReleaseVersion.parse(current_version)
    except ManifestError as exc:
        raise HelperError("startup_state_invalid") from exc
    offered_version = value.get("offered_version")
    if offered_version is not None:
        if not isinstance(offered_version, str):
            raise HelperError("startup_state_invalid")
        try:
            ReleaseVersion.parse(offered_version)
        except ManifestError as exc:
            raise HelperError("startup_state_invalid") from exc
    if not isinstance(value.get("mandatory"), bool):
        raise HelperError("startup_state_invalid")
    for field in (
        "release_notes_url",
        "downloaded_path",
        "backup_path",
        "last_error",
        "transaction_path",
    ):
        field_value = value.get(field)
        if field_value is not None and (not isinstance(field_value, str) or not field_value):
            raise HelperError("startup_state_invalid")
    checked_at = value.get("last_checked_at")
    if checked_at is not None:
        if not isinstance(checked_at, str):
            raise HelperError("startup_state_invalid")
        try:
            parsed = datetime.fromisoformat(checked_at)
        except ValueError as exc:
            raise HelperError("startup_state_invalid") from exc
        if parsed.tzinfo is None:
            raise HelperError("startup_state_invalid")
    attempts = value.get("recovery_attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise HelperError("startup_state_invalid")
    operation_id = value.get("operation_id")
    if operation_id is not None and not _valid_operation_id(operation_id):
        raise HelperError("startup_state_invalid")
    for identity_field in (
        "manifest_identity",
        "handoff_identity",
        "pending_handoff_identity",
        "completed_handoff_identity",
    ):
        identity = value.get(identity_field)
        if identity is not None and not _valid_digest(identity):
            raise HelperError("startup_state_invalid")
    if value.get("transaction_path") is None and any(
        value.get(field) is not None for field in ("handoff_identity", "pending_handoff_identity")
    ):
        raise HelperError("startup_state_invalid")
    return phase


def _transaction_evidence_without_state(state_path: Path) -> bool:
    transactions = state_path.parent / "transactions"
    try:
        if not _plain_directory_chain_if_present(transactions, "startup_state_untrusted"):
            return False
        return any(transactions.iterdir())
    except OSError as exc:
        raise HelperError("startup_state_untrusted") from exc


@dataclass(slots=True)
class UpdateJournal:
    operation_id: str
    phase: HelperPhase
    current_version: str
    target_version: str
    parent_pid: int
    application_path: str
    replacement_path: str
    replacement_sha256: str
    replacement_size: int
    rollback_application_path: str
    rollback_application_sha256: str
    rollback_application_size: int
    mcp_path: str
    rollback_mcp_path: str | None
    rollback_mcp_sha256: str | None
    rollback_mcp_size: int | None
    recovery_path: str
    rollback_recovery_path: str | None
    rollback_recovery_sha256: str | None
    rollback_recovery_size: int | None
    stable_update_helper_path: str
    rollback_update_helper_path: str
    rollback_update_helper_sha256: str
    rollback_update_helper_size: int
    database_path: str
    database_backup_path: str
    database_backup_sha256: str
    database_backup_size: int
    state_path: str
    helper_path: str
    core_host: str
    core_port: int
    recovery_helper_sha256: str
    recovery_helper_size: int
    created_at: str
    updated_at: str
    last_error_code: str | None = None
    schema_version: int = JOURNAL_SCHEMA_VERSION

    @classmethod
    def load(cls, path: Path) -> UpdateJournal:
        _plain_directory_chain(path.parent, "journal_untrusted")
        _plain_file_stat(path, "journal_untrusted")
        value = _read_json(path, MAX_JOURNAL_BYTES, boundary_code="journal_untrusted")
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise HelperError("journal_shape_invalid")
        try:
            value["phase"] = HelperPhase(value["phase"])
            journal = cls(**value)
        except (TypeError, ValueError) as exc:
            raise HelperError("journal_value_invalid") from exc
        try:
            journal.validate(path)
        except HelperError:
            raise
        except (OSError, TypeError, ValueError) as exc:
            raise HelperError("journal_value_invalid") from exc
        return journal

    def save(self, path: Path) -> None:
        self.updated_at = _utc_now()
        value = asdict(self)
        value["phase"] = self.phase.value
        _atomic_json(path, value, boundary_code="journal_untrusted")

    def validate(self, path: Path, *, boundary_code: str = "journal_path_untrusted") -> None:
        if self.schema_version != JOURNAL_SCHEMA_VERSION or not _valid_operation_id(
            self.operation_id
        ):
            raise HelperError("journal_identity_invalid")
        if (
            isinstance(self.parent_pid, bool)
            or not isinstance(self.parent_pid, int)
            or self.parent_pid < 0
        ):
            raise HelperError("journal_process_invalid")
        try:
            ReleaseVersion.parse(self.current_version)
            ReleaseVersion.parse(self.target_version)
        except ManifestError as exc:
            raise HelperError("journal_version_invalid") from exc
        if (
            self.core_host != "127.0.0.1"
            or isinstance(self.core_port, bool)
            or not isinstance(self.core_port, int)
            or not 1 <= self.core_port <= 65_535
        ):
            raise HelperError("journal_core_invalid")
        for digest, size in (
            (self.replacement_sha256, self.replacement_size),
            (self.rollback_application_sha256, self.rollback_application_size),
            (self.rollback_update_helper_sha256, self.rollback_update_helper_size),
            (self.database_backup_sha256, self.database_backup_size),
            (self.recovery_helper_sha256, self.recovery_helper_size),
        ):
            if (
                not _valid_digest(digest)
                or isinstance(size, bool)
                or not isinstance(size, int)
                or size <= 0
            ):
                raise HelperError("journal_digest_invalid")
        optional_mcp = (
            self.rollback_mcp_path,
            self.rollback_mcp_sha256,
            self.rollback_mcp_size,
        )
        if any(item is None for item in optional_mcp) != all(item is None for item in optional_mcp):
            raise HelperError("journal_mcp_invalid")
        if self.rollback_mcp_sha256 is not None and (
            not _valid_digest(self.rollback_mcp_sha256)
            or isinstance(self.rollback_mcp_size, bool)
            or not isinstance(self.rollback_mcp_size, int)
            or self.rollback_mcp_size <= 0
        ):
            raise HelperError("journal_mcp_invalid")
        optional_recovery = (
            self.rollback_recovery_path,
            self.rollback_recovery_sha256,
            self.rollback_recovery_size,
        )
        if any(item is None for item in optional_recovery) != all(
            item is None for item in optional_recovery
        ):
            raise HelperError("journal_recovery_invalid")
        if self.rollback_recovery_sha256 is not None and (
            not _valid_digest(self.rollback_recovery_sha256)
            or isinstance(self.rollback_recovery_size, bool)
            or not isinstance(self.rollback_recovery_size, int)
            or self.rollback_recovery_size <= 0
        ):
            raise HelperError("journal_recovery_invalid")
        if self.last_error_code is not None and (
            not isinstance(self.last_error_code, str)
            or len(self.last_error_code) > 64
            or not self.last_error_code.replace("_", "").isalnum()
        ):
            raise HelperError("journal_error_invalid")
        for timestamp in (self.created_at, self.updated_at):
            if not isinstance(timestamp, str):
                raise HelperError("journal_time_invalid")
            try:
                parsed = datetime.fromisoformat(timestamp)
            except ValueError as exc:
                raise HelperError("journal_time_invalid") from exc
            if parsed.tzinfo is None:
                raise HelperError("journal_time_invalid")
        path_values = (
            self.application_path,
            self.replacement_path,
            self.rollback_application_path,
            self.mcp_path,
            self.recovery_path,
            self.stable_update_helper_path,
            self.rollback_update_helper_path,
            self.database_path,
            self.database_backup_path,
            self.state_path,
            self.helper_path,
        )
        if any(not isinstance(item, str) or not item for item in path_values):
            raise HelperError("journal_path_invalid")

        _validate_install_targets(self, boundary_code)
        data_dir = _data_directory()
        updates_dir = data_dir / "updates"
        transaction_dir = updates_dir / "transactions" / self.operation_id
        expected_paths = {
            "journal": (path, transaction_dir / "journal.json"),
            "application": (
                Path(self.application_path),
                _install_directory() / "AllTheContext.exe",
            ),
            "mcp": (Path(self.mcp_path), _install_directory() / "AllTheContextMCP.exe"),
            "recovery": (
                Path(self.recovery_path),
                _install_directory() / "AllTheContextRecovery.exe",
            ),
            "stable_update_helper": (
                Path(self.stable_update_helper_path),
                _install_directory() / "AllTheContextUpdater.exe",
            ),
            "database": (Path(self.database_path), data_dir / "core.sqlite3"),
            "state": (Path(self.state_path), updates_dir / "state.json"),
            "helper": (
                Path(self.helper_path),
                transaction_dir / "AllTheContextUpdater.exe",
            ),
        }
        for candidate, expected_path in expected_paths.values():
            if not _same_path(candidate, expected_path):
                raise HelperError("journal_path_invalid")
        for child in (
            Path(self.replacement_path),
            Path(self.rollback_application_path),
            Path(self.rollback_update_helper_path),
            *([Path(self.rollback_mcp_path)] if self.rollback_mcp_path else []),
            *([Path(self.rollback_recovery_path)] if self.rollback_recovery_path else []),
        ):
            if not _within(child, transaction_dir):
                raise HelperError("journal_path_invalid")
        backup = Path(self.database_backup_path)
        if not _within(backup, updates_dir / "backups"):
            raise HelperError("journal_path_invalid")
        _validate_journal_storage_paths(self, path, boundary_code)


def journal_handoff_identity(journal: UpdateJournal) -> str:
    """Bind transaction authority independently of mutable progress fields."""

    value = asdict(journal)
    for mutable in ("phase", "updated_at", "last_error_code"):
        value.pop(mutable)
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    )
    return hashlib.sha256(raw).hexdigest()


def _validate_handoff_state(journal: UpdateJournal, journal_path: Path) -> dict[str, Any]:
    journal.validate(journal_path, boundary_code="application_state_untrusted")
    state_path = Path(journal.state_path)
    state = _read_json(
        state_path,
        MAX_STATE_BYTES,
        boundary_code="application_state_untrusted",
    )
    expected_path = _absolute_path(journal_path)
    transaction_path = state.get("transaction_path")
    if (
        state.get("operation_id") != journal.operation_id
        or not isinstance(transaction_path, str)
        or not _same_path(Path(transaction_path), expected_path)
    ):
        raise HelperError("application_state_mismatch")
    identity = journal_handoff_identity(journal)
    current = state.get("handoff_identity")
    pending = state.get("pending_handoff_identity")
    if identity == current and _valid_digest(current):
        if pending is not None:
            if not _valid_digest(pending):
                raise HelperError("application_state_mismatch")
            state["pending_handoff_identity"] = None
            _atomic_json(state_path, state, boundary_code="application_state_untrusted")
        return state
    if identity == pending and _valid_digest(pending) and _valid_digest(current):
        state["handoff_identity"] = pending
        state["pending_handoff_identity"] = None
        _atomic_json(state_path, state, boundary_code="application_state_untrusted")
        return state
    raise HelperError("application_state_mismatch")


def _transition_handoff_state(
    journal: UpdateJournal,
    journal_path: Path,
    *,
    previous_identity: str,
) -> str:
    """Publish an authority-changing journal update with crash reconciliation."""

    journal.validate(journal_path, boundary_code="application_state_untrusted")
    state_path = Path(journal.state_path)
    state = _read_json(
        state_path,
        MAX_STATE_BYTES,
        boundary_code="application_state_untrusted",
    )
    transaction_path = state.get("transaction_path")
    identity = journal_handoff_identity(journal)
    if (
        state.get("operation_id") != journal.operation_id
        or not isinstance(transaction_path, str)
        or not _same_path(Path(transaction_path), journal_path)
        or state.get("handoff_identity") != previous_identity
        or state.get("pending_handoff_identity") is not None
        or not _valid_digest(previous_identity)
    ):
        raise HelperError("application_state_mismatch")
    if identity == previous_identity:
        journal.save(journal_path)
        return identity
    state["pending_handoff_identity"] = identity
    _atomic_json(state_path, state, boundary_code="application_state_untrusted")
    journal.save(journal_path)
    promoted = _read_json(state_path, MAX_STATE_BYTES)
    if (
        promoted.get("operation_id") != journal.operation_id
        or promoted.get("transaction_path") != transaction_path
        or promoted.get("handoff_identity") != previous_identity
        or promoted.get("pending_handoff_identity") != identity
    ):
        raise HelperError("application_state_mismatch")
    promoted["handoff_identity"] = identity
    promoted["pending_handoff_identity"] = None
    _atomic_json(state_path, promoted, boundary_code="application_state_untrusted")
    _validate_handoff_state(journal, journal_path)
    return identity


def bind_handoff_state(journal: UpdateJournal, journal_path: Path) -> str:
    """Publish one prepared journal identity before recovery can be registered."""

    journal.validate(journal_path, boundary_code="application_state_untrusted")
    state_path = Path(journal.state_path)
    state = _read_json(
        state_path,
        MAX_STATE_BYTES,
        boundary_code="application_state_untrusted",
    )
    transaction_path = state.get("transaction_path")
    identity = journal_handoff_identity(journal)
    if (
        state.get("operation_id") != journal.operation_id
        or state.get("phase") != "restart_required"
        or not isinstance(transaction_path, str)
        or not _same_path(Path(transaction_path), journal_path)
        or state.get("handoff_identity") not in {None, identity}
        or state.get("pending_handoff_identity") is not None
    ):
        raise HelperError("application_state_mismatch")
    state["handoff_identity"] = identity
    state["pending_handoff_identity"] = None
    state["completed_handoff_identity"] = None
    _atomic_json(state_path, state, boundary_code="application_state_untrusted")
    _validate_handoff_state(journal, journal_path)
    return identity


def transaction_outcome(path: Path) -> str:
    try:
        phase = UpdateJournal.load(path).phase
    except HelperError:
        return "failed"
    if phase is HelperPhase.COMMITTED:
        return "installed"
    if phase is HelperPhase.ROLLED_BACK:
        return "rolled_back"
    return "pending"


def _runonce_key() -> str:
    override = os.environ.get("ATC_SMOKE_UPDATE_RUNONCE_KEY")
    if override is None:
        return WINDOWS_RUNONCE_KEY
    if os.environ.get(SMOKE_FLAG) != "1" or not override.startswith(
        "Software\\AllTheContext\\Smoke\\"
    ):
        raise HelperError("runonce_override_invalid")
    return override


def register_recovery(helper: Path, journal: Path, operation_id: str) -> None:
    if platform.system() != "Windows":
        raise HelperError("windows_required")
    loaded = UpdateJournal.load(journal)
    _validate_handoff_state(loaded, journal)
    expected_helper = journal.parent / "AllTheContextUpdater.exe"
    if loaded.operation_id != operation_id or not _same_path(helper, expected_helper):
        raise HelperError("recovery_identity_invalid")
    if not _verified(helper, loaded.recovery_helper_sha256, loaded.recovery_helper_size):
        raise HelperError("recovery_helper_untrusted")
    winreg = windows_registry()

    command = subprocess.list2cmdline((str(helper), "--journal", str(journal)))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _runonce_key()) as key:
        winreg.SetValueEx(
            key,
            f"AllTheContextUpdate-{operation_id}",
            0,
            winreg.REG_SZ,
            command,
        )


def unregister_recovery(operation_id: str) -> None:
    if platform.system() != "Windows":
        return
    winreg = windows_registry()

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _runonce_key(), 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, f"AllTheContextUpdate-{operation_id}")
    except FileNotFoundError:
        return


def _child_environment(journal: UpdateJournal, *, health: bool = False) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "PYINSTALLER_RESET_ENVIRONMENT": "1",
            "ATC_CORE_DATA_DIR": str(Path(journal.database_path).parent),
            "ATC_CORE_HOST": journal.core_host,
            "ATC_CORE_PORT": str(journal.core_port),
            "ATC_UPDATE_OPERATION": journal.operation_id,
        }
    )
    if health:
        # This flag suppresses capture scheduling in the probe process only;
        # startup recovery never treats an environment variable as authority.
        environment["ATC_UPDATE_HEALTH_OPERATION"] = journal.operation_id
    else:
        environment.pop("ATC_UPDATE_HEALTH_OPERATION", None)
    return environment


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def launch_recovery_helper(helper: Path, journal: Path) -> None:
    loaded = UpdateJournal.load(journal)
    _validate_handoff_state(loaded, journal)
    expected_helper = journal.parent / "AllTheContextUpdater.exe"
    if not _same_path(helper, expected_helper) or not _verified(
        helper,
        loaded.recovery_helper_sha256,
        loaded.recovery_helper_size,
    ):
        raise HelperError("recovery_helper_untrusted")
    environment = os.environ.copy()
    environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    subprocess.Popen(
        (str(helper), "--journal", str(journal)),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_creation_flags(),
        cwd=helper.parent,
    )


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        from ctypes import wintypes

        kernel32 = windows_dll("kernel32")
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        synchronize = 0x00100000
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            wait_timeout = 0x00000102
            return bool(kernel32.WaitForSingleObject(handle, 0) == wait_timeout)
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_for_parent(pid: int) -> None:
    deadline = time.monotonic() + PARENT_EXIT_TIMEOUT_SECONDS
    while _process_exists(pid):
        if time.monotonic() >= deadline:
            raise HelperError("parent_exit_timeout")
        time.sleep(0.1)
    # The PyInstaller bootloader can retain the executable briefly after the
    # Python child exits. The bounded apply retry below handles that tail.
    time.sleep(0.2)


def _verified(path: Path, digest: str, size: int) -> bool:
    if (
        not _valid_digest(digest)
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size <= 0
    ):
        return False
    try:
        path_stat = _plain_file_stat(path, "trusted_file_invalid")
        if path_stat.st_size != size:
            return False
        digest_value = hashlib.sha256()
        actual_size = 0
        with path.open("rb") as stream:
            if not _same_file(path_stat, os.fstat(stream.fileno())):
                return False
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest_value.update(chunk)
                actual_size += len(chunk)
            if not _same_file(path_stat, os.fstat(stream.fileno())):
                return False
        final_stat = _plain_file_stat(path, "trusted_file_invalid")
        return bool(
            _same_file(path_stat, final_stat)
            and actual_size == size
            and digest_value.hexdigest() == digest
        )
    except (HelperError, OSError, ValueError):
        return False


def _run_bounded(command: tuple[str, ...], environment: dict[str, str]) -> int:
    try:
        completed = subprocess.run(
            command,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=PROCESS_TIMEOUT_SECONDS,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HelperError("replacement_process_failed") from exc
    return completed.returncode


def _validate_replacement(journal: UpdateJournal, journal_path: Path) -> None:
    journal.validate(journal_path, boundary_code="install_target_untrusted")
    expected = journal_path.parent / "replacement" / "AllTheContextSetup.exe"
    replacement = Path(journal.replacement_path)
    if not _same_path(replacement, expected) or not _verified(
        replacement,
        journal.replacement_sha256,
        journal.replacement_size,
    ):
        raise HelperError("replacement_untrusted")


def _validate_application(journal: UpdateJournal, digest: str, size: int) -> None:
    journal_path = Path(journal.helper_path).parent / "journal.json"
    journal.validate(journal_path, boundary_code="application_untrusted")
    application = Path(journal.application_path)
    expected = _install_directory() / "AllTheContext.exe"
    if not _same_path(application, expected) or not _verified(application, digest, size):
        raise HelperError("application_untrusted")


def _apply_replacement(journal: UpdateJournal, journal_path: Path) -> None:
    application = Path(journal.application_path)
    _validate_replacement(journal, journal_path)
    journal.phase = HelperPhase.CUTOVER_STARTED
    journal.save(journal_path)
    report = journal_path.parent / "apply-report.json"
    _unlink_plain_file_if_present(report, "transaction_report_untrusted")
    environment = _child_environment(journal)
    deadline = time.monotonic() + PARENT_EXIT_TIMEOUT_SECONDS
    while True:
        code = _run_bounded(
            (str(journal.replacement_path), "--apply-update", str(report)),
            environment,
        )
        _validate_install_targets(journal, "install_target_untrusted")
        if code == 0 and _verified(
            application, journal.replacement_sha256, journal.replacement_size
        ):
            break
        if time.monotonic() >= deadline:
            raise HelperError("binary_cutover_failed")
        time.sleep(0.25)
    try:
        value = _read_json(report, MAX_JOURNAL_BYTES)
        expected_keys = {
            "status",
            "version",
            "application",
            "application_sha256",
            "application_size",
            "mcp",
            "mcp_sha256",
            "mcp_size",
            "recovery",
            "recovery_sha256",
            "recovery_size",
            "update_helper",
            "update_helper_sha256",
            "update_helper_size",
        }
        if (
            set(value) != expected_keys
            or value.get("status") != "installed"
            or value.get("version") != journal.target_version
            or not _same_path(Path(str(value.get("application"))), application)
            or value.get("application_sha256") != journal.replacement_sha256
            or value.get("application_size") != journal.replacement_size
            or not _same_path(Path(str(value.get("mcp"))), Path(journal.mcp_path))
            or not _valid_digest(value.get("mcp_sha256"))
            or isinstance(value.get("mcp_size"), bool)
            or not isinstance(value.get("mcp_size"), int)
            or not _verified(
                Path(journal.mcp_path),
                cast(str, value.get("mcp_sha256")),
                cast(int, value.get("mcp_size")),
            )
            or not _same_path(Path(str(value.get("recovery"))), Path(journal.recovery_path))
            or not _valid_digest(value.get("recovery_sha256"))
            or isinstance(value.get("recovery_size"), bool)
            or not isinstance(value.get("recovery_size"), int)
            or not _verified(
                Path(journal.recovery_path),
                cast(str, value.get("recovery_sha256")),
                cast(int, value.get("recovery_size")),
            )
            or not _same_path(
                Path(str(value.get("update_helper"))),
                Path(journal.stable_update_helper_path),
            )
            or not _valid_digest(value.get("update_helper_sha256"))
            or isinstance(value.get("update_helper_size"), bool)
            or not isinstance(value.get("update_helper_size"), int)
            or not _verified(
                Path(journal.stable_update_helper_path),
                cast(str, value.get("update_helper_sha256")),
                cast(int, value.get("update_helper_size")),
            )
        ):
            raise HelperError("apply_report_invalid")
    finally:
        _unlink_plain_file_if_present(report, "transaction_report_untrusted")
    journal.phase = HelperPhase.BINARY_REPLACED
    journal.last_error_code = None
    journal.save(journal_path)


def _verify_diagnostics(journal: UpdateJournal, journal_path: Path) -> None:
    _validate_application(journal, journal.replacement_sha256, journal.replacement_size)
    report = journal_path.parent / "diagnostics.json"
    _unlink_plain_file_if_present(report, "transaction_report_untrusted")
    if (
        _run_bounded(
            (journal.application_path, "--diagnostics", str(report)),
            _child_environment(journal),
        )
        != 0
    ):
        raise HelperError("diagnostics_failed")
    try:
        value = _read_json(report, MAX_JOURNAL_BYTES)
        if (
            value.get("application") != "All The Context"
            or value.get("version") != journal.target_version
            or value.get("frozen") is not True
            or value.get("mcp_helper_bundled") is not True
            or value.get("recovery_helper_bundled") is not True
            or value.get("update_helper_bundled") is not True
        ):
            raise HelperError("diagnostics_failed")
    finally:
        _unlink_plain_file_if_present(report, "transaction_report_untrusted")
    journal.phase = HelperPhase.DIAGNOSTICS_PASSED
    journal.last_error_code = None
    journal.save(journal_path)


def _verify_health(journal: UpdateJournal, journal_path: Path) -> None:
    _validate_application(journal, journal.replacement_sha256, journal.replacement_size)
    report = journal_path.parent / "health.json"
    _unlink_plain_file_if_present(report, "transaction_report_untrusted")
    if (
        _run_bounded(
            (journal.application_path, "--update-health-check", str(report)),
            _child_environment(journal, health=True),
        )
        != 0
    ):
        raise HelperError("health_check_failed")
    try:
        value = _read_json(report, MAX_JOURNAL_BYTES)
        if value != {
            "component": "core",
            "health": "ok",
            "version": journal.target_version,
        }:
            raise HelperError("health_check_failed")
    finally:
        _unlink_plain_file_if_present(report, "transaction_report_untrusted")
    journal.phase = HelperPhase.HEALTH_PASSED
    journal.last_error_code = None
    journal.save(journal_path)


def _copy_verified(
    source: Path,
    target: Path,
    digest: str,
    size: int,
    *,
    target_root: Path,
) -> None:
    _validate_write_target(target, target_root, "rollback_target_invalid")
    temporary = target.with_name(f"{target.name}.atc-rollback-new")
    _unlink_plain_file_if_present(temporary, "rollback_target_invalid")
    source_stat = _plain_file_stat(source, "rollback_source_invalid")
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            if not _same_file(source_stat, os.fstat(input_stream.fileno())):
                raise HelperError("rollback_source_changed")
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            if not _same_file(source_stat, os.fstat(input_stream.fileno())):
                raise HelperError("rollback_source_changed")
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if not _verified(temporary, digest, size):
            raise HelperError("rollback_copy_invalid")
        if not _same_file(
            source_stat,
            _plain_file_stat(source, "rollback_source_changed"),
        ):
            raise HelperError("rollback_source_changed")
        _validate_write_target(target, target_root, "rollback_target_invalid")
        _validate_write_target(temporary, target_root, "rollback_target_invalid")
        temporary.replace(target)
    except BaseException:
        with suppress(HelperError, OSError):
            _unlink_plain_file_if_present(temporary, "rollback_target_invalid")
        raise


def _refresh_database_backup(journal: UpdateJournal, journal_path: Path) -> None:
    """Capture the final stopped-Core database before any replacement can run."""

    journal.validate(journal_path, boundary_code="database_backup_invalid")
    previous_identity = journal_handoff_identity(journal)
    database = Path(journal.database_path)
    initial_backup = Path(journal.database_backup_path)
    backup = initial_backup.parent / f"core-{journal.operation_id}-stopped.sqlite3"
    data_dir = _data_directory()
    _validate_write_target(database, data_dir, "database_unavailable")
    _validate_write_target(backup, data_dir, "database_backup_invalid")
    temporary = backup.with_name(f"{backup.name}.{journal.operation_id}.atc-new")
    _validate_write_target(temporary, data_dir, "database_backup_invalid")
    _unlink_plain_file_if_present(temporary, "database_backup_invalid")
    try:
        source = sqlite3.connect(database, timeout=10)
        try:
            destination = sqlite3.connect(temporary)
            try:
                source.execute("PRAGMA busy_timeout=10000")
                source.backup(destination)
                if destination.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise HelperError("database_backup_invalid")
            finally:
                destination.close()
        finally:
            source.close()
        digest, size = _sha256(temporary)
        if size <= 0:
            raise HelperError("database_backup_invalid")
        _validate_write_target(temporary, data_dir, "database_backup_invalid")
        _validate_write_target(backup, data_dir, "database_backup_invalid")
        temporary.replace(backup)
        candidate = replace(
            journal,
            database_backup_path=str(backup),
            database_backup_sha256=digest,
            database_backup_size=size,
            last_error_code=None,
        )
        _transition_handoff_state(
            candidate,
            journal_path,
            previous_identity=previous_identity,
        )
        journal.database_backup_path = candidate.database_backup_path
        journal.database_backup_sha256 = candidate.database_backup_sha256
        journal.database_backup_size = candidate.database_backup_size
        journal.last_error_code = candidate.last_error_code
        journal.updated_at = candidate.updated_at
    finally:
        _unlink_plain_file_if_present(temporary, "database_backup_invalid")


def _restore_database(journal: UpdateJournal) -> None:
    journal_path = Path(journal.helper_path).parent / "journal.json"
    journal.validate(journal_path, boundary_code="database_target_invalid")
    backup = Path(journal.database_backup_path)
    if not _verified(backup, journal.database_backup_sha256, journal.database_backup_size):
        raise HelperError("database_backup_invalid")
    data_dir = _data_directory()
    temporary = Path(journal.database_path).with_name(
        f"core.{journal.operation_id}.rollback.sqlite3"
    )
    _validate_write_target(temporary, data_dir, "database_target_invalid")
    _copy_verified(
        backup,
        temporary,
        journal.database_backup_sha256,
        journal.database_backup_size,
        target_root=data_dir,
    )
    try:
        connection = sqlite3.connect(temporary)
        try:
            result = connection.execute("PRAGMA quick_check").fetchone()
        finally:
            connection.close()
        if result is None or result[0] != "ok":
            raise HelperError("database_backup_invalid")
        database = Path(journal.database_path)
        # A failed Core may leave any SQLite sidecar behind.  In particular,
        # rollback journals can replay against the restored main file on the
        # next Core start, so they are part of the rollback boundary too.
        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = database.with_name(f"{database.name}{suffix}")
            _validate_write_target(sidecar, data_dir, "database_target_invalid")
            _unlink_plain_file_if_present(sidecar, "database_target_invalid")
        _validate_write_target(temporary, data_dir, "database_target_invalid")
        _validate_write_target(database, data_dir, "database_target_invalid")
        temporary.replace(database)
    finally:
        _unlink_plain_file_if_present(temporary, "database_target_invalid")


def _restore_binaries(journal: UpdateJournal) -> None:
    journal_path = Path(journal.helper_path).parent / "journal.json"
    journal.validate(journal_path, boundary_code="rollback_target_invalid")
    install_root = _validate_install_targets(journal, "rollback_target_invalid")
    _copy_verified(
        Path(journal.rollback_application_path),
        Path(journal.application_path),
        journal.rollback_application_sha256,
        journal.rollback_application_size,
        target_root=install_root,
    )
    if journal.rollback_mcp_path is not None:
        _copy_verified(
            Path(journal.rollback_mcp_path),
            Path(journal.mcp_path),
            cast(str, journal.rollback_mcp_sha256),
            cast(int, journal.rollback_mcp_size),
            target_root=install_root,
        )
    else:
        _validate_write_target(Path(journal.mcp_path), install_root, "rollback_target_invalid")
        _unlink_plain_file_if_present(Path(journal.mcp_path), "rollback_target_invalid")
    if journal.rollback_recovery_path is not None:
        _copy_verified(
            Path(journal.rollback_recovery_path),
            Path(journal.recovery_path),
            cast(str, journal.rollback_recovery_sha256),
            cast(int, journal.rollback_recovery_size),
            target_root=install_root,
        )
    else:
        _validate_write_target(Path(journal.recovery_path), install_root, "rollback_target_invalid")
        _unlink_plain_file_if_present(Path(journal.recovery_path), "rollback_target_invalid")
    _copy_verified(
        Path(journal.rollback_update_helper_path),
        Path(journal.stable_update_helper_path),
        journal.rollback_update_helper_sha256,
        journal.rollback_update_helper_size,
        target_root=install_root,
    )


def _update_state(
    journal: UpdateJournal,
    *,
    phase: str,
    error: str | None,
    clear_transaction: bool,
) -> None:
    path = Path(journal.state_path)
    journal_path = Path(journal.helper_path).parent / "journal.json"
    journal.validate(journal_path, boundary_code="application_state_untrusted")
    value = _read_json(path, MAX_STATE_BYTES, boundary_code="application_state_untrusted")
    if value.get("operation_id") != journal.operation_id:
        raise HelperError("application_state_mismatch")
    transaction_path = value.get("transaction_path")
    journal_identity = journal_handoff_identity(journal)
    if journal.phase in TERMINAL_PHASES and value.get("phase") != phase:
        # Terminal cleanup is the second half of a state-first publication.
        # A journal whose progress marker was changed directly must not skip
        # cutover or rollback merely because progress fields are not authority.
        raise HelperError("application_state_mismatch")
    if (
        transaction_path is None
        and clear_transaction
        and value.get("phase") == phase
        and value.get("handoff_identity") is None
        and value.get("pending_handoff_identity") is None
        and value.get("completed_handoff_identity") == journal_identity
    ):
        value.update(
            {
                "current_version": (
                    journal.target_version if phase == "installed" else journal.current_version
                ),
                "downloaded_path": None,
                "last_error": error,
                "handoff_identity": None,
                "pending_handoff_identity": None,
                "completed_handoff_identity": journal_identity,
            }
        )
        _atomic_json(path, value, boundary_code="application_state_untrusted")
        return
    value = _validate_handoff_state(journal, journal_path)
    transaction_path = value["transaction_path"]
    if not isinstance(transaction_path, str) or not _same_path(
        Path(transaction_path),
        Path(journal.helper_path).parent / "journal.json",
    ):
        raise HelperError("application_state_mismatch")
    value.update(
        {
            "phase": phase,
            "current_version": (
                journal.target_version if phase == "installed" else journal.current_version
            ),
            "downloaded_path": None,
            "last_error": error,
            "transaction_path": None if clear_transaction else transaction_path,
            "handoff_identity": None if clear_transaction else value["handoff_identity"],
            "pending_handoff_identity": None,
            "completed_handoff_identity": journal_identity if clear_transaction else None,
        }
    )
    _atomic_json(path, value, boundary_code="application_state_untrusted")


def _launch_core(journal: UpdateJournal) -> None:
    if journal.phase is HelperPhase.COMMITTED:
        digest, size = journal.replacement_sha256, journal.replacement_size
    elif journal.phase is HelperPhase.ROLLED_BACK:
        digest, size = journal.rollback_application_sha256, journal.rollback_application_size
    else:
        raise HelperError("application_phase_invalid")
    _validate_application(journal, digest, size)
    environment = _child_environment(journal)
    subprocess.Popen(
        (journal.application_path, "--core"),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=_creation_flags(),
        cwd=str(Path(journal.application_path).parent),
    )


def _commit(journal: UpdateJournal, journal_path: Path) -> None:
    _update_state(journal, phase="installed", error=None, clear_transaction=False)
    journal.phase = HelperPhase.COMMITTED
    journal.last_error_code = None
    journal.save(journal_path)
    _update_state(journal, phase="installed", error=None, clear_transaction=True)
    unregister_recovery(journal.operation_id)
    _launch_core(journal)


def _abort_before_cutover(journal: UpdateJournal, journal_path: Path, error_code: str) -> None:
    """End a failed handoff without overwriting binaries or the live database."""

    message = "The update stopped before installation; the existing app and vault are unchanged"
    # Publish an abort authority before changing state. If power is lost after
    # state publication, replay must remain on the abort path and never resume
    # the forward cutover from a pre-cutover journal phase.
    journal.phase = HelperPhase.ABORT_REQUESTED
    journal.last_error_code = error_code
    journal.save(journal_path)
    _update_state(
        journal,
        phase="rolled_back",
        error=message,
        clear_transaction=False,
    )
    _fault_after_abort_state()
    journal.phase = HelperPhase.ROLLED_BACK
    journal.last_error_code = error_code
    journal.save(journal_path)
    _update_state(
        journal,
        phase="rolled_back",
        error=message,
        clear_transaction=True,
    )
    unregister_recovery(journal.operation_id)
    if not _process_exists(journal.parent_pid):
        _launch_core(journal)


def _rollback(journal: UpdateJournal, journal_path: Path, error_code: str) -> None:
    journal.phase = HelperPhase.ROLLING_BACK
    journal.last_error_code = error_code
    journal.save(journal_path)
    try:
        _restore_binaries(journal)
        _restore_database(journal)
        message = "The update did not become healthy; the previous app and vault were restored"
        _update_state(
            journal,
            phase="rolled_back",
            error=message,
            clear_transaction=False,
        )
        journal.phase = HelperPhase.ROLLED_BACK
        journal.save(journal_path)
        _update_state(
            journal,
            phase="rolled_back",
            error=message,
            clear_transaction=True,
        )
        unregister_recovery(journal.operation_id)
        _launch_core(journal)
    except (OSError, HelperError, sqlite3.Error) as exc:
        journal.phase = HelperPhase.ROLLING_BACK
        journal.last_error_code = "rollback_retry_required"
        journal.save(journal_path)
        raise HelperError("rollback_retry_required") from exc


def _fault_after_phase(journal: UpdateJournal) -> None:
    requested = os.environ.get("ATC_UPDATE_FAULT_AFTER_PHASE")
    if requested and os.environ.get(SMOKE_FLAG) == "1" and requested == journal.phase.value:
        raise SystemExit(86)


def _fault_after_abort_state() -> None:
    if (
        os.environ.get("ATC_UPDATE_FAULT_AFTER_ABORT_STATE") == "1"
        and os.environ.get(SMOKE_FLAG) == "1"
    ):
        raise SystemExit(86)


def run_transaction(journal_path: Path) -> int:
    resolved = _absolute_path(journal_path.expanduser())
    expected_root = _data_directory() / "updates" / "transactions"
    if resolved.name != "journal.json" or not _within(resolved, expected_root):
        raise HelperError("journal_path_invalid")
    if not _valid_operation_id(resolved.parent.name):
        raise HelperError("journal_identity_invalid")
    _plain_directory_chain(resolved.parent, "journal_untrusted")
    lock_path = resolved.with_suffix(".lock")
    _plain_file_stat_if_present(lock_path, "journal_untrusted")
    lock = FileLock(str(lock_path))
    try:
        lock.acquire(timeout=0)
    except Timeout:
        return 0
    try:
        journal = UpdateJournal.load(resolved)
        if journal.phase in TERMINAL_PHASES:
            _update_state(
                journal,
                phase="installed" if journal.phase is HelperPhase.COMMITTED else "rolled_back",
                error=(
                    None
                    if journal.phase is HelperPhase.COMMITTED
                    else (
                        "The update did not become healthy; the previous app and vault were "
                        "restored"
                    )
                ),
                clear_transaction=True,
            )
            unregister_recovery(journal.operation_id)
            _launch_core(journal)
            return 0
        register_recovery(Path(journal.helper_path), resolved, journal.operation_id)
        if journal.phase is HelperPhase.ABORT_REQUESTED:
            _abort_before_cutover(journal, resolved, journal.last_error_code or "cutover_failed")
            return 2
        if journal.phase in {HelperPhase.ROLLBACK_REQUESTED, HelperPhase.ROLLING_BACK}:
            _rollback(journal, resolved, journal.last_error_code or "rollback_requested")
            return 0
        try:
            if journal.phase in {HelperPhase.PREPARED, HelperPhase.WAITING_FOR_PARENT}:
                journal.phase = HelperPhase.WAITING_FOR_PARENT
                journal.save(resolved)
                _wait_for_parent(journal.parent_pid)
                previous_identity = journal_handoff_identity(journal)
                transitioned = replace(journal, parent_pid=0)
                _transition_handoff_state(
                    transitioned,
                    resolved,
                    previous_identity=previous_identity,
                )
                journal.parent_pid = transitioned.parent_pid
                journal.updated_at = transitioned.updated_at
                _refresh_database_backup(journal, resolved)
            if journal.phase in {
                HelperPhase.WAITING_FOR_PARENT,
                HelperPhase.CUTOVER_STARTED,
            }:
                _apply_replacement(journal, resolved)
                _fault_after_phase(journal)
            if journal.phase is HelperPhase.BINARY_REPLACED:
                _verify_diagnostics(journal, resolved)
                _fault_after_phase(journal)
            if journal.phase is HelperPhase.DIAGNOSTICS_PASSED:
                _verify_health(journal, resolved)
                _fault_after_phase(journal)
            if journal.phase is HelperPhase.HEALTH_PASSED:
                _commit(journal, resolved)
            return 0
        except SystemExit:
            raise
        except (OSError, HelperError, sqlite3.Error, ValueError) as exc:
            error_code = exc.code if isinstance(exc, HelperError) else "cutover_failed"
            if journal.phase in {HelperPhase.PREPARED, HelperPhase.WAITING_FOR_PARENT}:
                _abort_before_cutover(journal, resolved, error_code)
                return 2
            _rollback(journal, resolved, error_code)
            return 2
    finally:
        lock.release()


def request_rollback(journal_path: Path) -> None:
    journal = UpdateJournal.load(journal_path)
    if journal.phase in TERMINAL_PHASES:
        return
    journal.phase = HelperPhase.ROLLBACK_REQUESTED
    journal.last_error_code = "rollback_requested"
    journal.save(journal_path)
    launch_recovery_helper(Path(journal.helper_path), journal_path)


def ensure_recovery_before_core() -> bool:
    """Return false after starting recovery when an ordinary Core must stay down."""

    if platform.system() != "Windows" or not bool(getattr(sys, "frozen", False)):
        return True
    state_path = _data_directory() / "updates" / "state.json"
    try:
        if not _plain_directory_chain_if_present(state_path.parent, "startup_state_untrusted"):
            return True
        if _plain_file_stat_if_present(state_path, "startup_state_untrusted") is None:
            if _transaction_evidence_without_state(state_path):
                raise HelperError("startup_state_missing_with_transaction")
            return True
        state = _read_json(
            state_path,
            MAX_STATE_BYTES,
            boundary_code="startup_state_untrusted",
        )
    except HelperError as error:
        _write_startup_recovery_diagnostic(
            state_path,
            status="blocked",
            code=error.code,
        )
        return False
    try:
        phase = _validate_startup_state(state)
    except HelperError:
        _write_startup_recovery_diagnostic(
            state_path,
            status="blocked",
            code="startup_state_invalid",
        )
        return False
    transaction = state.get("transaction_path")
    operation_id = state.get("operation_id")
    handoff_identity = state.get("handoff_identity")
    pending_identity = state.get("pending_handoff_identity")
    completed_identity = state.get("completed_handoff_identity")
    if any(
        identity is not None and not _valid_digest(identity)
        for identity in (handoff_identity, pending_identity, completed_identity)
    ):
        _write_startup_recovery_diagnostic(
            state_path,
            status="blocked",
            code="startup_state_invalid",
            phase=phase,
        )
        return False
    if transaction is None:
        if phase == "installing":
            identities = (
                state.get("handoff_identity"),
                state.get("pending_handoff_identity"),
                state.get("completed_handoff_identity"),
            )
            operation_is_valid = _valid_operation_id(operation_id)
            expected_transaction_dir = (
                _data_directory() / "updates" / "transactions" / cast(str, operation_id)
                if operation_is_valid and operation_id is not None
                else None
            )
            try:
                transaction_evidence = expected_transaction_dir is not None and (
                    _plain_directory_chain_if_present(
                        expected_transaction_dir, "startup_state_untrusted"
                    )
                )
            except HelperError:
                _write_startup_recovery_diagnostic(
                    state_path,
                    status="blocked",
                    code="startup_state_untrusted",
                    phase=phase,
                )
                return False
            if (
                not operation_is_valid
                or any(identity is not None for identity in identities)
                or transaction_evidence
            ):
                _write_startup_recovery_diagnostic(
                    state_path,
                    status="blocked",
                    code="startup_state_active_without_transaction",
                    phase=phase,
                )
                return False
            if not _pre_cutover_staging_evidence(cast(str, operation_id), state):
                _write_startup_recovery_diagnostic(
                    state_path,
                    status="blocked",
                    code="pre_cutover_evidence_missing",
                    phase=phase,
                )
                return False
            state.update(
                {
                    "phase": "error",
                    "last_error": "The update stopped before installation and was reset safely",
                    "downloaded_path": None,
                    "backup_path": None,
                    "operation_id": None,
                    "manifest_identity": None,
                    "transaction_path": None,
                    "handoff_identity": None,
                    "pending_handoff_identity": None,
                    "completed_handoff_identity": None,
                }
            )
            try:
                _atomic_json(state_path, state, boundary_code="startup_state_untrusted")
            except (HelperError, OSError):
                _write_startup_recovery_diagnostic(
                    state_path,
                    status="blocked",
                    code="startup_state_reset_failed",
                    phase=phase,
                )
                return False
            _write_startup_recovery_diagnostic(
                state_path,
                status="cleared",
                code="pre_cutover_install_reset",
                phase="error",
            )
            return True
        if phase in {"installing", "restart_required"} or any(
            state.get(name) is not None
            for name in (
                "handoff_identity",
                "pending_handoff_identity",
            )
        ):
            _write_startup_recovery_diagnostic(
                state_path,
                status="blocked",
                code="startup_state_active_without_transaction",
                phase=phase,
            )
            return False
        diagnostic_path = _startup_recovery_diagnostic_path(state_path)
        if _startup_recovery_diagnostic_exists(diagnostic_path):
            _write_startup_recovery_diagnostic(
                state_path,
                status="cleared",
                code="none",
                phase=phase,
            )
        return True
    if not isinstance(transaction, str) or not _valid_operation_id(operation_id):
        _write_startup_recovery_diagnostic(
            state_path,
            status="blocked",
            code="startup_state_invalid",
            phase=phase,
        )
        return False
    if phase not in STARTUP_RECOVERY_TRANSACTION_PHASES:
        _write_startup_recovery_diagnostic(
            state_path,
            status="blocked",
            code="startup_state_invalid",
            phase=phase,
        )
        return False
    if completed_identity is not None:
        _write_startup_recovery_diagnostic(
            state_path,
            status="blocked",
            code="startup_state_invalid",
            phase=phase,
        )
        return False
    if handoff_identity is None:
        if pending_identity is not None:
            _write_startup_recovery_diagnostic(
                state_path,
                status="blocked",
                code="startup_state_invalid",
                phase=phase,
            )
            return False
        expected = (
            _data_directory()
            / "updates"
            / "transactions"
            / cast(str, operation_id)
            / "journal.json"
        )
        try:
            transaction_matches = _same_path(Path(transaction), expected)
            if transaction_matches:
                _plain_directory_chain(expected.parent, "startup_state_untrusted")
                _plain_file_stat(expected, "startup_state_untrusted")
        except (HelperError, OSError, ValueError):
            transaction_matches = False
        if state.get("phase") != "restart_required" or not transaction_matches:
            _write_startup_recovery_diagnostic(
                state_path,
                status="blocked",
                code="startup_state_invalid",
                phase=phase,
            )
            return False
        # No helper from this schema can cross cutover without the parent-published
        # binding, so an interrupted preparation can safely return to the old app.
        state.update(
            {
                "phase": "error",
                "last_error": "The update stopped before installation and was reset safely",
                "downloaded_path": None,
                "backup_path": None,
                "operation_id": None,
                "manifest_identity": None,
                "transaction_path": None,
                "handoff_identity": None,
                "pending_handoff_identity": None,
                "completed_handoff_identity": None,
            }
        )
        try:
            _atomic_json(state_path, state, boundary_code="startup_state_untrusted")
        except (HelperError, OSError):
            _write_startup_recovery_diagnostic(
                state_path,
                status="blocked",
                code="startup_state_reset_failed",
                phase=phase,
            )
            return False
        _write_startup_recovery_diagnostic(
            state_path,
            status="cleared",
            code="unbound_preparation_reset",
            phase="error",
        )
        return True
    expected = (
        _data_directory() / "updates" / "transactions" / cast(str, operation_id) / "journal.json"
    )
    try:
        transaction_matches = _same_path(Path(transaction), expected)
        _plain_directory_chain(expected.parent, "startup_state_untrusted")
        _plain_file_stat(expected, "startup_state_untrusted")
        if not transaction_matches:
            raise HelperError("startup_state_mismatch")
        journal_path = expected
        journal = UpdateJournal.load(journal_path)
        _validate_handoff_state(journal, journal_path)
    except (HelperError, OSError) as error:
        if isinstance(error, OSError):
            diagnostic_code = "journal_unreadable"
        elif error.code in {
            "startup_state_untrusted",
            "startup_state_mismatch",
            "journal_untrusted",
            "journal_path_untrusted",
            "application_state_untrusted",
        }:
            diagnostic_code = (
                error.code
                if error.code in {"startup_state_untrusted", "startup_state_mismatch"}
                else "startup_state_untrusted"
            )
        else:
            diagnostic_code = "journal_invalid"
        _write_startup_recovery_diagnostic(
            state_path,
            status="blocked",
            code=diagnostic_code,
            phase=phase,
        )
        return False
    if journal.operation_id != operation_id:
        _write_startup_recovery_diagnostic(
            state_path,
            status="blocked",
            code="startup_state_mismatch",
            phase=phase,
        )
        return False
    if journal.phase in TERMINAL_PHASES:
        # A power loss can land after the terminal journal save but before the
        # state pointer and RunOnce entry are cleared. Let the idempotent helper
        # finish that cleanup before an ordinary Core creates a new updater.
        try:
            launch_recovery_helper(Path(journal.helper_path), journal_path)
        except (HelperError, OSError):
            _write_startup_recovery_diagnostic(
                state_path,
                status="blocked",
                code="helper_launch_failed",
                phase=phase,
            )
            return False
        return False
    # The legitimate health probe starts an embedded loopback Core through
    # ``--update-health-check`` and never re-enters this ordinary ``--core``
    # startup guard. Do not trust a forgeable environment variable here.
    try:
        launch_recovery_helper(Path(journal.helper_path), journal_path)
    except (HelperError, OSError):
        _write_startup_recovery_diagnostic(
            state_path,
            status="blocked",
            code="helper_launch_failed",
            phase=phase,
        )
        return False
    return False


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="AllTheContextUpdater")
    parser.add_argument("--journal", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        return run_transaction(arguments.journal)
    except (HelperError, OSError, sqlite3.Error):
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
