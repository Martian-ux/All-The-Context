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
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from filelock import FileLock, Timeout
from platformdirs import user_data_path

from .platform_compat import windows_dll, windows_registry
from .release_manifest import ManifestError, ReleaseVersion

JOURNAL_SCHEMA_VERSION = 1
MAX_JOURNAL_BYTES = 64 * 1024
MAX_STATE_BYTES = 64 * 1024
PROCESS_TIMEOUT_SECONDS = 90
PARENT_EXIT_TIMEOUT_SECONDS = 60
WINDOWS_RUNONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
SMOKE_FLAG = "ATC_PACKAGED_SMOKE"


class HelperPhase(StrEnum):
    PREPARED = "prepared"
    WAITING_FOR_PARENT = "waiting_for_parent"
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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.", suffix=".atc-new", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_json(path: Path, maximum_bytes: int) -> dict[str, Any]:
    try:
        if path.stat().st_size > maximum_bytes:
            raise HelperError("metadata_too_large")
        value = json.loads(path.read_text(encoding="utf-8"))
    except HelperError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise HelperError("metadata_unreadable") from exc
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


def _sha256(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise HelperError("recovery_file_unreadable") from exc
    return digest.hexdigest(), size


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
        return Path(configured).expanduser().resolve()
    return Path(user_data_path("AllTheContext", "AllTheContext", roaming=False)).resolve()


def _install_directory() -> Path:
    configured = os.environ.get("ATC_INSTALL_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data).resolve() / "Programs" / "All The Context"
    data_path = _data_directory()
    return data_path.parent / "Programs" / "All The Context"


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


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
    created_at: str
    updated_at: str
    last_error_code: str | None = None
    schema_version: int = JOURNAL_SCHEMA_VERSION

    @classmethod
    def load(cls, path: Path) -> UpdateJournal:
        value = _read_json(path, MAX_JOURNAL_BYTES)
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
        _atomic_json(path, value)

    def validate(self, path: Path) -> None:
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
            self.stable_update_helper_path,
            self.rollback_update_helper_path,
            self.database_path,
            self.database_backup_path,
            self.state_path,
            self.helper_path,
        )
        if any(not isinstance(item, str) or not item for item in path_values):
            raise HelperError("journal_path_invalid")

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
            if candidate.resolve() != expected_path.resolve():
                raise HelperError("journal_path_invalid")
        for child in (
            Path(self.replacement_path),
            Path(self.rollback_application_path),
            Path(self.rollback_update_helper_path),
            *([Path(self.rollback_mcp_path)] if self.rollback_mcp_path else []),
        ):
            if not _within(child, transaction_dir):
                raise HelperError("journal_path_invalid")
        backup = Path(self.database_backup_path)
        if not _within(backup, updates_dir / "backups"):
            raise HelperError("journal_path_invalid")


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
        environment["ATC_UPDATE_HEALTH_OPERATION"] = journal.operation_id
    else:
        environment.pop("ATC_UPDATE_HEALTH_OPERATION", None)
    return environment


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


def launch_recovery_helper(helper: Path, journal: Path) -> None:
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
    if not path.is_file():
        return False
    actual_digest, actual_size = _sha256(path)
    return actual_digest == digest and actual_size == size


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


def _apply_replacement(journal: UpdateJournal, journal_path: Path) -> None:
    application = Path(journal.application_path)
    if journal.phase is HelperPhase.CUTOVER_STARTED and _verified(
        application, journal.replacement_sha256, journal.replacement_size
    ):
        journal.phase = HelperPhase.BINARY_REPLACED
        journal.last_error_code = None
        journal.save(journal_path)
        return
    journal.phase = HelperPhase.CUTOVER_STARTED
    journal.save(journal_path)
    report = journal_path.parent / "apply-report.json"
    report.unlink(missing_ok=True)
    environment = _child_environment(journal)
    deadline = time.monotonic() + PARENT_EXIT_TIMEOUT_SECONDS
    while True:
        code = _run_bounded(
            (str(journal.replacement_path), "--apply-update", str(report)),
            environment,
        )
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
            "update_helper",
            "update_helper_sha256",
            "update_helper_size",
        }
        if (
            set(value) != expected_keys
            or value.get("status") != "installed"
            or value.get("version") != journal.target_version
            or Path(str(value.get("application"))).resolve() != application.resolve()
            or value.get("application_sha256") != journal.replacement_sha256
            or value.get("application_size") != journal.replacement_size
            or Path(str(value.get("mcp"))).resolve() != Path(journal.mcp_path).resolve()
            or not _valid_digest(value.get("mcp_sha256"))
            or isinstance(value.get("mcp_size"), bool)
            or not isinstance(value.get("mcp_size"), int)
            or not _verified(
                Path(journal.mcp_path),
                cast(str, value.get("mcp_sha256")),
                cast(int, value.get("mcp_size")),
            )
            or Path(str(value.get("update_helper"))).resolve()
            != Path(journal.stable_update_helper_path).resolve()
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
        report.unlink(missing_ok=True)
    journal.phase = HelperPhase.BINARY_REPLACED
    journal.last_error_code = None
    journal.save(journal_path)


def _verify_diagnostics(journal: UpdateJournal, journal_path: Path) -> None:
    report = journal_path.parent / "diagnostics.json"
    report.unlink(missing_ok=True)
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
            or value.get("update_helper_bundled") is not True
        ):
            raise HelperError("diagnostics_failed")
    finally:
        report.unlink(missing_ok=True)
    journal.phase = HelperPhase.DIAGNOSTICS_PASSED
    journal.last_error_code = None
    journal.save(journal_path)


def _verify_health(journal: UpdateJournal, journal_path: Path) -> None:
    report = journal_path.parent / "health.json"
    report.unlink(missing_ok=True)
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
        report.unlink(missing_ok=True)
    journal.phase = HelperPhase.HEALTH_PASSED
    journal.last_error_code = None
    journal.save(journal_path)


def _copy_verified(source: Path, target: Path, digest: str, size: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.atc-rollback-new")
    temporary.unlink(missing_ok=True)
    try:
        with source.open("rb") as input_stream, temporary.open("xb") as output_stream:
            shutil.copyfileobj(input_stream, output_stream, length=1024 * 1024)
            output_stream.flush()
            os.fsync(output_stream.fileno())
        if not _verified(temporary, digest, size):
            raise HelperError("rollback_copy_invalid")
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _refresh_database_backup(journal: UpdateJournal, journal_path: Path) -> None:
    """Capture the final stopped-Core database before any replacement can run."""

    database = Path(journal.database_path)
    initial_backup = Path(journal.database_backup_path)
    backup = initial_backup.parent / f"core-{journal.operation_id}-stopped.sqlite3"
    if not database.is_file():
        raise HelperError("database_unavailable")
    temporary = backup.with_name(f"{backup.name}.{journal.operation_id}.atc-new")
    temporary.unlink(missing_ok=True)
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
        temporary.replace(backup)
        journal.database_backup_path = str(backup)
        journal.database_backup_sha256 = digest
        journal.database_backup_size = size
        journal.last_error_code = None
        journal.save(journal_path)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_database(journal: UpdateJournal) -> None:
    backup = Path(journal.database_backup_path)
    if not _verified(backup, journal.database_backup_sha256, journal.database_backup_size):
        raise HelperError("database_backup_invalid")
    temporary = Path(journal.database_path).with_name(
        f"core.{journal.operation_id}.rollback.sqlite3"
    )
    _copy_verified(
        backup,
        temporary,
        journal.database_backup_sha256,
        journal.database_backup_size,
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
        for suffix in ("-wal", "-shm"):
            database.with_name(f"{database.name}{suffix}").unlink(missing_ok=True)
        temporary.replace(database)
    finally:
        temporary.unlink(missing_ok=True)


def _restore_binaries(journal: UpdateJournal) -> None:
    _copy_verified(
        Path(journal.rollback_application_path),
        Path(journal.application_path),
        journal.rollback_application_sha256,
        journal.rollback_application_size,
    )
    if journal.rollback_mcp_path is not None:
        _copy_verified(
            Path(journal.rollback_mcp_path),
            Path(journal.mcp_path),
            cast(str, journal.rollback_mcp_sha256),
            cast(int, journal.rollback_mcp_size),
        )
    else:
        Path(journal.mcp_path).unlink(missing_ok=True)
    _copy_verified(
        Path(journal.rollback_update_helper_path),
        Path(journal.stable_update_helper_path),
        journal.rollback_update_helper_sha256,
        journal.rollback_update_helper_size,
    )


def _update_state(
    journal: UpdateJournal,
    *,
    phase: str,
    error: str | None,
    clear_transaction: bool,
) -> None:
    path = Path(journal.state_path)
    value = _read_json(path, MAX_STATE_BYTES)
    if value.get("operation_id") != journal.operation_id:
        raise HelperError("application_state_mismatch")
    transaction_path = value.get("transaction_path")
    if transaction_path is None and clear_transaction and value.get("phase") == phase:
        value.update(
            {
                "current_version": (
                    journal.target_version if phase == "installed" else journal.current_version
                ),
                "downloaded_path": None,
                "last_error": error,
            }
        )
        _atomic_json(path, value)
        return
    if (
        not isinstance(transaction_path, str)
        or Path(transaction_path).resolve()
        != (Path(journal.helper_path).parent / "journal.json").resolve()
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
        }
    )
    _atomic_json(path, value)


def _launch_core(journal: UpdateJournal) -> None:
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
    _update_state(
        journal,
        phase="rolled_back",
        error=message,
        clear_transaction=False,
    )
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


def run_transaction(journal_path: Path) -> int:
    resolved = journal_path.expanduser().resolve()
    expected_root = _data_directory() / "updates" / "transactions"
    if resolved.name != "journal.json" or not _within(resolved, expected_root):
        raise HelperError("journal_path_invalid")
    if not _valid_operation_id(resolved.parent.name):
        raise HelperError("journal_identity_invalid")
    lock = FileLock(str(resolved.with_suffix(".lock")))
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
        if journal.phase in {HelperPhase.ROLLBACK_REQUESTED, HelperPhase.ROLLING_BACK}:
            _rollback(journal, resolved, journal.last_error_code or "rollback_requested")
            return 0
        try:
            if journal.phase in {HelperPhase.PREPARED, HelperPhase.WAITING_FOR_PARENT}:
                journal.phase = HelperPhase.WAITING_FOR_PARENT
                journal.save(resolved)
                _wait_for_parent(journal.parent_pid)
                journal.parent_pid = 0
                journal.save(resolved)
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
    if not state_path.is_file():
        return True
    try:
        state = _read_json(state_path, MAX_STATE_BYTES)
    except HelperError:
        return True
    transaction = state.get("transaction_path")
    operation_id = state.get("operation_id")
    if transaction is None:
        return True
    if not isinstance(transaction, str) or not _valid_operation_id(operation_id):
        return False
    try:
        journal_path = Path(transaction).resolve()
        journal = UpdateJournal.load(journal_path)
    except (HelperError, OSError):
        return False
    if journal.operation_id != operation_id:
        return False
    if journal.phase in TERMINAL_PHASES:
        # A power loss can land after the terminal journal save but before the
        # state pointer and RunOnce entry are cleared. Let the idempotent helper
        # finish that cleanup before an ordinary Core creates a new updater.
        launch_recovery_helper(Path(journal.helper_path), journal_path)
        return False
    if os.environ.get("ATC_UPDATE_HEALTH_OPERATION") == journal.operation_id:
        return True
    launch_recovery_helper(Path(journal.helper_path), journal_path)
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
