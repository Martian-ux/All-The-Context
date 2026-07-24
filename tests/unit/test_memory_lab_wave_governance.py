from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "research" / "memory-lab" / "wave2-manifest.json"
GOVERNANCE_PATH = ROOT / "docs" / "research" / "ATC_MEMORY_LAB_GOVERNANCE.md"


def _manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_wave_has_one_coordinator_and_unique_visible_workers() -> None:
    manifest = _manifest()
    workers = manifest["workers"]

    assert manifest["schema_version"] == 1
    assert manifest["coordinator"]["sole_integration_authority"] is True
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["coordinator"]["base_commit"])
    assert len(workers) == 5
    assert len({worker["worker_id"] for worker in workers}) == len(workers)
    assert len({worker["thread_id"] for worker in workers}) == len(workers)
    assert all(
        re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            worker["thread_id"],
        )
        for worker in workers
    )
    assert all(worker["model"] == "gpt-5.6-sol" for worker in workers)
    assert all(worker["may_merge"] is False for worker in workers)
    assert all(worker["may_push"] is False for worker in workers)
    assert all(worker["may_edit_governance"] is False for worker in workers)


def test_only_declared_supplier_worker_can_use_external_code() -> None:
    manifest = _manifest()
    workers = {worker["worker_id"]: worker for worker in manifest["workers"]}
    external_workers = {
        worker_id
        for worker_id, worker in workers.items()
        if worker["external_code_policy"] != "forbidden"
    }

    assert external_workers == {"hindsight_supplier"}
    assert manifest["supplier_execution_gate"]["allowed_supplier"] == "Hindsight"
    assert manifest["supplier_execution_gate"]["production_import"] is False
    assert (
        "honest skipped receipt when any gate fails"
        in manifest["supplier_execution_gate"]["required"]
    )


def test_governance_documents_evidence_and_integration_gates() -> None:
    manifest = _manifest()
    governance = GOVERNANCE_PATH.read_text(encoding="utf-8")

    assert set(manifest["evidence_levels"]) == {"L0", "L1", "L2", "L3", "L4", "L5"}
    assert len(manifest["integration_gate"]) >= 8
    assert "untrusted until the coordinator" in governance
    assert "Cloning source is not permission to install or execute it." in governance
    assert "Rejected commits remain visible" in governance


def test_completed_wave_preserves_results_evidence_and_supplier_skip() -> None:
    manifest = _manifest()
    workers = {worker["worker_id"]: worker for worker in manifest["workers"]}

    assert manifest["status"] == "completed_with_supplier_skip"
    assert all(worker["result"] is not None for worker in workers.values())
    for worker in workers.values():
        result = worker["result"]
        assert re.fullmatch(r"[0-9a-f]{40}", result["worker_commit"])
        assert re.fullmatch(r"[0-9a-f]{40}", result["integrated_commit"])
        assert result["evidence_level"] in manifest["evidence_levels"]
        assert result["outcome"]
        assert result["report"]
        assert result["limitations"]

    supplier = workers["hindsight_supplier"]["result"]
    assert supplier["outcome"] == "not_executed_dependency_and_egress_gate"
    assert supplier["supplier_evidence_level"] is None
    assert manifest["completion"]["coordinator_reproductions"] == {
        "baseline_ladder_repeats": 20,
        "lifecycle_e01_repeats": 20,
        "operator_core_touched": False,
        "personal_context_used": False,
        "external_supplier_executed": False,
    }
    assert manifest["completion"]["accepted_evidence"] == {
        "L0": 2,
        "L1": 0,
        "L2": 3,
        "L3": 0,
        "L4": 0,
        "L5": 0,
    }
