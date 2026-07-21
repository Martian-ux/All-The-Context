from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.core.service import CoreService
from allthecontext.models import (
    ApprovalRequest,
    Availability,
    BeginIngestionRequest,
    CandidateInput,
    ClientCreate,
    CoverageReport,
    FinishIngestionRequest,
    IngestionMode,
    SearchRequest,
    SubmitBatchRequest,
)
from allthecontext.storage import ConflictError, NotFoundError


@pytest.fixture
def core(tmp_path: Path) -> CoreService:
    return CoreService.in_directory(tmp_path)


def test_ingestion_is_resumable_and_batches_are_idempotent(core: CoreService) -> None:
    begin_request = BeginIngestionRequest(
        mode=IngestionMode.BOOTSTRAP,
        accessible_sources=["visible chats"],
        unavailable_sources=["deleted chats"],
        idempotency_key="bootstrap-1",
    )
    first = core.ingestion.begin(begin_request)
    assert core.ingestion.begin(begin_request)["session_id"] == first["session_id"]

    batch = SubmitBatchRequest(
        session_id=first["session_id"],
        idempotency_key="page-1",
        candidates=[CandidateInput(kind="preference", content="Use concise answers")],
    )
    submitted = core.ingestion.submit(batch)
    replay = core.ingestion.submit(batch)
    assert replay["replayed"] is True
    assert replay["candidate_ids"] == submitted["candidate_ids"]

    changed = batch.model_copy(
        update={"candidates": [CandidateInput(kind="preference", content="Use long answers")]}
    )
    with pytest.raises(ConflictError):
        core.ingestion.submit(changed)

    report = CoverageReport(
        available=["visible chats"],
        unavailable=["deleted chats"],
        limitations=["Only the current window was visible."],
    )
    finished = core.ingestion.finish(
        FinishIngestionRequest(session_id=first["session_id"], coverage_report=report)
    )
    assert finished["status"] == "finished"
    assert finished["coverage"]["unavailable"] == ["deleted chats"]


def test_approval_fts_version_correction_and_tombstone(core: CoreService) -> None:
    candidate = core.ingestion.propose(
        CandidateInput(
            kind="project_decision",
            content="The launch color is cobalt blue",
            availability=Availability.ALWAYS,
            scopes=["project:atlas"],
        )
    )
    record = core.store.approve_candidate(candidate.id, ApprovalRequest())
    assert core.retrieval.search(SearchRequest(query="cobalt")).items[0].id == record.id
    assert [event["sequence"] for event in core.store.pending_replication_events()] == [1]

    corrected = core.store.correct_record(
        record.id, content="The launch color is forest green", reason="User corrected color"
    )
    assert corrected.version == 2
    assert len(core.store.record_history(record.id)) == 2
    assert core.retrieval.search(SearchRequest(query="cobalt")).items == []
    assert core.retrieval.search(SearchRequest(query="forest")).items[0].id == record.id

    tombstone = core.store.delete_record(record.id, reason="No longer relevant")
    assert tombstone["deleted_version"] == 3
    assert core.retrieval.search(SearchRequest(query="forest")).items == []
    with pytest.raises(NotFoundError):
        core.store.get_record(record.id)
    events = core.store.pending_replication_events()
    assert [event["event_type"] for event in events] == [
        "record_upserted",
        "record_upserted",
        "record_deleted",
    ]


def test_sensitive_replication_requires_explicit_confirmation(core: CoreService) -> None:
    candidate = core.ingestion.propose(
        CandidateInput(
            kind="fact",
            content="Sensitive fact",
            sensitivity="sensitive",
            availability=Availability.ALWAYS,
        )
    )
    from allthecontext.storage import InvalidStateError

    with pytest.raises(InvalidStateError):
        core.store.approve_candidate(candidate.id)
    approved = core.store.approve_candidate(
        candidate.id, ApprovalRequest(explicit_sensitive_replication=True)
    )
    assert approved.availability == Availability.ALWAYS


def test_record_scopes_are_query_categories_and_client_lists_enforce_access(
    core: CoreService,
) -> None:
    principal, _token = core.store.create_client(
        ClientCreate(name="Reader", scopes=["context:read"])
    )
    visible = core.ingestion.propose(
        CandidateInput(
            kind="project",
            content="Atlas uses SQLite",
            scopes=["project:atlas"],
        )
    )
    core.store.approve_candidate(visible.id)
    assert (
        core.retrieval.search(
            SearchRequest(query="SQLite", scopes=["project:atlas"]), principal
        ).total
        == 1
    )

    denied = core.ingestion.propose(
        CandidateInput(
            kind="project",
            content="Hidden Neptune decision",
            scopes=["project:neptune"],
            denied_clients=[principal.id],
        )
    )
    core.store.approve_candidate(denied.id)
    assert core.retrieval.search(SearchRequest(query="Neptune"), principal).total == 0
