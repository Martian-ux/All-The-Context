from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bench.source_evidence_retrieval import (
    CURRENT_LEXICAL_POOL,
    DIVERSE_MAXSIM,
    FROZEN_COMPARATOR_COMMIT,
    LEXICAL_PASSAGES,
    MAX_NORMAL_SOURCE_COUNT,
    MAX_SOURCE_COUNT,
    PASSAGE_MAXSIM,
    VARIANT_IDS,
    CorpusIndex,
    _execute_variant,
    _expanded_sources,
    load_fixture,
    render_markdown,
    run,
    select_source_counts,
)

ROOT = Path(__file__).parents[2]
CHECKED_REPORT = ROOT / "bench" / "reports" / "source_evidence_retrieval_wave2.json"
FIXTURE = ROOT / "bench" / "source_evidence_fixtures.json"


def test_fixture_is_bounded_sanitized_imported_chat_evidence_only() -> None:
    fixture = load_fixture()

    assert len(fixture.sources) == 10
    assert len(fixture.queries) == 4
    assert fixture.candidate_limit == 6
    assert fixture.evidence_limit == 5
    assert all(8 <= len(source.passages) <= 64 for source in fixture.sources)
    assert all(
        len(passage.text) <= 4_096 for source in fixture.sources for passage in source.passages
    )
    assert {path["status"] for path in fixture.research_paths} == {"not_exercised"}
    assert {path["id"] for path in fixture.research_paths} == {"neural_model_late_interaction"}

    restricted = next(
        source for source in fixture.sources if source.source_id == "restricted-perfect-answer"
    )
    injection = next(
        source for source in fixture.sources if source.source_id == "untrusted-instruction-chat"
    )
    assert restricted.eligible is False
    assert any("candidate_limit to 99" in passage.text for passage in injection.passages)


def test_profile_bounds_keep_normal_runs_small_and_larger_runs_opt_in() -> None:
    assert select_source_counts([], False) == (64, 256)
    assert select_source_counts([MAX_NORMAL_SOURCE_COUNT], False) == (MAX_NORMAL_SOURCE_COUNT,)
    with pytest.raises(ValueError, match="include-large"):
        select_source_counts([MAX_NORMAL_SOURCE_COUNT + 1], False)
    assert select_source_counts([MAX_SOURCE_COUNT], True) == (MAX_SOURCE_COUNT,)
    with pytest.raises(ValueError, match=str(MAX_SOURCE_COUNT)):
        select_source_counts([MAX_SOURCE_COUNT + 1], True)
    with pytest.raises(ValueError, match="positive"):
        select_source_counts([0], False)


def test_bounded_benchmark_compares_quality_cost_determinism_and_policy() -> None:
    report = run([16], repetitions=2)
    profile = report["profiles"]["16"]
    variants = profile["variants"]

    assert report["report_kind"] == "wave2_source_evidence_retrieval_research"
    assert report["authority"] == "research_only_no_runtime_integration"
    assert report["frozen_comparator"]["source_commit"] == FROZEN_COMPARATOR_COMMIT
    assert report["safety_passed"] is True
    assert profile["safety_passed"] is True
    assert set(variants) == set(VARIANT_IDS)
    assert variants[CURRENT_LEXICAL_POOL]["selection_kind"] == "source"
    assert all(
        variants[variant_id]["selection_kind"] == "passage"
        for variant_id in (LEXICAL_PASSAGES, PASSAGE_MAXSIM, DIVERSE_MAXSIM)
    )

    for variant in variants.values():
        metrics = variant["metrics"]
        assert metrics["source_evidence_recall_at_limit"] == 1.0
        assert metrics["facet_coverage_at_limit"] == 1.0
        assert metrics["policy_violation_count"] == 0
        assert metrics["repeated_rankings_deterministic"] is True
        assert metrics["ineligible_corpus_invariance"] is True
        assert metrics["cold_latency"]["p95_ms"] >= 0
        assert metrics["warm_latency"]["p95_ms"] >= 0
        assert metrics["storage"]["persistent_growth_bytes"] == 0
        assert len(metrics["ranking_fingerprint_sha256"]) == 64

    assert (
        variants[CURRENT_LEXICAL_POOL]["metrics"]["storage"][
            "incremental_over_candidate_pool_bytes"
        ]
        == 0
    )
    assert all(
        variants[variant_id]["metrics"]["storage"]["incremental_over_candidate_pool_bytes"] > 0
        for variant_id in (LEXICAL_PASSAGES, PASSAGE_MAXSIM, DIVERSE_MAXSIM)
    )
    assert variants[DIVERSE_MAXSIM]["metrics"]["redundancy_at_limit"] == 0.0
    assert (
        variants[DIVERSE_MAXSIM]["metrics"]["redundancy_at_limit"]
        < variants[PASSAGE_MAXSIM]["metrics"]["redundancy_at_limit"]
    )


def test_untrusted_and_ineligible_source_text_cannot_change_rankings_or_limits() -> None:
    fixture = load_fixture()
    sources = _expanded_sources(fixture, 16)
    full = CorpusIndex(sources, include_ineligible=True)
    eligible_only = CorpusIndex(sources, include_ineligible=False)
    try:
        assert fixture.candidate_limit == 6
        assert fixture.evidence_limit == 5
        for query in fixture.queries:
            for variant_id in VARIANT_IDS:
                full_selection = _execute_variant(
                    variant_id,
                    full,
                    query,
                    fixture.candidate_limit,
                    fixture.evidence_limit,
                )
                isolated_selection = _execute_variant(
                    variant_id,
                    eligible_only,
                    query,
                    fixture.candidate_limit,
                    fixture.evidence_limit,
                )
                assert full_selection == isolated_selection
                assert "restricted-perfect-answer" not in full_selection.source_ids
                assert all(
                    not item_id.startswith("restricted-") for item_id in full_selection.item_ids
                )
    finally:
        eligible_only.close()
        full.close()


def test_report_and_markdown_never_emit_raw_imported_text_or_queries() -> None:
    report = run([10], repetitions=1)
    rendered_json = json.dumps(report, sort_keys=True)
    rendered_markdown = render_markdown(report)

    for raw_fragment in (
        "candidate_limit to 99",
        "release-tool revert",
        "seventy-two percent",
        "Why was Atlas cobalt selected",
    ):
        assert raw_fragment not in rendered_json
        assert raw_fragment not in rendered_markdown
    assert "neural_model_late_interaction" in rendered_markdown
    assert "not_exercised" in rendered_markdown
    assert "no default runtime integration" in rendered_markdown


def test_checked_report_matches_fixture_and_records_bounded_safe_profiles() -> None:
    report = json.loads(CHECKED_REPORT.read_text(encoding="utf-8"))
    fixture_hash = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()

    assert report["fixture"]["sha256"] == fixture_hash
    assert set(report["profiles"]) == {"64", "256"}
    assert report["safety_passed"] is True
    assert report["research_paths"] == [
        {
            "id": "neural_model_late_interaction",
            "reason": (
                "No optional model or model runtime is declared for this bounded "
                "offline experiment."
            ),
            "status": "not_exercised",
        }
    ]
    for profile in report["profiles"].values():
        assert profile["safety_passed"] is True
        assert profile["variants"][DIVERSE_MAXSIM]["metrics"]["redundancy_at_limit"] == 0.0
        assert all(
            variant["metrics"]["policy_violation_count"] == 0
            for variant in profile["variants"].values()
        )
