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

V1 uses push synchronization initiated by Core. The future Core-online bridge
will also be outbound from Core; no home-network inbound port is required.
