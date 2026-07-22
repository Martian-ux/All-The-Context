from __future__ import annotations

import urllib.request
from pathlib import Path

import allthecontext
import pytest
from allthecontext.config import CoreConfig
from allthecontext.credentials import DevelopmentFileCredentialStore
from allthecontext.desktop import (
    _copy_atomically,
    _install_mcp_helper,
    _schedule_windows_install_removal,
    _stop_installed_core_for_upgrade,
    _uninstall,
    main,
    prepare_installed_runtime,
)
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.desktop_setup import CLAUDE_CLIENT_NAME, CODEX_CLIENT_NAME, CoreProbe
from allthecontext.edge_connection import decommission_edge_connection
from allthecontext.instance_identity import ensure_instance_secret
from allthecontext.models import ClientCreate
from allthecontext.storage import CoreStore


def test_bundled_dashboard_contains_current_edge_setup() -> None:
    package_root = Path(allthecontext.__file__).resolve().parent
    web_root = package_root / "web"
    javascript = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((web_root / "assets").glob("*.js"))
    )

    assert "Edge for web and mobile" in javascript
    assert "/admin/edge/prepare" in javascript
    assert "Cancel Edge setup" in javascript
    assert "Web + mobile" in javascript
    assert "Cloud setup unavailable" not in javascript
    assert "custom MCP apps are currently web-only" not in javascript


def test_windows_frozen_app_self_installs_with_mcp_helper(tmp_path: Path, monkeypatch) -> None:
    source_app = tmp_path / "download" / "AllTheContextSetup.exe"
    source_helper = tmp_path / "bundle" / "AllTheContextMCP.exe"
    source_app.parent.mkdir()
    source_helper.parent.mkdir()
    source_app.write_bytes(b"desktop")
    source_helper.write_bytes(b"mcp")
    install_dir = tmp_path / "installed"
    install_dir.mkdir()
    (install_dir / "AllTheContext.exe").write_bytes(b"older desktop")
    monkeypatch.setenv("ATC_INSTALL_DIR", str(install_dir))
    monkeypatch.setattr("allthecontext.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr("allthecontext.desktop.sys.frozen", True, raising=False)
    launched: list[tuple[str, ...]] = []
    launch_environments: list[dict[str, str]] = []
    stopped: list[bool] = []
    registered: list[Path] = []

    class Process:
        pass

    def fake_popen(command: tuple[str, ...], **kwargs: object) -> Process:
        launched.append(command)
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        launch_environments.append(environment)
        return Process()

    monkeypatch.setattr("allthecontext.desktop.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "allthecontext.desktop._stop_installed_core_for_upgrade",
        lambda: stopped.append(True),
    )
    monkeypatch.setattr(
        "allthecontext.desktop.install_application_entrypoints",
        lambda target: registered.append(target),
    )

    installed, relaunched = prepare_installed_runtime(
        RuntimeCommand(source_app, mcp_executable=source_helper),
        relaunch_args=(),
    )

    assert relaunched is True
    assert installed.executable == install_dir / "AllTheContext.exe"
    assert installed.executable.read_bytes() == b"desktop"
    assert installed.mcp_executable == install_dir / "AllTheContextMCP.exe"
    assert installed.mcp_executable.read_bytes() == b"mcp"
    assert launched == [(str(installed.executable),)]
    assert launch_environments[0]["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert stopped == [True]
    assert registered == [installed.executable]


def test_windows_uninstall_retries_self_removal_after_bootloader_exits(
    tmp_path: Path, monkeypatch
) -> None:
    install_dir = tmp_path / "installed"
    install_dir.mkdir()
    launched: list[tuple[list[str], dict[str, object]]] = []

    class Process:
        pass

    def fake_popen(command: list[str], **kwargs: object) -> Process:
        launched.append((command, kwargs))
        return Process()

    monkeypatch.setattr("allthecontext.desktop.windows_install_directory", lambda: install_dir)
    monkeypatch.setattr("allthecontext.desktop.subprocess.Popen", fake_popen)

    _schedule_windows_install_removal(install_dir)

    assert len(launched) == 1
    command, kwargs = launched[0]
    script = command[-1]
    assert "Wait-Process" in script
    assert "for($atcAttempt=0;$atcAttempt -lt 300;$atcAttempt++)" in script
    assert "Remove-Item" in script
    assert "-ErrorAction Stop" in script
    assert "Start-Sleep -Milliseconds 100" in script
    assert kwargs["env"]["ATC_UNINSTALL_DIR"] == str(install_dir.resolve())  # type: ignore[index]
    assert kwargs["cwd"] == install_dir.resolve().parent


def test_graphical_install_failure_is_reported_with_retry_and_diagnostics(
    tmp_path: Path, monkeypatch
) -> None:
    runtime = RuntimeCommand(tmp_path / "AllTheContextSetup.exe")
    diagnostics_path = tmp_path / "desktop-error.json"
    prompts: list[tuple[str, str]] = []
    monkeypatch.setattr("allthecontext.desktop.RuntimeCommand.current", lambda: runtime)
    monkeypatch.setattr(
        "allthecontext.desktop.prepare_installed_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("shortcut registration failed")),
    )
    monkeypatch.setattr(
        "allthecontext.desktop._write_failure_diagnostics",
        lambda _error: diagnostics_path,
    )
    monkeypatch.setattr(
        "allthecontext.desktop.messagebox.askretrycancel",
        lambda title, detail: prompts.append((title, detail)) or False,
    )

    assert main([]) == 1
    assert prompts
    assert "shortcut registration failed" in prompts[0][1]
    assert str(diagnostics_path) in prompts[0][1]
    assert "no local context was deleted" in prompts[0][1]


def test_uninstall_cleanup_failure_never_reports_false_success(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    monkeypatch.setattr("allthecontext.desktop.CoreConfig.default", lambda: config)
    monkeypatch.setattr("allthecontext.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr("allthecontext.desktop.sys.frozen", True, raising=False)
    monkeypatch.setattr("allthecontext.desktop.messagebox.askyesno", lambda *_args: True)
    successes: list[str] = []
    monkeypatch.setattr(
        "allthecontext.desktop.messagebox.showinfo",
        lambda title, _detail: successes.append(title),
    )

    class NoEdge:
        def __init__(self, _config: CoreConfig) -> None:
            pass

        def state(self) -> None:
            return None

        def material(self) -> None:
            return None

    monkeypatch.setattr("allthecontext.desktop.EdgeConnectionStore", NoEdge)
    monkeypatch.setattr(
        "allthecontext.desktop._stop_installed_core_for_upgrade",
        lambda: (_ for _ in ()).throw(OSError("running process could not stop")),
    )
    scheduled: list[Path] = []
    monkeypatch.setattr(
        "allthecontext.desktop._schedule_windows_install_removal",
        lambda path: scheduled.append(path),
    )

    with pytest.raises(RuntimeError, match="Local uninstall cleanup did not finish"):
        _uninstall(RuntimeCommand(tmp_path / "AllTheContext.exe"))

    assert not successes
    assert not scheduled


def test_uninstall_keeps_app_when_client_configuration_cannot_be_cleaned(
    tmp_path: Path, monkeypatch
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    monkeypatch.setattr("allthecontext.desktop.CoreConfig.default", lambda: config)
    monkeypatch.setattr("allthecontext.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr("allthecontext.desktop.sys.frozen", True, raising=False)
    monkeypatch.setattr("allthecontext.desktop.messagebox.askyesno", lambda *_args: True)
    successes: list[str] = []
    monkeypatch.setattr(
        "allthecontext.desktop.messagebox.showinfo",
        lambda title, _detail: successes.append(title),
    )

    class NoEdge:
        def __init__(self, _config: CoreConfig) -> None:
            pass

        def state(self) -> None:
            return None

        def material(self) -> None:
            return None

    monkeypatch.setattr("allthecontext.desktop.EdgeConnectionStore", NoEdge)
    monkeypatch.setattr("allthecontext.desktop._stop_installed_core_for_upgrade", lambda: None)
    monkeypatch.setattr(
        "allthecontext.desktop.plan_managed_client_cleanup",
        lambda: (_ for _ in ()).throw(ValueError("Codex config.toml is malformed")),
    )
    later_cleanup: list[str] = []
    monkeypatch.setattr(
        "allthecontext.desktop.apply_managed_client_cleanup",
        lambda _plan: later_cleanup.append("client configs"),
    )
    monkeypatch.setattr(
        "allthecontext.desktop.remove_application_entrypoints",
        lambda: later_cleanup.append("entrypoints"),
    )
    monkeypatch.setattr(
        "allthecontext.desktop._schedule_windows_install_removal",
        lambda _path: later_cleanup.append("delete"),
    )

    with pytest.raises(RuntimeError, match=r"Codex config\.toml is malformed"):
        _uninstall(RuntimeCommand(tmp_path / "AllTheContext.exe"))

    assert not successes
    assert not later_cleanup


def test_uninstall_revokes_managed_ai_clients_and_deletes_their_credentials(
    tmp_path: Path, monkeypatch
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    codex, _ = store.create_client(ClientCreate(name=CODEX_CLIENT_NAME, scopes=["context:read"]))
    claude, _ = store.create_client(ClientCreate(name=CLAUDE_CLIENT_NAME, scopes=["context:read"]))
    unrelated, _ = store.create_client(
        ClientCreate(name="User-managed integration", scopes=["context:read"])
    )
    fallback = DevelopmentFileCredentialStore(config.data_dir / "credentials.development.json")
    for client in (codex, claude, unrelated):
        fallback.set(f"client:{client.id}", f"token-for-{client.id}")

    monkeypatch.setattr("allthecontext.desktop.CoreConfig.default", lambda: config)
    monkeypatch.setattr("allthecontext.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr("allthecontext.desktop.sys.frozen", True, raising=False)
    monkeypatch.setattr("allthecontext.desktop.messagebox.askyesno", lambda *_args: True)
    monkeypatch.setattr("allthecontext.desktop.messagebox.showinfo", lambda *_args: None)
    monkeypatch.setattr("allthecontext.desktop._stop_installed_core_for_upgrade", lambda: None)
    monkeypatch.setattr("allthecontext.desktop.plan_managed_client_cleanup", lambda: ())
    monkeypatch.setattr("allthecontext.desktop.apply_managed_client_cleanup", lambda _plan: None)
    monkeypatch.setattr("allthecontext.desktop.remove_user_startup", lambda: None)
    monkeypatch.setattr("allthecontext.desktop.remove_application_entrypoints", lambda: None)
    scheduled: list[Path] = []
    monkeypatch.setattr(
        "allthecontext.desktop._schedule_windows_install_removal",
        lambda path: scheduled.append(path),
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.delete", lambda *_args: None
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.get", lambda *_args: None
    )

    class NoEdge:
        def __init__(self, _config: CoreConfig) -> None:
            pass

        def state(self) -> None:
            return None

        def material(self) -> None:
            return None

    monkeypatch.setattr("allthecontext.desktop.EdgeConnectionStore", NoEdge)

    assert _uninstall(RuntimeCommand(tmp_path / "AllTheContext.exe")) == 0

    clients = {str(item["id"]): item for item in store.list_clients()}
    assert clients[codex.id]["revoked"] is True
    assert clients[claude.id]["revoked"] is True
    assert clients[unrelated.id]["revoked"] is False
    assert fallback.get(f"client:{codex.id}") is None
    assert fallback.get(f"client:{claude.id}") is None
    assert fallback.get(f"client:{unrelated.id}") is not None
    assert scheduled == [tmp_path]


def test_locked_mcp_helper_uses_a_content_addressed_update(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source" / "AllTheContextMCP.exe"
    target = tmp_path / "installed" / "AllTheContextMCP.exe"
    source.parent.mkdir()
    target.parent.mkdir()
    source.write_bytes(b"new helper")
    target.write_bytes(b"running helper")

    def locked_copy(source_path: Path, target_path: Path) -> None:
        if target_path == target:
            raise PermissionError("in use")
        _copy_atomically(source_path, target_path)

    monkeypatch.setattr("allthecontext.desktop._copy_atomically", locked_copy)

    installed = _install_mcp_helper(source, target)

    assert installed.name.startswith("AllTheContextMCP-")
    assert installed.suffix == ".exe"
    assert installed.read_bytes() == b"new helper"
    assert target.read_bytes() == b"running helper"


def test_legacy_upgrade_uses_and_revokes_a_one_time_credential(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    monkeypatch.setattr("allthecontext.desktop.CoreConfig.default", lambda: config)
    states = iter((CoreProbe.UNVERIFIED, CoreProbe.UNREACHABLE))
    monkeypatch.setattr("allthecontext.desktop.probe_core", lambda _config: next(states))
    observed_tokens: list[str] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    def fake_urlopen(request: urllib.request.Request, **_kwargs: object) -> Response:
        authorization = request.get_header("Authorization")
        assert authorization is not None
        token = authorization.removeprefix("Bearer ")
        principal = store.authenticate(token)
        assert principal is not None
        assert principal.name == "All The Context one-time upgrade"
        observed_tokens.append(token)
        return Response()

    monkeypatch.setattr("allthecontext.desktop.urllib.request.urlopen", fake_urlopen)

    _stop_installed_core_for_upgrade()

    assert len(observed_tokens) == 1
    assert store.authenticate(observed_tokens[0]) is None


def test_upgrade_refuses_an_invalid_proof_when_identity_exists(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    ensure_instance_secret(config)
    monkeypatch.setattr("allthecontext.desktop.CoreConfig.default", lambda: config)
    monkeypatch.setattr(
        "allthecontext.desktop.probe_core",
        lambda _config: CoreProbe.UNVERIFIED,
    )
    called: list[bool] = []
    monkeypatch.setattr(
        "allthecontext.desktop.urllib.request.urlopen",
        lambda *_args, **_kwargs: called.append(True),
    )

    with pytest.raises(RuntimeError, match="did not send it a credential"):
        _stop_installed_core_for_upgrade()

    assert not called


@pytest.mark.parametrize("corrupt_database", [False, True])
def test_uninstall_decommissions_edge_before_optional_core_identity_cleanup(
    tmp_path: Path, monkeypatch, corrupt_database: bool
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    config.data_dir.mkdir(parents=True)
    if corrupt_database:
        config.database_path.write_bytes(b"not a sqlite database")
    managed_client_id = "configured-client-from-removed-mcp-block"
    fallback = DevelopmentFileCredentialStore(config.data_dir / "credentials.development.json")
    fallback.set(f"client:{managed_client_id}", "configured-client-token")
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    codex_config = codex_home / "config.toml"
    managed_config = (
        "# BEGIN All The Context managed MCP\n"
        "[mcp_servers.all_the_context]\n"
        'command = "AllTheContextMCP.exe"\n'
        f'env = {{ ATC_CLIENT_ID = "{managed_client_id}", '
        'ATC_CLIENT_TOKEN = "configured-client-token" }\n'
        "# END All The Context managed MCP\n"
    )
    codex_config.write_text(managed_config, encoding="utf-8")
    codex_backup = codex_home / "config.toml.atc-backup-existing"
    codex_backup.write_text(managed_config, encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("ATC_CLAUDE_CONFIG", str(tmp_path / "missing-claude.json"))
    monkeypatch.setattr("allthecontext.desktop.CoreConfig.default", lambda: config)
    monkeypatch.setattr("allthecontext.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr("allthecontext.desktop.sys.frozen", True, raising=False)
    monkeypatch.setattr("allthecontext.desktop.messagebox.askyesno", lambda *_args: True)
    information: list[str] = []
    monkeypatch.setattr(
        "allthecontext.desktop.messagebox.showinfo",
        lambda _title, detail: information.append(detail),
    )
    monkeypatch.setattr("allthecontext.desktop._stop_installed_core_for_upgrade", lambda: None)
    monkeypatch.setattr("allthecontext.desktop.remove_user_startup", lambda: None)
    monkeypatch.setattr("allthecontext.desktop.remove_application_entrypoints", lambda: None)
    scheduled: list[Path] = []
    monkeypatch.setattr(
        "allthecontext.desktop._schedule_windows_install_removal",
        lambda path: scheduled.append(path),
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.delete", lambda *_args: None
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.get", lambda *_args: None
    )

    class FakeConnections:
        def __init__(self, actual_config: CoreConfig) -> None:
            assert actual_config == config

        def state(self) -> object:
            return object()

    decommissioned: list[object] = []
    monkeypatch.setattr("allthecontext.desktop.EdgeConnectionStore", FakeConnections)
    monkeypatch.setattr(
        "allthecontext.desktop.decommission_edge_connection",
        lambda connections: decommissioned.append(connections),
    )

    assert _uninstall(RuntimeCommand(tmp_path / "AllTheContext.exe")) == 0
    assert scheduled == [tmp_path]
    assert len(decommissioned) == 1
    assert information
    assert ("internal AI client rows could not be revoked" in information[0]) is corrupt_database
    assert fallback.get(f"client:{managed_client_id}") is None
    assert "all_the_context" not in codex_config.read_text(encoding="utf-8")
    assert "configured-client-token" not in codex_backup.read_text(encoding="utf-8")


def test_uninstall_blocks_when_remote_edge_cannot_be_verified(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    monkeypatch.setattr("allthecontext.desktop.CoreConfig.default", lambda: config)
    monkeypatch.setattr("allthecontext.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr("allthecontext.desktop.sys.frozen", True, raising=False)
    answers = iter([True, False])
    monkeypatch.setattr("allthecontext.desktop.messagebox.askyesno", lambda *_args: next(answers))
    errors: list[str] = []
    monkeypatch.setattr(
        "allthecontext.desktop.messagebox.showerror",
        lambda _title, detail: errors.append(detail),
    )

    class FakeConnections:
        def __init__(self, _config: CoreConfig) -> None:
            pass

        def state(self) -> object:
            return object()

    monkeypatch.setattr("allthecontext.desktop.EdgeConnectionStore", FakeConnections)
    monkeypatch.setattr(
        "allthecontext.desktop.decommission_edge_connection",
        lambda _connections: (_ for _ in ()).throw(RuntimeError("Edge is offline")),
    )
    cleanup_called: list[bool] = []
    monkeypatch.setattr(
        "allthecontext.desktop._stop_installed_core_for_upgrade",
        lambda: cleanup_called.append(True),
    )

    result = _uninstall(RuntimeCommand(tmp_path / "AllTheContext.exe"))

    assert result == 1
    assert not cleanup_called
    assert errors and "recovery information were kept" in errors[0]


def test_uninstall_can_forget_edge_only_after_manual_host_deletion(
    tmp_path: Path, monkeypatch
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    monkeypatch.setattr("allthecontext.desktop.CoreConfig.default", lambda: config)
    monkeypatch.setattr("allthecontext.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr("allthecontext.desktop.sys.frozen", True, raising=False)
    answers = iter([True, True])
    monkeypatch.setattr("allthecontext.desktop.messagebox.askyesno", lambda *_args: next(answers))
    monkeypatch.setattr("allthecontext.desktop.messagebox.showinfo", lambda *_args: None)
    monkeypatch.setattr("allthecontext.desktop._stop_installed_core_for_upgrade", lambda: None)
    monkeypatch.setattr("allthecontext.desktop.plan_managed_client_cleanup", lambda: ())
    monkeypatch.setattr("allthecontext.desktop.apply_managed_client_cleanup", lambda _plan: None)
    monkeypatch.setattr("allthecontext.desktop.remove_user_startup", lambda: None)
    monkeypatch.setattr("allthecontext.desktop.remove_application_entrypoints", lambda: None)
    monkeypatch.setattr("allthecontext.desktop._schedule_windows_install_removal", lambda _p: None)

    reset_called: list[bool] = []

    class FakeConnections:
        def __init__(self, _config: CoreConfig) -> None:
            pass

        def reset(self) -> None:
            reset_called.append(True)

    monkeypatch.setattr("allthecontext.desktop.EdgeConnectionStore", FakeConnections)
    monkeypatch.setattr(
        "allthecontext.desktop.decommission_edge_connection",
        lambda _connections: (_ for _ in ()).throw(RuntimeError("Edge is offline")),
    )

    result = _uninstall(RuntimeCommand(tmp_path / "AllTheContext.exe"))

    assert result == 0
    assert reset_called == [True]


def test_decommission_blocks_orphaned_edge_credential() -> None:
    reset_called: list[bool] = []

    class OrphanedMaterial:
        def state(self) -> None:
            return None

        def material(self) -> object:
            return object()

        def reset(self) -> None:
            reset_called.append(True)

    with pytest.raises(RuntimeError, match="connection state is missing"):
        decommission_edge_connection(OrphanedMaterial())  # type: ignore[arg-type]

    assert not reset_called


def test_decommission_blocks_connected_state_without_credential() -> None:
    reset_called: list[bool] = []

    class ConnectedState:
        edge_url = "https://edge.example.test"

    class OrphanedState:
        def state(self) -> ConnectedState:
            return ConnectedState()

        def material(self) -> None:
            return None

        def reset(self) -> None:
            reset_called.append(True)

    with pytest.raises(RuntimeError, match="credentials are unavailable"):
        decommission_edge_connection(OrphanedState())  # type: ignore[arg-type]

    assert not reset_called


def test_decommission_preserves_unpaired_state_until_host_deletion_is_confirmed() -> None:
    reset_called: list[bool] = []

    class PreparedState:
        edge_url = None

    class PreparedConnection:
        def state(self) -> PreparedState:
            return PreparedState()

        def material(self) -> object:
            return object()

        def reset(self) -> None:
            reset_called.append(True)

    with pytest.raises(RuntimeError, match="cannot verify whether a hosted service was deployed"):
        decommission_edge_connection(PreparedConnection())  # type: ignore[arg-type]

    assert not reset_called
