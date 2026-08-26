# ZF-013 graph adversarial safety oracle

## Boundary

This is the Milestone 5 lane C pre-implementation contract for the project
graph requirement. It is frozen against protected-main base
`fd1f802a67b3eb689ecdd4d85cd4440e1a57b7d2` and is deliberately independent of
production runtime, storage, retrieval, authorization, scanning, providers,
network, UI, and private data. The fixture contains only opaque synthetic
identifiers and marker symbols.

The executable matrix is
[`bench/zf013_graph_adversarial_fixtures.json`](../../bench/zf013_graph_adversarial_fixtures.json).
The reusable stdlib-only oracle is
[`bench/zf013_graph_adversarial.py`](../../bench/zf013_graph_adversarial.py).
Focused coverage is
[`tests/unit/test_zf013_graph_adversarial.py`](../../tests/unit/test_zf013_graph_adversarial.py).

No graph implementation is present or credited by this slice. A later
implementation must adapt its result into the oracle's six observable
dimensions and pass the matrix without weakening these criteria.

## Frozen contract

The graph is directed from source to derived node. Authority and project scope
are applied before any assignment, lifecycle, edge, revision, count, ordering,
reason, or receipt work. Only assigned, authorized, non-secret-like,
non-imported-instruction, non-deleted, non-purged nodes are eligible. A
terminal purge removes the logical lineage from both current and historical
queries; an ordinary delete remains visible only before its effective time.

Edges are normalized deterministically. Self-edges, cycles, cross-project
edges, and unknown endpoints are rejected. Exact duplicate
source/target/relation edges collapse to one relation and cannot inflate a
projection. Expansion is breadth-first, deterministic, at most two hops, and
capped at twelve selected nodes. The projection revision is derived only from
the selected authorized graph and query contract, so filtered or other-project
inputs cannot revise an unrelated projection.

The result must match exactly on all six dimensions:

| Dimension | Required meaning |
|---|---|
| `content` | Only eligible opaque node symbols in deterministic traversal order |
| `reason_codes` | Stable bounded enums; no filtered-record diagnostics |
| `revision` | Stable digest of the selected authorized projection and query contract |
| `counts` | Exact bounded expansion/edge counts and cap indicators |
| `ordering` | Deterministic opaque node IDs |
| `receipts` | Bounded subject/version/dependency-path/depth records with no source text or secret-like data |

## Adversarial matrix

The 14 cases are machine-readable in the fixture and cover:

1. authorization-first filtering and paired unauthorized noninterference;
2. cross-project isolation and cross-project edge rejection;
3. ambiguous assignment abstention;
4. correction and supersession;
5. historical `as_of` behavior;
6. ordinary delete versus terminal purge closure;
7. stale dependency invalidation;
8. cycle rejection;
9. self-edge rejection;
10. duplicate-edge idempotence;
11. high fan-out and the two-hop/node bounds;
12. deterministic fresh rebuild and input reordering;
13. secret-like and imported-instruction inertness; and
14. exact equality across all six dimensions when unauthorized adversarial
    rows are added.

An ambiguous assignment must return no content, ordering, traversal, or
receipts and must use the explicit abstention reason. A correction must remove
the old revision and every stale descendant before the replacement becomes
visible. Before a correction timestamp, `as_of` returns the old revision; after
it, `as_of` returns the replacement. Purge must leave no current or historical
lineage residue. A later implementation may retain a shared descendant only
when an independently eligible dependency still supports it.

## Failure and kill criteria

The following are unconditional kill conditions; one violation yields
`KILL_ZF013` and cannot be offset by an aggregate score:

- any unauthorized influence on content, reason codes, revision, counts,
  ordering, or receipts;
- any cross-project content, identifier, edge, count, or revision leak;
- publication or traversal from an ambiguous assignment;
- stale superseded/deleted/purged dependencies or receipts;
- an `as_of` mismatch around correction or lifecycle boundaries;
- acceptance of a cycle or self-edge;
- duplicate-edge inflation of any observable dimension;
- more than two hops, more than twelve selected nodes, or unbounded high-
  fan-out traversal;
- any input-order-dependent rebuild result;
- influence from secret-like or imported instruction-like markers; or
- raw source text, credentials, secret-like markers, or hidden reasoning in a
  receipt.

The following are hold conditions and yield `HOLD_ZF013` until repaired:

- a missing case, invariant, or observable dimension;
- a production-runtime import or private/live-data dependency in the oracle;
- a rebuild control that shares optimized state;
- an unobservable required graph surface; or
- unsafe or content-bearing failure diagnostics.

All other outcomes are non-credit: the graph remains open under ZF-013. The
oracle is a safety gate, not a quality score, graph-store implementation, or
acceptance of provider/client/platform behavior.
