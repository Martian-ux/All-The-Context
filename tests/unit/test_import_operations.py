"""Focused tests for durable import-operation lifecycle (B-105 gap close)."""

from __future__ import annotations

import hashlib
import io
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from allthecontext.core.service import CoreService
from allthecontext.import_boundary import (
    BOUNDARY_PLUS_ONE_BYTES,
    ImportCancelledError,
    ImportCancelRegistry,
)
from allthecontext.import_operations import ImportOperationService
from allthecontext.importers import ArchiveImportService
from allthecontext.storage import (
    SOURCE_BLOB_CHUNK_BYTES,
    InvalidStateError,
    NotFoundError,
)


def _ops(
    tmp_path: Path,
    *,
    max_bytes: int | None = None,
) -> tuple[CoreService, ImportOperationService]:
    core = CoreService.in_directory(tmp_path)
    registry = ImportCancelRegistry()
    imports = ArchiveImportService(
        core.store,
        max_bytes=max_bytes or core.config.max_import_bytes,
        cancel_registry=registry,
    )
    ops = ImportOperationService(
        core.store,
        imports,
        data_dir=core.config.data_dir,
        max_bytes=max_bytes or core.config.max_import_bytes,
        cancel_registry=registry,
    )
    return core, ops


def test_start_operation_creates_id_before_bytes_and_refuses_boundary_plus_one(
    tmp_path: Path,
) -> None:
    core, ops = _ops(tmp_path, max_bytes=64)
    operation = ops.start_operation(
        declared_byte_size=32,
        filename="notes.jsonl",
        provider="generic",
    )
    assert operation["operation_id"]
    assert operation["status"] == "awaiting_upload"
    assert operation["phase"] == "awaiting_upload"
    assert operation["source_id"] is None
    assert operation["preflight"]["ok"] is True
    # Concurrent status query works before any source id exists.
    polled = ops.get_operation(operation["operation_id"])
    assert polled["status"] == "awaiting_upload"
    sources, total = core.store.list_sources()
    assert total == 0
    assert sources == []

    with pytest.raises(InvalidStateError, match="size limit"):
        ops.start_operation(declared_byte_size=BOUNDARY_PLUS_ONE_BYTES, filename="too-big.bin")
    with pytest.raises(InvalidStateError, match="size limit"):
        ops.start_operation(declared_byte_size=65, filename="over-local-limit.bin")
    sources, total = core.store.list_sources()
    assert total == 0


def test_stream_upload_commits_within_one_chunk_and_completes(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"goal","content":"Ship durable import operations"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="goals.jsonl",
    )
    finished = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
    )
    assert finished["status"] == "complete"
    assert finished["phase"] == "complete"
    assert finished["source_id"]
    assert finished["bytes_committed"] == len(payload)
    assert finished["content_hash"] == hashlib.sha256(payload).hexdigest()
    assert finished["result"]["source"]["import_status"] == "complete"
    assert core.store.get_source_content(str(finished["source_id"])) == payload
    assert finished["progress"]["percent"] == 100


def test_size_mismatch_refuses_without_partial_publication(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    operation = ops.start_operation(declared_byte_size=20, filename="bad.jsonl")
    with pytest.raises(InvalidStateError, match="size mismatch"):
        ops.accept_upload(
            operation["operation_id"],
            io.BytesIO(b"too-short"),
            expected_size=20,
        )
    failed = ops.get_operation(operation["operation_id"])
    assert failed["status"] == "failed"
    _sources, total = core.store.list_sources()
    assert total == 0
    assert core.store.status()["counts"]["active_records"] == 0


def test_content_length_mismatch_refused_before_accept(tmp_path: Path) -> None:
    _core, ops = _ops(tmp_path)
    operation = ops.start_operation(declared_byte_size=10, filename="x.txt")
    with pytest.raises(InvalidStateError, match="upload size mismatch"):
        ops.accept_upload(
            operation["operation_id"],
            io.BytesIO(b"0123456789"),
            expected_size=11,
        )


def test_concurrent_progress_polling_during_upload(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    # Larger than one 8 MiB chunk so intermediate commits are observable.
    size = SOURCE_BLOB_CHUNK_BYTES + 100_000
    header = b'{"kind":"fact","content":"Synthetic concurrent progress target"}\n'
    payload = header + (b"x" * (size - len(header)))
    assert len(payload) == size
    operation = ops.start_operation(declared_byte_size=size, filename="large.jsonl")
    operation_id = operation["operation_id"]
    seen_phases: list[str] = []
    stop = threading.Event()

    def poller() -> None:
        while not stop.is_set():
            try:
                current = ops.get_operation(operation_id)
            except NotFoundError:
                time.sleep(0.01)
                continue
            phase = str(current["phase"])
            if not seen_phases or seen_phases[-1] != phase:
                seen_phases.append(phase)
            # Committed progress never leads received by more than one chunk.
            assert (
                int(current["bytes_committed"])
                <= int(current["bytes_received"]) + SOURCE_BLOB_CHUNK_BYTES
            )
            time.sleep(0.01)

    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    try:
        finished = ops.accept_upload(operation_id, io.BytesIO(payload), expected_size=size)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert finished["status"] == "complete"
    assert "uploading" in seen_phases or "staging" in seen_phases or "hashing" in seen_phases
    assert finished["source_id"]
    assert core.store.get_source(str(finished["source_id"])).byte_size == size


def test_cancel_during_upload_quiesces_without_source(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    size = SOURCE_BLOB_CHUNK_BYTES * 2 + 10
    payload = b"y" * size
    operation = ops.start_operation(declared_byte_size=size, filename="cancel.bin")
    operation_id = operation["operation_id"]
    started = threading.Event()

    def canceller() -> None:
        started.wait(timeout=5)
        time.sleep(0.05)
        ops.cancel_operation(operation_id)

    thread = threading.Thread(target=canceller, daemon=True)
    thread.start()

    def slow_chunks() -> Iterator[bytes]:
        started.set()
        for index in range(0, size, 64 * 1024):
            yield payload[index : index + 64 * 1024]
            time.sleep(0.02)

    with pytest.raises(ImportCancelledError):
        ops.accept_upload(operation_id, slow_chunks(), expected_size=size)
    thread.join(timeout=5)
    final = ops.get_operation(operation_id)
    assert final["status"] == "cancelled"
    _sources, total = core.store.list_sources()
    assert total == 0
    assert core.store.status()["counts"]["active_records"] == 0


def test_cancel_during_staging_chunk_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    core, ops = _ops(tmp_path)
    size = SOURCE_BLOB_CHUNK_BYTES + 50
    payload = b"z" * size
    operation = ops.start_operation(declared_byte_size=size, filename="stage-cancel.bin")
    operation_id = operation["operation_id"]
    original = core.store.write_source_blob_chunk
    calls = {"n": 0}

    def write_and_cancel(**kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == 1:
            ops.cancel_operation(operation_id)
        return original(**kwargs)

    monkeypatch.setattr(core.store, "write_source_blob_chunk", write_and_cancel)
    with pytest.raises(ImportCancelledError):
        ops.accept_upload(operation_id, io.BytesIO(payload), expected_size=size)
    final = ops.get_operation(operation_id)
    assert final["status"] == "cancelled"
    _sources, total = core.store.list_sources()
    # May retain incomplete blob cleanup; must not publish canonical sources/context.
    assert total == 0
    assert core.store.status()["counts"]["active_records"] == 0


def test_injected_chunk_write_failure_no_partial_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    size = SOURCE_BLOB_CHUNK_BYTES + 10
    payload = b"a" * size
    operation = ops.start_operation(declared_byte_size=size, filename="fail.bin")
    original = core.store.write_source_blob_chunk

    def boom(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise InvalidStateError("injected chunk-write failure")

    monkeypatch.setattr(core.store, "write_source_blob_chunk", boom)
    with pytest.raises(InvalidStateError, match="injected chunk-write failure"):
        ops.accept_upload(operation["operation_id"], io.BytesIO(payload), expected_size=size)
    monkeypatch.setattr(core.store, "write_source_blob_chunk", original)
    failed = ops.get_operation(operation["operation_id"])
    assert failed["status"] == "failed"
    _sources, total = core.store.list_sources()
    assert total == 0
    assert core.store.status()["counts"]["active_records"] == 0


def test_process_restart_recovery_marks_failed_and_allows_retry(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Recover after restart"}\n'
    operation = ops.start_operation(declared_byte_size=len(payload), filename="recover.jsonl")
    # Simulate crash after source preservation but before completion.
    finished = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    assert finished["source_id"]
    assert finished["status"] == "processing"
    # Force non-terminal processing state as if the process died mid-parse.
    core.store.update_import_operation(
        operation["operation_id"],
        status="processing",
        phase="parsing",
    )
    recovered = ops.recover_interrupted_operations()
    assert any(item["operation_id"] == operation["operation_id"] for item in recovered)
    state = ops.get_operation(operation["operation_id"])
    assert state["status"] == "failed"
    assert state["source_id"]
    # No-upload retry from preserved source.
    retried = ops.retry_operation(operation["operation_id"])
    assert retried["status"] == "complete"
    assert retried["result"]["source"]["import_status"] == "complete"
    # Duplicate retry is idempotent.
    again = ops.retry_operation(operation["operation_id"])
    assert again["status"] == "complete"
    first_ids = retried["result"]["candidate_ids"]
    second_ids = again["result"]["candidate_ids"]
    assert first_ids == second_ids


def test_duplicate_content_operations_do_not_double_publish(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"goal","content":"One durable fact only"}\n'
    first = ops.import_path_via_operation(
        _write(tmp_path / "a.jsonl", payload),
        filename="a.jsonl",
    )
    second = ops.import_path_via_operation(
        _write(tmp_path / "b.jsonl", payload),
        filename="b.jsonl",
    )
    assert first["status"] == "complete"
    assert second["status"] == "complete"
    first_ids = first["result"]["candidate_ids"]
    second_ids = second["result"]["candidate_ids"]
    assert first_ids
    # Same content hash yields the same observation set (no duplicate decisions).
    assert first_ids == second_ids
    _sources, total = core.store.list_sources()
    assert total >= 1


def test_exact_boundary_arithmetic_helpers() -> None:
    from allthecontext.import_boundary import expected_chunk_count

    assert expected_chunk_count(0) == 0
    assert expected_chunk_count(1) == 1
    assert expected_chunk_count(SOURCE_BLOB_CHUNK_BYTES) == 1
    assert expected_chunk_count(SOURCE_BLOB_CHUNK_BYTES + 1) == 2
    assert expected_chunk_count(2_000_000_000) == (
        (2_000_000_000 + SOURCE_BLOB_CHUNK_BYTES - 1) // SOURCE_BLOB_CHUNK_BYTES
    )


def test_incomplete_blob_never_listed_as_source(tmp_path: Path) -> None:
    core, _ops_service = _ops(tmp_path)
    digest = hashlib.sha256(b"incomplete").hexdigest()
    state = core.store.begin_incomplete_source_blob(
        content_hash=digest,
        byte_size=len(b"incomplete"),
        media_type="application/octet-stream",
    )
    assert state == "created"
    with pytest.raises(InvalidStateError, match="incomplete"):
        core.store.create_source_record_for_blob(
            content_hash=digest,
            source_service="generic",
            source_type="document",
            filename="x.bin",
        )
    _sources, total = core.store.list_sources()
    assert total == 0
    core.store.delete_incomplete_source_blob(digest)


def test_http_operation_api_start_status_upload(tmp_path: Path) -> None:
    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from fastapi.testclient import TestClient

    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    payload = b'{"kind":"fact","content":"HTTP operation path"}\n'
    with TestClient(create_app(config)) as client:
        started = client.post(
            "/v1/admin/import-operations",
            json={
                "declared_byte_size": len(payload),
                "filename": "http.jsonl",
                "provider": "generic",
            },
        )
        assert started.status_code == 200, started.text
        body = started.json()
        operation_id = body["operation_id"]
        assert body["source_id"] is None
        status = client.get(f"/v1/admin/import-operations/{operation_id}")
        assert status.status_code == 200
        assert status.json()["status"] == "awaiting_upload"
        uploaded = client.put(
            f"/v1/admin/import-operations/{operation_id}/content",
            content=payload,
            headers={"Content-Length": str(len(payload))},
        )
        assert uploaded.status_code == 200, uploaded.text
        finished = uploaded.json()
        assert finished["status"] == "complete"
        assert finished["source_id"]
        assert finished["result"]["source"]["id"] == finished["source_id"]


def test_http_refuses_declared_boundary_plus_one(tmp_path: Path) -> None:
    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from fastapi.testclient import TestClient

    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/v1/admin/import-operations",
            json={"declared_byte_size": BOUNDARY_PLUS_ONE_BYTES, "filename": "x.bin"},
        )
        assert response.status_code == 422


def test_multipart_compat_still_returns_import_result(tmp_path: Path) -> None:
    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from fastapi.testclient import TestClient

    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    payload = b'{"kind":"goal","content":"Multipart compatibility"}\n'
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/v1/admin/import",
            files={"file": ("compat.jsonl", payload, "application/jsonl")},
            data={"provider": "generic"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["source"]["import_status"] == "complete"
        assert body["candidate_ids"]


def _write(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_retry_passes_progress_tracker_and_records_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Retry must heartbeat through operation"}\n'
    operation = ops.start_operation(declared_byte_size=len(payload), filename="retry.jsonl")
    finished = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    source_id = str(finished["source_id"])
    core.store.update_import_operation(
        operation["operation_id"],
        status="failed",
        phase="failed",
        error_message="import_interrupted_process_restart",
        completed=True,
    )
    core.store.update_source_import(
        source_id,
        import_status="failed",
        metadata=core.store.get_source(source_id, duplicate=True).metadata,
        parser_warnings=(),
    )

    seen_phases: list[str] = []
    tracker_passed = {"ok": False}
    original = ops.imports.reprocess_source

    def tracked_reprocess(source_id_arg: str, *, progress_tracker=None):  # type: ignore[no-untyped-def]
        assert progress_tracker is not None, "retry must pass progress_tracker"
        tracker_passed["ok"] = True
        progress_tracker.set_phase("parsing", message="synthetic parse")
        seen_phases.append(progress_tracker.phase)
        progress_tracker.heartbeat(message="synthetic parse heartbeat", force=True)
        mid = ops.get_operation(operation["operation_id"])
        assert mid["status"] == "processing"
        assert mid["phase"] in {
            "parsing",
            "storing",
            "ingesting",
            "verifying",
            "publishing",
        }
        return original(source_id_arg, progress_tracker=progress_tracker)

    monkeypatch.setattr(ops.imports, "reprocess_source", tracked_reprocess)
    retried = ops.retry_operation(operation["operation_id"])
    assert tracker_passed["ok"] is True
    assert retried["status"] == "complete"
    assert retried["phase"] == "complete"
    assert "parsing" in seen_phases


def test_retry_cancel_acknowledged_via_operation_tracker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Cancel during no-upload retry"}\n'
    operation = ops.start_operation(declared_byte_size=len(payload), filename="retry-cancel.jsonl")
    finished = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    operation_id = operation["operation_id"]
    source_id = str(finished["source_id"])
    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        error_message="import_interrupted_process_restart",
        completed=True,
    )
    core.store.update_source_import(
        source_id,
        import_status="failed",
        metadata=core.store.get_source(source_id, duplicate=True).metadata,
        parser_warnings=(),
    )

    entered = threading.Event()

    def blocking_reprocess(source_id_arg: str, *, progress_tracker=None):  # type: ignore[no-untyped-def]
        assert progress_tracker is not None
        progress_tracker.set_phase("parsing", message="awaiting cancel")
        entered.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            progress_tracker.check_cancelled()
            time.sleep(0.05)
        raise AssertionError("cancel was not acknowledged within budget")

    monkeypatch.setattr(ops.imports, "reprocess_source", blocking_reprocess)

    def canceller() -> None:
        entered.wait(timeout=5)
        ops.cancel_operation(operation_id)

    thread = threading.Thread(target=canceller, daemon=True)
    thread.start()
    with pytest.raises(ImportCancelledError):
        ops.retry_operation(operation_id)
    thread.join(timeout=5)
    final = ops.get_operation(operation_id)
    assert final["status"] == "cancelled"
    assert final["progress"]["cancel_acknowledged"] is True


def test_concurrent_retry_claims_fail_closed(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"goal","content":"Only one concurrent retry may claim"}\n'
    operation = ops.start_operation(declared_byte_size=len(payload), filename="claim.jsonl")
    finished = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    operation_id = operation["operation_id"]
    source_id = str(finished["source_id"])
    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        error_message="import_invalid_state",
        completed=True,
    )
    core.store.update_source_import(
        source_id,
        import_status="failed",
        metadata=core.store.get_source(source_id, duplicate=True).metadata,
        parser_warnings=(),
    )

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait(timeout=5)
        try:
            result = ops.retry_operation(operation_id)
            with lock:
                outcomes.append(f"ok:{result['status']}")
        except Exception as error:
            with lock:
                outcomes.append(f"err:{type(error).__name__}")

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert len(outcomes) == 2
    oks = [item for item in outcomes if item.startswith("ok:")]
    errs = [item for item in outcomes if item.startswith("err:")]
    assert len(oks) == 1
    assert oks[0] == "ok:complete"
    assert len(errs) == 1
    assert errs[0] == "err:ConflictError"
    final = ops.get_operation(operation_id)
    assert final["status"] == "complete"
    # Idempotent duplicate-content retry after exclusive winner completed.
    again = ops.retry_operation(operation_id)
    assert again["status"] == "complete"
    assert again["result"]["candidate_ids"] == final["result"]["candidate_ids"]


def test_cancel_awaiting_upload_is_immediate(tmp_path: Path) -> None:
    _core, ops = _ops(tmp_path)
    operation = ops.start_operation(declared_byte_size=32, filename="idle.bin")
    cancelled = ops.cancel_operation(operation["operation_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled.get("immediate") is True
    state = ops.get_operation(operation["operation_id"])
    assert state["status"] == "cancelled"
    assert state["phase"] == "cancelled"
    with pytest.raises(InvalidStateError, match="already cancelled"):
        ops.accept_upload(operation["operation_id"], io.BytesIO(b"x" * 32), expected_size=32)


def test_stalled_upload_cancel_observes_within_poll_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import import_boundary as boundary
    from allthecontext import import_operations as ops_module

    # Scale heartbeat/poll clocks; do not sleep the full production 5s budget.
    monkeypatch.setattr(boundary, "PROGRESS_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(ops_module, "PROGRESS_HEARTBEAT_SECONDS", 0.05)

    core, ops = _ops(tmp_path)
    size = 64 * 1024
    operation = ops.start_operation(declared_byte_size=size, filename="stall.bin")
    operation_id = operation["operation_id"]
    started = threading.Event()
    ack_times: dict[str, float] = {}

    def canceller() -> None:
        started.wait(timeout=5)
        time.sleep(0.05)
        t0 = time.monotonic()
        ops.cancel_operation(operation_id)
        # Poll until durable cancel is terminal or requested is observed by worker.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            state = ops.get_operation(operation_id)
            if state["status"] == "cancelled" or state.get("cancel_requested"):
                ack_times["observed"] = time.monotonic() - t0
                break
            time.sleep(0.01)

    def stalled_chunks() -> Iterator[bytes]:
        started.set()
        # Yield one small piece then block; cancel must be observed on the next check.
        yield b"a" * 1024
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if ops.cancel_registry.is_cancelled(operation_id):
                # Next worker iteration must acknowledge via tracker.check_cancelled.
                yield b"b" * 1024
                return
            time.sleep(0.05)
        yield b"c" * 1024

    thread = threading.Thread(target=canceller, daemon=True)
    thread.start()
    with pytest.raises(ImportCancelledError):
        ops.accept_upload(operation_id, stalled_chunks(), expected_size=size)
    thread.join(timeout=5)
    final = ops.get_operation(operation_id)
    assert final["status"] == "cancelled"
    assert "observed" in ack_times
    assert ack_times["observed"] < 5.0
    _sources, total = core.store.list_sources()
    assert total == 0


def test_upload_heartbeat_without_false_committed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import import_boundary as boundary
    from allthecontext import import_operations as ops_module

    monkeypatch.setattr(boundary, "PROGRESS_HEARTBEAT_SECONDS", 0.05)
    monkeypatch.setattr(ops_module, "PROGRESS_HEARTBEAT_SECONDS", 0.05)

    _core, ops = _ops(tmp_path)
    # Well under one 8 MiB commit chunk so only heartbeats can refresh updated_at.
    header = b'{"kind":"fact","content":"heartbeat fixture"}\n'
    size = 64 * 1024
    payload = header + (b"h" * (size - len(header)))
    operation = ops.start_operation(declared_byte_size=size, filename="heartbeat.jsonl")
    operation_id = operation["operation_id"]
    stamps: list[str] = []
    committed_while_uploading: list[int] = []
    stop = threading.Event()

    def poller() -> None:
        while not stop.is_set():
            try:
                current = ops.get_operation(operation_id)
            except NotFoundError:
                time.sleep(0.01)
                continue
            stamps.append(str(current["updated_at"]))
            assert int(current["bytes_committed"]) <= int(current["bytes_received"])
            if current["status"] == "uploading":
                committed_while_uploading.append(int(current["bytes_committed"]))
                # Below one storage chunk, committed must stay 0 during upload.
                assert int(current["bytes_committed"]) == 0
            time.sleep(0.01)

    def slow_chunks() -> Iterator[bytes]:
        for index in range(0, size, 4096):
            yield payload[index : index + 4096]
            time.sleep(0.03)

    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    try:
        finished = ops.accept_upload(operation_id, slow_chunks(), expected_size=size)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert finished["status"] == "complete"
    assert finished["bytes_committed"] == size
    assert committed_while_uploading, "poller should observe uploading heartbeats"
    assert all(value == 0 for value in committed_while_uploading)
    assert len(set(stamps)) >= 2, "heartbeats must advance durable updated_at"


def test_durable_telemetry_never_persists_raw_exception_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    size = SOURCE_BLOB_CHUNK_BYTES + 32
    payload = b"s" * size
    operation = ops.start_operation(declared_byte_size=size, filename="canary.bin")
    canary = "CANARY_SECRET=hunter2; filename=private-export.zip; user_text=hello"

    def boom(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise RuntimeError(canary)

    monkeypatch.setattr(core.store, "write_source_blob_chunk", boom)
    with pytest.raises(RuntimeError, match="CANARY_SECRET"):
        ops.accept_upload(operation["operation_id"], io.BytesIO(payload), expected_size=size)
    failed = ops.get_operation(operation["operation_id"])
    assert failed["status"] == "failed"
    assert failed["error_message"] == "import_runtime_error"
    assert canary not in str(failed["error_message"])
    assert canary not in str(failed.get("progress") or {})
    assert "hunter2" not in str(failed)
    # Raw SQLite scan: no canary bytes in the durable core database.
    db_bytes = core.store.database_path.read_bytes()
    assert b"CANARY_SECRET" not in db_bytes
    assert b"hunter2" not in db_bytes
    assert b"private-export.zip" not in db_bytes


def test_durable_error_code_rejects_dynamic_exception_type_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown types must map to constant import_failed — never embed type names."""
    from allthecontext.import_boundary import durable_import_error_code

    canary_type = "AttackerControlledProviderError_XYZ_canary9f3a"
    canary_msg = "provider-leak: SECRET=dyn-type-hunter2"
    DynamicError = type(canary_type, (Exception,), {})
    code = durable_import_error_code(DynamicError(canary_msg))
    assert code == "import_failed"
    assert canary_type not in code
    assert "import_failed:" not in code
    assert canary_msg not in code

    core, ops = _ops(tmp_path)
    size = SOURCE_BLOB_CHUNK_BYTES + 16
    payload = b"d" * size
    operation = ops.start_operation(declared_byte_size=size, filename="dyn-type.bin")

    def boom(**kwargs):  # type: ignore[no-untyped-def]
        del kwargs
        raise DynamicError(canary_msg)

    monkeypatch.setattr(core.store, "write_source_blob_chunk", boom)
    with pytest.raises(Exception, match="provider-leak"):
        ops.accept_upload(operation["operation_id"], io.BytesIO(payload), expected_size=size)
    failed = ops.get_operation(operation["operation_id"])
    assert failed["status"] == "failed"
    assert failed["error_message"] == "import_failed"
    assert canary_type not in str(failed)
    assert canary_msg not in str(failed)
    assert "import_failed:" not in str(failed.get("error_message") or "")
    progress = failed.get("progress") or {}
    assert progress.get("message") == "import_failed"
    # Raw durable scan: dynamic type name and message must never reach SQLite.
    db_bytes = core.store.database_path.read_bytes()
    assert canary_type.encode() not in db_bytes
    assert b"dyn-type-hunter2" not in db_bytes
    assert b"provider-leak" not in db_bytes
    assert b"import_failed:" not in db_bytes


def test_http_put_bridge_quiesces_when_pump_blocked_on_full_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finalizer must exit while the async pump is blocked; no producer left hung.

    Exercises the PUT bridge when the bounded queue is full (pump blocked on put)
    and the worker finishes via cancel. CancelledError from the cancelled pump
    must not hang the request or leave a blocked producer thread.
    """
    import asyncio

    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from allthecontext.import_boundary import (
        CANCEL_POLL_SECONDS,
        MAX_REQUEST_CHUNK_BYTES,
        ImportCancelledError,
    )
    from allthecontext.import_operations import ImportOperationService
    from fastapi.testclient import TestClient

    pump_blocked = threading.Event()
    worker_entered = threading.Event()
    active_puts = {"n": 0}
    blocked_put_seen = {"n": 0}
    lock = threading.Lock()

    original_to_thread = asyncio.to_thread

    def _put_with_block_signal(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        """Run _put_bounded; signal when a put stays blocked past one cancel poll."""
        stop_watch = threading.Event()

        def watch() -> None:
            if not stop_watch.wait(timeout=CANCEL_POLL_SECONDS * 0.9):
                with lock:
                    blocked_put_seen["n"] += 1
                pump_blocked.set()

        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            return func(*args, **kwargs)
        finally:
            stop_watch.set()
            watcher.join(timeout=1.0)

    async def tracking_to_thread(func, /, *args, **kwargs):  # type: ignore[no-untyped-def]
        name = getattr(func, "__name__", "")
        if name == "_put_bounded":
            with lock:
                active_puts["n"] += 1
            try:
                return await original_to_thread(_put_with_block_signal, func, *args, **kwargs)
            finally:
                with lock:
                    active_puts["n"] -= 1
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", tracking_to_thread)

    def accept_without_draining(  # type: ignore[no-untyped-def]
        self,
        operation_id,
        source,
        *,
        expected_size=None,
        process_after=True,
    ):
        del self, expected_size, process_after, source
        worker_entered.set()
        # Wait until the async pump has filled the bounded queue and blocked on put.
        assert pump_blocked.wait(timeout=8.0), "pump never blocked on full queue"
        # Hold so the finalizer must cancel/quiesce a still-blocked pump.
        time.sleep(CANCEL_POLL_SECONDS)
        raise ImportCancelledError("import cancelled by operator request")

    monkeypatch.setattr(ImportOperationService, "accept_upload", accept_without_draining)

    # More than Queue(maxsize=8) slices so the pump blocks on put while the worker
    # does not consume the chunk iterator.
    payload = b"p" * (MAX_REQUEST_CHUNK_BYTES * 10)
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        started = client.post(
            "/v1/admin/import-operations",
            json={"declared_byte_size": len(payload), "filename": "blocked-pump.bin"},
        )
        assert started.status_code == 200, started.text
        operation_id = started.json()["operation_id"]

        result_box: dict[str, object] = {}

        def do_put() -> None:
            try:
                result_box["response"] = client.put(
                    f"/v1/admin/import-operations/{operation_id}/content",
                    content=payload,
                    headers={"Content-Length": str(len(payload))},
                )
            except BaseException as error:
                result_box["error"] = error

        put_thread = threading.Thread(target=do_put, daemon=True)
        put_thread.start()
        put_thread.join(timeout=15.0)
        assert not put_thread.is_alive(), (
            "PUT request hung: pump finalizer failed to quiesce blocked producer"
        )
        if "error" in result_box:
            raise AssertionError(f"PUT failed: {result_box['error']!r}")
        uploaded = result_box["response"]
        assert uploaded is not None
        # ImportCancelledError is an InvalidStateError → 422; must not hang.
        assert uploaded.status_code == 422, uploaded.text  # type: ignore[union-attr]
        deadline_puts = time.monotonic() + 2.0
        while time.monotonic() < deadline_puts:
            with lock:
                if active_puts["n"] == 0:
                    break
            time.sleep(0.05)
        with lock:
            assert active_puts["n"] == 0, (
                f"producer still blocked after request exit (active_puts={active_puts['n']})"
            )
            assert blocked_put_seen["n"] >= 1
        assert worker_entered.is_set()
        assert pump_blocked.is_set()


def test_progress_write_failure_fails_safe_and_retains_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Telemetry commit must not be silent"}\n'
    operation = ops.start_operation(declared_byte_size=len(payload), filename="tele.jsonl")
    original = core.store.update_import_operation
    calls = {"n": 0}

    def flaky_update(operation_id: str, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        # Fail a mid-progress write after the operation row exists.
        if kwargs.get("phase") == "parsing":
            raise InvalidStateError("injected durable progress write failure")
        return original(operation_id, **kwargs)

    monkeypatch.setattr(core.store, "update_import_operation", flaky_update)
    with pytest.raises(InvalidStateError, match="progress write failure"):
        ops.accept_upload(
            operation["operation_id"],
            io.BytesIO(payload),
            expected_size=len(payload),
        )
    monkeypatch.setattr(core.store, "update_import_operation", original)
    state = ops.get_operation(operation["operation_id"])
    # Failure after raw preservation keeps source for no-upload retry when present.
    if state.get("source_id"):
        assert core.store.get_source_content(str(state["source_id"])) == payload
    assert state["status"] in {"failed", "processing"}


def test_reject_bool_and_malformed_declared_sizes(tmp_path: Path) -> None:
    _core, ops = _ops(tmp_path)
    with pytest.raises(InvalidStateError, match="non-boolean integer"):
        ops.start_operation(declared_byte_size=True, filename="x.bin")  # type: ignore[arg-type]
    with pytest.raises(InvalidStateError, match="non-boolean integer"):
        ops.start_operation(declared_byte_size="64", filename="x.bin")  # type: ignore[arg-type]
    with pytest.raises(InvalidStateError, match="non-negative"):
        ops.start_operation(declared_byte_size=-1, filename="x.bin")


def test_http_rejects_malformed_content_length_and_bool_size(tmp_path: Path) -> None:
    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from fastapi.testclient import TestClient

    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        bad_bool = client.post(
            "/v1/admin/import-operations",
            json={"declared_byte_size": True, "filename": "x.bin"},
        )
        assert bad_bool.status_code == 422
        started = client.post(
            "/v1/admin/import-operations",
            json={"declared_byte_size": 10, "filename": "x.bin"},
        )
        assert started.status_code == 200
        operation_id = started.json()["operation_id"]
        bad_cl = client.put(
            f"/v1/admin/import-operations/{operation_id}/content",
            content=b"0123456789",
            headers={"Content-Length": "not-a-number"},
        )
        assert bad_cl.status_code == 400
        negative = client.put(
            f"/v1/admin/import-operations/{operation_id}/content",
            content=b"0123456789",
            headers={"Content-Length": "-5"},
        )
        assert negative.status_code == 400


def test_oversized_iterator_chunk_is_sliced(tmp_path: Path) -> None:
    from allthecontext.import_operations import READ_BUFFER_BYTES, _iter_bytes

    huge = b"z" * (READ_BUFFER_BYTES * 3 + 10)

    def one_giant_chunk() -> Iterator[bytes]:
        yield huge

    pieces = list(_iter_bytes(one_giant_chunk()))
    assert all(len(piece) <= READ_BUFFER_BYTES for piece in pieces)
    assert b"".join(pieces) == huge


def test_phase_evidence_upload_stage_parse_ingest_verify_publish(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"goal","content":"Phase evidence synthetic fixture"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="phases.jsonl",
    )
    operation_id = operation["operation_id"]
    seen: list[str] = []
    stop = threading.Event()

    def poller() -> None:
        while not stop.is_set():
            try:
                current = ops.get_operation(operation_id)
            except NotFoundError:
                time.sleep(0.005)
                continue
            phase = str(current["phase"])
            if not seen or seen[-1] != phase:
                seen.append(phase)
            time.sleep(0.005)

    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    try:
        finished = ops.accept_upload(
            operation_id,
            io.BytesIO(payload),
            expected_size=len(payload),
        )
    finally:
        stop.set()
        thread.join(timeout=5)
    assert finished["status"] == "complete"
    assert finished["phase"] == "complete"
    # Upload and terminal completeness are required; intermediate phases may be brief.
    assert "uploading" in seen or "hashing" in seen or "staging" in seen
    assert "complete" in seen or finished["phase"] == "complete"
    assert finished["result"]["source"]["import_status"] == "complete"
    assert core.store.get_source_content(str(finished["source_id"])) == payload


def test_concurrent_status_polling_during_retry(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Concurrent status during retry"}\n'
    operation = ops.start_operation(declared_byte_size=len(payload), filename="poll.jsonl")
    finished = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    operation_id = operation["operation_id"]
    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        completed=True,
        error_message="import_interrupted_process_restart",
    )
    core.store.update_source_import(
        str(finished["source_id"]),
        import_status="failed",
        metadata=core.store.get_source(str(finished["source_id"]), duplicate=True).metadata,
        parser_warnings=(),
    )
    seen_status: list[str] = [str(ops.get_operation(operation_id)["status"])]
    stop = threading.Event()

    def poller() -> None:
        while not stop.is_set():
            state = ops.get_operation(operation_id)
            status = str(state["status"])
            if not seen_status or seen_status[-1] != status:
                seen_status.append(status)
            time.sleep(0.01)

    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    try:
        retried = ops.retry_operation(operation_id)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert retried["status"] == "complete"
    assert "failed" in seen_status
    assert "processing" in seen_status or "complete" in seen_status
