from __future__ import annotations

from pathlib import Path

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.models import (
    CandidateInput,
    CoverageReport,
    IngestionMode,
    MemoryTruthStatus,
    ObservationDisposition,
)
from allthecontext.storage import CoreStore
from fastapi.testclient import TestClient


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.sqlite3")
    store.initialize_vault()
    return store


def _archive_observation(
    store: CoreStore,
    source_id: str,
    *,
    content: str,
    source_reference: str,
    session_key: str,
) -> str:
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source_id],
        unavailable_sources=[],
        idempotency_key=f"session-{session_key}",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        f"batch-{session_key}",
        [
            CandidateInput(
                kind="preference",
                content=content,
                source_id=source_id,
                source_reference=source_reference,
                source_service="fiction-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fiction-archive"], complete=True),
    )
    return str(batch["candidate_ids"][0])


def test_source_rebuild_reuses_identity_only_for_rebuild_tombstones(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )

    first_observation_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:1",
        session_key="first",
    )
    first = store.get_observation(first_observation_id)
    assert first.disposition == ObservationDisposition.APPLIED
    assert first.record_id is not None
    record_id = first.record_id

    assert store.withdraw_automatic_source_records(source.id) == [record_id]
    replacement_observation_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:1",
        session_key="replacement",
    )
    replacement = store.get_observation(replacement_observation_id)
    assert replacement.record_id == record_id
    assert store.get_record(record_id).content == "I prefer concise answers."


def test_user_deletion_tombstone_cannot_be_reapplied_by_archive_import(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )

    first_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:1",
        session_key="initial",
    )
    first = store.get_observation(first_id)
    assert first.record_id is not None
    record_id = first.record_id
    tombstone = store.delete_record(record_id, reason="user explicitly removed this memory")

    second_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:1",
        session_key="reimport",
    )
    second = store.get_observation(second_id)
    assert second.disposition == ObservationDisposition.IGNORED
    assert second.record_id == record_id
    assert "blocked by an explicit deletion" in (second.decision_reason or "")
    assert store.get_memory_truth(record_id).status == MemoryTruthStatus.DELETED
    assert store.status()["counts"]["active_records"] == 0
    with store.connect() as connection:
        row = connection.execute(
            "SELECT deletion_origin FROM deletion_tombstones WHERE record_id=?",
            (record_id,),
        ).fetchone()
    assert row is not None
    assert row["deletion_origin"] == "ordinary"
    assert tombstone["record_id"] == record_id


def test_same_source_reference_different_values_do_not_collapse(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = store.add_source(
        b"fiction archive",
        source_service="fiction-provider",
        source_type="provider_archive",
    )

    first_id = _archive_observation(
        store,
        source.id,
        content="I prefer concise answers.",
        source_reference="message:collision",
        session_key="collision-first",
    )
    first = store.get_observation(first_id)
    assert first.record_id is not None
    record_id = first.record_id
    assert store.withdraw_automatic_source_records(source.id) == [record_id]

    second_id = _archive_observation(
        store,
        source.id,
        content="I prefer detailed answers.",
        source_reference="message:collision",
        session_key="collision-second",
    )
    second = store.get_observation(second_id)
    assert second.record_id is not None
    assert second.record_id != record_id
    assert store.get_memory_truth(record_id).status == MemoryTruthStatus.DELETED


def test_truth_projection_exposes_evidence_times_and_content_free_coverage(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path)
    with TestClient(create_app(config)) as client:
        proposed = client.post(
            "/v1/ingestion/propose",
            json={
                "kind": "fact",
                "content": "The synthetic truth endpoint is local.",
                "evidence": "explicit synthetic fixture",
                "observed_at": "2026-01-02T03:04:05+00:00",
                "valid_from": "2026-01-01T00:00:00+00:00",
                "explicit_user_statement": True,
            },
        )
        assert proposed.status_code == 200, proposed.text
        record_id = str(proposed.json()["record_id"])

        truth = client.get(f"/v1/context/truth/{record_id}")
        assert truth.status_code == 200, truth.text
        payload = truth.json()
        assert payload["status"] == "current"
        assert payload["record"]["status"] == "current"
        assert payload["evidence"][0]["observation_id"] == proposed.json()["id"]
        assert payload["evidence"][0]["effective_at"] == "2026-01-01T00:00:00+00:00"
        assert payload["evidence"][0]["observed_at"] == "2026-01-02T03:04:05+00:00"
        assert payload["evidence"][0]["recorded_at"]
        assert payload["history_count"] == 1

        coverage = client.get("/v1/context/coverage")
        assert coverage.status_code == 200, coverage.text
        coverage_payload = coverage.json()
        assert coverage_payload["record_count"] == 1
        assert coverage_payload["observations_by_disposition"]["applied"] == 1
        assert "content" not in coverage_payload

        admin_truth = client.get("/v1/admin/memory-truth")
        assert admin_truth.status_code == 200, admin_truth.text
        assert admin_truth.json()["items"][0]["record"]["id"] == record_id


def test_truth_status_distinguishes_conflict_supersession_and_deletion(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first = store.approve_candidate(
        store.add_candidate(
            CandidateInput(
                kind="fact",
                content="The synthetic city is Boston.",
                entity_key="synthetic-user",
                attribute_key="city",
            )
        ).id
    )
    peer = store.approve_candidate(
        store.add_candidate(
            CandidateInput(
                kind="fact",
                content="The synthetic city is Chicago.",
                entity_key="synthetic-user",
                attribute_key="city",
            )
        ).id
    )
    assert store.get_memory_truth(first.id).status == MemoryTruthStatus.CONFLICTED
    assert store.get_memory_truth(peer.id).status == MemoryTruthStatus.CONFLICTED

    replacement = store.approve_candidate(
        store.add_candidate(
            CandidateInput(
                kind="fact",
                content="The synthetic city is Seattle.",
                entity_key="synthetic-user",
                attribute_key="city",
                supersedes=first.id,
            )
        ).id
    )
    assert store.get_memory_truth(first.id).status == MemoryTruthStatus.SUPERSEDED
    store.delete_record(replacement.id, reason="synthetic deletion")
    assert store.get_memory_truth(replacement.id).status == MemoryTruthStatus.DELETED
