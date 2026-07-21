# All The Context

All The Context is a user-owned memory layer for AI tools. Your local **Core**
keeps the complete source material, provenance, review state, history,
permissions, and search index. An optional hosted **Relay** keeps only records
you approved as `always_available`, so permitted clients still have useful
context when your computer is offline.

The AI client is replaceable. Your context is not.

## Current release target

This repository implements the first end-to-end release-candidate slice: native
Python Core, separate Relay, typed ingestion and retrieval APIs, approval and
record history, signed event replication, a STDIO/HTTP MCP bridge, a local web
dashboard, generic archive import, encrypted portable export, and a scripted
offline-Relay demonstration. No vector database is required.

## Development quickstart

The bootstrap script creates or repairs `.venv`, installs the application, and
checks compiled dependencies before reporting success. It safely replaces a
stale environment left behind when the selected Python version changes.

PowerShell on Windows (Python launcher recommended):

```text
py -3.12 scripts/bootstrap.py
.\.venv\Scripts\atc.exe init
.\.venv\Scripts\atc.exe serve-core
```

If `py` is unavailable but `python --version` reports 3.12 or newer, use
`python scripts/bootstrap.py` for the first line.

macOS or Linux:

```text
python3 scripts/bootstrap.py
./.venv/bin/atc init
./.venv/bin/atc serve-core
```

Open `http://127.0.0.1:7337`. The initialization command prints a one-time
client credential and a ready-to-paste MCP configuration block. Keep the Core
terminal running. Use `.\.venv\Scripts\atc.exe doctor` in PowerShell or
`./.venv/bin/atc doctor` on macOS and Linux to verify the database.

To install the test and lint tools too, add `--dev` to the bootstrap command.
To deliberately rebuild the environment, add `--reset`. Do not run bootstrap
with the `.venv` interpreter itself.

Run the reproducible demonstration with:

Use `.\.venv\Scripts\python.exe scripts/demo.py` in PowerShell or
`./.venv/bin/python scripts/demo.py` on macOS and Linux.

## Startup troubleshooting

- `No module named '_cffi_backend'` means a virtual environment contains
  compiled packages from a different Python version. Rerun the bootstrap
  command; it detects and rebuilds that environment.
- If port 7337 is already in use, stop the earlier Core or start with
  `atc serve-core --port 7338`, then use that URL in the MCP configuration.
- If Core reports an existing owner, another Core process is using the same
  vault. Stop that process instead of deleting the lock file.

See [Architecture](docs/architecture/ARCHITECTURE.md),
[platform support](docs/operations/PLATFORMS.md), and
[security](SECURITY.md) before exposing a Relay. The exact locally exercised
boundary and deferred production work are recorded in
[project status](docs/STATUS.md).

## Privacy boundary

Core binds to `127.0.0.1` by default. Relay search necessarily decrypts the
small approved replica it stores, so this project does not claim zero-knowledge
hosting. Context returned to a cloud AI client is visible to that provider.
