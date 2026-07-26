from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.component_inventory import build_component_inventory
from allthecontext.exact_source_gate import (
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


def _success_jobs() -> list[dict[str, str]]:
    return [
        {"name": name, "conclusion": "success", "status": "completed"} for name in REQUIRED_CI_JOBS
    ]


def test_required_ci_jobs_cover_matrix_and_security_parity() -> None:
    assert len(REQUIRED_CI_MATRIX_JOBS) == 9
    assert len(REQUIRED_SECURITY_PARITY_JOBS) == 2
    assert len(REQUIRED_CI_JOBS) == 11
    assert "Python 3.12 - windows-latest" in REQUIRED_CI_MATRIX_JOBS
    assert "Dashboard - Node 22" in REQUIRED_CI_MATRIX_JOBS
    assert "Desktop artifact - macos-26-intel" in REQUIRED_CI_MATRIX_JOBS
    assert "Repository security gates" in REQUIRED_SECURITY_PARITY_JOBS
    assert "Dashboard production asset parity" in REQUIRED_SECURITY_PARITY_JOBS
    assert set(REQUIRED_CI_JOBS) == set(REQUIRED_CI_MATRIX_JOBS) | set(
        REQUIRED_SECURITY_PARITY_JOBS
    )


def test_select_successful_ci_run_and_jobs() -> None:
    runs = [
        {
            "id": 10,
            "head_sha": SOURCE,
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "failure",
            "name": "CI",
        },
        {
            "id": 11,
            "head_sha": SOURCE,
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
    ]
    selected = select_successful_ci_run(runs, source_commit=SOURCE)
    assert selected["id"] == 11
    jobs = _success_jobs()
    verified = verify_required_jobs(jobs)
    assert verified == REQUIRED_CI_JOBS
    evidence = matrix_evidence_from_github(
        source_commit=SOURCE,
        runs_payload={"workflow_runs": runs},
        jobs_payload={"jobs": [{**job, "run_id": 11} for job in jobs]},
    )
    assert evidence.as_dict()["ok"] is True
    assert set(evidence.as_dict()["jobs"]) >= set(REQUIRED_SECURITY_PARITY_JOBS)


def test_missing_job_fails() -> None:
    jobs = [
        {"name": name, "conclusion": "success", "status": "completed"}
        for name in REQUIRED_CI_JOBS[:-1]
    ]
    with pytest.raises(ManifestError, match="incomplete or not green"):
        verify_required_jobs(jobs)


def test_skipped_cancelled_neutral_required_jobs_fail() -> None:
    for conclusion in ("skipped", "cancelled", "neutral", "timed_out"):
        jobs = _success_jobs()
        jobs[0] = {
            "name": REQUIRED_CI_JOBS[0],
            "conclusion": conclusion,
            "status": "completed",
        }
        with pytest.raises(ManifestError, match="incomplete or not green"):
            verify_required_jobs(jobs)


def test_branch_name_or_wrong_sha_never_satisfies_matrix() -> None:
    runs = [
        {
            "id": 1,
            "head_sha": "main",
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
        {
            "id": 2,
            "head_sha": OTHER,
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
        {
            "id": 3,
            "head_sha": SOURCE[:7],
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        },
    ]
    with pytest.raises(ManifestError, match="no successful CI workflow run"):
        select_successful_ci_run(runs, source_commit=SOURCE)


def test_non_ci_workflow_name_alone_does_not_satisfy() -> None:
    runs = [
        {
            "id": 9,
            "head_sha": SOURCE,
            "path": ".github/workflows/release-candidate.yml",
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        }
    ]
    with pytest.raises(ManifestError, match="no successful CI workflow run"):
        select_successful_ci_run(runs, source_commit=SOURCE)


def test_jobs_from_other_run_id_are_refused() -> None:
    runs = [
        {
            "id": 11,
            "head_sha": SOURCE,
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "name": "CI",
        }
    ]
    jobs = [{**job, "run_id": 99} for job in _success_jobs()]
    with pytest.raises(ManifestError, match="different workflow run"):
        matrix_evidence_from_github(
            source_commit=SOURCE,
            runs_payload={"workflow_runs": runs},
            jobs_payload={"jobs": jobs},
        )


def test_forged_matrix_evidence_ok_boolean_is_refused() -> None:
    payload = {
        "schema_version": 1,
        "source_commit": SOURCE,
        "workflow_run_id": 1,
        "workflow_name": "CI",
        "conclusion": "success",
        "jobs": list(REQUIRED_CI_JOBS),
        "required_jobs": list(REQUIRED_CI_JOBS),
        "ok": "true",
    }
    with pytest.raises(ManifestError, match="boolean true"):
        validate_matrix_evidence(payload, source_commit=SOURCE)
    payload["ok"] = True
    payload["jobs"] = list(REQUIRED_CI_MATRIX_JOBS)  # missing security/parity
    with pytest.raises(ManifestError, match="omit required"):
        validate_matrix_evidence(payload, source_commit=SOURCE)
    payload["jobs"] = list(REQUIRED_CI_JOBS)
    payload["source_commit"] = OTHER
    with pytest.raises(ManifestError, match="source_commit"):
        validate_matrix_evidence(payload, source_commit=SOURCE)


def test_valid_matrix_evidence_round_trip() -> None:
    payload = {
        "schema_version": 1,
        "source_commit": SOURCE,
        "workflow_run_id": 99,
        "workflow_name": "CI",
        "conclusion": "success",
        "jobs": list(REQUIRED_CI_JOBS),
        "required_jobs": list(REQUIRED_CI_JOBS),
        "ok": True,
    }
    validated = validate_matrix_evidence(payload, source_commit=SOURCE)
    assert validated["ok"] is True


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
