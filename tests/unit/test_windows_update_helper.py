from __future__ import annotations

import base64
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
from allthecontext.release_manifest import (
    create_manifest,
    public_key_fingerprint,
    public_key_value,
)
from allthecontext.windows_update_helper import (
    HelperError,
    HelperPhase,
    UpdateJournal,
    ensure_recovery_before_core,
    journal_failure_diagnostic,
    run_transaction,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _digest(path: Path) -> tuple[str, int]:
    value = path.read_bytes()
    return hashlib.sha256(value).hexdigest(), len(value)


def _signed_staging(
    fixture: TransactionFixture,
    monkeypatch: pytest.MonkeyPatch,
    *,
    operation_id: str = "a" * 24,
    artifact_bytes: bytes = b"verified staged artifact",
) -> tuple[dict[str, Any], Path, Path]:
    staging_dir = fixture.state_path.parent / "staging" / operation_id
    staging_dir.mkdir(parents=True)
    artifact = staging_dir / "artifact.zip"
    artifact.write_bytes(artifact_bytes)
    private_key = Ed25519PrivateKey.generate()
    public_value = public_key_value(private_key)
    keyring = fixture.state_path.parent / "test-update-keys.json"
    keyring.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "keys": [
                    {
                        "key_id": "test-release-key",
                        "algorithm": "Ed25519",
                        "public_key": public_value,
                        "public_key_sha256": public_key_fingerprint(public_value),
                        "channels": ["stable"],
                        "status": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    manifest = create_manifest(
        artifact=artifact,
        version="0.2.0",
        channel="stable",
        platform_name="windows",
        architecture="x86_64",
        artifact_url=(
            "https://updates.example.test/releases/v0.2.0/all-the-context-0.2.0-windows-x86_64.zip"
        ),
        minimum_supported_version="0.1.0-beta.7",
        mandatory=False,
        release_notes_url="https://updates.example.test/releases/v0.2.0",
        key_id="test-release-key",
        private_key=private_key,
    )
    manifest_path = staging_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "installing",
            "operation_id": operation_id,
            "transaction_path": None,
            "handoff_identity": None,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
            "offered_version": manifest["version"],
            "mandatory": manifest["mandatory"],
            "release_notes_url": manifest["release_notes_url"],
            "downloaded_path": str(artifact),
            "manifest_identity": _digest(manifest_path)[0],
        }
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(helper_module, "_update_keyring_path", lambda: keyring)
    monkeypatch.setattr(helper_module, "_update_architecture", lambda: "x86_64")
    return manifest, manifest_path, artifact


@dataclass
class TransactionFixture:
    journal_path: Path
    application: Path
    mcp: Path
    recovery: Path
    update_helper: Path
    database: Path
    state_path: Path
    old_application: bytes
    old_mcp: bytes
    old_recovery: bytes
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
    old_recovery = b"old recovery helper binary"
    old_update_helper = b"old update helper binary"
    replacement = b"new application binary"
    application = install_dir / "AllTheContext.exe"
    mcp = install_dir / "AllTheContextMCP.exe"
    recovery = install_dir / "AllTheContextRecovery.exe"
    stable_update_helper = install_dir / "AllTheContextUpdater.exe"
    rollback_application = rollback_dir / "AllTheContext.exe"
    rollback_mcp = rollback_dir / "AllTheContextMCP.exe"
    rollback_recovery = rollback_dir / "AllTheContextRecovery.exe"
    rollback_update_helper = rollback_dir / "AllTheContextUpdater.exe"
    replacement_path = replacement_dir / "AllTheContextSetup.exe"
    helper_path = transaction_dir / "AllTheContextUpdater.exe"
    application.write_bytes(old_application)
    mcp.write_bytes(old_mcp)
    recovery.write_bytes(old_recovery)
    stable_update_helper.write_bytes(old_update_helper)
    rollback_application.write_bytes(old_application)
    rollback_mcp.write_bytes(old_mcp)
    rollback_recovery.write_bytes(old_recovery)
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
    rollback_recovery_digest, rollback_recovery_size = _digest(rollback_recovery)
    rollback_update_digest, rollback_update_size = _digest(rollback_update_helper)
    recovery_helper_digest, recovery_helper_size = _digest(helper_path)
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
                "manifest_identity": None,
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
        recovery_path=str(recovery),
        rollback_recovery_path=str(rollback_recovery),
        rollback_recovery_sha256=rollback_recovery_digest,
        rollback_recovery_size=rollback_recovery_size,
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
        recovery_helper_sha256=recovery_helper_digest,
        recovery_helper_size=recovery_helper_size,
        created_at=now,
        updated_at=now,
    ).save(journal_path)
    helper_module.bind_handoff_state(UpdateJournal.load(journal_path), journal_path)
    return TransactionFixture(
        journal_path,
        application,
        mcp,
        recovery,
        stable_update_helper,
        database,
        state_path,
        old_application,
        old_mcp,
        old_recovery,
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
            fixture.recovery.write_bytes(b"new recovery helper binary")
            fixture.update_helper.write_bytes(b"new update helper binary")
            application_digest, application_size = _digest(fixture.application)
            mcp_digest, mcp_size = _digest(fixture.mcp)
            recovery_digest, recovery_size = _digest(fixture.recovery)
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
                        "recovery": str(fixture.recovery),
                        "recovery_sha256": recovery_digest,
                        "recovery_size": recovery_size,
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
                        "recovery_helper_bundled": True,
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
    state["handoff_identity"] = helper_module.journal_handoff_identity(
        UpdateJournal.load(fixture.journal_path)
    )
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


@pytest.mark.parametrize("fault_phase", ["diagnostics_passed", "health_passed"])
def test_power_loss_after_each_post_cutover_phase_replays_safely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault_phase: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    monkeypatch.setenv("ATC_PACKAGED_SMOKE", "1")
    monkeypatch.setenv("ATC_UPDATE_FAULT_AFTER_PHASE", fault_phase)

    with pytest.raises(SystemExit, match="86"):
        run_transaction(fixture.journal_path)
    assert UpdateJournal.load(fixture.journal_path).phase.value == fault_phase

    monkeypatch.delenv("ATC_UPDATE_FAULT_AFTER_PHASE")
    assert run_transaction(fixture.journal_path) == 0
    assert UpdateJournal.load(fixture.journal_path).phase is HelperPhase.COMMITTED
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "installed"
    assert state["transaction_path"] is None


def test_cutover_started_resume_reapplies_every_packaged_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    journal = UpdateJournal.load(fixture.journal_path)
    journal.phase = HelperPhase.CUTOVER_STARTED
    journal.save(fixture.journal_path)
    fixture.application.write_bytes(fixture.replacement)
    fixture.mcp.write_bytes(b"incomplete cutover")

    assert run_transaction(fixture.journal_path) == 0

    assert fixture.mcp.read_bytes() == b"new mcp binary"
    assert fixture.recovery.read_bytes() == b"new recovery helper binary"
    assert fixture.update_helper.read_bytes() == b"new update helper binary"
    assert UpdateJournal.load(fixture.journal_path).phase is HelperPhase.COMMITTED


def test_same_operation_forged_journal_is_rejected_by_state_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    forged = b"forged replacement"
    journal = json.loads(fixture.journal_path.read_text(encoding="utf-8"))
    Path(str(journal["replacement_path"])).write_bytes(forged)
    journal["replacement_sha256"] = hashlib.sha256(forged).hexdigest()
    journal["replacement_size"] = len(forged)
    fixture.journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(HelperError, match="application_state_mismatch"):
        run_transaction(fixture.journal_path)

    assert fixture.application.read_bytes() == fixture.old_application


@pytest.mark.parametrize("authority", ["parent_pid", "database_backup"])
def test_same_operation_forged_mutable_authority_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authority: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = json.loads(fixture.journal_path.read_text(encoding="utf-8"))
    if authority == "parent_pid":
        journal["parent_pid"] = 123
    else:
        replacement_backup = Path(journal["database_backup_path"]).with_name("unrelated.sqlite3")
        replacement_backup.write_bytes(b"unrelated database authority")
        digest, size = _digest(replacement_backup)
        journal["database_backup_path"] = str(replacement_backup)
        journal["database_backup_sha256"] = digest
        journal["database_backup_size"] = size
    fixture.journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(HelperError, match="application_state_mismatch"):
        run_transaction(fixture.journal_path)

    assert fixture.application.read_bytes() == fixture.old_application


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
    assert fixture.recovery.read_bytes() == fixture.old_recovery
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


def test_rollback_removes_all_sqlite_sidecars_and_preserves_unrelated_user_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    user_file = fixture.database.parent / "user-settings.json"
    user_file.write_text('{"theme":"dark"}\n', encoding="utf-8")
    stale_journal = fixture.database.with_name(f"{fixture.database.name}-journal")
    stale_journal.write_bytes(b"stale rollback journal")
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(
        helper_module,
        "_run_bounded",
        _fake_commands(fixture, health_result=1),
    )

    assert run_transaction(fixture.journal_path) == 2
    assert not stale_journal.exists()
    assert user_file.read_text(encoding="utf-8") == '{"theme":"dark"}\n'
    with sqlite3.connect(fixture.database) as connection:
        assert connection.execute("SELECT value FROM facts").fetchall() == [("before",)]
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='migrated'"
            ).fetchone()
            is None
        )


@pytest.mark.parametrize("blocked_target", ["install_root", "install_parent"])
def test_rollback_rejects_reparse_install_root_or_parent_before_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked_target: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    install_root = fixture.application.parent
    blocked_path = install_root if blocked_target == "install_root" else install_root.parent
    original_stat = helper_module._plain_directory_stat

    def reject_reparse(path: Path, code: str) -> os.stat_result:
        if path == blocked_path:
            raise HelperError(code)
        return original_stat(path, code)

    monkeypatch.setattr(helper_module, "_plain_directory_stat", reject_reparse)

    with pytest.raises(HelperError, match="rollback_target_invalid"):
        helper_module._restore_binaries(journal)
    assert fixture.application.read_bytes() == fixture.old_application


def test_write_target_creates_missing_install_tail_and_revalidates(
    tmp_path: Path,
) -> None:
    install_root = tmp_path / "Programs" / "All The Context"
    target = install_root / "AllTheContext.exe"

    helper_module._validate_write_target(target, install_root, "install_target_untrusted")

    assert install_root.is_dir()
    helper_module._plain_directory_chain(install_root, "install_target_untrusted")


def test_write_target_rejects_missing_tail_below_reparse_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_root = tmp_path / "Programs" / "All The Context"
    target = install_root / "AllTheContext.exe"
    original_stat = helper_module._plain_directory_stat

    def reject_reparse(path: Path, code: str) -> os.stat_result:
        if path == tmp_path:
            raise HelperError(code)
        return original_stat(path, code)

    monkeypatch.setattr(helper_module, "_plain_directory_stat", reject_reparse)
    with pytest.raises(HelperError, match="install_target_untrusted"):
        helper_module._validate_write_target(target, install_root, "install_target_untrusted")
    assert not install_root.exists()


def test_write_target_rejects_reparse_introduced_during_tail_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_root = tmp_path / "install"
    target = install_root / "AllTheContext.exe"
    original_chain = helper_module._plain_directory_chain
    created = False

    def reject_after_creation(path: Path, code: str) -> None:
        if path == install_root and created:
            raise HelperError(code)
        original_chain(path, code)

    original_mkdir = Path.mkdir

    def mark_created(path: Path, *args: Any, **kwargs: Any) -> None:
        nonlocal created
        original_mkdir(path, *args, **kwargs)
        if path == install_root:
            created = True

    monkeypatch.setattr(helper_module, "_plain_directory_chain", reject_after_creation)
    monkeypatch.setattr(Path, "mkdir", mark_created)
    with pytest.raises(HelperError, match="install_target_untrusted"):
        helper_module._validate_write_target(target, install_root, "install_target_untrusted")
    assert install_root.is_dir()


def test_bind_handoff_state_accepts_clean_install_target_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    install_root = tmp_path / "Programs" / "All The Context"
    journal = UpdateJournal.load(fixture.journal_path)
    monkeypatch.setenv("ATC_INSTALL_DIR", str(install_root))
    journal.application_path = str(install_root / "AllTheContext.exe")
    journal.mcp_path = str(install_root / "AllTheContextMCP.exe")
    journal.recovery_path = str(install_root / "AllTheContextRecovery.exe")
    journal.stable_update_helper_path = str(install_root / "AllTheContextUpdater.exe")
    journal.save(fixture.journal_path)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    state["pending_handoff_identity"] = None
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")

    helper_module.bind_handoff_state(journal, fixture.journal_path)

    assert install_root.is_dir()
    assert not (install_root / "AllTheContext.exe").exists()


@pytest.mark.parametrize(
    "reparse_target",
    [
        "transaction_root",
        "backup_root",
        "database_parent",
        "state_parent",
        "helper_parent",
        "install_root",
        "install_parent",
        "target_parent",
    ],
)
def test_journal_rejects_reparse_storage_path_family(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reparse_target: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    data_dir = fixture.state_path.parent.parent
    install_root = fixture.application.parent
    target_paths = {
        "transaction_root": fixture.journal_path.parent.parent,
        "backup_root": fixture.database.parent / "updates" / "backups",
        "database_parent": fixture.database.parent,
        "state_parent": fixture.state_path.parent,
        "helper_parent": fixture.journal_path.parent,
        "install_root": install_root,
        "install_parent": install_root.parent,
        "target_parent": fixture.mcp.parent,
    }
    assert target_paths["transaction_root"] == data_dir / "updates" / "transactions"
    blocked_path = target_paths[reparse_target]
    original_stat = helper_module._plain_directory_stat

    def reject_reparse(path: Path, code: str) -> os.stat_result:
        if path == blocked_path:
            raise HelperError(code)
        return original_stat(path, code)

    monkeypatch.setattr(helper_module, "_plain_directory_stat", reject_reparse)
    with pytest.raises(HelperError):
        UpdateJournal.load(fixture.journal_path)


def test_journal_rejects_reparse_helper_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    helper_path = fixture.journal_path.parent / "AllTheContextUpdater.exe"
    original_stat = helper_module._plain_file_stat

    def reject_reparse(path: Path, code: str) -> os.stat_result:
        if path == helper_path:
            raise HelperError(code)
        return original_stat(path, code)

    monkeypatch.setattr(helper_module, "_plain_file_stat", reject_reparse)
    with pytest.raises(HelperError):
        UpdateJournal.load(fixture.journal_path)


def test_rollback_rejects_reparse_database_sidecar_before_unlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    sidecar = fixture.database.with_name(f"{fixture.database.name}-journal")
    sidecar.write_bytes(b"stale rollback journal")
    journal = UpdateJournal.load(fixture.journal_path)
    original_stat = helper_module._plain_file_stat_if_present

    def reject_reparse(path: Path, code: str) -> os.stat_result | None:
        if path == sidecar and code == "database_target_invalid":
            raise HelperError(code)
        return original_stat(path, code)

    monkeypatch.setattr(helper_module, "_plain_file_stat_if_present", reject_reparse)
    with pytest.raises(HelperError, match="database_target_invalid"):
        helper_module._restore_database(journal)
    assert sidecar.exists()


@pytest.mark.parametrize("blocked_target", ["install_root", "install_parent"])
def test_forward_install_rejects_reparse_install_root_or_parent_before_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked_target: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    install_root = fixture.application.parent
    blocked_path = install_root if blocked_target == "install_root" else install_root.parent
    original_stat = helper_module._plain_directory_stat

    def reject_reparse(path: Path, code: str) -> os.stat_result:
        if path == blocked_path:
            raise HelperError(code)
        return original_stat(path, code)

    monkeypatch.setattr(helper_module, "_plain_directory_stat", reject_reparse)
    with pytest.raises(HelperError, match="install_target_untrusted"):
        helper_module._validate_replacement(journal, fixture.journal_path)


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
    assert fixture.recovery.read_bytes() == fixture.old_recovery
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


def test_pre_cutover_abort_replay_cannot_resume_forward_install(
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
    run_commands: list[tuple[str, ...]] = []
    fake_commands = _fake_commands(fixture)

    def record_commands(command: tuple[str, ...], environment: dict[str, str]) -> int:
        run_commands.append(command)
        return fake_commands(command, environment)

    monkeypatch.setattr(helper_module, "_run_bounded", record_commands)
    wait_calls = 0

    def fail_first_wait(_pid: int) -> None:
        nonlocal wait_calls
        wait_calls += 1
        if wait_calls == 1:
            raise HelperError("parent_exit_timeout")

    monkeypatch.setattr(helper_module, "_wait_for_parent", fail_first_wait)
    monkeypatch.setenv(helper_module.SMOKE_FLAG, "1")
    monkeypatch.setenv("ATC_UPDATE_FAULT_AFTER_ABORT_STATE", "1")

    with pytest.raises(SystemExit, match="86"):
        run_transaction(fixture.journal_path)

    interrupted_journal = UpdateJournal.load(fixture.journal_path)
    assert interrupted_journal.phase is HelperPhase.ABORT_REQUESTED
    interrupted_state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert interrupted_state["phase"] == "rolled_back"
    assert interrupted_state["transaction_path"] == str(fixture.journal_path)
    assert fixture.application.read_bytes() == fixture.old_application

    monkeypatch.delenv("ATC_UPDATE_FAULT_AFTER_ABORT_STATE")
    assert run_transaction(fixture.journal_path) == 2

    replayed_journal = UpdateJournal.load(fixture.journal_path)
    assert replayed_journal.phase is HelperPhase.ROLLED_BACK
    replayed_state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert replayed_state["phase"] == "rolled_back"
    assert replayed_state["transaction_path"] is None
    assert fixture.application.read_bytes() == fixture.old_application
    with sqlite3.connect(fixture.database) as connection:
        assert connection.execute("SELECT value FROM facts ORDER BY rowid").fetchall() == [
            ("before",),
            ("still-current",),
        ]
    assert run_commands == []
    assert launched == ["0.1.0"]


def test_handoff_publication_failure_aborts_without_parent_identity_wedge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    journal.parent_pid = 123
    journal.save(fixture.journal_path)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    helper_module.bind_handoff_state(journal, fixture.journal_path)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    original_atomic = helper_module._atomic_json
    failed = False

    def fail_state_publication(
        path: Path,
        value: dict[str, Any],
        *,
        boundary_code: str = "metadata_untrusted",
    ) -> None:
        nonlocal failed
        if path == fixture.state_path and not failed:
            failed = True
            raise HelperError("handoff_state_publish_failed")
        original_atomic(path, value, boundary_code=boundary_code)

    monkeypatch.setattr(helper_module, "_atomic_json", fail_state_publication)

    assert run_transaction(fixture.journal_path) == 2
    persisted = UpdateJournal.load(fixture.journal_path)
    assert persisted.phase is HelperPhase.ROLLED_BACK
    assert persisted.parent_pid == 123
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "rolled_back"
    assert state["transaction_path"] is None
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


def test_journal_rejects_missing_recovery_helper_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    value = json.loads(fixture.journal_path.read_text(encoding="utf-8"))
    value.pop("recovery_helper_sha256")
    fixture.journal_path.write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(HelperError, match="journal_shape_invalid"):
        UpdateJournal.load(fixture.journal_path)


def test_recovery_helper_swap_is_rejected_before_detached_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    helper = fixture.journal_path.parent / "AllTheContextUpdater.exe"
    helper.write_bytes(b"tampered helper")
    launches: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        helper_module.subprocess,
        "Popen",
        lambda command, **_kwargs: launches.append(command),
    )

    with pytest.raises(HelperError, match="recovery_helper_untrusted"):
        helper_module.launch_recovery_helper(helper, fixture.journal_path)
    assert launches == []


def test_replacement_swap_is_rejected_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    replacement = fixture.journal_path.parent / "replacement" / "AllTheContextSetup.exe"
    replacement.write_bytes(b"tampered replacement")
    executed: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        helper_module,
        "_run_bounded",
        lambda command, _environment: executed.append(command) or 0,
    )

    with pytest.raises(HelperError, match="replacement_untrusted"):
        helper_module._apply_replacement(
            UpdateJournal.load(fixture.journal_path),
            fixture.journal_path,
        )
    assert executed == []


def test_hardlinked_recovery_helper_is_not_trusted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    helper = fixture.journal_path.parent / "AllTheContextUpdater.exe"
    linked = helper.with_name("linked-helper.exe")
    try:
        linked.hardlink_to(helper)
    except OSError:
        pytest.skip("hardlinks are unavailable on this filesystem")

    with pytest.raises(HelperError, match="journal_path_untrusted"):
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


def test_core_start_guard_resumes_active_transaction_and_ignores_health_environment(
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
    assert ensure_recovery_before_core() is False
    assert len(launched) == 2

    monkeypatch.delenv("ATC_UPDATE_HEALTH_OPERATION")
    journal = UpdateJournal.load(fixture.journal_path)
    journal.phase = HelperPhase.COMMITTED
    journal.save(fixture.journal_path)
    assert ensure_recovery_before_core() is False
    assert len(launched) == 3


def test_core_start_guard_recovers_interrupted_unbound_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is True

    recovered = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert recovered["phase"] == "error"
    assert recovered["transaction_path"] is None
    assert recovered["operation_id"] is None
    assert recovered["handoff_identity"] is None


def test_core_start_guard_resets_pre_cutover_install_without_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    original_database = fixture.database.read_bytes()
    _signed_staging(fixture, monkeypatch)
    shutil.rmtree(fixture.journal_path.parent)
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is True
    assert launched == []
    recovered = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert recovered["phase"] == "error"
    assert recovered["operation_id"] is None
    assert recovered["transaction_path"] is None
    assert fixture.database.read_bytes() == original_database
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["status"] == "cleared"
    assert diagnostic["code"] == "pre_cutover_install_reset"


@pytest.mark.parametrize("has_transaction_evidence", [False, True])
def test_core_start_guard_handles_missing_state_without_pruning_transaction_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_transaction_evidence: bool,
) -> None:
    data_dir = tmp_path / "data"
    updates = data_dir / "updates"
    transactions = updates / "transactions"
    transactions.mkdir(parents=True)
    evidence = transactions / ("a" * 24)
    if has_transaction_evidence:
        evidence.mkdir()
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(data_dir))
    monkeypatch.setattr(helper_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is (not has_transaction_evidence)
    assert (not has_transaction_evidence) or evidence.exists()
    if has_transaction_evidence:
        diagnostic = json.loads(
            (updates / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(encoding="utf-8")
        )
        assert diagnostic["code"] == "startup_state_missing_with_transaction"


@pytest.mark.parametrize("phase", ["idle", "current", "error"])
def test_core_start_guard_rejects_malformed_inactive_state_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    fixture.state_path.write_text(json.dumps({"phase": phase}), encoding="utf-8")
    original_state = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert fixture.state_path.read_bytes() == original_state
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "startup_state_invalid"
    assert diagnostic["phase"] is None


def test_core_start_guard_rejects_unsigned_pre_cutover_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _, manifest_path, _ = _signed_staging(fixture, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["signature"] = base64.urlsafe_b64encode(b"\0" * 64).rstrip(b"=").decode()
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["manifest_identity"] = _digest(manifest_path)[0]
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    shutil.rmtree(fixture.journal_path.parent)
    original_state = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert fixture.state_path.read_bytes() == original_state
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "pre_cutover_evidence_missing"


def test_core_start_guard_rejects_staged_artifact_digest_or_size_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _, _, artifact = _signed_staging(fixture, monkeypatch)
    artifact.write_bytes(b"tampered staged artifact with a different size")
    shutil.rmtree(fixture.journal_path.parent)
    original_state = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert fixture.state_path.read_bytes() == original_state
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "pre_cutover_evidence_missing"


def test_core_start_guard_rejects_stale_staging_for_a_different_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _signed_staging(fixture, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    stale_operation_id = "b" * 24
    state["operation_id"] = stale_operation_id
    state["downloaded_path"] = str(
        fixture.state_path.parent / "staging" / stale_operation_id / "artifact.zip"
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    shutil.rmtree(fixture.journal_path.parent)
    original_state = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert fixture.state_path.read_bytes() == original_state
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "pre_cutover_evidence_missing"


@pytest.mark.parametrize(
    "reparse_target",
    ["data_root", "staging_root", "operation_dir", "manifest", "artifact"],
)
def test_core_start_guard_rejects_reparse_staging_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reparse_target: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _signed_staging(fixture, monkeypatch)
    data_root = fixture.state_path.parent.parent
    staging_root = fixture.state_path.parent / "staging"
    operation_dir = staging_root / ("a" * 24)
    manifest = operation_dir / "manifest.json"
    artifact = operation_dir / "artifact.zip"
    original_directory_stat = helper_module._plain_directory_stat
    original_file_stat = helper_module._plain_file_stat

    def reject_directory(path: Path, code: str) -> os.stat_result:
        if reparse_target == "data_root" and path == data_root:
            raise HelperError(code)
        if reparse_target == "staging_root" and path == staging_root:
            raise HelperError(code)
        if reparse_target == "operation_dir" and path == operation_dir:
            raise HelperError(code)
        return original_directory_stat(path, code)

    def reject_file(path: Path, code: str) -> os.stat_result:
        if reparse_target == "manifest" and path == manifest:
            raise HelperError(code)
        if reparse_target == "artifact" and path == artifact:
            raise HelperError(code)
        return original_file_stat(path, code)

    monkeypatch.setattr(helper_module, "_plain_directory_stat", reject_directory)
    monkeypatch.setattr(helper_module, "_plain_file_stat", reject_file)
    shutil.rmtree(fixture.journal_path.parent)
    original_state = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert fixture.state_path.read_bytes() == original_state
    diagnostic_path = fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME
    if reparse_target == "data_root":
        assert not diagnostic_path.exists()
    else:
        diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
        assert diagnostic["code"] == "pre_cutover_evidence_missing"


@pytest.mark.parametrize("operation_id", [None, "", "a" * 23, 17])
def test_core_start_guard_does_not_reset_pre_cutover_install_without_valid_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation_id: object
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "installing",
            "operation_id": operation_id,
            "transaction_path": None,
            "handoff_identity": None,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
        }
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    shutil.rmtree(fixture.journal_path.parent)
    original_state = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == []
    assert fixture.state_path.read_bytes() == original_state
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == (
        "startup_state_active_without_transaction"
        if operation_id is None
        else "startup_state_invalid"
    )


def test_core_start_guard_does_not_reset_valid_format_unknown_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    operation_id = "b" * 24
    state.update(
        {
            "phase": "installing",
            "operation_id": operation_id,
            "transaction_path": None,
            "downloaded_path": str(
                fixture.state_path.parent / "staging" / operation_id / "artifact.zip"
            ),
            "manifest_identity": "c" * 64,
            "handoff_identity": None,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
        }
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    shutil.rmtree(fixture.journal_path.parent)
    original_state = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == []
    assert fixture.state_path.read_bytes() == original_state
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "pre_cutover_evidence_missing"


def test_core_start_guard_does_not_reset_pre_cutover_install_with_journal_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "installing",
            "transaction_path": None,
            "handoff_identity": None,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
        }
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    fixture.journal_path.unlink()
    original_state = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == []
    assert fixture.state_path.read_bytes() == original_state
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "startup_state_active_without_transaction"


@pytest.mark.parametrize("invalid_identity", ["", 17, "a" * 63, "not-a-digest"])
def test_core_start_guard_rejects_invalid_handoff_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_identity: object
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = invalid_identity
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    original_state = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == []
    assert fixture.state_path.read_bytes() == original_state
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "startup_state_invalid"


def test_core_start_guard_does_not_treat_pending_identity_as_unbound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    state["pending_handoff_identity"] = "b" * 64
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == []
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "startup_state_invalid"


def test_core_start_guard_rejects_reparse_state_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    original_chain = helper_module._plain_directory_chain_if_present
    state_parent = fixture.state_path.parent

    def reject_reparse(path: Path, code: str) -> bool:
        if path == state_parent:
            raise HelperError(code)
        return original_chain(path, code)

    monkeypatch.setattr(helper_module, "_plain_directory_chain_if_present", reject_reparse)
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert not (state_parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).exists()


def test_core_start_guard_rejects_reparse_transaction_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    original_chain = helper_module._plain_directory_chain
    transaction_parent = fixture.journal_path.parent

    def reject_reparse(path: Path, code: str) -> None:
        if path == transaction_parent:
            raise HelperError(code)
        original_chain(path, code)

    monkeypatch.setattr(helper_module, "_plain_directory_chain", reject_reparse)
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "startup_state_untrusted"


def test_core_start_guard_blocks_unreadable_state_and_records_safe_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    original_database = fixture.database.read_bytes()
    fixture.state_path.write_bytes(b'{"phase":')
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal: launched.append((helper, journal)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == []
    assert fixture.state_path.read_bytes() == b'{"phase":'
    assert fixture.database.read_bytes() == original_database
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["status"] == "blocked"
    assert diagnostic["code"] == "metadata_unreadable"
    assert diagnostic["phase"] is None
    assert "application_path" not in json.dumps(diagnostic)


def test_core_start_guard_blocks_invalid_journal_and_keeps_core_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = json.loads(fixture.journal_path.read_text(encoding="utf-8"))
    journal["phase"] = "not-a-real-phase"
    fixture.journal_path.write_text(json.dumps(journal), encoding="utf-8")
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == []
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["status"] == "blocked"
    assert diagnostic["code"] == "journal_invalid"


def test_core_start_guard_blocks_impossible_transaction_state_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["phase"] = "idle"
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == []
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["status"] == "blocked"
    assert diagnostic["code"] == "startup_state_invalid"
    assert diagnostic["phase"] == "idle"


def test_pending_handoff_transition_reconciles_either_crash_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    original_identity = state["handoff_identity"]
    journal.parent_pid = 123
    next_identity = helper_module.journal_handoff_identity(journal)

    state["pending_handoff_identity"] = next_identity
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    original = UpdateJournal.load(fixture.journal_path)
    reconciled = helper_module._validate_handoff_state(original, fixture.journal_path)
    assert reconciled["handoff_identity"] == original_identity
    assert reconciled["pending_handoff_identity"] is None

    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["pending_handoff_identity"] = next_identity
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    journal.save(fixture.journal_path)
    promoted = helper_module._validate_handoff_state(journal, fixture.journal_path)
    assert promoted["handoff_identity"] == next_identity
    assert promoted["pending_handoff_identity"] is None


def test_terminal_replay_requires_completed_journal_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    assert run_transaction(fixture.journal_path) == 0
    journal = json.loads(fixture.journal_path.read_text(encoding="utf-8"))
    journal["replacement_sha256"] = "0" * 64
    fixture.journal_path.write_text(json.dumps(journal), encoding="utf-8")

    with pytest.raises(HelperError, match="application_state_mismatch"):
        run_transaction(fixture.journal_path)


def test_terminal_journal_requires_state_first_terminal_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    journal.phase = HelperPhase.COMMITTED
    journal.save(fixture.journal_path)

    with pytest.raises(HelperError, match="application_state_mismatch"):
        run_transaction(fixture.journal_path)

    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "restart_required"
    assert state["transaction_path"] == str(fixture.journal_path)


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
