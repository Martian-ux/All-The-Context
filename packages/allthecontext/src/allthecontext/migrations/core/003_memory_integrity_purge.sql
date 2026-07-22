PRAGMA foreign_keys = ON;
PRAGMA secure_delete = ON;

ALTER TABLE context_candidates ADD COLUMN entity_key TEXT;
ALTER TABLE context_candidates ADD COLUMN attribute_key TEXT;
ALTER TABLE context_records ADD COLUMN entity_key TEXT;
ALTER TABLE context_records ADD COLUMN attribute_key TEXT;

CREATE INDEX IF NOT EXISTS idx_candidates_slot
    ON context_candidates(vault_id, entity_key, attribute_key, approval_status);
CREATE INDEX IF NOT EXISTS idx_records_slot
    ON context_records(vault_id, entity_key, attribute_key, approval_status, deleted_at);

CREATE TABLE IF NOT EXISTS integrity_groups (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    entity_key TEXT NOT NULL,
    attribute_key TEXT NOT NULL,
    group_type TEXT NOT NULL CHECK(group_type IN ('duplicate', 'conflict')),
    value_fingerprint TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open', 'resolved')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(vault_id, entity_key, attribute_key, group_type, value_fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_integrity_groups_review
    ON integrity_groups(vault_id, status, group_type, updated_at);

CREATE TABLE IF NOT EXISTS integrity_group_members (
    group_id TEXT NOT NULL REFERENCES integrity_groups(id) ON DELETE CASCADE,
    record_id TEXT NOT NULL REFERENCES context_records(id) ON DELETE CASCADE,
    PRIMARY KEY(group_id, record_id)
);

CREATE TABLE IF NOT EXISTS purge_tombstones (
    stable_id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    target_type TEXT NOT NULL CHECK(target_type IN ('record', 'source')),
    purged_at TEXT NOT NULL,
    replication_sequence INTEGER,
    replication_event_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_purge_tombstones_vault
    ON purge_tombstones(vault_id, purged_at);

CREATE TABLE IF NOT EXISTS purge_jobs (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    target_type TEXT NOT NULL CHECK(target_type IN ('record', 'source')),
    target_id TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('compaction_pending', 'completed')),
    last_error_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(vault_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_purge_jobs_resume
    ON purge_jobs(vault_id, phase, updated_at);

UPDATE vaults SET schema_version = 3 WHERE schema_version < 3;
