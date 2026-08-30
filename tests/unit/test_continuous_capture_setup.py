"""Focused setup/configuration tests for client continuous capture opt-in."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import anyio
import pytest
from allthecontext import client_config, desktop_setup
from allthecontext.claude_code_config import (
    CLAUDE_CODE_CAPTURE_MCP_PROFILE,
    CLAUDE_CODE_CAPTURE_MCP_SERVER_KEY,
    CLAUDE_CODE_CAPTURE_STOP_HOOK_TOOL,
    CLAUDE_CODE_CAPTURE_USER_PROMPT_HOOK_TOOL,
    CLAUDE_CODE_HOOK_TOOL,
    CLAUDE_CODE_MCP_SERVER_KEY,
    configure_claude_code,
    disconnect_claude_code,
)
from allthecontext.client_config import (
    CODEX_CAPTURE_PROFILE,
    CODEX_CAPTURE_SERVER_KEY,
    CODEX_CAPTURE_STOP_TOOL,
    CODEX_CAPTURE_USER_PROMPT_TOOL,
    CODEX_EXPLICIT_PROFILE,
    CODEX_EXPLICIT_SERVER_KEY,
    CODEX_READ_HOOK_TOOL,
    CODEX_READ_PROFILE,
    CODEX_READ_SERVER_KEY,
    configure_codex_integration,
    disconnect_codex_integration,
)
from allthecontext.config import CoreConfig
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.mcp_adapter import _server_for_profile
from allthecontext.models import ClientCreate
from allthecontext.storage import CoreStore


def _runtime(tmp_path: Path) -> RuntimeCommand:
    return RuntimeCommand(
        tmp_path / "AllTheContext.exe",
        mcp_executable=tmp_path / "AllTheContextMCP.exe",
    )


def _hook_handlers(parsed: dict[str, object], event_name: str) -> list[dict[str, object]]:
    hooks = parsed["hooks"]
    assert isinstance(hooks, dict)
    groups = hooks[event_name]
    assert isinstance(groups, list)
    handlers: list[dict[str, object]] = []
    for group in groups:
        assert isinstance(group, dict)
        values = group["hooks"]
        assert isinstance(values, list)
        handlers.extend(value for value in values if isinstance(value, dict))
    return handlers


def _runtime_tool_names() -> set[str]:
    return {tool.name for tool in anyio.run(_server_for_profile().list_tools)}


def test_generated_codex_profiles_exist_with_closed_runtime_tools(monkeypatch) -> None:
    monkeypatch.setenv("ATC_MCP_PROFILE", CODEX_READ_PROFILE)
    assert _runtime_tool_names() == {
        "bootstrap_context",
        CODEX_READ_HOOK_TOOL,
        "search_context",
        "get_context_item",
        "context_status",
    }

    monkeypatch.setenv("ATC_MCP_PROFILE", CODEX_EXPLICIT_PROFILE)
    assert _runtime_tool_names() == {
        "propose_memory",
        "report_context_error",
        "forget_context",
    }


def test_codex_default_is_read_only_and_preserves_unrelated_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text('[profiles.keep]\nmodel = "user-model"\n', encoding="utf-8")

    result = configure_codex_integration(
        _runtime(tmp_path),
        read_client_id="read-client",
        path=config_path,
        hooks_path=tmp_path / "hooks.json",
        skills_dir=tmp_path / "skills",
    )

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    servers = parsed["mcp_servers"]
    assert isinstance(servers, dict)
    assert set(servers) == {CODEX_READ_SERVER_KEY}
    assert servers[CODEX_READ_SERVER_KEY]["env"]["ATC_MCP_PROFILE"] == CODEX_READ_PROFILE
    assert parsed["profiles"]["keep"]["model"] == "user-model"
    handlers = _hook_handlers(parsed, "UserPromptSubmit")
    assert len(handlers) == 1
    assert handlers[0]["server"] == CODEX_READ_SERVER_KEY
    assert handlers[0]["tool"] == CODEX_READ_HOOK_TOOL
    assert "Stop" not in parsed["hooks"]
    raw = config_path.read_text(encoding="utf-8")
    assert all(field not in raw for field in ("cwd", "caller", "provenance", "sensitivity"))
    assert result.managed_client_ids == ("read-client",)


def test_codex_opt_in_has_exact_scopes_hooks_approvals_and_explicit_skills(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    skills_dir = tmp_path / "skills"
    result = configure_codex_integration(
        _runtime(tmp_path),
        read_client_id="read-client",
        capture_client_id="capture-client",
        explicit_client_id="explicit-client",
        path=config_path,
        hooks_path=tmp_path / "hooks.json",
        skills_dir=skills_dir,
    )

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    servers = parsed["mcp_servers"]
    assert isinstance(servers, dict)
    assert set(servers) == {
        CODEX_READ_SERVER_KEY,
        CODEX_CAPTURE_SERVER_KEY,
        CODEX_EXPLICIT_SERVER_KEY,
    }
    assert servers[CODEX_CAPTURE_SERVER_KEY]["env"]["ATC_MCP_PROFILE"] == CODEX_CAPTURE_PROFILE
    assert servers[CODEX_CAPTURE_SERVER_KEY]["enabled_tools"] == [
        CODEX_CAPTURE_USER_PROMPT_TOOL,
        CODEX_CAPTURE_STOP_TOOL,
    ]
    explicit = servers[CODEX_EXPLICIT_SERVER_KEY]
    assert explicit["env"]["ATC_MCP_PROFILE"] == CODEX_EXPLICIT_PROFILE
    assert explicit["default_tools_approval_mode"] == "approve"
    assert all(
        explicit["tools"][tool]["approval_mode"] == "prompt"
        for tool in ("propose_memory", "report_context_error", "forget_context")
    )

    user_prompt = _hook_handlers(parsed, "UserPromptSubmit")
    stop = _hook_handlers(parsed, "Stop")
    assert {handler["server"] for handler in user_prompt} == {
        CODEX_READ_SERVER_KEY,
        CODEX_CAPTURE_SERVER_KEY,
    }
    capture_handler = next(
        handler for handler in user_prompt if handler["server"] == CODEX_CAPTURE_SERVER_KEY
    )
    assert capture_handler["tool"] == CODEX_CAPTURE_USER_PROMPT_TOOL
    assert set(capture_handler["input"]) == {"prompt", "session_id", "turn_id"}
    capture_stop = next(
        handler for handler in stop if handler["server"] == CODEX_CAPTURE_SERVER_KEY
    )
    assert capture_stop["tool"] == CODEX_CAPTURE_STOP_TOOL
    assert set(capture_stop["input"]) == {
        "last_assistant_message",
        "session_id",
        "turn_id",
    }
    serialized = config_path.read_text(encoding="utf-8")
    assert "event_name" not in serialized
    for name in ("atc-remember", "atc-correct", "atc-forget"):
        assert (skills_dir / name / "SKILL.md").is_file()
        assert (skills_dir / name / "agents" / "openai.yaml").read_text(encoding="utf-8") == (
            "policy:\n  allow_implicit_invocation: false\n"
        )
    assert result.managed_client_ids == (
        "read-client",
        "capture-client",
        "explicit-client",
    )

    repeated = configure_codex_integration(
        _runtime(tmp_path),
        read_client_id="read-client",
        capture_client_id="capture-client",
        explicit_client_id="explicit-client",
        path=config_path,
        hooks_path=tmp_path / "hooks.json",
        skills_dir=skills_dir,
    )
    assert repeated.changed is False
    assert repeated.backup_path is None


def test_codex_disconnect_preserves_unrelated_config_and_skills(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    hooks_path = tmp_path / "hooks.json"
    skills_dir = tmp_path / "skills"
    config_path.write_text('[profiles.keep]\nmodel = "user-model"\n', encoding="utf-8")
    hooks_path.write_text(json.dumps({"description": "keep this"}) + "\n", encoding="utf-8")
    configure_codex_integration(
        _runtime(tmp_path),
        read_client_id="read-client",
        capture_client_id="capture-client",
        explicit_client_id="explicit-client",
        path=config_path,
        hooks_path=hooks_path,
        skills_dir=skills_dir,
    )
    unrelated_skill = skills_dir / "user-skill" / "SKILL.md"
    unrelated_skill.parent.mkdir(parents=True)
    unrelated_skill.write_text("user-owned", encoding="utf-8")
    result = disconnect_codex_integration(
        path=config_path,
        hooks_path=hooks_path,
        skills_dir=skills_dir,
    )

    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "mcp_servers" not in parsed
    assert parsed["profiles"]["keep"]["model"] == "user-model"
    hooks = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert hooks == {"description": "keep this"}
    assert unrelated_skill.read_text(encoding="utf-8") == "user-owned"
    assert result.managed_client_ids == (
        "read-client",
        "capture-client",
        "explicit-client",
    )


def test_codex_multi_file_write_rolls_back_on_second_write(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"
    hooks_path = tmp_path / "hooks.json"
    config_original = '[profiles.keep]\nmodel = "user-model"\n'
    hooks_original = '{"description": "keep"}\n'
    config_path.write_text(config_original, encoding="utf-8")
    hooks_path.write_text(hooks_original, encoding="utf-8")
    real_write = client_config._codex_atomic_write
    calls = 0

    def fail_second(path: Path, content: str) -> None:
        nonlocal calls
        calls += 1
        real_write(path, content)
        if calls == 2:
            raise OSError("injected Codex write fault")

    monkeypatch.setattr(client_config, "_codex_atomic_write", fail_second)
    with pytest.raises(OSError, match="injected Codex write fault"):
        configure_codex_integration(
            _runtime(tmp_path),
            read_client_id="read-client",
            capture_client_id="capture-client",
            path=config_path,
            hooks_path=hooks_path,
        )
    assert config_path.read_text(encoding="utf-8") == config_original
    assert hooks_path.read_text(encoding="utf-8") == hooks_original
    assert list(tmp_path.glob("*.atc-backup-*")) == []


def test_claude_code_opt_in_adds_capture_only_hooks_and_keeps_opt_out_read_only(
    tmp_path: Path,
) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"
    mcp_path.write_text(json.dumps({"custom": {"keep": True}}) + "\n", encoding="utf-8")
    settings_path.write_text(json.dumps({"permissions": {"keep": True}}) + "\n", encoding="utf-8")

    configure_claude_code(
        _runtime(tmp_path),
        "read-client",
        mcp_path=mcp_path,
        settings_path=settings_path,
    )
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert set(mcp["mcpServers"]) == {CLAUDE_CODE_MCP_SERVER_KEY}
    assert settings["hooks"].keys() == {"UserPromptSubmit"}
    assert settings["hooks"]["UserPromptSubmit"][-1]["hooks"][0]["tool"] == CLAUDE_CODE_HOOK_TOOL
    assert "cwd" in settings["hooks"]["UserPromptSubmit"][-1]["hooks"][0]["input"]

    configure_claude_code(
        _runtime(tmp_path),
        "read-client",
        capture_client_id="capture-client",
        mcp_path=mcp_path,
        settings_path=settings_path,
    )
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert set(mcp["mcpServers"]) == {
        CLAUDE_CODE_MCP_SERVER_KEY,
        CLAUDE_CODE_CAPTURE_MCP_SERVER_KEY,
    }
    assert (
        mcp["mcpServers"][CLAUDE_CODE_CAPTURE_MCP_SERVER_KEY]["env"]["ATC_MCP_PROFILE"]
        == CLAUDE_CODE_CAPTURE_MCP_PROFILE
    )
    user_handlers = settings["hooks"]["UserPromptSubmit"]
    capture_user = next(
        group["hooks"][0]
        for group in user_handlers
        if group["hooks"][0]["server"] == CLAUDE_CODE_CAPTURE_MCP_SERVER_KEY
    )
    assert capture_user["tool"] == CLAUDE_CODE_CAPTURE_USER_PROMPT_HOOK_TOOL
    assert set(capture_user["input"]) == {"prompt", "session_id", "cwd"}
    stop = next(
        group["hooks"][0]
        for group in settings["hooks"]["Stop"]
        if group["hooks"][0]["server"] == CLAUDE_CODE_CAPTURE_MCP_SERVER_KEY
    )
    assert stop["tool"] == CLAUDE_CODE_CAPTURE_STOP_HOOK_TOOL
    assert set(stop["input"]) == {
        "last_assistant_message",
        "session_id",
        "cwd",
        "stop_hook_active",
    }
    serialized_settings = json.dumps(settings)
    assert "event_name" not in serialized_settings

    repeated = configure_claude_code(
        _runtime(tmp_path),
        "read-client",
        capture_client_id="capture-client",
        mcp_path=mcp_path,
        settings_path=settings_path,
    )
    assert repeated.changed is False

    disconnected = disconnect_claude_code(mcp_path=mcp_path, settings_path=settings_path)
    assert disconnected.managed_client_ids == ("read-client", "capture-client")
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert mcp["custom"] == {"keep": True}
    assert settings["permissions"] == {"keep": True}
    assert "mcpServers" not in mcp
    assert "hooks" not in settings


def test_setup_principals_have_exact_read_and_capture_scopes(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    monkeypatch.setenv("ATC_CLAUDE_CODE_MCP_CONFIG", str(tmp_path / "claude.json"))
    monkeypatch.setenv("ATC_CLAUDE_CODE_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setattr(desktop_setup.KeyringCredentialStore, "get", lambda *_: None)
    monkeypatch.setattr(desktop_setup.KeyringCredentialStore, "set", lambda *_: None)

    accesses, _result = desktop_setup._configure_claude_code_accesses(
        store,
        config,
        _runtime(tmp_path),
        capture=True,
        target_url="http://127.0.0.1:7337",
    )

    assert set(accesses) == {
        desktop_setup.CLAUDE_CODE_CLIENT_NAME,
        desktop_setup.CLAUDE_CODE_CAPTURE_CLIENT_NAME,
    }
    clients = {item["name"]: item for item in store.list_clients() if not item["revoked"]}
    assert clients[desktop_setup.CLAUDE_CODE_CLIENT_NAME]["scopes"] == ["context:read"]
    assert clients[desktop_setup.CLAUDE_CODE_CAPTURE_CLIENT_NAME]["scopes"] == ["context:capture"]


def test_successful_opt_out_retires_omitted_managed_authority(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    store = CoreStore(config.database_path)
    store.initialize_vault()
    read, _read_token = store.create_client(
        ClientCreate(name=desktop_setup.CODEX_CLIENT_NAME, scopes=["context:read"])
    )
    capture, _capture_token = store.create_client(
        ClientCreate(
            name=desktop_setup.CODEX_CAPTURE_CLIENT_NAME,
            scopes=["context:capture"],
        )
    )
    explicit, _explicit_token = store.create_client(
        ClientCreate(
            name=desktop_setup.CODEX_EXPLICIT_CLIENT_NAME,
            scopes=["context:propose", "witness:explicit_user_statement"],
        )
    )
    deleted: list[str] = []
    monkeypatch.setattr(
        desktop_setup,
        "delete_client_credential",
        lambda client_id, _config: deleted.append(client_id),
    )

    desktop_setup.retire_managed_client_group(
        store,
        config,
        accesses={
            desktop_setup.CODEX_CLIENT_NAME: desktop_setup.DesktopAccess(
                read.id,
                "unused",
                "operating-system credential store",
            )
        },
        managed_names=(
            desktop_setup.CODEX_CLIENT_NAME,
            desktop_setup.CODEX_CAPTURE_CLIENT_NAME,
            desktop_setup.CODEX_EXPLICIT_CLIENT_NAME,
        ),
    )

    clients = {str(item["id"]): item for item in store.list_clients()}
    assert clients[read.id]["revoked"] is False
    assert clients[capture.id]["revoked"] is True
    assert clients[explicit.id]["revoked"] is True
    assert set(deleted) == {capture.id, explicit.id}
