"""Prove that CI parallel pytest commands execute every required test exactly once."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_PREFIX = "tests/"
DEFAULT_TEST_FILE_SECONDS = 0.859

# The twenty slowest files in the exact d18b4b7 sequential JUnit receipt. These
# bounded historical weights affect scheduling only. Unknown/new files receive
# the deterministic median default above and can never be omitted.
HISTORICAL_TEST_FILE_SECONDS = {
    "tests/unit/test_windows_update_helper.py": 95.737,
    "tests/unit/test_memory_reliability_spec.py": 82.009,
    "tests/unit/test_retrieval_m3_current_candidate.py": 42.628,
    "tests/unit/test_import_operations.py": 32.825,
    "tests/integration/test_cross_client_memory_acceptance.py": 29.074,
    "tests/unit/test_updater.py": 24.019,
    "tests/unit/test_export.py": 19.968,
    "tests/unit/test_packet_h_retrieval.py": 19.613,
    "tests/unit/test_recovery_admin.py": 19.221,
    "tests/integration/test_core_api.py": 19.190,
    "tests/unit/test_capture_runtime.py": 18.723,
    "tests/unit/test_registered_source_admission.py": 17.248,
    "tests/unit/test_memory_truth.py": 16.832,
    "tests/integration/test_mcp_stdio.py": 14.864,
    "tests/integration/test_claude_code_memory_core_api.py": 14.644,
    "tests/unit/test_capture.py": 13.483,
    "tests/unit/test_retrieval_usefulness.py": 13.407,
    "tests/unit/test_capture_scheduler_productization.py": 12.777,
    "tests/unit/test_windows_bootstrap_install.py": 11.590,
    "tests/unit/test_local_git_workspace_connector.py": 11.489,
}


def normalize_test_file(path: str) -> str:
    """Return a stable repository-relative test path or fail closed."""

    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    parts = normalized.split("/")
    if (
        not normalized.startswith(COLLECTION_PREFIX)
        or not normalized.endswith(".py")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"invalid repository test path: {path!r}")
    return "/".join(parts)


def collection_command(*, workers: int | None, targets: Sequence[str] = ()) -> list[str]:
    """Build sequential or file-level parallel collection commands."""

    command = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    if workers is not None:
        command.extend(["-n", str(workers), "--dist=loadfile"])
    command.extend(normalize_test_file(target) for target in targets)
    return command


def parse_collection(output: str) -> Counter[str]:
    """Return normalized nodeids while ignoring pytest progress and summaries."""

    nodeids: Counter[str] = Counter()
    for line in output.splitlines():
        normalized = line.strip().replace("\\", "/")
        if normalized.startswith(COLLECTION_PREFIX) and "::" in normalized:
            nodeids[normalized] += 1
    return nodeids


def collect(*, workers: int | None, targets: Sequence[str] = ()) -> Counter[str]:
    result = subprocess.run(
        collection_command(workers=workers, targets=targets),
        cwd=REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError(f"pytest collection failed with exit code {result.returncode}")
    nodeids = parse_collection(result.stdout)
    if not nodeids:
        raise RuntimeError("pytest collection produced no test nodeids")
    return nodeids


def test_file_for_nodeid(nodeid: str) -> str:
    path, separator, _ = nodeid.partition("::")
    if not separator:
        raise ValueError(f"invalid pytest nodeid: {nodeid!r}")
    return normalize_test_file(path)


def collection_digest(collection: Counter[str]) -> str:
    """Return an order-independent receipt for exact nodeids and multiplicity."""

    payload = "".join(f"{count} {nodeid}\n" for nodeid, count in sorted(collection.items())).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def assign_test_files(files: Sequence[str], *, shard_count: int) -> tuple[tuple[str, ...], ...]:
    """Assign every normalized test file to one deterministic weighted shard."""

    if shard_count < 1:
        raise ValueError("shard count must be positive")
    normalized = [normalize_test_file(path) for path in files]
    if len({path.casefold() for path in normalized}) != len(normalized):
        raise ValueError("test file list contains duplicate normalized paths")
    if len(normalized) < shard_count:
        raise ValueError("shard count exceeds collected test file count")

    weighted = sorted(
        normalized,
        key=lambda path: (
            -HISTORICAL_TEST_FILE_SECONDS.get(path, DEFAULT_TEST_FILE_SECONDS),
            path.casefold(),
            path,
        ),
    )
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    totals = [0.0] * shard_count
    for path in weighted:
        shard_index = min(
            range(shard_count),
            key=lambda index: (totals[index], len(shards[index]), index),
        )
        shards[shard_index].append(path)
        totals[shard_index] += HISTORICAL_TEST_FILE_SECONDS.get(path, DEFAULT_TEST_FILE_SECONDS)

    if any(not shard for shard in shards):
        raise ValueError("test file assignment produced an empty shard")
    return tuple(tuple(sorted(shard, key=lambda path: (path.casefold(), path))) for shard in shards)


def expected_shard_collections(
    complete: Counter[str], shards: Sequence[Sequence[str]]
) -> tuple[Counter[str], ...]:
    file_to_shard = {
        path: shard_index for shard_index, shard in enumerate(shards) for path in shard
    }
    expected = tuple(Counter() for _ in shards)
    for nodeid, count in complete.items():
        path = test_file_for_nodeid(nodeid)
        shard_index = file_to_shard.get(path)
        if shard_index is None:
            raise ValueError(f"collected test file was not assigned: {path}")
        expected[shard_index][nodeid] = count
    verify_complete_disjoint_union(complete, expected)
    return expected


def verify_complete_disjoint_union(
    complete: Counter[str], shard_collections: Sequence[Counter[str]]
) -> None:
    """Reject missing or multiply assigned nodeids before test execution."""

    if not shard_collections or any(not shard for shard in shard_collections):
        raise ValueError("shard collection is missing or empty")
    combined: Counter[str] = Counter()
    for shard in shard_collections:
        overlap = combined & shard
        if overlap:
            raise ValueError(f"shard collections overlap on {sum(overlap.values())} nodeids")
        combined.update(shard)
    if combined != complete:
        raise ValueError(
            "shard collection union differs from complete collection "
            f"(complete={sum(complete.values())}, union={sum(combined.values())}, "
            f"missing={sum((complete - combined).values())}, "
            f"extra={sum((combined - complete).values())})"
        )


def _collections_match(expected: Counter[str], actual: Counter[str], *, description: str) -> bool:
    if expected == actual:
        return True
    print(
        f"pytest collection contract failed: {description} "
        f"(expected={sum(expected.values())}, actual={sum(actual.values())}, "
        f"missing={sum((expected - actual).values())}, "
        f"extra={sum((actual - expected).values())})",
        file=sys.stderr,
    )
    return False


def verify_collection(
    *,
    workers: int,
    shard_count: int | None = None,
    shard_index: int | None = None,
    write_targets: Path | None = None,
) -> int:
    if workers < 1:
        raise ValueError("workers must be positive")
    sequential = collect(workers=None)
    parallel = collect(workers=workers)
    if not _collections_match(
        sequential,
        parallel,
        description="sequential and complete parallel nodeid sets differ",
    ):
        return 1

    if shard_count is None:
        if shard_index is not None or write_targets is not None:
            raise ValueError("shard index/output requires a shard count")
        print(
            "pytest collection contract passed: "
            f"{sum(sequential.values())} nodeids are identical for sequential and "
            f"{workers}-worker --dist=loadfile collection; "
            f"sha256={collection_digest(sequential)}"
        )
        return 0
    if shard_index is None:
        raise ValueError("shard index is required when shard count is set")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard index is outside the configured shard range")

    files = sorted({test_file_for_nodeid(nodeid) for nodeid in sequential})
    shards = assign_test_files(files, shard_count=shard_count)
    expected = expected_shard_collections(sequential, shards)
    targets = shards[shard_index]
    shard_sequential = collect(workers=None, targets=targets)
    shard_parallel = collect(workers=workers, targets=targets)
    if not _collections_match(
        expected[shard_index],
        shard_sequential,
        description=f"shard {shard_index} sequential nodeids differ from assignment",
    ):
        return 1
    if not _collections_match(
        shard_sequential,
        shard_parallel,
        description=f"shard {shard_index} sequential and parallel nodeids differ",
    ):
        return 1

    if write_targets is not None:
        write_targets.write_text("".join(f"{target}\n" for target in targets), encoding="utf-8")
    print(
        "pytest sharding contract passed: "
        f"{sum(sequential.values())} complete nodeids partition into {shard_count} "
        f"disjoint shards; shard {shard_index} has {len(targets)} files and "
        f"{sum(shard_sequential.values())} nodeids identical for sequential and "
        f"{workers}-worker --dist=loadfile collection; "
        f"complete_sha256={collection_digest(sequential)}; "
        f"shard_sha256={collection_digest(shard_sequential)}"
    )
    return 0


def require_successful_shards(result: str) -> int:
    """Propagate a matrix dependency result through the stable required check."""

    if result == "success":
        print("Windows pytest shards completed successfully")
        return 0
    print(f"Windows pytest shards did not succeed: {result or 'missing'}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--shard-count", type=int)
    parser.add_argument("--shard-index", type=int)
    parser.add_argument("--write-targets", type=Path)
    parser.add_argument("--require-shard-result")
    arguments = parser.parse_args()
    try:
        if arguments.require_shard_result is not None:
            if any(
                value is not None
                for value in (
                    arguments.workers,
                    arguments.shard_count,
                    arguments.shard_index,
                    arguments.write_targets,
                )
            ):
                raise ValueError("shard result propagation cannot collect tests")
            return require_successful_shards(arguments.require_shard_result)
        if arguments.workers is None:
            raise ValueError("workers are required for collection verification")
        return verify_collection(
            workers=arguments.workers,
            shard_count=arguments.shard_count,
            shard_index=arguments.shard_index,
            write_targets=arguments.write_targets,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"pytest collection contract error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
