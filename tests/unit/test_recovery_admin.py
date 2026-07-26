"""B-109 packaged recovery/admin surface (no Python checkout required for product path)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.desktop import main as desktop_main
from allthecontext.models import CandidateInput
from allthecontext.recovery_admin import (
    RecoveryError,
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


def test_cutover_and_rollback_verify_integrity_and_restore_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    # Injected failure after preserving active must restore prior active vault.
    with pytest.raises(RecoveryError, match="prior active vault was restored"):
        cutover_active_vault(
            isolated,
            data_dir=active,
            rollback_path=tmp_path / "rollback-fail",
            inject_failure="after_preserve",
        )
    assert (active / "core.sqlite3").is_file()
    restored_active = CoreStore(active / "core.sqlite3")
    assert restored_active.get_record(record_id).content == "Active fiction vault content A."

    # Successful cutover then rollback both verify integrity.
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

    # Overlapping / nonempty targets refuse closed.
    with pytest.raises(RecoveryError, match=r"not empty|overlap"):
        cutover_active_vault(isolated, data_dir=active, rollback_path=tmp_path / "rollback-ok")


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
