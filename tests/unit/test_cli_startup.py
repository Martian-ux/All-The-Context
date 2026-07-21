from __future__ import annotations

import argparse
from pathlib import Path

from allthecontext import cli
from allthecontext.config import CoreConfig


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
    assert "ATC_CLIENT_TOKEN" in output
    assert "serve-core" in output
    assert "atc" in output
