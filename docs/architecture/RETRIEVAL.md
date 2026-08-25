# Retrieval and context compilation

`RetrievalEngine` is a stable facade over replaceable local components. Retrieval
V3 remains SQLite-first and deterministic; no embedding, graph database, hosted
service, native extension, or production vector dependency is required.

This architecture note describes the combined local Import/Memory Truth/
Retrieval integration. `ContextPackMetadata` is a bounded provider-facing
selection report, distinct from import `closed_coverage` and Memory Truth
coverage. The 17-case usefulness harness and focused checks are developer
evidence only; this document makes no hosted CI, release, or exact-client
acceptance claim.

The production pipeline has seven ordered boundaries:

1. `EligibleRecordSelector.select_authorized` applies vault, applied/current
   state, request filters, and client allow/deny policy. Relevance never receives
   a staged, tentative, or ignored observation. Deleted rows can cross only this metadata boundary so the temporal
   resolver can enforce their terminal state; they never reach ranking.
2. `TemporalSidecar` rebuilds content-free UTC interval metadata from
   authoritative current-record rows and purge tombstones. It resolves `current` or an offset-aware
   `as_of` instant over the already-authorized ID set.
3. `LexicalV3CandidateRanker` runs weighted BM25 over an ephemeral FTS5 corpus
   containing only temporally eligible IDs. The bounded evidence path used by
   context compilation uses a 100-record result pool; phrase/all-term channels
   precede a bounded exact-OR fallback, and prefix fallback is limited to four
   tokens of at least four characters. Multi-term fallback keeps candidates
   with at least two lexical terms, or one explicitly curated alias target;
   admissibility still decides whether that alias target occurs in candidate
   content. The production evidence-pool threshold is two hits.
4. Conflict state is joined for the temporally eligible candidates, then
   `DeterministicAdmissibilityGate` evaluates content-only task/query coverage,
   scope/project fit, requested-kind compatibility, confidence/explicitness,
   and conflict state. Query intent is projected into direct topical anchors
   and request scaffolding; scaffolding is not a required content match. A
   focused multi-term task retains the deterministic `0.75` content floor. A
   broad task with scaffolding may cover a meaningful content subset, but still
   needs at least two matched anchors for up to three anchors or three matched
   anchors for longer tasks, bounded by that same floor. A curated lexical alias
   counts only when its target occurs in candidate content. Zero- and
   one-anchor content matches remain rejected; no row count, metadata, fixture
   alias, confidence, or conflict state can bypass the rule. Single-term and
   empty-query paths retain their usefulness/fail-open behavior. Sparse or
   underspecified evidence otherwise fails open. An optional learned gate can
   run only in shadow and has no production authority.
5. `DeterministicUsefulnessReranker` reranks only the authorized, temporally
   eligible, conflict-checked, task-admissible candidates. It applies bounded
   local query-intent, field-coverage, recency, confidence, availability,
   sensitivity, conflict, provenance, and actionability signals without
   weakening any preceding gate.
6. `ContextCompiler` maps only the already-authorized, temporally eligible, and
   task-admitted result set into opaque hashed semantic/diversity labels. A
   `DeterministicSetSelector` then maximizes exact rational marginal utility per
   character while prioritizing mandatory preferences and enforcing transitive
   duplicate groups, same-slot conflict exclusion, supporting-evidence
   relationships, a 32-record pack cap, and the exact budget. When more than
   eight interaction preferences are eligible, the compiler uses that same
   selector and opaque signals to choose a deterministic reserve of at most
   eight; overflow preferences become optional fallback candidates after a
   compatible primary result. Each overflow preference may follow any selected
   compatible primary, while applicable supporting evidence remains ahead of
   that fallback tier. The compiler expresses primary-plus-evidence as a chain:
   evidence supports the primary, and an actually selected applicable evidence
   item gates overflow. The final exact budget may keep that evidence and omit
   overflow; when no applicable evidence is selected (absent, infeasible, or
   tied to an excluded primary), overflow falls back to primary support rather
   than deadlocking. Fixed mandatory records are first reduced to their
   deterministic feasible selector survivors, whose duplicate/conflict groups
   exclude preferences from the reserve. Fixed candidates use their original
   ordered base utility and context-independent semantic/diversity signals in
   both compiler passes, so the authoritative survivor cannot change after
   reserve facets are selected. Caller-ranked non-preference candidates retain
   their order; only preference reserve/overflow tiers are canonicalized for
   input-order determinism. The reserve budget leaves room for the cheapest
   feasible primary result when such a pair fits. At eight or
   fewer preferences, the existing mandatory behavior is unchanged. Bootstrap
   returns optional content-free `pack_metadata` accounting for the exact union
   of complete bounded policy/temporal candidate pools, omissions,
   provenance-backed items, and truthful truncation reasons. It cannot weaken an
   upstream gate.
7. Administrator-only diagnostics expose authorized returned record IDs plus
   numeric values, aggregate counts, and closed reason codes. They never include
   raw query/context text, denied IDs, or unauthorized-derived vocabulary.

The frozen V2 comparator uses the legacy complete policy selector and V2 ranker
behind `FrozenV2Comparator.frozen_pipeline`; advancing the production default
therefore cannot silently move the baseline. `CandidateRanker` remains the
fail-fast seam. Future backends may rank only the already-permitted set, and
every derived index must remain discardable and rebuildable from Core.

## Temporal semantics

All intervals are normalized to UTC and half-open: `[valid_from, valid_to)`;
expiry is exclusive. With no explicit `valid_from`, current-record creation is the
start. Already-expired imported records with no asserted start are treated as
historical rather than making the sidecar invalid. A superseder closes its
predecessor at the superseder's effective start and the predecessor does not
return merely because the superseder expires. Unrelated conflicting claims
remain separate series for later conflict-aware set selection.

Deletion and purge are terminal for both current and historical search. Restore
never imports the sidecar as authority: startup/restart/search reconciles it
against current authoritative rows and purge tombstones, replacing stale or corrupt
derived state. The normal current path resolves only records with meaningful
temporal state through the sidecar; ordinary active IDs are a deterministic
fast path. `as_of` always resolves the complete authorized set.

An in-place Core correction retains its stable record ID and advances its
current-record revision. Retrieval uses that latest applied content across the
record's interval; `record_history` remains the audit API for earlier content
snapshots. Separate superseding records provide content-addressable historical
search across revisions.

`SearchRequest.as_of`, MCP `search_context(as_of=...)`, and CLI `atc search
--as-of ...` require an offset-aware ISO 8601 timestamp. `current_project` is an
optional admissibility hint, not an authorization grant.

## Catalog search versus bounded evidence

The user-facing `RetrievalEngine.search()` and Core `/v1/context/search`
endpoint are catalog searches. After authorization, request filters, temporal
selection, and admissibility, they enumerate the complete deterministic match
set and report that exact post-policy count. Offset/cursor pages therefore do
not stop at the evidence-pool boundary. Catalog enumeration remains bounded by
the lexical candidate hard cap of 50,000 authorized IDs and never evaluates
untrusted or unauthorized corpus rows.

`RetrievalEngine.bootstrap()` uses the separate bounded evidence path and keeps
its 100-record retrieval pool before `ContextCompiler` performs budgeted set
selection. Its nonempty request is a broad context-assembly query: authorization,
request scopes/kinds, temporal eligibility, and dedicated project signals still
apply, while the direct-search multi-term content floor is reserved for the
answer-oriented search path. Empty bootstrap already has no task terms and
remains fail-open. This preserves bounded MCP context compilation; it is not the
source of the catalog API's `total`. Its optional `pack_metadata` envelope is
provider-facing accounting, not ranking diagnostics, and does not expose query
text or record IDs beyond the selected items already returned. The candidate
count is the exact union of the two complete bounded policy/temporal-eligible
pools when those internal IDs are available; legacy/injected diagnostics use
their bounded count fallback.

The high-cardinality repair is local to compilation: authorization, temporal
eligibility, sensitivity, admissibility, retrieval-pool limits, selector
contracts, storage, and public response schemas are unchanged. With more than
eight preferences, no-match queries still receive the reserve; matching queries
can select primary records and supporting evidence before optional preference
overflow. Tight budgets remain deterministic and fail closed. The sanitized
regression uses 77 preferences, 20 relevant records, ten generic queries, a
4,000-character budget, and a no-match query.

Pack accounting is reconciled at each forwarding boundary: `selected_count`
equals the returned item count, `omitted_count` equals
`candidate_count - selected_count`, `used_chars` is recomputed from the
returned items, and `provenance_backed_count` counts only returned items with
forwarded provenance. Core's duplicate/conflict suppression aggregates are
explicitly Core-selection-scoped; Edge has no suppression-group identities, so
it preserves only bounded claims about candidates still omitted and does not
attribute ACL or envelope removals to those reasons. The public model rejects
contradictory cross-field counts.

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

Retrieval V2 comparison is executable through the comparison command. Every
common profile must have zero policy violations, exact Recall@5 no worse than
V1, overall MRR at least 10% better, and multi-term empty rate at least 50%
lower. The 10k profile additionally requires warm p95 below
`max(150 ms, 1.25 × V1)`.

## Phase 1 local measured evidence

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
stage diagnosis. The set-selection harness adds compatibility, semantic
coverage, diversity, supporting-evidence, mandatory-preference, conflict,
redundancy, and budget scenarios; all 11 local gates pass.

## Synthetic retrieval usefulness

`python -m bench.retrieval_usefulness` is a developer-facing eval of whether
the production search, bootstrap, and get paths return the right current facts,
exclude stale, conflicting, withdrawn, tentative, and denied items, keep
sensitive records within client allow/deny and sensitivity labels, preserve
provenance fields, stay inside character budgets, and emit the provider JSON
shape used by Core HTTP/MCP. It uses public observation APIs and an isolated
synthetic vault only; its runner self-checks that the checkout-local package is
being exercised. It is not a beta-acceptance gate. Usage and the compact
baseline scorecard are in
[`bench/README.md`](../../bench/README.md).

## Optional shadow research

The repository contains two research-only paths under `bench/`; neither is
imported by the application package or has ranking authority:

- `dense_shadow` is explicitly disabled by default, rebuild-only, in-memory,
  CPU-only, and fixed at 384 float32 dimensions. A deterministic synthetic
  runtime measures storage and exact-scan mechanics but makes no semantic
  claim. At 10,000 candidates, exact scan used 15,360,000 vector bytes and
  measured `400.294955 ms` warm p95 against a `150 ms` target. This satisfies the
  latency precondition for a later ANN shadow study, but the real local-model
  and semantic-coverage paths remain `not_exercised`; no ANN or production
  dense retrieval is implemented.
- `source_evidence_retrieval` freezes the candidate-scoped lexical source pool
  and compares lexical passages, deterministic token MaxSim, and
  diversity-aware token MaxSim on sanitized imported-chat evidence. At the
  normal 64/256-source profiles, every variant preserves `1.0` evidence recall
  and facet coverage with zero policy violations; diversity-aware MaxSim
  reduces measured redundancy to zero. Neural late interaction remains
  `not_exercised`, and no source-evidence variant is wired into runtime.

Learned sparse retrieval and production late interaction remain unscheduled.
A reranker still requires evidence that candidate-pool recall is strong while
final ordering is measurably poor. ANN may now be researched only as an
optional local shadow because exact scan missed its explicit target; production
use still requires a genuine model, semantic benefit, recall parity, and a
default dependency strategy consistent with the local cross-platform boundary.
