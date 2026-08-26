# ZF-013 Milestone 5 lane A: frozen project-graph evaluation

Status: local synthetic evaluation contract, accepted for this isolated lane on
2026-08-25. This is a benchmark and evaluator boundary, not a production graph
implementation or promotion decision.

The frozen comparator is pinned to protected-main base
`fd1f802a67b3eb689ecdd4d85cd4440e1a57b7d2`. Its machine-readable contract is
`bench/zf013_project_graph_contract.json` and its sanitized corpus is
`bench/zf013_project_graph_fixtures.json`. The fixture contains two synthetic
projects, 31 records across current/stale/deleted/purged lifecycle states, 19
typed relations, nine query cases, a directed cycle, a cross-project edge, and
an eight-neighbor high-fan-out source. No workspace, Core database, provider,
browser, dashboard, network, model, or private data is used.

Run the bounded evaluator with:

```text
python -m bench.zf013_project_graph_benchmark --output tmp/zf013-project-graph.json
```

The report is aggregate-only. It contains contract hashes, metric counts,
boolean gate results, and deterministic receipt hashes; it does not contain
record IDs, query text, project labels, or fixture text. Reports are disposable
and must not be committed.

## Compared profiles

Each profile uses the same frozen corpus and query cases.

| Profile | Definition | Purpose |
|---|---|---|
| `retrieval_v3_current` | Current-only lexical ranking without an explicit project filter | Current Retrieval V3 comparator control |
| `structured_project_filter` | The same lexical ranking after an explicit project filter | Project-scoping control |
| `deterministic_project_context_capsule` | Current, project-scoped records selected by fixed capsule rank | Deterministic Project Context Capsule control |
| `lexical_typed_one_hop` | Highest-score project-scoped lexical seeds plus directed typed expansion to depth 1 | First graph candidate |
| `bounded_typed_two_hop` | The same seeds plus bounded directed typed expansion to depth 2 | Two-hop graph candidate |

Graph traversal filters lifecycle eligibility and project scope before a record
can be selected. It follows only the six frozen relation families: `belongs_to`,
`supersedes`, `depends_on`, `blocks`, `implements`, and `tested_by`. It uses a
maximum of two outgoing neighbors per source and 24 expanded edges per query.
The fixture's cross-project edge may be examined but never becomes disclosure.

## Metrics and exact gates

Required-evidence recall is the macro mean over query cases of:

```text
|selected ∩ required| / |required|
```

Project CAOS (Current Authorized Outcome Success) is the mean of one binary
outcome per query. A query succeeds only when all of the following are true:

```text
required ⊆ selected
wrong_project = stale = deleted = purged = unnecessary = 0
selected_items <= max_items
selected_characters <= max_chars
traversal_bound_violations = 0
```

Unnecessary disclosure is every selected record outside that query's frozen
`allowed` set. Wrong-project, stale, deleted, and purged disclosure are counted
separately and each has a zero tolerance. Lifecycle filtering is therefore a
hard safety condition, not a recall tradeoff.

The two graph profiles must pass all of these gates:

| Gate | One hop | Two hop |
|---|---:|---:|
| Required-evidence recall | `>= 0.75` | `>= 0.90` |
| Project CAOS | `>= 0.75` | `>= 0.85` |
| Recall gain over structured filter | `>= 0.20` | `>= 0.30` |
| CAOS gain over structured filter | `>= 0.20` | `>= 0.30` |
| Wrong-project, stale, deleted, purged disclosure | `0` each | `0` each |
| Unnecessary disclosure | `0` | `0` |
| Repeated receipt determinism | `true` | `true` |
| Maximum depth | `1` | `2` |
| Expanded edges per query | `<= 24` | `<= 24` |
| Neighbors per source | `<= 2` | `<= 2` |
| Cycle revisits per query | `<= 1` | `<= 1` |
| Warm p95 latency | `<= 50 ms` | `<= 50 ms` |

Receipts hash the ordered, query-ordinal result projection and outcome flags.
They must be identical across repeated runs and input reordering. The report
does not emit the material being hashed.

## Relation-family ablation and kill rule

One frozen single-hop query case is assigned to each relation family. For each
family, the evaluator compares all-family one-hop traversal with the same case
after removing only that family. A family is kept when removing it reduces
required-evidence recall by at least `0.10` or Project CAOS by at least `0.10`,
without a safety regression. A family is killed when both deltas are below
`0.10`, or when enabling that family creates a safety failure that disappears
under its ablation. Every family must receive an explicit `keep` or `kill`
decision; no family is silently promoted.

The lane A synthetic run at fixture revision `2026-08-25.lane-a` produced:

| Profile | Required recall | Project CAOS | Wrong-project | Unnecessary |
|---|---:|---:|---:|---:|
| Current Retrieval V3 | `0.537037` | `0.111111` | `1` | `4` |
| Structured project filter | `0.537037` | `0.111111` | `0` | `3` |
| Deterministic capsule | `0.111111` | `0.000000` | `0` | `16` |
| Lexical typed one-hop | `0.962963` | `0.888889` | `0` | `0` |
| Bounded typed two-hop | `1.000000` | `1.000000` | `0` | `0` |

Both graph profiles passed their gates. The two-hop run observed one bounded
cycle revisit, one high-fan-out truncation, maximum depth 2, maximum expanded
edges 3, and zero bound violations. Ablations kept `belongs_to`, `depends_on`,
`blocks`, `implements`, and `tested_by`; `supersedes` was killed because its
removal changed neither required recall nor CAOS in this usefulness fixture.
These are local synthetic observations, not production acceptance, provider or
client claims, private-data evidence, release evidence, or a graph-promotion
decision.

The lane hashes are fixture SHA-256
`100611b4b3da0deefa808d46701cc9c8eff374f9f9b77e8defc65bd74ea871cd` and
contract SHA-256
`a97a8db9a4c8ab2501b6db8a2032ff6508ef10df16b1a2b782275313fff09d35`.
