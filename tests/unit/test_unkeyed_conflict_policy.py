"""B-102 chronological unkeyed conflict fixtures (synthetic only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.export import create_export, restore_export
from allthecontext.memory_policy import archive_lineage_key, classify_sensitivity
from allthecontext.models import (
    CandidateInput,
    ClientCreate,
    CoverageReport,
    IngestionMode,
    ObservationDisposition,
    Sensitivity,
)
from allthecontext.recovery_admin import carry_forward_purge_tombstones
from allthecontext.storage import CoreStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "b102_chronological_conflicts.json"


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.db")
    store.initialize_vault()
    return store


def _import_archive_statements(
    store: CoreStore,
    *,
    kind: str,
    statements: list[dict[str, object]],
    scenario_id: str,
) -> list:
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["fiction-archive"],
        unavailable_sources=[],
        idempotency_key=f"begin-{scenario_id}",
    )
    session_id = str(session["session_id"])
    submitted_ids: list[str] = []
    for index, statement in enumerate(statements):
        batch = store.submit_batch(
            session_id,
            f"batch-{scenario_id}-{index}",
            [
                CandidateInput(
                    kind=kind,
                    content=str(statement["content"]),
                    observed_at=str(statement["observed_at"]),
                    explicit_user_statement=True,
                    source_type="provider_archive",
                    source_service="fiction-provider",
                    idempotency_key=f"{scenario_id}:{statement['observed_at']}",
                )
            ],
        )
        submitted_ids.extend(str(item) for item in batch["candidate_ids"])
    store.finish_ingestion(
        session_id,
        CoverageReport(available=["fiction-archive"], complete=True),
    )
    return [store.get_candidate(item_id) for item_id in submitted_ids]


def test_chronological_unkeyed_conflicts_keep_one_current_and_history(
    tmp_path: Path,
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    store = _store(tmp_path)

    for scenario in payload["scenarios"]:
        kind = scenario["kind"]
        observations = _import_archive_statements(
            store,
            kind=kind,
            statements=scenario["statements"],
            scenario_id=scenario["id"],
        )

        status = store.status()
        assert status["counts"]["active_records"] >= 1
        current_by_kind: dict[str, str] = {}
        for item in observations:
            if item.record_id is None:
                continue
            try:
                record = store.get_record(item.record_id)
            except Exception:
                continue
            current_by_kind[record.kind] = record.content

        expected_current = next(
            statement["content"]
            for statement in scenario["statements"]
            if statement["expected_current"]
        )
        assert current_by_kind.get(kind) == expected_current

        winner = next(
            item
            for item in reversed(observations)
            if item.disposition == ObservationDisposition.APPLIED and item.record_id
        )
        history = store.record_history(winner.record_id)
        assert len(history) >= 1
        history_contents = {entry.get("content") for entry in history}
        older_content = scenario["statements"][0]["content"]
        assert (
            expected_current in history_contents
            or expected_current == store.get_record(winner.record_id).content
        )
        older = observations[0]
        assert older.disposition in {
            ObservationDisposition.APPLIED,
            ObservationDisposition.IGNORED,
            ObservationDisposition.REINFORCED,
        }
        if older.disposition == ObservationDisposition.APPLIED and older.record_id:
            assert older_content in history_contents or len(history) >= 2


def test_two_unrelated_direct_unkeyed_goals_coexist(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is fiction project Alpha shipping.",
            explicit_user_statement=True,
        )
    )
    second = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is fiction project Beta research.",
            explicit_user_statement=True,
            idempotency_key="direct-goal-beta",
        )
    )
    assert first.disposition == ObservationDisposition.APPLIED
    assert second.disposition == ObservationDisposition.APPLIED
    assert first.record_id is not None
    assert second.record_id is not None
    assert first.record_id != second.record_id
    assert store.get_record(first.record_id).content == first.content
    assert store.get_record(second.record_id).content == second.content


def test_archive_import_does_not_replace_direct_same_kind_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    direct = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is the live direct fiction objective.",
            explicit_user_statement=True,
        )
    )
    assert direct.record_id is not None
    archive_observations = _import_archive_statements(
        store,
        kind="goal",
        statements=[
            {
                "content": "My goal was the older archive fiction objective.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            },
            {
                "content": "My goal is the newer archive fiction objective.",
                "observed_at": "2025-01-01T00:00:00+00:00",
                "expected_current": True,
            },
        ],
        scenario_id="archive-vs-direct",
    )
    # Direct record remains current and is not overwritten by archive lineage.
    assert store.get_record(direct.record_id).content == direct.content
    archive_winner = next(
        item
        for item in reversed(archive_observations)
        if item.disposition == ObservationDisposition.APPLIED and item.record_id
    )
    assert archive_winner.record_id != direct.record_id
    assert (
        store.get_record(archive_winner.record_id).content
        == "My goal is the newer archive fiction objective."
    )


def test_exact_unkeyed_reinforce_does_not_duplicate_current(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.add_candidate(
        CandidateInput(
            kind="workflow",
            content="I use fiction-shell Beta for local automation.",
            observed_at="2025-01-01T00:00:00+00:00",
            explicit_user_statement=True,
        )
    )
    second = store.add_candidate(
        CandidateInput(
            kind="workflow",
            content="I use fiction-shell Beta for local automation.",
            observed_at="2025-02-01T00:00:00+00:00",
            explicit_user_statement=True,
            idempotency_key="reinforce-same",
        )
    )
    assert first.disposition == ObservationDisposition.APPLIED
    assert second.disposition == ObservationDisposition.REINFORCED
    assert second.record_id == first.record_id


def test_source_less_archive_reimport_routes_around_user_superseder(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer concise answers.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-user-superseder-original",
    )[0]
    assert original.disposition == ObservationDisposition.APPLIED
    assert original.record_id is not None

    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["fiction-archive"],
        unavailable_sources=[],
        idempotency_key="source-less-user-superseder-correction",
    )
    submitted = store.submit_batch(
        str(session["session_id"]),
        "source-less-user-superseder-correction-batch",
        [
            CandidateInput(
                kind="interaction_preference",
                content="I prefer detailed answers.",
                observed_at="2025-01-01T00:00:00+00:00",
                explicit_user_statement=True,
                supersedes=original.record_id,
            )
        ],
    )
    corrected = store.approve_candidate(str(submitted["candidate_ids"][0]))
    assert corrected.supersedes == original.record_id
    stale = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer concise answers.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-user-superseder-stale",
    )[0]

    assert stale.disposition == ObservationDisposition.IGNORED
    assert stale.record_id == corrected.id
    assert store.get_record(corrected.id).content == "I prefer detailed answers."


def test_source_less_archive_correction_and_slot_change_block_stale_replay(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer concise answers.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-correction-original",
    )[0]
    assert original.record_id is not None

    corrected = store.correct_record(
        original.record_id,
        content="I prefer dark mode.",
        reason="source-less slot-changing correction",
        entity_key="user",
        attribute_key="style",
    )
    stale = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer concise answers.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-correction-stale",
    )[0]

    assert corrected.entity_key == "user"
    assert corrected.attribute_key == "style"
    assert stale.disposition == ObservationDisposition.IGNORED
    assert stale.record_id == original.record_id
    assert store.get_record(original.record_id).content == "I prefer dark mode."
    assert store.status()["counts"]["active_records"] == 1


def test_source_less_archive_delete_blocks_stale_replay(tmp_path: Path) -> None:
    store = _store(tmp_path)
    original = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer concise answers.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-delete-original",
    )[0]
    assert original.record_id is not None

    store.delete_record(original.record_id, reason="source-less deletion barrier")
    replays = [
        _import_archive_statements(
            store,
            kind="interaction_preference",
            statements=[
                {
                    "content": "I prefer concise answers.",
                    "observed_at": "2024-01-01T00:00:00+00:00",
                }
            ],
            scenario_id=f"source-less-delete-replay-{index}",
        )[0]
        for index in ("one", "two")
    ]

    assert all(item.disposition == ObservationDisposition.IGNORED for item in replays)
    assert all(item.record_id == original.record_id for item in replays)
    assert store.get_memory_truth(original.record_id).status.value == "deleted"
    assert store.status()["counts"]["active_records"] == 0


def test_source_less_archive_ambiguity_fails_closed_without_replacement(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    first_statement = {
        "content": "I prefer dark mode.",
        "observed_at": "2024-01-01T00:00:00+00:00",
    }
    second_statement = {
        "content": "I prefer light mode.",
        "observed_at": "2024-01-01T00:00:00+00:00",
    }
    first = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[first_statement],
        scenario_id="source-less-ambiguity-first",
    )[0]
    second = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[second_statement],
        scenario_id="source-less-ambiguity-second",
    )[0]
    assert first.record_id is not None and second.record_id is not None
    assert first.record_id != second.record_id

    for record_id, content in (
        (first.record_id, "I prefer high contrast mode."),
        (second.record_id, "I prefer dim mode."),
    ):
        store.correct_record(
            record_id,
            content=content,
            reason="source-less ambiguity correction",
            entity_key="user",
            attribute_key="style",
        )

    stale = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[first_statement],
        scenario_id="source-less-ambiguity-stale",
    )[0]

    assert stale.disposition == ObservationDisposition.TENTATIVE
    assert stale.record_id is None
    assert store.status()["counts"]["active_records"] == 2


def test_source_less_archive_purge_barrier_survives_restart_and_restore(
    tmp_path: Path,
) -> None:
    database = tmp_path / "core.db"
    store = _store(tmp_path)
    original = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer concise answers.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-purge-original",
    )[0]
    assert original.record_id is not None
    export_path = tmp_path / "before-source-less-purge.atcexp"
    create_export(database, export_path, "source-less-purge-passphrase")

    store.correct_record(
        original.record_id,
        content="I prefer dark mode.",
        reason="source-less purge correction",
        entity_key="user",
        attribute_key="style",
    )
    store.delete_record(original.record_id, reason="source-less purge deletion")
    store.purge(
        "record",
        original.record_id,
        confirmation=store.purge_confirmation_phrase("record", original.record_id),
        compact=False,
    )

    restarted = CoreStore(database)
    restarted.initialize_vault()
    with restarted.connect() as connection:
        barrier_count = connection.execute(
            "SELECT COUNT(*) FROM archive_source_less_purge_barriers"
        ).fetchone()[0]
        barrier = connection.execute(
            "SELECT * FROM archive_source_less_purge_barriers"
        ).fetchone()
    assert barrier_count == 1
    assert barrier is not None
    assert set(barrier.keys()) == {"vault_id", "source_kind", "barrier_digest", "purged_at"}
    assert "concise" not in repr(tuple(barrier))
    assert "dark mode" not in repr(tuple(barrier))

    replay = _import_archive_statements(
        restarted,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer concise answers.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-purge-replay",
    )[0]
    assert replay.disposition == ObservationDisposition.IGNORED
    assert replay.record_id is None
    assert restarted.status()["counts"]["active_records"] == 0

    isolated_database = tmp_path / "isolated.db"
    isolated = CoreStore(isolated_database)
    isolated.initialize_vault()
    carried = carry_forward_purge_tombstones(database, isolated_database)
    assert carried["carried_archive_source_less_purge_barriers"] == 1
    restore_export(export_path, isolated_database, "source-less-purge-passphrase")
    restored = _import_archive_statements(
        isolated,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer concise answers.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-purge-restore-replay",
    )[0]
    assert restored.disposition == ObservationDisposition.IGNORED
    assert restored.record_id is None
    assert isolated.status()["counts"]["active_records"] == 0


def test_source_less_archive_barriers_do_not_collide_across_kind_or_claim(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    original = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer concise answers.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-isolation-original",
    )[0]
    assert original.record_id is not None
    store.correct_record(
        original.record_id,
        content="I prefer dark mode.",
        reason="source-less isolation correction",
        entity_key="user",
        attribute_key="style",
    )

    different_kind = _import_archive_statements(
        store,
        kind="goal",
        statements=[
            {
                "content": "My goal is to write fiction in Boston.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-isolation-kind",
    )[0]
    different_claim = _import_archive_statements(
        store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer Python examples for project Alpha.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            }
        ],
        scenario_id="source-less-isolation-claim",
    )[0]

    assert different_kind.disposition == ObservationDisposition.APPLIED
    assert different_claim.disposition == ObservationDisposition.APPLIED
    assert different_kind.record_id not in {None, original.record_id}
    assert different_claim.record_id not in {None, original.record_id}
    assert store.status()["counts"]["active_records"] == 3


def test_different_kinds_do_not_collide(tmp_path: Path) -> None:
    store = _store(tmp_path)
    goal = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is fiction isolation of kinds.",
            explicit_user_statement=True,
        )
    )
    constraint = store.add_candidate(
        CandidateInput(
            kind="constraint",
            content="We must keep fiction kinds independent.",
            explicit_user_statement=True,
        )
    )
    assert goal.disposition == ObservationDisposition.APPLIED
    assert constraint.disposition == ObservationDisposition.APPLIED
    assert goal.record_id != constraint.record_id


def test_reverse_chronological_archive_import_keeps_newer_current(tmp_path: Path) -> None:
    """Older statements arriving after newer ones must not become concurrent truth."""

    store = _store(tmp_path)
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["fiction-archive"],
        unavailable_sources=[],
        idempotency_key="reverse-chrono-begin",
    )
    newer = store.submit_batch(
        str(session["session_id"]),
        "batch-newer",
        [
            CandidateInput(
                kind="preference",
                content="Prefer detailed answers for fiction reverse chrono.",
                observed_at="2025-06-15T09:30:00+00:00",
                explicit_user_statement=True,
                source_type="provider_archive",
                source_service="fiction-provider",
            )
        ],
    )
    older = store.submit_batch(
        str(session["session_id"]),
        "batch-older",
        [
            CandidateInput(
                kind="preference",
                content="Prefer short answers for fiction reverse chrono.",
                observed_at="2024-01-10T12:00:00+00:00",
                explicit_user_statement=True,
                source_type="provider_archive",
                source_service="fiction-provider",
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fiction-archive"], complete=True),
    )
    newer_obs = store.get_candidate(str(newer["candidate_ids"][0]))
    older_obs = store.get_candidate(str(older["candidate_ids"][0]))
    assert newer_obs.disposition == ObservationDisposition.APPLIED
    assert newer_obs.record_id is not None
    assert older_obs.disposition == ObservationDisposition.IGNORED
    assert older_obs.record_id == newer_obs.record_id
    assert (
        store.get_record(newer_obs.record_id).content
        == "Prefer detailed answers for fiction reverse chrono."
    )


def test_unrelated_archive_goals_remain_independent_current_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    observations = _import_archive_statements(
        store,
        kind="goal",
        statements=[
            {
                "content": "My goal is fiction project Alpha shipping.",
                "observed_at": "2024-01-01T00:00:00+00:00",
            },
            {
                "content": "My goal is fiction language study of Spanish.",
                "observed_at": "2024-06-01T00:00:00+00:00",
            },
        ],
        scenario_id="unrelated-goals",
    )
    applied = [item for item in observations if item.disposition == ObservationDisposition.APPLIED]
    assert len(applied) == 2
    assert applied[0].record_id is not None
    assert applied[1].record_id is not None
    assert applied[0].record_id != applied[1].record_id
    assert store.get_record(applied[0].record_id).version == 1
    assert store.get_record(applied[1].record_id).version == 1
    assert store.status()["counts"]["active_records"] == 2


def test_archive_lineage_key_collapses_same_subject_not_kind() -> None:
    assert archive_lineage_key(
        "interaction_preference",
        "Prefer short answers for fiction scenario Alpha.",
    ) == archive_lineage_key(
        "interaction_preference",
        "Prefer detailed answers for fiction scenario Alpha.",
    )
    assert archive_lineage_key(
        "goal",
        "My goal is to ship fiction project Orion by March.",
    ) == archive_lineage_key(
        "goal",
        "My goal is to ship fiction project Orion by September.",
    )
    assert archive_lineage_key(
        "project",
        "I am working on fiction project Nebula.",
    ) != archive_lineage_key(
        "project",
        "I am working on fiction project Quasar.",
    )
    assert archive_lineage_key("note", "Unrelated kind has no archive slot.") is None


def test_archive_lineage_key_separates_preference_subjects_from_values() -> None:
    dark_mode = archive_lineage_key(
        "interaction_preference",
        "I prefer dark mode",
    )
    light_mode = archive_lineage_key(
        "interaction_preference",
        "I prefer light mode",
    )
    concise_answers = archive_lineage_key(
        "interaction_preference",
        "I prefer concise answers",
    )

    assert dark_mode is not None
    assert dark_mode == light_mode
    assert dark_mode != concise_answers


def test_preference_choice_values_share_lineage_only_when_purpose_matches() -> None:
    python_alpha = archive_lineage_key(
        "preference",
        "I prefer Python for fiction project Alpha",
    )
    rust_alpha = archive_lineage_key(
        "preference",
        "I prefer Rust for fiction project Alpha",
    )
    python_beta = archive_lineage_key(
        "preference",
        "I prefer Python for fiction project Beta",
    )
    python_examples = archive_lineage_key(
        "preference",
        "I prefer Python examples",
    )
    rust_examples = archive_lineage_key(
        "preference",
        "I prefer Rust examples",
    )

    assert python_alpha is not None
    assert python_alpha == rust_alpha
    assert python_alpha != python_beta
    assert python_examples != rust_examples


def test_archive_preference_revisions_share_lineage_but_direct_records_do_not(
    tmp_path: Path,
) -> None:
    revision_store = _store(tmp_path / "revisions.db")
    observations = _import_archive_statements(
        revision_store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer dark mode",
                "observed_at": "2024-01-01T00:00:00+00:00",
            },
            {
                "content": "I prefer light mode",
                "observed_at": "2025-01-01T00:00:00+00:00",
            },
        ],
        scenario_id="preference-values",
    )

    assert observations[0].record_id is not None
    assert observations[1].record_id == observations[0].record_id
    assert observations[1].disposition == ObservationDisposition.APPLIED
    assert revision_store.get_record(observations[1].record_id).content == ("I prefer light mode")
    assert revision_store.status()["counts"]["active_records"] == 1

    direct_store = _store(tmp_path / "direct.db")
    direct = direct_store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="I prefer dark mode",
            explicit_user_statement=True,
        )
    )
    assert direct.record_id is not None
    archive_observation = _import_archive_statements(
        direct_store,
        kind="interaction_preference",
        statements=[
            {
                "content": "I prefer light mode",
                "observed_at": "2025-01-01T00:00:00+00:00",
            },
        ],
        scenario_id="preference-vs-direct",
    )

    assert archive_observation[0].record_id is not None
    assert archive_observation[0].record_id != direct.record_id
    assert direct_store.get_record(archive_observation[0].record_id).content == (
        "I prefer light mode"
    )
    assert direct_store.get_record(direct.record_id).content == "I prefer dark mode"
    assert direct_store.status()["counts"]["active_records"] == 2


def test_archive_preference_choices_for_same_purpose_supersede(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    observations = _import_archive_statements(
        store,
        kind="preference",
        statements=[
            {
                "content": "I prefer Python for fiction project Alpha",
                "observed_at": "2024-01-01T00:00:00+00:00",
            },
            {
                "content": "I prefer Rust for fiction project Alpha",
                "observed_at": "2025-01-01T00:00:00+00:00",
            },
        ],
        scenario_id="preference-choice-purpose",
    )

    assert observations[0].record_id is not None
    assert observations[1].record_id == observations[0].record_id
    assert observations[1].disposition == ObservationDisposition.APPLIED
    assert store.get_record(observations[1].record_id).content == (
        "I prefer Rust for fiction project Alpha"
    )
    assert store.status()["counts"]["active_records"] == 1


def test_classify_sensitivity_is_conservative_for_health_and_location() -> None:
    assert classify_sensitivity("I prefer concise technical answers.") == Sensitivity.NORMAL
    assert (
        classify_sensitivity("I was diagnosed with asthma and use an inhaler.")
        == Sensitivity.SENSITIVE
    )
    assert (
        classify_sensitivity("I live in Seattle for the fiction scenario.") == Sensitivity.SENSITIVE
    )
    assert (
        classify_sensitivity("My wife works remotely in the fiction lab.") == Sensitivity.SENSITIVE
    )
    assert classify_sensitivity("My salary is listed with a bank account.") == Sensitivity.SENSITIVE
    assert classify_sensitivity("My social security number is 123-45-6789.") == (
        Sensitivity.HIGHLY_SENSITIVE
    )


@pytest.mark.parametrize(
    "content",
    (
        "My partner lives in Seattle.",
        "My significant other resides in Seattle.",
        "I reside in Boston.",
        "I am residing in Boston.",
        "I currently reside in Boston.",
        "I have HIV.",
        "I live with HIV.",
        "I have a medical condition.",
        "My mortgage is with a bank.",
        "I have a mortgage with a credit union.",
    ),
)
def test_personally_framed_sensitivity_gaps_are_localized(content: str) -> None:
    assert classify_sensitivity(content) == Sensitivity.SENSITIVE


@pytest.mark.parametrize(
    "content",
    (
        "The partner function is used by the parser.",
        "Mortgage rates are included in the technical example.",
        "HIV is discussed in the medical training material.",
        "A fictional character resides in Boston.",
    ),
)
def test_unframed_technical_and_general_text_is_not_promoted(content: str) -> None:
    assert classify_sensitivity(content) == Sensitivity.NORMAL


def test_unattested_unkeyed_client_contradictions_do_not_become_current(
    tmp_path: Path,
) -> None:
    """Non-witness explicit claims stay tentative; contradictions never both apply."""

    store = _store(tmp_path)
    plain, _ = store.create_client(ClientCreate(name="plain", scopes=["context:propose"]))
    first = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is fiction unattested Alpha.",
            explicit_user_statement=True,
            observed_at="2024-01-01T00:00:00+00:00",
        ),
        client=plain,
    )
    second = store.add_candidate(
        CandidateInput(
            kind="goal",
            content="My goal is fiction unattested Beta.",
            explicit_user_statement=True,
            observed_at="2025-01-01T00:00:00+00:00",
            idempotency_key="unattested-beta",
        ),
        client=plain,
    )
    assert first.disposition == ObservationDisposition.TENTATIVE
    assert second.disposition == ObservationDisposition.TENTATIVE
    assert first.record_id is None
    assert second.record_id is None
    assert store.status()["counts"]["active_records"] == 0
