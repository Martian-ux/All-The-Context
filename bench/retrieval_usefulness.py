"""Synthetic retrieval usefulness evaluation for provider-facing Core APIs.

The harness builds an isolated vault through public observation, deletion, and
forget APIs, then scores search, bootstrap, and get packaging. It never opens
the operator Core database, never logs raw personal context, and does not
change ingestion, storage schema, or live user data. It exercises the
checkout-local production retrieval facade and grants no acceptance credit.
"""

from __future__ import annotations

# The repository-local source path is intentionally inserted before imports;
# E402 is expected for this standalone, cross-checking benchmark entry point.
# ruff: noqa: E402, I001

import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

# Make the standalone benchmark self-contained when another checkout has been
# installed into the interpreter.  The benchmark must never silently measure
# a different source tree than the fixture and report being green.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_SOURCE = _REPO_ROOT / "packages" / "allthecontext" / "src"
if not (_LOCAL_SOURCE / "allthecontext" / "__init__.py").is_file():
    raise RuntimeError("retrieval usefulness benchmark requires the repository source tree")
if str(_LOCAL_SOURCE) not in sys.path:
    sys.path.insert(0, str(_LOCAL_SOURCE))

import allthecontext

from allthecontext.config import CoreConfig
from allthecontext.models import (
    BootstrapRequest,
    BootstrapResponse,
    CandidateInput,
    ContextRecordOut,
    SearchRequest,
    SearchResponse,
    Sensitivity,
)
from allthecontext.retrieval import RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore
from platformdirs import user_data_path

try:
    Path(allthecontext.__file__ or "").resolve().relative_to(_LOCAL_SOURCE / "allthecontext")
except ValueError as error:
    raise RuntimeError(
        "retrieval usefulness benchmark resolved allthecontext outside this checkout"
    ) from error

FIXTURES = Path(__file__).with_name("retrieval_usefulness_fixtures.json")
DEFAULT_BASELINE = Path(__file__).parent / "baselines" / "retrieval_usefulness_v1.json"
FIXTURE_SCHEMA = "atc.retrieval-usefulness.fixture.v1"
SCORECARD_SCHEMA = "atc.retrieval-usefulness.scorecard.v1"
REPORT_KIND = "synthetic_retrieval_usefulness"
SURFACE_VALUES = frozenset({"search", "bootstrap", "get"})
MUTATION_ACTIONS = frozenset({"delete", "forget"})
DIMENSIONS = (
    "current_facts",
    "stale_conflict_exclusion",
    "withdrawn_exclusion",
    "sensitivity",
    "provenance",
    "budget",
    "packaging",
)
PROVIDER_ITEM_FIELDS = (
    "id",
    "kind",
    "content",
    "scopes",
    "tags",
    "sensitivity",
    "availability",
    "confidence",
    "version",
    "content_hash",
    "created_at",
    "updated_at",
    "observation_origin",
    "policy_version",
)
FORBIDDEN_PACKAGING_FIELDS = (
    "ranking_explanations",
    "pipeline_diagnostics",
    "denied_ids",
)
COMPILER_ITEM_OVERHEAD_CHARS = 64

Surface = Literal["search", "bootstrap", "get"]


class UsefulnessError(ValueError):
    """Raised when the fixture, isolation boundary, or apply path is invalid."""


@dataclass(frozen=True, slots=True)
class AppliedObservation:
    alias: str
    observation_id: str
    record_id: str | None
    disposition: str
    content: str


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    dimensions: tuple[str, ...]
    passed: bool
    reason_codes: tuple[str, ...]
    required_hits: int
    required_total: int
    forbidden_leaks: int
    forbidden_total: int
    provenance_hits: int
    provenance_total: int
    packaging_hits: int
    packaging_total: int
    budget_ok: bool
    budget_checked: bool
    sensitivity_ok: bool
    sensitivity_checked: bool
    deterministic: bool


def _load_json_object(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise UsefulnessError(f"{path.name} must contain a JSON object")
    return loaded


def _string_list(value: object, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise UsefulnessError(f"{field} must be a list of nonempty strings")
    return [str(item) for item in value]


def _optional_string(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise UsefulnessError(f"{field} must be a nonempty string")
    return value


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else round(numerator / denominator, 6)


def _principal(spec: Mapping[str, Any]) -> ClientPrincipal:
    return ClientPrincipal(
        str(spec["id"]),
        str(spec["name"]),
        frozenset(_string_list(spec.get("scopes", []), field="principal.scopes")),
    )


def load_fixture(path: Path = FIXTURES) -> dict[str, Any]:
    """Load and validate the synthetic usefulness fixture."""

    fixture = _load_json_object(path)
    if fixture.get("schema") != FIXTURE_SCHEMA or fixture.get("schema_version") != 1:
        raise UsefulnessError("unsupported retrieval usefulness fixture schema")
    principals = fixture.get("principals")
    observations = fixture.get("observations")
    mutations = fixture.get("mutations")
    cases = fixture.get("cases")
    if not isinstance(principals, dict) or not principals:
        raise UsefulnessError("fixture principals are required")
    if not isinstance(observations, list) or not observations:
        raise UsefulnessError("fixture observations are required")
    if not isinstance(mutations, list):
        raise UsefulnessError("fixture mutations must be a list")
    if not isinstance(cases, list) or not cases:
        raise UsefulnessError("fixture cases are required")
    aliases = [str(item["alias"]) for item in observations if isinstance(item, dict)]
    if len(aliases) != len(set(aliases)):
        raise UsefulnessError("observation aliases must be unique")
    case_ids = [str(item["id"]) for item in cases if isinstance(item, dict)]
    if len(case_ids) != len(set(case_ids)):
        raise UsefulnessError("case ids must be unique")
    covered = {
        dimension
        for item in cases
        if isinstance(item, dict)
        for dimension in item.get("dimensions", [])
    }
    missing = [dimension for dimension in DIMENSIONS if dimension not in covered]
    if missing:
        raise UsefulnessError(f"fixture is missing dimensions: {', '.join(missing)}")
    return fixture


def default_live_data_dirs() -> tuple[Path, ...]:
    """Return configured/default Core data roots without opening a database."""

    roots = [Path(user_data_path("AllTheContext", "AllTheContext", roaming=False)).resolve()]
    configured = os.environ.get("ATC_CORE_DATA_DIR")
    if configured:
        roots.append(Path(configured).expanduser().resolve())
    unique: list[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return tuple(unique)


def assert_isolated_work_dir(work_dir: Path) -> Path:
    """Refuse live Core data roots and existing vault files."""

    resolved = work_dir.expanduser().resolve()
    for live in default_live_data_dirs():
        try:
            resolved.relative_to(live)
        except ValueError:
            continue
        raise UsefulnessError("refusing to use the live Core data directory")
    if (resolved / "core.sqlite3").is_file():
        raise UsefulnessError("refusing to reuse an existing Core database")
    return resolved


def _candidate_payload(
    spec: Mapping[str, Any],
    *,
    aliases: Mapping[str, AppliedObservation],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": spec["kind"],
        "content": spec["content"],
        "explicit_user_statement": bool(spec.get("explicit_user_statement", False)),
    }
    for field in (
        "entity_key",
        "attribute_key",
        "source_reference",
        "source_service",
        "source_type",
        "evidence",
        "observed_at",
        "valid_from",
        "expires_at",
        "sensitivity",
        "confidence",
        "structured_value",
    ):
        if field in spec:
            payload[field] = spec[field]
    for field in ("scopes", "tags", "allowed_clients", "denied_clients"):
        if field in spec:
            payload[field] = _string_list(spec[field], field=field)
    supersedes = spec.get("supersedes")
    if isinstance(supersedes, str) and supersedes.startswith("@"):
        target = aliases.get(supersedes[1:])
        if target is None or target.record_id is None:
            raise UsefulnessError(f"{spec.get('alias')} supersedes an unknown current record")
        payload["supersedes"] = target.record_id
    elif supersedes is not None:
        payload["supersedes"] = supersedes
    return payload


def _apply_corpus(store: CoreStore, fixture: Mapping[str, Any]) -> dict[str, AppliedObservation]:
    applied: dict[str, AppliedObservation] = {}
    for spec in fixture["observations"]:
        if not isinstance(spec, dict):
            raise UsefulnessError("observation entries must be objects")
        alias = str(spec["alias"])
        observation = store.add_candidate(
            CandidateInput(**_candidate_payload(spec, aliases=applied))
        )
        expected = str(spec.get("expected_disposition", "applied"))
        if observation.disposition.value != expected:
            raise UsefulnessError(
                f"{alias} disposition {observation.disposition.value} != {expected}"
            )
        applied[alias] = AppliedObservation(
            alias=alias,
            observation_id=observation.id,
            record_id=observation.record_id,
            disposition=observation.disposition.value,
            content=str(spec["content"]),
        )
    for spec in fixture["mutations"]:
        if not isinstance(spec, dict):
            raise UsefulnessError("mutation entries must be objects")
        action = str(spec["action"])
        if action not in MUTATION_ACTIONS:
            raise UsefulnessError(f"unsupported mutation action {action}")
        alias = str(spec["alias"])
        target = applied.get(alias)
        if target is None or target.record_id is None:
            raise UsefulnessError(f"{action} target {alias} is not a current record")
        if action == "delete":
            store.delete_record(target.record_id, reason=str(spec["reason"]))
            continue
        forget = store.add_candidate(
            CandidateInput(
                kind="context_forget",
                content=str(spec["content"]),
                supersedes=target.record_id,
                explicit_user_statement=True,
            )
        )
        if forget.disposition.value != "applied":
            raise UsefulnessError(
                f"forget for {alias} disposition {forget.disposition.value} != applied"
            )
    return applied


def _search_request(payload: Mapping[str, Any]) -> SearchRequest:
    return SearchRequest(
        query=str(payload.get("query", "")),
        scopes=_string_list(payload.get("scopes", []), field="request.scopes"),
        kinds=_string_list(payload.get("kinds", []), field="request.kinds"),
        as_of=_optional_string(payload.get("as_of"), field="request.as_of"),
        current_project=_optional_string(
            payload.get("current_project"), field="request.current_project"
        ),
        limit=int(payload.get("limit", 20)),
        offset=int(payload.get("offset", 0)),
    )


def _bootstrap_request(payload: Mapping[str, Any]) -> BootstrapRequest:
    return BootstrapRequest(
        query=str(payload.get("query", "")),
        requested_scopes=_string_list(
            payload.get("requested_scopes", []), field="request.requested_scopes"
        ),
        budget_chars=int(payload.get("budget_chars", 12_000)),
        current_project=_optional_string(
            payload.get("current_project"), field="request.current_project"
        ),
    )


def _provider_dump(value: SearchResponse | BootstrapResponse | ContextRecordOut) -> dict[str, Any]:
    return value.model_dump(mode="json")


def _execute_case(
    engine: RetrievalEngine,
    principal: ClientPrincipal,
    surface: Surface,
    request: Mapping[str, Any],
    applied: Mapping[str, AppliedObservation],
) -> tuple[list[ContextRecordOut], dict[str, Any], int | None]:
    if surface == "search":
        response = engine.search(_search_request(request), principal)
        return list(response.items), _provider_dump(response), None
    if surface == "bootstrap":
        bootstrap_request = _bootstrap_request(request)
        response = engine.bootstrap(bootstrap_request, principal)
        return list(response.items), _provider_dump(response), bootstrap_request.budget_chars
    alias = str(request["alias"])
    target = applied.get(alias)
    if target is None:
        raise UsefulnessError(f"get case references unknown alias {alias}")
    record = engine.get(target.record_id or "", principal) if target.record_id else None
    items = [] if record is None else [record]
    packaging = {} if record is None else _provider_dump(record)
    return items, packaging, None


def _item_by_id(items: Sequence[ContextRecordOut]) -> dict[str, ContextRecordOut]:
    return {item.id: item for item in items}


def _require_alias(
    applied: Mapping[str, AppliedObservation],
    alias: str,
    *,
    field: str,
) -> AppliedObservation:
    if alias not in applied:
        raise UsefulnessError(f"{field} references unknown alias {alias}")
    return applied[alias]


def _evaluate_case(
    case: Mapping[str, Any],
    *,
    engine: RetrievalEngine,
    principals: Mapping[str, ClientPrincipal],
    applied: Mapping[str, AppliedObservation],
) -> CaseOutcome:
    case_id = str(case["id"])
    dimensions = tuple(_string_list(case.get("dimensions", []), field=f"{case_id}.dimensions"))
    unknown = [dimension for dimension in dimensions if dimension not in DIMENSIONS]
    if unknown:
        raise UsefulnessError(f"{case_id} has unknown dimensions: {', '.join(unknown)}")
    surface = str(case["surface"])
    if surface not in SURFACE_VALUES:
        raise UsefulnessError(f"{case_id} has unsupported surface {surface}")
    principal_name = str(case["principal"])
    if principal_name not in principals:
        raise UsefulnessError(f"{case_id} has unknown principal {principal_name}")
    request = case.get("request")
    expect = case.get("expect")
    if not isinstance(request, dict) or not isinstance(expect, dict):
        raise UsefulnessError(f"{case_id} request and expect must be objects")
    first_items, packaging, budget = _execute_case(
        engine,
        principals[principal_name],
        cast(Surface, surface),
        request,
        applied,
    )
    repeated_items, repeated_packaging, _budget = _execute_case(
        engine,
        principals[principal_name],
        cast(Surface, surface),
        request,
        applied,
    )
    deterministic = [item.id for item in first_items] == [item.id for item in repeated_items] and [
        item.content for item in first_items
    ] == [item.content for item in repeated_items]
    _ = repeated_packaging
    items = first_items
    returned_ids = {item.id for item in items}
    returned_content = {item.content for item in items}
    by_id = _item_by_id(items)
    reasons: list[str] = []

    required_aliases = _string_list(
        expect.get("required_aliases", []), field=f"{case_id}.required_aliases"
    )
    forbidden_aliases = _string_list(
        expect.get("forbidden_aliases", []), field=f"{case_id}.forbidden_aliases"
    )
    required_content = _string_list(
        expect.get("required_content_aliases", []),
        field=f"{case_id}.required_content_aliases",
    )
    forbidden_content = _string_list(
        expect.get("forbidden_content_aliases", []),
        field=f"{case_id}.forbidden_content_aliases",
    )
    required_hits = 0
    required_total = 0
    forbidden_leaks = 0
    forbidden_total = 0

    for alias in required_aliases:
        required_total += 1
        record_id = _require_alias(applied, alias, field=f"{case_id}.required_aliases").record_id
        if record_id is not None and record_id in returned_ids:
            required_hits += 1
        else:
            reasons.append("missing_required_alias")
    for alias in required_content:
        required_total += 1
        content = _require_alias(
            applied, alias, field=f"{case_id}.required_content_aliases"
        ).content
        if content in returned_content:
            required_hits += 1
        else:
            reasons.append("missing_required_content")
    for alias in forbidden_aliases:
        forbidden_total += 1
        record_id = _require_alias(applied, alias, field=f"{case_id}.forbidden_aliases").record_id
        if record_id is not None and record_id in returned_ids:
            forbidden_leaks += 1
            reasons.append("leaked_forbidden_alias")
    for alias in forbidden_content:
        forbidden_total += 1
        content = _require_alias(
            applied, alias, field=f"{case_id}.forbidden_content_aliases"
        ).content
        if content in returned_content:
            forbidden_leaks += 1
            reasons.append("leaked_forbidden_content")

    provenance_fields = _string_list(
        expect.get("required_provenance_fields", []),
        field=f"{case_id}.required_provenance_fields",
    )
    provenance_hits = 0
    provenance_total = 0
    inspected: list[ContextRecordOut] = []
    for alias in required_aliases:
        record_id = applied[alias].record_id
        if record_id in by_id:
            inspected.append(by_id[record_id])
    if not inspected and provenance_fields:
        inspected = list(items)
    for item in inspected:
        dumped = item.model_dump(mode="json")
        for field in provenance_fields:
            provenance_total += 1
            value = dumped.get(field)
            if isinstance(value, str) and value.strip():
                provenance_hits += 1
            else:
                reasons.append("missing_provenance_field")

    packaging_hits = 0
    packaging_total = 0
    if expect.get("require_provider_packaging"):
        rendered = json.dumps(packaging, sort_keys=True)
        for field in FORBIDDEN_PACKAGING_FIELDS:
            packaging_total += 1
            if field in packaging or f'"{field}"' in rendered:
                reasons.append("leaked_diagnostic_packaging")
            else:
                packaging_hits += 1
        item_payloads: list[dict[str, Any]]
        if surface == "get":
            item_payloads = [packaging] if packaging else []
        else:
            raw_items = packaging.get("items", [])
            if not isinstance(raw_items, list):
                raw_items = []
            item_payloads = [item for item in raw_items if isinstance(item, dict)]
            packaging_total += 1
            if surface == "search":
                has_envelope = {"items", "total", "trace_id"} <= set(packaging)
            else:
                has_envelope = {
                    "items",
                    "context_mode",
                    "omitted_scopes",
                    "audit_trace_id",
                    "used_chars",
                    "pack_metadata",
                } <= set(packaging) and packaging.get("context_mode") == "local_core"
            if has_envelope:
                packaging_hits += 1
            else:
                reasons.append("missing_provider_envelope")
            if surface == "bootstrap":
                metadata = packaging.get("pack_metadata")
                packaging_total += 1
                required_metadata = {
                    "pack_schema",
                    "candidate_count",
                    "selected_count",
                    "omitted_count",
                    "budget_chars",
                    "used_chars",
                    "provenance_backed_count",
                    "candidate_pool_truncated",
                    "truncated",
                    "truncation_reasons",
                    "duplicate_suppressed_count",
                    "conflict_suppressed_count",
                    "selection_policy",
                }
                if isinstance(metadata, dict) and required_metadata <= set(metadata):
                    packaging_hits += 1
                else:
                    reasons.append("missing_pack_metadata")
                expectations = expect.get("pack_metadata_expectations", {})
                if not isinstance(expectations, dict):
                    raise UsefulnessError(f"{case_id}.pack_metadata_expectations must be an object")
                if expectations:
                    if not isinstance(metadata, dict):
                        reasons.append("missing_pack_metadata")
                    for field, expected in expectations.items():
                        packaging_total += 1
                        if isinstance(metadata, dict) and metadata.get(str(field)) == expected:
                            packaging_hits += 1
                        else:
                            reasons.append("pack_metadata_mismatch")
        for item in item_payloads:
            for field in PROVIDER_ITEM_FIELDS:
                packaging_total += 1
                if field in item:
                    packaging_hits += 1
                else:
                    reasons.append("missing_provider_field")

    budget_checked = budget is not None
    budget_ok = True
    if budget is not None:
        used = (
            int(packaging.get("used_chars", -1))
            if isinstance(packaging.get("used_chars"), int)
            else -1
        )
        content_chars = sum(len(item.content) for item in items)
        compiled_cost = sum(len(item.content) + COMPILER_ITEM_OVERHEAD_CHARS for item in items)
        if used < 0 or used > budget or content_chars > used or used != compiled_cost:
            budget_ok = False
            reasons.append("budget_violation")

    expected_sensitivity = expect.get("expected_sensitivity")
    sensitivity_checked = expected_sensitivity is not None
    sensitivity_ok = True
    if expected_sensitivity is not None:
        wanted = Sensitivity(str(expected_sensitivity))
        if not inspected or any(item.sensitivity != wanted for item in inspected):
            sensitivity_ok = False
            reasons.append("sensitivity_mismatch")

    if not deterministic:
        reasons.append("nondeterministic")

    passed = (
        required_hits == required_total
        and forbidden_leaks == 0
        and provenance_hits == provenance_total
        and packaging_hits == packaging_total
        and budget_ok
        and sensitivity_ok
        and deterministic
    )
    return CaseOutcome(
        case_id=case_id,
        dimensions=dimensions,
        passed=passed,
        reason_codes=tuple(dict.fromkeys(reasons)),
        required_hits=required_hits,
        required_total=required_total,
        forbidden_leaks=forbidden_leaks,
        forbidden_total=forbidden_total,
        provenance_hits=provenance_hits,
        provenance_total=provenance_total,
        packaging_hits=packaging_hits,
        packaging_total=packaging_total,
        budget_ok=budget_ok,
        budget_checked=budget_checked,
        sensitivity_ok=sensitivity_ok,
        sensitivity_checked=sensitivity_checked,
        deterministic=deterministic,
    )


def _dimension_score(outcomes: Sequence[CaseOutcome], dimension: str) -> float:
    selected = [item for item in outcomes if dimension in item.dimensions]
    return _ratio(sum(item.passed for item in selected), len(selected))


def _scorecard(outcomes: Sequence[CaseOutcome]) -> dict[str, Any]:
    required_hits = sum(item.required_hits for item in outcomes)
    required_total = sum(item.required_total for item in outcomes)
    forbidden_leaks = sum(item.forbidden_leaks for item in outcomes)
    forbidden_total = sum(item.forbidden_total for item in outcomes)
    provenance_hits = sum(item.provenance_hits for item in outcomes)
    provenance_total = sum(item.provenance_total for item in outcomes)
    packaging_hits = sum(item.packaging_hits for item in outcomes)
    packaging_total = sum(item.packaging_total for item in outcomes)
    budget_cases = [item for item in outcomes if item.budget_checked]
    sensitivity_cases = [item for item in outcomes if item.sensitivity_checked]
    metrics = {
        "current_fact_recall": _dimension_score(outcomes, "current_facts"),
        "stale_conflict_exclusion": _dimension_score(outcomes, "stale_conflict_exclusion"),
        "withdrawn_exclusion": _dimension_score(outcomes, "withdrawn_exclusion"),
        "sensitivity_respect": _dimension_score(outcomes, "sensitivity"),
        "provenance_completeness": _ratio(provenance_hits, provenance_total),
        "budget_compliance": _ratio(
            sum(item.budget_ok for item in budget_cases), len(budget_cases)
        ),
        "packaging_usefulness": _ratio(packaging_hits, packaging_total),
        "required_hit_rate": _ratio(required_hits, required_total),
        "forbidden_exclusion_rate": _ratio(forbidden_total - forbidden_leaks, forbidden_total),
        "forbidden_result_count": forbidden_leaks,
        "budget_violation_count": sum(not item.budget_ok for item in budget_cases),
        "packaging_violation_count": packaging_total - packaging_hits,
        "sensitivity_mismatch_count": sum(not item.sensitivity_ok for item in sensitivity_cases),
        "failed_case_count": sum(not item.passed for item in outcomes),
        "repeat_determinism": all(item.deterministic for item in outcomes),
    }
    gates = {
        "all_cases_passed": metrics["failed_case_count"] == 0,
        "zero_forbidden_results": metrics["forbidden_result_count"] == 0,
        "zero_budget_violations": metrics["budget_violation_count"] == 0,
        "zero_packaging_violations": metrics["packaging_violation_count"] == 0,
        "zero_sensitivity_mismatches": metrics["sensitivity_mismatch_count"] == 0,
        "repeat_deterministic": metrics["repeat_determinism"] is True,
        "isolated_synthetic_vault": True,
    }
    return {
        "schema": SCORECARD_SCHEMA,
        "case_count": len(outcomes),
        "passed_case_count": sum(item.passed for item in outcomes),
        "metrics": metrics,
        "gates": gates,
        "passed": all(gates.values()),
    }


def compare_to_baseline(
    report: Mapping[str, Any],
    baseline: Mapping[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    """Compare a candidate scorecard to the checked-in baseline."""

    messages: list[str] = []
    passed = True
    if baseline.get("schema") != SCORECARD_SCHEMA:
        return False, ("baseline schema is unsupported",)
    candidate_scorecard = report["scorecard"]
    assert isinstance(candidate_scorecard, dict)
    if report.get("fixture_sha256") != baseline.get("fixture_sha256"):
        passed = False
        messages.append("FAIL fixture hash differs from baseline")
    else:
        messages.append("PASS fixture hash matches baseline")
    baseline_gates = baseline.get("gates")
    if not isinstance(baseline_gates, dict):
        return False, ("baseline gates are required",)
    for name, expected in baseline_gates.items():
        actual = candidate_scorecard["gates"].get(name)
        if actual is True and expected is True:
            messages.append(f"PASS gate {name}")
            continue
        passed = False
        messages.append(f"FAIL gate {name}: {actual} != {expected}")
    baseline_metrics = baseline.get("metrics")
    if not isinstance(baseline_metrics, dict):
        return False, ("baseline metrics are required",)
    for name, expected in baseline_metrics.items():
        actual = candidate_scorecard["metrics"].get(name)
        if actual == expected:
            messages.append(f"PASS metric {name}")
            continue
        passed = False
        messages.append(f"FAIL metric {name}: {actual} != {expected}")
    return passed, tuple(messages)


def run(
    work_dir: Path,
    *,
    fixture_path: Path = FIXTURES,
) -> dict[str, Any]:
    """Evaluate retrieval usefulness in an isolated synthetic vault."""

    fixture = load_fixture(fixture_path)
    isolated_root = assert_isolated_work_dir(work_dir)
    isolated_root.mkdir(parents=True, exist_ok=True)
    vault_dir = Path(tempfile.mkdtemp(prefix="retrieval-usefulness-vault-", dir=isolated_root))
    if CoreConfig.in_directory(vault_dir).database_path.exists():
        raise UsefulnessError("refusing to reuse an existing Core database")
    store = CoreStore(vault_dir / "core.sqlite3")
    try:
        store.initialize_vault(
            str(fixture["vault"]["name"]),
            str(fixture["vault"]["display_timezone"]),
        )
        applied = _apply_corpus(store, fixture)
        engine = RetrievalEngine(store)
        principals = {
            name: _principal(spec)
            for name, spec in fixture["principals"].items()
            if isinstance(spec, dict)
        }
        outcomes = [
            _evaluate_case(
                case,
                engine=engine,
                principals=principals,
                applied=applied,
            )
            for case in fixture["cases"]
            if isinstance(case, dict)
        ]
    finally:
        store.close()
    scorecard = _scorecard(outcomes)
    return {
        "schema": SCORECARD_SCHEMA,
        "report_kind": REPORT_KIND,
        "authority": "developer_eval_no_production_authority",
        "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "case_count": len(outcomes),
        "cases": [
            {
                "id": item.case_id,
                "dimensions": list(item.dimensions),
                "passed": item.passed,
                "reason_codes": list(item.reason_codes),
            }
            for item in outcomes
        ],
        "scorecard": scorecard,
        "passed": scorecard["passed"],
        "validity_limitations": [
            "synthetic_sanitized_corpus_only",
            "isolated_temporary_vault",
            "public_observation_and_retrieval_apis",
            "no_live_core_database",
            "no_raw_personal_context",
            "not_a_user_distribution_or_latency_claim",
            "not_beta_acceptance_credit",
        ],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact, identifier-safe scorecard."""

    scorecard = report["scorecard"]
    assert isinstance(scorecard, dict)
    metrics = scorecard["metrics"]
    gates = scorecard["gates"]
    assert isinstance(metrics, dict)
    assert isinstance(gates, dict)
    lines = [
        "# Retrieval usefulness scorecard",
        "",
        f"Fixture `{report['fixture_sha256']}`; {report['case_count']} cases.",
        "Authority: developer evaluation only; no production ranking or acceptance credit.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Current fact recall | {metrics['current_fact_recall']:.3f} |",
        f"| Stale/conflict exclusion | {metrics['stale_conflict_exclusion']:.3f} |",
        f"| Withdrawn exclusion | {metrics['withdrawn_exclusion']:.3f} |",
        f"| Sensitivity respect | {metrics['sensitivity_respect']:.3f} |",
        f"| Provenance completeness | {metrics['provenance_completeness']:.3f} |",
        f"| Budget compliance | {metrics['budget_compliance']:.3f} |",
        f"| Packaging usefulness | {metrics['packaging_usefulness']:.3f} |",
        f"| Forbidden results | {metrics['forbidden_result_count']} |",
        f"| Failed cases | {metrics['failed_case_count']} |",
        "",
        "## Gates",
        "",
    ]
    for name, value in gates.items():
        lines.append(f"- `{name}`: {'pass' if value else 'fail'}")
    lines.extend(["", "## Cases", ""])
    for case in report["cases"]:
        assert isinstance(case, dict)
        status = "pass" if case["passed"] else "fail"
        reasons = ", ".join(str(code) for code in case["reason_codes"]) or "none"
        lines.append(f"- `{case['id']}`: {status} ({reasons})")
    lines.extend(
        [
            "",
            "## Validity limitations",
            "",
        ]
    )
    for item in report["validity_limitations"]:
        lines.append(f"- `{item}`")
    lines.append("")
    return "\n".join(lines)


def baseline_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    """Return the checked-in baseline shape without case identifiers."""

    scorecard = report["scorecard"]
    assert isinstance(scorecard, dict)
    return {
        "schema": SCORECARD_SCHEMA,
        "report_kind": REPORT_KIND,
        "fixture_sha256": report["fixture_sha256"],
        "case_count": report["case_count"],
        "metrics": scorecard["metrics"],
        "gates": scorecard["gates"],
        "passed": report["passed"],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path.")
    parser.add_argument("--markdown", type=Path, help="Write the Markdown scorecard to this path.")
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Parent directory for the isolated synthetic vault. Never a live Core data dir.",
    )
    parser.add_argument("--fixture", type=Path, default=FIXTURES)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=DEFAULT_BASELINE,
        help="Optional checked-in baseline to compare after the run.",
    )
    parser.add_argument(
        "--write-baseline",
        action="store_true",
        help="Rewrite the baseline file from this isolated run.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.work_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="atc-retrieval-usefulness-")
        work_dir = Path(temporary.name)
    else:
        temporary = None
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
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(baseline_payload(report), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {args.baseline}")
    elif args.baseline.exists():
        passed, messages = compare_to_baseline(report, _load_json_object(args.baseline))
        for message in messages:
            print(message)
        if not passed:
            return 1
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
