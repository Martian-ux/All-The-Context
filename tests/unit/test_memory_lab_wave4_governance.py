from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parents[2]
MANIFEST_PATH = ROOT / "research" / "memory-lab" / "wave4-manifest.json"


def _manifest() -> dict[str, Any]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_wave4_has_one_coordinator_and_four_disjoint_workers() -> None:
    manifest = _manifest()
    workers = manifest["workers"]

    assert manifest["schema_version"] == 1
    assert manifest["status"] in {"preparing", "active", "completed_with_mixed_results"}
    assert manifest["coordinator"]["sole_integration_authority"] is True
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["coordinator"]["parent_commit"])
    assert len(workers) == 4
    assert len({worker["worker_id"] for worker in workers}) == 4
    assert all(worker["model"] == "gpt-5.6-sol" for worker in workers)
    assert [worker["reasoning_effort"] for worker in workers].count("medium") == 2
    assert [worker["reasoning_effort"] for worker in workers].count("high") == 2

    thread_ids = [worker["thread_id"] for worker in workers]
    worker_base = manifest["coordinator"]["worker_base_commit"]
    if manifest["status"] == "preparing":
        assert thread_ids == [None, None, None, None]
        assert worker_base is None
    else:
        assert len(set(thread_ids)) == 4
        assert all(
            re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                thread_id,
            )
            for thread_id in thread_ids
        )
        assert re.fullmatch(r"[0-9a-f]{40}", worker_base)


def test_wave4_workers_cannot_patch_or_promote_production() -> None:
    workers = _manifest()["workers"]

    assert all(worker["external_code_policy"] == "forbidden" for worker in workers)
    assert all(worker["may_edit_existing_shared_harness"] is False for worker in workers)
    assert all(worker["may_edit_production"] is False for worker in workers)
    assert all(worker["may_edit_governance"] is False for worker in workers)
    assert all(worker["may_merge"] is False for worker in workers)
    assert all(worker["may_push"] is False for worker in workers)
    e02 = next(
        worker
        for worker in workers
        if worker["worker_id"] == "e02_production_semantic_gaps"
    )
    assert e02["operator_core_access"] is False


def test_wave4_freezes_independent_oracles_and_hard_safety_gates() -> None:
    manifest = _manifest()
    contracts = manifest["frozen_contracts"]
    oracle = manifest["frozen_oracle"]

    assert manifest["promotion_stages"][0] == "f02_independent_oracle"
    assert manifest["promotion_stages"][-1] == "f02_post_result_review"
    assert re.fullmatch(r"[0-9a-f]{40}", oracle["commit"])
    assert oracle["committed_before_m3_and_m1_dispatch"] is True
    assert oracle["m3_case_count"] == 15
    assert oracle["m1_case_count"] == 16
    assert oracle["integrated"] is False
    assert len(contracts["m3"]["hard_safety_gates"]) >= 5
    assert len(contracts["m1"]["hard_safety_gates"]) >= 6
    assert set(contracts["e02"]["classifications"]) == {
        "SUPPORTED_OBSERVED",
        "CONTRADICTED_OBSERVED",
        "UNSUPPORTED",
        "NOT_EXERCISED",
    }
    assert {
        "issued_context",
        "procedure",
        "selection_cache",
        "working_state",
        "use_statistics",
    }.issubset(contracts["m3"]["derived_surfaces"])
    assert contracts["m1"]["observable_stages"] == [
        "assigned",
        "supplied",
        "acknowledged",
        "observed_use",
        "action",
        "outcome",
        "invalidated",
    ]
    assert len(manifest["integration_gate"]) >= 12
    assert "No mechanism may grade itself against an oracle authored after its implementation." in (
        manifest["global_invariants"]
    )
