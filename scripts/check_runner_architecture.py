"""Fail closed when a native artifact label does not match the build runner."""

from __future__ import annotations

import argparse
import json
import platform


def normalized_architecture(machine: str) -> str:
    normalized = machine.strip().casefold()
    if normalized in {"amd64", "x86_64", "x64"}:
        return "x86_64"
    if normalized in {"aarch64", "arm64"}:
        return "arm64"
    raise RuntimeError(f"unsupported native runner architecture: {machine!r}")


def verify_runner_architecture(expected: str, *, machine: str | None = None) -> str:
    observed = normalized_architecture(machine or platform.machine())
    if observed != expected:
        raise RuntimeError(
            f"native runner architecture mismatch: expected {expected}, observed {observed}"
        )
    return observed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", choices=("x86_64", "arm64"), required=True)
    arguments = parser.parse_args()
    observed = verify_runner_architecture(arguments.expected)
    print(json.dumps({"expected": arguments.expected, "observed": observed, "verified": True}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
