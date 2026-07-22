CREATE TABLE IF NOT EXISTS edge_identity_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    vault_id TEXT NOT NULL,
    binding_fingerprint TEXT NOT NULL,
    bound_at TEXT NOT NULL
);
