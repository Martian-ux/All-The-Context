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

The Memory Truth/automatic-policy schema exports observations, disposition
fields, policy versions, observation/record evidence links, optional slot
metadata, opaque purge tombstones/jobs, and the append-only
`context_user_mutations` ledger. Core migrations 010–014 define this foundation;
the provider-neutral Continuous Capture foundation is migration 015 and the
registered-source admission contract is migration 016. Capture runtime tables
remain machine-local operational state and are not part of portable exports.
They are excluded even when `include_sources=true`:
`capture_sources`, `capture_events`, `capture_items`, `capture_checkpoints`, and
`capture_runs`. Admitted candidate rows may retain their content-free binding
hash, but exported `capture_source_id` and `capture_event_id` are always null so
the archive has no dangling capture foreign keys. Restore safely ignores those
five tables in older archives and never rehydrates them; same-database restart
retains them normally.
Derived integrity groups and search indexes are excluded. A legacy package that
does not contain the ledger is upgraded from durable historical evidence with
one deterministic `legacy_user_edit` row per affected record at most.
Schema-14 packages also export nullable `user_action_kind` and
`user_action_key` fields on record-version history. New ledger rows use
`evidence_kind='user_action'`; restore validates the action kind/key, exact
history row, canonical reason, source relationship, and typed digest together.
An older generic `record_version` ledger row is not accepted as a typed action;
it remains eligible only for the explicitly compatibility-scoped
`legacy_user_edit` inference path.
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

Source metadata preserves import `closed_coverage` and the separate
`source_terminal_reason`; neither is merged into Memory Truth coverage during
restore. Retrieval `ContextPackMetadata` describes one transient compiled pack
and is not a canonical export table.

Database-file replication is unrelated and prohibited; this explicit user
backup format is application-level and versioned.

The encrypted container authenticates the package bytes to whoever holds the
passphrase. It does not attest that an external, untampered Core authored the
contents: a passphrase holder can rewrite the ZIP and re-encrypt it. The
typed-action checks close the row-only forgery described above while retaining
that honest trust boundary.
