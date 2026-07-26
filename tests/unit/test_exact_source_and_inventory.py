from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.component_inventory import build_component_inventory
from allthecontext.exact_source_gate import (
    CANONICAL_CI_WORKFLOW_NAME,
    CANONICAL_CI_WORKFLOW_PATH,
    REQUIRED_CI_JOBS,
    REQUIRED_CI_MATRIX_JOBS,
    REQUIRED_SECURITY_PARITY_JOBS,
    matrix_evidence_from_github,
    select_successful_ci_run,
    validate_matrix_evidence,
    verify_required_jobs,
)
from allthecontext.release_manifest import ManifestError

SOURCE = "c" * 40
OTHER = "d" * 40
ROOT = Path(__file__).resolve().parents[2]
RUN_ID = 11


def _success_job_records(
    *, run_id: int = RUN_ID, head_sha: str = SOURCE
) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "run_id": run_id,
            "head_sha": head_sha,
            "status": "completed",
            "conclusion": "success",
        }
        for name in REQUIRED_CI_JOBS
    ]


def _matrix_payload(
    *,
    source_commit: str = SOURCE,
    run_id: int = RUN_ID,
    ok: object = True,
    jobs: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    records = (
        jobs if jobs is not None else _success_job_records(run_id=run_id, head_sha=source_commit)
    )
    return {
        "schema_version": 1,
        "source_commit": source_commit,
        "workflow_path": CANONICAL_CI_WORKFLOW_PATH,
        "workflow_name": CANONICAL_CI_WORKFLOW_NAME,
        "workflow_run_id": run_id,
        "run_status": "completed",
        "run_conclusion": "success",
        "job_records": records,
        "required_jobs": list(REQUIRED_CI_JOBS),
        "ok": ok,
    }


def test_required_ci_jobs_cover_matrix_and_security_parity() -> None:
    assert len(REQUIRED_CI_MATRIX_JOBS) == 9
    assert len(REQUIRED_SECURITY_PARITY_JOBS) == 2
    assert len(REQUIRED_CI_JOBS) == 11
    assert "Repository security gates" in REQUIRED_SECURITY_PARITY_JOBS
    assert "Dashboard production asset parity" in REQUIRED_SECURITY_PARITY_JOBS


def test_select_successful_ci_run_and_jobs() -> None:
    runs = [
        {
            "id": 10,
            "head_sha": SOURCE,
            "path": CANONICAL_CI_WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "failure",
            "name": "CI",
        },
        {
            "id": RUN_ID,
            "head_sha": SOURCE,
            "path": CANONICAL_CI_WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
    ]
    selected = select_successful_ci_run(runs, source_commit=SOURCE)
    assert selected["id"] == RUN_ID
    jobs = _success_job_records()
    verified = verify_required_jobs(jobs, source_commit=SOURCE, workflow_run_id=RUN_ID)
    assert [record.name for record in verified] == list(REQUIRED_CI_JOBS)
    evidence = matrix_evidence_from_github(
        source_commit=SOURCE,
        runs_payload={"workflow_runs": runs},
        jobs_payload={"jobs": jobs, "total_count": len(jobs)},
    )
    payload = evidence.as_dict()
    assert payload["ok"] is True
    assert payload["workflow_path"] == CANONICAL_CI_WORKFLOW_PATH
    assert len(payload["job_records"]) == 11


def test_evil_suffix_workflow_path_never_satisfies() -> None:
    for path in (
        ".github/workflows/evilci.yml",
        ".github/workflows/not-ci.yml",
        "ci.yml",
        ".github/workflows/ci.yml.backup",
    ):
        runs = [
            {
                "id": 1,
                "head_sha": SOURCE,
                "path": path,
                "status": "completed",
                "conclusion": "success",
                "name": "CI",
            }
        ]
        with pytest.raises(ManifestError, match="no successful CI workflow run"):
            select_successful_ci_run(runs, source_commit=SOURCE)


def test_branch_short_wrong_sha_and_malformed_run_ids_fail() -> None:
    runs = [
        {
            "id": True,
            "head_sha": SOURCE,
            "path": CANONICAL_CI_WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
        {
            "id": 1,
            "head_sha": "main",
            "path": CANONICAL_CI_WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
        {
            "id": 2,
            "head_sha": SOURCE[:7],
            "path": CANONICAL_CI_WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
        {
            "id": 3,
            "head_sha": OTHER,
            "path": CANONICAL_CI_WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
        {
            "id": 4,
            "head_sha": SOURCE,
            "path": CANONICAL_CI_WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "success",
            "name": "Not CI",
        },
    ]
    with pytest.raises(ManifestError, match="no successful CI workflow run"):
        select_successful_ci_run(runs, source_commit=SOURCE)


def test_duplicate_required_job_names_fail() -> None:
    jobs = _success_job_records()
    jobs.append(dict(jobs[0]))
    with pytest.raises(ManifestError, match="duplicate"):
        verify_required_jobs(jobs, source_commit=SOURCE, workflow_run_id=RUN_ID)


def test_missing_run_id_or_head_sha_on_job_fails() -> None:
    jobs = _success_job_records()
    del jobs[0]["run_id"]
    with pytest.raises(ManifestError, match=r"missing_identity|incomplete"):
        verify_required_jobs(jobs, source_commit=SOURCE, workflow_run_id=RUN_ID)
    jobs = _success_job_records()
    del jobs[1]["head_sha"]
    with pytest.raises(ManifestError, match=r"missing_identity|incomplete"):
        verify_required_jobs(jobs, source_commit=SOURCE, workflow_run_id=RUN_ID)


def test_skipped_cancelled_neutral_required_jobs_fail() -> None:
    for conclusion in ("skipped", "cancelled", "neutral", "timed_out"):
        jobs = _success_job_records()
        jobs[0]["conclusion"] = conclusion
        with pytest.raises(ManifestError, match="incomplete or not green"):
            verify_required_jobs(jobs, source_commit=SOURCE, workflow_run_id=RUN_ID)


def test_incomplete_jobs_pagination_fails_closed() -> None:
    runs = [
        {
            "id": RUN_ID,
            "head_sha": SOURCE,
            "path": CANONICAL_CI_WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        }
    ]
    jobs = _success_job_records()
    with pytest.raises(ManifestError, match="incomplete"):
        matrix_evidence_from_github(
            source_commit=SOURCE,
            runs_payload={"workflow_runs": runs},
            jobs_payload={"jobs": jobs, "total_count": len(jobs) + 5},
        )


def test_jobs_from_other_run_or_sha_fail() -> None:
    runs = [
        {
            "id": RUN_ID,
            "head_sha": SOURCE,
            "path": CANONICAL_CI_WORKFLOW_PATH,
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        }
    ]
    jobs = _success_job_records(run_id=99)
    with pytest.raises(ManifestError, match=r"run_id|incomplete"):
        matrix_evidence_from_github(
            source_commit=SOURCE,
            runs_payload={"workflow_runs": runs},
            jobs_payload={"jobs": jobs, "total_count": len(jobs)},
        )


def test_forged_matrix_evidence_ok_and_name_only_jobs_fail() -> None:
    with pytest.raises(ManifestError, match=r"boolean true|recompute|ok"):
        validate_matrix_evidence(_matrix_payload(ok="true"), source_commit=SOURCE)
    with pytest.raises(ManifestError, match=r"unknown fields|missing fields|job_records"):
        validate_matrix_evidence(
            {
                "schema_version": 1,
                "source_commit": SOURCE,
                "workflow_run_id": RUN_ID,
                "workflow_name": "CI",
                "conclusion": "success",
                "jobs": list(REQUIRED_CI_JOBS),
                "required_jobs": list(REQUIRED_CI_JOBS),
                "ok": True,
            },
            source_commit=SOURCE,
        )
    payload = _matrix_payload()
    payload["job_records"] = payload["job_records"][:9]
    with pytest.raises(ManifestError, match=r"count|omit|incomplete"):
        validate_matrix_evidence(payload, source_commit=SOURCE)
    payload = _matrix_payload()
    payload["schema_version"] = True
    with pytest.raises(ManifestError, match="schema_version"):
        validate_matrix_evidence(payload, source_commit=SOURCE)
    payload = _matrix_payload()
    payload["workflow_run_id"] = True
    with pytest.raises(ManifestError, match="workflow_run_id"):
        validate_matrix_evidence(payload, source_commit=SOURCE)


def test_valid_matrix_evidence_round_trip() -> None:
    validated = validate_matrix_evidence(_matrix_payload(), source_commit=SOURCE)
    assert validated["ok"] is True
    assert validated["job_records"][0]["name"] == REQUIRED_CI_JOBS[0]


def test_component_inventory_from_locks() -> None:
    inventory = build_component_inventory(
        ROOT,
        source_commit=SOURCE,
        version="0.1.0-beta.1",
    )
    assert inventory["component_count"] >= 2
    ecosystems = {item["ecosystem"] for item in inventory["components"]}
    assert ecosystems == {"python", "npm"}
    assert all(item["locked"] is True for item in inventory["components"])
    assert "uv.lock" in inventory["locks"]
    assert inventory["source_commit"] == SOURCE
