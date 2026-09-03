from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import urllib.error
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import allthecontext.updater as updater_module
import allthecontext.windows_update_helper as update_helper_module
import pytest
from allthecontext.installed_component_manifest import (
    CHECKSUM_FILE_NAME,
    MANIFEST_FILE_NAME,
    canonical_json,
)
from allthecontext.release_manifest import (
    canonical_payload,
    create_manifest,
    public_key_fingerprint,
    public_key_value,
)
from allthecontext.updater import (
    DEFAULT_BETA_MANIFEST_URL,
    MAX_ARTIFACT_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_STATE_BYTES,
    HttpsTransport,
    InstallPlan,
    PlatformInstaller,
    UpdateBusyError,
    UpdateConfig,
    UpdateEndpointHttpError,
    UpdateError,
    UpdateManager,
    UpdatePhase,
    UpdateState,
)
from allthecontext.windows_update_helper import (
    MAX_JOURNAL_BYTES,
    HelperPhase,
    UpdateJournal,
    journal_handoff_identity,
)
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SEED = bytes(range(32))
_RECOVERY_AUTHORITY_VALUES: dict[str, str] = {}


def _windows_component_manifest(setup: bytes, *, version: str = "0.2.0") -> tuple[bytes, bytes]:
    digest = hashlib.sha256(setup).hexdigest()
    payload = {
        "architecture": "x86_64",
        "component_count": 4,
        "components": [
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContext.exe",
                "role": "main",
                "sha256": digest,
                "size": len(setup),
            },
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContextMCP.exe",
                "role": "mcp",
                "sha256": digest,
                "size": len(setup),
            },
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContextRecovery.exe",
                "role": "recovery",
                "sha256": digest,
                "size": len(setup),
            },
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContextUpdater.exe",
                "role": "updater",
                "sha256": digest,
                "size": len(setup),
            },
        ],
        "manifest_type": "installed-component",
        "package": {
            "direct_package": {
                "filename": "all-the-context-windows-x86_64-unsigned.exe",
                "sha256": digest,
                "size": len(setup),
            },
            "filename": "AllTheContextSetup.exe",
            "sha256": digest,
            "size": len(setup),
        },
        "platform": "windows",
        "schema_version": 1,
        "source_commit": "0" * 40,
        "version": version,
    }
    raw = canonical_json(payload)
    checksum = f"{hashlib.sha256(raw).hexdigest()}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    return raw, checksum


class _RecoveryAuthorityStore:
    def __init__(self, *, fail_delete: bool = False) -> None:
        self.fail_delete = fail_delete

    def get(self, name: str) -> str | None:
        return _RECOVERY_AUTHORITY_VALUES.get(name)

    def set(self, name: str, value: str) -> None:
        _RECOVERY_AUTHORITY_VALUES[name] = value

    def delete(self, name: str) -> None:
        if not self.fail_delete:
            _RECOVERY_AUTHORITY_VALUES.pop(name, None)


@pytest.fixture(autouse=True)
def _use_test_recovery_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    _RECOVERY_AUTHORITY_VALUES.clear()
    monkeypatch.setattr(
        update_helper_module,
        "_recovery_authority_store",
        lambda: _RecoveryAuthorityStore(),
    )


class FakeTransport:
    def __init__(self, manifest: dict[str, Any], artifact: bytes) -> None:
        self.manifest = manifest
        self.artifact = artifact
        self.metadata_error: UpdateError | None = None
        self.download_error: UpdateError | None = None
        self.reported_bytes: int | None = None
        self.reported_digest: str | None = None
        self.cancel_during_download = False
        self.metadata_bytes: bytes | None = None
        self.metadata_calls = 0
        self.stream_calls = 0
        self.stream_urls: list[str] = []

    def get_bytes(self, url: str, *, maximum_bytes: int) -> bytes:
        self.metadata_calls += 1
        assert url.startswith("https://")
        assert maximum_bytes == MAX_MANIFEST_BYTES
        if self.metadata_error:
            raise self.metadata_error
        if self.metadata_bytes is not None:
            return self.metadata_bytes
        return json.dumps(self.manifest).encode("utf-8")

    def stream(
        self,
        url: str,
        target: Path,
        *,
        expected_bytes: int,
        cancelled: Any,
    ) -> tuple[str, int]:
        self.stream_calls += 1
        self.stream_urls.append(url)
        assert url.startswith("https://")
        assert expected_bytes == self.manifest["size"]
        if self.download_error:
            raise self.download_error
        if self.cancel_during_download:
            raise UpdateError("Update download was cancelled")
        target.write_bytes(self.artifact)
        return (
            self.reported_digest or hashlib.sha256(self.artifact).hexdigest(),
            self.reported_bytes if self.reported_bytes is not None else len(self.artifact),
        )


@dataclass
class FakeInstaller:
    supported: bool = True
    failure: str | None = None
    rollback_failure: str | None = None
    handed_off: bool = False
    rolled_back: bool = False
    expected_target_version: str | None = "0.2.0"

    @property
    def unsupported_reason(self) -> str:
        return "Manual installation is required on this test platform"

    def preflight(self, artifact: Path, required_bytes: int) -> None:
        assert artifact.is_file()
        assert required_bytes > 0
        if self.failure == "preflight":
            raise UpdateError("Insufficient disk space")

    def handoff(self, plan: InstallPlan) -> None:
        assert plan.artifact.is_file()
        if self.expected_target_version is not None:
            assert plan.target_version == self.expected_target_version
        assert plan.operation_dir.is_dir()
        if self.failure == "locked":
            raise UpdateError("Installed files are locked")
        if self.failure == "crash":
            raise UpdateError("Installer process crashed")
        plan.transaction_dir.mkdir(parents=True, exist_ok=True)
        if self.failure in {"empty", "incomplete", "partial-journal"}:
            if self.failure == "incomplete":
                (plan.transaction_dir / "replacement").mkdir()
                (plan.transaction_dir / "rollback").mkdir()
            elif self.failure == "partial-journal":
                (plan.transaction_dir / "journal.json").write_bytes(b'{"phase":')
            raise UpdateError("Installer process crashed")
        install_dir = plan.transaction_dir / "installed"
        rollback_dir = plan.transaction_dir / "rollback"
        replacement = plan.transaction_dir / "replacement" / "AllTheContextSetup.exe"
        transaction_files = (
            replacement,
            rollback_dir / "AllTheContext.exe",
            rollback_dir / "AllTheContextMCP.exe",
            rollback_dir / "AllTheContextRecovery.exe",
            rollback_dir / "AllTheContextUpdater.exe",
            plan.transaction_dir / "AllTheContextUpdater.exe",
        )
        for path in transaction_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"test transaction evidence")
        evidence_digest = hashlib.sha256(b"test transaction evidence").hexdigest()
        evidence_size = len(b"test transaction evidence")
        backup_digest = hashlib.sha256(plan.database_backup_path.read_bytes()).hexdigest()
        backup_size = plan.database_backup_path.stat().st_size
        journal = UpdateJournal(
            operation_id=plan.operation_id,
            phase=HelperPhase.PREPARED,
            current_version=plan.current_version,
            target_version=plan.target_version,
            parent_pid=0,
            application_path=str(install_dir / "AllTheContext.exe"),
            replacement_path=str(replacement),
            replacement_sha256=evidence_digest,
            replacement_size=evidence_size,
            rollback_application_path=str(rollback_dir / "AllTheContext.exe"),
            rollback_application_sha256=evidence_digest,
            rollback_application_size=evidence_size,
            mcp_path=str(install_dir / "AllTheContextMCP.exe"),
            rollback_mcp_path=str(rollback_dir / "AllTheContextMCP.exe"),
            rollback_mcp_sha256=evidence_digest,
            rollback_mcp_size=evidence_size,
            recovery_path=str(install_dir / "AllTheContextRecovery.exe"),
            rollback_recovery_path=str(rollback_dir / "AllTheContextRecovery.exe"),
            rollback_recovery_sha256=evidence_digest,
            rollback_recovery_size=evidence_size,
            stable_update_helper_path=str(install_dir / "AllTheContextUpdater.exe"),
            rollback_update_helper_path=str(rollback_dir / "AllTheContextUpdater.exe"),
            rollback_update_helper_sha256=evidence_digest,
            rollback_update_helper_size=evidence_size,
            database_path=str(plan.database_path),
            database_backup_path=str(plan.database_backup_path),
            database_backup_sha256=backup_digest,
            database_backup_size=backup_size,
            state_path=str(plan.state_path),
            helper_path=str(plan.transaction_dir / "AllTheContextUpdater.exe"),
            core_host=plan.core_host,
            core_port=plan.core_port,
            recovery_helper_sha256=evidence_digest,
            recovery_helper_size=evidence_size,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )
        journal_path = plan.transaction_dir / "journal.json"
        journal.save(journal_path)
        update_helper_module.bind_recovery_authority(journal, journal_path)
        state = json.loads(plan.state_path.read_text(encoding="utf-8"))
        state["handoff_identity"] = journal_handoff_identity(journal)
        state["pending_handoff_identity"] = None
        state["completed_handoff_identity"] = None
        plan.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.handed_off = True
        if self.failure == "after-journal":
            raise UpdateError("Installer process crashed")

    def recovery_outcome(self, state: UpdateState) -> str | None:
        del state
        return None

    def rollback(self, state: UpdateState) -> None:
        assert state.backup_path
        if self.rollback_failure:
            raise UpdateError(self.rollback_failure)
        self.rolled_back = True


@dataclass
class FakeHealth:
    result: bool

    def healthy(self) -> bool:
        return self.result


def _fixture(
    tmp_path: Path,
    *,
    version: str = "0.2.0",
    channel: str = "stable",
    platform_name: str = "windows",
    architecture: str = "x86_64",
    minimum: str = "0.1.0",
) -> tuple[dict[str, Any], bytes, Path]:
    artifact = b"signed release archive\n"
    artifact_path = tmp_path / f"all-the-context-{version}-{platform_name}-{architecture}.zip"
    artifact_path.write_bytes(artifact)
    private = Ed25519PrivateKey.from_private_bytes(SEED)
    manifest = create_manifest(
        artifact=artifact_path,
        version=version,
        channel=cast(Any, channel),
        platform_name=cast(Any, platform_name),
        architecture=cast(Any, architecture),
        artifact_url=(
            f"https://updates.example.test/releases/v{version}/"
            f"all-the-context-{version}-{platform_name}-{architecture}.zip"
        ),
        minimum_supported_version=minimum,
        mandatory=False,
        release_notes_url=f"https://updates.example.test/releases/v{version}",
        key_id="test-release-key",
        private_key=private,
    )
    keyring_path = tmp_path / "keys.json"
    keyring_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "keys": [
                    {
                        "key_id": "test-release-key",
                        "algorithm": "Ed25519",
                        "public_key": public_key_value(private),
                        "public_key_sha256": public_key_fingerprint(public_key_value(private)),
                        "channels": ["stable", "beta"],
                        "status": "active",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest, artifact, keyring_path


def _manager(
    tmp_path: Path,
    manifest: dict[str, Any],
    artifact: bytes,
    keyring: Path,
    *,
    current_version: str = "0.1.0",
    installer: FakeInstaller | None = None,
    health: FakeHealth | None = None,
) -> tuple[UpdateManager, FakeTransport, FakeInstaller]:
    database = tmp_path / "core.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT OR IGNORE INTO records VALUES (1)")
    transport = FakeTransport(manifest, artifact)
    active_installer = installer or FakeInstaller()
    manager = UpdateManager(
        UpdateConfig(
            tmp_path / "updates",
            keyring,
            {"stable": "https://updates.example.test/stable/manifest.json"},
            current_version=current_version,
            platform_name="windows",
            architecture="x86_64",
        ),
        database_path=database,
        transport=transport,
        installer=active_installer,
        health_probe=health or FakeHealth(True),
    )
    return manager, transport, active_installer


def _publish_helper_terminal(
    manager: UpdateManager,
    outcome: str,
    *,
    clear_transaction: bool = True,
) -> None:
    """Model the helper's state-first terminal publication in an updater fixture."""

    journal_path = Path(manager.state.transaction_path or "")
    journal = UpdateJournal.load(journal_path, validate_storage=False)
    identity = journal_handoff_identity(journal)
    terminal_phase = HelperPhase.COMMITTED if outcome == "installed" else HelperPhase.ROLLED_BACK
    state = json.loads(manager.state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "installed" if outcome == "installed" else "rolled_back",
            "current_version": (
                journal.target_version if outcome == "installed" else journal.current_version
            ),
            "downloaded_path": None,
            "last_error": (
                None
                if outcome == "installed"
                else "The update did not become healthy; the previous app and vault were restored"
            ),
            "handoff_identity": identity,
            "pending_handoff_identity": None,
            "completed_handoff_identity": None,
        }
    )
    manager.state_path.write_text(json.dumps(state), encoding="utf-8")
    journal.phase = terminal_phase
    update_helper_module.seal_terminal_recovery_authority(journal)
    journal.save(journal_path)
    if clear_transaction:
        state.update(
            {
                "transaction_path": None,
                "handoff_identity": None,
                "completed_handoff_identity": identity,
            }
        )
        manager.state_path.write_text(json.dumps(state), encoding="utf-8")


def _completed_manager(
    tmp_path: Path,
    manifest: dict[str, Any],
    artifact: bytes,
    keyring: Path,
    *,
    outcome: str = "installed",
) -> tuple[UpdateManager, str]:
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"
    _publish_helper_terminal(manager, outcome)
    manager.state = manager._load_state()
    manager.state.current_version = manager.config.current_version
    return manager, cast(str, manager.state.operation_id)


def _beta_manager(tmp_path: Path, keyring: Path) -> UpdateManager:
    database = tmp_path / "core.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER PRIMARY KEY)")
    return UpdateManager(
        UpdateConfig(
            tmp_path / "updates",
            keyring,
            {"beta": "https://updates.example.test/beta/manifest.json"},
            current_version="0.1.0-beta.1",
            platform_name="windows",
            architecture="x86_64",
        ),
        database_path=database,
    )


def test_packaged_windows_beta_uses_the_project_channel_when_a_key_is_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater_module, "current_platform", lambda: ("windows", "x86_64"))
    monkeypatch.setattr(
        updater_module,
        "load_keyring",
        lambda _path: {
            "keys": [{"channels": ["beta"], "status": "active"}],
        },
    )
    monkeypatch.delenv("ATC_UPDATE_STABLE_URL", raising=False)
    monkeypatch.delenv("ATC_UPDATE_BETA_URL", raising=False)

    config = UpdateConfig.default()

    assert config.manifest_urls == {"beta": DEFAULT_BETA_MANIFEST_URL}
    assert config.platform_name == "windows"
    assert config.architecture == "x86_64"


def test_installed_windows_runtime_recovers_packaged_channel_without_frozen_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "AllTheContext.exe"
    executable.write_bytes(b"app")
    executable.with_name("AllTheContextUpdater.exe").write_bytes(b"helper")
    monkeypatch.delattr(updater_module.sys, "frozen", raising=False)
    monkeypatch.setattr(updater_module.sys, "executable", str(executable))
    monkeypatch.setattr(updater_module, "current_platform", lambda: ("windows", "x86_64"))
    monkeypatch.setattr(
        updater_module,
        "load_keyring",
        lambda _path: {
            "keys": [{"channels": ["beta"], "status": "active"}],
        },
    )
    monkeypatch.delenv("ATC_UPDATE_STABLE_URL", raising=False)
    monkeypatch.delenv("ATC_UPDATE_BETA_URL", raising=False)

    config = UpdateConfig.default()

    assert config.manifest_urls == {"beta": DEFAULT_BETA_MANIFEST_URL}


def test_source_build_and_packaged_build_without_a_trusted_key_have_no_default_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(updater_module, "current_platform", lambda: ("windows", "x86_64"))
    monkeypatch.setattr(updater_module, "load_keyring", lambda _path: {"keys": []})
    monkeypatch.delenv("ATC_UPDATE_STABLE_URL", raising=False)
    monkeypatch.delenv("ATC_UPDATE_BETA_URL", raising=False)

    monkeypatch.delattr(updater_module.sys, "frozen", raising=False)
    assert UpdateConfig.default().manifest_urls == {}

    monkeypatch.setattr(updater_module.sys, "frozen", True, raising=False)
    assert UpdateConfig.default().manifest_urls == {}


def test_prerelease_defaults_and_migrates_to_the_available_beta_channel(
    tmp_path: Path,
) -> None:
    _, _, keyring = _fixture(
        tmp_path,
        version="0.2.0-beta.1",
        channel="beta",
        minimum="0.1.0-beta.1",
    )
    manager = _beta_manager(tmp_path, keyring)
    assert manager.preferences.channel == "beta"

    manager.preferences_path.write_text(
        json.dumps({"enabled": True, "channel": "stable", "deferred_version": "0.1.0"}),
        encoding="utf-8",
    )
    migrated = _beta_manager(tmp_path, keyring)
    assert migrated.preferences.channel == "beta"
    assert migrated.preferences.deferred_version is None


def test_canonical_beta_404_is_a_truthful_unpublished_channel_state(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(
        tmp_path,
        version="0.2.0-beta.1",
        channel="beta",
        minimum="0.1.0-beta.1",
    )
    database = tmp_path / "core.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY)")
    transport = FakeTransport(manifest, artifact)
    transport.metadata_error = UpdateEndpointHttpError(404)
    config = UpdateConfig(
        tmp_path / "updates",
        keyring,
        {"beta": DEFAULT_BETA_MANIFEST_URL},
        current_version="0.1.0-beta.1",
        platform_name="windows",
        architecture="x86_64",
    )
    manager = UpdateManager(
        config,
        database_path=database,
        transport=transport,
        installer=FakeInstaller(),
        health_probe=FakeHealth(True),
    )

    status = manager.check()

    assert status["phase"] == "unpublished"
    assert status["last_error"] is None
    assert status["last_checked_at"] is not None
    assert status["configured"] is True
    assert status["available_channels"] == ["beta"]
    assert manager.scheduled_check()["phase"] == "unpublished"


def test_legacy_canonical_beta_404_state_migrates_without_another_network_check(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(
        tmp_path,
        version="0.2.0-beta.1",
        channel="beta",
        minimum="0.1.0-beta.1",
    )
    database = tmp_path / "core.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY)")
    config = UpdateConfig(
        tmp_path / "updates",
        keyring,
        {"beta": DEFAULT_BETA_MANIFEST_URL},
        current_version="0.1.0-beta.1",
        platform_name="windows",
        architecture="x86_64",
    )
    config.data_dir.mkdir(parents=True)
    (config.data_dir / "preferences.json").write_text(
        json.dumps({"enabled": True, "channel": "beta", "deferred_version": None}),
        encoding="utf-8",
    )
    (config.data_dir / "state.json").write_text(
        json.dumps(
            {
                "phase": "error",
                "current_version": "0.1.0-beta.1",
                "last_checked_at": "2026-07-23T07:14:47+00:00",
                "last_error": "Update endpoint returned HTTP 404",
            }
        ),
        encoding="utf-8",
    )
    transport = FakeTransport(manifest, artifact)

    manager = UpdateManager(
        config,
        database_path=database,
        transport=transport,
        installer=FakeInstaller(),
        health_probe=FakeHealth(True),
    )

    assert manager.public_status()["phase"] == "unpublished"
    assert manager.public_status()["last_error"] is None


def test_custom_channel_404_remains_a_visible_error(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, transport, _ = _manager(tmp_path, manifest, artifact, keyring)
    transport.metadata_error = UpdateEndpointHttpError(404)

    status = manager.check()

    assert status["phase"] == "error"
    assert status["last_error"] == "Update endpoint returned HTTP 404"


def test_valid_n_minus_one_update_download_backup_and_handoff(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, installer = _manager(tmp_path, manifest, artifact, keyring)

    assert manager.check()["phase"] == "available"
    assert manager.download()["phase"] == "ready"
    status = manager.install()

    assert status["phase"] == "restart_required"
    assert installer.handed_off
    backup = Path(manager.state.backup_path or "")
    assert backup.is_file()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_equal_version_is_truthfully_current_and_can_be_disabled(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path, version="0.1.0")
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert manager.check()["phase"] == "current"
    assert manager.configure(enabled=False, channel="stable")["phase"] == "disabled"
    assert manager.check()["phase"] == "disabled"


def test_same_version_acceptance_reopens_verified_candidate_without_network(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path, version="0.1.0")
    manager, transport, installer = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        installer=FakeInstaller(expected_target_version="0.1.0"),
    )
    assert manager.check()["phase"] == "current"
    transport.metadata_error = UpdateError("network must not be used after check")
    status = manager.accept_exact_candidate()
    assert status["phase"] == "available"
    assert status["offered_version"] == "0.1.0"
    assert manager.download()["phase"] == "ready"
    install = manager.install()
    assert install["phase"] == "restart_required"
    assert installer.handed_off
    backup = Path(manager.state.backup_path or "")
    assert backup.is_file()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("SELECT id FROM records").fetchone() == (1,)
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)


def test_same_version_acceptance_failed_health_rolls_back_and_preserves_vault(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path, version="0.1.0")
    installer = FakeInstaller(expected_target_version="0.1.0")
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring, installer=installer)
    manager.check()
    manager.accept_exact_candidate()
    manager.download()
    manager.install()
    _publish_helper_terminal(manager, "rolled_back")
    vault = tmp_path / "core.sqlite3"
    original_digest = hashlib.sha256(vault.read_bytes()).hexdigest()

    recovered, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.1.0",
        installer=installer,
        health=FakeHealth(False),
    )
    status = recovered.recover_after_restart()
    assert status["phase"] == "rolled_back"
    assert "previous app and vault were restored" in (status["last_error"] or "").casefold()
    assert hashlib.sha256(vault.read_bytes()).hexdigest() == original_digest
    assert recovered.recover_after_restart()["phase"] == "rolled_back"


def test_same_version_acceptance_success_marks_installed(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path, version="0.1.0")
    installer = FakeInstaller(expected_target_version="0.1.0")
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring, installer=installer)
    manager.check()
    manager.accept_exact_candidate()
    manager.download()
    manager.install()
    _publish_helper_terminal(manager, "installed")
    recovered, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.1.0",
        installer=installer,
        health=FakeHealth(True),
    )
    assert recovered.public_status()["phase"] == "installed"
    assert recovered.recover_after_restart()["phase"] == "installed"
    assert recovered.configure(enabled=True, channel="stable")["phase"] == "installed"


@pytest.mark.parametrize("action", ["defer", "configure"])
def test_terminal_evidence_is_retired_before_deferred_or_disabled_state(
    tmp_path: Path, action: str
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    manager.install()
    _publish_helper_terminal(manager, "installed")
    manager.state = manager._load_state()
    manager.state.current_version = manager.config.current_version
    operation_id = manager.state.operation_id or ""
    staging_dir = manager.state_path.parent / "staging" / operation_id
    transaction_dir = manager.state_path.parent / "transactions" / operation_id
    assert staging_dir.is_dir()
    assert transaction_dir.is_dir()

    if action == "defer":
        status = manager.defer()
        assert status["phase"] == "deferred"
        assert status["deferred_version"] == "0.2.0"
    else:
        status = manager.configure(enabled=False, channel="stable")
        assert status["phase"] == "disabled"
        assert status["enabled"] is False

    persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
    assert persisted["phase"] == status["phase"]
    assert persisted["completed_handoff_identity"] is None
    assert not staging_dir.exists()
    assert not transaction_dir.exists()

    restarted, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert restarted._state_write_allowed is True
    assert restarted.public_status()["phase"] == status["phase"]


@pytest.mark.parametrize("action", ["defer", "configure"])
def test_terminal_evidence_cleanup_failure_does_not_persist_nonterminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    manager.install()
    _publish_helper_terminal(manager, "installed")
    manager.state = manager._load_state()
    manager.state.current_version = manager.config.current_version
    state_before = manager.state_path.read_bytes()
    preferences_before = (
        manager.preferences_path.read_bytes() if manager.preferences_path.exists() else None
    )
    monkeypatch.setattr(manager, "_clear_completed_recovery_evidence", lambda: False)

    with pytest.raises(UpdateError, match="cleanup could not be completed safely"):
        if action == "defer":
            manager.defer()
        else:
            manager.configure(enabled=False, channel="stable")

    assert manager.state_path.read_bytes() == state_before
    assert (
        manager.preferences_path.read_bytes() if manager.preferences_path.exists() else None
    ) == preferences_before
    persisted = json.loads(state_before)
    assert persisted["phase"] == "installed"
    assert persisted["transaction_path"] is None
    assert persisted["completed_handoff_identity"] is not None


def test_accept_exact_candidate_rejects_newer_available_offer(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path, version="0.2.0")
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert manager.check()["phase"] == "available"
    with pytest.raises(UpdateError, match="same-version candidate"):
        manager.accept_exact_candidate()


def test_channel_change_discards_an_old_verified_offer(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert manager.check()["phase"] == "available"
    status = manager.configure(enabled=True, channel="beta")
    assert status["phase"] == "idle"
    assert status["offered_version"] is None
    with pytest.raises(UpdateError, match="verified available"):
        manager.download()


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        ("tamper", "signature"),
        ("revoked", "revoked"),
        ("unknown", "uniquely trusted"),
        ("wrong_channel", "requested channel"),
        ("wrong_platform", "different platform"),
        ("wrong_architecture", "different architecture"),
        ("downgrade", "downgrade"),
    ],
)
def test_manifest_trust_and_target_failures_close_the_transaction(
    tmp_path: Path, mutation: str, error: str
) -> None:
    version = (
        "0.0.9"
        if mutation == "downgrade"
        else "0.2.0-beta.1"
        if mutation == "wrong_channel"
        else "0.2.0"
    )
    channel = "beta" if mutation == "wrong_channel" else "stable"
    platform_name = "linux" if mutation == "wrong_platform" else "windows"
    architecture = "arm64" if mutation == "wrong_architecture" else "x86_64"
    manifest, artifact, keyring = _fixture(
        tmp_path,
        version=version,
        channel=channel,
        platform_name=platform_name,
        architecture=architecture,
        minimum="0.0.1" if mutation == "downgrade" else "0.1.0",
    )
    if mutation == "tamper":
        manifest["mandatory"] = True
    if mutation in {"revoked", "unknown"}:
        value = json.loads(keyring.read_text(encoding="utf-8"))
        if mutation == "revoked":
            value["keys"][0]["status"] = "revoked"
        else:
            value["keys"][0]["key_id"] = "another-key"
        keyring.write_text(json.dumps(value), encoding="utf-8")
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    status = manager.check()
    assert status["phase"] == "error"
    assert error in status["last_error"]


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("partial", "checksum"),
        ("oversized", "checksum"),
        ("checksum", "checksum"),
        ("http", "HTTP 503"),
        ("redirect", "redirect"),
        ("cancel", "cancelled"),
    ],
)
def test_download_failures_remove_partial_artifacts(
    tmp_path: Path, case: str, message: str
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, transport, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert manager.check()["phase"] == "available"
    if case == "partial":
        transport.reported_bytes = len(artifact) - 1
    elif case == "oversized":
        transport.reported_bytes = len(artifact) + 1
    elif case == "checksum":
        transport.reported_digest = "0" * 64
    elif case == "http":
        transport.download_error = UpdateError("Update endpoint returned HTTP 503")
    elif case == "redirect":
        transport.download_error = UpdateError("Update endpoint redirect was refused")
    else:
        transport.cancel_during_download = True
    status = manager.download()
    assert status["phase"] in {"error", "cancelled"}
    assert message.casefold() in status["last_error"].casefold()
    assert not (manager._operation_directory() / "artifact.zip").exists()


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("preflight", "Insufficient disk"),
        ("locked", "locked"),
        ("crash", "crashed"),
    ],
)
def test_install_preflight_lock_and_crash_are_truthful(
    tmp_path: Path, failure: str, message: str
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    installer = FakeInstaller(failure=failure)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring, installer=installer)
    manager.check()
    manager.download()
    status = manager.install()
    assert status["phase"] == "error"
    assert message in status["last_error"]


def test_install_rejects_artifact_swapped_after_preflight(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)

    class SwapAfterPreflight(FakeInstaller):
        def preflight(self, path: Path, required_bytes: int) -> None:
            super().preflight(path, required_bytes)
            path.write_bytes(b"attacker-controlled archive")

    installer = SwapAfterPreflight()
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring, installer=installer)
    manager.check()
    manager.download()

    status = manager.install()

    assert status["phase"] == "error"
    assert "artifact" in (status["last_error"] or "").casefold()
    assert manager.state.downloaded_path is None
    assert installer.handed_off is False


def test_install_rejects_persisted_manifest_replacement(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    manifest_path = manager._operation_directory() / "manifest.json"
    value = json.loads(manifest_path.read_text(encoding="utf-8"))
    value["release_notes_url"] = "https://updates.example.test/releases/forged"
    manifest_path.write_text(json.dumps(value), encoding="utf-8")

    status = manager.install()

    assert status["phase"] == "error"
    assert "metadata" in (status["last_error"] or "").casefold()


def test_manual_platform_fails_closed_after_verified_download(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    installer = FakeInstaller(supported=False)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring, installer=installer)
    manager.check()
    status = manager.download()
    assert status["phase"] == "manual_required"
    assert "Manual installation" in status["last_error"]
    with pytest.raises(UpdateError, match="ready"):
        manager.install()


def test_manual_platform_can_save_only_a_freshly_reverified_package(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(
        tmp_path, manifest, artifact, keyring, installer=FakeInstaller(supported=False)
    )
    manager.check()
    status = manager.download()
    assert status["verified_artifact_available"] is True
    assert "downloaded_path" not in status

    prepared = manager.prepare_artifact_export()
    try:
        assert prepared.path.read_bytes() == artifact
        assert prepared.filename == "all-the-context-0.2.0-windows-x86_64.zip"
    finally:
        prepared.path.unlink(missing_ok=True)

    Path(manager.state.downloaded_path or "missing").write_bytes(b"tampered")
    with pytest.raises(UpdateError, match="checksum"):
        manager.prepare_artifact_export()


def test_failed_health_check_rolls_back_and_recovery_is_idempotent(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    installer = FakeInstaller()
    old, _, _ = _manager(tmp_path, manifest, artifact, keyring, installer=installer)
    old.check()
    old.download()
    old.install()
    _publish_helper_terminal(old, "rolled_back")

    recovered, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.2.0",
        installer=installer,
        health=FakeHealth(False),
    )
    assert recovered.recover_after_restart()["phase"] == "rolled_back"
    assert recovered.recover_after_restart()["phase"] == "rolled_back"


def test_successful_restart_health_check_completes_and_cleans_staging(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    manager.install()
    _publish_helper_terminal(manager, "installed")
    recovered, _, _ = _manager(
        tmp_path, manifest, artifact, keyring, current_version="0.2.0", health=FakeHealth(True)
    )
    assert recovered.public_status()["phase"] == "installed"
    assert recovered.recover_after_restart()["phase"] == "installed"


def test_valid_terminal_publication_is_cleaned_before_new_operation(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    manager.install()
    _publish_helper_terminal(manager, "installed")
    operation_id = manager.state.operation_id or ""
    transaction_dir = manager.state_path.parent / "transactions" / operation_id
    staging_dir = manager.state_path.parent / "staging" / operation_id
    assert transaction_dir.is_dir()
    assert (staging_dir / "artifact.zip").is_file()
    assert (staging_dir / "manifest.json").is_file()

    recovered, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.2.0",
    )

    assert recovered.public_status()["phase"] == "installed"
    assert recovered.state.completed_handoff_identity is None
    assert not transaction_dir.exists()
    assert not staging_dir.exists()
    assert (tmp_path / "core.sqlite3").is_file()
    assert Path(recovered.state.backup_path or "missing").is_file()


def test_successful_terminal_recovery_removes_staging_and_transaction_evidence(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    manager.install()
    _publish_helper_terminal(manager, "installed", clear_transaction=False)
    operation_id = manager.state.operation_id or ""
    staging_dir = manager.state_path.parent / "staging" / operation_id
    transaction_dir = manager.state_path.parent / "transactions" / operation_id
    backup = Path(manager.state.backup_path or "missing")
    assert (staging_dir / "artifact.zip").is_file()
    assert (staging_dir / "manifest.json").is_file()
    assert transaction_dir.is_dir()

    recovered, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.2.0",
    )

    assert recovered.recover_after_restart()["phase"] == "installed"
    assert recovered.state.completed_handoff_identity is None
    assert not staging_dir.exists()
    assert not transaction_dir.exists()
    assert (tmp_path / "core.sqlite3").is_file()
    assert backup.is_file()
    assert (tmp_path / "all-the-context-0.2.0-windows-x86_64.zip").is_file()


def test_pointerless_recovery_rejects_recomputed_identity_forgery(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"
    _publish_helper_terminal(manager, "installed")

    persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
    operation = persisted["operation_id"]
    journal_path = manager.state_path.parent / "transactions" / operation / "journal.json"
    journal = UpdateJournal.load(journal_path, validate_storage=False)
    journal.parent_pid += 1
    forged_identity = journal_handoff_identity(journal)
    journal.save(journal_path)
    persisted["completed_handoff_identity"] = forged_identity
    manager.state_path.write_text(json.dumps(persisted), encoding="utf-8")

    recovered, _, _ = _manager(tmp_path, manifest, artifact, keyring, current_version="0.2.0")

    assert recovered.public_status()["phase"] == "error"
    assert recovered.public_status()["last_error"] == (
        updater_module.RECOVERY_EVIDENCE_INCOMPLETE_ERROR
    )
    assert recovered._state_write_allowed is False
    assert journal_path.is_file()
    assert journal_path.parent.is_dir()


@pytest.mark.parametrize("action", ["clear_error", "configure", "defer", "check"])
@pytest.mark.parametrize(
    "operation_id",
    [None, "b" * 23],
    ids=["missing-operation", "corrupt-operation"],
)
def test_completed_identity_without_operation_blocks_lifecycle_mutations(
    tmp_path: Path, action: str, operation_id: str | None
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    tombstone, _ = manager._ensure_retirement_tombstone()
    persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
    persisted["operation_id"] = operation_id
    state_bytes = json.dumps(persisted).encode("utf-8")
    manager.state_path.write_bytes(state_bytes)

    broken, transport, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert broken._state_write_allowed is False
    with pytest.raises(UpdateError):
        if action == "clear_error":
            broken.clear_error()
        elif action == "configure":
            broken.configure(enabled=True, channel="stable")
        elif action == "defer":
            broken.defer()
        else:
            broken.check()
    assert transport.metadata_calls == 0
    assert broken.state_path.read_bytes() == state_bytes
    assert tombstone.is_file()
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{operation}"]


@pytest.mark.parametrize(
    "operation_id",
    [None, "b" * 23],
    ids=["missing-operation", "corrupt-operation"],
)
def test_completed_identity_without_operation_blocks_pruning_and_preserves_authority(
    tmp_path: Path, operation_id: str | None
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    tombstone, _ = manager._ensure_retirement_tombstone()
    shutil.rmtree(manager.state_path.parent / "transactions" / operation)
    persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
    persisted["operation_id"] = operation_id
    manager.state_path.write_text(json.dumps(persisted), encoding="utf-8")

    broken, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert broken._state_write_allowed is False
    assert broken._prune_retirement_tombstones() is False
    assert tombstone.is_file()
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{operation}"]


def test_completed_identity_without_operation_is_deterministically_blocked_on_restart(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    tombstone, _ = manager._ensure_retirement_tombstone()
    shutil.rmtree(manager.state_path.parent / "transactions" / operation)
    persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
    persisted["operation_id"] = None
    manager.state_path.write_text(json.dumps(persisted), encoding="utf-8")
    state_bytes = manager.state_path.read_bytes()

    first, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    second, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert first._state_write_allowed is False
    assert second._state_write_allowed is False
    assert first.state_path.read_bytes() == state_bytes
    assert second.state_path.read_bytes() == state_bytes
    assert tombstone.is_file()
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{operation}"]


def test_completed_identity_with_forged_operation_preserves_bound_evidence(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    tombstone, _ = manager._ensure_retirement_tombstone()
    persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
    persisted["operation_id"] = "b" * 24
    state_bytes = json.dumps(persisted).encode("utf-8")
    manager.state_path.write_bytes(state_bytes)

    broken, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert broken._state_write_allowed is False
    assert broken.state_path.read_bytes() == state_bytes
    assert tombstone.is_file()
    assert (manager.state_path.parent / "transactions" / operation).is_dir()
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{operation}"]


def test_completed_cleanup_retires_authority_after_tree_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"
    _publish_helper_terminal(manager, "installed")
    persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
    operation = persisted["operation_id"]
    operation_dir = manager.state_path.parent / "staging" / operation
    transaction_dir = manager.state_path.parent / "transactions" / operation
    manager.state.phase = UpdatePhase.INSTALLED
    manager.state.transaction_path = None
    manager.state.handoff_identity = None
    manager.state.pending_handoff_identity = None
    manager.state.completed_handoff_identity = persisted["completed_handoff_identity"]

    events: list[str] = []
    removed_paths: list[Path] = []
    original_remove = updater_module._remove_owned_tree
    original_retire = updater_module.retire_recovery_authority

    def remove_tree(path: Path, *, expected: object) -> bool:
        events.append("tree")
        removed_paths.append(path)
        return original_remove(path, expected=expected)

    def retire_authority(value: str) -> bool:
        events.append("retire")
        assert not transaction_dir.exists()
        return original_retire(value)

    monkeypatch.setattr(updater_module, "_remove_owned_tree", remove_tree)
    monkeypatch.setattr(updater_module, "retire_recovery_authority", retire_authority)

    assert manager._clear_completed_recovery_evidence() is True
    assert events == ["tree", "tree", "retire"]
    assert removed_paths == [operation_dir, transaction_dir]
    assert not transaction_dir.exists()


def test_intact_completed_binding_retires_after_transaction_tree_removal(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    tombstone, _ = manager._ensure_retirement_tombstone()
    transaction_dir = manager.state_path.parent / "transactions" / operation
    shutil.rmtree(transaction_dir)

    assert manager._clear_completed_recovery_evidence() is True
    assert manager.state.completed_handoff_identity is None
    assert not tombstone.exists()
    assert f"transaction:{operation}" not in _RECOVERY_AUTHORITY_VALUES


@pytest.mark.parametrize("outcome", ["installed", "rolled_back"])
def test_retirement_tombstone_retries_after_credential_deletion_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring, outcome=outcome)
    staging_dir = manager.state_path.parent / "staging" / operation
    transaction_dir = manager.state_path.parent / "transactions" / operation
    original_retire = updater_module.retire_recovery_authority
    attempts: list[str] = []

    def fail_retirement(value: str) -> bool:
        attempts.append(value)
        return False

    monkeypatch.setattr(updater_module, "retire_recovery_authority", fail_retirement)
    assert manager._clear_completed_recovery_evidence() is False
    assert attempts == [operation]
    assert manager._state_write_allowed is True
    assert manager.state.completed_handoff_identity is not None
    assert not staging_dir.exists()
    assert not transaction_dir.exists()
    tombstones = list((manager.state_path.parent / "retirements").iterdir())
    assert len(tombstones) == 1

    restarted, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert restarted._state_write_allowed is True
    assert restarted.state.completed_handoff_identity is not None
    assert restarted.state.phase.value == outcome

    monkeypatch.setattr(updater_module, "retire_recovery_authority", original_retire)
    assert restarted._clear_completed_recovery_evidence() is True
    assert restarted.state.completed_handoff_identity is None
    assert not list((restarted.state_path.parent / "retirements").iterdir())


def test_clear_error_preserves_failed_retirement_evidence_and_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(
        tmp_path, manifest, artifact, keyring, outcome="rolled_back"
    )
    original_retire = updater_module.retire_recovery_authority

    monkeypatch.setattr(updater_module, "retire_recovery_authority", lambda _value: False)

    with pytest.raises(UpdateError, match="staging cleanup"):
        manager.clear_error()

    retirement_records = list((manager.state_path.parent / "retirements").iterdir())
    assert len(retirement_records) == 1
    tombstone = retirement_records[0]
    assert manager._load_retirement_tombstone(tombstone) is not None
    assert manager.state.completed_handoff_identity is not None
    assert manager.state.phase is UpdatePhase.ROLLED_BACK
    assert manager.state.last_error == "Updater staging cleanup could not be completed safely"
    failed_error = manager.state.last_error
    failed_identity = manager.state.completed_handoff_identity
    failed_tombstone = tombstone.read_bytes()
    failed_authority = _RECOVERY_AUTHORITY_VALUES[f"transaction:{operation}"]

    with pytest.raises(UpdateError, match="staging cleanup"):
        manager.clear_error()

    assert manager.state.last_error == failed_error
    assert manager.state.completed_handoff_identity == failed_identity
    assert tombstone.read_bytes() == failed_tombstone
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{operation}"] == failed_authority

    monkeypatch.setattr(updater_module, "retire_recovery_authority", original_retire)
    assert manager.clear_error()["phase"] == "rolled_back"
    assert manager.state.last_error is None
    assert manager.state.completed_handoff_identity is None
    assert not tombstone.exists()
    assert f"transaction:{operation}" not in _RECOVERY_AUTHORITY_VALUES

    assert manager.clear_error()["phase"] == "rolled_back"


def test_clear_error_rejects_missing_retirement_tombstone_after_failed_retirement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    monkeypatch.setattr(updater_module, "retire_recovery_authority", lambda _value: False)

    with pytest.raises(UpdateError, match="staging cleanup"):
        manager.clear_error()

    tombstone = next((manager.state_path.parent / "retirements").iterdir())
    tombstone.unlink()
    with pytest.raises(UpdateError, match="staging cleanup"):
        manager.clear_error()

    assert manager._state_write_allowed is False
    assert manager.state.phase is UpdatePhase.ERROR
    assert manager.state.last_error == updater_module.RECOVERY_EVIDENCE_INCOMPLETE_ERROR
    assert manager.state.completed_handoff_identity is not None
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{operation}"]


def test_clear_error_rejects_tampered_retirement_tombstone(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    tombstone, payload = manager._ensure_retirement_tombstone()
    payload["terminal_authority_mac"] = "0" * 64
    tombstone.write_text(manager._render_json(payload), encoding="utf-8")
    tampered = tombstone.read_bytes()

    with pytest.raises(UpdateError, match="staging cleanup"):
        manager.clear_error()

    assert manager._state_write_allowed is False
    assert manager.state.phase is UpdatePhase.ERROR
    assert manager.state.last_error == updater_module.RECOVERY_EVIDENCE_INCOMPLETE_ERROR
    assert manager.state.completed_handoff_identity is not None
    assert tombstone.read_bytes() == tampered
    assert (manager.state_path.parent / "transactions" / operation).is_dir()
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{operation}"]


def test_clear_error_clears_ordinary_non_recovery_error(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, transport, _ = _manager(tmp_path, manifest, artifact, keyring)
    transport.metadata_error = UpdateError("Update endpoint returned HTTP 503")

    assert manager.check()["phase"] == "error"
    status = manager.clear_error()

    assert status["phase"] == "idle"
    assert status["last_error"] is None
    assert manager.state.completed_handoff_identity is None


def test_retirement_tombstone_handles_credential_deleted_before_cleanup_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    original_retire = updater_module.retire_recovery_authority

    def delete_then_report_failure(value: str) -> bool:
        assert original_retire(value) is True
        return False

    monkeypatch.setattr(updater_module, "retire_recovery_authority", delete_then_report_failure)
    assert manager._clear_completed_recovery_evidence() is False
    assert manager.state.completed_handoff_identity is not None
    assert not (manager.state_path.parent / "staging" / operation).exists()
    assert not (manager.state_path.parent / "transactions" / operation).exists()

    restarted, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert restarted._state_write_allowed is True
    assert restarted.state.completed_handoff_identity is None
    assert not list((restarted.state_path.parent / "retirements").iterdir())


def test_retirement_tombstone_retries_a_real_store_delete_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _RecoveryAuthorityStore(fail_delete=True)
    monkeypatch.setattr(update_helper_module, "_recovery_authority_store", lambda: store)
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)

    assert manager._clear_completed_recovery_evidence() is False
    assert _RECOVERY_AUTHORITY_VALUES[f"transaction:{operation}"]
    assert manager.state.completed_handoff_identity is not None

    store.fail_delete = False
    restarted, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert restarted._state_write_allowed is True
    assert restarted.state.completed_handoff_identity is None
    assert f"transaction:{operation}" not in _RECOVERY_AUTHORITY_VALUES


@pytest.mark.parametrize("tamper", ["tombstone", "journal"])
def test_retirement_tampering_preserves_evidence_and_blocks_writes(
    tmp_path: Path,
    tamper: str,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    tombstone, payload = manager._ensure_retirement_tombstone()
    if tamper == "tombstone":
        payload["terminal_authority_mac"] = "0" * 64
        tombstone.write_text(manager._render_json(payload), encoding="utf-8")
    else:
        journal_path = manager.state_path.parent / "transactions" / operation / "journal.json"
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["parent_pid"] += 1
        journal_path.write_text(manager._render_json(journal), encoding="utf-8")

    assert manager._clear_completed_recovery_evidence() is False
    assert manager._state_write_allowed is False
    assert manager.state.last_error == updater_module.RECOVERY_EVIDENCE_INCOMPLETE_ERROR
    assert (manager.state_path.parent / "staging" / operation).exists()
    assert (manager.state_path.parent / "transactions" / operation).exists()


def test_retirement_tombstone_orphan_is_reaped_by_startup_pruning(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _ = _completed_manager(tmp_path, manifest, artifact, keyring)
    tombstone, _ = manager._ensure_retirement_tombstone()
    manager.state.completed_handoff_identity = None
    manager._save()
    assert tombstone.is_file()

    restarted, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert restarted._state_write_allowed is True
    assert not tombstone.exists()


def test_frozen_windows_startup_accepts_retirement_tombstone_after_credential_retired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, operation = _completed_manager(tmp_path, manifest, artifact, keyring)
    tombstone, _ = manager._ensure_retirement_tombstone()
    _RECOVERY_AUTHORITY_VALUES.clear()
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(update_helper_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(update_helper_module.sys, "frozen", True, raising=False)

    assert tombstone.is_file()
    assert (manager.state_path.parent / "transactions" / operation).is_dir()
    assert update_helper_module.ensure_recovery_before_core() is True


@pytest.mark.parametrize("outcome", ["installed", "rolled_back"])
def test_unconfirmed_installer_outcome_cannot_complete_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str | None,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"
    recovered, _, installer = _manager(tmp_path, manifest, artifact, keyring)
    journal = Path(recovered.state.transaction_path or "")
    monkeypatch.setattr(installer, "recovery_outcome", lambda _state: outcome)
    monkeypatch.setattr(recovered, "_clean_operation", lambda: False)

    status = recovered.recover_after_restart()

    assert status["phase"] == "error"
    assert status["last_error"] == updater_module.RECOVERY_EVIDENCE_INCOMPLETE_ERROR
    assert recovered.state.transaction_path == str(journal)
    persisted = json.loads(recovered.state_path.read_text(encoding="utf-8"))
    assert persisted["phase"] == "restart_required"
    assert persisted["transaction_path"] == str(journal)
    assert journal.is_file()


def test_recovery_cleanup_failure_does_not_overwrite_helper_terminal_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"
    _publish_helper_terminal(manager, "installed", clear_transaction=False)

    recovered, _, _ = _manager(tmp_path, manifest, artifact, keyring, current_version="0.2.0")
    journal = Path(recovered.state.transaction_path or "")
    monkeypatch.setattr(recovered, "_clean_operation", lambda: False)

    status = recovered.recover_after_restart()

    assert status["phase"] == "restart_required"
    assert status["last_error"] == (
        "Update recovery completed but cleanup could not be completed safely; retry recovery"
    )
    persisted = json.loads(recovered.state_path.read_text(encoding="utf-8"))
    assert persisted["phase"] == "installed"
    assert persisted["transaction_path"] == str(journal)
    assert journal.is_file()


def test_interrupted_download_recovers_as_cancelled(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.state.phase = UpdatePhase.DOWNLOADING
    manager.state.operation_id = "1" * 24
    operation = manager._operation_directory()
    operation.mkdir(parents=True)
    (operation / "artifact.zip").write_bytes(b"partial")
    manager._save()
    recovered, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert recovered.public_status()["phase"] == "cancelled"
    assert not operation.exists()


def test_concurrent_check_and_install_are_rejected(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager._operation_gate.acquire()
    try:
        with pytest.raises(UpdateBusyError):
            manager.check()
    finally:
        manager._operation_gate.release()


@pytest.mark.parametrize("action", ["check", "defer", "clear_error"])
def test_external_windows_handoff_blocks_competing_state_mutations(
    tmp_path: Path, action: str
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"

    with pytest.raises(UpdateBusyError, match="recovery helper"):
        getattr(manager, action)()
    with pytest.raises(UpdateBusyError, match="recovery helper"):
        manager.configure(enabled=True, channel="stable")


@pytest.mark.parametrize("action", ["defer", "clear_error", "recover_after_restart"])
def test_all_state_mutations_share_the_operation_gate(tmp_path: Path, action: str) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager._operation_gate.acquire()
    try:
        with pytest.raises(UpdateBusyError):
            getattr(manager, action)()
    finally:
        manager._operation_gate.release()


def test_cancel_signals_before_waiting_for_serialized_state(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager._operation_gate.acquire()
    completed = threading.Event()

    def cancel() -> None:
        manager.cancel()
        completed.set()

    worker = threading.Thread(target=cancel)
    worker.start()
    assert manager._cancel.wait(timeout=1)
    assert not completed.is_set()
    manager._operation_gate.release()
    worker.join(timeout=1)
    assert completed.is_set()


def test_corrupt_persisted_preferences_and_state_reset_safely(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    updates = tmp_path / "updates"
    updates.mkdir()
    (updates / "preferences.json").write_text('{"enabled": ', encoding="utf-8")
    (updates / "state.json").write_text(
        json.dumps(
            {
                "phase": "restart_required",
                "current_version": "invalid",
                "offered_version": "also-invalid",
            }
        ),
        encoding="utf-8",
    )
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    status = manager.public_status()
    assert status["channel"] == "stable"
    assert status["enabled"] is True
    assert status["phase"] == "error"
    assert "corrupt" in status["last_error"].casefold()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b'{"value":"' + b"a" * 32 + b'"}', {"value": "a" * 32}),
        (b"\xff", "could not be decoded safely"),
        (b"[]", "must be a JSON object"),
        (b'{"value":' + b"9" * 5000 + b"}", "could not be decoded safely"),
        (
            b'{"value":' + b"[" * 30000 + b"0" + b"]" * 30000 + b"}",
            "could not be decoded safely",
        ),
    ],
    ids=["valid", "invalid-utf8", "non-object", "huge-integer", "deep-nesting"],
)
def test_update_metadata_boundary_contains_real_parser_and_root_failures(
    tmp_path: Path, raw: bytes, expected: object
) -> None:
    path = tmp_path / "metadata.json"
    path.write_bytes(raw)

    if isinstance(expected, dict):
        result = updater_module._read_bounded_json(path, MAX_STATE_BYTES, label="Test metadata")
        assert result.value == expected
    else:
        with pytest.raises(UpdateError, match=expected):
            updater_module._read_bounded_json(path, MAX_STATE_BYTES, label="Test metadata")


def test_update_metadata_boundary_reads_exact_limit_plus_one_and_accepts_multibyte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maximum = 128
    prefix = b'{"value":"'
    suffix = b'"}'
    value_bytes = maximum - len(prefix) - len(suffix)
    assert value_bytes % len("é".encode()) == 0
    raw = prefix + "é".encode() * (value_bytes // 2) + suffix
    path = tmp_path / "metadata.json"
    path.write_bytes(raw)
    read_sizes: list[int] = []
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

        def fileno(self) -> int:
            return self._handle.fileno()  # type: ignore[attr-defined,no-any-return]

    def tracking_open(target: Path, *args: object, **kwargs: object) -> object:
        handle = original_open(target, *args, **kwargs)
        return TrackingReader(handle) if target == path else handle

    monkeypatch.setattr(Path, "open", tracking_open)
    result = updater_module._read_bounded_json(path, maximum, label="Test metadata")

    assert len(raw) == maximum
    assert result.value == {"value": "é" * (value_bytes // 2)}
    assert read_sizes == [maximum + 1]


def test_update_metadata_boundary_rejects_growth_after_initial_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    maximum = 128
    path = tmp_path / "metadata.json"
    path.write_text("{}", encoding="utf-8")
    original_open = Path.open
    grew = False

    def grow_before_open(target: Path, *args: object, **kwargs: object) -> object:
        nonlocal grew
        if target == path and not grew:
            grew = True
            with original_open(path, "wb") as stream:
                stream.write(b"{}" + b"x" * maximum)
        return original_open(target, *args, **kwargs)

    monkeypatch.setattr(Path, "open", grow_before_open)
    with pytest.raises(UpdateError, match=r"(changed while it was read|exceeds the size limit)"):
        updater_module._read_bounded_json(path, maximum, label="Test metadata")


@pytest.mark.parametrize("control_exception", [SystemExit, KeyboardInterrupt, GeneratorExit])
def test_update_json_decoder_does_not_swallow_process_control_exceptions(
    monkeypatch: pytest.MonkeyPatch, control_exception: type[BaseException]
) -> None:
    def fail(_value: str) -> object:
        raise control_exception("sentinel")

    monkeypatch.setattr(updater_module.json, "loads", fail)
    with pytest.raises(control_exception):
        updater_module._decode_bounded_json(b"{}", MAX_STATE_BYTES, label="Test metadata")


def test_update_json_decoder_does_not_swallow_unexpected_programming_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_value: str) -> object:
        raise RuntimeError("programming failure")

    monkeypatch.setattr(updater_module.json, "loads", fail)
    with pytest.raises(RuntimeError, match="programming failure"):
        updater_module._decode_bounded_json(b"{}", MAX_STATE_BYTES, label="Test metadata")


@pytest.mark.parametrize(
    "raw",
    [
        b'{"value":' + b"9" * 5000 + b"}",
        b'{"value":' + b"[" * 30000 + b"0" + b"]" * 30000 + b"}",
    ],
    ids=["huge-integer", "deep-nesting"],
)
def test_network_manifest_parser_failures_are_bounded_public_errors(
    tmp_path: Path, raw: bytes
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, transport, _ = _manager(tmp_path, manifest, artifact, keyring)
    transport.metadata_bytes = raw

    status = manager.check()

    assert status["phase"] == "error"
    assert "could not be decoded safely" in (status["last_error"] or "")
    assert "9" * 100 not in (status["last_error"] or "")


@pytest.mark.parametrize("size", [0, MAX_ARTIFACT_BYTES + 1, True])
def test_verified_manifest_artifact_size_is_bounded(size: object) -> None:
    with pytest.raises(UpdateError, match="unsupported artifact size"):
        UpdateManager._validate_manifest_artifact_size({"size": size})


def test_download_rejects_nonregular_persisted_manifest_without_transport(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, transport, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert manager.check()["phase"] == "available"
    manifest_path = manager._operation_directory() / "manifest.json"
    manifest_path.unlink()
    manifest_path.mkdir()

    status = manager.download()

    assert status["phase"] == "error"
    assert transport.stream_calls == 0
    assert manifest_path.is_dir()


@pytest.mark.parametrize("kind", ["preferences", "state"])
def test_oversized_persisted_update_metadata_fails_closed_without_raw_content(
    tmp_path: Path, kind: str
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    updates = tmp_path / "updates"
    updates.mkdir()
    marker = "secret-oversized-metadata"
    if kind == "preferences":
        (updates / "preferences.json").write_text(
            json.dumps({"enabled": True, "channel": "stable", "padding": marker * 1000}),
            encoding="utf-8",
        )
    else:
        (updates / "state.json").write_text(
            json.dumps({"phase": "idle", "current_version": "0.1.0", "padding": marker * 10000}),
            encoding="utf-8",
        )

    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    status = manager.public_status()

    assert status["channel"] == "stable"
    assert marker not in json.dumps(status)
    if kind == "state":
        assert status["phase"] == "error"


def test_corrupt_state_preserves_recovery_evidence_and_last_good_file(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    updates = tmp_path / "updates"
    updates.mkdir()
    state_path = updates / "state.json"
    original_state = b'{"phase":"restart_required","recovery_attempts":' + b"9" * 5000 + b"}"
    state_path.write_bytes(original_state)
    evidence = updates / "transactions" / ("a" * 24)
    evidence.mkdir(parents=True)
    evidence_marker = evidence / "journal.json"
    evidence_marker.write_text('{"phase":"prepared"}', encoding="utf-8")

    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert manager.public_status()["phase"] == "error"
    assert state_path.read_bytes() == original_state
    assert evidence_marker.is_file()


def test_partial_active_state_preserves_state_and_recovery_evidence(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    updates = tmp_path / "updates"
    updates.mkdir()
    state_path = updates / "state.json"
    original_state = b'{"phase":"restart_required"}\n'
    state_path.write_bytes(original_state)
    operation_id = "e" * 24
    staging_marker = updates / "staging" / operation_id / "artifact.zip"
    staging_marker.parent.mkdir(parents=True)
    staging_marker.write_bytes(b"recovery staging evidence")
    journal_marker = updates / "transactions" / operation_id / "journal.json"
    journal_marker.parent.mkdir(parents=True)
    journal_marker.write_text('{"phase":"prepared"}', encoding="utf-8")

    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert manager.public_status()["phase"] == "error"
    assert manager._state_write_allowed is False
    assert state_path.read_bytes() == original_state
    assert staging_marker.is_file()
    assert journal_marker.is_file()
    assert "restart_required" not in (manager.public_status()["last_error"] or "")


@pytest.mark.parametrize("phase", ["installing", "restart_required"])
@pytest.mark.parametrize("missing", ["artifact", "backup", "journal"])
def test_active_recovery_missing_physical_evidence_fails_closed(
    tmp_path: Path,
    phase: str,
    missing: str,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    updates = tmp_path / "updates"
    updates.mkdir()
    operation_id = "d" * 24
    operation_dir = updates / "staging" / operation_id
    operation_dir.mkdir(parents=True)
    artifact_path = operation_dir / "artifact.zip"
    artifact_path.write_bytes(artifact)
    backup_path = updates / "backups" / "before.sqlite3"
    backup_path.parent.mkdir(parents=True)
    backup_path.write_bytes(b"verified backup")
    journal_path = updates / "transactions" / operation_id / "journal.json"
    journal_path.parent.mkdir(parents=True)
    journal_path.write_text('{"phase":"committed"}', encoding="utf-8")
    state = {
        "phase": phase,
        "current_version": "0.1.0",
        "offered_version": manifest["version"],
        "mandatory": False,
        "release_notes_url": manifest["release_notes_url"],
        "downloaded_path": str(artifact_path),
        "backup_path": str(backup_path),
        "operation_id": operation_id,
        "transaction_path": str(journal_path),
        "manifest_identity": "a" * 64,
    }
    state_path = updates / "state.json"
    original_state = (json.dumps(state, sort_keys=True) + "\n").encode("utf-8")
    state_path.write_bytes(original_state)
    missing_path = {
        "artifact": artifact_path,
        "backup": backup_path,
        "journal": journal_path,
    }[missing]
    missing_path.unlink()
    orphan = updates / "exports" / "surviving-orphan.zip"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"survive")

    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    status = manager.public_status()
    assert status["phase"] == "error"
    assert status["last_error"] == updater_module.RECOVERY_EVIDENCE_INCOMPLETE_ERROR
    assert manager._state_write_allowed is False
    assert state_path.read_bytes() == original_state
    assert orphan.is_file()
    for candidate in (artifact_path, backup_path, journal_path):
        if candidate != missing_path:
            assert candidate.is_file()


def _invalid_journal_bytes(original: bytes, kind: str) -> bytes:
    if kind == "malformed":
        return b"{not-json"
    if kind == "deep":
        return b'{"padding":' + b"[" * 30_000 + b"0" + b"]" * 30_000 + b"}"
    if kind == "oversized":
        return b'{"padding":"' + b"x" * MAX_JOURNAL_BYTES + b'"}'
    value = json.loads(original)
    if kind == "incomplete":
        return b"{}"
    if kind == "schema":
        value["schema_version"] = 1
    elif kind == "phase":
        value["phase"] = "not-a-real-phase"
    elif kind == "operation":
        value["operation_id"] = "b" * 24
    elif kind == "inconsistent":
        value["target_version"] = "0.3.0"
    else:
        raise AssertionError(kind)
    return json.dumps(value, sort_keys=True).encode("utf-8")


@pytest.mark.parametrize(
    "kind",
    [
        "malformed",
        "incomplete",
        "schema",
        "phase",
        "operation",
        "oversized",
        "deep",
        "inconsistent",
    ],
)
def test_invalid_active_recovery_journal_preserves_authority_across_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"

    state_path = manager.state_path
    journal_path = Path(manager.state.transaction_path or "")
    original_state = state_path.read_bytes()
    journal_path.write_bytes(_invalid_journal_bytes(journal_path.read_bytes(), kind))
    invalid_journal = journal_path.read_bytes()

    recovered, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.2.0",
    )
    status = recovered.public_status()
    assert status["phase"] == "error"
    assert status["last_error"] == updater_module.RECOVERY_EVIDENCE_INCOMPLETE_ERROR
    assert recovered._state_write_allowed is False
    assert state_path.read_bytes() == original_state
    assert journal_path.read_bytes() == invalid_journal
    assert journal_path.parent.is_dir()

    recovery_status = recovered.recover_after_restart()
    assert recovery_status["phase"] == "error"
    assert recovery_status["last_error"] == updater_module.RECOVERY_EVIDENCE_INCOMPLETE_ERROR
    assert state_path.read_bytes() == original_state
    assert journal_path.read_bytes() == invalid_journal
    with pytest.raises(UpdateError, match=r"recovery helper owns|state cannot be changed safely"):
        recovered.clear_error()
    with pytest.raises(UpdateError, match=r"recovery helper owns|state cannot be changed safely"):
        recovered.configure(enabled=True, channel="beta")

    restarted, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.2.0",
    )
    assert restarted._state_write_allowed is False
    assert state_path.read_bytes() == original_state
    assert journal_path.read_bytes() == invalid_journal
    assert journal_path.parent.is_dir()

    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(update_helper_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(update_helper_module.sys, "frozen", True, raising=False)
    assert update_helper_module.ensure_recovery_before_core() is False
    assert state_path.read_bytes() == original_state
    assert journal_path.read_bytes() == invalid_journal


def test_valid_active_recovery_journal_remains_recoverable(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"
    journal_path = Path(manager.state.transaction_path or "")

    journal = UpdateJournal.load(journal_path, validate_storage=False)
    assert journal.operation_id == manager.state.operation_id

    recovered, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.2.0",
        health=FakeHealth(True),
    )
    assert recovered._state_write_allowed is True
    assert recovered.recover_after_restart()["phase"] == "restart_required"
    assert recovered.state.transaction_path == str(journal_path)


@pytest.mark.parametrize("terminal_phase", [HelperPhase.COMMITTED, HelperPhase.ROLLED_BACK])
def test_forged_terminal_phase_during_restart_required_preserves_authority(
    tmp_path: Path,
    terminal_phase: HelperPhase,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"
    state_before = manager.state_path.read_bytes()
    journal_path = Path(manager.state.transaction_path or "")
    journal = UpdateJournal.load(journal_path, validate_storage=False)
    journal.phase = terminal_phase
    journal.save(journal_path)

    recovered, _, _ = _manager(tmp_path, manifest, artifact, keyring, current_version="0.2.0")

    assert recovered.public_status()["phase"] == "error"
    assert recovered.public_status()["last_error"] == (
        updater_module.RECOVERY_EVIDENCE_INCOMPLETE_ERROR
    )
    assert recovered._state_write_allowed is False
    assert recovered.state_path.read_bytes() == state_before
    assert journal_path.is_file()


def test_handoff_failure_after_journal_persistence_keeps_recovery_authority(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    installer = FakeInstaller(failure="after-journal")
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring, installer=installer)
    manager.check()
    manager.download()

    status = manager.install()

    journal_path = Path(manager.state.transaction_path or "")
    assert status["phase"] == "error"
    assert manager.state.transaction_path == str(journal_path)
    persisted = json.loads(manager.state_path.read_text(encoding="utf-8"))
    assert persisted["handoff_identity"] is not None
    assert journal_path.is_file()

    restarted, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.2.0",
        installer=installer,
    )
    assert restarted._state_write_allowed is False
    assert restarted.state.transaction_path == str(journal_path)
    with pytest.raises(UpdateBusyError, match="recovery helper"):
        restarted.clear_error()
    with pytest.raises(UpdateBusyError, match="recovery helper"):
        restarted.configure(enabled=True, channel="beta")
    assert journal_path.is_file()


@pytest.mark.parametrize("failure", ["empty", "incomplete"])
def test_pre_authority_transaction_directories_are_reclaimed_after_handoff_failure(
    tmp_path: Path, failure: str
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    installer = FakeInstaller(failure=failure)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring, installer=installer)
    manager.check()
    manager.download()

    status = manager.install()

    operation_id = manager.state.operation_id or ""
    transaction_dir = manager.state_path.parent / "transactions" / operation_id
    assert status["phase"] == "error"
    assert manager.state.transaction_path is None
    assert transaction_dir.is_dir()

    restarted, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert restarted._state_write_allowed is True
    assert not transaction_dir.exists()
    assert restarted.clear_error()["phase"] == "idle"
    assert restarted.configure(enabled=True, channel="stable")["phase"] == "idle"
    assert restarted.check()["phase"] == "available"


def test_partial_journal_creation_keeps_recovery_authority_after_restart(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    installer = FakeInstaller(failure="partial-journal")
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring, installer=installer)
    manager.check()
    manager.download()

    status = manager.install()

    journal_path = Path(manager.state.transaction_path or "")
    assert status["phase"] == "error"
    assert journal_path.read_bytes() == b'{"phase":'

    restarted, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert restarted._state_write_allowed is False
    assert restarted.state.transaction_path == str(journal_path)
    assert journal_path.is_file()
    with pytest.raises(UpdateError, match=r"recovery helper owns|state cannot be changed safely"):
        restarted.clear_error()
    with pytest.raises(UpdateError, match=r"recovery helper owns|state cannot be changed safely"):
        restarted.configure(enabled=True, channel="beta")


def test_malicious_nonempty_transaction_directory_remains_fail_closed(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    operation_id = manager.state.operation_id or ""
    transaction_dir = manager.state_path.parent / "transactions" / operation_id
    transaction_dir.mkdir(parents=True)
    marker = transaction_dir / "unexpected.bin"
    marker.write_bytes(b"untrusted")

    restarted, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert restarted._state_write_allowed is False
    assert restarted.public_status()["phase"] == "error"
    assert marker.is_file()
    with pytest.raises(UpdateError, match="state cannot be changed safely"):
        restarted.check()
    with pytest.raises(UpdateError, match="state cannot be changed safely"):
        restarted.clear_error()
    with pytest.raises(UpdateError, match="state cannot be changed safely"):
        restarted.configure(enabled=True, channel="stable")


def test_rollback_failure_keeps_authority_across_error_clear_configure_and_restart(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    assert manager.install()["phase"] == "restart_required"
    journal_path = Path(manager.state.transaction_path or "")
    journal = UpdateJournal.load(journal_path, validate_storage=False)
    journal.phase = HelperPhase.ROLLING_BACK
    journal.last_error_code = "rollback_retry_required"
    journal.save(journal_path)
    manager.state.phase = UpdatePhase.ERROR
    manager.state.last_error = (
        "The new version did not become healthy and automatic rollback failed"
    )
    manager._save()
    state_before_restart = manager.state_path.read_bytes()

    with pytest.raises(UpdateBusyError, match="recovery helper"):
        manager.clear_error()
    with pytest.raises(UpdateBusyError, match="recovery helper"):
        manager.configure(enabled=True, channel="beta")

    restarted, _, _ = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.2.0",
    )
    assert restarted._state_write_allowed is False
    assert restarted.state.phase is UpdatePhase.ERROR
    assert restarted.state.transaction_path == str(journal_path)
    assert manager.state_path.read_bytes() == state_before_restart
    assert journal_path.is_file()


def test_public_update_errors_do_not_expose_manifest_or_keyring_details(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, transport, _ = _manager(tmp_path, manifest, artifact, keyring)
    keyring_bytes = keyring.read_bytes()
    keyring.unlink()

    status = manager.check()

    rendered = json.dumps(status)
    assert status["phase"] == "error"
    assert "SECRET_VALUE" not in rendered
    assert str(keyring) not in rendered

    keyring.write_bytes(keyring_bytes)
    transport.metadata_bytes = json.dumps({**manifest, "version": "token=SECRET_VALUE"}).encode(
        "utf-8"
    )
    status = manager.check()

    rendered = json.dumps(status)
    assert status["phase"] == "error"
    assert "SECRET_VALUE" not in rendered
    assert str(keyring) not in rendered


def test_public_status_sanitizes_persisted_error_and_release_notes_url(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    updates = tmp_path / "updates"
    updates.mkdir()
    (updates / "state.json").write_text(
        json.dumps(
            {
                "phase": "idle",
                "current_version": "0.1.0",
                "last_error": "token=SECRET_VALUE",
                "release_notes_url": "https://user:password@example.test/private",
            }
        ),
        encoding="utf-8",
    )

    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    rendered = json.dumps(manager.public_status())
    assert "SECRET_VALUE" not in rendered
    assert "password" not in rendered
    assert manager.public_status()["release_notes_url"] is None


def test_prune_directory_refuses_after_deterministic_root_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    orphan = root / "orphan"
    orphan.mkdir()
    (orphan / "marker").write_text("keep", encoding="utf-8")
    replacement = tmp_path / "replacement"
    replacement_marker = replacement / "outside-marker"
    original_iterdir = Path.iterdir
    swapped = False

    def swap_root(path: Path) -> object:
        nonlocal swapped
        if path == root and not swapped:
            swapped = True
            root.rename(tmp_path / "original-staging")
            replacement.mkdir()
            replacement_marker.write_text("outside", encoding="utf-8")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", swap_root)

    assert updater_module.UpdateManager._prune_directory(root, keep=None) is False
    assert replacement_marker.read_text(encoding="utf-8") == "outside"
    assert (tmp_path / "original-staging" / "orphan" / "marker").is_file()


def test_unlink_refuses_after_deterministic_parent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = tmp_path / "owned"
    parent.mkdir()
    target = parent / "temporary.bin"
    target.write_bytes(b"original")
    original_parent = tmp_path / "original-owned"
    replacement_target = target
    original_lstat = Path.lstat
    target_lstat_calls = 0

    def swap_parent(path: Path) -> object:
        nonlocal target_lstat_calls
        if path == target:
            target_lstat_calls += 1
            if target_lstat_calls == 2:
                parent.rename(original_parent)
                parent.mkdir()
                replacement_target.write_bytes(b"replacement")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", swap_parent)

    with pytest.raises(updater_module.UpdateError, match="not a plain file"):
        updater_module._unlink_plain_file(target, "temporary file is not a plain file")

    assert (original_parent / "temporary.bin").read_bytes() == b"original"
    assert replacement_target.read_bytes() == b"replacement"


def test_prune_refuses_after_deterministic_entry_parent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    entry = root / "orphan.bin"
    entry.write_bytes(b"original")
    original_root = tmp_path / "original-staging"
    replacement_entry = entry
    original_lstat = Path.lstat
    entry_lstat_calls = 0

    def swap_parent(path: Path) -> object:
        nonlocal entry_lstat_calls
        if path == entry:
            entry_lstat_calls += 1
            if entry_lstat_calls == 1:
                root.rename(original_root)
                root.mkdir()
                replacement_entry.write_bytes(b"replacement")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", swap_parent)

    assert updater_module.UpdateManager._prune_directory(root, keep=None) is False
    assert (original_root / "orphan.bin").read_bytes() == b"original"
    assert replacement_entry.read_bytes() == b"replacement"


def test_cleanup_rejects_wide_tree_before_deleting_any_entry(tmp_path: Path) -> None:
    root = tmp_path / "wide"
    root.mkdir()
    entries = [
        root / f"entry-{index}.bin" for index in range(updater_module.MAX_CLEANUP_ENTRIES + 1)
    ]
    for entry in entries:
        entry.write_bytes(b"entry")

    assert updater_module._remove_owned_tree(root) is False
    assert root.is_dir()
    assert all(entry.is_file() for entry in entries)


def test_cleanup_removes_tree_at_exact_entry_budget(tmp_path: Path) -> None:
    root = tmp_path / "at-budget"
    root.mkdir()
    entries = [root / f"entry-{index}.bin" for index in range(updater_module.MAX_CLEANUP_ENTRIES)]
    for entry in entries:
        entry.write_bytes(b"entry")

    assert updater_module._remove_owned_tree(root) is True
    assert not root.exists()


def test_prune_budget_is_global_across_nested_entries(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    nested = root / "orphan"
    nested.mkdir()
    entries = [nested / f"entry-{index}.bin" for index in range(updater_module.MAX_CLEANUP_ENTRIES)]
    for entry in entries:
        entry.write_bytes(b"entry")

    assert updater_module.UpdateManager._prune_directory(root, keep=None) is False
    assert nested.is_dir()
    assert all(entry.is_file() for entry in entries)


def test_prune_removes_tree_at_exact_global_nested_budget(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    root.mkdir()
    nested = root / "orphan"
    nested.mkdir()
    entries = [
        nested / f"entry-{index}.bin" for index in range(updater_module.MAX_CLEANUP_ENTRIES - 1)
    ]
    for entry in entries:
        entry.write_bytes(b"entry")

    assert updater_module.UpdateManager._prune_directory(root, keep=None) is True
    assert not nested.exists()
    assert root.is_dir()


def test_cleanup_rejects_tree_deeper_than_global_depth_budget(tmp_path: Path) -> None:
    root = tmp_path / "deep"
    root.mkdir()
    current = root
    for index in range(updater_module.MAX_CLEANUP_DEPTH + 1):
        current = current / f"level-{index}"
        current.mkdir()
    marker = current / "marker.bin"
    marker.write_bytes(b"marker")

    assert updater_module._remove_owned_tree(root) is False
    assert root.is_dir()
    assert marker.is_file()


def test_cleanup_removes_tree_at_exact_depth_budget(tmp_path: Path) -> None:
    root = tmp_path / "depth"
    root.mkdir()
    current = root
    for index in range(updater_module.MAX_CLEANUP_DEPTH - 1):
        current = current / f"level-{index}"
        current.mkdir()
    marker = current / "marker.bin"
    marker.write_bytes(b"marker")

    assert updater_module._remove_owned_tree(root) is True
    assert not root.exists()


def test_cleanup_recursion_error_fails_closed_without_deleting_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "pathological"
    root.mkdir()
    marker = root / "marker.bin"
    marker.write_bytes(b"marker")
    original_iterdir = Path.iterdir

    def fail_iterdir(path: Path) -> object:
        if path == root:
            raise RecursionError("pathological traversal")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    assert updater_module._remove_owned_tree(root) is False
    assert marker.is_file()


def test_substituted_persisted_manifest_never_controls_download_transport(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, transport, _ = _manager(tmp_path, manifest, artifact, keyring)
    assert manager.check()["phase"] == "available"
    manifest_path = manager._operation_directory() / "manifest.json"
    substituted = json.loads(manifest_path.read_text(encoding="utf-8"))
    substituted["url"] = "https://attacker.example.test/releases/v9.9.9/forged.zip"
    substituted["size"] = 2 * 1024 * 1024 * 1024
    substituted["signature"] = "A" * len(substituted["signature"])
    manifest_path.write_text(json.dumps(substituted), encoding="utf-8")

    status = manager.download()

    assert status["phase"] == "error"
    assert transport.stream_calls == 0
    assert transport.stream_urls == []


def test_update_atomic_metadata_failure_preserves_last_good_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state.json"
    original = b'{"phase":"idle"}\n'
    path.write_bytes(original)
    original_replace = Path.replace

    def fail_replace(self: Path, target: Path) -> Path:
        if target == path:
            raise OSError("replacement refused")
        return original_replace(self, target)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(UpdateError, match="could not be saved safely"):
        UpdateManager._atomic_json(path, {"phase": "error"})

    assert path.read_bytes() == original


def test_update_atomic_metadata_rejects_nonregular_parent_without_touching_siblings(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "not-a-directory"
    parent.write_text("unrelated", encoding="utf-8")
    sibling = tmp_path / "sibling.txt"
    sibling.write_text("keep", encoding="utf-8")

    with pytest.raises(UpdateError, match="could not be saved safely"):
        UpdateManager._atomic_json(parent / "state.json", {"phase": "error"})

    assert parent.read_text(encoding="utf-8") == "unrelated"
    assert sibling.read_text(encoding="utf-8") == "keep"


def test_hostile_persisted_preferences_symlink_is_not_followed_when_supported(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    updates = tmp_path / "updates"
    updates.mkdir()
    target = tmp_path / "outside-preferences.json"
    original = b'{"enabled":false,"channel":"beta"}'
    target.write_bytes(original)
    link = updates / "preferences.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this filesystem")

    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert manager.preferences.enabled is True
    assert target.read_bytes() == original
    assert link.is_symlink()


def test_hostile_persisted_state_symlink_stays_last_good_and_blocks_network(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    updates = tmp_path / "updates"
    updates.mkdir()
    target = tmp_path / "outside-state.json"
    original = b'{"phase":"idle"}'
    target.write_bytes(original)
    link = updates / "state.json"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this filesystem")

    manager, transport, _ = _manager(tmp_path, manifest, artifact, keyring)

    assert manager.public_status()["phase"] == "error"
    with pytest.raises(UpdateError, match="state cannot be changed safely"):
        manager.check()
    assert transport.metadata_calls == 0
    assert transport.stream_calls == 0
    assert target.read_bytes() == original
    assert link.is_symlink()


def test_repeated_checks_preserve_orphan_staging_when_global_budget_is_exceeded(
    tmp_path: Path,
) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    staging = manager.config.data_dir / "staging"
    for index in range(40):
        (staging / f"orphan-{index:02d}").mkdir(parents=True)
    manager.check()
    assert len(list(staging.iterdir())) == 41
    assert all((staging / f"orphan-{index:02d}").is_dir() for index in range(40))
    manager.check()
    assert len(list(staging.iterdir())) == 41
    assert (staging / (manager.state.operation_id or "missing")).is_dir()
    assert all((staging / f"orphan-{index:02d}").is_dir() for index in range(40))


def test_restart_preserves_unresolved_recovery_journals_from_pruning(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    operation_id = "c" * 24
    updates = tmp_path / "updates"
    retained = updates / "transactions" / operation_id
    orphan = updates / "transactions" / ("d" * 24)
    retained.mkdir(parents=True)
    orphan.mkdir()
    (retained / "journal.json").write_text('{"phase":"committed"}', encoding="utf-8")
    (orphan / "journal.json").write_text('{"phase":"committed"}', encoding="utf-8")
    (updates / "state.json").write_text(
        json.dumps(
            {
                "phase": "installed",
                "current_version": "0.1.0",
                "operation_id": operation_id,
                "transaction_path": None,
            }
        ),
        encoding="utf-8",
    )

    _manager(tmp_path, manifest, artifact, keyring)

    assert retained.is_dir()
    assert orphan.is_dir()


def test_current_platform_rejects_unknown_and_32_bit_architectures(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr(updater_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(updater_module.platform, "machine", lambda: "i686")
    monkeypatch.setattr(updater_module.struct, "calcsize", lambda _format: 4)
    with pytest.raises(UpdateError, match="64-bit"):
        updater_module.current_platform()
    monkeypatch.setattr(updater_module.struct, "calcsize", lambda _format: 8)
    with pytest.raises(UpdateError, match="CPU architecture"):
        updater_module.current_platform()


def test_malformed_content_length_is_sanitized() -> None:
    with pytest.raises(UpdateError, match="invalid Content-Length"):
        HttpsTransport._content_length({"Content-Length": "not-a-number"})


def test_windows_preflight_detects_insufficient_disk(tmp_path: Path, monkeypatch: Any) -> None:
    artifact = tmp_path / "artifact.zip"
    artifact.write_bytes(b"not important")
    usage = shutil._ntuple_diskusage(total=100, used=99, free=1)
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage)
    with pytest.raises(UpdateError, match="Insufficient disk"):
        PlatformInstaller(system="Windows", frozen=True).preflight(artifact, 10)


@pytest.mark.parametrize(
    "member_name",
    [
        "../outside/AllTheContextSetup.exe",
        r"..\outside\AllTheContextSetup.exe",
        "AllTheContextSetup.exe:stream",
    ],
)
def test_windows_archive_rejects_unsafe_member_paths(tmp_path: Path, member_name: str) -> None:
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member_name, b"untrusted application")

    with pytest.raises(UpdateError, match="unsafe path"):
        PlatformInstaller._extract_windows_setup(archive, tmp_path / "extracted")


@pytest.mark.parametrize(
    "member_names",
    [
        ("AllTheContextSetup.exe", MANIFEST_FILE_NAME),
        (
            "AllTheContextSetup.exe",
            MANIFEST_FILE_NAME,
            CHECKSUM_FILE_NAME,
            "unexpected.exe",
        ),
    ],
)
def test_windows_archive_requires_exact_component_package(
    tmp_path: Path, member_names: tuple[str, ...]
) -> None:
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in member_names:
            bundle.writestr(name, b"untrusted package member")

    with pytest.raises(UpdateError, match="four-component manifest"):
        PlatformInstaller._extract_windows_package(archive, tmp_path / "extracted")


def test_windows_adapter_requires_the_packaged_recovery_helper(tmp_path: Path) -> None:
    application = tmp_path / "AllTheContext.exe"
    helper = tmp_path / "AllTheContextUpdater.exe"
    application.write_bytes(b"application")
    helper.write_bytes(b"helper")
    installer = PlatformInstaller(
        system="Windows",
        frozen=True,
        application_path=application,
        helper_path=helper,
    )
    assert installer.supported is False
    assert "recovery/admin helper" in installer.unsupported_reason


def test_windows_adapter_enables_automatic_install_with_independent_helper(
    tmp_path: Path,
) -> None:
    application = tmp_path / "AllTheContext.exe"
    mcp = tmp_path / "AllTheContextMCP.exe"
    helper = tmp_path / "AllTheContextUpdater.exe"
    recovery = tmp_path / "AllTheContextRecovery.exe"
    application.write_bytes(b"application")
    mcp.write_bytes(b"mcp")
    helper.write_bytes(b"helper")
    recovery.write_bytes(b"recovery")
    installer = PlatformInstaller(
        system="Windows",
        frozen=True,
        application_path=application,
        helper_path=helper,
        mcp_path=mcp,
        recovery_path=recovery,
    )
    assert installer.supported is True


@pytest.mark.parametrize("failure", [None, "state", "register", "launch"])
def test_windows_adapter_prepares_strict_journal_before_detached_handoff(
    tmp_path: Path, monkeypatch: Any, failure: str | None
) -> None:
    data_dir = tmp_path / "data"
    updates = data_dir / "updates"
    install_dir = tmp_path / "installed"
    install_dir.mkdir()
    application = install_dir / "AllTheContext.exe"
    mcp = install_dir / "AllTheContextMCP.exe"
    recovery = install_dir / "AllTheContextRecovery.exe"
    stable_update_helper = install_dir / "AllTheContextUpdater.exe"
    packaged_helper = tmp_path / "bundle" / "AllTheContextUpdater.exe"
    packaged_helper.parent.mkdir()
    application.write_bytes(b"old application")
    mcp.write_bytes(b"old mcp")
    recovery.write_bytes(b"old recovery")
    stable_update_helper.write_bytes(b"old update helper")
    packaged_helper.write_bytes(b"helper")
    database = data_dir / "core.sqlite3"
    data_dir.mkdir()
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE records(id INTEGER PRIMARY KEY)")
        connection.commit()
    finally:
        connection.close()
    backup = updates / "backups" / "core.sqlite3"
    backup.parent.mkdir(parents=True)
    shutil.copy2(database, backup)
    operation_id = "b" * 24
    operation_dir = updates / "staging" / operation_id
    operation_dir.mkdir(parents=True)
    artifact = operation_dir / "artifact.zip"
    setup = b"new application"
    component_manifest, component_checksum = _windows_component_manifest(setup)
    with zipfile.ZipFile(artifact, "w") as bundle:
        bundle.writestr("AllTheContextSetup.exe", setup)
        bundle.writestr(MANIFEST_FILE_NAME, component_manifest)
        bundle.writestr(CHECKSUM_FILE_NAME, component_checksum)
    transaction_dir = updates / "transactions" / operation_id
    state_path = updates / "state.json"
    journal_path = transaction_dir / "journal.json"
    state_path.write_text(
        json.dumps(
            {
                "phase": "restart_required",
                "current_version": "0.1.0",
                "offered_version": "0.2.0",
                "operation_id": operation_id,
                "transaction_path": str(journal_path),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ATC_CORE_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ATC_INSTALL_DIR", str(install_dir))
    registrations: list[tuple[Path, Path, str]] = []
    launches: list[tuple[Path, Path]] = []

    def register(helper: Path, journal: Path, operation: str) -> None:
        registrations.append((helper, journal, operation))
        if failure == "register":
            raise OSError("registration failed")

    def launch(helper: Path, journal: Path) -> None:
        launches.append((helper, journal))
        if failure == "launch":
            raise OSError("launch failed")

    monkeypatch.setattr(updater_module, "register_recovery", register)
    monkeypatch.setattr(updater_module, "launch_recovery_helper", launch)
    if failure == "state":

        def fail_state_binding(_journal: UpdateJournal, _journal_path: Path) -> str:
            raise OSError("state binding interrupted")

        monkeypatch.setattr(updater_module, "bind_handoff_state", fail_state_binding)
    installer = PlatformInstaller(
        system="Windows",
        frozen=True,
        application_path=application,
        helper_path=packaged_helper,
        mcp_path=mcp,
        recovery_path=recovery,
    )
    plan = InstallPlan(
        artifact=artifact,
        target_version="0.2.0",
        current_version="0.1.0",
        operation_id=operation_id,
        operation_dir=operation_dir,
        transaction_dir=transaction_dir,
        database_path=database,
        database_backup_path=backup,
        state_path=state_path,
        core_host="127.0.0.1",
        core_port=7337,
        artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        artifact_size=artifact.stat().st_size,
    )

    if failure is None:
        installer.handoff(plan)
    else:
        with pytest.raises(UpdateError, match="transaction"):
            installer.handoff(plan)

    journal = UpdateJournal.load(journal_path)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert journal.phase is HelperPhase.PREPARED
    identity = journal_handoff_identity(journal)
    assert state["handoff_identity"] == (None if failure == "state" else identity)
    assert state["pending_handoff_identity"] == (identity if failure == "state" else None)
    assert state["transaction_path"] == str(journal_path)
    assert Path(journal.rollback_application_path).read_bytes() == b"old application"
    assert Path(journal.rollback_mcp_path or "").read_bytes() == b"old mcp"
    assert Path(journal.rollback_recovery_path or "").read_bytes() == b"old recovery"
    assert Path(journal.rollback_update_helper_path).read_bytes() == b"old update helper"
    assert Path(journal.replacement_path).read_bytes() == b"new application"
    expected_registrations = (
        [] if failure == "state" else [(Path(journal.helper_path), journal_path, operation_id)]
    )
    assert registrations == expected_registrations
    expected_launches = (
        [] if failure in {"state", "register"} else [(Path(journal.helper_path), journal_path)]
    )
    assert launches == expected_launches


@pytest.mark.parametrize(
    "url",
    [
        "http://updates.example.test/stable/manifest.json",
        "https://updates.example.test/main/manifest.json",
        "https://updates.example.test/latest/manifest.json",
        "https://updates.example.test/stable/manifest.json?ref=v1",
    ],
)
def test_metadata_transport_rejects_insecure_or_mutable_endpoints(url: str) -> None:
    with pytest.raises(UpdateError):
        HttpsTransport._request(url)


def test_release_download_follows_one_pinned_github_asset_redirect(tmp_path: Path) -> None:
    source = (
        "https://github.com/Martian-ux/All-The-Context/releases/download/"
        "v0.2.0-beta.1/all-the-context-0.2.0-beta.1-windows-x86_64.zip"
    )
    redirected = (
        "https://release-assets.githubusercontent.com/"
        "github-production-release-asset/123/asset-id?sig=temporary"
    )
    artifact = b"signed artifact bytes"

    class Response:
        fp = None
        remaining = artifact

        def __init__(self) -> None:
            self.headers = {"Content-Length": str(len(artifact))}

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, _size: int = -1) -> bytes:
            value, self.remaining = self.remaining, b""
            return value

    class RedirectingOpener:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def open(self, request: Any, *, timeout: float) -> Response:
            assert timeout == updater_module.CONNECT_TIMEOUT_SECONDS
            self.calls.append(request.full_url)
            if len(self.calls) == 1:
                raise urllib.error.HTTPError(
                    source,
                    302,
                    "Found",
                    {"Location": redirected},
                    None,
                )
            return Response()

    transport = HttpsTransport()
    opener = RedirectingOpener()
    transport._opener = cast(Any, opener)
    target = tmp_path / "artifact.zip"

    digest, received = transport.stream(
        source,
        target,
        expected_bytes=len(artifact),
        cancelled=lambda: False,
    )

    assert opener.calls == [source, redirected]
    assert target.read_bytes() == artifact
    assert digest == hashlib.sha256(artifact).hexdigest()
    assert received == len(artifact)


@pytest.mark.parametrize(
    "redirected",
    [
        "https://example.com/github-production-release-asset/123/asset?sig=value",
        "http://release-assets.githubusercontent.com/github-production-release-asset/123/a?x=1",
        "https://release-assets.githubusercontent.com/unexpected/123/asset?sig=value",
        "https://release-assets.githubusercontent.com/github-production-release-asset/123/asset",
    ],
)
def test_release_download_refuses_unpinned_redirect_targets(redirected: str) -> None:
    source = (
        "https://github.com/Martian-ux/All-The-Context/releases/download/"
        "v0.2.0-beta.1/all-the-context.zip"
    )
    with pytest.raises(UpdateError, match="redirect"):
        HttpsTransport._release_asset_redirect(source, redirected)


def test_metadata_fetch_still_refuses_the_release_asset_redirect() -> None:
    source = "https://updates.example.test/beta/manifest-v1.json"

    class RedirectingOpener:
        @staticmethod
        def open(_request: Any, *, timeout: float) -> Any:
            assert timeout == updater_module.CONNECT_TIMEOUT_SECONDS
            raise urllib.error.HTTPError(
                source,
                302,
                "Found",
                {
                    "Location": (
                        "https://release-assets.githubusercontent.com/"
                        "github-production-release-asset/123/asset?sig=temporary"
                    )
                },
                None,
            )

    transport = HttpsTransport()
    transport._opener = cast(Any, RedirectingOpener())
    with pytest.raises(UpdateError, match="redirect"):
        transport.get_bytes(source, maximum_bytes=MAX_MANIFEST_BYTES)


def test_metadata_transport_preserves_the_http_status_code() -> None:
    source = "https://updates.example.test/beta/manifest-v1.json"

    class NotFoundOpener:
        @staticmethod
        def open(_request: Any, *, timeout: float) -> Any:
            assert timeout == updater_module.CONNECT_TIMEOUT_SECONDS
            raise urllib.error.HTTPError(source, 404, "Not Found", {}, None)

    transport = HttpsTransport()
    transport._opener = cast(Any, NotFoundOpener())

    with pytest.raises(UpdateEndpointHttpError) as error:
        transport.get_bytes(source, maximum_bytes=MAX_MANIFEST_BYTES)

    assert error.value.status_code == 404
    assert str(error.value) == "Update endpoint returned HTTP 404"


def test_defer_is_persisted_and_mandatory_update_cannot_be_deferred(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    assert manager.defer()["deferred_version"] == "0.2.0"
    assert manager.scheduled_check()["phase"] == "deferred"
    assert manager.check()["phase"] == "available"
    manifest["mandatory"] = True
    private = Ed25519PrivateKey.from_private_bytes(SEED)
    manifest["signature"] = (
        __import__("base64")
        .urlsafe_b64encode(private.sign(canonical_payload(manifest)))
        .rstrip(b"=")
        .decode("ascii")
    )
    manager.check()
    with pytest.raises(UpdateError, match="cannot be deferred"):
        manager.defer()
