from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "research" / "memory-lab" / "wave3-manifest.json"


def _manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_wave3_has_one_coordinator_and_six_unique_visible_workers() -> None:
    manifest = _manifest()
    workers = manifest["workers"]

    assert manifest["schema_version"] == 1
    assert manifest["status"] == "completed_with_mixed_results"
    assert manifest["coordinator"]["sole_integration_authority"] is True
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["coordinator"]["base_commit"])
    assert len(workers) == 6
    assert len({worker["worker_id"] for worker in workers}) == 6
    assert len({worker["thread_id"] for worker in workers}) == 6
    assert all(
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            worker["thread_id"],
        )
        for worker in workers
    )
    assert all(worker["model"] == "gpt-5.6-sol" for worker in workers)
    assert {worker["reasoning_effort"] for worker in workers} == {"medium", "high"}


def test_wave3_workers_have_disjoint_authority_and_no_external_code() -> None:
    manifest = _manifest()
    workers = manifest["workers"]

    assert all(worker["external_code_policy"] == "forbidden" for worker in workers)
    assert all(worker["may_edit_existing_shared_harness"] is False for worker in workers)
    assert all(worker["may_edit_production"] is False for worker in workers)
    assert all(worker["may_edit_governance"] is False for worker in workers)
    assert all(worker["may_merge"] is False for worker in workers)
    assert all(worker["may_push"] is False for worker in workers)
    assert next(
        worker
        for worker in workers
        if worker["worker_id"] == "e01b_production_conformance"
    )["operator_core_access"] is False
    intake = next(
        worker
        for worker in workers
        if worker["worker_id"] == "external_artifact_intake"
    )
    assert intake["official_metadata_only"] is True
    assert intake["raw_adversarial_payload_access"] is False
    assert re.fullmatch(r"[0-9a-f]{40}", intake["base_commit"])


def test_wave3_preserves_questions_results_and_integration_gates() -> None:
    manifest = _manifest()
    worker_ids = [worker["worker_id"] for worker in manifest["workers"]]
    results = {
        worker["worker_id"]: worker["result"] for worker in manifest["workers"]
    }

    assert manifest["promotion_order"] == worker_ids
    assert all(worker["question"] for worker in manifest["workers"])
    assert all(isinstance(result, dict) for result in results.values())
    assert all(result["worker_commits"] for result in results.values())
    assert all(result["integrated_commits"] for result in results.values())
    assert all(
        re.fullmatch(r"[0-9a-f]{40}", commit)
        for result in results.values()
        for commit in (*result["worker_commits"], *result["integrated_commits"])
    )
    assert {
        results["b01_programmatic_log"]["worker_decision"],
        results["o01_online_shift"]["worker_decision"],
        results["p01_poisoning"]["worker_decision"],
        results["m2_sealed_minimal_projection"]["worker_decision"],
    } == {
        "KILL_MECHANISM",
        "HOLD",
        "HOLD_AUTOMATIC_DURABILITY",
        "narrow_retain_bounded_m2",
    }
    assert results["e01b_production_conformance"]["metrics"] == {
        "case_count": 12,
        "conformance_pass_count": 6,
        "unsupported_not_exercised_count": 6,
        "observed_failure_count": 0,
    }
    assert results["external_artifact_intake"]["evidence_level"] == "L0"
    assert results["external_artifact_intake"]["coordinator_reproduced"] is False
    assert all(
        result["coordinator_reproduced"] is True
        for worker_id, result in results.items()
        if worker_id != "external_artifact_intake"
    )
    assert manifest["completion"]["focused_coordinator_tests_passed"] == 43
    assert manifest["completion"]["quality_gates"] == {
        "python": "3.12.10",
        "ruff": "passed",
        "mypy": "passed across 66 source files",
        "pytest": {
            "passed": 603,
            "skipped": 4,
            "skip_reason": (
                "Windows host cannot create the symlinks required by four "
                "platform-specific tests"
            ),
        },
    }
    assert manifest["completion"]["production_promotion"] is False
    assert manifest["completion"]["external_execution"] is False
    assert set(manifest["evidence_levels"]) == {"L0", "L1", "L2", "L3", "L4", "L5"}
    assert len(manifest["integration_gate"]) >= 10
    assert "Negative, unsupported, not-exercised, held, and killed results remain visible." in (
        manifest["global_invariants"]
    )
