from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.models import (
    ApprovalRequest,
    BootstrapRequest,
    CandidateInput,
    ContextRecordOut,
    SearchRequest,
    SearchResponse,
)
from allthecontext.retrieval import ContextCompiler, RetrievalEngine, _PipelineDiagnostics
from allthecontext.security import ClientPrincipal
from allthecontext.set_selection import DeterministicSetSelector
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


@pytest.mark.parametrize(
    ("mandatory_ids", "relevant_ids", "expected_count"),
    (
        (
            frozenset(f"pool-{index:03d}" for index in range(150)),
            frozenset(f"pool-{index:03d}" for index in range(150, 300)),
            300,
        ),
        (
            frozenset(f"pool-{index:03d}" for index in range(150)),
            frozenset(f"pool-{index:03d}" for index in range(100, 250)),
            250,
        ),
    ),
)
def test_bootstrap_candidate_metadata_unions_exact_bounded_pool_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mandatory_ids: frozenset[str],
    relevant_ids: frozenset[str],
    expected_count: int,
) -> None:
    store = CoreStore(tmp_path / "pool-accounting.sqlite3")
    store.initialize_vault("synthetic-pool-accounting", "UTC")
    engine = RetrievalEngine(store)
    mandatory_item = _record("mandatory-item", "interaction_preference", "Prefer concise output.")
    relevant_item = _record("relevant-item", "fact", "The scoped answer is cobalt.")

    def stub_search(
        request: object,
        principal: ClientPrincipal | None = None,
        *,
        bounded: bool = False,
    ) -> tuple[SearchResponse, tuple[object, ...], _PipelineDiagnostics]:
        del request, principal, bounded
        if not hasattr(stub_search, "calls"):
            stub_search.calls = 0  # type: ignore[attr-defined]
        stub_search.calls += 1  # type: ignore[attr-defined]
        if stub_search.calls == 1:  # type: ignore[attr-defined]
            items = [mandatory_item]
            pool_ids = mandatory_ids
        else:
            items = [relevant_item]
            pool_ids = relevant_ids
        return (
            SearchResponse(items=items, total=len(pool_ids), trace_id="synthetic-trace"),
            (),
            _PipelineDiagnostics(
                candidate_pool_count=len(pool_ids),
                candidate_pool_truncated=True,
                candidate_pool_ids=pool_ids,
            ),
        )

    monkeypatch.setattr(engine, "_search", stub_search)
    try:
        response = engine.bootstrap(BootstrapRequest(query="cobalt", budget_chars=4_000))
    finally:
        store.close()

    assert response.pack_metadata is not None
    assert response.pack_metadata.candidate_count == expected_count
    assert response.pack_metadata.omitted_count == expected_count - len(response.items)


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


def test_high_cardinality_fixed_mandatory_prepass_keeps_one_conflicting_survivor() -> None:
    preferences = [
        _record(
            f"preference-{index:02d}",
            "interaction_preference",
            f"p{index:02d} q{index:02d}",
        )
        for index in range(9)
    ]
    fixed_one = _record(
        "fixed-one",
        "fact",
        "f" * 36,
        entity_key="entity",
        attribute_key="slot",
    )
    fixed_two = _record(
        "fixed-two",
        "fact",
        "g" * 36,
        entity_key="entity",
        attribute_key="slot",
    )
    budget = 717

    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        [*preferences, fixed_one, fixed_two],
        [],
        budget,
    )

    selected_ids = {item.id for item in selected}
    selected_fixed = [
        item for item in selected if item.entity_key == "entity" and item.attribute_key == "slot"
    ]
    assert len(selected_fixed) == 1
    assert selected_fixed[0].id in {fixed_one.id, fixed_two.id}
    assert sum(item.kind == "interaction_preference" for item in selected) == 8
    assert used == sum(len(item.content) + 64 for item in selected)
    assert used <= budget
    assert metadata.used_chars == used
    assert len(selected_ids) == len(selected)


def test_high_cardinality_fixed_survivor_is_identical_after_preference_marginals() -> None:
    preferences = [
        _record(
            f"preference-{index:02d}",
            "interaction_preference",
            f"p{index:02d} q{index:02d} r{index:02d} s{index:02d}",
        )
        for index in range(9)
    ]
    fixed_a = _record(
        "fixed-a",
        "fact",
        "p00 q00 fixed overlap unique",
        entity_key="entity",
        attribute_key="slot",
    )
    fixed_b = _record(
        "fixed-b",
        "fact",
        "unique fixed survivor content",
        entity_key="entity",
        attribute_key="slot",
    )

    class RecordingSelector(DeterministicSetSelector):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[tuple[frozenset[str], frozenset[str]]] = []

        def select(self, candidates, constraints):  # type: ignore[no-untyped-def]
            selection = super().select(candidates, constraints)
            self.calls.append(
                (
                    frozenset(candidate.key for candidate in candidates),
                    frozenset(candidate.key for candidate in selection.candidates),
                )
            )
            return selection

    selector = RecordingSelector()
    selected, used, metadata = ContextCompiler(selector).compile_with_diagnostics(
        [*preferences, fixed_a, fixed_b],
        [],
        964,
    )

    fixed_input = frozenset({fixed_a.id, fixed_b.id})
    fixed_only = [
        selected_ids
        for candidate_ids, selected_ids in selector.calls
        if candidate_ids == fixed_input
    ]
    assert fixed_only == [frozenset({fixed_a.id})]
    final_fixed = {
        item.id for item in selected if item.entity_key == "entity" and item.attribute_key == "slot"
    }
    assert final_fixed == fixed_only[0]
    assert used == sum(len(item.content) + 64 for item in selected)
    assert used <= 964
    assert metadata.used_chars == used

    reordered_selector = RecordingSelector()
    reordered_selected, _reordered_used, _reordered_metadata = ContextCompiler(
        reordered_selector
    ).compile_with_diagnostics(
        [*reversed(preferences), fixed_a, fixed_b],
        [],
        964,
    )
    reordered_fixed = {
        item.id
        for item in reordered_selected
        if item.entity_key == "entity" and item.attribute_key == "slot"
    }
    reordered_fixed_only = [
        selected_ids
        for candidate_ids, selected_ids in reordered_selector.calls
        if candidate_ids == fixed_input
    ]
    assert reordered_fixed_only == [fixed_only[0]]
    assert reordered_fixed == final_fixed


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


def test_high_cardinality_large_supporting_evidence_precedes_overflow() -> None:
    preferences = _synthetic_preferences()[:9]
    primary = _record(
        "primary",
        "fact",
        "The selected primary answer is cobalt.",
        source_id="primary-source",
        source_reference="synthetic-primary-source",
    )
    anchor = _record(
        "anchor",
        "fact",
        "The additional compatible anchor is amber.",
        source_id="anchor-source",
        source_reference="synthetic-anchor-source",
    )
    evidence = _record(
        "large-supporting-evidence",
        "evidence",
        "support " * 375,
        source_id="primary-source",
        source_reference="synthetic-evidence-source",
    )

    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        preferences,
        [primary, anchor, evidence],
        budget_chars=5_000,
    )

    selected_ids = {item.id for item in selected}
    selected_order = [item.id for item in selected]
    overflow = "preference-08"
    assert primary.id in selected_ids
    assert anchor.id in selected_ids
    assert evidence.id in selected_ids
    assert overflow in selected_ids
    assert selected_order.index(primary.id) < selected_order.index(overflow)
    assert selected_order.index(anchor.id) < selected_order.index(overflow)
    assert selected_order.index(evidence.id) < selected_order.index(overflow)
    assert used == sum(len(item.content) + 64 for item in selected)
    assert used <= 5_000
    assert metadata.used_chars == used


def test_high_cardinality_overflow_falls_back_when_evidence_is_infeasible() -> None:
    preferences = _synthetic_preferences()[:9]
    primary = _record(
        "primary",
        "fact",
        "The feasible primary answer is cobalt.",
        source_id="primary-source",
    )
    evidence = _record(
        "infeasible-supporting-evidence",
        "evidence",
        "evidence " * 375,
        source_id="primary-source",
    )
    budget = 1_300

    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        preferences,
        [primary, evidence],
        budget,
    )

    selected_ids = {item.id for item in selected}
    selected_preferences = [item for item in selected if item.kind == "interaction_preference"]
    assert primary.id in selected_ids
    assert evidence.id not in selected_ids
    assert len(selected_preferences) > 8
    assert used == sum(len(item.content) + 64 for item in selected)
    assert used <= budget
    assert metadata.used_chars == used


def test_high_cardinality_evidence_wins_when_overflow_does_not_fit() -> None:
    preferences = [
        _record(
            f"preference-{index:02d}",
            "interaction_preference",
            f"p{index:02d} q{index:02d} r{index:02d} s{index:02d} t{index:02d}.",
        )
        for index in range(9)
    ]
    primary = _record(
        "primary",
        "fact",
        "primary",
        source_id="primary-source",
    )
    evidence = _record(
        "supporting-evidence",
        "evidence",
        "e" * 200,
        source_id="primary-source",
    )
    budget = 1_007

    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        preferences,
        [primary, evidence],
        budget,
    )

    selected_ids = {item.id for item in selected}
    assert primary.id in selected_ids
    assert evidence.id in selected_ids
    assert "preference-08" not in selected_ids
    assert used == sum(len(item.content) + 64 for item in selected)
    assert used <= budget
    assert metadata.used_chars == used


def test_high_cardinality_exact_905_budget_keeps_evidence_ahead_of_overflow() -> None:
    preferences = [
        _record(
            f"preference-{index:02d}",
            "interaction_preference",
            f"p{index:02d} q{index:02d}",
        )
        for index in range(9)
    ]
    primary = _record(
        "primary",
        "fact",
        "answer",
        source_id="primary-source",
    )
    evidence = _record(
        "supporting-evidence",
        "evidence",
        "e" * 200,
        source_id="primary-source",
    )
    budget = 905

    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        preferences,
        [primary, evidence],
        budget,
    )

    selected_ids = {item.id for item in selected}
    assert primary.id in selected_ids
    assert evidence.id in selected_ids
    assert "preference-08" not in selected_ids
    assert used == 902
    assert used == sum(len(item.content) + 64 for item in selected)
    assert used <= budget
    assert metadata.used_chars == used


def test_high_cardinality_overflow_uses_selected_duplicate_survivor_as_gate() -> None:
    preferences = [
        _record(
            f"preference-{index:02d}",
            "interaction_preference",
            f"p{index:02d} q{index:02d} r{index:02d} s{index:02d} t{index:02d}.",
        )
        for index in range(9)
    ]
    primary_b = _record(
        "b-ranked-primary",
        "fact",
        "The duplicate primary answer is cobalt.",
        source_id="primary-b-source",
    )
    primary_a = _record(
        "a-excluded-primary",
        "fact",
        "The duplicate primary answer is cobalt.",
        source_id="primary-a-source",
    )
    evidence_for_a = _record(
        "evidence-for-excluded-primary",
        "evidence",
        "Evidence for the excluded primary.",
        source_id="primary-a-source",
    )

    selected, used, metadata = ContextCompiler().compile_with_diagnostics(
        preferences,
        [primary_b, primary_a, evidence_for_a],
        _BUDGET,
    )

    selected_ids = {item.id for item in selected}
    assert primary_b.id in selected_ids
    assert primary_a.id not in selected_ids
    assert evidence_for_a.id not in selected_ids
    assert "preference-08" in selected_ids
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


def test_bounded_search_only_materializes_complete_candidate_pool_ids(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "bounded-pool-diagnostics.sqlite3")
    _apply_high_cardinality_store(store)
    principal = ClientPrincipal("reader", "Synthetic reader", frozenset({"context:read"}))
    engine = RetrievalEngine(store)
    request = SearchRequest(
        query="generic topic 00",
        scopes=["project:synthetic"],
        limit=100,
    )
    try:
        _unbounded_response, _unbounded_explanations, unbounded_diagnostics = engine._search(
            request,
            principal,
            bounded=False,
        )
        _bounded_response, _bounded_explanations, bounded_diagnostics = engine._search(
            request,
            principal,
            bounded=True,
        )
    finally:
        store.close()

    assert unbounded_diagnostics.candidate_pool_ids is None
    assert bounded_diagnostics.candidate_pool_ids is not None
    assert unbounded_diagnostics.candidate_pool_count == 2
    assert len(bounded_diagnostics.candidate_pool_ids) == _RELEVANT_COUNT
    assert bounded_diagnostics.candidate_pool_count == 2
    assert "candidate_pool_ids" not in unbounded_diagnostics.safe_dict()
    assert "candidate_pool_ids" not in bounded_diagnostics.safe_dict()


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
