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
| `audit_event` | Client access and administrative decision trace |
| `pending_memory_proposal` | Non-canonical Relay proposal queue |
| `export_manifest` | Portable package schema and integrity metadata |

Every record supports stable ID, kind, content and optional structured value,
scopes, tags, provenance/source, service and type, evidence, confidence,
sensitivity, availability, allow/deny clients, validity, approval, version,
supersession, timestamps, content hash, and schema version.
