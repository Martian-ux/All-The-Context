PRAGMA foreign_keys = ON;

-- User mutations are an explicit local ledger, not an inference from version
-- reason text.  It intentionally has no record foreign key: purge removes the
-- record but must not remove the durable barrier history for that record ID.
CREATE TABLE IF NOT EXISTS context_user_mutations (
    id TEXT PRIMARY KEY,
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    record_id TEXT NOT NULL,
    mutation_kind TEXT NOT NULL CHECK (
        mutation_kind IN ('restore', 'correction', 'availability_change',
                          'delete', 'source_delete', 'legacy_user_edit')
    ),
    mutation_origin TEXT NOT NULL CHECK (mutation_origin='local_user'),
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (length(trim(record_id)) > 0),
    CHECK (length(trim(actor)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_context_user_mutations_record
    ON context_user_mutations(vault_id, record_id, created_at, id);

-- Legacy inference is one compatibility fact per record, while explicit
-- local operations remain an append-only event stream.  This makes repeated
-- restore of the same pre-ledger snapshot a no-op for inferred rows.
CREATE UNIQUE INDEX IF NOT EXISTS uq_context_user_mutations_legacy_record
    ON context_user_mutations(record_id)
    WHERE mutation_kind='legacy_user_edit';

-- The ledger is append-only.  A restore/export can insert historical rows, but
-- no restore payload or purge path may clear an already-recorded mutation.
CREATE TRIGGER IF NOT EXISTS reject_context_user_mutations_update
BEFORE UPDATE ON context_user_mutations
BEGIN
    SELECT RAISE(ABORT, 'context user mutation ledger is append-only');
END;

CREATE TRIGGER IF NOT EXISTS reject_context_user_mutations_delete
BEFORE DELETE ON context_user_mutations
BEGIN
    SELECT RAISE(ABORT, 'context user mutation ledger is append-only');
END;

-- Preserve the existing fail-closed behavior for databases upgraded from the
-- pre-ledger schema.  Deleted snapshots identify a prior restore without
-- depending on arbitrary reason text; the reason checks retain the older
-- availability/correction protections that had no structured representation.
INSERT OR IGNORE INTO context_user_mutations
    (id,vault_id,record_id,mutation_kind,mutation_origin,actor,created_at)
SELECT
    'legacy-013:' || r.id,
    r.vault_id,
    r.id,
    'legacy_user_edit',
    'local_user',
    'migration-013',
    COALESCE(r.updated_at, r.created_at)
FROM context_records r
WHERE r.observation_origin='archive_import'
  AND NOT EXISTS (
      SELECT 1
      FROM deletion_tombstones t
      WHERE t.record_id=r.id AND t.deletion_origin='source_rebuild'
  )
  AND (
      EXISTS (
          SELECT 1
          FROM context_candidates c
          WHERE c.supersedes=r.id AND lower(c.kind)='correction'
      )
      OR EXISTS (
          SELECT 1
          FROM context_record_versions v
          WHERE v.record_id=r.id
            AND (
                lower(v.reason) LIKE '%availability changed%'
                OR lower(v.reason) LIKE '%by user%'
                OR lower(v.reason) LIKE '%explicit user correction%'
                OR CASE WHEN json_valid(v.snapshot_json)
                        THEN json_type(v.snapshot_json, '$.deleted_at')
                   END = 'text'
            )
      )
  );

UPDATE vaults SET schema_version = 13 WHERE schema_version < 13;
