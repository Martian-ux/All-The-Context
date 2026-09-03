"""Prove that the CI parallel pytest command does not omit required tests."""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_PREFIX = "tests/"


def collection_command(*, workers: int | None) -> list[str]:
    """Build the only two collection commands permitted by the CI contract."""

    command = [sys.executable, "-m", "pytest", "--collect-only", "-q"]
    if workers is not None:
        command.extend(["-n", str(workers), "--dist=loadfile"])
    return command


def parse_collection(output: str) -> Counter[str]:
    """Return normalized nodeids while ignoring pytest progress and summaries."""

    nodeids: Counter[str] = Counter()
    for line in output.splitlines():
        normalized = line.strip().replace("\\", "/")
        if normalized.startswith(COLLECTION_PREFIX) and "::" in normalized:
            nodeids[normalized] += 1
    return nodeids


def collect(*, workers: int | None) -> Counter[str]:
    result = subprocess.run(
        collection_command(workers=workers),
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


def verify_collection(*, workers: int) -> int:
    if workers < 1:
        raise ValueError("workers must be positive")
    sequential = collect(workers=None)
    parallel = collect(workers=workers)
    if sequential != parallel:
        print(
            "pytest collection contract failed: sequential and parallel nodeid sets differ "
            f"(sequential={sum(sequential.values())}, parallel={sum(parallel.values())}, "
            f"missing={sum((sequential - parallel).values())}, "
            f"extra={sum((parallel - sequential).values())})",
            file=sys.stderr,
        )
        return 1
    print(
        "pytest collection contract passed: "
        f"{sum(sequential.values())} nodeids are identical for sequential and "
        f"{workers}-worker --dist=loadfile collection"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, required=True)
    arguments = parser.parse_args()
    try:
        return verify_collection(workers=arguments.workers)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"pytest collection contract error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
