from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import allthecontext.updater as updater_module
import pytest
from allthecontext.release_manifest import (
    canonical_payload,
    create_manifest,
    public_key_fingerprint,
    public_key_value,
)
from allthecontext.updater import (
    MAX_MANIFEST_BYTES,
    HttpsTransport,
    InstallPlan,
    PlatformInstaller,
    UpdateBusyError,
    UpdateConfig,
    UpdateError,
    UpdateManager,
    UpdatePhase,
    UpdateState,
)
from allthecontext.windows_update_helper import HelperPhase, UpdateJournal
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

SEED = bytes(range(32))


class FakeTransport:
    def __init__(self, manifest: dict[str, Any], artifact: bytes) -> None:
        self.manifest = manifest
        self.artifact = artifact
        self.metadata_error: UpdateError | None = None
        self.download_error: UpdateError | None = None
        self.reported_bytes: int | None = None
        self.reported_digest: str | None = None
        self.cancel_during_download = False

    def get_bytes(self, url: str, *, maximum_bytes: int) -> bytes:
        assert url.startswith("https://")
        assert maximum_bytes == MAX_MANIFEST_BYTES
        if self.metadata_error:
            raise self.metadata_error
        return json.dumps(self.manifest).encode("utf-8")

    def stream(
        self,
        url: str,
        target: Path,
        *,
        expected_bytes: int,
        cancelled: Any,
    ) -> tuple[str, int]:
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
        assert plan.target_version == "0.2.0"
        assert plan.operation_dir.is_dir()
        if self.failure == "locked":
            raise UpdateError("Installed files are locked")
        if self.failure == "crash":
            raise UpdateError("Installer process crashed")
        self.handed_off = True

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

    recovered, _, recovered_installer = _manager(
        tmp_path,
        manifest,
        artifact,
        keyring,
        current_version="0.2.0",
        installer=installer,
        health=FakeHealth(False),
    )
    assert recovered.recover_after_restart()["phase"] == "rolled_back"
    assert recovered_installer.rolled_back
    assert recovered.recover_after_restart()["phase"] == "rolled_back"


def test_successful_restart_health_check_completes_and_cleans_staging(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    manager.check()
    manager.download()
    manager.install()
    recovered, _, _ = _manager(
        tmp_path, manifest, artifact, keyring, current_version="0.2.0", health=FakeHealth(True)
    )
    assert recovered.recover_after_restart()["phase"] == "installed"
    assert recovered.recover_after_restart()["phase"] == "installed"


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


def test_repeated_checks_remove_bounded_orphan_staging(tmp_path: Path) -> None:
    manifest, artifact, keyring = _fixture(tmp_path)
    manager, _, _ = _manager(tmp_path, manifest, artifact, keyring)
    staging = manager.config.data_dir / "staging"
    for index in range(40):
        (staging / f"orphan-{index:02d}").mkdir(parents=True)
    manager.check()
    assert len(list(staging.iterdir())) <= 9
    manager.check()
    assert [entry.name for entry in staging.iterdir()] == [manager.state.operation_id]


def test_restart_retains_the_latest_terminal_recovery_journal(tmp_path: Path) -> None:
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
    assert not orphan.exists()


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


def test_windows_adapter_requires_the_packaged_recovery_helper() -> None:
    installer = PlatformInstaller(system="Windows", frozen=True)
    assert installer.supported is False
    assert "recovery helper" in installer.unsupported_reason


def test_windows_adapter_enables_automatic_install_with_independent_helper(
    tmp_path: Path,
) -> None:
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
    assert installer.supported is True


def test_windows_adapter_prepares_strict_journal_before_detached_handoff(
    tmp_path: Path, monkeypatch: Any
) -> None:
    data_dir = tmp_path / "data"
    updates = data_dir / "updates"
    install_dir = tmp_path / "installed"
    install_dir.mkdir()
    application = install_dir / "AllTheContext.exe"
    mcp = install_dir / "AllTheContextMCP.exe"
    stable_update_helper = install_dir / "AllTheContextUpdater.exe"
    packaged_helper = tmp_path / "bundle" / "AllTheContextUpdater.exe"
    packaged_helper.parent.mkdir()
    application.write_bytes(b"old application")
    mcp.write_bytes(b"old mcp")
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
    with zipfile.ZipFile(artifact, "w") as bundle:
        bundle.writestr("AllTheContextSetup.exe", b"new application")
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
    monkeypatch.setattr(
        updater_module,
        "register_recovery",
        lambda helper, journal, operation: registrations.append((helper, journal, operation)),
    )
    monkeypatch.setattr(
        updater_module,
        "launch_recovery_helper",
        lambda helper, journal: launches.append((helper, journal)),
    )
    installer = PlatformInstaller(
        system="Windows",
        frozen=True,
        application_path=application,
        helper_path=packaged_helper,
        mcp_path=mcp,
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
    )

    installer.handoff(plan)

    journal = UpdateJournal.load(journal_path)
    assert journal.phase is HelperPhase.PREPARED
    assert Path(journal.rollback_application_path).read_bytes() == b"old application"
    assert Path(journal.rollback_mcp_path or "").read_bytes() == b"old mcp"
    assert Path(journal.rollback_update_helper_path).read_bytes() == b"old update helper"
    assert Path(journal.replacement_path).read_bytes() == b"new application"
    assert registrations == [(Path(journal.helper_path), journal_path, operation_id)]
    assert launches == [(Path(journal.helper_path), journal_path)]


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
