from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from allthecontext.models import (
    Availability,
    CandidateInput,
    CoverageReport,
    IngestionMode,
    ObservationDisposition,
    Sensitivity,
)
from allthecontext.storage import CoreStore, InvalidStateError


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.db")
    store.initialize_vault()
    return store


def test_explicit_observation_is_applied_without_review(tmp_path: Path) -> None:
    store = _store(tmp_path)

    observation = store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Prefer direct technical answers.",
            explicit_user_statement=True,
        )
    )

    assert observation.disposition == ObservationDisposition.APPLIED
    assert observation.record_id is not None
    assert observation.observation_origin == "local_admin"
    assert observation.policy_version == "automatic-v1"
    assert store.get_record(observation.record_id).content == observation.content
    assert store.status()["counts"]["pending_candidates"] == 0


def test_inference_requires_explicit_corroboration_before_becoming_current(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    candidate = CandidateInput(
        kind="workflow",
        content="Uses PowerShell for local automation.",
        confidence=0.9,
        explicit_user_statement=False,
    )

    first = store.add_candidate(candidate)
    second = store.add_candidate(
        candidate.model_copy(update={"idempotency_key": "independent-second-signal"})
    )
    explicit = store.add_candidate(
        candidate.model_copy(
            update={
                "explicit_user_statement": True,
                "idempotency_key": "explicit-user-evidence",
            }
        )
    )

    assert first.disposition == ObservationDisposition.TENTATIVE
    assert second.disposition == ObservationDisposition.TENTATIVE
    assert explicit.disposition == ObservationDisposition.APPLIED
    assert explicit.record_id is not None
    assert store.get_candidate(first.id).disposition == ObservationDisposition.REINFORCED
    assert store.get_candidate(second.id).disposition == ObservationDisposition.REINFORCED
    assert store.get_candidate(first.id).record_id == explicit.record_id


def test_secret_is_refused_before_write_and_sensitive_context_stays_local(tmp_path: Path) -> None:
    store = _store(tmp_path)

    secrets = (
        CandidateInput(
            kind="fact",
            content="API key: do-not-store-this",
            explicit_user_statement=True,
        ),
        CandidateInput(
            kind="fact",
            content="My password is do-not-store-this",
            explicit_user_statement=True,
        ),
        CandidateInput(
            kind="fact",
            content="Benign summary",
            structured_value={"api_key": "do-not-store-this"},
            explicit_user_statement=True,
        ),
        CandidateInput(
            kind="fact",
            content="Benign summary",
            evidence="Access token = do-not-store-this",
            explicit_user_statement=True,
        ),
    )
    for secret in secrets:
        with pytest.raises(InvalidStateError):
            store.add_candidate(secret)
    sensitive = store.add_candidate(
        CandidateInput(
            kind="personal_context",
            content="A sensitive local detail.",
            sensitivity=Sensitivity.SENSITIVE,
            availability=Availability.ALWAYS,
            explicit_user_statement=True,
        )
    )

    assert store.list_observations()[1] == 1
    assert sensitive.disposition == ObservationDisposition.APPLIED
    assert sensitive.record_id is not None
    assert store.get_record(sensitive.record_id).availability == Availability.LOCAL


@pytest.mark.parametrize(
    "content",
    (
        "My partner lives in Seattle.",
        "I reside in Boston.",
        "I have HIV.",
        "My mortgage is with a bank.",
    ),
)
def test_personal_sensitivity_classifier_forces_local_only(tmp_path: Path, content: str) -> None:
    store = _store(tmp_path)

    observation = store.add_candidate(
        CandidateInput(
            kind="personal_context",
            content=content,
            availability=Availability.CORE,
            explicit_user_statement=True,
        )
    )

    assert observation.sensitivity == Sensitivity.SENSITIVE
    assert observation.record_id is not None
    record = store.get_record(observation.record_id)
    assert record.sensitivity == Sensitivity.SENSITIVE
    assert record.availability == Availability.LOCAL
    assert store.pending_replication_events() == []


def test_automatic_observation_cannot_opt_into_relay_availability(tmp_path: Path) -> None:
    store = _store(tmp_path)

    observation = store.add_candidate(
        CandidateInput(
            kind="preference",
            content="Use short headings.",
            availability=Availability.ALWAYS,
            explicit_user_statement=True,
        )
    )

    assert observation.record_id is not None
    assert store.get_record(observation.record_id).availability == Availability.CORE
    assert store.pending_replication_events() == []


def test_newer_explicit_slot_value_updates_stable_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.add_candidate(
        CandidateInput(
            kind="personal_detail",
            content="I am based in Boston.",
            entity_key="user",
            attribute_key="location",
            observed_at="2026-01-01T00:00:00+00:00",
            explicit_user_statement=True,
        )
    )
    assert first.record_id is not None

    newer = store.add_candidate(
        CandidateInput(
            kind="personal_detail",
            content="I am based in Philadelphia.",
            entity_key="user",
            attribute_key="location",
            observed_at="2026-07-01T00:00:00+00:00",
            explicit_user_statement=True,
        )
    )
    older = store.add_candidate(
        CandidateInput(
            kind="personal_detail",
            content="I am based in Chicago.",
            entity_key="user",
            attribute_key="location",
            observed_at="2025-01-01T00:00:00+00:00",
            explicit_user_statement=True,
        )
    )

    assert newer.disposition == ObservationDisposition.APPLIED
    assert newer.record_id == first.record_id
    assert store.get_record(first.record_id).content == "I am based in Philadelphia."
    assert older.disposition == ObservationDisposition.IGNORED
    assert older.record_id == first.record_id


def test_matching_text_in_different_slots_neither_merges_nor_corroborates(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)

    first = store.add_candidate(
        CandidateInput(
            kind="location",
            content="Boston",
            entity_key="user",
            attribute_key="location",
            explicit_user_statement=True,
        )
    )
    second = store.add_candidate(
        CandidateInput(
            kind="location",
            content="Boston",
            entity_key="company",
            attribute_key="headquarters",
            explicit_user_statement=True,
        )
    )
    tentative = store.add_candidate(
        CandidateInput(
            kind="location",
            content="Chicago",
            entity_key="user",
            attribute_key="future_location",
        )
    )
    other_slot = store.add_candidate(
        CandidateInput(
            kind="location",
            content="Chicago",
            entity_key="company",
            attribute_key="future_headquarters",
        )
    )

    assert first.record_id is not None
    assert second.record_id is not None
    assert first.record_id != second.record_id
    assert tentative.disposition == ObservationDisposition.TENTATIVE
    assert other_slot.disposition == ObservationDisposition.TENTATIVE
    assert other_slot.record_id is None


def test_explicit_correction_preserves_target_security_and_provenance(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = store.add_candidate(
        CandidateInput(
            kind="editor_preference",
            content="Use tabs.",
            structured_value={"indentation": "tabs"},
            entity_key="user",
            attribute_key="indentation",
            scopes=["project:private"],
            tags=["editor"],
            source_reference="settings#indentation",
            source_service="settings",
            source_type="explicit_setting",
            evidence="The user selected tabs.",
            sensitivity=Sensitivity.SENSITIVE,
            availability=Availability.ALWAYS,
            allowed_clients=["trusted-client"],
            denied_clients=["blocked-client"],
            explicit_user_statement=True,
        )
    )
    assert original.record_id is not None

    correction = store.add_candidate(
        CandidateInput(
            kind="correction",
            content="Use spaces.",
            supersedes=original.record_id,
            explicit_user_statement=True,
        )
    )
    updated = store.get_record(original.record_id)

    assert correction.disposition == ObservationDisposition.APPLIED
    assert updated.kind == "editor_preference"
    assert updated.structured_value == {"indentation": "tabs"}
    assert updated.entity_key == "user"
    assert updated.attribute_key == "indentation"
    assert updated.scopes == ["project:private"]
    assert updated.tags == ["editor"]
    assert updated.source_id is None
    assert updated.source_reference == "settings#indentation"
    assert updated.source_service == "settings"
    assert updated.source_type == "explicit_setting"
    assert updated.sensitivity == Sensitivity.SENSITIVE
    assert updated.availability == Availability.LOCAL
    assert updated.allowed_clients == ["trusted-client"]
    assert updated.denied_clients == ["blocked-client"]


def test_automatic_slot_update_never_loosens_security_or_acl(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = store.add_candidate(
        CandidateInput(
            kind="personal_detail",
            content="Old private value.",
            entity_key="user",
            attribute_key="private_setting",
            sensitivity=Sensitivity.SENSITIVE,
            availability=Availability.LOCAL,
            allowed_clients=["trusted-client"],
            denied_clients=["blocked-client"],
            observed_at="2026-01-01T00:00:00+00:00",
            explicit_user_statement=True,
        )
    )
    assert original.record_id is not None

    updated_observation = store.add_candidate(
        CandidateInput(
            kind="personal_detail",
            content="New private value.",
            entity_key="user",
            attribute_key="private_setting",
            observed_at="2026-07-01T00:00:00+00:00",
            explicit_user_statement=True,
        )
    )
    updated = store.get_record(original.record_id)

    assert updated_observation.record_id == original.record_id
    assert updated.sensitivity == Sensitivity.SENSITIVE
    assert updated.availability == Availability.LOCAL
    assert updated.allowed_clients == ["trusted-client"]
    assert updated.denied_clients == ["blocked-client"]


def test_disjoint_replacement_and_reinforcement_keep_acl_boundaries(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = store.add_candidate(
        CandidateInput(
            kind="slot_value",
            content="old slot value",
            entity_key="user",
            attribute_key="private_slot",
            allowed_clients=["old-client", "shared-client"],
            explicit_user_statement=True,
            observed_at="2026-01-01T00:00:00+00:00",
        )
    )
    assert original.record_id is not None

    replacement = store.add_candidate(
        CandidateInput(
            kind="slot_value",
            content="new slot value",
            entity_key="user",
            attribute_key="private_slot",
            allowed_clients=["new-client", "shared-client"],
            explicit_user_statement=True,
            observed_at="2026-02-01T00:00:00+00:00",
        )
    )
    assert replacement.disposition == ObservationDisposition.APPLIED
    assert store.get_record(original.record_id).content == "new slot value"
    assert store.get_record(original.record_id).allowed_clients == ["shared-client"]

    omitted_allowlist = store.add_candidate(
        CandidateInput(
            kind="slot_value",
            content="newer slot value",
            entity_key="user",
            attribute_key="private_slot",
            explicit_user_statement=True,
            observed_at="2026-03-01T00:00:00+00:00",
        )
    )
    assert omitted_allowlist.disposition == ObservationDisposition.APPLIED
    assert store.get_record(original.record_id).allowed_clients == ["shared-client"]

    reinforced_record = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="existing private value",
            allowed_clients=["old-client"],
            explicit_user_statement=True,
        )
    )
    reinforcement = store.add_candidate(
        CandidateInput(
            kind="fact",
            content="existing private value",
            allowed_clients=["new-client"],
            explicit_user_statement=True,
            idempotency_key="disjoint-reinforcement",
        )
    )
    assert reinforcement.disposition == ObservationDisposition.REINFORCED
    assert reinforced_record.record_id is not None
    assert store.get_record(reinforced_record.record_id).allowed_clients == ["old-client"]


def test_corroborated_inference_cannot_overwrite_explicit_target(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = store.add_candidate(
        CandidateInput(
            kind="editor_preference",
            content="Use tabs.",
            explicit_user_statement=True,
        )
    )
    assert original.record_id is not None
    inferred = CandidateInput(
        kind="editor_preference",
        content="Use spaces.",
        supersedes=original.record_id,
    )

    first = store.add_candidate(inferred)
    second = store.add_candidate(
        inferred.model_copy(update={"idempotency_key": "second-independent-signal"})
    )

    assert first.disposition == ObservationDisposition.TENTATIVE
    assert second.disposition == ObservationDisposition.TENTATIVE
    assert store.get_record(original.record_id).content == "Use tabs."


def test_archive_observations_stay_staged_until_successful_finish(tmp_path: Path) -> None:
    store = _store(tmp_path)
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["archive"],
        unavailable_sources=[],
    )
    submitted = store.submit_batch(
        str(session["session_id"]),
        "batch-1",
        [
            CandidateInput(
                kind="goal",
                content="Build a portable context system.",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    observation_id = str(submitted["candidate_ids"][0])

    assert store.get_candidate(observation_id).disposition == ObservationDisposition.STAGED
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["archive"], complete=True),
    )
    assert store.get_candidate(observation_id).disposition == ObservationDisposition.APPLIED


def test_archive_finish_recomputes_integrity_once_for_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["archive"],
        unavailable_sources=[],
    )
    submitted = store.submit_batch(
        str(session["session_id"]),
        "batch-many",
        [
            CandidateInput(
                kind="goal",
                content=f"Complete synthetic objective {index}.",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
            for index in range(3)
        ],
    )
    recomputes = 0
    original_recompute = store._recompute_integrity

    def counted_recompute(connection: sqlite3.Connection) -> None:
        nonlocal recomputes
        recomputes += 1
        original_recompute(connection)

    monkeypatch.setattr(store, "_recompute_integrity", counted_recompute)
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["archive"], complete=True),
    )

    observations = [store.get_candidate(str(item)) for item in submitted["candidate_ids"]]
    assert recomputes == 1
    assert all(item.disposition == ObservationDisposition.APPLIED for item in observations)
    assert all(item.record_id is not None for item in observations)


def test_archive_forget_recomputes_integrity_once_at_batch_finish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    current = store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Keep detailed technical context.",
            explicit_user_statement=True,
        )
    )
    assert current.record_id is not None
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["archive"],
        unavailable_sources=[],
    )
    submitted = store.submit_batch(
        str(session["session_id"]),
        "batch-forget",
        [
            CandidateInput(
                kind="context_forget",
                content="The user explicitly requested deletion.",
                source_type="provider_archive",
                supersedes=current.record_id,
                explicit_user_statement=True,
            )
        ],
    )
    recomputes = 0
    original_recompute = store._recompute_integrity

    def counted_recompute(connection: sqlite3.Connection) -> None:
        nonlocal recomputes
        recomputes += 1
        original_recompute(connection)

    monkeypatch.setattr(store, "_recompute_integrity", counted_recompute)
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["archive"], complete=True),
    )

    observation = store.get_candidate(str(submitted["candidate_ids"][0]))
    assert recomputes == 1
    assert observation.disposition == ObservationDisposition.APPLIED
    assert observation.record_id == current.record_id
    assert store.get_record(current.record_id, include_deleted=True).deleted_at is not None


def test_startup_staged_evaluation_recomputes_integrity_once_for_the_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["archive"],
        unavailable_sources=[],
        idempotency_key="legacy-finished-archive",
    )
    submitted = store.submit_batch(
        str(session["session_id"]),
        "batch-many",
        [
            CandidateInput(
                kind="goal",
                content=f"Recover synthetic objective {index}.",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
            for index in range(3)
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["archive"], complete=True),
        publish=False,
    )
    recomputes = 0
    original_recompute = store._recompute_integrity

    def counted_recompute(connection: sqlite3.Connection) -> None:
        nonlocal recomputes
        recomputes += 1
        original_recompute(connection)

    monkeypatch.setattr(store, "_recompute_integrity", counted_recompute)

    assert store.evaluate_staged_observations() == 3
    observations = [store.get_candidate(str(item)) for item in submitted["candidate_ids"]]
    assert recomputes == 1
    assert all(item.disposition == ObservationDisposition.APPLIED for item in observations)


def test_soft_delete_and_version_restore_are_reversible(tmp_path: Path) -> None:
    store = _store(tmp_path)
    observation = store.add_candidate(
        CandidateInput(
            kind="project_decision",
            content="Use SQLite.",
            explicit_user_statement=True,
        )
    )
    assert observation.record_id is not None
    record_id = observation.record_id
    store.correct_record(
        record_id,
        content="Use PostgreSQL.",
        reason="temporary experiment",
    )

    restored_version = store.restore_record(
        record_id,
        version=1,
        reason="undo experiment",
    )
    assert restored_version.content == "Use SQLite."

    store.delete_record(record_id, reason="temporary removal")
    restored_delete = store.restore_record(record_id, reason="undo removal")
    assert restored_delete.content == "Use SQLite."
    assert store.get_record(record_id).id == record_id
