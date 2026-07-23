# Retrieval and context compilation

`RetrievalEngine` is a stable facade over replaceable local components. Retrieval
V3 remains SQLite-first and deterministic; no embedding, graph database, hosted
service, native extension, or production vector dependency is required.

The production pipeline has six ordered boundaries:

1. `EligibleRecordSelector.select_authorized` applies vault, approval, request
   filters, and client allow/deny policy. Relevance never receives a rejected
   row. Deleted rows can cross only this metadata boundary so the temporal
   resolver can enforce their terminal state; they never reach ranking.
2. `TemporalSidecar` rebuilds content-free UTC interval metadata from canonical
   Core rows and purge tombstones. It resolves `current` or an offset-aware
   `as_of` instant over the already-authorized ID set.
3. `LexicalV3CandidateRanker` runs weighted BM25 over an ephemeral FTS5 corpus
   containing only temporally eligible IDs. Phrase/all-term channels precede a
   bounded exact-OR fallback; prefix fallback is limited to four tokens of at
   least four characters. The production evidence-pool threshold is two hits.
4. `DeterministicAdmissibilityGate` evaluates numeric task/query coverage,
   scope/project fit, requested-kind compatibility, confidence/explicitness,
   and conflict state. Sparse or underspecified evidence fails open. An optional
   learned gate can run only in shadow and has no production authority.
5. `ContextCompiler` reserves mandatory-preference budget, suppresses exact and
   conservative near duplicates, diversifies kinds/projects/sources, and places
   supporting evidence after primary context. Deterministic marginal set
   selection is the next integration stage.
6. Administrator-only diagnostics expose authorized returned record IDs plus
   numeric values, aggregate counts, and closed reason codes. They never include
   raw query/context text, denied IDs, or unauthorized-derived vocabulary.

The frozen V2 comparator uses the legacy complete policy selector and V2 ranker
behind `FrozenV2Comparator.frozen_pipeline`; advancing the production default
therefore cannot silently move the baseline. `CandidateRanker` remains the
fail-fast seam. Future backends may rank only the already-permitted set, and
every derived index must remain discardable and rebuildable from Core.

## Temporal semantics

All intervals are normalized to UTC and half-open: `[valid_from, valid_to)`;
expiry is exclusive. With no explicit `valid_from`, canonical creation is the
start. Already-expired imported records with no asserted start are treated as
historical rather than making the sidecar invalid. A superseder closes its
predecessor at the superseder's effective start and the predecessor does not
return merely because the superseder expires. Unrelated conflicting claims
remain separate series for later conflict-aware set selection.

Deletion and purge are terminal for both current and historical search. Restore
never imports the sidecar as authority: startup/restart/search reconciles it
against current canonical rows and purge tombstones, replacing stale or corrupt
derived state. The normal current path resolves only records with meaningful
temporal state through the sidecar; ordinary active IDs are a deterministic
fast path. `as_of` always resolves the complete authorized set.

An in-place Core correction retains its stable record ID and advances its
canonical revision. Retrieval uses that latest approved content across the
record's interval; `record_history` remains the audit API for earlier content
snapshots. Separate superseding records provide content-addressable historical
search across revisions.

`SearchRequest.as_of`, MCP `search_context(as_of=...)`, and CLI `atc search
--as-of ...` require an offset-aware ISO 8601 timestamp. `current_project` is an
optional admissibility hint, not an authorization grant.

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

## Retrieval V3 gates

`python -m bench.retrieval_v3_foundation run` measures the immutable V2
comparator and pins its base commit, sanitized fixture hashes, and ranking
fingerprints. `python -m bench.retrieval_v3_combined` runs the production V3
candidate at 1k and 10k and fails unless exact Recall@5 and semantic coverage
are at least the comparator, admissibility and temporal precision improve,
duplicate redundancy and policy violations are zero, conflict/ranking behavior
is deterministic, deleted/purged records do not resurrect, historical and
restore paths are exercised, and 10k warm p95 stays below 150 ms.

The standalone lexical, temporal, and admissibility harnesses remain useful for
stage diagnosis. Learned sparse retrieval, production late interaction,
rerankers, and ANN remain unscheduled. A reranker requires evidence that the
candidate pool has strong recall but final ordering is poor; ANN requires exact
scan to miss an explicit latency target.
