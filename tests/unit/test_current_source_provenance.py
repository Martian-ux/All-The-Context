from __future__ import annotations

from pathlib import Path

from allthecontext.models import (
    CandidateInput,
    ContextRecordOut,
    CoverageReport,
    IngestionMode,
    ObservationDisposition,
    ObservationOut,
    SearchRequest,
)
from allthecontext.retrieval import RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore


def _ingest_event(
    store: CoreStore,
    source_id: str,
    *,
    event_id: str,
    content: str,
    source_reference: str,
    source_service: str,
    source_type: str,
    evidence: str,
    scopes: list[str],
    kind: str = "fact",
    supersedes: str | None = None,
) -> ObservationOut:
    session_id = str(
        store.begin_ingestion(
            mode=IngestionMode.ARCHIVE,
            accessible_sources=[source_id],
            unavailable_sources=[],
            idempotency_key=f"session:{event_id}",
        )["session_id"]
    )
    batch = store.submit_batch(
        session_id,
        f"batch:{event_id}",
        [
            CandidateInput(
                kind=kind,
                content=content,
                source_id=source_id,
                source_reference=source_reference,
                source_service=source_service,
                source_type=source_type,
                evidence=evidence,
                scopes=scopes,
                supersedes=supersedes,
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        session_id,
        CoverageReport(available=[source_id], complete=True),
    )
    return store.get_observation(str(batch["candidate_ids"][0]))


def _search(store: CoreStore, scope: str) -> list[ContextRecordOut]:
    return RetrievalEngine(store).search(
        SearchRequest(query="Atlas launch", scopes=[scope], limit=10),
        ClientPrincipal("synthetic-reader", "Synthetic reader", frozenset({"context:read"})),
    ).items


def test_changed_archive_content_uses_its_supporting_source_through_retrieval(
    tmp_path: Path,
) -> None:
    store = CoreStore(tmp_path / "core.sqlite3")
    store.initialize_vault(name="Synthetic current-source provenance")

    old_source = store.add_source(
        b"Atlas launch is planned for May.",
        source_service="planning-v1",
        source_type="provider_archive",
    )
    original = _ingest_event(
        store,
        old_source.id,
        event_id="d3a",
        content="Atlas launch is planned for May.",
        source_reference="d3a",
        source_service="planning-v1",
        source_type="provider_archive",
        evidence="May planning note",
        scopes=["project:atlas"],
    )
    record_id = original.record_id
    assert record_id is not None
    initial = store.get_record(record_id)
    assert initial.content == "Atlas launch is planned for May."
    assert initial.source_id == old_source.id
    assert initial.source_reference == "d3a"
    assert [item.id for item in _search(store, "project:atlas")] == [record_id]

    new_source = store.add_source(
        b"Atlas launch is now planned for June.",
        source_service="planning-v2",
        source_type="meeting_minutes",
    )
    correction = _ingest_event(
        store,
        new_source.id,
        event_id="d3b",
        content="Atlas launch is now planned for June.",
        source_reference="d3b",
        source_service="planning-v2",
        source_type="meeting_minutes",
        evidence="June planning decision",
        scopes=["project:other"],
        kind="correction",
        supersedes=record_id,
    )
    assert correction.record_id == record_id

    updated = store.get_record(record_id)
    assert updated.content == "Atlas launch is now planned for June."
    assert updated.source_id == new_source.id
    assert updated.source_reference == "d3b"
    assert updated.source_service == "planning-v2"
    assert updated.source_type == "meeting_minutes"
    assert updated.evidence == "June planning decision"
    assert updated.scopes == ["project:atlas"]
    assert [item.id for item in _search(store, "project:atlas")] == [record_id]
    assert _search(store, "project:other") == []

    duplicate = _ingest_event(
        store,
        new_source.id,
        event_id="d3b-duplicate",
        content="Atlas launch is now planned for June.",
        source_reference="d3b",
        source_service="planning-v2",
        source_type="meeting_minutes",
        evidence="June planning decision",
        scopes=["project:atlas"],
    )
    assert duplicate.record_id == record_id
    assert duplicate.disposition == ObservationDisposition.REINFORCED
    assert store.get_record(record_id).source_reference == "d3b"
    history = store.record_history(record_id)
    assert [item["snapshot"]["content"] for item in history] == [
        "Atlas launch is planned for May.",
        "Atlas launch is now planned for June.",
    ]
    assert [item["snapshot"]["source_reference"] for item in history] == ["d3a", "d3b"]
    assert [item["snapshot"]["source_id"] for item in history] == [
        old_source.id,
        new_source.id,
    ]
    truth = store.get_memory_truth(record_id)
    assert {(item.source_reference, item.content) for item in truth.evidence} >= {
        ("d3a", "Atlas launch is planned for May."),
        ("d3b", "Atlas launch is now planned for June."),
    }

    deleted_old_source = store.delete_source(old_source.id, reason="obsolete source")
    assert deleted_old_source["deleted_record_ids"] == []
    assert store.get_record(record_id).deleted_at is None
    assert [item.id for item in _search(store, "project:atlas")] == [record_id]

    deleted_current_source = store.delete_source(new_source.id, reason="current source removed")
    assert deleted_current_source["deleted_record_ids"] == [record_id]
    assert store.get_record(record_id, include_deleted=True).deleted_at is not None
    assert _search(store, "project:atlas") == []

    store.close()
