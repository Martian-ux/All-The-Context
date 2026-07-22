CREATE TABLE IF NOT EXISTS edge_proposal_receipts (
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    proposal_id TEXT NOT NULL,
    proposal_hash TEXT NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES context_candidates(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (vault_id, proposal_id)
);
