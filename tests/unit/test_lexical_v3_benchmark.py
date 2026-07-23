from __future__ import annotations

import json
from pathlib import Path

from allthecontext.retrieval import RetrievalEngine, V2LexicalRanker

from bench.lexical_v3_benchmark import _macro_precision_at_5, compare, run_profile
from bench.retrieval_benchmark import DEFAULT_BASELINE, FIXTURES, build_database


def _fixture() -> dict[str, object]:
    loaded = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def test_focused_profile_improves_frozen_relevance_without_policy_leaks(
    tmp_path: Path,
) -> None:
    fixture = _fixture()

    profile = run_profile(100, tmp_path, fixture)
    metrics = profile["metrics"]

    assert metrics["unauthorized_result_count"] == 0
    assert metrics["exact_recall_at_5"] == 1.0
    assert metrics["recall_at_5"] > 0.666667
    assert metrics["mrr"] > 0.666667
    assert metrics["persistent_index_growth_bytes"] <= 4_096
    assert any(
        query["id"] == "multi_term_empty" and query["ids"] == ["multi-cache"]
        for query in profile["queries"]
    )


def test_comparator_evidences_precision_mrr_recall_and_growth_gates(tmp_path: Path) -> None:
    fixture = _fixture()
    candidate_profile = run_profile(100, tmp_path, fixture)
    frozen = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
    baseline_profile = frozen["profiles"]["1000"]
    candidate = {"profiles": {"100": candidate_profile}}
    baseline = {"profiles": {"100": baseline_profile}}

    passed, messages = compare(candidate, baseline)

    assert passed is True
    assert any("PASS [100] exact Recall@5 preserved" in message for message in messages)
    assert any("PASS [100] MRR at least 10% better" in message for message in messages)
    assert any("PASS [100] macro Precision@5 improved" in message for message in messages)
    assert any("PASS [100] persistent index growth" in message for message in messages)
    assert _macro_precision_at_5(candidate_profile["queries"]) > _macro_precision_at_5(
        baseline_profile["queries"]
    )


def test_lexical_v3_remains_unwired_from_production_retrieval(tmp_path: Path) -> None:
    fixture = _fixture()
    store, _elapsed = build_database(tmp_path / "production.sqlite3", 100, fixture)

    engine = RetrievalEngine(store)

    assert isinstance(engine.ranker, V2LexicalRanker)
