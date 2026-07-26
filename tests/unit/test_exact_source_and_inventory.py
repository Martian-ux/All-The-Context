from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.component_inventory import build_component_inventory
from allthecontext.exact_source_gate import (
    REQUIRED_CI_JOBS,
    matrix_evidence_from_github,
    select_successful_ci_run,
    verify_required_jobs,
)
from allthecontext.release_manifest import ManifestError

SOURCE = "c" * 40
ROOT = Path(__file__).resolve().parents[2]


def test_required_ci_jobs_cover_nine_matrix_slots() -> None:
    assert len(REQUIRED_CI_JOBS) == 9
    assert "Python 3.12 - windows-latest" in REQUIRED_CI_JOBS
    assert "Dashboard - Node 22" in REQUIRED_CI_JOBS
    assert "Desktop artifact - macos-26-intel" in REQUIRED_CI_JOBS


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
    jobs = [{"name": name, "conclusion": "success"} for name in REQUIRED_CI_JOBS]
    verified = verify_required_jobs(jobs)
    assert verified == REQUIRED_CI_JOBS
    evidence = matrix_evidence_from_github(
        source_commit=SOURCE,
        runs_payload={"workflow_runs": runs},
        jobs_payload={"jobs": jobs},
    )
    assert evidence.as_dict()["ok"] is True


def test_missing_job_fails() -> None:
    jobs = [{"name": name, "conclusion": "success"} for name in REQUIRED_CI_JOBS[:-1]]
    with pytest.raises(ManifestError, match="nine-job"):
        verify_required_jobs(jobs)


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
