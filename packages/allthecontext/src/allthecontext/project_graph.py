"""Bounded deterministic ephemeral typed project graphs.

Only already-authorized, single-project, temporally/lifecycle-eligible
relation evidence is accepted. This module is read-only and in-memory:
prose, models, storage, scans, capture, runtime wiring, and canonical truth
are all outside its boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final, Literal

from .secret_boundary import contains_secret_like_text

GRAPH_SCHEMA: Final = "atc.project-typed-graph.v0"
GRAPH_COMPILER_VERSION: Final = "project-graph-v0"
MAX_GRAPH_NODES: Final = 256
MAX_GRAPH_EDGES: Final = 512
MAX_GRAPH_FANOUT: Final = 16
MAX_GRAPH_INPUT_RELATIONS: Final = 2_048
MAX_EXPANSION_NODES: Final = 64
MAX_EXPANSION_EDGES: Final = 128


class ProjectGraphError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ProjectRelationFamily(StrEnum):
    BELONGS_TO = "belongs_to"
    SUPERSEDES = "supersedes"
    DEPENDS_ON = "depends_on"
    BLOCKS = "blocks"
    IMPLEMENTS = "implements"
    TESTED_BY = "tested_by"


class RelationBasis(StrEnum):
    EXPLICIT = "explicit"
    STRUCTURAL = "structural"
    INFERRED = "inferred"


class GraphDirection(StrEnum):
    OUTGOING = "outgoing"
    INCOMING = "incoming"
    BOTH = "both"


class GraphAbstentionReason(StrEnum):
    INVALID_EVIDENCE = "invalid_evidence"
    INPUT_BOUND_EXCEEDED = "input_bound_exceeded"
    AMBIGUOUS_EVIDENCE = "ambiguous_evidence"
    UNAUTHORIZED_EVIDENCE = "unauthorized_evidence"
    LIFECYCLE_INELIGIBLE = "lifecycle_ineligible"
    TEMPORAL_CUTOFF_REQUIRED = "temporal_cutoff_required"
    TEMPORAL_WINDOW_INELIGIBLE = "temporal_window_ineligible"
    UNSUPPORTED_RELATION = "unsupported_relation"
    INFERRED_RELATION_UNSUPPORTED = "inferred_relation_unsupported"
    PROJECT_MISMATCH = "project_mismatch"
    INVALID_ENDPOINT = "invalid_endpoint"
    SELF_EDGE = "self_edge"
    DUPLICATE_CONFLICT = "duplicate_conflict"
    NODE_KIND_CONFLICT = "node_kind_conflict"
    CYCLE_DETECTED = "cycle_detected"
    FANOUT_CAP = "fanout_cap"
    NODE_CAP = "node_cap"
    EDGE_CAP = "edge_cap"
    NODE_NOT_FOUND = "node_not_found"
    INVALID_EXPANSION = "invalid_expansion"
    NO_ELIGIBLE_RELATIONS = "no_eligible_relations"


_MACHINE_TOKEN_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.:-]{0,127})\Z", re.ASCII)


def _token(value: object, maximum: int = 128) -> str:
    if not isinstance(value, str):
        raise ProjectGraphError("invalid_token")
    value = unicodedata.normalize("NFKC", value).strip()
    if (
        not value
        or len(value) > maximum
        or _MACHINE_TOKEN_RE.fullmatch(value) is None
        or ".." in value
    ):
        raise ProjectGraphError("invalid_token")
    if contains_secret_like_text(value):
        raise ProjectGraphError("secret_like_token")
    return value


def _refs(values: Iterable[str], maximum: int = 4_096) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ProjectGraphError("invalid_reference_list")
    result = tuple(_token(value) for value in values)
    if len(result) > maximum or len(set(result)) != len(result):
        raise ProjectGraphError("invalid_reference_list")
    return tuple(sorted(result))


def _timestamp(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 100:
        raise ProjectGraphError("invalid_timestamp")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ProjectGraphError("invalid_timestamp")
    if contains_secret_like_text(value):
        raise ProjectGraphError("invalid_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProjectGraphError("invalid_timestamp") from exc
    if parsed.tzinfo is None:
        raise ProjectGraphError("timestamp_requires_offset")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _enum[T: StrEnum](value: T | str, enum: type[T], code: str) -> T:
    try:
        return value if isinstance(value, enum) else enum(value)
    except ValueError as exc:
        raise ProjectGraphError(code) from exc


def _cap(value: int, maximum: int, code: str) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ProjectGraphError(code)
    return value


@dataclass(frozen=True, slots=True)
class ProjectGraphEvidence:
    """A typed relation after authorization and project assignment."""

    evidence_id: str
    project_id: str
    subject_id: str
    subject_project_id: str
    subject_kind: str
    relation: ProjectRelationFamily
    object_id: str
    object_project_id: str
    object_kind: str
    provenance_ids: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    authorized: bool = True
    lifecycle_eligible: bool = True
    ambiguous: bool = False
    purged: bool = False
    basis: RelationBasis = RelationBasis.EXPLICIT
    valid_from: str | None = None
    valid_to: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "evidence_id",
            "project_id",
            "subject_id",
            "subject_project_id",
            "object_id",
            "object_project_id",
        ):
            value = _token(getattr(self, name))
            if name.endswith("project_id") and not value.startswith("project-"):
                raise ProjectGraphError("invalid_project_id")
            object.__setattr__(self, name, value)
        for name in ("subject_kind", "object_kind"):
            object.__setattr__(self, name, _token(getattr(self, name), 64).casefold())
        relation = self.relation.casefold() if isinstance(self.relation, str) else self.relation
        object.__setattr__(
            self, "relation", _enum(relation, ProjectRelationFamily, "unsupported_relation")
        )
        object.__setattr__(self, "basis", _enum(self.basis, RelationBasis, "invalid_basis"))
        if (
            self.basis is RelationBasis.STRUCTURAL
            and self.relation is not ProjectRelationFamily.BELONGS_TO
        ):
            raise ProjectGraphError("structural_relation_unsupported")
        object.__setattr__(self, "provenance_ids", _refs(self.provenance_ids, 256))
        object.__setattr__(self, "dependency_ids", _refs(self.dependency_ids, 256))
        valid_from, valid_to = _timestamp(self.valid_from), _timestamp(self.valid_to)
        if valid_from and valid_to and valid_to <= valid_from:
            raise ProjectGraphError("invalid_temporal_window")
        object.__setattr__(self, "valid_from", valid_from)
        object.__setattr__(self, "valid_to", valid_to)
        for name in ("authorized", "lifecycle_eligible", "ambiguous", "purged"):
            if type(getattr(self, name)) is not bool:
                raise ProjectGraphError("invalid_evidence_state")

    def to_dict(self) -> dict[str, object]:
        return {
            "evidence_id": self.evidence_id,
            "project_id": self.project_id,
            "subject_id": self.subject_id,
            "subject_project_id": self.subject_project_id,
            "subject_kind": self.subject_kind,
            "relation": self.relation.value,
            "object_id": self.object_id,
            "object_project_id": self.object_project_id,
            "object_kind": self.object_kind,
            "provenance_ids": list(self.provenance_ids),
            "dependency_ids": list(self.dependency_ids),
            "authorized": self.authorized,
            "lifecycle_eligible": self.lifecycle_eligible,
            "ambiguous": self.ambiguous,
            "purged": self.purged,
            "basis": self.basis.value,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }


@dataclass(frozen=True, slots=True)
class GraphAbstention:
    reason: GraphAbstentionReason

    def to_dict(self) -> dict[str, object]:
        return {"reason": self.reason.value}


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: str
    project_id: str
    kind: str
    evidence_ids: tuple[str, ...] = ()
    provenance_ids: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "node_id": self.node_id,
            "project_id": self.project_id,
            "kind": self.kind,
            "evidence_ids": list(self.evidence_ids),
            "provenance_ids": list(self.provenance_ids),
            "dependency_ids": list(self.dependency_ids),
        }


@dataclass(frozen=True, slots=True)
class GraphEdge:
    edge_id: str
    project_id: str
    subject_id: str
    relation: ProjectRelationFamily
    object_id: str
    evidence_ids: tuple[str, ...]
    provenance_ids: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    basis: RelationBasis

    def to_dict(self) -> dict[str, object]:
        return {
            "edge_id": self.edge_id,
            "project_id": self.project_id,
            "subject_id": self.subject_id,
            "relation": self.relation.value,
            "object_id": self.object_id,
            "evidence_ids": list(self.evidence_ids),
            "provenance_ids": list(self.provenance_ids),
            "dependency_ids": list(self.dependency_ids),
            "basis": self.basis.value,
        }


@dataclass(frozen=True, slots=True)
class GraphExpansion:
    project_id: str
    origin_id: str
    hops: Literal[1, 2]
    outcome: Literal["expanded", "abstained"]
    graph_revision: str
    node_ids: tuple[str, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    provenance_ids: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    omitted_nodes: int = 0
    omitted_edges: int = 0
    abstention_reason: GraphAbstentionReason | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": GRAPH_SCHEMA,
            "project_id": self.project_id,
            "origin_id": self.origin_id,
            "hops": self.hops,
            "outcome": self.outcome,
            "graph_revision": self.graph_revision,
            "node_ids": list(self.node_ids),
            "edges": [edge.to_dict() for edge in self.edges],
            "provenance_ids": list(self.provenance_ids),
            "dependency_ids": list(self.dependency_ids),
            "omitted_nodes": self.omitted_nodes,
            "omitted_edges": self.omitted_edges,
            "abstention_reason": (self.abstention_reason.value if self.abstention_reason else None),
        }


@dataclass(frozen=True, slots=True)
class ProjectGraph:
    project_id: str
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    revision: str
    as_of: str | None
    node_cap: int
    edge_cap: int
    fanout_cap: int
    abstentions: tuple[GraphAbstention, ...] = ()
    truncated: bool = False

    @property
    def abstained(self) -> bool:
        return not self.edges

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": GRAPH_SCHEMA,
            "compiler_version": GRAPH_COMPILER_VERSION,
            "project_id": self.project_id,
            "as_of": self.as_of,
            "nodes": [node.to_dict() for node in self.nodes],
            "edges": [edge.to_dict() for edge in self.edges],
            "revision": self.revision,
            "node_cap": self.node_cap,
            "edge_cap": self.edge_cap,
            "fanout_cap": self.fanout_cap,
            "abstentions": [item.to_dict() for item in self.abstentions],
            "truncated": self.truncated,
            "derived_read_only": True,
        }

    def stable_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def expand_one_hop(
        self,
        origin_id: str,
        *,
        direction: GraphDirection | str = GraphDirection.BOTH,
        node_cap: int = MAX_EXPANSION_NODES,
        edge_cap: int = MAX_EXPANSION_EDGES,
        fanout_cap: int | None = None,
    ) -> GraphExpansion:
        return self.expand(
            origin_id,
            hops=1,
            direction=direction,
            node_cap=node_cap,
            edge_cap=edge_cap,
            fanout_cap=fanout_cap,
        )

    def expand_two_hop(
        self,
        origin_id: str,
        *,
        direction: GraphDirection | str = GraphDirection.BOTH,
        node_cap: int = MAX_EXPANSION_NODES,
        edge_cap: int = MAX_EXPANSION_EDGES,
        fanout_cap: int | None = None,
    ) -> GraphExpansion:
        return self.expand(
            origin_id,
            hops=2,
            direction=direction,
            node_cap=node_cap,
            edge_cap=edge_cap,
            fanout_cap=fanout_cap,
        )

    def expand(
        self,
        origin_id: str,
        *,
        hops: Literal[1, 2] = 1,
        direction: GraphDirection | str = GraphDirection.BOTH,
        node_cap: int = MAX_EXPANSION_NODES,
        edge_cap: int = MAX_EXPANSION_EDGES,
        fanout_cap: int | None = None,
    ) -> GraphExpansion:
        try:
            if type(hops) is not int or hops not in {1, 2}:
                raise ProjectGraphError("invalid_expansion")
            origin_id, direction = (
                _token(origin_id),
                _enum(direction, GraphDirection, "invalid_direction"),
            )
            _cap(node_cap, MAX_EXPANSION_NODES, "invalid_expansion_cap")
            _cap(edge_cap, MAX_EXPANSION_EDGES, "invalid_expansion_cap")
            fanout_cap = _cap(
                self.fanout_cap if fanout_cap is None else fanout_cap,
                MAX_GRAPH_FANOUT,
                "invalid_expansion_cap",
            )
        except ProjectGraphError:
            return _abstained(self, "invalid-origin", 1, "invalid_expansion")
        if not any(node.node_id == origin_id for node in self.nodes):
            return _abstained(self, origin_id, hops, "node_not_found")
        seen, frontier = {origin_id}, [origin_id]
        selected: dict[str, GraphEdge] = {}
        omitted_nodes = omitted_edges = 0
        for _ in range(hops):
            next_frontier: list[str] = []
            for current in sorted(frontier):
                incident = sorted(
                    (edge for edge in self.edges if _neighbor(edge, current, direction)),
                    key=_edge_key,
                )
                omitted_edges += max(0, len(incident) - fanout_cap)
                for edge in incident[:fanout_cap]:
                    if edge.edge_id in selected:
                        continue
                    neighbor = _neighbor(edge, current, direction)
                    if neighbor is None or len(selected) >= edge_cap:
                        omitted_edges += 1
                    elif neighbor not in seen and len(seen) >= node_cap:
                        omitted_nodes += 1
                    else:
                        selected[edge.edge_id] = edge
                        if neighbor not in seen:
                            seen.add(neighbor)
                            next_frontier.append(neighbor)
            frontier = next_frontier
        edges = tuple(sorted(selected.values(), key=_edge_key))
        return GraphExpansion(
            self.project_id,
            origin_id,
            hops,
            "expanded",
            self.revision,
            tuple(sorted(seen)),
            edges,
            _merge(edge.provenance_ids for edge in edges),
            _merge(edge.dependency_ids for edge in edges),
            omitted_nodes,
            omitted_edges,
        )


def _abstained(
    graph: ProjectGraph, origin: str, hops: Literal[1, 2], reason: str
) -> GraphExpansion:
    return GraphExpansion(
        graph.project_id,
        origin or "invalid-origin",
        hops,
        "abstained",
        graph.revision,
        abstention_reason=GraphAbstentionReason(reason),
    )


def _merge(values: Iterable[Iterable[str]]) -> tuple[str, ...]:
    return tuple(sorted({item for group in values for item in group}))


def _edge_key(value: GraphEdge | ProjectGraphEvidence) -> tuple[str, str, str, str]:
    evidence_key = (
        value.evidence_id
        if isinstance(value, ProjectGraphEvidence)
        else "\0".join(value.evidence_ids)
    )
    return value.subject_id, value.relation.value, value.object_id, evidence_key


def _neighbor(edge: GraphEdge, node: str, direction: GraphDirection) -> str | None:
    if direction in {GraphDirection.OUTGOING, GraphDirection.BOTH} and edge.subject_id == node:
        return edge.object_id
    if direction in {GraphDirection.INCOMING, GraphDirection.BOTH} and edge.object_id == node:
        return edge.subject_id
    return None


def _reachable(adjacency: dict[str, set[str]], start: str, target: str) -> bool:
    seen: set[str] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node not in seen:
            seen.add(node)
            stack.extend(sorted(adjacency.get(node, ())))
    return False


def _edge_id(
    project_id: str,
    subject_id: str,
    relation: ProjectRelationFamily,
    object_id: str,
) -> str:
    material = "\0".join(
        (GRAPH_COMPILER_VERSION, project_id, subject_id, relation.value, object_id)
    )
    return "edge-" + hashlib.sha256(material.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class _MergedRelation:
    subject_id: str
    subject_kind: str
    relation: ProjectRelationFamily
    object_id: str
    object_kind: str
    evidence_ids: tuple[str, ...]
    provenance_ids: tuple[str, ...]
    dependency_ids: tuple[str, ...]
    basis: RelationBasis


def _evidence_binding(item: ProjectGraphEvidence) -> tuple[object, ...]:
    return (
        item.project_id,
        item.subject_id,
        item.subject_project_id,
        item.subject_kind,
        item.relation,
        item.object_id,
        item.object_project_id,
        item.object_kind,
        item.provenance_ids,
        item.dependency_ids,
        item.basis,
        item.valid_from,
        item.valid_to,
    )


def _build(
    project_id: str,
    evidence: Sequence[ProjectGraphEvidence],
    *,
    as_of: str | None,
    node_cap: int,
    edge_cap: int,
    fanout_cap: int,
) -> ProjectGraph:
    project_id = _token(project_id)
    if not project_id.startswith("project-"):
        raise ProjectGraphError("invalid_project_id")
    node_cap, edge_cap, fanout_cap = (
        _cap(node_cap, MAX_GRAPH_NODES, "invalid_node_cap"),
        _cap(edge_cap, MAX_GRAPH_EDGES, "invalid_edge_cap"),
        _cap(fanout_cap, MAX_GRAPH_FANOUT, "invalid_fanout_cap"),
    )
    as_of, abstentions = _timestamp(as_of), []
    if isinstance(evidence, (str, bytes)) or not isinstance(evidence, Sequence):
        raise ProjectGraphError("invalid_evidence_sequence")
    if len(evidence) > MAX_GRAPH_INPUT_RELATIONS:
        abstentions.append(GraphAbstention(GraphAbstentionReason.INPUT_BOUND_EXCEEDED))
        evidence = ()
    candidates: list[ProjectGraphEvidence] = []
    for item in evidence:
        if not isinstance(item, ProjectGraphEvidence):
            abstentions.append(GraphAbstention(GraphAbstentionReason.INVALID_EVIDENCE))
            continue
        # These boundaries precede all graph-visible diagnostics and revision
        # work. Adding inaccessible, stale, purged, ambiguous, temporal, or
        # other-project evidence must be exactly noninterfering.
        if (
            not item.authorized
            or item.purged
            or not item.lifecycle_eligible
            or item.ambiguous
            or any(
                endpoint != project_id
                for endpoint in (
                    item.project_id,
                    item.subject_project_id,
                    item.object_project_id,
                )
            )
            or (item.valid_from and (as_of is None or item.valid_from > as_of))
            or (item.valid_to and (as_of is None or item.valid_to <= as_of))
        ):
            continue
        if item.basis is RelationBasis.INFERRED:
            abstentions.append(GraphAbstention(GraphAbstentionReason.INFERRED_RELATION_UNSUPPORTED))
            continue
        if item.subject_id == item.object_id:
            abstentions.append(GraphAbstention(GraphAbstentionReason.SELF_EDGE))
            continue
        if item.relation is ProjectRelationFamily.BELONGS_TO and (
            item.object_id != project_id or item.object_kind != "project"
        ):
            abstentions.append(GraphAbstention(GraphAbstentionReason.INVALID_ENDPOINT))
            continue
        candidates.append(item)

    # One evidence identifier cannot bind different relation facts. Exact
    # replay is idempotent; conflicting reuse is removed fail-closed.
    by_evidence_id: dict[str, ProjectGraphEvidence] = {}
    conflicting_evidence_ids: set[str] = set()
    for item in sorted(candidates, key=_edge_key):
        previous = by_evidence_id.get(item.evidence_id)
        if previous is None:
            by_evidence_id[item.evidence_id] = item
        elif _evidence_binding(previous) != _evidence_binding(item):
            conflicting_evidence_ids.add(item.evidence_id)
    if conflicting_evidence_ids:
        abstentions.append(GraphAbstention(GraphAbstentionReason.DUPLICATE_CONFLICT))
    deduplicated = tuple(
        item
        for evidence_id, item in sorted(by_evidence_id.items())
        if evidence_id not in conflicting_evidence_ids
    )

    # Multiple relation families for one ordered endpoint pair are ambiguous;
    # drop the whole pair instead of allowing lexical order to choose authority.
    endpoint_families: dict[tuple[str, str], set[ProjectRelationFamily]] = {}
    for item in deduplicated:
        endpoint_families.setdefault((item.subject_id, item.object_id), set()).add(item.relation)
    conflicting_endpoints = {
        endpoints for endpoints, families in endpoint_families.items() if len(families) > 1
    }
    if conflicting_endpoints:
        abstentions.append(GraphAbstention(GraphAbstentionReason.DUPLICATE_CONFLICT))

    grouped: dict[tuple[str, ProjectRelationFamily, str], list[ProjectGraphEvidence]] = {}
    for item in deduplicated:
        if (item.subject_id, item.object_id) in conflicting_endpoints:
            continue
        grouped.setdefault((item.subject_id, item.relation, item.object_id), []).append(item)

    merged: list[_MergedRelation] = []
    for (subject_id, relation, object_id), items in sorted(
        grouped.items(), key=lambda pair: (pair[0][0], pair[0][1].value, pair[0][2])
    ):
        subject_kinds = {item.subject_kind for item in items}
        object_kinds = {item.object_kind for item in items}
        if len(subject_kinds) != 1 or len(object_kinds) != 1:
            abstentions.append(GraphAbstention(GraphAbstentionReason.NODE_KIND_CONFLICT))
            continue
        bases = {item.basis for item in items}
        basis = (
            RelationBasis.EXPLICIT if RelationBasis.EXPLICIT in bases else RelationBasis.STRUCTURAL
        )
        merged.append(
            _MergedRelation(
                subject_id=subject_id,
                subject_kind=next(iter(subject_kinds)),
                relation=relation,
                object_id=object_id,
                object_kind=next(iter(object_kinds)),
                evidence_ids=tuple(sorted(item.evidence_id for item in items)),
                provenance_ids=_merge(item.provenance_ids for item in items),
                dependency_ids=_merge(item.dependency_ids for item in items),
                basis=basis,
            )
        )

    adjacency: dict[str, set[str]] = {}
    kinds: dict[str, str] = {project_id: "project"}
    fanout: dict[str, int] = {}
    accepted: list[_MergedRelation] = []
    truncated = False
    for relation_item in merged:
        if any(
            kinds.get(node) not in (None, kind)
            for node, kind in (
                (relation_item.subject_id, relation_item.subject_kind),
                (relation_item.object_id, relation_item.object_kind),
            )
        ):
            reason = GraphAbstentionReason.NODE_KIND_CONFLICT
        elif fanout.get(relation_item.subject_id, 0) >= fanout_cap:
            reason, truncated = GraphAbstentionReason.FANOUT_CAP, True
        elif len(accepted) >= edge_cap:
            reason, truncated = GraphAbstentionReason.EDGE_CAP, True
        elif _reachable(adjacency, relation_item.object_id, relation_item.subject_id):
            reason = GraphAbstentionReason.CYCLE_DETECTED
        else:
            reason = None
            if (
                len(kinds) + len({relation_item.subject_id, relation_item.object_id} - set(kinds))
                > node_cap
            ):
                reason, truncated = GraphAbstentionReason.NODE_CAP, True
        if reason is not None:
            abstentions.append(GraphAbstention(reason))
            continue
        kinds.setdefault(relation_item.subject_id, relation_item.subject_kind)
        kinds.setdefault(relation_item.object_id, relation_item.object_kind)
        adjacency.setdefault(relation_item.subject_id, set()).add(relation_item.object_id)
        fanout[relation_item.subject_id] = fanout.get(relation_item.subject_id, 0) + 1
        accepted.append(relation_item)
    edges = tuple(
        GraphEdge(
            _edge_id(project_id, item.subject_id, item.relation, item.object_id),
            project_id,
            item.subject_id,
            item.relation,
            item.object_id,
            item.evidence_ids,
            item.provenance_ids,
            item.dependency_ids,
            item.basis,
        )
        for item in accepted
    )
    lineage: dict[str, list[set[str]]] = {node: [set(), set(), set()] for node in kinds}
    for edge in edges:
        for node in (edge.subject_id, edge.object_id):
            lineage[node][0].update(edge.evidence_ids)
            lineage[node][1].update(edge.provenance_ids)
            lineage[node][2].update(edge.dependency_ids)
    nodes = tuple(
        GraphNode(
            node,
            project_id,
            kinds[node],
            tuple(sorted(lineage[node][0])),
            tuple(sorted(lineage[node][1])),
            tuple(sorted(lineage[node][2])),
        )
        for node in sorted(kinds)
    )
    if not edges:
        abstentions.append(GraphAbstention(GraphAbstentionReason.NO_ELIGIBLE_RELATIONS))
    material = {
        "schema": GRAPH_SCHEMA,
        "compiler_version": GRAPH_COMPILER_VERSION,
        "project_id": project_id,
        "as_of": as_of,
        "nodes": [n.to_dict() for n in nodes],
        "edges": [e.to_dict() for e in edges],
        "node_cap": node_cap,
        "edge_cap": edge_cap,
        "fanout_cap": fanout_cap,
        "truncated": truncated,
    }
    revision = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return ProjectGraph(
        project_id,
        nodes,
        edges,
        revision,
        as_of,
        node_cap,
        edge_cap,
        fanout_cap,
        tuple(abstentions),
        truncated,
    )


def build_project_graph(
    project_id: str,
    evidence: Sequence[ProjectGraphEvidence],
    *,
    as_of: str | None = None,
    node_cap: int = MAX_GRAPH_NODES,
    edge_cap: int = MAX_GRAPH_EDGES,
    fanout_cap: int = MAX_GRAPH_FANOUT,
) -> ProjectGraph:
    """Build one isolated graph from already-authorized temporal evidence."""
    return _build(
        project_id,
        evidence,
        as_of=as_of,
        node_cap=node_cap,
        edge_cap=edge_cap,
        fanout_cap=fanout_cap,
    )


__all__ = [
    "GRAPH_COMPILER_VERSION",
    "GRAPH_SCHEMA",
    "MAX_EXPANSION_EDGES",
    "MAX_EXPANSION_NODES",
    "MAX_GRAPH_EDGES",
    "MAX_GRAPH_FANOUT",
    "MAX_GRAPH_NODES",
    "GraphAbstention",
    "GraphAbstentionReason",
    "GraphDirection",
    "GraphEdge",
    "GraphExpansion",
    "GraphNode",
    "ProjectGraph",
    "ProjectGraphError",
    "ProjectGraphEvidence",
    "ProjectRelationFamily",
    "RelationBasis",
    "build_project_graph",
]
