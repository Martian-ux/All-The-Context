# Local Core runbook

## Start and verify

Run each command as a separate line. These forms work in PowerShell, macOS
shells, and Linux shells:

```text
python -m allthecontext.cli init
python -m allthecontext.core.app
python -m allthecontext.cli status
```

Open `http://127.0.0.1:7337` for the administration dashboard. Do not expose
this listener to a LAN or the internet.

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

- If startup reports an existing owner, confirm the prior process has exited;
  do not delete a lock file while that process is alive.
- If Relay is behind, leave Core running and inspect the dashboard Relay page.
  Ordered events are safe to retry.
- If an import is interrupted, resume its ingestion session or re-import the
  same content. Content hashes and idempotency keys prevent duplication.
- Before repair or migration, create a verified export and stop Core cleanly.
