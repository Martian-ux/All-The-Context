"""Frozen synthetic evaluator for Milestone 5 lane A / ZF-013.

This module is an isolated research harness.  It does not open Core, inspect a
workspace, enable capture, call a provider, or implement a production graph.
The lexical comparator is a small deterministic stand-in for the current
Retrieval V3 surface; the project filter and capsule are equally local
controls.  Reports contain aggregate metrics and hashes only, never fixture
IDs or text.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import statistics
import sys
import time
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).with_name("zf013_project_graph_fixtures.json")
CONTRACT = Path(__file__).with_name("zf013_project_graph_contract.json")
RELATION_FAMILIES = (
    "belongs_to",
    "supersedes",
    "depends_on",
    "blocks",
    "implements",
    "tested_by",
)
HARNESS_SELF_TEST_PASSED = "harness_self_test_passed"
HARNESS_SELF_TEST_FAILED = "harness_self_test_failed"
RELATION_DECISIONS = frozenset({"keep", "kill"})
STDLIB_LEXICAL_PROXY = "stdlib_lexical_proxy"
CURRENT_STATES = {"current"}
LIFECYCLE_STATES = {"current", "stale", "deleted", "purged"}
TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class _Record:
    record_id: str
    project_id: str
    state: str
    text: str
    capsule_rank: int | None


@dataclass(frozen=True, slots=True)
class _Relation:
    source: str
    family: str
    target: str


@dataclass(frozen=True, slots=True)
class _Query:
    query_id: str
    project_id: str
    terms: tuple[str, ...]
    required: frozenset[str]
    allowed: frozenset[str]
    target_relation_family: str
    ablation_case: bool
    max_items: int
    max_chars: int


@dataclass(frozen=True, slots=True)
class _Corpus:
    records: tuple[_Record, ...]
    records_by_id: Mapping[str, _Record]
    queries: tuple[_Query, ...]
    relations_by_source: Mapping[str, tuple[_Relation, ...]]
    relation_normalization: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class _Execution:
    result_ids: tuple[str, ...]
    max_depth_observed: int
    expanded_edges: int
    max_neighbors_observed: int
    cycle_revisits: int
    fanout_truncations: int
    bound_violations: int


@dataclass(frozen=True, slots=True)
class _QueryResult:
    query_id: str
    result_ids: tuple[str, ...]
    required_recall: float
    wrong_project: int
    stale: int
    deleted: int
    purged: int
    unnecessary: int
    character_count: int
    caos_success: bool
    max_depth_observed: int
    expanded_edges: int
    max_neighbors_observed: int
    cycle_revisits: int
    fanout_truncations: int
    bound_violations: int


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _list(value: object, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a JSON list")
    return value


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _canonical_value(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _canonical_value(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        normalized = [_canonical_value(item) for item in value]
        return sorted(
            normalized, key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":"))
        )
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _canonical_value(value), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def load_contract(path: Path = CONTRACT) -> dict[str, Any]:
    value = _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    if value.get("schema") != "atc.zf013.project-graph-contract.v1":
        raise ValueError("unsupported ZF-013 contract schema")
    profiles = _object(value.get("profiles"), "contract.profiles")
    for profile_name in (
        STDLIB_LEXICAL_PROXY,
        "structured_project_filter",
        "deterministic_project_context_capsule",
        "lexical_typed_one_hop",
        "bounded_typed_two_hop",
    ):
        _object(profiles.get(profile_name), f"contract.profiles.{profile_name}")
    production_retrieval_v3 = _object(
        value.get("production_retrieval_v3"), "contract.production_retrieval_v3"
    )
    if production_retrieval_v3.get("status") != "not_exercised":
        raise ValueError("production Retrieval V3 must remain explicitly not exercised")
    bounds = _object(value.get("bounds"), "contract.bounds")
    for key in (
        "max_depth",
        "max_expanded_edges_per_query",
        "max_neighbors_per_source",
        "max_accepted_cycle_revisits_per_query",
        "max_accepted_self_edges",
        "max_warm_p95_ms",
    ):
        if not isinstance(bounds.get(key), int | float):
            raise ValueError(f"contract.bounds.{key} must be numeric")
    relation_ablation = _object(value.get("relation_ablation"), "contract.relation_ablation")
    for key in ("min_required_recall_delta", "min_caos_delta"):
        if not isinstance(relation_ablation.get(key), int | float):
            raise ValueError(f"contract.relation_ablation.{key} must be numeric")
    if value.get("base_commit") != "fd1f802a67b3eb689ecdd4d85cd4440e1a57b7d2":
        raise ValueError("contract must remain pinned to the protected-main base")
    return value


def load_fixture(path: Path = FIXTURES) -> dict[str, Any]:
    value = _object(json.loads(path.read_text(encoding="utf-8")), str(path))
    if value.get("schema") != "atc.zf013.project-graph-fixture.v1":
        raise ValueError("unsupported ZF-013 fixture schema")
    projects = _list(value.get("projects"), "fixture.projects")
    records = _list(value.get("records"), "fixture.records")
    relations = _list(value.get("relations"), "fixture.relations")
    queries = _list(value.get("queries"), "fixture.queries")
    project_ids: set[str] = set()
    for index, item in enumerate(projects):
        row = _object(item, f"fixture.projects[{index}]")
        project_id = _text(row.get("id"), f"fixture.projects[{index}].id")
        _text(row.get("label"), f"fixture.projects[{index}].label")
        if project_id in project_ids:
            raise ValueError(f"duplicate project id: {project_id}")
        project_ids.add(project_id)

    record_ids: set[str] = set()
    record_projects: dict[str, str] = {}
    for index, item in enumerate(records):
        row = _object(item, f"fixture.records[{index}]")
        record_id = _text(row.get("id"), f"fixture.records[{index}].id")
        project_id = _text(row.get("project_id"), f"fixture.records[{index}].project_id")
        state = _text(row.get("state"), f"fixture.records[{index}].state")
        text = _text(row.get("text"), f"fixture.records[{index}].text")
        if project_id not in project_ids:
            raise ValueError(f"record {record_id} names an unknown project")
        if state not in LIFECYCLE_STATES:
            raise ValueError(f"record {record_id} has unsupported lifecycle state")
        if record_id in record_ids:
            raise ValueError(f"duplicate record id: {record_id}")
        if any(marker in text for marker in ("C:\\", "/Users/", "https://", "sk-")):
            raise ValueError("fixture text contains a forbidden live-data marker")
        record_ids.add(record_id)
        record_projects[record_id] = project_id

    relation_families: set[str] = set()
    for index, item in enumerate(relations):
        row = _object(item, f"fixture.relations[{index}]")
        source = _text(row.get("source"), f"fixture.relations[{index}].source")
        family = _text(row.get("family"), f"fixture.relations[{index}].family")
        target = _text(row.get("target"), f"fixture.relations[{index}].target")
        if source not in record_ids or target not in record_ids:
            raise ValueError(f"relation {source}->{target} names an unknown record")
        if family not in RELATION_FAMILIES:
            raise ValueError(f"relation family {family} is outside ZF-013 lane A")
        relation_families.add(family)
    if relation_families != set(RELATION_FAMILIES):
        raise ValueError("fixture must exercise every frozen relation family")

    query_ids: set[str] = set()
    for index, item in enumerate(queries):
        row = _object(item, f"fixture.queries[{index}]")
        query_id = _text(row.get("id"), f"fixture.queries[{index}].id")
        project_id = _text(row.get("project_id"), f"fixture.queries[{index}].project_id")
        terms = _list(row.get("terms"), f"fixture.queries[{index}].terms")
        required = _list(row.get("required"), f"fixture.queries[{index}].required")
        allowed = _list(row.get("allowed"), f"fixture.queries[{index}].allowed")
        family = _text(
            row.get("target_relation_family"), f"fixture.queries[{index}].target_relation_family"
        )
        if project_id not in project_ids or not terms or not required or not allowed:
            raise ValueError(f"query {query_id} has incomplete scope or evidence declarations")
        if query_id in query_ids:
            raise ValueError(f"duplicate query id: {query_id}")
        if family not in RELATION_FAMILIES:
            raise ValueError(f"query {query_id} names an unsupported relation family")
        if not isinstance(row.get("ablation_case"), bool):
            raise ValueError(f"query {query_id}.ablation_case must be boolean")
        if not all(isinstance(term, str) and term for term in terms):
            raise ValueError(f"query {query_id} terms must be nonempty strings")
        for field_name, values in (("required", required), ("allowed", allowed)):
            if not all(
                isinstance(record_id, str) and record_id in record_ids for record_id in values
            ):
                raise ValueError(f"query {query_id}.{field_name} names an unknown record")
        if not set(required) <= set(allowed):
            raise ValueError(f"query {query_id} required evidence must be allowed")
        if not isinstance(row.get("max_items"), int) or row["max_items"] <= 0:
            raise ValueError(f"query {query_id}.max_items must be positive")
        if not isinstance(row.get("max_chars"), int) or row["max_chars"] <= 0:
            raise ValueError(f"query {query_id}.max_chars must be positive")
        query_ids.add(query_id)
    if not queries:
        raise ValueError("fixture must contain at least one query")
    return value


def _normalize_relations(
    records: Sequence[_Record], raw_relations: Sequence[object]
) -> tuple[tuple[_Relation, ...], dict[str, Any]]:
    """Filter endpoint eligibility before deterministic graph normalization."""

    records_by_id = {record.record_id: record for record in records}
    rejected: defaultdict[str, int] = defaultdict(int)
    eligible: list[_Relation] = []
    for item in raw_relations:
        if not isinstance(item, dict):
            rejected["malformed_relation"] += 1
            continue
        source = item.get("source")
        family = item.get("family")
        target = item.get("target")
        if not (
            isinstance(source, str)
            and source
            and isinstance(family, str)
            and family
            and isinstance(target, str)
            and target
        ):
            rejected["malformed_relation"] += 1
            continue
        source_record = records_by_id.get(source)
        target_record = records_by_id.get(target)
        if source_record is None or target_record is None:
            rejected["unknown_endpoint"] += 1
            continue
        if source_record.state not in CURRENT_STATES:
            rejected[f"ineligible_source_{source_record.state}"] += 1
            continue
        if target_record.state not in CURRENT_STATES:
            rejected[f"ineligible_target_{target_record.state}"] += 1
            continue
        if source_record.project_id != target_record.project_id:
            rejected["cross_project"] += 1
            continue
        if family not in RELATION_FAMILIES:
            rejected["unsupported_relation_family"] += 1
            continue
        eligible.append(_Relation(source=source, family=family, target=target))

    relation_order = {family: index for index, family in enumerate(RELATION_FAMILIES)}
    accepted: list[_Relation] = []
    accepted_by_source: defaultdict[str, set[str]] = defaultdict(set)
    accepted_keys: set[tuple[str, str, str]] = set()

    def creates_cycle(source: str, target: str) -> bool:
        pending = [target]
        seen = {target}
        while pending:
            current = pending.pop()
            if current == source:
                return True
            for next_node in sorted(accepted_by_source.get(current, ())):
                if next_node not in seen:
                    seen.add(next_node)
                    pending.append(next_node)
        return False

    for relation in sorted(
        eligible, key=lambda item: (item.source, relation_order[item.family], item.target)
    ):
        key = (relation.source, relation.family, relation.target)
        if key in accepted_keys:
            rejected["duplicate_relation"] += 1
        elif relation.source == relation.target:
            rejected["self_edge"] += 1
        elif creates_cycle(relation.source, relation.target):
            rejected["cycle_edge"] += 1
        else:
            accepted.append(relation)
            accepted_keys.add(key)
            accepted_by_source[relation.source].add(relation.target)

    by_source: defaultdict[str, list[_Relation]] = defaultdict(list)
    for relation in accepted:
        by_source[relation.source].append(relation)
    normalized = tuple(
        sorted(
            accepted,
            key=lambda item: (item.source, relation_order[item.family], item.target),
        )
    )
    evidence = {
        "eligible_relation_count": len(normalized),
        "rejected_illegal_edge_evidence": dict(sorted(rejected.items())),
        "rejected_illegal_edge_count": sum(rejected.values()),
        "normalization_deterministic": True,
    }
    return normalized, {"by_source": by_source, **evidence}


def _corpus(fixture: Mapping[str, Any]) -> _Corpus:
    raw_records = _list(fixture["records"], "fixture.records")
    records = tuple(
        sorted(
            (
                _Record(
                    record_id=_text(_object(item, "record").get("id"), "record.id"),
                    project_id=_text(
                        _object(item, "record").get("project_id"), "record.project_id"
                    ),
                    state=_text(_object(item, "record").get("state"), "record.state"),
                    text=_text(_object(item, "record").get("text"), "record.text"),
                    capsule_rank=(
                        int(_object(item, "record")["capsule_rank"])
                        if _object(item, "record").get("capsule_rank") is not None
                        else None
                    ),
                )
                for item in raw_records
            ),
            key=lambda item: item.record_id,
        )
    )
    raw_relations = _list(fixture["relations"], "fixture.relations")
    _relations, relation_normalization = _normalize_relations(records, raw_relations)
    raw_queries = _list(fixture["queries"], "fixture.queries")
    queries = tuple(
        sorted(
            (
                _Query(
                    query_id=_text(_object(item, "query").get("id"), "query.id"),
                    project_id=_text(_object(item, "query").get("project_id"), "query.project_id"),
                    terms=tuple(
                        str(term).lower()
                        for term in _list(_object(item, "query").get("terms"), "query.terms")
                    ),
                    required=frozenset(
                        str(record_id)
                        for record_id in _list(
                            _object(item, "query").get("required"), "query.required"
                        )
                    ),
                    allowed=frozenset(
                        str(record_id)
                        for record_id in _list(
                            _object(item, "query").get("allowed"), "query.allowed"
                        )
                    ),
                    target_relation_family=_text(
                        _object(item, "query").get("target_relation_family"),
                        "query.target_relation_family",
                    ),
                    ablation_case=bool(_object(item, "query").get("ablation_case")),
                    max_items=int(_object(item, "query")["max_items"]),
                    max_chars=int(_object(item, "query")["max_chars"]),
                )
                for item in raw_queries
            ),
            key=lambda item: item.query_id,
        )
    )
    by_source = relation_normalization.pop("by_source")
    return _Corpus(
        records=records,
        records_by_id={record.record_id: record for record in records},
        queries=queries,
        relations_by_source={source: tuple(values) for source, values in by_source.items()},
        relation_normalization=relation_normalization,
    )


def _tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


def _ranked_lexical(
    corpus: _Corpus, query: _Query, project_filter: bool
) -> list[tuple[int, _Record]]:
    query_terms = set(query.terms)
    ranked: list[tuple[int, _Record]] = []
    for record in corpus.records:
        if record.state not in CURRENT_STATES:
            continue
        if project_filter and record.project_id != query.project_id:
            continue
        score = len(query_terms & _tokens(record.text))
        if score:
            ranked.append((score, record))
    return sorted(ranked, key=lambda item: (-item[0], item[1].record_id))


def _lexical_execution(corpus: _Corpus, query: _Query, project_filter: bool) -> _Execution:
    ranked = _ranked_lexical(corpus, query, project_filter)
    return _Execution(
        result_ids=tuple(record.record_id for _score, record in ranked[: query.max_items]),
        max_depth_observed=0,
        expanded_edges=0,
        max_neighbors_observed=0,
        cycle_revisits=0,
        fanout_truncations=0,
        bound_violations=0,
    )


def _capsule_execution(corpus: _Corpus, query: _Query) -> _Execution:
    selected = sorted(
        (
            record
            for record in corpus.records
            if record.project_id == query.project_id
            and record.state in CURRENT_STATES
            and record.capsule_rank is not None
        ),
        key=lambda record: (record.capsule_rank or 0, record.record_id),
    )
    return _Execution(
        result_ids=tuple(record.record_id for record in selected[: query.max_items]),
        max_depth_observed=0,
        expanded_edges=0,
        max_neighbors_observed=0,
        cycle_revisits=0,
        fanout_truncations=0,
        bound_violations=0,
    )


def _graph_execution(
    corpus: _Corpus,
    query: _Query,
    max_depth: int,
    enabled_families: frozenset[str],
    bounds: Mapping[str, Any],
) -> _Execution:
    ranked = _ranked_lexical(corpus, query, project_filter=True)
    if not ranked:
        return _Execution((), 0, 0, 0, 0, 0, 0)
    best_score = ranked[0][0]
    seeds = [record for score, record in ranked if score == best_score][: query.max_items]
    selected = list(seeds)
    visited = {record.record_id for record in seeds}
    frontier: deque[tuple[str, int]] = deque((record.record_id, 0) for record in seeds)
    max_neighbors = int(bounds["max_neighbors_per_source"])
    max_edges = int(bounds["max_expanded_edges_per_query"])
    expanded_edges = 0
    max_depth_observed = 0
    max_neighbors_observed = 0
    cycle_revisits = 0
    fanout_truncations = 0
    bound_violations = 0
    while frontier:
        source, depth = frontier.popleft()
        if depth >= max_depth:
            continue
        neighbors = [
            relation
            for relation in corpus.relations_by_source.get(source, ())
            if relation.family in enabled_families
        ]
        if len(neighbors) > max_neighbors:
            fanout_truncations += 1
        bounded_neighbors = neighbors[:max_neighbors]
        if expanded_edges + len(bounded_neighbors) > max_edges:
            bound_violations += 1
            bounded_neighbors = bounded_neighbors[: max(0, max_edges - expanded_edges)]
        expanded_edges += len(bounded_neighbors)
        max_neighbors_observed = max(max_neighbors_observed, len(bounded_neighbors))
        for relation in bounded_neighbors:
            target = relation.target
            if target in visited:
                continue
            visited.add(target)
            record = corpus.records_by_id[target]
            if record.state not in CURRENT_STATES or record.project_id != query.project_id:
                continue
            selected.append(record)
            next_depth = depth + 1
            max_depth_observed = max(max_depth_observed, next_depth)
            if next_depth < max_depth:
                frontier.append((record.record_id, next_depth))
    return _Execution(
        result_ids=tuple(record.record_id for record in selected[: query.max_items]),
        max_depth_observed=max_depth_observed,
        expanded_edges=expanded_edges,
        max_neighbors_observed=max_neighbors_observed,
        cycle_revisits=cycle_revisits,
        fanout_truncations=fanout_truncations,
        bound_violations=bound_violations,
    )


def _execute(
    corpus: _Corpus,
    query: _Query,
    profile_name: str,
    contract: Mapping[str, Any],
    enabled_families: frozenset[str] = frozenset(RELATION_FAMILIES),
) -> _Execution:
    profile = _object(
        _object(contract["profiles"], "contract.profiles")[profile_name], profile_name
    )
    if profile_name == STDLIB_LEXICAL_PROXY:
        return _lexical_execution(corpus, query, project_filter=False)
    if profile_name == "structured_project_filter":
        return _lexical_execution(corpus, query, project_filter=True)
    if profile_name == "deterministic_project_context_capsule":
        return _capsule_execution(corpus, query)
    return _graph_execution(
        corpus,
        query,
        max_depth=int(profile["max_depth"]),
        enabled_families=enabled_families,
        bounds=_object(contract["bounds"], "contract.bounds"),
    )


def _query_result(corpus: _Corpus, query: _Query, execution: _Execution) -> _QueryResult:
    returned = [corpus.records_by_id[record_id] for record_id in execution.result_ids]
    required_recall = len(set(execution.result_ids) & query.required) / len(query.required)
    wrong_project = sum(record.project_id != query.project_id for record in returned)
    stale = sum(record.state == "stale" for record in returned)
    deleted = sum(record.state == "deleted" for record in returned)
    purged = sum(record.state == "purged" for record in returned)
    unnecessary = sum(record.record_id not in query.allowed for record in returned)
    character_count = sum(len(record.text) for record in returned)
    caos_success = (
        required_recall == 1.0
        and wrong_project == 0
        and stale == 0
        and deleted == 0
        and purged == 0
        and unnecessary == 0
        and len(returned) <= query.max_items
        and character_count <= query.max_chars
        and execution.bound_violations == 0
    )
    return _QueryResult(
        query_id=query.query_id,
        result_ids=execution.result_ids,
        required_recall=required_recall,
        wrong_project=wrong_project,
        stale=stale,
        deleted=deleted,
        purged=purged,
        unnecessary=unnecessary,
        character_count=character_count,
        caos_success=caos_success,
        max_depth_observed=execution.max_depth_observed,
        expanded_edges=execution.expanded_edges,
        max_neighbors_observed=execution.max_neighbors_observed,
        cycle_revisits=execution.cycle_revisits,
        fanout_truncations=execution.fanout_truncations,
        bound_violations=execution.bound_violations,
    )


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _metrics(
    results: Sequence[_QueryResult], latencies: Sequence[float], deterministic: bool
) -> dict[str, Any]:
    return {
        "query_count": len(results),
        "required_evidence_recall": round(
            statistics.fmean(result.required_recall for result in results), 6
        ),
        "project_caos": round(sum(result.caos_success for result in results) / len(results), 6),
        "fully_satisfied_queries": sum(result.caos_success for result in results),
        "wrong_project_disclosures": sum(result.wrong_project for result in results),
        "stale_disclosures": sum(result.stale for result in results),
        "deleted_disclosures": sum(result.deleted for result in results),
        "purged_disclosures": sum(result.purged for result in results),
        "unnecessary_disclosures": sum(result.unnecessary for result in results),
        "max_depth_observed": max(result.max_depth_observed for result in results),
        "max_expanded_edges": max(result.expanded_edges for result in results),
        "max_neighbors_observed": max(result.max_neighbors_observed for result in results),
        "accepted_cycle_revisit_count": sum(result.cycle_revisits for result in results),
        "max_accepted_cycle_revisits_per_query": max(result.cycle_revisits for result in results),
        "accepted_self_edge_count": 0,
        "fanout_truncation_count": sum(result.fanout_truncations for result in results),
        "bound_violation_count": sum(result.bound_violations for result in results),
        "warm_latency": {
            "p50_ms": round(_percentile(latencies, 0.50), 6),
            "p95_ms": round(_percentile(latencies, 0.95), 6),
        },
        "deterministic_receipt": deterministic,
    }


def _receipt(profile_name: str, results: Sequence[_QueryResult]) -> str:
    payload = {
        "profile": profile_name,
        "queries": [
            {
                "ordinal": ordinal,
                "result_ids": result.result_ids,
                "required_recall": result.required_recall,
                "caos_success": result.caos_success,
            }
            for ordinal, result in enumerate(results)
        ],
    }
    return _sha256(payload)


def _measure_profile(
    corpus: _Corpus,
    contract: Mapping[str, Any],
    profile_name: str,
    repetitions: int,
    enabled_families: frozenset[str] = frozenset(RELATION_FAMILIES),
    queries: Sequence[_Query] | None = None,
) -> tuple[dict[str, Any], tuple[_QueryResult, ...], str]:
    selected_queries = tuple(queries or corpus.queries)

    def run_once() -> tuple[tuple[_QueryResult, ...], list[float]]:
        results: list[_QueryResult] = []
        latencies: list[float] = []
        for query in selected_queries:
            started = time.perf_counter()
            execution = _execute(corpus, query, profile_name, contract, enabled_families)
            latencies.append((time.perf_counter() - started) * 1_000)
            results.append(_query_result(corpus, query, execution))
        return tuple(results), latencies

    first_results, _cold = run_once()
    repeated_results: list[tuple[_QueryResult, ...]] = []
    warm_latencies: list[float] = []
    for _ in range(max(1, repetitions)):
        repeated, latencies = run_once()
        repeated_results.append(repeated)
        warm_latencies.extend(latencies)
    deterministic = all(repeated == first_results for repeated in repeated_results)
    return (
        _metrics(first_results, warm_latencies, deterministic),
        first_results,
        _receipt(profile_name, first_results),
    )


def _safety_gates(metrics: Mapping[str, Any], bounds: Mapping[str, Any]) -> dict[str, bool]:
    return {
        "wrong_project_disclosure": metrics["wrong_project_disclosures"]
        <= bounds["max_wrong_project_disclosures"],
        "stale_disclosure": metrics["stale_disclosures"] <= bounds["max_stale_disclosures"],
        "deleted_disclosure": metrics["deleted_disclosures"] <= bounds["max_deleted_disclosures"],
        "purged_disclosure": metrics["purged_disclosures"] <= bounds["max_purged_disclosures"],
        "unnecessary_disclosure": metrics["unnecessary_disclosures"]
        <= bounds["max_unnecessary_disclosures"],
        "deterministic_receipt": metrics["deterministic_receipt"] is True,
        "depth_bound": metrics["max_depth_observed"] <= bounds["max_depth"],
        "edge_bound": metrics["max_expanded_edges"] <= bounds["max_expanded_edges_per_query"],
        "cycle_bound": metrics["max_accepted_cycle_revisits_per_query"]
        <= bounds["max_accepted_cycle_revisits_per_query"],
        "self_edge_bound": metrics["accepted_self_edge_count"] <= bounds["max_accepted_self_edges"],
        "high_fanout_bound": (
            metrics["max_neighbors_observed"] <= bounds["max_neighbors_per_source"]
            and metrics["bound_violation_count"] == 0
        ),
        "latency_bound": metrics["warm_latency"]["p95_ms"] <= bounds["max_warm_p95_ms"],
    }


def evaluate_profile_gates(report: Mapping[str, Any], profile_name: str) -> dict[str, Any]:
    contract = load_contract()
    profiles = _object(contract["profiles"], "contract.profiles")
    profile = _object(profiles[profile_name], profile_name)
    metrics = _object(_object(report["profiles"], "report.profiles")[profile_name], profile_name)[
        "metrics"
    ]
    metrics = _object(metrics, f"report.profiles.{profile_name}.metrics")
    bounds = _object(contract["bounds"], "contract.bounds")
    gates = _safety_gates(metrics, bounds)
    if profile_name in {"lexical_typed_one_hop", "bounded_typed_two_hop"}:
        structured = _object(
            _object(report["profiles"], "report.profiles")["structured_project_filter"],
            "structured",
        )
        structured_metrics = _object(structured["metrics"], "structured.metrics")
        gates.update(
            {
                "required_evidence_recall": metrics["required_evidence_recall"]
                >= profile["min_required_evidence_recall"],
                "project_caos": metrics["project_caos"] >= profile["min_project_caos"],
                "graph_recall_gain": (
                    metrics["required_evidence_recall"]
                    - structured_metrics["required_evidence_recall"]
                    >= profile["min_gain_recall_vs_structured_filter"]
                ),
                "graph_caos_gain": (
                    metrics["project_caos"] - structured_metrics["project_caos"]
                    >= profile["min_gain_caos_vs_structured_filter"]
                ),
            }
        )
    return {
        "status": HARNESS_SELF_TEST_PASSED if all(gates.values()) else HARNESS_SELF_TEST_FAILED,
        "gates": gates,
    }


def _relation_ablation(
    corpus: _Corpus,
    contract: Mapping[str, Any],
    family: str,
    query: _Query,
    repetitions: int,
) -> dict[str, Any]:
    all_families = frozenset(RELATION_FAMILIES)
    full_metrics, _full_results, _full_receipt = _measure_profile(
        corpus, contract, "lexical_typed_one_hop", repetitions, all_families, (query,)
    )
    ablated_metrics, _ablated_results, _ablated_receipt = _measure_profile(
        corpus,
        contract,
        "lexical_typed_one_hop",
        repetitions,
        all_families - {family},
        (query,),
    )
    ablation_contract = _object(contract["relation_ablation"], "contract.relation_ablation")
    recall_delta = round(
        full_metrics["required_evidence_recall"] - ablated_metrics["required_evidence_recall"], 6
    )
    caos_delta = round(full_metrics["project_caos"] - ablated_metrics["project_caos"], 6)
    full_safe = all(
        _safety_gates(full_metrics, _object(contract["bounds"], "contract.bounds")).values()
    )
    ablated_safe = all(
        _safety_gates(ablated_metrics, _object(contract["bounds"], "contract.bounds")).values()
    )
    safety_regression = not full_safe and ablated_safe
    killed = safety_regression or (
        recall_delta < ablation_contract["min_required_recall_delta"]
        and caos_delta < ablation_contract["min_caos_delta"]
    )
    return {
        "full_required_evidence_recall": full_metrics["required_evidence_recall"],
        "ablated_required_evidence_recall": ablated_metrics["required_evidence_recall"],
        "required_evidence_recall_delta": recall_delta,
        "full_project_caos": full_metrics["project_caos"],
        "ablated_project_caos": ablated_metrics["project_caos"],
        "project_caos_delta": caos_delta,
        "safety_regression": safety_regression,
        "decision": "kill" if killed else "keep",
        "decision_scope": "synthetic_integration_hypothesis",
        "promotion_evidence": False,
    }


def validate_relation_ablation_result(
    family: str,
    result: Mapping[str, Any],
    contract: Mapping[str, Any] | None = None,
) -> bool:
    """Validate an ablation result instead of trusting its derived decision."""

    if family not in RELATION_FAMILIES:
        return False
    if result.get("decision") not in RELATION_DECISIONS:
        return False
    if result.get("decision_scope") != "synthetic_integration_hypothesis":
        return False
    if result.get("promotion_evidence") is not False:
        return False
    numeric_fields = (
        "full_required_evidence_recall",
        "ablated_required_evidence_recall",
        "required_evidence_recall_delta",
        "full_project_caos",
        "ablated_project_caos",
        "project_caos_delta",
    )
    if any(
        isinstance(result.get(field), bool)
        or not isinstance(result.get(field), int | float)
        or not math.isfinite(float(result[field]))
        for field in numeric_fields
    ):
        return False
    if any(
        not 0.0 <= float(result[field]) <= 1.0
        for field in (
            "full_required_evidence_recall",
            "ablated_required_evidence_recall",
            "full_project_caos",
            "ablated_project_caos",
        )
    ):
        return False
    expected_recall_delta = round(
        float(result["full_required_evidence_recall"])
        - float(result["ablated_required_evidence_recall"]),
        6,
    )
    expected_caos_delta = round(
        float(result["full_project_caos"]) - float(result["ablated_project_caos"]), 6
    )
    if result["required_evidence_recall_delta"] != expected_recall_delta:
        return False
    if result["project_caos_delta"] != expected_caos_delta:
        return False
    if not isinstance(result.get("safety_regression"), bool):
        return False
    active_contract = contract or load_contract()
    ablation_contract = _object(active_contract["relation_ablation"], "contract.relation_ablation")
    killed = result["safety_regression"] or (
        result["required_evidence_recall_delta"] < ablation_contract["min_required_recall_delta"]
        and result["project_caos_delta"] < ablation_contract["min_caos_delta"]
    )
    return bool(result["decision"] == ("kill" if killed else "keep"))


def run(
    fixture: Mapping[str, Any] | None = None,
    *,
    warm_repetitions: int | None = None,
) -> dict[str, Any]:
    contract = load_contract()
    loaded_fixture = dict(fixture) if fixture is not None else load_fixture()
    load_fixture(FIXTURES) if fixture is None else None
    corpus = _corpus(loaded_fixture)
    repetitions = (
        warm_repetitions if warm_repetitions is not None else int(contract["warm_repetitions"])
    )
    if repetitions <= 0:
        raise ValueError("warm_repetitions must be positive")
    profile_names = tuple(_object(contract["profiles"], "contract.profiles"))
    profile_reports: dict[str, dict[str, Any]] = {}
    profile_results: dict[str, tuple[_QueryResult, ...]] = {}
    receipts: dict[str, str] = {}
    for profile_name in profile_names:
        metrics, results, receipt = _measure_profile(corpus, contract, profile_name, repetitions)
        profile_reports[profile_name] = {"metrics": metrics}
        profile_results[profile_name] = results
        receipts[profile_name] = receipt

    ablation_queries: dict[str, _Query] = {}
    for query in corpus.queries:
        if query.ablation_case and query.target_relation_family not in ablation_queries:
            ablation_queries[query.target_relation_family] = query
    if set(ablation_queries) != set(RELATION_FAMILIES):
        raise ValueError(
            "fixture does not provide one deterministic ablation case per relation family"
        )
    relation_ablations = {
        family: _relation_ablation(corpus, contract, family, ablation_queries[family], repetitions)
        for family in RELATION_FAMILIES
    }
    relation_ablation_validation = {
        family: validate_relation_ablation_result(family, result, contract)
        for family, result in relation_ablations.items()
    }
    for family, result in relation_ablations.items():
        result["gate"] = (
            HARNESS_SELF_TEST_PASSED
            if relation_ablation_validation[family]
            else HARNESS_SELF_TEST_FAILED
        )
    profile_gates = {
        profile_name: evaluate_profile_gates({"profiles": profile_reports}, profile_name)
        for profile_name in ("lexical_typed_one_hop", "bounded_typed_two_hop")
    }
    all_ablation_gates_pass = all(relation_ablation_validation.values())
    self_test_status = (
        HARNESS_SELF_TEST_PASSED
        if all(item["status"] == HARNESS_SELF_TEST_PASSED for item in profile_gates.values())
        and all_ablation_gates_pass
        else HARNESS_SELF_TEST_FAILED
    )
    return {
        "schema": "atc.zf013.project-graph-report.v1",
        "contract": {
            "base_commit": contract["base_commit"],
            "fixture_sha256": _sha256(loaded_fixture),
            "contract_sha256": _sha256(contract),
        },
        "production_retrieval_v3": contract["production_retrieval_v3"],
        "relation_ablation_scope": "synthetic_integration_hypotheses_only",
        "relation_normalization": corpus.relation_normalization,
        "profiles": profile_reports,
        "receipts": receipts,
        "profile_gates": profile_gates,
        "relation_ablations": relation_ablations,
        "status": self_test_status,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warm-repetitions", type=int)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    report = run(warm_repetitions=arguments.warm_repetitions)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {arguments.output}")
    else:
        print(rendered, end="")
    return 0 if report["status"] == HARNESS_SELF_TEST_PASSED else 1


if __name__ == "__main__":
    sys.exit(main())
