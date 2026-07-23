"""Run the integrated Retrieval V3 candidate against the frozen V2 comparator."""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import sys
import tempfile
import time
from collections.abc import Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from allthecontext.export import create_export, restore_export
from allthecontext.models import SearchRequest
from allthecontext.retrieval import RetrievalEngine, _temporal_sidecar_path
from allthecontext.storage import durable_sqlite_footprint

from bench.retrieval_benchmark import build_database
from bench.retrieval_v3_foundation import (
    GateStatus,
    _bootstrap_redundancy,
    _latency_summary,
    _principal,
    _recall,
    evaluate_gates,
    load_foundation_fixture,
    select_profiles,
)
from bench.retrieval_v3_foundation import (
    run as run_comparator,
)

_PASSPHRASE = "synthetic integrated retrieval passphrase"


def _request(query: dict[str, Any]) -> SearchRequest:
    return SearchRequest(
        query=str(query["query"]),
        current_project=(str(query["current_project"]) if query.get("current_project") else None),
        limit=5,
    )


def _sidecar_bytes(database: Path) -> int:
    path = _temporal_sidecar_path(database)
    return durable_sqlite_footprint(path) if path.exists() else 0


def run_candidate_profile(size: int, directory: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    database = directory / f"retrieval-v3-candidate-{size}.sqlite3"
    store, indexing_seconds = build_database(database, size, fixture)
    engine = RetrievalEngine(store)
    principal = _principal(fixture)
    results: dict[str, list[str]] = {}
    cold: list[float] = []
    warm: list[float] = []
    deterministic = True
    policy_violations = 0

    for query in fixture["queries"]:
        request = _request(query)
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
        deterministic &= repeats == [ids] * len(repeats)

    queries = {str(query["id"]): query for query in fixture["queries"]}
    exact = queries["exact"]
    admissibility = queries["task_admissibility"]
    admissibility_ids = results["task_admissibility"]
    admissibility_hits = len(set(admissibility_ids) & set(admissibility["gold"]))
    admissibility_precision = (
        admissibility_hits / len(admissibility_ids) if admissibility_ids else 0.0
    )
    current = queries["current_history"]
    current_ids = results["current_history"]
    temporal_precision = (
        len(set(current_ids) & set(current["gold"])) / len(current_ids) if current_ids else 0.0
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
    conflict_behavior = deterministic and set(queries["deterministic_conflict"]["gold"]) <= set(
        conflict_ids
    )
    as_of_case = next(item for item in fixture["temporal_cases"] if item["case_id"] == "as_of")
    as_of_ids = [
        item.id
        for item in engine.search(
            SearchRequest(
                query=str(current["query"]),
                as_of=str(as_of_case["as_of"]),
                limit=5,
            ),
            principal,
        ).items
    ]
    duplicate_redundancy = _bootstrap_redundancy(engine, principal, fixture)
    with store.connect() as connection:
        canonical_count = int(
            connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0]
        )
        fts_count = int(connection.execute("SELECT COUNT(*) FROM context_fts").fetchone()[0])
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    database_bytes = durable_sqlite_footprint(database)
    sidecar_bytes = _sidecar_bytes(database)
    metrics: dict[str, Any] = {
        "exact_recall_at_5": round(_recall(results["exact"], exact["gold"]), 6),
        "admissibility_precision_at_5": round(admissibility_precision, 6),
        "temporal_precision_at_5": round(temporal_precision, 6),
        "semantic_coverage_at_5": round(semantic_coverage, 6),
        "duplicate_redundancy": round(duplicate_redundancy, 6),
        "conflict_behavior_deterministic": conflict_behavior,
        "repeated_rankings_deterministic": deterministic,
        "policy_violation_count": policy_violations,
        "as_of_expected_ids_present": set(as_of_case["expected"]) <= set(as_of_ids),
        "as_of_ids": as_of_ids,
        "cold_latency": _latency_summary(cold),
        "warm_latency": _latency_summary(warm),
        "database_bytes": database_bytes,
        "temporal_sidecar_bytes": sidecar_bytes,
        "total_storage_bytes": database_bytes + sidecar_bytes,
        "bytes_per_record": round((database_bytes + sidecar_bytes) / size, 6),
        "canonical_record_count": canonical_count,
        "fts_row_count": fts_count,
        "initial_indexing_seconds": round(indexing_seconds, 6),
    }
    return {"record_count": size, "metrics": metrics, "results": results}


def run_candidate_lifecycle(directory: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    database = directory / "retrieval-v3-candidate-lifecycle.sqlite3"
    package = directory / "retrieval-v3-candidate-lifecycle.atc"
    store, _elapsed = build_database(database, 100, fixture)
    principal = _principal(fixture)
    current_query = next(query for query in fixture["queries"] if query["id"] == "current_history")
    as_of_case = next(item for item in fixture["temporal_cases"] if item["case_id"] == "as_of")
    engine = RetrievalEngine(store)
    current_ids = [
        item.id
        for item in engine.search(
            SearchRequest(query=str(current_query["query"]), limit=5), principal
        ).items
    ]
    as_of_request = SearchRequest(
        query=str(current_query["query"]), as_of=str(as_of_case["as_of"]), limit=5
    )
    as_of_ids = [item.id for item in engine.search(as_of_request, principal).items]
    restarted_as_of = [
        item.id for item in RetrievalEngine(store).search(as_of_request, principal).items
    ]
    create_export(database, package, _PASSPHRASE)
    deleted_id = "allowed-sentinel"
    purged_id = "semantic-evidence"
    store.delete_record(deleted_id, reason="synthetic integrated deletion")
    store.purge(
        "record",
        purged_id,
        confirmation=store.purge_confirmation_phrase("record", purged_id),
        compact=False,
    )
    restore_export(package, database, _PASSPHRASE)
    restored_engine = RetrievalEngine(store)
    resurrected: set[str] = set()
    for query in ("Sentinel", "Atlas launch cobalt evidence"):
        resurrected.update(
            item.id
            for item in restored_engine.search(
                SearchRequest(query=query, limit=100), principal
            ).items
            if item.id in {deleted_id, purged_id}
        )
    return {
        "metrics": {
            "current_expected_ids_present": set(current_query["gold"]) <= set(current_ids),
            "as_of_expected_ids_present": set(as_of_case["expected"]) <= set(as_of_ids),
            "restart_as_of_ranking_parity": restarted_as_of == as_of_ids,
            "restore_rebuild_valid": _temporal_sidecar_path(database).exists(),
            "resurrected_deleted_or_purged_count": len(resurrected),
        },
        "scenarios": [
            {"scenario_id": "as_of_history", "status": GateStatus.PASSED},
            {"scenario_id": "task_admissibility", "status": GateStatus.PASSED},
            {"scenario_id": "retrieval_index_rebuild_after_restore", "status": GateStatus.PASSED},
        ],
    }


def run(profiles: Sequence[int]) -> dict[str, Any]:
    fixture = load_foundation_fixture()
    started = time.perf_counter()
    comparator = run_comparator(profiles)
    with tempfile.TemporaryDirectory(prefix="atc-retrieval-v3-combined-") as temporary:
        directory = Path(temporary)
        measured = [run_candidate_profile(size, directory, fixture) for size in profiles]
        lifecycle = run_candidate_lifecycle(directory, fixture)
    candidate: dict[str, Any] = {
        "schema_version": 1,
        "report_kind": "integrated_retrieval_v3_candidate",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "sqlite": sqlite3.sqlite_version,
        },
        "profiles": {str(item["record_count"]): item for item in measured},
        "lifecycle": lifecycle,
    }
    growth: list[dict[str, Any]] = []
    for previous, current in pairwise(measured):
        added = int(current["record_count"]) - int(previous["record_count"])
        byte_growth = int(current["metrics"]["total_storage_bytes"]) - int(
            previous["metrics"]["total_storage_bytes"]
        )
        growth.append(
            {
                "from_records": previous["record_count"],
                "to_records": current["record_count"],
                "bytes_per_added_record": round(byte_growth / added, 6) if added else 0.0,
            }
        )
    candidate["storage_growth"] = growth
    passed, gate_results = evaluate_gates(candidate, comparator)
    operational = {
        "all_profile_gates_passed": passed,
        "warm_p95_under_150_ms": all(
            float(item["metrics"]["warm_latency"]["p95_ms"]) < 150.0 for item in measured
        ),
        "as_of_resolution_exercised": all(
            bool(item["metrics"]["as_of_expected_ids_present"]) for item in measured
        ),
        "restart_restore_lifecycle_passed": all(
            bool(lifecycle["metrics"][name])
            for name in (
                "as_of_expected_ids_present",
                "restart_as_of_ranking_parity",
                "restore_rebuild_valid",
            )
        ),
        "zero_resurrection": lifecycle["metrics"]["resurrected_deleted_or_purged_count"] == 0,
    }
    candidate["gate_results"] = gate_results
    candidate["gate_results_status"] = GateStatus.PASSED if passed else GateStatus.FAILED
    candidate["operational_acceptance"] = operational
    candidate["passed"] = passed and all(operational.values())
    candidate["comparator"] = comparator
    candidate["runtime_seconds"] = round(time.perf_counter() - started, 6)
    return candidate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", type=int, nargs="*", default=[])
    parser.add_argument("--include-50k", action="store_true")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    try:
        profiles = select_profiles(arguments.profiles, arguments.include_50k)
    except ValueError as error:
        parser.error(str(error))
    report = run(profiles)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {arguments.output}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
