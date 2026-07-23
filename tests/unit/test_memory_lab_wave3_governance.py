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


def test_wave3_has_one_coordinator_and_five_unique_visible_workers() -> None:
    manifest = _manifest()
    workers = manifest["workers"]

    assert manifest["schema_version"] == 1
    assert manifest["status"] == "active"
    assert manifest["coordinator"]["sole_integration_authority"] is True
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["coordinator"]["base_commit"])
    assert len(workers) == 5
    assert len({worker["worker_id"] for worker in workers}) == 5
    assert len({worker["thread_id"] for worker in workers}) == 5
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


def test_wave3_freezes_questions_results_and_integration_gates() -> None:
    manifest = _manifest()
    worker_ids = [worker["worker_id"] for worker in manifest["workers"]]

    assert manifest["promotion_order"] == worker_ids
    assert all(worker["question"] for worker in manifest["workers"])
    assert all(worker["result"] is None for worker in manifest["workers"])
    assert set(manifest["evidence_levels"]) == {"L0", "L1", "L2", "L3", "L4", "L5"}
    assert len(manifest["integration_gate"]) >= 10
    assert "Negative, unsupported, not-exercised, held, and killed results remain visible." in (
        manifest["global_invariants"]
    )
