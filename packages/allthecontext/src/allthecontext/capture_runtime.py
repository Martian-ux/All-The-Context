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

_PLAIN_PATH_TYPE = type(Path())
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WORKSPACE_IDENTITY = re.compile(r"^workspace-source-[0-9a-f]{64}$")
_SIDECAR_KEYS = frozenset({"version", "source_identity", "canonical_root"})


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


def canonical_workspace_root(root: Path) -> Path:
    """Return one explicit, canonical, non-redirecting directory root."""

    if type(root) is not _PLAIN_PATH_TYPE:
        _fail_closed()
    raw = os.fspath(root)
    if not isinstance(raw, str) or not raw or _implicit_home_text(raw):
        _fail_closed()
    if not root.is_absolute():
        _fail_closed()
    try:
        root_stat = root.lstat()
    except (OSError, RuntimeError):
        _fail_closed()
    if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_or_symlink(root_stat):
        _fail_closed()
    try:
        resolved = root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        _fail_closed()
    if type(resolved) is not _PLAIN_PATH_TYPE:
        _fail_closed()
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


def _sidecar_lock(path: Path) -> FileLock:
    return FileLock(str(path.with_suffix(path.suffix + ".lock")), timeout=5)


def _read_sidecar_document(data_dir: Path) -> dict[str, str] | None:
    path = authorization_path(data_dir)
    try:
        if not path.is_file():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, RuntimeError, json.JSONDecodeError, UnicodeError):
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
        or not canonical_root
        or _implicit_home_text(canonical_root)
    ):
        return None
    return {"source_identity": identity, "canonical_root": canonical_root}


def _write_sidecar(data_dir: Path, *, canonical_root: Path, source_identity: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = authorization_path(data_dir)
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
    with _sidecar_lock(path):
        temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
        try:
            temporary.write_text(payload, encoding="utf-8", newline="\n")
            temporary.replace(path)
        except OSError:
            raise CaptureError("capture_failed") from None
        finally:
            temporary.unlink(missing_ok=True)


def _build_adapter(root: Path) -> LocalGitWorkspaceCaptureProviderAdapter:
    try:
        adapter = LocalGitWorkspaceCaptureProviderAdapter((root,))
    except (TypeError, ValueError, OSError, RuntimeError):
        _fail_closed()
    if _WORKSPACE_IDENTITY.fullmatch(adapter.source_identity) is None:
        _fail_closed()
    return adapter


def _workspace_sources(coordinator: CaptureCoordinator) -> list[CaptureSource]:
    sources, _total = coordinator.list_sources()
    return [source for source in sources if source.provider == LOCAL_GIT_WORKSPACE_PROVIDER]


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


def _try_register_authorized_adapter(
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
    workspace_sources = _workspace_sources(coordinator)
    if len(workspace_sources) > 1:
        return
    if workspace_sources and workspace_sources[0].account_fingerprint != adapter.source_identity:
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
    registered only for a valid machine-local authorization. Sidecar or root
    failures stay closed here and do not prevent Core from starting.
    """

    coordinator = CaptureCoordinator(
        store,
        sink=RegisteredSourceCaptureApplicationSink(store, clock=clock),
        clock=clock,
    )
    _try_register_authorized_adapter(coordinator, config)
    return coordinator


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
    config.prepare()
    canonical = canonical_workspace_root(root)
    adapter = _build_adapter(canonical)
    identity = adapter.source_identity
    existing = _read_sidecar_document(config.data_dir)
    if existing is not None and existing["source_identity"] != identity:
        _fail_identity()
    coordinator = compose_capture_coordinator(store, config)
    workspace_sources = _workspace_sources(coordinator)
    mismatched = [source for source in workspace_sources if source.account_fingerprint != identity]
    if mismatched:
        _fail_identity()
    matching = [source for source in workspace_sources if source.account_fingerprint == identity]
    if len(matching) > 1:
        raise CaptureError("capture_failed")
    if matching:
        source = matching[0]
        if (
            source.requested_scopes != REGISTERED_SOURCE_CODE_OWNED_SCOPES
            or not source.local_only
            or not source.local_only_acknowledged
            or source.lifecycle_state == "revoked"
        ):
            _fail_identity()
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
    _write_sidecar(config.data_dir, canonical_root=canonical, source_identity=identity)
    return _public_authorization(source, reconciled=reconciled)


__all__ = [
    "AUTHORIZATION_FILENAME",
    "AUTHORIZATION_VERSION",
    "LOCAL_WORKSPACE_ACCOUNT_LABEL",
    "authorization_path",
    "authorize_local_workspace",
    "canonical_workspace_root",
    "compose_capture_coordinator",
]
