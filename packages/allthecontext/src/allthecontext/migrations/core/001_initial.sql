PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS vaults (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    display_timezone TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS source_blobs (
    content_hash TEXT PRIMARY KEY,
    content BLOB NOT NULL,
    byte_size INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS source_records (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    content_hash TEXT NOT NULL REFERENCES source_blobs(content_hash),
    source_service TEXT NOT NULL,
    source_type TEXT NOT NULL,
    filename TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    import_status TEXT NOT NULL DEFAULT 'complete',
    parser_warnings_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(vault_id, content_hash, source_service, source_type)
);

CREATE TABLE IF NOT EXISTS client_registrations (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    name TEXT NOT NULL,
    token_hash TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    auto_approve INTEGER NOT NULL DEFAULT 0,
    revoked_at TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS permission_grants (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL REFERENCES client_registrations(id),
    scope TEXT NOT NULL,
    granted_at TEXT NOT NULL,
    revoked_at TEXT,
    UNIQUE(client_id, scope)
);

CREATE TABLE IF NOT EXISTS ingestion_sessions (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    client_id TEXT REFERENCES client_registrations(id),
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    accessible_sources_json TEXT NOT NULL,
    unavailable_sources_json TEXT NOT NULL,
    notes TEXT,
    idempotency_key TEXT,
    coverage_json TEXT,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ingestion_begin_idempotency
    ON ingestion_sessions(vault_id, client_id, mode, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS ingestion_batches (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES ingestion_sessions(id),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    candidate_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS context_candidates (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    session_id TEXT REFERENCES ingestion_sessions(id),
    source_id TEXT REFERENCES source_records(id),
    source_reference TEXT,
    submitted_by_client_id TEXT REFERENCES client_registrations(id),
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    structured_value_json TEXT,
    scopes_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    source_service TEXT,
    source_type TEXT,
    evidence TEXT,
    confidence REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    availability TEXT NOT NULL,
    allowed_clients_json TEXT NOT NULL,
    denied_clients_json TEXT NOT NULL,
    valid_from TEXT,
    expires_at TEXT,
    supersedes TEXT,
    explicit_user_statement INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT,
    approval_status TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_candidates_review
    ON context_candidates(vault_id, approval_status, created_at);
CREATE INDEX IF NOT EXISTS idx_candidates_source ON context_candidates(source_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_proposal_idempotency
    ON context_candidates(vault_id, submitted_by_client_id, idempotency_key)
    WHERE submitted_by_client_id IS NOT NULL AND idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS context_records (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    candidate_id TEXT REFERENCES context_candidates(id),
    source_id TEXT REFERENCES source_records(id),
    source_reference TEXT,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    structured_value_json TEXT,
    scopes_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    source_service TEXT,
    source_type TEXT,
    evidence TEXT,
    confidence REAL NOT NULL,
    sensitivity TEXT NOT NULL,
    availability TEXT NOT NULL,
    allowed_clients_json TEXT NOT NULL,
    denied_clients_json TEXT NOT NULL,
    valid_from TEXT,
    expires_at TEXT,
    supersedes TEXT,
    explicit_user_statement INTEGER NOT NULL DEFAULT 0,
    approval_status TEXT NOT NULL,
    version INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_records_filter
    ON context_records(vault_id, approval_status, availability, kind, updated_at);

CREATE TABLE IF NOT EXISTS context_record_versions (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES context_records(id),
    version INTEGER NOT NULL,
    snapshot_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(record_id, version)
);

CREATE VIRTUAL TABLE IF NOT EXISTS context_fts USING fts5(
    record_id UNINDEXED,
    content,
    kind,
    tags,
    scopes,
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS replication_events (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    record_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    mac TEXT,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    UNIQUE(vault_id, sequence)
);

CREATE TABLE IF NOT EXISTS replication_checkpoints (
    relay_id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    sequence INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deletion_tombstones (
    record_id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    deleted_version INTEGER NOT NULL,
    reason TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    deleted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    client_id TEXT,
    action TEXT NOT NULL,
    trace_id TEXT NOT NULL,
    record_ids_json TEXT NOT NULL DEFAULT '[]',
    denied_record_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_events(vault_id, created_at);

CREATE TABLE IF NOT EXISTS context_errors (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    client_id TEXT,
    record_id TEXT,
    candidate_id TEXT REFERENCES context_candidates(id),
    description TEXT NOT NULL,
    evidence TEXT,
    created_at TEXT NOT NULL
);
