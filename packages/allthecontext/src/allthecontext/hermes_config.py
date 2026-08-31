"""Transactional Hermes Agent configuration and lifecycle integration.

Hermes owns a YAML configuration file, but All The Context deliberately does
not depend on a YAML parser at runtime.  This module edits only narrow,
marker-owned mappings in ``mcp_servers`` and ``hooks`` and fails closed for
unsupported shapes.  The managed shell hooks use the stable Hermes JSON
stdin/stdout protocol and resolve their Core credentials from the operating
system credential store at invocation time.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import shutil
import stat
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .config import CoreConfig
from .credentials import OS_CREDENTIAL_STORAGE
from .desktop_runtime import RuntimeCommand

HERMES_HOME_ENV = "HERMES_HOME"
HERMES_CONFIG_ENV = "ATC_HERMES_CONFIG"
HERMES_EXECUTABLE_ENV = "ATC_HERMES_EXECUTABLE"
HERMES_DEFAULT_PROFILE = "default"
HERMES_MCP_SERVER_KEY = "all_the_context"
HERMES_MCP_PROFILE = "hermes_read"
HERMES_READ_HOOK_EVENT = "pre_llm_call"
HERMES_CAPTURE_HOOK_EVENT = "post_llm_call"
HERMES_READ_CLIENT_NAME = "Hermes"
HERMES_CAPTURE_CLIENT_NAME = "Hermes Continuous Capture"
HERMES_CONFIG_MARKER_BEGIN = "# BEGIN All The Context managed Hermes MCP"
HERMES_CONFIG_MARKER_END = "# END All The Context managed Hermes MCP"
HERMES_READ_HOOK_MARKER_BEGIN = "# BEGIN All The Context managed Hermes pre_llm_call hook"
HERMES_READ_HOOK_MARKER_END = "# END All The Context managed Hermes pre_llm_call hook"
HERMES_CAPTURE_HOOK_MARKER_BEGIN = "# BEGIN All The Context managed Hermes post_llm_call hook"
HERMES_CAPTURE_HOOK_MARKER_END = "# END All The Context managed Hermes post_llm_call hook"
HERMES_MCP_SECTION_MARKER_BEGIN = "# BEGIN All The Context managed Hermes mcp_servers section"
HERMES_MCP_SECTION_MARKER_END = "# END All The Context managed Hermes mcp_servers section"
HERMES_HOOKS_SECTION_MARKER_BEGIN = "# BEGIN All The Context managed Hermes hooks section"
HERMES_HOOKS_SECTION_MARKER_END = "# END All The Context managed Hermes hooks section"
MAX_HERMES_CONFIG_BYTES = 4 * 1024 * 1024
MAX_HERMES_ALLOWLIST_BYTES = 1 * 1024 * 1024
MAX_HERMES_PROFILE_CHARS = 64


class HermesConfigError(ValueError):
    """A safe, content-free Hermes setup/configuration error."""


@dataclass(frozen=True, slots=True)
class HermesProfile:
    """One explicitly addressable Hermes profile."""

    name: str
    home: Path
    config_path: Path
    allowlist_path: Path
    active: bool = False


@dataclass(frozen=True, slots=True)
class HermesConfigResult:
    """Content-free result for setup, status, and rollback reporting."""

    client: str
    profile: str
    config_path: Path
    allowlist_path: Path
    changed: bool
    config_changed: bool
    allowlist_changed: bool
    restart_required: bool
    hook_consent_authorized: bool
    config_backup_path: Path | None = None
    allowlist_backup_path: Path | None = None
    managed_client_ids: tuple[str, ...] = ()

    @property
    def backup_paths(self) -> tuple[Path, ...]:
        return tuple(
            path
            for path in (self.config_backup_path, self.allowlist_backup_path)
            if path is not None
        )


@dataclass(frozen=True, slots=True)
class _TextDocument:
    path: Path
    original: str
    existed: bool
    mode: int | None


@dataclass(frozen=True, slots=True)
class _Section:
    name: str
    start: int
    end: int
    inline: str


_TOP_LEVEL_KEY = re.compile(r"^(?P<key>[A-Za-z0-9_-]+):(?P<value>.*)$")
_MAPPING_KEY = re.compile(r"^(?P<indent> *)(?P<key>[A-Za-z0-9_-]+):(?P<value>.*)$")
_MARKER_COMMAND = re.compile(r"^\s*-\s+command:\s+(?P<value>\".*\")\s*$")
_PROFILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_linked_path(path: Path) -> None:
    """Reject symlink/reparse components before reading or writing."""

    current = path
    while True:
        try:
            information = current.lstat()
        except FileNotFoundError:
            information = None
        except OSError as exc:
            raise HermesConfigError("Could not verify Hermes configuration path") from exc
        if information is not None:
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISLNK(information.st_mode) or bool(
                getattr(information, "st_file_attributes", 0) & reparse_flag
            ):
                raise HermesConfigError(
                    "Hermes configuration paths may not contain symlinks or reparse points"
                )
        parent = current.parent
        if parent == current:
            return
        current = parent


def _read_document(path: Path, *, maximum: int) -> _TextDocument:
    selected = _absolute_path(path.expanduser())
    _reject_linked_path(selected)
    try:
        if not selected.exists():
            return _TextDocument(selected, "", False, None)
        if not selected.is_file():
            raise HermesConfigError("Hermes configuration path is not a regular file")
        initial = selected.stat()
        if initial.st_size > maximum:
            raise HermesConfigError("Hermes configuration exceeds the bounded read limit")
        raw = selected.read_bytes()
        if len(raw) > maximum:
            raise HermesConfigError("Hermes configuration exceeds the bounded read limit")
        text = raw.decode("utf-8")
        if selected.stat().st_size != initial.st_size:
            raise HermesConfigError("Hermes configuration changed while it was being read")
        return _TextDocument(selected, text, True, stat.S_IMODE(initial.st_mode))
    except HermesConfigError:
        raise
    except (OSError, UnicodeError) as exc:
        raise HermesConfigError("Could not read Hermes configuration") from exc


def _user_data_root(*, platform_name: str | None = None, home: Path | None = None) -> Path:
    configured = os.environ.get(HERMES_HOME_ENV)
    if configured:
        configured_path = _absolute_path(Path(configured).expanduser())
        if configured_path.parent.name.casefold() == "profiles":
            return configured_path.parent.parent
        return configured_path
    active_platform = platform_name or sys.platform
    user_home = (home or Path.home()).expanduser()
    if active_platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = (
            Path(local_app_data).expanduser()
            if local_app_data
            else user_home / "AppData" / "Local"
        )
        return _absolute_path(base / "hermes")
    return _absolute_path(user_home / ".hermes")


def _validate_profile_name(value: str) -> str:
    if type(value) is not str or len(value) > MAX_HERMES_PROFILE_CHARS:
        raise HermesConfigError("Hermes profile name is outside its bound")
    if value != HERMES_DEFAULT_PROFILE and _PROFILE_NAME.fullmatch(value) is None:
        raise HermesConfigError("Hermes profile name is invalid")
    return value


def _profile_home(name: str, root: Path) -> Path:
    validated = _validate_profile_name(name)
    return root if validated == HERMES_DEFAULT_PROFILE else root / "profiles" / validated


def hermes_home(profile: str | None = None, *, root: Path | None = None) -> Path:
    """Return the default or named Hermes home without creating it."""

    base = _absolute_path((root or _user_data_root()).expanduser())
    return _profile_home(profile or HERMES_DEFAULT_PROFILE, base)


def hermes_config_path(
    profile: str | None = None,
    *,
    root: Path | None = None,
    path: Path | None = None,
) -> Path:
    """Resolve one profile's config, honoring only an explicit test override."""

    if path is not None:
        return _absolute_path(path.expanduser())
    configured = os.environ.get(HERMES_CONFIG_ENV) if profile is None and root is None else None
    if configured:
        return _absolute_path(Path(configured).expanduser())
    return hermes_home(profile, root=root) / "config.yaml"


def hermes_allowlist_path(
    profile: str | None = None,
    *,
    root: Path | None = None,
    config_path: Path | None = None,
) -> Path:
    if config_path is not None:
        return _absolute_path(config_path.expanduser()).with_name("shell-hooks-allowlist.json")
    return hermes_home(profile, root=root) / "shell-hooks-allowlist.json"


def _active_profile_pointer(root: Path) -> Path:
    return _absolute_path(root) / "active_profile"


def active_hermes_profile(*, root: Path | None = None) -> str:
    """Read the active-profile pointer, failing closed when it is malformed."""

    selected_root = _absolute_path(root.expanduser()) if root is not None else _user_data_root()
    pointer = _active_profile_pointer(selected_root)
    document = _read_document(pointer, maximum=16 * 1024)
    if not document.existed:
        return HERMES_DEFAULT_PROFILE
    selected = document.original.strip()
    if not selected:
        raise HermesConfigError("Hermes active profile pointer is malformed")
    name = _validate_profile_name(selected)
    if name != HERMES_DEFAULT_PROFILE and not _profile_home(name, selected_root).exists():
        raise HermesConfigError("Hermes active profile is unavailable")
    return name


def resolve_hermes_profile(
    profile: str | None = None,
    *,
    root: Path | None = None,
    config_path: Path | None = None,
) -> HermesProfile:
    """Resolve exactly one profile; never enumerate-and-mutate all profiles."""

    selected = _validate_profile_name(profile) if profile is not None else HERMES_DEFAULT_PROFILE
    if profile is None and config_path is None and not os.environ.get(HERMES_CONFIG_ENV):
        selected = active_hermes_profile(root=root)
    base = _absolute_path((root or _user_data_root()).expanduser())
    home = _profile_home(selected, base)
    direct_config = config_path is not None or (
        profile is None and root is None and bool(os.environ.get(HERMES_CONFIG_ENV))
    )
    selected_config = hermes_config_path(
        selected,
        root=(base if direct_config or root is not None else None),
        path=config_path,
    )
    return HermesProfile(
        name=selected,
        home=home,
        config_path=selected_config,
        allowlist_path=hermes_allowlist_path(
            selected,
            root=base,
            config_path=selected_config if direct_config else None,
        ),
        active=(
            profile is None
            and config_path is None
            and not os.environ.get(HERMES_CONFIG_ENV)
            and selected == active_hermes_profile(root=root)
        ),
    )


def discover_hermes_profiles(*, root: Path | None = None) -> tuple[HermesProfile, ...]:
    """List addressable profiles for a UI selector without changing any file."""

    base = _absolute_path((root or _user_data_root()).expanduser())
    names = [HERMES_DEFAULT_PROFILE]
    profiles_dir = base / "profiles"
    try:
        children = sorted(profiles_dir.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        children = []
    for child in children:
        if (
            child.is_dir()
            and _PROFILE_NAME.fullmatch(child.name)
            and child.name != "default"
            and (child / "config.yaml").is_file()
        ):
            names.append(child.name)
    try:
        active = active_hermes_profile(root=base)
    except HermesConfigError:
        active = ""
    return tuple(
        HermesProfile(
            name=name,
            home=_profile_home(name, base),
            config_path=_profile_home(name, base) / "config.yaml",
            allowlist_path=_profile_home(name, base) / "shell-hooks-allowlist.json",
            active=name == active,
        )
        for name in names
    )


def hermes_is_detected() -> bool:
    """Detect Hermes without treating an arbitrary config file as an executable."""

    configured = os.environ.get(HERMES_EXECUTABLE_ENV)
    if configured:
        try:
            if Path(configured).expanduser().resolve().is_file():
                return True
        except (OSError, RuntimeError, ValueError):
            pass
    if shutil.which("hermes") is not None:
        return True
    try:
        root = _user_data_root()
        if (root / "config.yaml").is_file():
            return True
        profiles = root / "profiles"
        return profiles.is_dir() and any(
            (profile / "config.yaml").is_file()
            for profile in profiles.iterdir()
            if profile.is_dir()
        )
    except OSError:
        return False


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _line_body(line: str) -> str:
    return line.rstrip("\r\n")


def _top_sections(lines: list[str]) -> dict[str, _Section]:
    sections: dict[str, _Section] = {}
    current: tuple[str, int, str] | None = None
    for index, line in enumerate(lines):
        body = _line_body(line)
        if not body.strip() or body.lstrip().startswith("#") or body.strip() in {"---", "..."}:
            continue
        if re.search(r"(^|[\s:])[&*!][A-Za-z0-9_-]+", body) or re.search(
            r":\s*[|>]\s*(?:#.*)?$", body
        ):
            raise HermesConfigError(
                "Hermes configuration contains an unsupported YAML feature"
            )
        if body.startswith((" ", "\t")):
            continue
        match = _TOP_LEVEL_KEY.fullmatch(body)
        if match is None:
            raise HermesConfigError("Hermes configuration contains an unsupported YAML shape")
        name = match.group("key")
        if name in sections or (current is not None and current[0] == name):
            raise HermesConfigError("Hermes configuration contains duplicate top-level keys")
        if current is not None:
            sections[current[0]] = _Section(current[0], current[1], index, current[2])
        current = (name, index, match.group("value"))
    if current is not None:
        sections[current[0]] = _Section(current[0], current[1], len(lines), current[2])
    return sections


def _section_is_mapping(lines: list[str], section: _Section) -> None:
    inline = section.inline.strip()
    if inline and inline not in {"{}", "{ }"}:
        raise HermesConfigError(f"Hermes {section.name} must be a YAML mapping")
    for line in lines[section.start + 1 : section.end]:
        body = _line_body(line)
        if not body.strip() or body.lstrip().startswith("#"):
            continue
        if body.startswith("\t"):
            raise HermesConfigError("Hermes configuration may not use tab indentation")
        indent = len(body) - len(body.lstrip(" "))
        if indent == 0:
            continue
        if body.lstrip().startswith("-") and indent <= 2:
            raise HermesConfigError(f"Hermes {section.name} must be a YAML mapping")
        if indent < 2:
            raise HermesConfigError("Hermes configuration contains an unsupported indentation")


def _direct_child(lines: list[str], section: _Section, name: str) -> int | None:
    for index in range(section.start + 1, section.end):
        body = _line_body(lines[index])
        match = _MAPPING_KEY.fullmatch(body)
        if match and len(match.group("indent")) == 2 and match.group("key") == name:
            return index
    return None


def _child_end(lines: list[str], section: _Section, child_index: int) -> int:
    for index in range(child_index + 1, section.end):
        body = _line_body(lines[index])
        if body.strip() == HERMES_HOOKS_SECTION_MARKER_END:
            return index
        if not body.strip() or body.lstrip().startswith("#"):
            continue
        indent = len(body) - len(body.lstrip(" "))
        if indent <= 2:
            return index
    return section.end


def _remove_marker(
    lines: list[str], begin: str, end: str
) -> tuple[list[str], tuple[str, ...]]:
    begins = [index for index, line in enumerate(lines) if _line_body(line).strip() == begin]
    ends = [index for index, line in enumerate(lines) if _line_body(line).strip() == end]
    if len(begins) != len(ends):
        if begins or ends:
            raise HermesConfigError("Hermes managed configuration marker is incomplete")
        return lines, ()
    if not begins:
        return lines, ()
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for begin_index in begins:
        end_index = next((candidate for candidate in ends if candidate > begin_index), None)
        if end_index is None or begin_index < cursor:
            raise HermesConfigError("Hermes managed configuration marker is malformed")
        ranges.append((begin_index, end_index))
        cursor = end_index + 1
    if len(ranges) != 1:
        raise HermesConfigError("Hermes managed configuration contains duplicate markers")
    start, finish = ranges[0]
    commands: list[str] = []
    for line in lines[start : finish + 1]:
        match = _MARKER_COMMAND.match(_line_body(line))
        if match:
            try:
                value = json.loads(match.group("value"))
            except ValueError as exc:
                raise HermesConfigError("Hermes managed hook command is malformed") from exc
            if not isinstance(value, str):
                raise HermesConfigError("Hermes managed hook command is malformed")
            commands.append(value)
    return lines[:start] + lines[finish + 1 :], tuple(commands)


def _append_at(lines: list[str], index: int, block: str, newline: str) -> list[str]:
    while index > 0 and not _line_body(lines[index - 1]).strip():
        index -= 1
    rendered = [f"{line}{newline}" for line in block.splitlines()]
    return lines[:index] + rendered + lines[index:]


def _container_end_index(lines: list[str], section: _Section, marker: str) -> int:
    for index in range(section.start + 1, section.end):
        if _line_body(lines[index]).strip() == marker:
            return index
    return section.end


def _ensure_section(lines: list[str], name: str, newline: str) -> tuple[list[str], _Section]:
    sections = _top_sections(lines)
    section = sections.get(name)
    if section is None:
        prefix = [] if not lines or not _line_body(lines[-1]).strip() else [newline]
        lines = lines + prefix + [f"{name}:{newline}"]
        sections = _top_sections(lines)
        section = sections[name]
    _section_is_mapping(lines, section)
    if section.inline.strip() in {"{}", "{ }"}:
        lines[section.start] = f"{name}:{newline}"
        section = _top_sections(lines)[name]
    return lines, section


def _ensure_managed_section(
    lines: list[str],
    name: str,
    newline: str,
    *,
    begin: str,
    end: str,
) -> tuple[list[str], _Section]:
    sections = _top_sections(lines)
    if name in sections:
        _section_is_mapping(lines, sections[name])
        return lines, sections[name]
    lines = [
        *lines,
        f"{begin}{newline}",
        f"{name}:{newline}",
        f"{end}{newline}",
    ]
    section = _top_sections(lines)[name]
    _section_is_mapping(lines, section)
    return lines, section


def _remove_empty_managed_event(lines: list[str], event: str) -> list[str]:
    sections = _top_sections(lines)
    hooks = sections.get("hooks")
    if hooks is None:
        return lines
    child = _direct_child(lines, hooks, event)
    if child is None:
        return lines
    child_end = _child_end(lines, hooks, child)
    if any(
        _line_body(line).strip() and not _line_body(line).lstrip().startswith("#")
        for line in lines[child + 1 : child_end]
    ):
        return lines
    return lines[:child] + lines[child_end:]


def _remove_empty_managed_section(
    lines: list[str], name: str, *, begin: str, end: str
) -> list[str]:
    starts = [index for index, line in enumerate(lines) if _line_body(line).strip() == begin]
    ends = [index for index, line in enumerate(lines) if _line_body(line).strip() == end]
    if not starts and not ends:
        return lines
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise HermesConfigError("Hermes managed section marker is malformed")
    sections = _top_sections(lines)
    section = sections.get(name)
    if section is None or not (starts[0] < section.start < ends[0]):
        raise HermesConfigError("Hermes managed section marker is malformed")
    if any(
        _line_body(line).strip() and not _line_body(line).lstrip().startswith("#")
        for line in lines[section.start + 1 : ends[0]]
    ):
        return lines
    return lines[:starts[0]] + lines[ends[0] + 1 :]


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _hook_command(
    runtime: RuntimeCommand,
    *,
    role: str,
    client_id: str,
    target_url: str,
    core_data_dir: Path | None,
) -> str:
    command = (
        *runtime.mode("--hermes-hook"),
        "--hermes-role",
        role,
        "--hermes-client-id",
        client_id,
        "--hermes-target-url",
        target_url,
        "--hermes-core-data-dir",
        str((core_data_dir or CoreConfig.default().data_dir).resolve()),
        "--hermes-core-command",
        json.dumps(runtime.core(), ensure_ascii=False),
    )
    return subprocess.list2cmdline(command) if os.name == "nt" else shlex.join(command)


def _validate_loopback_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise HermesConfigError("Hermes Core URL has an invalid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise HermesConfigError("Hermes Core URL must be plain HTTP on 127.0.0.1")
    return value


def _validate_client_id(value: str, label: str) -> str:
    if type(value) is not str or not value or len(value) > 1_000 or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        raise HermesConfigError(f"Hermes {label} is outside its bound")
    return value


def _mcp_block(
    runtime: RuntimeCommand,
    *,
    client_id: str,
    target_url: str,
    core_data_dir: Path | None,
    newline: str,
) -> str:
    environment = {
        "ATC_MCP_PROFILE": HERMES_MCP_PROFILE,
        "ATC_TARGET_URL": target_url,
        "ATC_CLIENT_ID": client_id,
        "ATC_AUTO_START_CORE": "1",
        "ATC_CORE_COMMAND": json.dumps(runtime.core(), ensure_ascii=False),
        "ATC_CORE_DATA_DIR": str((core_data_dir or CoreConfig.default().data_dir).resolve()),
    }
    lines = [
        f"  {HERMES_CONFIG_MARKER_BEGIN}",
        f"  {HERMES_MCP_SERVER_KEY}:",
        f"    command: {_yaml_scalar(runtime.mcp()[0])}",
        f"    args: {json.dumps(list(runtime.mcp()[1:]), ensure_ascii=False)}",
        "    env:",
    ]
    lines.extend(f"      {key}: {_yaml_scalar(value)}" for key, value in environment.items())
    lines.extend(
        [
            "    enabled: true",
            "    tools:",
            "      include: [bootstrap_context, search_context, get_context_item, context_status]",
            "      resources: false",
            "      prompts: false",
            f"  {HERMES_CONFIG_MARKER_END}",
        ]
    )
    return newline.join(lines)


def _hook_block(begin: str, end: str, command: str, newline: str) -> str:
    return newline.join(
        [
            f"    {begin}",
            f"    - command: {_yaml_scalar(command)}",
            "      timeout: 3",
            f"    {end}",
        ]
    )


def _insert_mcp(
    lines: list[str], section: _Section, block: str, newline: str
) -> list[str]:
    if _direct_child(lines, section, HERMES_MCP_SERVER_KEY) is not None:
        raise HermesConfigError("Hermes MCP server name belongs to an unrelated entry")
    return _append_at(
        lines,
        _container_end_index(lines, section, HERMES_MCP_SECTION_MARKER_END),
        block,
        newline,
    )


def _insert_hook(
    lines: list[str],
    section: _Section,
    event: str,
    begin: str,
    end: str,
    command: str,
    newline: str,
) -> list[str]:
    child = _direct_child(lines, section, event)
    block = _hook_block(begin, end, command, newline)
    if child is None:
        event_block = newline.join([f"  {event}:", block])
        return _append_at(
            lines,
            _container_end_index(lines, section, HERMES_HOOKS_SECTION_MARKER_END),
            event_block,
            newline,
        )
    child_end = _child_end(lines, section, child)
    child_value = _line_body(lines[child]).split(":", 1)[1].strip()
    if child_value and child_value not in {"[]", "[ ]"}:
        raise HermesConfigError(f"Hermes hooks.{event} must be a YAML list")
    if child_value in {"[]", "[ ]"}:
        lines[child] = f"  {event}:{newline}"
        child_end = child + 1
    for index in range(child + 1, child_end):
        body = _line_body(lines[index])
        if body.strip() and not body.lstrip().startswith("#"):
            indent = len(body) - len(body.lstrip(" "))
            if indent < 4 or not body.lstrip().startswith("-"):
                raise HermesConfigError(f"Hermes hooks.{event} must be a YAML list")
    return _append_at(
        lines,
        min(child_end, _container_end_index(lines, section, HERMES_HOOKS_SECTION_MARKER_END)),
        block,
        newline,
    )


def _render_config(
    original: str,
    *,
    runtime: RuntimeCommand,
    read_client_id: str,
    capture_client_id: str | None,
    target_url: str,
    core_data_dir: Path | None,
    remove: bool,
) -> tuple[str, tuple[str, ...]]:
    newline = _newline(original)
    lines = _lines(original)
    sections = _top_sections(lines)
    for section_name in ("mcp_servers", "hooks"):
        section = sections.get(section_name)
        if section is not None:
            _section_is_mapping(lines, section)
    has_mcp_marker = any(
        _line_body(line).strip() == HERMES_CONFIG_MARKER_BEGIN for line in lines
    )
    has_read_marker = any(
        _line_body(line).strip() == HERMES_READ_HOOK_MARKER_BEGIN for line in lines
    )
    has_capture_marker = any(
        _line_body(line).strip() == HERMES_CAPTURE_HOOK_MARKER_BEGIN for line in lines
    )
    lines, mcp_commands = _remove_marker(
        lines, HERMES_CONFIG_MARKER_BEGIN, HERMES_CONFIG_MARKER_END
    )
    lines, read_commands = _remove_marker(
        lines, HERMES_READ_HOOK_MARKER_BEGIN, HERMES_READ_HOOK_MARKER_END
    )
    lines, capture_commands = _remove_marker(
        lines, HERMES_CAPTURE_HOOK_MARKER_BEGIN, HERMES_CAPTURE_HOOK_MARKER_END
    )
    old_commands = (*mcp_commands, *read_commands, *capture_commands)
    if has_read_marker:
        lines = _remove_empty_managed_event(lines, HERMES_READ_HOOK_EVENT)
    if has_capture_marker:
        lines = _remove_empty_managed_event(lines, HERMES_CAPTURE_HOOK_EVENT)
    if has_mcp_marker:
        lines = _remove_empty_managed_section(
            lines,
            "mcp_servers",
            begin=HERMES_MCP_SECTION_MARKER_BEGIN,
            end=HERMES_MCP_SECTION_MARKER_END,
        )
    if has_read_marker or has_capture_marker:
        lines = _remove_empty_managed_section(
            lines,
            "hooks",
            begin=HERMES_HOOKS_SECTION_MARKER_BEGIN,
            end=HERMES_HOOKS_SECTION_MARKER_END,
        )
    if remove:
        return "".join(lines), old_commands
    lines, _ = _ensure_managed_section(
        lines,
        "mcp_servers",
        newline,
        begin=HERMES_MCP_SECTION_MARKER_BEGIN,
        end=HERMES_MCP_SECTION_MARKER_END,
    )
    lines = _insert_mcp(
        lines,
        _top_sections(lines)["mcp_servers"],
        _mcp_block(
            runtime,
            client_id=read_client_id,
            target_url=target_url,
            core_data_dir=core_data_dir,
            newline=newline,
        ),
        newline,
    )
    lines, _ = _ensure_managed_section(
        lines,
        "hooks",
        newline,
        begin=HERMES_HOOKS_SECTION_MARKER_BEGIN,
        end=HERMES_HOOKS_SECTION_MARKER_END,
    )
    lines = _insert_hook(
        lines,
        _top_sections(lines)["hooks"],
        HERMES_READ_HOOK_EVENT,
        HERMES_READ_HOOK_MARKER_BEGIN,
        HERMES_READ_HOOK_MARKER_END,
        _hook_command(
            runtime,
            role="read",
            client_id=read_client_id,
            target_url=target_url,
            core_data_dir=core_data_dir,
        ),
        newline,
    )
    if capture_client_id is not None:
        lines = _insert_hook(
            lines,
            _top_sections(lines)["hooks"],
            HERMES_CAPTURE_HOOK_EVENT,
            HERMES_CAPTURE_HOOK_MARKER_BEGIN,
            HERMES_CAPTURE_HOOK_MARKER_END,
            _hook_command(
                runtime,
                role="capture",
                client_id=capture_client_id,
                target_url=target_url,
                core_data_dir=core_data_dir,
            ),
            newline,
        )
    rendered = "".join(lines)
    if rendered and not rendered.endswith(("\n", "\r")):
        rendered += newline
    return rendered, old_commands


def _read_allowlist(path: Path) -> tuple[_TextDocument, dict[str, Any]]:
    document = _read_document(path, maximum=MAX_HERMES_ALLOWLIST_BYTES)
    if not document.existed:
        return document, {"approvals": []}
    try:
        value = json.loads(document.original, object_pairs_hook=_reject_duplicate_keys)
    except (TypeError, ValueError) as exc:
        raise HermesConfigError("Hermes shell-hook allowlist is malformed") from exc
    if not isinstance(value, dict):
        raise HermesConfigError("Hermes shell-hook allowlist is malformed")
    approvals = value.get("approvals", [])
    if not isinstance(approvals, list) or any(
        not isinstance(item, dict)
        or not isinstance(item.get("event"), str)
        or not isinstance(item.get("command"), str)
        for item in approvals
    ):
        raise HermesConfigError("Hermes shell-hook allowlist is malformed")
    return document, value


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _render_allowlist(
    document: _TextDocument,
    value: dict[str, Any],
    *,
    desired: tuple[tuple[str, str], ...],
    remove_commands: Iterable[str],
) -> str:
    removals = frozenset(remove_commands)
    raw_approvals = value.get("approvals", [])
    assert isinstance(raw_approvals, list)
    approvals = [
        item
        for item in raw_approvals
        if not (isinstance(item, dict) and item.get("command") in removals)
    ]
    existing = {
        (item.get("event"), item.get("command"))
        for item in approvals
        if isinstance(item, dict)
    }
    for event, command in desired:
        if (event, command) not in existing:
            approvals.append({"event": event, "command": command})
            existing.add((event, command))
    updated = dict(value)
    updated["approvals"] = approvals
    if not document.existed and not desired:
        return ""
    rendered = json.dumps(updated, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    newline = _newline(document.original)
    return rendered if newline == "\n" else rendered.replace("\n", newline)


def _backup(path: Path) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.atc-backup-{timestamp}-{secrets.token_hex(3)}")
    shutil.copy2(path, backup)
    return backup


def _atomic_write(path: Path, content: str, *, mode: int | None = None) -> None:
    _reject_linked_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
    try:
        temporary.write_bytes(content.encode("utf-8"))
        if mode is not None:
            os.chmod(temporary, mode)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _apply_transaction(plans: tuple[tuple[_TextDocument, str], ...]) -> tuple[Path | None, ...]:
    changed = [(document, content) for document, content in plans if content != document.original]
    if not changed:
        return tuple(None for _document, _content in plans)
    backups: list[Path | None] = []
    written: list[tuple[_TextDocument, Path | None]] = []
    try:
        for document, content in changed:
            backup = _backup(document.path) if document.existed else None
            backups.append(backup)
            _atomic_write(document.path, content, mode=document.mode)
            written.append((document, backup))
    except BaseException:
        rollback_error: BaseException | None = None
        for document, _backup_path in reversed(written):
            try:
                if document.existed:
                    _atomic_write(document.path, document.original, mode=document.mode)
                else:
                    document.path.unlink(missing_ok=True)
            except BaseException as exc:
                rollback_error = rollback_error or exc
        for backup in backups:
            if backup is not None:
                backup.unlink(missing_ok=True)
        if rollback_error is not None:
            raise HermesConfigError(
                "Hermes configuration failed and its prior state could not be restored"
            ) from rollback_error
        raise
    by_path = {
        document.path: backup
        for (document, _content), backup in zip(changed, backups, strict=True)
    }
    return tuple(by_path.get(document.path) for document, _content in plans)


def configure_hermes(
    runtime: RuntimeCommand,
    *,
    read_client_id: str,
    capture_client_id: str | None = None,
    profile: str | None = None,
    config_path: Path | None = None,
    allowlist_path: Path | None = None,
    target_url: str = "http://127.0.0.1:7337",
    core_data_dir: Path | None = None,
    read_token: str | None = None,
    read_credential_storage: str | None = None,
    capture_token: str | None = None,
    capture_credential_storage: str | None = None,
) -> HermesConfigResult:
    """Install one read MCP and selected lifecycle hooks for exactly one profile."""

    del read_token, capture_token
    if read_credential_storage != OS_CREDENTIAL_STORAGE or (
        capture_client_id is not None and capture_credential_storage != OS_CREDENTIAL_STORAGE
    ):
        raise HermesConfigError("Hermes integration requires the operating-system credential store")
    read_id = _validate_client_id(read_client_id, "read client ID")
    capture_id = (
        _validate_client_id(capture_client_id, "capture client ID")
        if capture_client_id is not None
        else None
    )
    target = _validate_loopback_url(target_url)
    resolved = resolve_hermes_profile(profile, config_path=config_path)
    selected_config_path = resolved.config_path
    selected_allowlist_path = (
        _absolute_path(allowlist_path.expanduser())
        if allowlist_path is not None
        else resolved.allowlist_path
    )
    config_document = _read_document(selected_config_path, maximum=MAX_HERMES_CONFIG_BYTES)
    allowlist_document, allowlist_value = _read_allowlist(selected_allowlist_path)
    updated_config, old_commands = _render_config(
        config_document.original,
        runtime=runtime,
        read_client_id=read_id,
        capture_client_id=capture_id,
        target_url=target,
        core_data_dir=core_data_dir,
        remove=False,
    )
    read_command = _hook_command(
        runtime,
        role="read",
        client_id=read_id,
        target_url=target,
        core_data_dir=core_data_dir,
    )
    desired = [(HERMES_READ_HOOK_EVENT, read_command)]
    if capture_id is not None:
        desired.append(
            (
                HERMES_CAPTURE_HOOK_EVENT,
                _hook_command(
                    runtime,
                    role="capture",
                    client_id=capture_id,
                    target_url=target,
                    core_data_dir=core_data_dir,
                ),
            )
        )
    updated_allowlist = _render_allowlist(
        allowlist_document,
        allowlist_value,
        desired=tuple(desired),
        remove_commands=old_commands,
    )
    config_backup, allowlist_backup = _apply_transaction(
        ((config_document, updated_config), (allowlist_document, updated_allowlist))
    )
    config_changed = updated_config != config_document.original
    allowlist_changed = updated_allowlist != allowlist_document.original
    return HermesConfigResult(
        client="Hermes",
        profile=resolved.name,
        config_path=selected_config_path,
        allowlist_path=selected_allowlist_path,
        changed=config_changed or allowlist_changed,
        config_changed=config_changed,
        allowlist_changed=allowlist_changed,
        restart_required=config_changed,
        hook_consent_authorized=allowlist_changed,
        config_backup_path=config_backup,
        allowlist_backup_path=allowlist_backup,
        managed_client_ids=tuple(item for item in (read_id, capture_id) if item is not None),
    )


def disconnect_hermes(
    *,
    profile: str | None = None,
    config_path: Path | None = None,
    allowlist_path: Path | None = None,
) -> HermesConfigResult:
    """Remove only ATC-owned Hermes surfaces from one explicitly selected profile."""

    resolved = resolve_hermes_profile(profile, config_path=config_path)
    selected_config_path = resolved.config_path
    selected_allowlist_path = (
        _absolute_path(allowlist_path.expanduser())
        if allowlist_path is not None
        else resolved.allowlist_path
    )
    config_document = _read_document(selected_config_path, maximum=MAX_HERMES_CONFIG_BYTES)
    allowlist_document, allowlist_value = _read_allowlist(selected_allowlist_path)
    updated_config, old_commands = _render_config(
        config_document.original,
        runtime=RuntimeCommand.current(),
        read_client_id="placeholder",
        capture_client_id=None,
        target_url="http://127.0.0.1:7337",
        core_data_dir=None,
        remove=True,
    )
    updated_allowlist = _render_allowlist(
        allowlist_document,
        allowlist_value,
        desired=(),
        remove_commands=old_commands,
    )
    config_backup, allowlist_backup = _apply_transaction(
        ((config_document, updated_config), (allowlist_document, updated_allowlist))
    )
    return HermesConfigResult(
        client="Hermes",
        profile=resolved.name,
        config_path=selected_config_path,
        allowlist_path=selected_allowlist_path,
        changed=updated_config != config_document.original
        or updated_allowlist != allowlist_document.original,
        config_changed=updated_config != config_document.original,
        allowlist_changed=updated_allowlist != allowlist_document.original,
        restart_required=updated_config != config_document.original,
        hook_consent_authorized=False,
        config_backup_path=config_backup,
        allowlist_backup_path=allowlist_backup,
    )


def read_hermes_registration_ids(
    *, profile: str | None = None, config_path: Path | None = None
) -> dict[str, str]:
    """Read only opaque ATC IDs from the managed block for runtime repair."""

    resolved = resolve_hermes_profile(profile, config_path=config_path)
    document = _read_document(resolved.config_path, maximum=MAX_HERMES_CONFIG_BYTES)
    if not document.existed:
        return {}
    lines = _lines(document.original)
    starts = [
        index
        for index, line in enumerate(lines)
        if _line_body(line).strip() == HERMES_CONFIG_MARKER_BEGIN
    ]
    ends = [
        index
        for index, line in enumerate(lines)
        if _line_body(line).strip() == HERMES_CONFIG_MARKER_END
    ]
    if not starts and not ends:
        return {}
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise HermesConfigError("Hermes managed configuration marker is malformed")
    managed = "".join(lines[starts[0] : ends[0] + 1])
    ids: dict[str, str] = {}
    for line in managed.splitlines():
        if "ATC_CLIENT_ID:" not in line:
            continue
        value = line.split("ATC_CLIENT_ID:", 1)[1].strip()
        try:
            parsed = json.loads(value)
        except ValueError as exc:
            raise HermesConfigError("Hermes managed client ID is malformed") from exc
        if isinstance(parsed, str):
            ids["read"] = _validate_client_id(parsed, "managed client ID")
    for begin, end, role in (
        (HERMES_READ_HOOK_MARKER_BEGIN, HERMES_READ_HOOK_MARKER_END, "read"),
        (HERMES_CAPTURE_HOOK_MARKER_BEGIN, HERMES_CAPTURE_HOOK_MARKER_END, "capture"),
    ):
        _, commands = _remove_marker(lines, begin, end)
        for command in commands:
            try:
                arguments = shlex.split(command, posix=os.name != "nt")
            except ValueError as exc:
                raise HermesConfigError("Hermes managed hook command is malformed") from exc
            if "--hermes-role" not in arguments or "--hermes-client-id" not in arguments:
                raise HermesConfigError("Hermes managed hook command is incomplete")
            role_index = arguments.index("--hermes-role")
            client_index = arguments.index("--hermes-client-id")
            if (
                role_index + 1 >= len(arguments)
                or client_index + 1 >= len(arguments)
                or arguments[role_index + 1] != role
            ):
                raise HermesConfigError("Hermes managed hook command has the wrong role")
            ids[role] = _validate_client_id(arguments[client_index + 1], "managed client ID")
    return ids


__all__ = [
    "HERMES_CAPTURE_CLIENT_NAME",
    "HERMES_CAPTURE_HOOK_EVENT",
    "HERMES_CONFIG_MARKER_BEGIN",
    "HERMES_CONFIG_MARKER_END",
    "HERMES_DEFAULT_PROFILE",
    "HERMES_MCP_PROFILE",
    "HERMES_MCP_SERVER_KEY",
    "HERMES_READ_CLIENT_NAME",
    "HERMES_READ_HOOK_EVENT",
    "HermesConfigError",
    "HermesConfigResult",
    "HermesProfile",
    "active_hermes_profile",
    "configure_hermes",
    "disconnect_hermes",
    "discover_hermes_profiles",
    "hermes_allowlist_path",
    "hermes_config_path",
    "hermes_home",
    "hermes_is_detected",
    "read_hermes_registration_ids",
    "resolve_hermes_profile",
]
