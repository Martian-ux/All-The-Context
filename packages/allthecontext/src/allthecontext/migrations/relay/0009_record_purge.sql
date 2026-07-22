CREATE TABLE IF NOT EXISTS relay_purge_tombstones (
    vault_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    purge_scope TEXT NOT NULL CHECK (purge_scope IN ('record', 'source')),
    purged_at TEXT NOT NULL,
    event_sequence INTEGER NOT NULL CHECK (event_sequence >= 1),
    event_id TEXT NOT NULL,
    PRIMARY KEY (vault_id, record_id)
);

CREATE TABLE IF NOT EXISTS relay_purge_compaction_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    pending INTEGER NOT NULL CHECK (pending IN (0, 1)),
    requested_at TEXT,
    completed_at TEXT,
    last_error_code TEXT
);

INSERT OR IGNORE INTO relay_purge_compaction_state(
    singleton, pending, requested_at, completed_at, last_error_code
) VALUES (1, 0, NULL, NULL, NULL);

CREATE TRIGGER IF NOT EXISTS edge_block_purge_tombstone_insert_after_decommission
BEFORE INSERT ON relay_purge_tombstones
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_purge_tombstone_update_after_decommission
BEFORE UPDATE ON relay_purge_tombstones
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_purge_compaction_update_after_decommission
BEFORE UPDATE ON relay_purge_compaction_state
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;
