"""Safe, reversible MCP client configuration adapters."""

from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .desktop_runtime import RuntimeCommand

MANAGED_BEGIN = "# BEGIN All The Context managed MCP"
MANAGED_END = "# END All The Context managed MCP"
TABLE_HEADER = "[mcp_servers.all_the_context]"


@dataclass(frozen=True, slots=True)
class ClientConfigResult:
    client: str
    path: Path
    backup_path: Path | None
    changed: bool


def codex_home() -> Path:
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser().resolve() if configured else Path.home() / ".codex"


def codex_config_path() -> Path:
    return codex_home() / "config.toml"


def codex_is_detected() -> bool:
    home = codex_home()
    return home.is_dir() or (Path.home() / ".codex").is_dir()


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


def _replace_managed_section(existing: str, block: str) -> str:
    marker_pattern = re.compile(
        rf"(?ms)^{re.escape(MANAGED_BEGIN)}\r?\n.*?^{re.escape(MANAGED_END)}\s*"
    )
    if marker_pattern.search(existing):
        return marker_pattern.sub(lambda _match: block + "\n", existing, count=1)

    header_pattern = re.compile(r"(?m)^\[mcp_servers\.all_the_context\]\s*$")
    match = header_pattern.search(existing)
    if match:
        following_table = re.compile(r"(?m)^\[").search(existing, match.end())
        end = following_table.start() if following_table else len(existing)
        prefix = existing[: match.start()].rstrip()
        suffix = existing[end:].lstrip()
        return "\n\n".join(part for part in (prefix, block, suffix) if part) + "\n"

    prefix = existing.rstrip()
    return f"{prefix}\n\n{block}\n" if prefix else f"{block}\n"


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

    backup: Path | None = None
    if config_path.is_file():
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = config_path.with_name(f"{config_path.name}.atc-backup-{timestamp}")
        shutil.copy2(config_path, backup)

    temporary = config_path.with_suffix(config_path.suffix + ".atc-new")
    temporary.write_text(updated, encoding="utf-8", newline="\n")
    temporary.replace(config_path)
    return ClientConfigResult("Codex", config_path, backup, True)
