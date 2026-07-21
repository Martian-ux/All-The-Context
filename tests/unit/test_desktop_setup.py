from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

from allthecontext.config import CoreConfig
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.desktop_setup import SetupOptions, perform_setup, recover_desktop_access


def test_setup_initializes_recoverable_access_and_codex(tmp_path: Path, monkeypatch) -> None:
    config = replace(CoreConfig.in_directory(tmp_path / "core"), port=17_440)
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    def empty_get(*_args: object, **_kwargs: object) -> None:
        return None

    def ignored_set(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.get", empty_get)
    monkeypatch.setattr("allthecontext.desktop_setup.KeyringCredentialStore.set", ignored_set)
    log_path = config.data_dir / "logs" / "core.log"
    monkeypatch.setattr(
        "allthecontext.desktop_setup.launch_core",
        lambda _runtime, _config: log_path,
    )
    runtime = RuntimeCommand(Path("python"), ("-m", "allthecontext.desktop"))

    result = perform_setup(
        SetupOptions(configure_codex=True, start_at_login=False),
        runtime,
        config=config,
    )

    assert result.credential_storage == "local app-data fallback"
    assert result.warnings
    assert result.dashboard_url.startswith("http://127.0.0.1:17440/#atc_token=")
    access = recover_desktop_access(config)
    assert access is not None
    assert access.client_id == result.client_id
    parsed = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    managed = parsed["mcp_servers"]["all_the_context"]
    assert managed["env"]["ATC_CLIENT_ID"] == result.client_id
    assert managed["env"]["ATC_CLIENT_TOKEN"] == access.token
    assert managed["env"]["ATC_TARGET_URL"] == "http://127.0.0.1:17440"

    repeated = perform_setup(
        SetupOptions(configure_codex=False, start_at_login=False),
        runtime,
        config=config,
    )
    assert repeated.client_id == result.client_id
