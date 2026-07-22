CREATE TABLE IF NOT EXISTS remote_edge_clients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    context_scopes_json TEXT NOT NULL,
    approved_at TEXT NOT NULL,
    revoked_at TEXT
);
