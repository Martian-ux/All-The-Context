from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from copy import deepcopy
from pathlib import Path

import pytest
from allthecontext.models import SearchRequest
from allthecontext.retrieval import RetrievalEngine, V1CandidateRanker
from allthecontext.security import ClientPrincipal

from bench.retrieval_benchmark import (
    DEFAULT_BASELINE,
    FIXTURES,
    _profiles,
    build_database,
    compare,
    run_profile,
)


class PolicyBoundarySpy:
    def __init__(self, forbidden: set[str]) -> None:
        self.forbidden = forbidden
        self.seen: list[str] = []
        self.delegate = V1CandidateRanker()

    def rank(
        self,
        connection: sqlite3.Connection,
        candidates: Sequence[sqlite3.Row],
        query: str,
    ) -> list[sqlite3.Row]:
        ids = [str(row["id"]) for row in candidates]
        leaked = self.forbidden & set(ids)
        if leaked:
            raise AssertionError(f"policy-rejected records reached ranking: {sorted(leaked)}")
        self.seen.extend(ids)
        return self.delegate.rank(connection, candidates, query)


def _fixture() -> dict[str, object]:
    loaded = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_policy_rejected_records_never_reach_relevance_scoring(tmp_path: Path) -> None:
    fixture = _fixture()
    records = fixture["records"]
    assert isinstance(records, list)
    store, _elapsed = build_database(tmp_path / "policy.sqlite3", len(records), fixture)
    permissions = next(
        query
        for query in fixture["queries"]
        if isinstance(query, dict) and query["id"] == "permissions"
    )
    forbidden = set(permissions["forbidden"])
    spy = PolicyBoundarySpy(forbidden)
    principal = ClientPrincipal(
        "benchmark-reader", "Synthetic benchmark reader", frozenset({"context:read"})
    )

    response = RetrievalEngine(store, ranker=spy).search(
        SearchRequest(query="Sentinel", limit=100), principal
    )

    returned = {item.id for item in response.items}
    assert returned.isdisjoint(forbidden)
    assert set(spy.seen).isdisjoint(forbidden)
    assert {"allowed-sentinel", "retention-current"} <= returned


def test_benchmark_covers_frozen_scenarios_and_metrics(tmp_path: Path) -> None:
    fixture = _fixture()
    categories = {
        str(query["category"])
        for query in fixture["queries"]
        if isinstance(query, dict)
    }
    assert {
        "exact",
        "multi_term",
        "multi_term_partial",
        "paraphrase",
        "typo",
        "relation",
        "temporal",
        "permissions",
        "near_duplicate",
        "empty",
    } <= categories

    profile = run_profile(100, tmp_path, fixture)
    metrics = profile["metrics"]
    for name in (
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "mrr",
        "ndcg_at_5",
        "empty_result_rate",
        "unauthorized_result_count",
        "temporal_precision_at_5",
        "context_coverage",
        "context_redundancy",
        "cold_latency",
        "warm_latency",
        "index_size_bytes",
        "mutation_indexing",
    ):
        assert name in metrics
    assert metrics["unauthorized_result_count"] == 0


def test_50k_scale_is_bounded_and_explicitly_opt_in() -> None:
    assert _profiles([], False) == (1_000, 10_000)
    with pytest.raises(ValueError, match="requires --include-50k"):
        _profiles([50_000], False)
    assert _profiles([50_000], True) == (50_000,)
    with pytest.raises(ValueError, match="above 50k"):
        _profiles([50_001], True)


def test_v2_acceptance_gates_are_enforced_without_claiming_v1_passes() -> None:
    metrics = {
        "unauthorized_result_count": 0,
        "exact_recall_at_5": 1.0,
        "mrr": 0.5,
        "multi_term_empty_rate": 0.5,
        "warm_latency": {"p95_ms": 10.0},
    }
    report = {"profiles": {"10000": {"metrics": metrics}}}

    passed, messages = compare(report, report)

    assert passed is False
    assert any("FAIL" in message and "MRR" in message for message in messages)
    assert any("FAIL" in message and "empty rate" in message for message in messages)


@pytest.mark.parametrize(
    ("field", "value", "failure_text"),
    [
        ("unauthorized_result_count", 1, "policy violations"),
        ("exact_recall_at_5", 0.9, "exact Recall@5"),
        ("warm_latency", {"p95_ms": 151.0}, "10k warm p95"),
    ],
)
def test_each_non_improvement_v2_gate_can_fail(
    field: str, value: object, failure_text: str
) -> None:
    baseline_metrics = {
        "unauthorized_result_count": 0,
        "exact_recall_at_5": 1.0,
        "mrr": 0.5,
        "multi_term_empty_rate": 0.5,
        "warm_latency": {"p95_ms": 10.0},
    }
    candidate_metrics = deepcopy(baseline_metrics)
    candidate_metrics.update({"mrr": 0.6, "multi_term_empty_rate": 0.2, field: value})
    baseline = {"profiles": {"10000": {"metrics": baseline_metrics}}}
    candidate = {"profiles": {"10000": {"metrics": candidate_metrics}}}

    passed, messages = compare(candidate, baseline)

    assert passed is False
    assert any("FAIL" in message and failure_text in message for message in messages)


def test_checked_in_v1_baseline_matches_frozen_fixture() -> None:
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))

    assert baseline["schema_version"] == 1
    assert baseline["engine"] == "retrieval_v1_sqlite_fts5"
    assert baseline["fixture_sha256"] == hashlib.sha256(FIXTURES.read_bytes()).hexdigest()
    assert set(baseline["profiles"]) == {"1000", "10000"}
    for profile in baseline["profiles"].values():
        assert profile["metrics"]["unauthorized_result_count"] == 0
