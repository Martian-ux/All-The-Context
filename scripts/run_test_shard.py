"""Run a fixture-aware CI shard or independent Windows discovery and retrieval."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

from check_test_collection import (
    CI_SHARD_COUNTS,
    RETRIEVAL_TEST_FILE,
    WINDOWS_DISCOVERY_JOB,
    read_receipt,
    require,
    source_identity,
)

ROOT = Path(__file__).resolve().parents[1]


def run_pytest(root: Path, output: Path, metadata: dict) -> int:
    """Keep pytest's default discovery and warnings; never retry failed workers."""

    mode = metadata["mode"]
    receipt = output / f"{mode}.json"
    require(not receipt.exists(), "refusing to reuse an existing invocation receipt")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "scripts.pytest_shard_receipt",
        "--atc-ci-metadata",
        json.dumps(metadata),
        "--atc-ci-receipt",
        str(receipt),
        "--basetemp",
        str(output / f"{mode}-tmp"),
    ]
    if mode == "canonical":
        command.extend(["--collect-only", "-q", "-n", "0"])
    elif mode == "retrieval":
        command.extend(["-n", "0", RETRIEVAL_TEST_FILE])
    else:
        command.extend(
            ["-n", str(metadata["workers"]), "--dist=loadgroup", "--max-worker-restart=0"]
        )
        if metadata["platform"] == "windows":
            command.append("--no-loadscope-reorder")
    # Separate canonical stdout avoids interleaving its full node listing with
    # live shard failures. Both logs survive failed invocations for diagnosis.
    with (output / f"{mode}.log").open("w", encoding="utf-8") as log:
        started = perf_counter()
        process = subprocess.Popen(
            command,
            cwd=root,
            stdout=log if mode == "canonical" else subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1"),
            text=True,
            encoding="utf-8",
            errors="backslashreplace",
        )
        if process.stdout is not None:
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
        code = process.wait()
        elapsed = perf_counter() - started
    require(receipt.is_file(), f"{mode} did not produce a receipt (exit {code})")
    payload = read_receipt(receipt)
    require(
        all(payload.get(key) == value for key, value in metadata.items()),
        "pytest receipt identity changed",
    )
    payload["process_exit"] = code
    payload["process_elapsed_seconds"] = elapsed
    receipt.write_text(json.dumps(payload, ensure_ascii=True) + "\n", encoding="utf-8")
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    invocation = parser.add_mutually_exclusive_group(required=True)
    invocation.add_argument("--shard-index", type=int)
    invocation.add_argument("--discovery", action="store_true")
    arguments = parser.parse_args()
    try:
        platform = {"win32": "windows", "linux": "linux"}[sys.platform]
        count = CI_SHARD_COUNTS[platform]
        job = "python-windows-shard" if platform == "windows" else "python"
        if arguments.discovery:
            require(platform == "windows", "standalone discovery requires Windows")
            job, shard, suffix = WINDOWS_DISCOVERY_JOB, 0, "discovery"
            modes = ["canonical", "retrieval"]
        else:
            shard = arguments.shard_index
            require(0 <= shard < count, "shard index is outside the frozen OS schedule")
            suffix = str(shard)
            modes = ["shard"]
            if platform == "linux" and shard == 0:
                modes.extend(["canonical", "retrieval"])
        run_id, run_attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
        require(run_id.isdecimal() and run_attempt.isdecimal(), "invalid GitHub run identity")
        source = source_identity()
        require(source["head"] == os.environ["GITHUB_SHA"], "checkout differs from CI source")
        require(os.environ["GITHUB_JOB"] == job, "unexpected CI job")
        output = ROOT / ".test-runs" / f"pytest-{run_id}-{run_attempt}-{platform}-{suffix}"
        output.mkdir(parents=True, exist_ok=False)
        metadata = dict(
            source,
            schema=1,
            platform=platform,
            run_id=run_id,
            run_attempt=run_attempt,
            shard_count=count,
            workers=4,
            job=f"{job}/{shard}",
            shard=shard,
        )
        with ThreadPoolExecutor(max_workers=len(modes)) as pool:
            futures = [
                pool.submit(run_pytest, ROOT, output, dict(metadata, mode=mode)) for mode in modes
            ]
            results = [future.result() for future in futures]
        require(source_identity() == source, "source changed during pytest execution")
        return 0 if all(result == 0 for result in results) else 1
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"pytest CI shard error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
