PRAGMA foreign_keys = ON;

ALTER TABLE source_records ADD COLUMN deleted_at TEXT;
ALTER TABLE source_records ADD COLUMN deleted_reason TEXT;
ALTER TABLE source_records ADD COLUMN deleted_by TEXT;

CREATE INDEX IF NOT EXISTS idx_sources_active
    ON source_records(vault_id, deleted_at, created_at);

CREATE TABLE IF NOT EXISTS source_deletion_members (
    source_id TEXT NOT NULL REFERENCES source_records(id) ON DELETE CASCADE,
    record_id TEXT NOT NULL REFERENCES context_records(id) ON DELETE CASCADE,
    deleted_version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(source_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_source_deletion_members_record
    ON source_deletion_members(record_id);

UPDATE vaults SET schema_version = 6 WHERE schema_version < 6;
