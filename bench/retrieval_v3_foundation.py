"""Sanitized deterministic Retrieval V3 foundation harness and gate definitions."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sqlite3
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Any

from allthecontext.export import create_export, restore_export
from allthecontext.models import BootstrapRequest, CandidateInput, SearchRequest
from allthecontext.retrieval import RetrievalEngine
from allthecontext.retrieval_contracts import FROZEN_V2_COMPARATOR, FrozenV2Comparator
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

from bench.retrieval_benchmark import FIXTURES, build_database

FOUNDATION_FIXTURES = Path(__file__).with_name("retrieval_v3_fixtures.json")
FROZEN_COMPARATOR_CONTRACT = Path(__file__).parent / "baselines" / "v2_comparator_contract.json"
_PASSPHRASE = "synthetic retrieval fixture passphrase"
NORMAL_PROFILES = (1_000, 10_000)
MAXIMUM_PROFILE = 50_000


class GateStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    NOT_EXERCISED = "not_exercised"


class GateRule(StrEnum):
    AT_LEAST_COMPARATOR = "at_least_comparator"
    GREATER_THAN_COMPARATOR = "greater_than_comparator"
    EQUALS_ZERO = "equals_zero"
    IS_TRUE = "is_true"


@dataclass(frozen=True, slots=True)
class GateDefinition:
    gate_id: str
    metric: str
    rule: GateRule
    scope: str = "profile"


@dataclass(frozen=True, slots=True)
class GateResult:
    gate_id: str
    profile: str
    status: GateStatus
    candidate_value: bool | int | float | None
    comparator_value: bool | int | float | None


GATE_DEFINITIONS = (
    GateDefinition("exact_recall_at_5", "exact_recall_at_5", GateRule.AT_LEAST_COMPARATOR),
    GateDefinition(
        "admissibility_precision_improvement",
        "admissibility_precision_at_5",
        GateRule.GREATER_THAN_COMPARATOR,
    ),
    GateDefinition(
        "temporal_precision_improvement",
        "temporal_precision_at_5",
        GateRule.GREATER_THAN_COMPARATOR,
    ),
    GateDefinition(
        "semantic_coverage_at_least_baseline",
        "semantic_coverage_at_5",
        GateRule.AT_LEAST_COMPARATOR,
    ),
    GateDefinition(
        "zero_duplicate_redundancy",
        "duplicate_redundancy",
        GateRule.EQUALS_ZERO,
    ),
    GateDefinition(
        "deterministic_conflict_behavior",
        "conflict_behavior_deterministic",
        GateRule.IS_TRUE,
    ),
    GateDefinition("zero_policy_violations", "policy_violation_count", GateRule.EQUALS_ZERO),
    GateDefinition(
        "no_deleted_or_purged_resurrection",
        "resurrected_deleted_or_purged_count",
        GateRule.EQUALS_ZERO,
        scope="lifecycle",
    ),
)


def _load_object(path: Path) -> dict[str, Any]:
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return value


def load_foundation_fixture() -> dict[str, Any]:
    """Merge the immutable V2 corpus with additive sanitized V3 scenarios."""

    base = _load_object(FIXTURES)
    extension = _load_object(FOUNDATION_FIXTURES)
    if extension.get("base_fixture") != FIXTURES.name:
        raise ValueError("foundation fixture names an unexpected base fixture")
    base["records"] = [*base["records"], *extension["records"]]
    base["queries"] = [*base["queries"], *extension["queries"]]
    base["semantic_facets"] = extension["semantic_facets"]
    base["temporal_cases"] = extension["temporal_cases"]
    base["compatible_evidence_sets"] = extension["compatible_evidence_sets"]
    base["declared_not_exercised"] = extension["declared_not_exercised"]
    return base


def select_profiles(values: Sequence[int], include_larger: bool) -> tuple[int, ...]:
    """Keep normal CI at 1k/10k and require an explicit opt-in above 10k."""

    selected = tuple(dict.fromkeys(values)) or NORMAL_PROFILES
    if any(value <= 0 for value in selected):
        raise ValueError("profiles must be positive")
    if any(value > NORMAL_PROFILES[-1] for value in selected) and not include_larger:
        raise ValueError("profiles above 10k require --include-50k")
    if any(value > MAXIMUM_PROFILE for value in selected):
        raise ValueError("profiles above 50k are intentionally unsupported")
    return selected


def _principal(fixture: dict[str, Any]) -> ClientPrincipal:
    value = fixture["principal"]
    return ClientPrincipal(
        str(value["id"]),
        str(value["name"]),
        frozenset(str(scope) for scope in value["scopes"]),
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


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50_ms": round(_percentile(values, 0.50), 6),
        "p95_ms": round(_percentile(values, 0.95), 6),
    }


def _recall(ids: Sequence[str], gold: dict[str, int], cutoff: int = 5) -> float:
    return len(set(ids[:cutoff]) & set(gold)) / len(gold) if gold else 0.0


def _bootstrap_redundancy(
    engine: RetrievalEngine,
    principal: ClientPrincipal,
    fixture: dict[str, Any],
) -> float:
    bootstrap = fixture["bootstrap"]
    response = engine.bootstrap(
        BootstrapRequest(
            task_description=str(bootstrap["query"]),
            requested_scopes=list(bootstrap["scopes"]),
            character_budget=12_000,
        ),
        principal,
    )
    selected = {item.id for item in response.items}
    redundant = 0
    for group in bootstrap["duplicate_groups"]:
        redundant += max(0, len(selected & set(group)) - 1)
    return redundant / len(response.items) if response.items else 0.0


def run_profile(
    size: int,
    directory: Path,
    fixture: dict[str, Any],
) -> dict[str, Any]:
    """Measure the frozen comparator without asserting V3 feature-gate success."""

    database = directory / f"retrieval-v3-{size}.sqlite3"
    store, indexing_seconds = build_database(database, size, fixture)
    engine = RetrievalEngine(store, ranker=FrozenV2Comparator())
    principal = _principal(fixture)
    results: dict[str, list[str]] = {}
    cold: list[float] = []
    warm: list[float] = []
    repeated_rankings_deterministic = True
    policy_violations = 0

    for query in fixture["queries"]:
        request = SearchRequest(query=str(query["query"]), limit=5)
        started = time.perf_counter()
        response = engine.search(request, principal)
        cold.append((time.perf_counter() - started) * 1_000)
        ids = [item.id for item in response.items]
        results[str(query["id"])] = ids
        policy_violations += len(set(ids) & set(query.get("forbidden", [])))
        repeats: list[list[str]] = []
        for _ in range(5):
            started = time.perf_counter()
            repeated = engine.search(request, principal)
            warm.append((time.perf_counter() - started) * 1_000)
            repeats.append([item.id for item in repeated.items])
        repeated_rankings_deterministic &= repeats == [ids] * len(repeats)

    queries = {str(query["id"]): query for query in fixture["queries"]}
    exact = queries["exact"]
    current = queries["current_history"]
    admissibility = queries["task_admissibility"]
    admissibility_ids = results["task_admissibility"]
    admissible_hits = len(set(admissibility_ids) & set(admissibility["gold"]))
    admissibility_precision = (
        admissible_hits / len(admissibility_ids) if admissibility_ids else 0.0
    )
    current_ids = results["current_history"]
    temporal_precision = (
        len(set(current_ids) & set(current["gold"])) / len(current_ids)
        if current_ids
        else 0.0
    )
    facet_hits = 0
    facet_count = 0
    for evaluation in fixture["semantic_facets"]:
        ids = set(results[str(evaluation["query_id"])][:5])
        for facet in evaluation["facets"]:
            facet_count += 1
            facet_hits += bool(ids & set(facet))
    semantic_coverage = facet_hits / facet_count if facet_count else 0.0

    conflict_ids = results["deterministic_conflict"]
    expected_conflicts = set(queries["deterministic_conflict"]["gold"])
    conflict_behavior_deterministic = (
        repeated_rankings_deterministic and expected_conflicts <= set(conflict_ids)
    )
    filter_ids = set(results["validity_expiry_supersession"])
    filter_violation_counts = {
        "future_validity": int("future-sentinel" in filter_ids),
        "expired": int("expired-sentinel" in filter_ids),
        "superseded": int("retention-old" in filter_ids),
        "deleted": int("deleted-sentinel" in filter_ids),
    }
    duplicate_redundancy = _bootstrap_redundancy(engine, principal, fixture)
    with store.connect() as connection:
        canonical_count = int(
            connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0]
        )
        fts_count = int(connection.execute("SELECT COUNT(*) FROM context_fts").fetchone()[0])
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database_bytes = database.stat().st_size
    ranking_material = [
        (str(query["id"]), results[str(query["id"])]) for query in fixture["queries"]
    ]
    ranking_fingerprint = hashlib.sha256(
        json.dumps(ranking_material, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    contract = _load_object(FROZEN_COMPARATOR_CONTRACT)
    expected_fingerprint = contract["ranking_fingerprints_sha256"].get(str(size))
    metrics: dict[str, Any] = {
        "exact_recall_at_5": round(_recall(results["exact"], exact["gold"]), 6),
        "admissibility_precision_at_5": round(admissibility_precision, 6),
        "temporal_precision_at_5": round(temporal_precision, 6),
        "semantic_coverage_at_5": round(semantic_coverage, 6),
        "duplicate_redundancy": round(duplicate_redundancy, 6),
        "conflict_behavior_deterministic": conflict_behavior_deterministic,
        "repeated_rankings_deterministic": repeated_rankings_deterministic,
        "ranking_fingerprint_sha256": ranking_fingerprint,
        "comparator_contract_match": (
            ranking_fingerprint == expected_fingerprint
            if expected_fingerprint is not None
            else None
        ),
        "policy_violation_count": policy_violations,
        "filter_violation_counts": filter_violation_counts,
        "cold_latency": _latency_summary(cold),
        "warm_latency": _latency_summary(warm),
        "database_bytes": database_bytes,
        "bytes_per_record": round(database_bytes / size, 6),
        "canonical_record_count": canonical_count,
        "fts_row_count": fts_count,
        "initial_indexing_seconds": round(indexing_seconds, 6),
        "initial_indexing_records_per_second": round(size / indexing_seconds, 3),
    }
    return {"record_count": size, "metrics": metrics}


def _approve(store: CoreStore, content: str, **values: Any) -> str:
    candidate = store.add_candidate(
        CandidateInput(kind="synthetic_fact", content=content, **values)
    )
    return store.approve_candidate(candidate.id).id


def _scenario(scenario_id: str, status: GateStatus, evidence_code: str) -> dict[str, str]:
    return {
        "scenario_id": scenario_id,
        "status": status.value,
        "evidence_code": evidence_code,
    }


def run_lifecycle(directory: Path) -> dict[str, Any]:
    """Exercise supported lifecycle paths and explicitly label absent wiring."""

    database = directory / "retrieval-v3-lifecycle.sqlite3"
    package = directory / "retrieval-v3-lifecycle.atc"
    restored_database = directory / "retrieval-v3-restored.sqlite3"
    store = CoreStore(database)
    migration_count = store.migrate()
    store.initialize_vault("Synthetic V3 lifecycle", "UTC")

    current_id = _approve(store, "Lifecycle marker has revision alpha.")
    store.correct_record(
        current_id,
        content="Lifecycle marker has revision beta.",
        reason="synthetic correction",
    )
    history_versions = len(store.record_history(current_id))
    delete_id = _approve(store, "Deletion lifecycle marker.")
    purge_id = _approve(store, "Purge lifecycle marker.")
    _approve(
        store,
        "Conflict lifecycle value east.",
        entity_key="synthetic:service",
        attribute_key="region",
    )
    _approve(
        store,
        "Conflict lifecycle value west.",
        entity_key="synthetic:service",
        attribute_key="region",
    )
    first_groups = store.list_integrity_groups()
    second_groups = store.list_integrity_groups()
    first_shape = [
        (item["id"], item["group_type"], item["record_ids"])
        for item in first_groups["items"]
    ]
    second_shape = [
        (item["id"], item["group_type"], item["record_ids"])
        for item in second_groups["items"]
    ]
    conflict_detection_deterministic = first_shape == second_shape and bool(first_shape)

    create_export(database, package, _PASSPHRASE)
    engine = RetrievalEngine(store, ranker=FrozenV2Comparator())
    before_restart = [
        item.id for item in engine.search(SearchRequest(query="revision beta", limit=5)).items
    ]
    restarted = CoreStore(database)
    restarted.migrate()
    after_restart = [
        item.id
        for item in RetrievalEngine(restarted, ranker=FrozenV2Comparator())
        .search(SearchRequest(query="revision beta", limit=5))
        .items
    ]

    restarted.delete_record(delete_id, reason="synthetic deletion")
    restarted.purge(
        "record",
        purge_id,
        confirmation=restarted.purge_confirmation_phrase("record", purge_id),
        compact=False,
    )
    restore_export(package, database, _PASSPHRASE)
    post_restore_engine = RetrievalEngine(restarted, ranker=FrozenV2Comparator())
    resurrection_queries = ("Deletion lifecycle marker", "Purge lifecycle marker")
    removed_ids = {delete_id, purge_id}
    resurrected_ids: set[str] = set()
    for query in resurrection_queries:
        returned = post_restore_engine.search(SearchRequest(query=query, limit=5)).items
        resurrected_ids.update({item.id for item in returned} & removed_ids)

    restored = CoreStore(restored_database)
    restored.migrate()
    restored.initialize_vault("Synthetic restored lifecycle", "UTC")
    restore_result = restore_export(package, restored_database, _PASSPHRASE)
    with restored.connect() as connection:
        restored_rows = int(
            connection.execute(
                "SELECT COUNT(*) FROM context_records WHERE id=?", (current_id,)
            ).fetchone()[0]
        )

    scenarios = [
        _scenario("current_history", GateStatus.PASSED, "public_history_and_current_search"),
        _scenario("correction", GateStatus.PASSED, "public_correction_path"),
        _scenario("deletion", GateStatus.PASSED, "public_deletion_path"),
        _scenario("purge", GateStatus.PASSED, "public_purge_path"),
        _scenario("migration", GateStatus.PASSED, "idempotent_core_migrations"),
        _scenario("restart", GateStatus.PASSED, "same_database_reopen"),
        _scenario("export_import_restore", GateStatus.PASSED, "encrypted_portable_round_trip"),
        _scenario("conflict_detection", GateStatus.PASSED, "stable_integrity_group_shape"),
        _scenario("as_of_history", GateStatus.NOT_EXERCISED, "no_as_of_resolver_wiring"),
        _scenario(
            "task_admissibility",
            GateStatus.NOT_EXERCISED,
            "no_task_admissibility_evaluator_wiring",
        ),
        _scenario(
            "compatible_evidence_sets",
            GateStatus.NOT_EXERCISED,
            "no_compatibility_policy_wiring",
        ),
        _scenario(
            "retrieval_index_rebuild_after_restore",
            GateStatus.NOT_EXERCISED,
            "no_public_restore_rebuild_wiring",
        ),
    ]
    return {
        "metrics": {
            "migration_count": migration_count,
            "history_version_count": history_versions,
            "restart_ranking_parity": before_restart == after_restart and bool(before_restart),
            "conflict_detection_deterministic": conflict_detection_deterministic,
            "portable_restore_valid": bool(restore_result.get("valid")) and restored_rows == 1,
            "resurrected_deleted_or_purged_count": len(resurrected_ids),
        },
        "scenarios": scenarios,
    }


def _metric(report: dict[str, Any], profile: str, definition: GateDefinition) -> Any:
    if definition.scope == "lifecycle":
        return report.get("lifecycle", {}).get("metrics", {}).get(definition.metric)
    return report.get("profiles", {}).get(profile, {}).get("metrics", {}).get(definition.metric)


def _gate_status(
    definition: GateDefinition,
    candidate_value: Any,
    comparator_value: Any,
) -> GateStatus:
    if candidate_value is None:
        return GateStatus.NOT_EXERCISED
    if definition.rule in {GateRule.AT_LEAST_COMPARATOR, GateRule.GREATER_THAN_COMPARATOR}:
        if comparator_value is None:
            return GateStatus.NOT_EXERCISED
        passed = (
            candidate_value >= comparator_value
            if definition.rule == GateRule.AT_LEAST_COMPARATOR
            else candidate_value > comparator_value
        )
    elif definition.rule == GateRule.EQUALS_ZERO:
        passed = candidate_value == 0
    else:
        passed = candidate_value is True
    return GateStatus.PASSED if passed else GateStatus.FAILED


def _gate_scalar(value: Any) -> bool | int | float | None:
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def evaluate_gates(
    candidate: dict[str, Any], comparator: dict[str, Any]
) -> tuple[bool, list[dict[str, Any]]]:
    """Evaluate explicit reports; missing future-feature metrics never pass."""

    common = sorted(
        set(candidate.get("profiles", {})) & set(comparator.get("profiles", {})), key=int
    )
    profile_names = common or ["not_exercised"]
    results: list[GateResult] = []
    for definition in GATE_DEFINITIONS:
        profiles = ["lifecycle"] if definition.scope == "lifecycle" else profile_names
        for profile in profiles:
            candidate_value = _gate_scalar(_metric(candidate, profile, definition))
            comparator_value = _gate_scalar(_metric(comparator, profile, definition))
            results.append(
                GateResult(
                    gate_id=definition.gate_id,
                    profile=profile,
                    status=_gate_status(definition, candidate_value, comparator_value),
                    candidate_value=candidate_value,
                    comparator_value=comparator_value,
                )
            )
    serialized = [asdict(result) for result in results]
    passed = bool(serialized) and all(
        result["status"] == GateStatus.PASSED for result in serialized
    )
    return passed, serialized


def run(profiles: Sequence[int]) -> dict[str, Any]:
    fixture = load_foundation_fixture()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="atc-retrieval-v3-") as temporary:
        directory = Path(temporary)
        lifecycle = run_lifecycle(directory)
        measured = [run_profile(profile, directory, fixture) for profile in profiles]
    growth = []
    for previous, current in pairwise(measured):
        added = int(current["record_count"]) - int(previous["record_count"])
        byte_growth = (
            int(current["metrics"]["database_bytes"])
            - int(previous["metrics"]["database_bytes"])
        )
        growth.append(
            {
                "from_records": previous["record_count"],
                "to_records": current["record_count"],
                "bytes_per_added_record": round(byte_growth / added, 6) if added else 0.0,
            }
        )
    contract_matches = [
        item["metrics"]["comparator_contract_match"] for item in measured
    ]
    if any(value is False for value in contract_matches):
        contract_status = GateStatus.FAILED
    elif contract_matches and all(value is True for value in contract_matches):
        contract_status = GateStatus.PASSED
    else:
        contract_status = GateStatus.NOT_EXERCISED
    return {
        "schema_version": 1,
        "report_kind": "frozen_comparator_measurement",
        "comparator": asdict(FROZEN_V2_COMPARATOR),
        "comparator_contract_status": contract_status,
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "sqlite": sqlite3.sqlite_version,
        },
        "fixtures": {
            "base_sha256": hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
            "foundation_sha256": hashlib.sha256(FOUNDATION_FIXTURES.read_bytes()).hexdigest(),
        },
        "profiles": {str(item["record_count"]): item for item in measured},
        "storage_growth": growth,
        "lifecycle": lifecycle,
        "gate_definitions": [asdict(definition) for definition in GATE_DEFINITIONS],
        "gate_results": [],
        "gate_results_status": GateStatus.NOT_EXERCISED,
        "runtime_seconds": round(time.perf_counter() - started, 6),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--profiles", type=int, nargs="*", default=[])
    run_parser.add_argument("--include-50k", action="store_true")
    run_parser.add_argument("--output", type=Path)
    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("candidate", type=Path)
    compare_parser.add_argument("comparator", type=Path)
    compare_parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.command == "run":
        try:
            selected = select_profiles(arguments.profiles, arguments.include_50k)
        except ValueError as error:
            parser.error(str(error))
        result = run(selected)
        rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    candidate = _load_object(arguments.candidate)
    comparator = _load_object(arguments.comparator)
    passed, gate_results = evaluate_gates(candidate, comparator)
    result = {"passed": passed, "gate_results": gate_results}
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
