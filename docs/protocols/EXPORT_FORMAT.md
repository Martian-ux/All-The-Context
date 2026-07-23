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

The automatic-policy schema exports observations, disposition fields, policy
versions, observation/record evidence links, optional slot metadata, and opaque
purge tombstones/jobs. Derived integrity groups and search indexes are excluded.
During restore, both existing and incoming purge tombstones are loaded before
content rows. A pre-purge record or source with a tombstoned stable ID, its
observation/history/deletion event, its source blob, and attributable batch hash
are skipped or scrubbed, so an older portable copy cannot resurrect that stable
ID. Operators must retain the current Core/tombstones when selectively merging
an older export; restoring an old export onto wholly unrelated empty storage
cannot know about tombstones that are absent from that export.

Restore preserves legacy review metadata for compatibility, maps existing
approved/rejected rows to applied/ignored dispositions, and runs the
idempotent versioned policy only for eligible unresolved observations. Staged,
tentative, or ignored observations do not become current merely because they
were exported and restored.

Database-file replication is unrelated and prohibited; this explicit user
backup format is application-level and versioned.
