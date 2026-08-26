# ZF-013 Milestone 5 lane A: frozen project-graph evaluation

Status: local synthetic harness self-test over the actual ephemeral typed graph
candidate, accepted for this isolated lane on 2026-08-25. This is component
evaluation, not runtime wiring, a production Retrieval V3 usefulness result, or
a promotion decision.

The frozen harness is pinned to protected-main base
`fd1f802a67b3eb689ecdd4d85cd4440e1a57b7d2`. Its machine-readable contract is
`bench/zf013_project_graph_contract.json` and its sanitized corpus is
`bench/zf013_project_graph_fixtures.json`. The fixture contains two synthetic
projects, 31 records across current/stale/deleted/purged lifecycle states, 23
typed relations, nine query cases, a directed cycle, a self-edge, a
cross-project edge, and an eight-neighbor high-fan-out source. No workspace,
Core database, provider, browser, dashboard, network, model, or private data is
used.

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
| `stdlib_lexical_proxy` | A stdlib-only current-record lexical proxy without an explicit project filter | Frozen weak lexical control |
| `structured_project_filter` | Checkout-local production `LexicalV3` over fixture-supplied current/project-eligible IDs | Strong ranker/project-scoping control; the full RetrievalEngine/Core policy façade is not exercised |
| `deterministic_project_context_capsule` | Current, project-scoped records selected by fixed capsule rank | Deterministic Project Context Capsule control |
| `lexical_typed_one_hop` | Highest-score project-scoped lexical seeds plus the actual `allthecontext.project_graph` expansion to depth 1 | First graph candidate |
| `bounded_typed_two_hop` | The same seeds plus the actual candidate's bounded expansion to depth 2 | Two-hop graph candidate |

The harness first normalizes fixture eligibility, then constructs and expands
the actual ephemeral typed graph. It drops unknown,
cross-project, non-current, unsupported, duplicate, self, and cycle edges
before relation ordering, neighbor counts, fan-out truncation, visited state,
timed traversal, or receipt construction. Only then does traversal follow the
six frozen relation families: `belongs_to`, `supersedes`, `depends_on`,
`blocks`, `implements`, and `tested_by`. It uses a maximum of two outgoing
neighbors per eligible source and 24 expanded edges per query. The normalized
graph has zero accepted self-edges and zero accepted cycle revisits; rejected
edge classes are reported only as aggregate normalization evidence. The
fixture's cross-project edge never becomes disclosure or traversal work.

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
| Accepted cycle revisits per query | `0` | `0` |
| Accepted self-edges | `0` | `0` |
| Warm p95 latency | `<= 50 ms` | `<= 50 ms` |

Receipts hash the ordered, query-ordinal result projection and outcome flags
after eligible graph normalization. They must be identical across repeated
runs, relation reordering, and addition of ineligible/unknown relations. The
report does not emit the material being hashed.

## Relation-family ablation and kill rule

One frozen single-hop query case is assigned to each relation family. For each
family, the evaluator compares all-family one-hop traversal with the same case
after removing only that family. A family is kept when removing it reduces
required-evidence recall by at least `0.10` or Project CAOS by at least `0.10`,
without a safety regression. A family is killed when both deltas are below
`0.10`, or when enabling that family creates a safety failure that disappears
under its ablation. Every family must receive a valid finite-metric `keep` or
`kill` decision; malformed, unknown, or internally inconsistent decisions fail
the harness self-test. These decisions are explicitly synthetic integration
hypotheses only, never promotion evidence.

The corrected synthetic run at fixture revision
`2026-08-25.production-ranker` produced:

| Profile | Required recall | Project CAOS | Wrong-project | Unnecessary |
|---|---:|---:|---:|---:|
| Stdlib lexical proxy | `0.537037` | `0.111111` | `1` | `4` |
| Production LexicalV3 + project eligibility | `0.537037` | `0.111111` | `0` | `0` |
| Deterministic capsule | `0.111111` | `0.000000` | `0` | `16` |
| Lexical typed one-hop | `0.962963` | `0.888889` | `0` | `0` |
| Bounded typed two-hop | `1.000000` | `1.000000` | `0` | `0` |

The harness self-test passed both graph profiles. The normalized graph rejected
seven illegal edges (one cross-project, two stale-target, one deleted-target,
one purged-target, one self-edge, and one cycle edge), accepted zero self-edges
and zero cycle revisits, observed one high-fan-out truncation, maximum depth 2,
maximum expanded edges 2, and zero bound violations. Ablations kept
`belongs_to`, `depends_on`, `blocks`, `implements`, and `tested_by`; `supersedes`
was killed because its removal changed neither required recall nor CAOS in this
synthetic integration hypothesis. These are not production acceptance,
provider or client claims, private-data evidence, release evidence, or a graph
promotion decision.

Machine output explicitly reports the checkout-local production lexical ranker
as `lexical_ranker_exercised` and the full RetrievalEngine/Core policy façade
as `not_exercised`. It names the aggregate result `harness_self_test_passed`/
`harness_self_test_failed`; it never emits a generic production-usefulness
`passed` status.

The lane hashes are fixture SHA-256
`7f17243ccf56bb83bc7e4463adaf00f2232cee4efcd219d940581e2d601bfb5e` and
contract SHA-256
`7d133a0a60ec8d72872775d845a60d30b508e2d103f7d99af811ee15f447a819`.
