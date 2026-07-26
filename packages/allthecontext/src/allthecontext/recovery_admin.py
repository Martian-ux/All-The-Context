"""Version-matched packaged recovery and irreversible administration.

Shipped as a deliberately hidden native mode and, on Windows/macOS windowed
desktop builds, as a version-matched console recovery helper so operators can
export, restore, cut over, and purge without a Python/source checkout. These
paths require a stopped Core, work on disposable local vaults, and never
expose purge as a client/MCP permission.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from . import __version__
from .config import CoreConfig
from .export import (
    _rebuild_context_fts,
    _validate_source_blob_storage,
    create_export,
    restore_export,
)
from .storage import CoreStore, InvalidStateError

PURGE_CONFIRMATION_TEMPLATE = "PURGE {target_type} {target_id}"
VAULT_FILE_NAMES = (
    "core.sqlite3",
    "core.sqlite3-wal",
    "core.sqlite3-shm",
    "core.sqlite3-journal",
)
STAGED_DB_NAME = "core.sqlite3.atc-incoming"
ACTIVE_DB_NAME = "core.sqlite3"


class RecoveryError(RuntimeError):
    """Operator-facing recovery/admin failure with a safe message."""


@dataclass(frozen=True, slots=True)
class RecoveryPaths:
    config: CoreConfig
    active_database: Path
    recovery_root: Path

    @classmethod
    def for_data_dir(cls, data_dir: Path | None = None) -> RecoveryPaths:
        config = (
            CoreConfig.in_directory(data_dir, require_auth=True)
            if data_dir is not None
            else CoreConfig.default()
        )
        recovery_root = config.data_dir / "recovery"
        return cls(config=config, active_database=config.database_path, recovery_root=recovery_root)


def recovery_console_helper_name(system: str | None = None) -> str:
    """Installed console recovery executable name for windowed desktop OSes."""

    active = system or platform.system()
    if active == "Windows":
        return "AllTheContextRecovery.exe"
    if active == "Darwin":
        return "all-the-context-recovery"
    return "all-the-context"


def recovery_help_text() -> str:
    system = platform.system()
    helper = recovery_console_helper_name(system)
    if system == "Windows":
        reachability = (
            f"Windows (windowed GUI): run the console helper `{helper}` from cmd/PowerShell.\n"
            "  It is installed next to the app (Programs\\AllTheContext) and needs no Python."
        )
    elif system == "Darwin":
        reachability = (
            f"macOS (windowed app): run the console helper `{helper}` from Terminal.\n"
            "  It is bundled beside the app binary inside AllTheContext.app and needs no Python."
        )
    else:
        reachability = (
            "Linux (console-capable desktop binary): run `all-the-context` with the "
            "flags below.\n"
            "  The installed portable binary is the recovery surface; no separate "
            "helper is required."
        )
    return f"""All The Context recovery/admin helper
version: {__version__}

Operator-reachable packaged recovery (no Python or source checkout required).
{reachability}

Stop Core before export, restore cutover, or purge. Passphrases come from the
environment variable ATC_EXPORT_PASSPHRASE (or --recovery-passphrase-env).

  --recovery-help
      Show this help and the installed product version.

  --recovery-export DESTINATION
      Create an encrypted backup of the active vault (sources + audit).

  --recovery-restore SOURCE
      Validate and restore into an isolated destination vault.
      Options:
        --recovery-destination DIR   isolated restore directory (default under data/recovery)
        --recovery-dry-run           integrity verification only
        --recovery-cutover           after successful isolated restore, cut over active vault
        --recovery-rollback-path DIR when cutting over, keep prior vault here for rollback

  --recovery-rollback ROLLBACK_DIR
      Restore the previous active vault from a cutover rollback directory.

  --recovery-purge {{record|source}} TARGET_ID --recovery-confirmation "PURGE TYPE id"
      Irreversible administrator purge with non-resurrection tombstones.
      Confirmation must be exactly: PURGE RECORD <id> or PURGE SOURCE <id>
      (case-sensitive type word as RECORD or SOURCE).

  --recovery-purge-resume
      Resume pending secure-delete compaction jobs.

  --recovery-doctor
      Report data directory, lock state, and version without mutating vaults.

Never grant purge through MCP or ordinary client scopes. Use only fictional or
operator-owned disposable vaults for acceptance exercises.
"""


def require_core_stopped(config: CoreConfig) -> None:
    """Refuse destructive recovery while Core holds the instance lock."""
    config.prepare()
    lock = FileLock(str(config.lock_path), timeout=0)
    try:
        lock.acquire(timeout=0)
    except FileLockTimeout as error:
        raise RecoveryError(
            "Core appears to be running (instance lock held). Stop Core before recovery."
        ) from error
    else:
        lock.release()


def passphrase_from_env(env_name: str = "ATC_EXPORT_PASSPHRASE") -> str:
    value = os.environ.get(env_name, "")
    if not value or len(value) < 10:
        raise RecoveryError(
            f"Set {env_name} to a passphrase of at least 10 characters for recovery operations."
        )
    return value


def export_active_vault(
    destination: Path,
    *,
    data_dir: Path | None = None,
    passphrase: str,
    include_sources: bool = True,
    include_audit: bool = True,
) -> dict[str, Any]:
    paths = RecoveryPaths.for_data_dir(data_dir)
    require_core_stopped(paths.config)
    if not paths.active_database.is_file():
        raise RecoveryError(f"Active vault database not found: {paths.active_database}")
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = create_export(
        paths.active_database,
        destination,
        passphrase,
        include_sources=include_sources,
        include_audit=include_audit,
    )
    return {
        "version": __version__,
        "action": "export",
        "destination": str(destination),
        "manifest": manifest,
    }


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def carry_forward_purge_tombstones(
    active_database: Path, isolated_database: Path
) -> dict[str, Any]:
    """Copy terminal purge facts from the stopped active Core into an isolated vault.

    Must run before restore_export so destination tombstones block resurrection
    of pre-purge export content during isolated restore and later cutover.
    """

    if not active_database.is_file():
        return {"carried_purge_tombstones": 0, "active_present": False}
    # sqlite3 Connection context managers do not close handles; close explicitly
    # so Windows can rename/move the active vault during cutover.
    active = sqlite3.connect(str(active_database), timeout=30)
    try:
        active.row_factory = sqlite3.Row
        if not _table_exists(active, "purge_tombstones"):
            return {"carried_purge_tombstones": 0, "active_present": True}
        vault = active.execute(
            "SELECT id,name,display_timezone,created_at,schema_version FROM vaults LIMIT 1"
        ).fetchone()
        tombs = active.execute(
            "SELECT stable_id,vault_id,target_type,purged_at,"
            "replication_sequence,replication_event_id FROM purge_tombstones"
        ).fetchall()
    finally:
        active.close()
    if not tombs:
        return {"carried_purge_tombstones": 0, "active_present": True}
    isolated = sqlite3.connect(str(isolated_database), timeout=30)
    try:
        isolated.execute("PRAGMA foreign_keys = ON")
        if vault is not None:
            isolated.execute(
                "INSERT OR IGNORE INTO vaults"
                "(id,name,display_timezone,created_at,schema_version) VALUES(?,?,?,?,?)",
                (
                    str(vault["id"]),
                    str(vault["name"]),
                    str(vault["display_timezone"]),
                    str(vault["created_at"]),
                    int(vault["schema_version"]),
                ),
            )
        carried = 0
        for tomb in tombs:
            isolated.execute(
                "INSERT OR IGNORE INTO purge_tombstones"
                "(stable_id,vault_id,target_type,purged_at,"
                "replication_sequence,replication_event_id) VALUES(?,?,?,?,?,?)",
                (
                    str(tomb["stable_id"]),
                    str(tomb["vault_id"]),
                    str(tomb["target_type"]),
                    str(tomb["purged_at"]),
                    tomb["replication_sequence"],
                    tomb["replication_event_id"],
                ),
            )
            carried += 1
        isolated.commit()
    finally:
        isolated.close()
    return {"carried_purge_tombstones": carried, "active_present": True}


def verify_vault_integrity(database_path: Path) -> dict[str, Any]:
    """Run bounded, content-free recovery integrity checks on a vault database.

    Does not report `integrity: verified` unless every check succeeds.
    """

    database_path = database_path.expanduser().resolve()
    summary: dict[str, Any] = {
        "database": str(database_path),
        "ok": False,
        "integrity_check": None,
        "foreign_keys": None,
        "source_blobs": None,
        "fts": None,
        "integrity_groups": None,
        "status_sanity": None,
        "label": "failed",
    }
    if not database_path.is_file():
        summary["error"] = "database_missing"
        return summary

    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(str(database_path), timeout=30)
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        summary["integrity_check"] = integrity
        if integrity != "ok":
            return summary
        connection.execute("PRAGMA foreign_keys = ON")
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        summary["foreign_keys"] = "ok" if not violations else f"violations:{len(violations)}"
        if violations:
            return summary

        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        columns_by_table = {
            table: {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
            for table in tables
        }
        _validate_source_blob_storage(connection, tables, columns_by_table)
        blob_count = 0
        if "source_blobs" in tables:
            blob_count = int(connection.execute("SELECT COUNT(*) FROM source_blobs").fetchone()[0])
        summary["source_blobs"] = {"checked": blob_count, "ok": True}

        if "context_fts" in tables and "context_records" in tables:
            with connection:
                _rebuild_context_fts(connection, tables)
            active_records = int(
                connection.execute(
                    "SELECT COUNT(*) FROM context_records "
                    "WHERE approval_status='approved' AND deleted_at IS NULL"
                ).fetchone()[0]
            )
            fts_rows = int(connection.execute("SELECT COUNT(*) FROM context_fts").fetchone()[0])
            summary["fts"] = {
                "rebuilt": True,
                "active_records": active_records,
                "fts_rows": fts_rows,
                "ok": fts_rows == active_records,
            }
            if fts_rows != active_records:
                return summary
        else:
            summary["fts"] = {"rebuilt": False, "ok": "context_fts" not in tables}
            if "context_records" in tables and "context_fts" not in tables:
                summary["error"] = "fts_missing"
                return summary
        # Checkpoint and release handles before CoreStore reopens the same path.
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.commit()
    except (sqlite3.Error, ValueError, OSError) as error:
        summary["error"] = f"{type(error).__name__}"
        return summary
    finally:
        if connection is not None:
            connection.close()
            connection = None

    try:
        store = CoreStore(database_path)
        store.migrate()
        store.rebuild_integrity_groups()
        status = store.status()
        summary["integrity_groups"] = "rebuilt"
        # Content-free sanity: counts and mode only; never surface raw context text.
        summary["status_sanity"] = {
            "ok": True,
            "active_records": int(status.get("counts", {}).get("active_records", 0)),
            "sources": int(status.get("counts", {}).get("sources", 0)),
            "vault_initialized": bool(status.get("vault_id")),
        }
        # Light retrieval surface exercise without returning private content.
        from .models import SearchRequest
        from .retrieval import RetrievalEngine

        engine = RetrievalEngine(store)
        search = engine.search(SearchRequest(query="", limit=1))
        summary["status_sanity"]["retrieval_ok"] = hasattr(search, "items")
        if not summary["status_sanity"]["retrieval_ok"]:
            return summary
        # Drop store references so Windows can rename the file immediately after.
        del engine
        del store
    except Exception as error:
        # Recovery boundary stays fail-closed for any verification exception.
        summary["error"] = f"status_failed:{type(error).__name__}"
        summary["status_sanity"] = {"ok": False}
        return summary

    summary["ok"] = True
    summary["label"] = "verified"
    return summary


def _paths_overlap(left: Path, right: Path) -> bool:
    left_resolved = left.expanduser().resolve()
    right_resolved = right.expanduser().resolve()
    if left_resolved == right_resolved:
        return True
    try:
        left_resolved.relative_to(right_resolved)
        return True
    except ValueError:
        pass
    try:
        right_resolved.relative_to(left_resolved)
        return True
    except ValueError:
        return False


def _same_volume(left: Path, right: Path) -> bool:
    left_resolved = left.expanduser().resolve()
    right_resolved = right.expanduser().resolve()
    if os.name == "nt":
        return left_resolved.drive.casefold() == right_resolved.drive.casefold()
    try:
        return left_resolved.stat().st_dev == right_resolved.stat().st_dev
    except OSError:
        left_resolved.parent.mkdir(parents=True, exist_ok=True)
        right_resolved.parent.mkdir(parents=True, exist_ok=True)
        try:
            return left_resolved.parent.stat().st_dev == right_resolved.parent.stat().st_dev
        except OSError:
            return False


def _refuse_nonempty_directory(path: Path, *, label: str) -> None:
    if path.exists() and any(path.iterdir()):
        raise RecoveryError(f"{label} is not empty: {path}. Choose a new empty directory.")


def _materialize_single_file_database(source_db: Path, destination_db: Path) -> None:
    """Copy a consistent single-file SQLite database onto the same volume stage."""

    if destination_db.exists():
        destination_db.unlink()
    destination_db.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination_db.with_name(f"{destination_db.name}.{os.getpid()}.partial")
    temporary.unlink(missing_ok=True)
    try:
        # Explicit close order matters on Windows; context managers can leave the
        # destination handle open long enough for replace/unlink to fail.
        source = sqlite3.connect(str(source_db), timeout=30)
        try:
            source.execute("PRAGMA busy_timeout=30000")
            destination = sqlite3.connect(str(temporary), timeout=30)
            try:
                source.backup(destination)
                destination.commit()
            finally:
                destination.close()
        finally:
            source.close()
        temporary.replace(destination_db)
    finally:
        temporary.unlink(missing_ok=True)


def _list_present_vault_files(directory: Path) -> list[str]:
    return [name for name in VAULT_FILE_NAMES if (directory / name).exists()]


def _move_vault_files(source_dir: Path, destination_dir: Path) -> list[str]:
    moved: list[str] = []
    destination_dir.mkdir(parents=True, exist_ok=True)
    for name in VAULT_FILE_NAMES:
        current = source_dir / name
        if not current.exists():
            continue
        target = destination_dir / name
        if target.exists():
            raise RecoveryError(f"Cannot move {name}; destination already has that file: {target}")
        shutil.move(str(current), str(target))
        moved.append(name)
    return moved


def _restore_vault_files_from(source_dir: Path, destination_dir: Path) -> list[str]:
    restored: list[str] = []
    for name in VAULT_FILE_NAMES:
        candidate = source_dir / name
        if not candidate.exists():
            continue
        target = destination_dir / name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        shutil.copy2(candidate, target)
        restored.append(name)
    return restored


def _clear_active_sidecars(active_dir: Path) -> None:
    for name in ("core.sqlite3-wal", "core.sqlite3-shm", "core.sqlite3-journal"):
        path = active_dir / name
        if path.is_file():
            path.unlink()


def cutover_active_vault(
    isolated_root: Path,
    *,
    data_dir: Path | None = None,
    rollback_path: Path | None = None,
    inject_failure: str | None = None,
) -> dict[str, Any]:
    """Atomically replace the active vault from an isolated restore.

    Stages a single-file candidate on the active volume, preserves the prior
    active set in a rollback directory, performs deliberate replacements, and
    automatically restores the prior active set on any failure.
    """

    paths = RecoveryPaths.for_data_dir(data_dir)
    require_core_stopped(paths.config)
    isolated_root = isolated_root.expanduser().resolve()
    isolated_db = isolated_root / ACTIVE_DB_NAME
    if not isolated_db.is_file():
        raise RecoveryError(f"Isolated vault database not found: {isolated_db}")

    paths.config.prepare()
    active_dir = paths.config.data_dir.resolve()
    if _paths_overlap(isolated_root, active_dir):
        raise RecoveryError(
            "Isolated restore directory must not overlap the active data directory."
        )

    rollback_dir = (
        rollback_path.expanduser().resolve()
        if rollback_path is not None
        else paths.recovery_root / f"rollback-{os.getpid()}"
    )
    if _paths_overlap(rollback_dir, active_dir) or _paths_overlap(rollback_dir, isolated_root):
        raise RecoveryError("Rollback directory must not overlap active or isolated vault paths.")
    _refuse_nonempty_directory(rollback_dir, label="Rollback directory")
    rollback_dir.mkdir(parents=True, exist_ok=True)

    if not _same_volume(isolated_db, active_dir):
        # Still stage under active_dir so the final rename is same-volume.
        pass
    if not _same_volume(rollback_dir, active_dir):
        raise RecoveryError(
            "Rollback directory must be on the same volume as the active data directory "
            "so cutover can preserve and restore the prior vault safely."
        )

    precheck = verify_vault_integrity(isolated_db)
    if not precheck.get("ok"):
        raise RecoveryError(
            "Isolated vault failed integrity verification before cutover; "
            "refusing to replace active."
        )

    staged = active_dir / STAGED_DB_NAME
    preserved: list[str] = []
    replaced = False
    try:
        if staged.exists():
            staged.unlink()
        _materialize_single_file_database(isolated_db, staged)
        if inject_failure == "after_stage":
            raise OSError("injected cutover failure after_stage")
        staged_check = verify_vault_integrity(staged)
        if not staged_check.get("ok"):
            raise RecoveryError("Staged cutover candidate failed integrity verification.")

        preserved = _move_vault_files(active_dir, rollback_dir)
        if inject_failure == "after_preserve":
            raise OSError("injected cutover failure after_preserve")

        active_db = active_dir / ACTIVE_DB_NAME
        # Deliberate same-volume atomic replacement of the main database file.
        staged.replace(active_db)
        replaced = True
        _clear_active_sidecars(active_dir)
        if inject_failure == "after_replace":
            raise OSError("injected cutover failure after_replace")

        postcheck = verify_vault_integrity(active_db)
        if not postcheck.get("ok"):
            raise RecoveryError("Active vault failed integrity verification after cutover.")
        return {
            "active_database": str(paths.active_database),
            "rollback_directory": str(rollback_dir),
            "preserved_from_active": preserved,
            "status": "cutover_complete",
            "integrity": postcheck,
        }
    except Exception as error:
        # Automatic restore of the prior active set whenever cutover fails mid-flight.
        try:
            if replaced or (active_dir / ACTIVE_DB_NAME).exists():
                failed_root = paths.recovery_root / f"failed-cutover-{os.getpid()}"
                failed_root.mkdir(parents=True, exist_ok=True)
                for name in _list_present_vault_files(active_dir):
                    current = active_dir / name
                    target = failed_root / name
                    if current.exists():
                        shutil.move(str(current), str(target))
            if any((rollback_dir / name).exists() for name in VAULT_FILE_NAMES):
                _restore_vault_files_from(rollback_dir, active_dir)
            if (active_dir / ACTIVE_DB_NAME).is_file():
                verify_vault_integrity(active_dir / ACTIVE_DB_NAME)
        except Exception as restore_error:
            raise RecoveryError(
                f"Cutover failed ({error}); automatic restore of prior active vault "
                f"also failed ({restore_error}). Prior files may remain under the "
                f"rollback directory: {rollback_dir}"
            ) from restore_error
        finally:
            staged.unlink(missing_ok=True)
        raise RecoveryError(
            f"Cutover failed and the prior active vault was restored: {error}"
        ) from error


def rollback_active_vault(
    rollback_dir: Path,
    *,
    data_dir: Path | None = None,
    inject_failure: str | None = None,
) -> dict[str, Any]:
    paths = RecoveryPaths.for_data_dir(data_dir)
    require_core_stopped(paths.config)
    rollback_dir = rollback_dir.expanduser().resolve()
    rollback_db = rollback_dir / ACTIVE_DB_NAME
    if not rollback_db.is_file():
        raise RecoveryError(f"Rollback vault database not found: {rollback_db}")

    paths.config.prepare()
    active_dir = paths.config.data_dir.resolve()
    if _paths_overlap(rollback_dir, active_dir):
        raise RecoveryError("Rollback directory must not overlap the active data directory.")
    if not _same_volume(rollback_dir, active_dir):
        raise RecoveryError(
            "Rollback directory must be on the same volume as the active data directory."
        )

    precheck = verify_vault_integrity(rollback_db)
    if not precheck.get("ok"):
        raise RecoveryError("Rollback vault failed integrity verification; refusing to apply.")

    failed_root = paths.recovery_root / f"failed-cutover-{os.getpid()}"
    _refuse_nonempty_directory(failed_root, label="Failed-cutover preserve directory")
    failed_root.mkdir(parents=True, exist_ok=True)
    staged = active_dir / STAGED_DB_NAME
    moved_failed: list[str] = []
    restored: list[str] = []
    replaced = False
    try:
        if staged.exists():
            staged.unlink()
        _materialize_single_file_database(rollback_db, staged)
        staged_check = verify_vault_integrity(staged)
        if not staged_check.get("ok"):
            raise RecoveryError("Staged rollback candidate failed integrity verification.")
        if inject_failure == "after_stage":
            raise OSError("injected rollback failure after_stage")

        moved_failed = _move_vault_files(active_dir, failed_root)
        if inject_failure == "after_preserve":
            raise OSError("injected rollback failure after_preserve")

        staged.replace(active_dir / ACTIVE_DB_NAME)
        replaced = True
        _clear_active_sidecars(active_dir)
        if inject_failure == "after_replace":
            raise OSError("injected rollback failure after_replace")

        postcheck = verify_vault_integrity(active_dir / ACTIVE_DB_NAME)
        if not postcheck.get("ok"):
            raise RecoveryError("Active vault failed integrity verification after rollback.")
        restored = [ACTIVE_DB_NAME]
        return {
            "status": "rollback_complete",
            "rollback_directory": str(rollback_dir),
            "failed_cutover_preserved_at": str(failed_root),
            "restored": restored,
            "displaced_failed_cutover": moved_failed,
            "integrity": postcheck,
        }
    except Exception as error:
        try:
            if replaced or (active_dir / ACTIVE_DB_NAME).exists():
                # Put the just-failed candidate aside if present, then restore displaced active.
                emergency = paths.recovery_root / f"failed-rollback-{os.getpid()}"
                emergency.mkdir(parents=True, exist_ok=True)
                for name in _list_present_vault_files(active_dir):
                    current = active_dir / name
                    if current.exists():
                        shutil.move(str(current), str(emergency / name))
            if any((failed_root / name).exists() for name in VAULT_FILE_NAMES):
                _restore_vault_files_from(failed_root, active_dir)
        except Exception as restore_error:
            raise RecoveryError(
                f"Rollback failed ({error}); restoring displaced active also failed "
                f"({restore_error})."
            ) from restore_error
        finally:
            staged.unlink(missing_ok=True)
        raise RecoveryError(
            f"Rollback failed and the prior active vault was restored when possible: {error}"
        ) from error


def restore_isolated(
    source: Path,
    *,
    data_dir: Path | None = None,
    destination: Path | None = None,
    passphrase: str,
    dry_run: bool = False,
    cutover: bool = False,
    rollback_path: Path | None = None,
    inject_cutover_failure: str | None = None,
) -> dict[str, Any]:
    paths = RecoveryPaths.for_data_dir(data_dir)
    require_core_stopped(paths.config)
    source = source.expanduser().resolve()
    if not source.is_file():
        raise RecoveryError(f"Export package not found: {source}")

    if dry_run:
        verified = restore_export(source, paths.active_database, passphrase, dry_run=True)
        return {
            "version": __version__,
            "action": "restore",
            "dry_run": True,
            "valid": bool(verified.get("valid")),
            "source": str(source),
        }

    isolated_root = (
        destination.expanduser().resolve()
        if destination is not None
        else paths.recovery_root / f"restore-{os.getpid()}"
    )
    if _paths_overlap(isolated_root, paths.config.data_dir):
        raise RecoveryError(
            "Isolated restore destination must not overlap the active data directory."
        )
    _refuse_nonempty_directory(isolated_root, label="Isolated restore destination")
    isolated_root.mkdir(parents=True, exist_ok=True)
    isolated_db = isolated_root / ACTIVE_DB_NAME
    store = CoreStore(isolated_db)
    store.migrate()
    purge_carry = carry_forward_purge_tombstones(paths.active_database, isolated_db)
    result = restore_export(source, isolated_db, passphrase, dry_run=False)
    store.initialize_vault()
    while store.evaluate_staged_observations():
        pass
    store.rebuild_integrity_groups()
    integrity = verify_vault_integrity(isolated_db)
    if not integrity.get("ok"):
        raise RecoveryError(
            "Isolated restore completed file import but failed vault integrity verification."
        )
    payload: dict[str, Any] = {
        "version": __version__,
        "action": "restore",
        "dry_run": False,
        "source": str(source),
        "isolated_destination": str(isolated_root),
        "isolated_database": str(isolated_db),
        "restore": result,
        "purge_carry_forward": purge_carry,
        "integrity": integrity["label"] if integrity.get("ok") else "failed",
        "integrity_report": integrity,
        "cutover": False,
    }
    if cutover:
        payload["cutover"] = True
        payload["cutover_result"] = cutover_active_vault(
            isolated_root,
            data_dir=data_dir,
            rollback_path=rollback_path,
            inject_failure=inject_cutover_failure,
        )
    return payload


def purge_target(
    target_type: Literal["record", "source"],
    target_id: str,
    *,
    confirmation: str,
    data_dir: Path | None = None,
    compact: bool = True,
) -> dict[str, Any]:
    paths = RecoveryPaths.for_data_dir(data_dir)
    require_core_stopped(paths.config)
    if not paths.active_database.is_file():
        raise RecoveryError(f"Active vault database not found: {paths.active_database}")
    expected = PURGE_CONFIRMATION_TEMPLATE.format(
        target_type=target_type.upper(),
        target_id=target_id,
    )
    if confirmation != expected:
        raise RecoveryError(
            "Purge confirmation must match exactly "
            f"'{expected}' (unmistakable administrator phrase)."
        )
    store = CoreStore(paths.active_database)
    store.migrate()
    try:
        result = store.purge(
            target_type,
            target_id,
            confirmation=expected,
            actor="packaged-recovery-admin",
            compact=compact,
        )
    except InvalidStateError as error:
        raise RecoveryError(str(error)) from error
    return {
        "version": __version__,
        "action": "purge",
        "target_type": target_type,
        "target_id": target_id,
        "result": result,
    }


def resume_purge_jobs(*, data_dir: Path | None = None, limit: int = 10) -> dict[str, Any]:
    paths = RecoveryPaths.for_data_dir(data_dir)
    require_core_stopped(paths.config)
    store = CoreStore(paths.active_database)
    store.migrate()
    completed = store.resume_purge_jobs(limit=limit)
    return {
        "version": __version__,
        "action": "purge_resume",
        "completed": completed,
    }


def doctor(*, data_dir: Path | None = None) -> dict[str, Any]:
    paths = RecoveryPaths.for_data_dir(data_dir)
    lock_held = False
    try:
        require_core_stopped(paths.config)
    except RecoveryError:
        lock_held = True
    integrity: dict[str, Any] | None = None
    if paths.active_database.is_file() and not lock_held:
        integrity = verify_vault_integrity(paths.active_database)
    return {
        "version": __version__,
        "application": "All The Context",
        "data_dir": str(paths.config.data_dir),
        "database_path": str(paths.active_database),
        "database_present": paths.active_database.is_file(),
        "core_lock_held": lock_held,
        "recovery_root": str(paths.recovery_root),
        "recovery_surface": "packaged-console-helper",
        "recovery_console_helper": recovery_console_helper_name(),
        "python_checkout_required": False,
        "integrity": integrity,
    }


def dump_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))
