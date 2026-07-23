# Local Core runbook

## Start and verify

Run each command as a separate line after completing the README bootstrap.
PowerShell:

```text
.\.venv\Scripts\atc.exe init
.\.venv\Scripts\atc.exe open-dashboard
```

macOS and Linux:

```text
./.venv/bin/atc init
./.venv/bin/atc open-dashboard
```

The command opens an authenticated one-use dashboard link. A bare
`http://127.0.0.1:7337` tab is deliberately unauthenticated. Do not expose this
listener to a LAN or the internet. Run `atc status` or `atc doctor` from a second
terminal using the same platform-specific executable path.

## Cross-platform smoke sequence

```text
python -m pytest tests/unit/test_core_storage.py tests/unit/test_core_importers.py
python -m pytest tests/integration/test_core_api.py
python -m pytest tests/unit/test_export.py
```

These commands exercise Core lifecycle/storage, ingestion/retrieval, and export
behavior. CI runs the complete suite independently on Windows,
macOS, and Linux.

## Locking, shutdown, and restart

Only one Core process may own a vault. Locking uses a portable file lock rather
than POSIX advisory locks. Stop Core with the foreground process interrupt or
the CLI stop command when a packaged service adapter is installed. Core must
finish the active SQLite transaction, close HTTP listeners, and release its
lock before process exit. A restart never copies or replaces a live database.

## Troubleshooting

- If startup raises `No module named '_cffi_backend'`, the virtual environment
  contains compiled dependencies for a different Python version. Run the
  platform bootstrap command from the README; it will rebuild the environment.
- If startup reports an existing owner, confirm the prior process has exited;
  do not delete a lock file while that process is alive.
- If an import is interrupted, open **Sources** and choose **Retry extraction**
  on the failed source. Core reuses the preserved raw blob and versioned
  session/batch idempotency keys; no second upload or duplicate observation or
  decision is required. A failed or unfinished session has not changed current
  context.
- Before repair or migration, create a verified export and stop Core cleanly.

## Optional activity/integrity inspection and secure purge

Normal operation does not require clearing a review queue. Use the dashboard
Activity view, the administrator observations endpoint, or
`atc integrity-groups --status open` only when diagnosing an automatic
decision, legacy conflict, or suspected policy defect. Activity passively
summarizes audit action, actor, target, outcome, and time. The observations
endpoint adds disposition, affected record, decision reason/time/version,
source, and evidence. The bounded integrity command lists group type,
normalized slot, and member record IDs; Context provides record provenance,
history, correction, restoration, and delete actions. These are optional
inspection tools, not unresolved work.

Ordinary `atc delete RECORD --reason ...` preserves history and is reversible.
Use dashboard **Undo/Restore** or the administrator record-restore endpoint to
return the latest deleted state or a chosen historical version to current
context. Explicit corrections preserve the earlier version and should be
preferred to purge when the content may be useful for audit or undo.
For irreversible Core purge, first stop other Core processes and close long
read transactions, then run exactly one of:

```text
atc purge record RECORD_ID --confirmation "PURGE RECORD RECORD_ID"
atc purge source SOURCE_ID --confirmation "PURGE SOURCE SOURCE_ID"
```

Logical purge commits before compaction. It uses foreign keys, SQLite
`secure_delete`, in-memory SQLite temp storage, a truncated WAL checkpoint, a
free-space preflight, and `VACUUM`. If the database is locked, the process is
interrupted, or disk is insufficient, content stays logically absent and the
job remains `compaction_pending`; inspect the admin purge-jobs API and run
`atc purge-resume`. Startup attempts one bounded pending job. Never replace the
database with a pre-purge copy during recovery.

Purge is not a promise of erasure from SSD remanence, filesystem snapshots,
external backups, already-downloaded exports, remote copies not yet integrated,
or files the user copied elsewhere. Rotate or delete those systems separately.
