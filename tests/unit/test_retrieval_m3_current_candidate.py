from __future__ import annotations

import json
from pathlib import Path

import pytest

from bench.retrieval_m3_current_candidate import (
    FIXTURES,
    SCORECARD_SCHEMA,
    CaseScore,
    CurrentCandidateEvaluationError,
    _content_free_case,
    _tokens,
    _validate_anchor_distribution,
    load_fixture,
    render_markdown,
    run,
)

ROOT = Path(__file__).resolve().parents[2]


def test_current_candidate_fixture_requires_distinct_one_anchor_split_records() -> None:
    fixture = load_fixture()
    cases = {str(case["id"]): case for case in fixture["cases"] if isinstance(case, dict)}
    records = {
        str(record["alias"]): record for record in fixture["records"] if isinstance(record, dict)
    }

    split = cases["bootstrap_split_union_recall"]
    assert split["anchor_mode"] == "split_distinct"
    expected = split["expected_aliases"]
    assert all(
        len(
            set(str(anchor) for anchor in records[str(alias)]["content_anchors"])
            & set(str(anchor) for anchor in split["task_anchors"])
        )
        == 1
        for alias in expected
    )
    assert len(expected) == 2
    assert len(split["task_anchors"]) == 2
    query_tokens = _tokens(str(split["query"]))
    for alias in split["anchor_aliases"]:
        content = records[str(alias)]
        content_tokens = _tokens(str(content["content"]))
        assert len(content_tokens & set(str(anchor) for anchor in split["task_anchors"])) == 1
        assert len(content_tokens & query_tokens) == 1
    alias_case = cases["alias_only_not_full_coverage"]
    alias_record = records[str(alias_case["record_aliases"][0])]
    assert alias_case["query"] == "eviction cobalt"
    assert alias_record["content"] == "cache"
    noise = fixture["noise_profiles"]["metadata_only_256"]
    assert noise["count"] == 256
    assert not {"cobalt", "orbit", "ledger"} & _tokens(str(noise["content"]))
    assert {"cobalt", "orbit", "ledger"} <= _tokens(str(noise["kind"]))
    noisy_split = cases["bootstrap_split_metadata_noise_invariance"]
    assert noisy_split["compare_with"] == "bootstrap_split_union_recall"
    assert noisy_split["expected_aliases"] == split["expected_aliases"]
    fixture_text = FIXTURES.read_text(encoding="utf-8")
    assert "Noah" not in fixture_text
    assert "C:\\Users\\" not in fixture_text


def test_current_candidate_rejects_a_per_record_three_anchor_substitute() -> None:
    fixture = load_fixture()
    case = next(case for case in fixture["cases"] if case["id"] == "bootstrap_split_union_recall")
    records = {
        str(record["alias"]): dict(record)
        for record in fixture["records"]
        if isinstance(record, dict)
    }
    records["split_cobalt"]["content"] = "support cobalt orbit"

    with pytest.raises(CurrentCandidateEvaluationError, match="one query token"):
        _validate_anchor_distribution(case, records)


def test_current_candidate_report_is_content_free_deterministic_and_exactly_judged() -> None:
    fixture = load_fixture()
    report = run(ROOT / "tmp" / "test-retrieval-m3-current-candidate")
    serialized = json.dumps(report, sort_keys=True)
    markdown = render_markdown(report)

    assert report["schema"] == SCORECARD_SCHEMA
    assert report["content_free"] is True
    assert report["case_count"] == len(fixture["cases"])
    assert "record_id" not in serialized
    assert str(ROOT) not in serialized
    fixture_content = [
        str(record["content"]) for record in fixture["records"] if isinstance(record, dict)
    ]
    fixture_content.extend(
        str(profile["content"])
        for profile in fixture["noise_profiles"].values()
        if isinstance(profile, dict)
    )
    assert all(content not in serialized for content in fixture_content)
    assert all(str(case["query"]) not in serialized for case in fixture["cases"])
    assert all(content not in markdown for content in fixture_content)
    assert all(str(case["query"]) not in markdown for case in fixture["cases"])
    assert {
        "false_positive_count",
        "union_coverage",
        "exact_set_match",
        "noise_invariant",
        "reason_codes",
    } <= set(report["cases"][0])
    assert report["scorecard"]["schema"] == SCORECARD_SCHEMA


def test_current_candidate_repeated_runs_keep_content_free_scores_stable() -> None:
    first = run(ROOT / "tmp" / "test-retrieval-m3-current-candidate-one")
    second = run(ROOT / "tmp" / "test-retrieval-m3-current-candidate-two")

    assert first == second


def test_positive_case_score_rejects_unjudged_false_positives() -> None:
    score = CaseScore(
        case_id="synthetic_positive",
        surface="search",
        precision=0.5,
        recall=1.0,
        union_coverage=1.0,
        task_anchor_count=3,
        union_anchor_count=3,
        expected_count=1,
        returned_count=2,
        relevant_count=1,
        false_positive_count=1,
        missing_relevant_count=0,
        abstained=False,
        exact_set_match=False,
        deterministic=True,
        noise_invariant=True,
        reason_codes=("unjudged_false_positive",),
        passed=False,
    )

    rendered = _content_free_case(score)
    assert rendered["false_positive_count"] == 1
    assert rendered["exact_set_match"] is False
    assert rendered["passed"] is False


def test_current_candidate_quality_gate_reports_production_red_cases() -> None:
    report = run(ROOT / "tmp" / "test-retrieval-m3-current-candidate-gate")
    failures = {
        case["case"]: case["reason_codes"] for case in report["cases"] if not case["passed"]
    }

    assert report["passed"], failures


def test_current_candidate_isolation_rejects_existing_database() -> None:
    work_dir = ROOT / "tmp" / "test-retrieval-m3-current-candidate-existing"
    work_dir.mkdir(parents=True, exist_ok=True)
    database = work_dir / "core.sqlite3"
    database.write_text("synthetic sentinel", encoding="utf-8")

    try:
        try:
            run(work_dir)
        except CurrentCandidateEvaluationError as error:
            assert "existing Core database" in str(error)
        else:
            raise AssertionError("existing Core database was not rejected")
    finally:
        database.unlink(missing_ok=True)
