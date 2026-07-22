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
python -m pytest tests/integration/test_relay_core_outbox.py
python -m pytest tests/unit/test_export.py
```

These commands exercise Core lifecycle/storage, ingestion/retrieval, replication,
and export behavior. CI runs the complete suite independently on Windows,
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
- If Edge is behind, leave Core running and inspect the dashboard Edge page.
  Ordered events are safe to retry.
- If an import is interrupted, resume its ingestion session or re-import the
  same content. Content hashes and idempotency keys prevent duplication.
- Before repair or migration, create a verified export and stop Core cleanly.
