"""Honest raw-import scale boundary: budgets, disk preflight, progress, cancel.

The inclusive product boundary is ``2_000_000_000`` raw bytes. This module does
not claim OS-level exact-boundary acceptance; it implements the runtime
machinery and deterministic refusal rules that acceptance later exercises.
"""

from __future__ import annotations

import shutil
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .config import MAX_IMPORT_BYTES
from .storage import SOURCE_BLOB_CHUNK_BYTES, InvalidStateError, durable_sqlite_footprint

# Frozen scale profile (product budgets, not wall-clock project estimates).
REFERENCE_LOGICAL_CORES = 4
REFERENCE_RAM_BYTES = 8 * 1024**3
REFERENCE_FREE_DISK_BYTES = 16 * 1024**3
IMPORT_RSS_BUDGET_BYTES = 1 * 1024**3
STORAGE_MULTIPLIER = 4
STORAGE_OVERHEAD_BYTES = 1 * 1024**3
PROGRESS_HEARTBEAT_SECONDS = 5.0
PROGRESS_HEARTBEAT_BYTES = 64 * 1024 * 1024
CANCEL_ACKNOWLEDGE_SECONDS = 5.0
CANCEL_QUIESCE_SECONDS = 30.0
OPERATION_WALL_SECONDS = 60 * 60
BOUNDARY_BYTES = MAX_IMPORT_BYTES
BOUNDARY_PLUS_ONE_BYTES = MAX_IMPORT_BYTES + 1

ImportPhase = Literal[
    "preflight",
    "storing",
    "parsing",
    "ingesting",
    "verifying",
    "publishing",
    "complete",
    "failed",
    "cancelled",
]

ImportStatus = Literal["processing", "complete", "failed", "cancelled"]

ProgressCallback = Callable[["ImportProgress"], None]


class ImportCancelledError(InvalidStateError):
    """Raised when an in-flight import acknowledges a cancellation request."""


@dataclass(frozen=True, slots=True)
class DiskPreflightResult:
    source_bytes: int
    required_free_bytes: int
    free_bytes: int
    path: str
    measured_high_water_bytes: int | None
    formula_bytes: int
    ok: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_bytes": self.source_bytes,
            "required_free_bytes": self.required_free_bytes,
            "free_bytes": self.free_bytes,
            "path": self.path,
            "measured_high_water_bytes": self.measured_high_water_bytes,
            "formula_bytes": self.formula_bytes,
            "ok": self.ok,
            "storage_multiplier": STORAGE_MULTIPLIER,
            "storage_overhead_bytes": STORAGE_OVERHEAD_BYTES,
        }


@dataclass(slots=True)
class ImportProgress:
    phase: ImportPhase
    bytes_processed: int
    bytes_total: int
    updated_at_monotonic: float = field(default_factory=time.monotonic)
    message: str = ""
    cancel_requested: bool = False
    cancel_acknowledged_at_monotonic: float | None = None
    percent: int = 0

    def __post_init__(self) -> None:
        self.percent = _percent(self.bytes_processed, self.bytes_total, self.phase)

    def as_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "bytes_processed": self.bytes_processed,
            "bytes_total": self.bytes_total,
            "percent": self.percent,
            "message": self.message,
            "cancel_requested": self.cancel_requested,
            "cancel_acknowledged": self.cancel_acknowledged_at_monotonic is not None,
        }


def formula_storage_budget_bytes(source_bytes: int) -> int:
    """Return four-times-source-plus-1-GiB for the given raw source size."""
    if source_bytes < 0:
        raise ValueError("source_bytes must be non-negative")
    return source_bytes * STORAGE_MULTIPLIER + STORAGE_OVERHEAD_BYTES


def required_free_bytes(
    source_bytes: int,
    *,
    measured_high_water_bytes: int | None = None,
) -> int:
    """Disk preflight requirement: max(4x source + 1 GiB, measured high-water + 25%)."""
    formula = formula_storage_budget_bytes(source_bytes)
    if measured_high_water_bytes is None:
        return formula
    if measured_high_water_bytes < 0:
        raise ValueError("measured_high_water_bytes must be non-negative")
    measured = measured_high_water_bytes + (measured_high_water_bytes // 4)
    return max(formula, measured)


def expected_chunk_count(byte_size: int, *, chunk_bytes: int = SOURCE_BLOB_CHUNK_BYTES) -> int:
    if byte_size < 0:
        raise ValueError("byte_size must be non-negative")
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be positive")
    if byte_size == 0:
        return 0
    return (byte_size + chunk_bytes - 1) // chunk_bytes


def refuse_if_over_boundary(byte_size: int, *, limit: int = BOUNDARY_BYTES) -> None:
    """Deterministically refuse sources larger than the inclusive boundary."""
    if byte_size < 0:
        raise InvalidStateError("source size must be non-negative")
    if byte_size > limit:
        raise InvalidStateError(
            f"import exceeds the {limit}-byte size limit "
            f"(received {byte_size} bytes; boundary+1 is refused)"
        )


def preflight_disk_space(
    path: Path,
    source_bytes: int,
    *,
    measured_high_water_bytes: int | None = None,
    database_path: Path | None = None,
) -> DiskPreflightResult:
    """Require enough free space for temporary upload, SQLite growth, and WAL."""
    if source_bytes < 0:
        raise InvalidStateError("source size must be non-negative")
    target = path.expanduser().resolve()
    if target.is_file():
        probe = target.parent
    else:
        probe = target
        probe.mkdir(parents=True, exist_ok=True)
    free = int(shutil.disk_usage(probe).free)
    formula = formula_storage_budget_bytes(source_bytes)
    high_water = measured_high_water_bytes
    if high_water is None and database_path is not None:
        try:
            high_water = durable_sqlite_footprint(database_path)
        except OSError:
            high_water = None
    required = required_free_bytes(source_bytes, measured_high_water_bytes=high_water)
    result = DiskPreflightResult(
        source_bytes=source_bytes,
        required_free_bytes=required,
        free_bytes=free,
        path=str(probe),
        measured_high_water_bytes=high_water,
        formula_bytes=formula,
        ok=free >= required,
    )
    if not result.ok:
        raise InvalidStateError(
            "insufficient free disk space for import preflight: "
            f"need at least {required} bytes free near {probe}, found {free}"
        )
    return result


def _percent(processed: int, total: int, phase: ImportPhase) -> int:
    if phase == "complete":
        return 100
    if phase in {"failed", "cancelled"}:
        if total <= 0:
            return 0
        return min(99, max(0, int((max(processed, 0) * 100) // total)))
    if total <= 0:
        if phase == "preflight":
            return 0
        return 1
    # 100% is reserved for integrity verification + atomic publication.
    raw = int((max(processed, 0) * 99) // total)
    return min(99, max(0, raw))


class ImportCancelRegistry:
    """Process-local cancel requests keyed by source id or import token."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, threading.Event] = {}

    def register(self, key: str) -> threading.Event:
        with self._lock:
            event = self._events.get(key)
            if event is None:
                event = threading.Event()
                self._events[key] = event
            return event

    def request_cancel(self, key: str) -> bool:
        with self._lock:
            event = self._events.get(key)
            if event is None:
                event = threading.Event()
                self._events[key] = event
                event.set()
                return False
            event.set()
            return True

    def is_cancelled(self, key: str) -> bool:
        with self._lock:
            event = self._events.get(key)
            return bool(event is not None and event.is_set())

    def clear(self, key: str) -> None:
        with self._lock:
            self._events.pop(key, None)


DEFAULT_CANCEL_REGISTRY = ImportCancelRegistry()


@dataclass(slots=True)
class ImportProgressTracker:
    """Bounded monotonic progress with cancel checks and optional durable sink."""

    bytes_total: int
    source_id: str | None = None
    cancel_key: str | None = None
    registry: ImportCancelRegistry = field(default_factory=lambda: DEFAULT_CANCEL_REGISTRY)
    on_progress: ProgressCallback | None = None
    durable_sink: Callable[[ImportProgress], None] | None = None
    heartbeat_seconds: float = PROGRESS_HEARTBEAT_SECONDS
    heartbeat_bytes: int = PROGRESS_HEARTBEAT_BYTES
    _phase: ImportPhase = "preflight"
    _bytes_processed: int = 0
    _message: str = ""
    _last_emit_monotonic: float = field(default_factory=time.monotonic)
    _last_emit_bytes: int = 0
    _cancel_acknowledged_at: float | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        key = self.cancel_key or self.source_id
        if key is not None:
            self.registry.register(key)

    @property
    def phase(self) -> ImportPhase:
        return self._phase

    @property
    def bytes_processed(self) -> int:
        return self._bytes_processed

    def snapshot(self) -> ImportProgress:
        with self._lock:
            return ImportProgress(
                phase=self._phase,
                bytes_processed=self._bytes_processed,
                bytes_total=self.bytes_total,
                updated_at_monotonic=time.monotonic(),
                message=self._message,
                cancel_requested=self._cancel_requested(),
                cancel_acknowledged_at_monotonic=self._cancel_acknowledged_at,
            )

    def set_phase(self, phase: ImportPhase, *, message: str = "", force: bool = True) -> None:
        with self._lock:
            self._phase = phase
            if message:
                self._message = message
        self._emit(force=force)

    def advance_bytes(self, absolute_processed: int, *, message: str = "") -> None:
        with self._lock:
            processed = max(0, min(absolute_processed, max(self.bytes_total, 0)))
            if processed < self._bytes_processed:
                # Monotonic: never report less committed progress.
                processed = self._bytes_processed
            # Stay within one committed storage chunk of true progress.
            if self.bytes_total > 0:
                max_ahead = self._bytes_processed + SOURCE_BLOB_CHUNK_BYTES
                if processed > max_ahead and self._phase == "storing":
                    processed = max_ahead
            self._bytes_processed = processed
            if message:
                self._message = message
            should_emit = self._should_emit_locked()
        if should_emit:
            self._emit(force=True)
        self.check_cancelled()

    def add_bytes(self, delta: int, *, message: str = "") -> None:
        if delta < 0:
            raise ValueError("progress delta must be non-negative")
        with self._lock:
            target = self._bytes_processed + delta
        self.advance_bytes(target, message=message)

    def check_cancelled(self) -> None:
        if not self._cancel_requested():
            return
        with self._lock:
            if self._cancel_acknowledged_at is None:
                self._cancel_acknowledged_at = time.monotonic()
                self._phase = "cancelled"
                self._message = "cancellation acknowledged"
        self._emit(force=True)
        raise ImportCancelledError("import cancelled by operator request")

    def complete(self, *, message: str = "import complete") -> None:
        with self._lock:
            self._phase = "complete"
            self._bytes_processed = max(self._bytes_processed, self.bytes_total)
            self._message = message
        self._emit(force=True)

    def fail(self, *, message: str) -> None:
        with self._lock:
            self._phase = "failed"
            self._message = message
        self._emit(force=True)

    def close(self) -> None:
        key = self.cancel_key or self.source_id
        if key is not None:
            self.registry.clear(key)

    def bind_source(self, source_id: str) -> None:
        self.source_id = source_id
        if self.cancel_key is None:
            self.cancel_key = source_id
        self.registry.register(source_id)

    def _cancel_requested(self) -> bool:
        key = self.cancel_key or self.source_id
        if key is None:
            return False
        return self.registry.is_cancelled(key)

    def _should_emit_locked(self) -> bool:
        now = time.monotonic()
        timed_out = now - self._last_emit_monotonic >= self.heartbeat_seconds
        bytes_elapsed = self._bytes_processed - self._last_emit_bytes >= self.heartbeat_bytes
        return timed_out or bytes_elapsed

    def _emit(self, *, force: bool) -> None:
        progress = self.snapshot()
        with self._lock:
            if not force and not self._should_emit_locked():
                return
            self._last_emit_monotonic = time.monotonic()
            self._last_emit_bytes = self._bytes_processed
        if self.on_progress is not None:
            self.on_progress(progress)
        if self.durable_sink is not None:
            self.durable_sink(progress)


def merge_progress_metadata(
    metadata: Mapping[str, Any] | None,
    progress: ImportProgress,
) -> dict[str, Any]:
    merged = dict(metadata or {})
    merged["import_progress"] = progress.as_dict()
    return merged


def scale_profile() -> dict[str, Any]:
    return {
        "boundary_bytes": BOUNDARY_BYTES,
        "boundary_plus_one_bytes": BOUNDARY_PLUS_ONE_BYTES,
        "reference_logical_cores": REFERENCE_LOGICAL_CORES,
        "reference_ram_bytes": REFERENCE_RAM_BYTES,
        "reference_free_disk_bytes": REFERENCE_FREE_DISK_BYTES,
        "import_rss_budget_bytes": IMPORT_RSS_BUDGET_BYTES,
        "storage_multiplier": STORAGE_MULTIPLIER,
        "storage_overhead_bytes": STORAGE_OVERHEAD_BYTES,
        "source_blob_chunk_bytes": SOURCE_BLOB_CHUNK_BYTES,
        "progress_heartbeat_seconds": PROGRESS_HEARTBEAT_SECONDS,
        "progress_heartbeat_bytes": PROGRESS_HEARTBEAT_BYTES,
        "cancel_acknowledge_seconds": CANCEL_ACKNOWLEDGE_SECONDS,
        "cancel_quiesce_seconds": CANCEL_QUIESCE_SECONDS,
        "operation_wall_seconds": OPERATION_WALL_SECONDS,
    }
