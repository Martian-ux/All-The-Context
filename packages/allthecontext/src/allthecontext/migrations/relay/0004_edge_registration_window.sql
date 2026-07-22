CREATE TABLE IF NOT EXISTS edge_registration_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    open_until REAL NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO edge_registration_state(singleton, open_until)
VALUES (1, 0);
