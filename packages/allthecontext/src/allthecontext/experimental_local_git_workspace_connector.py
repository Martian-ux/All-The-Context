"""Experimental explicit-root, read-only local workspace capture adapter.

This module deliberately has no Git command, hook, configuration-program, or
network integration. A caller must provide every authorized directory. The
adapter walks only those resolved directories, treats all discovered text as
inert source data, and emits the existing provider-neutral capture contract.

Capture cursors contain only bounded scan metadata. A complete file manifest is
never serialized into the cursor and the adapter does not create a durable
manifest or event ledger. Incremental and deletion reconciliation use a
read-only state reader supplied by the Core composition; a direct adapter may
continue an in-process snapshot, but cannot claim restart-safe incremental
semantics without that reader.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import stat
import unicodedata
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from .capture import (
    MAX_CAPTURE_INTEGER,
    MAX_CURSOR_CHARS,
    MAX_PAGE_EVENTS,
    MAX_RUN_EVENTS,
    MAX_RUN_PAGES,
    CaptureCapabilityManifest,
    CaptureError,
    CaptureEvent,
    CapturePage,
    CaptureProviderAdapter,
    CaptureSource,
)

LOCAL_GIT_WORKSPACE_PROVIDER = "local-git-workspace"

MAX_SCAN_DEPTH = 16
# This is a bounded safety ceiling for one catalog pass, not a per-page limit.
# A normal repository above the historical 512-file cap can now paginate. The
# coordinator's lower run-event/page ceilings still apply to one capture run.
MAX_DISCOVERED_FILES = 16_384
MAX_TRACKED_ITEMS = min(128, MAX_PAGE_EVENTS)
MAX_FILE_BYTES = 256 * 1024
MAX_RELATIVE_PATH_CHARS = 512

_CURSOR_VERSION = "v1"
_LEGACY_CURSOR_VERSION = "v0"
_ROOT_SET_TOKEN_BYTES = 16
_SNAPSHOT_TOKEN_CHARS = 64
_STATE_TOKEN_BYTES = 16
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_EXCLUDED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)
_CREDENTIAL_NAME_RE = re.compile(
    r"(?i)(^|[._-])(env|secret|secrets|credential|credentials|password|passwd|"
    r"token|tokens|api[-_]?key|private[-_]?key|id_rsa|oauth|cookie)(?:$|[._-])"
)
_CREDENTIAL_SUFFIXES = (".key", ".pem", ".p12", ".pfx")
_SECRET_CONTENT_RE = re.compile(
    r"(?i)(bearer\s+|basic\s+|sk-[a-z0-9]|gh[pousr]_[a-z0-9]|AIza[a-z0-9]|"
    r"(?:password|passwd|secret|credential|token|authorization|api[-_]?key)\s*[:=]|"
    r"-----begin\s+[^\r\n]*private\s+key)"
)
_AWS_ACCESS_KEY_RE = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_ITEM_ID_RE = re.compile(r"^item:[0-9a-f]{16}:[0-9a-f]{24}$")
_HEX_RE = re.compile(r"^[0-9a-f]+$")

WorkspaceStateReader = Callable[[str], Mapping[str, bytes | None]]
_CursorMode = Literal["full", "incremental"]
_CursorPhase = Literal["scan", "delete", "done"]


@dataclass(frozen=True, slots=True)
class CaptureScanReport:
    """Content-free diagnostics for the most recent bounded catalog pass."""

    authorized_root_count: int
    files_considered: int
    items_emitted: int
    excluded_paths: int
    credential_like_paths: int
    symlinks_or_reparse_points_skipped: int
    incomplete: bool
    scan_complete: bool = False
    scan_reset: bool = False
    scan_offset: int = 0
    scan_total: int | None = None

    def model_dump(self) -> dict[str, Any]:
        return {
            "authorized_root_count": self.authorized_root_count,
            "files_considered": self.files_considered,
            "items_emitted": self.items_emitted,
            "excluded_paths": self.excluded_paths,
            "credential_like_paths": self.credential_like_paths,
            "symlinks_or_reparse_points_skipped": self.symlinks_or_reparse_points_skipped,
            "incomplete": self.incomplete,
            "scan_complete": self.scan_complete,
            "scan_reset": self.scan_reset,
            "scan_offset": self.scan_offset,
            "scan_total": self.scan_total,
        }


@dataclass(frozen=True, slots=True)
class _ScannedItem:
    item_id: str
    root_token: str
    relative_path: str
    size: int
    content_sha256: str
    content_truncated: bool
    content_kind: str
    state_token: bytes


@dataclass(frozen=True, slots=True)
class _CatalogScan:
    page_items: tuple[_ScannedItem, ...]
    current_item_ids: frozenset[str]
    current_state: dict[str, bytes]
    snapshot_token: str
    total_items: int


@dataclass(frozen=True, slots=True)
class _Cursor:
    generation: int
    root_set_token: str
    snapshot_token: str
    mode: _CursorMode
    phase: _CursorPhase
    offset: int
    event_position: int
    last_item_id: str | None


@dataclass(slots=True)
class _MutableScanReport:
    files_considered: int = 0
    items_emitted: int = 0
    excluded_paths: int = 0
    credential_like_paths: int = 0
    symlinks_or_reparse_points_skipped: int = 0
    incomplete: bool = False
    scan_complete: bool = False
    scan_reset: bool = False
    scan_offset: int = 0
    scan_total: int | None = None


class LocalGitWorkspaceCaptureProviderAdapter(CaptureProviderAdapter):
    """Read-only capture adapter for explicitly authorized local roots.

    ``roots`` is intentionally required and has no discovery fallback. Git
    metadata is excluded as a directory; workspace files are never interpreted
    as commands, hooks, configuration, or instructions.

    ``state_reader`` is a Core-owned, metadata-only callback. It returns the
    last state token for active items and ``None`` for items whose last Core
    state is deleted. It is intentionally optional so direct snapshot fixtures
    can remain small, but a newly constructed adapter without it refuses
    incremental/deletion continuation rather than silently losing the prior
    manifest.
    """

    def __init__(
        self,
        roots: Iterable[Path],
        *,
        state_reader: WorkspaceStateReader | None = None,
    ) -> None:
        if isinstance(roots, (str, bytes, Path)):
            raise ValueError("explicit_roots_must_be_a_sequence")
        raw_roots = tuple(roots)
        if not raw_roots:
            raise ValueError("explicit_root_required")

        resolved_roots: list[Path] = []
        for raw_root in raw_roots:
            if not isinstance(raw_root, Path):
                raise TypeError("roots_must_contain_path_objects")
            try:
                resolved = raw_root.expanduser().resolve(strict=True)
                root_stat = resolved.lstat()
            except (OSError, RuntimeError) as error:
                del error
                raise ValueError("explicit_root_unavailable") from None
            if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_or_symlink(root_stat):
                raise ValueError("explicit_root_must_be_a_directory")
            if _excluded_directory_name(resolved.name):
                raise ValueError("excluded_root_refused")
            if _credential_like_name(resolved.name):
                raise ValueError("credential_like_root_refused")
            resolved_roots.append(resolved)

        ordered_roots = tuple(sorted(resolved_roots, key=lambda value: value.as_posix()))
        if len(set(ordered_roots)) != len(ordered_roots):
            raise ValueError("duplicate_explicit_root")
        for index, root in enumerate(ordered_roots):
            if any(root != other and _is_contained(root, other) for other in ordered_roots[:index]):
                raise ValueError("overlapping_explicit_roots")

        self._roots = ordered_roots
        self._root_tokens = tuple(_root_token(root) for root in ordered_roots)
        self._root_set_token = _root_set_token(self._root_tokens)
        self._source_identity = (
            "workspace-source-"
            + hashlib.sha256("\0".join(self._root_tokens).encode("ascii")).hexdigest()
        )
        self._state_reader = state_reader
        self._baseline_state: dict[str, bytes | None] | None = None
        self._baseline_generation: int | None = None
        self._baseline_snapshot: str | None = None
        self._completed_state: dict[str, bytes] | None = None
        self._capability_manifest = CaptureCapabilityManifest(
            provider=LOCAL_GIT_WORKSPACE_PROVIDER,
            availability="partial",
            acquisition_mode="snapshot_and_incremental",
            initial_snapshot=True,
            incremental=True,
            cursor_support=True,
            coverage="partial",
            coverage_reason="explicit-root-exclusions",
            freshness="fresh",
            authorization="authorized",
            connection="connected",
            disconnect_supported=False,
            source_deletion="unsupported",
            purge_coordination="unsupported",
            network_access="denied",
            data_egress=(),
            health="healthy",
            health_diagnostics=(
                "explicit-roots-only",
                "git-metadata-excluded",
                "credential-paths-excluded",
                "symlinks-skipped",
                "catalog-rescanned-per-page",
                f"max-discovered-files-{MAX_DISCOVERED_FILES}",
                f"max-items-per-page-{MAX_TRACKED_ITEMS}",
                f"max-run-pages-{MAX_RUN_PAGES}",
                f"max-run-events-{MAX_RUN_EVENTS}",
                f"max-effective-run-items-{min(MAX_RUN_EVENTS, MAX_RUN_PAGES * MAX_TRACKED_ITEMS)}",
                f"sample-bytes-{MAX_FILE_BYTES}",
            ),
        )
        self._scan_diagnostic: str | None = None
        self._last_scan_report: CaptureScanReport | None = None

    @property
    def capability_manifest(self) -> CaptureCapabilityManifest:
        if self._scan_diagnostic is None:
            return self._capability_manifest
        return replace(
            self._capability_manifest,
            health="degraded",
            health_diagnostics=(
                *self._capability_manifest.health_diagnostics,
                self._scan_diagnostic,
            ),
        )

    @property
    def source_identity(self) -> str:
        """Return a stable, path-derived identifier without exposing the path."""

        return self._source_identity

    @property
    def authorized_roots(self) -> tuple[Path, ...]:
        """Return the resolved roots explicitly supplied to this adapter."""

        return self._roots

    @property
    def last_scan_report(self) -> CaptureScanReport | None:
        return self._last_scan_report

    def fetch_page(
        self,
        source: CaptureSource,
        cursor: str | None,
        page_order: int,
    ) -> CapturePage:
        if not isinstance(source, CaptureSource):
            raise CaptureError("capture_capability_invalid")
        if (
            source.provider != LOCAL_GIT_WORKSPACE_PROVIDER
            or source.account_fingerprint != self.source_identity
        ):
            raise CaptureError("capture_capability_invalid")
        if type(page_order) is not int or not 0 <= page_order <= MAX_CAPTURE_INTEGER:
            raise CaptureError("capture_page_malformed")

        report = _MutableScanReport()
        decoded, legacy_state = self._decode_cursor(cursor)
        catalog: _CatalogScan | None = None
        if decoded is None:
            generation = 1
            mode: _CursorMode = "full"
            phase: _CursorPhase = "scan"
            offset = 0
            event_position = 1
            baseline = self._initial_baseline(source.id, legacy_state=None, allow_empty=True)
            expected_snapshot: str | None = None
        elif decoded.phase == "done":
            # A done cursor is the boundary between one completed snapshot and
            # the next incremental run. A fresh direct adapter cannot safely
            # infer changes or deletions without Core's state reader.
            baseline = self._initial_baseline(
                source.id, legacy_state=legacy_state, allow_empty=False
            )
            catalog = self._scan_catalog(report, page_start=0)
            if report.incomplete:
                self._finish_report(report)
                raise CaptureError("capture_adapter_unavailable")
            if decoded.snapshot_token and catalog.snapshot_token == decoded.snapshot_token:
                report.scan_complete = True
                report.scan_offset = catalog.total_items
                report.scan_total = catalog.total_items
                report.items_emitted = 0
                self._completed_state = dict(catalog.current_state)
                self._finish_report(report)
                return CapturePage(
                    generation=decoded.generation,
                    events=(),
                    page_order=page_order,
                    done=True,
                    next_cursor=cursor,
                    coverage="partial",
                    freshness="fresh",
                )
            generation = _next_generation(decoded.generation)
            mode = "incremental"
            phase = "scan"
            offset = 0
            event_position = 1
            expected_snapshot = None
        else:
            generation = decoded.generation
            mode = decoded.mode
            phase = decoded.phase
            offset = decoded.offset
            event_position = decoded.event_position
            baseline = self._continuation_baseline(
                source.id,
                generation=generation,
                snapshot=decoded.snapshot_token,
                mode=mode,
                phase=phase,
                legacy_state=legacy_state,
            )
            expected_snapshot = decoded.snapshot_token

            catalog = self._scan_catalog(report, page_start=offset)
            if report.incomplete:
                self._finish_report(report)
                raise CaptureError("capture_adapter_unavailable")
            if catalog.snapshot_token != expected_snapshot:
                # A page from the old catalog may already have been admitted.
                # Reset ordering in Core with an empty newer-generation page;
                # the next page performs a complete incremental reconciliation
                # against the Core-owned state observed here.
                if self._state_reader is None:
                    self._finish_report(report)
                    raise CaptureError("capture_adapter_unavailable")
                reset_baseline = self._load_state(source.id, legacy_state=None)
                reset_mode: _CursorMode = "incremental"
                reset_generation = _next_generation(generation)
                reset_cursor = self._encode_cursor(
                    _Cursor(
                        generation=reset_generation,
                        root_set_token=self._root_set_token,
                        snapshot_token=catalog.snapshot_token,
                        mode=reset_mode,
                        phase="scan",
                        offset=0,
                        event_position=1,
                        last_item_id=None,
                    )
                )
                self._baseline_state = reset_baseline
                self._baseline_generation = reset_generation
                self._baseline_snapshot = catalog.snapshot_token
                report.scan_reset = True
                report.scan_offset = 0
                report.scan_total = catalog.total_items
                self._finish_report(report)
                return CapturePage(
                    generation=reset_generation,
                    events=(),
                    page_order=page_order,
                    done=False,
                    next_cursor=reset_cursor,
                    coverage="partial",
                    freshness="fresh",
                )

        if catalog is None:
            catalog = self._scan_catalog(report, page_start=offset)
            if report.incomplete:
                self._finish_report(report)
                raise CaptureError("capture_adapter_unavailable")
        elif phase == "delete":
            assert decoded is not None
            return self._fetch_delete_page(
                source.id,
                catalog,
                baseline,
                decoded,
                page_order,
                report,
            )

        self._set_baseline(baseline, generation=generation, snapshot=catalog.snapshot_token)
        page_items = self._page_events(
            catalog.page_items,
            mode=mode,
            baseline=baseline,
            generation=generation,
            event_position=event_position,
        )
        events = tuple(item[0] for item in page_items)
        next_position = event_position + len(events)
        next_offset = min(offset + MAX_TRACKED_ITEMS, catalog.total_items)
        report.items_emitted = len(events)
        report.scan_offset = next_offset
        report.scan_total = catalog.total_items

        if next_offset < catalog.total_items:
            next_cursor = self._encode_cursor(
                _Cursor(
                    generation=generation,
                    root_set_token=self._root_set_token,
                    snapshot_token=catalog.snapshot_token,
                    mode=mode,
                    phase="scan",
                    offset=next_offset,
                    event_position=next_position,
                    last_item_id=None,
                )
            )
            report.scan_complete = False
            self._finish_report(report)
            return CapturePage(
                generation=generation,
                events=events,
                page_order=page_order,
                done=False,
                next_cursor=next_cursor,
                coverage="partial",
                freshness="fresh",
            )

        missing = self._missing_item_ids(baseline, catalog.current_item_ids)
        if missing:
            next_cursor = self._encode_cursor(
                _Cursor(
                    generation=generation,
                    root_set_token=self._root_set_token,
                    snapshot_token=catalog.snapshot_token,
                    mode=mode,
                    phase="delete",
                    offset=catalog.total_items,
                    event_position=next_position,
                    last_item_id=None,
                )
            )
            report.scan_complete = True
            self._finish_report(report)
            return CapturePage(
                generation=generation,
                events=events,
                page_order=page_order,
                done=False,
                next_cursor=next_cursor,
                coverage="partial",
                freshness="fresh",
            )

        next_cursor = self._encode_cursor(
            _Cursor(
                generation=generation,
                root_set_token=self._root_set_token,
                snapshot_token=catalog.snapshot_token,
                mode=mode,
                phase="done",
                offset=catalog.total_items,
                event_position=next_position,
                last_item_id=None,
            )
        )
        report.scan_complete = True
        self._completed_state = dict(catalog.current_state)
        self._finish_report(report)
        return CapturePage(
            generation=generation,
            events=events,
            page_order=page_order,
            done=True,
            next_cursor=next_cursor,
            coverage="partial",
            freshness="fresh",
        )

    def _fetch_delete_page(
        self,
        source_id: str,
        catalog: _CatalogScan,
        baseline: Mapping[str, bytes | None],
        cursor: _Cursor,
        page_order: int,
        report: _MutableScanReport,
    ) -> CapturePage:
        del source_id
        missing = self._missing_item_ids(baseline, catalog.current_item_ids)
        if cursor.last_item_id is not None:
            missing = tuple(item_id for item_id in missing if item_id > cursor.last_item_id)
        selected = missing[:MAX_TRACKED_ITEMS]
        events: list[CaptureEvent] = []
        position = cursor.event_position
        for item_id in selected:
            previous_state = baseline[item_id]
            assert previous_state is not None
            token = _state_token_text(previous_state)
            events.append(
                CaptureEvent(
                    provider_event_id=f"delete:g{cursor.generation}:{item_id}:{token}",
                    provider_item_id=item_id,
                    order_key=_order_key(cursor.generation, position),
                    operation="delete",
                    payload={},
                    generation=cursor.generation,
                )
            )
            position += 1

        report.items_emitted = len(events)
        report.scan_complete = not missing or len(selected) == len(missing)
        report.scan_offset = catalog.total_items
        report.scan_total = catalog.total_items
        if len(selected) < len(missing):
            next_cursor = self._encode_cursor(
                _Cursor(
                    generation=cursor.generation,
                    root_set_token=self._root_set_token,
                    snapshot_token=catalog.snapshot_token,
                    mode=cursor.mode,
                    phase="delete",
                    offset=catalog.total_items,
                    event_position=position,
                    last_item_id=selected[-1],
                )
            )
            done = False
        else:
            next_cursor = self._encode_cursor(
                _Cursor(
                    generation=cursor.generation,
                    root_set_token=self._root_set_token,
                    snapshot_token=catalog.snapshot_token,
                    mode=cursor.mode,
                    phase="done",
                    offset=catalog.total_items,
                    event_position=position,
                    last_item_id=None,
                )
            )
            done = True
            self._completed_state = dict(catalog.current_state)
        self._finish_report(report)
        return CapturePage(
            generation=cursor.generation,
            events=tuple(events),
            page_order=page_order,
            done=done,
            next_cursor=next_cursor,
            coverage="partial",
            freshness="fresh",
        )

    def _initial_baseline(
        self,
        source_id: str,
        *,
        legacy_state: Mapping[str, bytes | None] | None,
        allow_empty: bool,
    ) -> dict[str, bytes | None]:
        if legacy_state is not None:
            baseline = dict(legacy_state)
        elif self._state_reader is None:
            if self._completed_state is None and not allow_empty:
                raise CaptureError("capture_adapter_unavailable")
            baseline = {} if self._completed_state is None else dict(self._completed_state)
        else:
            baseline = self._load_state(source_id, legacy_state=None)
        self._set_baseline(baseline, generation=None, snapshot=None)
        return baseline

    def _continuation_baseline(
        self,
        source_id: str,
        *,
        generation: int,
        snapshot: str,
        mode: _CursorMode,
        phase: _CursorPhase,
        legacy_state: Mapping[str, bytes | None] | None,
    ) -> dict[str, bytes | None]:
        if (
            self._baseline_state is not None
            and self._baseline_generation == generation
            and self._baseline_snapshot == snapshot
        ):
            return self._baseline_state
        if legacy_state is None and self._state_reader is None and self._completed_state is None:
            if mode != "full" or phase != "scan":
                raise CaptureError("capture_adapter_unavailable")
            baseline: dict[str, bytes | None] = {}
        else:
            baseline = self._load_state(source_id, legacy_state=legacy_state)
        self._set_baseline(baseline, generation=generation, snapshot=snapshot)
        return baseline

    def _set_baseline(
        self,
        baseline: Mapping[str, bytes | None],
        *,
        generation: int | None,
        snapshot: str | None,
    ) -> None:
        self._baseline_state = dict(baseline)
        self._baseline_generation = generation
        self._baseline_snapshot = snapshot

    def _load_state(
        self,
        source_id: str,
        *,
        legacy_state: Mapping[str, bytes | None] | None,
    ) -> dict[str, bytes | None]:
        if legacy_state is not None:
            raw: Mapping[str, bytes | None] = legacy_state
        elif self._state_reader is None:
            if self._completed_state is None:
                raise CaptureError("capture_adapter_unavailable")
            raw = self._completed_state
        else:
            try:
                raw = self._state_reader(source_id)
            except Exception as error:
                del error
                raise CaptureError("capture_adapter_unavailable") from None
        if not isinstance(raw, Mapping):
            raise CaptureError("capture_adapter_unavailable")
        validated: dict[str, bytes | None] = {}
        for item_id, state_token in raw.items():
            if not isinstance(item_id, str) or _ITEM_ID_RE.fullmatch(item_id) is None:
                raise CaptureError("capture_adapter_unavailable")
            if state_token is not None and (
                not isinstance(state_token, bytes) or len(state_token) != _STATE_TOKEN_BYTES
            ):
                raise CaptureError("capture_adapter_unavailable")
            validated[item_id] = state_token
        if len(validated) > MAX_DISCOVERED_FILES:
            raise CaptureError("capture_adapter_unavailable")
        return validated

    def _finish_report(self, report: _MutableScanReport) -> None:
        self._last_scan_report = CaptureScanReport(
            authorized_root_count=len(self._roots),
            files_considered=report.files_considered,
            items_emitted=report.items_emitted,
            excluded_paths=report.excluded_paths,
            credential_like_paths=report.credential_like_paths,
            symlinks_or_reparse_points_skipped=report.symlinks_or_reparse_points_skipped,
            incomplete=report.incomplete,
            scan_complete=report.scan_complete,
            scan_reset=report.scan_reset,
            scan_offset=report.scan_offset,
            scan_total=report.scan_total,
        )
        if report.incomplete:
            self._scan_diagnostic = "scan-incomplete"
        elif report.scan_complete:
            self._scan_diagnostic = None

    def _scan_catalog(self, report: _MutableScanReport, *, page_start: int) -> _CatalogScan:
        page_end = page_start + MAX_TRACKED_ITEMS
        page_items: list[_ScannedItem] = []
        current_item_ids: set[str] = set()
        current_state: dict[str, bytes] = {}
        digest = hashlib.sha256()
        total_items = 0
        for item in self._iter_items(report):
            item_index = total_items
            total_items += 1
            current_item_ids.add(item.item_id)
            current_state[item.item_id] = item.state_token
            digest.update(item.item_id.encode("ascii"))
            digest.update(b"\0")
            digest.update(item.state_token)
            digest.update(b"\0")
            if page_start <= item_index < page_end:
                page_items.append(item)
        return _CatalogScan(
            page_items=tuple(page_items),
            current_item_ids=frozenset(current_item_ids),
            current_state=current_state,
            snapshot_token=digest.hexdigest(),
            total_items=total_items,
        )

    def _iter_items(self, report: _MutableScanReport) -> Iterator[_ScannedItem]:
        for root, root_token in zip(self._roots, self._root_tokens, strict=True):
            if report.files_considered >= MAX_DISCOVERED_FILES:
                report.incomplete = True
                return
            try:
                root_stat = root.lstat()
            except OSError as error:
                del error
                report.incomplete = True
                raise CaptureError("capture_adapter_unavailable") from None
            if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_or_symlink(root_stat):
                report.incomplete = True
                raise CaptureError("capture_adapter_unavailable")
            yield from self._iter_root_items(root, root_token, report)
            if report.incomplete:
                return

    def _iter_root_items(
        self,
        root: Path,
        root_token: str,
        report: _MutableScanReport,
    ) -> Iterator[_ScannedItem]:
        pending: list[tuple[Path, int]] = [(root, 0)]
        while pending:
            if report.files_considered >= MAX_DISCOVERED_FILES:
                report.incomplete = True
                return
            directory, depth = pending.pop()
            try:
                directory_stat = directory.lstat()
                resolved_directory = directory.resolve(strict=True)
                resolved_stat = resolved_directory.lstat()
            except (OSError, RuntimeError) as error:
                del error
                report.incomplete = True
                continue
            if (
                not stat.S_ISDIR(directory_stat.st_mode)
                or _is_reparse_or_symlink(directory_stat)
                or not stat.S_ISDIR(resolved_stat.st_mode)
                or _is_reparse_or_symlink(resolved_stat)
                or not _is_contained(resolved_directory, root)
            ):
                report.symlinks_or_reparse_points_skipped += 1
                continue
            directory = resolved_directory
            try:
                entries = sorted(directory.iterdir(), key=lambda value: _path_sort_key(value.name))
            except OSError as error:
                del error
                report.incomplete = True
                continue
            for entry in entries:
                if report.files_considered >= MAX_DISCOVERED_FILES:
                    report.incomplete = True
                    return
                relative_path = _relative_path(root, entry, report)
                if relative_path is None:
                    continue
                if _excluded_directory_name(entry.name):
                    report.excluded_paths += 1
                    continue
                if _credential_like_name(entry.name):
                    report.credential_like_paths += 1
                    continue
                try:
                    entry_stat = entry.lstat()
                except OSError as error:
                    del error
                    report.incomplete = True
                    continue
                if _is_reparse_or_symlink(entry_stat):
                    report.symlinks_or_reparse_points_skipped += 1
                    continue
                if stat.S_ISDIR(entry_stat.st_mode):
                    if depth >= MAX_SCAN_DEPTH:
                        report.incomplete = True
                        continue
                    pending.append((entry, depth + 1))
                    continue
                if not stat.S_ISREG(entry_stat.st_mode):
                    report.excluded_paths += 1
                    continue
                report.files_considered += 1
                item = self._read_item(
                    entry,
                    entry_stat.st_size,
                    root,
                    root_token,
                    relative_path,
                    report,
                )
                if item is not None:
                    yield item

    def _read_item(
        self,
        entry: Path,
        size: int,
        root: Path,
        root_token: str,
        relative_path: str,
        report: _MutableScanReport,
    ) -> _ScannedItem | None:
        try:
            resolved = entry.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            del error
            report.incomplete = True
            return None
        if not _is_contained(resolved, root):
            report.symlinks_or_reparse_points_skipped += 1
            return None
        try:
            resolved_stat = resolved.lstat()
            if (
                not stat.S_ISREG(resolved_stat.st_mode)
                or _is_reparse_or_symlink(resolved_stat)
                or resolved_stat.st_size != size
            ):
                report.incomplete = True
                return None
            with resolved.open("rb") as handle:
                sample = handle.read(MAX_FILE_BYTES + 1)
            after_stat = resolved.lstat()
            if after_stat.st_size != resolved_stat.st_size:
                report.incomplete = True
                return None
        except OSError as error:
            del error
            report.incomplete = True
            return None

        truncated = size > MAX_FILE_BYTES or len(sample) > MAX_FILE_BYTES
        if len(sample) > MAX_FILE_BYTES:
            sample = sample[:MAX_FILE_BYTES]
        decoded = _decode_text_sample(sample)
        if decoded is not None and _secret_like_content(decoded):
            report.credential_like_paths += 1
            return None
        content_kind = "text" if decoded is not None else "binary"
        content_sha256 = hashlib.sha256(sample).hexdigest()
        item_id = _item_id(root_token, relative_path)
        fingerprint = hashlib.sha256(f"{size}:{content_sha256}".encode("ascii")).digest()
        state_token = fingerprint[:_STATE_TOKEN_BYTES]
        return _ScannedItem(
            item_id=item_id,
            root_token=root_token,
            relative_path=relative_path,
            size=size,
            content_sha256=content_sha256,
            content_truncated=truncated,
            content_kind=content_kind,
            state_token=state_token,
        )

    def _page_events(
        self,
        items: Iterable[_ScannedItem],
        *,
        mode: _CursorMode,
        baseline: Mapping[str, bytes | None],
        generation: int,
        event_position: int,
    ) -> list[tuple[CaptureEvent, _ScannedItem]]:
        events: list[tuple[CaptureEvent, _ScannedItem]] = []
        position = event_position
        for item in items:
            prior = baseline.get(item.item_id, _MISSING_STATE)
            if mode == "incremental" and prior == item.state_token:
                continue
            recreation = prior is None
            state_token = _state_token_text(item.state_token)
            prefix = f"upsert:g{generation}:"
            if recreation:
                prefix += "recreate:"
            event = CaptureEvent(
                provider_event_id=f"{prefix}{item.item_id}:{state_token}",
                provider_item_id=item.item_id,
                order_key=_order_key(generation, position),
                operation="upsert",
                payload={
                    "relative_path": item.relative_path,
                    "root_id": item.root_token,
                    "kind": item.content_kind,
                    "size": item.size,
                    "content_sha256": item.content_sha256,
                    "content_truncated": item.content_truncated,
                    "hash_scope": "sample" if item.content_truncated else "full",
                },
                generation=generation,
            )
            events.append((event, item))
            position += 1
        return events

    @staticmethod
    def _missing_item_ids(
        baseline: Mapping[str, bytes | None], current_item_ids: Iterable[str]
    ) -> tuple[str, ...]:
        current = set(current_item_ids)
        return tuple(
            sorted(
                item_id
                for item_id, state_token in baseline.items()
                if state_token is not None and item_id not in current
            )
        )

    def _decode_cursor(
        self,
        cursor: str | None,
    ) -> tuple[_Cursor | None, Mapping[str, bytes | None] | None]:
        if cursor is None:
            return None, None
        if not isinstance(cursor, str) or len(cursor) > MAX_CURSOR_CHARS:
            raise CaptureError("capture_invalid_cursor")
        parts = cursor.split(":", 3)
        if len(parts) == 4 and parts[0] == _LEGACY_CURSOR_VERSION:
            return self._decode_legacy_cursor(parts)
        parts = cursor.split(":", 8)
        if len(parts) != 9 or parts[0] != _CURSOR_VERSION:
            raise CaptureError("capture_invalid_cursor")
        try:
            generation = int(parts[1])
            offset = int(parts[6])
            event_position = int(parts[7])
        except ValueError:
            raise CaptureError("capture_invalid_cursor") from None
        root_set_token, snapshot_token = parts[2], parts[3]
        if (
            generation < 1
            or generation > MAX_CAPTURE_INTEGER
            or root_set_token != self._root_set_token
            or not _valid_hex(root_set_token, _ROOT_SET_TOKEN_BYTES * 2)
            or not _valid_hex(snapshot_token, _SNAPSHOT_TOKEN_CHARS)
            or parts[4] not in {"full", "incremental"}
            or parts[5] not in {"scan", "delete", "done"}
            or not 0 <= offset <= MAX_DISCOVERED_FILES
            or not 1 <= event_position <= MAX_CAPTURE_INTEGER
        ):
            raise CaptureError("capture_invalid_cursor")
        continuation = parts[8]
        last_item_id = None if continuation == "-" else _decode_item_token(continuation)
        phase = parts[5]
        if (phase == "scan" and last_item_id is not None) or (
            phase != "delete" and last_item_id is not None
        ):
            raise CaptureError("capture_invalid_cursor")
        return (
            _Cursor(
                generation=generation,
                root_set_token=root_set_token,
                snapshot_token=snapshot_token,
                mode=parts[4],  # type: ignore[arg-type]
                phase=phase,  # type: ignore[arg-type]
                offset=offset,
                event_position=event_position,
                last_item_id=last_item_id,
            ),
            None,
        )

    def _decode_legacy_cursor(
        self,
        parts: list[str],
    ) -> tuple[_Cursor, Mapping[str, bytes | None]]:
        if len(parts) != 4 or parts[0] != _LEGACY_CURSOR_VERSION:
            raise CaptureError("capture_invalid_cursor")
        try:
            generation = int(parts[1])
        except ValueError:
            raise CaptureError("capture_invalid_cursor") from None
        if not 1 <= generation <= MAX_CAPTURE_INTEGER or parts[2].split(",") != list(
            self._root_tokens
        ):
            raise CaptureError("capture_invalid_cursor")
        encoded = parts[3]
        if re.fullmatch(r"[A-Za-z0-9_-]*", encoded) is None:
            raise CaptureError("capture_invalid_cursor")
        try:
            padding = "=" * (-len(encoded) % 4)
            raw = base64.urlsafe_b64decode(encoded + padding)
        except (ValueError, binascii.Error):
            raise CaptureError("capture_invalid_cursor") from None
        entry_bytes = 8 + 12 + _STATE_TOKEN_BYTES
        if len(raw) % entry_bytes != 0 or len(raw) // entry_bytes > MAX_TRACKED_ITEMS:
            raise CaptureError("capture_invalid_cursor")
        state: dict[str, bytes] = {}
        for offset in range(0, len(raw), entry_bytes):
            entry = raw[offset : offset + entry_bytes]
            root_token = entry[:8].hex()
            path_token = entry[8:20].hex()
            if root_token not in self._root_tokens:
                raise CaptureError("capture_invalid_cursor")
            item_id = f"item:{root_token}:{path_token}"
            if item_id in state:
                raise CaptureError("capture_invalid_cursor")
            state[item_id] = entry[20:]
        return (
            _Cursor(
                generation=generation,
                root_set_token=self._root_set_token,
                snapshot_token="",
                mode="incremental",
                phase="done",
                offset=0,
                event_position=1,
                last_item_id=None,
            ),
            state,
        )

    def _encode_cursor(self, cursor: _Cursor) -> str:
        if not _valid_hex(cursor.snapshot_token, _SNAPSHOT_TOKEN_CHARS):
            raise CaptureError("capture_page_limit_exceeded")
        continuation = (
            "-" if cursor.last_item_id is None else _encode_item_token(cursor.last_item_id)
        )
        value = ":".join(
            (
                _CURSOR_VERSION,
                str(cursor.generation),
                self._root_set_token,
                cursor.snapshot_token,
                cursor.mode,
                cursor.phase,
                str(cursor.offset),
                str(cursor.event_position),
                continuation,
            )
        )
        if len(value) > MAX_CURSOR_CHARS:
            raise CaptureError("capture_page_limit_exceeded")
        return value


_MISSING_STATE = object()


def _root_token(root: Path) -> str:
    return hashlib.sha256(root.as_posix().encode("utf-8")).digest()[:8].hex()


def _root_set_token(root_tokens: Iterable[str]) -> str:
    return (
        hashlib.sha256("\0".join(root_tokens).encode("ascii"))
        .digest()[:_ROOT_SET_TOKEN_BYTES]
        .hex()
    )


def _item_id(root_token: str, relative_path: str) -> str:
    path_token = hashlib.sha256(relative_path.encode("utf-8")).digest()[:12].hex()
    return f"item:{root_token}:{path_token}"


def _next_generation(generation: int) -> int:
    if generation >= MAX_CAPTURE_INTEGER:
        raise CaptureError("capture_invalid_cursor")
    return generation + 1


def _is_contained(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _is_reparse_or_symlink(value: Any) -> bool:
    mode = getattr(value, "st_mode", 0)
    attributes = int(getattr(value, "st_file_attributes", 0))
    return stat.S_ISLNK(mode) or bool(attributes & _REPARSE_POINT)


def _path_sort_key(value: str) -> tuple[str, str]:
    return value.casefold(), value


def _relative_path(root: Path, entry: Path, report: _MutableScanReport) -> str | None:
    try:
        relative = entry.relative_to(root).as_posix()
    except ValueError:
        report.excluded_paths += 1
        return None
    if (
        not relative
        or len(relative) > MAX_RELATIVE_PATH_CHARS
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in relative)
    ):
        report.excluded_paths += 1
        return None
    return relative


def _excluded_directory_name(name: str) -> bool:
    return name.casefold() in _EXCLUDED_DIRECTORY_NAMES


def _credential_like_name(name: str) -> bool:
    lowered = name.casefold()
    return (
        lowered in {".env", ".git-credentials", ".netrc"}
        or lowered.endswith(_CREDENTIAL_SUFFIXES)
        or _CREDENTIAL_NAME_RE.search(name) is not None
    )


def _decode_text_sample(sample: bytes) -> str | None:
    if b"\x00" in sample:
        return None
    try:
        return sample.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _secret_like_content(value: str) -> bool:
    if _AWS_ACCESS_KEY_RE.search(value) is not None:
        return True
    normalized = unicodedata.normalize("NFKD", value)
    compact = "".join(
        char for char in normalized if unicodedata.category(char) not in {"Cf", "Mn", "Mc", "Me"}
    )
    return _SECRET_CONTENT_RE.search(compact) is not None


def _state_token_text(value: bytes) -> str:
    return value.hex()


def _order_key(generation: int, position: int) -> str:
    return f"g{generation:020d}-e{position:08d}"


def _valid_hex(value: str, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and _HEX_RE.fullmatch(value) is not None


def _encode_item_token(item_id: str) -> str:
    if _ITEM_ID_RE.fullmatch(item_id) is None:
        raise CaptureError("capture_invalid_cursor")
    return base64.urlsafe_b64encode(item_id.encode("ascii")).decode("ascii").rstrip("=")


def _decode_item_token(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise CaptureError("capture_invalid_cursor")
    try:
        padding = "=" * (-len(value) % 4)
        item_id = base64.urlsafe_b64decode(value + padding).decode("ascii")
    except (ValueError, UnicodeDecodeError, binascii.Error):
        raise CaptureError("capture_invalid_cursor") from None
    if _ITEM_ID_RE.fullmatch(item_id) is None:
        raise CaptureError("capture_invalid_cursor")
    return item_id


__all__ = [
    "LOCAL_GIT_WORKSPACE_PROVIDER",
    "MAX_DISCOVERED_FILES",
    "MAX_FILE_BYTES",
    "MAX_SCAN_DEPTH",
    "MAX_TRACKED_ITEMS",
    "CaptureScanReport",
    "LocalGitWorkspaceCaptureProviderAdapter",
    "WorkspaceStateReader",
]
