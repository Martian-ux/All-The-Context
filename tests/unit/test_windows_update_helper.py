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
from allthecontext.build_identity import BuildIdentity
from allthecontext.installed_component_manifest import (
    CHECKSUM_FILE_NAME,
    MANIFEST_FILE_NAME,
    canonical_json,
)
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
    main,
    record_startup_recovery_parser_failure,
    run_transaction,
    startup_recovery_diagnostic,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_RECOVERY_AUTHORITY_VALUES: dict[str, str] = {}
TEST_SOURCE_COMMIT = "0" * 40


class _RecoveryAuthorityStore:
    def get(self, name: str) -> str | None:
        return _RECOVERY_AUTHORITY_VALUES.get(name)

    def set(self, name: str, value: str) -> None:
        _RECOVERY_AUTHORITY_VALUES[name] = value

    def delete(self, name: str) -> None:
        _RECOVERY_AUTHORITY_VALUES.pop(name, None)


@pytest.fixture(autouse=True)
def _use_test_recovery_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    _RECOVERY_AUTHORITY_VALUES.clear()
    monkeypatch.setattr(
        helper_module,
        "_recovery_authority_store",
        lambda: _RecoveryAuthorityStore(),
    )
    monkeypatch.setattr(
        helper_module,
        "runtime_build_identity",
        lambda **_: BuildIdentity(
            version="0.1.0",
            channel="stable",
            platform="windows",
            architecture="x86_64",
            source_commit=TEST_SOURCE_COMMIT,
        ),
    )


def _digest(path: Path) -> tuple[str, int]:
    value = path.read_bytes()
    return hashlib.sha256(value).hexdigest(), len(value)


def _pathological_json(kind: str) -> bytes:
    if kind == "huge_integer":
        return b'{"value":' + b"9" * 5_000 + b"}"
    if kind == "deep_nesting":
        return b'{"value":' + b"[" * 30_000 + b"0" + b"]" * 30_000 + b"}"
    raise AssertionError(kind)


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
        source_commit=TEST_SOURCE_COMMIT,
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
            "current_source_commit": TEST_SOURCE_COMMIT,
            "offered_source_commit": TEST_SOURCE_COMMIT,
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
    component_manifest: Path
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
    new_mcp = b"new mcp binary"
    new_recovery = b"new recovery helper binary"
    new_update_helper = b"new update helper binary"
    component_manifest = transaction_dir / MANIFEST_FILE_NAME
    component_payload = {
        "architecture": "x86_64",
        "component_count": 4,
        "components": [
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContext.exe",
                "role": "main",
                "sha256": replacement_digest,
                "size": replacement_size,
            },
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContextMCP.exe",
                "role": "mcp",
                "sha256": hashlib.sha256(new_mcp).hexdigest(),
                "size": len(new_mcp),
            },
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContextRecovery.exe",
                "role": "recovery",
                "sha256": hashlib.sha256(new_recovery).hexdigest(),
                "size": len(new_recovery),
            },
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContextUpdater.exe",
                "role": "updater",
                "sha256": hashlib.sha256(new_update_helper).hexdigest(),
                "size": len(new_update_helper),
            },
        ],
        "manifest_type": "installed-component",
        "package": {
            "direct_package": {
                "filename": "all-the-context-0.2.0-windows-x86_64-unsigned.exe",
                "sha256": replacement_digest,
                "size": replacement_size,
            },
            "filename": "AllTheContextSetup.exe",
            "sha256": replacement_digest,
            "size": replacement_size,
        },
        "platform": "windows",
        "schema_version": 1,
        "source_commit": "0" * 40,
        "version": "0.2.0",
    }
    component_raw = canonical_json(component_payload)
    component_manifest.write_bytes(component_raw)
    component_manifest.with_name(CHECKSUM_FILE_NAME).write_bytes(
        f"{hashlib.sha256(component_raw).hexdigest()}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    )
    state_path = updates / "state.json"
    journal_path = transaction_dir / "journal.json"
    state_path.write_text(
        json.dumps(
            {
                "phase": "restart_required",
                "current_version": "0.1.0",
                "current_source_commit": TEST_SOURCE_COMMIT,
                "offered_version": "0.2.0",
                "offered_source_commit": TEST_SOURCE_COMMIT,
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
        current_source_commit=TEST_SOURCE_COMMIT,
        target_source_commit=TEST_SOURCE_COMMIT,
        rollback_source_commit=TEST_SOURCE_COMMIT,
        recovery_source_commit=TEST_SOURCE_COMMIT,
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
        component_manifest_path=str(component_manifest),
        component_manifest_sha256=hashlib.sha256(component_raw).hexdigest(),
        component_manifest_size=len(component_raw),
        created_at=now,
        updated_at=now,
    ).save(journal_path)
    journal = UpdateJournal.load(journal_path)
    helper_module.bind_recovery_authority(journal, journal_path)
    helper_module.bind_handoff_state(journal, journal_path)
    return TransactionFixture(
        journal_path,
        application,
        mcp,
        recovery,
        stable_update_helper,
        database,
        state_path,
        component_manifest,
        old_application,
        old_mcp,
        old_recovery,
        old_update_helper,
        replacement,
    )


@pytest.mark.parametrize(
    ("kind", "parser_error"),
    [
        ("huge_integer", ValueError),
        ("deep_nesting", RecursionError),
    ],
)
def test_read_json_contains_real_parser_failures(
    tmp_path: Path, kind: str, parser_error: type[Exception]
) -> None:
    path = tmp_path / "metadata.json"
    raw = _pathological_json(kind)
    assert len(raw) <= helper_module.MAX_STATE_BYTES
    with pytest.raises(parser_error):
        json.loads(raw.decode("utf-8"))
    path.write_bytes(raw)

    with pytest.raises(HelperError, match="metadata_unreadable"):
        helper_module._read_json(path, helper_module.MAX_STATE_BYTES)


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        (b"{ malformed", "metadata_unreadable"),
        (b"\xff", "metadata_unreadable"),
        (b"[]", "metadata_invalid"),
    ],
)
def test_read_json_classifies_utf8_json_and_root_failures(
    tmp_path: Path, raw: bytes, expected_code: str
) -> None:
    path = tmp_path / "metadata.json"
    path.write_bytes(raw)

    with pytest.raises(HelperError) as raised:
        helper_module._read_json(path, helper_module.MAX_STATE_BYTES)

    assert raised.value.code == expected_code


def test_read_json_accepts_exact_byte_limit_with_multibyte_utf8(tmp_path: Path) -> None:
    maximum = 64
    prefix = b'{"value":"'
    suffix = b'"}'
    value_bytes = maximum - len(prefix) - len(suffix)
    assert value_bytes % len("é".encode()) == 0
    raw = prefix + "é".encode() * (value_bytes // 2) + suffix
    path = tmp_path / "metadata.json"
    path.write_bytes(raw)

    assert len(raw) == maximum
    assert helper_module._read_json(path, maximum) == {"value": "é" * (value_bytes // 2)}


@pytest.mark.parametrize(
    ("raw", "expected_code"),
    [
        (b'{"value":"' + b"a" * 53 + b"\xc3", "metadata_unreadable"),
        (b'{"value":"' + b"a" * 54 + "é".encode() + b'"}', "metadata_too_large"),
    ],
)
def test_read_json_handles_multibyte_boundary_without_overread(
    tmp_path: Path, raw: bytes, expected_code: str
) -> None:
    maximum = 64
    path = tmp_path / "metadata.json"
    path.write_bytes(raw)

    assert len(raw) in {maximum, maximum + 4}
    with pytest.raises(HelperError, match=expected_code):
        helper_module._read_json(path, maximum)


@pytest.mark.parametrize("control_exception", [SystemExit, KeyboardInterrupt, GeneratorExit])
def test_json_decoder_does_not_swallow_process_control_exceptions(
    monkeypatch: pytest.MonkeyPatch, control_exception: type[BaseException]
) -> None:
    def fail(_value: str) -> object:
        raise control_exception("sentinel")

    monkeypatch.setattr(helper_module.json, "loads", fail)

    with pytest.raises(control_exception):
        helper_module._decode_json(b"{}")


def test_json_decoder_does_not_swallow_unexpected_programming_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_value: str) -> object:
        raise RuntimeError("programming failure")

    monkeypatch.setattr(helper_module.json, "loads", fail)

    with pytest.raises(RuntimeError, match="programming failure"):
        helper_module._decode_json(b"{}")


def _fake_commands(
    fixture: TransactionFixture,
    *,
    health_result: int = 0,
    hostile_report_phase: str | None = None,
    hostile_report: bytes | None = None,
    apply_result: int = 0,
    apply_failure_code: str | None = None,
    apply_failure_attempt: str | None = None,
) -> Callable[[tuple[str, ...], dict[str, str]], int]:
    def run(command: tuple[str, ...], environment: dict[str, str]) -> int:
        if "--apply-update" in command:
            report = Path(command[-1])
            if apply_result != 0:
                if apply_failure_code is not None:
                    report.write_text(
                        json.dumps(
                            {
                                "attempt": apply_failure_attempt
                                or environment["ATC_UPDATE_ATTEMPT"],
                                "code": apply_failure_code,
                                "phase": "component_bootstrap",
                                "status": "failed",
                            }
                        ),
                        encoding="utf-8",
                    )
                return apply_result
            fixture.application.write_bytes(fixture.replacement)
            fixture.mcp.write_bytes(b"new mcp binary")
            fixture.recovery.write_bytes(b"new recovery helper binary")
            fixture.update_helper.write_bytes(b"new update helper binary")
            application_digest, application_size = _digest(fixture.application)
            mcp_digest, mcp_size = _digest(fixture.mcp)
            recovery_digest, recovery_size = _digest(fixture.recovery)
            update_digest, update_size = _digest(fixture.update_helper)
            if hostile_report_phase == "apply":
                assert hostile_report is not None
                report.write_bytes(hostile_report)
            else:
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
            report = Path(command[-1])
            if hostile_report_phase == "diagnostics":
                assert hostile_report is not None
                report.write_bytes(hostile_report)
            else:
                report.write_text(
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
                report = Path(command[-1])
                if hostile_report_phase == "health":
                    assert hostile_report is not None
                    report.write_bytes(hostile_report)
                else:
                    report.write_text(
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


def _advance_time_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"now": 100.0}

    def monotonic() -> float:
        return clock["now"]

    def sleep(seconds: float) -> None:
        clock["now"] += seconds

    monkeypatch.setattr(helper_module.time, "monotonic", monotonic)
    monkeypatch.setattr(helper_module.time, "sleep", sleep)


def test_child_failure_report_code_persists_through_rollback_without_user_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    _advance_time_for_retry(monkeypatch)
    monkeypatch.setattr(
        helper_module,
        "_run_bounded",
        _fake_commands(
            fixture,
            apply_result=1,
            apply_failure_code="bootstrap_journal_invalid",
        ),
    )

    assert run_transaction(fixture.journal_path) == 2

    journal = UpdateJournal.load(fixture.journal_path)
    assert journal.phase is HelperPhase.ROLLED_BACK
    assert journal.last_error_code == "bootstrap_journal_invalid"
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert "diagnostic code: bootstrap_journal_invalid" in state["last_error"]
    assert fixture.application.read_bytes() == fixture.old_application
    assert fixture.mcp.read_bytes() == fixture.old_mcp
    assert fixture.recovery.read_bytes() == fixture.old_recovery
    assert fixture.update_helper.read_bytes() == fixture.old_update_helper
    with sqlite3.connect(fixture.database) as connection:
        assert connection.execute("SELECT value FROM facts").fetchall() == [("before",)]
    assert not (fixture.journal_path.parent / "apply-report.json").exists()
    assert launched == ["0.1.0"]


@pytest.mark.parametrize(
    ("report_kind", "expected_code"),
    [
        ("missing", "child_failure_report_missing"),
        ("malformed", "child_failure_report_invalid"),
        ("unallowlisted", "child_failure_report_invalid"),
        ("oversized", "child_failure_report_invalid"),
        ("wrong_attempt", "child_failure_report_invalid"),
    ],
)
def test_nonzero_child_report_is_strictly_classified(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    report_kind: str,
    expected_code: str,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _advance_time_for_retry(monkeypatch)

    def run(command: tuple[str, ...], environment: dict[str, str]) -> int:
        if "--apply-update" not in command:
            raise AssertionError(command)
        report = Path(command[-1])
        if report_kind == "malformed":
            report.write_bytes(b"{ malformed")
        elif report_kind == "unallowlisted":
            report.write_text(
                json.dumps(
                    {
                        "attempt": environment["ATC_UPDATE_ATTEMPT"],
                        "code": "token_leak",
                        "phase": "component_bootstrap",
                        "status": "failed",
                    }
                ),
                encoding="utf-8",
            )
        elif report_kind == "oversized":
            report.write_bytes(b"{" + b"x" * helper_module.UPDATE_FAILURE_REPORT_MAX_BYTES + b"}")
        elif report_kind == "wrong_attempt":
            report.write_text(
                json.dumps(
                    {
                        "attempt": "f" * 32,
                        "code": "bootstrap_journal_invalid",
                        "phase": "component_bootstrap",
                        "status": "failed",
                    }
                ),
                encoding="utf-8",
            )
        return 1

    monkeypatch.setattr(helper_module, "_run_bounded", run)
    journal = UpdateJournal.load(fixture.journal_path)

    with pytest.raises(HelperError) as raised:
        helper_module._apply_replacement(journal, fixture.journal_path)

    assert raised.value.code == expected_code
    assert not (fixture.journal_path.parent / "apply-report.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("phase", []),
        ("phase", {}),
        ("phase", True),
        ("phase", 1),
        ("phase", None),
        ("phase", {"nested": {"value": "component_bootstrap"}}),
        ("code", []),
        ("code", {}),
        ("code", False),
        ("code", 1.5),
        ("code", None),
        ("code", {"nested": {"value": "bootstrap_journal_invalid"}}),
        ("attempt", []),
        ("attempt", {}),
        ("attempt", True),
        ("attempt", 32),
        ("attempt", None),
        ("attempt", {"nested": {"value": "b" * 32}}),
        ("status", []),
        ("status", {}),
        ("status", False),
        ("status", 1),
        ("status", None),
        ("status", {"nested": {"value": "failed"}}),
    ],
)
def test_adversarial_child_failure_report_values_reach_safe_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _isolate_runtime(monkeypatch, [])
    _advance_time_for_retry(monkeypatch)

    def run(command: tuple[str, ...], environment: dict[str, str]) -> int:
        if "--apply-update" not in command:
            raise AssertionError(command)
        payload: dict[str, Any] = {
            "attempt": environment["ATC_UPDATE_ATTEMPT"],
            "code": "bootstrap_journal_invalid",
            "phase": "component_bootstrap",
            "status": "failed",
        }
        payload[field] = value
        Path(command[-1]).write_text(json.dumps(payload), encoding="utf-8")
        return 1

    monkeypatch.setattr(helper_module, "_run_bounded", run)

    assert run_transaction(fixture.journal_path) == 2

    journal = UpdateJournal.load(fixture.journal_path)
    assert journal.phase is HelperPhase.ROLLED_BACK
    assert journal.last_error_code == "child_failure_report_invalid"
    assert fixture.application.read_bytes() == fixture.old_application
    assert fixture.mcp.read_bytes() == fixture.old_mcp
    assert fixture.recovery.read_bytes() == fixture.old_recovery
    assert fixture.update_helper.read_bytes() == fixture.old_update_helper
    assert not (fixture.journal_path.parent / "apply-report.json").exists()


def test_zero_exit_report_and_target_mismatch_have_distinct_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    monkeypatch.setattr(
        helper_module,
        "_run_bounded",
        lambda _command, _environment: 0,
    )
    journal = UpdateJournal.load(fixture.journal_path)

    with pytest.raises(HelperError) as raised:
        helper_module._apply_replacement(journal, fixture.journal_path)

    assert raised.value.code == "child_zero_target_digest_mismatch"

    fixture = _transaction(tmp_path / "matching", monkeypatch)

    def match_without_report(command: tuple[str, ...], _environment: dict[str, str]) -> int:
        if "--apply-update" in command:
            fixture.application.write_bytes(fixture.replacement)
        return 0

    monkeypatch.setattr(helper_module, "_run_bounded", match_without_report)
    journal = UpdateJournal.load(fixture.journal_path)
    with pytest.raises(HelperError) as raised:
        helper_module._apply_replacement(journal, fixture.journal_path)
    assert raised.value.code == "child_zero_report_missing"


def test_zero_exit_target_mismatch_exhausts_the_absolute_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _advance_time_for_retry(monkeypatch)
    monkeypatch.setattr(helper_module, "PARENT_EXIT_TIMEOUT_SECONDS", 1)

    def run(command: tuple[str, ...], _environment: dict[str, str]) -> int:
        if "--apply-update" in command:
            Path(command[-1]).write_text(json.dumps({"status": "installed"}), encoding="utf-8")
        return 0

    monkeypatch.setattr(helper_module, "_run_bounded", run)
    journal = UpdateJournal.load(fixture.journal_path)
    with pytest.raises(HelperError) as raised:
        helper_module._apply_replacement(journal, fixture.journal_path)
    assert raised.value.code == "binary_cutover_deadline"
    assert not (fixture.journal_path.parent / "apply-report.json").exists()


def test_retry_rejects_stale_report_and_success_clears_failure_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    seen_preexisting: list[bool] = []
    attempts = 0
    successful_child = _fake_commands(fixture)

    def run(command: tuple[str, ...], environment: dict[str, str]) -> int:
        nonlocal attempts
        if "--apply-update" not in command:
            return successful_child(command, environment)
        attempts += 1
        report = Path(command[-1])
        seen_preexisting.append(report.exists())
        if attempts == 1:
            fixture.application.write_bytes(b"not the target")
            report.write_text(json.dumps({"status": "installed"}), encoding="utf-8")
            return 0
        return successful_child(command, environment)

    monkeypatch.setattr(helper_module, "_run_bounded", run)
    _isolate_runtime(monkeypatch, [])
    journal = UpdateJournal.load(fixture.journal_path)

    helper_module._apply_replacement(journal, fixture.journal_path)

    assert attempts == 2
    assert seen_preexisting == [False, False]
    assert journal.phase is HelperPhase.BINARY_REPLACED
    assert journal.last_error_code is None
    assert not (fixture.journal_path.parent / "apply-report.json").exists()


def _prepare_interrupted_packaged_terminal_replay(
    fixture: TransactionFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path, str, str]:
    old_source = TEST_SOURCE_COMMIT
    new_source = "b" * 40
    journal = UpdateJournal.load(fixture.journal_path)
    component = json.loads(fixture.component_manifest.read_text(encoding="utf-8"))
    component["source_commit"] = new_source
    component_raw = canonical_json(component)
    fixture.component_manifest.write_bytes(component_raw)
    fixture.component_manifest.with_name(CHECKSUM_FILE_NAME).write_bytes(
        f"{hashlib.sha256(component_raw).hexdigest()}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    )
    journal.target_source_commit = new_source
    journal.component_manifest_sha256 = hashlib.sha256(component_raw).hexdigest()
    journal.component_manifest_size = len(component_raw)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "offered_source_commit": new_source,
            "handoff_identity": None,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
        }
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    journal.save(fixture.journal_path)
    helper_module.bind_recovery_authority(journal, fixture.journal_path)
    helper_module.bind_handoff_state(journal, fixture.journal_path)

    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    monkeypatch.setattr(helper_module, "_packaged_helper_runtime", lambda: False)
    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: old_source)
    original_update_state = helper_module._update_state
    crashed = False

    def crash_after_terminal_journal(
        current: UpdateJournal,
        *,
        phase: str,
        error: str | None,
        clear_transaction: bool,
    ) -> None:
        nonlocal crashed
        if clear_transaction and current.phase is HelperPhase.COMMITTED and not crashed:
            crashed = True
            raise SystemExit(86)
        original_update_state(
            current,
            phase=phase,
            error=error,
            clear_transaction=clear_transaction,
        )

    monkeypatch.setattr(helper_module, "_update_state", crash_after_terminal_journal)
    with pytest.raises(SystemExit, match="86"):
        run_transaction(fixture.journal_path)
    assert crashed is True
    target_helper = Path(journal.stable_update_helper_path)
    old_helper = Path(journal.helper_path)
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(helper_module, "_packaged_helper_runtime", lambda: True)
    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: old_source)
    return target_helper, old_helper, old_source, new_source


def _install_new_component_targets(fixture: TransactionFixture) -> None:
    fixture.application.write_bytes(fixture.replacement)
    fixture.mcp.write_bytes(b"new mcp binary")
    fixture.recovery.write_bytes(b"new recovery helper binary")
    fixture.update_helper.write_bytes(b"new update helper binary")


def _mutate_component_manifest(
    fixture: TransactionFixture,
    mutation: str,
) -> UpdateJournal:
    journal = UpdateJournal.load(fixture.journal_path)
    payload = json.loads(fixture.component_manifest.read_text(encoding="utf-8"))
    if mutation == "missing":
        payload["components"].pop()
    elif mutation == "extra":
        payload["components"].append(dict(payload["components"][-1]))
    elif mutation == "version":
        payload["version"] = "0.3.0"
    elif mutation == "digest":
        payload["components"][1]["sha256"] = "0" * 64
    else:
        raise AssertionError(mutation)
    raw = canonical_json(payload)
    fixture.component_manifest.write_bytes(raw)
    fixture.component_manifest.with_name(CHECKSUM_FILE_NAME).write_bytes(
        f"{hashlib.sha256(raw).hexdigest()}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    )
    journal.component_manifest_sha256 = hashlib.sha256(raw).hexdigest()
    journal.component_manifest_size = len(raw)
    return journal


def _write_retirement_tombstone(fixture: TransactionFixture) -> Path:
    journal = UpdateJournal.load(fixture.journal_path)
    assert journal.phase is HelperPhase.COMMITTED
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    identity = helper_module.journal_handoff_identity(journal)
    assert state["completed_handoff_identity"] == identity
    payload = {
        "schema_version": helper_module.RETIREMENT_TOMBSTONE_SCHEMA_VERSION,
        "operation_id": journal.operation_id,
        "outcome": "installed",
        "terminal_phase": journal.phase.value,
        "handoff_identity": identity,
        "terminal_authority_mac": journal.terminal_authority_mac,
        "journal_sha256": hashlib.sha256(fixture.journal_path.read_bytes()).hexdigest(),
    }
    raw = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    root = fixture.state_path.parent / helper_module.RETIREMENT_TOMBSTONE_DIRECTORY
    root.mkdir()
    path = root / f"{journal.operation_id}-{hashlib.sha256(raw).hexdigest()}.json"
    path.write_bytes(raw)
    return path


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
    state["completed_handoff_identity"] = None
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    assert run_transaction(fixture.journal_path) == 0
    replayed_state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert replayed_state["transaction_path"] is None


@pytest.mark.parametrize("mutation", ["missing", "extra", "version", "digest"])
def test_component_manifest_rejects_adversarial_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _install_new_component_targets(fixture)
    journal = _mutate_component_manifest(fixture, mutation)

    with pytest.raises(HelperError, match="component_manifest_invalid"):
        helper_module._validate_component_manifest(journal)


def test_component_manifest_rejects_a_substituted_installed_component(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _install_new_component_targets(fixture)
    journal = UpdateJournal.load(fixture.journal_path)
    fixture.mcp.write_bytes(b"substituted mcp binary")

    with pytest.raises(HelperError, match="component_manifest_invalid"):
        helper_module._validate_component_manifest(journal)


def test_component_manifest_file_is_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _install_new_component_targets(fixture)
    journal = UpdateJournal.load(fixture.journal_path)
    fixture.component_manifest.unlink()

    with pytest.raises(HelperError, match="component_manifest_invalid"):
        helper_module._validate_component_manifest(journal)


@pytest.mark.parametrize("failed_step", ["state_cleanup", "unregister", "launch"])
def test_post_commit_side_effect_failure_keeps_terminal_install_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_step: str,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))

    def fail_post_commit(_value: object) -> None:
        raise OSError(failed_step)

    if failed_step == "state_cleanup":
        original_update_state = helper_module._update_state

        def fail_state_cleanup(
            journal: UpdateJournal,
            *,
            phase: str,
            error: str | None,
            clear_transaction: bool,
        ) -> None:
            if clear_transaction and journal.phase is HelperPhase.COMMITTED:
                raise OSError("state_cleanup")
            original_update_state(
                journal,
                phase=phase,
                error=error,
                clear_transaction=clear_transaction,
            )

        monkeypatch.setattr(helper_module, "_update_state", fail_state_cleanup)
    elif failed_step == "unregister":
        monkeypatch.setattr(helper_module, "unregister_recovery", fail_post_commit)
    else:
        monkeypatch.setattr(helper_module, "_launch_core", fail_post_commit)

    assert run_transaction(fixture.journal_path) == 0

    journal = UpdateJournal.load(fixture.journal_path)
    assert journal.phase is HelperPhase.COMMITTED
    assert journal.last_error_code is None
    assert fixture.application.read_bytes() == fixture.replacement
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "installed"
    if failed_step == "state_cleanup":
        assert state["transaction_path"] == str(fixture.journal_path)
        assert state["handoff_identity"] == helper_module.journal_handoff_identity(journal)
        assert state["completed_handoff_identity"] is None
        assert state["last_error"] is None
    else:
        assert state["transaction_path"] is None
        assert state["completed_handoff_identity"] == helper_module.journal_handoff_identity(
            journal
        )
        assert state["last_error"] == helper_module.POST_COMMIT_DEGRADED_ERROR
    if failed_step in {"state_cleanup", "unregister"}:
        assert launched == ["0.1.0"]
    else:
        assert launched == []

    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    if failed_step == "state_cleanup":
        assert ensure_recovery_before_core() is False
    else:
        assert ensure_recovery_before_core() is True


def test_crash_after_committed_journal_publication_replays_without_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    original_update_state = helper_module._update_state
    crashed = False

    def crash_after_terminal_journal(
        journal: UpdateJournal,
        *,
        phase: str,
        error: str | None,
        clear_transaction: bool,
    ) -> None:
        nonlocal crashed
        if clear_transaction and journal.phase is HelperPhase.COMMITTED and not crashed:
            crashed = True
            raise SystemExit(86)
        original_update_state(
            journal,
            phase=phase,
            error=error,
            clear_transaction=clear_transaction,
        )

    monkeypatch.setattr(helper_module, "_update_state", crash_after_terminal_journal)

    with pytest.raises(SystemExit, match="86"):
        run_transaction(fixture.journal_path)

    assert crashed is True
    assert UpdateJournal.load(fixture.journal_path).phase is HelperPhase.COMMITTED
    assert fixture.application.read_bytes() == fixture.replacement
    interrupted_state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert interrupted_state["transaction_path"] == str(fixture.journal_path)
    assert launched == []

    assert run_transaction(fixture.journal_path) == 0
    replayed_journal = UpdateJournal.load(fixture.journal_path)
    replayed_state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert replayed_journal.phase is HelperPhase.COMMITTED
    assert replayed_state["transaction_path"] is None
    assert replayed_state["completed_handoff_identity"] == helper_module.journal_handoff_identity(
        replayed_journal
    )
    assert fixture.application.read_bytes() == fixture.replacement
    assert launched == ["0.1.0"]


def test_terminal_replay_rebinds_to_new_packaged_identity_after_cutover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    old_source = TEST_SOURCE_COMMIT
    new_source = "b" * 40
    unrelated_source = "c" * 40
    journal = UpdateJournal.load(fixture.journal_path)
    component = json.loads(fixture.component_manifest.read_text(encoding="utf-8"))
    component["source_commit"] = new_source
    component_raw = canonical_json(component)
    fixture.component_manifest.write_bytes(component_raw)
    fixture.component_manifest.with_name(CHECKSUM_FILE_NAME).write_bytes(
        f"{hashlib.sha256(component_raw).hexdigest()}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    )
    journal.target_source_commit = new_source
    journal.component_manifest_sha256 = hashlib.sha256(component_raw).hexdigest()
    journal.component_manifest_size = len(component_raw)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "offered_source_commit": new_source,
            "handoff_identity": None,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
        }
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    journal.save(fixture.journal_path)
    helper_module.bind_recovery_authority(journal, fixture.journal_path)
    helper_module.bind_handoff_state(journal, fixture.journal_path)

    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    monkeypatch.setattr(helper_module, "_packaged_helper_runtime", lambda: False)
    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: old_source)
    original_update_state = helper_module._update_state
    crashed = False

    def crash_after_terminal_journal(
        current: UpdateJournal,
        *,
        phase: str,
        error: str | None,
        clear_transaction: bool,
    ) -> None:
        nonlocal crashed
        if clear_transaction and current.phase is HelperPhase.COMMITTED and not crashed:
            crashed = True
            raise SystemExit(86)
        original_update_state(
            current,
            phase=phase,
            error=error,
            clear_transaction=clear_transaction,
        )

    monkeypatch.setattr(helper_module, "_update_state", crash_after_terminal_journal)
    with pytest.raises(SystemExit, match="86"):
        run_transaction(fixture.journal_path)
    assert crashed is True
    assert json.loads(fixture.state_path.read_text(encoding="utf-8"))["current_source_commit"] == (
        new_source
    )

    monkeypatch.setattr(helper_module, "_packaged_helper_runtime", lambda: True)
    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: unrelated_source)
    with pytest.raises(HelperError, match="journal_identity_invalid"):
        UpdateJournal.load(fixture.journal_path)

    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: new_source)
    assert run_transaction(fixture.journal_path) == 0
    replayed = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert replayed["transaction_path"] is None
    assert replayed["current_source_commit"] == new_source
    assert UpdateJournal.load(fixture.journal_path).phase is HelperPhase.COMMITTED


def test_packaged_terminal_replay_dispatches_old_helper_to_target_and_rejects_bad_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    old_source = TEST_SOURCE_COMMIT
    new_source = "b" * 40
    journal = UpdateJournal.load(fixture.journal_path)
    component = json.loads(fixture.component_manifest.read_text(encoding="utf-8"))
    component["source_commit"] = new_source
    component_raw = canonical_json(component)
    fixture.component_manifest.write_bytes(component_raw)
    fixture.component_manifest.with_name(CHECKSUM_FILE_NAME).write_bytes(
        f"{hashlib.sha256(component_raw).hexdigest()}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    )
    journal.target_source_commit = new_source
    journal.component_manifest_sha256 = hashlib.sha256(component_raw).hexdigest()
    journal.component_manifest_size = len(component_raw)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "offered_source_commit": new_source,
            "handoff_identity": None,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
        }
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    journal.save(fixture.journal_path)
    helper_module.bind_recovery_authority(journal, fixture.journal_path)
    helper_module.bind_handoff_state(journal, fixture.journal_path)

    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    monkeypatch.setattr(helper_module, "_packaged_helper_runtime", lambda: False)
    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: old_source)
    original_update_state = helper_module._update_state
    crashed = False

    def crash_after_terminal_journal(
        current: UpdateJournal,
        *,
        phase: str,
        error: str | None,
        clear_transaction: bool,
    ) -> None:
        nonlocal crashed
        if clear_transaction and current.phase is HelperPhase.COMMITTED and not crashed:
            crashed = True
            raise SystemExit(86)
        original_update_state(
            current,
            phase=phase,
            error=error,
            clear_transaction=clear_transaction,
        )

    monkeypatch.setattr(helper_module, "_update_state", crash_after_terminal_journal)
    with pytest.raises(SystemExit, match="86"):
        run_transaction(fixture.journal_path)
    assert crashed is True
    assert fixture.application.read_bytes() == fixture.replacement
    target_helper = Path(journal.stable_update_helper_path)
    old_helper = Path(journal.helper_path)

    monkeypatch.setattr(helper_module, "_packaged_helper_runtime", lambda: True)
    dispatched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "_spawn_recovery_helper",
        lambda helper, journal_path: dispatched.append((helper, journal_path)),
    )

    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: new_source)
    helper_module.launch_recovery_helper(old_helper, fixture.journal_path)
    assert dispatched == [(target_helper, fixture.journal_path)]
    dispatched.clear()

    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: old_source)
    monkeypatch.setattr(helper_module.sys, "executable", str(old_helper))
    assert run_transaction(fixture.journal_path) == 0
    assert dispatched == [(target_helper, fixture.journal_path)]

    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: new_source)
    assert run_transaction(fixture.journal_path) == 0
    assert json.loads(fixture.state_path.read_text(encoding="utf-8"))["transaction_path"] is None

    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: old_source)
    monkeypatch.setattr(helper_module.sys, "executable", str(tmp_path / "unrelated.exe"))
    with pytest.raises(HelperError, match="recovery_helper_untrusted"):
        run_transaction(fixture.journal_path)

    monkeypatch.setattr(helper_module.sys, "executable", str(old_helper))
    old_helper_bytes = old_helper.read_bytes()
    old_helper.write_bytes(b"tampered old helper")
    with pytest.raises(HelperError, match="journal_digest_invalid"):
        run_transaction(fixture.journal_path)
    old_helper.write_bytes(old_helper_bytes)

    target_helper_bytes = target_helper.read_bytes()
    target_helper.write_bytes(b"stale target helper")
    with pytest.raises(HelperError, match="component_manifest_invalid"):
        run_transaction(fixture.journal_path)
    target_helper.write_bytes(target_helper_bytes)


def test_packaged_core_start_guard_dispatches_authenticated_terminal_replay_to_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    target_helper, _old_helper, old_source, new_source = (
        _prepare_interrupted_packaged_terminal_replay(fixture, monkeypatch)
    )
    assert old_source != new_source
    state_before = fixture.state_path.read_bytes()
    journal_before = fixture.journal_path.read_bytes()
    dispatched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "_spawn_recovery_helper",
        lambda helper, journal_path: dispatched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert dispatched == [(target_helper, fixture.journal_path)]
    assert fixture.state_path.read_bytes() == state_before
    assert fixture.journal_path.read_bytes() == journal_before

    dispatched.clear()
    assert ensure_recovery_before_core() is False
    assert dispatched == [(target_helper, fixture.journal_path)]

    dispatched.clear()
    monkeypatch.setattr(helper_module, "_packaged_source_commit", lambda: new_source)
    assert ensure_recovery_before_core() is False
    assert dispatched == [(target_helper, fixture.journal_path)]


@pytest.mark.parametrize("tampered_target", ["manifest", "updater", "old_helper"])
def test_packaged_core_start_guard_rejects_tampered_terminal_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered_target: str,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    target_helper, old_helper, _old_source, _new_source = (
        _prepare_interrupted_packaged_terminal_replay(fixture, monkeypatch)
    )
    if tampered_target == "manifest":
        fixture.component_manifest.write_bytes(
            fixture.component_manifest.read_bytes() + b"tampered"
        )
    elif tampered_target == "updater":
        target_helper.write_bytes(b"tampered target updater")
    else:
        old_helper.write_bytes(b"tampered old updater")
    dispatched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "_spawn_recovery_helper",
        lambda helper, journal_path: dispatched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert dispatched == []
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "helper_launch_failed"


def test_source_mode_does_not_enter_packaged_terminal_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _target_helper, _old_helper, _old_source, _new_source = (
        _prepare_interrupted_packaged_terminal_replay(fixture, monkeypatch)
    )
    monkeypatch.setattr(helper_module.sys, "frozen", False, raising=False)
    monkeypatch.setattr(helper_module, "_packaged_helper_runtime", lambda: False)
    dispatched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "_spawn_recovery_helper",
        lambda helper, journal_path: dispatched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is True
    assert dispatched == []


@pytest.mark.parametrize("tampered", ["journal", "state"])
def test_packaged_core_start_guard_rejects_tampered_terminal_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tampered: str,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _target_helper, _old_helper, _old_source, _new_source = (
        _prepare_interrupted_packaged_terminal_replay(fixture, monkeypatch)
    )
    if tampered == "journal":
        journal = json.loads(fixture.journal_path.read_text(encoding="utf-8"))
        journal["recovery_authority_mac"] = "0" * 64
        fixture.journal_path.write_text(json.dumps(journal), encoding="utf-8")
    else:
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        state["handoff_identity"] = "0" * 64
        fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    dispatched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "_spawn_recovery_helper",
        lambda helper, journal_path: dispatched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert dispatched == []


@pytest.mark.parametrize("journal_phase", [HelperPhase.BINARY_REPLACED, HelperPhase.ROLLED_BACK])
def test_packaged_core_start_guard_does_not_replay_noncommitted_or_rollback_journal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    journal_phase: HelperPhase,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _target_helper, _old_helper, _old_source, _new_source = (
        _prepare_interrupted_packaged_terminal_replay(fixture, monkeypatch)
    )
    journal = UpdateJournal.load(fixture.journal_path, terminal_replay=True)
    if journal_phase is HelperPhase.ROLLED_BACK:
        journal.phase = journal_phase
        journal.terminal_authority_mac = None
        helper_module.seal_terminal_recovery_authority(journal)
        journal.save(fixture.journal_path)
        state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
        state.update(
            {
                "phase": "rolled_back",
                "current_version": journal.current_version,
                "current_source_commit": journal.current_source_commit,
            }
        )
        fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    else:
        journal.phase = journal_phase
        journal.save(fixture.journal_path)
    dispatched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "_spawn_recovery_helper",
        lambda helper, journal_path: dispatched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    if journal_phase is HelperPhase.ROLLED_BACK:
        assert dispatched == [(_old_helper, fixture.journal_path)]
    else:
        assert dispatched == []
    if journal_phase is not HelperPhase.ROLLED_BACK:
        diagnostic = json.loads(
            (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
                encoding="utf-8"
            )
        )
        assert diagnostic["status"] == "blocked"


def test_packaged_core_start_guard_rejects_stale_terminal_transaction_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _target_helper, _old_helper, _old_source, _new_source = (
        _prepare_interrupted_packaged_terminal_replay(fixture, monkeypatch)
    )
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["transaction_path"] = str(
        fixture.state_path.parent / "transactions" / ("b" * 24) / "journal.json"
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    dispatched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "_spawn_recovery_helper",
        lambda helper, journal_path: dispatched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert dispatched == []


@pytest.mark.parametrize("hostile_report_phase", ["apply", "diagnostics", "health"])
def test_run_transaction_contains_pathological_child_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hostile_report_phase: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(
        helper_module,
        "_run_bounded",
        _fake_commands(
            fixture,
            hostile_report_phase=hostile_report_phase,
            hostile_report=_pathological_json("huge_integer"),
        ),
    )

    assert run_transaction(fixture.journal_path) == 2
    journal = UpdateJournal.load(fixture.journal_path)
    assert journal.phase is HelperPhase.ROLLED_BACK
    assert journal.last_error_code == "metadata_unreadable"
    assert fixture.application.read_bytes() == fixture.old_application
    assert fixture.mcp.read_bytes() == fixture.old_mcp
    assert fixture.recovery.read_bytes() == fixture.old_recovery
    assert fixture.update_helper.read_bytes() == fixture.old_update_helper
    assert fixture.database.is_file()
    connection = sqlite3.connect(fixture.database)
    try:
        assert connection.execute("SELECT value FROM facts").fetchall() == [("before",)]
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'migrated'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()
    assert launched == ["0.1.0"]
    report_name = {
        "apply": "apply-report.json",
        "diagnostics": "diagnostics.json",
        "health": "health.json",
    }[hostile_report_phase]
    assert not (fixture.journal_path.parent / report_name).exists()


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

    with pytest.raises(HelperError, match="recovery_authority_invalid"):
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

    with pytest.raises(HelperError, match="recovery_authority_invalid"):
        run_transaction(fixture.journal_path)

    assert fixture.application.read_bytes() == fixture.old_application


@pytest.mark.parametrize("mutation", ["artifact", "digest"])
def test_pointerless_validation_rejects_artifact_or_digest_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path, validate_storage=False)
    if mutation == "artifact":
        Path(journal.replacement_path).write_bytes(b"mutated replacement")
    else:
        journal.replacement_sha256 = "0" * 64
        journal.save(fixture.journal_path)

    with pytest.raises(HelperError, match="journal_digest_invalid"):
        UpdateJournal.load(fixture.journal_path, validate_storage=False)

    assert fixture.journal_path.is_file()
    assert fixture.journal_path.parent.is_dir()


@pytest.mark.parametrize(
    ("health_result", "expected_code", "expected_outcome"),
    [(0, 0, "installed"), (1, 2, "rolled_back")],
)
def test_terminal_recovery_authority_accepts_legitimate_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    health_result: int,
    expected_code: int,
    expected_outcome: str,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(
        helper_module,
        "_run_bounded",
        _fake_commands(fixture, health_result=health_result),
    )

    assert run_transaction(fixture.journal_path) == expected_code

    journal = UpdateJournal.load(fixture.journal_path, validate_storage=False)
    helper_module.validate_recovery_authority(
        journal,
        fixture.journal_path,
        require_terminal=True,
    )
    assert (
        helper_module.transaction_outcome(
            fixture.journal_path,
            validate_storage=False,
        )
        == expected_outcome
    )


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


@pytest.mark.parametrize(
    "rollback_field",
    [
        "rollback_application_path",
        "rollback_mcp_path",
        "rollback_recovery_path",
        "rollback_update_helper_path",
    ],
)
def test_rollback_requires_every_component_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rollback_field: str,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    Path(getattr(journal, rollback_field)).unlink()

    with pytest.raises(HelperError, match="rollback_target_invalid"):
        helper_module._restore_binaries(journal)


@pytest.mark.parametrize("component", ["application", "mcp", "recovery", "update_helper"])
def test_rollback_revalidates_each_restored_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    target = getattr(fixture, component)
    target.write_bytes(b"substituted rollback target")

    with pytest.raises(HelperError, match="rollback_component_invalid"):
        helper_module._validate_rollback_components(journal)


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
    helper_module.bind_recovery_authority(journal, fixture.journal_path)
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


def test_atomic_json_refuses_after_deterministic_parent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "metadata"
    parent.mkdir()
    original_parent = tmp_path / "original-metadata"
    original_marker = original_parent / "original-marker"
    replacement_marker = parent / "replacement-marker"
    target = parent / "state.json"
    original_mkstemp = helper_module.tempfile.mkstemp
    swapped = False

    def swap_parent(*args: object, **kwargs: object) -> tuple[int, str]:
        nonlocal swapped
        if not swapped:
            swapped = True
            parent.rename(original_parent)
            parent.mkdir()
            original_marker.write_text("original", encoding="utf-8")
            replacement_marker.write_text("replacement", encoding="utf-8")
        return original_mkstemp(*args, **kwargs)

    monkeypatch.setattr(helper_module.tempfile, "mkstemp", swap_parent)

    with pytest.raises(HelperError):
        helper_module._atomic_json(target, {"phase": "error"})

    assert original_marker.read_text(encoding="utf-8") == "original"
    assert replacement_marker.read_text(encoding="utf-8") == "replacement"
    assert not target.exists()


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
    helper_module.bind_recovery_authority(journal, fixture.journal_path)
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

    with pytest.raises(HelperError, match="journal_digest_invalid"):
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

    with pytest.raises(HelperError, match="journal_digest_invalid"):
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
    assert len(launched) == 2
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "journal_invalid"


def test_core_start_guard_relaunches_error_transaction_after_rollback_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    journal.phase = HelperPhase.ROLLING_BACK
    journal.last_error_code = "rollback_retry_required"
    journal.save(fixture.journal_path)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["phase"] = "error"
    state["last_error"] = "The new version did not become healthy and automatic rollback failed"
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    state_before = fixture.state_path.read_bytes()
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == [(Path(journal.helper_path), fixture.journal_path)]
    assert fixture.state_path.read_bytes() == state_before


def test_core_start_guard_allows_published_terminal_pointerless_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    assert run_transaction(fixture.journal_path) == 0
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "installed"
    assert state["transaction_path"] is None
    assert state["completed_handoff_identity"] is not None

    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is True


def test_core_start_guard_blocks_live_retirement_authority_after_tree_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    assert run_transaction(fixture.journal_path) == 0
    tombstone = _write_retirement_tombstone(fixture)
    state_before = fixture.state_path.read_bytes()
    shutil.rmtree(fixture.journal_path.parent)
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert ensure_recovery_before_core() is False
    assert fixture.state_path.read_bytes() == state_before
    assert tombstone.is_file()
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{'a' * 24}"]
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "startup_state_active_without_transaction"


def test_core_start_guard_rejects_completed_identity_without_operation_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    assert run_transaction(fixture.journal_path) == 0
    tombstone = _write_retirement_tombstone(fixture)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["operation_id"] = None
    state_before = json.dumps(state).encode("utf-8")
    fixture.state_path.write_bytes(state_before)
    shutil.rmtree(fixture.journal_path.parent)
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert ensure_recovery_before_core() is False
    assert fixture.state_path.read_bytes() == state_before
    assert tombstone.is_file()
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{'a' * 24}"]
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "startup_state_invalid"


def test_core_start_guard_allows_removed_tree_after_intact_retirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    launched: list[str] = []
    _isolate_runtime(monkeypatch, launched)
    monkeypatch.setattr(helper_module, "_run_bounded", _fake_commands(fixture))
    assert run_transaction(fixture.journal_path) == 0
    tombstone = _write_retirement_tombstone(fixture)
    _RECOVERY_AUTHORITY_VALUES.clear()
    shutil.rmtree(fixture.journal_path.parent)
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert tombstone.is_file()
    assert ensure_recovery_before_core() is True
    assert ensure_recovery_before_core() is True


def test_core_start_guard_recovers_interrupted_unbound_preparation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal_path: launched.append((helper, journal_path)),
    )

    assert ensure_recovery_before_core() is False
    assert ensure_recovery_before_core() is False

    recovered = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert recovered["phase"] == "restart_required"
    assert recovered["transaction_path"] == str(fixture.journal_path)
    assert recovered["operation_id"] == "a" * 24
    assert recovered["handoff_identity"] == helper_module.journal_handoff_identity(
        UpdateJournal.load(fixture.journal_path)
    )
    assert recovered["pending_handoff_identity"] is None
    assert len(launched) == 2


def test_core_start_guard_reclaims_prebinding_crash_and_allows_new_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    journal.recovery_authority_mac = None
    journal.terminal_authority_mac = None
    journal.save(fixture.journal_path)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    state["pending_handoff_identity"] = helper_module.journal_handoff_identity(journal)
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is True
    assert ensure_recovery_before_core() is True

    recovered = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert recovered["phase"] == "error"
    assert recovered["transaction_path"] is None
    assert recovered["operation_id"] is None
    assert recovered["handoff_identity"] is None
    assert recovered["pending_handoff_identity"] is None
    assert not fixture.journal_path.parent.exists()
    assert _RECOVERY_AUTHORITY_VALUES == {}


def test_core_start_guard_reclaims_empty_prebinding_tree_after_cleanup_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    journal.recovery_authority_mac = None
    journal.terminal_authority_mac = None
    journal.save(fixture.journal_path)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    state["pending_handoff_identity"] = helper_module.journal_handoff_identity(journal)
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    original_atomic = helper_module._atomic_json
    failed = False

    def fail_state_reset(
        path: Path,
        value: dict[str, Any],
        *,
        boundary_code: str = "metadata_untrusted",
    ) -> None:
        nonlocal failed
        if path == fixture.state_path and not failed:
            failed = True
            raise HelperError("state_reset_interrupted")
        original_atomic(path, value, boundary_code=boundary_code)

    monkeypatch.setattr(helper_module, "_atomic_json", fail_state_reset)
    assert ensure_recovery_before_core() is False
    assert fixture.state_path.read_text(encoding="utf-8")
    assert not fixture.journal_path.parent.exists()

    monkeypatch.setattr(helper_module, "_atomic_json", original_atomic)
    assert ensure_recovery_before_core() is True
    recovered = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert recovered["transaction_path"] is None
    assert recovered["operation_id"] is None


@pytest.mark.parametrize("raw", [b'{"phase":', b"not-json"])
def test_core_start_guard_preserves_partial_prebinding_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    fixture.journal_path.write_bytes(raw)
    state_before = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state_before["handoff_identity"] = None
    state_before["pending_handoff_identity"] = "a" * 64
    fixture.state_path.write_text(json.dumps(state_before), encoding="utf-8")
    state_bytes = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False

    assert fixture.state_path.read_bytes() == state_bytes
    assert fixture.journal_path.read_bytes() == raw
    assert fixture.journal_path.parent.exists()


def test_core_start_guard_preserves_invalid_prebinding_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    journal.recovery_authority_mac = "0" * 64
    journal.save(fixture.journal_path)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    state["pending_handoff_identity"] = helper_module.journal_handoff_identity(journal)
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    state_bytes = fixture.state_path.read_bytes()
    journal_bytes = fixture.journal_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False

    assert fixture.state_path.read_bytes() == state_bytes
    assert fixture.journal_path.read_bytes() == journal_bytes
    assert fixture.journal_path.parent.exists()


def test_core_start_guard_preserves_extra_prebinding_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    journal.recovery_authority_mac = None
    journal.terminal_authority_mac = None
    journal.save(fixture.journal_path)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    state["pending_handoff_identity"] = helper_module.journal_handoff_identity(journal)
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    marker = fixture.journal_path.parent / "unexpected.bin"
    marker.write_bytes(b"untrusted")
    state_bytes = fixture.state_path.read_bytes()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False

    assert fixture.state_path.read_bytes() == state_bytes
    assert marker.read_bytes() == b"untrusted"
    assert fixture.journal_path.is_file()


def test_core_start_guard_resets_pre_cutover_install_without_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    original_database = fixture.database.read_bytes()
    _signed_staging(fixture, monkeypatch)
    shutil.rmtree(fixture.journal_path.parent)
    fixture.journal_path.parent.mkdir()
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
        (evidence / "journal.json").write_bytes(b"partial journal")
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


def test_core_start_guard_contains_numeric_version_parser_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["offered_version"] = "1" * 5_000 + ".0.0"
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
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
    assert "1" * 5_000 not in json.dumps(diagnostic)


def test_startup_parser_failure_diagnostic_is_fixed_and_content_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(tmp_path / "data"))

    record_startup_recovery_parser_failure()

    diagnostic = json.loads(
        (tmp_path / "data" / "updates" / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["status"] == "blocked"
    assert diagnostic["code"] == "startup_state_invalid"
    assert diagnostic["phase"] is None


def test_helper_contains_huge_manifest_version_without_echo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _, manifest_path, _ = _signed_staging(fixture, monkeypatch)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    huge_version = "1" * 5_000 + ".0.0"
    manifest["version"] = huge_version
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["manifest_identity"] = _digest(manifest_path)[0]

    assert helper_module._pre_cutover_staging_evidence("a" * 24, state) is False


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
    assert diagnostic["code"] == "startup_state_mismatch"


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


def test_startup_diagnostic_writer_contains_atomic_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "new" / "updates" / "state.json"

    def fail_atomic(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("diagnostic write denied")

    monkeypatch.setattr(helper_module, "_atomic_json", fail_atomic)

    helper_module._write_startup_recovery_diagnostic(
        state_path,
        status="blocked",
        code="startup_state_invalid",
    )

    assert not state_path.parent.exists()


def test_startup_diagnostic_writer_creates_missing_parent_with_closed_schema(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "new" / "updates" / "state.json"

    helper_module._write_startup_recovery_diagnostic(
        state_path,
        status="unexpected-status",
        code="untrusted-code",
        phase="not-a-safe-phase",
    )

    diagnostic_path = state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME
    assert startup_recovery_diagnostic(diagnostic_path) == {
        "status": "blocked",
        "code": "startup_state_invalid",
    }
    payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    assert set(payload) == {"schema_version", "status", "code", "phase", "updated_at"}
    assert len(diagnostic_path.read_bytes()) <= helper_module.MAX_STARTUP_RECOVERY_DIAGNOSTIC_BYTES


@pytest.mark.parametrize(
    "marker_kind",
    ["missing", "malformed", "invalid_utf8", "oversized", "non_regular", "hostile_parent"],
)
def test_startup_recovery_diagnostic_is_bounded_for_untrusted_markers(
    tmp_path: Path, marker_kind: str
) -> None:
    if marker_kind == "hostile_parent":
        hostile_parent = tmp_path / "hostile"
        hostile_parent.write_text("not a directory", encoding="utf-8")
        marker_path = hostile_parent / "updates" / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME
    else:
        updates = tmp_path / "updates"
        updates.mkdir()
        marker_path = updates / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME
        if marker_kind == "malformed":
            marker_path.write_bytes(b"{ malformed")
        elif marker_kind == "invalid_utf8":
            marker_path.write_bytes(b"\xff")
        elif marker_kind == "oversized":
            marker_path.write_bytes(
                b"x" * (helper_module.MAX_STARTUP_RECOVERY_DIAGNOSTIC_BYTES + 1)
            )
        elif marker_kind == "non_regular":
            marker_path.mkdir()

    expected = (
        None
        if marker_kind == "missing"
        else {
            "status": "unreadable",
            "code": "diagnostic_unreadable",
        }
    )
    assert startup_recovery_diagnostic(marker_path) == expected


def test_startup_recovery_diagnostic_bounds_read_after_marker_grows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker_path = tmp_path / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME
    marker_path.write_text("{}", encoding="utf-8")
    read_sizes: list[int] = []
    grew = False
    original_open = Path.open

    class TrackingReader:
        def __init__(self, handle: object) -> None:
            self._handle = handle

        def __enter__(self) -> TrackingReader:
            self._handle.__enter__()  # type: ignore[attr-defined]
            return self

        def __exit__(self, *args: object) -> object:
            return self._handle.__exit__(*args)  # type: ignore[attr-defined]

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return self._handle.read(size)  # type: ignore[attr-defined,no-any-return]

    def grow_before_open(path: Path, *args: object, **kwargs: object) -> TrackingReader:
        nonlocal grew
        if path == marker_path and not grew:
            grew = True
            with original_open(marker_path, "wb") as stream:
                stream.write(b"{}" + b"x" * helper_module.MAX_STARTUP_RECOVERY_DIAGNOSTIC_BYTES)
        return TrackingReader(original_open(path, *args, **kwargs))

    monkeypatch.setattr(Path, "open", grow_before_open)

    assert startup_recovery_diagnostic(marker_path) == {
        "status": "unreadable",
        "code": "diagnostic_unreadable",
    }
    assert read_sizes == [helper_module.MAX_STARTUP_RECOVERY_DIAGNOSTIC_BYTES + 1]
    assert marker_path.stat().st_size > helper_module.MAX_STARTUP_RECOVERY_DIAGNOSTIC_BYTES


def test_startup_recovery_diagnostic_rejects_reparse_marker_when_supported(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    marker_path = tmp_path / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME
    try:
        marker_path.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this filesystem")

    assert startup_recovery_diagnostic(marker_path) == {
        "status": "unreadable",
        "code": "diagnostic_unreadable",
    }


def test_core_start_guard_ignores_untrusted_existing_marker_without_escaping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "idle",
            "transaction_path": None,
            "operation_id": None,
            "manifest_identity": None,
            "downloaded_path": None,
            "backup_path": None,
            "handoff_identity": None,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
        }
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    diagnostic_path = fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME
    diagnostic_path.mkdir()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert diagnostic_path.is_dir()


def test_core_start_guard_stays_blocked_when_diagnostic_marker_is_non_regular(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    fixture.state_path.write_bytes(b"{ malformed")
    diagnostic_path = fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME
    diagnostic_path.mkdir()
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    assert diagnostic_path.is_dir()


def test_core_start_guard_contains_marker_probe_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "idle",
            "transaction_path": None,
            "operation_id": None,
            "manifest_identity": None,
            "downloaded_path": None,
            "backup_path": None,
            "handoff_identity": None,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
        }
    )
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")
    diagnostic_path = fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME
    original_stat = helper_module._plain_file_stat_if_present

    def raced_stat(path: Path, code: str) -> os.stat_result | None:
        if path == diagnostic_path and code == "startup_diagnostic_untrusted":
            raise FileNotFoundError("marker changed during probe")
        return original_stat(path, code)

    monkeypatch.setattr(helper_module, "_plain_file_stat_if_present", raced_stat)
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    assert ensure_recovery_before_core() is False
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["code"] == "startup_state_missing_with_transaction"


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


@pytest.mark.parametrize("kind", ["huge_integer", "deep_nesting"])
def test_frozen_core_guard_contains_real_pathological_state_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    raw = _pathological_json(kind)
    fixture.state_path.write_bytes(raw)
    original_database = fixture.database.read_bytes()
    launched: list[tuple[Path, Path]] = []
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        helper_module,
        "launch_recovery_helper",
        lambda helper, journal: launched.append((helper, journal)),
    )

    assert ensure_recovery_before_core() is False
    assert launched == []
    assert fixture.state_path.read_bytes() == raw
    assert fixture.database.read_bytes() == original_database
    diagnostic = json.loads(
        (fixture.state_path.parent / helper_module.STARTUP_RECOVERY_DIAGNOSTIC_NAME).read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic == {
        "schema_version": 1,
        "status": "blocked",
        "code": "metadata_unreadable",
        "phase": None,
        "updated_at": diagnostic["updated_at"],
    }
    assert "value" not in json.dumps(diagnostic)


@pytest.mark.parametrize("kind", ["huge_integer", "deep_nesting"])
def test_pre_cutover_staging_parser_failure_refuses_state_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    _, manifest_path, _ = _signed_staging(fixture, monkeypatch)
    manifest_path.write_bytes(_pathological_json(kind))
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
    assert "value" not in json.dumps(diagnostic)


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
    secret = helper_module._recovery_authority_secret(journal.operation_id, create=False)
    journal.recovery_authority_mac = helper_module._recovery_authority_mac(
        secret,
        journal.operation_id,
        next_identity,
    )

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


def test_prebinding_handoff_transition_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    journal.recovery_authority_mac = None
    journal.terminal_authority_mac = None
    journal.save(fixture.journal_path)
    state = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    state["handoff_identity"] = None
    state["pending_handoff_identity"] = None
    fixture.state_path.write_text(json.dumps(state), encoding="utf-8")

    identity = helper_module.prepare_handoff_state(journal, fixture.journal_path)
    assert helper_module.prepare_handoff_state(journal, fixture.journal_path) == identity
    pending = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert pending["handoff_identity"] is None
    assert pending["pending_handoff_identity"] == identity

    helper_module.bind_recovery_authority(journal, fixture.journal_path)
    assert helper_module.bind_handoff_state(journal, fixture.journal_path) == identity
    assert helper_module.bind_handoff_state(journal, fixture.journal_path) == identity
    bound = json.loads(fixture.state_path.read_text(encoding="utf-8"))
    assert bound["handoff_identity"] == identity
    assert bound["pending_handoff_identity"] is None


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

    with pytest.raises(HelperError, match="journal_digest_invalid"):
        run_transaction(fixture.journal_path)


def test_terminal_journal_requires_state_first_terminal_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    journal.phase = HelperPhase.COMMITTED
    journal.save(fixture.journal_path)

    with pytest.raises(HelperError, match="recovery_authority_invalid"):
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
        ("schema_version", True),
        ("schema_version", 1.0),
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


@pytest.mark.parametrize(
    "field",
    [
        "current_source_commit",
        "target_source_commit",
        "rollback_source_commit",
        "recovery_source_commit",
    ],
)
@pytest.mark.parametrize("value", [None, "b" * 40, "a" * 39, True])
def test_frozen_helper_rejects_missing_or_conflicting_journal_source_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = json.loads(fixture.journal_path.read_text(encoding="utf-8"))
    journal[field] = value
    fixture.journal_path.write_text(json.dumps(journal), encoding="utf-8")
    monkeypatch.setattr(helper_module.sys, "frozen", True, raising=False)

    if field == "target_source_commit" and isinstance(value, str) and len(value) == 40:
        journal_value = UpdateJournal.load(fixture.journal_path)
        with pytest.raises(HelperError, match="component_manifest_invalid"):
            helper_module._validate_component_manifest(journal_value)
    else:
        with pytest.raises(HelperError, match="journal_identity_invalid"):
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


@pytest.mark.parametrize("kind", ["huge_integer", "deep_nesting"])
def test_journal_entry_points_contain_pathological_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    raw = _pathological_json(kind)
    fixture.journal_path.write_bytes(raw)

    with pytest.raises(HelperError) as raised:
        UpdateJournal.load(fixture.journal_path)
    assert raised.value.code == "metadata_unreadable"
    assert helper_module.transaction_outcome(fixture.journal_path) == "failed"
    assert journal_failure_diagnostic(fixture.journal_path) == (
        '{"journal_status": "metadata_unreadable"}'
    )
    with pytest.raises(HelperError, match="metadata_unreadable"):
        helper_module.request_rollback(fixture.journal_path)
    assert main(["--journal", str(fixture.journal_path)]) == 3
    assert fixture.journal_path.read_bytes() == raw


@pytest.mark.parametrize("state_entry_point", ["validate", "transition", "bind", "update"])
def test_application_state_callers_preserve_parser_error_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state_entry_point: str
) -> None:
    fixture = _transaction(tmp_path, monkeypatch)
    journal = UpdateJournal.load(fixture.journal_path)
    raw = _pathological_json("huge_integer")
    fixture.state_path.write_bytes(raw)

    with pytest.raises(HelperError) as raised:
        if state_entry_point == "validate":
            helper_module._validate_handoff_state(journal, fixture.journal_path)
        elif state_entry_point == "transition":
            helper_module._transition_handoff_state(
                journal,
                fixture.journal_path,
                previous_identity=helper_module.journal_handoff_identity(journal),
            )
        elif state_entry_point == "bind":
            helper_module.bind_handoff_state(journal, fixture.journal_path)
        else:
            helper_module._update_state(
                journal,
                phase="installed",
                error=None,
                clear_transaction=False,
            )

    assert raised.value.code == "metadata_unreadable"
    assert fixture.state_path.read_bytes() == raw
