# Replication protocol

Core writes one event sequence per vault in the same transaction as canonical
state changes. Events use stable IDs, monotonic sequence numbers, canonical
JSON payload hashes, and HMAC-SHA256 authentication with a Relay-specific
secret.

Relay accepts only the next sequence. An already-applied matching event is an
idempotent success; a gap, changed replay, invalid MAC, invalid payload hash, or
malformed event is rejected. Relay checkpoints only after applying the event
transactionally.

`record_upserted` carries the minimum approved `always_available` record and
access metadata. `record_withdrawn` removes a record whose availability changed.
`record_deleted` removes it and persists a tombstone. Permission and corrected
record state propagate through a new upsert. Raw sources and candidates never
cross this boundary.

`record_purged` is the irreversible Core-authoritative v1 contract. Its payload
has exactly `record_id`, `purged_at`, `purge_scope` (`record` or `source`), and
`irreversible: true`. The envelope remains signed and monotonically ordered,
but contains no content, structured value, evidence, source metadata, reason,
or content-derived hash. Extra fields, a mismatched record ID, or a false
irreversible marker are invalid. Core rewrites any still-retained historical
outbox payload for that record to an opaque withdrawal while preserving its
sequence position, then appends the purge event transactionally.

The purge contract is one-way authority: Edge proposals cannot use this event
shape and no Edge action can downgrade it into an upsert. Relay applies a valid
next-sequence purge in one transaction: it removes the live projection, FTS,
ordinary deletion tombstone, supersession references, and historical
content-derived event fingerprints for the record; persists an opaque hash-free
purge tombstone; retains the exact current purge event for idempotent replay;
advances the checkpoint; and marks physical compaction pending. Any later
transition for that stable ID is rejected.

Checkpoint advancement means logical absence is durable even if SQLite is busy.
Relay retries WAL truncation and secure-delete VACUUM at startup and when Core
requests Edge status. Status exposes the pending state and a fixed error code,
never raw SQLite text. Core reports synchronization as degraded while that
physical phase remains pending, then ready after a later successful retry.
Compaction covers the live Relay database/WAL set; provider snapshots, backups,
media remanence, and user copies remain outside the protocol.

V1 uses push synchronization initiated by Core. The future Core-online bridge
will also be outbound from Core; no home-network inbound port is required.
