"""B-109 packaged recovery/admin surface (no Python checkout required for product path)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from allthecontext.desktop import main as desktop_main
from allthecontext.models import CandidateInput
from allthecontext.recovery_admin import (
    RecoveryError,
    RecoveryPaths,
    cutover_active_vault,
    doctor,
    export_active_vault,
    purge_target,
    recovery_console_helper_name,
    recovery_help_text,
    require_core_stopped,
    restore_isolated,
    rollback_active_vault,
    verify_vault_integrity,
)
from allthecontext.storage import CoreStore, NotFoundError
from filelock import FileLock


def _seed_vault(data_dir: Path, *, content: str = "Prefer fiction recovery short answers.") -> str:
    data_dir.mkdir(parents=True, exist_ok=True)
    store = CoreStore(data_dir / "core.sqlite3")
    store.initialize_vault("Fiction Recovery Vault")
    observation = store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content=content,
            explicit_user_statement=True,
        )
    )
    assert observation.record_id is not None
    return observation.record_id


def _logical_vault_fingerprint(database: Path) -> str:
    """Content-derived fingerprint of vault identity + durable rows (no raw text)."""

    connection = sqlite3.connect(str(database), timeout=30)
    try:
        connection.row_factory = sqlite3.Row
        vault = connection.execute(
            "SELECT id,name,schema_version FROM vaults ORDER BY id LIMIT 1"
        ).fetchone()
        records = connection.execute(
            "SELECT id,kind,content,approval_status,deleted_at FROM context_records ORDER BY id"
        ).fetchall()
        tombs = connection.execute(
            "SELECT stable_id,target_type FROM purge_tombstones ORDER BY stable_id"
        ).fetchall()
        payload = {
            "vault": dict(vault) if vault is not None else None,
            "records": [dict(row) for row in records],
            "tombs": [dict(row) for row in tombs],
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    finally:
        connection.close()


def _assert_active_intact(
    active: Path,
    *,
    record_id: str,
    content: str,
    pre_fingerprint: str | None = None,
) -> None:
    active_db = active / "core.sqlite3"
    assert active_db.is_file(), "active/core.sqlite3 must exist"
    if pre_fingerprint is not None:
        assert _logical_vault_fingerprint(active_db) == pre_fingerprint
    integrity = verify_vault_integrity(active_db)
    assert integrity["ok"] is True
    assert integrity["label"] == "verified"
    store = CoreStore(active_db)
    assert store.get_record(record_id).content == content


def test_recovery_help_is_installed_and_versioned() -> None:
    text = recovery_help_text()
    assert "recovery/admin helper" in text
    assert "no Python" in text.casefold() or "Python" in text
    assert "--recovery-restore" in text
    assert "--recovery-purge" in text
    assert "PURGE" in text
    helper = recovery_console_helper_name()
    assert helper
    # Help must name the operator-reachable surface for the current OS.
    assert helper in text or "all-the-context" in text


def test_preflight_refuses_when_core_lock_held(tmp_path: Path) -> None:
    data_dir = tmp_path / "vault"
    _seed_vault(data_dir)
    lock = FileLock(str(data_dir / "core.lock"), timeout=0)
    lock.acquire()
    try:
        with pytest.raises(RecoveryError, match="running"):
            require_core_stopped(
                __import__("allthecontext.config", fromlist=["CoreConfig"]).CoreConfig.in_directory(
                    data_dir
                )
            )
    finally:
        lock.release()


def test_isolated_restore_cutover_preserves_purge_non_resurrection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Export before purge, purge live, isolated-restore pre-purge export, cutover, prove gone."""

    data_dir = tmp_path / "active"
    store = CoreStore(data_dir / "core.sqlite3")
    store.initialize_vault("Fiction non-resurrection vault")
    record = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="Fiction purge target goal must not resurrect.",
            explicit_user_statement=True,
        )
    )
    assert record.record_id is not None
    source = store.add_source(
        b"fiction-source-body-for-purge-non-resurrection",
        source_service="fiction-provider",
        source_type="provider_archive",
        filename="fiction.txt",
    )
    source_id = source.id
    passphrase = "fiction-recovery-passphrase"
    monkeypatch.setenv("ATC_EXPORT_PASSPHRASE", passphrase)

    export_path = tmp_path / "pre-purge.atcexp"
    export_active_vault(export_path, data_dir=data_dir, passphrase=passphrase)
    assert export_path.is_file()

    purge_target(
        "record",
        record.record_id,
        confirmation=f"PURGE RECORD {record.record_id}",
        data_dir=data_dir,
        compact=False,
    )
    purge_target(
        "source",
        source_id,
        confirmation=f"PURGE SOURCE {source_id}",
        data_dir=data_dir,
        compact=False,
    )
    live = CoreStore(data_dir / "core.sqlite3")
    with pytest.raises(NotFoundError):
        live.get_record(record.record_id)

    isolated = tmp_path / "isolated"
    restored = restore_isolated(
        export_path,
        data_dir=data_dir,
        destination=isolated,
        passphrase=passphrase,
        dry_run=False,
        cutover=True,
        rollback_path=tmp_path / "rollback",
    )
    assert restored["integrity"] == "verified"
    assert restored["purge_carry_forward"]["carried_purge_tombstones"] >= 2
    assert restored["cutover"] is True
    assert restored["cutover_result"]["status"] == "cutover_complete"
    assert restored["cutover_result"]["integrity"]["ok"] is True

    after = CoreStore(data_dir / "core.sqlite3")
    with pytest.raises(NotFoundError):
        after.get_record(record.record_id)
    with pytest.raises(NotFoundError):
        after.get_source(source_id)
    # Source body must not reappear via status/source list either.
    listed, _total = after.list_sources()
    assert all(item["id"] != source_id for item in listed)


def test_default_recovery_paths_are_outside_active_and_usable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operators may omit --recovery-destination and --recovery-rollback-path."""

    passphrase = "fiction-default-path-passphrase"
    monkeypatch.setenv("ATC_EXPORT_PASSPHRASE", passphrase)
    active = tmp_path / "active"
    content = "Default-path fiction vault content."
    record_id = _seed_vault(active, content=content)
    export_path = tmp_path / "backup.atcexp"
    export_active_vault(export_path, data_dir=active, passphrase=passphrase)

    paths = RecoveryPaths.for_data_dir(active)
    assert not paths.recovery_root.is_relative_to(paths.config.data_dir)
    assert paths.recovery_root.parent == paths.config.data_dir.parent
    assert paths.recovery_root.name == f"{paths.config.data_dir.name}-recovery"

    restored = restore_isolated(
        export_path,
        data_dir=active,
        passphrase=passphrase,
        dry_run=False,
        cutover=True,
        # deliberately omit destination and rollback_path
    )
    assert restored["cutover"] is True
    assert restored["cutover_result"]["status"] == "cutover_complete"
    isolated = Path(str(restored["isolated_destination"]))
    rollback = Path(str(restored["cutover_result"]["rollback_directory"]))
    assert isolated.is_relative_to(paths.recovery_root)
    assert rollback.is_relative_to(paths.recovery_root)
    assert not isolated.is_relative_to(paths.config.data_dir)
    assert not rollback.is_relative_to(paths.config.data_dir)
    assert (active / "core.sqlite3").is_file()
    assert verify_vault_integrity(active / "core.sqlite3")["ok"] is True
    assert CoreStore(active / "core.sqlite3").get_record(record_id).content == content


@pytest.mark.parametrize("boundary", ["after_stage", "after_preserve", "after_replace"])
def test_cutover_soft_failures_preserve_active_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    passphrase = "fiction-cutover-boundary-pass"
    monkeypatch.setenv("ATC_EXPORT_PASSPHRASE", passphrase)
    active = tmp_path / "active"
    content = f"Active fiction vault content for cutover {boundary}."
    record_id = _seed_vault(active, content=content)
    pre_fingerprint = _logical_vault_fingerprint(active / "core.sqlite3")
    export_path = tmp_path / "backup.atcexp"
    export_active_vault(export_path, data_dir=active, passphrase=passphrase)
    isolated = tmp_path / "isolated"
    restore_isolated(
        export_path,
        data_dir=active,
        destination=isolated,
        passphrase=passphrase,
        dry_run=False,
        cutover=False,
    )
    # Mutate isolated so a successful cutover would change active content identity.
    other = CoreStore(isolated / "core.sqlite3")
    other.add_candidate(
        CandidateInput(
            kind="goal",
            content=f"Isolated-only fiction goal for {boundary}.",
            explicit_user_statement=True,
        )
    )

    with pytest.raises(RecoveryError, match="prior active vault was restored"):
        cutover_active_vault(
            isolated,
            data_dir=active,
            rollback_path=tmp_path / f"rollback-{boundary}",
            inject_failure=boundary,
        )
    _assert_active_intact(
        active,
        record_id=record_id,
        content=content,
        pre_fingerprint=pre_fingerprint,
    )


@pytest.mark.parametrize("boundary", ["after_stage", "after_preserve", "after_replace"])
def test_rollback_soft_failures_preserve_active_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    passphrase = "fiction-rollback-boundary-pass"
    monkeypatch.setenv("ATC_EXPORT_PASSPHRASE", passphrase)
    active = tmp_path / "active"
    content = f"Active fiction vault content for rollback {boundary}."
    record_id = _seed_vault(active, content=content)
    export_path = tmp_path / "backup.atcexp"
    export_active_vault(export_path, data_dir=active, passphrase=passphrase)
    isolated = tmp_path / "isolated"
    restore_isolated(
        export_path,
        data_dir=active,
        destination=isolated,
        passphrase=passphrase,
        dry_run=False,
        cutover=False,
    )
    rollback_ok = tmp_path / "rollback-ok"
    cutover_active_vault(isolated, data_dir=active, rollback_path=rollback_ok)
    current_content = CoreStore(active / "core.sqlite3").get_record(record_id).content
    pre_fingerprint = _logical_vault_fingerprint(active / "core.sqlite3")

    with pytest.raises(RecoveryError, match="prior active vault was restored"):
        rollback_active_vault(
            rollback_ok,
            data_dir=active,
            inject_failure=boundary,
        )
    _assert_active_intact(
        active,
        record_id=record_id,
        content=current_content,
        pre_fingerprint=pre_fingerprint,
    )


@pytest.mark.parametrize(
    ("operation", "boundary"),
    [
        ("cutover", "after_stage"),
        ("cutover", "after_preserve"),
        ("cutover", "after_replace"),
        ("rollback", "after_stage"),
        ("rollback", "after_preserve"),
        ("rollback", "after_replace"),
    ],
)
def test_subprocess_crash_keeps_complete_active_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str, boundary: str
) -> None:
    """Hard process exit must leave a complete verified old or new vault, never missing."""

    passphrase = "fiction-crash-boundary-pass"
    monkeypatch.setenv("ATC_EXPORT_PASSPHRASE", passphrase)
    active = tmp_path / "active"
    content = f"Crash-boundary fiction vault {operation} {boundary}."
    record_id = _seed_vault(active, content=content)
    pre_fingerprint = _logical_vault_fingerprint(active / "core.sqlite3")
    export_path = tmp_path / "backup.atcexp"
    export_active_vault(export_path, data_dir=active, passphrase=passphrase)
    isolated = tmp_path / "isolated"
    restore_isolated(
        export_path,
        data_dir=active,
        destination=isolated,
        passphrase=passphrase,
        dry_run=False,
        cutover=False,
    )
    rollback_path = tmp_path / "rollback-crash"
    if operation == "rollback":
        cutover_active_vault(isolated, data_dir=active, rollback_path=rollback_path)
        pre_fingerprint = _logical_vault_fingerprint(active / "core.sqlite3")
        expected_content = CoreStore(active / "core.sqlite3").get_record(record_id).content
    else:
        expected_content = content

    script = f"""
from pathlib import Path
from allthecontext.recovery_admin import cutover_active_vault, rollback_active_vault
if {operation!r} == "cutover":
    cutover_active_vault(
        Path({str(isolated)!r}),
        data_dir=Path({str(active)!r}),
        rollback_path=Path({str(rollback_path)!r}),
        inject_failure="crash_{boundary}",
    )
else:
    rollback_active_vault(
        Path({str(rollback_path)!r}),
        data_dir=Path({str(active)!r}),
        inject_failure="crash_{boundary}",
    )
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 87, completed.stderr
    active_db = active / "core.sqlite3"
    assert active_db.is_file(), "process crash must not leave active missing"
    if boundary in {"after_stage", "after_preserve"}:
        # Replace never happened: logical pre-operation content remains.
        assert _logical_vault_fingerprint(active_db) == pre_fingerprint
    integrity = verify_vault_integrity(active_db)
    assert integrity["ok"] is True
    store = CoreStore(active_db)
    if boundary in {"after_stage", "after_preserve"}:
        assert store.get_record(record_id).content == expected_content
    else:
        # after_replace crash: complete candidate vault, never missing/partial.
        recovered = store.get_record(record_id)
        assert recovered is not None
        assert recovered.content


def test_cutover_and_rollback_success_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    passphrase = "fiction-cutover-passphrase"
    monkeypatch.setenv("ATC_EXPORT_PASSPHRASE", passphrase)

    active = tmp_path / "active"
    record_id = _seed_vault(active, content="Active fiction vault content A.")
    export_path = tmp_path / "backup.atcexp"
    export_active_vault(export_path, data_dir=active, passphrase=passphrase)

    isolated = tmp_path / "isolated"
    restore_isolated(
        export_path,
        data_dir=active,
        destination=isolated,
        passphrase=passphrase,
        dry_run=False,
        cutover=False,
    )
    integrity = verify_vault_integrity(isolated / "core.sqlite3")
    assert integrity["ok"] is True
    assert integrity["label"] == "verified"

    cutover_active_vault(
        isolated,
        data_dir=active,
        rollback_path=tmp_path / "rollback-ok",
    )
    assert verify_vault_integrity(active / "core.sqlite3")["ok"] is True

    rollback_active_vault(tmp_path / "rollback-ok", data_dir=active)
    assert verify_vault_integrity(active / "core.sqlite3")["ok"] is True
    assert CoreStore(active / "core.sqlite3").get_record(record_id).content == (
        "Active fiction vault content A."
    )

    with pytest.raises(RecoveryError, match=r"not empty|overlap"):
        cutover_active_vault(isolated, data_dir=active, rollback_path=tmp_path / "rollback-ok")


def test_restore_from_different_vault_with_purge_tombstones_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    passphrase = "fiction-cross-vault-passphrase"
    monkeypatch.setenv("ATC_EXPORT_PASSPHRASE", passphrase)

    active = tmp_path / "active"
    store = CoreStore(active / "core.sqlite3")
    store.initialize_vault("Active fiction vault")
    record = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="Active fiction purge target.",
            explicit_user_statement=True,
        )
    )
    assert record.record_id is not None
    active_vault_id = store.vault_id()
    purge_target(
        "record",
        record.record_id,
        confirmation=f"PURGE RECORD {record.record_id}",
        data_dir=active,
        compact=False,
    )
    tombs = (
        sqlite3.connect(active / "core.sqlite3")
        .execute("SELECT COUNT(*) FROM purge_tombstones")
        .fetchone()
    )
    assert tombs is not None and int(tombs[0]) >= 1

    other = tmp_path / "other"
    other_store = CoreStore(other / "core.sqlite3")
    other_store.initialize_vault("Other fiction vault")
    other_store.add_candidate(
        CandidateInput(
            kind="goal",
            content="Other vault fiction goal that must not blend.",
            explicit_user_statement=True,
        )
    )
    other_vault_id = other_store.vault_id()
    assert other_vault_id != active_vault_id
    export_path = tmp_path / "other.atcexp"
    export_active_vault(export_path, data_dir=other, passphrase=passphrase)

    with pytest.raises(RecoveryError, match="different vault"):
        restore_isolated(
            export_path,
            data_dir=active,
            destination=tmp_path / "isolated-cross",
            passphrase=passphrase,
            dry_run=False,
            cutover=False,
        )
    # Active identity and non-resurrection boundary remain intact.
    live = CoreStore(active / "core.sqlite3")
    assert live.vault_id() == active_vault_id
    with pytest.raises(NotFoundError):
        live.get_record(record.record_id)
    remaining = (
        sqlite3.connect(active / "core.sqlite3")
        .execute("SELECT COUNT(*) FROM purge_tombstones")
        .fetchone()
    )
    assert remaining is not None and int(remaining[0]) >= 1


def test_export_restore_cutover_rollback_and_purge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "active"
    record_id = _seed_vault(data_dir)
    passphrase = "fiction-recovery-passphrase"
    monkeypatch.setenv("ATC_EXPORT_PASSPHRASE", passphrase)

    export_path = tmp_path / "backup.atcexp"
    exported = export_active_vault(export_path, data_dir=data_dir, passphrase=passphrase)
    assert export_path.is_file()
    assert exported["action"] == "export"

    dry = restore_isolated(
        export_path,
        data_dir=data_dir,
        passphrase=passphrase,
        dry_run=True,
    )
    assert dry["valid"] is True

    cutover_dir = tmp_path / "cutover-active"
    _seed_vault(cutover_dir)
    export2 = tmp_path / "backup2.atcexp"
    export_active_vault(export2, data_dir=cutover_dir, passphrase=passphrase)
    isolated2 = tmp_path / "isolated2"
    restore_isolated(
        export2,
        data_dir=cutover_dir,
        destination=isolated2,
        passphrase=passphrase,
        dry_run=False,
        cutover=True,
        rollback_path=tmp_path / "rollback",
    )
    assert (cutover_dir / "core.sqlite3").is_file()
    assert (tmp_path / "rollback" / "core.sqlite3").is_file()
    assert verify_vault_integrity(cutover_dir / "core.sqlite3")["ok"] is True

    rollback_active_vault(tmp_path / "rollback", data_dir=cutover_dir)
    assert (cutover_dir / "core.sqlite3").is_file()

    report = doctor(data_dir=cutover_dir)
    assert report["python_checkout_required"] is False
    assert report["recovery_surface"] == "packaged-console-helper"
    assert report["core_lock_held"] is False
    assert report["integrity"] is not None
    assert report["integrity"]["ok"] is True

    # Keep purge path covered without conflating it with cutover resurrection.
    purged = purge_target(
        "record",
        record_id,
        confirmation=f"PURGE RECORD {record_id}",
        data_dir=data_dir,
        compact=False,
    )
    assert purged["action"] == "purge"


def test_desktop_recovery_help_mode(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = desktop_main(["--recovery-help"])
    captured = capsys.readouterr()
    assert code == 0
    assert "--recovery-restore" in captured.out
    assert "All The Context recovery/admin helper" in captured.out


def test_desktop_recovery_doctor_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "vault"
    _seed_vault(data_dir)
    code = desktop_main(["--recovery-doctor", "--recovery-data-dir", str(data_dir)])
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["database_present"] is True
    assert payload["python_checkout_required"] is False


def test_desktop_recovery_doctor_reports_only_sanitized_startup_diagnostic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "vault"
    _seed_vault(data_dir)
    updates = data_dir / "updates"
    updates.mkdir()
    (updates / "startup-recovery.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "blocked",
                "code": "journal_invalid",
                "phase": "restart_required",
                "updated_at": "2026-09-01T12:00:00+00:00",
                "application_path": "C:/Users/private/never-report-this",
                "token": "never-report-this-secret",
            }
        ),
        encoding="utf-8",
    )

    assert desktop_main(["--recovery-doctor", "--recovery-data-dir", str(data_dir)]) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["startup_recovery"] == {
        "status": "blocked",
        "code": "journal_invalid",
        "phase": "restart_required",
    }
    assert payload["data_dir"] == str(data_dir)
    assert "never-report-this" not in captured.out


def test_desktop_recovery_doctor_rejects_unallowlisted_startup_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "vault"
    updates = data_dir / "updates"
    updates.mkdir(parents=True)
    (updates / "startup-recovery.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "blocked",
                "code": "attacker_controlled_code",
                "phase": "error",
            }
        ),
        encoding="utf-8",
    )

    assert desktop_main(["--recovery-doctor", "--recovery-data-dir", str(data_dir)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["startup_recovery"] == {
        "status": "unreadable",
        "code": "diagnostic_invalid",
    }


def test_desktop_recovery_export_restore_purge_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "vault"
    record_id = _seed_vault(data_dir)
    passphrase = "fiction-packaged-mode-pass"
    monkeypatch.setenv("ATC_EXPORT_PASSPHRASE", passphrase)
    export_path = tmp_path / "packaged.atcexp"

    assert (
        desktop_main(
            [
                "--recovery-export",
                str(export_path),
                "--recovery-data-dir",
                str(data_dir),
            ]
        )
        == 0
    )
    assert export_path.is_file()
    capsys.readouterr()

    isolated = tmp_path / "iso"
    assert (
        desktop_main(
            [
                "--recovery-restore",
                str(export_path),
                "--recovery-data-dir",
                str(data_dir),
                "--recovery-destination",
                str(isolated),
            ]
        )
        == 0
    )
    assert (isolated / "core.sqlite3").is_file()
    payload = json.loads(capsys.readouterr().out)
    assert payload["integrity"] == "verified"
    assert payload["integrity_report"]["ok"] is True

    assert (
        desktop_main(
            [
                "--recovery-purge",
                "record",
                record_id,
                "--recovery-confirmation",
                f"PURGE RECORD {record_id}",
                "--recovery-data-dir",
                str(data_dir),
                "--recovery-no-compact",
            ]
        )
        == 0
    )
    # wrong confirmation fails closed
    assert (
        desktop_main(
            [
                "--recovery-purge",
                "record",
                record_id,
                "--recovery-confirmation",
                "wrong",
                "--recovery-data-dir",
                str(data_dir),
            ]
        )
        == 2
    )
