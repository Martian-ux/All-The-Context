PRAGMA foreign_keys = ON;

-- A source-rebuild tombstone is reopenable only when the validated publish
-- ceremony records the exact finished session and generation that created it.
-- Existing 011 rows cannot prove that binding, so downgrade them to ordinary
-- deletion barriers before adding the fail-closed binding columns.
ALTER TABLE deletion_tombstones ADD COLUMN rebuild_session_id TEXT REFERENCES ingestion_sessions(id);
ALTER TABLE deletion_tombstones ADD COLUMN rebuild_generation INTEGER;
ALTER TABLE deletion_tombstones ADD COLUMN rebuild_source_marker TEXT;

UPDATE deletion_tombstones
SET deletion_origin='ordinary',
    deletion_source_id=NULL
WHERE deletion_origin='source_rebuild'
   OR rebuild_session_id IS NULL
   OR rebuild_generation IS NULL
   OR rebuild_source_marker IS NULL;

CREATE INDEX IF NOT EXISTS idx_deletion_tombstones_rebuild_binding
    ON deletion_tombstones(
        record_id,
        deletion_origin,
        deletion_source_id,
        rebuild_session_id,
        rebuild_generation,
        rebuild_source_marker
    );

CREATE TRIGGER IF NOT EXISTS validate_deletion_tombstone_rebuild_binding_insert
BEFORE INSERT ON deletion_tombstones
WHEN NEW.deletion_origin NOT IN ('ordinary', 'source_rebuild')
    OR (NEW.deletion_origin='ordinary' AND (
        NEW.deletion_source_id IS NOT NULL
        OR NEW.rebuild_session_id IS NOT NULL
        OR NEW.rebuild_generation IS NOT NULL
        OR NEW.rebuild_source_marker IS NOT NULL
    ))
    OR (NEW.deletion_origin='source_rebuild' AND (
        NEW.deletion_source_id IS NULL
        OR NEW.rebuild_session_id IS NULL
        OR NEW.rebuild_generation IS NULL
        OR NEW.rebuild_generation < 1
        OR NEW.rebuild_source_marker IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM ingestion_sessions s
            WHERE s.id=NEW.rebuild_session_id
              AND s.mode='archive_import'
              AND s.status='finished'
              AND s.client_id IS NULL
        )
    ))
BEGIN
    SELECT RAISE(ABORT, 'invalid deletion tombstone rebuild binding');
END;

CREATE TRIGGER IF NOT EXISTS validate_deletion_tombstone_rebuild_binding_update
BEFORE UPDATE OF deletion_origin, deletion_source_id,
    rebuild_session_id, rebuild_generation, rebuild_source_marker
ON deletion_tombstones
WHEN NEW.deletion_origin NOT IN ('ordinary', 'source_rebuild')
    OR (NEW.deletion_origin='ordinary' AND (
        NEW.deletion_source_id IS NOT NULL
        OR NEW.rebuild_session_id IS NOT NULL
        OR NEW.rebuild_generation IS NOT NULL
        OR NEW.rebuild_source_marker IS NOT NULL
    ))
    OR (NEW.deletion_origin='source_rebuild' AND (
        NEW.deletion_source_id IS NULL
        OR NEW.rebuild_session_id IS NULL
        OR NEW.rebuild_generation IS NULL
        OR NEW.rebuild_generation < 1
        OR NEW.rebuild_source_marker IS NULL
        OR NOT EXISTS (
            SELECT 1 FROM ingestion_sessions s
            WHERE s.id=NEW.rebuild_session_id
              AND s.mode='archive_import'
              AND s.status='finished'
              AND s.client_id IS NULL
        )
    ))
BEGIN
    SELECT RAISE(ABORT, 'invalid deletion tombstone rebuild binding');
END;

UPDATE vaults SET schema_version = 12 WHERE schema_version < 12;
