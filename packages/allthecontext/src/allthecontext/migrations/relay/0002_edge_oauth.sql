CREATE TABLE IF NOT EXISTS edge_oauth_clients (
    client_id TEXT PRIMARY KEY,
    client_json TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    last_authorized_at TEXT
);

CREATE TABLE IF NOT EXISTS edge_oauth_requests (
    request_id_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    oauth_state TEXT,
    scopes_json TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL CHECK (redirect_uri_explicit IN (0, 1)),
    resource TEXT NOT NULL,
    expires_at REAL NOT NULL,
    FOREIGN KEY (client_id) REFERENCES edge_oauth_clients(client_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS edge_oauth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    code_challenge TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    redirect_uri_explicit INTEGER NOT NULL CHECK (redirect_uri_explicit IN (0, 1)),
    resource TEXT NOT NULL,
    subject TEXT NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at TEXT,
    FOREIGN KEY (client_id) REFERENCES edge_oauth_clients(client_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS edge_oauth_access_tokens (
    token_hash TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    logical_client_id TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    resource TEXT NOT NULL,
    subject TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (client_id) REFERENCES edge_oauth_clients(client_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS edge_access_token_family
    ON edge_oauth_access_tokens (family_id, revoked_at);

CREATE TABLE IF NOT EXISTS edge_oauth_refresh_tokens (
    token_hash TEXT PRIMARY KEY,
    family_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    logical_client_id TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    resource TEXT NOT NULL,
    subject TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (client_id) REFERENCES edge_oauth_clients(client_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS edge_refresh_token_family
    ON edge_oauth_refresh_tokens (family_id, revoked_at);

CREATE TABLE IF NOT EXISTS edge_owner_tickets (
    ticket_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL,
    consumed_at TEXT
);

CREATE TABLE IF NOT EXISTS edge_owner_sessions (
    session_hash TEXT PRIMARY KEY,
    expires_at REAL NOT NULL,
    created_at TEXT NOT NULL,
    revoked_at TEXT
);
