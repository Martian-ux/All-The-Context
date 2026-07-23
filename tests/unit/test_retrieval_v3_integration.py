from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.models import CandidateInput, SearchRequest
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
