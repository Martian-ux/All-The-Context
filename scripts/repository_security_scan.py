"""Run content-free repository, history, and artifact security scans."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from allthecontext.repository_security import (
    SecurityScanError,
    require_clean,
    scan_artifact_directory,
    scan_committed_tree,
    scan_git_history,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _source_commit(repository_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--scope",
        choices=("tree", "history", "artifacts", "all"),
        default="all",
    )
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--source-commit")
    parser.add_argument("--report", type=Path, help="optional content-free JSON report path")
    parser.add_argument(
        "--allow-absolute-paths",
        action="store_true",
        help="disable absolute developer-path detection (not for release candidates)",
    )
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    root = arguments.repository_root.resolve()
    try:
        source_commit = arguments.source_commit or _source_commit(root)
        reports = []
        if arguments.scope in {"tree", "all"}:
            report = scan_committed_tree(
                root,
                scope="tree",
                source_commit=source_commit,
                allow_absolute_paths=arguments.allow_absolute_paths,
            )
            require_clean(report)
            reports.append(report)
            print(
                f"tree scan ok files={report.files_examined} findings=0 commit={source_commit[:12]}"
            )
        if arguments.scope in {"history", "all"}:
            report = scan_git_history(root, source_commit=source_commit)
            require_clean(report)
            reports.append(report)
            print(
                f"history scan ok blobs={report.files_examined} findings=0 "
                f"commit={source_commit[:12]}"
            )
        if arguments.scope in {"artifacts", "all"} and arguments.artifact_dir is not None:
            report = scan_artifact_directory(
                arguments.artifact_dir,
                source_commit=source_commit,
            )
            require_clean(report)
            reports.append(report)
            print(
                f"artifact scan ok files={report.files_examined} findings=0 "
                f"commit={source_commit[:12]}"
            )
        elif arguments.scope == "artifacts" and arguments.artifact_dir is None:
            raise SecurityScanError("--artifact-dir is required for artifact scans")
        if arguments.report is not None:
            payload = {
                "schema_version": 1,
                "source_commit": source_commit,
                "ok": all(item.ok for item in reports),
                "reports": [item.as_dict() for item in reports],
            }
            arguments.report.parent.mkdir(parents=True, exist_ok=True)
            if arguments.report.exists():
                raise SecurityScanError(
                    f"refusing to replace existing scan report: {arguments.report.name}"
                )
            arguments.report.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            print(arguments.report)
        return 0
    except (SecurityScanError, OSError, subprocess.SubprocessError) as exc:
        print(f"repository security scan error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
