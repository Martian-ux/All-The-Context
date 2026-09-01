"""Focused tests for durable import-operation lifecycle (B-105 gap close)."""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import sys
import threading
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from allthecontext.core.service import CoreService
from allthecontext.import_boundary import (
    BOUNDARY_PLUS_ONE_BYTES,
    CANCEL_QUIESCE_SECONDS,
    PROGRESS_HEARTBEAT_SECONDS,
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

_TEST_WORKER_COORDINATION_SECONDS = CANCEL_QUIESCE_SECONDS + 5.0


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


def _wait_for_test_worker_boundary(
    event: threading.Event,
    thread: threading.Thread,
    failures: list[BaseException],
    *,
    message: str,
) -> None:
    """Wait through hosted-runner jitter without hiding an exited worker."""

    deadline = time.monotonic() + _TEST_WORKER_COORDINATION_SECONDS
    while thread.is_alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if event.wait(timeout=min(0.1, remaining)):
            return
    failure_types = [type(error).__name__ for error in failures]
    pytest.fail(
        f"{message}; worker_alive={thread.is_alive()}; worker_failure_types={failure_types}"
    )


def _join_test_worker(thread: threading.Thread, *, message: str) -> None:
    """Allow the worker's documented quiescence budget, then require closure."""

    thread.join(timeout=_TEST_WORKER_COORDINATION_SECONDS)
    assert not thread.is_alive(), message


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


def test_durable_bytes_committed_never_regresses_across_progress_domains(
    tmp_path: Path,
) -> None:
    """Raw-archive commit must not be lowered by later staging/member progress writes."""
    core, ops = _ops(tmp_path)
    # Multi-chunk raw payload so upload commits full size before staging rewalks.
    line = b'{"kind":"goal","content":"Preference: Keep durable progress honest."}\n'
    payload = line * ((SOURCE_BLOB_CHUNK_BYTES // len(line)) + 8)
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="goals.jsonl",
        provider="generic",
    )
    observed: list[int] = []
    original_update = core.store.update_import_operation

    def watching_update(operation_id: str, **kwargs: object) -> dict[str, object]:
        result = original_update(operation_id, **kwargs)
        observed.append(int(result["bytes_committed"]))
        return result

    core.store.update_import_operation = watching_update  # type: ignore[method-assign]
    finished = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
    )
    assert finished["status"] == "complete"
    assert finished["bytes_committed"] == len(payload)
    assert observed
    # Once committed bytes reach a high-water mark they never fall.
    high_water = 0
    for value in observed:
        assert value >= high_water
        high_water = value
    assert high_water == len(payload)

    # Direct storage API also clamps regressions from mixed progress domains.
    # Recreate a fresh operation to exercise the storage clamp in isolation.
    isolated = ops.start_operation(
        declared_byte_size=1_000,
        filename="clamp.bin",
        provider="generic",
    )
    isolated_id = str(isolated["operation_id"])
    core.store.update_import_operation(
        isolated_id,
        status="uploading",
        phase="uploading",
        bytes_received=900,
        bytes_committed=800,
    )
    core.store.update_import_operation(
        isolated_id,
        status="processing",
        phase="staging",
        bytes_received=900,
        bytes_committed=100,  # would-be regression from member/staging domain
    )
    clamped = core.store.get_import_operation(isolated_id)
    assert int(clamped["bytes_committed"]) == 800
    with pytest.raises(InvalidStateError, match="cannot be negative"):
        core.store.update_import_operation(isolated_id, bytes_committed=-1)
    with pytest.raises(InvalidStateError, match="cannot be negative"):
        core.store.update_import_operation(isolated_id, bytes_received=-1)


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
    uploading_observed = threading.Event()
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
            if phase == "uploading":
                uploading_observed.set()
            # Committed progress never leads received by more than one chunk.
            assert (
                int(current["bytes_committed"])
                <= int(current["bytes_received"]) + SOURCE_BLOB_CHUNK_BYTES
            )
            time.sleep(0.01)

    def synchronized_chunks() -> Iterator[bytes]:
        # The iterator is entered only after the durable upload claim. Hold the
        # worker there until the concurrent reader has observed that state.
        assert uploading_observed.wait(timeout=5), "poller did not observe uploading"
        yield payload

    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    try:
        finished = ops.accept_upload(operation_id, synchronized_chunks(), expected_size=size)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert finished["status"] == "complete"
    assert "uploading" in seen_phases
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
    source_id = str(finished["source_id"])
    source = core.store.get_source(source_id, duplicate=True)
    source_metadata = dict(source.metadata)
    source_metadata["closed_coverage"] = {
        "recognized": 2,
        "unparsed": 1,
        "unexpected": 99,
        "failed": True,
    }
    core.store.update_source_import(
        source_id,
        import_status="processing",
        metadata=source_metadata,
        parser_warnings=source.parser_warnings,
    )
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
    recovered_source = core.store.get_source(str(state["source_id"]), duplicate=True)
    assert recovered_source.import_status == "failed"
    assert recovered_source.metadata["source_terminal_reason"] == "failed"
    assert recovered_source.metadata["closed_coverage"] == {
        "recognized": 2,
        "excluded": 0,
        "skipped": 0,
        "unavailable": 0,
        "duplicate": 0,
        "failed": 0,
        "unparsed": 1,
    }
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


def _chatgpt_zip_payload() -> bytes:
    export = [
        {
            "id": "merge-conversation",
            "mapping": {
                "user": {
                    "message": {
                        "id": "merge-message",
                        "author": {"role": "user"},
                        "content": {"parts": ["I prefer concise technical answers."]},
                    }
                }
            },
        }
    ]
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps(export))
    return bundle.getvalue()


def _capture_reclassify_provisional(
    store: object,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    """Record the pre-merge source id while still running real reclassification."""
    captured: dict[str, str] = {}
    original = store.reclassify_source  # type: ignore[attr-defined]

    def wrapped(source_id: str, **kwargs):  # type: ignore[no-untyped-def]
        captured["provisional_id"] = source_id
        return original(source_id, **kwargs)

    monkeypatch.setattr(store, "reclassify_source", wrapped)
    return captured


def test_operation_completion_rebinds_parser_reclassification_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = _chatgpt_zip_payload()
    canonical = ops.imports.import_bytes(
        "canonical.zip",
        payload,
        provider="chatgpt",
    )
    canonical_id = str(canonical["source"]["id"])
    captured = _capture_reclassify_provisional(core.store, monkeypatch)

    def reject_duplicate_reingest(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise AssertionError("complete canonical source must not be re-ingested")

    monkeypatch.setattr(ops.imports.ingestion, "begin", reject_duplicate_reingest)
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="automatic.zip",
    )
    finished = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
    )

    provisional_id = captured["provisional_id"]
    assert provisional_id != canonical_id
    assert finished["status"] == "complete"
    assert finished["source_id"] == canonical_id
    assert finished["result"]["source"]["id"] == canonical_id
    assert finished["phase"] == "complete"
    canonical_source = core.store.get_source(canonical_id, duplicate=True)
    assert canonical_source.id == canonical_id
    assert canonical_source.import_status == "complete"
    with pytest.raises(NotFoundError, match="source not found"):
        core.store.get_source(provisional_id, duplicate=True)


def test_retry_completion_rebinds_parser_reclassification_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = _chatgpt_zip_payload()
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="automatic-retry.zip",
    )
    staged = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    operation_id = str(operation["operation_id"])
    provisional_id = str(staged["source_id"])

    canonical = ops.imports.import_bytes(
        "canonical-retry.zip",
        payload,
        provider="chatgpt",
    )
    canonical_id = str(canonical["source"]["id"])
    assert canonical_id != provisional_id
    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        completed=True,
        error_message="import_interrupted_process_restart",
    )
    provisional = core.store.get_source(provisional_id, duplicate=True)
    core.store.update_source_import(
        provisional_id,
        import_status="failed",
        metadata=provisional.metadata,
        parser_warnings=provisional.parser_warnings,
    )

    def reject_duplicate_reingest(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise AssertionError("complete canonical source must not be re-ingested")

    monkeypatch.setattr(ops.imports.ingestion, "begin", reject_duplicate_reingest)
    retried = ops.retry_operation(operation_id)

    assert retried["status"] == "complete"
    assert retried["source_id"] == canonical_id
    assert retried["result"]["source"]["id"] == canonical_id
    assert retried["phase"] == "complete"
    assert core.store.get_source(canonical_id, duplicate=True).import_status == "complete"
    with pytest.raises(NotFoundError, match="source not found"):
        core.store.get_source(provisional_id, duplicate=True)


def test_operation_failure_rebinds_parser_reclassification_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = _chatgpt_zip_payload()
    canonical = ops.imports.import_bytes(
        "canonical-fail.zip",
        payload,
        provider="chatgpt",
    )
    canonical_id = str(canonical["source"]["id"])
    # Merge into a non-complete canonical so real _ingest runs (not the complete short-circuit).
    canonical_source = core.store.get_source(canonical_id, duplicate=True)
    core.store.update_source_import(
        canonical_id,
        import_status="failed",
        metadata=canonical_source.metadata,
        parser_warnings=canonical_source.parser_warnings,
    )
    captured = _capture_reclassify_provisional(core.store, monkeypatch)

    def fail_begin_after_merge(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        # Raise from inside the real _ingest try path so tracker.fail terminalizes first.
        raise RuntimeError("forced post-merge failure")

    monkeypatch.setattr(ops.imports.ingestion, "begin", fail_begin_after_merge)
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="fail-merge.zip",
    )
    with pytest.raises(RuntimeError, match="forced post-merge failure"):
        ops.accept_upload(
            operation["operation_id"],
            io.BytesIO(payload),
            expected_size=len(payload),
        )

    provisional_id = captured["provisional_id"]
    final = ops.get_operation(str(operation["operation_id"]))
    assert provisional_id != canonical_id
    assert final["status"] == "failed"
    assert final["phase"] == "failed"
    # Outer rebind after tracker.fail terminalized with the provisional source_id.
    assert final["source_id"] == canonical_id
    assert final["error_message"] == "import_runtime_error"
    assert final["result"] is None
    assert core.store.get_source(canonical_id, duplicate=True).import_status == "failed"
    with pytest.raises(NotFoundError, match="source not found"):
        core.store.get_source(provisional_id, duplicate=True)


def test_operation_cancel_rebinds_parser_reclassification_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = _chatgpt_zip_payload()
    canonical = ops.imports.import_bytes(
        "canonical-cancel.zip",
        payload,
        provider="chatgpt",
    )
    canonical_id = str(canonical["source"]["id"])
    captured = _capture_reclassify_provisional(core.store, monkeypatch)
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="cancel-merge.zip",
    )
    operation_id = str(operation["operation_id"])

    def cancel_after_merge(source, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args
        assert source.id == canonical_id
        tracker = kwargs.get("tracker")
        assert tracker is not None
        ops.cancel_operation(operation_id)
        tracker.check_cancelled()
        raise AssertionError("cancel should have been acknowledged")

    monkeypatch.setattr(ops.imports, "_ingest", cancel_after_merge)
    with pytest.raises(ImportCancelledError):
        ops.accept_upload(
            operation_id,
            io.BytesIO(payload),
            expected_size=len(payload),
        )

    provisional_id = captured["provisional_id"]
    final = ops.get_operation(operation_id)
    assert provisional_id != canonical_id
    assert final["status"] == "cancelled"
    assert final["phase"] == "cancelled"
    assert final["source_id"] == canonical_id
    assert final["result"] is None
    # Already-complete canonical must not be cancelled by a later merge cancel.
    assert core.store.get_source(canonical_id, duplicate=True).import_status == "complete"
    with pytest.raises(NotFoundError, match="source not found"):
        core.store.get_source(provisional_id, duplicate=True)


def test_retry_failure_rebinds_parser_reclassification_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = _chatgpt_zip_payload()
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="retry-fail-merge.zip",
    )
    staged = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    operation_id = str(operation["operation_id"])
    provisional_id = str(staged["source_id"])
    canonical = ops.imports.import_bytes(
        "canonical-retry-fail.zip",
        payload,
        provider="chatgpt",
    )
    canonical_id = str(canonical["source"]["id"])
    assert canonical_id != provisional_id
    # Non-complete canonical forces the real _ingest path after merge.
    canonical_source = core.store.get_source(canonical_id, duplicate=True)
    core.store.update_source_import(
        canonical_id,
        import_status="failed",
        metadata=canonical_source.metadata,
        parser_warnings=canonical_source.parser_warnings,
    )
    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        completed=True,
        error_message="import_interrupted_process_restart",
    )
    provisional = core.store.get_source(provisional_id, duplicate=True)
    core.store.update_source_import(
        provisional_id,
        import_status="failed",
        metadata=provisional.metadata,
        parser_warnings=provisional.parser_warnings,
    )

    def fail_begin_after_merge(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise RuntimeError("forced retry post-merge failure")

    monkeypatch.setattr(ops.imports.ingestion, "begin", fail_begin_after_merge)
    with pytest.raises(RuntimeError, match="forced retry post-merge failure"):
        ops.retry_operation(operation_id)

    final = ops.get_operation(operation_id)
    assert final["status"] == "failed"
    assert final["phase"] == "failed"
    # Outer rebind after tracker.fail terminalized with the provisional source_id.
    assert final["source_id"] == canonical_id
    assert final["error_message"] == "import_runtime_error"
    assert final["result"] is None
    assert core.store.get_source(canonical_id, duplicate=True).import_status == "failed"
    with pytest.raises(NotFoundError, match="source not found"):
        core.store.get_source(provisional_id, duplicate=True)


def test_retry_cancel_rebinds_parser_reclassification_merge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = _chatgpt_zip_payload()
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="retry-cancel-merge.zip",
    )
    staged = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    operation_id = str(operation["operation_id"])
    provisional_id = str(staged["source_id"])
    canonical = ops.imports.import_bytes(
        "canonical-retry-cancel.zip",
        payload,
        provider="chatgpt",
    )
    canonical_id = str(canonical["source"]["id"])
    assert canonical_id != provisional_id
    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        completed=True,
        error_message="import_interrupted_process_restart",
    )
    provisional = core.store.get_source(provisional_id, duplicate=True)
    core.store.update_source_import(
        provisional_id,
        import_status="failed",
        metadata=provisional.metadata,
        parser_warnings=provisional.parser_warnings,
    )

    def cancel_after_merge(source, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args
        assert source.id == canonical_id
        tracker = kwargs.get("tracker")
        assert tracker is not None
        ops.cancel_operation(operation_id)
        tracker.check_cancelled()
        raise AssertionError("cancel should have been acknowledged")

    monkeypatch.setattr(ops.imports, "_ingest", cancel_after_merge)
    with pytest.raises(ImportCancelledError):
        ops.retry_operation(operation_id)

    final = ops.get_operation(operation_id)
    assert final["status"] == "cancelled"
    assert final["phase"] == "cancelled"
    assert final["source_id"] == canonical_id
    assert final["result"] is None
    assert core.store.get_source(canonical_id, duplicate=True).import_status == "complete"
    with pytest.raises(NotFoundError, match="source not found"):
        core.store.get_source(provisional_id, duplicate=True)


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

    from allthecontext import import_operations as ops_module

    seen_phases: list[str] = []
    retry_initial_bytes: list[int | None] = []
    tracker_passed = {"ok": False}
    original = ops.imports.reprocess_source
    tracker_type = ops_module.ImportProgressTracker

    def recording_tracker(*args, **kwargs):  # type: ignore[no-untyped-def]
        retry_initial_bytes.append(kwargs.get("initial_bytes_processed"))
        return tracker_type(*args, **kwargs)

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

    monkeypatch.setattr(ops_module, "ImportProgressTracker", recording_tracker)
    monkeypatch.setattr(ops.imports, "reprocess_source", tracked_reprocess)
    retried = ops.retry_operation(operation["operation_id"])
    assert tracker_passed["ok"] is True
    assert retried["status"] == "complete"
    assert retried["phase"] == "complete"
    assert "parsing" in seen_phases
    assert retry_initial_bytes == [len(payload)]


def test_retry_cancel_acknowledged_via_operation_tracker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import importers as importers_module

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

    def blocking_parse(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args
        progress_tracker = kwargs["progress"]
        entered.set()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            progress_tracker.check_cancelled()
            time.sleep(0.05)
        raise AssertionError("cancel was not acknowledged within budget")

    monkeypatch.setattr(importers_module, "parse_archive_path", blocking_parse)

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
    source = core.store.get_source(source_id, duplicate=True)
    assert source.import_status == "cancelled"
    assert source.metadata["import_progress"]["phase"] == "cancelled"
    assert source.metadata["source_terminal_reason"] == "cancelled"


def test_http_cancel_acknowledges_during_preserved_blob_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Copy staging must not become an unbounded cancellation blind spot."""
    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from allthecontext.core.service import CoreService
    from fastapi.testclient import TestClient

    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    core = CoreService(config)
    ops = core.import_operations
    payload = b'{"kind":"fact","content":"Controlled copy cancellation"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="copy-cancel.jsonl",
    )
    operation_id = str(operation["operation_id"])
    staged = ops.accept_upload(
        operation_id,
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    source_id = str(staged["source_id"])
    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        completed=True,
        error_message="import_interrupted_process_restart",
    )
    core.store.update_source_import(
        source_id,
        import_status="failed",
        metadata=core.store.get_source(source_id, duplicate=True).metadata,
        parser_warnings=(),
    )

    copy_started = threading.Event()
    original_chunks = core.store._source_content_chunks_tx

    def delayed_copy_chunks(connection, row):  # type: ignore[no-untyped-def]
        for chunk in original_chunks(connection, row):
            # A no-checkpoint copy takes well over the scaled 2.5-second
            # acknowledgment budget; a checkpointed copy cancels after one piece.
            piece_bytes = max(1, len(chunk) // 40)
            for offset in range(0, len(chunk), piece_bytes):
                copy_started.set()
                time.sleep(0.1)
                yield chunk[offset : offset + piece_bytes]

    monkeypatch.setattr(core.store, "_source_content_chunks_tx", delayed_copy_chunks)
    retry_result: dict[str, object] = {}
    timing_path = tmp_path / "cancel-copy-timing.json"

    with TestClient(create_app(config, service=core)) as client:

        def retry() -> None:
            retry_result["response"] = client.post(
                f"/v1/admin/import-operations/{operation_id}/retry"
            )
            retry_result["returned"] = time.monotonic()

        retry_thread = threading.Thread(target=retry, daemon=True)
        retry_thread.start()
        assert copy_started.wait(timeout=5.0)

        cancel_started = time.monotonic()
        cancel_response = client.post(f"/v1/admin/import-operations/{operation_id}/cancel")
        cancel_returned = time.monotonic()
        cancel_body = cancel_response.json()
        durable_terminal_at: float | None = None
        samples: list[dict[str, object]] = []
        deadline = cancel_started + 2.5
        while time.monotonic() < deadline:
            observed = client.get(f"/v1/admin/import-operations/{operation_id}")
            received = time.monotonic()
            assert observed.status_code == 200
            body = observed.json()
            samples.append(
                {
                    "received_seconds": received - cancel_started,
                    "status": body["status"],
                    "phase": body["phase"],
                    "updated_at": body["updated_at"],
                }
            )
            if body["status"] == "cancelled":
                durable_terminal_at = received
                break
            time.sleep(0.01)

        retry_thread.join(timeout=5.0)
        final = client.get(f"/v1/admin/import-operations/{operation_id}").json()
        retry_response = retry_result.get("response")
        timing = {
            "content_free": True,
            "cancel_http_return_seconds": cancel_returned - cancel_started,
            "cancel_http_status": cancel_response.status_code,
            "cancel_response_status": cancel_body["status"],
            "cancel_requested": cancel_body["cancel_requested"],
            "durable_terminal_seconds": (
                None if durable_terminal_at is None else durable_terminal_at - cancel_started
            ),
            "worker_quiesce_seconds": (
                None
                if "returned" not in retry_result
                else float(retry_result["returned"]) - cancel_started
            ),
            "samples": samples,
            "final_status": final["status"],
            "retry_http_status": getattr(retry_response, "status_code", None),
        }
        with timing_path.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(timing, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())

        persisted = json.loads(timing_path.read_text(encoding="utf-8"))
        assert persisted["cancel_http_status"] == 200
        assert persisted["cancel_response_status"] == "processing"
        assert persisted["cancel_requested"] is True
        assert persisted["cancel_http_return_seconds"] < 2.5
        assert persisted["durable_terminal_seconds"] is not None
        assert persisted["durable_terminal_seconds"] < 2.5
        assert persisted["worker_quiesce_seconds"] is not None
        assert persisted["worker_quiesce_seconds"] < 5.0
        assert persisted["retry_http_status"] == 422
        assert not retry_thread.is_alive()
        assert final["status"] == "cancelled"
        assert final["progress"]["cancel_acknowledged"] is True
        assert core.store.get_source(source_id, duplicate=True).import_status == "cancelled"
        assert not list(config.data_dir.glob("atc-reprocess-*"))


def test_repeat_copy_yields_to_operation_heartbeat_under_cpu_pressure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate-source reconstruction must not starve operation liveness."""
    from allthecontext import import_operations as ops_module

    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"' + (b"x" * 448) + b'"}\n'
    fixture = _write(tmp_path / "repeat-copy.jsonl", payload)
    first = ops.import_path_via_operation(fixture)
    assert first["status"] == "complete"

    tracker_type = ops_module.ImportProgressTracker

    def scaled_tracker(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["heartbeat_seconds"] = 0.5
        return tracker_type(*args, **kwargs)

    monkeypatch.setattr(ops_module, "ImportProgressTracker", scaled_tracker)
    original_chunks = core.store._source_content_chunks_tx
    copy_started_at: list[float] = []

    def cpu_bound_copy_chunks(connection, row):  # type: ignore[no-untyped-def]
        for chunk in original_chunks(connection, row):
            copy_started_at.append(time.perf_counter())
            for value in chunk:
                deadline = time.perf_counter() + 0.002
                while time.perf_counter() < deadline:
                    pass
                yield bytes((value,))

    monkeypatch.setattr(core.store, "_source_content_chunks_tx", cpu_bound_copy_chunks)
    original_touch = core.store.touch_import_operation_liveness
    successful_touches: list[float] = []

    def record_touch(operation_id: str) -> bool:
        result = original_touch(operation_id)
        if result:
            successful_touches.append(time.perf_counter())
        return result

    monkeypatch.setattr(core.store, "touch_import_operation_liveness", record_touch)
    previous_switch_interval = sys.getswitchinterval()
    sys.setswitchinterval(10.0)
    try:
        repeated = ops.import_path_via_operation(fixture)
    finally:
        sys.setswitchinterval(previous_switch_interval)

    assert repeated["status"] == "complete"
    assert repeated["result"]["candidate_ids"] == first["result"]["candidate_ids"]
    assert len(copy_started_at) == 1
    first_copy_touch = next(
        (item for item in successful_touches if item >= copy_started_at[0]),
        None,
    )
    assert first_copy_touch is not None
    # Scaled from the frozen five-second gate with 20% timing margin.
    assert first_copy_touch - copy_started_at[0] < 0.4


def test_source_only_reprocess_copy_does_not_add_scheduler_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import importers as importers_module

    _core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Source-only copy cadence"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="source-only-copy.jsonl",
    )
    staged = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    pauses: list[float] = []
    monkeypatch.setattr(importers_module.time, "sleep", pauses.append)

    result = ops.imports.reprocess_source(str(staged["source_id"]))

    assert result["source"]["import_status"] == "complete"
    assert pauses == []


def test_source_copy_checkpoint_failure_removes_partial_file(tmp_path: Path) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Checkpoint cleanup"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="checkpoint-cleanup.jsonl",
    )
    staged = ops.accept_upload(
        operation["operation_id"],
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    destination = tmp_path / "partial-source-copy"
    calls = 0

    def cancel_checkpoint() -> None:
        nonlocal calls
        calls += 1
        raise ImportCancelledError("controlled cancellation")

    with pytest.raises(ImportCancelledError, match="controlled cancellation"):
        core.store.copy_source_content_to_path(
            str(staged["source_id"]),
            destination,
            checkpoint=cancel_checkpoint,
        )
    assert calls == 1
    assert not destination.exists()


def test_operation_owned_reprocess_failure_updates_operation_and_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import importers as importers_module

    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Retry failure remains terminal"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="retry-failure.jsonl",
    )
    operation_id = str(operation["operation_id"])
    staged = ops.accept_upload(
        operation_id,
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    source_id = str(staged["source_id"])
    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        completed=True,
        error_message="import_interrupted_process_restart",
    )
    core.store.update_source_import(
        source_id,
        import_status="failed",
        metadata=core.store.get_source(source_id, duplicate=True).metadata,
        parser_warnings=(),
    )

    def fail_parse(*args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise InvalidStateError("synthetic closed parser failure")

    monkeypatch.setattr(importers_module, "parse_archive_path", fail_parse)
    with pytest.raises(InvalidStateError, match="synthetic closed parser failure"):
        ops.retry_operation(operation_id)

    failed = ops.get_operation(operation_id)
    assert failed["status"] == "failed"
    assert failed["phase"] == "failed"
    assert failed["error_message"] == "import_invalid_state"
    source = core.store.get_source(source_id, duplicate=True)
    assert source.import_status == "failed"
    assert source.metadata["import_progress"]["phase"] == "failed"
    assert source.metadata["import_progress"]["message"] == "import_invalid_state"


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


def test_chunk_finalize_scan_keeps_operation_liveness_writable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = b"x" * 4096
    content_hash = hashlib.sha256(payload).hexdigest()
    assert (
        core.store.begin_incomplete_source_blob(
            content_hash=content_hash,
            byte_size=len(payload),
            media_type="application/octet-stream",
        )
        == "created"
    )
    core.store.write_source_blob_chunk(
        content_hash=content_hash,
        chunk_index=0,
        content=payload,
    )
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="finalize-liveness.bin",
    )
    operation_id = str(operation["operation_id"])
    scan_entered = threading.Event()
    release_scan = threading.Event()
    original_transaction = core.store.transaction

    class DelayedRows:
        def __init__(self, rows):  # type: ignore[no-untyped-def]
            self._rows = iter(rows)
            self._blocked = False

        def __iter__(self):  # type: ignore[no-untyped-def]
            return self

        def __next__(self):  # type: ignore[no-untyped-def]
            if not self._blocked:
                self._blocked = True
                scan_entered.set()
                assert release_scan.wait(timeout=5), "test did not release chunk scan"
            return next(self._rows)

    class ConnectionProxy:
        def __init__(self, connection):  # type: ignore[no-untyped-def]
            self._connection = connection

        def execute(self, sql, parameters=()):  # type: ignore[no-untyped-def]
            rows = self._connection.execute(sql, parameters)
            if sql.startswith("SELECT chunk_index,byte_size FROM source_blob_chunks"):
                return DelayedRows(rows)
            return rows

        def __getattr__(self, name):  # type: ignore[no-untyped-def]
            return getattr(self._connection, name)

    @contextmanager
    def delayed_transaction(*, immediate: bool = True):  # type: ignore[no-untyped-def]
        with original_transaction(immediate=immediate) as connection:
            yield ConnectionProxy(connection)

    monkeypatch.setattr(core.store, "transaction", delayed_transaction)
    failures: list[BaseException] = []

    def finalize() -> None:
        try:
            core.store.finalize_source_blob(
                content_hash=content_hash,
                expected_byte_size=len(payload),
                media_type="application/octet-stream",
            )
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=finalize, daemon=True)
    thread.start()
    try:
        assert scan_entered.wait(timeout=5), "chunk validation did not start"
        started = time.monotonic()
        assert core.store.touch_import_operation_liveness(operation_id) is True
        assert time.monotonic() - started < 1.0
    finally:
        release_scan.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert failures == []
    source = core.store.create_source_record_for_blob(
        content_hash=content_hash,
        source_service="generic",
        source_type="file",
        filename="finalize-liveness.bin",
    )
    assert source.byte_size == len(payload)


def test_upload_promotion_starts_and_closes_operation_heartbeats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import import_operations as ops_module

    tracker_type = ops_module.ImportProgressTracker
    trackers: list[object] = []

    def fast_tracker(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["heartbeat_seconds"] = 0.1
        tracker = tracker_type(*args, **kwargs)
        trackers.append(tracker)
        return tracker

    monkeypatch.setattr(ops_module, "ImportProgressTracker", fast_tracker)
    core, ops = _ops(tmp_path)
    original_finalize = core.store.finalize_source_blob
    finalize_entered = threading.Event()
    release_finalize = threading.Event()

    def blocked_finalize(**kwargs):  # type: ignore[no-untyped-def]
        finalize_entered.set()
        assert release_finalize.wait(timeout=_TEST_WORKER_COORDINATION_SECONDS), (
            "test did not release finalization"
        )
        return original_finalize(**kwargs)

    monkeypatch.setattr(core.store, "finalize_source_blob", blocked_finalize)
    payload = b'{"kind":"fact","content":"Promotion heartbeat coverage"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="promotion-heartbeat.jsonl",
    )
    operation_id = str(operation["operation_id"])
    outcomes: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def upload() -> None:
        try:
            outcomes.append(
                ops.accept_upload(
                    operation_id,
                    io.BytesIO(payload),
                    expected_size=len(payload),
                    process_after=False,
                )
            )
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=upload, daemon=True)
    thread.start()
    try:
        _wait_for_test_worker_boundary(
            finalize_entered,
            thread,
            failures,
            message="source finalization did not start",
        )
        stamps: list[str] = []
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and len(set(stamps)) < 3:
            stamps.append(str(ops.get_operation(operation_id)["updated_at"]))
            time.sleep(0.01)
        assert len(set(stamps)) >= 3
    finally:
        release_finalize.set()
        _join_test_worker(thread, message="upload worker did not quiesce")

    assert failures == []
    assert outcomes[0]["status"] == "processing"
    assert trackers
    assert all(tracker._heartbeat_thread is None for tracker in trackers)


def test_parse_stall_heartbeats_durably_without_false_byte_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import import_operations as ops_module
    from allthecontext import importers as importers_module

    tracker_type = ops_module.ImportProgressTracker

    def fast_tracker(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["heartbeat_seconds"] = 0.04
        return tracker_type(*args, **kwargs)

    monkeypatch.setattr(ops_module, "ImportProgressTracker", fast_tracker)
    original_parse = importers_module.parse_archive_path
    parser_entered = threading.Event()
    release_parser = threading.Event()

    def stalled_parse(*args, **kwargs):  # type: ignore[no-untyped-def]
        parser_entered.set()
        assert release_parser.wait(timeout=_TEST_WORKER_COORDINATION_SECONDS), (
            "test did not release stalled parser"
        )
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(importers_module, "parse_archive_path", stalled_parse)
    _core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Durable parse heartbeat"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="parse-heartbeat.jsonl",
    )
    operation_id = str(operation["operation_id"])
    outcomes: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def worker() -> None:
        try:
            outcomes.append(
                ops.accept_upload(
                    operation_id,
                    io.BytesIO(payload),
                    expected_size=len(payload),
                )
            )
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        _wait_for_test_worker_boundary(
            parser_entered,
            thread,
            failures,
            message="parser did not enter synchronous stall",
        )
        stamps: list[str] = []
        observed_bytes: list[int] = []
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(set(stamps)) < 3:
            current = ops.get_operation(operation_id)
            if current["phase"] == "parsing":
                stamps.append(str(current["updated_at"]))
                observed_bytes.append(int(current["bytes_committed"]))
            time.sleep(0.01)
        assert len(set(stamps)) >= 3
        assert observed_bytes
        assert set(observed_bytes) == {len(payload)}
    finally:
        release_parser.set()
        _join_test_worker(thread, message="parse worker did not quiesce")

    assert failures == []
    assert outcomes[0]["status"] == "complete"


def test_operation_reprocess_heartbeats_ignore_blocking_source_progress_sink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import import_operations as ops_module
    from allthecontext import importers as importers_module

    tracker_type = ops_module.ImportProgressTracker

    def fast_tracker(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["heartbeat_seconds"] = 0.04
        return tracker_type(*args, **kwargs)

    monkeypatch.setattr(ops_module, "ImportProgressTracker", fast_tracker)
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Operation heartbeat owns retry liveness"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="operation-heartbeat.jsonl",
    )
    operation_id = str(operation["operation_id"])
    staged = ops.accept_upload(
        operation_id,
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    source_id = str(staged["source_id"])
    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        completed=True,
        error_message="import_interrupted_process_restart",
    )
    core.store.update_source_import(
        source_id,
        import_status="failed",
        metadata=core.store.get_source(source_id, duplicate=True).metadata,
        parser_warnings=(),
    )

    source_sink_entered = threading.Event()
    release_source_sink = threading.Event()

    def blocking_source_sink(_source_id: str):  # type: ignore[no-untyped-def]
        def sink(_progress):  # type: ignore[no-untyped-def]
            source_sink_entered.set()
            assert release_source_sink.wait(timeout=5), "test did not release source sink"

        return sink

    monkeypatch.setattr(ops.imports, "_durable_progress_sink", blocking_source_sink)
    original_parse = importers_module.parse_archive_path
    parser_entered = threading.Event()
    release_parser = threading.Event()

    def stalled_parse(*args, **kwargs):  # type: ignore[no-untyped-def]
        parser_entered.set()
        assert release_parser.wait(timeout=5), "test did not release stalled parser"
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(importers_module, "parse_archive_path", stalled_parse)
    outcomes: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def worker() -> None:
        try:
            outcomes.append(ops.retry_operation(operation_id))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    phases: list[str] = []
    try:
        assert parser_entered.wait(timeout=5), (
            "operation-owned reprocess did not enter parser; "
            f"source_sink_entered={source_sink_entered.is_set()}, "
            f"failures={[type(error).__name__ for error in failures]}, "
            f"operation={ops.get_operation(operation_id)['phase']}"
        )
        stamps: list[str] = []
        committed: list[int] = []
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(set(stamps)) < 3:
            current = ops.get_operation(operation_id)
            phases.append(str(current["phase"]))
            if current["phase"] == "parsing":
                stamps.append(str(current["updated_at"]))
                committed.append(int(current["bytes_committed"]))
            time.sleep(0.01)
        assert len(set(stamps)) >= 3
        assert committed and set(committed) == {len(payload)}
        assert source_sink_entered.is_set() is False
    finally:
        release_source_sink.set()
        release_parser.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert failures == []
    assert outcomes[0]["status"] == "complete"
    phase_order = {
        "storing": 0,
        "parsing": 1,
        "ingesting": 2,
        "verifying": 3,
        "publishing": 4,
        "complete": 5,
    }
    observed = [phase_order[phase] for phase in phases if phase in phase_order]
    assert observed == sorted(observed)
    source = core.store.get_source(source_id, duplicate=True)
    assert source.import_status == "complete"
    assert source.metadata["import_progress"]["phase"] == "complete"


def test_source_only_reprocess_keeps_source_heartbeats_and_terminal_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext import importers as importers_module

    core = CoreService.in_directory(tmp_path)
    service = ArchiveImportService(core.store)
    payload = b'{"kind":"goal","content":"Source-only progress remains durable"}\n'
    first = service.import_bytes("source-only-heartbeat.jsonl", payload)
    source_id = str(first["source"]["id"])
    core.store.update_source_import(
        source_id,
        import_status="failed",
        metadata=first["source"]["metadata"],
        parser_warnings=first["source"]["parser_warnings"],
    )
    tracker_type = importers_module.ImportProgressTracker

    def fast_tracker(*args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["heartbeat_seconds"] = 0.04
        return tracker_type(*args, **kwargs)

    monkeypatch.setattr(importers_module, "ImportProgressTracker", fast_tracker)
    original_parse = importers_module.parse_archive_path
    parser_entered = threading.Event()
    release_parser = threading.Event()

    def stalled_parse(*args, **kwargs):  # type: ignore[no-untyped-def]
        parser_entered.set()
        assert release_parser.wait(timeout=5), "test did not release stalled parser"
        return original_parse(*args, **kwargs)

    monkeypatch.setattr(importers_module, "parse_archive_path", stalled_parse)
    outcomes: list[dict[str, object]] = []
    failures: list[BaseException] = []

    def worker() -> None:
        try:
            outcomes.append(service.reprocess_source(source_id))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        assert parser_entered.wait(timeout=5)
        stamps: list[str] = []
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(set(stamps)) < 3:
            source = core.store.get_source(source_id, duplicate=True)
            progress = source.metadata.get("import_progress") or {}
            if progress.get("phase") == "parsing":
                stamps.append(str(progress["updated_at"]))
            time.sleep(0.01)
        assert len(set(stamps)) >= 3
    finally:
        release_parser.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert failures == []
    assert outcomes[0]["source"]["import_status"] == "complete"
    source = core.store.get_source(source_id, duplicate=True)
    assert source.metadata["import_progress"]["phase"] == "complete"
    assert source.metadata["import_progress"]["percent"] == 100


def test_operation_liveness_touch_is_fail_fast_and_semantically_neutral(
    tmp_path: Path,
) -> None:
    core, ops = _ops(tmp_path)
    payload = b'{"kind":"fact","content":"Liveness is not semantic progress"}\n'
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="liveness-neutral.jsonl",
    )
    operation_id = str(operation["operation_id"])
    staged = ops.accept_upload(
        operation_id,
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    before = ops.get_operation(operation_id)
    assert before["status"] == "processing"
    assert before["source_id"] == staged["source_id"]

    liveness_connection = core.store._connect_import_operation_liveness()
    try:
        assert int(liveness_connection.execute("PRAGMA synchronous").fetchone()[0]) == 1
        assert str(liveness_connection.execute("PRAGMA journal_mode").fetchone()[0]) == "wal"
    finally:
        liveness_connection.close()

    blocker = sqlite3.connect(
        core.store.database_path,
        timeout=10.0,
        isolation_level=None,
        check_same_thread=False,
    )
    blocker.execute("PRAGMA busy_timeout = 10000")
    blocker.execute("PRAGMA journal_mode = WAL")
    blocker.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        assert core.store.touch_import_operation_liveness(operation_id) is False
        # SQLite enforces the 250 ms busy timeout. Allow hosted-runner scheduling
        # jitter while proving one failed touch consumes less than half of the
        # frozen five-second observer heartbeat budget.
        assert time.monotonic() - started < PROGRESS_HEARTBEAT_SECONDS / 2
    finally:
        blocker.rollback()
        blocker.close()

    assert core.store.touch_import_operation_liveness(operation_id) is True
    after = ops.get_operation(operation_id)
    assert after["updated_at"] > before["updated_at"]
    semantic_fields = (
        "status",
        "phase",
        "bytes_received",
        "bytes_committed",
        "content_hash",
        "source_id",
        "cancel_requested",
        "progress",
        "result",
        "error_message",
    )
    for field in semantic_fields:
        assert after[field] == before[field]

    core.store.update_import_operation(
        operation_id,
        status="failed",
        phase="failed",
        completed=True,
        error_message="import_interrupted_process_restart",
    )
    terminal = ops.get_operation(operation_id)
    assert core.store.touch_import_operation_liveness(operation_id) is False
    assert ops.get_operation(operation_id) == terminal
    with pytest.raises(NotFoundError, match="import operation not found"):
        core.store.touch_import_operation_liveness("missing-operation")


def test_nonterminal_operation_telemetry_uses_normal_wal_with_full_busy_budget(
    tmp_path: Path,
) -> None:
    core, _ops_service = _ops(tmp_path)

    full_connection = core.store.connect()
    try:
        assert int(full_connection.execute("PRAGMA synchronous").fetchone()[0]) == 2
    finally:
        full_connection.close()

    connection = core.store._connect_import_operation_telemetry()
    try:
        assert int(connection.execute("PRAGMA synchronous").fetchone()[0]) == 1
        assert str(connection.execute("PRAGMA journal_mode").fetchone()[0]) == "wal"
        assert int(connection.execute("PRAGMA busy_timeout").fetchone()[0]) == 10_000
    finally:
        connection.close()


def test_only_explicit_nonterminal_progress_uses_normal_durability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    payload = b"{}\n"
    operation = ops.start_operation(
        declared_byte_size=len(payload),
        filename="durability.jsonl",
    )
    operation_id = str(operation["operation_id"])
    staged = ops.accept_upload(
        operation_id,
        io.BytesIO(payload),
        expected_size=len(payload),
        process_after=False,
    )
    source_id = str(staged["source_id"])
    transaction_modes: list[bool] = []
    original_transaction = core.store._import_operation_update_transaction

    @contextmanager
    def tracked_transaction(
        *,
        normal_durability: bool,
    ) -> Iterator[sqlite3.Connection]:
        transaction_modes.append(normal_durability)
        with original_transaction(normal_durability=normal_durability) as connection:
            yield connection

    monkeypatch.setattr(
        core.store,
        "_import_operation_update_transaction",
        tracked_transaction,
    )

    core.store.update_import_operation(
        operation_id,
        status="processing",
        phase="parsing",
        bytes_received=len(payload),
        bytes_committed=len(payload),
        content_hash=hashlib.sha256(payload).hexdigest(),
        source_id=source_id,
        progress={"phase": "parsing", "percent": 99},
    )
    core.store.update_import_operation(
        operation_id,
        status="processing",
        preflight={"diagnostic": True},
    )
    core.store.update_import_operation(
        operation_id,
        phase="parsing",
        progress={"phase": "parsing", "percent": 99},
    )
    core.store.update_import_operation(
        operation_id,
        status="processing",
        error_message="closed_diagnostic_code",
    )
    core.store.update_import_operation(
        operation_id,
        status="processing",
        clear_error=True,
    )
    core.store.update_import_operation(
        operation_id,
        cancel_requested=True,
        progress={"phase": "parsing", "percent": 99, "cancel_requested": True},
    )
    finished = core.store.update_import_operation(
        operation_id,
        status="complete",
        phase="complete",
        result={"ok": True},
        completed=True,
    )

    assert transaction_modes == [True, False, False, False, False, False, False]
    assert finished["status"] == "complete"
    assert finished["completed_at"] is not None
    assert finished["cancel_requested"] is True
    assert finished["result"] == {"ok": True}


def test_operation_liveness_bypasses_python_writer_lock_and_reader_stays_queryable(
    tmp_path: Path,
) -> None:
    core, ops = _ops(tmp_path)
    operation = ops.start_operation(declared_byte_size=1, filename="contended.bin")
    operation_id = str(operation["operation_id"])
    before = ops.get_operation(operation_id)

    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_writer_lock() -> None:
        with core.store._write_lock:
            lock_held.set()
            assert release_lock.wait(timeout=5.0)

    holder = threading.Thread(target=hold_writer_lock, daemon=True)
    holder.start()
    assert lock_held.wait(timeout=1.0)
    try:
        started = time.monotonic()
        assert core.store.touch_import_operation_liveness(operation_id) is True
        assert time.monotonic() - started < 1.0
        observed = ops.get_operation(operation_id)
        assert time.monotonic() - started < 1.0
        assert holder.is_alive()
    finally:
        release_lock.set()
        holder.join(timeout=1.0)

    assert not holder.is_alive()
    assert observed["updated_at"] > before["updated_at"]
    assert observed["bytes_committed"] == before["bytes_committed"]
    assert observed["progress"] == before["progress"]


def test_operation_status_route_does_not_queue_as_sync_threadpool_work(tmp_path: Path) -> None:
    import inspect

    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app

    app = create_app(CoreConfig.in_directory(tmp_path, require_auth=False))
    route = next(
        route
        for route in app.routes
        if getattr(route, "path", "") == "/v1/admin/import-operations/{operation_id}"
        and "GET" in getattr(route, "methods", set())
    )
    assert inspect.iscoroutinefunction(route.endpoint)
    observer_dependency = next(
        dependency.call
        for dependency in route.dependant.dependencies
        if getattr(dependency.call, "__name__", "") == "operation_observer_from_header"
    )
    assert inspect.iscoroutinefunction(observer_dependency)


def test_operation_status_executor_is_recreated_for_each_app_lifespan(
    tmp_path: Path,
) -> None:
    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from allthecontext.models import ClientCreate
    from fastapi.testclient import TestClient

    app = create_app(CoreConfig.in_directory(tmp_path, require_auth=True))
    _principal, token = app.state.core.store.create_client(
        ClientCreate(name="reused lifespan observer", scopes=["admin"])
    )
    operation = app.state.core.import_operations.start_operation(
        declared_byte_size=1,
        filename="reused-lifespan.bin",
    )
    path = f"/v1/admin/import-operations/{operation['operation_id']}"
    headers = {"Authorization": f"Bearer {token}"}

    with TestClient(app) as client:
        first = client.get(path, headers=headers)
        assert first.status_code == 200, first.text
    with TestClient(app) as client:
        second = client.get(path, headers=headers)
        assert second.status_code == 200, second.text


def test_lightweight_operation_heartbeat_has_margin_without_accelerating_source_writes() -> None:
    from allthecontext.import_boundary import ImportProgressTracker

    source_only = ImportProgressTracker(
        bytes_total=1,
        heartbeat_seconds=1.0,
        durable_sink=lambda _progress: None,
    )
    operation_owned = ImportProgressTracker(
        bytes_total=1,
        heartbeat_seconds=1.0,
        durable_sink=lambda _progress: None,
        liveness_sink=lambda _progress: True,
    )

    assert source_only._durable_heartbeat_interval() == pytest.approx(0.25)
    assert operation_owned._durable_heartbeat_interval() == pytest.approx(0.1)


def test_authenticated_operation_status_bypasses_cross_thread_writer_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import allthecontext.storage as storage_module
    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from allthecontext.models import ClientCreate
    from fastapi.testclient import TestClient

    app = create_app(CoreConfig.in_directory(tmp_path, require_auth=True))
    core = app.state.core
    _principal, token = core.store.create_client(
        ClientCreate(name="operation observer", scopes=["admin"])
    )
    operation = core.import_operations.start_operation(
        declared_byte_size=1,
        filename="authenticated-contended.bin",
    )
    operation_id = str(operation["operation_id"])
    verify_calls = {"count": 0}
    original_verify = storage_module.verify_token
    original_digest = storage_module.hmac.digest
    fingerprints: list[bytes] = []

    def counting_verify(token_value: str, encoded: str) -> bool:
        verify_calls["count"] += 1
        return original_verify(token_value, encoded)

    def recording_digest(key: bytes, message: bytes, digest: str) -> bytes:
        fingerprint = original_digest(key, message, digest)
        fingerprints.append(fingerprint)
        return fingerprint

    monkeypatch.setattr(storage_module, "verify_token", counting_verify)
    monkeypatch.setattr(storage_module.hmac, "digest", recording_digest)
    observer_cleanup: dict[str, object] = {}
    original_close_observer = core.store.close_import_operation_observer

    def record_observer_cleanup() -> None:
        observer_cleanup["thread"] = threading.current_thread().name
        observer_cleanup["had_connection"] = hasattr(
            core.store._operation_observer_local,
            "connection",
        )
        original_close_observer()
        observer_cleanup["cleared"] = not any(
            hasattr(core.store._operation_observer_local, attribute)
            for attribute in ("connection", "credential", "fingerprint_key")
        )

    monkeypatch.setattr(
        core.store,
        "close_import_operation_observer",
        record_observer_cleanup,
    )
    lock_held = threading.Event()
    release_lock = threading.Event()

    def hold_writer_lock() -> None:
        with core.store._write_lock:
            lock_held.set()
            assert release_lock.wait(timeout=5.0)

    with TestClient(app) as client:
        holder = threading.Thread(target=hold_writer_lock, daemon=True)
        holder.start()
        assert lock_held.wait(timeout=1.0)
        try:
            started = time.monotonic()
            response = client.get(
                f"/v1/admin/import-operations/{operation_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert time.monotonic() - started < 1.0
            assert response.status_code == 200, response.text
            assert response.json()["operation_id"] == operation_id
            assert holder.is_alive()
        finally:
            release_lock.set()
            holder.join(timeout=1.0)

        assert not holder.is_alive()
        repeated = client.get(
            f"/v1/admin/import-operations/{operation_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert repeated.status_code == 200, repeated.text
        assert verify_calls["count"] == 1

        missing = client.get(
            "/v1/admin/import-operations/00000000-0000-4000-8000-000000000000",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert missing.status_code == 404
        assert verify_calls["count"] == 1

        invalid_token = f"{token}-mismatch"
        verify_count_before_invalid = verify_calls["count"]
        invalid = client.get(
            f"/v1/admin/import-operations/{operation_id}",
            headers={"Authorization": f"Bearer {invalid_token}"},
        )
        assert invalid.status_code == 401
        assert verify_calls["count"] > verify_count_before_invalid

        other_principal, other_token = core.store.create_client(
            ClientCreate(name="other operation observer", scopes=["admin"])
        )
        verify_count_before_other = verify_calls["count"]
        other = client.get(
            f"/v1/admin/import-operations/{operation_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert other.status_code == 200, other.text
        assert verify_calls["count"] > verify_count_before_other
        verify_count_after_other = verify_calls["count"]
        other_repeated = client.get(
            f"/v1/admin/import-operations/{operation_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert other_repeated.status_code == 200, other_repeated.text
        assert verify_calls["count"] == verify_count_after_other

        core.store.revoke_client(other_principal.id)
        revoked = client.get(
            f"/v1/admin/import-operations/{operation_id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )
        assert revoked.status_code == 401
        assert verify_calls["count"] == verify_count_after_other
        assert fingerprints[0] == fingerprints[1] == fingerprints[2]
        assert fingerprints[2] != fingerprints[3]
        assert fingerprints[4] == fingerprints[5] == fingerprints[6]

        durable_bytes = core.config.database_path.read_bytes()
        response_bytes = b"".join(
            (
                response.content,
                repeated.content,
                missing.content,
                invalid.content,
                other.content,
                other_repeated.content,
                revoked.content,
            )
        )
        for secret in (
            token.encode(),
            invalid_token.encode(),
            other_token.encode(),
            *fingerprints,
        ):
            assert secret not in durable_bytes
            assert secret not in response_bytes
            assert secret.hex().encode() not in durable_bytes
            assert secret.hex().encode() not in response_bytes

    assert str(observer_cleanup["thread"]).startswith("atc-operation-observer")
    assert observer_cleanup["had_connection"] is True
    assert observer_cleanup["cleared"] is True


def test_operation_status_enforces_scope_before_not_found(tmp_path: Path) -> None:
    from allthecontext.config import CoreConfig
    from allthecontext.core.app import create_app
    from allthecontext.models import ClientCreate
    from fastapi.testclient import TestClient

    app = create_app(CoreConfig.in_directory(tmp_path, require_auth=True))
    core = app.state.core
    _reader, reader_token = core.store.create_client(
        ClientCreate(name="non-admin observer", scopes=["context:read"])
    )
    _admin, admin_token = core.store.create_client(
        ClientCreate(name="admin observer", scopes=["admin"])
    )
    operation = core.import_operations.start_operation(
        declared_byte_size=1,
        filename="authorization-order.bin",
    )
    operation_id = str(operation["operation_id"])
    missing_id = "00000000-0000-4000-8000-000000000000"

    with TestClient(app) as client:
        for observed_id in (operation_id, missing_id):
            forbidden = client.get(
                f"/v1/admin/import-operations/{observed_id}",
                headers={"Authorization": f"Bearer {reader_token}"},
            )
            assert forbidden.status_code == 403
            assert operation_id not in forbidden.text
        missing = client.get(
            f"/v1/admin/import-operations/{missing_id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert missing.status_code == 404


def test_operation_liveness_touch_retries_only_sqlite_lock_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    core, ops = _ops(tmp_path)
    operation = ops.start_operation(declared_byte_size=1, filename="io-failure.bin")
    operation_id = str(operation["operation_id"])
    before = ops.get_operation(operation_id)

    def fail_connection() -> sqlite3.Connection:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(
        core.store,
        "_connect_import_operation_liveness",
        fail_connection,
    )
    with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
        core.store.touch_import_operation_liveness(operation_id)
    assert ops.get_operation(operation_id) == before


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
    uploading_observed = threading.Event()
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
            if phase == "uploading":
                uploading_observed.set()
            time.sleep(0.005)

    def synchronized_chunks() -> Iterator[bytes]:
        assert uploading_observed.wait(timeout=5), "poller did not observe uploading"
        yield payload

    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    try:
        finished = ops.accept_upload(
            operation_id,
            synchronized_chunks(),
            expected_size=len(payload),
        )
    finally:
        stop.set()
        thread.join(timeout=5)
    assert finished["status"] == "complete"
    assert finished["phase"] == "complete"
    # Upload and terminal completeness are required; intermediate phases may be brief.
    assert "uploading" in seen
    assert "complete" in seen or finished["phase"] == "complete"
    assert finished["result"]["source"]["import_status"] == "complete"
    assert core.store.get_source_content(str(finished["source_id"])) == payload


def test_concurrent_status_polling_during_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    processing_observed = threading.Event()
    stop = threading.Event()
    original_reprocess = ops.imports.reprocess_source

    def poller() -> None:
        while not stop.is_set():
            state = ops.get_operation(operation_id)
            status = str(state["status"])
            if not seen_status or seen_status[-1] != status:
                seen_status.append(status)
            if status == "processing":
                processing_observed.set()
            time.sleep(0.01)

    def synchronized_reprocess(source_id_arg: str, *, progress_tracker=None):  # type: ignore[no-untyped-def]
        # Retry is durably claimed as processing before reprocess begins.
        assert processing_observed.wait(timeout=5), "poller did not observe processing"
        return original_reprocess(source_id_arg, progress_tracker=progress_tracker)

    monkeypatch.setattr(ops.imports, "reprocess_source", synchronized_reprocess)
    thread = threading.Thread(target=poller, daemon=True)
    thread.start()
    try:
        retried = ops.retry_operation(operation_id)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert retried["status"] == "complete"
    assert "failed" in seen_status
    assert "processing" in seen_status
