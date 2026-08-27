"""Safe, inspectable, and reversible MCP client configuration adapters."""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import shutil
import stat
import tomllib
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import CoreConfig
from .credentials import (
    FALLBACK_CREDENTIAL_STORAGE,
    OS_CREDENTIAL_STORAGE,
    KeyringCredentialStore,
    development_file_credentials_enabled,
    require_development_file_credentials,
)
from .desktop_runtime import RuntimeCommand
from .platform_compat import windows_registry

MANAGED_BEGIN = "# BEGIN All The Context managed MCP"
MANAGED_END = "# END All The Context managed MCP"
TABLE_HEADER = "[mcp_servers.all_the_context]"
TABLE_PATH = ("mcp_servers", "all_the_context")
CODEX_CAPTURE_SCOPE = "context:capture"
CODEX_READ_SERVER_KEY = "all_the_context"
CODEX_CAPTURE_SERVER_KEY = "all_the_context_capture"
CODEX_EXPLICIT_SERVER_KEY = "all_the_context_explicit"
CODEX_READ_PROFILE = "codex_read"
# Stable setup seam for the lifecycle-adapter lane.  That lane owns the
# codex_hook profile and its capture-only tool implementation.
CODEX_CAPTURE_PROFILE = "codex_hook"
CODEX_EXPLICIT_PROFILE = "codex_explicit"
CODEX_CAPTURE_USER_PROMPT_TOOL = "codex_user_prompt_submit"
CODEX_CAPTURE_STOP_TOOL = "codex_stop"
CODEX_EXPLICIT_COMMANDS = ("atc-remember", "atc-correct", "atc-forget")
CODEX_READ_TOOLS = ("bootstrap_context", "search_context", "get_context_item", "context_status")
CODEX_CAPTURE_TOOLS = (CODEX_CAPTURE_USER_PROMPT_TOOL, CODEX_CAPTURE_STOP_TOOL)
CODEX_EXPLICIT_TOOLS = ("propose_memory", "report_context_error", "forget_context")
CODEX_SKILLS_DIR_ENV = "ATC_CODEX_SKILLS_DIR"
CODEX_HOOKS_FILE_NAME = "hooks.json"
CODEX_HOOK_EVENT_NAMES = ("UserPromptSubmit", "Stop")
MAX_CODEX_CONFIG_BYTES = 4 * 1024 * 1024
CLAUDE_SERVER_KEY = "all-the-context"
CLAUDE_MSIX_PACKAGES_KEY = (
    r"Software\Classes\Local Settings\Software\Microsoft\Windows"
    r"\CurrentVersion\AppModel\Repository\Packages"
)


@dataclass(frozen=True, slots=True)
class ClientConfigResult:
    client: str
    path: Path
    backup_path: Path | None
    changed: bool
    managed_client_id: str | None = None
    managed_client_ids: tuple[str, ...] = ()
    skill_changed: bool = False
    skill_paths: tuple[Path, ...] = ()


@dataclass(frozen=True, slots=True)
class ManagedClientConfig:
    path: Path
    command: str
    args: tuple[str, ...]
    env: dict[str, str]


@dataclass(frozen=True, slots=True)
class ManagedConfigCleanup:
    """An exact, preflighted removal of ATC authority from one config copy."""

    path: Path
    original: str
    updated: str
    managed_client_id: str | None
    credential_storage: str | None


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".codex"


def codex_config_path() -> Path:
    return codex_home() / "config.toml"


def codex_is_detected() -> bool:
    configured = os.environ.get("ATC_CODEX_EXECUTABLE")
    if configured:
        try:
            if Path(configured).expanduser().resolve().is_file():
                return True
        except (OSError, RuntimeError, ValueError):
            pass
    home = codex_home()
    return shutil.which("codex") is not None or home.is_dir() or (Path.home() / ".codex").is_dir()


def _claude_msix_installations() -> list[tuple[str, Path]]:
    """Return registered Claude MSIX package IDs and verified executables."""

    try:
        winreg = windows_registry()
    except ImportError:  # pragma: no cover - defensive on nonstandard runtimes
        return []
    installations: list[tuple[str, Path]] = []
    try:
        packages = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            CLAUDE_MSIX_PACKAGES_KEY,
            0,
            winreg.KEY_READ,
        )
    except OSError:
        return []
    with packages:
        index = 0
        while True:
            try:
                package_name = winreg.EnumKey(packages, index)
            except OSError:
                break
            index += 1
            if not isinstance(package_name, str) or not package_name.casefold().startswith(
                "claude_"
            ):
                continue
            try:
                package = winreg.OpenKey(packages, package_name, 0, winreg.KEY_READ)
            except OSError:
                continue
            with package:
                try:
                    package_id, _kind = winreg.QueryValueEx(package, "PackageID")
                    package_root, _kind = winreg.QueryValueEx(package, "PackageRootFolder")
                except OSError:
                    continue
                try:
                    display_name, _kind = winreg.QueryValueEx(package, "DisplayName")
                except OSError:
                    display_name = None
            if (
                not isinstance(package_id, str)
                or not package_id.casefold().startswith("claude_")
                or not isinstance(package_root, str)
                or (isinstance(display_name, str) and display_name.casefold() != "claude")
            ):
                continue
            executable = Path(package_root).expanduser() / "app" / "Claude.exe"
            try:
                if executable.is_file():
                    installations.append((package_id, executable))
            except OSError:
                continue
    return installations


def _claude_msix_config_path() -> Path | None:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return None
    local_root = Path(local_app_data).expanduser().resolve()
    candidates: list[Path] = []
    for package_id, _executable in _claude_msix_installations():
        if "__" not in package_id or "_" not in package_id:
            continue
        package_name = package_id.split("_", 1)[0]
        publisher_id = package_id.rsplit("__", 1)[1]
        if not package_name or not publisher_id:
            continue
        family_name = f"{package_name}_{publisher_id}"
        candidate = (
            local_root
            / "Packages"
            / family_name
            / "LocalCache"
            / "Roaming"
            / "Claude"
            / "claude_desktop_config.json"
        )
        if candidate not in candidates:
            candidates.append(candidate)
    return next((candidate for candidate in candidates if candidate.is_file()), None) or (
        candidates[0] if candidates else None
    )


def claude_config_path() -> Path:
    configured = os.environ.get("ATC_CLAUDE_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    system = platform.system()
    if system == "Windows":
        msix_config = _claude_msix_config_path()
        if msix_config is not None:
            return msix_config
        app_data = os.environ.get("APPDATA")
        root = Path(app_data) if app_data else Path.home() / "AppData" / "Roaming"
        return root.resolve() / "Claude" / "claude_desktop_config.json"
    if system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Claude"
            / "claude_desktop_config.json"
        )
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_root.expanduser().resolve() / "Claude" / "claude_desktop_config.json"


def claude_is_detected() -> bool:
    """Detect the desktop application, not a stale or pre-created config folder."""

    configured = os.environ.get("ATC_CLAUDE_DESKTOP_EXECUTABLE")
    if configured:
        try:
            if Path(configured).expanduser().resolve().is_file():
                return True
        except (OSError, RuntimeError, ValueError):
            pass

    system = platform.system()
    if system == "Windows":
        candidates: list[Path] = []
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            local_root = Path(local_app_data).expanduser().resolve()
            versioned_roots = (
                local_root / "AnthropicClaude",
                local_root / "Claude",
            )
            candidates.extend(
                (
                    local_root / "AnthropicClaude" / "Claude.exe",
                    local_root / "Programs" / "Claude" / "Claude.exe",
                    local_root / "Claude" / "Claude.exe",
                )
            )
            for versioned_root in versioned_roots:
                candidates.extend(versioned_root.glob("app-*/Claude.exe"))
        for variable in ("ProgramFiles", "ProgramFiles(x86)"):
            root = os.environ.get(variable)
            if root:
                candidates.append(Path(root).expanduser().resolve() / "Claude" / "Claude.exe")
        if any(candidate.is_file() for candidate in candidates):
            return True
        if _claude_msix_installations():
            return True
        try:
            winreg = windows_registry()
        except ImportError:  # pragma: no cover - defensive on nonstandard runtimes
            return False
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(
                        hive,
                        r"Software\Microsoft\Windows\CurrentVersion\App Paths\Claude.exe",
                        0,
                        winreg.KEY_READ | view,
                    ) as key:
                        executable, _kind = winreg.QueryValueEx(key, "")
                except OSError:
                    continue
                if isinstance(executable, str) and Path(executable).expanduser().is_file():
                    return True
        return False
    if system == "Darwin":
        return any(
            path.is_dir()
            for path in (
                Path("/Applications/Claude.app"),
                Path.home() / "Applications" / "Claude.app",
            )
        )
    return False


def _managed_config(path: Path, server: Any) -> ManagedClientConfig | None:
    if not isinstance(server, dict) or not isinstance(server.get("command"), str):
        return None
    raw_args = server.get("args", [])
    raw_env = server.get("env", {})
    if not isinstance(raw_args, list) or any(not isinstance(item, str) for item in raw_args):
        return None
    if not isinstance(raw_env, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in raw_env.items()
    ):
        return None
    return ManagedClientConfig(
        path=path,
        command=server["command"],
        args=tuple(raw_args),
        env=dict(raw_env),
    )


def read_codex_config(path: Path | None = None) -> ManagedClientConfig | None:
    config_path = (path or codex_config_path()).expanduser().resolve()
    if not config_path.is_file():
        return None
    parsed = tomllib.loads(config_path.read_text(encoding="utf-8"))
    servers = parsed.get("mcp_servers", {})
    server = servers.get("all_the_context") if isinstance(servers, dict) else None
    return _managed_config(config_path, server)


def read_claude_config(path: Path | None = None) -> ManagedClientConfig | None:
    config_path = (path or claude_config_path()).expanduser().resolve()
    if not config_path.is_file():
        return None
    parsed = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Claude Desktop configuration must contain a JSON object")
    servers = parsed.get("mcpServers", {})
    server = servers.get(CLAUDE_SERVER_KEY) if isinstance(servers, dict) else None
    return _managed_config(config_path, server)


def codex_is_configured(path: Path | None = None) -> bool:
    try:
        return read_codex_config(path) is not None
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return False


def claude_is_configured(path: Path | None = None) -> bool:
    try:
        return read_claude_config(path) is not None
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def repair_managed_runtime_bindings(
    runtime: RuntimeCommand,
    config: CoreConfig,
) -> tuple[ClientConfigResult, ...]:
    """Refresh existing managed entries without creating apps or rotating credentials."""

    target_url = f"http://{config.host}:{config.port}"
    repaired: list[ClientConfigResult] = []
    try:
        current_codex = read_codex_config()
        if current_codex is not None:
            registrations = read_codex_registration_ids()
            read_id = registrations.get("read") or current_codex.env.get("ATC_CLIENT_ID")
            if read_id:
                repaired.append(
                    configure_codex_integration(
                        runtime,
                        read_client_id=read_id,
                        read_token=current_codex.env.get("ATC_CLIENT_TOKEN"),
                        capture_client_id=registrations.get("capture"),
                        explicit_client_id=registrations.get("explicit"),
                        target_url=target_url,
                        core_data_dir=config.data_dir,
                    )
                )
    except (OSError, ValueError):
        # A user-owned invalid or unwritable config is left untouched. The
        # authenticated Connections page reports the precise degraded state.
        pass

    for read_config, configure in ((read_claude_config, configure_claude),):
        try:
            current = read_config()
            if current is None:
                continue
            client_id = current.env.get("ATC_CLIENT_ID")
            if not client_id:
                continue
            repaired.append(
                configure(
                    runtime,
                    client_id,
                    token=current.env.get("ATC_CLIENT_TOKEN"),
                    path=current.path,
                    target_url=target_url,
                    core_data_dir=config.data_dir,
                )
            )
        except (OSError, ValueError):
            # A user-owned invalid or unwritable config is left untouched. The
            # authenticated Connections page reports the precise degraded state.
            continue
    return tuple(repaired)


def render_codex_mcp_block(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    token: str | None,
    target_url: str = "http://127.0.0.1:7337",
    core_data_dir: Path | None = None,
) -> str:
    mcp_command = runtime.mcp()
    arguments = list(mcp_command[1:])
    environment = {
        "ATC_TARGET_URL": target_url,
        "ATC_CLIENT_ID": client_id,
        "ATC_AUTO_START_CORE": "1",
        "ATC_CORE_COMMAND": json.dumps(runtime.core(), ensure_ascii=False),
        "ATC_CORE_DATA_DIR": str((core_data_dir or CoreConfig.default().data_dir).resolve()),
    }
    if token:
        environment["ATC_CLIENT_TOKEN"] = token
    rendered_args = ", ".join(json.dumps(argument) for argument in arguments)
    rendered_env = ", ".join(f"{name} = {json.dumps(value)}" for name, value in environment.items())
    return "\n".join(
        [
            MANAGED_BEGIN,
            TABLE_HEADER,
            f"command = {json.dumps(mcp_command[0])}",
            f"args = [{rendered_args}]",
            f"env = {{ {rendered_env} }}",
            "required = true",
            "startup_timeout_sec = 20",
            'default_tools_approval_mode = "approve"',
            MANAGED_END,
        ]
    )


def _toml_table_path(header: str) -> tuple[str, ...] | None:
    """Use tomllib itself so quoted/dotted official TOML headers are handled correctly."""
    if header.lstrip().startswith("[["):
        return None
    marker = "__all_the_context_table_marker__"
    try:
        parsed = tomllib.loads(f"{header}\n{marker} = true\n")
    except tomllib.TOMLDecodeError:
        return None

    def find(value: Any, prefix: tuple[str, ...] = ()) -> tuple[str, ...] | None:
        if not isinstance(value, dict):
            return None
        if value.get(marker) is True:
            return prefix
        for key, child in value.items():
            result = find(child, (*prefix, str(key)))
            if result is not None:
                return result
        return None

    return find(parsed)


def _remove_codex_tables(existing: str) -> str:
    header_pattern = re.compile(r"(?m)^[ \t]*\[\[?[^\r\n]+\]\]?[ \t]*(?:#.*)?$")
    matches = list(header_pattern.finditer(existing))
    if not matches:
        return existing
    pieces: list[str] = [existing[: matches[0].start()]]
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(existing)
        table_path = _toml_table_path(match.group(0))
        if table_path is None or table_path[: len(TABLE_PATH)] != TABLE_PATH:
            pieces.append(existing[match.start() : end])
    return "".join(pieces)


def _replace_managed_section(existing: str, block: str | None) -> str:
    marker_pattern = re.compile(
        rf"(?ms)^[ \t]*{re.escape(MANAGED_BEGIN)}\r?\n.*?"
        rf"^[ \t]*{re.escape(MANAGED_END)}[ \t]*(?:\r?\n)?"
    )
    if marker_pattern.search(existing):
        replacement = f"{block}\n" if block else ""
        updated = marker_pattern.sub(lambda _match: replacement, existing, count=1)
        # A hand-edited duplicate table must not survive beside our marker.
        if block and updated.count(TABLE_HEADER) > 1:
            updated = _remove_codex_tables(updated)
            updated = f"{updated.rstrip()}\n\n{block}\n" if updated.strip() else f"{block}\n"
        return updated

    cleaned = _remove_codex_tables(existing).rstrip()
    if block:
        return f"{cleaned}\n\n{block}\n" if cleaned else f"{block}\n"
    return f"{cleaned}\n" if cleaned else ""


def _backup(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.atc-backup-{timestamp}-{secrets.token_hex(3)}")
    shutil.copy2(path, backup)
    return backup


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_config_transactionally(path: Path, original: str, updated: str) -> Path | None:
    """Replace one config and restore its exact prior state if the write reports failure."""

    existed = path.is_file()
    backup = _backup(path) if existed else None
    try:
        _atomic_write(path, updated)
    except BaseException:
        try:
            if existed:
                _atomic_write(path, original)
            else:
                path.unlink(missing_ok=True)
        except BaseException as rollback_error:
            raise RuntimeError(
                "AI client configuration failed and its prior state could not be restored"
            ) from rollback_error
        raise
    return backup


def configure_codex(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    token: str | None,
    path: Path | None = None,
    target_url: str = "http://127.0.0.1:7337",
    core_data_dir: Path | None = None,
) -> ClientConfigResult:
    config_path = (path or codex_config_path()).expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    if existing:
        tomllib.loads(existing)
    block = render_codex_mcp_block(
        runtime,
        client_id,
        token=token,
        target_url=target_url,
        core_data_dir=core_data_dir,
    )
    updated = _replace_managed_section(existing, block)
    tomllib.loads(updated)
    if updated == existing:
        return ClientConfigResult("Codex", config_path, None, False)
    backup = _write_config_transactionally(config_path, existing, updated)
    return ClientConfigResult("Codex", config_path, backup, True)


def disconnect_codex(path: Path | None = None) -> ClientConfigResult:
    config_path = (path or codex_config_path()).expanduser().resolve()
    if not config_path.is_file():
        return ClientConfigResult("Codex", config_path, None, False)
    existing = config_path.read_text(encoding="utf-8")
    parsed = tomllib.loads(existing)
    servers = parsed.get("mcp_servers", {})
    server = servers.get("all_the_context") if isinstance(servers, dict) else None
    managed = _managed_config(config_path, server)
    managed_client_id = managed.env.get("ATC_CLIENT_ID") if managed is not None else None
    updated = _replace_managed_section(existing, None)
    tomllib.loads(updated)
    if updated == existing:
        return ClientConfigResult("Codex", config_path, None, False, managed_client_id)
    backup = _write_config_transactionally(config_path, existing, updated)
    return ClientConfigResult("Codex", config_path, backup, True, managed_client_id)


def _claude_document(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if not text:
        return text, {}
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Claude Desktop configuration must contain a JSON object")
    return text, parsed


def configure_claude(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    token: str | None,
    path: Path | None = None,
    target_url: str = "http://127.0.0.1:7337",
    core_data_dir: Path | None = None,
) -> ClientConfigResult:
    """Add the local STDIO adapter while preserving every other Claude setting."""
    config_path = (path or claude_config_path()).expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing_text, parsed = _claude_document(config_path)
    updated = deepcopy(parsed)
    servers = updated.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError("Claude Desktop mcpServers setting must contain a JSON object")
    command = runtime.mcp()
    environment = {
        "ATC_TARGET_URL": target_url,
        "ATC_CLIENT_ID": client_id,
        "ATC_AUTO_START_CORE": "1",
        "ATC_CORE_COMMAND": json.dumps(runtime.core(), ensure_ascii=False),
        "ATC_CORE_DATA_DIR": str((core_data_dir or CoreConfig.default().data_dir).resolve()),
    }
    if token:
        environment["ATC_CLIENT_TOKEN"] = token
    servers[CLAUDE_SERVER_KEY] = {
        "command": command[0],
        "args": list(command[1:]),
        "env": environment,
    }
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
    if rendered == existing_text:
        return ClientConfigResult("Claude Desktop", config_path, None, False)
    backup = _write_config_transactionally(config_path, existing_text, rendered)
    return ClientConfigResult("Claude Desktop", config_path, backup, True)


def disconnect_claude(path: Path | None = None) -> ClientConfigResult:
    config_path = (path or claude_config_path()).expanduser().resolve()
    if not config_path.is_file():
        return ClientConfigResult("Claude Desktop", config_path, None, False)
    existing_text, parsed = _claude_document(config_path)
    updated = deepcopy(parsed)
    servers = updated.get("mcpServers")
    server = servers.get(CLAUDE_SERVER_KEY) if isinstance(servers, dict) else None
    managed = _managed_config(config_path, server)
    managed_client_id = managed.env.get("ATC_CLIENT_ID") if managed is not None else None
    if not isinstance(servers, dict) or CLAUDE_SERVER_KEY not in servers:
        return ClientConfigResult("Claude Desktop", config_path, None, False, managed_client_id)
    del servers[CLAUDE_SERVER_KEY]
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
    if rendered == existing_text:
        return ClientConfigResult("Claude Desktop", config_path, None, False)
    backup = _write_config_transactionally(config_path, existing_text, rendered)
    return ClientConfigResult("Claude Desktop", config_path, backup, True, managed_client_id)


def _atc_backup_paths(config_path: Path) -> list[Path]:
    if not config_path.parent.is_dir():
        return []
    return sorted(config_path.parent.glob(f"{config_path.name}.atc-backup-*"))


def _plan_codex_cleanup(path: Path) -> ManagedConfigCleanup | None:
    if not path.is_file():
        return None
    original = path.read_text(encoding="utf-8")
    parsed = tomllib.loads(original)
    servers = parsed.get("mcp_servers", {})
    server = servers.get("all_the_context") if isinstance(servers, dict) else None
    managed = _managed_config(path, server)
    updated = _replace_managed_section(original, None)
    tomllib.loads(updated)
    if updated == original:
        return None
    return ManagedConfigCleanup(
        path=path,
        original=original,
        updated=updated,
        managed_client_id=(managed.env.get("ATC_CLIENT_ID") if managed is not None else None),
        credential_storage=(
            FALLBACK_CREDENTIAL_STORAGE
            if managed is not None and "ATC_CLIENT_TOKEN" in managed.env
            else OS_CREDENTIAL_STORAGE
            if managed is not None
            else None
        ),
    )


def _plan_claude_cleanup(path: Path) -> ManagedConfigCleanup | None:
    if not path.is_file():
        return None
    original, parsed = _claude_document(path)
    updated = deepcopy(parsed)
    servers = updated.get("mcpServers")
    server = servers.get(CLAUDE_SERVER_KEY) if isinstance(servers, dict) else None
    managed = _managed_config(path, server)
    if not isinstance(servers, dict) or CLAUDE_SERVER_KEY not in servers:
        return None
    del servers[CLAUDE_SERVER_KEY]
    rendered = json.dumps(updated, indent=2, ensure_ascii=False) + "\n"
    return ManagedConfigCleanup(
        path=path,
        original=original,
        updated=rendered,
        managed_client_id=(managed.env.get("ATC_CLIENT_ID") if managed is not None else None),
        credential_storage=(
            FALLBACK_CREDENTIAL_STORAGE
            if managed is not None and "ATC_CLIENT_TOKEN" in managed.env
            else OS_CREDENTIAL_STORAGE
            if managed is not None
            else None
        ),
    )


def plan_managed_client_cleanup(
    *,
    codex_path: Path | None = None,
    claude_path: Path | None = None,
) -> tuple[ManagedConfigCleanup, ...]:
    """Preflight current configs and every ATC-created backup before uninstall."""

    active_codex = (codex_path or codex_config_path()).expanduser().resolve()
    active_claude = (claude_path or claude_config_path()).expanduser().resolve()
    # Backups are deliberately first. The active config, which preserves a
    # retryable client ID, is not changed until every generated backup is clean.
    candidates = [
        *(("codex", path) for path in _atc_backup_paths(active_codex)),
        *(("claude", path) for path in _atc_backup_paths(active_claude)),
        ("codex", active_codex),
        ("claude", active_claude),
    ]
    planned: list[ManagedConfigCleanup] = []
    for kind, path in candidates:
        cleanup = _plan_codex_cleanup(path) if kind == "codex" else _plan_claude_cleanup(path)
        if cleanup is not None:
            planned.append(cleanup)
    return tuple(planned)


def apply_managed_client_cleanup(cleanups: tuple[ManagedConfigCleanup, ...]) -> None:
    """Apply only if every preflighted file is unchanged, without making token backups."""

    for cleanup in cleanups:
        try:
            current = cleanup.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"Could not recheck AI client config: {cleanup.path}") from exc
        if current != cleanup.original:
            raise RuntimeError(
                f"AI client config changed during uninstall; retry safely: {cleanup.path}"
            )
    for cleanup in cleanups:
        _atomic_write(cleanup.path, cleanup.updated)


# Codex's lifecycle and explicit controls use separate MCP registrations.  The
# Core lane owns the eventual capture endpoint; these names are the single
# setup/configuration seam shared with that implementation.
_CODEX_MANAGED_BY = "all_the_context_codex"
_CODEX_READ_HOOKS = (
    {
        "type": "mcp_tool",
        "server": CODEX_READ_SERVER_KEY,
        "tool": "bootstrap_context",
        "input": {
            "task_description": "${prompt}",
            "character_budget": 8000,
            "session_id": "${session_id}",
            "turn_id": "${turn_id}",
        },
    },
)
_CODEX_CAPTURE_USER_HOOK = {
    "type": "mcp_tool",
    "server": CODEX_CAPTURE_SERVER_KEY,
    "tool": CODEX_CAPTURE_USER_PROMPT_TOOL,
    "input": {
        "prompt": "${prompt}",
        "session_id": "${session_id}",
        "turn_id": "${turn_id}",
    },
}
_CODEX_CAPTURE_STOP_HOOK = {
    "type": "mcp_tool",
    "server": CODEX_CAPTURE_SERVER_KEY,
    "tool": CODEX_CAPTURE_STOP_TOOL,
    "input": {
        "last_assistant_message": "${last_assistant_message}",
        "session_id": "${session_id}",
        "turn_id": "${turn_id}",
    },
}


@dataclass(frozen=True, slots=True)
class _CodexDocument:
    path: Path
    original: str
    parsed: dict[str, Any]
    existed: bool


@dataclass(frozen=True, slots=True)
class _CodexWritePlan:
    document: _CodexDocument
    updated: str
    remove: bool = False


def codex_skills_dir(path: Path | None = None, *, config_path: Path | None = None) -> Path:
    """Return the user Codex skills directory without resolving link components."""

    configured = os.environ.get(CODEX_SKILLS_DIR_ENV) if path is None else None
    if configured:
        selected = Path(configured)
    elif path is not None:
        selected = path
    else:
        selected = (config_path or _codex_selected_config_path()).parent / "skills"
    return Path(os.path.abspath(selected.expanduser()))


def codex_hooks_path(path: Path | None = None, *, config_path: Path | None = None) -> Path:
    """Return the optional Codex JSON hook layer beside config.toml."""

    selected = path or (
        (config_path or _codex_selected_config_path()).parent / CODEX_HOOKS_FILE_NAME
    )
    return Path(os.path.abspath(selected.expanduser()))


def _codex_selected_config_path(path: Path | None = None) -> Path:
    if path is not None:
        selected = path
    else:
        configured_home = os.environ.get("CODEX_HOME")
        selected = (
            Path(configured_home).expanduser() / "config.toml"
            if configured_home
            else Path.home() / ".codex" / "config.toml"
        )
    return Path(os.path.abspath(selected.expanduser()))


def _codex_reject_linked_path(path: Path) -> None:
    """Reject symlink and Windows reparse-point components before any I/O."""

    current = Path(os.path.abspath(path.expanduser()))
    while True:
        try:
            information = current.lstat()
        except FileNotFoundError:
            information = None
        except OSError as exc:
            raise RuntimeError("Could not verify Codex configuration path") from exc
        if information is not None:
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(information.st_mode) or bool(
                getattr(information, "st_file_attributes", 0) & reparse_flag
            ):
                raise ValueError(
                    "Codex configuration paths may not contain symlinks or reparse points"
                )
        parent = current.parent
        if parent == current:
            return
        current = parent


def _codex_read_text(path: Path) -> tuple[str, bool]:
    _codex_reject_linked_path(path)
    try:
        if not path.exists():
            return "", False
        if not path.is_file():
            raise ValueError("Codex configuration path is not a regular file")
        initial_size = path.stat().st_size
        if initial_size > MAX_CODEX_CONFIG_BYTES:
            raise ValueError("Codex configuration exceeds the bounded read limit")
        with path.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read(MAX_CODEX_CONFIG_BYTES + 1)
        if len(text.encode("utf-8")) > MAX_CODEX_CONFIG_BYTES:
            raise ValueError("Codex configuration exceeds the bounded read limit")
        if path.stat().st_size != initial_size:
            raise RuntimeError("Codex configuration changed while it was being read")
        return text, True
    except OSError as exc:
        raise RuntimeError("Could not read Codex configuration") from exc


def _codex_read_document(path: Path, *, json_document: bool = False) -> _CodexDocument:
    text, existed = _codex_read_text(path)
    if not text:
        return _CodexDocument(path, text, {}, existed)
    parsed = json.loads(text) if json_document else tomllib.loads(text)
    if not isinstance(parsed, dict):
        label = "Codex hooks" if json_document else "Codex configuration"
        raise ValueError(f"{label} must contain an object")
    return _CodexDocument(path, text, parsed, True)


def _codex_token_environment(
    client_id: str,
    token: str | None,
    *,
    credential_storage: str | None,
) -> dict[str, str]:
    """Serialize only an explicitly enabled development fallback credential."""

    if token is None:
        return {}
    if credential_storage == OS_CREDENTIAL_STORAGE:
        raise RuntimeError("refusing to serialize a credential stored in the OS credential store")
    if credential_storage == FALLBACK_CREDENTIAL_STORAGE:
        if not development_file_credentials_enabled():
            require_development_file_credentials()
        return {"ATC_CLIENT_TOKEN": token}
    try:
        stored = KeyringCredentialStore().get(f"client:{client_id}")
    except RuntimeError:
        stored = None
        keyring_available = False
    else:
        keyring_available = True
    if stored is not None:
        if stored != token:
            raise RuntimeError("the supplied credential does not match the OS credential")
        return {}
    if keyring_available:
        raise RuntimeError(
            "refusing to serialize a client credential when the OS credential store is available"
        )
    if not development_file_credentials_enabled():
        require_development_file_credentials()
    return {"ATC_CLIENT_TOKEN": token}


def _render_codex_server(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    profile: str,
    token: str | None,
    credential_storage: str | None,
    target_url: str,
    core_data_dir: Path | None,
    server_key: str,
    enabled_tools: tuple[str, ...],
    approval_mode: str = "approve",
    tool_approval_mode: dict[str, str] | None = None,
) -> list[str]:
    command = runtime.mcp()
    if not command or any(type(item) is not str or not item for item in command):
        raise ValueError("runtime MCP command must contain non-empty strings")
    if not client_id or any(
        ord(character) < 32 or ord(character) == 127 for character in client_id
    ):
        raise ValueError("client ID is invalid")
    environment = {
        "ATC_MANAGED_BY": _CODEX_MANAGED_BY,
        "ATC_MCP_PROFILE": profile,
        "ATC_TARGET_URL": target_url,
        "ATC_CLIENT_ID": client_id,
        "ATC_AUTO_START_CORE": "1",
        "ATC_CORE_COMMAND": json.dumps(runtime.core(), ensure_ascii=False),
        "ATC_CORE_DATA_DIR": str((core_data_dir or CoreConfig.default().data_dir).resolve()),
    }
    environment.update(
        _codex_token_environment(
            client_id,
            token,
            credential_storage=credential_storage,
        )
    )
    rendered_args = ", ".join(json.dumps(argument) for argument in command[1:])
    rendered_env = ", ".join(
        f"{name} = {json.dumps(value)}" for name, value in environment.items()
    )
    lines = [
        f"[mcp_servers.{server_key}]",
        f"command = {json.dumps(command[0])}",
        f"args = [{rendered_args}]",
        f"env = {{ {rendered_env} }}",
        "required = true",
        "startup_timeout_sec = 20",
        f"enabled_tools = [{', '.join(json.dumps(tool) for tool in enabled_tools)}]",
        f"default_tools_approval_mode = {json.dumps(approval_mode)}",
    ]
    for tool in enabled_tools:
        if tool_approval_mode and tool in tool_approval_mode:
            lines.extend(
                [
                    f"[mcp_servers.{server_key}.tools.{tool}]",
                    f"approval_mode = {json.dumps(tool_approval_mode[tool])}",
                ]
            )
    return lines


def _render_codex_hooks_inline(*, capture_enabled: bool) -> list[str]:
    lines = [
        "[[hooks.UserPromptSubmit]]",
        "[[hooks.UserPromptSubmit.hooks]]",
        'type = "mcp_tool"',
        f'server = {json.dumps(CODEX_READ_SERVER_KEY)}',
        'tool = "bootstrap_context"',
        'input = { task_description = "${prompt}", character_budget = 8000, '
        'session_id = "${session_id}", turn_id = "${turn_id}" }',
    ]
    if capture_enabled:
        lines.extend(
            [
                "[[hooks.UserPromptSubmit]]",
                "[[hooks.UserPromptSubmit.hooks]]",
                'type = "mcp_tool"',
                f'server = {json.dumps(CODEX_CAPTURE_SERVER_KEY)}',
                f'tool = {json.dumps(CODEX_CAPTURE_USER_PROMPT_TOOL)}',
                'input = { prompt = "${prompt}", session_id = "${session_id}", '
                'turn_id = "${turn_id}" }',
                "[[hooks.Stop]]",
                "[[hooks.Stop.hooks]]",
                'type = "mcp_tool"',
                f'server = {json.dumps(CODEX_CAPTURE_SERVER_KEY)}',
                f'tool = {json.dumps(CODEX_CAPTURE_STOP_TOOL)}',
                'input = { last_assistant_message = "${last_assistant_message}", '
                'session_id = "${session_id}", turn_id = "${turn_id}" }',
            ]
        )
    return lines


def _codex_json_hook_document(
    document: _CodexDocument,
    *,
    capture_enabled: bool,
) -> tuple[str, dict[str, Any]]:
    hooks = deepcopy(document.parsed)
    hook_map = hooks.setdefault("hooks", {})
    if not isinstance(hook_map, dict):
        raise ValueError("Codex hooks must contain an object")
    for event_name in CODEX_HOOK_EVENT_NAMES:
        current = hook_map.get(event_name, [])
        if not isinstance(current, list):
            raise ValueError(f"Codex hooks.{event_name} must contain an array")
        for group in current:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"Codex hooks.{event_name} groups are invalid")
    desired = {"UserPromptSubmit": [*_CODEX_READ_HOOKS]}
    managed = {
        "UserPromptSubmit": [*_CODEX_READ_HOOKS, _CODEX_CAPTURE_USER_HOOK],
        "Stop": [_CODEX_CAPTURE_STOP_HOOK],
    }
    if capture_enabled:
        desired["UserPromptSubmit"].append(_CODEX_CAPTURE_USER_HOOK)
        desired["Stop"] = [_CODEX_CAPTURE_STOP_HOOK]
    for event_name, handlers in managed.items():
        groups = hook_map.setdefault(event_name, [])
        kept: list[dict[str, Any]] = []
        for group in groups:
            group_copy = deepcopy(group)
            group_copy["hooks"] = [
                candidate for candidate in group["hooks"] if candidate not in handlers
            ]
            if group_copy["hooks"] or set(group_copy) != {"hooks"}:
                kept.append(group_copy)
        if handlers_to_keep := desired.get(event_name):
            kept.append({"hooks": [deepcopy(handler) for handler in handlers_to_keep]})
            hook_map[event_name] = kept
        else:
            hook_map.pop(event_name, None)
    return json.dumps(hooks, indent=2, ensure_ascii=False, allow_nan=False) + "\n", hooks


def _codex_is_managed_server(value: object, *, expected_profile: str | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    env = value.get("env")
    if not isinstance(env, dict):
        return False
    if env.get("ATC_MANAGED_BY") == _CODEX_MANAGED_BY:
        return expected_profile is None or env.get("ATC_MCP_PROFILE") == expected_profile
    # The previous Codex adapter did not stamp a profile.  Its managed marker
    # and client identity are sufficient to migrate that one legacy entry.
    return expected_profile is None and isinstance(env.get("ATC_CLIENT_ID"), str)


def _codex_existing_hook_layer(
    config: _CodexDocument, hooks: _CodexDocument
) -> str:
    config_has_hooks = "hooks" in config.parsed
    hooks_exists = hooks.existed
    if config_has_hooks and hooks_exists:
        raise ValueError(
            "Codex has hooks in both config.toml and hooks.json; remove one layer before setup"
        )
    if config_has_hooks and not hooks_exists:
        marker_present = MANAGED_BEGIN in config.original
        hook_map = config.parsed.get("hooks")
        known_handlers = (
            *_CODEX_READ_HOOKS,
            _CODEX_CAPTURE_USER_HOOK,
            _CODEX_CAPTURE_STOP_HOOK,
        )
        if not marker_present or not isinstance(hook_map, dict):
            raise ValueError("Codex inline hooks could not be safely preserved")
        for event_name, groups in hook_map.items():
            if event_name not in CODEX_HOOK_EVENT_NAMES or not isinstance(groups, list):
                raise ValueError("Codex inline hooks could not be safely preserved")
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    raise ValueError("Codex inline hooks could not be safely preserved")
                if any(candidate not in known_handlers for candidate in group["hooks"]):
                    raise ValueError("Codex inline hooks could not be safely preserved")
    return "json" if hooks_exists else "inline"


def _codex_document_with_managed_block(
    document: _CodexDocument,
    block: str | None,
) -> str:
    existing = document.original
    marker_pattern = re.compile(
        rf"(?ms)^[ \t]*{re.escape(MANAGED_BEGIN)}\r?\n.*?"
        rf"^[ \t]*{re.escape(MANAGED_END)}[ \t]*(?:\r?\n)?"
    )
    if marker_pattern.search(existing):
        return marker_pattern.sub(
            lambda _match: f"{block}\n" if block else "",
            existing,
            count=1,
        )
    # A legacy managed table may be unmarked.  Remove only the reserved ATC
    # server namespace, retaining all unrelated Codex configuration verbatim.
    cleaned = _remove_codex_tables(existing).rstrip()
    if block:
        return f"{cleaned}\n\n{block}\n" if cleaned else f"{block}\n"
    return f"{cleaned}\n" if cleaned else ""


def _codex_validate_reserved_servers(parsed: dict[str, Any]) -> None:
    servers = parsed.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcp_servers must contain a TOML table")
    expected = {
        CODEX_READ_SERVER_KEY: None,
        CODEX_CAPTURE_SERVER_KEY: CODEX_CAPTURE_PROFILE,
        CODEX_EXPLICIT_SERVER_KEY: CODEX_EXPLICIT_PROFILE,
    }
    for key, profile in expected.items():
        if key in servers and not _codex_is_managed_server(servers[key], expected_profile=profile):
            raise ValueError(f"Codex server key {key} belongs to an unrelated server")


def read_codex_registration_ids(path: Path | None = None) -> dict[str, str]:
    """Read managed Codex registration IDs for repair without exposing credentials."""

    config_path = _codex_selected_config_path(path)
    document = _codex_read_document(config_path)
    _codex_validate_reserved_servers(document.parsed)
    servers = document.parsed.get("mcp_servers", {})
    if not isinstance(servers, dict):
        return {}
    keys = {
        CODEX_READ_SERVER_KEY: "read",
        CODEX_CAPTURE_SERVER_KEY: "capture",
        CODEX_EXPLICIT_SERVER_KEY: "explicit",
    }
    result: dict[str, str] = {}
    for server_key, purpose in keys.items():
        server = servers.get(server_key)
        if not _codex_is_managed_server(server):
            continue
        environment = server.get("env") if isinstance(server, dict) else None
        client_id = environment.get("ATC_CLIENT_ID") if isinstance(environment, dict) else None
        if isinstance(client_id, str) and client_id:
            result[purpose] = client_id
    return result


def _codex_skill_content(name: str) -> str:
    descriptions = {
        "atc-remember": "Store an exact user-stated context item in All The Context.",
        "atc-correct": "Correct one exact All The Context record.",
        "atc-forget": "Forget one exact All The Context record.",
    }
    instructions = {
        "atc-remember": (
            "Call only the Codex explicit All The Context MCP server. Use propose_memory with "
            "the user's exact words, explicit_user_statement=true, and no inferred content."
        ),
        "atc-correct": (
            "Call only the Codex explicit All The Context MCP server. Identify the requested "
            "record and submit the user's exact correction as an explicit proposal."
        ),
        "atc-forget": (
            "Call only the Codex explicit All The Context MCP server. Use forget_context only "
            "for the exact record the user asked to forget."
        ),
    }
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {descriptions[name]}\n"
        "---\n\n"
        f"{instructions[name]}\n"
        "This is a precision override, not the ordinary memory workflow. Never invoke it "
        "implicitly and never use it for routine conversational observation.\n"
    )


def _codex_skill_policy() -> str:
    return "policy:\n  allow_implicit_invocation: false\n"


def _codex_skill_plans(
    skills_dir: Path, *, remove: bool = False
) -> tuple[tuple[_CodexWritePlan, ...], tuple[Path, ...]]:
    paths: list[Path] = []
    plans: list[_CodexWritePlan] = []
    for name in CODEX_EXPLICIT_COMMANDS:
        skill_dir = skills_dir / name
        path = skill_dir / "SKILL.md"
        policy_path = skill_dir / "agents" / "openai.yaml"
        paths.extend((path, policy_path))
        for candidate, managed_content in (
            (path, _codex_skill_content(name)),
            (policy_path, _codex_skill_policy()),
        ):
            text, existed = _codex_read_text(candidate)
            document = _CodexDocument(candidate, text, {}, existed)
            if document.existed and document.original != managed_content:
                if remove:
                    plans.append(_CodexWritePlan(document, document.original))
                    continue
                raise ValueError(
                    f"the reserved Codex skill path for {name} belongs to an unrelated skill"
                )
            plans.append(
                _CodexWritePlan(
                    document,
                    "" if remove else managed_content,
                    remove=remove and document.existed,
                )
            )
    return tuple(plans), tuple(paths)


def _codex_backup(path: Path) -> Path:
    _codex_reject_linked_path(path)
    backup = path.with_name(
        f"{path.name}.atc-backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-"
        f"{secrets.token_hex(3)}"
    )
    _codex_reject_linked_path(backup)
    if backup.exists():
        raise RuntimeError("could not allocate a private Codex backup path")
    shutil.copy2(path, backup)
    return backup


def _codex_atomic_write(path: Path, content: str) -> None:
    _codex_reject_linked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
    _codex_reject_linked_path(temporary)
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
        temporary.replace(path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _codex_revalidate(plan: _CodexWritePlan) -> None:
    current, exists = _codex_read_text(plan.document.path)
    if exists != plan.document.existed or current != plan.document.original:
        raise RuntimeError("Codex configuration changed during setup; retry safely")


def _codex_restore(plan: _CodexWritePlan) -> None:
    current, exists = _codex_read_text(plan.document.path)
    if plan.document.existed:
        if not exists:
            _codex_atomic_write(plan.document.path, plan.document.original)
        elif current == plan.document.original:
            return
        elif current == plan.updated and not plan.remove:
            _codex_atomic_write(plan.document.path, plan.document.original)
        else:
            raise RuntimeError("Codex configuration changed during rollback")
    elif exists:
        if plan.remove or current != plan.updated:
            raise RuntimeError("an unrelated Codex file appeared during rollback")
        plan.document.path.unlink()


def _codex_apply_transaction(plans: tuple[_CodexWritePlan, ...]) -> dict[Path, Path | None]:
    changed = tuple(plan for plan in plans if plan.remove or plan.updated != plan.document.original)
    if not changed:
        return {}
    for plan in changed:
        _codex_reject_linked_path(plan.document.path)
        plan.document.path.parent.mkdir(parents=True, exist_ok=True)
    backups: dict[Path, Path | None] = {}
    attempted: list[_CodexWritePlan] = []
    try:
        for plan in changed:
            _codex_revalidate(plan)
            backups[plan.document.path] = (
                _codex_backup(plan.document.path) if plan.document.existed else None
            )
            _codex_revalidate(plan)
            attempted.append(plan)
            if plan.remove:
                plan.document.path.unlink()
            else:
                _codex_atomic_write(plan.document.path, plan.updated)
    except BaseException:
        rollback_error: BaseException | None = None
        for plan in reversed(attempted):
            try:
                _codex_restore(plan)
            except BaseException as candidate:
                rollback_error = candidate
                break
        for backup in backups.values():
            if backup is not None:
                with suppress(OSError):
                    backup.unlink(missing_ok=True)
        if rollback_error is not None:
            raise RuntimeError("Codex configuration failed and could not be rolled back") from (
                rollback_error
            )
        raise
    return backups


def configure_codex_integration(
    runtime: RuntimeCommand,
    *,
    read_client_id: str,
    read_token: str | None = None,
    read_credential_storage: str | None = None,
    capture_client_id: str | None = None,
    capture_token: str | None = None,
    capture_credential_storage: str | None = None,
    explicit_client_id: str | None = None,
    explicit_token: str | None = None,
    explicit_credential_storage: str | None = None,
    target_url: str = "http://127.0.0.1:7337",
    core_data_dir: Path | None = None,
    path: Path | None = None,
    hooks_path: Path | None = None,
    skills_dir: Path | None = None,
) -> ClientConfigResult:
    """Install least-privilege Codex registrations and optional lifecycle controls."""

    config_path = _codex_selected_config_path(path)
    hooks_file = codex_hooks_path(hooks_path, config_path=config_path)
    selected_skills_dir = codex_skills_dir(skills_dir, config_path=config_path)
    config = _codex_read_document(config_path)
    hooks = _codex_read_document(hooks_file, json_document=True)
    _codex_validate_reserved_servers(config.parsed)
    hook_layer = _codex_existing_hook_layer(config, hooks)
    capture_enabled = capture_client_id is not None
    explicit_enabled = explicit_client_id is not None
    server_lines = [MANAGED_BEGIN]
    server_lines.extend(
        _render_codex_server(
            runtime,
            read_client_id,
            profile=CODEX_READ_PROFILE,
            token=read_token,
            credential_storage=read_credential_storage,
            target_url=target_url,
            core_data_dir=core_data_dir,
            server_key=CODEX_READ_SERVER_KEY,
            enabled_tools=CODEX_READ_TOOLS,
        )
    )
    if capture_enabled:
        assert capture_client_id is not None
        server_lines.extend(
            _render_codex_server(
                runtime,
                capture_client_id,
                profile=CODEX_CAPTURE_PROFILE,
                token=capture_token,
                credential_storage=capture_credential_storage,
                target_url=target_url,
                core_data_dir=core_data_dir,
                server_key=CODEX_CAPTURE_SERVER_KEY,
                enabled_tools=CODEX_CAPTURE_TOOLS,
            )
        )
    if explicit_enabled:
        assert explicit_client_id is not None
        server_lines.extend(
            _render_codex_server(
                runtime,
                explicit_client_id,
                profile=CODEX_EXPLICIT_PROFILE,
                token=explicit_token,
                credential_storage=explicit_credential_storage,
                target_url=target_url,
                core_data_dir=core_data_dir,
                server_key=CODEX_EXPLICIT_SERVER_KEY,
                enabled_tools=CODEX_EXPLICIT_TOOLS,
                tool_approval_mode={tool: "prompt" for tool in CODEX_EXPLICIT_TOOLS},
            )
        )
    if hook_layer == "inline":
        server_lines.extend(_render_codex_hooks_inline(capture_enabled=capture_enabled))
    server_lines.append(MANAGED_END)
    updated_config = _codex_document_with_managed_block(
        config,
        "\n".join(server_lines),
    )
    tomllib.loads(updated_config)
    plans: list[_CodexWritePlan] = [_CodexWritePlan(config, updated_config)]
    if hook_layer == "json":
        updated_hooks, _ = _codex_json_hook_document(
            hooks,
            capture_enabled=capture_enabled,
        )
        plans.append(_CodexWritePlan(hooks, updated_hooks))
    skill_paths: tuple[Path, ...] = ()
    if explicit_enabled:
        skill_plans, skill_paths = _codex_skill_plans(selected_skills_dir)
        plans.extend(skill_plans)
    backups = _codex_apply_transaction(tuple(plans))
    changed = any(
        plan.remove or plan.updated != plan.document.original for plan in plans
    )
    return ClientConfigResult(
        "Codex",
        config_path,
        backups.get(config_path),
        changed,
        read_client_id,
        tuple(
            client_id
            for client_id in (read_client_id, capture_client_id, explicit_client_id)
            if client_id is not None
        ),
        any(
            plan.document.path in skill_paths
            and (plan.remove or plan.updated != plan.document.original)
            for plan in plans
        ),
        skill_paths,
    )


def disconnect_codex_integration(
    path: Path | None = None,
    *,
    hooks_path: Path | None = None,
    skills_dir: Path | None = None,
) -> ClientConfigResult:
    """Remove only the managed Codex lifecycle/configuration surfaces."""

    config_path = _codex_selected_config_path(path)
    hooks_file = codex_hooks_path(hooks_path, config_path=config_path)
    selected_skills_dir = codex_skills_dir(skills_dir, config_path=config_path)
    config = _codex_read_document(config_path)
    hooks = _codex_read_document(hooks_file, json_document=True)
    _codex_validate_reserved_servers(config.parsed)
    hook_layer = _codex_existing_hook_layer(config, hooks)
    servers = config.parsed.get("mcp_servers", {})
    managed_ids: list[str] = []
    if isinstance(servers, dict):
        for key in (
            CODEX_READ_SERVER_KEY,
            CODEX_CAPTURE_SERVER_KEY,
            CODEX_EXPLICIT_SERVER_KEY,
        ):
            value = servers.get(key)
            if _codex_is_managed_server(value):
                env = value.get("env") if isinstance(value, dict) else None
                if isinstance(env, dict) and isinstance(env.get("ATC_CLIENT_ID"), str):
                    managed_ids.append(env["ATC_CLIENT_ID"])
    updated_config = _codex_document_with_managed_block(config, None)
    tomllib.loads(updated_config)
    plans: list[_CodexWritePlan] = [_CodexWritePlan(config, updated_config)]
    if hook_layer == "json" and hooks.existed:
        updated_hooks = deepcopy(hooks.parsed)
        hook_map = updated_hooks.get("hooks")
        if not isinstance(hook_map, dict):
            raise ValueError("Codex hooks must contain an object")
        for event_name, handlers in (
            ("UserPromptSubmit", [*_CODEX_READ_HOOKS, _CODEX_CAPTURE_USER_HOOK]),
            ("Stop", [_CODEX_CAPTURE_STOP_HOOK]),
        ):
            groups = hook_map.get(event_name)
            if not isinstance(groups, list):
                continue
            kept_groups: list[dict[str, Any]] = []
            for group in groups:
                if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                    raise ValueError(f"Codex hooks.{event_name} groups are invalid")
                remaining = [candidate for candidate in group["hooks"] if candidate not in handlers]
                if remaining or set(group) != {"hooks"}:
                    group_copy = deepcopy(group)
                    group_copy["hooks"] = remaining
                    kept_groups.append(group_copy)
            if kept_groups:
                hook_map[event_name] = kept_groups
            else:
                hook_map.pop(event_name, None)
        if not hook_map:
            updated_hooks.pop("hooks", None)
        rendered_hooks = json.dumps(
            updated_hooks, indent=2, ensure_ascii=False, allow_nan=False
        ) + "\n"
        plans.append(_CodexWritePlan(hooks, rendered_hooks, remove=not updated_hooks))
    skill_plans, skill_paths = _codex_skill_plans(selected_skills_dir, remove=True)
    plans.extend(skill_plans)
    backups = _codex_apply_transaction(tuple(plans))
    return ClientConfigResult(
        "Codex",
        config_path,
        backups.get(config_path),
        any(plan.remove or plan.updated != plan.document.original for plan in plans),
        managed_ids[0] if managed_ids else None,
        tuple(managed_ids),
        any(
            plan.document.path in skill_paths
            and (plan.remove or plan.updated != plan.document.original)
            for plan in plans
        ),
        skill_paths,
    )
