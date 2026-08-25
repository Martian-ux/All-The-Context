from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from bench.retrieval_precision_m3 import (
    DEFAULT_BASELINE,
    FIXTURES,
    PrecisionEvaluationError,
    load_fixture,
    run,
)


def _fixture_text() -> str:
    return FIXTURES.read_text(encoding="utf-8")


def test_fixture_covers_the_five_milestone_3_shapes() -> None:
    fixture = load_fixture()
    case_ids = {str(case["id"]) for case in fixture["cases"] if isinstance(case, dict)}

    assert case_ids == {
        "exact_project_phrase",
        "platform_support_over_linux_noise",
        "local_core_relay_architecture",
        "mcp_provider_ingestion",
        "coverage_gap_abstention",
    }
    assert "Noah" not in _fixture_text()
    assert "C:\\Users\\" not in _fixture_text()


def test_report_is_content_free_and_measures_required_fields(tmp_path: Path) -> None:
    report = run(tmp_path)
    serialized = json.dumps(report, sort_keys=True)
    fixture = load_fixture()

    assert report["content_free"] is True
    assert report["case_count"] == 5
    assert "record_id" not in serialized
    for record in fixture["records"]:
        assert isinstance(record, dict)
        assert str(record["content"]) not in serialized
    for case in fixture["cases"]:
        assert isinstance(case, dict)
        assert str(case["query"]) not in serialized
    for score in report["cases"]:
        assert isinstance(score, dict)
        assert {
            "precision_at_5_or_returned_depth",
            "first_relevant_rank",
            "returned_count",
            "passed",
        } <= set(score)
    assert {
        "aggregate_precision",
        "passed_case_count",
        "returned_count",
        "passed",
    } <= set(report["scorecard"])


def test_separate_runs_are_deterministic(tmp_path: Path) -> None:
    first = run(tmp_path / "first")
    second = run(tmp_path / "second")

    assert first == second
    assert all(case["deterministic"] for case in first["cases"])


def test_existing_core_database_is_refused(tmp_path: Path) -> None:
    (tmp_path / "core.sqlite3").write_text("synthetic sentinel", encoding="utf-8")

    with pytest.raises(PrecisionEvaluationError, match="existing Core database"):
        run(tmp_path)


def test_checked_in_baseline_is_preserved_as_a_historical_snapshot() -> None:
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))

    assert baseline["captured_revision"] == "f5e3a2b6e7f86ad65c0bf9aa78d6baa8b639456f"
    assert baseline["fixture_sha256"] == hashlib.sha256(FIXTURES.read_bytes()).hexdigest()
    assert baseline["case_count"] == 5
    assert baseline["passed"] is False
    assert baseline["scorecard"]["passed_case_count"] < baseline["case_count"]


def test_current_production_quality_gate_passes(tmp_path: Path) -> None:
    report = run(tmp_path)

    assert report["passed"] is True
    assert report["scorecard"]["passed_case_count"] == report["case_count"] == 5
    assert report["scorecard"]["aggregate_precision"] == 1.0
    assert report["scorecard"]["abstention_case_count"] == 1
