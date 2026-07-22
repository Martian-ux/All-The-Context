"""Safe, inspectable, and reversible MCP client configuration adapters."""

from __future__ import annotations

import json
import os
import platform
import re
import secrets
import shutil
import tomllib
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .credentials import FALLBACK_CREDENTIAL_STORAGE, OS_CREDENTIAL_STORAGE
from .desktop_runtime import RuntimeCommand

MANAGED_BEGIN = "# BEGIN All The Context managed MCP"
MANAGED_END = "# END All The Context managed MCP"
TABLE_HEADER = "[mcp_servers.all_the_context]"
TABLE_PATH = ("mcp_servers", "all_the_context")
CLAUDE_SERVER_KEY = "all-the-context"


@dataclass(frozen=True, slots=True)
class ClientConfigResult:
    client: str
    path: Path
    backup_path: Path | None
    changed: bool
    managed_client_id: str | None = None


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
    home = codex_home()
    return home.is_dir() or (Path.home() / ".codex").is_dir()


def claude_config_path() -> Path:
    configured = os.environ.get("ATC_CLAUDE_CONFIG")
    if configured:
        return Path(configured).expanduser().resolve()
    system = platform.system()
    if system == "Windows":
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
    path = claude_config_path()
    return path.is_file() or path.parent.is_dir()


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


def render_codex_mcp_block(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    token: str | None,
    target_url: str = "http://127.0.0.1:7337",
) -> str:
    mcp_command = runtime.mcp()
    arguments = list(mcp_command[1:])
    environment = {
        "ATC_TARGET_URL": target_url,
        "ATC_CLIENT_ID": client_id,
        "ATC_AUTO_START_CORE": "1",
        "ATC_CORE_COMMAND": json.dumps(runtime.core(), ensure_ascii=False),
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


def configure_codex(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    token: str | None,
    path: Path | None = None,
    target_url: str = "http://127.0.0.1:7337",
) -> ClientConfigResult:
    config_path = (path or codex_config_path()).expanduser().resolve()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    if existing:
        tomllib.loads(existing)
    block = render_codex_mcp_block(runtime, client_id, token=token, target_url=target_url)
    updated = _replace_managed_section(existing, block)
    tomllib.loads(updated)
    if updated == existing:
        return ClientConfigResult("Codex", config_path, None, False)
    backup = _backup(config_path) if config_path.is_file() else None
    _atomic_write(config_path, updated)
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
    backup = _backup(config_path)
    _atomic_write(config_path, updated)
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
    backup = _backup(config_path) if config_path.is_file() else None
    _atomic_write(config_path, rendered)
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
    backup = _backup(config_path)
    _atomic_write(config_path, rendered)
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
