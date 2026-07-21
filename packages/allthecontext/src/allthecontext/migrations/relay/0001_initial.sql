CREATE TABLE IF NOT EXISTS replication_checkpoints (
    vault_id TEXT PRIMARY KEY,
    last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
    last_event_id TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applied_replication_events (
    vault_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    event_fingerprint TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (vault_id, sequence)
);

CREATE TABLE IF NOT EXISTS relay_context_records (
    vault_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    source_service TEXT,
    confidence REAL,
    sensitivity TEXT NOT NULL,
    availability TEXT NOT NULL CHECK (availability = 'always_available'),
    allowed_clients_json TEXT NOT NULL,
    denied_clients_json TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    version INTEGER NOT NULL CHECK (version >= 1),
    supersedes TEXT,
    superseded_by TEXT,
    approval_status TEXT NOT NULL CHECK (approval_status = 'approved'),
    content_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    event_sequence INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (vault_id, record_id)
);

CREATE INDEX IF NOT EXISTS relay_records_retrieval
    ON relay_context_records (vault_id, superseded_by, valid_from, valid_until);

CREATE VIRTUAL TABLE IF NOT EXISTS relay_context_fts USING fts5(
    vault_id UNINDEXED,
    record_id UNINDEXED,
    kind,
    content,
    tokenize = 'unicode61'
);

CREATE TABLE IF NOT EXISTS relay_deletion_tombstones (
    vault_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    deleted_at TEXT NOT NULL,
    version INTEGER,
    content_hash TEXT,
    event_sequence INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    PRIMARY KEY (vault_id, record_id)
);

CREATE TABLE IF NOT EXISTS pending_memory_proposals (
    proposal_id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    confidence REAL,
    sensitivity TEXT NOT NULL,
    requested_availability TEXT NOT NULL,
    source_service TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'imported', 'rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE (vault_id, client_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS pending_proposals_queue
    ON pending_memory_proposals (vault_id, status, created_at, proposal_id);
