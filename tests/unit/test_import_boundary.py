from __future__ import annotations

import threading
from pathlib import Path

import pytest
from allthecontext.boundary_canary import (
    BOUNDARY_CANARY_GENERATOR_VERSION,
    write_boundary_canary,
)
from allthecontext.config import MAX_IMPORT_BYTES
from allthecontext.core.service import CoreService
from allthecontext.export import create_export, restore_export
from allthecontext.import_boundary import (
    BOUNDARY_PLUS_ONE_BYTES,
    DEFAULT_CANCEL_REGISTRY,
    ImportCancelledError,
    ImportCancelRegistry,
    ImportProgressTracker,
    formula_storage_budget_bytes,
    preflight_disk_space,
    refuse_if_over_boundary,
    required_free_bytes,
    scale_profile,
)
from allthecontext.importers import ArchiveImportService
from allthecontext.provider_shapes import (
    frozen_provider_shapes,
    grok_markdown_canary_text,
    provider_claim_manifest,
    reconcile_closed_coverage,
)
from allthecontext.storage import SOURCE_BLOB_CHUNK_BYTES, InvalidStateError


def test_inclusive_boundary_constants() -> None:
    assert MAX_IMPORT_BYTES == 2_000_000_000
    assert BOUNDARY_PLUS_ONE_BYTES == 2_000_000_001
    profile = scale_profile()
    assert profile["boundary_bytes"] == MAX_IMPORT_BYTES
    assert profile["storage_multiplier"] == 4
    assert profile["storage_overhead_bytes"] == 1 * 1024**3


def test_refuse_boundary_plus_one_deterministically() -> None:
    refuse_if_over_boundary(MAX_IMPORT_BYTES)
    with pytest.raises(InvalidStateError, match="size limit"):
        refuse_if_over_boundary(BOUNDARY_PLUS_ONE_BYTES)


def test_disk_preflight_uses_greater_of_formula_and_measured_high_water(
    tmp_path: Path,
) -> None:
    source_bytes = 1_000_000
    formula = formula_storage_budget_bytes(source_bytes)
    assert formula == source_bytes * 4 + 1 * 1024**3
    assert required_free_bytes(source_bytes) == formula
    measured = formula * 2
    assert required_free_bytes(source_bytes, measured_high_water_bytes=measured) == measured + (
        measured // 4
    )
    result = preflight_disk_space(tmp_path, source_bytes)
    assert result.ok is True
    assert result.required_free_bytes == formula


def test_progress_is_monotonic_and_reserves_100_for_completion() -> None:
    tracker = ImportProgressTracker(bytes_total=1_000)
    tracker.set_phase("storing")
    tracker.advance_bytes(400)
    first = tracker.snapshot()
    tracker.advance_bytes(200)  # non-monotonic absolute ignored
    second = tracker.snapshot()
    assert second.bytes_processed == 400
    assert first.percent < 100
    assert second.percent < 100
    tracker.complete()
    assert tracker.snapshot().percent == 100


def test_cancel_registry_acknowledges_in_flight_import() -> None:
    registry = ImportCancelRegistry()
    tracker = ImportProgressTracker(
        bytes_total=100,
        source_id="source-cancel",
        registry=registry,
    )
    tracker.set_phase("ingesting")
    assert registry.request_cancel("source-cancel") is True
    with pytest.raises(ImportCancelledError):
        tracker.check_cancelled()
    assert tracker.snapshot().phase == "cancelled"
    tracker.close()


def test_import_bytes_records_preflight_progress_and_closed_coverage(tmp_path: Path) -> None:
    service = ArchiveImportService(CoreService.in_directory(tmp_path).store)
    result = service.import_bytes(
        "notes.md",
        b"Preference: Prefer synthetic fixture answers\n",
    )
    source = result["source"]
    assert source["import_status"] == "complete"
    assert source["metadata"]["preflight"]["ok"] is True
    assert source["metadata"]["import_progress"]["phase"] == "complete"
    assert source["metadata"]["import_progress"]["percent"] == 100
    assert result["parser_version"]
    assert "closed_coverage" in result["coverage"]


def test_boundary_plus_one_import_refuses_without_publication(tmp_path: Path) -> None:
    # Tight limit simulates the product ceiling without allocating 2 GB.
    tight = ArchiveImportService(CoreService.in_directory(tmp_path / "tight").store, max_bytes=8)
    with pytest.raises(InvalidStateError, match="size limit"):
        tight.import_bytes("too-large.txt", b"Preference: too large for limit")
    sources, total = tight.store.list_sources()
    assert total == 0
    assert sources == []
    refuse_if_over_boundary(MAX_IMPORT_BYTES)
    with pytest.raises(InvalidStateError, match="size limit"):
        refuse_if_over_boundary(BOUNDARY_PLUS_ONE_BYTES)


def test_retry_from_preserved_source_is_idempotent(tmp_path: Path) -> None:
    core = CoreService.in_directory(tmp_path)
    service = ArchiveImportService(core.store)
    payload = b'{"kind":"goal","content":"Ship the honest import boundary"}\n'
    first = service.import_bytes("goals.jsonl", payload)
    source_id = first["source"]["id"]
    # Force failed state while retaining the raw blob.
    core.store.update_source_import(
        source_id,
        import_status="failed",
        metadata=first["source"]["metadata"],
        parser_warnings=first["source"]["parser_warnings"],
    )
    second = service.reprocess_source(source_id)
    assert second["source"]["import_status"] == "complete"
    assert second["candidate_ids"] == first["candidate_ids"]
    third = service.reprocess_source(source_id)
    assert third["session"]["status"] == "duplicate"
    assert third["candidate_ids"] == first["candidate_ids"]


def test_scaled_boundary_canary_imports_exports_and_restores(tmp_path: Path) -> None:
    size = 256_000
    canary_path = tmp_path / "scaled-canary.jsonl"
    spec = write_boundary_canary(canary_path, size_bytes=size)
    assert spec.generator_version == BOUNDARY_CANARY_GENERATOR_VERSION
    expected_chunks = (size + SOURCE_BLOB_CHUNK_BYTES - 1) // SOURCE_BLOB_CHUNK_BYTES
    assert spec.expected_chunk_count == expected_chunks
    assert canary_path.stat().st_size == size

    core = CoreService.in_directory(tmp_path / "core")
    service = ArchiveImportService(core.store)
    result = service.import_path(canary_path)
    assert result["source"]["import_status"] == "complete"
    assert result["source"]["byte_size"] == size
    assert result["source"]["content_hash"] == spec.expected_sha256
    assert len(result["candidate_ids"]) >= spec.expected_min_candidates
    assert result["coverage"]["closed_coverage"]["recognized"] >= 1

    export_path = tmp_path / "export.atcexp"
    create_export(
        core.store.database_path,
        export_path,
        "test-passphrase-boundary",
        include_sources=True,
    )
    restore_database = tmp_path / "restored.sqlite3"
    from allthecontext.storage import CoreStore

    destination = CoreStore(restore_database)
    destination.initialize_vault()
    restore_export(export_path, restore_database, "test-passphrase-boundary")
    restored_source = destination.get_source(result["source"]["id"])
    assert restored_source.byte_size == size
    assert restored_source.content_hash == spec.expected_sha256
    assert len(destination.get_source_content(restored_source.id)) == size


def test_cancel_request_is_visible_on_source_metadata(tmp_path: Path) -> None:
    core = CoreService.in_directory(tmp_path)
    service = ArchiveImportService(core.store, cancel_registry=DEFAULT_CANCEL_REGISTRY)
    result = service.import_bytes("done.txt", b"Goal: complete before cancel tests\n")
    source_id = result["source"]["id"]
    # Terminal imports report already_terminal rather than hanging.
    cancel = service.cancel_import(source_id)
    assert cancel["already_terminal"] is True
    progress = service.import_progress(source_id)
    assert progress["import_status"] == "complete"


def test_in_flight_cancel_marks_cancelled_without_publication(tmp_path: Path) -> None:
    registry = ImportCancelRegistry()
    core = CoreService.in_directory(tmp_path)
    service = ArchiveImportService(core.store, cancel_registry=registry)

    # Pre-register a cancel token and bind it during ingest by racing the worker.
    started = threading.Event()
    original_submit = service.ingestion.submit

    def submit_and_cancel(request, principal=None):  # type: ignore[no-untyped-def]
        started.set()
        registry.request_cancel(request.session_id.split(":")[0] if False else "")
        # Cancel every known processing source.
        items, _total = core.store.list_sources()
        for item in items:
            registry.request_cancel(item["id"])
        return original_submit(request, principal)

    service.ingestion.submit = submit_and_cancel  # type: ignore[method-assign]
    with pytest.raises(ImportCancelledError):
        service.import_bytes(
            "cancel-me.jsonl",
            b'{"kind":"fact","content":"Synthetic cancel target one"}\n'
            b'{"kind":"fact","content":"Synthetic cancel target two"}\n'
            * 150,
        )
    items, total = core.store.list_sources()
    assert total == 1
    assert items[0]["import_status"] == "cancelled"
    # No current context publication after cancel.
    status = core.store.status()
    assert status["counts"]["active_records"] == 0


def test_frozen_provider_shapes_have_closed_coverage_and_identities() -> None:
    manifest = provider_claim_manifest()
    assert set(manifest["parser_identities"]) >= {"chatgpt", "claude", "grok"}
    shapes = frozen_provider_shapes()
    assert {shape.provider for shape in shapes} >= {"chatgpt", "claude", "grok"}
    for shape in shapes:
        closed = reconcile_closed_coverage(shape.expected_counts)
        assert closed["counts"]["recognized"] >= 1
        assert shape.parser_identity.endswith("-v1")
        if shape.filename.endswith(".md"):
            assert "offline-only" in grok_markdown_canary_text()
        else:
            assert shape.payload_bytes()


def test_provider_shape_import_excludes_assistant_and_reports_coverage(tmp_path: Path) -> None:
    from allthecontext.importers import parse_json, parse_text
    from allthecontext.provider_shapes import frozen_shape_by_id

    chatgpt = frozen_shape_by_id("chatgpt-conversation-graph-v1")
    parsed = parse_json(chatgpt.payload_bytes().decode("utf-8"))
    assert parsed.provider == "chatgpt"
    assert parsed.parser_identity == "chatgpt-archives-v1"
    assert all("fabricated" not in item.content for item in parsed.candidates)
    assert parsed.closed_coverage["excluded"] >= 1
    assert parsed.closed_coverage["recognized"] >= 1

    claude = frozen_shape_by_id("claude-chat-messages-v1")
    parsed_claude = parse_json(claude.payload_bytes().decode("utf-8"))
    assert parsed_claude.provider == "claude"
    assert parsed_claude.closed_coverage["skipped"] >= 1

    grok = frozen_shape_by_id("grok-conversation-json-v1")
    parsed_grok = parse_json(grok.payload_bytes().decode("utf-8"))
    assert parsed_grok.provider == "grok"

    markdown = frozen_shape_by_id("grok-markdown-transcript-v1")
    parsed_md = parse_text(markdown.payload_bytes().decode("utf-8"), source_name="session.md")
    assert parsed_md.provider == "grok"
    assert parsed_md.candidates

    service = ArchiveImportService(CoreService.in_directory(tmp_path).store)
    result = service.import_bytes(chatgpt.filename, chatgpt.payload_bytes())
    assert result["provider"] == "chatgpt"
    assert result["parser_identity"] == "chatgpt-archives-v1"
    assert result["coverage"]["closed_coverage"]["excluded"] >= 1
