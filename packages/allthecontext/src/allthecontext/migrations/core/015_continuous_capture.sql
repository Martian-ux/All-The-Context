-- Continuous Capture is a local, opt-in ledger.  It contains no provider
-- credential material and does not make any provider/network claim.
CREATE TABLE IF NOT EXISTS capture_sources (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    provider TEXT NOT NULL CHECK(length(provider) BETWEEN 1 AND 128),
    account_label TEXT NOT NULL CHECK(length(account_label) BETWEEN 1 AND 200),
    account_fingerprint TEXT CHECK(account_fingerprint IS NULL OR length(account_fingerprint) BETWEEN 1 AND 256),
    requested_scopes_json TEXT NOT NULL DEFAULT '[]' CHECK(length(requested_scopes_json) <= 16384),
    local_only INTEGER NOT NULL DEFAULT 0 CHECK(local_only IN (0, 1)),
    local_only_acknowledged INTEGER NOT NULL DEFAULT 0 CHECK(local_only_acknowledged IN (0, 1)),
    lifecycle_state TEXT NOT NULL DEFAULT 'disabled' CHECK(lifecycle_state IN ('disabled', 'enabled', 'paused', 'degraded', 'revoked', 'reconciling')),
    credential_ref TEXT CHECK(credential_ref IS NULL OR length(credential_ref) <= 256),
    retry_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_count >= 0),
    next_retry_at TEXT,
    last_error_code TEXT CHECK(last_error_code IS NULL OR length(last_error_code) <= 96),
    last_error_at TEXT,
    lag_events INTEGER NOT NULL DEFAULT 0 CHECK(lag_events >= 0),
    lag_pages INTEGER NOT NULL DEFAULT 0 CHECK(lag_pages >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_run_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_capture_sources_vault_state
    ON capture_sources(vault_id, lifecycle_state, created_at);

CREATE TABLE IF NOT EXISTS capture_checkpoints (
    source_id TEXT PRIMARY KEY REFERENCES capture_sources(id) ON DELETE CASCADE,
    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
    cursor TEXT CHECK(cursor IS NULL OR length(cursor) <= 1024),
    last_order_key TEXT CHECK(last_order_key IS NULL OR length(last_order_key) <= 256),
    last_event_id TEXT CHECK(last_event_id IS NULL OR length(last_event_id) <= 256),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capture_events (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES capture_sources(id) ON DELETE CASCADE,
    provider_event_id TEXT NOT NULL CHECK(length(provider_event_id) BETWEEN 1 AND 256),
    provider_item_id TEXT NOT NULL CHECK(length(provider_item_id) BETWEEN 1 AND 256),
    generation INTEGER NOT NULL CHECK(generation >= 0),
    order_key TEXT NOT NULL CHECK(length(order_key) BETWEEN 1 AND 256),
    operation TEXT NOT NULL CHECK(operation IN ('upsert', 'delete')),
    normalized_payload_json TEXT NOT NULL DEFAULT '{}' CHECK(length(normalized_payload_json) <= 65536),
    payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64),
    status TEXT NOT NULL DEFAULT 'staged' CHECK(status IN ('staged', 'applied', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    error_code TEXT CHECK(error_code IS NULL OR length(error_code) <= 96),
    application_receipt TEXT CHECK(application_receipt IS NULL OR length(application_receipt) <= 96),
    idempotency_key TEXT NOT NULL UNIQUE CHECK(length(idempotency_key) <= 128),
    received_at TEXT NOT NULL,
    applied_at TEXT,
    UNIQUE(source_id, provider_event_id)
);

CREATE INDEX IF NOT EXISTS ix_capture_events_source_order
    ON capture_events(source_id, generation, order_key, id);
CREATE INDEX IF NOT EXISTS ix_capture_events_source_status
    ON capture_events(source_id, status, received_at);

CREATE TABLE IF NOT EXISTS capture_items (
    source_id TEXT NOT NULL REFERENCES capture_sources(id) ON DELETE CASCADE,
    provider_item_id TEXT NOT NULL CHECK(length(provider_item_id) BETWEEN 1 AND 256),
    canonical_record_id TEXT NOT NULL CHECK(length(canonical_record_id) BETWEEN 1 AND 256),
    generation INTEGER NOT NULL CHECK(generation >= 0),
    last_event_id TEXT NOT NULL,
    item_state TEXT NOT NULL CHECK(item_state IN ('active', 'deleted')),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source_id, provider_item_id)
);

CREATE TABLE IF NOT EXISTS capture_runs (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES capture_sources(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK(state IN ('running', 'completed', 'failed', 'abandoned')),
    lease_token TEXT NOT NULL CHECK(length(lease_token) <= 256),
    lease_expires_at TEXT NOT NULL CHECK(length(lease_expires_at) <= 64),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    page_count INTEGER NOT NULL DEFAULT 0 CHECK(page_count >= 0),
    event_count INTEGER NOT NULL DEFAULT 0 CHECK(event_count >= 0),
    applied_event_count INTEGER NOT NULL DEFAULT 0 CHECK(applied_event_count >= 0),
    duplicate_event_count INTEGER NOT NULL DEFAULT 0 CHECK(duplicate_event_count >= 0),
    failure_count INTEGER NOT NULL DEFAULT 0 CHECK(failure_count >= 0),
    error_code TEXT CHECK(error_code IS NULL OR length(error_code) <= 96),
    started_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_capture_runs_source_time
    ON capture_runs(source_id, started_at DESC);
CREATE INDEX IF NOT EXISTS ix_capture_runs_lease
    ON capture_runs(state, lease_expires_at);

UPDATE vaults SET schema_version = 15 WHERE schema_version < 15;
