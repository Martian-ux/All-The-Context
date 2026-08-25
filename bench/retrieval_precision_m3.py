"""Synthetic Milestone 3 retrieval-precision regression lane.

This developer-facing evaluator creates a disposable fictional Core vault,
writes observations through the public CoreStore API, and searches through the
production RetrievalEngine facade. It measures known lexical precision failure
shapes without changing retrieval, storage, ingestion, or provider code.

Reports intentionally contain only case labels and aggregate scores. Fixture
text, queries, record IDs, and trace IDs never leave the isolated run.
"""

from __future__ import annotations

# The repository-local source path is intentionally inserted before imports;
# E402 is expected for this standalone, cross-checking benchmark entry point.
# ruff: noqa: E402, I001

import argparse
import hashlib
import json
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_SOURCE = _REPO_ROOT / "packages" / "allthecontext" / "src"
if not (_LOCAL_SOURCE / "allthecontext" / "__init__.py").is_file():
    raise RuntimeError("Milestone 3 precision benchmark requires the repository source tree")
if str(_LOCAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(_LOCAL_SOURCE))

from allthecontext.models import CandidateInput, SearchRequest
from allthecontext.retrieval import RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

FIXTURES = Path(__file__).with_name("retrieval_precision_m3_fixtures.json")
DEFAULT_BASELINE = Path(__file__).parent / "baselines" / "retrieval_precision_m3_f5e3a2b.json"
FIXTURE_SCHEMA = "atc.retrieval-precision-m3.fixture.v1"
SCORECARD_SCHEMA = "atc.retrieval-precision-m3.scorecard.v1"
REPORT_KIND = "synthetic_retrieval_precision_m3"
READER = ClientPrincipal(
    "synthetic-m3-reader",
    "Synthetic Milestone 3 reader",
    frozenset({"context:read"}),
)


class PrecisionEvaluationError(ValueError):
    """Raised when the synthetic fixture or isolation boundary is invalid."""


@dataclass(frozen=True, slots=True)
class CaseScore:
    case_id: str
    precision: float | None
    scored_depth: int
    first_relevant_rank: int | None
    returned_count: int
    abstained: bool
    deterministic: bool
    passed: bool


def _load_json_object(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise PrecisionEvaluationError(f"{path.name} must contain a JSON object")
    return loaded


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PrecisionEvaluationError(f"{field} must be a list of nonempty strings")
    return [str(item) for item in value]


def load_fixture(path: Path = FIXTURES) -> dict[str, Any]:
    """Load and validate the bounded, fictional precision fixture."""

    fixture = _load_json_object(path)
    if fixture.get("schema") != FIXTURE_SCHEMA or fixture.get("schema_version") != 1:
        raise PrecisionEvaluationError("unsupported Milestone 3 precision fixture schema")
    records = fixture.get("records")
    cases = fixture.get("cases")
    if not isinstance(records, list) or not records:
        raise PrecisionEvaluationError("fixture records are required")
    if not isinstance(cases, list) or not cases:
        raise PrecisionEvaluationError("fixture cases are required")
    aliases = [str(item["alias"]) for item in records if isinstance(item, dict)]
    if len(aliases) != len(set(aliases)):
        raise PrecisionEvaluationError("record aliases must be unique")
    case_ids = [str(item["id"]) for item in cases if isinstance(item, dict)]
    if len(case_ids) != len(set(case_ids)):
        raise PrecisionEvaluationError("case IDs must be unique")
    for item in records:
        if not isinstance(item, dict):
            raise PrecisionEvaluationError("record entries must be objects")
        if not str(item.get("alias", "")) or not str(item.get("content", "")):
            raise PrecisionEvaluationError("each record needs an alias and content")
    known_aliases = set(aliases)
    for case in cases:
        if not isinstance(case, dict):
            raise PrecisionEvaluationError("case entries must be objects")
        if not str(case.get("id", "")) or not str(case.get("query", "")):
            raise PrecisionEvaluationError("each case needs an ID and query")
        limit = case.get("limit")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise PrecisionEvaluationError("case limits must be integers from 1 through 100")
        for alias in _string_list(case.get("relevant_aliases", []), field="relevant_aliases"):
            if alias not in known_aliases:
                raise PrecisionEvaluationError(f"case references unknown relevant alias {alias}")
        if not isinstance(case.get("expect_abstention", False), bool):
            raise PrecisionEvaluationError("expect_abstention must be a boolean")
    return fixture


def _candidate(spec: Mapping[str, Any]) -> CandidateInput:
    """Convert one fictional fixture row into a directly admissible observation."""

    return CandidateInput(
        kind=str(spec["kind"]),
        content=str(spec["content"]),
        scopes=_string_list(spec.get("scopes", []), field="record.scopes"),
        tags=_string_list(spec.get("tags", []), field="record.tags"),
        source_reference=f"synthetic-m3:{spec['alias']}",
        source_service="synthetic-m3-lab",
        source_type="synthetic_fixture",
        evidence="Fictional benchmark observation; not operator context.",
        observed_at="2026-08-01T00:00:00+00:00",
        explicit_user_statement=True,
    )


def _apply_records(store: CoreStore, fixture: Mapping[str, Any]) -> dict[str, str]:
    record_ids: dict[str, str] = {}
    for spec in fixture["records"]:
        if not isinstance(spec, dict):
            raise PrecisionEvaluationError("record entries must be objects")
        alias = str(spec["alias"])
        observation = store.add_candidate(_candidate(spec))
        if observation.disposition.value != "applied" or observation.record_id is None:
            raise PrecisionEvaluationError(f"synthetic record {alias} was not applied")
        record_ids[alias] = observation.record_id
    return record_ids


def _round_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _score_case(
    engine: RetrievalEngine,
    case: Mapping[str, Any],
    record_ids: Mapping[str, str],
) -> CaseScore:
    case_id = str(case["id"])
    request = SearchRequest(query=str(case["query"]), limit=int(case["limit"]))
    first = engine.search(request, READER)
    second = engine.search(request, READER)
    first_ids = [item.id for item in first.items]
    second_ids = [item.id for item in second.items]
    deterministic = first_ids == second_ids and first.total == second.total

    relevant_ids = {
        record_ids[alias]
        for alias in _string_list(case.get("relevant_aliases", []), field="relevant_aliases")
    }
    returned_count = len(first.items)
    scored_items = first.items[:5]
    scored_depth = len(scored_items)
    precision = _round_ratio(
        sum(item.id in relevant_ids for item in scored_items),
        scored_depth,
    )
    first_relevant_rank = next(
        (rank for rank, item in enumerate(first.items, 1) if item.id in relevant_ids),
        None,
    )
    abstained = returned_count == 0
    expect_abstention = bool(case.get("expect_abstention", False))
    required_relevance = relevant_ids <= set(first_ids)
    quality_pass = (
        not expect_abstention
        and bool(relevant_ids)
        and required_relevance
        and precision == 1.0
        and first_relevant_rank is not None
    )
    abstention_pass = expect_abstention and abstained and not relevant_ids
    return CaseScore(
        case_id=case_id,
        precision=precision,
        scored_depth=scored_depth,
        first_relevant_rank=first_relevant_rank,
        returned_count=returned_count,
        abstained=abstained,
        deterministic=deterministic,
        passed=(quality_pass or abstention_pass) and deterministic,
    )


def _scorecard(scores: Sequence[CaseScore]) -> dict[str, Any]:
    precision_values = [score.precision for score in scores if score.precision is not None]
    aggregate_precision = (
        round(sum(precision_values) / len(precision_values), 6) if precision_values else None
    )
    return {
        "schema": SCORECARD_SCHEMA,
        "case_count": len(scores),
        "passed_case_count": sum(score.passed for score in scores),
        "aggregate_precision": aggregate_precision,
        "returned_count": sum(score.returned_count for score in scores),
        "abstention_case_count": sum(score.abstained for score in scores),
        "deterministic": all(score.deterministic for score in scores),
        "passed": all(score.passed for score in scores),
    }


def _content_free_case(score: CaseScore) -> dict[str, Any]:
    return {
        "case": score.case_id,
        "precision_at_5_or_returned_depth": score.precision,
        "scored_depth": score.scored_depth,
        "first_relevant_rank": score.first_relevant_rank,
        "returned_count": score.returned_count,
        "abstained": score.abstained,
        "deterministic": score.deterministic,
        "passed": score.passed,
    }


def _isolated_parent(work_dir: Path) -> Path:
    resolved = work_dir.expanduser().resolve()
    if (resolved / "core.sqlite3").exists():
        raise PrecisionEvaluationError("refusing to reuse an existing Core database")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def run(work_dir: Path, *, fixture_path: Path = FIXTURES) -> dict[str, Any]:
    """Run the precision lane in a temporary synthetic vault."""

    fixture = load_fixture(fixture_path)
    parent = _isolated_parent(work_dir)
    scores: list[CaseScore] = []
    with tempfile.TemporaryDirectory(
        prefix="atc-m3-retrieval-precision-", dir=parent
    ) as vault_path:
        store = CoreStore(Path(vault_path) / "core.sqlite3")
        try:
            store.initialize_vault("Synthetic Milestone 3 Vault", "UTC")
            record_ids = _apply_records(store, fixture)
            engine = RetrievalEngine(store)
            for case in fixture["cases"]:
                if not isinstance(case, dict):
                    raise PrecisionEvaluationError("case entries must be objects")
                scores.append(_score_case(engine, case, record_ids))
        finally:
            store.close()
    scorecard = _scorecard(scores)
    return {
        "schema": SCORECARD_SCHEMA,
        "report_kind": REPORT_KIND,
        "authority": "developer_evaluation_only",
        "content_free": True,
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "case_count": len(scores),
        "cases": [_content_free_case(score) for score in scores],
        "scorecard": scorecard,
        "passed": scorecard["passed"],
    }


def baseline_payload(report: Mapping[str, Any], *, captured_revision: str) -> dict[str, Any]:
    """Return a content-free baseline snapshot with an explicit revision marker."""

    if not captured_revision:
        raise PrecisionEvaluationError("captured_revision is required for a baseline snapshot")

    return {
        "schema": SCORECARD_SCHEMA,
        "report_kind": REPORT_KIND,
        "captured_revision": captured_revision,
        "fixture_sha256": report["fixture_sha256"],
        "case_count": report["case_count"],
        "cases": report["cases"],
        "scorecard": report["scorecard"],
        "passed": report["passed"],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a content-free human-readable scorecard."""

    scorecard = report["scorecard"]
    assert isinstance(scorecard, dict)
    lines = [
        "# Synthetic Milestone 3 retrieval precision",
        "",
        "Developer evaluation only; no production authority.",
        "",
        "| Case | Precision @5/returned depth | First relevant | Returned | Abstained | Pass |",
        "|---|---:|---:|---:|:---:|:---:|",
    ]
    for case in report["cases"]:
        assert isinstance(case, dict)
        precision = case["precision_at_5_or_returned_depth"]
        rendered_precision = "—" if precision is None else f"{precision:.3f}"
        first = case["first_relevant_rank"]
        lines.append(
            f"| `{case['case']}` | {rendered_precision} | {first or '—'} | "
            f"{case['returned_count']} | {'yes' if case['abstained'] else 'no'} | "
            f"{'pass' if case['passed'] else 'fail'} |"
        )
    lines.extend(
        [
            "",
            f"Aggregate precision: {scorecard['aggregate_precision']}",
            f"Aggregate pass: {'pass' if scorecard['passed'] else 'fail'}",
            f"Deterministic: {'yes' if scorecard['deterministic'] else 'no'}",
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the content-free JSON report here.")
    parser.add_argument(
        "--markdown", type=Path, help="Write the content-free Markdown report here."
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Parent for the disposable synthetic vault; it must not contain core.sqlite3.",
    )
    parser.add_argument("--fixture", type=Path, default=FIXTURES)
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write a baseline shape from this run; requires --captured-revision.",
    )
    parser.add_argument(
        "--captured-revision",
        help="Explicit source revision to record when writing a baseline.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Baseline path used with --write-baseline.",
    )
    parser.add_argument(
        "--fail-on-quality",
        action="store_true",
        help="Return nonzero when the measured aggregate is not passing.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    temporary: tempfile.TemporaryDirectory[str] | None = None
    if args.work_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="atc-m3-retrieval-precision-parent-")
        work_dir = Path(temporary.name)
    else:
        work_dir = args.work_dir
    try:
        report = run(work_dir, fixture_path=args.fixture)
    finally:
        if temporary is not None:
            temporary.cleanup()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {args.output}")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8")
        print(f"wrote {args.markdown}")
    if args.write_baseline:
        if not args.captured_revision:
            raise PrecisionEvaluationError("--captured-revision is required with --write-baseline")
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(
                baseline_payload(report, captured_revision=args.captured_revision),
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.baseline}")
    return 1 if args.fail_on_quality and not report["passed"] else 0


if __name__ == "__main__":
    sys.exit(main())
