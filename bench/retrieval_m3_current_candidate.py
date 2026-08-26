"""Current-candidate Milestone 3 retrieval evaluation.

This developer-only lane uses a small fictional corpus and public Core
interfaces. It keeps direct-search cases separate from compositional
bootstrap cases, validates that split records contribute one distinct content
anchor each, and scores exact expected result sets so unjudged false positives
never receive free credit.

The emitted report is content-free: fixture text, queries, record IDs, vault
paths, and private context remain inside each disposable synthetic run.
"""

from __future__ import annotations

# The repository-local source path is intentionally inserted before imports;
# E402 is expected for this standalone, cross-checking benchmark entry point.
# ruff: noqa: E402, I001

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_SOURCE = _REPO_ROOT / "packages" / "allthecontext" / "src"
if not (_LOCAL_SOURCE / "allthecontext" / "__init__.py").is_file():
    raise RuntimeError("current-candidate retrieval evaluation requires the repository source tree")
if str(_LOCAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(_LOCAL_SOURCE))

from allthecontext.models import BootstrapRequest, CandidateInput, SearchRequest
from allthecontext.retrieval import RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

FIXTURES = Path(__file__).with_name("retrieval_m3_current_candidate_fixture.json")
FIXTURE_SCHEMA = "atc.retrieval-m3-current-candidate.fixture.v1"
SCORECARD_SCHEMA = "atc.retrieval-m3-current-candidate.scorecard.v1"
REPORT_KIND = "synthetic_retrieval_m3_current_candidate"
READER = ClientPrincipal(
    "synthetic-m3-current-reader",
    "Synthetic Milestone 3 current-candidate reader",
    frozenset({"context:read"}),
)
_TOKEN_RE = re.compile(r"[\w@]+", flags=re.UNICODE)
_SURFACES = frozenset({"search", "bootstrap"})


class CurrentCandidateEvaluationError(ValueError):
    """Raised when the current-candidate fixture or isolation boundary is invalid."""


@dataclass(frozen=True, slots=True)
class CaseScore:
    case_id: str
    surface: str
    precision: float | None
    recall: float | None
    union_coverage: float
    task_anchor_count: int
    union_anchor_count: int
    expected_count: int
    returned_count: int
    relevant_count: int
    false_positive_count: int
    missing_relevant_count: int
    abstained: bool
    exact_set_match: bool
    deterministic: bool
    noise_invariant: bool
    reason_codes: tuple[str, ...]
    passed: bool


@dataclass(frozen=True, slots=True)
class _CaseEvaluation:
    score: CaseScore
    returned_order: tuple[str, ...]
    relevant_order: tuple[str, ...]


def _load_json_object(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise CurrentCandidateEvaluationError(f"{path.name} must contain a JSON object")
    return loaded


def _string_list(value: object, *, field: str, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise CurrentCandidateEvaluationError(f"{field} must be a list of nonempty strings")
    if not allow_empty and not value:
        raise CurrentCandidateEvaluationError(f"{field} must not be empty")
    return [str(item) for item in value]


def _tokens(value: str) -> set[str]:
    return {match.group(0).casefold() for match in _TOKEN_RE.finditer(value)}


def _fixture_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(_REPO_ROOT)
    except ValueError as error:
        raise CurrentCandidateEvaluationError(
            "current-candidate fixture must be inside this repository"
        ) from error
    return resolved


def _record_map(fixture: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    records = fixture["records"]
    assert isinstance(records, list)
    return {str(record["alias"]): record for record in records if isinstance(record, dict)}


def _validate_anchor_distribution(
    case: Mapping[str, Any], records: Mapping[str, Mapping[str, Any]]
) -> None:
    if case.get("anchor_mode") != "split_distinct":
        return
    task_anchors = {
        anchor.casefold()
        for anchor in _string_list(
            case.get("task_anchors", []), field="case.task_anchors", allow_empty=True
        )
    }
    query_tokens = _tokens(str(case["query"]))
    anchor_aliases = _string_list(
        case.get("anchor_aliases", case.get("expected_aliases", [])),
        field="case.anchor_aliases",
        allow_empty=True,
    )
    contributions: list[set[str]] = []
    for alias in anchor_aliases:
        content_tokens = _tokens(str(records[alias]["content"]))
        if len(content_tokens & query_tokens) != 1:
            raise CurrentCandidateEvaluationError(
                f"{case['id']} requires one query token in each expected record content"
            )
        contributions.append(content_tokens & task_anchors)
    if any(len(contribution) != 1 for contribution in contributions):
        raise CurrentCandidateEvaluationError(
            f"{case['id']} requires exactly one genuine content anchor per expected record"
        )
    flattened = [next(iter(contribution)) for contribution in contributions]
    if len(flattened) != len(set(flattened)):
        raise CurrentCandidateEvaluationError(
            f"{case['id']} requires distinct content anchors across expected records"
        )


def load_fixture(path: Path = FIXTURES) -> dict[str, Any]:
    """Load and validate the bounded, fictional current-candidate fixture."""

    fixture_path = _fixture_path(path)
    fixture = _load_json_object(fixture_path)
    if fixture.get("schema") != FIXTURE_SCHEMA or fixture.get("schema_version") != 1:
        raise CurrentCandidateEvaluationError("unsupported current-candidate fixture schema")
    records = fixture.get("records")
    profiles = fixture.get("noise_profiles")
    cases = fixture.get("cases")
    if not isinstance(records, list) or not records:
        raise CurrentCandidateEvaluationError("fixture records are required")
    if not isinstance(profiles, dict):
        raise CurrentCandidateEvaluationError("fixture noise_profiles are required")
    if not isinstance(cases, list) or not cases:
        raise CurrentCandidateEvaluationError("fixture cases are required")

    aliases: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            raise CurrentCandidateEvaluationError("record entries must be objects")
        alias = str(record.get("alias", ""))
        if not alias or not str(record.get("kind", "")) or not str(record.get("content", "")):
            raise CurrentCandidateEvaluationError("each record needs alias, kind, and content")
        aliases.append(alias)
        anchors = _string_list(
            record.get("content_anchors", []),
            field=f"{alias}.content_anchors",
            allow_empty=True,
        )
        if len(anchors) != len(set(anchors)):
            raise CurrentCandidateEvaluationError(f"{alias}.content_anchors must be unique")
        content_tokens = _tokens(str(record["content"]))
        if any(anchor.casefold() not in content_tokens for anchor in anchors):
            raise CurrentCandidateEvaluationError(
                f"{alias}.content_anchors must occur directly in content"
            )
        for field in ("scopes", "tags"):
            _string_list(record.get(field, []), field=f"{alias}.{field}", allow_empty=True)
    if len(aliases) != len(set(aliases)):
        raise CurrentCandidateEvaluationError("record aliases must be unique")

    for profile_id, profile in profiles.items():
        if not isinstance(profile, dict):
            raise CurrentCandidateEvaluationError(f"noise profile {profile_id} must be an object")
        count = profile.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= 512:
            raise CurrentCandidateEvaluationError(
                f"noise profile {profile_id} count must be an integer from 1 through 512"
            )
        if not str(profile.get("kind", "")) or not str(profile.get("content", "")):
            raise CurrentCandidateEvaluationError(
                f"noise profile {profile_id} needs kind and content"
            )
        for field in ("scopes", "tags"):
            _string_list(profile.get(field, []), field=f"{profile_id}.{field}", allow_empty=True)

    record_by_alias = _record_map(fixture)
    case_ids: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            raise CurrentCandidateEvaluationError("case entries must be objects")
        case_id = str(case.get("id", ""))
        if not case_id or str(case.get("surface", "")) not in _SURFACES:
            raise CurrentCandidateEvaluationError("each case needs a supported ID and surface")
        case_ids.append(case_id)
        if not str(case.get("query", "")):
            raise CurrentCandidateEvaluationError(f"{case_id} needs a query")
        record_aliases = _string_list(
            case.get("record_aliases", []), field=f"{case_id}.record_aliases"
        )
        expected_aliases = _string_list(
            case.get("expected_aliases", []),
            field=f"{case_id}.expected_aliases",
            allow_empty=True,
        )
        unknown = set(record_aliases) - set(record_by_alias)
        if unknown:
            raise CurrentCandidateEvaluationError(f"{case_id} references unknown record aliases")
        if not set(expected_aliases) <= set(record_aliases):
            raise CurrentCandidateEvaluationError(
                f"{case_id}.expected_aliases must be in record_aliases"
            )
        anchor_aliases = _string_list(
            case.get("anchor_aliases", case.get("expected_aliases", [])),
            field=f"{case_id}.anchor_aliases",
            allow_empty=True,
        )
        if not set(anchor_aliases) <= set(record_aliases):
            raise CurrentCandidateEvaluationError(
                f"{case_id}.anchor_aliases must be in record_aliases"
            )
        task_anchors = _string_list(case.get("task_anchors", []), field=f"{case_id}.task_anchors")
        if len(task_anchors) != len(set(task_anchors)):
            raise CurrentCandidateEvaluationError(f"{case_id}.task_anchors must be unique")
        if not isinstance(case.get("expect_abstention", False), bool):
            raise CurrentCandidateEvaluationError(f"{case_id}.expect_abstention must be boolean")
        limit = case.get("limit", 5)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise CurrentCandidateEvaluationError(f"{case_id}.limit must be 1 through 100")
        if str(case["surface"]) == "bootstrap":
            budget = case.get("budget_chars", 4_000)
            if not isinstance(budget, int) or isinstance(budget, bool) or budget < 256:
                raise CurrentCandidateEvaluationError(
                    f"{case_id}.budget_chars must be at least 256"
                )
        noise_profile = case.get("noise_profile")
        if noise_profile is not None and str(noise_profile) not in profiles:
            raise CurrentCandidateEvaluationError(f"{case_id} references an unknown noise profile")
        if noise_profile is not None:
            profile = profiles[str(noise_profile)]
            assert isinstance(profile, dict)
            task_anchor_set = {anchor.casefold() for anchor in task_anchors}
            content_tokens = _tokens(str(profile["content"]))
            metadata_tokens = _tokens(
                " ".join(
                    [
                        str(profile["kind"]),
                        *_string_list(
                            profile.get("scopes", []),
                            field=f"{noise_profile}.scopes",
                            allow_empty=True,
                        ),
                        *_string_list(
                            profile.get("tags", []),
                            field=f"{noise_profile}.tags",
                            allow_empty=True,
                        ),
                    ]
                )
            )
            if content_tokens & task_anchor_set:
                raise CurrentCandidateEvaluationError(
                    f"{case_id} noise content must not contain task anchors"
                )
            if not task_anchor_set <= metadata_tokens:
                raise CurrentCandidateEvaluationError(
                    f"{case_id} noise metadata must contain every task anchor"
                )
        compare_with = case.get("compare_with")
        if compare_with is not None and not isinstance(compare_with, str):
            raise CurrentCandidateEvaluationError(f"{case_id}.compare_with must be a string")
        _validate_anchor_distribution(case, record_by_alias)

    if len(case_ids) != len(set(case_ids)):
        raise CurrentCandidateEvaluationError("case IDs must be unique")
    case_id_set = set(case_ids)
    for case in cases:
        if isinstance(case, dict) and case.get("compare_with") not in (None, *case_id_set):
            raise CurrentCandidateEvaluationError(
                f"{case['id']} compare_with references an unknown case"
            )
    return fixture


def _candidate(spec: Mapping[str, Any], *, alias: str) -> CandidateInput:
    return CandidateInput(
        kind=str(spec["kind"]),
        content=str(spec["content"]),
        scopes=_string_list(spec.get("scopes", []), field=f"{alias}.scopes", allow_empty=True),
        tags=_string_list(spec.get("tags", []), field=f"{alias}.tags", allow_empty=True),
        source_reference=f"synthetic-m3-current:{alias}",
        source_service="synthetic-m3-current-candidate",
        source_type="synthetic_fixture",
        evidence="Fictional current-candidate evaluation observation.",
        observed_at="2026-08-01T00:00:00+00:00",
        explicit_user_statement=True,
        idempotency_key=f"synthetic-m3-current:{alias}",
    )


def _noise_specs(
    profile_id: str, profile: Mapping[str, Any]
) -> list[tuple[str, Mapping[str, Any]]]:
    specs: list[tuple[str, Mapping[str, Any]]] = []
    for index in range(int(profile["count"])):
        alias = f"{profile_id}_{index:03d}"
        spec = dict(profile)
        spec["content"] = f"{profile['content']} marker{index:03d}"
        specs.append((alias, spec))
    return specs


def _apply_case(
    store: CoreStore, fixture: Mapping[str, Any], case: Mapping[str, Any]
) -> dict[str, str]:
    records = _record_map(fixture)
    record_ids: dict[str, str] = {}
    specs: list[tuple[str, Mapping[str, Any]]] = [
        (alias, records[alias])
        for alias in _string_list(case["record_aliases"], field="case.record_aliases")
    ]
    noise_profile = case.get("noise_profile")
    if noise_profile is not None:
        profiles = fixture["noise_profiles"]
        assert isinstance(profiles, dict)
        profile = profiles[str(noise_profile)]
        assert isinstance(profile, dict)
        specs.extend(_noise_specs(str(noise_profile), profile))
    for alias, spec in specs:
        observation = store.add_candidate(_candidate(spec, alias=alias))
        if observation.disposition.value != "applied" or observation.record_id is None:
            raise CurrentCandidateEvaluationError(f"synthetic case record {alias} was not applied")
        record_ids[alias] = observation.record_id
    return record_ids


def _execute(
    engine: RetrievalEngine, case: Mapping[str, Any]
) -> tuple[list[Any], int | None, int | None]:
    surface = str(case["surface"])
    query = str(case["query"])
    if surface == "search":
        response = engine.search(
            SearchRequest(query=query, limit=int(case.get("limit", 5))), READER
        )
        return list(response.items), response.total, None
    response = engine.bootstrap(
        BootstrapRequest(
            query=query,
            requested_scopes=[],
            budget_chars=int(case.get("budget_chars", 4_000)),
        ),
        READER,
    )
    return list(response.items), None, response.used_chars


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _evaluate_case(
    store: CoreStore,
    fixture: Mapping[str, Any],
    case: Mapping[str, Any],
) -> _CaseEvaluation:
    record_ids = _apply_case(store, fixture, case)
    engine = RetrievalEngine(store)
    first_items, first_total, first_used = _execute(engine, case)
    second_items, second_total, second_used = _execute(engine, case)
    first_ids = [item.id for item in first_items]
    second_ids = [item.id for item in second_items]
    deterministic = (
        first_ids == second_ids and first_total == second_total and first_used == second_used
    )
    alias_by_id = {record_id: alias for alias, record_id in record_ids.items()}
    returned_aliases = [alias_by_id[item.id] for item in first_items if item.id in alias_by_id]
    expected_aliases = _string_list(
        case.get("expected_aliases", []),
        field=f"{case['id']}.expected_aliases",
        allow_empty=True,
    )
    expected_set = set(expected_aliases)
    returned_set = set(returned_aliases)
    relevant_order = tuple(alias for alias in returned_aliases if alias in expected_set)
    relevant_count = len(returned_set & expected_set)
    false_positive_count = len(returned_set - expected_set)
    missing_relevant_count = len(expected_set - returned_set)
    task_anchors = {
        anchor.casefold()
        for anchor in _string_list(
            case.get("task_anchors", []),
            field=f"{case['id']}.task_anchors",
            allow_empty=True,
        )
    }
    records = _record_map(fixture)
    union_anchors: set[str] = set()
    for alias in relevant_order:
        content_tokens = _tokens(str(records[alias]["content"]))
        union_anchors.update(content_tokens & task_anchors)
    union_anchor_count = len(union_anchors)
    union_coverage = _ratio(union_anchor_count, len(task_anchors)) or 0.0
    exact_set_match = returned_set == expected_set
    abstained = not returned_aliases
    reasons: list[str] = []
    if missing_relevant_count:
        reasons.append("missing_relevant")
    if false_positive_count:
        reasons.append("unjudged_false_positive")
    if bool(case.get("expect_abstention", False)) and not abstained:
        reasons.append("abstention_violation")
    if not bool(case.get("expect_abstention", False)) and union_coverage < 1.0:
        reasons.append("union_coverage_shortfall")
    if not deterministic:
        reasons.append("nondeterministic_result")
    passed = deterministic and (
        (bool(case.get("expect_abstention", False)) and abstained)
        or (
            not bool(case.get("expect_abstention", False))
            and bool(expected_set)
            and exact_set_match
            and union_coverage == 1.0
        )
    )
    score = CaseScore(
        case_id=str(case["id"]),
        surface=str(case["surface"]),
        precision=_ratio(relevant_count, len(returned_aliases)),
        recall=_ratio(relevant_count, len(expected_set)),
        union_coverage=union_coverage,
        task_anchor_count=len(task_anchors),
        union_anchor_count=union_anchor_count,
        expected_count=len(expected_set),
        returned_count=len(returned_aliases),
        relevant_count=relevant_count,
        false_positive_count=false_positive_count,
        missing_relevant_count=missing_relevant_count,
        abstained=abstained,
        exact_set_match=exact_set_match,
        deterministic=deterministic,
        noise_invariant=True,
        reason_codes=tuple(reasons),
        passed=passed,
    )
    return _CaseEvaluation(
        score=score,
        returned_order=tuple(returned_aliases),
        relevant_order=relevant_order,
    )


def _isolated_parent(work_dir: Path) -> Path:
    resolved = work_dir.expanduser().resolve()
    try:
        resolved.relative_to(_REPO_ROOT)
    except ValueError as error:
        raise CurrentCandidateEvaluationError(
            "current-candidate work directory must be inside this repository"
        ) from error
    if (resolved / "core.sqlite3").exists():
        raise CurrentCandidateEvaluationError("refusing to reuse an existing Core database")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _scorecard(evaluations: Sequence[_CaseEvaluation]) -> dict[str, Any]:
    scores = [evaluation.score for evaluation in evaluations]
    by_id = {score.case_id: score for score in scores}

    def passed(case_id: str) -> bool:
        return by_id[case_id].passed

    positive_precisions = [score.precision for score in scores if score.precision is not None]
    gates = {
        "direct_exact_precision": passed("direct_exact_precision"),
        "direct_two_of_three_abstention": passed("direct_two_of_three_abstention"),
        "bootstrap_split_union_recall": passed("bootstrap_split_union_recall"),
        "bootstrap_union_insufficient_abstention": passed(
            "bootstrap_union_insufficient_abstention"
        ),
        "alias_only_rejected": passed("alias_only_not_full_coverage"),
        "metadata_only_rejected": all(
            passed(case_id)
            for case_id in (
                "kind_metadata_only_rejection",
                "tag_metadata_only_rejection",
                "scope_metadata_only_rejection",
            )
        ),
        "metadata_noise_invariant": all(
            score.noise_invariant
            for score in scores
            if score.case_id
            in {
                "direct_exact_metadata_noise_invariance",
                "bootstrap_split_metadata_noise_invariance",
            }
        ),
        "exact_set_judgment": all(score.false_positive_count == 0 for score in scores),
        "deterministic": all(score.deterministic for score in scores),
    }
    return {
        "schema": SCORECARD_SCHEMA,
        "case_count": len(scores),
        "passed_case_count": sum(score.passed for score in scores),
        "failed_case_count": sum(not score.passed for score in scores),
        "false_positive_count": sum(score.false_positive_count for score in scores),
        "aggregate_precision": (
            round(sum(positive_precisions) / len(positive_precisions), 6)
            if positive_precisions
            else None
        ),
        "gates": gates,
        "failed_cases": [score.case_id for score in scores if not score.passed],
        "failure_reasons": {
            score.case_id: list(score.reason_codes) for score in scores if score.reason_codes
        },
        "passed": all(gates.values()) and all(score.passed for score in scores),
    }


def _content_free_case(score: CaseScore) -> dict[str, Any]:
    return {
        "case": score.case_id,
        "surface": score.surface,
        "precision": score.precision,
        "recall": score.recall,
        "union_coverage": score.union_coverage,
        "task_anchor_count": score.task_anchor_count,
        "union_anchor_count": score.union_anchor_count,
        "expected_count": score.expected_count,
        "returned_count": score.returned_count,
        "relevant_count": score.relevant_count,
        "false_positive_count": score.false_positive_count,
        "missing_relevant_count": score.missing_relevant_count,
        "abstained": score.abstained,
        "exact_set_match": score.exact_set_match,
        "deterministic": score.deterministic,
        "noise_invariant": score.noise_invariant,
        "reason_codes": list(score.reason_codes),
        "passed": score.passed,
    }


def run(work_dir: Path, *, fixture_path: Path = FIXTURES) -> dict[str, Any]:
    """Run the current-candidate lane in one disposable vault per case."""

    fixture_path = fixture_path.expanduser().resolve()
    fixture = load_fixture(fixture_path)
    parent = _isolated_parent(work_dir)
    evaluations: list[_CaseEvaluation] = []
    cases = fixture["cases"]
    assert isinstance(cases, list)
    for case in cases:
        if not isinstance(case, dict):
            raise CurrentCandidateEvaluationError("case entries must be objects")
        with tempfile.TemporaryDirectory(prefix="atc-m3-current-candidate-", dir=parent) as vault:
            store = CoreStore(Path(vault) / "core.sqlite3")
            try:
                store.initialize_vault("Synthetic Milestone 3 Current Candidate", "UTC")
                evaluations.append(_evaluate_case(store, fixture, case))
            finally:
                store.close()

    by_id = {evaluation.score.case_id: evaluation for evaluation in evaluations}
    updated: list[_CaseEvaluation] = []
    for evaluation in evaluations:
        case = next(
            case
            for case in cases
            if isinstance(case, dict) and case["id"] == evaluation.score.case_id
        )
        compare_with = case.get("compare_with")
        if compare_with is None:
            updated.append(evaluation)
            continue
        baseline = by_id[str(compare_with)]
        invariant = evaluation.returned_order == baseline.returned_order
        score = replace(
            evaluation.score,
            noise_invariant=invariant,
            reason_codes=(
                (*evaluation.score.reason_codes, "metadata_noise_changed_returned_set")
                if not invariant
                else evaluation.score.reason_codes
            ),
            passed=evaluation.score.passed and invariant,
        )
        updated.append(
            _CaseEvaluation(
                score=score,
                returned_order=evaluation.returned_order,
                relevant_order=evaluation.relevant_order,
            )
        )
    evaluations = updated
    scorecard = _scorecard(evaluations)
    return {
        "schema": SCORECARD_SCHEMA,
        "report_kind": REPORT_KIND,
        "authority": "developer_evaluation_only",
        "content_free": True,
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "case_count": len(evaluations),
        "cases": [_content_free_case(evaluation.score) for evaluation in evaluations],
        "scorecard": scorecard,
        "passed": scorecard["passed"],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render the content-free current-candidate scorecard."""

    scorecard = report["scorecard"]
    assert isinstance(scorecard, dict)
    lines = [
        "# Milestone 3 current-candidate retrieval scorecard",
        "",
        "Developer evaluation only; no production authority.",
        "",
        (
            "| Case | Surface | Precision | Recall | Union | Returned | "
            "False positives | Abstained | Pass |"
        ),
        "|---|---|---:|---:|---:|---:|---:|:---:|:---:|",
    ]
    for case in report["cases"]:
        assert isinstance(case, dict)
        precision = case["precision"]
        recall = case["recall"]
        lines.append(
            f"| `{case['case']}` | {case['surface']} | "
            f"{'—' if precision is None else f'{precision:.3f}'} | "
            f"{'—' if recall is None else f'{recall:.3f}'} | "
            f"{case['union_coverage']:.3f} | {case['returned_count']} | "
            f"{case['false_positive_count']} | "
            f"{'yes' if case['abstained'] else 'no'} | "
            f"{'pass' if case['passed'] else 'fail'} |"
        )
    lines.extend(
        [
            "",
            f"Aggregate precision: {scorecard['aggregate_precision']}",
            f"Failed cases: {scorecard['failed_case_count']}",
            f"Aggregate pass: {'pass' if scorecard['passed'] else 'fail'}",
            f"Deterministic: {'yes' if scorecard['gates']['deterministic'] else 'no'}",
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
        help="Repository-local parent for disposable synthetic vaults.",
    )
    parser.add_argument("--fixture", type=Path, default=FIXTURES)
    parser.add_argument(
        "--fail-on-quality",
        action="store_true",
        help="Return nonzero when the measured scorecard is not passing.",
    )
    return parser


def _output_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(_REPO_ROOT)
    except ValueError as error:
        raise CurrentCandidateEvaluationError(
            "output path must be inside this repository"
        ) from error
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    work_dir = (
        args.work_dir
        if args.work_dir is not None
        else _REPO_ROOT / "tmp" / "retrieval-m3-current-candidate"
    )
    report = run(work_dir, fixture_path=args.fixture)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(rendered, end="")
    else:
        output = _output_path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
        print("wrote content-free JSON report")
    if args.markdown is not None:
        markdown = _output_path(args.markdown)
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_markdown(report), encoding="utf-8")
        print("wrote content-free Markdown scorecard")
    return 1 if args.fail_on_quality and not report["passed"] else 0


if __name__ == "__main__":
    sys.exit(main())
