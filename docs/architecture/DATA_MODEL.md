# Data model

All IDs are time-sortable UUIDv7-compatible values. Times are UTC ISO 8601.
Schemas carry `schema_version`; mutable current records also carry a monotonic
`version` and immutable version snapshots.

## Logical entities

| Entity | Purpose |
|---|---|
| `vault` | User-owned authority, display time zone, and versioned memory policy |
| `source_record` / `source_blob` / `source_blob_chunk` | Deduplicated raw local evidence; provider/format/import-coverage metadata, extraction status, and ordered bounded storage for large raw sources |
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
| `context_user_mutation` | Append-only typed local mutation ledger used as a rebuild barrier |
| integrity group/member | Derived duplicate/conflict diagnostics; never authority or a user task queue |
| `purge_tombstone` | Minimum opaque stable-ID replay barrier, with no raw content hash |
| `purge_job` | Crash-resumable logical-delete/compaction phase metadata |
| `audit_event` | Client access and automatic or administrative decision trace |
| Relay queued observation | Noncurrent input waiting for authoritative Core evaluation |
| `export_manifest` | Portable package schema and integrity metadata |

Import, truth, and retrieval accounting are separate contracts. Import parsing
publishes the item-level `CoverageReport.closed_coverage` map with exactly the
seven logical keys `recognized`, `excluded`, `skipped`, `unavailable`,
`duplicate`, `failed`, and `unparsed`. A source-level `failed` or `cancelled`
terminal state is stored separately in source metadata and operation status; it
is never added to those item counts. Memory Truth exposes the durable,
content-free `TruthCoverageOut` projection over sources, observations, records,
conflicts, and ingestion sessions. Retrieval's `ContextPackMetadata` is a
bounded provider-facing report for one compiled bootstrap pack. It is transient
selection accounting, not import coverage or Memory Truth coverage, and is not
part of canonical Core authority.

Core migrations `010_memory_truth.sql` through
`014_typed_user_action_evidence.sql` belong to the Memory Truth foundation. The
Migration `015_continuous_capture.sql` is the provider-neutral Continuous
Capture foundation. Migration `016_registered_source_admission.sql` adds the
nullable registered-source provenance seam to existing candidates. Migration
`017_capture_page_recovery.sql` adds bounded page-recovery state to the capture
checkpoint row. Migration `018_client_capture_source.sql` binds a capture source
to its registered client principal and preserves lifecycle/formation event
lineage. Migration `019_integrity_member_reverse_lookup.sql` adds the reverse
record-to-integrity-group lookup index; the next free Core migration number is
`020`.

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

`context_user_mutations` is an append-only typed authority ledger. Each new
row stores a closed mutation kind/origin, a bounded normalized actor, typed
user-action evidence ID/version/digest, and a deterministic intent key. Schema
14 adds nullable `user_action_kind` and `user_action_key` fields to
`context_record_versions`; Core fills them only on the local user-action path.
The ledger's `evidence_kind='user_action'` row must match those fields exactly,
including the action key, before a portable restore may import it. A generic
record-version coordinate remains only as a compatibility fact for
`legacy_user_edit`, never as authority for a typed action. The ledger does not
store caller reason text; version, source-deletion, and tombstone reason fields
are canonical content-free codes. Purge may remove the referenced record and
history while retaining the opaque barrier row.

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
authorized restore. Every public/local restore, correction, availability
change, and explicit deletion path writes a typed `local_user` row to
`context_user_mutations`; source rebuild checks that ledger rather than
interpreting free-form version reasons. Portable restore does not generically
insert ledger rows: it accepts only typed rows whose action kind/key, evidence
ID/version, digest, vault, record, source relationship, canonical reason, and
intent key all match same-package durable evidence; malformed or forged rows
are ignored. Isolated
recovery carries verified destination-local mutation rows transactionally with
purge tombstones before package import. A restore of an already-current record
still creates one version-backed barrier and exact retry is idempotent. Legacy
databases and pre-ledger exports infer at most one `legacy_user_edit` row from
typed source-lineage evidence, excluding source-free manual records and
validated automatic reapplications. Distinct values that reuse one source
reference remain distinct records. Encrypted package authentication proves
possession of the passphrase and package integrity, not provenance from an
untampered originating Core: a party able to rewrite and re-encrypt every
package member can also rewrite its canonical evidence. The typed boundary
therefore rejects row-only forgery but does not claim external authenticity.

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
idempotency. `import_status` exposes `processing`, `failed`, `cancelled`, or
`complete`; the
content-addressed source blob is retained for a safe retry or later parser.
`source_blobs` owns the source hash, total size, media type, and storage kind.
Inline values are at most 8 MiB; nonempty path imports and larger in-memory
sources use contiguous, 8 MiB-or-smaller `source_blob_chunks` rows under the
same Core transaction. Chunk reconstruction must reproduce both the declared
total size and the parent SHA-256 identity.
