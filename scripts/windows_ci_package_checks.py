"""Overlap read-only built-byte packaging with the stock Windows CI lifecycle.

Each task invokes the maintained scripts with their original gates. Packaging
reads pristine bytes and writes only dist/platform and dist/release; lifecycle
owns its existing disposable installs and diagnostics. Drain both before exit.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter

if __package__:
    from scripts.verify_reproducible_build import project_version
else:
    from verify_reproducible_build import project_version

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = [
    "--artifact-root",
    ".",
    "--provenance-manifest",
    "dist/native-build-provenance/native-build-provenance-v1.json",
    "--provenance-checksum",
    "dist/native-build-provenance/native-build-provenance-v1.json.sha256",
]


def run(script: str, *arguments: str) -> None:
    started = perf_counter()
    try:
        subprocess.run(
            [sys.executable, str(ROOT / "scripts" / script), *arguments], cwd=ROOT, check=True
        )
    finally:
        print(f"Windows CI package check {script}: {perf_counter() - started:.2f}s", flush=True)


def packaged_checks() -> None:
    run("smoke_desktop_artifact.py", *PROVENANCE)
    run("smoke_packaged_recovery.py", *PROVENANCE)


def first_run() -> None:
    run("smoke_packaged_first_run.py", *PROVENANCE)
    # Preserve successful lifecycle diagnostics even if the other branch fails.
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write("lifecycle_success=true\n")


def prepare() -> None:
    version = project_version(ROOT)
    target = ["--version", version, "--platform", "windows", "--architecture", "x86_64"]
    run("package_desktop.py", *target, "--output-dir", "dist/platform")
    run(
        "smoke_platform_package.py",
        "--directory",
        "dist/platform",
        "--platform",
        "windows",
        "--architecture",
        "x86_64",
    )
    run(
        "build_release_assets.py",
        "--source",
        "dist/desktop/AllTheContextSetup.exe",
        "--output-dir",
        "dist/release",
        *target,
    )


def lifecycle() -> None:
    packaged_checks()
    first_run()


def packaged_checks_then_prepare() -> None:
    packaged_checks()
    prepare()


def main(*, overlap_first_run: bool = False) -> int:
    # First-run has its own install, settings and process paths.
    primary, ancillary = (
        (first_run, packaged_checks_then_prepare) if overlap_first_run else (lifecycle, prepare)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        checks = [pool.submit(primary), pool.submit(ancillary)]
        failed = False
        for check in checks:
            try:
                check.result()
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                print(f"Windows CI package checks failed: {exc}", file=sys.stderr)
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overlap-first-run", action="store_true")
    raise SystemExit(main(overlap_first_run=parser.parse_args().overlap_first_run))
