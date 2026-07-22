from __future__ import annotations

import json
import tomllib
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest
from allthecontext.client_config import configure_codex
from allthecontext.config import CoreConfig
from allthecontext.credentials import (
    OS_CREDENTIAL_STORAGE,
    DevelopmentFileCredentialStore,
)
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.desktop_setup import (
    AI_CLIENT_SCOPES,
    CODEX_CLIENT_NAME,
    DESKTOP_CLIENT_NAME,
    DESKTOP_SCOPES,
    CoreProbe,
    SetupOptions,
    delete_client_credential,
    ensure_client_access,
    launch_core,
    migrate_existing_integrations,
    perform_setup,
    recover_desktop_access,
    retire_other_named_clients,
)
from allthecontext.models import ClientCreate
from allthecontext.storage import CoreStore


def test_frozen_core_launch_uses_an_independent_pyinstaller_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    config = replace(CoreConfig.in_directory(tmp_path / "core"), port=17_439)
    runtime = RuntimeCommand(tmp_path / "AllTheContext.exe")
    states = iter((CoreProbe.UNREACHABLE, CoreProbe.VERIFIED))
    launched: list[tuple[tuple[str, ...], dict[str, object]]] = []

    class Process:
        pass

    def fake_popen(command: tuple[str, ...], **kwargs: object) -> Process:
        launched.append((command, kwargs))
        return Process()

    monkeypatch.setattr("allthecontext.desktop_setup.sys.frozen", True, raising=False)
    monkeypatch.setattr(
        "allthecontext.desktop_setup.probe_core",
        lambda _config: next(states),
    )
    monkeypatch.setattr("allthecontext.desktop_setup.subprocess.Popen", fake_popen)

    launch_core(runtime, config, wait_seconds=0.1)

    assert len(launched) == 1
    command, kwargs = launched[0]
    assert command == runtime.core()
    environment = kwargs["env"]
    assert isinstance(environment, dict)
    assert environment["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_client_credential_deletion_is_verified_before_removing_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    fallback = DevelopmentFileCredentialStore(config.data_dir / "credentials.development.json")
    fallback.set("client:managed-client", "recoverable-token")
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.delete",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("Credential Manager unavailable")),
    )

    with pytest.raises(RuntimeError, match="Could not verify removal"):
        delete_client_credential(
            "managed-client",
            config,
            strict_storage=OS_CREDENTIAL_STORAGE,
        )

    assert fallback.get("client:managed-client") == "recoverable-token"


def test_revoked_client_cleanup_tolerates_missing_linux_secret_service(
    tmp_path: Path, monkeypatch
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    stale, _token = store.create_client(
        ClientCreate(name=CODEX_CLIENT_NAME, scopes=AI_CLIENT_SCOPES)
    )
    fallback = DevelopmentFileCredentialStore(config.data_dir / "credentials.development.json")
    fallback.set(f"client:{stale.id}", "fallback-token")
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.delete",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("Secret Service unavailable")),
    )

    retire_other_named_clients(
        store,
        config,
        name=CODEX_CLIENT_NAME,
        keep_id="replacement-client",
    )

    retired = next(item for item in store.list_clients() if item["id"] == stale.id)
    assert retired["revoked"] is True
    assert fallback.get(f"client:{stale.id}") is None


def test_setup_initializes_recoverable_access_and_codex(tmp_path: Path, monkeypatch) -> None:
    config = replace(CoreConfig.in_directory(tmp_path / "core"), port=17_440)
    codex_home = tmp_path / "codex"
    claude_config = tmp_path / "claude" / "claude_desktop_config.json"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("ATC_CLAUDE_CONFIG", str(claude_config))

    def empty_get(*_args: object, **_kwargs: object) -> None:
        return None

    def ignored_set(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.get", empty_get)
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.set", ignored_set)
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.delete", ignored_set)
    log_path = config.data_dir / "logs" / "core.log"
    monkeypatch.setattr(
        "allthecontext.desktop_setup.launch_core",
        lambda _runtime, _config: log_path,
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.authenticated_dashboard_url",
        lambda active_config, _token: (
            f"http://{active_config.host}:{active_config.port}/v1/browser/connect?ticket=test"
        ),
    )
    runtime = RuntimeCommand(Path("python"), ("-m", "allthecontext.desktop"))

    result = perform_setup(
        SetupOptions(configure_codex=True, start_at_login=False),
        runtime,
        config=config,
    )

    assert result.credential_storage == "local app-data fallback"
    assert result.warnings
    assert result.dashboard_url == "http://127.0.0.1:17440/v1/browser/connect?ticket=test"
    access = recover_desktop_access(config)
    assert access is not None
    assert access.client_id == result.client_id
    parsed = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    managed = parsed["mcp_servers"]["all_the_context"]
    assert managed["env"]["ATC_CLIENT_ID"] != result.client_id
    assert managed["env"]["ATC_CLIENT_TOKEN"] != access.token
    assert managed["env"]["ATC_TARGET_URL"] == "http://127.0.0.1:17440"
    claude = json.loads(claude_config.read_text(encoding="utf-8"))
    claude_managed = claude["mcpServers"]["all-the-context"]
    assert claude_managed["env"]["ATC_CLIENT_ID"] != result.client_id
    assert claude_managed["env"]["ATC_CLIENT_ID"] != managed["env"]["ATC_CLIENT_ID"]
    assert claude_managed["env"]["ATC_CLIENT_TOKEN"] != access.token
    assert result.claude is not None
    store = CoreStore(config.database_path)
    codex_principal = store.authenticate(managed["env"]["ATC_CLIENT_TOKEN"])
    claude_principal = store.authenticate(claude_managed["env"]["ATC_CLIENT_TOKEN"])
    assert codex_principal is not None and "admin" not in codex_principal.scopes
    assert claude_principal is not None and "admin" not in claude_principal.scopes

    repeated = perform_setup(
        SetupOptions(configure_codex=False, configure_claude=False, start_at_login=False),
        runtime,
        config=config,
    )
    assert repeated.client_id == result.client_id


def test_dashboard_handoff_refuses_an_unverified_service_without_sending_token(
    tmp_path: Path, monkeypatch
) -> None:
    from allthecontext.desktop_setup import authenticated_dashboard_url

    config = CoreConfig.in_directory(tmp_path / "core")
    observed: list[urllib.request.Request | str] = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"status":"ok","component":"core","proof":"forged"}'

    def fake_urlopen(request: urllib.request.Request | str, **_kwargs: object) -> FakeResponse:
        observed.append(request)
        return FakeResponse()

    monkeypatch.setattr("allthecontext.desktop_setup.urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="credential was not sent"):
        authenticated_dashboard_url(config, "administrator-secret")

    assert len(observed) == 1
    first = observed[0]
    assert isinstance(first, str)
    assert "administrator-secret" not in first


def test_dashboard_handoff_can_open_guided_connections_page(tmp_path: Path, monkeypatch) -> None:
    from allthecontext.desktop_setup import authenticated_dashboard_url

    config = replace(CoreConfig.in_directory(tmp_path / "core"), port=17_441)

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"connect_path":"/v1/browser/connect?ticket=one-use"}'

    monkeypatch.setattr(
        "allthecontext.desktop_setup.probe_core",
        lambda *_args, **_kwargs: CoreProbe.VERIFIED,
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.urllib.request.urlopen",
        lambda *_args, **_kwargs: FakeResponse(),
    )

    url = authenticated_dashboard_url(
        config,
        "administrator-secret",
        landing_page="connections",
    )

    assert url == ("http://127.0.0.1:17441/v1/browser/connect?ticket=one-use&page=connections")
    with pytest.raises(ValueError, match="recognized dashboard page"):
        authenticated_dashboard_url(config, "administrator-secret", landing_page="unsafe")


def test_named_client_is_not_reused_when_its_scopes_are_wrong(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.get", lambda *_: None)
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.set", lambda *_: None)
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.delete", lambda *_: None
    )

    over_scoped = ensure_client_access(
        store,
        config,
        name=CODEX_CLIENT_NAME,
        scopes=["*", "admin"],
    )
    scoped = ensure_client_access(
        store,
        config,
        name=CODEX_CLIENT_NAME,
        scopes=AI_CLIENT_SCOPES,
    )

    assert scoped.client_id != over_scoped.client_id
    principal = store.authenticate(scoped.token)
    assert principal is not None
    assert principal.scopes == frozenset(AI_CLIENT_SCOPES)


def test_existing_legacy_admin_config_is_repaired_and_rotated(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("ATC_CLAUDE_CONFIG", str(tmp_path / "claude.json"))
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.get", lambda *_: None)
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.set", lambda *_: None)
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.delete", lambda *_: None
    )

    desktop_access = ensure_client_access(
        store,
        config,
        name=DESKTOP_CLIENT_NAME,
        scopes=DESKTOP_SCOPES,
    )
    old_runtime = RuntimeCommand(
        tmp_path / "AllTheContext.exe",
        mcp_executable=tmp_path / "AllTheContextMCP.exe",
    )
    configure_codex(
        old_runtime,
        desktop_access.client_id,
        token=desktop_access.token,
        target_url=f"http://{config.host}:{config.port}",
    )
    versioned_helper = tmp_path / "AllTheContextMCP-new-build.exe"
    new_runtime = RuntimeCommand(tmp_path / "AllTheContext.exe", mcp_executable=versioned_helper)

    replacement = migrate_existing_integrations(new_runtime, config, desktop_access)

    parsed = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    managed = parsed["mcp_servers"]["all_the_context"]
    assert managed["command"] == str(versioned_helper)
    assert managed["env"]["ATC_CLIENT_ID"] not in {
        desktop_access.client_id,
        replacement.client_id,
    }
    assert managed["env"]["ATC_CLIENT_TOKEN"] not in {
        desktop_access.token,
        replacement.token,
    }
    mcp_principal = store.authenticate(managed["env"]["ATC_CLIENT_TOKEN"])
    assert mcp_principal is not None
    assert mcp_principal.scopes == frozenset(AI_CLIENT_SCOPES)
    assert replacement.client_id != desktop_access.client_id
    assert store.authenticate(desktop_access.token) is None
    assert store.authenticate(replacement.token) is not None
