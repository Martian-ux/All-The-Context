# Data model

All IDs are time-sortable UUIDv7-compatible values. Times are UTC ISO 8601.
Schemas carry `schema_version`; mutable current records also carry a monotonic
`version` and immutable version snapshots.

## Logical entities

| Entity | Purpose |
|---|---|
| `vault` | User-owned authority, display time zone, and versioned memory policy |
| `source_record` / `source_blob` / `source_blob_chunk` | Deduplicated raw local evidence; provider/format/coverage metadata, extraction status, and ordered bounded storage for large raw sources |
| `ingestion_session` / `ingestion_batch` | Coverage, resumability, atomic publication, and idempotency |
| observation | Immutable proposed, extracted, corrected, or inferred durable-context evidence |
| observation decision | Core-derived `applied`, `reinforced`, `tentative`, or `ignored` disposition, reason, policy version, origin class, and decision time |
| `context_record` | Current applied context selected by Core policy |
| Memory Truth projection | Canonical status, provenance/evidence, source metadata, decision timing, conflict state, and content-free coverage over the durable entities above |
| `context_record_version` | Immutable correction, replacement, deletion, and restoration history |
| observation/record evidence link | Why an observation created, changed, or reinforced a current record |
| `client_registration` / `permission_grant` | Identity, credential hash, scopes, and server-known client origin |
| `replication_event` / `replication_checkpoint` | Ordered signed Core projection; never an alternate authority |
| `deletion_tombstone` | Durable evidence that content is reversibly absent |
| integrity group/member | Derived duplicate/conflict diagnostics; never authority or a user task queue |
| `purge_tombstone` | Minimum opaque stable-ID replay barrier, with no raw content hash |
| `purge_job` | Crash-resumable logical-delete/compaction phase metadata |
| `audit_event` | Client access and automatic or administrative decision trace |
| Relay queued observation | Noncurrent input waiting for authoritative Core evaluation |
| `export_manifest` | Portable package schema and integrity metadata |

The compatibility schema may retain historical table or column names such as
`context_candidate` and `approval_status` during migration. Those are storage
details, not the product contract: APIs and UI describe observations,
dispositions, and current context. Existing approved rows map to applied
current records; rejected rows map to ignored observations; unresolved legacy
rows are reevaluated idempotently by the versioned policy.

Every observation carries a stable ID, content and optional structured value,
kind, scope, tags, provenance, source reference, asserted basis, observed time,
confidence, sensitivity, optional memory-slot keys, idempotency material, and
schema version. Core, not the submitter, derives the effective origin and writes
the disposition, bounded decision reason, policy version, and
affected current-record ID.

Every current record carries stable ID, kind, content and optional structured
value, scopes, tags, provenance/evidence links, confidence, sensitivity,
availability, allow/deny clients, validity, version, replacement/supersession,
timestamps, content hash, and schema version. Only current applied records are
retrieval-eligible.

## Memory Truth semantics

The Core truth projection distinguishes `current`, `tentative`, `superseded`,
`conflicted`, and `deleted`. A record's evidence links retain the observation,
relationship, disposition, decision reason, policy version, confidence,
sensitivity, source identity, and three different clocks: `effective_at` is
the asserted validity time, `observed_at` is when the source observation was
made, and `recorded_at` is when Core stored it. `deleted` is a reversible
tombstoned state; `superseded` and resolved conflict history remain inspectable
rather than being silently discarded. Coverage counts are intentionally
content-free.

Archive rebuild identity uses a source-scoped, value-aware key containing the
source ID/reference, kind and slot keys, and a canonical value fingerprint.
Only an untouched automatic record with a matching internal source-rebuild
tombstone can reuse its record ID. An ordinary user deletion blocks matching
archive evidence from creating any replacement current record until an
authorized restore. Distinct values that reuse one source reference remain
distinct records.

## Slots, conflicts, and reinforcement

`entity_key` and `attribute_key` are optional observation and record metadata.
Both must be present together. Slot equality uses NFKC, case folding, and
collapsed whitespace. Value grouping uses canonical structured JSON when
present and normalized word content otherwise.

An identical observation reinforces the existing record and adds evidence
without creating duplicate current context. Materially different values in the
same slot invoke deterministic conflict policy: explicit user evidence outranks
inference, then observation time and stable tie breakers decide otherwise equal
evidence. The policy preserves the prior version and the reason for the selected
current value. Unkeyed archive-import statements share a lineage only when a
derived subject key matches; unrelated same-kind archive memories stay
independent current records. Integrity groups remain optional diagnostics for
unusual or legacy states, not an approval queue.

## Deletion and purge

Ordinary deletion keeps the current-record identity, versions, evidence, and a
deletion tombstone so an authorized user can restore it. Purge tombstones retain
only stable ID, vault, target type, time, and optional ordered-event
coordinates. Purge jobs retain target identity, phase, timestamps, and a
bounded error code. Neither stores purged content, evidence, reasons,
content-derived hashes, or confirmation text.

Provider archive metadata is intentionally schema-flexible JSON attached to the
source record. The writer records detected provider, export format, parser
version, coverage completion, and bounded aggregate statistics. Durable
session/batch rows, not metadata, remain the authority for replay and
idempotency. `import_status` exposes `processing`, `failed`, or `complete`; the
content-addressed source blob is retained for a safe retry or later parser.
`source_blobs` owns the source hash, total size, media type, and storage kind.
Inline values are at most 8 MiB; nonempty path imports and larger in-memory
sources use contiguous, 8 MiB-or-smaller `source_blob_chunks` rows under the
same Core transaction. Chunk reconstruction must reproduce both the declared
total size and the parent SHA-256 identity.
