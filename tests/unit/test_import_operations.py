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
