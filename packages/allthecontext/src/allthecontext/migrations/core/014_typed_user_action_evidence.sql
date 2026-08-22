PRAGMA foreign_keys = ON;

-- A record-version reason is not proof that a local user action occurred.
-- These nullable fields are emitted only by Core's local user-action paths;
-- imported rows must match both the kind and deterministic action key.
ALTER TABLE context_record_versions ADD COLUMN user_action_kind TEXT;
ALTER TABLE context_record_versions ADD COLUMN user_action_key TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_context_record_versions_user_action
    ON context_record_versions(record_id, user_action_key)
    WHERE user_action_key IS NOT NULL;

UPDATE vaults SET schema_version = 14 WHERE schema_version < 14;
