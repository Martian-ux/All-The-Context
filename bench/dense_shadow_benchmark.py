"""Bounded benchmark for the optional local 384-dimensional dense shadow.

The default command is disabled and reports ``not_exercised``. An explicit
``--exact-scan-only`` run uses a deterministic non-semantic fixture runtime to
measure the float32 matrix and exhaustive scan without making a model-quality
claim. Semantic coverage is compared with candidate-scoped lexical retrieval
only after an explicitly supplied local Sentence Transformers model is loaded
and genuinely exercised.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import struct
import sys
import time
from collections.abc import Iterable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Any

from allthecontext.lexical_v3 import LexicalV3

from bench.dense_shadow import (
    EMBEDDING_DIMENSION,
    MAX_AUTHORIZED_CANDIDATES,
    VECTOR_BYTES_PER_CANDIDATE,
    DenseEmbeddingRuntime,
    DenseShadowCandidate,
    DenseShadowExperiment,
    DenseShadowReason,
    ExperimentStatus,
    LocalSentenceTransformerRuntime,
    probe_local_sentence_transformer,
)

FIXTURES = Path(__file__).with_name("dense_shadow_fixtures.json")
DEFAULT_PROFILES = (128, 1_024)
MAXIMUM_PROFILE = 10_000
DEFAULT_ITERATIONS = 5
MAX_ITERATIONS = 20
EXACT_SCAN_TARGET_RECORDS = 10_000
EXACT_SCAN_P95_TARGET_MS = 150.0


class BenchmarkMode(StrEnum):
    DISABLED = "disabled"
    EXACT_SCAN_ONLY = "exact_scan_only"
    LOCAL_MODEL = "local_model"


class FixtureMeasurementRuntime:
    """Deterministic 384-float generator with explicitly no semantic claim."""

    @property
    def dimension(self) -> int:
        return EMBEDDING_DIMENSION

    def embed(self, texts: Sequence[str]) -> Iterable[Iterable[float]]:
        for text in texts:
            payload = hashlib.shake_256(
                b"atc-dense-shadow-fixture-v1\0" + text.encode("utf-8")
            ).digest(EMBEDDING_DIMENSION * 2)
            yield tuple(float(value) for (value,) in struct.iter_unpack("<h", payload))


def _load_fixture() -> dict[str, Any]:
    loaded: object = json.loads(FIXTURES.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("schema_version") != 1:
        raise ValueError("dense shadow fixture has an unsupported schema")
    records = loaded.get("records")
    queries = loaded.get("queries")
    if not isinstance(records, list) or not records:
        raise ValueError("dense shadow fixture requires records")
    if not isinstance(queries, list) or not queries:
        raise ValueError("dense shadow fixture requires queries")
    return loaded


def select_profiles(values: Sequence[int], include_10k: bool) -> tuple[int, ...]:
    selected = tuple(dict.fromkeys(values)) or DEFAULT_PROFILES
    if any(value <= 0 for value in selected):
        raise ValueError("profiles must be positive")
    if any(value > DEFAULT_PROFILES[-1] for value in selected) and not include_10k:
        raise ValueError("profiles above 1024 require --include-10k")
    if any(value > MAXIMUM_PROFILE for value in selected):
        raise ValueError("profiles above 10k are intentionally unsupported")
    return selected


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    return {
        "p50_ms": round(_percentile(values, 0.50), 6),
        "p95_ms": round(_percentile(values, 0.95), 6),
    }


def _candidate(value: dict[str, Any]) -> DenseShadowCandidate:
    authorized = bool(value["authorized"])
    eligible = bool(value["eligible"])
    return DenseShadowCandidate(
        record_id=str(value["id"]),
        content=str(value["content"]),
        candidate_authorized=authorized,
        candidate_temporally_eligible=eligible,
        content_authorized=authorized,
        content_temporally_eligible=eligible,
    )


def _fixture_candidates(fixture: dict[str, Any]) -> tuple[DenseShadowCandidate, ...]:
    return tuple(_candidate(value) for value in fixture["records"])


def _profile_candidates(size: int, fixture: dict[str, Any]) -> tuple[DenseShadowCandidate, ...]:
    base = _fixture_candidates(fixture)
    authorized = [candidate for candidate in base if candidate.boundary_verified]
    if size < len(authorized):
        authorized = authorized[:size]
    else:
        for position in range(size - len(authorized)):
            authorized.append(
                DenseShadowCandidate(
                    record_id=f"filler-{position:05d}",
                    content=(
                        "Deterministic synthetic measurement filler "
                        f"number {position:05d} for exhaustive CPU scan."
                    ),
                )
            )
    rejected = [candidate for candidate in base if not candidate.boundary_verified]
    return (*authorized, *rejected)


def _query_values(fixture: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(value["query"]) for value in fixture["queries"])


def _not_exercised_profile(size: int, reason: DenseShadowReason) -> dict[str, Any]:
    return {
        "record_count": size,
        "status": ExperimentStatus.NOT_EXERCISED,
        "reason": reason,
    }


def _determinism_sample(
    runtime: DenseEmbeddingRuntime,
    candidates: Sequence[DenseShadowCandidate],
) -> bool:
    safe = [candidate for candidate in candidates if candidate.boundary_verified][:32]
    experiment = DenseShadowExperiment(runtime, enabled=True)
    first = experiment.rebuild(safe)
    second = experiment.rebuild(list(reversed(safe)))
    return (
        first.index is not None
        and second.index is not None
        and first.index.payload_equal(second.index)
    )


def _run_profile(
    size: int,
    fixture: dict[str, Any],
    runtime: DenseEmbeddingRuntime,
    iterations: int,
) -> dict[str, Any]:
    candidates = _profile_candidates(size, fixture)
    authorized_candidates = tuple(
        candidate for candidate in candidates if candidate.boundary_verified
    )
    experiment = DenseShadowExperiment(runtime, enabled=True)
    started = time.perf_counter()
    # Keep the timed statistic candidate-scoped. Boundary pollution invariance
    # is exercised separately in focused tests, not folded into build latency.
    build = experiment.rebuild(authorized_candidates)
    build_ms = (time.perf_counter() - started) * 1_000
    if build.index is None:
        return _not_exercised_profile(size, build.diagnostics.reason)
    index = build.index
    query_embedding: list[float] = []
    exact_scan: list[float] = []
    deterministic = True
    for query in _query_values(fixture):
        started = time.perf_counter()
        prepared = index.prepare_query(query)
        query_embedding.append((time.perf_counter() - started) * 1_000)
        if prepared.query_vector is None:
            return _not_exercised_profile(size, prepared.diagnostics.reason)
        expected: tuple[tuple[str, float], ...] | None = None
        for _iteration in range(iterations):
            started = time.perf_counter()
            result = index.exact_scan(prepared.query_vector, limit=5)
            exact_scan.append((time.perf_counter() - started) * 1_000)
            projection = tuple((hit.record_id, hit.score) for hit in result.hits)
            if expected is None:
                expected = projection
            else:
                deterministic &= projection == expected
    metrics: dict[str, Any] = {
        "dimension": EMBEDDING_DIMENSION,
        "vector_format": "float32",
        "vector_storage_bytes": index.vector_storage_bytes,
        "vector_bytes_per_candidate": round(index.vector_storage_bytes / size, 6),
        "expected_vector_bytes_per_candidate": VECTOR_BYTES_PER_CANDIDATE,
        "build_latency_ms": round(build_ms, 6),
        "query_embedding_latency": _latency_summary(query_embedding),
        "exact_scan_latency": _latency_summary(exact_scan),
        "repeated_exact_scan_deterministic": deterministic,
        "embedding_sample_deterministic": _determinism_sample(runtime, candidates),
    }
    return {
        "record_count": size,
        "status": ExperimentStatus.EXERCISED,
        "reason": DenseShadowReason.COMPLETED,
        "metrics": metrics,
    }


def _coverage(results: dict[str, tuple[str, ...]], fixture: dict[str, Any]) -> float:
    covered = 0
    facet_count = 0
    for query in fixture["queries"]:
        returned = set(results[str(query["id"])])
        for facet in query["facets"]:
            facet_count += 1
            covered += bool(returned & {str(record_id) for record_id in facet})
    return covered / facet_count if facet_count else 0.0


def _lexical_results(fixture: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    candidates = _fixture_candidates(fixture)
    eligible_ids = tuple(
        candidate.record_id for candidate in candidates if candidate.boundary_verified
    )
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE context_fts USING "
            "fts5(record_id UNINDEXED, content, kind, tags, scopes)"
        )
        connection.executemany(
            "INSERT INTO context_fts(record_id,content,kind,tags,scopes) VALUES(?,?,?,?,?)",
            (
                (candidate.record_id, candidate.content, "synthetic", "", "")
                for candidate in candidates
            ),
        )
        lexical = LexicalV3()
        return {
            str(query["id"]): tuple(
                hit.record_id
                for hit in lexical.search(
                    connection,
                    eligible_ids,
                    str(query["query"]),
                    limit=5,
                ).hits
            )
            for query in fixture["queries"]
        }
    finally:
        connection.close()


def _semantic_comparison(
    fixture: dict[str, Any], runtime: LocalSentenceTransformerRuntime
) -> dict[str, Any]:
    experiment = DenseShadowExperiment(runtime, enabled=True)
    build = experiment.rebuild(_fixture_candidates(fixture))
    if build.index is None:
        return {
            "status": ExperimentStatus.NOT_EXERCISED,
            "reason": build.diagnostics.reason,
        }
    dense_results: dict[str, tuple[str, ...]] = {}
    for query in fixture["queries"]:
        result = build.index.search(str(query["query"]), limit=5)
        if result.diagnostics.status != ExperimentStatus.EXERCISED:
            return {
                "status": ExperimentStatus.NOT_EXERCISED,
                "reason": result.diagnostics.reason,
            }
        dense_results[str(query["id"])] = tuple(hit.record_id for hit in result.hits)
    try:
        lexical_results = _lexical_results(fixture)
    except sqlite3.Error:
        return {
            "status": ExperimentStatus.NOT_EXERCISED,
            "reason": DenseShadowReason.RUNTIME_UNAVAILABLE,
        }
    lexical_coverage = _coverage(lexical_results, fixture)
    dense_coverage = _coverage(dense_results, fixture)
    return {
        "status": ExperimentStatus.EXERCISED,
        "reason": DenseShadowReason.COMPLETED,
        "query_count": len(fixture["queries"]),
        "facet_count": sum(len(query["facets"]) for query in fixture["queries"]),
        "lexical_coverage_at_5": round(lexical_coverage, 6),
        "dense_coverage_at_5": round(dense_coverage, 6),
        "dense_minus_lexical_coverage_at_5": round(dense_coverage - lexical_coverage, 6),
    }


def _scan_target(profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    measured = profiles.get(str(EXACT_SCAN_TARGET_RECORDS))
    if measured is None or measured.get("status") != ExperimentStatus.EXERCISED:
        return {
            "status": ExperimentStatus.NOT_EXERCISED,
            "record_count": EXACT_SCAN_TARGET_RECORDS,
            "p95_target_ms": EXACT_SCAN_P95_TARGET_MS,
        }
    latency = float(measured["metrics"]["exact_scan_latency"]["p95_ms"])
    return {
        "status": ExperimentStatus.EXERCISED,
        "record_count": EXACT_SCAN_TARGET_RECORDS,
        "p95_target_ms": EXACT_SCAN_P95_TARGET_MS,
        "measured_p95_ms": latency,
        "target_met": latency <= EXACT_SCAN_P95_TARGET_MS,
    }


def run(
    mode: BenchmarkMode = BenchmarkMode.DISABLED,
    *,
    profiles: Sequence[int] = DEFAULT_PROFILES,
    iterations: int = DEFAULT_ITERATIONS,
    model_path: Path | None = None,
) -> dict[str, Any]:
    """Run one explicitly selected experiment mode without a production gate."""

    if not 1 <= iterations <= MAX_ITERATIONS:
        raise ValueError(f"iterations must be between 1 and {MAX_ITERATIONS}")
    selected = tuple(profiles)
    if not selected or any(size <= 0 or size > MAX_AUTHORIZED_CANDIDATES for size in selected):
        raise ValueError("profiles must contain values between 1 and 10000")
    fixture = _load_fixture()
    runtime: DenseEmbeddingRuntime | None = None
    optional_model: dict[str, Any]
    if mode == BenchmarkMode.DISABLED:
        optional_model = {
            "status": ExperimentStatus.NOT_EXERCISED,
            "reason": DenseShadowReason.DISABLED,
        }
    elif mode == BenchmarkMode.EXACT_SCAN_ONLY:
        runtime = FixtureMeasurementRuntime()
        optional_model = {
            "status": ExperimentStatus.NOT_EXERCISED,
            "reason": DenseShadowReason.FIXTURE_RUNTIME,
        }
    else:
        availability = probe_local_sentence_transformer(
            enabled=True,
            model_path=model_path,
        )
        runtime = availability.runtime
        optional_model = {
            "status": availability.status,
            "reason": availability.reason,
        }

    measured: dict[str, dict[str, Any]] = {}
    if runtime is not None:
        measured = {
            str(size): _run_profile(size, fixture, runtime, iterations) for size in selected
        }
    exact_scan_status = (
        ExperimentStatus.EXERCISED
        if measured
        and all(profile["status"] == ExperimentStatus.EXERCISED for profile in measured.values())
        else ExperimentStatus.NOT_EXERCISED
    )
    if isinstance(runtime, LocalSentenceTransformerRuntime):
        semantic = _semantic_comparison(fixture, runtime)
    else:
        semantic_reason = optional_model["reason"]
        semantic = {
            "status": ExperimentStatus.NOT_EXERCISED,
            "reason": semantic_reason,
        }
    model_status = (
        ExperimentStatus.EXERCISED
        if optional_model["status"] == ExperimentStatus.EXERCISED
        and exact_scan_status == ExperimentStatus.EXERCISED
        and semantic["status"] == ExperimentStatus.EXERCISED
        else ExperimentStatus.NOT_EXERCISED
    )
    if model_status == ExperimentStatus.NOT_EXERCISED:
        if optional_model["status"] == ExperimentStatus.EXERCISED:
            failed_profile = next(
                (
                    profile
                    for profile in measured.values()
                    if profile["status"] != ExperimentStatus.EXERCISED
                ),
                None,
            )
            optional_model["reason"] = (
                failed_profile["reason"] if failed_profile is not None else semantic["reason"]
            )
        optional_model["status"] = ExperimentStatus.NOT_EXERCISED

    declared_not_exercised = ["production_ranking_authority", "ann"]
    if model_status == ExperimentStatus.NOT_EXERCISED:
        declared_not_exercised.extend(["optional_local_model", "semantic_coverage_comparison"])
    declared_not_exercised.append("cross_platform_determinism")
    return {
        "schema_version": 1,
        "report_kind": "optional_local_dense_shadow",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
        },
        "configuration": {
            "mode": mode,
            "default_enabled": False,
            "dimension": EMBEDDING_DIMENSION,
            "device": "cpu",
            "scan": "exact",
            "persistent_storage": False,
            "canonical": False,
            "production_ranking_authority": False,
        },
        "optional_model": optional_model,
        "exact_scan": {
            "status": exact_scan_status,
            "profiles": measured,
            "latency_target": _scan_target(measured),
        },
        "semantic_comparison": semantic,
        "ann": {
            "implemented": False,
            "status": ExperimentStatus.NOT_EXERCISED,
            "reason": "forbidden_in_this_experiment",
        },
        "declared_not_exercised": sorted(declared_not_exercised),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--exact-scan-only", action="store_true")
    mode.add_argument("--enable-local-model", action="store_true")
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--profiles", type=int, nargs="*", default=[])
    parser.add_argument("--include-10k", action="store_true")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    if arguments.model_path is not None and not arguments.enable_local_model:
        parser.error("--model-path requires --enable-local-model")
    selected_mode = BenchmarkMode.DISABLED
    if arguments.exact_scan_only:
        selected_mode = BenchmarkMode.EXACT_SCAN_ONLY
    elif arguments.enable_local_model:
        selected_mode = BenchmarkMode.LOCAL_MODEL
    try:
        profiles = select_profiles(arguments.profiles, arguments.include_10k)
        report = run(
            selected_mode,
            profiles=profiles,
            iterations=arguments.iterations,
            model_path=arguments.model_path,
        )
    except ValueError as error:
        parser.error(str(error))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is None:
        print(rendered, end="")
    else:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
        print(f"wrote {arguments.output}")
    if (
        selected_mode == BenchmarkMode.LOCAL_MODEL
        and report["optional_model"]["status"] != ExperimentStatus.EXERCISED
    ):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
