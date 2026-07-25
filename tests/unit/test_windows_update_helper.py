from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import allthecontext.windows_update_helper as helper_module
import pytest
from allthecontext.windows_update_helper import (
    HelperError,
    HelperPhase,
    UpdateJournal,
    ensure_recovery_before_core,
    journal_failure_diagnostic,
    run_transaction,
)


def _digest(path: Path) -> tuple[str, int]:
    value = path.read_bytes()
    return hashlib.sha256(value).hexdigest(), len(value)


@dataclass
class TransactionFixture:
    journal_path: Path
    application: Path
    mcp: Path
    update_helper: Path
    database: Path
    state_path: Path
    old_application: bytes
    old_mcp: bytes
    old_update_helper: bytes
    replacement: bytes


def _transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TransactionFixture:
    data_dir = tmp_path / "data"
    install_dir = tmp_path / "installed"
    updates = data_dir / "updates"
    operation_id = "a" * 24
    transaction_dir = updates / "transactions" / operation_id
    rollback_dir = transaction_dir / "rollback"
    replacement_dir = transaction_dir / "replacement"
    backup_dir = updates / "backups"
    for directory in (install_dir, rollback_dir, replacement_dir, backup_dir):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ATC_INSTALL_DIR", str(install_dir))
    monkeypatch.setattr(helper_module.platform, "system", lambda: "Windows")

    old_application = b"old application binary"
    old_mcp = b"old mcp binary"
    old_update_helper = b"old update helper binary"
    replacement = b"new application binary"
    application = install_dir / "AllTheContext.exe"
    mcp = install_dir / "AllTheContextMCP.exe"
    stable_update_helper = install_dir / "AllTheContextUpdater.exe"
    rollback_application = rollback_dir / "AllTheContext.exe"
    rollback_mcp = rollback_dir / "AllTheContextMCP.exe"
    rollback_update_helper = rollback_dir / "AllTheContextUpdater.exe"
    replacement_path = replacement_dir / "AllTheContextSetup.exe"
    helper_path = transaction_dir / "AllTheContextUpdater.exe"
    application.write_bytes(old_application)
    mcp.write_bytes(old_mcp)
    stable_update_helper.write_bytes(old_update_helper)
    rollback_application.write_bytes(old_application)
    rollback_mcp.write_bytes(old_mcp)
    rollback_update_helper.write_bytes(old_update_helper)
    replacement_path.write_bytes(replacement)
    helper_path.write_bytes(b"independent helper")

    database = data_dir / "core.sqlite3"
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE facts(value TEXT NOT NULL)")
        connection.execute("INSERT INTO facts VALUES ('before')")
        connection.commit()
    finally:
        connection.close()
    database_backup = backup_dir / "core-0.1.0-before-0.2.0.sqlite3"
    shutil.copy2(database, database_backup)
    replacement_digest, replacement_size = _digest(replacement_path)
    rollback_digest, rollback_size = _digest(rollback_application)
    rollback_mcp_digest, rollback_mcp_size = _digest(rollback_mcp)
    rollback_update_digest, rollback_update_size = _digest(rollback_update_helper)
    backup_digest, backup_size = _digest(database_backup)
    state_path = updates / "state.json"
    journal_path = transaction_dir / "journal.json"
    state_path.write_text(
        json.dumps(
            {
                "phase": "restart_required",
                "current_version": "0.1.0",
                "offered_version": "0.2.0",
                "mandatory": False,
                "release_notes_url": None,
                "downloaded_path": str(updates / "staging" / operation_id / "artifact.zip"),
                "backup_path": str(database_backup),
                "last_checked_at": None,
                "last_error": None,
                "operation_id": operation_id,
                "transaction_path": str(journal_path),
                "recovery_attempts": 1,
            }
        ),
        encoding="utf-8",
    )
    now = "2026-07-22T12:00:00+00:00"
    UpdateJournal(
        operation_id=operation_id,
        phase=HelperPhase.PREPARED,
        current_version="0.1.0",
        target_version="0.2.0",
        parent_pid=0,
        application_path=str(application),
        replacement_path=str(replacement_path),
        replacement_sha256=replacement_digest,
        replacement_size=replacement_size,
        rollback_application_path=str(rollback_application),
        rollback_application_sha256=rollback_digest,
        rollback_application_size=rollback_size,
        mcp_path=str(mcp),
        rollback_mcp_path=str(rollback_mcp),
        rollback_mcp_sha256=rollback_mcp_digest,
        rollback_mcp_size=rollback_mcp_size,
        stable_update_helper_path=str(stable_update_helper),
        rollback_update_helper_path=str(rollback_update_helper),
        rollback_update_helper_sha256=rollback_update_digest,
        rollback_update_helper_size=rollback_update_size,
        database_path=str(database),
        database_backup_path=str(database_backup),
        database_backup_sha256=backup_digest,
        database_backup_size=backup_size,
        state_path=str(state_path),
        helper_path=str(helper_path),
        core_host="127.0.0.1",
        core_port=7337,
        created_at=now,
        updated_at=now,
    ).save(journal_path)
    return TransactionFixture(
        journal_path,
        application,
        mcp,
        stable_update_helper,
        database,
        state_path,
        old_application,
        old_mcp,
        old_update_helper,
        replacement,
    )


def _fake_commands(
    fixture: TransactionFixture,
    *,
    health_result: int = 0,
) -> Callable[[tuple[str, ...], dict[str, str]], int]:
    def run(command: tuple[str, ...], _environment: dict[str, str]) -> int:
        if "--apply-update" in command:
            fixture.application.write_bytes(fixture.replacement)
            fixture.mcp.write_bytes(b"new mcp binary")
            fixture.update_helper.write_bytes(b"new update helper binary")
            application_digest, application_size = _digest(fixture.application)
            mcp_digest, mcp_size = _digest(fixture.mcp)
            update_digest, update_size = _digest(fixture.update_helper)
            report = Path(command[-1])
            report.write_text(
                json.dumps(
                    {
                        "status": "installed",
                        "version": "0.2.0",
                        "application": str(fixture.application),
                        "application_sha256": application_digest,
                        "application_size": application_size,
                        "mcp": str(fixture.mcp),
                        "mcp_sha256": mcp_digest,
                        "mcp_size": mcp_size,
                        "update_helper": str(fixture.update_helper),
                        "update_helper_sha256": update_digest,
                        "update_helper_size": update_size,
                    }
                ),
                encoding="utf-8",
            )
            return 0
        if "--diagnostics" in command:
            Path(command[-1]).write_text(
                json.dumps(
                    {
                        "application": "All The Context",
                        "version": "0.2.0",
                        "frozen": True,
                        "mcp_helper_bundled": True,
                        "update_helper_bundled": True,
                    }
                ),
                encoding="utf-8",
            )
            return 0
        if "--update-health-check" in command:
            connection = sqlite3.connect(fixture.database)
            try:
                connection.execute("CREATE TABLE migrated(version TEXT NOT NULL)")
                connection.execute("INSERT INTO migrated VALUES ('0.2.0')")
                connection.commit()
            finally:
                connection.close()
            if health_result == 0:
                Path(command[-1]).write_text(
                    json.dumps({"component": "core", "health": "ok", "version": "0.2.0"}),
                    encoding="utf-8",
                )
            return health_result
        raise AssertionError(command)

    return run


def _isolate_runtime(monkeypatch: pytest.MonkeyPatch, launched: list[str]) -> None:
    monkeypatch.setattr(helper_module, "register_recovery", lambda *_args: None)
    monkeypatch.setattr(helper_module, "unregister_recovery", lambda *_args: None)
    monkeypatch.setattr(helper_module, "_wait_for_parent", lambda _pid: None)
    monkeypatch.setattr(
        helper_module,
        "_launch_core",
        lambda journal: launched.append(journal.current_version),
    )


def test_independent_helper_commits_after_real_state_and_database_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))

    assert run_transaction(fixture.journal_path) == 0
    assert fixture.application.read_bytes() == fixture.replacement
    assert UpdateJournal.load(fixture.journal_path).phase is HelperPhase.COMMITTED
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "installed"
    assert state["current_version"] == "0.2.0"
    assert state["transaction_path"] is None
    connection = sqlite3.connect(fixture.database)
    try:
        assert connection.execute("SELECT version FROM migrated").fetchone() == ("0.2.0",)
    finally:
        connection.close()
    assert launched == ["0.1.0"]

    # Simulate power loss after the terminal journal save but before the state
    # pointer was cleared; terminal replay must finish cleanup idempotently.
    state["transaction_path"] = str(fixture.journal_path)
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    assert run_transaction(fixture.journal_path) == 0
    replayed_state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert replayed_state["transaction_path"] is None


def test_windows_liveness_probe_observes_without_signalling_current_process() -> None:
    assert helper_module._process_exists(os.getpid()) is True


def test_power_loss_after_binary_replacement_resumes_from_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    monkeypatch.setenv("ATC_PACKAGED_SMOKE", "1")
    monkeypatch.setenv("ATC_UPDATE_FAULT_AFTER_PHASE", "binary_replaced")

    with pytest.raises(SystemExit, match="86"):
        run_transaction(fixture.journal_path)
    assert fixture.application.read_bytes() == fixture.replacement
    assert UpdateJournal.load(fixture.journal_path).phase is HelperPhase.BINARY_REPLACED

    monkeypatch.delenv("ATC_UPDATE_FAULT_AFTER_PHASE")
    assert run_transaction(fixture.journal_path) == 0
    assert UpdateJournal.load(fixture.journal_path).phase is HelperPhase.COMMITTED


def test_failed_health_restores_previous_binary_mcp_and_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    connection = sqlite3.connect(fixture.database)
    try:
        connection.execute("INSERT INTO facts VALUES ('after-initial-backup')")
        connection.commit()
    finally:
        connection.close()
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(
        helper_module,
        "_run_bounded",
        _fake_commands(fixture, health_result=1),
    )

    assert run_transaction(fixture.journal_path) == 2
    assert fixture.application.read_bytes() == fixture.old_application
    assert fixture.mcp.read_bytes() == fixture.old_mcp
    assert fixture.update_helper.read_bytes() == fixture.old_update_helper
    assert UpdateJournal.load(fixture.journal_path).phase is HelperPhase.ROLLED_BACK
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "rolled_back"
    assert state["current_version"] == "0.1.0"
    assert state["transaction_path"] is None
    with sqlite3.connect(fixture.database) as connection:
        assert connection.execute("SELECT value FROM facts ORDER BY rowid").fetchall() == [
            ("before",),
            ("after-initial-backup",),
        ]
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='migrated'"
            ).fetchone()
            is None
        )
    assert launched == ["0.1.0"]


def test_failure_before_cutover_never_restores_the_older_database_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    connection = sqlite3.connect(fixture.database)
    try:
        connection.execute("INSERT INTO facts VALUES ('still-current')")
        connection.commit()
    finally:
        connection.close()
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)

    def fail_wait(_pid: int) -> None:
        raise HelperError("parent_exit_timeout")

    monkeypatch.setattr(helper_module, "_wait_for_parent", fail_wait)

    assert run_transaction(fixture.journal_path) == 2
    assert fixture.application.read_bytes() == fixture.old_application
    assert fixture.mcp.read_bytes() == fixture.old_mcp
    assert fixture.update_helper.read_bytes() == fixture.old_update_helper
    with sqlite3.connect(fixture.database) as connection:
        assert connection.execute("SELECT value FROM facts ORDER BY rowid").fetchall() == [
            ("before",),
            ("still-current",),
        ]
    journal = UpdateJournal.load(fixture.journal_path)
    assert journal.phase is HelperPhase.ROLLED_BACK
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "rolled_back"
    assert "unchanged" in state["last_error"]
    assert launched == ["0.1.0"]


def test_interrupted_rollback_stays_pending_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(
        helper_module,
        "_run_bounded",
        _fake_commands(fixture, health_result=1),
    )
    original_restore = helper_module._restore_database
    attempts = 0

    def interrupted(journal: UpdateJournal) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("database is locked")
        original_restore(journal)

    monkeypatch.setattr(helper_module, "_restore_database", interrupted)
    with pytest.raises(HelperError, match="rollback_retry_required"):
        run_transaction(fixture.journal_path)
    interrupted_journal = UpdateJournal.load(fixture.journal_path)
    assert interrupted_journal.phase is HelperPhase.ROLLING_BACK
    assert interrupted_journal.last_error_code == "rollback_retry_required"

    assert run_transaction(fixture.journal_path) == 0
    assert UpdateJournal.load(fixture.journal_path).phase is HelperPhase.ROLLED_BACK
    assert attempts == 2


def test_journal_rejects_paths_outside_per_user_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    value = json.loads(fixture.journal_path.read_text(encoding="utf-8"))
    value["rollback_application_path"] = str(tmp_path / "unrelated.exe")
    fixture.journal_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(HelperError, match="journal_path_invalid"):
        UpdateJournal.load(fixture.journal_path)


def test_helper_rejects_arbitrary_journal_location_before_creating_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(data_dir))
    outside = tmp_path / "journal.json"
    outside.write_text("{}", encoding="utf-8")
    with pytest.raises(HelperError, match="journal_path_invalid"):
        run_transaction(outside)
    assert not outside.with_suffix(".lock").exists()


def test_core_start_guard_resumes_active_transaction_and_allows_health_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal: launched.append((helper, journal)),
    )

    assert ensure_recovery_before_core() is False
    assert launched and launched[0][1] == fixture.journal_path
    monkeypatch.setenv("ATC_UPDATE_HEALTH_OPERATION", "a" * 24)
    assert ensure_recovery_before_core() is True

    monkeypatch.delenv("ATC_UPDATE_HEALTH_OPERATION")
    journal = UpdateJournal.load(fixture.journal_path)
    journal.phase = HelperPhase.COMMITTED
    journal.save(fixture.journal_path)
    assert ensure_recovery_before_core() is False
    assert len(launched) == 2


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parent_pid", "not-an-int"),
        ("core_port", True),
        ("replacement_size", "large"),
        ("schema_version", 99),
    ],
)
def test_malformed_journal_values_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = json.loads(fixture.journal_path.read_text(encoding="utf-8"))
    journal[field] = value
    fixture.journal_path.write_text(json.dumps(journal), encoding="utf-8")
    with pytest.raises(HelperError):
        UpdateJournal.load(fixture.journal_path)


def test_journal_failure_diagnostic_is_bounded_and_non_sensitive(tmp_path: Path) -> None:
    journal = tmp_path / "journal.json"
    journal.write_text(
        json.dumps(
            {
                "application_path": "sensitive-local-path",
                "last_error_code": "rollback_retry_required",
                "operation_id": "private-operation-id",
                "phase": "rollback_requested",
                "schema_version": 1,
            }
        ),
        encoding="utf-8",
    )

    diagnostic = journal_failure_diagnostic(journal)

    assert diagnostic == (
        '{"last_error_code": "rollback_retry_required", '
        '"phase": "rollback_requested", "schema_version": 1}'
    )
    assert "sensitive-local-path" not in diagnostic
    assert "private-operation-id" not in diagnostic

    journal.write_text(
        json.dumps(
            {
                "last_error_code": "x" * 1_000,
                "phase": ["not", "text"],
                "schema_version": True,
            }
        ),
        encoding="utf-8",
    )
    assert journal_failure_diagnostic(journal) == (
        '{"last_error_code": "invalid", "phase": "invalid", "schema_version": "invalid"}'
    )
