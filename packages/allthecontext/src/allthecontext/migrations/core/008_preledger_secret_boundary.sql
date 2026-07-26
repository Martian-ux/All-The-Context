PRAGMA foreign_keys = ON;
PRAGMA secure_delete = ON;

CREATE TABLE IF NOT EXISTS secret_refusal_receipts (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    principal_key TEXT NOT NULL,
    route TEXT NOT NULL,
    operation_id TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    detector_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(vault_id, principal_key, route, operation_id)
);
CREATE INDEX IF NOT EXISTS idx_secret_refusal_created
    ON secret_refusal_receipts(vault_id, created_at);

ALTER TABLE ingestion_batches
    ADD COLUMN refused_count INTEGER NOT NULL DEFAULT 0 CHECK(refused_count >= 0);

UPDATE vaults SET schema_version = 8 WHERE schema_version < 8;
