"""Shared Core/CLI composition for foreground local-workspace capture.

CoreService and the contributor CLI construct ``CaptureCoordinator`` only
through this module so they cannot diverge. The registered-source sink is
always injected. The local Git workspace adapter is registered only when a
valid machine-local authorization sidecar exists under Core's data directory.

This slice is manual, opt-in, and foreground-only. It does not start a
scheduler, persist scheduler state, or change file-discovery caps.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from collections.abc import Callable
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
LOCAL_WORKSPACE_ACCOUNT_LABEL = "local-workspace"
MAX_AUTHORIZATION_SIDECAR_BYTES = 16_384
_SOURCE_PAGE_SIZE = 500
_MAX_SOURCE_INVENTORY = 10_000
_AUTHORIZATION_LOCK_TIMEOUT_SECONDS = 5.0
_MAX_PATH_ANCESTORS = 4096
_PLAIN_PATH_TYPE = type(Path())
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_LOCAL_DRIVE_TYPES = frozenset({2, 3, 6})  # removable, fixed, ramdisk
_WORKSPACE_IDENTITY = re.compile(r"^workspace-source-[0-9a-f]{64}$")
_SIDECAR_KEYS = frozenset({"version", "source_identity", "canonical_root"})
_ACCEPTABLE_WORKSPACE_LIFECYCLES = frozenset(
    {"disabled", "enabled", "paused", "degraded", "reconciling"}
)


def _fail_closed() -> NoReturn:
    """Raise a bounded authorization error without path or raw context."""

    raise CaptureError("capture_authorization_unavailable")


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


def _non_local_root_text(value: str) -> bool:
    if not value or "\x00" in value or _implicit_home_text(value):
        return True
    return len(value) >= 2 and value[0] in "\\/" and value[1] in "\\/"


def _windows_drive_type(root: Path) -> int | None:
    """Return the Windows GetDriveType code, or None when classification fails."""

    import ctypes

    drive, _rest = os.path.splitdrive(os.fspath(root))
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
    if not isinstance(raw, str) or _non_local_root_text(raw):
        _fail_closed()
    if not root.is_absolute():
        _fail_closed()
    _reject_non_local_volume(root)
    _reject_redirecting_ancestors(root)
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail_closed()
    if type(resolved) is not _PLAIN_PATH_TYPE:
        _fail_closed()
    resolved_raw = os.fspath(resolved)
    if not isinstance(resolved_raw, str) or _non_local_root_text(resolved_raw):
        _fail_closed()
    _reject_non_local_volume(resolved)
    _reject_redirecting_ancestors(resolved)
    try:
        resolved_stat = resolved.lstat()
        same_directory = root.samefile(resolved)
    except (OSError, RuntimeError):
        _fail_closed()
    if (
        not same_directory
        or not stat.S_ISDIR(resolved_stat.st_mode)
        or _is_reparse_or_symlink(resolved_stat)
    ):
        _fail_closed()
    return resolved


def authorization_path(data_dir: Path) -> Path:
    return data_dir / AUTHORIZATION_FILENAME


def authorization_lock_path(data_dir: Path) -> Path:
    path = authorization_path(data_dir)
    return path.with_suffix(path.suffix + ".lock")


def _authorization_lock(data_dir: Path, *, timeout: float) -> FileLock:
    return FileLock(str(authorization_lock_path(data_dir)), timeout=timeout)


def _read_sidecar_document(data_dir: Path) -> dict[str, str] | None:
    path = authorization_path(data_dir)
    try:
        sidecar_stat = path.lstat()
    except FileNotFoundError:
        return None
    except (OSError, RuntimeError):
        return None
    if not stat.S_ISREG(sidecar_stat.st_mode) or _is_reparse_or_symlink(sidecar_stat):
        return None
    if sidecar_stat.st_size <= 0 or sidecar_stat.st_size > MAX_AUTHORIZATION_SIDECAR_BYTES:
        return None
    try:
        payload = path.read_bytes()
    except (OSError, RuntimeError):
        return None
    if len(payload) > MAX_AUTHORIZATION_SIDECAR_BYTES:
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
        or _non_local_root_text(canonical_root)
    ):
        return None
    return {"source_identity": identity, "canonical_root": canonical_root}


def _write_sidecar_unlocked(data_dir: Path, *, canonical_root: Path, source_identity: str) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        raise CaptureError("capture_failed") from None
    path = authorization_path(data_dir)
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
    if len(payload.encode("utf-8")) > MAX_AUTHORIZATION_SIDECAR_BYTES:
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


def _build_adapter(root: Path) -> LocalGitWorkspaceCaptureProviderAdapter:
    try:
        adapter = LocalGitWorkspaceCaptureProviderAdapter((root,))
    except (TypeError, ValueError, OSError, RuntimeError):
        _fail_closed()
    if _WORKSPACE_IDENTITY.fullmatch(adapter.source_identity) is None:
        _fail_closed()
    return adapter


def _complete_source_inventory(coordinator: CaptureCoordinator) -> list[CaptureSource] | None:
    collected: list[CaptureSource] = []
    seen: set[str] = set()
    offset = 0
    reported_total: int | None = None
    while True:
        try:
            page, total = coordinator.list_sources(limit=_SOURCE_PAGE_SIZE, offset=offset)
        except CaptureError:
            return None
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
    try:
        with _authorization_lock(config.data_dir, timeout=0):
            _register_authorized_adapter_unlocked(coordinator, config)
    except FileLockTimeout:
        return coordinator
    except OSError:
        return coordinator
    return coordinator


def reject_reserved_workspace_provider(provider: str) -> None:
    """Refuse generic public creation of the reserved local-workspace provider."""

    if provider == LOCAL_GIT_WORKSPACE_PROVIDER:
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
        raise CaptureError("capture_failed")
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
    "authorization_lock_path",
    "authorization_path",
    "authorize_local_workspace",
    "canonical_workspace_root",
    "compose_capture_coordinator",
    "reject_reserved_workspace_provider",
]
