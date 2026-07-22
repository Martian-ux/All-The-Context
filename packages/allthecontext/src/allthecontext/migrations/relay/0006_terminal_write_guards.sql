CREATE TRIGGER IF NOT EXISTS edge_block_record_tombstone_update_after_decommission
BEFORE UPDATE ON relay_deletion_tombstones
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_proposal_update_after_decommission
BEFORE UPDATE ON pending_memory_proposals
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_oauth_client_update_after_decommission
BEFORE UPDATE ON edge_oauth_clients
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_oauth_request_insert_after_decommission
BEFORE INSERT ON edge_oauth_requests
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_oauth_request_update_after_decommission
BEFORE UPDATE ON edge_oauth_requests
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_oauth_code_insert_after_decommission
BEFORE INSERT ON edge_oauth_codes
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_oauth_code_update_after_decommission
BEFORE UPDATE ON edge_oauth_codes
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_access_token_insert_after_decommission
BEFORE INSERT ON edge_oauth_access_tokens
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_access_token_update_after_decommission
BEFORE UPDATE ON edge_oauth_access_tokens
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_refresh_token_insert_after_decommission
BEFORE INSERT ON edge_oauth_refresh_tokens
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_refresh_token_update_after_decommission
BEFORE UPDATE ON edge_oauth_refresh_tokens
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_owner_ticket_update_after_decommission
BEFORE UPDATE ON edge_owner_tickets
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_owner_session_insert_after_decommission
BEFORE INSERT ON edge_owner_sessions
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_owner_session_update_after_decommission
BEFORE UPDATE ON edge_owner_sessions
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_registration_update_after_decommission
BEFORE UPDATE ON edge_registration_state
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_identity_insert_after_decommission
BEFORE INSERT ON edge_identity_state
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;

CREATE TRIGGER IF NOT EXISTS edge_block_identity_update_after_decommission
BEFORE UPDATE ON edge_identity_state
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;
