from __future__ import annotations

import json
import threading
import time
import tomllib
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest
from allthecontext import claude_code_config, desktop_setup
from allthecontext import client_config as client_config_module
from allthecontext.capture_runtime import scheduler_config_path
from allthecontext.capture_scheduler import (
    CAPTURE_SCHEDULER_ENABLED_ENV,
    capture_scheduler_status_payload,
)
from allthecontext.client_config import configure_codex
from allthecontext.config import CoreConfig
from allthecontext.credentials import (
    DEVELOPMENT_FALLBACK_ENV,
    OS_CREDENTIAL_STORAGE,
    DevelopmentFileCredentialStore,
)
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.desktop_setup import (
    AI_CLIENT_SCOPES,
    CLAUDE_CODE_CLIENT_NAME,
    CLAUDE_CODE_EXPLICIT_CLIENT_NAME,
    CLAUDE_CODE_EXPLICIT_SCOPES,
    CLAUDE_CODE_SCOPES,
    CODEX_CAPTURE_CLIENT_NAME,
    CODEX_CLIENT_NAME,
    CODEX_EXPLICIT_CLIENT_NAME,
    CODEX_READ_SCOPES,
    DESKTOP_CLIENT_NAME,
    DESKTOP_SCOPES,
    MAX_CORE_PROBE_RESPONSE_BYTES,
    CoreProbe,
    SetupOptions,
    configure_client_access_transactionally,
    delete_client_credential,
    ensure_client_access,
    launch_core,
    migrate_existing_integrations,
    perform_setup,
    probe_core,
    recover_desktop_access,
    retire_other_named_clients,
)
from allthecontext.models import ClientCreate
from allthecontext.storage import CoreStore
from filelock import FileLock
from keyring.errors import KeyringError


def test_core_probe_rejects_an_oversized_untrusted_health_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read_sizes: list[int] = []

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return b"x" * size

    monkeypatch.setattr(
        "allthecontext.desktop_setup.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    assert probe_core(CoreConfig.in_directory(tmp_path)) is CoreProbe.UNVERIFIED
    assert read_sizes == [MAX_CORE_PROBE_RESPONSE_BYTES + 1]


def test_strict_core_probe_does_not_follow_a_redirect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        status = 302

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class Opener:
        def open(self, *_args: object, **_kwargs: object) -> Response:
            return Response()

    handlers: tuple[object, ...] = ()

    def build_opener(*provided: object) -> Opener:
        nonlocal handlers
        handlers = provided
        return Opener()

    monkeypatch.setattr("allthecontext.desktop_setup.urllib.request.build_opener", build_opener)

    assert (
        probe_core(
            CoreConfig.in_directory(tmp_path),
            ignore_environment_proxy=True,
        )
        is CoreProbe.UNVERIFIED
    )
    assert len(handlers) == 2
    assert isinstance(handlers[0], urllib.request.ProxyHandler)
    assert type(handlers[1]).__name__ == "_NoRedirectHandler"


def test_frozen_core_launch_uses_an_independent_pyinstaller_runtime(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv(CAPTURE_SCHEDULER_ENABLED_ENV, raising=False)
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
    assert environment[CAPTURE_SCHEDULER_ENABLED_ENV] == "1"
    assert not scheduler_config_path(config.data_dir).exists()

    monkeypatch.setenv(CAPTURE_SCHEDULER_ENABLED_ENV, "1")
    status = capture_scheduler_status_payload(config.data_dir)
    assert status["durable_enabled"] is False
    assert status["dispatch_allowed"] is False


def test_core_launch_waits_for_a_shutting_down_process_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(CoreConfig.in_directory(tmp_path / "core"), port=17_438)
    config.prepare()
    runtime = RuntimeCommand(tmp_path / "AllTheContext.exe")
    lock_ready = threading.Event()
    launched: list[tuple[str, ...]] = []

    def hold_previous_core_lock() -> None:
        with FileLock(str(config.lock_path), timeout=1):
            lock_ready.set()
            time.sleep(0.08)

    holder = threading.Thread(target=hold_previous_core_lock)
    holder.start()
    assert lock_ready.wait(timeout=1)

    class Process:
        pass

    def fake_popen(command: tuple[str, ...], **_kwargs: object) -> Process:
        launched.append(command)
        return Process()

    monkeypatch.setattr(
        "allthecontext.desktop_setup.probe_core",
        lambda _config: CoreProbe.VERIFIED if launched else CoreProbe.UNREACHABLE,
    )
    monkeypatch.setattr("allthecontext.desktop_setup.subprocess.Popen", fake_popen)

    launch_core(runtime, config, wait_seconds=0.5)
    holder.join(timeout=1)

    assert not holder.is_alive()
    assert launched == [runtime.core()]


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


def test_revoke_managed_clients_revokes_all_before_partial_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    managed = [
        store.create_client(ClientCreate(name=name, scopes=AI_CLIENT_SCOPES))[0]
        for name in (
            CODEX_CLIENT_NAME,
            CODEX_CAPTURE_CLIENT_NAME,
            CODEX_EXPLICIT_CLIENT_NAME,
        )
    ]
    unrelated, _token = store.create_client(
        ClientCreate(name="Unrelated integration", scopes=AI_CLIENT_SCOPES)
    )
    cleanup_attempts: list[str] = []
    failed_once = False

    def delete_with_one_failure(client_id: str, _config: CoreConfig) -> None:
        nonlocal failed_once
        cleanup_attempts.append(client_id)
        if client_id == managed[0].id and not failed_once:
            failed_once = True
            raise RuntimeError("synthetic credential cleanup failure")

    monkeypatch.setattr(desktop_setup, "delete_client_credential", delete_with_one_failure)

    with pytest.raises(RuntimeError, match="credential cleanup was incomplete"):
        desktop_setup.revoke_managed_clients(
            store,
            config,
            managed_client_ids=(client.id for client in managed),
            managed_names=(
                CODEX_CLIENT_NAME,
                CODEX_CAPTURE_CLIENT_NAME,
                CODEX_EXPLICIT_CLIENT_NAME,
            ),
        )

    rows = {row["id"]: row for row in store.list_clients()}
    assert all(rows[client.id]["revoked"] for client in managed)
    assert rows[unrelated.id]["revoked"] is False
    assert set(cleanup_attempts) == {client.id for client in managed}
    assert desktop_setup.revoke_managed_clients(
        store,
        config,
        managed_client_ids=(),
        managed_names=(CODEX_CLIENT_NAME, CODEX_CAPTURE_CLIENT_NAME, CODEX_EXPLICIT_CLIENT_NAME),
    ) == ()
    assert cleanup_attempts.count(managed[0].id) == 2


def test_null_keyring_without_explicit_fallback_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Packaged smoke isolation must not silently weaken production credential safety."""

    config = CoreConfig.in_directory(tmp_path / "null-keyring")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    monkeypatch.delenv(DEVELOPMENT_FALLBACK_ENV, raising=False)
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    with pytest.raises(RuntimeError, match="plaintext credential storage is disabled"):
        ensure_client_access(
            store,
            config,
            name=CODEX_CLIENT_NAME,
            scopes=AI_CLIENT_SCOPES,
        )

    clients = store.list_clients()
    assert len(clients) == 1
    assert clients[0]["revoked"] is True
    assert not (config.data_dir / "credentials.development.json").exists()


def test_null_keyring_with_explicit_fallback_uses_development_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Isolated non-secret smokes may opt into the development file deliberately."""

    config = CoreConfig.in_directory(tmp_path / "null-keyring-fallback")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    monkeypatch.setenv(DEVELOPMENT_FALLBACK_ENV, "1")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")

    access = ensure_client_access(
        store,
        config,
        name=CODEX_CLIENT_NAME,
        scopes=AI_CLIENT_SCOPES,
    )

    assert access.credential_storage == "insecure development credential file"
    credential_path = config.data_dir / "credentials.development.json"
    assert credential_path.is_file()
    payload = json.loads(credential_path.read_text(encoding="utf-8"))
    assert payload.get(f"client:{access.client_id}") == access.token


@pytest.mark.parametrize(
    ("platform_name", "backend_error"),
    [
        ("Windows", "Credential Manager is locked"),
        ("macOS", "Keychain interaction is not allowed"),
        ("Linux", "Secret Service collection is unavailable"),
    ],
)
def test_platform_credential_failure_fails_closed_and_revokes_new_principal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    platform_name: str,
    backend_error: str,
) -> None:
    config = CoreConfig.in_directory(tmp_path / platform_name)
    store = CoreStore(config.database_path)
    store.initialize_vault()
    secret_marker = "never-log-this-credential"
    monkeypatch.delenv(DEVELOPMENT_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(
        "allthecontext.credentials.keyring.get_password",
        lambda *_args: (_ for _ in ()).throw(KeyringError(f"{backend_error}: {secret_marker}")),
    )

    with pytest.raises(RuntimeError) as failure:
        ensure_client_access(
            store,
            config,
            name=CODEX_CLIENT_NAME,
            scopes=AI_CLIENT_SCOPES,
        )

    clients = store.list_clients()
    assert len(clients) == 1
    assert clients[0]["revoked"] is True
    assert not (config.data_dir / "credentials.development.json").exists()
    assert secret_marker not in str(failure.value)
    assert secret_marker not in caplog.text


@pytest.mark.parametrize("platform_name", ["Windows", "macOS", "Linux"])
def test_configuration_failure_removes_new_credential_and_restores_prior_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform_name: str,
) -> None:
    config = CoreConfig.in_directory(tmp_path / platform_name / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    client_config = tmp_path / platform_name / "config.toml"
    client_config.parent.mkdir(parents=True, exist_ok=True)
    original = 'model = "keep-me"\n'
    client_config.write_text(original, encoding="utf-8")
    credentials: dict[str, str] = {}
    monkeypatch.delenv(DEVELOPMENT_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(
        "allthecontext.credentials.keyring.get_password",
        lambda _service, name: credentials.get(name),
    )
    monkeypatch.setattr(
        "allthecontext.credentials.keyring.set_password",
        lambda _service, name, value: credentials.__setitem__(name, value),
    )
    monkeypatch.setattr(
        "allthecontext.credentials.keyring.delete_password",
        lambda _service, name: credentials.pop(name, None),
    )
    real_atomic_write = client_config_module._atomic_write
    attempts = 0

    def fail_after_replace(path: Path, content: str) -> None:
        nonlocal attempts
        attempts += 1
        real_atomic_write(path, content)
        if attempts == 1:
            raise OSError(f"{platform_name} configuration fault")

    monkeypatch.setattr(client_config_module, "_atomic_write", fail_after_replace)

    with pytest.raises(OSError, match="configuration fault"):
        configure_client_access_transactionally(
            store,
            config,
            name=CODEX_CLIENT_NAME,
            scopes=AI_CLIENT_SCOPES,
            configure=lambda access: configure_codex(
                RuntimeCommand(Path("python")),
                access.client_id,
                token=None,
                path=client_config,
            ),
        )

    clients = store.list_clients()
    assert len(clients) == 1
    assert clients[0]["revoked"] is True
    assert credentials == {}
    assert client_config.read_text(encoding="utf-8") == original


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

    assert result.credential_storage == "insecure development credential file"
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


def test_setup_connects_claude_code_with_exact_read_only_principal_and_managed_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(CoreConfig.in_directory(tmp_path / "core"), port=17_442)
    mcp_path = tmp_path / "user" / ".claude.json"
    settings_path = tmp_path / "user" / ".claude" / "settings.json"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("ATC_CLAUDE_CODE_MCP_CONFIG", str(mcp_path))
    monkeypatch.setenv("ATC_CLAUDE_CODE_SETTINGS", str(settings_path))
    desktop_config = tmp_path / "desktop-claude.json"
    monkeypatch.setenv("ATC_CLAUDE_CONFIG", str(desktop_config))

    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.get", lambda *_: None)
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.set", lambda *_: None)
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.delete", lambda *_: None
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.launch_core",
        lambda _runtime, _config: config.data_dir / "logs" / "core.log",
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.authenticated_dashboard_url",
        lambda active_config, _token: (
            f"http://{active_config.host}:{active_config.port}/v1/browser/connect"
        ),
    )
    runtime = RuntimeCommand(Path("python"), ("-m", "allthecontext.desktop"))

    result = perform_setup(
        SetupOptions(
            configure_codex=False,
            configure_claude=False,
            configure_claude_code=True,
            start_at_login=False,
        ),
        runtime,
        config=config,
    )

    assert result.claude is None
    assert result.claude_code is not None
    managed = json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"][
        "all-the-context-claude-code"
    ]
    assert set(managed["env"]) == {
        "ATC_MCP_PROFILE",
        "ATC_TARGET_URL",
        "ATC_CLIENT_ID",
        "ATC_CLIENT_TOKEN",
        "ATC_AUTO_START_CORE",
        "ATC_CORE_COMMAND",
        "ATC_CORE_DATA_DIR",
    }
    assert managed["env"]["ATC_MCP_PROFILE"] == "claude_code_read"
    assert managed["env"]["ATC_CLIENT_TOKEN"]
    assert managed["env"]["ATC_AUTO_START_CORE"] == "1"
    assert managed["env"]["ATC_CORE_COMMAND"] == json.dumps(runtime.core(), ensure_ascii=False)
    assert managed["env"]["ATC_CORE_DATA_DIR"] == str(config.data_dir)
    assert not desktop_config.exists()
    assert not (project / ".claude").exists()
    assert not (settings_path.parent / "commands").exists()

    clients = [
        item
        for item in CoreStore(config.database_path).list_clients()
        if item["name"] == CLAUDE_CODE_CLIENT_NAME
    ]
    assert len(clients) == 1
    assert clients[0]["scopes"] == CLAUDE_CODE_SCOPES

    repeated = perform_setup(
        SetupOptions(
            configure_codex=False,
            configure_claude=False,
            configure_claude_code=True,
            start_at_login=False,
        ),
        runtime,
        config=config,
    )
    assert repeated.claude_code is not None
    assert repeated.claude_code.changed is False
    repeated_clients = [
        item
        for item in CoreStore(config.database_path).list_clients()
        if item["name"] == CLAUDE_CODE_CLIENT_NAME and not item["revoked"]
    ]
    assert [item["id"] for item in repeated_clients] == [clients[0]["id"]]


def test_setup_keeps_read_principal_and_opt_in_explicit_principal_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(CoreConfig.in_directory(tmp_path / "core"), port=17_444)
    mcp_path = tmp_path / "user" / ".claude.json"
    settings_path = tmp_path / "user" / ".claude" / "settings.json"
    monkeypatch.setenv("ATC_CLAUDE_CODE_MCP_CONFIG", str(mcp_path))
    monkeypatch.setenv("ATC_CLAUDE_CODE_SETTINGS", str(settings_path))
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.get", lambda *_: None)
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.set", lambda *_: None)
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.delete", lambda *_: None
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.launch_core",
        lambda _runtime, _config: config.data_dir / "logs" / "core.log",
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.authenticated_dashboard_url",
        lambda active_config, _token: (
            f"http://{active_config.host}:{active_config.port}/v1/browser/connect"
        ),
    )

    result = perform_setup(
        SetupOptions(
            configure_codex=False,
            configure_claude=False,
            configure_claude_code=True,
            configure_claude_code_explicit_commands=True,
            start_at_login=False,
        ),
        RuntimeCommand(Path("python"), ("-m", "allthecontext.desktop")),
        config=config,
    )

    assert result.claude_code is not None
    assert result.claude_code_explicit is not None
    clients = {
        item["name"]: item
        for item in CoreStore(config.database_path).list_clients()
        if not item["revoked"]
    }
    assert clients[CLAUDE_CODE_CLIENT_NAME]["scopes"] == CLAUDE_CODE_SCOPES
    assert clients[CLAUDE_CODE_EXPLICIT_CLIENT_NAME]["scopes"] == CLAUDE_CODE_EXPLICIT_SCOPES
    assert json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"].keys() >= {
        "all-the-context-claude-code",
        "all-the-context-claude-code-explicit",
    }
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert "UserPromptSubmit" in settings["hooks"]
    assert "UserPromptExpansion" in settings["hooks"]
    skills_dir = settings_path.parent / "skills"
    assert all(
        (skills_dir / name / "SKILL.md").exists()
        for name in (
            "atc-remember",
            "atc-correct",
            "atc-forget",
        )
    )


def test_claude_code_configuration_failure_restores_both_files_and_cleans_principal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = replace(CoreConfig.in_directory(tmp_path / "core"), port=17_443)
    mcp_path = tmp_path / "user" / ".claude.json"
    settings_path = tmp_path / "user" / "settings.json"
    mcp_original = '{"custom": true}\n'
    settings_original = '{"hooks": {"UserPromptSubmit": []}}\n'
    mcp_path.parent.mkdir(parents=True)
    mcp_path.write_text(mcp_original, encoding="utf-8")
    settings_path.write_text(settings_original, encoding="utf-8")
    monkeypatch.setenv("ATC_CLAUDE_CODE_MCP_CONFIG", str(mcp_path))
    monkeypatch.setenv("ATC_CLAUDE_CODE_SETTINGS", str(settings_path))
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.get", lambda *_: None)
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.set", lambda *_: None)
    monkeypatch.setattr(
        "allthecontext.desktop_setup.KeyringCredentialStore.delete", lambda *_: None
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.launch_core",
        lambda _runtime, _config: config.data_dir / "logs" / "core.log",
    )
    monkeypatch.setattr(
        "allthecontext.desktop_setup.authenticated_dashboard_url",
        lambda _config, _token: "http://127.0.0.1:17443/v1/browser/connect",
    )
    real_atomic_write = claude_code_config._atomic_write
    attempts = 0

    def fail_first_write(path: Path, content: str) -> None:
        nonlocal attempts
        attempts += 1
        real_atomic_write(path, content)
        if attempts == 1:
            raise OSError("injected Claude Code write fault")

    monkeypatch.setattr(claude_code_config, "_atomic_write", fail_first_write)
    result = perform_setup(
        SetupOptions(
            configure_codex=False,
            configure_claude=False,
            configure_claude_code=True,
            start_at_login=False,
        ),
        RuntimeCommand(Path("python"), ("-m", "allthecontext.desktop")),
        config=config,
    )

    assert result.claude_code is None
    assert mcp_path.read_text(encoding="utf-8") == mcp_original
    assert settings_path.read_text(encoding="utf-8") == settings_original
    assert list(tmp_path.rglob("*.atc-backup-*")) == []
    clients = [
        item
        for item in CoreStore(config.database_path).list_clients()
        if item["name"] == CLAUDE_CODE_CLIENT_NAME
    ]
    assert len(clients) == 1
    assert clients[0]["revoked"] is True
    fallback = DevelopmentFileCredentialStore(config.data_dir / "credentials.development.json")
    assert fallback.get(f"client:{clients[0]['id']}") is None


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

        def read(self, _size: int = -1) -> bytes:
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
    assert managed["env"]["ATC_CORE_DATA_DIR"] == str(config.data_dir)
    mcp_principal = store.authenticate(managed["env"]["ATC_CLIENT_TOKEN"])
    assert mcp_principal is not None
    assert mcp_principal.scopes == frozenset(CODEX_READ_SCOPES)
    assert replacement.client_id != desktop_access.client_id
    assert store.authenticate(desktop_access.token) is None
    assert store.authenticate(replacement.token) is not None
