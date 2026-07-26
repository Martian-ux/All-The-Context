"""Version-matched packaged recovery and irreversible administration.

Shipped as a deliberately hidden native mode on the desktop artifact so
operators can restore and purge without a Python/source checkout. These
paths require a stopped Core, work on disposable local vaults, and never
expose purge as a client/MCP permission.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from . import __version__
from .config import CoreConfig
from .export import create_export, restore_export
from .storage import CoreStore, InvalidStateError

PURGE_CONFIRMATION_TEMPLATE = "PURGE {target_type} {target_id}"


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


def recovery_help_text() -> str:
    return f"""All The Context recovery/admin helper
version: {__version__}

Deliberately hidden native modes (no Python or source checkout required).
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


def restore_isolated(
    source: Path,
    *,
    data_dir: Path | None = None,
    destination: Path | None = None,
    passphrase: str,
    dry_run: bool = False,
    cutover: bool = False,
    rollback_path: Path | None = None,
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
    if isolated_root.exists() and any(isolated_root.iterdir()):
        raise RecoveryError(
            f"Isolated restore destination is not empty: {isolated_root}. "
            "Choose a new empty directory."
        )
    isolated_root.mkdir(parents=True, exist_ok=True)
    isolated_db = isolated_root / "core.sqlite3"
    store = CoreStore(isolated_db)
    store.migrate()
    result = restore_export(source, isolated_db, passphrase, dry_run=False)
    store.initialize_vault()
    while store.evaluate_staged_observations():
        pass
    store.rebuild_integrity_groups()
    payload: dict[str, Any] = {
        "version": __version__,
        "action": "restore",
        "dry_run": False,
        "source": str(source),
        "isolated_destination": str(isolated_root),
        "isolated_database": str(isolated_db),
        "restore": result,
        "integrity": "verified",
        "cutover": False,
    }
    if cutover:
        payload["cutover"] = True
        payload["cutover_result"] = cutover_active_vault(
            isolated_root,
            data_dir=data_dir,
            rollback_path=rollback_path,
        )
    return payload


def cutover_active_vault(
    isolated_root: Path,
    *,
    data_dir: Path | None = None,
    rollback_path: Path | None = None,
) -> dict[str, Any]:
    paths = RecoveryPaths.for_data_dir(data_dir)
    require_core_stopped(paths.config)
    isolated_root = isolated_root.expanduser().resolve()
    isolated_db = isolated_root / "core.sqlite3"
    if not isolated_db.is_file():
        raise RecoveryError(f"Isolated vault database not found: {isolated_db}")

    rollback_dir = (
        rollback_path.expanduser().resolve()
        if rollback_path is not None
        else paths.recovery_root / f"rollback-{os.getpid()}"
    )
    if rollback_dir.exists() and any(rollback_dir.iterdir()):
        raise RecoveryError(f"Rollback directory is not empty: {rollback_dir}")
    rollback_dir.mkdir(parents=True, exist_ok=True)

    paths.config.prepare()
    active_dir = paths.config.data_dir
    preserved: list[str] = []
    for name in ("core.sqlite3", "core.sqlite3-wal", "core.sqlite3-shm", "core.sqlite3-journal"):
        current = active_dir / name
        if current.exists():
            target = rollback_dir / name
            shutil.move(str(current), str(target))
            preserved.append(name)

    for name in ("core.sqlite3", "core.sqlite3-wal", "core.sqlite3-shm", "core.sqlite3-journal"):
        candidate = isolated_root / name
        if candidate.exists():
            shutil.copy2(candidate, active_dir / name)

    return {
        "active_database": str(paths.active_database),
        "rollback_directory": str(rollback_dir),
        "preserved_from_active": preserved,
        "status": "cutover_complete",
    }


def rollback_active_vault(
    rollback_dir: Path,
    *,
    data_dir: Path | None = None,
) -> dict[str, Any]:
    paths = RecoveryPaths.for_data_dir(data_dir)
    require_core_stopped(paths.config)
    rollback_dir = rollback_dir.expanduser().resolve()
    rollback_db = rollback_dir / "core.sqlite3"
    if not rollback_db.is_file():
        raise RecoveryError(f"Rollback vault database not found: {rollback_db}")

    failed_root = paths.recovery_root / f"failed-cutover-{os.getpid()}"
    failed_root.mkdir(parents=True, exist_ok=True)
    active_dir = paths.config.data_dir
    moved_failed: list[str] = []
    for name in ("core.sqlite3", "core.sqlite3-wal", "core.sqlite3-shm", "core.sqlite3-journal"):
        current = active_dir / name
        if current.exists():
            shutil.move(str(current), str(failed_root / name))
            moved_failed.append(name)
    restored: list[str] = []
    for name in ("core.sqlite3", "core.sqlite3-wal", "core.sqlite3-shm", "core.sqlite3-journal"):
        candidate = rollback_dir / name
        if candidate.exists():
            shutil.copy2(candidate, active_dir / name)
            restored.append(name)
    return {
        "status": "rollback_complete",
        "rollback_directory": str(rollback_dir),
        "failed_cutover_preserved_at": str(failed_root),
        "restored": restored,
        "displaced_failed_cutover": moved_failed,
    }


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
    return {
        "version": __version__,
        "application": "All The Context",
        "data_dir": str(paths.config.data_dir),
        "database_path": str(paths.active_database),
        "database_present": paths.active_database.is_file(),
        "core_lock_held": lock_held,
        "recovery_root": str(paths.recovery_root),
        "recovery_surface": "packaged-native-mode",
        "python_checkout_required": False,
    }


def dump_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))
