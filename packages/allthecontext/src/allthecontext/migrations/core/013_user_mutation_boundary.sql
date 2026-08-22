PRAGMA foreign_keys = ON;

-- User mutations are an explicit local ledger, not an inference from version
-- reason text.  It intentionally has no record foreign key: purge removes the
-- record but must not remove the durable barrier history for that record ID.
-- Evidence columns bind each row to a typed, content-free local state proof;
-- the export boundary revalidates that proof before accepting a row.
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
    evidence_kind TEXT,
    evidence_id TEXT,
    evidence_version INTEGER,
    evidence_hash TEXT,
    intent_key TEXT,
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

CREATE UNIQUE INDEX IF NOT EXISTS uq_context_user_mutations_intent
    ON context_user_mutations(intent_key)
    WHERE intent_key IS NOT NULL;

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
-- pre-ledger schema.  Only source-backed lineage with typed state evidence is
-- eligible.  Brand-new direct records and validated automatic reapplications
-- are deliberately excluded; no free-form reason text is authoritative.
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
JOIN source_records s ON s.id=r.source_id AND s.vault_id=r.vault_id
WHERE r.source_id IS NOT NULL
  AND (
      r.observation_origin='local_admin'
      OR EXISTS (
          SELECT 1
          FROM context_candidates c
          WHERE c.supersedes=r.id
            AND lower(c.kind)='correction'
            AND c.source_id IS NOT NULL
            AND c.observation_origin='local_admin'
      )
      OR EXISTS (
          SELECT 1
          FROM context_record_versions v
          WHERE v.record_id=r.id
            AND json_valid(v.snapshot_json)
            AND json_type(v.snapshot_json, '$.source_id')='text'
            AND (
                json_type(v.snapshot_json, '$.deleted_at')='text'
                OR json_extract(v.snapshot_json, '$.observation_origin')='local_admin'
                OR lower(v.reason) IN ('availability changed', 'availability_changed')
            )
      )
      OR EXISTS (
          SELECT 1
          FROM source_deletion_members m
          WHERE m.source_id=r.source_id AND m.record_id=r.id
      )
  )
  AND NOT EXISTS (
      SELECT 1
      FROM deletion_tombstones t
      WHERE t.record_id=r.id AND t.deletion_origin='source_rebuild'
  )
  AND NOT EXISTS (
      SELECT 1
      FROM context_observation_links l
      WHERE l.record_id=r.id
        AND l.observation_id=r.candidate_id
        AND l.relationship='reapplied'
  );

UPDATE vaults SET schema_version = 13 WHERE schema_version < 13;
