CREATE TABLE IF NOT EXISTS edge_instance_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    decommissioned_at TEXT
);

INSERT OR IGNORE INTO edge_instance_state(singleton, decommissioned_at)
VALUES (1, NULL);

CREATE TRIGGER IF NOT EXISTS edge_block_record_insert_after_decommission
BEFORE INSERT ON relay_context_records
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_record_update_after_decommission
BEFORE UPDATE ON relay_context_records
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_tombstone_after_decommission
BEFORE INSERT ON relay_deletion_tombstones
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_event_after_decommission
BEFORE INSERT ON applied_replication_events
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_checkpoint_insert_after_decommission
BEFORE INSERT ON replication_checkpoints
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_checkpoint_update_after_decommission
BEFORE UPDATE ON replication_checkpoints
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_proposal_after_decommission
BEFORE INSERT ON pending_memory_proposals
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_oauth_client_after_decommission
BEFORE INSERT ON edge_oauth_clients
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_owner_ticket_after_decommission
BEFORE INSERT ON edge_owner_tickets
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;
