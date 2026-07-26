"""Hosted Python and dashboard dependency vulnerability gates."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
# Keep the audited tool pin aligned with pyproject optional-dependencies.dev.
REQUIRED_PIP_AUDIT_VERSION = "2.10.1"
# V1 scopes that participate in release composition and packaging.
PYTHON_AUDIT_EXTRAS = ("dev", "packaging")


def _load_install_locked() -> ModuleType:
    path = Path(__file__).resolve().parent / "install_locked_python.py"
    spec = importlib.util.spec_from_file_location("install_locked_python", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _export_frozen_requirements(repository_root: Path, output: Path) -> None:
    """Write a hashed requirements export of the reviewed uv.lock (no re-resolve)."""

    install_locked = _load_install_locked()
    uv = install_locked.ensure_pinned_uv(sys.executable)
    command = [
        uv,
        "export",
        "--frozen",
        "--no-emit-project",
        "--output-file",
        str(output),
    ]
    for extra in PYTHON_AUDIT_EXTRAS:
        command.extend(["--extra", extra])
    subprocess.run(
        command,
        cwd=repository_root,
        check=True,
        capture_output=True,
        text=True,
    )
    text = output.read_text(encoding="utf-8")
    if "--hash=" not in text and "sha256:" not in text:
        raise RuntimeError("uv export did not produce hash-pinned requirements for audit")


def pip_audit_command(python: str, requirements: Path) -> list[str]:
    """Exact pip-audit argv for the frozen hashed export (no dependency resolution)."""

    return [
        python,
        "-m",
        "pip_audit",
        "--requirement",
        str(requirements),
        "--disable-pip",
        "--progress-spinner",
        "off",
        "--desc",
        "off",
    ]


def audit_python(repository_root: Path) -> dict[str, object]:
    """Audit the exact frozen lock export; never bootstrap tools or re-resolve ranges."""

    python = sys.executable
    try:
        version = importlib.metadata.version("pip-audit")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            "pip-audit is not installed in the active environment; "
            "install the reviewed dev lock via scripts/install_locked_python.py --extra dev"
        ) from exc
    if version != REQUIRED_PIP_AUDIT_VERSION:
        raise RuntimeError(
            f"pip-audit {version} is not the reviewed pin {REQUIRED_PIP_AUDIT_VERSION}; "
            "install the reviewed dev lock via scripts/install_locked_python.py --extra dev"
        )
    lock = repository_root / "uv.lock"
    if not lock.is_file():
        raise RuntimeError("uv.lock is missing; cannot audit the reviewed lock")

    with tempfile.TemporaryDirectory(prefix="atc-dep-audit-") as temporary_name:
        requirements = Path(temporary_name) / "locked-requirements.txt"
        _export_frozen_requirements(repository_root, requirements)
        command = pip_audit_command(python, requirements)
        completed = subprocess.run(
            command,
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(
            f"pip-audit {version} failed (exit {completed.returncode}) while auditing "
            f"the frozen uv.lock export{suffix}"
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
