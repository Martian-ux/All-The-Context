from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from allthecontext.content_evidence import CURATED_CONTENT_ALIASES, project_content_evidence
from allthecontext.models import (
    ApprovalRequest,
    BootstrapRequest,
    CandidateInput,
    SearchRequest,
)
from allthecontext.retrieval import RetrievalEngine, parse_query_intent
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

READER = ClientPrincipal("reader", "Synthetic reader", frozenset({"context:read"}))


@pytest.mark.parametrize(
    "exclusion",
    [
        "Do not mention unrelated preferences.",
        "Don't include preferences.",
        "Never discuss unrelated preferences.",
    ],
)
def test_output_exclusions_preserve_factual_and_positive_preference_intent(exclusion: str) -> None:
    intent = parse_query_intent(
        "Prepare a brief deployment handoff for Project Aurora. "
        f"State the current region, production blocker, and next action. {exclusion}"
    )
    assert set(intent.anchor_tokens) == {
        "deployment",
        "aurora",
        "region",
        "production",
        "blocker",
        "next",
        "action",
    }
    assert intent.asks_current
    assert "preferences" in parse_query_intent("State my current preferences.").anchor_tokens
    assert "concise" in parse_query_intent("Do I prefer concise answers?").anchor_tokens
    assert "not" in parse_query_intent("Which region is not deployed?").anchor_tokens
    assert "state" in parse_query_intent("What is the deployment state?").anchor_tokens
    assert "preferences" in parse_query_intent("No preferences changed for Aurora").anchor_tokens
    assert "preferences" in parse_query_intent("Do not delete preferences").anchor_tokens
    assert "latency" in parse_query_intent("not preferences, latency").anchor_tokens
    assert "concise" in parse_query_intent("concise handoff preferences").anchor_tokens


@pytest.mark.parametrize("request_prefix", ["Please prepare", "Write"])
def test_output_request_verbs_are_not_content_anchors(request_prefix: str) -> None:
    query = (
        f"{request_prefix} a concise deployment handoff for Project Aurora. "
        "State the current region, production blocker, and next action."
    )
    assert set(parse_query_intent(query).anchor_tokens) == {
        "deployment",
        "aurora",
        "region",
        "production",
        "blocker",
        "next",
        "action",
    }


@pytest.mark.parametrize("request_prefix", ["Please prepare", "Write"])
def test_output_request_verbs_do_not_create_required_handoff_anchors(
    tmp_path: Path,
    request_prefix: str,
) -> None:
    store = CoreStore(tmp_path / "request-verb.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    region = _approve(store, key="region", content="Aurora deployment region is west.")
    blocker = _approve(
        store, key="blocker", content="Aurora production is blocked pending approval."
    )
    action = _approve(store, key="action", content="The next Aurora action is to request approval.")
    query = (
        f"{request_prefix} a concise deployment handoff for Project Aurora. "
        "State the current region, production blocker, and next action."
    )
    try:
        response = RetrievalEngine(store).bootstrap(
            BootstrapRequest(query=query, budget_chars=1600), READER
        )
        assert {region, blocker, action} <= {item.id for item in response.items}
    finally:
        store.close()


def test_positive_preference_bootstrap_keeps_current_preference(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "preference.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    preference = _approve(
        store,
        key="preference",
        content="I prefer concise answers.",
        kind="interaction_preference",
        explicit_user_statement=True,
    )
    try:
        response = RetrievalEngine(store).bootstrap(
            BootstrapRequest(query="State my current answer preferences.", budget_chars=1600),
            READER,
        )
        assert preference in {item.id for item in response.items}
    finally:
        store.close()


@pytest.mark.parametrize(
    "boundary",
    [
        "none",
        "unknown",
        "client",
        "project",
        "budget",
        "correction",
        "deletion",
        "conflict",
    ],
)
def test_project_handoff_composes_facts_only_when_eligible_union_fits(
    tmp_path: Path,
    boundary: str,
) -> None:
    store = CoreStore(tmp_path / "handoff.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    common = {"scopes": ["project:aurora"], "explicit_user_statement": True}
    region = _approve(store, key="region", content="Aurora deployment region is west.", **common)
    blocker = _approve(
        store, key="blocker", content="Aurora production is blocked pending approval.", **common
    )
    action = _approve(
        store,
        key="action",
        content="The next Aurora action is to request approval.",
        scopes=["project:other"] if boundary == "project" else ["project:aurora"],
        denied_clients=[READER.id] if boundary == "client" else [],
        explicit_user_statement=boundary != "conflict",
        entity_key="aurora",
        attribute_key="next_action",
        structured_value={"action": "request"},
    )
    if boundary == "correction":
        store.correct_record(
            region,
            content="Aurora deployment region is east.",
            reason="Synthetic correction",
        )
    if boundary == "deletion":
        store.delete_record(action, reason="Synthetic deletion")
    if boundary == "conflict":
        _approve(
            store,
            key="conflicting-action",
            content="The next Aurora action is to cancel approval.",
            entity_key="aurora",
            attribute_key="next_action",
            structured_value={"action": "cancel"},
            **{**common, "explicit_user_statement": False},
        )
    unrelated = _approve(
        store,
        key="unrelated",
        content="Aurora deployment region production blocker next action.",
        scopes=["project:other"],
        explicit_user_statement=True,
    )
    query = (
        "Prepare a concise deployment handoff for Project Aurora. "
        "State the current deployment region, the production blocker, and the next action. "
        "Do not mention unrelated preferences."
    )
    if boundary == "unknown":
        query += " State the operator."
    try:
        response = RetrievalEngine(store).bootstrap(
            BootstrapRequest(
                query=query,
                current_project="Aurora",
                requested_scopes=["project:aurora"],
                budget_chars=256 if boundary == "budget" else 1600,
            ),
            READER,
        )
        ids = {item.id for item in response.items}
        assert unrelated not in ids
        if boundary in {"none", "correction"}:
            assert {region, blocker, action} <= ids
            if boundary == "correction":
                assert "Aurora deployment region is west." not in {
                    item.content for item in response.items
                }
                assert "Aurora deployment region is east." in {
                    item.content for item in response.items
                }
        else:
            assert ids == set()
        assert response.used_chars <= (256 if boundary == "budget" else 1600)
    finally:
        store.close()


@pytest.mark.parametrize(
    "exclusion",
    [
        "not preferences.",
        "Not preferences!",
        "Don\u2019t include preferences.",
        "Do not include my preferences.",
        "No preferences, please.",
        "Don't include: preferences.",
    ],
)
def test_preference_exclusion_does_not_block_project_facts(
    tmp_path: Path,
    exclusion: str,
) -> None:
    store = CoreStore(tmp_path / "exclusion.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    fact = _approve(
        store,
        key="region",
        content="Aurora region is west.",
        scopes=["project:aurora"],
        explicit_user_statement=True,
    )
    preference = _approve(
        store,
        key="preference",
        content="I prefer concise answers.",
        kind="interaction_preference",
        explicit_user_statement=True,
    )
    try:
        response = RetrievalEngine(store).bootstrap(
            BootstrapRequest(
                query=f"Aurora region; {exclusion}",
                current_project="Aurora",
                budget_chars=1600,
            ),
            READER,
        )
        assert {fact, preference} <= {item.id for item in response.items}
    finally:
        store.close()


def test_positive_preference_request_survives_unrelated_preference_exclusion() -> None:
    query = "Do I prefer concise answers? Do not mention unrelated preferences."
    intent = parse_query_intent(query)
    assert intent.anchor_tokens == ("i", "prefer", "concise", "answers")


@pytest.mark.parametrize(
    "query, content",
    [
        ("State regulations for Aurora", "Aurora regulations require approval."),
        ("Aurora handoff owner", "Aurora owner is Morgan."),
        ("Aurora region; not preferences, latency", "Aurora region is west."),
    ],
)
def test_meaningful_state_and_handoff_terms_require_content(
    tmp_path: Path,
    query: str,
    content: str,
) -> None:
    store = CoreStore(tmp_path / "meaningful.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    _approve(store, key="near-miss", content=content, explicit_user_statement=True)
    try:
        assert (
            not RetrievalEngine(store)
            .bootstrap(
                BootstrapRequest(query=query, budget_chars=1600),
                READER,
            )
            .items
        )
    finally:
        store.close()


def _approve(store: CoreStore, *, key: str, content: str, **kwargs: Any) -> str:
    candidate = store.add_candidate(
        CandidateInput(
            kind=str(kwargs.pop("kind", "fact")),
            content=content,
            idempotency_key=key,
            **kwargs,
        )
    )
    return store.approve_candidate(candidate.id, ApprovalRequest(), actor="test").id


def test_direct_exact_positive_and_two_of_three_near_miss_abstention(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "direct-precision.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    strong = _approve(store, key="strong", content="alpha beta gamma")
    near_store = CoreStore(tmp_path / "near-miss.sqlite3")
    near_store.initialize_vault("synthetic", "UTC")
    near = _approve(near_store, key="near", content="alpha beta")
    try:
        engine = RetrievalEngine(store)
        exact = engine.search(SearchRequest(query="latest alpha beta gamma", limit=10), READER)
        near_only = RetrievalEngine(near_store).search(
            SearchRequest(query="latest alpha beta gamma", limit=10), READER
        )

        assert [item.id for item in exact.items] == [strong]
        assert near not in {item.id for item in exact.items}
        assert near_only.items == []
    finally:
        store.close()
        near_store.close()


def test_bootstrap_assembles_distinct_one_anchor_facets_when_union_is_complete(
    tmp_path: Path,
) -> None:
    store = CoreStore(tmp_path / "bootstrap-union.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    first = _approve(store, key="first-facet", content="cobalt")
    second = _approve(store, key="second-facet", content="orbit")
    try:
        anchors = frozenset({"cobalt", "orbit"})
        first_record = store.get_record(first)
        second_record = store.get_record(second)
        assert project_content_evidence(
            first_record.content, anchors, CURATED_CONTENT_ALIASES
        ).matched_anchors == frozenset({"cobalt"})
        assert project_content_evidence(
            second_record.content, anchors, CURATED_CONTENT_ALIASES
        ).matched_anchors == frozenset({"orbit"})
        response = RetrievalEngine(store).bootstrap(
            BootstrapRequest(
                query="cobalt orbit",
                budget_chars=4_000,
            ),
            READER,
        )

        assert {item.id for item in response.items} >= {first, second}
    finally:
        store.close()


def test_bootstrap_abstains_when_authorized_content_union_is_insufficient(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "bootstrap-insufficient.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    first = _approve(store, key="first-facet", content="cobalt")
    second = _approve(store, key="second-facet", content="orbit")
    try:
        anchors = frozenset({"cobalt", "orbit", "relay"})
        first_record = store.get_record(first)
        second_record = store.get_record(second)
        assert project_content_evidence(
            first_record.content, anchors, CURATED_CONTENT_ALIASES
        ).matched_anchors == frozenset({"cobalt"})
        assert project_content_evidence(
            second_record.content, anchors, CURATED_CONTENT_ALIASES
        ).matched_anchors == frozenset({"orbit"})
        response = RetrievalEngine(store).bootstrap(
            BootstrapRequest(
                query="cobalt orbit relay",
                budget_chars=4_000,
            ),
            READER,
        )

        assert response.items == []
    finally:
        store.close()


def test_alias_only_content_is_one_mapped_anchor_not_full_coverage(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "alias-coverage.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    alias_only = _approve(store, key="alias-only", content="cache")
    try:
        evidence = project_content_evidence(
            "cache",
            ("segmented", "eviction"),
            CURATED_CONTENT_ALIASES,
        )
        response = RetrievalEngine(store).search(
            SearchRequest(query="segmented eviction strategy", limit=10),
            READER,
        )

        assert evidence.matched_anchors == frozenset({"eviction"})
        assert alias_only not in {item.id for item in response.items}
    finally:
        store.close()


def test_metadata_noise_cannot_change_bootstrap_content_selection(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "metadata-noise.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    first = _approve(store, key="first-facet", content="cobalt")
    second = _approve(store, key="second-facet", content="orbit")
    engine = RetrievalEngine(store)
    request = BootstrapRequest(
        query="cobalt orbit",
        budget_chars=4_000,
    )
    try:
        before = [item.id for item in engine.bootstrap(request, READER).items]
        for index in range(200):
            _approve(
                store,
                key=f"metadata-noise-{index}",
                content="unrelated inventory note",
                kind="atlas_windows_relay_synchronization_metadata",
                tags=["atlas", "windows", "relay", "synchronization"],
                scopes=["project:atlas"],
            )

        after = [item.id for item in engine.bootstrap(request, READER).items]

        assert before == after
        assert {first, second} <= set(after)
    finally:
        store.close()


def test_bootstrap_coverage_aware_pool_keeps_missing_anchor_beyond_100_candidates(
    tmp_path: Path,
) -> None:
    store = CoreStore(tmp_path / "bootstrap-coverage-aware-pool.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    shared = [
        _approve(store, key=f"shared-cobalt-{index}", content=f"cobalt filler{index}")
        for index in range(120)
    ]
    orbit_content = "orbit"
    unique = _approve(store, key="unique-orbit", content=orbit_content)
    anchors = frozenset({"cobalt", "orbit"})
    assert project_content_evidence(
        "cobalt filler0", anchors, CURATED_CONTENT_ALIASES
    ).matched_anchors == frozenset({"cobalt"})
    assert project_content_evidence(
        orbit_content, anchors, CURATED_CONTENT_ALIASES
    ).matched_anchors == frozenset({"orbit"})
    try:
        response = RetrievalEngine(store).bootstrap(
            BootstrapRequest(query="cobalt orbit", budget_chars=10_000), READER
        )

        returned = {item.id for item in response.items}
        assert unique in returned
        assert returned & set(shared)
        assert response.pack_metadata is not None
        assert response.pack_metadata.candidate_pool_truncated is True
    finally:
        store.close()


def test_bootstrap_union_keeps_authorization_and_lifecycle_filters(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "bootstrap-boundaries.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    allowed = _approve(store, key="allowed", content="alpha beta")
    denied = _approve(
        store,
        key="denied",
        content="gamma",
        denied_clients=["reader"],
    )
    expired = _approve(
        store,
        key="expired",
        content="delta",
        expires_at="2020-01-01T00:00:00+00:00",
    )
    try:
        response = RetrievalEngine(store).bootstrap(
            BootstrapRequest(query="alpha beta gamma delta", budget_chars=4_000),
            READER,
        )

        assert allowed not in {item.id for item in response.items}
        assert denied not in {item.id for item in response.items}
        assert expired not in {item.id for item in response.items}
    finally:
        store.close()
