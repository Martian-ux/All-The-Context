"""Hosted Python and dashboard dependency vulnerability gates."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def audit_python(repository_root: Path) -> dict[str, object]:
    """Run the lock-installed pip-audit tool; never bootstrap a version range."""

    python = sys.executable
    try:
        version = importlib.metadata.version("pip-audit")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            "pip-audit is not installed in the active environment; "
            "install the reviewed dev lock via scripts/install_locked_python.py --extra dev"
        ) from exc
    # Audit the project's declared/locked dependency set, not the ambient
    # environment (which may contain unrelated global packages).
    completed = subprocess.run(
        [
            python,
            "-m",
            "pip_audit",
            str(repository_root),
            "--progress-spinner",
            "off",
            "--desc",
            "off",
        ],
        cwd=repository_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"pip-audit {version} failed (exit {completed.returncode}); "
            "fix or justify dependencies before candidate freeze"
        )
    return {
        "ecosystem": "python",
        "tool": "pip-audit",
        "tool_version": version,
        "ok": True,
    }


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
