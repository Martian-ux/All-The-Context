from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from allthecontext import claude_code_config
from allthecontext.claude_code_config import (
    CLAUDE_CODE_EXECUTABLE_ENV,
    CLAUDE_CODE_EXPLICIT_HOOK_TOOL,
    CLAUDE_CODE_EXPLICIT_MCP_PROFILE,
    CLAUDE_CODE_EXPLICIT_MCP_SERVER_KEY,
    CLAUDE_CODE_HOOK_TOOL,
    CLAUDE_CODE_MCP_CONFIG_ENV,
    CLAUDE_CODE_MCP_PROFILE,
    CLAUDE_CODE_MCP_SERVER_KEY,
    CLAUDE_CODE_SETTINGS_ENV,
    CLAUDE_CODE_SKILLS_DIR_ENV,
    claude_code_is_detected,
    claude_code_mcp_config_path,
    claude_code_settings_path,
    claude_code_skills_dir,
    configure_claude_code,
    configure_claude_code_explicit_commands,
    disconnect_claude_code,
    disconnect_claude_code_explicit_commands,
    managed_claude_code_hook_handler,
)
from allthecontext.desktop_runtime import RuntimeCommand


def _runtime(tmp_path: Path) -> RuntimeCommand:
    return RuntimeCommand(
        tmp_path / "AllTheContext.exe",
        mcp_executable=tmp_path / "AllTheContextMCP.exe",
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_user_scope_paths_use_patched_windows_and_posix_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows_home = tmp_path / "windows-profile"
    monkeypatch.setenv("USERPROFILE", str(windows_home))
    monkeypatch.setenv("ATC_CLAUDE_CONFIG", str(tmp_path / "desktop-config.json"))
    monkeypatch.setattr(claude_code_config.platform, "system", lambda: "Windows")

    assert claude_code_mcp_config_path() == windows_home / ".claude.json"
    assert claude_code_settings_path() == windows_home / ".claude" / "settings.json"

    posix_home = tmp_path / "posix-home"
    monkeypatch.setenv("HOME", str(posix_home))
    monkeypatch.delenv("USERPROFILE")
    monkeypatch.setattr(claude_code_config.platform, "system", lambda: "Linux")

    assert claude_code_mcp_config_path() == posix_home / ".claude.json"
    assert claude_code_settings_path() == posix_home / ".claude" / "settings.json"

    mcp_override = tmp_path / "test-mcp.json"
    settings_override = tmp_path / "test-settings.json"
    monkeypatch.setenv(CLAUDE_CODE_MCP_CONFIG_ENV, str(mcp_override))
    monkeypatch.setenv(CLAUDE_CODE_SETTINGS_ENV, str(settings_override))
    assert claude_code_mcp_config_path() == mcp_override
    assert claude_code_settings_path() == settings_override

    skills_override = tmp_path / "test-skills"
    monkeypatch.setenv(CLAUDE_CODE_SKILLS_DIR_ENV, str(skills_override))
    assert claude_code_skills_dir() == skills_override


def test_connect_writes_exact_schema_preserves_unrelated_data_and_is_idempotent(
    tmp_path: Path,
) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / ".claude" / "settings.json"
    _write_json(
        mcp_path,
        {
            "custom": {"keep": True},
            "mcpServers": {"other": {"type": "stdio", "command": "other"}},
        },
    )
    _write_json(
        settings_path,
        {
            "permissions": {"allow": ["Bash(git status)"]},
            "hooks": {
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command"}]}],
                "UserPromptSubmit": [
                    {"matcher": "Bash", "hooks": [{"type": "command", "command": "keep"}]}
                ],
            },
        },
    )

    core_data_dir = tmp_path / "core-data"
    first = configure_claude_code(
        _runtime(tmp_path),
        "claude-code-client",
        target_url="http://127.0.0.1:7444",
        mcp_path=mcp_path,
        settings_path=settings_path,
        core_data_dir=core_data_dir,
    )

    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    managed_server = mcp["mcpServers"][CLAUDE_CODE_MCP_SERVER_KEY]
    assert managed_server == {
        "type": "stdio",
        "command": str(tmp_path / "AllTheContextMCP.exe"),
        "args": [],
        "env": {
            "ATC_MCP_PROFILE": CLAUDE_CODE_MCP_PROFILE,
            "ATC_TARGET_URL": "http://127.0.0.1:7444",
            "ATC_CLIENT_ID": "claude-code-client",
            "ATC_AUTO_START_CORE": "1",
            "ATC_CORE_COMMAND": json.dumps(_runtime(tmp_path).core(), ensure_ascii=False),
            "ATC_CORE_DATA_DIR": str(core_data_dir.resolve()),
        },
    }
    assert mcp["custom"] == {"keep": True}
    assert mcp["mcpServers"]["other"] == {"type": "stdio", "command": "other"}
    assert settings["permissions"] == {"allow": ["Bash(git status)"]}
    assert settings["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [{"type": "command"}]}]
    user_prompt_groups = settings["hooks"]["UserPromptSubmit"]
    assert user_prompt_groups[0]["matcher"] == "Bash"
    assert user_prompt_groups[-1] == {"hooks": [managed_claude_code_hook_handler()]}
    assert "matcher" not in user_prompt_groups[-1]
    assert first.changed is True
    assert first.mcp_changed is True
    assert first.settings_changed is True
    assert first.mcp_backup_path is not None and first.mcp_backup_path.is_file()
    assert first.settings_backup_path is not None and first.settings_backup_path.is_file()

    second = configure_claude_code(
        _runtime(tmp_path),
        "claude-code-client",
        target_url="http://127.0.0.1:7444",
        mcp_path=mcp_path,
        settings_path=settings_path,
        core_data_dir=core_data_dir,
    )
    assert second.changed is False
    assert second.backup_paths == ()


def test_connect_coexists_with_other_handlers_and_repairs_duplicate_managed_handlers(
    tmp_path: Path,
) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"
    managed = managed_claude_code_hook_handler()
    _write_json(mcp_path, {})
    _write_json(
        settings_path,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {"hooks": [managed, {"type": "command", "command": "keep"}]},
                    {"matcher": "Edit", "hooks": [managed]},
                ]
            }
        },
    )

    configure_claude_code(
        _runtime(tmp_path),
        "client",
        mcp_path=mcp_path,
        settings_path=settings_path,
    )
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    groups = settings["hooks"]["UserPromptSubmit"]
    assert sum(handler == managed for group in groups for handler in group["hooks"]) == 1
    assert {"type": "command", "command": "keep"} in groups[0]["hooks"]
    assert groups[-1] == {"hooks": [managed]}
    assert groups[1]["matcher"] == "Edit"
    assert groups[1]["hooks"] == []


@pytest.mark.parametrize(
    "broken_kind",
    ["mcp-json", "settings-json", "mcp-shape", "settings-shape"],
)
def test_malformed_input_fails_closed_without_mutation(tmp_path: Path, broken_kind: str) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"
    mcp_original = '{"keep": true}\n'
    settings_original = '{"hooks": {"UserPromptSubmit": []}}\n'
    mcp_path.write_text(mcp_original, encoding="utf-8")
    settings_path.write_text(settings_original, encoding="utf-8")
    if broken_kind == "mcp-json":
        mcp_path.write_text("{broken", encoding="utf-8")
    elif broken_kind == "settings-json":
        settings_path.write_text("[not-an-object]", encoding="utf-8")
    elif broken_kind == "mcp-shape":
        mcp_path.write_text('{"mcpServers": null}', encoding="utf-8")
    else:
        settings_path.write_text('{"hooks": null}', encoding="utf-8")

    with pytest.raises((ValueError, json.JSONDecodeError)):
        configure_claude_code(
            _runtime(tmp_path),
            "client",
            mcp_path=mcp_path,
            settings_path=settings_path,
        )
    expected_mcp = (
        "{broken"
        if broken_kind == "mcp-json"
        else '{"mcpServers": null}'
        if broken_kind == "mcp-shape"
        else mcp_original
    )
    expected_settings = "[not-an-object]" if broken_kind == "settings-json" else settings_original
    if broken_kind == "settings-shape":
        expected_settings = '{"hooks": null}'
    assert mcp_path.read_text(encoding="utf-8") == expected_mcp
    assert settings_path.read_text(encoding="utf-8") == expected_settings
    assert list(tmp_path.glob("*.atc-backup-*")) == []


@pytest.mark.parametrize("failure_call", [1, 2])
def test_first_and_second_write_faults_roll_back_both_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_call: int
) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"
    mcp_original = '{"mcpServers": {"other": {"command": "keep"}}}\n'
    settings_original = '{"permissions": {"deny": ["rm -rf"]}}\n'
    mcp_path.write_text(mcp_original, encoding="utf-8")
    settings_path.write_text(settings_original, encoding="utf-8")
    unrelated_backup = tmp_path / "unrelated.atc-backup-user"
    unrelated_backup.write_text("user backup", encoding="utf-8")

    real_atomic_write = claude_code_config._atomic_write
    calls = 0

    def write_then_fail(path: Path, content: str) -> None:
        nonlocal calls
        calls += 1
        real_atomic_write(path, content)
        if calls == failure_call:
            raise OSError("injected Claude Code write fault")

    monkeypatch.setattr(claude_code_config, "_atomic_write", write_then_fail)
    with pytest.raises(OSError, match="injected Claude Code write fault"):
        configure_claude_code(
            _runtime(tmp_path),
            "client",
            mcp_path=mcp_path,
            settings_path=settings_path,
        )

    assert mcp_path.read_text(encoding="utf-8") == mcp_original
    assert settings_path.read_text(encoding="utf-8") == settings_original
    assert unrelated_backup.read_text(encoding="utf-8") == "user backup"
    assert calls == (2 if failure_call == 1 else 4)


def test_preimage_change_between_writes_rolls_back_only_atc_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"
    mcp_original = '{"keep": "mcp"}\n'
    settings_original = '{"keep": "settings"}\n'
    mcp_path.write_text(mcp_original, encoding="utf-8")
    settings_path.write_text(settings_original, encoding="utf-8")

    real_atomic_write = claude_code_config._atomic_write

    def write_then_user_edit(path: Path, content: str) -> None:
        real_atomic_write(path, content)
        if path == mcp_path:
            settings_path.write_text('{"user_edit": true}\n', encoding="utf-8")

    monkeypatch.setattr(claude_code_config, "_atomic_write", write_then_user_edit)
    with pytest.raises(RuntimeError, match="changed during setup"):
        configure_claude_code(
            _runtime(tmp_path),
            "client",
            mcp_path=mcp_path,
            settings_path=settings_path,
        )

    assert mcp_path.read_text(encoding="utf-8") == mcp_original
    assert settings_path.read_text(encoding="utf-8") == '{"user_edit": true}\n'


def test_linked_configuration_path_is_rejected_without_following_target(
    tmp_path: Path,
) -> None:
    target = tmp_path / "unrelated.json"
    target.write_text('{"user": true}\n', encoding="utf-8")
    linked = tmp_path / ".claude.json"
    try:
        linked.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ValueError, match="symlinks or reparse points"):
        configure_claude_code(
            _runtime(tmp_path),
            "client",
            mcp_path=linked,
            settings_path=tmp_path / "settings.json",
        )

    assert target.read_text(encoding="utf-8") == '{"user": true}\n'
    assert not (tmp_path / "settings.json").exists()


def test_detection_requires_an_existing_claude_code_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = tmp_path / "claude.exe"
    monkeypatch.setenv(CLAUDE_CODE_EXECUTABLE_ENV, str(executable))
    monkeypatch.setattr(claude_code_config.shutil, "which", lambda _name: None)

    assert claude_code_is_detected() is False
    executable.write_text("executable", encoding="utf-8")
    assert claude_code_is_detected() is True


def test_disconnect_removes_only_managed_server_and_exact_hook_handler(
    tmp_path: Path,
) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"
    _write_json(mcp_path, {})
    _write_json(settings_path, {})
    configure_claude_code(
        _runtime(tmp_path),
        "client-to-remove",
        mcp_path=mcp_path,
        settings_path=settings_path,
    )
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp["mcpServers"]["other"] = {"type": "stdio", "command": "keep"}
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["hooks"]["UserPromptSubmit"][0]["hooks"].append({"type": "command", "command": "keep"})
    _write_json(mcp_path, mcp)
    _write_json(settings_path, settings)

    result = disconnect_claude_code(mcp_path=mcp_path, settings_path=settings_path)

    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert CLAUDE_CODE_MCP_SERVER_KEY not in mcp["mcpServers"]
    assert mcp["mcpServers"]["other"] == {"type": "stdio", "command": "keep"}
    assert settings["hooks"]["UserPromptSubmit"][0]["hooks"] == [
        {"type": "command", "command": "keep"}
    ]
    assert result.changed is True
    assert result.managed_client_id == "client-to-remove"


def test_disconnect_ignores_unmanaged_same_key_and_missing_files(tmp_path: Path) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"
    _write_json(
        mcp_path,
        {"mcpServers": {CLAUDE_CODE_MCP_SERVER_KEY: {"type": "stdio", "command": "user"}}},
    )
    _write_json(
        settings_path,
        {
            "hooks": {
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "mcp_tool",
                                "server": CLAUDE_CODE_MCP_SERVER_KEY,
                                "tool": "different_tool",
                            }
                        ]
                    }
                ]
            }
        },
    )
    mcp_before = mcp_path.read_text(encoding="utf-8")
    settings_before = settings_path.read_text(encoding="utf-8")

    result = disconnect_claude_code(mcp_path=mcp_path, settings_path=settings_path)
    assert result.changed is False
    assert mcp_path.read_text(encoding="utf-8") == mcp_before
    assert settings_path.read_text(encoding="utf-8") == settings_before

    missing_result = disconnect_claude_code(
        mcp_path=tmp_path / "missing.json", settings_path=tmp_path / "missing-settings.json"
    )
    assert missing_result.changed is False
    assert not (tmp_path / "missing.json").exists()
    assert not (tmp_path / "missing-settings.json").exists()


def test_default_paths_never_follow_cwd_into_project_local_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    user_home = tmp_path / "user-home"
    monkeypatch.chdir(project)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setattr(claude_code_config.platform, "system", lambda: "Linux")

    result = configure_claude_code(_runtime(tmp_path), "client")

    assert result.mcp_path == user_home / ".claude.json"
    assert result.settings_path == user_home / ".claude" / "settings.json"
    assert result.mcp_path.is_file()
    assert result.settings_path.is_file()
    assert not (project / ".claude.json").exists()
    assert not (project / ".claude").exists()


def test_config_replacement_preserves_posix_mode_and_new_files_are_private(
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX mode bits are not enforced on Windows")

    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"
    _write_json(mcp_path, {})
    os.chmod(mcp_path, 0o640)

    configure_claude_code(
        _runtime(tmp_path),
        "client",
        mcp_path=mcp_path,
        settings_path=settings_path,
    )

    assert stat.S_IMODE(mcp_path.stat().st_mode) == 0o640
    assert stat.S_IMODE(settings_path.stat().st_mode) == 0o600


def test_production_token_is_not_serialized_when_os_credential_lookup_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        claude_code_config.KeyringCredentialStore,
        "get",
        lambda _self, _name: "production-token",
    )
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"

    result = configure_claude_code(
        _runtime(tmp_path),
        "client",
        token="production-token",
        mcp_path=mcp_path,
        settings_path=settings_path,
    )

    managed = json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"][
        CLAUDE_CODE_MCP_SERVER_KEY
    ]
    assert "ATC_CLIENT_TOKEN" not in managed["env"]
    assert "production-token" not in repr(result)


def test_token_serialization_requires_explicit_development_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_self: object, _name: str) -> None:
        raise RuntimeError("credential store unavailable")

    monkeypatch.setattr(claude_code_config.KeyringCredentialStore, "get", unavailable)
    monkeypatch.setenv("ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE", "1")
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"

    configure_claude_code(
        _runtime(tmp_path),
        "client",
        token="development-token",
        mcp_path=mcp_path,
        settings_path=settings_path,
    )

    managed = json.loads(mcp_path.read_text(encoding="utf-8"))["mcpServers"][
        CLAUDE_CODE_MCP_SERVER_KEY
    ]
    assert managed["env"]["ATC_CLIENT_TOKEN"] == "development-token"


def test_token_serialization_fails_closed_without_os_store_or_explicit_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unavailable(_self: object, _name: str) -> None:
        raise RuntimeError("credential store unavailable")

    monkeypatch.setattr(claude_code_config.KeyringCredentialStore, "get", unavailable)
    monkeypatch.delenv("ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE", raising=False)
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"

    with pytest.raises(RuntimeError, match="plaintext credential storage is disabled"):
        configure_claude_code(
            _runtime(tmp_path),
            "client",
            token="production-token",
            mcp_path=mcp_path,
            settings_path=settings_path,
        )
    assert not mcp_path.exists()
    assert not settings_path.exists()


def test_oversized_input_fails_closed_without_writing_the_other_file(tmp_path: Path) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / "settings.json"
    mcp_original = '{"keep": true}\n'
    mcp_path.write_text(mcp_original, encoding="utf-8")
    settings_path.write_text(
        "{" + '"padding": "' + "x" * claude_code_config.MAX_CLAUDE_CODE_CONFIG_BYTES + '"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bounded read limit"):
        configure_claude_code(
            _runtime(tmp_path),
            "client",
            mcp_path=mcp_path,
            settings_path=settings_path,
        )
    assert mcp_path.read_text(encoding="utf-8") == mcp_original
    assert list(tmp_path.glob("*.atc-backup-*")) == []


def test_managed_hook_handler_has_exact_current_shape() -> None:
    assert managed_claude_code_hook_handler() == {
        "type": "mcp_tool",
        "server": CLAUDE_CODE_MCP_SERVER_KEY,
        "tool": CLAUDE_CODE_HOOK_TOOL,
        "input": {"prompt": "${prompt}", "cwd": "${cwd}", "session_id": "${session_id}"},
    }


def test_explicit_commands_are_opt_in_transactional_and_idempotent(tmp_path: Path) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / ".claude" / "settings.json"
    _write_json(mcp_path, {"mcpServers": {"other": {"type": "stdio"}}})
    _write_json(settings_path, {"permissions": {"allow": ["Bash(git status)"]}})

    first = configure_claude_code_explicit_commands(
        _runtime(tmp_path),
        "claude-code-explicit-client",
        mcp_path=mcp_path,
        settings_path=settings_path,
        target_url="http://127.0.0.1:7444",
        core_data_dir=tmp_path / "core-data",
    )

    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert (
        mcp["mcpServers"][CLAUDE_CODE_EXPLICIT_MCP_SERVER_KEY]["env"]["ATC_MCP_PROFILE"]
        == CLAUDE_CODE_EXPLICIT_MCP_PROFILE
    )
    assert settings["permissions"] == {"allow": ["Bash(git status)"]}
    assert settings["hooks"]["UserPromptExpansion"] == [
        {
            "matcher": "^(atc-remember|atc-correct|atc-forget)$",
            "hooks": [
                {
                    "type": "mcp_tool",
                    "server": CLAUDE_CODE_EXPLICIT_MCP_SERVER_KEY,
                    "tool": CLAUDE_CODE_EXPLICIT_HOOK_TOOL,
                    "input": {
                        "expansion_type": "${expansion_type}",
                        "command_name": "${command_name}",
                        "command_args": "${command_args}",
                        "command_source": "${command_source}",
                    },
                }
            ],
        }
    ]
    skill_paths = [
        settings_path.parent / "skills" / name / "SKILL.md"
        for name in (
            "atc-remember",
            "atc-correct",
            "atc-forget",
        )
    ]
    assert all(path.is_file() for path in skill_paths)
    assert all("UserPromptExpansion" in path.read_text(encoding="utf-8") for path in skill_paths)
    assert all(
        "\nname:" not in path.read_text(encoding="utf-8").split("---", 2)[1]
        for path in skill_paths
    )
    assert not (settings_path.parent / "commands").exists()
    assert first.changed is True
    assert first.skill_changed is True

    second = configure_claude_code_explicit_commands(
        _runtime(tmp_path),
        "claude-code-explicit-client",
        mcp_path=mcp_path,
        settings_path=settings_path,
        target_url="http://127.0.0.1:7444",
        core_data_dir=tmp_path / "core-data",
    )
    assert second.changed is False
    assert second.skill_changed is False

    removed = disconnect_claude_code_explicit_commands(
        mcp_path=mcp_path, settings_path=settings_path
    )
    assert removed.changed is True
    assert all(not path.exists() for path in skill_paths)
    assert "UserPromptExpansion" not in json.loads(settings_path.read_text(encoding="utf-8")).get(
        "hooks", {}
    )


def test_explicit_skill_collision_fails_before_any_config_write(tmp_path: Path) -> None:
    mcp_path = tmp_path / ".claude.json"
    settings_path = tmp_path / ".claude" / "settings.json"
    _write_json(mcp_path, {"keep": "mcp"})
    _write_json(settings_path, {"keep": "settings"})
    skill_path = settings_path.parent / "skills" / "atc-correct" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("---\ndescription: user-owned\n---\nuser-owned\n", encoding="utf-8")
    before_mcp = mcp_path.read_text(encoding="utf-8")
    before_settings = settings_path.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="reserved Claude Code skill path"):
        configure_claude_code_explicit_commands(
            _runtime(tmp_path),
            "claude-code-explicit-client",
            mcp_path=mcp_path,
            settings_path=settings_path,
        )

    assert mcp_path.read_text(encoding="utf-8") == before_mcp
    assert settings_path.read_text(encoding="utf-8") == before_settings
    assert skill_path.read_text(encoding="utf-8") == (
        "---\ndescription: user-owned\n---\nuser-owned\n"
    )


def test_remove_rollback_never_overwrites_file_created_after_delete(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    original = '{"keep": true}\n'
    path.write_text(original, encoding="utf-8")
    document = claude_code_config._Document(path, original, {"keep": True}, True)
    plan = claude_code_config._WritePlan(document, "", remove=True)

    path.unlink()
    external = '{"external": true}\n'
    path.write_text(external, encoding="utf-8")

    with pytest.raises(RuntimeError, match="changed during rollback"):
        claude_code_config._restore(plan)
    assert path.read_text(encoding="utf-8") == external
