-- Client-runtime capture reuses the existing capture ledger.  This nullable
-- ownership link lets Core derive one stable source from an exact registered
-- principal without accepting a caller-supplied source or authority claim.
ALTER TABLE capture_sources ADD COLUMN client_principal_id TEXT
    REFERENCES client_registrations(id) ON DELETE SET NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_capture_sources_client_principal
    ON capture_sources(vault_id, client_principal_id)
    WHERE client_principal_id IS NOT NULL;

-- One lifecycle event may retain both its raw turn observation and a formed
-- Core candidate. Preserve database-enforced uniqueness for registered-source
-- and raw lifecycle observations while allowing formation projections to point
-- back to the same event.
DROP INDEX IF EXISTS uq_context_candidates_capture_event;
CREATE UNIQUE INDEX IF NOT EXISTS uq_context_candidates_capture_event
    ON context_candidates(capture_event_id)
    WHERE capture_event_id IS NOT NULL
      AND COALESCE(source_type, '') <> 'client_capture_formation';
CREATE INDEX IF NOT EXISTS ix_context_candidates_capture_event
    ON context_candidates(capture_event_id)
    WHERE capture_event_id IS NOT NULL;

UPDATE vaults SET schema_version = 18 WHERE schema_version < 18;
