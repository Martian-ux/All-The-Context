"""Run the isolated Wave 4 M3 dependency-complete closure experiment."""

from __future__ import annotations

import argparse
import importlib
import json
import random
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from allthecontext.memory_lab_m3 import (
    ArtifactBlueprint,
    CanonicalRecord,
    ClosureConfig,
    Dependency,
    DerivedArtifact,
    FullRebuildOracle,
    IncrementalInfluenceClosure,
    InfluenceClass,
    MutationKind,
    Surface,
)

FIXTURES = Path(__file__).with_name("memory_lab_m3_fixtures.json")
REPORT_SCHEMA = "atc.memory-lab.m3-report.v1"
DEFAULT_REPEATS = 20
PRINCIPAL_ALPHA = "principal-alpha"
PRINCIPAL_BETA = "principal-beta"
DOMAIN_MAIN = "domain-main"

F02_INVARIANTS: dict[str, tuple[str, ...]] = {
    "M3-C01-CORRECTION-CHAIN": ("M3-I01", "M3-I02", "M3-I04", "M3-I08"),
    "M3-C02-SCOPE-NARROWING-FANOUT": ("M3-I01", "M3-I02", "M3-I04", "M3-I08"),
    "M3-C03-PERMISSION-REVOCATION-FANIN": ("M3-I01", "M3-I02", "M3-I04"),
    "M3-C04-ORDINARY-DELETE-RESTORE": ("M3-I01", "M3-I02", "M3-I05"),
    "M3-C05-TERMINAL-PURGE-IDENTIFIER-REUSE": (
        "M3-I02",
        "M3-I03",
        "M3-I04",
        "M3-I05",
        "M3-I08",
    ),
    "M3-C06-POLICY-GENERATION-CHANGE": ("M3-I01", "M3-I02", "M3-I04", "M3-I07"),
    "M3-C07-PARTIAL-REPAIR-BARRIER": ("M3-I01", "M3-I02", "M3-I04"),
    "M3-C08-STALE-WRITER": ("M3-I02", "M3-I04"),
    "M3-C09-CYCLE-EDGE-ATTEMPT": ("M3-I06", "M3-I08"),
    "M3-C10-CROSS-SCOPE-EDGE-ATTEMPT": ("M3-I06", "M3-I08"),
    "M3-C11-DUPLICATE-AND-CONFLICTING-REPLAY": ("M3-I02", "M3-I04"),
    "M3-C12-OUT-OF-ORDER-MUTATION": ("M3-I02", "M3-I04"),
    "M3-C13-SHARED-DESCENDANT-CORRECTION": ("M3-I01", "M3-I02"),
    "M3-C14-PURGE-DURING-ISSUE": ("M3-I02", "M3-I03", "M3-I04", "M3-I08"),
    "M3-C15-OPTIMIZATION-WORK-CONTROL": ("M3-I02",),
}


@dataclass(slots=True)
class CaseMetrics:
    """Safety, equality, privacy, work, and availability observations."""

    published_stale_descendant_count: int = 0
    optimized_full_rebuild_mismatch_count: int = 0
    terminal_purge_residue_count: int = 0
    fail_open_publication_count: int = 0
    illegal_edge_accept_count: int = 0
    conflicting_replay_accept_count: int = 0
    duplicate_side_effect_count: int = 0
    privacy_receipt_violation_count: int = 0
    partial_repair_exposure_count: int = 0
    stale_writer_accept_count: int = 0
    purged_lineage_revival_count: int = 0
    ordinary_delete_purge_conflation_count: int = 0
    descendants_scanned: int = 0
    descendants_rebuilt: int = 0
    full_rebuild_nodes_evaluated: int = 0
    publication_observations: int = 0
    safe_publication_observations: int = 0
    final_eligible_artifacts: int = 0
    full_rebuild_eligible_artifacts: int = 0
    purge_exclusive_descendant_ids_checked: int = 0
    purge_shared_descendant_recipes_validated: int = 0

    def add(self, other: CaseMetrics) -> None:
        for field_name in self.__dataclass_fields__:
            setattr(self, field_name, getattr(self, field_name) + getattr(other, field_name))

    def required_zero_total(self) -> int:
        return sum(
            getattr(self, name)
            for name in (
                "published_stale_descendant_count",
                "optimized_full_rebuild_mismatch_count",
                "terminal_purge_residue_count",
                "fail_open_publication_count",
                "illegal_edge_accept_count",
                "conflicting_replay_accept_count",
                "duplicate_side_effect_count",
                "privacy_receipt_violation_count",
                "partial_repair_exposure_count",
                "stale_writer_accept_count",
                "purged_lineage_revival_count",
                "ordinary_delete_purge_conflation_count",
            )
        )


def load_fixture() -> dict[str, Any]:
    """Load the frozen synthetic M3 fixture."""

    return json.loads(FIXTURES.read_text(encoding="utf-8"))


def verify_local_module_origin(
    repository_root: Path | None = None,
) -> dict[str, object]:
    """Verify runtime origins and return only repository-relative attestation data."""

    root = (repository_root or Path(__file__).resolve().parents[1]).resolve()
    expected = (root / "packages" / "allthecontext" / "src").resolve()
    module = importlib.import_module("allthecontext.memory_lab_m3")
    module_path = Path(module.__file__ or "").resolve()
    runner_path = Path(__file__).resolve()
    if not module_path.is_relative_to(expected):
        raise RuntimeError("allthecontext.memory_lab_m3 resolved outside the active worktree")
    if not runner_path.is_relative_to(root):
        raise RuntimeError("bench.memory_lab_m3 resolved outside the active worktree")
    return {
        "governance_base_sha": "f545c37157845f0bd402215719cb8c747b7fc21d",
        "import_origin_verified_to_worker_worktree": True,
        "imported_module_paths": [
            module_path.relative_to(root).as_posix(),
            runner_path.relative_to(root).as_posix(),
        ],
    }


def _surface(artifact_id: str) -> Surface:
    prefixes = {
        "sel": Surface.RETRIEVAL_SELECTION,
        "issue": Surface.ISSUED_CONTEXT,
        "proc": Surface.PROCEDURE,
        "cache": Surface.SELECTION_CACHE,
        "work": Surface.WORKING_STATE,
        "stats": Surface.USE_STATISTICS,
    }
    return next(value for prefix, value in prefixes.items() if artifact_id.startswith(prefix))


def _engine(
    topology: str,
    *,
    principal: str = PRINCIPAL_ALPHA,
    nonce: str,
    config: ClosureConfig | None = None,
) -> IncrementalInfluenceClosure:
    fixture = load_fixture()["topologies"][topology]
    engine = IncrementalInfluenceClosure(
        principal=principal,
        policy_generation=7,
        run_nonce=nonce,
        config=config,
    )
    scopes = frozenset({PRINCIPAL_ALPHA, PRINCIPAL_BETA})
    grants = frozenset({PRINCIPAL_ALPHA, PRINCIPAL_BETA})
    for ordinal, record_id in enumerate(fixture["roots"]):
        domain = "scope-alpha" if topology == "cross_scope_edge_attempt" else DOMAIN_MAIN
        engine.install_record(
            CanonicalRecord(
                record_id=record_id,
                version=1,
                payload_symbol=f"opaque-{topology}-{ordinal}-v1",
                domain=domain,
                scopes=scopes,
                grants=grants,
            )
        )
    if topology == "cross_scope_edge_attempt":
        engine.install_record(
            CanonicalRecord(
                record_id="root-scope-beta-anchor",
                version=1,
                payload_symbol="opaque-scope-beta-anchor",
                domain="scope-beta",
                scopes=scopes,
                grants=grants,
            )
        )
        engine.install_blueprint(
            ArtifactBlueprint(
                artifact_id="proc-scope-beta",
                surface=Surface.PROCEDURE,
                domain="scope-beta",
                dependencies=(Dependency("root-scope-beta-anchor", InfluenceClass.CONTENT),),
            )
        )
    else:
        edge_rows: list[list[str]] = fixture.get("edges", [])
        by_successor: dict[str, list[Dependency]] = {}
        for predecessor, successor, influence in edge_rows:
            by_successor.setdefault(successor, []).append(
                Dependency(predecessor, InfluenceClass(influence))
            )
        for artifact_id in fixture["artifacts"]:
            engine.install_blueprint(
                ArtifactBlueprint(
                    artifact_id=artifact_id,
                    surface=_surface(artifact_id),
                    domain=DOMAIN_MAIN,
                    dependencies=tuple(by_successor[artifact_id]),
                )
            )
    engine.build_initial()
    return engine


def _observe_barrier(
    engine: IncrementalInfluenceClosure,
    old_artifacts: Mapping[str, DerivedArtifact],
    metrics: CaseMetrics,
) -> None:
    barrier = engine.active_barrier
    if barrier is None:
        return
    for artifact_id in barrier.affected:
        metrics.publication_observations += 1
        published = engine.published(artifact_id)
        if published is None:
            metrics.safe_publication_observations += 1
            continue
        metrics.fail_open_publication_count += 1
        if (
            artifact_id in old_artifacts
            and published.semantic_commitment == old_artifacts[artifact_id].semantic_commitment
        ):
            metrics.published_stale_descendant_count += 1


def _randomized_repair(
    engine: IncrementalInfluenceClosure,
    *,
    seed: int,
    metrics: CaseMetrics,
    old_artifacts: Mapping[str, DerivedArtifact],
    observe_each_step: bool = True,
) -> None:
    barrier = engine.active_barrier
    if barrier is None:
        return
    remaining = {item for item in barrier.affected if item in engine.blueprints}
    randomizer = random.Random(seed)
    while remaining:
        candidates = list(remaining)
        randomizer.shuffle(candidates)
        progressed = False
        for artifact_id in candidates:
            if engine.repair_one(artifact_id):
                remaining.remove(artifact_id)
                progressed = True
                if observe_each_step:
                    _observe_barrier(engine, old_artifacts, metrics)
        if not progressed:
            raise AssertionError("incremental repair made no topological progress")
    if not engine.finalize_repair():
        raise AssertionError("complete repair did not finalize")


def _compare_clean_build(
    engine: IncrementalInfluenceClosure,
    metrics: CaseMetrics,
    *,
    seed: int,
) -> None:
    oracle = FullRebuildOracle(
        records=engine.records,
        blueprints=engine.blueprints,
        principal=engine.principal,
        policy_generation=engine.policy_generation,
        graph_epoch=engine.graph_epoch,
    )
    schedule = list(engine.blueprints)
    random.Random(seed).shuffle(schedule)
    rebuilt = oracle.rebuild(schedule=schedule)
    optimized_state = engine.observable_state()
    rebuilt_state = oracle.observable_state(rebuilt)
    metrics.optimized_full_rebuild_mismatch_count += int(optimized_state != rebuilt_state)
    metrics.descendants_scanned += engine.descendants_scanned
    metrics.descendants_rebuilt += engine.descendants_rebuilt
    metrics.full_rebuild_nodes_evaluated += oracle.evaluated_nodes
    metrics.final_eligible_artifacts += len(optimized_state)
    metrics.full_rebuild_eligible_artifacts += len(rebuilt_state)


def _privacy_receipt_violations(
    engine: IncrementalInfluenceClosure,
    forbidden: Sequence[str],
) -> int:
    serialized = json.dumps(
        [asdict(item) for item in engine.failure_receipts],
        sort_keys=True,
    )
    return sum(int(token in serialized) for token in forbidden)


def _purge_residue(
    engine: IncrementalInfluenceClosure,
    *,
    forbidden: Sequence[str],
) -> int:
    serialized = json.dumps(engine.privacy_boundary(), sort_keys=True)
    return sum(int(token in serialized) for token in forbidden)


def _exclusive_and_shared_descendants(
    artifacts: Mapping[str, DerivedArtifact],
    *,
    purged_root_id: str,
) -> tuple[list[str], list[str]]:
    exclusive: list[str] = []
    shared: list[str] = []
    for artifact in artifacts.values():
        source_ids = {record_id for record_id, _ in artifact.source_versions}
        if purged_root_id not in source_ids:
            continue
        if source_ids == {purged_root_id}:
            exclusive.append(artifact.artifact_id)
        else:
            shared.append(artifact.artifact_id)
    return sorted(exclusive), sorted(shared)


def _recipe_root_ids(
    engine: IncrementalInfluenceClosure,
    artifact_id: str,
) -> set[str]:
    roots: set[str] = set()
    stack = [artifact_id]
    seen: set[str] = set()
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        if node_id in engine.records:
            roots.add(node_id)
            continue
        blueprint = engine.blueprints.get(node_id)
        if blueprint is not None:
            stack.extend(item.predecessor_id for item in blueprint.dependencies)
    return roots


def _standard_mutation_case(
    topology: str,
    mutation: MutationKind,
    *,
    repeat: int,
    seed: int,
) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = _engine(topology, nonce=f"r{repeat}")
    root_id = next(iter(engine.records))
    old = dict(engine.artifacts)
    kwargs: dict[str, Any] = {"record_id": root_id}
    if mutation is MutationKind.CORRECTION:
        kwargs["payload_symbol"] = f"opaque-corrected-{repeat}"
    elif mutation is MutationKind.SCOPE_NARROWING:
        kwargs["scopes"] = frozenset({PRINCIPAL_ALPHA})
    elif mutation is MutationKind.PERMISSION_REVOCATION:
        kwargs["grants"] = frozenset({PRINCIPAL_BETA})
    elif mutation is MutationKind.POLICY_GENERATION_CHANGE:
        kwargs = {"policy_generation": 8}
    engine.mutate(mutation, **kwargs)
    _observe_barrier(engine, old, metrics)
    _randomized_repair(
        engine,
        seed=seed,
        metrics=metrics,
        old_artifacts=old,
    )
    _compare_clean_build(engine, metrics, seed=seed + 1)
    metrics.privacy_receipt_violation_count += _privacy_receipt_violations(
        engine,
        [record.payload_symbol for record in engine.records.values()],
    )
    return metrics


def _case_01(repeat: int, seed: int) -> CaseMetrics:
    return _standard_mutation_case(
        "chain",
        MutationKind.CORRECTION,
        repeat=repeat,
        seed=seed,
    )


def _case_02(repeat: int, seed: int) -> CaseMetrics:
    beta = _standard_mutation_case(
        "fan_out",
        MutationKind.SCOPE_NARROWING,
        repeat=repeat,
        seed=seed,
    )
    alpha = CaseMetrics()
    engine = _engine("fan_out", principal=PRINCIPAL_ALPHA, nonce=f"a{repeat}")
    root_id = next(iter(engine.records))
    old = dict(engine.artifacts)
    engine.mutate(
        MutationKind.SCOPE_NARROWING,
        record_id=root_id,
        scopes=frozenset({PRINCIPAL_ALPHA}),
    )
    _observe_barrier(engine, old, alpha)
    _randomized_repair(
        engine,
        seed=seed + 2,
        metrics=alpha,
        old_artifacts=old,
    )
    _compare_clean_build(engine, alpha, seed=seed + 3)
    beta.add(alpha)
    return beta


def _case_03(repeat: int, seed: int) -> CaseMetrics:
    return _standard_mutation_case(
        "fan_in",
        MutationKind.PERMISSION_REVOCATION,
        repeat=repeat,
        seed=seed,
    )


def _case_04(repeat: int, seed: int) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = _engine("fan_out", nonce=f"r{repeat}")
    root_id = next(iter(engine.records))
    old = dict(engine.artifacts)
    original_version = engine.records[root_id].version
    engine.mutate(MutationKind.ORDINARY_DELETE, record_id=root_id)
    _observe_barrier(engine, old, metrics)
    if not engine.restore(root_id):
        metrics.ordinary_delete_purge_conflation_count += 1
    if engine.records[root_id].version != original_version:
        metrics.ordinary_delete_purge_conflation_count += 1
    _randomized_repair(
        engine,
        seed=seed,
        metrics=metrics,
        old_artifacts=old,
    )
    _compare_clean_build(engine, metrics, seed=seed + 1)
    return metrics


def _case_05(repeat: int, seed: int) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = _engine("shared_descendant", nonce=f"r{repeat}")
    root_id = "root-shared-a"
    old_payload = engine.records[root_id].payload_symbol
    old = dict(engine.artifacts)
    old_commitments = [
        artifact.semantic_commitment
        for artifact in old.values()
        if any(binding[0] == root_id for binding in artifact.source_versions)
    ]
    exclusive_ids, shared_ids = _exclusive_and_shared_descendants(
        old,
        purged_root_id=root_id,
    )
    metrics.purge_exclusive_descendant_ids_checked += len(exclusive_ids)
    engine.accept_ordered_event(
        sequence=1,
        event_id="event-before-purge",
        body_digest="digest-before-purge",
        generation=engine.graph_epoch,
    )
    engine.mutate(MutationKind.TERMINAL_PURGE, record_id=root_id)
    forbidden = [
        root_id,
        old_payload,
        "digest-before-purge",
        *old_commitments,
        *exclusive_ids,
    ]
    metrics.terminal_purge_residue_count += _purge_residue(
        engine,
        forbidden=forbidden,
    )
    if engine.restore(root_id):
        metrics.purged_lineage_revival_count += 1
    recreated = engine.create_record(
        requested_id=root_id,
        payload_symbol=f"opaque-new-{repeat}",
        domain=DOMAIN_MAIN,
        scopes=frozenset({PRINCIPAL_ALPHA}),
        grants=frozenset({PRINCIPAL_ALPHA}),
    )
    if recreated == root_id:
        metrics.purged_lineage_revival_count += 1
    replay = engine.accept_ordered_event(
        sequence=1,
        event_id="event-before-purge",
        body_digest="digest-before-purge",
        generation=0,
    )
    if replay == "accepted":
        metrics.purged_lineage_revival_count += 1
    _randomized_repair(
        engine,
        seed=seed,
        metrics=metrics,
        old_artifacts=old,
    )
    metrics.terminal_purge_residue_count += _purge_residue(
        engine,
        forbidden=forbidden,
    )
    metrics.privacy_receipt_violation_count += _privacy_receipt_violations(
        engine,
        forbidden,
    )
    for artifact_id in shared_ids:
        artifact = engine.artifacts.get(artifact_id)
        recipe_roots = _recipe_root_ids(engine, artifact_id)
        if (
            artifact is not None
            and root_id not in recipe_roots
            and root_id not in {source_id for source_id, _ in artifact.source_versions}
            and recipe_roots <= set(engine.records)
        ):
            metrics.purge_shared_descendant_recipes_validated += 1
        else:
            metrics.terminal_purge_residue_count += 1
    _compare_clean_build(engine, metrics, seed=seed + 1)
    return metrics


def _case_06(repeat: int, seed: int) -> CaseMetrics:
    return _standard_mutation_case(
        "fan_out",
        MutationKind.POLICY_GENERATION_CHANGE,
        repeat=repeat,
        seed=seed,
    )


def _case_07(repeat: int, seed: int) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = _engine("chain", nonce=f"r{repeat}")
    old = dict(engine.artifacts)
    engine.mutate(
        MutationKind.CORRECTION,
        record_id="root-chain",
        payload_symbol=f"opaque-v2-{repeat}",
    )
    assert engine.repair_one("sel-chain")
    _observe_barrier(engine, old, metrics)
    assert engine.repair_one("issue-chain")
    _observe_barrier(engine, old, metrics)
    if engine.finalize_repair():
        metrics.partial_repair_exposure_count += 1
    _observe_barrier(engine, old, metrics)
    _randomized_repair(
        engine,
        seed=seed,
        metrics=metrics,
        old_artifacts=old,
    )
    _compare_clean_build(engine, metrics, seed=seed + 1)
    return metrics


def _case_08(repeat: int, seed: int) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = _engine("shared_descendant", nonce=f"r{repeat}")
    old = dict(engine.artifacts)
    stale = old["proc-shared"]
    old_epoch = engine.graph_epoch
    engine.mutate(
        MutationKind.CORRECTION,
        record_id="root-shared-a",
        payload_symbol=f"opaque-v2-{repeat}",
    )
    _randomized_repair(
        engine,
        seed=seed,
        metrics=metrics,
        old_artifacts=old,
    )
    before = engine.graph_snapshot()
    if engine.commit_stale_writer(stale, expected_epoch=old_epoch):
        metrics.stale_writer_accept_count += 1
    if engine.graph_snapshot() != before:
        metrics.stale_writer_accept_count += 1
    _compare_clean_build(engine, metrics, seed=seed + 1)
    return metrics


def _case_09(repeat: int, seed: int) -> CaseMetrics:
    del seed
    metrics = CaseMetrics()
    engine = _engine("cycle_attempt", nonce=f"r{repeat}")
    before_graph = engine.graph_snapshot()
    before_state = engine.observable_state()
    if engine.try_add_edge("cache-cycle", "root-cycle"):
        metrics.illegal_edge_accept_count += 1
    if engine.graph_snapshot() != before_graph or engine.observable_state() != before_state:
        metrics.illegal_edge_accept_count += 1
    metrics.privacy_receipt_violation_count += _privacy_receipt_violations(
        engine,
        [record.payload_symbol for record in engine.records.values()],
    )
    _compare_clean_build(engine, metrics, seed=1)
    return metrics


def _case_10(repeat: int, seed: int) -> CaseMetrics:
    del seed
    metrics = CaseMetrics()
    engine = _engine("cross_scope_edge_attempt", nonce=f"r{repeat}")
    before_graph = engine.graph_snapshot()
    before_state = engine.observable_state()
    if engine.try_add_edge("root-scope-alpha", "proc-scope-beta"):
        metrics.illegal_edge_accept_count += 1
    if engine.graph_snapshot() != before_graph or engine.observable_state() != before_state:
        metrics.illegal_edge_accept_count += 1
    metrics.privacy_receipt_violation_count += _privacy_receipt_violations(
        engine,
        [record.payload_symbol for record in engine.records.values()],
    )
    _compare_clean_build(engine, metrics, seed=1)
    return metrics


def _case_11(repeat: int, seed: int) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = _engine("fan_in", nonce=f"r{repeat}")
    initial_state = engine.observable_state()
    if (
        engine.accept_ordered_event(
            sequence=1,
            event_id="event-11",
            body_digest="digest-1",
        )
        != "accepted"
    ):
        metrics.conflicting_replay_accept_count += 1
    for _ in range(2):
        if (
            engine.accept_ordered_event(
                sequence=1,
                event_id="event-11",
                body_digest="digest-1",
            )
            != "duplicate"
        ):
            metrics.duplicate_side_effect_count += 1
    if (
        engine.accept_ordered_event(
            sequence=1,
            event_id="event-11",
            body_digest="digest-2",
        )
        != "conflict"
    ):
        metrics.conflicting_replay_accept_count += 1
    for _ in range(3):
        engine.record_use(event_id="use-11", issue_id="issue-in", epoch=0)
    if engine.use_event_count != 1:
        metrics.duplicate_side_effect_count += abs(engine.use_event_count - 1)
    if engine.observable_state() != initial_state:
        metrics.conflicting_replay_accept_count += 1
    _compare_clean_build(engine, metrics, seed=seed)
    return metrics


def _case_12(repeat: int, seed: int) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = _engine("chain", nonce=f"r{repeat}")
    old = dict(engine.artifacts)
    engine.mutate(
        MutationKind.CORRECTION,
        record_id="root-chain",
        payload_symbol=f"opaque-v2-{repeat}",
    )
    if (
        engine.accept_ordered_event(
            sequence=2,
            event_id="repair-complete",
            body_digest="digest-complete",
        )
        == "accepted"
    ):
        metrics.fail_open_publication_count += 1
    _observe_barrier(engine, old, metrics)
    assert (
        engine.accept_ordered_event(
            sequence=1,
            event_id="mutation",
            body_digest="digest-mutation",
        )
        == "accepted"
    )
    assert (
        engine.accept_ordered_event(
            sequence=2,
            event_id="repair-complete",
            body_digest="digest-complete",
        )
        == "accepted"
    )
    _randomized_repair(
        engine,
        seed=seed,
        metrics=metrics,
        old_artifacts=old,
    )
    _compare_clean_build(engine, metrics, seed=seed + 1)
    return metrics


def _case_13(repeat: int, seed: int) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = _engine("shared_descendant", nonce=f"r{repeat}")
    old = dict(engine.artifacts)
    old_proc = old["proc-shared"]
    same_payload = engine.records["root-shared-a"].payload_symbol
    engine.mutate(
        MutationKind.CORRECTION,
        record_id="root-shared-a",
        payload_symbol=same_payload,
    )
    _observe_barrier(engine, old, metrics)
    _randomized_repair(
        engine,
        seed=seed,
        metrics=metrics,
        old_artifacts=old,
    )
    new_proc = engine.artifacts["proc-shared"]
    if (
        new_proc.semantic_commitment == old_proc.semantic_commitment
        or ("root-shared-a", 2) not in new_proc.source_versions
    ):
        metrics.published_stale_descendant_count += 1
    _compare_clean_build(engine, metrics, seed=seed + 1)
    return metrics


def _case_14(repeat: int, seed: int) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = _engine("fan_out", nonce=f"r{repeat}")
    old = dict(engine.artifacts)
    issue = old["issue-fan"]
    root = engine.records["root-fan"]
    forbidden = [
        root.record_id,
        root.payload_symbol,
        *(item.semantic_commitment for item in old.values()),
    ]
    exclusive_ids, shared_ids = _exclusive_and_shared_descendants(
        old,
        purged_root_id=root.record_id,
    )
    if shared_ids:
        raise AssertionError("fan-out purge fixture unexpectedly has shared descendants")
    forbidden.extend(exclusive_ids)
    metrics.purge_exclusive_descendant_ids_checked += len(exclusive_ids)
    engine.mutate(MutationKind.TERMINAL_PURGE, record_id="root-fan")
    if engine.record_use(
        event_id="late-use",
        issue_id=issue.artifact_id,
        epoch=issue.graph_epoch,
    ):
        metrics.fail_open_publication_count += 1
    metrics.terminal_purge_residue_count += _purge_residue(
        engine,
        forbidden=forbidden,
    )
    _randomized_repair(
        engine,
        seed=seed,
        metrics=metrics,
        old_artifacts=old,
    )
    metrics.terminal_purge_residue_count += _purge_residue(
        engine,
        forbidden=forbidden,
    )
    _compare_clean_build(engine, metrics, seed=seed + 1)
    return metrics


def _case_15(repeat: int, seed: int) -> CaseMetrics:
    metrics = CaseMetrics()
    engine = IncrementalInfluenceClosure(
        principal=PRINCIPAL_ALPHA,
        policy_generation=7,
        run_nonce=f"r{repeat}",
    )
    surfaces = tuple(Surface)
    classes = tuple(InfluenceClass)
    for record_ordinal in range(100):
        root_id = f"root-control-{record_ordinal:03d}"
        engine.install_record(
            CanonicalRecord(
                record_id=root_id,
                version=1,
                payload_symbol=f"opaque-control-{record_ordinal:03d}-v1",
                domain=DOMAIN_MAIN,
                scopes=frozenset({PRINCIPAL_ALPHA}),
                grants=frozenset({PRINCIPAL_ALPHA}),
            )
        )
        for surface_ordinal, surface in enumerate(surfaces):
            artifact_id = f"{surface.value}-control-{record_ordinal:03d}"
            engine.install_blueprint(
                ArtifactBlueprint(
                    artifact_id=artifact_id,
                    surface=surface,
                    domain=DOMAIN_MAIN,
                    dependencies=(
                        Dependency(
                            root_id,
                            classes[surface_ordinal],
                        ),
                    ),
                )
            )
    engine.build_initial()
    old = dict(engine.artifacts)
    engine.mutate(
        MutationKind.CORRECTION,
        record_id="root-control-037",
        payload_symbol=f"opaque-control-037-v2-{repeat}",
    )
    _observe_barrier(engine, old, metrics)
    _randomized_repair(
        engine,
        seed=seed,
        metrics=metrics,
        old_artifacts=old,
    )
    _compare_clean_build(engine, metrics, seed=seed + 1)
    return metrics


CASE_RUNNERS = {
    "M3-C01-CORRECTION-CHAIN": _case_01,
    "M3-C02-SCOPE-NARROWING-FANOUT": _case_02,
    "M3-C03-PERMISSION-REVOCATION-FANIN": _case_03,
    "M3-C04-ORDINARY-DELETE-RESTORE": _case_04,
    "M3-C05-TERMINAL-PURGE-IDENTIFIER-REUSE": _case_05,
    "M3-C06-POLICY-GENERATION-CHANGE": _case_06,
    "M3-C07-PARTIAL-REPAIR-BARRIER": _case_07,
    "M3-C08-STALE-WRITER": _case_08,
    "M3-C09-CYCLE-EDGE-ATTEMPT": _case_09,
    "M3-C10-CROSS-SCOPE-EDGE-ATTEMPT": _case_10,
    "M3-C11-DUPLICATE-AND-CONFLICTING-REPLAY": _case_11,
    "M3-C12-OUT-OF-ORDER-MUTATION": _case_12,
    "M3-C13-SHARED-DESCENDANT-CORRECTION": _case_13,
    "M3-C14-PURGE-DURING-ISSUE": _case_14,
    "M3-C15-OPTIMIZATION-WORK-CONTROL": _case_15,
}


def _run_ablation(name: str) -> dict[str, int]:
    metrics = CaseMetrics()
    if name == "repair_before_withdrawal":
        config = ClosureConfig(withdraw_before_repair=False)
        engine = _engine("chain", nonce="ab-rbw", config=config)
        old = dict(engine.artifacts)
        engine.mutate(
            MutationKind.CORRECTION,
            record_id="root-chain",
            payload_symbol="opaque-ablation-v2",
        )
        _observe_barrier(engine, old, metrics)
    elif name == "direct_edge_only":
        config = ClosureConfig(transitive_closure=False)
        engine = _engine("chain", nonce="ab-direct", config=config)
        old = dict(engine.artifacts)
        engine.mutate(
            MutationKind.CORRECTION,
            record_id="root-chain",
            payload_symbol="opaque-ablation-v2",
        )
        barrier_affected = engine.active_barrier.affected if engine.active_barrier else ()
        stale_unwithdrawn = set(old) - set(barrier_affected)
        metrics.published_stale_descendant_count += sum(
            engine.published(item) is not None for item in stale_unwithdrawn
        )
        _randomized_repair(
            engine,
            seed=1,
            metrics=metrics,
            old_artifacts=old,
        )
        _compare_clean_build(engine, metrics, seed=2)
    elif name == "content_only_lineage":
        config = ClosureConfig(influence_classes=frozenset({InfluenceClass.CONTENT}))
        engine = _engine("fan_out", nonce="ab-content", config=config)
        old = dict(engine.artifacts)
        engine.mutate(
            MutationKind.CORRECTION,
            record_id="root-fan",
            payload_symbol="opaque-ablation-v2",
        )
        unaffected = set(old) - set(engine.active_barrier.affected if engine.active_barrier else ())
        metrics.published_stale_descendant_count += sum(
            engine.published(item) is not None for item in unaffected
        )
    elif name == "generation_only":
        config = ClosureConfig(inventory_enabled=False, erase_derived_on_purge=False)
        engine = _engine("fan_out", nonce="ab-generation", config=config)
        old = dict(engine.artifacts)
        root = engine.records["root-fan"]
        engine.mutate(MutationKind.TERMINAL_PURGE, record_id="root-fan")
        metrics.terminal_purge_residue_count += _purge_residue(
            engine,
            forbidden=[
                root.record_id,
                root.payload_symbol,
                *(item.semantic_commitment for item in old.values()),
            ],
        )
    elif name == "inventory_only":
        config = ClosureConfig(generation_guard=False)
        engine = _engine("shared_descendant", nonce="ab-inventory", config=config)
        stale = engine.artifacts["proc-shared"]
        old_epoch = engine.graph_epoch
        engine.mutate(
            MutationKind.TERMINAL_PURGE,
            record_id="root-shared-a",
        )
        _randomized_repair(
            engine,
            seed=1,
            metrics=metrics,
            old_artifacts={},
        )
        if engine.commit_stale_writer(stale, expected_epoch=old_epoch):
            metrics.stale_writer_accept_count += 1
    elif name == "raw_record_only_purge":
        config = ClosureConfig(
            withdraw_before_repair=False,
            erase_derived_on_purge=False,
        )
        engine = _engine("fan_out", nonce="ab-raw", config=config)
        old = dict(engine.artifacts)
        root = engine.records["root-fan"]
        engine.mutate(MutationKind.TERMINAL_PURGE, record_id="root-fan")
        metrics.fail_open_publication_count += sum(
            engine.published(item) is not None for item in old
        )
        metrics.terminal_purge_residue_count += _purge_residue(
            engine,
            forbidden=[
                root.record_id,
                root.payload_symbol,
                *(item.semantic_commitment for item in old.values()),
            ],
        )
    else:
        raise ValueError(f"unknown ablation: {name}")
    return {
        key: value for key, value in asdict(metrics).items() if value and key.endswith("_count")
    }


def _run_missing_edge_fault() -> dict[str, int]:
    metrics = CaseMetrics()
    engine = _engine("chain", nonce="fault-missing")
    old = dict(engine.artifacts)
    engine.inject_missing_inventory_edge("root-chain", "sel-chain")
    engine.mutate(
        MutationKind.CORRECTION,
        record_id="root-chain",
        payload_symbol="opaque-fault-v2",
    )
    barrier_affected = engine.active_barrier.affected if engine.active_barrier else ()
    stale_unwithdrawn = set(old) - set(barrier_affected)
    metrics.published_stale_descendant_count += sum(
        engine.published(item) is not None for item in stale_unwithdrawn
    )
    _randomized_repair(
        engine,
        seed=1,
        metrics=metrics,
        old_artifacts=old,
    )
    _compare_clean_build(engine, metrics, seed=2)
    return {
        key: value for key, value in asdict(metrics).items() if value and key.endswith("_count")
    }


def run_experiment(*, repeats: int = DEFAULT_REPEATS) -> dict[str, Any]:
    """Execute all frozen F02 M3 cases, faults, and decisive ablations."""

    execution_origin = verify_local_module_origin()
    if repeats < 1:
        raise ValueError("repeats must be positive")
    fixture = load_fixture()
    case_reports: list[dict[str, Any]] = []
    total = CaseMetrics()
    seeds: list[int] = fixture["seeds"]
    for case_ordinal, (case_id, runner) in enumerate(CASE_RUNNERS.items()):
        aggregate = CaseMetrics()
        for repeat in range(repeats):
            seed = seeds[repeat % len(seeds)] + (case_ordinal * 1_000) + repeat
            aggregate.add(runner(repeat, seed))
        total.add(aggregate)
        observations = aggregate.publication_observations
        case_reports.append(
            {
                "case_id": case_id,
                "f02_invariants": list(F02_INVARIANTS[case_id]),
                "repeats": repeats,
                "verdict": "PASS" if aggregate.required_zero_total() == 0 else "FAIL",
                "safety_metrics": {
                    key: value for key, value in asdict(aggregate).items() if key.endswith("_count")
                },
                "work_metrics": {
                    "descendants_scanned": aggregate.descendants_scanned,
                    "descendants_rebuilt": aggregate.descendants_rebuilt,
                    "full_rebuild_nodes_evaluated": (aggregate.full_rebuild_nodes_evaluated),
                },
                "purge_boundary": {
                    "exclusive_descendant_ids_checked": (
                        aggregate.purge_exclusive_descendant_ids_checked
                    ),
                    "shared_descendant_recipes_validated": (
                        aggregate.purge_shared_descendant_recipes_validated
                    ),
                },
                "availability": {
                    "safe_barrier_observation_fraction": (
                        aggregate.safe_publication_observations / observations
                        if observations
                        else 1.0
                    ),
                    "final_eligible_fraction_of_full_rebuild": (
                        aggregate.final_eligible_artifacts
                        / aggregate.full_rebuild_eligible_artifacts
                        if aggregate.full_rebuild_eligible_artifacts
                        else 1.0
                    ),
                },
            }
        )
    c15 = next(
        item for item in case_reports if item["case_id"] == "M3-C15-OPTIMIZATION-WORK-CONTROL"
    )
    work = c15["work_metrics"]
    full_work = work["full_rebuild_nodes_evaluated"]
    reduction = 1.0 - (work["descendants_rebuilt"] / full_work) if full_work else 0.0
    ablations = {name: _run_ablation(name) for name in fixture["ablations"]}
    injected_faults = {
        "missing_inventory_edge": _run_missing_edge_fault(),
        "stale_writer": next(
            item["safety_metrics"]
            for item in case_reports
            if item["case_id"] == "M3-C08-STALE-WRITER"
        ),
    }
    hard_failures = total.required_zero_total()
    complete_coverage = len(case_reports) == len(F02_INVARIANTS)
    if hard_failures:
        decision = "KILL_M3_MECHANISM"
    elif not complete_coverage:
        decision = "HOLD_M3_CLOSURE"
    elif reduction < 0.25:
        decision = "RETAIN_M3_CONTRACT_HOLD_OPTIMIZATION"
    else:
        decision = "RETAIN_M3_CONTRACT_AND_OPTIMIZATION"
    ablations_decisive = all(bool(result) for result in ablations.values())
    report = {
        "schema": REPORT_SCHEMA,
        "evidence_level": "L1",
        "fixture_schema": fixture["schema"],
        "f02_oracle_commit": "a866ad5b9d17a72d73d2dca4de4dd8be1e71ca9e",
        "governance_base_commit": "f545c37157845f0bd402215719cb8c747b7fc21d",
        "execution_origin_attestation": execution_origin,
        "synthetic_only": True,
        "repeats_per_case": repeats,
        "deterministic_seeds": seeds,
        "case_count": len(case_reports),
        "case_coverage_fraction": len(case_reports) / len(F02_INVARIANTS),
        "surface_coverage_fraction": len(fixture["surfaces"]) / len(Surface),
        "cases": case_reports,
        "decisive_metrics": {
            key: value for key, value in asdict(total).items() if key.endswith("_count")
        },
        "work_control": {
            "optimized_descendants_scanned": work["descendants_scanned"],
            "optimized_descendants_rebuilt": work["descendants_rebuilt"],
            "full_rebuild_nodes_evaluated": full_work,
            "evaluated_node_reduction_fraction": reduction,
            "retain_optimization_minimum": 0.25,
        },
        "availability": {
            "safe_barrier_observation_fraction": (
                total.safe_publication_observations / total.publication_observations
                if total.publication_observations
                else 1.0
            ),
            "final_eligible_fraction_of_full_rebuild": (
                total.final_eligible_artifacts / total.full_rebuild_eligible_artifacts
                if total.full_rebuild_eligible_artifacts
                else 1.0
            ),
        },
        "purge_boundary": {
            "exclusive_descendant_ids_checked": (total.purge_exclusive_descendant_ids_checked),
            "shared_descendant_recipes_validated": (
                total.purge_shared_descendant_recipes_validated
            ),
            "declared_boundary_includes_graph_inventory": True,
        },
        "ablations": ablations,
        "ablations_decisive": ablations_decisive,
        "injected_faults": injected_faults,
        "decision": decision,
        "validity_limitations": [
            "isolated_deterministic_symbolic_evidence_only",
            "no_operator_core_personal_context_credentials_network_models_or_real_actions",
            "six_declared_surfaces_do_not_prove_unknown_production_surfaces_absent",
            "full_rebuild_is_independently_coded_but_shares_the_frozen_artifact_semantics",
            "opaque_sha256_commitments_are_synthetic_semantics_not_privacy_redactions",
            "availability_is_logical_publication_eligibility_not_wall_clock_latency",
            "generation_barrier_models_disconnected_stale_state_without_external_clients",
        ],
    }
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render the checked-in human-readable M3 result."""

    metrics = report["decisive_metrics"]
    work = report["work_control"]
    lines = [
        "# Memory Lab Wave 4 M3 dependency-complete influence closure",
        "",
        "## Result",
        "",
        f"Decision: `{report['decision']}`.",
        "",
        (
            f"All {report['case_count']} frozen F02 M3 cases ran "
            f"{report['repeats_per_case']} deterministic repeats with complete "
            "case and six-surface coverage."
        ),
        "",
        "## Safety and equivalence",
        "",
        "| Metric | Count |",
        "|---|---:|",
    ]
    for name, value in metrics.items():
        lines.append(f"| `{name}` | {value} |")
    lines.extend(
        [
            "",
            "## Work and availability",
            "",
            f"- Optimized descendants scanned: `{work['optimized_descendants_scanned']}`.",
            f"- Optimized descendants rebuilt: `{work['optimized_descendants_rebuilt']}`.",
            f"- Full-rebuild nodes evaluated: `{work['full_rebuild_nodes_evaluated']}`.",
            (
                "- Evaluated-node reduction: "
                f"`{work['evaluated_node_reduction_fraction']:.6f}` "
                "(frozen retain threshold `0.25`)."
            ),
            (
                "- Safe barrier observations: "
                f"`{report['availability']['safe_barrier_observation_fraction']:.6f}`."
            ),
            (
                "- Final eligible fraction of full rebuild: "
                f"`{report['availability']['final_eligible_fraction_of_full_rebuild']:.6f}`."
            ),
            (
                "- Exclusive purge-descendant identifiers checked across the full "
                "inspectable boundary, including graph inventory: "
                f"`{report['purge_boundary']['exclusive_descendant_ids_checked']}`."
            ),
            (
                "- Shared descendant recipes validated after reconstruction solely "
                "from retained support: "
                f"`{report['purge_boundary']['shared_descendant_recipes_validated']}`."
            ),
            "",
            "## F02 coverage",
            "",
            "| F02 case | Invariants | Verdict |",
            "|---|---|---|",
        ]
    )
    for case in report["cases"]:
        lines.append(
            f"| `{case['case_id']}` | {', '.join(case['f02_invariants'])} | `{case['verdict']}` |"
        )
    lines.extend(
        [
            "",
            "## Execution origin",
            "",
            (
                "- Governance base: "
                f"`{report['execution_origin_attestation']['governance_base_sha']}`."
            ),
            (
                "- Import origin verified to worker worktree: "
                f"`{str(report['execution_origin_attestation']['import_origin_verified_to_worker_worktree']).lower()}`."
            ),
            (
                "- Repository-relative imported modules: "
                + ", ".join(
                    f"`{item}`"
                    for item in report["execution_origin_attestation"]["imported_module_paths"]
                )
                + "."
            ),
            "",
            "## Decisive faults and ablations",
            "",
        ]
    )
    for name, result in report["ablations"].items():
        lines.append(f"- `{name}`: preserved failures `{json.dumps(result, sort_keys=True)}`.")
    for name, result in report["injected_faults"].items():
        nonzero = {key: value for key, value in result.items() if value}
        lines.append(
            f"- Injected `{name}`: "
            f"`{json.dumps(nonzero, sort_keys=True) if nonzero else 'rejected safely'}`."
        )
    lines.extend(
        [
            "",
            "## Limits",
            "",
            *[f"- `{item}`." for item in report["validity_limitations"]],
            "",
            "This is research-only L1 synthetic evidence. It does not change production "
            "behavior or establish hidden-state erasure, unknown-surface completeness, "
            "real-client compliance, cross-platform behavior, or product readiness.",
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEATS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_experiment(repeats=args.repeats)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8", newline="\n")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(
            render_markdown(report),
            encoding="utf-8",
            newline="\n",
        )
    return int(
        report["decision"] in {"KILL_M3_MECHANISM", "HOLD_M3_CLOSURE"}
        or not report["ablations_decisive"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
