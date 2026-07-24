from __future__ import annotations

import inspect
import json
from pathlib import Path

from allthecontext.memory_lab_m3 import (
    ClosureConfig,
    FullRebuildOracle,
    IncrementalInfluenceClosure,
    MutationKind,
)

from bench.memory_lab_m3 import (
    CASE_RUNNERS,
    F02_INVARIANTS,
    CaseMetrics,
    _engine,
    _randomized_repair,
    load_fixture,
    run_experiment,
    verify_local_module_origin,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPORT = REPOSITORY_ROOT / "bench" / "reports" / "memory_lab_m3_wave4.json"


def test_fixture_maps_every_frozen_f02_m3_case_and_surface() -> None:
    fixture = load_fixture()

    assert fixture["synthetic_only"] is True
    assert {row[0] for row in fixture["f02_case_mapping"]} == set(F02_INVARIANTS)
    assert set(CASE_RUNNERS) == set(F02_INVARIANTS)
    assert set(fixture["surfaces"]) == {
        "retrieval_selection",
        "issued_context",
        "procedure",
        "selection_cache",
        "working_state",
        "use_statistics",
    }
    assert set(fixture["topologies"]) == {
        "chain",
        "fan_out",
        "fan_in",
        "shared_descendant",
        "cycle_attempt",
        "cross_scope_edge_attempt",
    }


def test_execution_origin_attestation_is_verified_and_repository_relative() -> None:
    attestation = verify_local_module_origin(REPOSITORY_ROOT)

    assert attestation == {
        "governance_base_sha": "f545c37157845f0bd402215719cb8c747b7fc21d",
        "import_origin_verified_to_worker_worktree": True,
        "imported_module_paths": [
            "packages/allthecontext/src/allthecontext/memory_lab_m3.py",
            "bench/memory_lab_m3.py",
        ],
    }
    assert all(not Path(item).is_absolute() for item in attestation["imported_module_paths"])


def test_barrier_withdraws_complete_chain_before_partial_repair() -> None:
    engine = _engine("chain", nonce="unit-barrier")
    old = dict(engine.artifacts)

    engine.mutate(
        MutationKind.CORRECTION,
        record_id="root-chain",
        payload_symbol="opaque-unit-v2",
    )

    assert engine.active_barrier is not None
    assert engine.active_barrier.affected == frozenset(old)
    assert all(engine.published(artifact_id) is None for artifact_id in old)
    assert engine.repair_one("sel-chain") is True
    assert engine.repair_one("issue-chain") is True
    assert engine.finalize_repair() is False
    assert all(engine.published(artifact_id) is None for artifact_id in old)

    metrics = CaseMetrics()
    _randomized_repair(
        engine,
        seed=47001,
        metrics=metrics,
        old_artifacts=old,
    )
    assert all(engine.published(artifact_id) is not None for artifact_id in old)


def test_incremental_and_clean_build_implementations_are_separate() -> None:
    incremental_source = inspect.getsource(IncrementalInfluenceClosure._evaluate_incremental)
    oracle_source = inspect.getsource(FullRebuildOracle._evaluate_clean)

    assert "_evaluate_clean" not in incremental_source
    assert "_evaluate_incremental" not in oracle_source
    assert "self.artifacts" not in oracle_source
    assert "_incremental_commitment" not in oracle_source


def test_cycle_and_cross_scope_attempts_are_atomic_and_privacy_safe() -> None:
    cycle = _engine("cycle_attempt", nonce="unit-cycle")
    cycle_graph = cycle.graph_snapshot()
    cycle_state = cycle.observable_state()
    assert cycle.try_add_edge("cache-cycle", "root-cycle") is False
    assert cycle.graph_snapshot() == cycle_graph
    assert cycle.observable_state() == cycle_state

    cross_scope = _engine("cross_scope_edge_attempt", nonce="unit-scope")
    scope_graph = cross_scope.graph_snapshot()
    scope_state = cross_scope.observable_state()
    assert cross_scope.try_add_edge("root-scope-alpha", "proc-scope-beta") is False
    assert cross_scope.graph_snapshot() == scope_graph
    assert cross_scope.observable_state() == scope_state

    receipts = json.dumps(
        [
            (receipt.reason.value, receipt.per_run_artifact_ref, receipt.count)
            for receipt in (*cycle.failure_receipts, *cross_scope.failure_receipts)
        ]
    )
    assert "opaque-" not in receipts
    assert "root-cycle" not in receipts
    assert "root-scope-alpha" not in receipts


def test_delete_is_reversible_but_purge_leaves_only_generation_barrier() -> None:
    deleted = _engine("fan_out", nonce="unit-delete")
    deleted.mutate(MutationKind.ORDINARY_DELETE, record_id="root-fan")
    assert deleted.restore("root-fan") is True
    assert deleted.records["root-fan"].version == 1

    purged = _engine("fan_out", nonce="unit-purge")
    old = dict(purged.artifacts)
    payload = purged.records["root-fan"].payload_symbol
    purged.mutate(MutationKind.TERMINAL_PURGE, record_id="root-fan")
    assert purged.restore("root-fan") is False
    boundary = json.dumps(purged.privacy_boundary(), sort_keys=True)
    assert "root-fan" not in boundary
    assert payload not in boundary
    assert all(item.semantic_commitment not in boundary for item in old.values())
    assert all(artifact_id not in boundary for artifact_id in old)
    assert purged.blueprints == {}
    assert purged.minimum_generation == purged.graph_epoch
    assert purged.privacy_boundary()["aggregate_invalidation_count"] == 1


def test_missing_edge_and_ablations_are_observably_decisive() -> None:
    report = run_experiment(repeats=1)

    assert report["decision"] == "RETAIN_M3_CONTRACT_AND_OPTIMIZATION"
    assert all(value == 0 for value in report["decisive_metrics"].values())
    assert report["ablations_decisive"] is True
    assert all(report["ablations"].values())
    assert (
        report["injected_faults"]["missing_inventory_edge"]["published_stale_descendant_count"] > 0
    )
    assert report["work_control"]["evaluated_node_reduction_fraction"] >= 0.25
    assert report["purge_boundary"] == {
        "declared_boundary_includes_graph_inventory": True,
        "exclusive_descendant_ids_checked": 7,
        "shared_descendant_recipes_validated": 4,
    }


def test_checked_report_preserves_complete_f02_result() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["schema"] == "atc.memory-lab.m3-report.v1"
    assert report["repeats_per_case"] == 20
    assert report["case_count"] == 15
    assert report["case_coverage_fraction"] == 1.0
    assert report["surface_coverage_fraction"] == 1.0
    assert {case["case_id"] for case in report["cases"]} == set(F02_INVARIANTS)
    assert all(case["verdict"] == "PASS" for case in report["cases"])
    assert all(value == 0 for value in report["decisive_metrics"].values())
    assert report["decision"] == "RETAIN_M3_CONTRACT_AND_OPTIMIZATION"
    assert report["work_control"]["evaluated_node_reduction_fraction"] >= 0.25
    assert report["purge_boundary"] == {
        "declared_boundary_includes_graph_inventory": True,
        "exclusive_descendant_ids_checked": 140,
        "shared_descendant_recipes_validated": 80,
    }
    assert (
        report["execution_origin_attestation"]["import_origin_verified_to_worker_worktree"] is True
    )
    assert all(
        not Path(item).is_absolute()
        for item in report["execution_origin_attestation"]["imported_module_paths"]
    )


def test_raw_record_only_purge_ablation_preserves_failure() -> None:
    engine = _engine(
        "fan_out",
        nonce="unit-raw-only",
        config=ClosureConfig(
            withdraw_before_repair=False,
            erase_derived_on_purge=False,
        ),
    )
    old = dict(engine.artifacts)

    engine.mutate(MutationKind.TERMINAL_PURGE, record_id="root-fan")

    assert all(engine.published(artifact_id) is not None for artifact_id in old)
