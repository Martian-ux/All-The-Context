"""Durable import-operation lifecycle owned by Core.

Creates an opaque operation id before any source bytes are accepted, streams
upload with committed-chunk heartbeats, promotes only complete blobs to
canonical sources, and supports cancel/retry/recovery without partial current
context publication. Operation telemetry is not a second source of truth.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any, BinaryIO

from .config import MAX_IMPORT_BYTES
from .import_boundary import (
    DEFAULT_CANCEL_REGISTRY,
    MAX_REQUEST_CHUNK_BYTES,
    PROGRESS_HEARTBEAT_SECONDS,
    ImportCancelledError,
    ImportCancelRegistry,
    ImportProgress,
    ImportProgressTracker,
    coerce_declared_byte_size,
    durable_import_error_code,
    merge_progress_metadata,
    preflight_disk_space,
    refuse_if_over_boundary,
)
from .importers import (
    ArchiveImportService,
    _media_type,
    _processing_source_metadata,
    _provisional_source_service,
    _source_type,
)
from .provider_ingestion import ArchiveProvider, normalize_provider
from .storage import (
    SOURCE_BLOB_CHUNK_BYTES,
    ConflictError,
    CoreStore,
    InvalidStateError,
    NotFoundError,
)

STAGING_DIR_NAME = "import-staging"
READ_BUFFER_BYTES = MAX_REQUEST_CHUNK_BYTES
ACTIVE_OPERATION_STATUSES = frozenset({"awaiting_upload", "uploading", "processing"})
TERMINAL_OPERATION_STATUSES = frozenset({"complete", "failed", "cancelled"})

ByteSource = BinaryIO | Iterator[bytes] | Callable[[], Iterator[bytes]]


def _result_source_id(result: Mapping[str, Any]) -> str:
    """Return the canonical source id after parser-driven reclassification."""

    source = result.get("source")
    if not isinstance(source, Mapping):
        raise InvalidStateError("import result is missing its canonical source")
    source_id = source.get("id")
    if not isinstance(source_id, str) or not source_id:
        raise InvalidStateError("import result has an invalid canonical source id")
    return source_id


def new_import_operation_id() -> str:
    """Return an opaque random UUIDv4 operation identifier."""
    return str(uuid.uuid4())


class ImportOperationService:
    """Core-owned import operation API: start, upload, status, cancel, retry."""

    def __init__(
        self,
        store: CoreStore,
        imports: ArchiveImportService,
        *,
        data_dir: Path,
        max_bytes: int = MAX_IMPORT_BYTES,
        cancel_registry: ImportCancelRegistry | None = None,
        skip_disk_preflight: bool = False,
    ) -> None:
        if not 1 <= max_bytes <= MAX_IMPORT_BYTES:
            raise ValueError(f"max_bytes must be between 1 and {MAX_IMPORT_BYTES}")
        self.store = store
        self.imports = imports
        self.data_dir = data_dir.expanduser().resolve()
        self.max_bytes = max_bytes
        self.cancel_registry = cancel_registry or DEFAULT_CANCEL_REGISTRY
        self.skip_disk_preflight = skip_disk_preflight
        self._active_lock = threading.Lock()
        self._active_workers: set[str] = set()
        self.staging_root = self.data_dir / STAGING_DIR_NAME
        self.staging_root.mkdir(parents=True, exist_ok=True)

    def recover_interrupted_operations(self) -> list[dict[str, Any]]:
        """After process restart, terminalize orphan non-terminal operations safely."""
        recovered: list[dict[str, Any]] = []
        for operation in self.store.list_nonterminal_import_operations():
            operation_id = str(operation["operation_id"])
            with self._active_lock:
                if operation_id in self._active_workers:
                    continue
            source_id = operation.get("source_id")
            if source_id:
                # Raw source was preserved; mark failed so no-upload retry works.
                progress = dict(operation.get("progress") or {})
                progress.update(
                    {
                        "phase": "failed",
                        "message": "import interrupted by process restart; raw source retained",
                        "percent": min(99, int(progress.get("percent") or 0)),
                    }
                )
                updated = self.store.update_import_operation(
                    operation_id,
                    status="failed",
                    phase="failed",
                    progress=progress,
                    error_message="import_interrupted_process_restart",
                    completed=True,
                )
                try:
                    source = self.store.get_source(str(source_id), duplicate=True)
                    if source.import_status == "processing":
                        metadata = merge_progress_metadata(
                            source.metadata,
                            _progress_from_dict(progress),
                        )
                        self.store.update_source_import(
                            str(source_id),
                            import_status="failed",
                            metadata=metadata,
                            parser_warnings=source.parser_warnings,
                        )
                except NotFoundError:
                    pass
            else:
                content_hash = operation.get("content_hash")
                if content_hash:
                    self.store.delete_incomplete_source_blob(str(content_hash))
                self._cleanup_staging(str(operation["staging_name"]))
                progress = dict(operation.get("progress") or {})
                progress.update(
                    {
                        "phase": "failed",
                        "message": "import interrupted before raw source preservation",
                        "percent": min(99, int(progress.get("percent") or 0)),
                    }
                )
                updated = self.store.update_import_operation(
                    operation_id,
                    status="failed",
                    phase="failed",
                    progress=progress,
                    error_message="import_interrupted_before_source",
                    completed=True,
                )
            recovered.append(updated)
        self.store.cleanup_orphan_incomplete_blobs()
        self._cleanup_orphan_staging_dirs()
        return recovered

    def start_operation(
        self,
        *,
        declared_byte_size: object,
        filename: str | None = None,
        source_service: str = "auto",
        provider: str | None = None,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        """Create a durable operation id after boundary + disk preflight, before bytes."""
        size = coerce_declared_byte_size(declared_byte_size)
        refuse_if_over_boundary(size, limit=self.max_bytes)
        safe_name = Path(filename or "import.bin").name
        provider_hint = provider
        preflight = None
        if not self.skip_disk_preflight:
            preflight = preflight_disk_space(
                self.store.database_path.parent,
                size,
                database_path=self.store.database_path,
            )
        operation_id = new_import_operation_id()
        staging_name = operation_id
        progress = ImportProgress(
            phase="awaiting_upload",
            bytes_processed=0,
            bytes_total=max(size, 1),
            message="awaiting source upload",
        )
        self.cancel_registry.register(operation_id)
        operation = self.store.create_import_operation(
            operation_id=operation_id,
            declared_byte_size=size,
            filename=safe_name,
            media_type=media_type or _media_type(safe_name),
            source_service=source_service,
            provider_hint=provider_hint,
            staging_name=staging_name,
            preflight=preflight.as_dict() if preflight is not None else {},
            progress=progress.as_dict(),
        )
        self._staging_dir(staging_name).mkdir(parents=True, exist_ok=True)
        return operation

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        return self.store.get_import_operation(operation_id)

    def cancel_operation(self, operation_id: str) -> dict[str, Any]:
        result = self.store.request_import_operation_cancel(operation_id)
        worker_registered = self.cancel_registry.request_cancel(operation_id)
        result["worker_registered"] = worker_registered
        source_id = result.get("source_id")
        if source_id:
            self.cancel_registry.request_cancel(str(source_id))
            with suppress(NotFoundError, InvalidStateError):
                self.imports.cancel_import(str(source_id))
        return result

    def retry_operation(self, operation_id: str) -> dict[str, Any]:
        """Retry parse/ingest from a preserved raw source without re-upload."""
        operation = self.store.get_import_operation(operation_id)
        if operation["status"] == "complete":
            return operation
        source_id = operation.get("source_id")
        if not source_id:
            raise InvalidStateError(
                "import operation has no preserved source; re-upload is required"
            )
        if operation["status"] in ACTIVE_OPERATION_STATUSES:
            raise ConflictError("import operation is still active")

        claimed: dict[str, Any] | None = None
        with self._active_lock:
            if operation_id in self._active_workers:
                raise ConflictError("import operation retry already in progress")
            # Exclusive durable claim under the process lock so concurrent retries
            # cannot both reactivate the same failed/cancelled row.
            claimed = self.store.claim_import_operation_retry(operation_id)
            if claimed["status"] == "complete":
                return claimed
            self._active_workers.add(operation_id)
        assert claimed is not None
        source_id = str(claimed["source_id"])
        self.cancel_registry.register(operation_id)
        self.cancel_registry.alias(source_id, operation_id)
        tracker: ImportProgressTracker | None = None
        try:
            declared_byte_size = max(int(claimed["declared_byte_size"]), 0)
            tracker = ImportProgressTracker(
                bytes_total=max(declared_byte_size, 1),
                initial_bytes_processed=declared_byte_size,
                cancel_key=operation_id,
                registry=self.cancel_registry,
                durable_sink=self._operation_progress_sink(operation_id),
                liveness_sink=self._operation_liveness_sink(operation_id),
            )
            tracker.bind_source(source_id)
            tracker.set_phase("storing", message="preserved raw source ready")
            tracker.set_phase("parsing", message="retry parse/ingest from preserved source")
            # Pass the operation tracker so phase/progress/cancel heartbeats land
            # on the durable operation row during long reprocess work.
            result = self.imports.reprocess_source(source_id, progress_tracker=tracker)
            source_id = _result_source_id(result)
            self.store.update_import_operation(
                operation_id,
                status="complete",
                phase="complete",
                source_id=source_id,
                progress=tracker.snapshot().as_dict()
                if tracker.phase == "complete"
                else {
                    **tracker.snapshot().as_dict(),
                    "phase": "complete",
                    "percent": 100,
                    "message": "import complete",
                },
                result=result,
                completed=True,
            )
            tracker.close()
            return self.get_operation(operation_id)
        except ImportCancelledError:
            # After parser reclassification merges, the tracker holds the canonical id.
            terminal_source = (tracker.source_id if tracker is not None else None) or source_id
            self._mark_cancelled(operation_id, source_id=terminal_source)
            raise
        except Exception as error:
            terminal_source = (tracker.source_id if tracker is not None else None) or source_id
            self._mark_failed(operation_id, error, source_id=terminal_source)
            raise
        finally:
            with self._active_lock:
                self._active_workers.discard(operation_id)

    def accept_upload(
        self,
        operation_id: str,
        source: ByteSource,
        *,
        expected_size: int | None = None,
        process_after: bool = True,
    ) -> dict[str, Any]:
        """Stream source bytes, stage durably, then optionally parse/ingest."""
        if expected_size is not None:
            expected_size = coerce_declared_byte_size(expected_size)
        operation = self.store.get_import_operation(operation_id)
        if operation["status"] in TERMINAL_OPERATION_STATUSES:
            raise InvalidStateError(f"import operation is already {operation['status']}")
        if operation["status"] not in {"awaiting_upload", "uploading"}:
            raise InvalidStateError(
                f"import operation is not accepting upload (status={operation['status']})"
            )
        declared = int(operation["declared_byte_size"])
        if expected_size is not None and expected_size != declared:
            raise InvalidStateError(
                f"upload size mismatch: declared {declared} bytes, content-length {expected_size}"
            )
        with self._active_lock:
            if operation_id in self._active_workers:
                raise ConflictError("import operation upload already in progress")
            if operation["status"] != "awaiting_upload":
                raise ConflictError(
                    f"import operation upload already claimed (status={operation['status']})"
                )
            # Durable exclusive claim prevents concurrent upload writers.
            self.store.claim_import_operation_upload(operation_id)
            self._active_workers.add(operation_id)
        try:
            operation = self.store.get_import_operation(operation_id)
            return self._accept_upload_locked(
                operation,
                source,
                process_after=process_after,
            )
        finally:
            with self._active_lock:
                self._active_workers.discard(operation_id)

    def import_path_via_operation(
        self,
        path: Path,
        *,
        filename: str | None = None,
        source_service: str = "auto",
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Convenience: start + stream a local file + process under one operation."""
        resolved = path.expanduser().resolve()
        if not resolved.is_file():
            raise InvalidStateError("import file does not exist")
        size = resolved.stat().st_size
        operation = self.start_operation(
            declared_byte_size=size,
            filename=filename or resolved.name,
            source_service=source_service,
            provider=provider,
        )
        with resolved.open("rb") as handle:
            return self.accept_upload(
                str(operation["operation_id"]),
                handle,
                expected_size=size,
                process_after=True,
            )

    def _accept_upload_locked(
        self,
        operation: Mapping[str, Any],
        source: ByteSource,
        *,
        process_after: bool,
    ) -> dict[str, Any]:
        operation_id = str(operation["operation_id"])
        declared = int(operation["declared_byte_size"])
        staging_name = str(operation["staging_name"])
        staging_path = self._staging_path(staging_name)
        tracker = ImportProgressTracker(
            bytes_total=max(declared, 1),
            cancel_key=operation_id,
            registry=self.cancel_registry,
            durable_sink=self._operation_progress_sink(
                operation_id,
                bytes_received_provider=lambda: received,
            ),
            liveness_sink=self._operation_liveness_sink(operation_id),
        )
        self.cancel_registry.register(operation_id)
        digest = hashlib.sha256()
        received = 0
        committed = 0
        pending = bytearray()
        content_hash: str | None = None
        source_id: str | None = None
        last_heartbeat_mono = time.monotonic()
        try:
            tracker.set_phase("uploading", message="receiving source bytes")
            self.store.update_import_operation(
                operation_id,
                status="uploading",
                phase="uploading",
                progress=tracker.snapshot().as_dict(),
            )
            tracker.start_durable_heartbeats()
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            if staging_path.exists():
                staging_path.unlink()
            with staging_path.open("wb") as destination:
                for chunk in _iter_bytes(source):
                    tracker.check_cancelled()
                    if not chunk:
                        continue
                    if received + len(chunk) > declared:
                        raise InvalidStateError(f"upload exceeds declared size of {declared} bytes")
                    destination.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    pending.extend(chunk)
                    # Report received, but durable commit advances in 8 MiB steps.
                    while len(pending) >= SOURCE_BLOB_CHUNK_BYTES:
                        tracker.check_cancelled()
                        destination.flush()
                        _fsync_file(destination)
                        committed += SOURCE_BLOB_CHUNK_BYTES
                        del pending[:SOURCE_BLOB_CHUNK_BYTES]
                        tracker.advance_bytes(
                            committed,
                            message=f"committed {committed} source bytes",
                        )
                        self.store.update_import_operation(
                            operation_id,
                            status="uploading",
                            phase="uploading",
                            bytes_received=received,
                            bytes_committed=committed,
                            progress=tracker.snapshot().as_dict(),
                        )
                        last_heartbeat_mono = time.monotonic()
                    # Liveness heartbeat: slow/stalled sources below chunk threshold
                    # must still advance updated_at without a false committed claim.
                    now = time.monotonic()
                    if now - last_heartbeat_mono >= PROGRESS_HEARTBEAT_SECONDS:
                        tracker.heartbeat(
                            message=f"received {received} source bytes",
                            force=True,
                        )
                        self.store.update_import_operation(
                            operation_id,
                            status="uploading",
                            phase="uploading",
                            bytes_received=received,
                            bytes_committed=committed,
                            progress=tracker.snapshot().as_dict(),
                        )
                        last_heartbeat_mono = now
                # Final partial chunk.
                destination.flush()
                _fsync_file(destination)
            if received != declared:
                raise InvalidStateError(
                    f"upload size mismatch: declared {declared} bytes, received {received}"
                )
            committed = received
            content_hash = digest.hexdigest()
            tracker.set_phase("hashing", message="source integrity digest complete")
            tracker.advance_bytes(committed, message="upload integrity verified")
            self.store.update_import_operation(
                operation_id,
                status="processing",
                phase="hashing",
                bytes_received=received,
                bytes_committed=committed,
                content_hash=content_hash,
                progress=tracker.snapshot().as_dict(),
            )
            tracker.check_cancelled()
            source_id = self._promote_staging_to_source(
                operation_id=operation_id,
                operation=operation,
                staging_path=staging_path,
                content_hash=content_hash,
                byte_size=declared,
                tracker=tracker,
            )
            self._cleanup_staging(staging_name)
            if not process_after:
                tracker.set_phase("storing", message="raw source preserved")
                self.store.update_import_operation(
                    operation_id,
                    status="processing",
                    phase="storing",
                    source_id=source_id,
                    content_hash=content_hash,
                    bytes_committed=declared,
                    bytes_received=declared,
                    progress=tracker.snapshot().as_dict(),
                )
                return self.get_operation(operation_id)
            result = self._parse_and_ingest(
                operation_id=operation_id,
                source_id=source_id,
                operation=operation,
                tracker=tracker,
            )
            source_id = _result_source_id(result)
            self.store.update_import_operation(
                operation_id,
                status="complete",
                phase="complete",
                source_id=source_id,
                content_hash=content_hash,
                bytes_committed=declared,
                bytes_received=declared,
                progress={
                    "phase": "complete",
                    "bytes_processed": declared,
                    "bytes_total": max(declared, 1),
                    "percent": 100,
                    "message": "import complete",
                    "cancel_requested": False,
                    "cancel_acknowledged": False,
                },
                result=result,
                completed=True,
            )
            return self.get_operation(operation_id)
        except ImportCancelledError:
            # Prefer the tracker's post-merge source id when reclassification rebound it.
            self._handle_cancel_cleanup(
                operation_id,
                staging_name=staging_name,
                content_hash=content_hash,
                source_id=tracker.source_id or source_id,
            )
            raise
        except Exception as error:
            self._handle_failure_cleanup(
                operation_id,
                error,
                staging_name=staging_name,
                content_hash=content_hash,
                source_id=tracker.source_id or source_id,
            )
            raise
        finally:
            tracker.close()

    def _promote_staging_to_source(
        self,
        *,
        operation_id: str,
        operation: Mapping[str, Any],
        staging_path: Path,
        content_hash: str,
        byte_size: int,
        tracker: ImportProgressTracker,
    ) -> str:
        tracker.set_phase("staging", message="committing source blob chunks")
        self.store.update_import_operation(
            operation_id,
            status="processing",
            phase="staging",
            content_hash=content_hash,
            progress=tracker.snapshot().as_dict(),
        )
        media_type = str(operation["media_type"])
        filename = operation.get("filename")
        provider_hint = operation.get("provider_hint")
        source_service = str(operation.get("source_service") or "auto")
        progress_meta = _processing_source_metadata(
            _provider_enum(str(provider_hint) if provider_hint else None, source_service),
            preflight=None,
            progress=tracker.snapshot(),
        )
        if operation.get("preflight"):
            progress_meta["preflight"] = operation["preflight"]
        progress_meta["import_operation_id"] = operation_id

        blob_state = self.store.begin_incomplete_source_blob(
            content_hash=content_hash,
            byte_size=byte_size,
            media_type=media_type,
        )
        if blob_state == "in_progress":
            raise ConflictError("identical content is still staging under another import operation")
        if blob_state == "created":
            if byte_size == 0:
                self.store.finalize_source_blob(
                    content_hash=content_hash,
                    expected_byte_size=0,
                    media_type=media_type,
                    inline_content=b"",
                )
            else:
                written = 0
                verify = hashlib.sha256()
                chunk_index = 0
                with staging_path.open("rb") as handle:
                    while True:
                        tracker.check_cancelled()
                        chunk = handle.read(SOURCE_BLOB_CHUNK_BYTES)
                        if not chunk:
                            break
                        self.store.write_source_blob_chunk(
                            content_hash=content_hash,
                            chunk_index=chunk_index,
                            content=chunk,
                        )
                        verify.update(chunk)
                        written += len(chunk)
                        chunk_index += 1
                        # Committed progress tracks durable Core blob writes.
                        tracker.advance_bytes(
                            written,
                            message=f"staged chunk {chunk_index}",
                        )
                        self.store.update_import_operation(
                            operation_id,
                            status="processing",
                            phase="staging",
                            bytes_committed=written,
                            bytes_received=byte_size,
                            content_hash=content_hash,
                            progress=tracker.snapshot().as_dict(),
                        )
                if written != byte_size or verify.hexdigest() != content_hash:
                    self.store.delete_incomplete_source_blob(content_hash)
                    raise InvalidStateError(
                        "staging payload integrity failed during Core blob commit"
                    )
                self.store.finalize_source_blob(
                    content_hash=content_hash,
                    expected_byte_size=byte_size,
                    media_type=media_type,
                )
        # blob_state == complete: reuse existing complete blob (duplicate content)
        tracker.set_phase("storing", message="linking canonical source record")
        provisional = _provisional_source_service(
            source_service,
            str(provider_hint) if provider_hint else None,
        )
        source = self.store.create_source_record_for_blob(
            content_hash=content_hash,
            source_service=provisional,
            source_type=_source_type(str(filename or "import.bin")),
            filename=str(filename) if filename else None,
            metadata=progress_meta,
            parser_warnings=(),
            import_status="processing",
        )
        tracker.bind_source(source.id)
        self.cancel_registry.alias(source.id, operation_id)
        self.store.update_import_operation(
            operation_id,
            status="processing",
            phase="storing",
            source_id=source.id,
            content_hash=content_hash,
            bytes_committed=byte_size,
            bytes_received=byte_size,
            progress=tracker.snapshot().as_dict(),
        )
        return source.id

    def _parse_and_ingest(
        self,
        *,
        operation_id: str,
        source_id: str,
        operation: Mapping[str, Any],
        tracker: ImportProgressTracker,
    ) -> dict[str, Any]:
        del operation  # reserved for future provider/result wiring
        tracker.set_phase("parsing", message="parsing preserved raw source")
        self.store.update_import_operation(
            operation_id,
            status="processing",
            phase="parsing",
            source_id=source_id,
            progress=tracker.snapshot().as_dict(),
        )
        # Reuse reprocess so parser/ingest/cancel stay single-sourced; dual-write
        # progress onto the operation via the external tracker sink.
        result = self.imports.reprocess_source(source_id, progress_tracker=tracker)
        return result

    def _operation_progress_sink(
        self,
        operation_id: str,
        *,
        bytes_received_provider: Callable[[], int] | None = None,
    ) -> Callable[[ImportProgress], None]:
        def _sink(progress: ImportProgress) -> None:
            # Cancel/fail may terminalize from the tracker; complete+result is owned
            # by the worker so pollers never observe complete without a result payload.
            if progress.phase == "cancelled":
                status = "cancelled"
                completed = True
                phase: str = "cancelled"
                progress_payload = progress.as_dict()
            elif progress.phase == "failed":
                status = "failed"
                completed = True
                phase = "failed"
                progress_payload = progress.as_dict()
            elif progress.phase == "complete":
                status = "processing"
                completed = False
                phase = "publishing"
                progress_payload = {
                    **progress.as_dict(),
                    "phase": "publishing",
                    "percent": min(99, int(progress.percent)),
                }
            elif progress.phase == "awaiting_upload":
                status = "awaiting_upload"
                completed = False
                phase = progress.phase
                progress_payload = progress.as_dict()
            elif progress.phase == "uploading":
                status = "uploading"
                completed = False
                phase = progress.phase
                progress_payload = progress.as_dict()
            else:
                status = "processing"
                completed = False
                phase = progress.phase
                progress_payload = progress.as_dict()
            kwargs: dict[str, Any] = {
                "status": status,
                "phase": phase,
                "progress": progress_payload,
                "completed": completed,
            }
            if progress.phase == "failed":
                # progress.message is already a closed durable code from tracker.fail.
                kwargs["error_message"] = progress.message or "import_failed"
            if bytes_received_provider is not None:
                received = max(bytes_received_provider(), 0)
                committed = min(progress.bytes_processed, received)
                kwargs["bytes_received"] = max(received, committed)
                kwargs["bytes_committed"] = committed
            else:
                kwargs["bytes_committed"] = progress.bytes_processed
            # Durable telemetry must commit or the import fails safely; do not
            # swallow write failures and claim progress silently.
            self.store.update_import_operation(operation_id, **kwargs)

        return _sink

    def _operation_liveness_sink(
        self,
        operation_id: str,
    ) -> Callable[[ImportProgress], bool | None]:
        """Observer-visible heartbeat path that cannot become a lifecycle rewrite.

        Qualified Linux evidence clustered operation-row gaps near the 10s SQLite
        busy/connect budget when periodic ticks used ``transaction()`` +
        ``BEGIN IMMEDIATE`` under concurrent lock pressure. Liveness uses the
        fail-fast touch (short busy/lock waits, no row re-fetch) and returns
        False so the tracker retries without advancing its emit throttle.
        Terminal and byte-advancing transitions keep the full sink.
        """

        def _sink(progress: ImportProgress) -> bool:
            if progress.phase in {"cancelled", "failed", "complete"}:
                # The serialized full sink owns terminal transitions.
                return False
            return self.store.touch_import_operation_liveness(operation_id)

        return _sink

    def _rebind_terminal_operation_source(
        self,
        operation_id: str,
        *,
        status: str,
        source_id: str | None,
        current: Mapping[str, Any],
    ) -> None:
        """Rebind a same-status terminal operation to the post-merge source id.

        The tracker sink may terminalize first while the row still points at a
        deleted provisional source. Outer cleanup may then rebind only source_id
        without changing terminal status, phase, result, or closed error code.
        """
        if source_id is None or current.get("source_id") == source_id:
            return
        with suppress(InvalidStateError, NotFoundError):
            self.store.update_import_operation(
                operation_id,
                status=status,
                source_id=source_id,
            )

    def _mark_cancelled(
        self,
        operation_id: str,
        *,
        source_id: str | None = None,
    ) -> None:
        bytes_processed = 0
        bytes_total = 1
        try:
            current = self.store.get_import_operation(operation_id)
            if current["status"] == "cancelled":
                self._rebind_terminal_operation_source(
                    operation_id,
                    status="cancelled",
                    source_id=source_id,
                    current=current,
                )
                return
            bytes_processed = int(current.get("bytes_committed") or 0)
            bytes_total = max(int(current.get("declared_byte_size") or 1), 1)
        except NotFoundError:
            current = {}
        percent = min(99, (bytes_processed * 99) // bytes_total if bytes_total else 0)
        progress: dict[str, Any] = {
            "phase": "cancelled",
            "bytes_processed": bytes_processed,
            "bytes_total": bytes_total,
            "percent": percent,
            "message": "import cancelled",
            "cancel_requested": True,
            "cancel_acknowledged": True,
        }
        with suppress(InvalidStateError):
            self.store.update_import_operation(
                operation_id,
                status="cancelled",
                phase="cancelled",
                source_id=source_id or current.get("source_id"),
                cancel_requested=True,
                progress=progress,
                completed=True,
            )
        if source_id:
            try:
                source = self.store.get_source(source_id, duplicate=True)
                # Never cancel an already-complete canonical after a merge rebind;
                # only non-terminal processing sources are cancelled here.
                if source.import_status != "processing":
                    return
                metadata = merge_progress_metadata(
                    source.metadata,
                    _progress_from_dict(progress),
                )
                metadata["cancel_requested"] = True
                self.store.update_source_import(
                    source_id,
                    import_status="cancelled",
                    metadata=metadata,
                    parser_warnings=source.parser_warnings,
                )
            except Exception:
                pass

    def _mark_failed(
        self,
        operation_id: str,
        error: Exception,
        *,
        source_id: str | None = None,
    ) -> None:
        # Never persist raw exception text (may carry provider content/secrets).
        message = durable_import_error_code(error)
        progress = {
            "phase": "failed",
            "bytes_processed": 0,
            "bytes_total": 1,
            "percent": 0,
            "message": message,
            "cancel_requested": False,
            "cancel_acknowledged": False,
        }
        try:
            current = self.store.get_import_operation(operation_id)
            if current["status"] in TERMINAL_OPERATION_STATUSES:
                if current["status"] == "failed":
                    self._rebind_terminal_operation_source(
                        operation_id,
                        status="failed",
                        source_id=source_id,
                        current=current,
                    )
                return
            progress["bytes_processed"] = int(current.get("bytes_committed") or 0)
            progress["bytes_total"] = max(int(current.get("declared_byte_size") or 1), 1)
        except NotFoundError:
            current = {}
        with suppress(InvalidStateError):
            self.store.update_import_operation(
                operation_id,
                status="failed",
                phase="failed",
                source_id=source_id or current.get("source_id"),
                progress=progress,
                error_message=message,
                completed=True,
            )

    def _handle_cancel_cleanup(
        self,
        operation_id: str,
        *,
        staging_name: str,
        content_hash: str | None,
        source_id: str | None,
    ) -> None:
        if source_id is None and content_hash is not None:
            self.store.delete_incomplete_source_blob(content_hash)
        if source_id is None:
            self._cleanup_staging(staging_name)
        self._mark_cancelled(operation_id, source_id=source_id)
        self.cancel_registry.clear(operation_id)

    def _handle_failure_cleanup(
        self,
        operation_id: str,
        error: Exception,
        *,
        staging_name: str,
        content_hash: str | None,
        source_id: str | None,
    ) -> None:
        if source_id is None and content_hash is not None:
            self.store.delete_incomplete_source_blob(content_hash)
        if source_id is None:
            self._cleanup_staging(staging_name)
        self._mark_failed(operation_id, error, source_id=source_id)
        self.cancel_registry.clear(operation_id)

    def _staging_dir(self, staging_name: str) -> Path:
        # Opaque UUIDv4 only; never interpolate user paths.
        safe = Path(staging_name).name
        if safe != staging_name or ".." in staging_name:
            raise InvalidStateError("invalid staging name")
        return self.staging_root / safe

    def _staging_path(self, staging_name: str) -> Path:
        return self._staging_dir(staging_name) / "payload.bin"

    def _cleanup_staging(self, staging_name: str) -> None:
        directory = self._staging_dir(staging_name)
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)

    def _cleanup_orphan_staging_dirs(self) -> None:
        if not self.staging_root.exists():
            return
        known = {
            str(item["staging_name"])
            for item in self.store.list_import_operations(limit=500)[0]
            if item.get("status") in ACTIVE_OPERATION_STATUSES
        }
        for child in self.staging_root.iterdir():
            if not child.is_dir():
                continue
            if child.name not in known:
                shutil.rmtree(child, ignore_errors=True)


def _iter_bytes(source: ByteSource) -> Iterator[bytes]:
    """Yield bounded slices so oversized caller chunks are never buffered whole."""
    read = getattr(source, "read", None)
    if callable(read):
        while True:
            chunk = read(READ_BUFFER_BYTES)
            if not chunk:
                break
            yield bytes(chunk)
        return
    iterable: Iterator[bytes] = source() if callable(source) else source
    for raw in iterable:
        if not raw:
            continue
        view = memoryview(bytes(raw))
        for offset in range(0, len(view), READ_BUFFER_BYTES):
            yield bytes(view[offset : offset + READ_BUFFER_BYTES])


def _fsync_file(handle: BinaryIO) -> None:
    handle.flush()
    # Some platforms/temp files may not support fsync; best-effort durability.
    with suppress(OSError, AttributeError, ValueError):
        os.fsync(handle.fileno())


def _progress_from_dict(data: Mapping[str, Any]) -> ImportProgress:
    phase = str(data.get("phase") or "failed")
    if phase not in {
        "preflight",
        "awaiting_upload",
        "uploading",
        "hashing",
        "staging",
        "storing",
        "parsing",
        "ingesting",
        "verifying",
        "publishing",
        "complete",
        "failed",
        "cancelled",
    }:
        phase = "failed"
    return ImportProgress(
        phase=phase,  # type: ignore[arg-type]
        bytes_processed=int(data.get("bytes_processed") or 0),
        bytes_total=int(data.get("bytes_total") or 1),
        message=str(data.get("message") or ""),
        cancel_requested=bool(data.get("cancel_requested")),
    )


def _provider_enum(provider_hint: str | None, source_service: str) -> ArchiveProvider:
    raw = provider_hint or source_service or "auto"
    try:
        return normalize_provider(raw)
    except ValueError:
        return ArchiveProvider.AUTO
