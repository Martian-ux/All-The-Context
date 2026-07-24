from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from allthecontext.client_config import (
    MANAGED_BEGIN,
    apply_managed_client_cleanup,
    claude_config_path,
    claude_is_configured,
    claude_is_detected,
    codex_is_configured,
    configure_claude,
    configure_codex,
    disconnect_claude,
    disconnect_codex,
    plan_managed_client_cleanup,
    read_claude_config,
    read_codex_config,
    repair_managed_runtime_bindings,
)
from allthecontext.config import CoreConfig
from allthecontext.desktop_runtime import RuntimeCommand


def test_configure_codex_preserves_config_and_is_idempotent(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'model_reasoning_effort = "high"\n\n[mcp_servers.existing]\ncommand = "existing"\n',
        encoding="utf-8",
    )
    helper = tmp_path / "AllTheContextMCP.exe"
    runtime = RuntimeCommand(tmp_path / "AllTheContext.exe", mcp_executable=helper)

    vault = tmp_path / "vault"
    first = configure_codex(
        runtime,
        "client-1",
        token=None,
        path=config,
        core_data_dir=vault,
    )

    parsed = tomllib.loads(config.read_text(encoding="utf-8"))
    assert parsed["model_reasoning_effort"] == "high"
    assert parsed["mcp_servers"]["existing"]["command"] == "existing"
    managed = parsed["mcp_servers"]["all_the_context"]
    assert managed["command"] == str(helper)
    assert managed["args"] == []
    assert managed["env"]["ATC_CLIENT_ID"] == "client-1"
    assert managed["env"]["ATC_AUTO_START_CORE"] == "1"
    assert managed["env"]["ATC_CORE_DATA_DIR"] == str(vault.resolve())
    assert json.loads(managed["env"]["ATC_CORE_COMMAND"]) == [
        str(runtime.executable),
        "--core",
    ]
    assert "ATC_CLIENT_TOKEN" not in managed["env"]
    assert first.changed is True
    assert first.backup_path is not None and first.backup_path.is_file()

    second = configure_codex(
        runtime,
        "client-1",
        token=None,
        path=config,
        core_data_dir=vault,
    )
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

    vault = tmp_path / "vault"
    first = configure_claude(
        runtime,
        "client-3",
        token="secret",
        path=config,
        core_data_dir=vault,
    )

    parsed = json.loads(config.read_text(encoding="utf-8"))
    assert parsed["theme"] == "system"
    assert parsed["mcpServers"]["existing"]["command"] == "existing"
    managed = parsed["mcpServers"]["all-the-context"]
    assert managed["command"] == str(helper)
    assert managed["args"] == []
    assert managed["env"]["ATC_CLIENT_ID"] == "client-3"
    assert managed["env"]["ATC_CLIENT_TOKEN"] == "secret"
    assert managed["env"]["ATC_AUTO_START_CORE"] == "1"
    assert managed["env"]["ATC_CORE_DATA_DIR"] == str(vault.resolve())
    assert json.loads(managed["env"]["ATC_CORE_COMMAND"]) == [
        str(runtime.executable),
        "--core",
    ]
    assert first.backup_path is not None and first.backup_path.is_file()
    assert claude_is_configured(config) is True

    second = configure_claude(
        runtime,
        "client-3",
        token="secret",
        path=config,
        core_data_dir=vault,
    )
    assert second.changed is False
    assert second.backup_path is None


def test_claude_detection_does_not_treat_a_config_folder_as_an_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "Claude" / "claude_desktop_config.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ATC_CLAUDE_CONFIG", str(config))
    monkeypatch.delenv("ATC_CLAUDE_DESKTOP_EXECUTABLE", raising=False)
    monkeypatch.setattr("allthecontext.client_config.platform.system", lambda: "Linux")

    assert claude_is_detected() is False

    executable = tmp_path / "ClaudeDesktop.bin"
    executable.write_bytes(b"test application marker")
    monkeypatch.setenv("ATC_CLAUDE_DESKTOP_EXECUTABLE", str(executable))
    assert claude_is_detected() is True


def test_claude_detection_and_config_support_the_microsoft_store_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_id = "Claude_1.24012.1.0_x64__publisher"
    package_root = tmp_path / "WindowsApps" / package_id
    executable = package_root / "app" / "Claude.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"test application marker")
    local_app_data = tmp_path / "Local"
    config = (
        local_app_data
        / "Packages"
        / "Claude_publisher"
        / "LocalCache"
        / "Roaming"
        / "Claude"
        / "claude_desktop_config.json"
    )
    config.parent.mkdir(parents=True)
    config.write_text("{}", encoding="utf-8")

    class FakeKey:
        def __init__(self, name: str) -> None:
            self.name = name

        def __enter__(self) -> FakeKey:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class FakeRegistry:
        HKEY_CURRENT_USER = "HKCU"
        HKEY_LOCAL_MACHINE = "HKLM"
        KEY_READ = 1
        KEY_WOW64_64KEY = 2
        KEY_WOW64_32KEY = 4

        def OpenKey(self, root: object, path: str, _reserved: int, _access: int) -> FakeKey:
            if root == self.HKEY_CURRENT_USER and path.endswith(r"Repository\Packages"):
                return FakeKey("packages")
            if isinstance(root, FakeKey) and root.name == "packages" and path == package_id:
                return FakeKey("claude")
            raise OSError

        def EnumKey(self, key: FakeKey, index: int) -> str:
            if key.name == "packages" and index == 0:
                return package_id
            raise OSError

        def QueryValueEx(self, key: FakeKey, name: str) -> tuple[str, int]:
            if key.name != "claude":
                raise OSError
            values = {
                "PackageID": package_id,
                "PackageRootFolder": str(package_root),
                "DisplayName": "Claude",
            }
            try:
                return values[name], 1
            except KeyError as error:
                raise OSError from error

    monkeypatch.setattr("allthecontext.client_config.platform.system", lambda: "Windows")
    monkeypatch.setattr("allthecontext.client_config.windows_registry", lambda: FakeRegistry())
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "ProgramFiles"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "ProgramFilesX86"))
    monkeypatch.delenv("ATC_CLAUDE_CONFIG", raising=False)
    monkeypatch.delenv("ATC_CLAUDE_DESKTOP_EXECUTABLE", raising=False)

    assert claude_is_detected() is True
    assert claude_config_path() == config


def test_lightweight_launch_repair_binds_existing_entries_to_the_active_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_home = tmp_path / "codex"
    claude_path = tmp_path / "claude" / "claude_desktop_config.json"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("ATC_CLAUDE_CONFIG", str(claude_path))
    runtime = RuntimeCommand(Path("python"), ("-m", "allthecontext.desktop"))
    wrong_vault = tmp_path / "wrong-vault"
    active = CoreConfig.in_directory(tmp_path / "active-vault")
    configure_codex(runtime, "codex-client", token=None, core_data_dir=wrong_vault)
    configure_claude(runtime, "claude-client", token=None, core_data_dir=wrong_vault)

    repaired = repair_managed_runtime_bindings(runtime, active)

    assert len(repaired) == 2
    codex = read_codex_config()
    claude = read_claude_config()
    assert codex is not None and codex.env["ATC_CORE_DATA_DIR"] == str(active.data_dir)
    assert claude is not None and claude.env["ATC_CORE_DATA_DIR"] == str(active.data_dir)


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
