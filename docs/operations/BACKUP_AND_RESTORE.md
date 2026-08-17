# Export, backup, and restore

Exports are application-level packages, not copies of a live SQLite file. They
include a manifest, schema version, content hashes, current-context history,
observations, automatic dispositions and policy versions, evidence links,
source metadata and blobs, permissions, and tombstones.

The dashboard **Backup** page creates and downloads a complete encrypted export
in one operation. It includes source material and audit events. The passphrase
is sent only in the protected same-origin POST body, is retained only for the
request, and is never put in a URL or browser storage. Core streams the encrypted
result from an isolated temporary file and removes that file after the response
finishes or fails. Only one dashboard export runs at a time, and Core directs
vaults above the configured dashboard-export size bound to the CLI.

The status value labeled **Core database** is the durable SQLite footprint: the
main database file plus its write-ahead log (`-wal`) when present. The transient
shared-memory (`-shm`) coordination file is intentionally excluded.

The current contributor CLI supports scripted exports, selective packages, and
restore:

```text
python -m allthecontext.cli export PATH_TO_EXPORT --include-sources --include-audit
python -m allthecontext.cli restore PATH_TO_EXPORT --dry-run
```

Replace `PATH_TO_EXPORT` with an explicit path appropriate to the current
shell. Stop Core before destructive recovery. Restore into a new empty vault,
verify the manifest and all hashes, apply migrations transactionally, then run
retrieval smoke tests before switching the active vault.

This contributor command requires Python/source and is not the public-beta
recovery surface. Every supported Windows and Linux release artifact includes a
version-matched recovery/admin helper or native mode with installed
help (`--recovery-help`). It performs stopped-Core preflight, isolated restore,
manifest/database/source verification, retrieval checks, explicit cutover, and
rollback without a Python installation or source checkout. The same packaged
administrator surface exposes deliberate confirmed purge; scoped AI clients
cannot invoke it. Exact downloaded-artifact acceptance receipts on both
supported OS families remain required before publication.

Exports may contain the complete vault and raw source material. Store them in
an encrypted location and test restore procedures regularly.

Opaque purge tombstones are included even when sources/audit are omitted. A
merge restore consults them before importing content and refuses to recreate a
tombstoned record or source stable ID from an older export. Do not discard the
current tombstone-bearing vault and then expect a pre-purge export to remember a
purge it predates. Existing backups remain external copies outside Core's purge
boundary and must be expired or destroyed under the operator's backup policy.

A one-click dashboard restore is not a beta requirement. The required packaged
helper may remain a documented CLI/native mode, but it must implement the safe
stopped-Core workflow above and pass exact downloaded-artifact acceptance on
both supported OS families. The dashboard does not upload or restore export
files.
