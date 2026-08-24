"""Shared Core/CLI composition for foreground local-workspace capture.

CoreService and the contributor CLI construct ``CaptureCoordinator`` only
through this module so they cannot diverge. The registered-source sink is
always injected. The local Git workspace adapter is registered only when a
valid machine-local authorization sidecar exists under Core's data directory.

This slice is manual, opt-in, and foreground-only for adapter authorization.
Core-owned Packet E scheduling is a separate explicit opt-in that reuses this
composition and the existing coordinator; it does not fork sink or adapter
logic or change file-discovery caps.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from .capture import CaptureCoordinator, CaptureError, CaptureSource
from .config import CoreConfig
from .experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
    LocalGitWorkspaceCaptureProviderAdapter,
)
from .ids import utc_now
from .memory_policy import REGISTERED_SOURCE_CODE_OWNED_SCOPES
from .registered_source_admission import RegisteredSourceCaptureApplicationSink
from .storage import CoreStore

AUTHORIZATION_FILENAME = "local-workspace-authorization.json"
AUTHORIZATION_VERSION = 1
SCHEDULER_CONFIG_FILENAME = "capture-scheduler.json"
SCHEDULER_CONFIG_VERSION = 1
LOCAL_WORKSPACE_ACCOUNT_LABEL = "local-workspace"
MAX_AUTHORIZATION_SIDECAR_BYTES = 16_384
MAX_SCHEDULER_SIDECAR_BYTES = MAX_AUTHORIZATION_SIDECAR_BYTES
_SOURCE_PAGE_SIZE = 500
_MAX_SOURCE_INVENTORY = 10_000
_AUTHORIZATION_LOCK_TIMEOUT_SECONDS = 5.0
_MAX_PATH_ANCESTORS = 4096
_PLAIN_PATH_TYPE = type(Path())
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_LOCAL_DRIVE_TYPES = frozenset({2, 3, 6})  # removable, fixed, ramdisk
_WORKSPACE_IDENTITY = re.compile(r"^workspace-source-[0-9a-f]{64}$")
_SIDECAR_KEYS = frozenset({"version", "source_identity", "canonical_root"})
_SCHEDULER_SIDECAR_KEYS = frozenset({"version", "enabled"})
_ACCEPTABLE_WORKSPACE_LIFECYCLES = frozenset(
    {"disabled", "enabled", "paused", "degraded", "reconciling"}
)


@dataclass(frozen=True, slots=True)
class CaptureSchedulerDurableState:
    """Content-free machine-local scheduler sidecar projection."""

    present: bool
    valid: bool
    enabled: bool


def _fail_closed() -> NoReturn:
    """Raise a bounded authorization error without path or raw context."""

    raise CaptureError("capture_authorization_unavailable") from None


def _fail_identity() -> NoReturn:
    """Refuse a second or retargeted workspace identity without leaking paths."""

    raise CaptureError("capture_capability_invalid")


def _is_reparse_or_symlink(value: os.stat_result | Any) -> bool:
    mode = getattr(value, "st_mode", 0)
    attributes = int(getattr(value, "st_file_attributes", 0))
    return stat.S_ISLNK(mode) or bool(attributes & _REPARSE_POINT)


def _implicit_home_text(value: str) -> bool:
    if value.startswith("~"):
        return True
    return value[:2] in {"/~", "\\~"}


def _unwrap_windows_extended_local_drive(value: str) -> str:
    """Unwrap only ``\\\\?\\X:\\...`` / ``//?/X:/...`` to the ordinary drive form."""

    if os.name != "nt" or not isinstance(value, str):
        return value
    if not value.startswith(("\\\\?\\", "//?/")):
        return value
    remainder = value[4:]
    if len(remainder) < 3 or remainder[1] != ":" or not remainder[0].isalpha():
        return value
    if remainder[2] not in "\\/":
        return value
    return remainder


def _normalized_local_root_text(value: str) -> str | None:
    if not value or "\x00" in value:
        return None
    candidate = _unwrap_windows_extended_local_drive(value)
    if _implicit_home_text(candidate):
        return None
    if len(candidate) >= 2 and candidate[0] in "\\/" and candidate[1] in "\\/":
        return None
    return candidate


def _windows_drive_type(root: Path) -> int | None:
    """Return the Windows GetDriveType code, or None when classification fails."""

    import ctypes

    drive, _rest = os.path.splitdrive(_unwrap_windows_extended_local_drive(os.fspath(root)))
    if len(drive) != 2 or drive[1] != ":":
        return None
    windll = getattr(ctypes, "windll", None)
    kernel32 = getattr(windll, "kernel32", None)
    getter = getattr(kernel32, "GetDriveTypeW", None)
    if getter is None:
        return None
    try:
        getter.argtypes = [ctypes.c_wchar_p]
        getter.restype = ctypes.c_uint
        return int(getter(f"{drive}\\"))
    except (OSError, RuntimeError, TypeError, ValueError):
        return None


def _reject_non_local_volume(root: Path) -> None:
    if os.name != "nt":
        return
    if _windows_drive_type(root) not in _WINDOWS_LOCAL_DRIVE_TYPES:
        _fail_closed()


def _reject_redirecting_ancestors(root: Path) -> None:
    current = root
    for _ in range(_MAX_PATH_ANCESTORS):
        try:
            current_stat = current.lstat()
        except (OSError, RuntimeError):
            current_stat = None
        if current_stat is None:
            _fail_closed()
        if not stat.S_ISDIR(current_stat.st_mode) or _is_reparse_or_symlink(current_stat):
            _fail_closed()
        parent = current.parent
        if parent == current:
            return
        current = parent
    _fail_closed()


def canonical_workspace_root(root: Path) -> Path:
    """Return one explicit, canonical, non-redirecting local directory root."""

    if type(root) is not _PLAIN_PATH_TYPE:
        _fail_closed()
    raw = os.fspath(root)
    if not isinstance(raw, str):
        _fail_closed()
    normalized = _normalized_local_root_text(raw)
    if normalized is None:
        _fail_closed()
    if normalized != raw:
        root = Path(normalized)
        if type(root) is not _PLAIN_PATH_TYPE:
            _fail_closed()
    if not root.is_absolute():
        _fail_closed()
    _reject_non_local_volume(root)
    _reject_redirecting_ancestors(root)
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        resolved = None
    if resolved is None:
        _fail_closed()
    if type(resolved) is not _PLAIN_PATH_TYPE:
        _fail_closed()
    resolved_raw = os.fspath(resolved)
    if not isinstance(resolved_raw, str) or _normalized_local_root_text(resolved_raw) is None:
        _fail_closed()
    _reject_non_local_volume(resolved)
    _reject_redirecting_ancestors(resolved)
    try:
        root_stat = root.lstat()
        resolved_stat = resolved.lstat()
    except (OSError, RuntimeError):
        _fail_closed()
    if not os.path.samestat(root_stat, resolved_stat):
        _fail_closed()
    if (
        not stat.S_ISDIR(root_stat.st_mode)
        or not stat.S_ISDIR(resolved_stat.st_mode)
        or _is_reparse_or_symlink(root_stat)
        or _is_reparse_or_symlink(resolved_stat)
    ):
        _fail_closed()
    return resolved


def authorization_path(data_dir: Path) -> Path:
    return data_dir / AUTHORIZATION_FILENAME


def authorization_lock_path(data_dir: Path) -> Path:
    path = authorization_path(data_dir)
    return path.with_suffix(path.suffix + ".lock")


def scheduler_config_path(data_dir: Path) -> Path:
    return data_dir / SCHEDULER_CONFIG_FILENAME


def scheduler_config_lock_path(data_dir: Path) -> Path:
    path = scheduler_config_path(data_dir)
    return path.with_suffix(path.suffix + ".lock")


def _authorization_lock(data_dir: Path, *, timeout: float) -> FileLock:
    return FileLock(str(authorization_lock_path(data_dir)), timeout=timeout)


def _sidecar_open_flags() -> int:
    flags = os.O_RDONLY
    flags |= int(getattr(os, "O_CLOEXEC", 0))
    flags |= int(getattr(os, "O_NOINHERIT", 0))
    flags |= int(getattr(os, "O_NONBLOCK", 0))
    flags |= int(getattr(os, "O_NOFOLLOW", 0))
    flags |= int(getattr(os, "O_BINARY", 0))
    return flags


def _read_fd_bounded(fd: int, maximum: int) -> bytes | None:
    chunks: list[bytes] = []
    remaining = maximum
    while remaining > 0:
        try:
            chunk = os.read(fd, remaining)
        except (BlockingIOError, OSError, RuntimeError):
            return None
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_sidecar_bytes(
    path: Path,
    *,
    maximum: int = MAX_AUTHORIZATION_SIDECAR_BYTES,
) -> bytes | None:
    flags = _sidecar_open_flags()
    fd: int | None = None
    try:
        fd = os.open(os.fspath(path), flags)
        with suppress(OSError, RuntimeError):
            os.set_inheritable(fd, False)
        try:
            opened_stat = os.fstat(fd)
        except (OSError, RuntimeError):
            return None
        if not stat.S_ISREG(opened_stat.st_mode) or _is_reparse_or_symlink(opened_stat):
            return None
        size = int(opened_stat.st_size)
        if size <= 0 or size > maximum:
            return None
        payload = _read_fd_bounded(fd, maximum + 1)
        if payload is None or not payload or len(payload) > maximum:
            return None
        if len(payload) != size:
            return None
        try:
            path_stat = os.lstat(os.fspath(path))
        except (OSError, RuntimeError):
            return None
        if not os.path.samestat(opened_stat, path_stat):
            return None
        if not stat.S_ISREG(path_stat.st_mode) or _is_reparse_or_symlink(path_stat):
            return None
        return payload
    except (OSError, RuntimeError):
        return None
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)


def _read_sidecar_document(data_dir: Path) -> dict[str, str] | None:
    payload = _read_sidecar_bytes(authorization_path(data_dir))
    if payload is None:
        return None
    try:
        loaded = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(loaded, dict) or set(loaded) != _SIDECAR_KEYS:
        return None
    version = loaded.get("version")
    identity = loaded.get("source_identity")
    canonical_root = loaded.get("canonical_root")
    if (
        type(version) is not int
        or version != AUTHORIZATION_VERSION
        or not isinstance(identity, str)
        or _WORKSPACE_IDENTITY.fullmatch(identity) is None
        or not isinstance(canonical_root, str)
    ):
        return None
    normalized_root = _normalized_local_root_text(canonical_root)
    if normalized_root is None:
        return None
    return {"source_identity": identity, "canonical_root": normalized_root}


def _replace_text_sidecar(path: Path, payload: str, *, maximum: int) -> None:
    try:
        existing_stat = path.lstat()
    except FileNotFoundError:
        existing_stat = None
    except OSError:
        raise CaptureError("capture_failed") from None
    if existing_stat is not None and (
        not stat.S_ISREG(existing_stat.st_mode) or _is_reparse_or_symlink(existing_stat)
    ):
        raise CaptureError("capture_failed") from None
    encoded = payload.encode("utf-8")
    if len(encoded) > maximum:
        raise CaptureError("capture_failed") from None
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
    try:
        temporary.write_text(payload, encoding="utf-8", newline="\n")
        temporary.replace(path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            raise CaptureError("capture_failed") from None
        raise CaptureError("capture_failed") from None
    try:
        temporary.unlink(missing_ok=True)
    except OSError:
        raise CaptureError("capture_failed") from None


def _write_sidecar_unlocked(data_dir: Path, *, canonical_root: Path, source_identity: str) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise CaptureError("capture_failed") from None
    payload = (
        json.dumps(
            {
                "canonical_root": os.fspath(canonical_root),
                "source_identity": source_identity,
                "version": AUTHORIZATION_VERSION,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    _replace_text_sidecar(
        authorization_path(data_dir),
        payload,
        maximum=MAX_AUTHORIZATION_SIDECAR_BYTES,
    )


def _missing_or_unreadable_scheduler_state(path: Path) -> CaptureSchedulerDurableState:
    try:
        path.lstat()
    except FileNotFoundError:
        return CaptureSchedulerDurableState(present=False, valid=True, enabled=False)
    except OSError:
        return CaptureSchedulerDurableState(present=True, valid=False, enabled=False)
    return CaptureSchedulerDurableState(present=True, valid=False, enabled=False)


def read_scheduler_durable_state(data_dir: Path) -> CaptureSchedulerDurableState:
    """Read the scheduler sidecar fail-closed without mutating scheduling state."""

    path = scheduler_config_path(data_dir)
    payload = _read_sidecar_bytes(path, maximum=MAX_SCHEDULER_SIDECAR_BYTES)
    if payload is None:
        return _missing_or_unreadable_scheduler_state(path)
    try:
        loaded = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        return CaptureSchedulerDurableState(present=True, valid=False, enabled=False)
    if not isinstance(loaded, dict) or set(loaded) != _SCHEDULER_SIDECAR_KEYS:
        return CaptureSchedulerDurableState(present=True, valid=False, enabled=False)
    version = loaded.get("version")
    enabled = loaded.get("enabled")
    if type(version) is not int or version != SCHEDULER_CONFIG_VERSION or type(enabled) is not bool:
        return CaptureSchedulerDurableState(present=True, valid=False, enabled=False)
    return CaptureSchedulerDurableState(present=True, valid=True, enabled=enabled)


def _write_scheduler_sidecar_unlocked(data_dir: Path, *, enabled: bool) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise CaptureError("capture_failed") from None
    payload = (
        json.dumps(
            {"enabled": enabled, "version": SCHEDULER_CONFIG_VERSION},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    _replace_text_sidecar(
        scheduler_config_path(data_dir),
        payload,
        maximum=MAX_SCHEDULER_SIDECAR_BYTES,
    )


def _scheduler_config_lock(data_dir: Path, *, timeout: float) -> FileLock:
    return FileLock(str(scheduler_config_lock_path(data_dir)), timeout=timeout)


def write_scheduler_enabled(data_dir: Path, *, enabled: bool) -> CaptureSchedulerDurableState:
    """Atomically persist the content-free scheduler enablement sidecar."""

    if type(enabled) is not bool:
        raise CaptureError("capture_failed") from None
    try:
        with _scheduler_config_lock(data_dir, timeout=_AUTHORIZATION_LOCK_TIMEOUT_SECONDS):
            _write_scheduler_sidecar_unlocked(data_dir, enabled=enabled)
    except FileLockTimeout:
        raise CaptureError("capture_failed") from None
    except OSError:
        raise CaptureError("capture_failed") from None
    return read_scheduler_durable_state(data_dir)


def _build_adapter(root: Path) -> LocalGitWorkspaceCaptureProviderAdapter:
    try:
        adapter = LocalGitWorkspaceCaptureProviderAdapter((root,))
    except (TypeError, ValueError, OSError, RuntimeError):
        adapter = None
    if adapter is None:
        _fail_closed()
    if _WORKSPACE_IDENTITY.fullmatch(adapter.source_identity) is None:
        _fail_closed()
    return adapter


def _complete_source_inventory(coordinator: CaptureCoordinator) -> list[CaptureSource] | None:
    collected: list[CaptureSource] = []
    seen: set[str] = set()
    offset = 0
    reported_total: int | None = None
    try:
        while True:
            page, total = coordinator.list_sources(limit=_SOURCE_PAGE_SIZE, offset=offset)
            if reported_total is None:
                reported_total = total
                if reported_total > _MAX_SOURCE_INVENTORY:
                    return None
            if total != reported_total or total > _MAX_SOURCE_INVENTORY:
                return None
            if not page:
                break
            for source in page:
                if source.id in seen:
                    continue
                seen.add(source.id)
                collected.append(source)
            offset += len(page)
            if len(page) < _SOURCE_PAGE_SIZE or offset >= total:
                break
            if offset > _MAX_SOURCE_INVENTORY:
                return None
    except (CaptureError, json.JSONDecodeError, TypeError, ValueError, KeyError, UnicodeError):
        return None
    if reported_total is None or len(collected) != reported_total:
        return None
    return collected


def _workspace_source_rows(coordinator: CaptureCoordinator) -> list[CaptureSource] | None:
    inventory = _complete_source_inventory(coordinator)
    if inventory is None:
        return None
    return [source for source in inventory if source.provider == LOCAL_GIT_WORKSPACE_PROVIDER]


def _canonical_workspace_source(source: CaptureSource, identity: str) -> bool:
    return (
        source.provider == LOCAL_GIT_WORKSPACE_PROVIDER
        and source.account_label == LOCAL_WORKSPACE_ACCOUNT_LABEL
        and source.account_fingerprint == identity
        and source.requested_scopes == REGISTERED_SOURCE_CODE_OWNED_SCOPES
        and source.local_only is True
        and source.local_only_acknowledged is True
        and source.lifecycle_state in _ACCEPTABLE_WORKSPACE_LIFECYCLES
    )


def _public_authorization(source: CaptureSource, *, reconciled: bool) -> dict[str, Any]:
    return {
        "account_fingerprint": source.account_fingerprint,
        "account_label": source.account_label,
        "authorized": True,
        "id": source.id,
        "lifecycle_state": source.lifecycle_state,
        "local_only": source.local_only,
        "local_only_acknowledged": source.local_only_acknowledged,
        "provider": source.provider,
        "reconciled": reconciled,
        "requested_scopes": list(source.requested_scopes),
    }


def _new_coordinator(
    store: CoreStore,
    *,
    clock: Callable[[], str] = utc_now,
) -> CaptureCoordinator:
    return CaptureCoordinator(
        store,
        sink=RegisteredSourceCaptureApplicationSink(store, clock=clock),
        clock=clock,
    )


def _register_authorized_adapter_unlocked(
    coordinator: CaptureCoordinator,
    config: CoreConfig,
) -> None:
    document = _read_sidecar_document(config.data_dir)
    if document is None:
        return
    try:
        canonical = canonical_workspace_root(Path(document["canonical_root"]))
        adapter = LocalGitWorkspaceCaptureProviderAdapter((canonical,))
    except (CaptureError, TypeError, ValueError, OSError, RuntimeError):
        return
    if adapter.source_identity != document["source_identity"]:
        return
    workspace_sources = _workspace_source_rows(coordinator)
    if workspace_sources is None:
        return
    matching = [
        source
        for source in workspace_sources
        if _canonical_workspace_source(source, adapter.source_identity)
    ]
    if len(workspace_sources) != 1 or len(matching) != 1:
        return
    coordinator.register_adapter(LOCAL_GIT_WORKSPACE_PROVIDER, adapter)


def _try_register_authorized_adapter(
    coordinator: CaptureCoordinator,
    config: CoreConfig,
) -> None:
    try:
        with _authorization_lock(config.data_dir, timeout=0):
            _register_authorized_adapter_unlocked(coordinator, config)
    except FileLockTimeout:
        return
    except OSError:
        return


def compose_capture_coordinator(
    store: CoreStore,
    config: CoreConfig,
    *,
    clock: Callable[[], str] = utc_now,
) -> CaptureCoordinator:
    """Construct the shared foreground capture coordinator.

    The registered-source sink is always present. The local-workspace adapter is
    registered only for a valid machine-local authorization whose matching source
    metadata is complete and canonical. Sidecar, lock, inventory, or root
    failures stay closed here and do not prevent Core from starting.
    """

    coordinator = _new_coordinator(store, clock=clock)
    _try_register_authorized_adapter(coordinator, config)
    return coordinator


def refresh_local_workspace_adapter(
    coordinator: CaptureCoordinator,
    config: CoreConfig,
) -> None:
    """Revalidate the local-workspace adapter on a long-lived coordinator.

    The stale adapter is removed first. A nonblocking lock then revalidates the
    sidecar, canonical root, and complete inventory before registration. Failure
    leaves the adapter unavailable. CLI composition is unchanged: each CLI
    command still constructs a fresh coordinator.
    """

    coordinator.adapters.pop(LOCAL_GIT_WORKSPACE_PROVIDER, None)
    _try_register_authorized_adapter(coordinator, config)


def reject_reserved_workspace_provider(provider: str) -> None:
    """Refuse generic public creation of the reserved local-workspace provider.

    Comparison uses the same Unicode ``str.strip`` normalization as
    ``CaptureLedger.create_source`` so leading, trailing, and tab whitespace
    cannot bypass the reserved-provider gate. The internal coordinator seam
    stays provider-neutral.
    """

    if isinstance(provider, str) and provider.strip() == LOCAL_GIT_WORKSPACE_PROVIDER:
        raise CaptureError("capture_authorize_workspace_required")


def _authorize_unlocked(
    store: CoreStore,
    config: CoreConfig,
    root: Path,
) -> dict[str, Any]:
    canonical = canonical_workspace_root(root)
    adapter = _build_adapter(canonical)
    identity = adapter.source_identity
    existing = _read_sidecar_document(config.data_dir)
    if existing is not None and existing["source_identity"] != identity:
        _fail_identity()
    coordinator = _new_coordinator(store)
    workspace_sources = _workspace_source_rows(coordinator)
    if workspace_sources is None:
        raise CaptureError("capture_failed") from None
    matching = [
        source for source in workspace_sources if _canonical_workspace_source(source, identity)
    ]
    if len(workspace_sources) != len(matching):
        _fail_identity()
    if len(matching) > 1:
        raise CaptureError("capture_failed")
    if matching:
        source = matching[0]
        reconciled = True
    else:
        source = coordinator.create_source(
            provider=LOCAL_GIT_WORKSPACE_PROVIDER,
            account_label=LOCAL_WORKSPACE_ACCOUNT_LABEL,
            account_fingerprint=identity,
            requested_scopes=REGISTERED_SOURCE_CODE_OWNED_SCOPES,
            local_only_acknowledged=True,
        )
        reconciled = False
    _write_sidecar_unlocked(
        config.data_dir,
        canonical_root=canonical,
        source_identity=identity,
    )
    return _public_authorization(source, reconciled=reconciled)


def authorize_local_workspace(
    store: CoreStore,
    config: CoreConfig,
    root: Path,
    *,
    local_only_acknowledged: bool,
) -> dict[str, Any]:
    """Authorize exactly one canonical workspace root and reconcile its source."""

    if not local_only_acknowledged:
        raise CaptureError("capture_local_only_required")
    try:
        config.prepare()
    except OSError:
        raise CaptureError("capture_failed") from None
    canonical_workspace_root(root)
    try:
        with _authorization_lock(
            config.data_dir,
            timeout=_AUTHORIZATION_LOCK_TIMEOUT_SECONDS,
        ):
            return _authorize_unlocked(store, config, root)
    except FileLockTimeout:
        raise CaptureError("capture_failed") from None
    except OSError:
        raise CaptureError("capture_failed") from None


__all__ = [
    "AUTHORIZATION_FILENAME",
    "AUTHORIZATION_VERSION",
    "LOCAL_WORKSPACE_ACCOUNT_LABEL",
    "MAX_AUTHORIZATION_SIDECAR_BYTES",
    "MAX_SCHEDULER_SIDECAR_BYTES",
    "SCHEDULER_CONFIG_FILENAME",
    "SCHEDULER_CONFIG_VERSION",
    "CaptureSchedulerDurableState",
    "authorization_lock_path",
    "authorization_path",
    "authorize_local_workspace",
    "canonical_workspace_root",
    "compose_capture_coordinator",
    "read_scheduler_durable_state",
    "refresh_local_workspace_adapter",
    "reject_reserved_workspace_provider",
    "scheduler_config_lock_path",
    "scheduler_config_path",
    "write_scheduler_enabled",
]
