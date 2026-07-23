"""Deterministic symbolic prototype for Memory Lab M3 influence closure.

This module is research-only.  It is not wired into Core, retrieval, MCP, or
any operator data.  Canonical inputs are opaque synthetic symbols.  The
incremental mechanism and the clean-build control deliberately use separate
graph-walk and artifact-construction implementations.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum


class Surface(StrEnum):
    """The complete inspectable M3 derived boundary."""

    RETRIEVAL_SELECTION = "retrieval_selection"
    ISSUED_CONTEXT = "issued_context"
    PROCEDURE = "procedure"
    SELECTION_CACHE = "selection_cache"
    WORKING_STATE = "working_state"
    USE_STATISTICS = "use_statistics"


class InfluenceClass(StrEnum):
    """Why one node can affect another."""

    CONTENT = "content"
    SELECTION = "selection"
    NEGATIVE_DECISION = "negative_decision"
    STATISTIC = "statistic"
    WORKING_STATE = "working_state"
    ISSUED_INTERVENTION = "issued_intervention"


class MutationKind(StrEnum):
    """Canonical changes covered by the frozen Wave 4 contract."""

    CORRECTION = "correction"
    SCOPE_NARROWING = "scope_narrowing"
    PERMISSION_REVOCATION = "permission_revocation"
    ORDINARY_DELETE = "ordinary_delete"
    TERMINAL_PURGE = "terminal_purge"
    POLICY_GENERATION_CHANGE = "policy_generation_change"


class FailureReason(StrEnum):
    """Content-free failure vocabulary."""

    CYCLE_REJECTED = "cycle_rejected"
    CROSS_SCOPE_REJECTED = "cross_scope_rejected"
    STALE_WRITER_REJECTED = "stale_writer_rejected"
    CONFLICTING_REPLAY_REJECTED = "conflicting_replay_rejected"
    OUT_OF_ORDER_REJECTED = "out_of_order_rejected"
    PURGED_TARGET_REJECTED = "purged_target_rejected"
    INCOMPLETE_REPAIR_REJECTED = "incomplete_repair_rejected"


@dataclass(frozen=True, slots=True)
class CanonicalRecord:
    """One Core-owned synthetic canonical root."""

    record_id: str
    version: int
    payload_symbol: str
    domain: str
    scopes: frozenset[str]
    grants: frozenset[str]
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class Dependency:
    """One declared direct predecessor and its influence class."""

    predecessor_id: str
    influence_class: InfluenceClass


@dataclass(frozen=True, slots=True)
class ArtifactBlueprint:
    """Discardable recipe for one derived artifact."""

    artifact_id: str
    surface: Surface
    domain: str
    dependencies: tuple[Dependency, ...]


@dataclass(frozen=True, slots=True)
class DerivedArtifact:
    """One immutable derived value eligible for publication."""

    artifact_id: str
    surface: Surface
    domain: str
    source_versions: tuple[tuple[str, int], ...]
    scopes: tuple[str, ...]
    grants: tuple[str, ...]
    policy_generation: int
    semantic_commitment: str
    graph_epoch: int


@dataclass(frozen=True, slots=True)
class FailureReceipt:
    """Privacy-bounded diagnostic with a per-run opaque reference."""

    reason: FailureReason
    per_run_artifact_ref: str
    count: int = 1


@dataclass(frozen=True, slots=True)
class Barrier:
    """One barrier whose affected descendants remain withdrawn until commit."""

    barrier_ref: str
    graph_epoch: int
    affected: frozenset[str]
    cause: MutationKind


@dataclass(frozen=True, slots=True)
class ClosureConfig:
    """Fault switches used only by the falsification harness."""

    transitive_closure: bool = True
    influence_classes: frozenset[InfluenceClass] = field(
        default_factory=lambda: frozenset(InfluenceClass)
    )
    withdraw_before_repair: bool = True
    inventory_enabled: bool = True
    generation_guard: bool = True
    erase_derived_on_purge: bool = True


@dataclass(frozen=True, slots=True)
class MutationReceipt:
    """Opaque mutation result returned to the harness."""

    barrier_ref: str
    graph_epoch: int
    affected_count: int


class IncrementalInfluenceClosure:
    """Barrier-first incremental closure over declared dependency inventory."""

    def __init__(
        self,
        *,
        principal: str,
        policy_generation: int = 1,
        run_nonce: str = "run",
        config: ClosureConfig | None = None,
    ) -> None:
        self.principal = principal
        self.policy_generation = policy_generation
        self.run_nonce = run_nonce
        self.config = config or ClosureConfig()
        self.records: dict[str, CanonicalRecord] = {}
        self.blueprints: dict[str, ArtifactBlueprint] = {}
        self.artifacts: dict[str, DerivedArtifact] = {}
        self._reverse: dict[str, set[str]] = defaultdict(set)
        self._pending: dict[str, DerivedArtifact | None] = {}
        self._barrier: Barrier | None = None
        self._graph_epoch = 0
        self._minimum_generation = 0
        self._record_counter = 0
        self._receipt_counter = 0
        self._failure_receipts: list[FailureReceipt] = []
        self._accepted_events: dict[str, str] = {}
        self._last_sequence = 0
        self._use_event_ids: set[str] = set()
        self.use_event_count = 0
        self.aggregate_invalidation_count = 0
        self.descendants_scanned = 0
        self.descendants_rebuilt = 0

    @property
    def graph_epoch(self) -> int:
        return self._graph_epoch

    @property
    def minimum_generation(self) -> int:
        return self._minimum_generation

    @property
    def active_barrier(self) -> Barrier | None:
        return self._barrier

    @property
    def failure_receipts(self) -> tuple[FailureReceipt, ...]:
        return tuple(self._failure_receipts)

    def install_record(self, record: CanonicalRecord) -> None:
        """Install a synthetic canonical root during fixture construction."""

        if record.record_id in self.records or record.record_id in self.blueprints:
            raise ValueError("node identifiers must be unique")
        if record.version < 1 or not record.payload_symbol:
            raise ValueError("canonical records require positive versions and opaque payloads")
        self.records[record.record_id] = record

    def create_record(
        self,
        *,
        requested_id: str,
        payload_symbol: str,
        domain: str,
        scopes: frozenset[str],
        grants: frozenset[str],
    ) -> str:
        """Create a fresh non-caller-controlled lineage after any purge."""

        del requested_id
        self._record_counter += 1
        record_id = (
            f"root-{self.run_nonce}-{self._minimum_generation}-{self._record_counter}"
        )
        self.install_record(
            CanonicalRecord(
                record_id=record_id,
                version=1,
                payload_symbol=payload_symbol,
                domain=domain,
                scopes=scopes,
                grants=grants,
            )
        )
        return record_id

    def install_blueprint(self, blueprint: ArtifactBlueprint) -> None:
        """Install an acyclic same-domain recipe atomically."""

        if blueprint.artifact_id in self.records or blueprint.artifact_id in self.blueprints:
            raise ValueError("node identifiers must be unique")
        if not blueprint.dependencies:
            raise ValueError("derived artifacts require at least one predecessor")
        known = set(self.records) | set(self.blueprints)
        if any(item.predecessor_id not in known for item in blueprint.dependencies):
            raise ValueError("blueprint predecessors must already exist")
        predecessor_domains = {
            self._node_domain(item.predecessor_id) for item in blueprint.dependencies
        }
        if predecessor_domains != {blueprint.domain}:
            raise ValueError("cross-domain blueprints require an explicit bridge")
        self.blueprints[blueprint.artifact_id] = blueprint
        for item in blueprint.dependencies:
            self._reverse[item.predecessor_id].add(blueprint.artifact_id)

    def build_initial(self) -> None:
        """Build every fixture artifact in deterministic topological order."""

        for artifact_id in self.topological_artifact_ids():
            artifact = self._evaluate_incremental(artifact_id, {})
            if artifact is not None:
                self.artifacts[artifact_id] = artifact

    def topological_artifact_ids(self) -> tuple[str, ...]:
        """Return a deterministic topological order without using reverse closure."""

        remaining = set(self.blueprints)
        resolved = set(self.records)
        ordered: list[str] = []
        while remaining:
            ready = sorted(
                artifact_id
                for artifact_id in remaining
                if all(
                    dependency.predecessor_id in resolved
                    for dependency in self.blueprints[artifact_id].dependencies
                )
            )
            if not ready:
                raise ValueError("blueprint graph is cyclic")
            for artifact_id in ready:
                ordered.append(artifact_id)
                resolved.add(artifact_id)
                remaining.remove(artifact_id)
        return tuple(ordered)

    def try_add_edge(
        self,
        predecessor_id: str,
        successor_id: str,
    ) -> bool:
        """Attempt one edge transaction, rejecting cycles and cross-domain edges."""

        before = self.graph_snapshot()
        if predecessor_id not in self._all_nodes() or successor_id not in self._all_nodes():
            raise KeyError("edge endpoints must exist")
        if (
            successor_id in self.records
            or self._node_domain(predecessor_id) != self._node_domain(successor_id)
            or self._reachable(successor_id, predecessor_id)
        ):
            reason = (
                FailureReason.CROSS_SCOPE_REJECTED
                if self._node_domain(predecessor_id) != self._node_domain(successor_id)
                else FailureReason.CYCLE_REJECTED
            )
            self._record_failure(reason)
            if self.graph_snapshot() != before:
                raise AssertionError("rejected edge altered graph state")
            return False
        blueprint = self.blueprints[successor_id]
        dependency = Dependency(predecessor_id, InfluenceClass.CONTENT)
        if dependency in blueprint.dependencies:
            return True
        self.blueprints[successor_id] = replace(
            blueprint,
            dependencies=(*blueprint.dependencies, dependency),
        )
        self._reverse[predecessor_id].add(successor_id)
        return True

    def mutate(
        self,
        kind: MutationKind,
        *,
        record_id: str | None = None,
        payload_symbol: str | None = None,
        scopes: frozenset[str] | None = None,
        grants: frozenset[str] | None = None,
        policy_generation: int | None = None,
    ) -> MutationReceipt:
        """Commit a barrier, withdraw the full closure, then change canonical state."""

        if self._barrier is not None:
            raise RuntimeError("only one mutation barrier may be active")
        if kind is MutationKind.POLICY_GENERATION_CHANGE:
            seeds = set()
            affected = set(self.blueprints)
        else:
            if record_id is None or record_id not in self.records:
                self._record_failure(FailureReason.PURGED_TARGET_REJECTED)
                raise KeyError("mutation target is not canonical")
            seeds = {record_id}
            affected = self._affected_descendants(seeds)
        self._graph_epoch += 1
        self._receipt_counter += 1
        barrier = Barrier(
            barrier_ref=f"barrier-{self.run_nonce}-{self._receipt_counter}",
            graph_epoch=self._graph_epoch,
            affected=frozenset(affected),
            cause=kind,
        )
        self._barrier = barrier
        if self.config.withdraw_before_repair:
            for artifact_id in affected:
                self.artifacts.pop(artifact_id, None)

        if kind is MutationKind.POLICY_GENERATION_CHANGE:
            if policy_generation is None or policy_generation <= self.policy_generation:
                raise ValueError("policy generation must advance")
            self.policy_generation = policy_generation
        else:
            assert record_id is not None
            record = self.records[record_id]
            if kind is MutationKind.CORRECTION:
                if payload_symbol is None:
                    raise ValueError("correction requires a new opaque payload")
                self.records[record_id] = replace(
                    record,
                    version=record.version + 1,
                    payload_symbol=payload_symbol,
                )
            elif kind is MutationKind.SCOPE_NARROWING:
                if scopes is None or not scopes < record.scopes:
                    raise ValueError("scope narrowing must be a strict subset")
                self.records[record_id] = replace(record, scopes=scopes)
            elif kind is MutationKind.PERMISSION_REVOCATION:
                if grants is None or not grants < record.grants:
                    raise ValueError("permission revocation must be a strict subset")
                self.records[record_id] = replace(record, grants=grants)
            elif kind is MutationKind.ORDINARY_DELETE:
                self.records[record_id] = replace(record, deleted=True)
            elif kind is MutationKind.TERMINAL_PURGE:
                self._terminal_purge(record_id, record, affected)
                self._barrier = replace(
                    barrier,
                    affected=frozenset(
                        artifact_id
                        for artifact_id in affected
                        if artifact_id in self.blueprints
                    ),
                )
        return MutationReceipt(
            barrier_ref=barrier.barrier_ref,
            graph_epoch=barrier.graph_epoch,
            affected_count=len(affected),
        )

    def restore(self, record_id: str) -> bool:
        """Restore only a reversible ordinary deletion."""

        record = self.records.get(record_id)
        if record is None:
            self._record_failure(FailureReason.PURGED_TARGET_REJECTED)
            return False
        if not record.deleted:
            return False
        self.records[record_id] = replace(record, deleted=False)
        return True

    def repair_one(self, artifact_id: str) -> bool:
        """Evaluate one affected artifact into hidden pending state."""

        barrier = self._require_barrier()
        if artifact_id not in barrier.affected or artifact_id not in self.blueprints:
            return False
        if not self._predecessors_resolved(artifact_id):
            return False
        artifact = self._evaluate_incremental(artifact_id, self._pending)
        self._pending[artifact_id] = artifact
        self.descendants_rebuilt += 1
        if not self.config.withdraw_before_repair and artifact is not None:
            self.artifacts[artifact_id] = artifact
        return True

    def finalize_repair(self) -> bool:
        """Publish an affected generation only after the whole closure resolves."""

        barrier = self._require_barrier()
        expected = {
            item for item in barrier.affected if item in self.blueprints
        }
        if not expected <= self._pending.keys():
            self._record_failure(FailureReason.INCOMPLETE_REPAIR_REJECTED)
            return False
        for artifact_id in expected:
            self.artifacts.pop(artifact_id, None)
        for artifact_id, artifact in self._pending.items():
            if artifact is not None:
                self.artifacts[artifact_id] = artifact
        self._pending.clear()
        self._barrier = None
        return True

    def commit_stale_writer(
        self,
        artifact: DerivedArtifact,
        *,
        expected_epoch: int,
    ) -> bool:
        """Apply optimistic concurrency at the publication boundary."""

        if self.config.generation_guard and (
            expected_epoch != self._graph_epoch
            or artifact.graph_epoch != self._graph_epoch
            or expected_epoch < self._minimum_generation
        ):
            self._record_failure(FailureReason.STALE_WRITER_REJECTED)
            return False
        self.artifacts[artifact.artifact_id] = artifact
        return True

    def accept_ordered_event(
        self,
        *,
        sequence: int,
        event_id: str,
        body_digest: str,
        generation: int | None = None,
    ) -> str:
        """Accept ordered idempotent events and reject conflicts fail closed."""

        if generation is not None and generation < self._minimum_generation:
            self._record_failure(FailureReason.OUT_OF_ORDER_REJECTED)
            return "out_of_order"
        existing = self._accepted_events.get(event_id)
        if existing is not None:
            if existing == body_digest:
                return "duplicate"
            self._record_failure(FailureReason.CONFLICTING_REPLAY_REJECTED)
            return "conflict"
        if sequence != self._last_sequence + 1:
            self._record_failure(FailureReason.OUT_OF_ORDER_REJECTED)
            return "out_of_order"
        self._accepted_events[event_id] = body_digest
        self._last_sequence = sequence
        return "accepted"

    def record_use(self, *, event_id: str, issue_id: str, epoch: int) -> bool:
        """Count an observable use once only while its issue remains eligible."""

        if event_id in self._use_event_ids:
            return True
        issue = self.artifacts.get(issue_id)
        if (
            issue is None
            or issue.surface is not Surface.ISSUED_CONTEXT
            or epoch != self._graph_epoch
        ):
            return False
        self._use_event_ids.add(event_id)
        self.use_event_count += 1
        return True

    def published(self, artifact_id: str) -> DerivedArtifact | None:
        """Return only barrier-safe published state."""

        if (
            self.config.withdraw_before_repair
            and self._barrier is not None
            and artifact_id in self._barrier.affected
        ):
            return None
        return self.artifacts.get(artifact_id)

    def observable_state(self) -> tuple[tuple[object, ...], ...]:
        """Return the complete eligible state for clean-build comparison."""

        return tuple(
            (
                artifact.artifact_id,
                artifact.surface.value,
                artifact.domain,
                artifact.source_versions,
                artifact.scopes,
                artifact.grants,
                artifact.policy_generation,
                artifact.semantic_commitment,
            )
            for artifact in sorted(self.artifacts.values(), key=lambda item: item.artifact_id)
            if self.published(artifact.artifact_id) is not None
        )

    def graph_snapshot(self) -> tuple[tuple[str, tuple[tuple[str, str], ...]], ...]:
        """Return a stable graph representation for atomic-rejection checks."""

        return tuple(
            (
                artifact_id,
                tuple(
                    (item.predecessor_id, item.influence_class.value)
                    for item in blueprint.dependencies
                ),
            )
            for artifact_id, blueprint in sorted(self.blueprints.items())
        )

    def privacy_boundary(self) -> Mapping[str, object]:
        """Expose the entire declared synthetic inspection boundary."""

        return {
            "records": tuple(
                (
                    record.record_id,
                    record.version,
                    record.payload_symbol,
                    tuple(sorted(record.scopes)),
                    tuple(sorted(record.grants)),
                    record.deleted,
                )
                for record in sorted(self.records.values(), key=lambda item: item.record_id)
            ),
            "blueprints": self.graph_snapshot(),
            "artifacts": state_from_artifacts(self.artifacts),
            "pending": tuple(
                (
                    artifact_id,
                    None
                    if artifact is None
                    else (
                        artifact.source_versions,
                        artifact.semantic_commitment,
                        artifact.policy_generation,
                    ),
                )
                for artifact_id, artifact in sorted(self._pending.items())
            ),
            "active_barrier": (
                None
                if self._barrier is None
                else (
                    self._barrier.barrier_ref,
                    self._barrier.graph_epoch,
                    tuple(sorted(self._barrier.affected)),
                    self._barrier.cause.value,
                )
            ),
            "accepted_event_digests": tuple(sorted(self._accepted_events.values())),
            "failure_receipts": tuple(
                (item.reason.value, item.per_run_artifact_ref, item.count)
                for item in self._failure_receipts
            ),
            "minimum_generation": self._minimum_generation,
            "aggregate_invalidation_count": self.aggregate_invalidation_count,
        }

    def inject_missing_inventory_edge(
        self,
        predecessor_id: str,
        successor_id: str,
    ) -> None:
        """Remove only reverse inventory while retaining the clean-build recipe."""

        self._reverse[predecessor_id].discard(successor_id)

    def _affected_descendants(self, seeds: set[str]) -> set[str]:
        if not self.config.inventory_enabled:
            all_artifacts = set(self.artifacts)
            self.descendants_scanned += len(all_artifacts)
            return all_artifacts
        affected: set[str] = set()
        queue: deque[str] = deque(seeds)
        while queue:
            predecessor = queue.popleft()
            successors = sorted(self._reverse.get(predecessor, ()))
            self.descendants_scanned += len(successors)
            for successor in successors:
                blueprint = self.blueprints[successor]
                edge = next(
                    (
                        item
                        for item in blueprint.dependencies
                        if item.predecessor_id == predecessor
                    ),
                    None,
                )
                if edge is None or edge.influence_class not in self.config.influence_classes:
                    continue
                if successor in affected:
                    continue
                affected.add(successor)
                if self.config.transitive_closure:
                    queue.append(successor)
        return affected

    def _terminal_purge(
        self,
        record_id: str,
        record: CanonicalRecord,
        affected: set[str],
    ) -> None:
        self._minimum_generation = self._graph_epoch
        del self.records[record_id]
        self.aggregate_invalidation_count += 1
        self._accepted_events.clear()
        self._last_sequence = 0
        self._use_event_ids.clear()
        self._failure_receipts.clear()
        if not self.config.erase_derived_on_purge:
            return
        for artifact_id in affected:
            self.artifacts.pop(artifact_id, None)
            self._pending.pop(artifact_id, None)
        if self.config.inventory_enabled:
            for artifact_id, blueprint in tuple(self.blueprints.items()):
                kept = tuple(
                    item
                    for item in blueprint.dependencies
                    if item.predecessor_id != record_id
                )
                if kept != blueprint.dependencies:
                    self.blueprints[artifact_id] = replace(
                        blueprint,
                        dependencies=kept,
                    )
            while True:
                unsupported = {
                    artifact_id
                    for artifact_id, blueprint in self.blueprints.items()
                    if not blueprint.dependencies
                }
                if not unsupported:
                    break
                for artifact_id in unsupported:
                    self.blueprints.pop(artifact_id)
                for artifact_id, blueprint in tuple(self.blueprints.items()):
                    kept = tuple(
                        item
                        for item in blueprint.dependencies
                        if item.predecessor_id not in unsupported
                    )
                    if kept != blueprint.dependencies:
                        self.blueprints[artifact_id] = replace(
                            blueprint,
                            dependencies=kept,
                        )
            self._reverse.clear()
            for artifact_id, blueprint in self.blueprints.items():
                for dependency in blueprint.dependencies:
                    self._reverse[dependency.predecessor_id].add(artifact_id)
        del record

    def _evaluate_incremental(
        self,
        artifact_id: str,
        pending: Mapping[str, DerivedArtifact | None],
    ) -> DerivedArtifact | None:
        blueprint = self.blueprints[artifact_id]
        inputs: list[tuple[str, str, tuple[tuple[str, int], ...], set[str], set[str]]] = []
        for dependency in blueprint.dependencies:
            predecessor_id = dependency.predecessor_id
            if predecessor_id in self.records:
                record = self.records[predecessor_id]
                if (
                    record.deleted
                    or self.principal not in record.scopes
                    or self.principal not in record.grants
                ):
                    continue
                inputs.append(
                    (
                        predecessor_id,
                        record.payload_symbol,
                        ((record.record_id, record.version),),
                        set(record.scopes),
                        set(record.grants),
                    )
                )
                continue
            predecessor = (
                pending.get(predecessor_id)
                if predecessor_id in pending
                else self.artifacts.get(predecessor_id)
            )
            if predecessor is None:
                continue
            inputs.append(
                (
                    predecessor_id,
                    predecessor.semantic_commitment,
                    predecessor.source_versions,
                    set(predecessor.scopes),
                    set(predecessor.grants),
                )
            )
        if not inputs:
            return None
        source_versions = tuple(
            sorted({binding for item in inputs for binding in item[2]})
        )
        scopes = tuple(sorted(set().union(*(item[3] for item in inputs))))
        grants = tuple(sorted(set().union(*(item[4] for item in inputs))))
        commitment = _incremental_commitment(
            artifact_id=artifact_id,
            surface=blueprint.surface,
            policy_generation=self.policy_generation,
            inputs=((item[0], item[1]) for item in inputs),
            source_versions=source_versions,
        )
        return DerivedArtifact(
            artifact_id=artifact_id,
            surface=blueprint.surface,
            domain=blueprint.domain,
            source_versions=source_versions,
            scopes=scopes,
            grants=grants,
            policy_generation=self.policy_generation,
            semantic_commitment=commitment,
            graph_epoch=self._graph_epoch,
        )

    def _predecessors_resolved(self, artifact_id: str) -> bool:
        blueprint = self.blueprints[artifact_id]
        barrier = self._require_barrier()
        for dependency in blueprint.dependencies:
            predecessor_id = dependency.predecessor_id
            if (
                predecessor_id in barrier.affected
                and predecessor_id in self.blueprints
                and predecessor_id not in self._pending
            ):
                return False
        return True

    def _record_failure(self, reason: FailureReason) -> None:
        self._receipt_counter += 1
        self._failure_receipts.append(
            FailureReceipt(
                reason=reason,
                per_run_artifact_ref=(
                    f"ref-{self.run_nonce}-{self._minimum_generation}-"
                    f"{self._receipt_counter}"
                ),
            )
        )

    def _require_barrier(self) -> Barrier:
        if self._barrier is None:
            raise RuntimeError("no active repair barrier")
        return self._barrier

    def _all_nodes(self) -> set[str]:
        return set(self.records) | set(self.blueprints)

    def _node_domain(self, node_id: str) -> str:
        if node_id in self.records:
            return self.records[node_id].domain
        return self.blueprints[node_id].domain

    def _reachable(self, start: str, target: str) -> bool:
        queue = deque([start])
        seen: set[str] = set()
        while queue:
            item = queue.popleft()
            if item == target:
                return True
            if item in seen:
                continue
            seen.add(item)
            queue.extend(sorted(self._reverse.get(item, ())))
        return False


class FullRebuildOracle:
    """Independent clean-build control that never reads incremental artifacts."""

    def __init__(
        self,
        *,
        records: Mapping[str, CanonicalRecord],
        blueprints: Mapping[str, ArtifactBlueprint],
        principal: str,
        policy_generation: int,
        graph_epoch: int,
    ) -> None:
        self._records = dict(records)
        self._blueprints = dict(blueprints)
        self._principal = principal
        self._policy_generation = policy_generation
        self._graph_epoch = graph_epoch
        self.evaluated_nodes = 0

    def rebuild(
        self,
        *,
        schedule: Sequence[str] | None = None,
    ) -> dict[str, DerivedArtifact]:
        """Discard and recompute derived state using fixed-point evaluation."""

        output: dict[str, DerivedArtifact] = {}
        ordering = tuple(schedule or sorted(self._blueprints, reverse=True))
        unresolved = set(self._blueprints)
        while unresolved:
            progressed = False
            for artifact_id in ordering:
                if artifact_id not in unresolved:
                    continue
                blueprint = self._blueprints[artifact_id]
                if any(
                    dependency.predecessor_id in unresolved
                    for dependency in blueprint.dependencies
                    if dependency.predecessor_id in self._blueprints
                ):
                    continue
                self.evaluated_nodes += 1
                artifact = self._evaluate_clean(blueprint, output)
                if artifact is not None:
                    output[artifact_id] = artifact
                unresolved.remove(artifact_id)
                progressed = True
            if not progressed:
                raise ValueError("clean-build blueprint graph contains a cycle")
        return output

    def observable_state(
        self,
        artifacts: Mapping[str, DerivedArtifact],
    ) -> tuple[tuple[object, ...], ...]:
        """Enumerate every clean-build surface and comparison field."""

        rows: list[tuple[object, ...]] = []
        for artifact_id in sorted(artifacts):
            artifact = artifacts[artifact_id]
            rows.append(
                (
                    artifact.artifact_id,
                    artifact.surface.value,
                    artifact.domain,
                    artifact.source_versions,
                    artifact.scopes,
                    artifact.grants,
                    artifact.policy_generation,
                    artifact.semantic_commitment,
                )
            )
        return tuple(rows)

    def _evaluate_clean(
        self,
        blueprint: ArtifactBlueprint,
        built: Mapping[str, DerivedArtifact],
    ) -> DerivedArtifact | None:
        input_rows: list[
            tuple[str, str, tuple[tuple[str, int], ...], frozenset[str], frozenset[str]]
        ] = []
        for dependency in blueprint.dependencies:
            root = self._records.get(dependency.predecessor_id)
            if root is not None:
                if (
                    not root.deleted
                    and self._principal in root.scopes
                    and self._principal in root.grants
                ):
                    input_rows.append(
                        (
                            root.record_id,
                            root.payload_symbol,
                            ((root.record_id, root.version),),
                            root.scopes,
                            root.grants,
                        )
                    )
                continue
            ancestor = built.get(dependency.predecessor_id)
            if ancestor is not None:
                input_rows.append(
                    (
                        ancestor.artifact_id,
                        ancestor.semantic_commitment,
                        ancestor.source_versions,
                        frozenset(ancestor.scopes),
                        frozenset(ancestor.grants),
                    )
                )
        if not input_rows:
            return None
        versions = tuple(sorted({pair for row in input_rows for pair in row[2]}))
        visible_scopes = tuple(sorted({scope for row in input_rows for scope in row[3]}))
        visible_grants = tuple(sorted({grant for row in input_rows for grant in row[4]}))
        encoded_inputs = sorted((row[0], row[1]) for row in input_rows)
        pieces = [
            blueprint.artifact_id,
            blueprint.surface.value,
            str(self._policy_generation),
            *(f"{node_id}={value}" for node_id, value in encoded_inputs),
            *(f"{root_id}@{version}" for root_id, version in versions),
        ]
        clean_digest = hashlib.sha256()
        clean_digest.update(b"atc-m3-semantic-v1|")
        clean_digest.update("|".join(pieces).encode())
        return DerivedArtifact(
            artifact_id=blueprint.artifact_id,
            surface=blueprint.surface,
            domain=blueprint.domain,
            source_versions=versions,
            scopes=visible_scopes,
            grants=visible_grants,
            policy_generation=self._policy_generation,
            semantic_commitment=clean_digest.hexdigest(),
            graph_epoch=self._graph_epoch,
        )


def state_from_artifacts(
    artifacts: Mapping[str, DerivedArtifact],
) -> tuple[tuple[object, ...], ...]:
    """Canonicalize a derived-state mapping for test comparisons."""

    return tuple(
        (
            item.artifact_id,
            item.surface.value,
            item.domain,
            item.source_versions,
            item.scopes,
            item.grants,
            item.policy_generation,
            item.semantic_commitment,
        )
        for item in sorted(artifacts.values(), key=lambda value: value.artifact_id)
    )


def _incremental_commitment(
    *,
    artifact_id: str,
    surface: Surface,
    policy_generation: int,
    inputs: Iterable[tuple[str, str]],
    source_versions: Sequence[tuple[str, int]],
) -> str:
    ordered_inputs = sorted(inputs)
    fields = [
        artifact_id,
        surface.value,
        str(policy_generation),
        *(f"{node_id}={value}" for node_id, value in ordered_inputs),
        *(f"{root_id}@{version}" for root_id, version in source_versions),
    ]
    digest = hashlib.sha256()
    digest.update(b"atc-m3-semantic-v1|")
    digest.update("|".join(fields).encode("utf-8"))
    return digest.hexdigest()
