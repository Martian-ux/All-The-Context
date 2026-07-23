# Data model

All IDs are time-sortable UUIDv7-compatible values. Times are UTC ISO 8601.
Schemas carry `schema_version`; mutable canonical records also carry a monotonic
`version` and immutable version snapshots.

| Entity | Purpose |
|---|---|
| `vault` | User-owned authority and display time zone |
| `source_record` / `source_blob` | Deduplicated raw local evidence and metadata |
| `ingestion_session` / `ingestion_batch` | Coverage, resumability, and idempotency |
| `context_candidate` | Untrusted proposed or extracted durable context |
| `context_record` | Current approved canonical state |
| `context_record_version` | Immutable correction and supersession history |
| `client_registration` / `permission_grant` | Identity, credential hash, and scopes |
| `replication_event` / `replication_checkpoint` | Ordered signed synchronization |
| `deletion_tombstone` | Durable evidence that content must remain absent |
| `integrity_group` / `integrity_group_member` | Derived duplicate/conflict review state; never canonical authority |
| `purge_tombstone` | Minimum opaque stable-ID replay barrier, with no raw content hash |
| `purge_job` | Crash-resumable logical-delete/compaction phase metadata |
| `audit_event` | Client access and administrative decision trace |
| `pending_memory_proposal` | Legacy non-canonical experimental Relay queue; not used by V1 UI |
| `export_manifest` | Portable package schema and integrity metadata |

Every record supports stable ID, kind, content and optional structured value,
scopes, tags, provenance/source, service and type, evidence, confidence,
sensitivity, availability, allow/deny clients, validity, approval, version,
supersession, timestamps, content hash, and schema version.

Core migration `003_memory_integrity_purge.sql` adds optional normalized
`entity_key` and `attribute_key` columns to candidates and records. Both keys
must be present together. Candidate keys are copied (or administrator-edited)
only during explicit approval. Existing rows remain valid with both fields
null. Slot equality uses NFKC, case folding, and collapsed whitespace. Value
grouping uses canonical structured JSON when present and normalized word content
otherwise. Multiple fingerprints produce a conflict group; two or more records
with one fingerprint produce a separate duplicate group.

Purge tombstones retain only stable ID, vault, target type, time, and optional
ordered-event coordinates. Purge jobs retain target identity, phase, timestamps,
and a bounded error code. Neither stores canonical content, evidence, reasons,
content-derived hashes, or confirmation text.
