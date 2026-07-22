"""Deterministic Retrieval V1 benchmark and V2 acceptance-gate comparator."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import sqlite3
import statistics
import sys
import tempfile
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from allthecontext.models import BootstrapRequest, SearchRequest
from allthecontext.retrieval import RetrievalEngine
from allthecontext.security import ClientPrincipal
from allthecontext.storage import CoreStore

FIXTURES = Path(__file__).with_name("retrieval_fixtures.json")
DEFAULT_BASELINE = Path(__file__).parent / "baselines" / "v1.json"
NORMAL_PROFILES = (1_000, 10_000)
OPT_IN_PROFILE = 50_000


def _load_json(path: Path) -> dict[str, Any]:
    loaded: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return loaded


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50_ms": round(_percentile(values, 0.50), 6),
        "p95_ms": round(_percentile(values, 0.95), 6),
    }


def _record_tuple(vault_id: str, record: dict[str, Any], ordinal: int) -> tuple[Any, ...]:
    content = str(record["content"])
    timestamp = f"2025-01-01T00:00:{ordinal % 60:02d}+00:00"
    return (
        str(record["id"]),
        vault_id,
        str(record.get("kind", "fact")),
        content,
        _json(record.get("scopes", [])),
        _json(record.get("tags", [])),
        _json(record.get("allowed_clients", [])),
        _json(record.get("denied_clients", [])),
        record.get("valid_from"),
        record.get("expires_at"),
        record.get("supersedes"),
        hashlib.sha256(content.encode("utf-8")).hexdigest(),
        timestamp,
        timestamp,
        record.get("deleted_at"),
    )


def _synthetic_record(ordinal: int) -> dict[str, Any]:
    group = ordinal % 101
    checksum = (ordinal * 2_654_435_761) % 1_000_003
    return {
        "id": f"scale-{ordinal:06d}",
        "kind": "synthetic_fact",
        "content": (
            f"Synthetic fixture {ordinal:06d} for project P{group:03d}; "
            f"topic T{ordinal % 257:03d}; checksum C{checksum:06d}."
        ),
        "scopes": [f"project:p{group:03d}"],
        "tags": [f"topic-{ordinal % 257:03d}"],
    }


def build_database(path: Path, size: int, fixture: dict[str, Any]) -> tuple[CoreStore, float]:
    records = list(fixture["records"])
    if size < len(records):
        raise ValueError(f"profile must contain at least {len(records)} records")
    records.extend(_synthetic_record(index) for index in range(size - len(records)))
    store = CoreStore(path)
    store.migrate()
    vault_id = store.initialize_vault("Synthetic retrieval benchmark", "UTC")
    insert_sql = (
        "INSERT INTO context_records("
        "id,vault_id,kind,content,scopes_json,tags_json,allowed_clients_json,"
        "denied_clients_json,valid_from,expires_at,supersedes,content_hash,created_at,"
        "updated_at,deleted_at,confidence,sensitivity,availability,approval_status,version,"
        "schema_version,explicit_user_statement) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
        "1.0,'normal','core_available','approved',1,1,0)"
    )
    started = time.perf_counter()
    with store.transaction() as connection:
        for ordinal, record in enumerate(records):
            values = _record_tuple(vault_id, record, ordinal)
            connection.execute(insert_sql, values)
            connection.execute(
                "INSERT INTO context_fts(record_id,content,kind,tags,scopes) "
                "VALUES(?,?,?,?,?)",
                (values[0], values[3], values[2], " ".join(record.get("tags", [])),
                 " ".join(record.get("scopes", []))),
            )
    elapsed = time.perf_counter() - started
    return store, elapsed


def _dcg(ids: Sequence[str], gold: dict[str, int]) -> float:
    return sum(
        (2 ** gold.get(record_id, 0) - 1) / math.log2(rank + 2)
        for rank, record_id in enumerate(ids)
    )


def _query_metrics(results: list[dict[str, Any]]) -> dict[str, float | int]:
    judged = [result for result in results if result["gold"]]
    forbidden_ids = {
        str(record_id) for result in results for record_id in result["forbidden"]
    }
    recalls: dict[int, list[float]] = {1: [], 3: [], 5: []}
    reciprocal_ranks: list[float] = []
    ndcgs: list[float] = []
    unauthorized = 0
    for result in results:
        ids = result["ids"]
        gold = result["gold"]
        unauthorized += len(set(ids) & forbidden_ids)
        if not gold:
            continue
        for cutoff in recalls:
            recalls[cutoff].append(len(set(ids[:cutoff]) & set(gold)) / len(gold))
        first = next((rank for rank, record_id in enumerate(ids, 1) if record_id in gold), None)
        reciprocal_ranks.append(0.0 if first is None else 1.0 / first)
        ideal = sorted(gold, key=gold.get, reverse=True)[:5]
        denominator = _dcg(ideal, gold)
        ndcgs.append(0.0 if denominator == 0 else _dcg(ids[:5], gold) / denominator)
    empty_rate = sum(not result["ids"] for result in results) / len(results)
    multi = [result for result in results if str(result["category"]).startswith("multi_term")]
    exact = [result for result in results if result["category"] == "exact"]
    return {
        "recall_at_1": round(statistics.fmean(recalls[1]), 6),
        "recall_at_3": round(statistics.fmean(recalls[3]), 6),
        "recall_at_5": round(statistics.fmean(recalls[5]), 6),
        "exact_recall_at_5": round(
            statistics.fmean(
                len(set(result["ids"][:5]) & set(result["gold"])) / len(result["gold"])
                for result in exact
            ),
            6,
        ),
        "mrr": round(statistics.fmean(reciprocal_ranks), 6),
        "ndcg_at_5": round(statistics.fmean(ndcgs), 6),
        "empty_result_rate": round(empty_rate, 6),
        "multi_term_empty_rate": round(sum(not item["ids"] for item in multi) / len(multi), 6),
        "unauthorized_result_count": unauthorized,
        "judged_query_count": len(judged),
    }


def run_profile(size: int, directory: Path, fixture: dict[str, Any]) -> dict[str, Any]:
    database = directory / f"retrieval-{size}.sqlite3"
    store, indexing_seconds = build_database(database, size, fixture)
    principal_data = fixture["principal"]
    principal = ClientPrincipal(
        str(principal_data["id"]),
        str(principal_data["name"]),
        frozenset(str(scope) for scope in principal_data["scopes"]),
    )
    engine = RetrievalEngine(store)
    results: list[dict[str, Any]] = []
    cold: list[float] = []
    warm: list[float] = []
    for query in fixture["queries"]:
        request = SearchRequest(query=str(query["query"]), limit=5)
        started = time.perf_counter()
        response = engine.search(request, principal)
        cold.append((time.perf_counter() - started) * 1_000)
        ids = [item.id for item in response.items]
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
            engine.search(request, principal)
            warm.append((time.perf_counter() - started) * 1_000)

    temporal = next(item for item in results if item["category"] == "temporal")
    temporal_ids = temporal["ids"]
    temporal_precision = (
        len(set(temporal_ids) & set(temporal["gold"])) / len(temporal_ids)
        if temporal_ids
        else 0.0
    )
    bootstrap_data = fixture["bootstrap"]
    bootstrap = engine.bootstrap(
        BootstrapRequest(
            task_description=str(bootstrap_data["query"]),
            requested_scopes=list(bootstrap_data["scopes"]),
            character_budget=12_000,
        ),
        principal,
    )
    bootstrap_ids = [item.id for item in bootstrap.items]
    bootstrap_gold = set(bootstrap_data["gold"])
    covered = len(set(bootstrap_ids) & bootstrap_gold) / len(bootstrap_gold)
    redundant_ids: set[str] = set()
    for group in bootstrap_data["duplicate_groups"]:
        selected = set(group) & set(bootstrap_ids)
        if len(selected) > 1:
            redundant_ids.update(sorted(selected)[1:])
    redundancy = len(redundant_ids) / len(bootstrap_ids) if bootstrap_ids else 0.0

    mutation: list[float] = []
    for iteration in range(10):
        content = f"Exports use ISO-8601 timestamp formatting. Revision {iteration % 2}."
        started = time.perf_counter()
        store.correct_record(
            "duplicate-secondary", content=content, reason="synthetic benchmark mutation"
        )
        mutation.append((time.perf_counter() - started) * 1_000)
    with store.connect() as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    metrics = _query_metrics(results)
    metrics.update(
        {
            "temporal_precision_at_5": round(temporal_precision, 6),
            "context_coverage": round(covered, 6),
            "context_redundancy": round(redundancy, 6),
            "cold_latency": _summary(cold),
            "warm_latency": _summary(warm),
            "index_size_bytes": database.stat().st_size,
            "initial_indexing_seconds": round(indexing_seconds, 6),
            "initial_indexing_records_per_second": round(size / indexing_seconds, 3),
            "mutation_indexing": _summary(mutation),
        }
    )
    return {"record_count": size, "metrics": metrics, "queries": results}


def run(profiles: Sequence[int]) -> dict[str, Any]:
    fixture = _load_json(FIXTURES)
    fixture_hash = hashlib.sha256(FIXTURES.read_bytes()).hexdigest()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="atc-retrieval-bench-") as temporary:
        directory = Path(temporary)
        measured = [run_profile(profile, directory, fixture) for profile in profiles]
    return {
        "schema_version": 1,
        "engine": "retrieval_v2_lexical_rrf",
        "fixture_sha256": fixture_hash,
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
        current = candidate["profiles"][profile]["metrics"]
        previous = baseline["profiles"][profile]["metrics"]
        gates = [
            (
                current["unauthorized_result_count"] == 0,
                "zero policy violations",
                current["unauthorized_result_count"],
            ),
            (
                current["exact_recall_at_5"] >= previous["exact_recall_at_5"],
                "exact Recall@5 not worse",
                current["exact_recall_at_5"],
            ),
            (
                current["mrr"] >= previous["mrr"] * 1.10,
                "overall MRR at least 10% better",
                current["mrr"],
            ),
            (
                current["multi_term_empty_rate"] <= previous["multi_term_empty_rate"] * 0.50,
                "multi-term empty rate at least 50% lower",
                current["multi_term_empty_rate"],
            ),
        ]
        if int(profile) == 10_000:
            limit = max(150.0, previous["warm_latency"]["p95_ms"] * 1.25)
            gates.append(
                (
                    current["warm_latency"]["p95_ms"] <= limit,
                    f"10k warm p95 <= {limit:.3f} ms",
                    current["warm_latency"]["p95_ms"],
                )
            )
        for ok, label, value in gates:
            passed = passed and ok
            messages.append(f"{'PASS' if ok else 'FAIL'} [{profile}] {label}: {value}")
    return passed, messages


def _profiles(values: Iterable[int], include_50k: bool) -> tuple[int, ...]:
    selected = tuple(dict.fromkeys(values)) or NORMAL_PROFILES
    if any(value <= 0 for value in selected):
        raise ValueError("profiles must be positive")
    if any(value >= OPT_IN_PROFILE for value in selected) and not include_50k:
        raise ValueError("the bounded 50k profile requires --include-50k")
    if any(value > OPT_IN_PROFILE for value in selected):
        raise ValueError("profiles above 50k are intentionally unsupported")
    return selected


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run the frozen V1 benchmark")
    run_parser.add_argument("--profiles", type=int, nargs="*", default=[])
    run_parser.add_argument("--include-50k", action="store_true")
    run_parser.add_argument("--output", type=Path)
    compare_parser = subparsers.add_parser("compare", help="evaluate V2 acceptance gates")
    compare_parser.add_argument("candidate", type=Path)
    compare_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    arguments = parser.parse_args(argv)
    if arguments.command == "run":
        try:
            profiles = _profiles(arguments.profiles, arguments.include_50k)
        except ValueError as error:
            parser.error(str(error))
        report = run(profiles)
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
    print("Retrieval V2 acceptance gates (V1 baseline comparison)")
    print("\n".join(messages))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
