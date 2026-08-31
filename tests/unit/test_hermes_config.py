import json
from pathlib import Path

import pytest
from allthecontext.config import CoreConfig
from allthecontext.credentials import FALLBACK_CREDENTIAL_STORAGE, OS_CREDENTIAL_STORAGE
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.desktop_setup import (
    HERMES_CAPTURE_CLIENT_NAME,
    DesktopAccess,
    SetupOptions,
    perform_setup,
)
from allthecontext.hermes_config import (
    HERMES_CAPTURE_HOOK_EVENT,
    HERMES_READ_HOOK_EVENT,
    HermesConfigError,
    configure_hermes,
    disconnect_hermes,
    hermes_is_detected,
    resolve_hermes_profile,
)


def _runtime() -> RuntimeCommand:
    return RuntimeCommand(Path("C:/ATC/all-the-context.exe"), ("--base",))


def _configure(
    config_path: Path,
    *,
    profile: str = "work",
    capture: bool = True,
    **kwargs: object,
):
    read_storage = kwargs.pop("read_credential_storage", OS_CREDENTIAL_STORAGE)
    capture_storage = kwargs.pop(
        "capture_credential_storage", OS_CREDENTIAL_STORAGE if capture else None
    )
    return configure_hermes(
        _runtime(),
        read_client_id="read-client",
        capture_client_id="capture-client" if capture else None,
        profile=profile,
        config_path=config_path,
        target_url="http://127.0.0.1:7337",
        core_data_dir=config_path.parent / "core-data",
        read_credential_storage=read_storage,
        capture_credential_storage=capture_storage,
        **kwargs,
    )


def test_named_profile_isolated_and_install_is_idempotent(tmp_path: Path) -> None:
    default_config = tmp_path / "config.yaml"
    default_config.write_text("model: default\n", encoding="utf-8")
    selected = tmp_path / "profiles" / "work" / "config.yaml"
    selected.parent.mkdir(parents=True)
    original = "model: work\r\n# retain this byte\r\n"
    selected.write_bytes(original.encode("utf-8"))

    first = _configure(selected)
    configured = selected.read_bytes().decode("utf-8")
    allowlist = first.allowlist_path.read_text(encoding="utf-8")
    assert first.changed is True
    assert first.restart_required is True
    assert first.hook_consent_authorized is True
    assert "model: work\r\n# retain this byte\r\n" in configured
    assert "read-client" in configured
    assert "capture-client" not in configured.split("hooks:", 1)[0]
    assert "ATC_CLIENT_TOKEN" not in configured
    assert "secret" not in configured
    assert "--accept-hooks" not in configured
    assert '"event": "pre_llm_call"' in allowlist
    assert '"event": "post_llm_call"' in allowlist
    assert '"ATC_CLIENT_ID":' not in allowlist
    assert default_config.read_text(encoding="utf-8") == "model: default\n"

    second = _configure(selected)
    assert second.changed is False
    assert second.restart_required is False
    assert second.hook_consent_authorized is False
    assert selected.read_bytes().decode("utf-8") == configured
    assert first.allowlist_path.read_text(encoding="utf-8") == allowlist


def test_allowlist_preserves_unrelated_approvals_and_disconnects_only_atc(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    allowlist = config.with_name("shell-hooks-allowlist.json")
    config.write_text("model: keep\n", encoding="utf-8")
    unrelated = {"event": "post_llm_call", "command": "echo user-hook"}
    allowlist.write_text(
        json.dumps({"approvals": [unrelated], "other": {"keep": True}}) + "\n",
        encoding="utf-8",
    )
    result = _configure(config, capture=False)
    approvals = json.loads(result.allowlist_path.read_text(encoding="utf-8"))["approvals"]
    assert unrelated in approvals
    assert sum(item["event"] == HERMES_READ_HOOK_EVENT for item in approvals) == 1
    assert not any(
        item["event"] == HERMES_CAPTURE_HOOK_EVENT and item["command"] != unrelated["command"]
        for item in approvals
    )

    disconnected = disconnect_hermes(profile="work", config_path=config)
    assert disconnected.changed is True
    assert config.read_text(encoding="utf-8") == "model: keep\n"
    remaining = json.loads(allowlist.read_text(encoding="utf-8"))
    assert remaining["approvals"] == [unrelated]
    assert remaining["other"] == {"keep": True}


def test_malformed_config_fails_closed_without_creating_allowlist(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    original = "mcp_servers: []\nmodel: keep\n"
    config.write_text(original, encoding="utf-8")
    with pytest.raises(HermesConfigError):
        _configure(config, capture=False)
    assert config.read_text(encoding="utf-8") == original
    assert not config.with_name("shell-hooks-allowlist.json").exists()


def test_two_file_write_rolls_back_when_allowlist_write_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import allthecontext.hermes_config as hermes_config

    config = tmp_path / "config.yaml"
    calls = 0
    original_write = hermes_config._atomic_write

    def fail_second(path: Path, content: str, *, mode: int | None = None) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated allowlist failure")
        original_write(path, content, mode=mode)

    monkeypatch.setattr(hermes_config, "_atomic_write", fail_second)
    with pytest.raises(OSError):
        _configure(config, capture=False)
    assert not config.exists()
    assert not config.with_name("shell-hooks-allowlist.json").exists()
    assert not list(tmp_path.glob("*.atc-backup-*"))


def test_credential_fallback_is_rejected_before_file_changes(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    config.write_text("model: keep\n", encoding="utf-8")
    with pytest.raises(HermesConfigError):
        _configure(
            config,
            capture=False,
            read_credential_storage=FALLBACK_CREDENTIAL_STORAGE,
        )
    assert config.read_text(encoding="utf-8") == "model: keep\n"
    assert not config.with_name("shell-hooks-allowlist.json").exists()


def test_omitted_credential_storage_is_rejected_before_file_changes(tmp_path: Path) -> None:
    config = tmp_path / "config.yaml"
    original = "model: keep\n"
    config.write_text(original, encoding="utf-8")
    with pytest.raises(HermesConfigError):
        configure_hermes(
            _runtime(),
            read_client_id="read-client",
            config_path=config,
        )
    with pytest.raises(HermesConfigError):
        configure_hermes(
            _runtime(),
            read_client_id="read-client",
            capture_client_id="capture-client",
            read_credential_storage=OS_CREDENTIAL_STORAGE,
            config_path=config,
        )
    assert config.read_text(encoding="utf-8") == original
    assert not config.with_name("shell-hooks-allowlist.json").exists()


def test_setup_reports_hermes_capture_principal_and_no_startup_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import allthecontext.desktop_setup as desktop_setup

    config = CoreConfig.in_directory(tmp_path / "core")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    monkeypatch.setattr(
        desktop_setup,
        "_persist_client_token",
        lambda client_id, token, _config: DesktopAccess(
            client_id, token, OS_CREDENTIAL_STORAGE
        ),
    )
    monkeypatch.setattr(
        desktop_setup,
        "launch_core",
        lambda _runtime, active_config: active_config.data_dir / "logs" / "core.log",
    )
    monkeypatch.setattr(
        desktop_setup,
        "authenticated_dashboard_url",
        lambda active_config, _token: f"http://{active_config.host}:{active_config.port}/dashboard",
    )
    result = perform_setup(
        SetupOptions(
            configure_codex=False,
            configure_claude=False,
            configure_hermes=True,
            configure_hermes_continuous_capture=True,
            hermes_profile="named",
            start_at_login=False,
        ),
        _runtime(),
        config=config,
    )
    assert result.hermes is not None
    assert result.hermes.profile == "named"
    assert result.continuous_capture_clients == (HERMES_CAPTURE_CLIENT_NAME,)
    assert result.startup is None
    clients = {
        item["name"]: item
        for item in desktop_setup.CoreStore(config.database_path).list_clients()
        if not item["revoked"]
    }
    assert clients["Hermes"]["scopes"] == ["context:read"]
    assert clients[HERMES_CAPTURE_CLIENT_NAME]["scopes"] == ["context:capture"]


def test_profile_resolution_and_absence_detection_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import allthecontext.hermes_config as hermes_config

    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert hermes_config._user_data_root(
        platform_name="posix", home=tmp_path
    ) == tmp_path / ".hermes"
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    assert hermes_config._user_data_root(platform_name="win32", home=tmp_path) == (
        tmp_path / "LocalAppData" / "hermes"
    )

    root = tmp_path / "hermes"
    (root).mkdir()
    (root / "profiles" / "named").mkdir(parents=True)
    (root / "active_profile").write_text("named\n", encoding="utf-8")
    assert hermes_config.active_hermes_profile(root=root) == "named"
    assert resolve_hermes_profile(root=root).name == "named"
    selected = resolve_hermes_profile("named", root=root)
    assert selected.name == "named"
    assert selected.config_path == root / "profiles" / "named" / "config.yaml"

    monkeypatch.setenv("HERMES_HOME", str(root))
    monkeypatch.setattr("allthecontext.hermes_config.shutil.which", lambda _name: None)
    assert hermes_is_detected() is False
    (root / "profiles" / "named" / "config.yaml").write_text("model: x\n", encoding="utf-8")
    assert hermes_is_detected() is True
    monkeypatch.setenv("HERMES_HOME", str(root / "profiles" / "named"))
    assert hermes_config._user_data_root() == root
    assert hermes_config.hermes_home("named") == root / "profiles" / "named"
