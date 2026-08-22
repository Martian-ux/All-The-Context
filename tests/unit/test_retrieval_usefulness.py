from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from allthecontext.admissibility import ConflictState
from allthecontext.config import CoreConfig
from allthecontext.models import ApprovalRequest, BootstrapRequest, CandidateInput, SearchRequest
from allthecontext.retrieval import RetrievalEngine, _admissibility_inputs, parse_query_intent
from allthecontext.storage import CoreStore

from bench.retrieval_usefulness import (
    DIMENSIONS,
    FIXTURES,
    FORBIDDEN_PACKAGING_FIELDS,
    REPORT_KIND,
    SCORECARD_SCHEMA,
    CaseOutcome,
    UsefulnessError,
    _apply_corpus,
    _bootstrap_request,
    _principal,
    _scorecard,
    assert_isolated_work_dir,
    compare_to_baseline,
    default_live_data_dirs,
    load_fixture,
    main,
    render_markdown,
    run,
)

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "bench" / "baselines" / "retrieval_usefulness_v1.json"
FIXTURE_SHA256 = "910e85defe71305f5554f4730b02e4261d0cf806da96bed8f1bfc8185c031022"


def _fixture() -> dict[str, object]:
    return load_fixture(FIXTURES)


def test_fixture_is_sanitized_and_covers_provider_usefulness_dimensions() -> None:
    fixture = _fixture()
    observations = fixture["observations"]
    cases = fixture["cases"]
    assert isinstance(observations, list)
    assert isinstance(cases, list)
    rendered = json.dumps(fixture)
    dimensions = {str(dimension) for case in cases for dimension in case["dimensions"]}

    assert fixture["schema"] == "atc.retrieval-usefulness.fixture.v1"
    assert dimensions == set(DIMENSIONS)
    assert {str(case["surface"]) for case in cases} == {"search", "bootstrap", "get"}
    assert all("password" not in str(item.get("content", "")).casefold() for item in observations)
    assert "core.sqlite3" not in rendered
    assert "C:\\\\Users" not in rendered
    assert "AppData" not in rendered
    assert "@" not in "".join(str(item.get("content", "")) for item in observations)


def test_runner_refuses_live_core_data_dir_and_existing_vault(tmp_path: Path) -> None:
    live = default_live_data_dirs()[0]
    with pytest.raises(UsefulnessError, match="live Core data directory"):
        assert_isolated_work_dir(live)
    existing = tmp_path / "existing-vault"
    existing.mkdir()
    (existing / "core.sqlite3").write_text("not a live vault", encoding="utf-8")
    with pytest.raises(UsefulnessError, match="existing Core database"):
        assert_isolated_work_dir(existing)


def test_harness_never_selects_the_default_core_database(tmp_path: Path) -> None:
    report = run(tmp_path / "isolated")
    default_db = CoreConfig.default().database_path
    rendered = json.dumps(report, sort_keys=True)

    assert report["passed"] is True
    assert report["report_kind"] == REPORT_KIND
    assert default_db.as_posix() not in rendered
    assert str(default_db) not in rendered
    assert "isolated_synthetic_vault" in report["scorecard"]["gates"]
    assert report["scorecard"]["gates"]["isolated_synthetic_vault"] is True


def test_bounded_usefulness_run_passes_every_scorecard_gate(tmp_path: Path) -> None:
    report = run(tmp_path)
    scorecard = report["scorecard"]
    metrics = scorecard["metrics"]

    assert scorecard["schema"] == SCORECARD_SCHEMA
    assert scorecard["passed"] is True
    assert all(scorecard["gates"].values())
    assert metrics["forbidden_result_count"] == 0
    assert metrics["budget_violation_count"] == 0
    assert metrics["packaging_violation_count"] == 0
    assert metrics["failed_case_count"] == 0
    assert metrics["repeat_determinism"] is True
    assert metrics["current_fact_recall"] == 1.0
    assert metrics["stale_conflict_exclusion"] == 1.0
    assert metrics["withdrawn_exclusion"] == 1.0
    assert metrics["sensitivity_respect"] == 1.0
    assert metrics["provenance_completeness"] == 1.0
    assert metrics["budget_compliance"] == 1.0
    assert metrics["packaging_usefulness"] == 1.0


def test_report_omits_record_content_queries_and_diagnostic_fields(tmp_path: Path) -> None:
    fixture = _fixture()
    report = run(tmp_path)
    rendered = json.dumps(report, sort_keys=True)
    markdown = render_markdown(report)
    observations = fixture["observations"]
    assert isinstance(observations, list)
    contents = {str(item["content"]) for item in observations}

    assert all(content not in rendered and content not in markdown for content in contents)
    assert all(field not in rendered for field in FORBIDDEN_PACKAGING_FIELDS)
    assert "Helios operator home city" not in rendered
    assert report["cases"][0]["id"] == "current_home_city_search"


def test_repeat_runs_are_deterministic(tmp_path: Path) -> None:
    first = run(tmp_path / "one")
    second = run(tmp_path / "two")
    assert first["scorecard"] == second["scorecard"]
    assert first["cases"] == second["cases"]
    assert first["fixture_sha256"] == second["fixture_sha256"]


def test_query_intent_uses_bounded_local_features() -> None:
    intent = parse_query_intent("Where is the latest Helios rollback runbook?")

    assert intent.raw_tokens == ("where", "is", "the", "latest", "helios", "rollback", "runbook")
    assert intent.focus_tokens == ("latest", "helios", "rollback", "runbook")
    assert {"current", "recent", "restore"} <= set(intent.expanded_tokens)
    assert intent.asks_current is True
    assert intent.asks_location is True
    assert intent.asks_procedure is True


def test_admissibility_uses_raw_query_tokens_not_focus_or_aliases(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "admissibility.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    candidate = store.add_candidate(
        CandidateInput(
            kind="location",
            content="Helios",
            scopes=["project:helios"],
            idempotency_key="raw-query-admissibility",
        )
    )
    record = store.approve_candidate(candidate.id, ApprovalRequest(), actor="test")
    try:
        with store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record.id,)
            ).fetchone()
            assert row is not None
            inputs, _context = _admissibility_inputs(
                [row],
                SearchRequest(query="what is Helios", current_project="helios"),
                {record.id: ConflictState.CLEAR},
            )
            assert inputs[0].signals.task_query_coverage == round(1 / 3, 6)

            alias_inputs, _alias_context = _admissibility_inputs(
                [row],
                SearchRequest(query="where", current_project="helios"),
                {record.id: ConflictState.CLEAR},
            )
            assert alias_inputs[0].signals.kind_compatibility == 0.0
    finally:
        store.close()


def test_empty_query_bootstrap_reports_omitted_bounded_pool(tmp_path: Path) -> None:
    store = CoreStore(tmp_path / "empty-pool.sqlite3")
    store.initialize_vault("synthetic", "UTC")
    for index in range(101):
        candidate = store.add_candidate(
            CandidateInput(
                kind="fact",
                content=f"empty pool record {index}",
                idempotency_key=f"empty-pool-{index}",
            )
        )
        store.approve_candidate(candidate.id, ApprovalRequest(), actor="test")
    principal = _principal(
        {"id": "reader", "name": "Synthetic reader", "scopes": ["context:read"]}
    )
    try:
        response = RetrievalEngine(store).bootstrap(
            BootstrapRequest(query="", requested_scopes=[], budget_chars=50_000), principal
        )
        metadata = response.pack_metadata
        assert metadata is not None
        assert metadata.candidate_pool_truncated is True
        assert metadata.candidate_count == 101
        assert metadata.selected_count == len(response.items) == 32
        assert metadata.omitted_count == metadata.candidate_count - metadata.selected_count
        assert metadata.used_chars == response.used_chars
        assert "candidate_pool" in metadata.truncation_reasons
    finally:
        store.close()


def test_bootstrap_pack_metadata_is_truthful_for_budget_truncation(tmp_path: Path) -> None:
    fixture = _fixture()
    assert isinstance(fixture["vault"], dict)
    assert isinstance(fixture["principals"], dict)

    store = CoreStore(tmp_path / "pack.sqlite3")
    store.initialize_vault(str(fixture["vault"]["name"]), str(fixture["vault"]["display_timezone"]))
    _apply_corpus(store, fixture)
    principal = _principal(fixture["principals"]["reader"])
    request = _bootstrap_request(fixture["cases"][12]["request"])

    response = RetrievalEngine(store).bootstrap(request, principal)
    assert response.pack_metadata is not None
    assert response.pack_metadata.truncated is True
    assert response.pack_metadata.truncation_reasons == ["budget"]
    assert response.pack_metadata.used_chars == response.used_chars
    assert response.pack_metadata.omitted_count > 0
    store.close()


def test_scorecard_fails_closed_on_leaks_and_budget() -> None:
    failed = CaseOutcome(
        case_id="synthetic-failure",
        dimensions=("current_facts", "budget", "packaging"),
        passed=False,
        reason_codes=("leaked_forbidden_content", "budget_violation"),
        required_hits=0,
        required_total=1,
        forbidden_leaks=2,
        forbidden_total=2,
        provenance_hits=0,
        provenance_total=0,
        packaging_hits=1,
        packaging_total=2,
        budget_ok=False,
        budget_checked=True,
        sensitivity_ok=True,
        sensitivity_checked=False,
        deterministic=True,
    )
    scorecard = _scorecard((failed,))
    assert scorecard["passed"] is False
    assert scorecard["metrics"]["forbidden_result_count"] == 2
    assert scorecard["metrics"]["budget_violation_count"] == 1
    assert scorecard["gates"]["all_cases_passed"] is False
    assert scorecard["gates"]["zero_forbidden_results"] is False


def test_checked_in_baseline_matches_isolated_run(tmp_path: Path) -> None:
    report = run(tmp_path)
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    passed, messages = compare_to_baseline(report, baseline)

    assert passed is True
    assert any("PASS fixture hash" in message for message in messages)
    assert report["fixture_sha256"] == FIXTURE_SHA256
    assert hashlib.sha256(FIXTURES.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert baseline["passed"] is True


def test_cli_writes_reports_and_exits_zero(tmp_path: Path) -> None:
    output = tmp_path / "usefulness.json"
    markdown = tmp_path / "usefulness.md"
    work_dir = tmp_path / "work"

    assert (
        main(
            [
                "--work-dir",
                str(work_dir),
                "--output",
                str(output),
                "--markdown",
                str(markdown),
            ]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert "Current fact recall" in markdown.read_text(encoding="utf-8")
    vaults = list(work_dir.glob("retrieval-usefulness-vault-*/core.sqlite3"))
    assert len(vaults) == 1
    assert vaults[0].is_file()
    assert CoreConfig.default().database_path not in vaults
