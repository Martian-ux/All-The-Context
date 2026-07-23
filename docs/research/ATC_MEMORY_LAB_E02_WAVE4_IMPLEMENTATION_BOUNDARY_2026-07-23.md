# E02 Wave 4 smallest-safe production implementation boundary

## Receipt status

This document is a design proposal derived from, but not part of, the observed
E02 result. No schema or production change was made. The observed gap evidence
is recorded separately in
`bench/reports/memory_reliability_e02_wave4.json` and
`bench/reports/memory_reliability_e02_wave4.md`.

The accepted machine receipt verifies that imported production modules
originated in the worker worktree and records only repository-relative module
paths. Earlier pre-verification receipts were invalidated and are not used as
evidence.

The proposal preserves the governing boundary: Core remains the sole canonical
authority. Relay, retrievers, clients, and derived artifacts may submit or
consume bounded metadata, but none may canonize role, applicability,
currentness, dependency, retirement, or purge state independently.

## Observed current touchpoints

These are production facts observed by code inspection and the disposable
E02 probes:

| Concern | Current touchpoints | Observed boundary |
|---|---|---|
| Canonical record semantics | `CandidateInput`, `ContextRecordOut`, `context_candidates`, `context_records`, version snapshots | `kind`, free-form `structured_value`, scopes, confidence, and time fields exist; explicit role, domain, dependency, decay, retirement, precondition, and `applies_to` fields do not. |
| Eligibility and retrieval | `SearchRequest`, `EligibleRecordSelector.select/select_authorized`, `_admissibility_inputs`, `context_fts` | Kind and explicit scopes can constrain retrieval. Scope matching accepts any requested label. `current_project` participates in admissibility/ranking but is not a universal hard applicability predicate. |
| Correction and deletion | `CoreStore.correct_record`, `delete_record`, `restore_record` | Canonical record versions change, but dependency-shaped structured metadata produces no descendant invalidation. |
| Time and lifecycle | `expires_at`, `confidence`, retrieval eligibility | Expired records are excluded from current search but remain canonical. Search does not decay confidence, and procedures have no distinct retirement state. |
| Terminal purge | `CoreStore.purge`, `purge_tombstones`, replication withdrawal events | Purge retains an opaque tombstone and fresh re-observation receives a new generated ID. Public inputs do not permit an exact same-ID reuse attempt. |
| Distribution and portability | replication events, Relay record payloads, export/import migrations | Any new canonical semantic must round-trip here; placing it only in retrieval or a sidecar would split authority. |

## Smallest safe first capability

Implement **generic epistemic role** first as one optional, explicit,
Core-owned canonical field.

The minimum end-to-end boundary is:

1. accept the field on canonical candidate input without inferring it from
   `kind`;
2. store it on candidates and current records and preserve it in every version
   snapshot;
3. expose it on record output and an exact retrieval eligibility filter;
4. include it in canonical replication and export/import round trips;
5. preserve `unknown` for legacy rows until an authorized explicit
   classification is supplied; and
6. keep role resolution before relevance scoring.

This is the smallest additive change that creates no second authority and
allows later applicability and procedure policy to depend on an explicit
semantic rather than overloaded kinds. It is not sufficient by itself for
production promotion: project/domain applicability, dependency closure, and
procedure preconditions remain required and separately gated.

## Migration risks and required guards

### Role

- An additive nullable column must remain null for legacy data. Backfilling
  from `kind` would encode the rejected kind-as-role substitution.
- Candidate, record, version snapshot, API, replication, export/import, and
  search paths must migrate atomically enough that older readers fail closed
  or preserve unknown rather than silently dropping the field.
- A role vocabulary must be versioned or bounded without turning a retrieval
  component into the taxonomy authority.

### Project and domain applicability

- Do not overload the current flat scope-label array. Its any-label behavior
  cannot represent a required project **AND** domain predicate.
- Applicability needs Core-owned normalized predicates, explicit unknown
  handling, and a hard pre-relevance eligibility check.
- Migration must avoid guessing a domain from content, tags, kind, or existing
  project scopes.

### Dependency lineage

- Store lineage in a Core-owned relation keyed to source record ID and version
  plus the derived artifact identity/version. A cache-local or research
  sidecar graph would become a second authority.
- Validate cycles, cross-scope edges, missing versions, and stale writers
  before publishing the edge or dependent artifact.
- Correction, scope change, permission change, ordinary delete, purge, and
  policy-generation change must close descendants before republication.
- Purge must remove source and descendant material while retaining only the
  permitted opaque replay barriers.

### Eviction, decay, and procedure retirement

- Keep canonical evidence history separate from eligibility or retention
  policy. Initial decay/retirement support must not destructively rewrite
  evidence or weaken purge tombstones.
- Define clock, version, audit, restoration, and replication behavior before
  adding automatic transitions.
- A procedure retirement state must be explicit and versioned; generic
  `expires_at` is not a substitute for procedure lifecycle semantics.

### Same identifier after purge

- Preserve the stable-ID tombstone namespace across imports and replication.
- Before adding any caller-selected ID path, specify and test whether reuse is
  rejected, mapped to a fresh ID, or accepted only under a separately
  authorized restore/import protocol. Never allow it to resurrect purged
  content or descendants.

### Procedure preconditions and transfer

- Preconditions and `applies_to` must be typed, versioned canonical metadata,
  not executable imported text or free-form structured-value conventions.
- Unknown or unevaluable preconditions must fail closed before relevance or
  issue.
- Transfer applicability must consume the same Core-owned project/domain
  predicates; a procedure adapter cannot maintain a parallel eligibility
  store.

## Proposed implementation order after the first field

1. Generic epistemic role end to end.
2. Core-owned project-and-domain hard applicability.
3. Core-owned version-bound dependency lineage and full invalidation closure.
4. Typed procedure preconditions and transfer applicability.
5. Explicit procedure retirement, then separately specified decay/eviction.
6. Caller-selected identifier semantics only if a real import or portability
   requirement justifies the added purge risk.

Each step requires a separate production decision and migration plan. None of
the E02 findings authorizes promotion.
