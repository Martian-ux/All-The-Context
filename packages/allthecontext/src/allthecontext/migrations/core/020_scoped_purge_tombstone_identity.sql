PRAGMA foreign_keys = ON;
PRAGMA secure_delete = ON;

-- The original table used stable_id as a database-global key even though the
-- row already carried vault and target-type identity. Preserve every legacy
-- row while making the authoritative identity explicit.
CREATE TABLE purge_tombstones_v20 (
    stable_id TEXT NOT NULL,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    target_type TEXT NOT NULL CHECK(target_type IN ('record', 'source')),
    purged_at TEXT NOT NULL,
    replication_sequence INTEGER,
    replication_event_id TEXT,
    PRIMARY KEY(vault_id, target_type, stable_id)
);

INSERT INTO purge_tombstones_v20
    (stable_id,vault_id,target_type,purged_at,replication_sequence,replication_event_id)
SELECT stable_id,vault_id,target_type,purged_at,replication_sequence,replication_event_id
FROM purge_tombstones;

DROP TABLE purge_tombstones;
ALTER TABLE purge_tombstones_v20 RENAME TO purge_tombstones;

CREATE INDEX idx_purge_tombstones_vault
    ON purge_tombstones(vault_id, purged_at);

UPDATE vaults SET schema_version = 20 WHERE schema_version < 20;
