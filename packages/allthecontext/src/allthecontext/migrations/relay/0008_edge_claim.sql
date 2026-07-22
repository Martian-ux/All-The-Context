CREATE TABLE IF NOT EXISTS edge_claim_runtime (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    claim_id TEXT NOT NULL,
    replication_secret TEXT NOT NULL,
    replication_token TEXT NOT NULL,
    generated_at REAL NOT NULL,
    acknowledged_at REAL
);

CREATE TABLE IF NOT EXISTS edge_claim_challenges (
    challenge_hash TEXT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL
);
