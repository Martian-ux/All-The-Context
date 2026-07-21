from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from allthecontext.client_config import MANAGED_BEGIN, configure_codex
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
    assert "ATC_CLIENT_TOKEN" not in managed["env"]
    assert first.changed is True
    assert first.backup_path is not None and first.backup_path.is_file()

    second = configure_codex(runtime, "client-1", token=None, path=config)
    assert second.changed is False
    assert second.backup_path is None
    assert config.read_text(encoding="utf-8").count(MANAGED_BEGIN) == 1


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


def test_configure_codex_never_overwrites_invalid_toml(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = "[broken\n"
    config.write_text(original, encoding="utf-8")

    with pytest.raises(tomllib.TOMLDecodeError):
        configure_codex(RuntimeCommand(Path("python")), "client", token="secret", path=config)

    assert config.read_text(encoding="utf-8") == original
