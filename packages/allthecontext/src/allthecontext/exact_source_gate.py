"""Exact-SHA source quality and hosted-matrix gate helpers."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .release_manifest import ManifestError

COMMIT = re.compile(r"[0-9a-f]{40}")

# Exact nine-job CI matrix names from .github/workflows/ci.yml
REQUIRED_CI_JOBS = (
    "Python 3.12 - windows-latest",
    "Python 3.12 - macos-latest",
    "Python 3.12 - ubuntu-latest",
    "Dashboard - Node 20",
    "Dashboard - Node 22",
    "Desktop artifact - windows-latest",
    "Desktop artifact - macos-26",
    "Desktop artifact - macos-26-intel",
    "Desktop artifact - ubuntu-latest",
)

LOCAL_QUALITY_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("python", "-m", "ruff", "format", "--check", "."),
    ("python", "-m", "ruff", "check", "."),
    ("python", "-m", "mypy", "packages/allthecontext/src"),
    ("python", "-m", "pytest"),
    ("python", "scripts/check_docs.py"),
)


@dataclass(frozen=True)
class MatrixEvidence:
    source_commit: str
    workflow_run_id: int
    workflow_name: str
    conclusion: str
    jobs: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "source_commit": self.source_commit,
            "workflow_run_id": self.workflow_run_id,
            "workflow_name": self.workflow_name,
            "conclusion": self.conclusion,
            "jobs": list(self.jobs),
            "required_jobs": list(REQUIRED_CI_JOBS),
            "ok": self.conclusion == "success" and set(self.jobs) >= set(REQUIRED_CI_JOBS),
        }


def run_local_quality_gates(repository_root: Path) -> list[dict[str, Any]]:
    """Run the exact local source quality commands; fail closed on any non-zero exit."""

    repository_root = repository_root.resolve()
    results: list[dict[str, Any]] = []
    for command in LOCAL_QUALITY_COMMANDS:
        completed = subprocess.run(
            list(command),
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
        entry = {
            "command": list(command),
            "exit_code": completed.returncode,
            "ok": completed.returncode == 0,
        }
        results.append(entry)
        if completed.returncode != 0:
            # Never dump full test output that might contain paths/context; summarize only.
            raise ManifestError(
                "exact source quality gate failed: "
                + " ".join(command)
                + f" (exit {completed.returncode})"
            )
    return results


def select_successful_ci_run(
    runs: Sequence[Mapping[str, Any]],
    *,
    source_commit: str,
    workflow_file: str = "ci.yml",
) -> Mapping[str, Any]:
    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("exact source commit must be a full lowercase SHA")
    candidates: list[Mapping[str, Any]] = []
    for run in runs:
        head_sha = run.get("head_sha")
        path = run.get("path")
        if head_sha != source_commit:
            continue
        if (isinstance(path, str) and path.endswith(workflow_file)) or run.get("name") == "CI":
            candidates.append(run)
    successful = [
        run
        for run in candidates
        if run.get("status") == "completed" and run.get("conclusion") == "success"
    ]
    if not successful:
        raise ManifestError(
            "no successful CI workflow run is available for the exact source commit"
        )

    # Prefer the newest successful run.
    def sort_key(run: Mapping[str, Any]) -> int:
        run_id = run.get("id")
        return int(run_id) if isinstance(run_id, int) else 0

    return max(successful, key=sort_key)


def verify_required_jobs(
    jobs: Sequence[Mapping[str, Any]],
    *,
    required_jobs: Iterable[str] = REQUIRED_CI_JOBS,
) -> tuple[str, ...]:
    required = list(required_jobs)
    successful_names: set[str] = set()
    for job in jobs:
        name = job.get("name")
        conclusion = job.get("conclusion")
        if isinstance(name, str) and conclusion == "success":
            successful_names.add(name)
    missing = [name for name in required if name not in successful_names]
    if missing:
        raise ManifestError(
            "exact nine-job hosted matrix is incomplete or not green: " + ", ".join(missing)
        )
    return tuple(required)


def matrix_evidence_from_github(
    *,
    source_commit: str,
    runs_payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    jobs_payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> MatrixEvidence:
    if isinstance(runs_payload, Mapping):
        runs_value = runs_payload.get("workflow_runs", runs_payload.get("runs", []))
    else:
        runs_value = runs_payload
    if not isinstance(runs_value, list):
        raise ManifestError("GitHub workflow runs payload is malformed")
    run = select_successful_ci_run(
        [item for item in runs_value if isinstance(item, dict)],
        source_commit=source_commit,
    )
    jobs_value = jobs_payload.get("jobs", []) if isinstance(jobs_payload, Mapping) else jobs_payload
    if not isinstance(jobs_value, list):
        raise ManifestError("GitHub workflow jobs payload is malformed")
    job_maps = [item for item in jobs_value if isinstance(item, dict)]
    verified = verify_required_jobs(job_maps)
    run_id = run.get("id")
    if not isinstance(run_id, int):
        raise ManifestError("GitHub workflow run id is missing")
    workflow_name = run.get("name")
    if not isinstance(workflow_name, str):
        workflow_name = "CI"
    return MatrixEvidence(
        source_commit=source_commit,
        workflow_run_id=run_id,
        workflow_name=workflow_name,
        conclusion="success",
        jobs=verified,
    )


def write_matrix_evidence(path: Path, evidence: MatrixEvidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ManifestError(f"refusing to replace matrix evidence: {path.name}")
    path.write_text(
        json.dumps(evidence.as_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
