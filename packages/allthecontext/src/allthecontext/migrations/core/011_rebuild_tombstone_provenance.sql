PRAGMA foreign_keys = ON;

-- A rebuild may reuse an automatically withdrawn record identity. Ordinary
-- user/source deletion must never be eligible for that path.
ALTER TABLE deletion_tombstones ADD COLUMN deletion_origin TEXT NOT NULL DEFAULT 'ordinary';
ALTER TABLE deletion_tombstones ADD COLUMN deletion_source_id TEXT REFERENCES source_records(id);

CREATE INDEX IF NOT EXISTS idx_deletion_tombstones_rebuild
    ON deletion_tombstones(record_id, deletion_origin, deletion_source_id);

UPDATE vaults SET schema_version = 11 WHERE schema_version < 11;
