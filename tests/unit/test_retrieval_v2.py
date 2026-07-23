from __future__ import annotations

import json
from pathlib import Path

import pytest
from allthecontext.models import ContextRecordOut, SearchRequest
from allthecontext.retrieval import ContextCompiler, RetrievalEngine
from allthecontext.security import ClientPrincipal

from bench.retrieval_benchmark import FIXTURES, build_database


def _fixture() -> dict[str, object]:
    loaded = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


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


def test_v3_ordering_is_stable_and_bounded_alias_recovers_multi_term_query(
    tmp_path: Path,
) -> None:
    fixture = _fixture()
    records = fixture["records"]
    assert isinstance(records, list)
    store, _elapsed = build_database(tmp_path / "stable.sqlite3", len(records), fixture)
    engine = RetrievalEngine(store)
    principal = ClientPrincipal(
        "benchmark-reader", "Synthetic benchmark reader", frozenset({"context:read"})
    )
    request = SearchRequest(query="segmented eviction strategy", limit=5)

    runs = [[item.id for item in engine.search(request, principal).items] for _ in range(5)]

    assert runs == [runs[0]] * 5
    assert runs[0] == ["multi-cache"]


def test_administrator_explanations_contain_only_authorized_returned_records(
    tmp_path: Path,
) -> None:
    fixture = _fixture()
    records = fixture["records"]
    assert isinstance(records, list)
    store, _elapsed = build_database(tmp_path / "explain.sqlite3", len(records), fixture)
    principal = ClientPrincipal(
        "benchmark-reader",
        "Synthetic benchmark administrator",
        frozenset({"admin", "context:read"}),
    )
    engine = RetrievalEngine(store)

    diagnostic = engine.diagnose_search(SearchRequest(query="Sentinel", limit=100), principal)
    returned = {item["id"] for item in diagnostic["items"]}
    explained = {item["record_id"] for item in diagnostic["ranking_explanations"]}
    forbidden = {
        "retention-old",
        "expired-sentinel",
        "deleted-sentinel",
        "denied-sentinel",
        "other-allowlist-sentinel",
    }

    assert explained == returned
    assert explained.isdisjoint(forbidden)
    assert all("all_terms" in item["channel_ranks"] for item in diagnostic["ranking_explanations"])
    assert diagnostic["pipeline_diagnostics"]["temporal"]["reason_counts"]
    assert diagnostic["pipeline_diagnostics"]["admissibility"]["rejected_count"] >= 0
    with pytest.raises(PermissionError, match="administrator"):
        engine.diagnose_search(SearchRequest(query="Sentinel"), principal=None)


def test_context_compiler_reserves_preferences_deduplicates_and_orders_supporting_last() -> None:
    compiler = ContextCompiler()
    preference = _record(
        "preference",
        "interaction_preference",
        "Use ISO 8601 timestamps in exports.",
    )
    near_duplicate = _record(
        "preference-copy",
        "interaction_preference",
        "Exports use ISO-8601 timestamp formatting.",
    )
    primary = _record("answer", "project_decision", "Atlas launch color is cobalt blue.")
    supporting = _record(
        "evidence",
        "supporting_evidence",
        "The launch brief specifies cobalt.",
        evidence="Approved launch brief",
    )

    selected, used = compiler.compile(
        [preference, near_duplicate], [supporting, primary], budget_chars=1_000
    )
    ids = [item.id for item in selected]

    assert ids[0] == "preference"
    assert "preference-copy" not in ids
    assert ids.index("answer") < ids.index("evidence")
    assert used == sum(len(item.content) + 64 for item in selected)


def test_context_compiler_keeps_a_mandatory_preference_under_tight_budget() -> None:
    compiler = ContextCompiler()
    preference = _record("preference", "interaction_preference", "Prefer concise answers.")
    answer = _record("answer", "fact", "A" * 170)

    selected, _used = compiler.compile([preference], [answer], budget_chars=256)

    assert [item.id for item in selected] == ["preference"]
