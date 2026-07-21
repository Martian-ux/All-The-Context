# Export, backup, and restore

Exports are application-level packages, not copies of a live SQLite file. They
include a manifest, schema version, content hashes, canonical history, source
metadata and blobs, permissions, approval state, and tombstones.

Create and inspect an export with the dashboard or CLI:

```text
python -m allthecontext.cli export PATH_TO_EXPORT --include-sources --include-audit
python -m allthecontext.cli restore PATH_TO_EXPORT --dry-run
```

Replace `PATH_TO_EXPORT` with an explicit path appropriate to the current
shell. Stop Core before destructive recovery. Restore into a new empty vault,
verify the manifest and all hashes, apply migrations transactionally, then run
retrieval and replication smoke tests before switching the active vault.

Exports contain more sensitive data than Relay. Store them in an encrypted
location and test restore procedures regularly.
