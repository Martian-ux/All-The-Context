-- Opaque field/anchor identities and chronology only: no deleted values or hashes.
CREATE TABLE client_capture_purge_barriers (
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    partition_id TEXT NOT NULL,
    facet_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    purged_at TEXT NOT NULL,
    PRIMARY KEY(vault_id, partition_id, facet_id, record_id)
);
CREATE TABLE client_capture_erasure_links (
    vault_id TEXT NOT NULL REFERENCES vaults(id),
    record_id TEXT NOT NULL REFERENCES context_records(id) ON DELETE CASCADE,
    observation_id TEXT NOT NULL REFERENCES context_candidates(id) ON DELETE CASCADE,
    partition_id TEXT NOT NULL,
    facet_id TEXT NOT NULL,
    PRIMARY KEY(vault_id, record_id, observation_id)
);
UPDATE vaults SET schema_version=22 WHERE schema_version<22;
