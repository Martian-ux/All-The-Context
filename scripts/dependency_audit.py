"""Hosted Python and dashboard dependency vulnerability gates."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def audit_python(repository_root: Path) -> dict[str, object]:
    """Run pip-audit against the active environment; fail on known vulnerabilities."""

    python = sys.executable
    # Ensure pip-audit is available without adding it as a runtime dependency.
    subprocess.run(
        [python, "-m", "pip", "install", "--quiet", "pip-audit>=2.7,<3"],
        check=True,
    )
    completed = subprocess.run(
        [
            python,
            "-m",
            "pip_audit",
            "--progress-spinner",
            "off",
            "--strict",
            "--desc",
            "off",
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    # pip-audit prints vulnerability details; keep stdout/stderr out of our summary.
    if completed.returncode != 0:
        raise RuntimeError(
            f"pip-audit failed (exit {completed.returncode}); "
            "fix or justify dependencies before candidate freeze"
        )
    return {"ecosystem": "python", "tool": "pip-audit", "ok": True}


def audit_dashboard(repository_root: Path) -> dict[str, object]:
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required for dashboard dependency audit")
    dashboard = repository_root / "apps" / "dashboard"
    subprocess.run([npm, "ci"], cwd=dashboard, check=True, capture_output=True, text=True)
    completed = subprocess.run(
        [npm, "audit", "--audit-level=high", "--json"],
        cwd=dashboard,
        check=False,
        capture_output=True,
        text=True,
    )
    # npm audit exit 1 means vulnerabilities at or above the level.
    if completed.returncode not in {0, 1}:
        raise RuntimeError(f"npm audit failed unexpectedly (exit {completed.returncode})")
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError("npm audit did not return JSON") from exc
    metadata = payload.get("metadata", {}) if isinstance(payload, dict) else {}
    vulns = metadata.get("vulnerabilities", {}) if isinstance(metadata, dict) else {}
    high = int(vulns.get("high", 0) or 0) if isinstance(vulns, dict) else 0
    critical = int(vulns.get("critical", 0) or 0) if isinstance(vulns, dict) else 0
    if completed.returncode != 0 or high or critical:
        raise RuntimeError(f"npm audit reported high={high} critical={critical} vulnerabilities")
    return {
        "ecosystem": "npm",
        "tool": "npm-audit",
        "ok": True,
        "high": high,
        "critical": critical,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument(
        "--ecosystem",
        choices=("python", "dashboard", "all"),
        default="all",
    )
    parser.add_argument("--report", type=Path)
    arguments = parser.parse_args()
    root = arguments.repository_root.resolve()
    results: list[dict[str, object]] = []
    try:
        if arguments.ecosystem in {"python", "all"}:
            results.append(audit_python(root))
        if arguments.ecosystem in {"dashboard", "all"}:
            results.append(audit_dashboard(root))
        payload = {"schema_version": 1, "ok": True, "results": results}
        if arguments.report is not None:
            arguments.report.parent.mkdir(parents=True, exist_ok=True)
            if arguments.report.exists():
                raise RuntimeError(f"refusing to replace audit report: {arguments.report.name}")
            arguments.report.write_text(
                json.dumps(payload, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(arguments.report)
        print(json.dumps({"ok": True, "audits": len(results)}, sort_keys=True))
        return 0
    except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
        print(f"dependency audit error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
