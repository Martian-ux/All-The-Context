PRAGMA foreign_keys = ON;

-- A rebuild may reuse an automatically withdrawn record identity. Ordinary
-- user/source deletion must never be eligible for that path.
ALTER TABLE deletion_tombstones ADD COLUMN deletion_origin TEXT NOT NULL DEFAULT 'ordinary';
ALTER TABLE deletion_tombstones ADD COLUMN deletion_source_id TEXT REFERENCES source_records(id);

CREATE INDEX IF NOT EXISTS idx_deletion_tombstones_rebuild
    ON deletion_tombstones(record_id, deletion_origin, deletion_source_id);

-- Keep the additive migration compatible with databases that already applied
-- either ALTER above before a process stopped.  The trigger rejects malformed
-- provenance rows, while Core's source-rebuild capability performs the
-- stronger record/source checks before creating one.
CREATE TRIGGER IF NOT EXISTS validate_deletion_tombstone_provenance_insert
BEFORE INSERT ON deletion_tombstones
WHEN NEW.deletion_origin NOT IN ('ordinary', 'source_rebuild')
    OR (NEW.deletion_origin = 'ordinary' AND NEW.deletion_source_id IS NOT NULL)
    OR (NEW.deletion_origin = 'source_rebuild' AND NEW.deletion_source_id IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'invalid deletion tombstone provenance');
END;

CREATE TRIGGER IF NOT EXISTS validate_deletion_tombstone_provenance_update
BEFORE UPDATE OF deletion_origin, deletion_source_id ON deletion_tombstones
WHEN NEW.deletion_origin NOT IN ('ordinary', 'source_rebuild')
    OR (NEW.deletion_origin = 'ordinary' AND NEW.deletion_source_id IS NOT NULL)
    OR (NEW.deletion_origin = 'source_rebuild' AND NEW.deletion_source_id IS NULL)
BEGIN
    SELECT RAISE(ABORT, 'invalid deletion tombstone provenance');
END;

UPDATE vaults SET schema_version = 11 WHERE schema_version < 11;
