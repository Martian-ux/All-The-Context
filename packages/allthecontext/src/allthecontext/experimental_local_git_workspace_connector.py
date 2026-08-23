"""Experimental explicit-root, read-only local workspace capture adapter.

This module deliberately has no Git command, hook, configuration-program, or
network integration.  A caller must provide every authorized directory.  The
adapter walks only those resolved directories, treats all discovered text as
inert source data, and emits the existing provider-neutral capture contract.

The opaque cursor contains a bounded metadata-only manifest of the previous
scan.  The Core capture checkpoint remains the only durable cursor and replay
authority; this adapter does not persist a second ledger.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import stat
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .capture import (
    MAX_CAPTURE_INTEGER,
    MAX_CURSOR_CHARS,
    CaptureCapabilityManifest,
    CaptureError,
    CaptureEvent,
    CapturePage,
    CaptureProviderAdapter,
    CaptureSource,
)

LOCAL_GIT_WORKSPACE_PROVIDER = "local-git-workspace"

MAX_SCAN_DEPTH = 16
MAX_DISCOVERED_FILES = 512
MAX_TRACKED_ITEMS = 20
MAX_FILE_BYTES = 256 * 1024
MAX_TEXT_CHARS = 1_800
MAX_RELATIVE_PATH_CHARS = 512

_CURSOR_VERSION = "v0"
_CURSOR_ENTRY_BYTES = 36  # 8-byte root token, 12-byte path token, 16-byte state token
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


@dataclass(frozen=True, slots=True)
class CaptureScanReport:
    """Content-free diagnostics for the most recent bounded scan."""

    authorized_root_count: int
    files_considered: int
    items_emitted: int
    excluded_paths: int
    credential_like_paths: int
    symlinks_or_reparse_points_skipped: int
    incomplete: bool


@dataclass(frozen=True, slots=True)
class _ScannedItem:
    item_id: str
    root_token: str
    relative_path: str
    size: int
    content_sha256: str
    content_truncated: bool
    content_kind: str
    text_excerpt: str | None
    state_token: bytes


@dataclass(slots=True)
class _MutableScanReport:
    files_considered: int = 0
    items_emitted: int = 0
    excluded_paths: int = 0
    credential_like_paths: int = 0
    symlinks_or_reparse_points_skipped: int = 0
    incomplete: bool = False


class LocalGitWorkspaceCaptureProviderAdapter(CaptureProviderAdapter):
    """Read-only capture adapter for explicitly authorized local roots.

    ``roots`` is intentionally required and has no discovery fallback.  Git
    metadata is excluded as a directory; workspace files are never interpreted
    as commands, hooks, configuration, or instructions.
    """

    def __init__(self, roots: Iterable[Path]) -> None:
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
        self._source_identity = (
            "workspace-source-"
            + hashlib.sha256("\0".join(self._root_tokens).encode("ascii")).hexdigest()
        )
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
                "cursor-state-bounded",
            ),
        )
        self._last_scan_report: CaptureScanReport | None = None

    @property
    def capability_manifest(self) -> CaptureCapabilityManifest:
        return self._capability_manifest

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
        previous_generation, previous_state = self._decode_cursor(cursor)
        try:
            current_items = self._scan(report)
        except CaptureError:
            self._last_scan_report = self._report(report)
            raise
        if report.incomplete:
            self._last_scan_report = self._report(report)
            raise CaptureError("capture_adapter_unavailable")
        if len(current_items) > MAX_TRACKED_ITEMS:
            report.incomplete = True
            self._last_scan_report = self._report(report)
            raise CaptureError("capture_page_limit_exceeded")

        generation = 1 if cursor is None else previous_generation + 1
        if generation > MAX_CAPTURE_INTEGER:
            raise CaptureError("capture_invalid_cursor")
        current_state = {item.item_id: item.state_token for item in current_items.values()}
        events = self._diff_events(
            current_items=current_items,
            previous_state=previous_state,
            generation=generation,
        )
        next_cursor = self._encode_cursor(generation, current_state)
        report.items_emitted = len(current_items)
        self._last_scan_report = self._report(report)
        return CapturePage(
            generation=generation,
            events=events,
            page_order=page_order,
            done=True,
            next_cursor=next_cursor,
            coverage="partial",
            freshness="fresh",
        )

    def _report(self, report: _MutableScanReport) -> CaptureScanReport:
        return CaptureScanReport(
            authorized_root_count=len(self._roots),
            files_considered=report.files_considered,
            items_emitted=report.items_emitted,
            excluded_paths=report.excluded_paths,
            credential_like_paths=report.credential_like_paths,
            symlinks_or_reparse_points_skipped=report.symlinks_or_reparse_points_skipped,
            incomplete=report.incomplete,
        )

    def _scan(self, report: _MutableScanReport) -> dict[str, _ScannedItem]:
        items: dict[str, _ScannedItem] = {}
        for root, root_token in zip(self._roots, self._root_tokens, strict=True):
            try:
                root_stat = root.lstat()
            except OSError as error:
                del error
                report.incomplete = True
                raise CaptureError("capture_adapter_unavailable") from None
            if not stat.S_ISDIR(root_stat.st_mode) or _is_reparse_or_symlink(root_stat):
                report.incomplete = True
                raise CaptureError("capture_adapter_unavailable")
            self._scan_root(root, root_token, items, report)
        return items

    def _scan_root(
        self,
        root: Path,
        root_token: str,
        items: dict[str, _ScannedItem],
        report: _MutableScanReport,
    ) -> None:
        pending: list[tuple[Path, int]] = [(root, 0)]
        while pending:
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
                if report.files_considered > MAX_DISCOVERED_FILES:
                    report.incomplete = True
                    continue
                item = self._read_item(
                    entry,
                    entry_stat.st_size,
                    root,
                    root_token,
                    relative_path,
                    report,
                )
                if item is not None:
                    items[item.item_id] = item

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
            if not stat.S_ISREG(resolved_stat.st_mode) or _is_reparse_or_symlink(resolved_stat):
                report.symlinks_or_reparse_points_skipped += 1
                return None
            with resolved.open("rb") as handle:
                sample = handle.read(MAX_FILE_BYTES + 1)
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
        text_excerpt = _safe_text_excerpt(decoded) if decoded is not None else None
        content_sha256 = hashlib.sha256(sample).hexdigest()
        path_token = hashlib.sha256(relative_path.encode("utf-8")).digest()[:12].hex()
        item_id = f"item:{root_token}:{path_token}"
        fingerprint = hashlib.sha256(f"{size}:{content_sha256}".encode("ascii")).digest()
        state_token = fingerprint[:16]
        return _ScannedItem(
            item_id=item_id,
            root_token=root_token,
            relative_path=relative_path,
            size=size,
            content_sha256=content_sha256,
            content_truncated=truncated,
            content_kind=content_kind,
            text_excerpt=text_excerpt,
            state_token=state_token,
        )

    def _diff_events(
        self,
        *,
        current_items: Mapping[str, _ScannedItem],
        previous_state: Mapping[str, bytes],
        generation: int,
    ) -> tuple[CaptureEvent, ...]:
        changed = [
            item
            for item_id, item in current_items.items()
            if previous_state.get(item_id) != item.state_token
        ]
        changed.sort(key=lambda item: item.item_id)
        deleted = sorted(set(previous_state) - set(current_items))
        events: list[CaptureEvent] = []
        position = 1
        for item in changed:
            state_token = _state_token_text(item.state_token)
            payload: dict[str, Any] = {
                "relative_path": item.relative_path,
                "root_id": item.root_token,
                "kind": item.content_kind,
                "size": item.size,
                "content_sha256": item.content_sha256,
                "content_truncated": item.content_truncated,
                "hash_scope": "sample" if item.content_truncated else "full",
            }
            if item.text_excerpt:
                payload["text"] = item.text_excerpt
            events.append(
                CaptureEvent(
                    provider_event_id=f"upsert:{item.item_id}:{state_token}",
                    provider_item_id=item.item_id,
                    order_key=_order_key(generation, position),
                    operation="upsert",
                    payload=payload,
                    generation=generation,
                )
            )
            position += 1
        for item_id in deleted:
            previous_token = _state_token_text(previous_state[item_id])
            events.append(
                CaptureEvent(
                    provider_event_id=f"delete:{item_id}:{previous_token}",
                    provider_item_id=item_id,
                    order_key=_order_key(generation, position),
                    operation="delete",
                    payload={},
                    generation=generation,
                )
            )
            position += 1
        return tuple(events)

    def _decode_cursor(self, cursor: str | None) -> tuple[int, dict[str, bytes]]:
        if cursor is None:
            return 0, {}
        if not isinstance(cursor, str):
            raise CaptureError("capture_invalid_cursor")
        if len(cursor) > MAX_CURSOR_CHARS:
            raise CaptureError("capture_invalid_cursor")
        parts = cursor.split(":", 3)
        if len(parts) != 4 or parts[0] != _CURSOR_VERSION:
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
        if len(raw) % _CURSOR_ENTRY_BYTES != 0 or (
            len(raw) // _CURSOR_ENTRY_BYTES > MAX_TRACKED_ITEMS
        ):
            raise CaptureError("capture_invalid_cursor")
        state: dict[str, bytes] = {}
        for offset in range(0, len(raw), _CURSOR_ENTRY_BYTES):
            entry = raw[offset : offset + _CURSOR_ENTRY_BYTES]
            root_token = entry[:8].hex()
            path_token = entry[8:20].hex()
            if root_token not in self._root_tokens:
                raise CaptureError("capture_invalid_cursor")
            item_id = f"item:{root_token}:{path_token}"
            if item_id in state:
                raise CaptureError("capture_invalid_cursor")
            state[item_id] = entry[20:]
        return generation, state

    def _encode_cursor(self, generation: int, state: Mapping[str, bytes]) -> str:
        if len(state) > MAX_TRACKED_ITEMS:
            raise CaptureError("capture_page_limit_exceeded")
        packed = bytearray()
        for item_id in sorted(state):
            parts = item_id.split(":")
            if len(parts) != 3 or parts[0] != "item":
                raise CaptureError("capture_invalid_cursor")
            try:
                root_bytes = bytes.fromhex(parts[1])
                path_bytes = bytes.fromhex(parts[2])
            except ValueError:
                raise CaptureError("capture_invalid_cursor") from None
            state_token = state[item_id]
            if (
                parts[1] not in self._root_tokens
                or len(root_bytes) != 8
                or len(path_bytes) != 12
                or not isinstance(state_token, bytes)
                or len(state_token) != 16
            ):
                raise CaptureError("capture_invalid_cursor")
            packed.extend(root_bytes)
            packed.extend(path_bytes)
            packed.extend(state_token)
        encoded = base64.urlsafe_b64encode(bytes(packed)).decode("ascii").rstrip("=")
        roots = ",".join(self._root_tokens)
        cursor = f"{_CURSOR_VERSION}:{generation}:{roots}:{encoded}"
        if len(cursor) > MAX_CURSOR_CHARS:
            raise CaptureError("capture_page_limit_exceeded")
        return cursor


def _root_token(root: Path) -> str:
    return hashlib.sha256(root.as_posix().encode("utf-8")).digest()[:8].hex()


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


def _safe_text_excerpt(value: str | None) -> str | None:
    if value is None:
        return None
    safe = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    safe = " ".join(safe.split())
    if not safe:
        return None
    return safe[:MAX_TEXT_CHARS]


def _state_token_text(value: bytes) -> str:
    return value.hex()


def _order_key(generation: int, position: int) -> str:
    return f"g{generation:020d}-e{position:08d}"


__all__ = [
    "LOCAL_GIT_WORKSPACE_PROVIDER",
    "MAX_DISCOVERED_FILES",
    "MAX_FILE_BYTES",
    "MAX_SCAN_DEPTH",
    "MAX_TEXT_CHARS",
    "MAX_TRACKED_ITEMS",
    "CaptureScanReport",
    "LocalGitWorkspaceCaptureProviderAdapter",
]
