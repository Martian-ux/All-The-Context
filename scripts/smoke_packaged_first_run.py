"""Exercise frozen first-run setup, installed MCP, retrieval, and graceful shutdown.

This smoke deliberately isolates credential storage from the host OS keyring:
it forces the null keyring backend and explicitly enables the insecure
development credential file. That proves packaged first-run install, MCP,
startup, and recovery paths under a non-secret isolated store.

It is not BETA-P03 / real OS credential acceptance. Real Windows Credential
Manager and macOS Keychain round-trips are exercised separately by
``scripts/smoke_desktop_artifact.py --packaged-credential-acceptance`` and
``scripts/smoke_platform_acceptance.py``.
"""

# The smoke must prepend its checkout source before importing package modules;
# suppress the corresponding intentional import-order diagnostic below.
# ruff: noqa: E402

from __future__ import annotations

import atexit
import hashlib
import html
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, TextIO

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "allthecontext" / "src"))

import anyio
import httpx2 as httpx
from allthecontext import __version__
from allthecontext.build_identity import BuildIdentity, BuildIdentityError
from allthecontext.credentials import (
    DEVELOPMENT_FALLBACK_ENV,
    FALLBACK_CREDENTIAL_STORAGE,
)
from allthecontext.desktop import (
    _HEADLESS_SETUP_ERROR_CODES,
    _HEADLESS_SETUP_SUBPHASES,
    WINDOWS_INSTALL_REMOVAL_TIMEOUT_SECONDS,
)
from allthecontext.installed_component_manifest import (
    CHECKSUM_FILE_NAME,
    MANIFEST_FILE_NAME,
    canonical_json,
)
from allthecontext.release_manifest import sha256_file
from allthecontext.windows_update_helper import (
    HelperPhase,
    UpdateJournal,
    bind_handoff_state,
    bind_recovery_authority,
    journal_failure_diagnostic,
)
from filelock import FileLock
from filelock import Timeout as FileLockTimeout
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from smoke_desktop_artifact import artifact_executable

# Explicit, isolated, non-secret smoke only. Production installs never set this.
ISOLATED_SMOKE_CREDENTIAL_BACKEND = "keyring.backends.null.Keyring"
WINDOWS_INSTALL_REMOVAL_OBSERVATION_SECONDS = WINDOWS_INSTALL_REMOVAL_TIMEOUT_SECONDS + 5.0
CORE_STOP_TIMEOUT_SECONDS = 10.0


@contextmanager
def _temporary_environment(values: Mapping[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# Work-tree artifacts inspected only for presence / closed-schema projection.
_DIAGNOSTIC_RELATIVE_NAMES = (
    "setup-report.json",
    "reopen-report.json",
    "uninstall-report.json",
    "mcp-stderr.log",
    "mcp-restart-stderr.log",
    "data/credentials.development.json",
    "data/core.sqlite3",
    "codex/config.toml",
)

# Fields from headless setup reports that may appear in failure summaries.
_SAFE_SETUP_REPORT_KEYS = frozenset(
    {
        "setup",
        "credential_storage",
        "error_type",
        "error_code",
        "setup_stage",
        "setup_subphase",
    }
)
_SAFE_SETUP_STAGES = frozenset({"prepare_installed_runtime", "perform_setup", "write_report"})
_SENSITIVE_SETUP_PRESENCE_KEYS = (
    "dashboard_url",
    "client_id",
    "vault_id",
    "log_path",
    "core_url",
    "codex",
    "claude",
    "startup",
    "warnings",
    "diagnostics_path",
)
_MAX_REDACTED_ERROR_CHARS = 500
_MAX_SETUP_REPORT_BYTES = 1_048_576
_MAX_SMOKE_RESPONSE_BYTES = 1_048_576
_SMOKE_RESPONSE_CHUNK_BYTES = 64 * 1024
_SMOKE_RESPONSE_LIMIT_ERROR = "smoke response exceeded maximum size"
_CLOSED_DIAGNOSTIC_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class _SetupReportTooLarge(ValueError):
    """The imported setup report exceeded the bounded diagnostics input."""


def _load_setup_report(path: Path) -> object:
    """Load an imported setup report with a bounded read and JSON parse."""

    with path.open("rb") as handle:
        content = handle.read(_MAX_SETUP_REPORT_BYTES + 1)
    if len(content) > _MAX_SETUP_REPORT_BYTES:
        raise _SetupReportTooLarge
    return json.loads(content.decode("utf-8"))


def packaged_smoke_parent(
    *,
    root: Path = ROOT,
    environ: Mapping[str, str] | None = None,
) -> Path:
    """Resolve an optional isolated smoke parent outside the source checkout."""

    active_environment = os.environ if environ is None else environ
    override = active_environment.get("ATC_PACKAGED_SMOKE_PARENT")
    if not override:
        return root / "tmp"
    requested = Path(override).expanduser()
    if not requested.is_absolute():
        raise SystemExit("ATC_PACKAGED_SMOKE_PARENT must be an absolute path")
    resolved = requested.resolve()
    source = root.resolve()
    if resolved == source or resolved.is_relative_to(source):
        raise SystemExit("ATC_PACKAGED_SMOKE_PARENT must be outside the source checkout")
    return resolved


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _read_http_response(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    timeout: float = 3.0,
) -> tuple[int, bytes]:
    """Read one bounded smoke response with urllib-compatible redirects."""

    with (
        httpx.Client(timeout=timeout, follow_redirects=True) as client,
        client.stream(method, url, headers=headers) as response,
    ):
        response.raise_for_status()
        content = bytearray()
        received = 0
        for chunk in response.iter_bytes(chunk_size=_SMOKE_RESPONSE_CHUNK_BYTES):
            received += len(chunk)
            if received > _MAX_SMOKE_RESPONSE_BYTES:
                raise RuntimeError(_SMOKE_RESPONSE_LIMIT_ERROR)
            content.extend(chunk)
        return response.status_code, bytes(content)


def api_request(url: str, token: str, *, method: str = "GET") -> dict[str, Any]:
    _, content = _read_http_response(
        url,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    value = json.loads(content.decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"unexpected API response from {url}")
    return value


def browser_session_from_handoff_html(handoff_html: str) -> str | None:
    """Read the opaque browser capability from its HTML-escaped data attribute."""

    match = re.search(r'\bdata-browser-token="([^"]+)"', handoff_html)
    if match is None:
        return None
    return html.unescape(match.group(1))


def wait_for_core_lock_release(data_dir: Path, *, deadline: float | None = None) -> None:
    """Wait for the Core process, not just its HTTP listener, to finish exiting."""

    if deadline is None:
        deadline = time.monotonic() + CORE_STOP_TIMEOUT_SECONDS
    timeout = deadline - time.monotonic()
    if timeout <= 0:
        raise RuntimeError("installed Core did not release its process lock")
    lock = FileLock(str(data_dir / "core.lock"))
    acquired = False
    try:
        lock.acquire(timeout=timeout)
        acquired = True
    except FileLockTimeout as exc:
        raise RuntimeError("installed Core did not release its process lock") from exc
    finally:
        if acquired:
            lock.release()


def stop_core(base_url: str, admin_token: str, *, data_dir: Path | None = None) -> None:
    if data_dir is None:
        configured_data_dir = os.environ.get("ATC_CORE_DATA_DIR")
        if not configured_data_dir:
            raise RuntimeError("packaged smoke Core data directory is unavailable")
        data_dir = Path(configured_data_dir).expanduser().resolve()
    deadline = time.monotonic() + CORE_STOP_TIMEOUT_SECONDS
    with suppress(OSError, httpx.HTTPError):
        api_request(f"{base_url}/v1/admin/shutdown", admin_token, method="POST")
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("installed Core did not shut down within ten seconds")
        try:
            _read_http_response(f"{base_url}/health", timeout=min(0.2, remaining))
        except (OSError, httpx.HTTPError):
            wait_for_core_lock_release(data_dir, deadline=deadline)
            return
        time.sleep(min(0.1, remaining))


def wait_for_core(base_url: str, admin_token: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if api_request(f"{base_url}/v1/context/status", admin_token).get("core_online") is True:
                return
        except (OSError, httpx.HTTPError):
            pass
        time.sleep(0.1)
    raise RuntimeError("transactional updater did not restart Core within twenty seconds")


def read_packaged_build_identity(
    executable: Path,
    *,
    report_path: Path,
    environment: Mapping[str, str],
) -> BuildIdentity:
    """Read the identity from the exact packaged executable used by the smoke."""

    report_path.unlink(missing_ok=True)
    completed = subprocess.run(
        [str(executable), "--diagnostics", str(report_path)],
        env=dict(environment),
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    try:
        if completed.returncode != 0:
            raise RuntimeError("packaged diagnostics did not complete")
        payload = json.loads(report_path.read_text(encoding="utf-8"))
        raw_identity = payload.get("build_identity") if isinstance(payload, dict) else None
        if not isinstance(raw_identity, dict):
            raise RuntimeError("packaged diagnostics did not contain a build identity")
        identity_fields = {
            field: raw_identity.get(field)
            for field in (
                "schema_version",
                "version",
                "channel",
                "platform",
                "architecture",
                "source_commit",
            )
        }
        identity = BuildIdentity.from_mapping(identity_fields)
    except (BuildIdentityError, OSError, TypeError, ValueError, KeyError) as exc:
        raise RuntimeError("packaged build identity is invalid") from exc
    finally:
        report_path.unlink(missing_ok=True)
    if (
        identity.version != __version__
        or identity.platform != "windows"
        or identity.architecture != "x86_64"
    ):
        raise RuntimeError("packaged build identity does not match the Windows smoke")
    return identity


def prepare_packaged_update_transaction(
    *,
    data_dir: Path,
    installed_app: Path,
    release_app: Path,
    operation_id: str,
    core_port: int,
    target_version: str,
    packaged_identity: BuildIdentity,
) -> tuple[Path, Path]:
    if (
        packaged_identity.version != target_version
        or packaged_identity.platform != "windows"
        or packaged_identity.architecture != "x86_64"
    ):
        raise RuntimeError("packaged update identity does not match the Windows target")
    source_commit = packaged_identity.source_commit
    updates = data_dir / "updates"
    transaction_dir = updates / "transactions" / operation_id
    rollback_dir = transaction_dir / "rollback"
    replacement_dir = transaction_dir / "replacement"
    rollback_dir.mkdir(parents=True)
    replacement_dir.mkdir()
    transaction_helper = transaction_dir / "AllTheContextUpdater.exe"
    stable_helper = installed_app.with_name("AllTheContextUpdater.exe")
    stable_mcp = installed_app.with_name("AllTheContextMCP.exe")
    stable_recovery = installed_app.with_name("AllTheContextRecovery.exe")
    if not stable_helper.is_file() or not stable_mcp.is_file() or not stable_recovery.is_file():
        raise RuntimeError("installed update, MCP, or recovery helper is missing")
    shutil.copy2(stable_helper, transaction_helper)
    replacement = replacement_dir / "AllTheContextSetup.exe"
    candidate_mcp = replacement_dir / "AllTheContextMCP.exe"
    candidate_recovery = replacement_dir / "AllTheContextRecovery.exe"
    candidate_update_helper = replacement_dir / "AllTheContextUpdater.exe"
    rollback_app = rollback_dir / "AllTheContext.exe"
    rollback_mcp = rollback_dir / "AllTheContextMCP.exe"
    rollback_recovery = rollback_dir / "AllTheContextRecovery.exe"
    rollback_update_helper = rollback_dir / "AllTheContextUpdater.exe"
    shutil.copy2(release_app, replacement)
    shutil.copy2(stable_mcp, candidate_mcp)
    shutil.copy2(stable_recovery, candidate_recovery)
    shutil.copy2(stable_helper, candidate_update_helper)
    shutil.copy2(installed_app, rollback_app)
    shutil.copy2(stable_mcp, rollback_mcp)
    shutil.copy2(stable_recovery, rollback_recovery)
    shutil.copy2(stable_helper, rollback_update_helper)

    database = data_dir / "core.sqlite3"
    backup = updates / "backups" / f"packaged-smoke-{operation_id}.sqlite3"
    backup.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(database)
    destination = sqlite3.connect(backup)
    try:
        source.backup(destination)
        if destination.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise RuntimeError("packaged update backup was not valid")
    finally:
        destination.close()
        source.close()

    journal_path = transaction_dir / "journal.json"
    state_path = updates / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        {
            "phase": "restart_required",
            "current_version": __version__,
            "offered_version": target_version,
            "downloaded_path": None,
            "backup_path": str(backup),
            "last_error": None,
            "operation_id": operation_id,
            "transaction_path": str(journal_path),
            "recovery_attempts": int(state.get("recovery_attempts", 0)) + 1,
            "current_source_commit": source_commit,
            "offered_source_commit": source_commit,
        }
    )
    state_temporary = state_path.with_name(f"{state_path.name}.{operation_id}.atc-new")
    state_temporary.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    state_temporary.replace(state_path)

    replacement_digest, replacement_size = sha256_file(replacement)
    candidate_mcp_digest, candidate_mcp_size = sha256_file(candidate_mcp)
    candidate_recovery_digest, candidate_recovery_size = sha256_file(candidate_recovery)
    candidate_update_digest, candidate_update_size = sha256_file(candidate_update_helper)
    rollback_digest, rollback_size = sha256_file(rollback_app)
    rollback_mcp_digest, rollback_mcp_size = sha256_file(rollback_mcp)
    rollback_recovery_digest, rollback_recovery_size = sha256_file(rollback_recovery)
    rollback_update_digest, rollback_update_size = sha256_file(rollback_update_helper)
    recovery_helper_digest, recovery_helper_size = sha256_file(transaction_helper)
    backup_digest, backup_size = sha256_file(backup)
    component_manifest = transaction_dir / MANIFEST_FILE_NAME
    component_payload = {
        "architecture": "x86_64",
        "component_count": 4,
        "components": [
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContext.exe",
                "role": "main",
                "sha256": replacement_digest,
                "size": replacement_size,
            },
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContextMCP.exe",
                "role": "mcp",
                "sha256": candidate_mcp_digest,
                "size": candidate_mcp_size,
            },
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContextRecovery.exe",
                "role": "recovery",
                "sha256": candidate_recovery_digest,
                "size": candidate_recovery_size,
            },
            {
                "authenticode": {"status": "not-present"},
                "filename": "AllTheContextUpdater.exe",
                "role": "updater",
                "sha256": candidate_update_digest,
                "size": candidate_update_size,
            },
        ],
        "manifest_type": "installed-component",
        "package": {
            "direct_package": {
                "filename": "all-the-context-direct-unsigned.exe",
                "sha256": replacement_digest,
                "size": replacement_size,
            },
            "filename": "AllTheContextSetup.exe",
            "sha256": replacement_digest,
            "size": replacement_size,
        },
        "platform": "windows",
        "schema_version": 1,
        "source_commit": source_commit,
        "version": target_version,
    }
    component_raw = canonical_json(component_payload)
    component_manifest.write_bytes(component_raw)
    component_manifest.with_name(CHECKSUM_FILE_NAME).write_bytes(
        f"{hashlib.sha256(component_raw).hexdigest()}  {MANIFEST_FILE_NAME}\n".encode("ascii")
    )
    now = "2026-07-22T12:00:00+00:00"
    journal = UpdateJournal(
        operation_id=operation_id,
        phase=HelperPhase.PREPARED,
        current_version=__version__,
        target_version=target_version,
        parent_pid=0,
        application_path=str(installed_app),
        replacement_path=str(replacement),
        replacement_sha256=replacement_digest,
        replacement_size=replacement_size,
        rollback_application_path=str(rollback_app),
        rollback_application_sha256=rollback_digest,
        rollback_application_size=rollback_size,
        mcp_path=str(stable_mcp),
        rollback_mcp_path=str(rollback_mcp),
        rollback_mcp_sha256=rollback_mcp_digest,
        rollback_mcp_size=rollback_mcp_size,
        recovery_path=str(stable_recovery),
        rollback_recovery_path=str(rollback_recovery),
        rollback_recovery_sha256=rollback_recovery_digest,
        rollback_recovery_size=rollback_recovery_size,
        stable_update_helper_path=str(stable_helper),
        rollback_update_helper_path=str(rollback_update_helper),
        rollback_update_helper_sha256=rollback_update_digest,
        rollback_update_helper_size=rollback_update_size,
        database_path=str(database),
        database_backup_path=str(backup),
        database_backup_sha256=backup_digest,
        database_backup_size=backup_size,
        state_path=str(state_path),
        helper_path=str(transaction_helper),
        core_host="127.0.0.1",
        core_port=core_port,
        recovery_helper_sha256=recovery_helper_digest,
        recovery_helper_size=recovery_helper_size,
        component_manifest_path=str(component_manifest),
        component_manifest_sha256=hashlib.sha256(component_raw).hexdigest(),
        component_manifest_size=len(component_raw),
        current_source_commit=source_commit,
        target_source_commit=source_commit,
        rollback_source_commit=source_commit,
        recovery_source_commit=source_commit,
        created_at=now,
        updated_at=now,
    )
    journal.save(journal_path)
    bind_recovery_authority(journal, journal_path)
    bind_handoff_state(journal, journal_path)
    return transaction_helper, journal_path


def run_packaged_rollback_smoke(
    *,
    helper: Path,
    journal: Path,
    environment: dict[str, str],
) -> None:
    """Exercise rollback, including one exact persisted-recovery re-entry."""

    command = [str(helper), "--journal", str(journal)]
    first = subprocess.run(
        command,
        env=environment,
        check=False,
        timeout=180,
    )
    if first.returncode == 2:
        return
    if first.returncode == 3:
        try:
            retry_state: Any = json.loads(journal.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            retry_state = None
        if (
            isinstance(retry_state, dict)
            and retry_state.get("phase") == "rolling_back"
            and retry_state.get("last_error_code") == "rollback_retry_required"
        ):
            retried = subprocess.run(
                command,
                env=environment,
                check=False,
                timeout=180,
            )
            if retried.returncode == 0:
                return
            retry_code: int | None = retried.returncode
        else:
            retry_code = None
    else:
        retry_code = None
    raise SystemExit(
        "packaged updater did not report or recover the exercised rollback: "
        f"first={first.returncode}; retry={retry_code}; "
        f"journal={journal_failure_diagnostic(journal)}"
    )


def packaged_update_failure_diagnostic(journal: Path, return_code: int) -> str:
    """Project authoritative updater evidence without copying child output."""

    try:
        evidence = json.loads(journal_failure_diagnostic(journal))
    except (TypeError, json.JSONDecodeError):
        evidence = {"journal_status": "diagnostic_unavailable"}
    if not isinstance(evidence, dict):
        evidence = {"journal_status": "diagnostic_invalid"}
    return json.dumps(
        {"journal": evidence, "return_code": return_code},
        sort_keys=True,
    )


_PACKAGED_MCP_PROFILE = "codex_read"
_PACKAGED_MCP_TOOLS = frozenset(
    {
        "bootstrap_context",
        "codex_user_prompt_submit_read",
        "get_context_item",
        "search_context",
    }
)


def validate_packaged_mcp_surface(profile: str, names: set[str]) -> None:
    """Require the packaged Codex connection to remain exactly read-only."""

    if profile != _PACKAGED_MCP_PROFILE:
        raise RuntimeError("packaged MCP did not use the managed read-only profile")
    missing = _PACKAGED_MCP_TOOLS - names
    unexpected = names - _PACKAGED_MCP_TOOLS
    if missing or unexpected:
        raise RuntimeError(
            "packaged MCP tool surface did not match the read-only profile: "
            f"missing={sorted(missing)}; unexpected={sorted(unexpected)}"
        )


async def exercise_mcp(parameters: StdioServerParameters, errlog: TextIO) -> None:
    async with (
        stdio_client(parameters, errlog=errlog) as streams,
        ClientSession(*streams) as session,
    ):
        await session.initialize()
        tools = await session.list_tools()
        names = {tool.name for tool in tools.tools}
        profile = str((parameters.env or {}).get("ATC_MCP_PROFILE", ""))
        validate_packaged_mcp_surface(profile, names)
        searched = await session.call_tool(
            "search_context",
            {"query": "packaged smoke readiness", "limit": 1},
        )
        if searched.is_error is True or searched.structured_content is None:
            raise RuntimeError("packaged read-only MCP query failed")


def redact_smoke_diagnostic_text(value: str) -> str:
    """Project free text to a content-free diagnostic string (no secrets/paths/URLs)."""

    message = value.strip() or "empty"
    patterns = (
        (r"(?i)(authorization\s*:\s*bearer\s+)\S+", r"\1[redacted]"),
        (r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]"),
        (r"atc-edge-v1\.[A-Za-z0-9_-]+", "[redacted Edge enrollment]"),
        (
            r"(?i)((?:token|secret|recovery[_ -]?code|password|api[_-]?key|"
            r"atc_client_token|client_token)\s*[=:]\s*)[^\s,;\"']+",
            r"\1[redacted]",
        ),
        (r"(?i)([?&](?:ticket|atc_token|token)=)[^&\s\"']+", r"\1[redacted]"),
        (r"https?://[^\s\"']+", "[redacted url]"),
        (r"[A-Za-z]:\\[^\s\"']+", "[redacted path]"),
        (r"/(?:Users|home|tmp|var|opt|private)[^\s\"']*", "[redacted path]"),
        (
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
            "[redacted id]",
        ),
    )
    for pattern, replacement in patterns:
        message = re.sub(pattern, replacement, message)
    return message[:_MAX_REDACTED_ERROR_CHARS]


def project_setup_report_for_diagnostics(raw: object) -> dict[str, Any]:
    """Allowlist a setup report into content-free fields only."""

    if not isinstance(raw, dict):
        return {"parseable": False}
    projected: dict[str, Any] = {"parseable": True}
    setup = raw.get("setup")
    if isinstance(setup, str) and setup in {"passed", "failed"}:
        projected["setup"] = setup
    storage = raw.get("credential_storage")
    if isinstance(storage, str) and storage in {
        FALLBACK_CREDENTIAL_STORAGE,
        "operating-system credential store",
    }:
        projected["credential_storage"] = storage
    error_type = raw.get("error_type")
    if isinstance(error_type, str) and error_type in {
        "RuntimeError",
        "OSError",
        "ValueError",
        "Exception",
    }:
        projected["error_type"] = error_type[:80]
    error_code = raw.get("error_code")
    if isinstance(error_code, str) and error_code in _HEADLESS_SETUP_ERROR_CODES:
        projected["error_code"] = error_code
    setup_stage = raw.get("setup_stage")
    if isinstance(setup_stage, str) and setup_stage in _SAFE_SETUP_STAGES:
        projected["setup_stage"] = setup_stage
    setup_subphase = raw.get("setup_subphase")
    if isinstance(setup_subphase, str) and setup_subphase in _HEADLESS_SETUP_SUBPHASES:
        projected["setup_subphase"] = setup_subphase
    projected["sensitive_fields_present"] = {
        key: key in raw and raw.get(key) not in (None, "", [], {})
        for key in _SENSITIVE_SETUP_PRESENCE_KEYS
    }
    # Never copy unknown keys through (including dashboard_url / tokens).
    assert (
        projected.keys()
        <= {
            "parseable",
            "setup",
            "credential_storage",
            "error_type",
            "error_code",
            "sensitive_fields_present",
        }
        | _SAFE_SETUP_REPORT_KEYS
    )
    return projected


def _relative_artifact_presence(work: Path) -> dict[str, bool]:
    return {name: (work / name).is_file() for name in _DIAGNOSTIC_RELATIVE_NAMES}


def build_failure_diagnostic_summary(
    *,
    phase: str,
    return_code: int | None,
    work: Path,
    report_path: Path | None = None,
    stdout_present: bool = False,
    stderr_present: bool = False,
    detail: str | None = None,
) -> dict[str, Any]:
    """Build a content-free failure summary; never embed raw reports or streams."""

    summary: dict[str, Any] = {
        "application": "All The Context packaged first-run smoke",
        "outcome": "failed",
        "phase": phase,
        "return_code": return_code,
        "labels": {
            "credential_mode": "explicit-isolated-development-file",
            "os_credential_acceptance": "not_this_smoke",
        },
        "artifacts_present": _relative_artifact_presence(work),
        "stdout_present": bool(stdout_present),
        "stderr_present": bool(stderr_present),
    }
    if detail:
        summary["detail_code"] = (
            detail if _CLOSED_DIAGNOSTIC_CODE.fullmatch(detail) else "diagnostic_failure"
        )
    candidate = report_path if report_path is not None else work / "setup-report.json"
    if candidate.is_file():
        try:
            raw = _load_setup_report(candidate)
        except (
            OSError,
            UnicodeError,
            json.JSONDecodeError,
            RecursionError,
            _SetupReportTooLarge,
        ):
            summary["setup_report"] = {"parseable": False, "present": True}
        else:
            projected = project_setup_report_for_diagnostics(raw)
            projected["present"] = True
            summary["setup_report"] = projected
    else:
        summary["setup_report"] = {"present": False, "parseable": False}
    return summary


def write_failure_diagnostic_summary(
    summary: dict[str, Any],
    *,
    diagnostics_root: Path,
) -> Path:
    """Persist only the allowlisted summary under a separate diagnostics directory."""

    diagnostics_root.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    target = diagnostics_root / f"packaged-first-run-failure-{stamp}-{os.getpid()}.json"
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def emit_failure_diagnostics(
    *,
    phase: str,
    return_code: int | None,
    work: Path,
    diagnostics_root: Path,
    report_path: Path | None = None,
    stdout_present: bool = False,
    stderr_present: bool = False,
    detail: str | None = None,
) -> Path:
    """Write content-free diagnostics and print only safe relative location + schema."""

    summary = build_failure_diagnostic_summary(
        phase=phase,
        return_code=return_code,
        work=work,
        report_path=report_path,
        stdout_present=stdout_present,
        stderr_present=stderr_present,
        detail=detail,
    )
    target = write_failure_diagnostic_summary(summary, diagnostics_root=diagnostics_root)
    # Print only the filename and closed outcome fields — never raw streams or reports.
    setup_report = summary.get("setup_report")
    setup_error_code = setup_report.get("error_code") if isinstance(setup_report, dict) else None
    setup_stage = setup_report.get("setup_stage") if isinstance(setup_report, dict) else None
    setup_subphase = setup_report.get("setup_subphase") if isinstance(setup_report, dict) else None
    print(
        json.dumps(
            {
                "packaged_first_run_failure": True,
                "phase": phase,
                "return_code": return_code,
                "diagnostics_file": target.name,
                "setup_report_present": bool(summary.get("setup_report", {}).get("present")),
                # ``build_failure_diagnostic_summary`` has already reduced this
                # to the closed diagnostic vocabulary; exposing it here makes
                # hosted failures actionable without copying report contents.
                "setup_error_code": setup_error_code,
                "setup_stage": setup_stage,
                "setup_subphase": setup_subphase,
                "stdout_present": stdout_present,
                "stderr_present": stderr_present,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return target


def remove_work_tree(work: Path, *, timeout_seconds: float = 10.0) -> None:
    """Always delete the disposable work tree (credentials, vault, configs, binaries)."""

    deadline = time.monotonic() + timeout_seconds
    while work.exists() and time.monotonic() < deadline:
        try:
            shutil.rmtree(work)
        except OSError:
            time.sleep(0.1)
        else:
            return
    if work.exists():
        raise RuntimeError(f"temporary smoke data remained locked: {work.name}")


def recover_disposable_admin_token(work: Path) -> str:
    """Recover the disposable desktop token solely to stop Core during cleanup."""

    credential_path = work / "data" / "credentials.development.json"
    if not credential_path.is_file():
        return ""
    client_id = ""
    for report_name in ("setup-report.json", "reopen-report.json"):
        candidate = work / report_name
        if not candidate.is_file():
            continue
        try:
            report = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        value = report.get("client_id") if isinstance(report, dict) else None
        if isinstance(value, str) and value:
            client_id = value
            break
    if not client_id:
        return ""
    try:
        credentials = json.loads(credential_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ""
    token = credentials.get(f"client:{client_id}") if isinstance(credentials, dict) else None
    return token if isinstance(token, str) and token else ""


def scrub_sensitive_work_tree(work: Path) -> None:
    """Remove credential-, vault-, config-, ticket-, and log-bearing smoke state first."""

    root = work.resolve()
    directories = ("data", "codex", "config")
    files = (
        "data/credentials.development.json",
        "codex/config.toml",
        "setup-report.json",
        "reopen-report.json",
        "uninstall-report.json",
        "mcp-stderr.log",
        "mcp-restart-stderr.log",
    )
    # Delete plaintext credentials, client config, tickets, and logs before
    # attempting recursive vault cleanup; a still-exiting Core can temporarily
    # hold SQLite open on Windows.
    for name in files:
        candidate = (work / name).resolve()
        if not candidate.is_relative_to(root):
            raise RuntimeError("refusing unsafe smoke cleanup target")
        candidate.unlink(missing_ok=True)
    for name in directories:
        candidate = (work / name).resolve()
        if not candidate.is_relative_to(root):
            raise RuntimeError("refusing unsafe smoke cleanup target")
        if candidate.is_dir():
            shutil.rmtree(candidate)
        elif candidate.exists():
            candidate.unlink()


def _run_headless_setup(
    *,
    executable: Path,
    report_path: Path,
    environment: dict[str, str],
    extra_args: list[str],
    work: Path,
    diagnostics_root: Path,
    label: str,
) -> dict[str, Any]:
    """Run packaged headless setup; on failure write content-free diagnostics only."""

    completed = subprocess.run(
        [
            str(executable),
            "--headless-setup",
            str(report_path),
            *extra_args,
        ],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )
    stdout_present = bool(completed.stdout and completed.stdout.strip())
    stderr_present = bool(completed.stderr and completed.stderr.strip())
    if completed.returncode != 0:
        emit_failure_diagnostics(
            phase=label,
            return_code=completed.returncode,
            work=work,
            diagnostics_root=diagnostics_root,
            report_path=report_path,
            stdout_present=stdout_present,
            stderr_present=stderr_present,
            detail="subprocess_nonzero",
        )
        raise SystemExit(
            f"{label} exited {completed.returncode}; content-free diagnostics written "
            f"(work tree will be removed; windowed packages may only leave a setup report)"
        )
    if not report_path.is_file():
        emit_failure_diagnostics(
            phase=label,
            return_code=0,
            work=work,
            diagnostics_root=diagnostics_root,
            report_path=report_path,
            stdout_present=stdout_present,
            stderr_present=stderr_present,
            detail="setup_report_missing",
        )
        raise SystemExit(f"{label} did not write a setup report")
    try:
        report = _load_setup_report(report_path)
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        RecursionError,
        _SetupReportTooLarge,
    ):
        emit_failure_diagnostics(
            phase=label,
            return_code=0,
            work=work,
            diagnostics_root=diagnostics_root,
            report_path=report_path,
            stdout_present=stdout_present,
            stderr_present=stderr_present,
            detail="setup_report_unparseable",
        )
        raise SystemExit(f"{label} did not write a parseable setup report") from None
    if not isinstance(report, dict):
        emit_failure_diagnostics(
            phase=label,
            return_code=0,
            work=work,
            diagnostics_root=diagnostics_root,
            report_path=report_path,
            stdout_present=stdout_present,
            stderr_present=stderr_present,
            detail="setup_report_unparseable",
        )
        raise SystemExit(f"{label} did not write a parseable setup report")
    if report.get("setup") == "failed":
        emit_failure_diagnostics(
            phase=label,
            return_code=0,
            work=work,
            diagnostics_root=diagnostics_root,
            report_path=report_path,
            stdout_present=stdout_present,
            stderr_present=stderr_present,
            detail="setup_report_failed",
        )
        raise SystemExit(f"{label} reported setup failure")
    return report


def main() -> int:
    system = os.environ.get("ATC_SMOKE_PLATFORM") or platform.system()
    executable = artifact_executable(system)
    if not executable.is_file():
        raise SystemExit(f"desktop artifact is missing: {executable}")

    temp_parent = packaged_smoke_parent()
    temp_parent.mkdir(parents=True, exist_ok=True)
    # Disposable work holds credentials/vault/binaries and is always removed.
    work = Path(tempfile.mkdtemp(prefix="packaged-first-run-", dir=temp_parent))
    # Content-free failure summaries live outside the work tree and never hold secrets.
    diagnostics_root = temp_parent / "packaged-first-run-diagnostics"
    diagnostics_root.mkdir(parents=True, exist_ok=True)
    data_dir = work / "data"
    codex_home = work / "codex"
    report_path = work / "setup-report.json"
    port = available_port()
    environment = dict(os.environ)
    environment.update(
        {
            "ATC_PACKAGED_SMOKE": "1",
            "ATC_CORE_DATA_DIR": str(data_dir),
            "ATC_CORE_PORT": str(port),
            "ATC_CORE_HOST": "127.0.0.1",
            "CODEX_HOME": str(codex_home),
            # Isolate non-secret smoke credentials from the host OS store.
            "PYTHON_KEYRING_BACKEND": ISOLATED_SMOKE_CREDENTIAL_BACKEND,
            # Explicit opt-in only for this disposable smoke; never production default.
            DEVELOPMENT_FALLBACK_ENV: "1",
        }
    )
    if system == "Windows":
        environment["ATC_INSTALL_DIR"] = str(work / "installed")
        environment.update(
            {
                "ATC_SMOKE_PROGRAMS_DIR": str(work / "shell" / "Programs"),
                "ATC_SMOKE_DESKTOP_DIR": str(work / "shell" / "Desktop"),
                "ATC_SMOKE_UNINSTALL_KEY": (
                    f"Software\\AllTheContext\\Smoke\\packaged-{os.getpid()}"
                ),
                "ATC_SMOKE_UPDATE_RUNONCE_KEY": (
                    f"Software\\AllTheContext\\Smoke\\packaged-update-{os.getpid()}"
                ),
                "ATC_SMOKE_STARTUP_WINDOWS_KEY": (
                    f"Software\\AllTheContext\\Smoke\\packaged-startup-{os.getpid()}"
                ),
            }
        )
    elif system == "Darwin":
        environment["ATC_INSTALL_DIR"] = str(work / "Applications" / "All The Context.app")
        environment["ATC_SMOKE_LAUNCH_AGENTS_DIR"] = str(work / "LaunchAgents")
    else:
        environment["XDG_CONFIG_HOME"] = str(work / "config")

    base_url = f"http://127.0.0.1:{port}"
    cleanup_admin_token = ""

    def fail_smoke(phase: str, error_code: str, *, return_code: int | None = None) -> None:
        """Record content-free diagnostics, then exit. Work tree is always cleaned."""

        emit_failure_diagnostics(
            phase=phase,
            return_code=return_code,
            work=work,
            diagnostics_root=diagnostics_root,
            report_path=report_path if report_path.is_file() else None,
            detail=error_code,
        )
        raise SystemExit(error_code)

    def cleanup_failed_smoke() -> None:
        cleanup_token = cleanup_admin_token or recover_disposable_admin_token(work)
        if cleanup_token:
            with suppress(Exception):
                stop_core(base_url, cleanup_token, data_dir=data_dir)
        if system == "Windows":
            import winreg

            for key_name in (
                environment["ATC_SMOKE_UNINSTALL_KEY"],
                environment["ATC_SMOKE_UPDATE_RUNONCE_KEY"],
                environment["ATC_SMOKE_STARTUP_WINDOWS_KEY"],
            ):
                with suppress(OSError):
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_name)
        cleanup_failed = False
        try:
            scrub_sensitive_work_tree(work)
        except Exception:
            cleanup_failed = True
        try:
            remove_work_tree(work)
        except Exception:
            cleanup_failed = True
        if cleanup_failed:
            print(
                json.dumps(
                    {
                        "packaged_first_run_cleanup": "failed",
                        "content_free": True,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )

    atexit.register(cleanup_failed_smoke)

    report = _run_headless_setup(
        executable=executable,
        report_path=report_path,
        environment=environment,
        extra_args=[
            "--no-claude",
            "--vault-name",
            "Packaged smoke vault",
        ],
        work=work,
        diagnostics_root=diagnostics_root,
        label="headless first-run setup",
    )
    if report.get("core_url") != f"http://127.0.0.1:{port}":
        fail_smoke("validate-setup-report", "unexpected_core_url")
    # Contract: this smoke uses the explicit isolated development store only.
    # Real OS credential acceptance remains a separate packaged gate.
    if report.get("credential_storage") != FALLBACK_CREDENTIAL_STORAGE:
        fail_smoke(
            "validate-credential-storage",
            "unexpected_credential_storage",
        )
    warnings = report.get("warnings") or []
    if not any("insecure development credential file" in str(item).casefold() for item in warnings):
        fail_smoke(
            "validate-credential-warning",
            "credential_warning_missing",
        )
    startup_report = report.get("startup")
    expected_startup = {
        "Windows": "HKCU Run",
        "Darwin": "LaunchAgent",
    }.get(system, "XDG autostart")
    if not isinstance(startup_report, dict) or startup_report.get("mechanism") != expected_startup:
        fail_smoke("validate-startup", "startup_adapter_missing")

    config_path = codex_home / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    managed = config["mcp_servers"]["all_the_context"]
    command = Path(managed["command"])
    arguments = list(managed["args"])
    command_override = os.environ.get("ATC_SMOKE_MCP_COMMAND")
    if command_override:
        command = Path(command_override).expanduser().resolve()
        arguments = []
    client_environment = {str(key): str(value) for key, value in managed["env"].items()}
    core_command = json.loads(client_environment.get("ATC_CORE_COMMAND", "null"))
    if not isinstance(core_command, list) or len(core_command) < 2 or core_command[-1] != "--core":
        raise SystemExit(f"configured Core recovery command is invalid: {core_command}")
    installed_app = Path(str(core_command[0]))
    if not installed_app.is_file():
        raise SystemExit(f"installed desktop app is not stable: {installed_app}")
    packaged_identity: BuildIdentity | None = None
    if system == "Windows":
        packaged_identity = read_packaged_build_identity(
            executable,
            report_path=work / "packaged-build-diagnostics.json",
            environment=environment,
        )
    if system == "Darwin":
        installed_bundles = [
            candidate
            for candidate in installed_app.parents
            if candidate.suffix.casefold() == ".app"
        ]
        if len(installed_bundles) != 1:
            raise SystemExit("installed macOS executable is not inside one stable app bundle")
        seal = subprocess.run(
            ["codesign", "--verify", "--deep", "--strict", str(installed_bundles[0])],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if seal.returncode != 0 and "not signed at all" not in seal.stderr.casefold():
            raise SystemExit("installed macOS app bundle has an invalid structural code seal")
    if not command.is_file():
        raise SystemExit(f"configured MCP command is not stable: {command}")
    token = client_environment.get("ATC_CLIENT_TOKEN", "")
    if not token:
        raise SystemExit("isolated fallback setup did not configure an MCP credential")
    desktop_client_id = str(report.get("client_id", ""))
    credential_path = data_dir / "credentials.development.json"
    credential_map = json.loads(credential_path.read_text(encoding="utf-8"))
    admin_token = str(credential_map.get(f"client:{desktop_client_id}", ""))
    if not admin_token:
        raise SystemExit("isolated setup did not retain its desktop administrator credential")
    cleanup_admin_token = admin_token

    if system == "Windows":
        expected_shortcuts = (
            work / "shell" / "Programs" / "All The Context" / "All The Context.lnk",
            work / "shell" / "Programs" / "All The Context" / "Uninstall All The Context.lnk",
            work / "shell" / "Desktop" / "All The Context.lnk",
        )
        if not all(path.is_file() for path in expected_shortcuts):
            raise SystemExit("isolated Windows launchers were not registered")
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            environment["ATC_SMOKE_UNINSTALL_KEY"],
        ):
            pass
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            environment["ATC_SMOKE_STARTUP_WINDOWS_KEY"],
        ) as startup_key:
            startup_command, _kind = winreg.QueryValueEx(startup_key, "All The Context Core")
        if str(installed_app) not in startup_command or "--core" not in startup_command:
            raise SystemExit("isolated Windows startup command did not use the installed app")
    elif system == "Darwin":
        launch_agent = work / "LaunchAgents" / "com.allthecontext.core.plist"
        with launch_agent.open("rb") as stream:
            launch_payload = plistlib.load(stream)
        if launch_payload.get("ProgramArguments") != [str(installed_app), "--core"]:
            raise SystemExit("isolated LaunchAgent did not use the installed app bundle")
    else:
        startup_entry = work / "config" / "autostart" / "all-the-context.desktop"
        startup_content = startup_entry.read_text(encoding="utf-8")
        if str(installed_app) not in startup_content or "--core" not in startup_content:
            raise SystemExit("isolated XDG startup entry did not use the portable app")

    try:
        dashboard_url = str(report.get("dashboard_url", ""))
        if "atc_token" in dashboard_url or "/v1/browser/connect?ticket=" not in dashboard_url:
            raise SystemExit(f"unsafe or invalid dashboard handoff URL: {dashboard_url}")
        status, content = _read_http_response(dashboard_url)
        if status != 200:
            raise SystemExit(f"browser handoff did not reach dashboard: {status}")
        handoff_html = content.decode("utf-8")
        browser_session = browser_session_from_handoff_html(handoff_html)
        if browser_session is None or admin_token in handoff_html:
            raise SystemExit("browser handoff exposed no safe opaque session")
        _, browser_content = _read_http_response(
            f"http://127.0.0.1:{port}/v1/context/status",
            headers={
                "Authorization": f"Browser {browser_session}",
                "X-ATC-Dashboard": "1",
            },
        )
        if json.loads(browser_content.decode("utf-8")).get("core_online") is not True:
            raise SystemExit("browser session did not authenticate to Core")

        # The MCP principal is deliberately read-only. Use the separately
        # retained disposable desktop administrator for readiness probes;
        # exercise_mcp below proves the MCP credential itself works.
        status = api_request(f"{base_url}/v1/context/status", admin_token)
        if status.get("core_online") is not True:
            raise SystemExit(f"installed Core status was not ready: {status}")
        updates = api_request(f"{base_url}/v1/admin/updates", admin_token)
        expected_automatic = system == "Windows"
        if updates.get("automatic_install_supported") is not expected_automatic:
            raise SystemExit(f"packaged updater capability was incorrect: {updates}")
        if (
            system != "Windows"
            and "manual" not in str(updates.get("installer_detail", "")).casefold()
        ):
            raise SystemExit(f"packaged updater did not explain its manual boundary: {updates}")

        mcp_environment = dict(environment)
        mcp_environment.update(client_environment)
        parameters = StdioServerParameters(
            command=str(command),
            args=arguments,
            env=mcp_environment,
            cwd=str(command.parent),
        )
        mcp_log_path = work / "mcp-stderr.log"
        with mcp_log_path.open("w", encoding="utf-8") as mcp_log:
            anyio.run(exercise_mcp, parameters, mcp_log)
        mcp_stderr = mcp_log_path.read_text(encoding="utf-8", errors="replace")
        if "Traceback" in mcp_stderr:
            raise RuntimeError(f"packaged MCP wrote a traceback to stderr:\n{mcp_stderr}")
    finally:
        stop_core(base_url, admin_token, data_dir=data_dir)

    # The already-configured packaged adapter must recover Core without the
    # user opening the desktop app again.
    restart_log_path = work / "mcp-restart-stderr.log"
    with restart_log_path.open("w", encoding="utf-8") as restart_log:
        anyio.run(exercise_mcp, parameters, restart_log)
    if "Traceback" in restart_log_path.read_text(encoding="utf-8", errors="replace"):
        raise RuntimeError("packaged MCP Core restart wrote a traceback")
    stop_core(base_url, admin_token, data_dir=data_dir)

    # Reopen the stable installed copy and run the idempotent setup/upgrade
    # path; the same vault and desktop authority must survive.
    reopen_report_path = work / "reopen-report.json"
    reopen_report = _run_headless_setup(
        executable=installed_app,
        report_path=reopen_report_path,
        environment=environment,
        extra_args=[
            "--no-startup",
            "--no-claude",
            "--vault-name",
            "Packaged smoke vault",
        ],
        work=work,
        diagnostics_root=diagnostics_root,
        label="installed reopen headless setup",
    )
    if reopen_report.get("vault_id") != report.get("vault_id") or reopen_report.get(
        "client_id"
    ) != report.get("client_id"):
        fail_smoke("validate-reopen", "reopen_authority_changed")
    if reopen_report.get("credential_storage") != FALLBACK_CREDENTIAL_STORAGE:
        fail_smoke(
            "validate-reopen-credentials",
            "reopen_credential_storage_changed",
        )
    if api_request(f"{base_url}/v1/context/status", admin_token).get("core_online") is not True:
        fail_smoke("validate-reopen-core", "reopened_core_not_ready")
    stop_core(base_url, admin_token, data_dir=data_dir)

    packaged_update_result = "not_applicable"
    if system == "Windows":
        assert packaged_identity is not None
        helper_authority = {
            "ATC_CORE_DATA_DIR": environment["ATC_CORE_DATA_DIR"],
            "ATC_INSTALL_DIR": environment["ATC_INSTALL_DIR"],
            DEVELOPMENT_FALLBACK_ENV: "1",
        }
        with _temporary_environment(helper_authority):
            crash_helper, crash_journal = prepare_packaged_update_transaction(
                data_dir=data_dir,
                installed_app=installed_app,
                release_app=executable,
                operation_id="d" * 24,
                core_port=port,
                target_version=__version__,
                packaged_identity=packaged_identity,
            )
        interrupted_environment = dict(environment)
        interrupted_environment["ATC_UPDATE_FAULT_AFTER_PHASE"] = "binary_replaced"
        interrupted = subprocess.run(
            [str(crash_helper), "--journal", str(crash_journal)],
            env=interrupted_environment,
            check=False,
            timeout=180,
        )
        if interrupted.returncode != 86:
            raise SystemExit(
                f"packaged updater did not stop at the injected crash point: "
                f"{packaged_update_failure_diagnostic(crash_journal, interrupted.returncode)}"
            )
        if json.loads(crash_journal.read_text(encoding="utf-8")).get("phase") != (
            "binary_replaced"
        ):
            raise SystemExit("packaged updater did not persist the interrupted cutover")
        subprocess.run(
            [str(crash_helper), "--journal", str(crash_journal)],
            env=environment,
            check=True,
            timeout=180,
        )
        # The helper publishes the committed journal before launching Core.
        # Core may safely retire that terminal evidence during its startup,
        # so validate the publication before waiting for the restarted process.
        if json.loads(crash_journal.read_text(encoding="utf-8")).get("phase") != "committed":
            raise SystemExit("packaged updater did not commit after crash recovery")
        wait_for_core(base_url, admin_token)
        stop_core(base_url, admin_token, data_dir=data_dir)

        with _temporary_environment(helper_authority):
            rollback_helper, rollback_journal = prepare_packaged_update_transaction(
                data_dir=data_dir,
                installed_app=installed_app,
                release_app=executable,
                operation_id="e" * 24,
                core_port=port,
                target_version=__version__,
                packaged_identity=packaged_identity,
            )
        rollback_environment = dict(environment)
        rollback_environment.update(
            {
                "ATC_UPDATE_FORCE_HEALTH_FAILURE": "1",
                "ATC_UPDATE_SMOKE_MUTATE_DB": "1",
            }
        )
        run_packaged_rollback_smoke(
            helper=rollback_helper,
            journal=rollback_journal,
            environment=rollback_environment,
        )
        rollback_status = json.loads(rollback_journal.read_text(encoding="utf-8"))
        if rollback_status.get("phase") != "rolled_back":
            raise SystemExit(f"packaged updater did not roll back: {rollback_status}")
        # As with the committed path above, Core may retire the fully
        # helper-confirmed terminal journal while it starts.
        wait_for_core(base_url, admin_token)
        restored_files = (
            (
                Path(str(rollback_status["application_path"])),
                str(rollback_status["rollback_application_sha256"]),
                int(rollback_status["rollback_application_size"]),
            ),
            (
                Path(str(rollback_status["mcp_path"])),
                str(rollback_status["rollback_mcp_sha256"]),
                int(rollback_status["rollback_mcp_size"]),
            ),
            (
                Path(str(rollback_status["recovery_path"])),
                str(rollback_status["rollback_recovery_sha256"]),
                int(rollback_status["rollback_recovery_size"]),
            ),
            (
                Path(str(rollback_status["stable_update_helper_path"])),
                str(rollback_status["rollback_update_helper_sha256"]),
                int(rollback_status["rollback_update_helper_size"]),
            ),
        )
        if any(sha256_file(path) != (digest, size) for path, digest, size in restored_files):
            raise SystemExit("packaged updater did not restore every installed binary")
        connection = sqlite3.connect(data_dir / "core.sqlite3")
        try:
            leaked_migration = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='packaged_update_smoke'"
            ).fetchone()
            if leaked_migration is not None:
                raise SystemExit("packaged updater did not restore the pre-update database")
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise SystemExit("packaged updater rollback database is not valid")
        finally:
            connection.close()
        stop_core(base_url, admin_token, data_dir=data_dir)
        packaged_update_result = "passed"

    uninstall_result = "not_applicable"
    if system == "Windows":
        uninstall_report_path = work / "uninstall-report.json"
        subprocess.run(
            [
                str(installed_app),
                "--packaged-smoke-uninstall",
                str(uninstall_report_path),
            ],
            # Match the WorkingDirectory used by the real Start Menu
            # uninstall shortcut. The detached cleanup helper must move out
            # of this directory before trying to remove it.
            cwd=installed_app.parent,
            env=environment,
            check=True,
            timeout=90,
        )
        uninstall_report = json.loads(uninstall_report_path.read_text(encoding="utf-8"))
        if uninstall_report != {"uninstalled": True, "vault_preserved": True}:
            raise SystemExit(f"unexpected packaged uninstall report: {uninstall_report}")

        install_dir = Path(environment["ATC_INSTALL_DIR"])
        # The product's detached helper may spend its full bounded retry window
        # waiting for the one-file bootloader and antivirus/indexer handles.
        # Observe beyond that contract before declaring retained app files.
        delete_deadline = time.monotonic() + WINDOWS_INSTALL_REMOVAL_OBSERVATION_SECONDS
        while install_dir.exists() and time.monotonic() < delete_deadline:
            time.sleep(0.1)
        if install_dir.exists():
            raise SystemExit(f"packaged uninstaller left application files: {install_dir}")
        if not (data_dir / "core.sqlite3").is_file():
            raise SystemExit("packaged uninstaller removed the retained local vault")
        if any(path.exists() for path in expected_shortcuts):
            raise SystemExit("packaged uninstaller left isolated Windows shortcuts")

        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                environment["ATC_SMOKE_UNINSTALL_KEY"],
            ):
                pass
        except FileNotFoundError:
            pass
        else:
            raise SystemExit("packaged uninstaller left its Apps & Features registration")
        with suppress(FileNotFoundError):
            winreg.DeleteKey(
                winreg.HKEY_CURRENT_USER,
                environment["ATC_SMOKE_UPDATE_RUNONCE_KEY"],
            )
        # Product uninstall removes the startup value only. The run-owned smoke
        # override key must also disappear on the successful path so independent
        # cleanup finds zero orphan startup/RunOnce smoke state.
        try:
            from allthecontext.user_startup import remove_smoke_windows_startup_key

            remove_smoke_windows_startup_key(
                windows_key=environment["ATC_SMOKE_STARTUP_WINDOWS_KEY"]
            )
        except OSError:
            raise SystemExit("smoke_startup_key_remove_failed") from None
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                environment["ATC_SMOKE_STARTUP_WINDOWS_KEY"],
            ):
                pass
        except FileNotFoundError:
            pass
        else:
            raise SystemExit("smoke_startup_key_orphan_remaining")
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                environment["ATC_SMOKE_UPDATE_RUNONCE_KEY"],
            ):
                pass
        except FileNotFoundError:
            pass
        else:
            raise SystemExit("smoke_update_runonce_key_orphan_remaining")

        cleaned_config = config_path.read_text(encoding="utf-8")
        if "all_the_context" in cleaned_config or token in cleaned_config:
            raise SystemExit("packaged uninstaller left the Codex MCP connection")
        cleaned_credentials = json.loads(credential_path.read_text(encoding="utf-8"))
        client_id = client_environment["ATC_CLIENT_ID"]
        if f"client:{client_id}" in cleaned_credentials:
            raise SystemExit("packaged uninstaller left the Codex credential")
        connection = sqlite3.connect(data_dir / "core.sqlite3")
        try:
            revoked = connection.execute(
                "SELECT revoked_at FROM client_registrations WHERE id=?",
                (client_id,),
            ).fetchone()
        finally:
            # sqlite3.Connection's context manager commits or rolls back but
            # does not close.  An open connection prevents deletion on
            # Windows and would make the smoke mistake its own handle for an
            # application shutdown leak.
            connection.close()
        if revoked is None or revoked[0] is None:
            raise SystemExit("packaged uninstaller did not revoke the Codex principal")
        uninstall_result = "passed"

    try:
        scrub_sensitive_work_tree(work)
        remove_work_tree(work)
    except (OSError, RuntimeError):
        raise SystemExit("packaged_first_run_cleanup_failed") from None
    atexit.unregister(cleanup_failed_smoke)
    print(
        json.dumps(
            {
                "setup": "passed",
                "credential_storage": FALLBACK_CREDENTIAL_STORAGE,
                "credential_mode": "explicit-isolated-development-file",
                "os_credential_acceptance": "not_this_smoke",
                "browser_handoff": "passed",
                "stable_mcp_command": True,
                "mcp_handshake": "passed",
                "mcp_core_restart": "passed",
                "installed_reopen": "passed",
                "per_user_startup": "passed",
                "ota_automatic_install": system == "Windows",
                "ota_transaction_recovery": packaged_update_result,
                "core_shutdown": "passed",
                "packaged_uninstall": uninstall_result,
                "temporary_data_removed": True,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
