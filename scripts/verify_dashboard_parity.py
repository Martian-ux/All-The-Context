"""Verify committed dashboard assets match the production build."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from allthecontext.dashboard_parity import verify_dashboard_parity, write_parity_report
from allthecontext.release_manifest import ManifestError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--source-commit")
    parser.add_argument("--built-dist", type=Path)
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    try:
        source_commit = arguments.source_commit
        if source_commit is None:
            source_commit = subprocess.run(
                ["git", "-C", str(arguments.repository_root), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        report = verify_dashboard_parity(
            arguments.repository_root,
            source_commit=source_commit,
            build=arguments.built_dist is None,
            built_dist=arguments.built_dist,
        )
        if arguments.report is not None:
            write_parity_report(arguments.report, report)
            print(arguments.report)
        print(
            f"dashboard parity ok files={report.committed_files} "
            f"tree_sha256={report.committed_digest}"
        )
        return 0
    except (ManifestError, OSError, subprocess.SubprocessError) as exc:
        print(f"dashboard parity error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
