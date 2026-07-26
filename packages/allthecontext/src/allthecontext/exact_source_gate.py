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
NON_SUCCESS_CONCLUSIONS = frozenset(
    {
        "failure",
        "cancelled",
        "skipped",
        "neutral",
        "timed_out",
        "action_required",
        "stale",
        "startup_failure",
    }
)

# Exact nine-job CI matrix names from .github/workflows/ci.yml
REQUIRED_CI_MATRIX_JOBS = (
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

# Additional CI jobs that must be green on the same SHA (not matrix slots).
REQUIRED_SECURITY_PARITY_JOBS = (
    "Repository security gates",
    "Dashboard production asset parity",
)

# Full set that must succeed for an exact release candidate.
REQUIRED_CI_JOBS = REQUIRED_CI_MATRIX_JOBS + REQUIRED_SECURITY_PARITY_JOBS

MATRIX_EVIDENCE_ALLOWED_KEYS = frozenset(
    {
        "schema_version",
        "source_commit",
        "workflow_run_id",
        "workflow_name",
        "conclusion",
        "jobs",
        "required_jobs",
        "ok",
    }
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
        # Branch names, short SHAs, and merge-queue SHAs that differ from the
        # reviewed source commit never satisfy the gate.
        if not isinstance(head_sha, str) or head_sha != source_commit:
            continue
        if COMMIT.fullmatch(head_sha) is None:
            continue
        path = run.get("path")
        # Require the canonical CI workflow path when present; never accept a
        # differently named workflow that only reuses the display name "CI".
        if isinstance(path, str):
            if not path.endswith(workflow_file):
                continue
        elif run.get("name") != "CI":
            continue
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
    """Require every named job to have a completed success conclusion.

    Skipped, cancelled, neutral, timed-out, or failed required jobs never
    satisfy the gate. A partial job set is refused even if the workflow run
    conclusion is success.
    """

    required = list(required_jobs)
    by_name: dict[str, list[Mapping[str, Any]]] = {}
    for job in jobs:
        name = job.get("name")
        if isinstance(name, str):
            by_name.setdefault(name, []).append(job)

    successful_names: set[str] = set()
    refused: list[str] = []
    missing: list[str] = []
    for name in required:
        matches = by_name.get(name, [])
        if not matches:
            missing.append(name)
            continue
        any_success = False
        for job in matches:
            status = job.get("status")
            conclusion = job.get("conclusion")
            if status is not None and status != "completed":
                refused.append(f"{name}:{status}/{conclusion}")
                continue
            if conclusion == "success":
                any_success = True
            elif conclusion in NON_SUCCESS_CONCLUSIONS or conclusion is None:
                refused.append(f"{name}:{status or 'unknown'}/{conclusion or 'missing'}")
        if any_success:
            successful_names.add(name)
        elif name not in missing:
            missing.append(name)
    if missing or refused:
        detail_parts: list[str] = []
        if missing:
            detail_parts.append("missing_or_not_success=" + ", ".join(missing))
        if refused:
            detail_parts.append("non_success=" + ", ".join(sorted(set(refused))))
        raise ManifestError(
            "exact hosted matrix/security/parity jobs incomplete or not green: "
            + "; ".join(detail_parts)
        )
    return tuple(required)


def validate_matrix_evidence(
    value: Mapping[str, Any],
    *,
    source_commit: str,
) -> dict[str, Any]:
    """Validate durable matrix-evidence.json produced for a release candidate.

    Forged ``ok`` booleans, partial job lists, wrong source commits, and
    incomplete security/parity coverage are refused.
    """

    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("matrix evidence source commit must be a full lowercase SHA")
    unknown = sorted(set(value) - MATRIX_EVIDENCE_ALLOWED_KEYS)
    if unknown:
        raise ManifestError(f"matrix evidence has unknown fields: {', '.join(unknown)}")
    required_fields = {
        "schema_version",
        "source_commit",
        "workflow_run_id",
        "workflow_name",
        "conclusion",
        "jobs",
        "required_jobs",
        "ok",
    }
    if not required_fields.issubset(value):
        missing = sorted(required_fields - set(value))
        raise ManifestError(f"matrix evidence is missing fields: {', '.join(missing)}")
    if value.get("schema_version") != 1:
        raise ManifestError("matrix evidence schema_version must be 1")
    evidence_commit = value.get("source_commit")
    if not isinstance(evidence_commit, str) or evidence_commit != source_commit:
        raise ManifestError("matrix evidence source_commit does not match the candidate")
    if COMMIT.fullmatch(evidence_commit) is None:
        raise ManifestError("matrix evidence source_commit must be a full lowercase SHA")
    run_id = value.get("workflow_run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ManifestError("matrix evidence workflow_run_id must be a positive integer")
    workflow_name = value.get("workflow_name")
    if not isinstance(workflow_name, str) or not workflow_name.strip():
        raise ManifestError("matrix evidence workflow_name is invalid")
    if value.get("conclusion") != "success":
        raise ManifestError("matrix evidence conclusion must be success")
    if value.get("ok") is not True:
        raise ManifestError("matrix evidence ok must be the boolean true")
    jobs = value.get("jobs")
    required_declared = value.get("required_jobs")
    if not isinstance(jobs, list) or not jobs or any(not isinstance(item, str) for item in jobs):
        raise ManifestError("matrix evidence jobs must be a non-empty list of strings")
    if not isinstance(required_declared, list) or any(
        not isinstance(item, str) for item in required_declared
    ):
        raise ManifestError("matrix evidence required_jobs must be a list of strings")
    if set(required_declared) != set(REQUIRED_CI_JOBS) or len(required_declared) != len(
        REQUIRED_CI_JOBS
    ):
        raise ManifestError(
            "matrix evidence required_jobs does not match the frozen hosted gate set"
        )
    if set(jobs) < set(REQUIRED_CI_JOBS):
        missing = sorted(set(REQUIRED_CI_JOBS) - set(jobs))
        raise ManifestError(
            "matrix evidence jobs omit required matrix/security/parity slots: " + ", ".join(missing)
        )
    return dict(value)


def load_matrix_evidence(path: Path, *, source_commit: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"matrix evidence is not valid UTF-8 JSON: {path.name}") from exc
    if not isinstance(raw, dict):
        raise ManifestError("matrix evidence must be a JSON object")
    return validate_matrix_evidence(raw, source_commit=source_commit)


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
    if run.get("head_sha") != source_commit:
        raise ManifestError("selected CI run head_sha does not match the exact source commit")
    jobs_value = jobs_payload.get("jobs", []) if isinstance(jobs_payload, Mapping) else jobs_payload
    if not isinstance(jobs_value, list):
        raise ManifestError("GitHub workflow jobs payload is malformed")
    job_maps = [item for item in jobs_value if isinstance(item, dict)]
    # When jobs declare a run id, require they belong to the selected run so a
    # partial set from another SHA cannot be mixed in.
    selected_run_id = run.get("id")
    bound_jobs: list[dict[str, Any]] = []
    for job in job_maps:
        job_run_id = job.get("run_id")
        if job_run_id is not None and job_run_id != selected_run_id:
            raise ManifestError("job payload includes jobs from a different workflow run")
        bound_jobs.append(job)
    verified = verify_required_jobs(bound_jobs)
    if not isinstance(selected_run_id, int):
        raise ManifestError("GitHub workflow run id is missing")
    workflow_name = run.get("name")
    if not isinstance(workflow_name, str):
        workflow_name = "CI"
    evidence = MatrixEvidence(
        source_commit=source_commit,
        workflow_run_id=selected_run_id,
        workflow_name=workflow_name,
        conclusion="success",
        jobs=verified,
    )
    # Fail closed if the durable form would not validate.
    validate_matrix_evidence(evidence.as_dict(), source_commit=source_commit)
    return evidence


def write_matrix_evidence(path: Path, evidence: MatrixEvidence) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ManifestError(f"refusing to replace matrix evidence: {path.name}")
    payload = evidence.as_dict()
    validate_matrix_evidence(payload, source_commit=evidence.source_commit)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
