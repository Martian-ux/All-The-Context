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
CANONICAL_CI_WORKFLOW_PATH = ".github/workflows/ci.yml"
CANONICAL_CI_WORKFLOW_NAME = "CI"
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
        "workflow_path",
        "workflow_name",
        "workflow_run_id",
        "run_status",
        "run_conclusion",
        "job_records",
        "required_jobs",
        "ok",
    }
)
JOB_RECORD_ALLOWED_KEYS = frozenset(
    {
        "name",
        "run_id",
        "head_sha",
        "status",
        "conclusion",
    }
)

LOCAL_QUALITY_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("python", "-m", "ruff", "format", "--check", "."),
    ("python", "-m", "ruff", "check", "."),
    ("python", "-m", "mypy", "packages/allthecontext/src"),
    ("python", "-m", "pytest"),
    ("python", "scripts/check_docs.py"),
)


def _require_positive_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ManifestError(f"{label} must be a positive integer")
    return value


def _normalize_workflow_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_canonical_ci_path(path: object) -> bool:
    return isinstance(path, str) and _normalize_workflow_path(path) == CANONICAL_CI_WORKFLOW_PATH


@dataclass(frozen=True)
class JobRecord:
    name: str
    run_id: int
    head_sha: str
    status: str
    conclusion: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "run_id": self.run_id,
            "head_sha": self.head_sha,
            "status": self.status,
            "conclusion": self.conclusion,
        }


@dataclass(frozen=True)
class MatrixEvidence:
    source_commit: str
    workflow_run_id: int
    workflow_name: str
    workflow_path: str
    run_status: str
    run_conclusion: str
    job_records: tuple[JobRecord, ...]

    def as_dict(self) -> dict[str, Any]:
        records = [record.as_dict() for record in self.job_records]
        ok = _compute_matrix_ok(
            source_commit=self.source_commit,
            workflow_path=self.workflow_path,
            workflow_name=self.workflow_name,
            workflow_run_id=self.workflow_run_id,
            run_status=self.run_status,
            run_conclusion=self.run_conclusion,
            job_records=records,
            required_jobs=list(REQUIRED_CI_JOBS),
        )
        return {
            "schema_version": 1,
            "source_commit": self.source_commit,
            "workflow_path": self.workflow_path,
            "workflow_name": self.workflow_name,
            "workflow_run_id": self.workflow_run_id,
            "run_status": self.run_status,
            "run_conclusion": self.run_conclusion,
            "job_records": records,
            "required_jobs": list(REQUIRED_CI_JOBS),
            "ok": ok,
        }


def _compute_matrix_ok(
    *,
    source_commit: str,
    workflow_path: str,
    workflow_name: str,
    workflow_run_id: int,
    run_status: str,
    run_conclusion: str,
    job_records: Sequence[Mapping[str, Any]],
    required_jobs: Sequence[str],
) -> bool:
    if COMMIT.fullmatch(source_commit) is None:
        return False
    if _normalize_workflow_path(workflow_path) != CANONICAL_CI_WORKFLOW_PATH:
        return False
    if workflow_name != CANONICAL_CI_WORKFLOW_NAME:
        return False
    if (
        isinstance(workflow_run_id, bool)
        or not isinstance(workflow_run_id, int)
        or workflow_run_id <= 0
    ):
        return False
    if run_status != "completed" or run_conclusion != "success":
        return False
    if list(required_jobs) != list(REQUIRED_CI_JOBS):
        return False
    if len(job_records) != len(REQUIRED_CI_JOBS):
        return False
    seen_names: set[str] = set()
    for record in job_records:
        name = record.get("name")
        if not isinstance(name, str) or name not in REQUIRED_CI_JOBS:
            return False
        if name in seen_names:
            return False
        seen_names.add(name)
        if record.get("run_id") != workflow_run_id:
            return False
        if record.get("head_sha") != source_commit:
            return False
        if record.get("status") != "completed" or record.get("conclusion") != "success":
            return False
    return seen_names == set(REQUIRED_CI_JOBS)


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
    workflow_path: str = CANONICAL_CI_WORKFLOW_PATH,
    workflow_name: str = CANONICAL_CI_WORKFLOW_NAME,
) -> Mapping[str, Any]:
    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("exact source commit must be a full lowercase SHA")
    expected_path = _normalize_workflow_path(workflow_path)
    if expected_path != CANONICAL_CI_WORKFLOW_PATH:
        raise ManifestError("exact source gate requires the canonical CI workflow path")
    candidates: list[Mapping[str, Any]] = []
    for run in runs:
        run_id = run.get("id")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            continue
        head_sha = run.get("head_sha")
        # Branch names, short SHAs, and merge-queue SHAs that differ from the
        # reviewed source commit never satisfy the gate.
        if not isinstance(head_sha, str) or head_sha != source_commit:
            continue
        if COMMIT.fullmatch(head_sha) is None:
            continue
        path = run.get("path")
        if not _is_canonical_ci_path(path):
            continue
        name = run.get("name")
        if name is not None and name != workflow_name:
            continue
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status != "completed" or conclusion != "success":
            continue
        candidates.append(run)
    if not candidates:
        raise ManifestError(
            "no successful CI workflow run is available for the exact source commit"
        )

    def sort_key(run: Mapping[str, Any]) -> int:
        run_id = run.get("id")
        return int(run_id) if isinstance(run_id, int) and not isinstance(run_id, bool) else 0

    return max(candidates, key=sort_key)


def _require_jobs_payload_complete(
    jobs_payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if isinstance(jobs_payload, Mapping):
        jobs_value = jobs_payload.get("jobs", [])
        total_count = jobs_payload.get("total_count")
    else:
        jobs_value = jobs_payload
        total_count = None
    if not isinstance(jobs_value, list):
        raise ManifestError("GitHub workflow jobs payload is malformed")
    job_maps = [item for item in jobs_value if isinstance(item, dict)]
    if len(job_maps) != len(jobs_value):
        raise ManifestError("GitHub workflow jobs payload contains non-object job entries")
    if total_count is not None:
        if isinstance(total_count, bool) or not isinstance(total_count, int) or total_count < 0:
            raise ManifestError("GitHub workflow jobs total_count is malformed")
        if total_count != len(job_maps):
            raise ManifestError(
                "GitHub workflow jobs payload is incomplete "
                f"(total_count={total_count}, returned={len(job_maps)})"
            )
    return job_maps


def verify_required_jobs(
    jobs: Sequence[Mapping[str, Any]],
    *,
    source_commit: str,
    workflow_run_id: int,
    required_jobs: Iterable[str] = REQUIRED_CI_JOBS,
) -> tuple[JobRecord, ...]:
    """Require exactly one completed-success record per required job name.

    Every job must bind run_id and head_sha to the selected run/source commit.
    Duplicate required names, shadow re-runs, skipped/cancelled/neutral, and
    omitted identity fields never satisfy the gate.
    """

    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("exact source commit must be a full lowercase SHA")
    run_id = _require_positive_int(workflow_run_id, "workflow run id")
    required = list(required_jobs)
    # Callers may pass the frozen set; order may be the canonical tuple.
    if set(required) != set(REQUIRED_CI_JOBS) or len(required) != len(REQUIRED_CI_JOBS):
        raise ManifestError("required job set does not match the frozen hosted gate")

    by_name: dict[str, list[Mapping[str, Any]]] = {}
    extra_names: list[str] = []
    for job in jobs:
        name = job.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ManifestError("hosted job is missing a name")
        if name not in REQUIRED_CI_JOBS:
            # Non-required jobs may exist on the run, but required names must be unique.
            continue
        by_name.setdefault(name, []).append(job)

    records: list[JobRecord] = []
    refused: list[str] = []
    missing: list[str] = []
    for name in REQUIRED_CI_JOBS:
        matches = by_name.get(name, [])
        if not matches:
            missing.append(name)
            continue
        if len(matches) != 1:
            refused.append(f"{name}:duplicate")
            continue
        job = matches[0]
        job_run_id = job.get("run_id")
        job_head = job.get("head_sha")
        status = job.get("status")
        conclusion = job.get("conclusion")
        if job_run_id is None or job_head is None or status is None or conclusion is None:
            refused.append(f"{name}:missing_identity")
            continue
        if isinstance(job_run_id, bool) or not isinstance(job_run_id, int) or job_run_id != run_id:
            refused.append(f"{name}:run_id_mismatch")
            continue
        if not isinstance(job_head, str) or job_head != source_commit:
            refused.append(f"{name}:head_sha_mismatch")
            continue
        if status != "completed" or conclusion != "success":
            refused.append(f"{name}:{status}/{conclusion}")
            continue
        if conclusion in NON_SUCCESS_CONCLUSIONS:
            refused.append(f"{name}:{status}/{conclusion}")
            continue
        records.append(
            JobRecord(
                name=name,
                run_id=run_id,
                head_sha=source_commit,
                status="completed",
                conclusion="success",
            )
        )
    if missing or refused or extra_names:
        detail_parts: list[str] = []
        if missing:
            detail_parts.append("missing=" + ", ".join(missing))
        if refused:
            detail_parts.append("refused=" + ", ".join(sorted(set(refused))))
        raise ManifestError(
            "exact hosted matrix/security/parity jobs incomplete or not green: "
            + "; ".join(detail_parts)
        )
    if len(records) != len(REQUIRED_CI_JOBS):
        raise ManifestError("exact hosted matrix/security/parity jobs incomplete or not green")
    return tuple(records)


def validate_matrix_evidence(
    value: Mapping[str, Any],
    *,
    source_commit: str,
) -> dict[str, Any]:
    """Validate durable matrix-evidence.json produced for a release candidate.

    Stored ``ok`` is recomputed from primitive run/job records and never trusted
    as an independent authority.
    """

    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("matrix evidence source commit must be a full lowercase SHA")
    unknown = sorted(set(value) - MATRIX_EVIDENCE_ALLOWED_KEYS)
    if unknown:
        raise ManifestError(f"matrix evidence has unknown fields: {', '.join(unknown)}")
    required_fields = {
        "schema_version",
        "source_commit",
        "workflow_path",
        "workflow_name",
        "workflow_run_id",
        "run_status",
        "run_conclusion",
        "job_records",
        "required_jobs",
        "ok",
    }
    if not required_fields.issubset(value):
        missing = sorted(required_fields - set(value))
        raise ManifestError(f"matrix evidence is missing fields: {', '.join(missing)}")
    schema_version = value.get("schema_version")
    if isinstance(schema_version, bool) or schema_version != 1:
        raise ManifestError("matrix evidence schema_version must be integer 1")
    evidence_commit = value.get("source_commit")
    if not isinstance(evidence_commit, str) or evidence_commit != source_commit:
        raise ManifestError("matrix evidence source_commit does not match the candidate")
    if COMMIT.fullmatch(evidence_commit) is None:
        raise ManifestError("matrix evidence source_commit must be a full lowercase SHA")
    workflow_path = value.get("workflow_path")
    if not isinstance(workflow_path, str) or _normalize_workflow_path(workflow_path) != (
        CANONICAL_CI_WORKFLOW_PATH
    ):
        raise ManifestError("matrix evidence workflow_path must be .github/workflows/ci.yml")
    workflow_name = value.get("workflow_name")
    if workflow_name != CANONICAL_CI_WORKFLOW_NAME:
        raise ManifestError("matrix evidence workflow_name must be CI")
    run_id = value.get("workflow_run_id")
    if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
        raise ManifestError("matrix evidence workflow_run_id must be a positive integer")
    if value.get("run_status") != "completed":
        raise ManifestError("matrix evidence run_status must be completed")
    if value.get("run_conclusion") != "success":
        raise ManifestError("matrix evidence run_conclusion must be success")
    required_declared = value.get("required_jobs")
    if not isinstance(required_declared, list) or any(
        not isinstance(item, str) for item in required_declared
    ):
        raise ManifestError("matrix evidence required_jobs must be a list of strings")
    if list(required_declared) != list(REQUIRED_CI_JOBS):
        raise ManifestError(
            "matrix evidence required_jobs does not match the frozen hosted gate set"
        )
    job_records = value.get("job_records")
    if not isinstance(job_records, list) or not job_records:
        raise ManifestError("matrix evidence job_records must be a non-empty list")
    if len(job_records) != len(REQUIRED_CI_JOBS):
        raise ManifestError("matrix evidence job_records count must equal the frozen gate set")
    seen_names: set[str] = set()
    normalized_records: list[dict[str, Any]] = []
    for item in job_records:
        if not isinstance(item, dict):
            raise ManifestError("matrix evidence job_records entries must be objects")
        unknown_job = sorted(set(item) - JOB_RECORD_ALLOWED_KEYS)
        if unknown_job:
            raise ManifestError(
                "matrix evidence job record has unknown fields: " + ", ".join(unknown_job)
            )
        if set(item) != JOB_RECORD_ALLOWED_KEYS:
            raise ManifestError("matrix evidence job record is missing required fields")
        name = item.get("name")
        if not isinstance(name, str) or name not in REQUIRED_CI_JOBS:
            raise ManifestError("matrix evidence job record name is not a required hosted job")
        if name in seen_names:
            raise ManifestError(f"matrix evidence contains duplicate job record: {name}")
        seen_names.add(name)
        job_run_id = item.get("run_id")
        if isinstance(job_run_id, bool) or not isinstance(job_run_id, int) or job_run_id != run_id:
            raise ManifestError("matrix evidence job run_id must match the selected workflow run")
        job_head = item.get("head_sha")
        if not isinstance(job_head, str) or job_head != source_commit:
            raise ManifestError("matrix evidence job head_sha must match the source commit")
        if item.get("status") != "completed" or item.get("conclusion") != "success":
            raise ManifestError(
                f"matrix evidence job {name} is not completed success "
                f"({item.get('status')}/{item.get('conclusion')})"
            )
        normalized_records.append(
            {
                "name": name,
                "run_id": job_run_id,
                "head_sha": job_head,
                "status": "completed",
                "conclusion": "success",
            }
        )
    if seen_names != set(REQUIRED_CI_JOBS):
        missing = sorted(set(REQUIRED_CI_JOBS) - seen_names)
        raise ManifestError(
            "matrix evidence job_records omit required matrix/security/parity slots: "
            + ", ".join(missing)
        )
    recomputed_ok = _compute_matrix_ok(
        source_commit=source_commit,
        workflow_path=str(workflow_path),
        workflow_name=str(workflow_name),
        workflow_run_id=run_id,
        run_status="completed",
        run_conclusion="success",
        job_records=normalized_records,
        required_jobs=list(REQUIRED_CI_JOBS),
    )
    if not recomputed_ok:
        raise ManifestError("matrix evidence primitives do not recompute to ok")
    if value.get("ok") is not True:
        raise ManifestError("matrix evidence ok must be the boolean true after recompute")
    if value.get("ok") is not recomputed_ok:
        raise ManifestError("matrix evidence ok does not match recomputed primitives")
    # Deterministic order matches the frozen required-job tuple.
    ordered = sorted(
        normalized_records,
        key=lambda record: list(REQUIRED_CI_JOBS).index(str(record["name"])),
    )
    result = dict(value)
    result["job_records"] = ordered
    result["required_jobs"] = list(REQUIRED_CI_JOBS)
    result["ok"] = True
    return result


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
    if not _is_canonical_ci_path(run.get("path")):
        raise ManifestError("selected CI run path is not the canonical workflow")
    selected_run_id = run.get("id")
    if isinstance(selected_run_id, bool) or not isinstance(selected_run_id, int):
        raise ManifestError("GitHub workflow run id is missing")
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        raise ManifestError("selected CI run is not completed success")
    job_maps = _require_jobs_payload_complete(jobs_payload)
    job_records = verify_required_jobs(
        job_maps,
        source_commit=source_commit,
        workflow_run_id=selected_run_id,
    )
    workflow_name = run.get("name")
    if workflow_name is None:
        workflow_name = CANONICAL_CI_WORKFLOW_NAME
    if workflow_name != CANONICAL_CI_WORKFLOW_NAME:
        raise ManifestError("selected CI run name is not CI")
    evidence = MatrixEvidence(
        source_commit=source_commit,
        workflow_run_id=selected_run_id,
        workflow_name=CANONICAL_CI_WORKFLOW_NAME,
        workflow_path=CANONICAL_CI_WORKFLOW_PATH,
        run_status="completed",
        run_conclusion="success",
        job_records=job_records,
    )
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
