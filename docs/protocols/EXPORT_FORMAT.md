# Portable export format

The `.atc` package begins with an All The Context magic/version header, random
salt and nonce, followed by an AES-256-GCM encrypted ZIP payload and its
authentication tag. Scrypt derives the key from a user passphrase. Plaintext is
created only in an operating-system temporary directory during the current CLI
implementation and is removed when the operation ends.

The encrypted payload contains `manifest.json` and one human-inspectable JSONL
file per included table. The manifest records format/schema versions, row
counts, options, and SHA-256 for every JSONL entry. Raw sources and audit data
are opt-in. FTS/embedding indexes are excluded and rebuilt.

Restore authenticates before parsing, rejects traversal/absolute/oversized ZIP
entries, verifies every manifest hash, and uses duplicate-safe inserts into an
already migrated clean Core. `--dry-run` verifies without writing.

Database-file replication is unrelated and prohibited; this explicit user
backup format is application-level and versioned.
