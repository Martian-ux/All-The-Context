"""Transactional configuration for the Claude Code user integration.

Claude Code keeps its user-scope MCP registry and settings in separate JSON
files.  This module owns only the All The Context server and UserPromptSubmit
hook it creates; all other JSON values are treated as user-owned data.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .credentials import (
    KeyringCredentialStore,
    development_file_credentials_enabled,
    require_development_file_credentials,
)
from .desktop_runtime import RuntimeCommand

CLAUDE_CODE_MCP_SERVER_KEY = "all-the-context-claude-code"
CLAUDE_CODE_HOOK_TOOL = "claude_code_user_prompt_submit"
CLAUDE_CODE_MCP_PROFILE = "claude_code_hook"

CLAUDE_CODE_MCP_CONFIG_ENV = "ATC_CLAUDE_CODE_MCP_CONFIG"
CLAUDE_CODE_SETTINGS_ENV = "ATC_CLAUDE_CODE_SETTINGS"

# Claude Code configuration is normally small.  Refusing unexpectedly large
# input prevents an accidental unbounded read of a user-owned file.
MAX_CLAUDE_CODE_CONFIG_BYTES = 4 * 1024 * 1024

_MANAGED_HOOK_HANDLER: dict[str, Any] = {
    "type": "mcp_tool",
    "server": CLAUDE_CODE_MCP_SERVER_KEY,
    "tool": CLAUDE_CODE_HOOK_TOOL,
    "input": {
        "prompt": "${prompt}",
        "cwd": "${cwd}",
        "session_id": "${session_id}",
    },
}


@dataclass(frozen=True, slots=True)
class ClaudeCodeConfigPaths:
    """The two user-scope files participating in one configuration change."""

    mcp: Path
    settings: Path


@dataclass(frozen=True, slots=True)
class ClaudeCodeConfigResult:
    """Content-free result for setup integration and status reporting."""

    client: str
    mcp_path: Path
    settings_path: Path
    changed: bool
    mcp_changed: bool
    settings_changed: bool
    mcp_backup_path: Path | None = None
    settings_backup_path: Path | None = None
    managed_client_id: str | None = None

    @property
    def backup_paths(self) -> tuple[Path, ...]:
        """Return only backups created by this operation."""

        return tuple(
            path for path in (self.mcp_backup_path, self.settings_backup_path) if path is not None
        )


@dataclass(frozen=True, slots=True)
class _Document:
    path: Path
    original: str
    parsed: dict[str, Any]
    existed: bool


@dataclass(frozen=True, slots=True)
class _WritePlan:
    document: _Document
    updated: str


def _user_home() -> Path:
    """Resolve the OS user home without consulting the current working tree."""

    if platform.system() == "Windows":
        configured = os.environ.get("USERPROFILE")
        if configured:
            return Path(configured).expanduser().resolve()
        drive = os.environ.get("HOMEDRIVE")
        tail = os.environ.get("HOMEPATH")
        if drive and tail:
            return Path(f"{drive}{tail}").expanduser().resolve()
    else:
        configured = os.environ.get("HOME")
        if configured:
            return Path(configured).expanduser().resolve()
    return Path.home().expanduser().resolve()


def _resolve_override(path: Path | None, default: Path, environment_name: str) -> Path:
    configured = os.environ.get(environment_name) if path is None else None
    selected = path if path is not None else Path(configured) if configured else default
    return selected.expanduser().resolve()


def claude_code_mcp_config_path(path: Path | None = None) -> Path:
    """Return the Claude Code user MCP registry path.

    The optional path and dedicated environment override exist for isolated
    tests and later setup integration; the production default is never
    derived from the current working tree.
    ``ATC_CLAUDE_CONFIG`` is deliberately not consulted because it belongs to
    the separate Claude Desktop integration.
    """

    return _resolve_override(
        path,
        _user_home() / ".claude.json",
        CLAUDE_CODE_MCP_CONFIG_ENV,
    )


def claude_code_settings_path(path: Path | None = None) -> Path:
    """Return the Claude Code user settings path."""

    return _resolve_override(
        path,
        _user_home() / ".claude" / "settings.json",
        CLAUDE_CODE_SETTINGS_ENV,
    )


def claude_code_config_paths(
    *, mcp_path: Path | None = None, settings_path: Path | None = None
) -> ClaudeCodeConfigPaths:
    """Resolve both user files, with explicit overrides for isolated callers."""

    paths = ClaudeCodeConfigPaths(
        mcp=claude_code_mcp_config_path(mcp_path),
        settings=claude_code_settings_path(settings_path),
    )
    if paths.mcp == paths.settings:
        raise ValueError("Claude Code MCP and settings paths must be different")
    return paths


def managed_claude_code_hook_handler() -> dict[str, Any]:
    """Return a defensive copy of the exact handler owned by this module."""

    return deepcopy(_MANAGED_HOOK_HANDLER)


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON objects with duplicate keys are not accepted")
        result[key] = value
    return result


def _reject_constant(_value: str) -> Any:
    raise ValueError("non-standard JSON constants are not accepted")


def _read_bounded_text(path: Path) -> tuple[str, bool]:
    try:
        exists = path.exists()
        if not exists:
            return "", False
        if not path.is_file():
            raise ValueError("Claude Code configuration path is not a regular file")
        initial_size = path.stat().st_size
        if initial_size > MAX_CLAUDE_CODE_CONFIG_BYTES:
            raise ValueError("Claude Code configuration exceeds the bounded read limit")
        with path.open("r", encoding="utf-8", newline="") as handle:
            text = handle.read(MAX_CLAUDE_CODE_CONFIG_BYTES + 1)
        if len(text.encode("utf-8")) > MAX_CLAUDE_CODE_CONFIG_BYTES:
            raise ValueError("Claude Code configuration exceeds the bounded read limit")
        if path.stat().st_size != initial_size:
            raise RuntimeError("Claude Code configuration changed while it was being read")
    except OSError as exc:
        raise RuntimeError("Could not read Claude Code configuration") from exc
    return text, True


def _read_document(path: Path) -> _Document:
    text, existed = _read_bounded_text(path)

    if text == "":
        return _Document(path, text, {}, existed)
    parsed = json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_constant,
    )
    if not isinstance(parsed, dict):
        raise ValueError("Claude Code configuration must contain a JSON object")
    return _Document(path, text, parsed, True)


def _render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _validate_text(value: str, *, label: str, maximum: int = 1_000) -> str:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"{label} must be a bounded non-empty string")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{label} contains control characters")
    return value


def _validate_target_url(target_url: str) -> str:
    value = _validate_text(target_url, label="target URL", maximum=2_000)
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("target URL must be an HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("target URL must not contain credentials")
    return value


def _keyring_token(client_id: str) -> tuple[bool, str | None]:
    try:
        return True, KeyringCredentialStore().get(f"client:{client_id}")
    except RuntimeError:
        return False, None


def _token_environment(client_id: str, token: str | None) -> dict[str, str]:
    """Keep production credentials out of JSON while retaining explicit dev fallback."""

    if token is None:
        return {}
    _validate_text(token, label="client token", maximum=16_384)

    keyring_available, stored = _keyring_token(client_id)
    if stored is not None:
        if stored != token:
            raise RuntimeError("the supplied credential does not match the OS credential")
        # The adapter resolves this same client:<id> entry at runtime.
        return {}

    if keyring_available:
        # A caller that has a working OS keyring should pass token=None.  A
        # missing entry is not permission to copy a production credential into
        # a user-owned JSON file.
        raise RuntimeError(
            "refusing to serialize a client credential when the OS credential store is available"
        )

    # This is the same explicit opt-in boundary used by the existing
    # credential helpers.  The MCP adapter receives this value only because
    # no OS credential lookup is available in this development configuration.
    if not development_file_credentials_enabled():
        require_development_file_credentials()
    return {"ATC_CLIENT_TOKEN": token}


def _mcp_server(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    token: str | None,
    target_url: str,
) -> dict[str, Any]:
    command = runtime.mcp()
    if not command or any(type(item) is not str or not item for item in command):
        raise ValueError("runtime MCP command must contain non-empty strings")
    validated_client_id = _validate_text(client_id, label="client ID")
    env = {
        "ATC_MCP_PROFILE": CLAUDE_CODE_MCP_PROFILE,
        "ATC_TARGET_URL": _validate_target_url(target_url),
        "ATC_CLIENT_ID": validated_client_id,
    }
    env.update(_token_environment(validated_client_id, token))
    return {
        "type": "stdio",
        "command": command[0],
        "args": list(command[1:]),
        "env": env,
    }


def _is_managed_server(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    env = value.get("env")
    return isinstance(env, dict) and env.get("ATC_MCP_PROFILE") == CLAUDE_CODE_MCP_PROFILE


def _validate_hook_groups(groups: object) -> list[dict[str, Any]]:
    if not isinstance(groups, list):
        raise ValueError("hooks.UserPromptSubmit must contain a JSON array")
    validated: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("hooks.UserPromptSubmit groups must be JSON objects")
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            raise ValueError("hooks.UserPromptSubmit groups must contain a hooks array")
        if any(not isinstance(handler, dict) for handler in handlers):
            raise ValueError("UserPromptSubmit hook handlers must be JSON objects")
        validated.append(group)
    return validated


def _remove_managed_handlers(
    groups: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int, bool]:
    cleaned: list[dict[str, Any]] = []
    removed = 0
    moved_from_matcher = False
    for original_group in groups:
        handlers = original_group["hooks"]
        kept = [handler for handler in handlers if handler != _MANAGED_HOOK_HANDLER]
        removed_here = len(handlers) - len(kept)
        removed += removed_here
        if removed_here and "matcher" in original_group:
            moved_from_matcher = True
        if removed_here == 0:
            cleaned.append(original_group)
            continue
        group = deepcopy(original_group)
        group["hooks"] = kept
        # Remove only the empty group that this module creates.  A matcher or
        # any other group field remains user-owned and is retained.
        if kept or set(group) != {"hooks"}:
            cleaned.append(group)
    return cleaned, removed, moved_from_matcher


def _configured_hook_groups(groups: object) -> list[dict[str, Any]]:
    validated = _validate_hook_groups(groups)
    cleaned, count, moved_from_matcher = _remove_managed_handlers(validated)
    if count == 1 and not moved_from_matcher:
        # Keep the one exact handler where the user already placed it.  The
        # settings document is already a defensive copy, so returning the
        # validated groups preserves idempotence and all surrounding fields.
        return validated
    cleaned.append({"hooks": [deepcopy(_MANAGED_HOOK_HANDLER)]})
    return cleaned


def _updated_connect_documents(
    mcp: _Document,
    settings: _Document,
    server: dict[str, Any],
) -> tuple[str, str]:
    mcp_updated = deepcopy(mcp.parsed)
    servers = mcp_updated.get("mcpServers")
    if "mcpServers" not in mcp_updated:
        servers = {}
        mcp_updated["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must contain a JSON object")
    if CLAUDE_CODE_MCP_SERVER_KEY in servers and not _is_managed_server(
        servers[CLAUDE_CODE_MCP_SERVER_KEY]
    ):
        raise ValueError("the Claude Code server key belongs to an unrelated server")
    servers[CLAUDE_CODE_MCP_SERVER_KEY] = server

    settings_updated = deepcopy(settings.parsed)
    hooks = settings_updated.get("hooks")
    if "hooks" not in settings_updated:
        hooks = {}
        settings_updated["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError("hooks must contain a JSON object")
    user_prompt_submit = hooks.get("UserPromptSubmit")
    if "UserPromptSubmit" not in hooks:
        user_prompt_submit = []
        hooks["UserPromptSubmit"] = user_prompt_submit
    hooks["UserPromptSubmit"] = _configured_hook_groups(user_prompt_submit)
    return (
        _render(mcp_updated) if mcp_updated != mcp.parsed else mcp.original,
        _render(settings_updated) if settings_updated != settings.parsed else settings.original,
    )


def _updated_disconnect_documents(
    mcp: _Document,
    settings: _Document,
) -> tuple[str, str, str | None]:
    mcp_updated = deepcopy(mcp.parsed)
    servers = mcp_updated.get("mcpServers")
    managed_client_id: str | None = None
    if "mcpServers" in mcp_updated and not isinstance(servers, dict):
        raise ValueError("mcpServers must contain a JSON object")
    mcp_removed = False
    if isinstance(servers, dict):
        current = servers.get(CLAUDE_CODE_MCP_SERVER_KEY)
        if isinstance(current, dict) and _is_managed_server(current):
            env = current.get("env")
            if isinstance(env, dict) and isinstance(env.get("ATC_CLIENT_ID"), str):
                managed_client_id = env["ATC_CLIENT_ID"]
            del servers[CLAUDE_CODE_MCP_SERVER_KEY]
            mcp_removed = True

    settings_updated = deepcopy(settings.parsed)
    hooks = settings_updated.get("hooks")
    if "hooks" in settings_updated and not isinstance(hooks, dict):
        raise ValueError("hooks must contain a JSON object")
    settings_removed = False
    if isinstance(hooks, dict) and "UserPromptSubmit" in hooks:
        groups = _validate_hook_groups(hooks["UserPromptSubmit"])
        cleaned, removed, _moved = _remove_managed_handlers(groups)
        hooks["UserPromptSubmit"] = cleaned
        settings_removed = removed > 0
    return (
        _render(mcp_updated) if mcp_removed else mcp.original,
        _render(settings_updated) if settings_removed else settings.original,
        managed_client_id,
    )


def _backup(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.atc-backup-{timestamp}-{secrets.token_hex(3)}")
    if backup.exists():
        raise RuntimeError("could not allocate a private Claude Code backup path")
    shutil.copy2(path, backup)
    return backup


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
        temporary.replace(path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _restore(plan: _WritePlan) -> None:
    if plan.document.existed:
        current, exists = _read_bounded_text(plan.document.path)
        if not exists:
            raise RuntimeError("the original Claude Code file disappeared during rollback")
        if current == plan.document.original:
            return
        if current != plan.updated:
            raise RuntimeError("the Claude Code file changed during rollback")
        _atomic_write(plan.document.path, plan.document.original)
    else:
        current, exists = _read_bounded_text(plan.document.path)
        if not exists:
            return
        if current != plan.updated:
            raise RuntimeError("an unrelated Claude Code file appeared during rollback")
        plan.document.path.unlink()


def _apply_transaction(plans: tuple[_WritePlan, _WritePlan]) -> tuple[Path | None, Path | None]:
    changed = tuple(plan for plan in plans if plan.updated != plan.document.original)
    if not changed:
        return None, None

    for plan in changed:
        plan.document.path.parent.mkdir(parents=True, exist_ok=True)

    backups: dict[Path, Path | None] = {}
    attempted: list[_WritePlan] = []
    try:
        for plan in changed:
            attempted.append(plan)
            backups[plan.document.path] = (
                _backup(plan.document.path) if plan.document.existed else None
            )
            _atomic_write(plan.document.path, plan.updated)
    except BaseException:
        rollback_error: BaseException | None = None
        for plan in reversed(attempted):
            try:
                _restore(plan)
            except BaseException as candidate:
                rollback_error = candidate
                break
        if rollback_error is not None:
            raise RuntimeError(
                "Claude Code configuration failed and could not be rolled back"
            ) from rollback_error
        raise

    return backups.get(plans[0].document.path), backups.get(plans[1].document.path)


def _result(
    paths: ClaudeCodeConfigPaths,
    plans: tuple[_WritePlan, _WritePlan],
    backups: tuple[Path | None, Path | None],
    *,
    managed_client_id: str | None = None,
) -> ClaudeCodeConfigResult:
    mcp_changed = plans[0].updated != plans[0].document.original
    settings_changed = plans[1].updated != plans[1].document.original
    return ClaudeCodeConfigResult(
        client="Claude Code",
        mcp_path=paths.mcp,
        settings_path=paths.settings,
        changed=mcp_changed or settings_changed,
        mcp_changed=mcp_changed,
        settings_changed=settings_changed,
        mcp_backup_path=backups[0],
        settings_backup_path=backups[1],
        managed_client_id=managed_client_id,
    )


def connect_claude_code(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    token: str | None = None,
    target_url: str = "http://127.0.0.1:7337",
    mcp_path: Path | None = None,
    settings_path: Path | None = None,
) -> ClaudeCodeConfigResult:
    """Install or refresh the exact user-scope Claude Code integration."""

    paths = claude_code_config_paths(mcp_path=mcp_path, settings_path=settings_path)
    mcp = _read_document(paths.mcp)
    settings = _read_document(paths.settings)
    server = _mcp_server(runtime, client_id, token=token, target_url=target_url)
    mcp_updated, settings_updated = _updated_connect_documents(mcp, settings, server)
    plans = (_WritePlan(mcp, mcp_updated), _WritePlan(settings, settings_updated))
    backups = _apply_transaction(plans)
    return _result(paths, plans, backups)


def configure_claude_code(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    token: str | None = None,
    target_url: str = "http://127.0.0.1:7337",
    mcp_path: Path | None = None,
    settings_path: Path | None = None,
) -> ClaudeCodeConfigResult:
    """Compatibility spelling for setup code that uses configure_* adapters."""

    return connect_claude_code(
        runtime,
        client_id,
        token=token,
        target_url=target_url,
        mcp_path=mcp_path,
        settings_path=settings_path,
    )


def disconnect_claude_code(
    mcp_path: Path | None = None, settings_path: Path | None = None
) -> ClaudeCodeConfigResult:
    """Remove only the exact server and hook handler owned by this module."""

    paths = claude_code_config_paths(mcp_path=mcp_path, settings_path=settings_path)
    mcp = _read_document(paths.mcp)
    settings = _read_document(paths.settings)
    mcp_updated, settings_updated, managed_client_id = _updated_disconnect_documents(mcp, settings)
    plans = (_WritePlan(mcp, mcp_updated), _WritePlan(settings, settings_updated))
    backups = _apply_transaction(plans)
    return _result(paths, plans, backups, managed_client_id=managed_client_id)


def disconnect_claude_code_config(
    mcp_path: Path | None = None, settings_path: Path | None = None
) -> ClaudeCodeConfigResult:
    """Positional-argument compatibility wrapper for cleanup callers."""

    return disconnect_claude_code(mcp_path=mcp_path, settings_path=settings_path)


__all__ = [
    "CLAUDE_CODE_HOOK_TOOL",
    "CLAUDE_CODE_MCP_CONFIG_ENV",
    "CLAUDE_CODE_MCP_PROFILE",
    "CLAUDE_CODE_MCP_SERVER_KEY",
    "CLAUDE_CODE_SETTINGS_ENV",
    "MAX_CLAUDE_CODE_CONFIG_BYTES",
    "ClaudeCodeConfigPaths",
    "ClaudeCodeConfigResult",
    "claude_code_config_paths",
    "claude_code_mcp_config_path",
    "claude_code_settings_path",
    "configure_claude_code",
    "connect_claude_code",
    "disconnect_claude_code",
    "disconnect_claude_code_config",
    "managed_claude_code_hook_handler",
]
