# Retrieval and context compilation

`RetrievalBackend` is a replaceable interface. The initial implementation uses
SQLite FTS5 plus structured SQL filters and recency ordering; no embedding is
canonical or required.

The context compiler receives task/query text, client identity, requested
scopes, an optional project, and a character budget. It returns mandatory
interaction preferences first, then relevant facts/projects/decisions while
deduplicating and stopping at the budget. The response includes provenance,
omitted/unavailable scopes, a mode (`local_core`, `relay_only`,
`relay_plus_core`, or `reduced_context`), and an audit trace ID.

Permission, validity, deletion, and supersession are hard predicates applied
before relevance. `CandidateRanker` is the narrow boundary for relevance
ordering: it receives only candidates that have passed record scope selection,
client allow/deny policy, approval, validity, deletion, and supersession checks.
V1's `V1CandidateRanker` retains SQLite FTS5 BM25 and recency behavior. A future
semantic backend may rank only this already-permitted candidate set and its
indexes must be rebuildable from canonical records.

## Reproducible V1 baseline

The offline harness, sanitized synthetic corpus, graded gold judgments, usage,
metric definitions, and limitations are documented in
[`bench/README.md`](../../bench/README.md). The checked-in machine-readable
baseline is [`bench/baselines/v1.json`](../../bench/baselines/v1.json). Normal
runs deterministically generate 1k and 10k records; a capped 50k run requires an
explicit opt-in.

The fixture covers exact matches, partial and vocabulary-gap multi-term
queries, paraphrases/synonyms, typos, project/entity relationships, current and
superseded/expired/deleted records, client permissions and allowlists, near
duplicates, mandatory interaction preferences, and empty results. The harness
measures Recall@1/3/5, MRR, nDCG@5, empty rates, policy violations, temporal
precision, compiled-context coverage/redundancy, cold/warm p50/p95, SQLite index
size, initial indexing throughput, and production correction/reindex cost.

Retrieval V2 acceptance is executable through the comparison command. Every
common profile must have zero policy violations, exact Recall@5 no worse than
V1, overall MRR at least 10% better, and multi-term empty rate at least 50%
lower. The 10k profile additionally requires warm p95 below
`max(150 ms, 1.25 × V1)`. Phase 0 freezes these gates; it does not implement a
V2 ranker or claim that V2 meets them.
