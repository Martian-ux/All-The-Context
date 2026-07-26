PRAGMA foreign_keys = ON;

-- Explicit incomplete/staging marker for content-addressed blobs.
-- Incomplete blobs are never linked from source_records and are not canonical.
ALTER TABLE source_blobs
    ADD COLUMN blob_complete INTEGER NOT NULL DEFAULT 1
    CHECK(blob_complete IN (0, 1));

-- Durable import-operation lifecycle owned by Core.
-- Progress and cancel are queryable by operation id before any source id exists.
CREATE TABLE IF NOT EXISTS import_operations (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    status TEXT NOT NULL
        CHECK(status IN (
            'awaiting_upload',
            'uploading',
            'processing',
            'complete',
            'failed',
            'cancelled'
        )),
    phase TEXT NOT NULL
        CHECK(phase IN (
            'preflight',
            'awaiting_upload',
            'uploading',
            'hashing',
            'staging',
            'storing',
            'parsing',
            'ingesting',
            'verifying',
            'publishing',
            'complete',
            'failed',
            'cancelled'
        )),
    declared_byte_size INTEGER NOT NULL
        CHECK(declared_byte_size >= 0 AND declared_byte_size <= 2000000000),
    bytes_received INTEGER NOT NULL DEFAULT 0 CHECK(bytes_received >= 0),
    bytes_committed INTEGER NOT NULL DEFAULT 0 CHECK(bytes_committed >= 0),
    content_hash TEXT,
    source_id TEXT,
    filename TEXT,
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    source_service TEXT NOT NULL DEFAULT 'auto',
    provider_hint TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0, 1)),
    progress_json TEXT NOT NULL DEFAULT '{}',
    preflight_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error_message TEXT,
    staging_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK(bytes_committed <= bytes_received),
    CHECK(bytes_received <= declared_byte_size)
);

CREATE INDEX IF NOT EXISTS idx_import_operations_status
    ON import_operations(vault_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_import_operations_source
    ON import_operations(source_id)
    WHERE source_id IS NOT NULL;

UPDATE vaults SET schema_version = 9 WHERE schema_version < 9;
