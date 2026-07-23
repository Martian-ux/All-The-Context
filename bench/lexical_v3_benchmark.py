"""Deterministic benchmark for the isolated candidate-scoped lexical-v3 ranker."""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from allthecontext.lexical_v3 import LexicalV3
from allthecontext.models import SearchRequest
from allthecontext.retrieval import EligibleRecordSelector
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore, durable_sqlite_footprint

from .retrieval_benchmark import (
    DEFAULT_BASELINE,
    FIXTURES,
    NORMAL_PROFILES,
    _load_json,
    _profiles,
    _query_metrics,
    _summary,
    build_database,
)

MAX_PERSISTENT_INDEX_GROWTH_BYTES = 4_096


def _macro_precision_at_5(results: Sequence[dict[str, Any]]) -> float:
    judged = [result for result in results if result["gold"]]
    precisions: list[float] = []
    for result in judged:
        ids = [str(record_id) for record_id in result["ids"][:5]]
        relevant = set(str(record_id) for record_id in result["gold"])
        precisions.append(len(set(ids) & relevant) / len(ids) if ids else 0.0)
    return round(statistics.fmean(precisions), 6)


def _search(
    store: CoreStore,
    selector: EligibleRecordSelector,
    ranker: LexicalV3,
    principal: ClientPrincipal,
    query: str,
) -> list[str]:
    request = SearchRequest(query=query, limit=5)
    with store.connect() as connection:
        eligible, _denied = selector.select(
            connection, request, principal, store.vault_id()
        )
        result = ranker.search(
            connection,
            [str(row["id"]) for row in eligible],
            query,
            limit=request.limit,
        )
    return [hit.record_id for hit in result.hits]


def _checkpointed_footprint(store: CoreStore) -> int:
    with store.connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    return durable_sqlite_footprint(store.database_path)


def run_profile(size: int, directory: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    database = directory / f"lexical-v3-{size}.sqlite3"
    store, indexing_seconds = build_database(database, size, fixture)
    persistent_bytes_before = _checkpointed_footprint(store)
    principal_data = fixture["principal"]
    principal = ClientPrincipal(
        str(principal_data["id"]),
        str(principal_data["name"]),
        frozenset(str(scope) for scope in principal_data["scopes"]),
    )
    selector = EligibleRecordSelector()
    ranker = LexicalV3()
    results: list[dict[str, Any]] = []
    cold: list[float] = []
    warm: list[float] = []
    for query in fixture["queries"]:
        query_text = str(query["query"])
        started = time.perf_counter()
        ids = _search(store, selector, ranker, principal, query_text)
        cold.append((time.perf_counter() - started) * 1_000)
        results.append(
            {
                "id": query["id"],
                "category": query["category"],
                "ids": ids,
                "gold": query["gold"],
                "forbidden": query.get("forbidden", []),
            }
        )
        for _ in range(5):
            started = time.perf_counter()
            _search(store, selector, ranker, principal, query_text)
            warm.append((time.perf_counter() - started) * 1_000)

    persistent_bytes_after = _checkpointed_footprint(store)
    metrics = _query_metrics(results)
    metrics.update(
        {
            "macro_precision_at_5": _macro_precision_at_5(results),
            "cold_latency": _summary(cold),
            "warm_latency": _summary(warm),
            "index_size_bytes": persistent_bytes_after,
            "persistent_index_growth_bytes": max(
                0, persistent_bytes_after - persistent_bytes_before
            ),
            "initial_indexing_seconds": round(indexing_seconds, 6),
        }
    )
    return {"record_count": size, "metrics": metrics, "queries": results}


def run(profiles: Sequence[int]) -> dict[str, Any]:
    fixture = _load_json(FIXTURES)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="atc-lexical-v3-bench-") as temporary:
        directory = Path(temporary)
        measured = [run_profile(profile, directory, fixture) for profile in profiles]
    return {
        "schema_version": 1,
        "engine": "retrieval_lexical_v3_candidate_scoped",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "sqlite": sqlite3.sqlite_version,
        },
        "profiles": {str(item["record_count"]): item for item in measured},
        "runtime_seconds": round(time.perf_counter() - started, 6),
    }


def compare(candidate: dict[str, Any], baseline: dict[str, Any]) -> tuple[bool, list[str]]:
    messages: list[str] = []
    passed = True
    common = sorted(set(candidate["profiles"]) & set(baseline["profiles"]), key=int)
    if not common:
        return False, ["FAIL no common benchmark profiles"]
    for profile in common:
        current_profile = candidate["profiles"][profile]
        baseline_profile = baseline["profiles"][profile]
        current = current_profile["metrics"]
        previous = baseline_profile["metrics"]
        baseline_precision = _macro_precision_at_5(baseline_profile["queries"])
        gates = [
            (
                current["unauthorized_result_count"] == 0,
                "zero policy violations",
                current["unauthorized_result_count"],
            ),
            (
                current["exact_recall_at_5"] >= previous["exact_recall_at_5"],
                "exact Recall@5 preserved",
                current["exact_recall_at_5"],
            ),
            (
                current["recall_at_5"] >= previous["recall_at_5"],
                "overall Recall@5 not worse",
                current["recall_at_5"],
            ),
            (
                current["mrr"] >= previous["mrr"] * 1.10,
                "MRR at least 10% better",
                current["mrr"],
            ),
            (
                current["macro_precision_at_5"] > baseline_precision,
                "macro Precision@5 improved",
                current["macro_precision_at_5"],
            ),
            (
                current["persistent_index_growth_bytes"]
                <= MAX_PERSISTENT_INDEX_GROWTH_BYTES,
                f"persistent index growth <= {MAX_PERSISTENT_INDEX_GROWTH_BYTES} bytes",
                current["persistent_index_growth_bytes"],
            ),
        ]
        if int(profile) == 10_000:
            gates.append(
                (
                    current["warm_latency"]["p95_ms"] < 150.0,
                    "10k warm p95 < 150 ms",
                    current["warm_latency"]["p95_ms"],
                )
            )
        for ok, label, value in gates:
            passed = passed and ok
            messages.append(f"{'PASS' if ok else 'FAIL'} [{profile}] {label}: {value}")
    return passed, messages


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run lexical-v3 on the frozen fixture")
    run_parser.add_argument("--profiles", type=int, nargs="*", default=[])
    run_parser.add_argument("--include-50k", action="store_true")
    run_parser.add_argument("--output", type=Path)
    compare_parser = subparsers.add_parser("compare", help="compare lexical-v3 with V1")
    compare_parser.add_argument("candidate", type=Path)
    compare_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    arguments = parser.parse_args(argv)
    if arguments.command == "run":
        try:
            profiles = _profiles(arguments.profiles, arguments.include_50k)
        except ValueError as error:
            parser.error(str(error))
        report = run(profiles or NORMAL_PROFILES)
        rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
        if arguments.output:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(rendered, encoding="utf-8")
            print(f"wrote {arguments.output} in {report['runtime_seconds']:.3f}s")
        else:
            print(rendered, end="")
        return 0
    candidate = _load_json(arguments.candidate)
    baseline = _load_json(arguments.baseline)
    passed, messages = compare(candidate, baseline)
    print("Retrieval lexical-v3 acceptance gates (frozen V1 comparison)")
    print("\n".join(messages))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
