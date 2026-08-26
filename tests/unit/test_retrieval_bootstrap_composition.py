from __future__ import annotations

from pathlib import Path
from typing import Any

from allthecontext.content_evidence import CURATED_CONTENT_ALIASES, project_content_evidence
from allthecontext.models import (
    ApprovalRequest,
    BootstrapRequest,
    CandidateInput,
    SearchRequest,
)
from allthecontext.retrieval import RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

READER = ClientPrincipal("reader", "Synthetic reader", frozenset({"context:read"}))


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
