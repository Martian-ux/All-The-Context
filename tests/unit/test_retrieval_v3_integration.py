from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.models import CandidateInput, ClientCreate, SearchRequest
from allthecontext.retrieval import RetrievalEngine, _temporal_sidecar_path
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore
from pydantic import ValidationError

from bench.retrieval_benchmark import build_database
from bench.retrieval_v3_foundation import load_foundation_fixture


def _principal(*, admin: bool = False) -> ClientPrincipal:
    scopes = {"context:read"}
    if admin:
        scopes.add("admin")
    return ClientPrincipal("benchmark-reader", "Synthetic reader", frozenset(scopes))


def _approve(store: CoreStore, value: CandidateInput) -> str:
    return store.approve_candidate(store.add_candidate(value).id).id


def test_search_request_requires_offset_aware_as_of_and_normalizes_utc() -> None:
    request = SearchRequest(as_of="2025-11-02T01:30:00-04:00")

    assert request.as_of == "2025-11-02T05:30:00+00:00"
    with pytest.raises(ValidationError, match="UTC offset"):
        SearchRequest(as_of="2025-11-02T01:30:00")


def test_production_current_and_as_of_resolution_are_deterministic_across_restart(
    tmp_path: Path,
) -> None:
    fixture = load_foundation_fixture()
    store, _elapsed = build_database(tmp_path / "temporal.sqlite3", 100, fixture)
    principal = _principal()
    current = SearchRequest(query="Sentinel archive retention", limit=5)
    as_of = SearchRequest(
        query="Sentinel archive retention",
        as_of="2025-01-01T05:00:05.500000+05:00",
        limit=5,
    )
    engine = RetrievalEngine(store)

    current_ids = [item.id for item in engine.search(current, principal).items]
    historical_runs = [
        [item.id for item in engine.search(as_of, principal).items] for _ in range(5)
    ]
    restarted = [item.id for item in RetrievalEngine(store).search(as_of, principal).items]

    assert current_ids == ["retention-current"]
    assert historical_runs == [["retention-old"]] * 5
    assert restarted == historical_runs[0]
    assert _temporal_sidecar_path(store.database_path).exists()


def test_production_admissibility_uses_project_quality_kind_and_conflict_factors(
    tmp_path: Path,
) -> None:
    store = CoreStore(tmp_path / "admissibility.sqlite3")
    store.migrate()
    store.initialize_vault("Admissibility integration", "UTC")
    relevant = _approve(
        store,
        CandidateInput(
            kind="workflow",
            content="Production deployment rollback uses the approved runbook.",
            scopes=["project:release"],
            confidence=1.0,
        ),
    )
    false_one = _approve(
        store,
        CandidateInput(
            kind="historical_note",
            content="Production deployment rollback appears in a training example.",
            structured_value={"authority": False},
            entity_key="training:deployment",
            attribute_key="authority",
            scopes=["training"],
            confidence=0.0,
        ),
    )
    false_two = _approve(
        store,
        CandidateInput(
            kind="historical_note",
            content="Production deployment rollback appears in a conflicting example.",
            structured_value={"authority": True},
            entity_key="training:deployment",
            attribute_key="authority",
            scopes=["training"],
            confidence=0.0,
        ),
    )
    engine = RetrievalEngine(store)

    diagnostic = engine.diagnose_search(
        SearchRequest(
            query="production deployment rollback",
            current_project="release",
            limit=10,
        ),
        _principal(admin=True),
    )

    assert [item["id"] for item in diagnostic["items"]] == [relevant]
    assert {false_one, false_two}.isdisjoint(item["id"] for item in diagnostic["items"])
    admissibility = diagnostic["pipeline_diagnostics"]["admissibility"]
    assert admissibility["rejected_count"] == 2
    assert admissibility["reason_counts"]["reject.conflict"] == 2
    rendered = json.dumps(diagnostic["pipeline_diagnostics"], sort_keys=True)
    assert "Production deployment rollback" not in rendered
    assert false_one not in rendered
    assert false_two not in rendered


def test_catalog_search_counts_and_pages_all_authorized_matches_without_unbounding_bootstrap(
    tmp_path: Path,
) -> None:
    store = CoreStore(tmp_path / "catalog-pagination.sqlite3")
    store.migrate()
    store.initialize_vault("Catalog pagination", "UTC")
    reader, _reader_token = store.create_client(
        ClientCreate(name="Catalog reader", scopes=["context:read"])
    )
    other, _other_token = store.create_client(
        ClientCreate(name="Other reader", scopes=["context:read"])
    )

    for index in range(123):
        store.add_candidate(
            CandidateInput(
                kind="fact",
                content=f"catalog page marker visible {index}",
                scopes=["project:atlas"],
                allowed_clients=[reader.id],
                confidence=1.0,
                explicit_user_statement=True,
            )
        )
    for index in range(11):
        store.add_candidate(
            CandidateInput(
                kind="note",
                content=f"catalog page marker filtered {index}",
                scopes=["project:atlas"],
                allowed_clients=[reader.id],
                confidence=1.0,
                explicit_user_statement=True,
            )
        )
    for index in range(9):
        store.add_candidate(
            CandidateInput(
                kind="fact",
                content=f"catalog page marker other-scope {index}",
                scopes=["project:neptune"],
                allowed_clients=[reader.id],
                confidence=1.0,
                explicit_user_statement=True,
            )
        )
    for index in range(7):
        store.add_candidate(
            CandidateInput(
                kind="fact",
                content=f"catalog page marker unauthorized {index}",
                scopes=["project:atlas"],
                allowed_clients=[other.id],
                confidence=1.0,
                explicit_user_statement=True,
            )
        )

    engine = RetrievalEngine(store)
    first = engine.search(
        SearchRequest(query="catalog page marker", limit=100, offset=0), reader
    )
    second = engine.search(
        SearchRequest(query="catalog page marker", limit=100, offset=100), reader
    )

    assert first.total == 143
    assert len(first.items) == 100
    assert second.total == first.total
    assert len(second.items) == 43
    assert {item.id for item in first.items}.isdisjoint(item.id for item in second.items)
    assert len({item.id for item in (*first.items, *second.items)}) == 143
    assert all("unauthorized" not in item.content for item in (*first.items, *second.items))

    filtered = engine.search(
        SearchRequest(
            query="catalog page marker",
            scopes=["project:atlas"],
            kinds=["fact"],
            limit=100,
        ),
        reader,
    )
    assert filtered.total == 123
    assert len(filtered.items) == 100
    assert all(item.kind == "fact" and "project:atlas" in item.scopes for item in filtered.items)
    assert all("unauthorized" not in item.content for item in filtered.items)

    bounded = engine._bounded_search(
        SearchRequest(query="catalog page marker", limit=100), reader
    )
    assert bounded.total == 100
    assert len(bounded.items) == 100
