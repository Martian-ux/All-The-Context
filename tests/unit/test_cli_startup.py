from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from allthecontext import cli
from allthecontext.config import CoreConfig
from allthecontext.credentials import DEVELOPMENT_FALLBACK_ENV
from allthecontext.storage import CoreStore


def test_mcp_config_uses_keyring_or_explicit_token(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path)

    keyring_config = cli._render_mcp_config(config, "client-id")
    fallback_config = cli._render_mcp_config(config, "client-id", token="one-time-token")

    assert 'ATC_CLIENT_ID = "client-id"' in keyring_config
    assert "ATC_CLIENT_TOKEN" not in keyring_config
    assert 'ATC_CLIENT_TOKEN = "one-time-token"' in fallback_config
    assert 'args = ["-m", "allthecontext.mcp_adapter"]' in fallback_config


def test_init_prints_copyable_mcp_config(tmp_path: Path, capsys: object) -> None:
    args = argparse.Namespace(
        data_dir=str(tmp_path),
        name="Startup test",
        timezone="UTC",
        client_name="Startup client",
        no_keyring=True,
        json_only=False,
    )

    cli._cmd_init(args)
    output = capsys.readouterr().out  # type: ignore[attr-defined]

    assert "# Paste this block into your MCP client configuration" in output
    assert "[mcp_servers.all_the_context]" in output
    assert "ATC_CLIENT_ID" in output
    assert "ATC_CORE_DATA_DIR" in output
    assert "ATC_TARGET_URL" in output
    assert "open-dashboard" in output
    assert "atc" in output


def test_init_keyring_failure_does_not_create_plaintext_or_usable_orphan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(DEVELOPMENT_FALLBACK_ENV, raising=False)
    monkeypatch.setattr(
        cli.KeyringCredentialStore,
        "set",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("credential service unavailable")),
    )
    args = argparse.Namespace(
        data_dir=str(tmp_path),
        name="Fail closed",
        timezone="UTC",
        client_name="Failed administrator",
        no_keyring=False,
        json_only=True,
    )

    with pytest.raises(RuntimeError, match="credential service unavailable"):
        cli._cmd_init(args)

    output = capsys.readouterr()
    clients = CoreStore(CoreConfig.in_directory(tmp_path).database_path).list_clients()
    assert len(clients) == 1
    assert clients[0]["revoked"] is True
    assert not (tmp_path / "credentials.development.json").exists()
    assert "client_token" not in output.out
    assert output.err == ""


def test_open_dashboard_starts_core_and_uses_authenticated_handoff(
    tmp_path: Path, monkeypatch, capsys: object
) -> None:
    codex_config = tmp_path / "client-config" / "codex" / "config.toml"
    claude_config = tmp_path / "client-config" / "claude" / "claude_desktop_config.json"
    monkeypatch.setenv("CODEX_HOME", str(codex_config.parent))
    monkeypatch.setenv("ATC_CLAUDE_CONFIG", str(claude_config))
    init_args = argparse.Namespace(
        data_dir=str(tmp_path),
        name="Dashboard test",
        timezone=None,
        client_name="Dashboard administrator",
        no_keyring=True,
        json_only=True,
    )
    cli._cmd_init(init_args)
    capsys.readouterr()  # type: ignore[attr-defined]
    launched: list[CoreConfig] = []
    monkeypatch.setattr(
        cli,
        "launch_core",
        lambda _runtime, config: launched.append(config),
    )
    monkeypatch.setattr(
        cli,
        "authenticated_dashboard_url",
        lambda _config, _token: "http://127.0.0.1:7337/v1/browser/connect?ticket=safe",
    )

    cli._cmd_open_dashboard(argparse.Namespace(data_dir=str(tmp_path), print_only=True))

    output = capsys.readouterr().out  # type: ignore[attr-defined]
    assert launched
    assert output.strip().endswith("/v1/browser/connect?ticket=safe")
    assert not codex_config.exists()
    assert not claude_config.exists()
