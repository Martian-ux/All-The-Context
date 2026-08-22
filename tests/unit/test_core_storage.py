from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.core.service import CoreService
from allthecontext.models import (
    ApprovalRequest,
    Availability,
    BeginIngestionRequest,
    BootstrapRequest,
    CandidateInput,
    ClientCreate,
    CoverageReport,
    FinishIngestionRequest,
    IngestionMode,
    SearchRequest,
    Sensitivity,
    SubmitBatchRequest,
)
from allthecontext.storage import (
    SOURCE_BLOB_CHUNK_BYTES,
    ConflictError,
    InvalidStateError,
    NotFoundError,
)


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


def test_secret_like_payloads_and_delete_reasons_never_reach_storage(
    core: CoreService,
) -> None:
    token = "sk-" + ("A7" * 16)
    with pytest.raises(InvalidStateError):
        core.store.add_candidate(CandidateInput(kind="fact", content=token))
    with pytest.raises(InvalidStateError):
        core.store.add_edge_candidate(
            "opaque-proposal",
            CandidateInput(kind="fact", content=token),
            client_id="relay-client",
        )

    created = core.store.add_candidate(
        CandidateInput(
            kind="fact",
            content="A safe record for deletion.",
            explicit_user_statement=True,
        )
    )
    assert created.record_id is not None
    deleted = core.store.delete_record(
        created.record_id,
        reason=f"api_key={token}",
    )
    assert deleted["reason"] == "Explicit user privacy action"
    assert token.encode() not in core.config.database_path.read_bytes()


def test_source_delete_and_restore_only_reverse_its_own_record_deletions(
    core: CoreService,
) -> None:
    content = b"private provider archive"
    source = core.store.add_source(
        content,
        source_service="test",
        source_type="archive",
        filename="provider-export.zip",
    )
    first = core.store.approve_candidate(
        core.ingestion.propose(
            CandidateInput(kind="fact", content="First imported memory", source_id=source.id)
        ).id
    )
    already_deleted = core.store.approve_candidate(
        core.ingestion.propose(
            CandidateInput(
                kind="fact",
                content="Memory removed before the source",
                source_id=source.id,
            )
        ).id
    )
    redeleted_independently = core.store.approve_candidate(
        core.ingestion.propose(
            CandidateInput(
                kind="fact",
                content="Memory independently changed after source removal",
                source_id=source.id,
            )
        ).id
    )
    core.store.delete_record(already_deleted.id, reason="removed independently")

    deletion = core.store.delete_source(source.id, reason="remove imported source")

    assert set(deletion["deleted_record_ids"]) == {first.id, redeleted_independently.id}
    assert core.store.list_sources() == ([], 0)
    assert core.store.status()["counts"]["sources"] == 0
    with pytest.raises(NotFoundError):
        core.store.get_source(source.id)
    with pytest.raises(NotFoundError):
        core.store.get_source_content(source.id)
    with pytest.raises(NotFoundError):
        core.store.get_record(first.id)
    with pytest.raises(NotFoundError):
        core.store.get_record(already_deleted.id)
    core.store.restore_record(
        redeleted_independently.id,
        reason="independent restore after source removal",
    )
    core.store.delete_record(
        redeleted_independently.id,
        reason="independent re-delete after source removal",
    )

    restored = core.store.restore_source(source.id, reason="undo source removal")

    assert restored["restored_record_ids"] == [first.id]
    assert restored["source"]["id"] == source.id
    assert core.store.get_record(first.id).content == "First imported memory"
    with pytest.raises(NotFoundError):
        core.store.get_record(already_deleted.id)
    with pytest.raises(NotFoundError):
        core.store.get_record(redeleted_independently.id)
    assert core.store.get_source_content(source.id) == content

    core.store.delete_source(source.id, reason="remove again")
    duplicate = core.store.add_source(
        content,
        source_service="test",
        source_type="archive",
        filename="provider-export.zip",
    )
    assert duplicate.id == source.id
    assert duplicate.duplicate is True
    assert duplicate.deleted_at is None
    assert core.store.get_record(first.id).content == "First imported memory"


def test_source_file_storage_chunks_large_content_and_verifies_copies(
    core: CoreService,
    tmp_path: Path,
) -> None:
    content = b"a" * SOURCE_BLOB_CHUNK_BYTES + b"tail"
    source_path = tmp_path / "large-provider-export.jsonl"
    source_path.write_bytes(content)

    source = core.store.add_source_file(
        source_path,
        source_service="test",
        source_type="jsonl",
        filename=source_path.name,
    )

    assert core.store.get_source_content(source.id) == content
    copied = tmp_path / "copied-provider-export.jsonl"
    assert core.store.copy_source_content_to_path(source.id, copied) == len(content)
    assert copied.read_bytes() == content
    with core.store.connect() as connection:
        parent = connection.execute(
            "SELECT storage_kind,length(content),byte_size FROM source_blobs WHERE content_hash=?",
            (source.content_hash,),
        ).fetchone()
        chunks = connection.execute(
            "SELECT chunk_index,length(content),byte_size FROM source_blob_chunks "
            "WHERE content_hash=? ORDER BY chunk_index",
            (source.content_hash,),
        ).fetchall()
    assert tuple(parent) == ("chunked", 0, len(content))
    assert [tuple(row) for row in chunks] == [
        (0, SOURCE_BLOB_CHUNK_BYTES, SOURCE_BLOB_CHUNK_BYTES),
        (1, 4, 4),
    ]

    with core.store.transaction() as connection:
        connection.execute(
            "UPDATE source_blob_chunks SET content=? WHERE content_hash=? AND chunk_index=1",
            (b"fail", source.content_hash),
        )
    corrupted_copy = tmp_path / "corrupted-copy.jsonl"
    with pytest.raises(InvalidStateError, match="integrity"):
        core.store.copy_source_content_to_path(source.id, corrupted_copy)
    assert not corrupted_copy.exists()

    core.store.purge(
        "source",
        source.id,
        confirmation=core.store.purge_confirmation_phrase("source", source.id),
        compact=False,
    )
    with core.store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM source_blob_chunks WHERE content_hash=?",
                (source.content_hash,),
            ).fetchone()[0]
            == 0
        )


def test_large_in_memory_source_uses_the_same_bounded_chunk_store(
    core: CoreService,
) -> None:
    content = b"b" * (SOURCE_BLOB_CHUNK_BYTES + 1)

    source = core.store.add_source(
        content,
        source_service="test-memory",
        source_type="json",
    )

    with core.store.connect() as connection:
        rows = connection.execute(
            "SELECT chunk_index,length(content) FROM source_blob_chunks "
            "WHERE content_hash=? ORDER BY chunk_index",
            (source.content_hash,),
        ).fetchall()
    assert [tuple(row) for row in rows] == [(0, SOURCE_BLOB_CHUNK_BYTES), (1, 1)]
    assert core.store.get_source_content(source.id) == content


def test_bootstrap_always_includes_authorized_interaction_preferences(
    core: CoreService,
) -> None:
    preference = core.ingestion.propose(
        CandidateInput(
            kind="interaction_preference",
            content="Prefer concise answers with concrete evidence",
            scopes=["general"],
        )
    )
    preference_record = core.store.approve_candidate(preference.id)
    decision = core.ingestion.propose(
        CandidateInput(
            kind="project_decision",
            content="Atlas planning uses a two-week milestone cadence",
            scopes=["project:atlas"],
        )
    )
    decision_record = core.store.approve_candidate(decision.id)

    result = core.retrieval.bootstrap(
        BootstrapRequest(
            task_description="write the Atlas project plan",
            requested_scopes=["project:atlas"],
        )
    )
    assert [item.id for item in result.items][:2] == [
        preference_record.id,
        decision_record.id,
    ]
    assert (
        core.retrieval.search(SearchRequest(query="cadence unrelated-token")).items[0].id
        == decision_record.id
    )


def test_sensitive_replication_requires_explicit_confirmation(core: CoreService) -> None:
    candidate = core.ingestion.propose(
        CandidateInput(
            kind="fact",
            content="Sensitive fact",
            sensitivity="sensitive",
            availability=Availability.ALWAYS,
        )
    )
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


def test_search_reports_total_and_supports_review_filters(core: CoreService) -> None:
    core.store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is fiction Alpha shipping.",
            explicit_user_statement=True,
        )
    )
    core.store.add_candidate(
        CandidateInput(
            kind="constraint",
            content="We must keep fiction data local.",
            explicit_user_statement=True,
            idempotency_key="constraint-local",
        )
    )
    sensitive = core.store.add_candidate(
        CandidateInput(
            kind="personal_detail",
            content="I live in Seattle for the fiction scenario.",
            explicit_user_statement=True,
            idempotency_key="location-seattle",
        )
    )
    assert sensitive.disposition.value == "applied"
    page = core.retrieval.search(SearchRequest(query="", limit=2, offset=0))
    assert page.total == 3
    assert len(page.items) == 2
    rest = core.retrieval.search(SearchRequest(query="", limit=2, offset=2))
    assert rest.total == 3
    assert len(rest.items) == 1
    goals = core.retrieval.search(SearchRequest(query="", kinds=["goal"]))
    assert goals.total == 1
    located = core.retrieval.search(SearchRequest(query="", sensitivity=[Sensitivity.SENSITIVE]))
    assert located.total == 1
    assert "Seattle" in located.items[0].content
    assert located.items[0].availability == Availability.LOCAL
