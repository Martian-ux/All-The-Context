from __future__ import annotations

from pathlib import Path

from allthecontext.models import (
    ApprovalRequest,
    BootstrapRequest,
    CandidateInput,
    ContextRecordOut,
)
from allthecontext.retrieval import ContextCompiler, RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

_BUDGET = 4_000
_PREFERENCE_COUNT = 77
_RELEVANT_COUNT = 20
_QUERY_COUNT = 10


def _record(record_id: str, kind: str, content: str, **values: object) -> ContextRecordOut:
    return ContextRecordOut(
        id=record_id,
        kind=kind,
        content=content,
        version=1,
        content_hash=record_id,
        created_at="2025-01-01T00:00:00+00:00",
        updated_at="2025-01-01T00:00:00+00:00",
        **values,
    )


def _synthetic_preferences() -> list[ContextRecordOut]:
    return [
        _record(
            f"preference-{index:02d}",
            "interaction_preference",
            (
                f"Synthetic preference rule {index:02d} "
                f"token{index:02d} axis{index * 7} marker{index * index}."
            ),
        )
        for index in range(_PREFERENCE_COUNT)
    ]


def _synthetic_relevant_records() -> list[ContextRecordOut]:
    return [
        _record(
            f"relevant-{index:02d}",
            "fact",
            (
                f"Synthetic generic topic {index // 2:02d} answer "
                f"signal{index:02d} facet{index * 11} marker{index * index}."
            ),
            scopes=["project:synthetic"],
            tags=[f"topic-{index // 2:02d}"],
            source_reference=f"synthetic-source-{index:02d}",
            source_service="synthetic-regression",
            source_type="synthetic_fact",
        )
        for index in range(_RELEVANT_COUNT)
    ]


def test_high_cardinality_compiler_keeps_relevant_records_across_generic_queries() -> None:
    preferences = _synthetic_preferences()
    relevant = _synthetic_relevant_records()
    compiler = ContextCompiler()

    for query_index in range(_QUERY_COUNT):
        query_records = relevant[query_index * 2 : query_index * 2 + 2]
        selected, used, metadata = compiler.compile_with_diagnostics(
            preferences,
            query_records,
            _BUDGET,
        )
        selected_ids = {item.id for item in selected}
        selected_preferences = [item for item in selected if item.kind == "interaction_preference"]
        selected_relevant = [item for item in selected if item.kind != "interaction_preference"]

        assert len(preferences) == _PREFERENCE_COUNT
        assert len(relevant) == _RELEVANT_COUNT
        assert len(selected) <= 32
        assert used == sum(len(item.content) + 64 for item in selected)
        assert used <= _BUDGET
        assert 8 <= len(selected_preferences) < len(preferences)
        assert {item.id for item in query_records} <= selected_ids
        assert selected_relevant
        assert selected_preferences and selected_relevant
        assert len(selected_ids) == len(selected)
        assert len(
            {
                (item.entity_key, item.attribute_key)
                for item in selected
                if item.entity_key is not None and item.attribute_key is not None
            }
        ) == sum(
            item.entity_key is not None and item.attribute_key is not None for item in selected
        )
        assert all(item.source_reference is not None for item in selected_relevant)
        assert metadata.candidate_count == len(preferences) + len(query_records)
        assert metadata.selected_count == len(selected)
        assert metadata.omitted_count == metadata.candidate_count - metadata.selected_count
        assert metadata.used_chars == used
        assert metadata.budget_chars == _BUDGET


def test_high_cardinality_no_match_returns_bounded_preferences() -> None:
    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        _synthetic_preferences(),
        [],
        _BUDGET,
    )

    assert len(selected) == 8
    assert all(item.kind == "interaction_preference" for item in selected)
    assert used == sum(len(item.content) + 64 for item in selected)
    assert metadata.candidate_count == _PREFERENCE_COUNT
    assert metadata.omitted_count == _PREFERENCE_COUNT - 8
    assert metadata.used_chars == used
    assert metadata.truncated is False


def test_high_cardinality_compiler_is_stable_when_input_is_reordered() -> None:
    preferences = _synthetic_preferences()
    relevant = _synthetic_relevant_records()[:2]
    compiler = ContextCompiler()

    forward = compiler.compile_with_diagnostics(preferences, relevant, _BUDGET)
    reverse = compiler.compile_with_diagnostics(
        list(reversed(preferences)),
        relevant,
        _BUDGET,
    )

    assert [item.id for item in forward[0]] == [item.id for item in reverse[0]]
    assert forward[1:] == reverse[1:]


def test_high_cardinality_preserves_caller_rank_for_duplicate_relevant_records() -> None:
    higher_ranked = _record(
        "z-ranked-answer",
        "fact",
        "The ranked answer is cobalt for launch planning.",
        source_reference="synthetic-ranked-source",
    )
    lower_ranked_duplicate = _record(
        "a-lower-ranked-duplicate",
        "fact",
        "The ranked answer is cobalt for launch planning.",
        source_reference="synthetic-duplicate-source",
    )

    selected, _used, _metadata = ContextCompiler().compile_with_diagnostics(
        _synthetic_preferences(),
        [higher_ranked, lower_ranked_duplicate],
        _BUDGET,
    )

    selected_ids = {item.id for item in selected}
    assert higher_ranked.id in selected_ids
    assert lower_ranked_duplicate.id not in selected_ids


def test_high_cardinality_reserve_cannot_displace_fixed_mandatory_conflict() -> None:
    fixed = _record(
        "fixed-authoritative-slot",
        "fact",
        "The authoritative launch color is blue.",
        entity_key="project:synthetic",
        attribute_key="launch-color",
        explicit_user_statement=True,
    )
    conflicting_preference = _record(
        "preference-conflicting-slot",
        "interaction_preference",
        "Prefer green launch colors for synthetic projects.",
        entity_key="project:synthetic",
        attribute_key="launch-color",
    )
    preferences = [conflicting_preference, *_synthetic_preferences()[1:]]
    answer = _record("answer", "fact", "The feasible synthetic answer is cobalt.")

    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        [*preferences, fixed],
        [answer],
        _BUDGET,
    )

    selected_ids = {item.id for item in selected}
    selected_slots = [
        item
        for item in selected
        if item.entity_key == fixed.entity_key and item.attribute_key == fixed.attribute_key
    ]
    assert fixed.id in selected_ids
    assert conflicting_preference.id not in selected_ids
    assert selected_slots == [fixed]
    assert used == sum(len(item.content) + 64 for item in selected)
    assert metadata.used_chars == used


def test_high_cardinality_overflow_supports_any_selected_compatible_primary() -> None:
    preferences = _synthetic_preferences()
    higher_ranked = _record(
        "z-ranked-primary",
        "fact",
        "The ranked answer is cobalt for launch planning.",
        source_id="ranked-source",
        source_reference="synthetic-ranked-source",
    )
    evidence = _record(
        "supporting-evidence",
        "evidence",
        "Review transcript record confirms the launch decision.",
        source_id="ranked-source",
        source_reference="synthetic-evidence-source",
    )
    cheapest_anchor = _record(
        "a-cheapest-anchor",
        "fact",
        "The ranked answer is cobalt for launch planning.",
        source_reference="synthetic-anchor-source",
    )

    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        preferences,
        [higher_ranked, evidence, cheapest_anchor],
        _BUDGET,
    )

    selected_ids = {item.id for item in selected}
    selected_overflow = [
        item
        for item in selected
        if item.kind == "interaction_preference"
        and item.id not in {candidate.id for candidate in preferences[:8]}
    ]
    selected_order = [item.id for item in selected]
    assert higher_ranked.id in selected_ids
    assert cheapest_anchor.id not in selected_ids
    assert evidence.id in selected_ids
    assert selected_overflow
    assert selected_order.index(evidence.id) < selected_order.index(selected_overflow[0].id)
    assert used == sum(len(item.content) + 64 for item in selected)
    assert used <= _BUDGET
    assert metadata.used_chars == used


def test_low_cardinality_preserves_every_feasible_preference() -> None:
    preferences = [
        _record(
            f"preference-{index}",
            "interaction_preference",
            f"Low-cardinality preference {index} with unique marker {index * 13}.",
        )
        for index in range(8)
    ]
    relevant = _record("answer", "fact", "The feasible synthetic answer is cobalt.")

    selected, used = ContextCompiler().compile(preferences, [relevant], budget_chars=4_000)

    assert {item.id for item in preferences} <= {item.id for item in selected}
    assert relevant.id in {item.id for item in selected}
    assert used == sum(len(item.content) + 64 for item in selected)


def test_high_cardinality_tight_budget_fails_closed_but_keeps_a_feasible_pair() -> None:
    preferences = [
        _record(
            f"preference-{index:02d}",
            "interaction_preference",
            f"Tight preference {index:02d} unique{index * 17}.",
        )
        for index in range(_PREFERENCE_COUNT)
    ]
    relevant = _record("answer", "fact", "A feasible synthetic answer with a distinct marker.")
    budget = 220

    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        preferences,
        [relevant],
        budget,
    )

    assert relevant.id in {item.id for item in selected}
    assert sum(item.kind == "interaction_preference" for item in selected) == 1
    assert used == sum(len(item.content) + 64 for item in selected)
    assert used <= budget
    assert metadata.used_chars == used
    assert metadata.omitted_count == metadata.candidate_count - metadata.selected_count


def _apply_high_cardinality_store(store: CoreStore) -> None:
    store.initialize_vault("synthetic-high-cardinality", "UTC")
    for index in range(_PREFERENCE_COUNT):
        candidate = store.add_candidate(
            CandidateInput(
                kind="interaction_preference",
                content=(
                    f"Synthetic preference rule {index:02d} "
                    f"token{index:02d} axis{index * 7} marker{index * index}."
                ),
                idempotency_key=f"preference-{index:02d}",
            )
        )
        if candidate.disposition.value in {"staged", "tentative"}:
            store.approve_candidate(candidate.id, ApprovalRequest(), actor="synthetic-test")
    for index in range(_RELEVANT_COUNT):
        candidate = store.add_candidate(
            CandidateInput(
                kind="fact",
                content=(
                    f"Synthetic generic topic {index // 2:02d} answer "
                    f"signal{index:02d} facet{index * 11} marker{index * index}."
                ),
                scopes=["project:synthetic"],
                tags=[f"topic-{index // 2:02d}"],
                source_reference=f"synthetic-source-{index:02d}",
                source_service="synthetic-regression",
                source_type="synthetic_fact",
                idempotency_key=f"relevant-{index:02d}",
            )
        )
        if candidate.disposition.value in {"staged", "tentative"}:
            store.approve_candidate(candidate.id, ApprovalRequest(), actor="synthetic-test")
    for kind, content, values in (
        (
            "fact",
            "Synthetic generic topic 00 denied ACL record.",
            {"allowed_clients": ["other-reader"]},
        ),
        (
            "fact",
            "Synthetic generic topic 00 expired temporal record.",
            {"expires_at": "2020-01-01T00:00:00+00:00"},
        ),
        (
            "fact",
            "Synthetic generic topic 00 sensitive record.",
            {"sensitivity": "highly_sensitive", "allowed_clients": ["other-reader"]},
        ),
    ):
        candidate = store.add_candidate(
            CandidateInput(
                kind=kind,
                content=content,
                scopes=["project:synthetic"],
                idempotency_key=f"excluded-{kind}-{content[-8:]}",
                **values,
            )
        )
        if candidate.disposition.value in {"staged", "tentative"}:
            store.approve_candidate(candidate.id, ApprovalRequest(), actor="synthetic-test")


def test_high_cardinality_bootstrap_regression_has_no_policy_or_pack_violations(
    tmp_path: Path,
) -> None:
    store = CoreStore(tmp_path / "high-cardinality.sqlite3")
    _apply_high_cardinality_store(store)
    principal = ClientPrincipal(
        "reader",
        "Synthetic reader",
        frozenset({"context:read"}),
    )
    engine = RetrievalEngine(store)
    try:
        for query_index in range(_QUERY_COUNT):
            request = BootstrapRequest(
                query=f"generic topic {query_index:02d}",
                requested_scopes=["project:synthetic"],
                budget_chars=_BUDGET,
                current_project="synthetic",
            )
            response = engine.bootstrap(request, principal)
            contents = {item.content for item in response.items}
            expected = {
                item.content
                for item in _synthetic_relevant_records()[query_index * 2 : query_index * 2 + 2]
            }
            preference_count = sum(item.kind == "interaction_preference" for item in response.items)
            relevant_count = sum(item.kind != "interaction_preference" for item in response.items)

            assert expected <= contents
            assert preference_count > 0
            assert relevant_count > 0
            assert not (preference_count > 0 and relevant_count == 0)
            assert len(response.items) <= 32
            assert response.used_chars == sum(len(item.content) + 64 for item in response.items)
            assert response.used_chars <= _BUDGET
            assert response.pack_metadata is not None
            assert response.pack_metadata.selected_count == len(response.items)
            assert response.pack_metadata.omitted_count == (
                response.pack_metadata.candidate_count - len(response.items)
            )
            assert response.pack_metadata.used_chars == response.used_chars
            assert response.pack_metadata.budget_chars == _BUDGET
            assert len({item.id for item in response.items}) == len(response.items)
            assert all(item.sensitivity.value == "normal" for item in response.items)
            assert all("denied ACL record" not in item.content for item in response.items)
            assert all("expired temporal record" not in item.content for item in response.items)
            assert all("sensitive record" not in item.content for item in response.items)
            assert all(item.evidence is None for item in response.items)

            repeat = engine.bootstrap(request, principal)
            assert [item.id for item in repeat.items] == [item.id for item in response.items]
            assert repeat.used_chars == response.used_chars
            assert repeat.pack_metadata == response.pack_metadata

        no_match = engine.bootstrap(
            BootstrapRequest(
                query="unmatched lunar phrase",
                requested_scopes=["project:synthetic"],
                budget_chars=_BUDGET,
                current_project="synthetic",
            ),
            principal,
        )
        assert no_match.items
        assert all(item.kind == "interaction_preference" for item in no_match.items)
        assert len(no_match.items) == 8
        assert no_match.used_chars == sum(len(item.content) + 64 for item in no_match.items)
    finally:
        store.close()
