PRAGMA foreign_keys = ON;

-- A source-scoped identity lets a stable parser reference reapply a memory
-- after a rebuild without creating a new current-record ID. Legacy rows stay
-- nullable because their source references may not be deterministic.
ALTER TABLE context_candidates ADD COLUMN record_key TEXT;
ALTER TABLE context_records ADD COLUMN record_key TEXT;

CREATE INDEX IF NOT EXISTS idx_candidates_record_key
    ON context_candidates(vault_id, record_key)
    WHERE record_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_records_record_key
    ON context_records(vault_id, record_key, deleted_at)
    WHERE record_key IS NOT NULL;

UPDATE context_records
SET record_key = (
    SELECT c.record_key
    FROM context_candidates c
    WHERE c.id = context_records.candidate_id
)
WHERE record_key IS NULL;

UPDATE vaults SET schema_version = 10 WHERE schema_version < 10;
