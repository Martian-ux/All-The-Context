-- Bounded page recovery belongs to the existing capture checkpoint row.  The
-- page event IDs refer to capture_events; this migration adds no truth table.
ALTER TABLE capture_checkpoints ADD COLUMN pending_generation INTEGER
    CHECK(pending_generation IS NULL OR pending_generation >= 0);
ALTER TABLE capture_checkpoints ADD COLUMN pending_cursor TEXT
    CHECK(pending_cursor IS NULL OR length(pending_cursor) <= 1024);
ALTER TABLE capture_checkpoints ADD COLUMN pending_event_ids_json TEXT
    CHECK(pending_event_ids_json IS NULL OR (
        length(pending_event_ids_json) <= 66306
        AND json_valid(pending_event_ids_json)
        AND json_type(pending_event_ids_json) = 'array'
    ));

UPDATE vaults SET schema_version = 17 WHERE schema_version < 17;
