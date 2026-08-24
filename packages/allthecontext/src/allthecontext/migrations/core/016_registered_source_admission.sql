PRAGMA foreign_keys = ON;

-- Registered-source provenance belongs to the existing observation ledger.
-- These nullable columns are deliberately not a second capture truth table.
ALTER TABLE context_candidates ADD COLUMN capture_source_id TEXT
    REFERENCES capture_sources(id) ON DELETE SET NULL;
ALTER TABLE context_candidates ADD COLUMN capture_event_id TEXT
    REFERENCES capture_events(id) ON DELETE SET NULL;
ALTER TABLE context_candidates ADD COLUMN capture_binding_hash TEXT
    CHECK(capture_binding_hash IS NULL OR (
        length(capture_binding_hash) = 64
        AND capture_binding_hash NOT GLOB '*[^0-9a-f]*'
    ));

CREATE UNIQUE INDEX IF NOT EXISTS uq_context_candidates_capture_event
    ON context_candidates(capture_event_id)
    WHERE capture_event_id IS NOT NULL;

UPDATE vaults SET schema_version = 16 WHERE schema_version < 16;
