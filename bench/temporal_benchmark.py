"""Offline temporal sidecar precision and zero-resurrection benchmark."""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import tempfile
import time
from contextlib import closing
from pathlib import Path

from allthecontext.temporal import (
    PolicyEligibility,
    TemporalFact,
    TemporalQuery,
    TemporalSidecar,
)


def _facts(record_count: int) -> tuple[list[TemporalFact], set[str]]:
    facts: list[TemporalFact] = []
    expected: set[str] = set()
    for ordinal in range(record_count):
        record_id = f"temporal-{ordinal:06d}"
        predecessor = f"temporal-{ordinal - 1:06d}" if ordinal % 2 else None
        facts.append(
            TemporalFact.active(
                record_id=record_id,
                series_key=f"series-{ordinal // 2:06d}",
                created_at=("2026-02-01T00:00:00Z" if predecessor else "2026-01-01T00:00:00Z"),
                supersedes_record_id=predecessor,
            )
        )
        if ordinal % 2 or ordinal == record_count - 1:
            expected.add(record_id)
    return facts, expected


def run_benchmark(record_count: int, iterations: int) -> dict[str, object]:
    if record_count < 1 or iterations < 1:
        raise ValueError("record_count and iterations must be positive")
    facts, expected = _facts(record_count)
    deleted_id = "terminal-deleted"
    purged_id = "terminal-purged"
    stale_terminal = [
        TemporalFact.active(
            record_id=deleted_id,
            series_key="terminal-deleted-series",
            created_at="2026-01-01T00:00:00Z",
        ),
        TemporalFact.active(
            record_id=purged_id,
            series_key="terminal-purged-series",
            created_at="2026-01-01T00:00:00Z",
        ),
    ]
    authoritative = [
        *facts,
        *stale_terminal,
        TemporalFact.deleted(record_id=deleted_id, deleted_at="2026-03-01T00:00:00Z"),
        TemporalFact.purged(record_id=purged_id, purged_at="2026-03-02T00:00:00Z"),
    ]
    eligibility = PolicyEligibility.after_hard_policy(
        [*(fact.record_id for fact in facts), deleted_id, purged_id]
    )
    query = TemporalQuery.current(at="2026-07-01T00:00:00Z")
    forbidden = {deleted_id, purged_id}

    with tempfile.TemporaryDirectory(prefix="atc-temporal-benchmark-") as temporary:
        root = Path(temporary)
        database = root / "temporal.sqlite3"
        sidecar = TemporalSidecar(database)
        sidecar.rebuild([*facts, *stale_terminal])
        stale_snapshot = sidecar.export_snapshot()

        build_started = time.perf_counter()
        sidecar.rebuild(authoritative)
        build_ms = (time.perf_counter() - build_started) * 1_000

        latencies: list[float] = []
        resolutions = []
        for _ in range(iterations):
            started = time.perf_counter()
            result = sidecar.resolve(query, eligibility)
            latencies.append((time.perf_counter() - started) * 1_000)
            resolutions.append(result)

        selected = set(resolutions[0].selected_record_ids)
        true_positive = len(selected & expected)
        precision = true_positive / len(selected) if selected else float(not expected)
        recall = true_positive / len(expected) if expected else 1.0
        resurrection_count = sum(
            len(set(result.selected_record_ids) & forbidden) for result in resolutions
        )

        reopened = TemporalSidecar(database)
        resurrection_count += len(
            set(reopened.resolve(query, eligibility).selected_record_ids) & forbidden
        )

        with closing(sqlite3.connect(database)) as connection:
            connection.execute(
                "INSERT INTO temporal_intervals("
                "record_id,series_key,valid_from_utc,valid_to_utc,supersedes_record_id,"
                "revision,updated_at_utc,expires_at_utc) VALUES(?,?,?,?,?,?,?,?)",
                (
                    purged_id,
                    "terminal-purged-series",
                    "2026-01-01T00:00:00.000000Z",
                    None,
                    None,
                    1,
                    "2026-01-01T00:00:00.000000Z",
                    None,
                ),
            )
            connection.commit()
        resurrection_count += len(
            set(reopened.resolve(query, eligibility).selected_record_ids) & forbidden
        )

        restored = TemporalSidecar(root / "restored.sqlite3")
        restored.restore_snapshot(stale_snapshot, authoritative)
        resurrection_count += len(
            set(restored.resolve(query, eligibility).selected_record_ids) & forbidden
        )

        sorted_latencies = sorted(latencies)
        p95_index = min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95))
        return {
            "records": record_count,
            "iterations": iterations,
            "temporal_precision": round(precision, 6),
            "temporal_recall": round(recall, 6),
            "zero_resurrection_violations": resurrection_count,
            "deterministic_repeated_resolution": len(set(resolutions)) == 1,
            "build_ms": round(build_ms, 3),
            "resolve_p50_ms": round(statistics.median(latencies), 3),
            "resolve_p95_ms": round(sorted_latencies[p95_index], 3),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=10_000)
    parser.add_argument("--iterations", type=int, default=20)
    arguments = parser.parse_args()
    report = run_benchmark(arguments.records, arguments.iterations)
    print(json.dumps(report, indent=2, sort_keys=True))
    if (
        report["temporal_precision"] != 1.0
        or report["temporal_recall"] != 1.0
        or report["zero_resurrection_violations"] != 0
        or report["deterministic_repeated_resolution"] is not True
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
