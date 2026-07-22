# Retrieval and context compilation

`RetrievalEngine` is a stable facade over replaceable internal components. Phase
1 uses only SQLite FTS5 and deterministic lexical signals; no embedding or graph
database is canonical or required. Existing search/bootstrap response models and
MCP tool names are unchanged.

The pipeline has five explicit responsibilities:

1. `EligibleRecordSelector` applies vault, approval, deletion, validity,
   expiry, supersession, request filters, and client allow/deny policy.
2. `LexicalCandidateChannels` receives only eligible IDs and runs bounded
   phrase, all-term AND, and broad OR/BM25 channels (256 results per channel).
3. `ReciprocalRankFusion` combines channel ranks and bounded token-coverage,
   exact-phrase, kind, tag, project, and explicit interaction-preference
   signals. Updated time is only a deterministic tie-breaker.
4. `ContextCompiler` reserves budget for mandatory interaction preferences,
   normalizes exact duplicates, conservatively suppresses near duplicates,
   diversifies kinds/projects/sources, and places supporting records after
   primary answers.
5. `RankingExplanation` records authorized-only channel ranks and signals.
   Explanations are available through local `atc search --explain` or the
   administrator-checked internal method; they are not added to MCP responses.

Permission, validity, deletion, and supersession remain hard predicates before
all relevance work. The eligible set is materialized as a temporary ID table;
every FTS channel joins that table. `CandidateRanker` remains the fail-fast seam,
and `V1CandidateRanker` remains available for the frozen comparator and boundary
tests. A future backend may rank only this already-permitted set and any derived
index must remain rebuildable from canonical Core records.

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
`max(150 ms, 1.25 × V1)`.

## Phase 1 measured evidence

Two consecutive Windows runs on Python 3.14.3/SQLite 3.50.4 produced identical
rankings and quality metrics at 1k and 10k. Against the checked-in Windows
Python 3.12 V1 baseline, exact Recall@5 remained `1.0`; MRR increased from
`0.666667` to `0.777778` (+16.67%); multi-term empty rate fell from `0.5` to
`0.0`; and forbidden-result count remained zero. The two 10k warm p95 values
were `73.13693 ms` and `75.00416 ms`, below the `150 ms` gate.

Near-duplicate suppression reduced benchmark context redundancy from `0.25` to
`0.0`. Context coverage changed from `1.0` to `0.75` because the frozen gold set
counts both members of its declared near-duplicate pair while Phase 1 retains
one. Temporal precision remains `0.5`. The bounded alias table currently closes
one explicit vocabulary gap (`eviction` to `cache`); typo, general paraphrase,
and broader vocabulary recovery remain out of scope. Timing is local evidence,
not a cross-platform performance claim.
