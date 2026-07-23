PRAGMA foreign_keys = ON;

ALTER TABLE context_candidates ADD COLUMN observed_at TEXT;
ALTER TABLE context_candidates ADD COLUMN observation_origin TEXT;
ALTER TABLE context_candidates ADD COLUMN disposition TEXT NOT NULL DEFAULT 'staged';
ALTER TABLE context_candidates ADD COLUMN record_id TEXT;
ALTER TABLE context_candidates ADD COLUMN decision_reason TEXT;
ALTER TABLE context_candidates ADD COLUMN decided_at TEXT;
ALTER TABLE context_candidates ADD COLUMN policy_version TEXT;

ALTER TABLE context_records ADD COLUMN observed_at TEXT;
ALTER TABLE context_records ADD COLUMN observation_origin TEXT;
ALTER TABLE context_records ADD COLUMN policy_version TEXT;

CREATE TABLE IF NOT EXISTS memory_policies (
    vault_id TEXT PRIMARY KEY REFERENCES vaults(id),
    mode TEXT NOT NULL DEFAULT 'automatic',
    sensitive_mode TEXT NOT NULL DEFAULT 'local_only',
    inference_mode TEXT NOT NULL DEFAULT 'corroborate',
    policy_version TEXT NOT NULL DEFAULT 'automatic-v1',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS context_observation_links (
    observation_id TEXT NOT NULL REFERENCES context_candidates(id) ON DELETE CASCADE,
    record_id TEXT NOT NULL REFERENCES context_records(id) ON DELETE CASCADE,
    relationship TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(observation_id, record_id)
);
CREATE INDEX IF NOT EXISTS idx_observation_links_record
    ON context_observation_links(record_id, created_at);
CREATE INDEX IF NOT EXISTS idx_observation_disposition
    ON context_candidates(vault_id, disposition, created_at);

INSERT OR IGNORE INTO memory_policies(
    vault_id, mode, sensitive_mode, inference_mode, policy_version, created_at, updated_at
)
SELECT id, 'automatic', 'local_only', 'corroborate', 'automatic-v1', created_at, created_at
FROM vaults;

UPDATE context_candidates
SET observed_at = COALESCE(observed_at, created_at),
    observation_origin = COALESCE(observation_origin, 'legacy'),
    disposition = CASE approval_status
        WHEN 'approved' THEN 'applied'
        WHEN 'rejected' THEN 'ignored'
        ELSE 'staged'
    END,
    record_id = (
        SELECT context_records.id
        FROM context_records
        WHERE context_records.candidate_id = context_candidates.id
        LIMIT 1
    ),
    decision_reason = COALESCE(
        decision_reason,
        CASE approval_status
            WHEN 'approved' THEN COALESCE(review_reason, 'approved before automatic policy')
            WHEN 'rejected' THEN COALESCE(review_reason, 'rejected before automatic policy')
            ELSE NULL
        END
    ),
    decided_at = CASE
        WHEN approval_status IN ('approved', 'rejected') THEN COALESCE(decided_at, reviewed_at)
        ELSE decided_at
    END,
    policy_version = CASE
        WHEN approval_status IN ('approved', 'rejected')
            THEN COALESCE(policy_version, 'legacy-review-v1')
        ELSE policy_version
    END;

UPDATE context_records
SET observed_at = COALESCE(observed_at, valid_from, created_at),
    observation_origin = COALESCE(observation_origin, 'legacy'),
    policy_version = COALESCE(policy_version, 'legacy-review-v1');

UPDATE vaults SET schema_version = 5 WHERE schema_version < 5;
