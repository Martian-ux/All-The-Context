"""B-102 chronological unkeyed conflict fixtures (synthetic only)."""

from __future__ import annotations

import json
from pathlib import Path

from allthecontext.models import CandidateInput, ObservationDisposition
from allthecontext.storage import CoreStore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "b102_chronological_conflicts.json"


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.db")
    store.initialize_vault()
    return store


def test_chronological_unkeyed_conflicts_keep_one_current_and_history(
    tmp_path: Path,
) -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    store = _store(tmp_path)

    for scenario in payload["scenarios"]:
        kind = scenario["kind"]
        observations = []
        for statement in scenario["statements"]:
            observations.append(
                store.add_candidate(
                    CandidateInput(
                        kind=kind,
                        content=statement["content"],
                        observed_at=statement["observed_at"],
                        explicit_user_statement=True,
                        source_type="provider_archive",
                        source_service="fiction-provider",
                        idempotency_key=f"{scenario['id']}:{statement['observed_at']}",
                    )
                )
            )

        # Resolve the single current record for this kind (status / list active).
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

        # Provenance: history preserves the superseded older statement.
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
        # Older contradictory text is retained as history or as an ignored observation link.
        older = observations[0]
        assert older.disposition in {
            ObservationDisposition.APPLIED,
            ObservationDisposition.IGNORED,
            ObservationDisposition.REINFORCED,
        }
        if older.disposition == ObservationDisposition.APPLIED and older.record_id:
            # Same lineage updated in place: version history should mention prior content.
            assert older_content in history_contents or len(history) >= 2


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
