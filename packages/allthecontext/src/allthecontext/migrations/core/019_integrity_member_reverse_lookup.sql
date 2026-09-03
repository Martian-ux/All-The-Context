PRAGMA foreign_keys = ON;

CREATE INDEX IF NOT EXISTS idx_integrity_group_members_record_group
    ON integrity_group_members(record_id, group_id);

UPDATE vaults SET schema_version = 19 WHERE schema_version < 19;
