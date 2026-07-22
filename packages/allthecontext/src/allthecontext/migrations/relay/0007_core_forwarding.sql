CREATE TABLE IF NOT EXISTS edge_forward_requests (
    request_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    client_scopes_json TEXT NOT NULL,
    operation TEXT NOT NULL,
    request_json TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','claimed','answered','cancelled')),
    claim_hash TEXT,
    claimed_at REAL,
    response_json TEXT,
    response_bytes INTEGER,
    completed_at REAL
);

CREATE INDEX IF NOT EXISTS edge_forward_requests_ready
ON edge_forward_requests(state, expires_at, created_at);

CREATE TABLE IF NOT EXISTS edge_forward_rate_events (
    client_id TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS edge_forward_rate_events_client
ON edge_forward_rate_events(client_id, created_at);

CREATE TABLE IF NOT EXISTS edge_forward_core_state (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    last_seen_at REAL NOT NULL
);

CREATE TRIGGER IF NOT EXISTS edge_block_forward_insert_after_decommission
BEFORE INSERT ON edge_forward_requests
WHEN (SELECT decommissioned_at FROM edge_instance_state WHERE singleton = 1) IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'Edge is decommissioned');
END;
