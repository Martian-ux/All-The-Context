"""Independent, sanitized ZF-013 graph safety oracle.

This module intentionally imports only the Python standard library.  It is a
pre-implementation contract for a later graph implementation, not a shadow of
Core storage, retrieval, authorization, or production graph code.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

MATRIX_PATH = Path(__file__).with_name("zf013_graph_adversarial_fixtures.json")
GOVERNANCE_BASE_COMMIT = "fd1f802a67b3eb689ecdd4d85cd4440e1a57b7d2"
MAX_HOPS = 2
MAX_NODES = 12
PROJECTION_DIMENSIONS = (
    "content",
    "reason_codes",
    "revision",
    "counts",
    "ordering",
    "receipts",
)


@dataclass(frozen=True, slots=True)
class Node:
    """A sanitized canonical graph node or evidence item."""

    node_id: str
    logical_id: str
    project_id: str
    content_symbol: str
    revision: int = 1
    authorized: bool = True
    assignment: str = "assigned"
    secret_like: bool = False
    imported_instruction: bool = False
    valid_from: str = "2026-08-01T00:00:00Z"
    valid_to: str | None = None
    deleted_at: str | None = None
    purged_at: str | None = None


@dataclass(frozen=True, slots=True)
class Edge:
    """A proposed directed source-to-derived relation."""

    edge_id: str
    source_id: str
    target_id: str
    relation: str = "supports"


@dataclass(frozen=True, slots=True)
class GraphInput:
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]


@dataclass(frozen=True, slots=True)
class Query:
    project_id: str
    seed_ids: tuple[str, ...]
    as_of: str = "2026-08-25T00:00:00Z"
    max_hops: int = MAX_HOPS
    max_nodes: int = MAX_NODES


@dataclass(frozen=True, slots=True)
class Receipt:
    """A bounded receipt with no source text or stable content fingerprint."""

    subject_id: str
    revision: int
    dependency_path: tuple[str, ...]
    depth: int

    def as_dict(self) -> dict[str, object]:
        return {
            "subject_id": self.subject_id,
            "revision": self.revision,
            "dependency_path": list(self.dependency_path),
            "depth": self.depth,
        }


@dataclass(frozen=True, slots=True)
class Projection:
    """The exact observable surface that a later implementation must match."""

    content: tuple[str, ...]
    reason_codes: tuple[str, ...]
    revision: str
    counts: tuple[tuple[str, int], ...]
    ordering: tuple[str, ...]
    receipts: tuple[Receipt, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "content": list(self.content),
            "reason_codes": list(self.reason_codes),
            "revision": self.revision,
            "counts": dict(self.counts),
            "ordering": list(self.ordering),
            "receipts": [receipt.as_dict() for receipt in self.receipts],
        }


@dataclass(frozen=True, slots=True)
class EdgeNormalization:
    accepted: tuple[Edge, ...]
    rejected: tuple[tuple[str, str], ...]
    duplicate_count: int


def load_matrix() -> dict[str, Any]:
    """Load the frozen sanitized matrix used by tests and future adapters."""

    return cast(dict[str, Any], json.loads(MATRIX_PATH.read_text(encoding="utf-8")))


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _matches_seed(node: Node, seed_id: str) -> bool:
    return node.node_id == seed_id or node.logical_id == seed_id


def _eligible_nodes(graph: GraphInput, query: Query) -> dict[str, Node]:
    """Apply the authority and project boundary before all graph inspection."""

    # This first comprehension is intentionally the first graph operation:
    # unauthorized rows cannot affect assignment, lifecycle, edge, or reason
    # diagnostics in this oracle.
    authorized = tuple(node for node in graph.nodes if node.authorized)
    project_nodes = tuple(node for node in authorized if node.project_id == query.project_id)

    purged_logical_ids = {node.logical_id for node in project_nodes if node.purged_at is not None}
    as_of = _instant(query.as_of)
    candidates: list[Node] = []
    for node in project_nodes:
        if node.logical_id in purged_logical_ids:
            continue
        if node.assignment != "assigned":
            continue
        if node.secret_like or node.imported_instruction:
            continue
        if _instant(node.valid_from) > as_of:
            continue
        if node.valid_to is not None and as_of >= _instant(node.valid_to):
            continue
        if node.deleted_at is not None and as_of >= _instant(node.deleted_at):
            continue
        candidates.append(node)

    by_logical_id: dict[str, Node] = {}
    for node in sorted(candidates, key=lambda item: (item.logical_id, item.revision, item.node_id)):
        previous = by_logical_id.get(node.logical_id)
        if previous is None or (node.revision, node.node_id) > (
            previous.revision,
            previous.node_id,
        ):
            by_logical_id[node.logical_id] = node
    return {node.node_id: node for node in by_logical_id.values()}


def normalize_edges(nodes: Mapping[str, Node], edges: Sequence[Edge]) -> EdgeNormalization:
    """Reject illegal edges and collapse exact duplicate relations deterministically."""

    accepted: list[Edge] = []
    rejected: list[tuple[str, str]] = []
    seen_relations: set[tuple[str, str, str]] = set()
    seen_endpoints: dict[tuple[str, str], str] = {}
    duplicate_count = 0
    adjacency: dict[str, set[str]] = defaultdict(set)

    def reaches(start: str, goal: str) -> bool:
        pending = [start]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == goal:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(adjacency[current] - seen)
        return False

    for edge in sorted(edges, key=lambda item: (item.edge_id, item.source_id, item.target_id)):
        source = nodes.get(edge.source_id)
        target = nodes.get(edge.target_id)
        if source is None or target is None:
            rejected.append((edge.edge_id, "unknown_endpoint"))
            continue
        if source.project_id != target.project_id:
            rejected.append((edge.edge_id, "cross_project"))
            continue
        if source.node_id == target.node_id:
            rejected.append((edge.edge_id, "self_edge"))
            continue
        endpoint_key = (source.node_id, target.node_id)
        previous_relation = seen_endpoints.get(endpoint_key)
        if previous_relation is not None and previous_relation != edge.relation:
            rejected.append((edge.edge_id, "conflicting_duplicate"))
            continue
        relation_key = (source.node_id, target.node_id, edge.relation)
        if relation_key in seen_relations:
            duplicate_count += 1
            continue
        if reaches(target.node_id, source.node_id):
            rejected.append((edge.edge_id, "cycle"))
            continue
        seen_endpoints[endpoint_key] = edge.relation
        seen_relations.add(relation_key)
        accepted.append(edge)
        adjacency[source.node_id].add(target.node_id)

    return EdgeNormalization(tuple(accepted), tuple(rejected), duplicate_count)


def _empty_projection(reason: str, query: Query) -> Projection:
    counts = (
        ("expanded_nodes", 0),
        ("max_hops", query.max_hops),
        ("max_nodes", query.max_nodes),
        ("selected_edges", 0),
        ("truncated", 0),
    )
    revision = _revision_digest(query, (), ())
    return Projection((), (reason,), revision, counts, (), ())


def _revision_digest(
    query: Query,
    nodes: Sequence[Node],
    edges: Sequence[Edge],
) -> str:
    payload = {
        "contract": "atc.zf013.graph-oracle.v1",
        "project_id": query.project_id,
        "as_of": query.as_of,
        "max_hops": query.max_hops,
        "max_nodes": query.max_nodes,
        "seed_ids": sorted(query.seed_ids),
        "nodes": [
            {
                "node_id": node.node_id,
                "logical_id": node.logical_id,
                "revision": node.revision,
                "content_symbol": node.content_symbol,
            }
            for node in nodes
        ],
        "edges": sorted(
            (
                {
                    "source_id": edge.source_id,
                    "target_id": edge.target_id,
                    "relation": edge.relation,
                }
                for edge in edges
            ),
            key=lambda item: (item["source_id"], item["target_id"], item["relation"]),
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def project(graph: GraphInput, query: Query) -> Projection:
    """Build the deterministic bounded reference projection for one query."""

    if query.max_hops != MAX_HOPS or query.max_nodes != MAX_NODES:
        raise ValueError("ZF-013 oracle bounds are frozen at two hops and twelve nodes")

    # Edge normalization receives only authorized, project-filtered, content-
    # eligible nodes.  In particular, secret-like, imported-instruction,
    # ambiguous, deleted, purged, and unauthorized nodes cannot create a
    # rejection, receipt, count, or revision difference.
    eligible = _eligible_nodes(graph, query)
    all_authorized = tuple(node for node in graph.nodes if node.authorized)
    edge_nodes = {
        node.node_id: node
        for node in all_authorized
        if node.project_id == query.project_id and node.node_id in eligible
    }
    normalized = normalize_edges(edge_nodes, graph.edges)

    seed_nodes = sorted(
        {
            node.node_id: node
            for node in eligible.values()
            if any(_matches_seed(node, seed_id) for seed_id in query.seed_ids)
        }.values(),
        key=lambda item: (item.logical_id, item.revision, item.node_id),
    )
    if not seed_nodes:
        ambiguous = any(
            node.authorized
            and node.project_id == query.project_id
            and node.assignment == "ambiguous"
            and any(_matches_seed(node, seed_id) for seed_id in query.seed_ids)
            for node in graph.nodes
        )
        reason = (
            "graph:abstain:ambiguous_assignment"
            if ambiguous
            else "graph:abstain:no_authorized_seed"
        )
        return _empty_projection(reason, query)

    adjacency: dict[str, list[Edge]] = defaultdict(list)
    for edge in normalized.accepted:
        if edge.source_id in eligible and edge.target_id in eligible:
            adjacency[edge.source_id].append(edge)
    for outgoing in adjacency.values():
        outgoing.sort(key=lambda item: (item.target_id, item.relation, item.edge_id))

    selected: list[Node] = []
    depths: dict[str, int] = {}
    paths: dict[str, tuple[str, ...]] = {}
    pending: deque[str] = deque()
    for seed in seed_nodes:
        if len(selected) >= query.max_nodes:
            break
        selected.append(seed)
        depths[seed.node_id] = 0
        paths[seed.node_id] = ()
        pending.append(seed.node_id)

    truncated = False
    depth_limited = False
    while pending:
        source_id = pending.popleft()
        source_depth = depths[source_id]
        outgoing = adjacency[source_id]
        if source_depth >= query.max_hops:
            if outgoing:
                depth_limited = True
            continue
        for edge in outgoing:
            if edge.target_id in depths:
                continue
            if len(selected) >= query.max_nodes:
                truncated = True
                continue
            target = eligible[edge.target_id]
            selected.append(target)
            depths[target.node_id] = source_depth + 1
            paths[target.node_id] = paths[source_id] + (source_id,)
            pending.append(target.node_id)

    selected_ids = set(depths)
    selected_edges = tuple(
        edge
        for edge in normalized.accepted
        if edge.source_id in selected_ids and edge.target_id in selected_ids
    )
    reasons = ("graph:bounded_two_hop",) if truncated or depth_limited else ("graph:ok",)
    receipts = tuple(
        Receipt(
            subject_id=node.node_id,
            revision=node.revision,
            dependency_path=paths[node.node_id],
            depth=depths[node.node_id],
        )
        for node in selected
    )
    counts = (
        ("expanded_nodes", len(selected)),
        ("max_hops", query.max_hops),
        ("max_nodes", query.max_nodes),
        ("selected_edges", len(selected_edges)),
        ("truncated", int(truncated)),
    )
    return Projection(
        content=tuple(node.content_symbol for node in selected),
        reason_codes=reasons,
        revision=_revision_digest(query, selected, selected_edges),
        counts=counts,
        ordering=tuple(node.node_id for node in selected),
        receipts=receipts,
    )


def projection_differences(left: Projection, right: Projection) -> tuple[str, ...]:
    """Return changed observable dimensions in stable contract order."""

    differences: list[str] = []
    for dimension in PROJECTION_DIMENSIONS:
        if getattr(left, dimension) != getattr(right, dimension):
            differences.append(dimension)
    return tuple(differences)


def validate_projection(actual: Projection, expected: Projection) -> tuple[str, ...]:
    """Compare a later implementation's normalized result to this oracle."""

    return projection_differences(actual, expected)


def base_graph() -> GraphInput:
    """Return the shared sanitized graph used by focused tests."""

    nodes = (
        Node("alpha-root", "alpha-root", "project-alpha", "alpha-root"),
        Node(
            "alpha-corrected-v1",
            "alpha-corrected",
            "project-alpha",
            "alpha-corrected-v1",
            revision=1,
            valid_to="2026-08-10T00:00:00Z",
        ),
        Node(
            "alpha-corrected-v2",
            "alpha-corrected",
            "project-alpha",
            "alpha-corrected-v2",
            revision=2,
            valid_from="2026-08-10T00:00:00Z",
        ),
        Node(
            "alpha-stale-derived",
            "alpha-stale-derived",
            "project-alpha",
            "alpha-stale-derived-v1",
        ),
        Node(
            "alpha-fresh-derived",
            "alpha-fresh-derived",
            "project-alpha",
            "alpha-fresh-derived-v2",
            revision=2,
        ),
        Node(
            "alpha-deleted",
            "alpha-deleted",
            "project-alpha",
            "alpha-deleted",
            deleted_at="2026-08-15T00:00:00Z",
        ),
        Node(
            "alpha-purged",
            "alpha-purged",
            "project-alpha",
            "alpha-purged",
            purged_at="2026-08-18T00:00:00Z",
        ),
        Node(
            "alpha-purged-derived",
            "alpha-purged-derived",
            "project-alpha",
            "alpha-purged-derived",
        ),
        Node(
            "alpha-imported-instruction",
            "alpha-imported-instruction",
            "project-alpha",
            "synthetic-imported-instruction-marker",
            imported_instruction=True,
        ),
        Node(
            "alpha-secret-like",
            "alpha-secret-like",
            "project-alpha",
            "synthetic-secret-like-marker",
            secret_like=True,
        ),
        Node(
            "alpha-ambiguous",
            "alpha-ambiguous",
            "project-alpha",
            "alpha-ambiguous",
            assignment="ambiguous",
        ),
        Node("beta-root", "beta-root", "project-beta", "beta-root"),
        Node("beta-child", "beta-child", "project-beta", "beta-child"),
    )
    edges = (
        Edge("e-alpha-corrected-v1", "alpha-root", "alpha-corrected-v1"),
        Edge("e-alpha-corrected-v2", "alpha-root", "alpha-corrected-v2"),
        Edge("e-alpha-stale", "alpha-corrected-v1", "alpha-stale-derived"),
        Edge("e-alpha-fresh", "alpha-corrected-v2", "alpha-fresh-derived"),
        Edge("e-alpha-deleted", "alpha-root", "alpha-deleted"),
        Edge("e-alpha-purged", "alpha-root", "alpha-purged"),
        Edge("e-alpha-purged-derived", "alpha-purged", "alpha-purged-derived"),
        Edge("e-beta-child", "beta-root", "beta-child"),
    )
    return GraphInput(nodes, edges)


def append_nodes(graph: GraphInput, *nodes: Node) -> GraphInput:
    """Return a new sanitized graph without mutating a fixture."""

    return GraphInput(graph.nodes + tuple(nodes), graph.edges)


def append_edges(graph: GraphInput, *edges: Edge) -> GraphInput:
    """Return a new sanitized graph without mutating a fixture."""

    return GraphInput(graph.nodes, graph.edges + tuple(edges))
