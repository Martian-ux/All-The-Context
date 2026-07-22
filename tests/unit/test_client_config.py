from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from allthecontext.client_config import (
    MANAGED_BEGIN,
    apply_managed_client_cleanup,
    claude_is_configured,
    codex_is_configured,
    configure_claude,
    configure_codex,
    disconnect_claude,
    disconnect_codex,
    plan_managed_client_cleanup,
    read_claude_config,
    read_codex_config,
)
from allthecontext.desktop_runtime import RuntimeCommand


def test_configure_codex_preserves_config_and_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'model_reasoning_effort = "high"\n\n[mcp_servers.existing]\ncommand = "existing"\n',
        encoding="utf-8",
    )
    helper = tmp_path / "AllTheContextMCP.exe"
    runtime = RuntimeCommand(tmp_path / "AllTheContext.exe", mcp_executable=helper)

    first = configure_codex(runtime, "client-1", token=None, path=config)

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["model_reasoning_effort"] == "high"
    assert parsed["mcp_servers"]["existing"]["command"] == "existing"
    managed = parsed["mcp_servers"]["all_the_context"]
    assert managed["command"] == str(helper)
    assert managed["args"] == []
    assert managed["env"]["ATC_CLIENT_ID"] == "client-1"
    assert managed["env"]["ATC_AUTO_START_CORE"] == "1"
    assert json.loads(managed["env"]["ATC_CORE_COMMAND"]) == [
        str(runtime.executable),
        "--core",
    ]
    assert "ATC_CLIENT_TOKEN" not in managed["env"]
    assert first.changed is True
    assert first.backup_path is not None and first.backup_path.is_file()

    second = configure_codex(runtime, "client-1", token=None, path=config)
    assert second.changed is False
    assert second.backup_path is None
    assert config.read_text(encoding="utf-8").count(MANAGED_BEGIN) == 1
    assert codex_is_configured(config) is True


def test_configure_codex_replaces_an_existing_unmanaged_table(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.all_the_context]\ncommand = "old"\n\n[mcp_servers.other]\ncommand = "keep"\n',
        encoding="utf-8",
    )
    runtime = RuntimeCommand(Path("python"), ("-m", "allthecontext.desktop"))

    configure_codex(runtime, "client-2", token="secret", path=config)

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    managed = parsed["mcp_servers"]["all_the_context"]
    assert managed["command"] == "python"
    assert managed["args"] == ["-m", "allthecontext.desktop", "--mcp-stdio"]
    assert managed["env"]["ATC_CLIENT_TOKEN"] == "secret"
    assert parsed["mcp_servers"]["other"]["command"] == "keep"


def test_configure_codex_replaces_official_nested_env_table(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.all_the_context]\ncommand = "old"\nargs = []\n'
        '[mcp_servers.all_the_context.env]\nATC_CLIENT_ID = "old-client"\n'
        'ATC_TARGET_URL = "http://127.0.0.1:9999"\n\n'
        '[mcp_servers.other]\ncommand = "keep"\n',
        encoding="utf-8",
    )
    runtime = RuntimeCommand(Path("python"), ("-m", "allthecontext.desktop"))

    configure_codex(runtime, "new-client", token="secret", path=config)

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    managed = parsed["mcp_servers"]["all_the_context"]
    assert managed["command"] == "python"
    assert managed["env"]["ATC_CLIENT_ID"] == "new-client"
    assert parsed["mcp_servers"]["other"]["command"] == "keep"
    assert read_codex_config(config) is not None


def test_disconnect_codex_is_reversible_and_preserves_unrelated_tables(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        '[mcp_servers.all_the_context]\ncommand = "old"\n'
        '[mcp_servers.all_the_context.env]\nATC_CLIENT_ID = "old-client"\n\n'
        '[mcp_servers.other]\ncommand = "keep"\n',
        encoding="utf-8",
    )

    result = disconnect_codex(config)

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert "all_the_context" not in parsed["mcp_servers"]
    assert parsed["mcp_servers"]["other"]["command"] == "keep"
    assert result.changed is True
    assert result.backup_path is not None and result.backup_path.is_file()
    assert result.managed_client_id == "old-client"


def test_uninstall_cleanup_scrubs_current_config_and_existing_token_backups(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    original = (
        'theme = "dark"\n\n'
        "# BEGIN All The Context managed MCP\n"
        "[mcp_servers.all_the_context]\n"
        'command = "AllTheContextMCP.exe"\n'
        'env = { ATC_CLIENT_ID = "managed-client", ATC_CLIENT_TOKEN = "raw-token" }\n'
        "# END All The Context managed MCP\n"
    )
    config.write_text(original, encoding="utf-8")
    existing_backup = tmp_path / "config.toml.atc-backup-existing"
    existing_backup.write_text(original, encoding="utf-8")

    cleanup = plan_managed_client_cleanup(
        codex_path=config,
        claude_path=tmp_path / "missing-claude.json",
    )

    assert len(cleanup) == 2
    assert {item.managed_client_id for item in cleanup} == {"managed-client"}
    apply_managed_client_cleanup(cleanup)
    assert tomllib.loads(config.read_text(encoding="utf-8"))["theme"] == "dark"
    assert "raw-token" not in config.read_text(encoding="utf-8")
    assert "raw-token" not in existing_backup.read_text(encoding="utf-8")
    assert list(tmp_path.glob("config.toml.atc-backup-*")) == [existing_backup]


def test_configure_codex_never_overwrites_invalid_toml(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = "[broken\n"
    config.write_text(original, encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        configure_codex(RuntimeCommand(Path("python")), "client", token="secret", path=config)

    assert config.read_text(encoding="utf-8") == original


def test_configure_claude_preserves_other_servers_and_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps(
            {
                "theme": "system",
                "mcpServers": {"existing": {"command": "existing", "args": []}},
            }
        ),
        encoding="utf-8",
    )
    helper = tmp_path / "AllTheContextMCP.exe"
    runtime = RuntimeCommand(tmp_path / "AllTheContext.exe", mcp_executable=helper)

    first = configure_claude(runtime, "client-3", token="secret", path=config)

    parsed = json.loads(config.read_text(encoding="utf-8"))
    assert parsed["theme"] == "system"
    assert parsed["mcpServers"]["existing"]["command"] == "existing"
    managed = parsed["mcpServers"]["all-the-context"]
    assert managed["command"] == str(helper)
    assert managed["args"] == []
    assert managed["env"]["ATC_CLIENT_ID"] == "client-3"
    assert managed["env"]["ATC_CLIENT_TOKEN"] == "secret"
    assert managed["env"]["ATC_AUTO_START_CORE"] == "1"
    assert json.loads(managed["env"]["ATC_CORE_COMMAND"]) == [
        str(runtime.executable),
        "--core",
    ]
    assert first.backup_path is not None and first.backup_path.is_file()
    assert claude_is_configured(config) is True

    second = configure_claude(runtime, "client-3", token="secret", path=config)
    assert second.changed is False
    assert second.backup_path is None


def test_configure_claude_never_overwrites_invalid_json(tmp_path: Path) -> None:
    config = tmp_path / "claude_desktop_config.json"
    config.write_text("{broken", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        configure_claude(RuntimeCommand(Path("python")), "client", token="secret", path=config)

    assert config.read_text(encoding="utf-8") == "{broken"


def test_disconnect_claude_preserves_other_servers(tmp_path: Path) -> None:
    config = tmp_path / "claude_desktop_config.json"
    config.write_text(
        json.dumps(
            {
                "theme": "system",
                "mcpServers": {
                    "all-the-context": {
                        "command": "old",
                        "args": [],
                        "env": {"ATC_CLIENT_ID": "claude-client"},
                    },
                    "other": {"command": "keep", "args": []},
                },
            }
        ),
        encoding="utf-8",
    )

    result = disconnect_claude(config)

    parsed = json.loads(config.read_text(encoding="utf-8"))
    assert parsed["theme"] == "system"
    assert "all-the-context" not in parsed["mcpServers"]
    assert parsed["mcpServers"]["other"]["command"] == "keep"
    assert read_claude_config(config) is None
    assert result.changed is True
    assert result.managed_client_id == "claude-client"
