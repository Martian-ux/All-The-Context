"""Transactional configuration for the Claude Code user integration.

Claude Code keeps its user-scope MCP registry, settings, and personal skills in
separate surfaces. This module owns only the All The Context entries and the
three reserved explicit skills it creates; all other user data is treated as
user-owned data.
"""

from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
import stat
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import CoreConfig
from .credentials import (
    FALLBACK_CREDENTIAL_STORAGE,
    OS_CREDENTIAL_STORAGE,
    KeyringCredentialStore,
    development_file_credentials_enabled,
    require_development_file_credentials,
)
from .desktop_runtime import RuntimeCommand

CLAUDE_CODE_MCP_SERVER_KEY = "all-the-context-claude-code"
CLAUDE_CODE_HOOK_TOOL = "claude_code_user_prompt_submit"
CLAUDE_CODE_MCP_PROFILE = "claude_code_hook"
CLAUDE_CODE_EXPLICIT_MCP_SERVER_KEY = "all-the-context-claude-code-explicit"
CLAUDE_CODE_EXPLICIT_HOOK_TOOL = "claude_code_user_prompt_expansion"
CLAUDE_CODE_EXPLICIT_MCP_PROFILE = "claude_code_explicit"
CLAUDE_CODE_EXPLICIT_COMMANDS = ("atc-remember", "atc-correct", "atc-forget")

CLAUDE_CODE_MCP_CONFIG_ENV = "ATC_CLAUDE_CODE_MCP_CONFIG"
CLAUDE_CODE_SETTINGS_ENV = "ATC_CLAUDE_CODE_SETTINGS"
CLAUDE_CODE_SKILLS_DIR_ENV = "ATC_CLAUDE_CODE_SKILLS_DIR"
CLAUDE_CODE_EXECUTABLE_ENV = "ATC_CLAUDE_CODE_EXECUTABLE"

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

_MANAGED_EXPLICIT_HOOK_HANDLER: dict[str, Any] = {
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

_EXPLICIT_SKILL_FILES: dict[str, str] = {
    "atc-remember": """---
description: Store an exact user-stated context item in All The Context
disable-model-invocation: true
user-invocable: true
---

This reserved skill is handled by the All The Context UserPromptExpansion hook.
Do not paraphrase, summarize, or repeat its arguments.
""",
    "atc-correct": """---
description: Correct one exact All The Context record
disable-model-invocation: true
user-invocable: true
---

This reserved skill is handled by the All The Context UserPromptExpansion hook.
Do not paraphrase, summarize, or repeat its arguments.
""",
    "atc-forget": """---
description: Forget one exact All The Context record
disable-model-invocation: true
user-invocable: true
---

This reserved skill is handled by the All The Context UserPromptExpansion hook.
Use exactly `/atc-forget <record-id>`; trailing text is rejected.
Do not paraphrase, summarize, or repeat its arguments.
""",
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
    skill_changed: bool = False
    mcp_backup_path: Path | None = None
    settings_backup_path: Path | None = None
    skill_backup_paths: tuple[Path, ...] = ()
    skill_paths: tuple[Path, ...] = ()
    managed_client_id: str | None = None

    @property
    def backup_paths(self) -> tuple[Path, ...]:
        """Return only backups created by this operation."""

        return tuple(
            path
            for path in (
                self.mcp_backup_path,
                self.settings_backup_path,
                *self.skill_backup_paths,
            )
            if path is not None
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
    remove: bool = False


def _user_home() -> Path:
    """Resolve the OS user home without consulting the current working tree."""

    if platform.system() == "Windows":
        configured = os.environ.get("USERPROFILE")
        if configured:
            return _absolute_path(Path(configured).expanduser())
        drive = os.environ.get("HOMEDRIVE")
        tail = os.environ.get("HOMEPATH")
        if drive and tail:
            return _absolute_path(Path(f"{drive}{tail}").expanduser())
    else:
        configured = os.environ.get("HOME")
        if configured:
            return _absolute_path(Path(configured).expanduser())
    return _absolute_path(Path.home().expanduser())


def _absolute_path(path: Path) -> Path:
    """Make a path absolute without resolving links that must be rejected later."""

    return Path(os.path.abspath(os.fspath(path)))


def _resolve_override(path: Path | None, default: Path, environment_name: str) -> Path:
    configured = os.environ.get(environment_name) if path is None else None
    selected = path if path is not None else Path(configured) if configured else default
    return _absolute_path(selected.expanduser())


def claude_code_is_detected() -> bool:
    """Detect an existing Claude Code executable without treating config as installation."""

    configured = os.environ.get(CLAUDE_CODE_EXECUTABLE_ENV)
    if configured:
        try:
            if Path(configured).expanduser().resolve().is_file():
                return True
        except (OSError, RuntimeError, ValueError):
            pass
    return shutil.which("claude") is not None


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


def claude_code_skills_dir(path: Path | None = None, *, settings_path: Path | None = None) -> Path:
    """Return the personal Claude Code skills root.

    A settings override keeps isolated setup callers inside their supplied
    test/user surface. Production defaults remain under ``~/.claude/skills``.
    """

    configured = os.environ.get(CLAUDE_CODE_SKILLS_DIR_ENV) if path is None else None
    if configured:
        selected = Path(configured)
    elif path is not None:
        selected = path
    else:
        selected = claude_code_settings_path(settings_path).parent / "skills"
    return _absolute_path(selected.expanduser())


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


def managed_claude_code_explicit_hook_handler() -> dict[str, Any]:
    """Return a defensive copy of the explicit-command hook handler."""

    return deepcopy(_MANAGED_EXPLICIT_HOOK_HANDLER)


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
    _reject_linked_path(path)
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


def _reject_linked_path(path: Path) -> None:
    """Reject symlink/reparse components before reading or writing user config."""

    current = path
    while True:
        try:
            information = current.lstat()
        except FileNotFoundError:
            information = None
        except OSError as exc:
            raise RuntimeError("Could not verify Claude Code configuration path") from exc
        if information is not None:
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(information.st_mode) or bool(
                getattr(information, "st_file_attributes", 0) & reparse_flag
            ):
                raise ValueError(
                    "Claude Code configuration paths may not contain symlinks or reparse points"
                )
        parent = current.parent
        if parent == current:
            return
        current = parent


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


def _read_skill_document(path: Path) -> _Document:
    """Read a bounded plain-text personal skill without interpreting it."""

    text, existed = _read_bounded_text(path)
    return _Document(path, text, {}, existed)


def _render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _explicit_skill_plans(
    skills_dir: Path, *, remove: bool = False
) -> tuple[tuple[_WritePlan, ...], tuple[Path, ...]]:
    """Plan reserved personal skills without overwriting user-owned skills."""

    paths = tuple(skills_dir / name / "SKILL.md" for name in CLAUDE_CODE_EXPLICIT_COMMANDS)
    plans: list[_WritePlan] = []
    for name, path in zip(CLAUDE_CODE_EXPLICIT_COMMANDS, paths, strict=True):
        document = _read_skill_document(path)
        managed_content = _EXPLICIT_SKILL_FILES[name]
        if document.existed and document.original != managed_content:
            if remove:
                plans.append(_WritePlan(document, document.original))
                continue
            raise ValueError(
                f"the reserved Claude Code skill path for {name} belongs to an unrelated skill"
            )
        plans.append(
            _WritePlan(
                document,
                "" if remove else managed_content,
                remove=remove and document.existed,
            )
        )
    return tuple(plans), paths


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


def _token_environment(
    client_id: str,
    token: str | None,
    *,
    credential_storage: str | None,
) -> dict[str, str]:
    """Keep production credentials out of JSON while retaining explicit dev fallback."""

    if token is None:
        return {}
    _validate_text(token, label="client token", maximum=16_384)

    if credential_storage == OS_CREDENTIAL_STORAGE:
        raise RuntimeError("refusing to serialize a credential stored in the OS credential store")
    if credential_storage == FALLBACK_CREDENTIAL_STORAGE:
        if not development_file_credentials_enabled():
            require_development_file_credentials()
        return {"ATC_CLIENT_TOKEN": token}

    keyring_available, stored = _keyring_token(client_id)
    if stored is not None:
        if stored != token:
            raise RuntimeError("the supplied credential does not match the OS credential")
        # The adapter resolves this same client:<id> entry at runtime.
        return {}

    # A caller that has a recoverable OS credential should pass token=None. A
    # successful but empty OS lookup is not permission to copy a production
    # credential into a user-owned JSON file. Only an unavailable OS store plus
    # the existing explicit development fallback permits serialization.
    if keyring_available:
        raise RuntimeError(
            "refusing to serialize a client credential when the OS credential store is available"
        )
    if not development_file_credentials_enabled():
        require_development_file_credentials()
    return {"ATC_CLIENT_TOKEN": token}


def _mcp_server(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    profile: str = CLAUDE_CODE_MCP_PROFILE,
    token: str | None,
    target_url: str,
    core_data_dir: Path | None,
    credential_storage: str | None,
) -> dict[str, Any]:
    command = runtime.mcp()
    if not command or any(type(item) is not str or not item for item in command):
        raise ValueError("runtime MCP command must contain non-empty strings")
    validated_client_id = _validate_text(client_id, label="client ID")
    env = {
        "ATC_MCP_PROFILE": _validate_text(profile, label="MCP profile", maximum=128),
        "ATC_TARGET_URL": _validate_target_url(target_url),
        "ATC_CLIENT_ID": validated_client_id,
        "ATC_AUTO_START_CORE": "1",
        "ATC_CORE_COMMAND": json.dumps(runtime.core(), ensure_ascii=False),
        "ATC_CORE_DATA_DIR": str((core_data_dir or CoreConfig.default().data_dir).resolve()),
    }
    env.update(
        _token_environment(
            validated_client_id,
            token,
            credential_storage=credential_storage,
        )
    )
    return {
        "type": "stdio",
        "command": command[0],
        "args": list(command[1:]),
        "env": env,
    }


def _is_managed_server(value: object, *, profile: str = CLAUDE_CODE_MCP_PROFILE) -> bool:
    if not isinstance(value, dict):
        return False
    env = value.get("env")
    return isinstance(env, dict) and env.get("ATC_MCP_PROFILE") == profile


def _validate_hook_groups(
    groups: object, *, event_name: str = "UserPromptSubmit"
) -> list[dict[str, Any]]:
    if not isinstance(groups, list):
        raise ValueError(f"hooks.{event_name} must contain a JSON array")
    validated: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError(f"hooks.{event_name} groups must be JSON objects")
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            raise ValueError(f"hooks.{event_name} groups must contain a hooks array")
        if any(not isinstance(handler, dict) for handler in handlers):
            raise ValueError(f"{event_name} hook handlers must be JSON objects")
        validated.append(group)
    return validated


def _remove_managed_handlers(
    groups: list[dict[str, Any]],
    *,
    handler: dict[str, Any] = _MANAGED_HOOK_HANDLER,
) -> tuple[list[dict[str, Any]], int, bool]:
    cleaned: list[dict[str, Any]] = []
    removed = 0
    moved_from_matcher = False
    for original_group in groups:
        handlers = original_group["hooks"]
        kept = [candidate for candidate in handlers if candidate != handler]
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


def _configured_hook_groups(
    groups: object,
    *,
    handler: dict[str, Any] = _MANAGED_HOOK_HANDLER,
    event_name: str = "UserPromptSubmit",
    matcher: str | None = None,
) -> list[dict[str, Any]]:
    validated = _validate_hook_groups(groups, event_name=event_name)
    cleaned, count, moved_from_matcher = _remove_managed_handlers(validated, handler=handler)
    already_configured = any(
        candidate == handler and (matcher is None or group.get("matcher") == matcher)
        for group in validated
        for candidate in group["hooks"]
    )
    if count == 1 and already_configured and (not moved_from_matcher or matcher is not None):
        # Keep the one exact handler where the user already placed it.  The
        # settings document is already a defensive copy, so returning the
        # validated groups preserves idempotence and all surrounding fields.
        return validated
    group: dict[str, Any] = {"hooks": [deepcopy(handler)]}
    if matcher is not None:
        group["matcher"] = matcher
    cleaned.append(group)
    return cleaned


def _updated_connect_documents(
    mcp: _Document,
    settings: _Document,
    server: dict[str, Any],
    *,
    server_key: str = CLAUDE_CODE_MCP_SERVER_KEY,
    profile: str = CLAUDE_CODE_MCP_PROFILE,
    hook_handler: dict[str, Any] = _MANAGED_HOOK_HANDLER,
    hook_event_name: str = "UserPromptSubmit",
    hook_matcher: str | None = None,
) -> tuple[str, str]:
    mcp_updated = deepcopy(mcp.parsed)
    servers = mcp_updated.get("mcpServers")
    if "mcpServers" not in mcp_updated:
        servers = {}
        mcp_updated["mcpServers"] = servers
    if not isinstance(servers, dict):
        raise ValueError("mcpServers must contain a JSON object")
    if server_key in servers and not _is_managed_server(servers[server_key], profile=profile):
        raise ValueError("the Claude Code server key belongs to an unrelated server")
    servers[server_key] = server

    settings_updated = deepcopy(settings.parsed)
    hooks = settings_updated.get("hooks")
    if "hooks" not in settings_updated:
        hooks = {}
        settings_updated["hooks"] = hooks
    if not isinstance(hooks, dict):
        raise ValueError("hooks must contain a JSON object")
    event_groups = hooks.get(hook_event_name)
    if hook_event_name not in hooks:
        event_groups = []
        hooks[hook_event_name] = event_groups
    hooks[hook_event_name] = _configured_hook_groups(
        event_groups,
        handler=hook_handler,
        event_name=hook_event_name,
        matcher=hook_matcher,
    )
    return (
        _render(mcp_updated) if mcp_updated != mcp.parsed else mcp.original,
        _render(settings_updated) if settings_updated != settings.parsed else settings.original,
    )


def _updated_disconnect_documents(
    mcp: _Document,
    settings: _Document,
    *,
    server_key: str = CLAUDE_CODE_MCP_SERVER_KEY,
    profile: str = CLAUDE_CODE_MCP_PROFILE,
    hook_handler: dict[str, Any] = _MANAGED_HOOK_HANDLER,
    hook_event_name: str = "UserPromptSubmit",
    hook_matcher: str | None = None,
) -> tuple[str, str, str | None]:
    mcp_updated = deepcopy(mcp.parsed)
    servers = mcp_updated.get("mcpServers")
    managed_client_id: str | None = None
    if "mcpServers" in mcp_updated and not isinstance(servers, dict):
        raise ValueError("mcpServers must contain a JSON object")
    mcp_removed = False
    if isinstance(servers, dict):
        current = servers.get(server_key)
        if isinstance(current, dict) and _is_managed_server(current, profile=profile):
            env = current.get("env")
            if isinstance(env, dict) and isinstance(env.get("ATC_CLIENT_ID"), str):
                managed_client_id = env["ATC_CLIENT_ID"]
            del servers[server_key]
            mcp_removed = True

    settings_updated = deepcopy(settings.parsed)
    hooks = settings_updated.get("hooks")
    if "hooks" in settings_updated and not isinstance(hooks, dict):
        raise ValueError("hooks must contain a JSON object")
    settings_removed = False
    if isinstance(hooks, dict) and hook_event_name in hooks:
        groups = _validate_hook_groups(hooks[hook_event_name], event_name=hook_event_name)
        cleaned, removed, _moved = _remove_managed_handlers(groups, handler=hook_handler)
        if hook_matcher is not None:
            cleaned = [
                group
                for group in cleaned
                if not (
                    group.get("matcher") == hook_matcher
                    and group.get("hooks") == []
                    and set(group) == {"matcher", "hooks"}
                )
            ]
        if cleaned:
            hooks[hook_event_name] = cleaned
        else:
            del hooks[hook_event_name]
        if not hooks:
            settings_updated.pop("hooks", None)
        settings_removed = removed > 0 or groups != cleaned
    return (
        _render(mcp_updated) if mcp_removed else mcp.original,
        _render(settings_updated) if settings_removed else settings.original,
        managed_client_id,
    )


def _backup(path: Path) -> Path:
    _reject_linked_path(path)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.atc-backup-{timestamp}-{secrets.token_hex(3)}")
    _reject_linked_path(backup)
    if backup.exists():
        raise RuntimeError("could not allocate a private Claude Code backup path")
    shutil.copy2(path, backup)
    return backup


def _atomic_write(path: Path, content: str) -> None:
    _reject_linked_path(path)
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
    _reject_linked_path(temporary)
    existing_mode: int | None = None
    if os.name != "nt" and path.exists():
        existing_mode = stat.S_IMODE(path.stat().st_mode)
    try:
        with temporary.open("w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
        if os.name != "nt":
            os.chmod(temporary, existing_mode if existing_mode is not None else 0o600)
        temporary.replace(path)
    finally:
        with suppress(OSError):
            temporary.unlink(missing_ok=True)


def _restore(plan: _WritePlan) -> None:
    if plan.document.existed:
        current, exists = _read_bounded_text(plan.document.path)
        if not exists:
            _atomic_write(plan.document.path, plan.document.original)
            return
        if current == plan.document.original:
            return
        if plan.remove:
            raise RuntimeError("the Claude Code file changed during rollback")
        if current != plan.updated:
            raise RuntimeError("the Claude Code file changed during rollback")
        _atomic_write(plan.document.path, plan.document.original)
    else:
        current, exists = _read_bounded_text(plan.document.path)
        if not exists:
            return
        if plan.remove or current != plan.updated:
            raise RuntimeError("an unrelated Claude Code file appeared during rollback")
        plan.document.path.unlink()


def _revalidate_preimage(plan: _WritePlan) -> None:
    """Refuse to overwrite a config that changed after its initial read."""

    current, exists = _read_bounded_text(plan.document.path)
    if exists != plan.document.existed or current != plan.document.original:
        raise RuntimeError("Claude Code configuration changed during setup; retry safely")


def _apply_transaction(plans: tuple[_WritePlan, ...]) -> dict[Path, Path | None]:
    changed = tuple(plan for plan in plans if plan.remove or plan.updated != plan.document.original)
    if not changed:
        return {}

    for plan in changed:
        plan.document.path.parent.mkdir(parents=True, exist_ok=True)

    backups: dict[Path, Path | None] = {}
    attempted: list[_WritePlan] = []
    try:
        for plan in changed:
            _revalidate_preimage(plan)
            backups[plan.document.path] = (
                _backup(plan.document.path) if plan.document.existed else None
            )
            _revalidate_preimage(plan)
            attempted.append(plan)
            if plan.remove:
                plan.document.path.unlink()
            else:
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
        cleanup_error: OSError | None = None
        for backup in backups.values():
            if backup is None:
                continue
            try:
                backup.unlink(missing_ok=True)
            except OSError as candidate:
                cleanup_error = candidate
                break
        if cleanup_error is not None:
            raise RuntimeError(
                "Claude Code configuration rolled back but could not clean up its backups"
            ) from cleanup_error
        raise

    return backups


def _result(
    paths: ClaudeCodeConfigPaths,
    plans: tuple[_WritePlan, ...],
    backups: dict[Path, Path | None],
    *,
    client: str = "Claude Code",
    skill_paths: tuple[Path, ...] = (),
    managed_client_id: str | None = None,
) -> ClaudeCodeConfigResult:
    mcp_plan = next(plan for plan in plans if plan.document.path == paths.mcp)
    settings_plan = next(plan for plan in plans if plan.document.path == paths.settings)
    mcp_changed = mcp_plan.remove or mcp_plan.updated != mcp_plan.document.original
    settings_changed = (
        settings_plan.remove or settings_plan.updated != settings_plan.document.original
    )
    skill_path_set = set(skill_paths)
    skill_changed = any(
        plan.document.path in skill_path_set
        and (plan.remove or plan.updated != plan.document.original)
        for plan in plans
    )
    skill_backups: list[Path] = []
    for path in skill_paths:
        backup = backups.get(path)
        if backup is not None:
            skill_backups.append(backup)
    return ClaudeCodeConfigResult(
        client=client,
        mcp_path=paths.mcp,
        settings_path=paths.settings,
        changed=mcp_changed or settings_changed or skill_changed,
        mcp_changed=mcp_changed,
        settings_changed=settings_changed,
        skill_changed=skill_changed,
        mcp_backup_path=backups.get(paths.mcp),
        settings_backup_path=backups.get(paths.settings),
        skill_backup_paths=tuple(skill_backups),
        skill_paths=skill_paths,
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
    core_data_dir: Path | None = None,
    credential_storage: str | None = None,
) -> ClaudeCodeConfigResult:
    """Install or refresh the exact user-scope Claude Code integration."""

    paths = claude_code_config_paths(mcp_path=mcp_path, settings_path=settings_path)
    mcp = _read_document(paths.mcp)
    settings = _read_document(paths.settings)
    server = _mcp_server(
        runtime,
        client_id,
        token=token,
        target_url=target_url,
        core_data_dir=core_data_dir,
        credential_storage=credential_storage,
    )
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
    core_data_dir: Path | None = None,
    credential_storage: str | None = None,
) -> ClaudeCodeConfigResult:
    """Compatibility spelling for setup code that uses configure_* adapters."""

    return connect_claude_code(
        runtime,
        client_id,
        token=token,
        target_url=target_url,
        mcp_path=mcp_path,
        settings_path=settings_path,
        core_data_dir=core_data_dir,
        credential_storage=credential_storage,
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


def connect_claude_code_explicit_commands(
    runtime: RuntimeCommand,
    client_id: str,
    *,
    token: str | None = None,
    target_url: str = "http://127.0.0.1:7337",
    mcp_path: Path | None = None,
    settings_path: Path | None = None,
    core_data_dir: Path | None = None,
    credential_storage: str | None = None,
) -> ClaudeCodeConfigResult:
    """Install the opt-in exact-argument Claude Code command boundary."""

    paths = claude_code_config_paths(mcp_path=mcp_path, settings_path=settings_path)
    skills_dir = claude_code_skills_dir(settings_path=paths.settings)
    mcp = _read_document(paths.mcp)
    settings = _read_document(paths.settings)
    skill_plans, skill_paths = _explicit_skill_plans(skills_dir)
    server = _mcp_server(
        runtime,
        client_id,
        profile=CLAUDE_CODE_EXPLICIT_MCP_PROFILE,
        token=token,
        target_url=target_url,
        core_data_dir=core_data_dir,
        credential_storage=credential_storage,
    )
    mcp_updated, settings_updated = _updated_connect_documents(
        mcp,
        settings,
        server,
        server_key=CLAUDE_CODE_EXPLICIT_MCP_SERVER_KEY,
        profile=CLAUDE_CODE_EXPLICIT_MCP_PROFILE,
        hook_handler=_MANAGED_EXPLICIT_HOOK_HANDLER,
        hook_event_name="UserPromptExpansion",
        hook_matcher="^(atc-remember|atc-correct|atc-forget)$",
    )
    plans = (
        _WritePlan(mcp, mcp_updated),
        _WritePlan(settings, settings_updated),
        *skill_plans,
    )
    backups = _apply_transaction(plans)
    return _result(
        paths,
        plans,
        backups,
        client="Claude Code explicit commands",
        skill_paths=skill_paths,
    )


def configure_claude_code_explicit_commands(
    runtime: RuntimeCommand,
    client_id: str,
    **kwargs: Any,
) -> ClaudeCodeConfigResult:
    """Compatibility spelling for the explicit command setup adapter."""

    return connect_claude_code_explicit_commands(runtime, client_id, **kwargs)


def disconnect_claude_code_explicit_commands(
    mcp_path: Path | None = None,
    settings_path: Path | None = None,
) -> ClaudeCodeConfigResult:
    """Remove only the explicit command integration owned by this module."""

    paths = claude_code_config_paths(mcp_path=mcp_path, settings_path=settings_path)
    skills_dir = claude_code_skills_dir(settings_path=paths.settings)
    mcp = _read_document(paths.mcp)
    settings = _read_document(paths.settings)
    skill_plans, skill_paths = _explicit_skill_plans(skills_dir, remove=True)
    mcp_updated, settings_updated, managed_client_id = _updated_disconnect_documents(
        mcp,
        settings,
        server_key=CLAUDE_CODE_EXPLICIT_MCP_SERVER_KEY,
        profile=CLAUDE_CODE_EXPLICIT_MCP_PROFILE,
        hook_handler=_MANAGED_EXPLICIT_HOOK_HANDLER,
        hook_event_name="UserPromptExpansion",
        hook_matcher="^(atc-remember|atc-correct|atc-forget)$",
    )
    plans = (
        _WritePlan(mcp, mcp_updated),
        _WritePlan(settings, settings_updated),
        *skill_plans,
    )
    backups = _apply_transaction(plans)
    return _result(
        paths,
        plans,
        backups,
        client="Claude Code explicit commands",
        skill_paths=skill_paths,
        managed_client_id=managed_client_id,
    )


def disconnect_claude_code_explicit_config(
    mcp_path: Path | None = None,
    settings_path: Path | None = None,
) -> ClaudeCodeConfigResult:
    """Compatibility wrapper for explicit command cleanup callers."""

    return disconnect_claude_code_explicit_commands(mcp_path=mcp_path, settings_path=settings_path)


def disconnect_claude_code_config(
    mcp_path: Path | None = None, settings_path: Path | None = None
) -> ClaudeCodeConfigResult:
    """Positional-argument compatibility wrapper for cleanup callers."""

    return disconnect_claude_code(mcp_path=mcp_path, settings_path=settings_path)


__all__ = [
    "CLAUDE_CODE_EXECUTABLE_ENV",
    "CLAUDE_CODE_EXPLICIT_COMMANDS",
    "CLAUDE_CODE_EXPLICIT_HOOK_TOOL",
    "CLAUDE_CODE_EXPLICIT_MCP_PROFILE",
    "CLAUDE_CODE_EXPLICIT_MCP_SERVER_KEY",
    "CLAUDE_CODE_HOOK_TOOL",
    "CLAUDE_CODE_MCP_CONFIG_ENV",
    "CLAUDE_CODE_MCP_PROFILE",
    "CLAUDE_CODE_MCP_SERVER_KEY",
    "CLAUDE_CODE_SETTINGS_ENV",
    "CLAUDE_CODE_SKILLS_DIR_ENV",
    "MAX_CLAUDE_CODE_CONFIG_BYTES",
    "ClaudeCodeConfigPaths",
    "ClaudeCodeConfigResult",
    "claude_code_config_paths",
    "claude_code_is_detected",
    "claude_code_mcp_config_path",
    "claude_code_settings_path",
    "claude_code_skills_dir",
    "configure_claude_code",
    "configure_claude_code_explicit_commands",
    "connect_claude_code",
    "connect_claude_code_explicit_commands",
    "disconnect_claude_code",
    "disconnect_claude_code_config",
    "disconnect_claude_code_explicit_commands",
    "disconnect_claude_code_explicit_config",
    "managed_claude_code_explicit_hook_handler",
    "managed_claude_code_hook_handler",
]
