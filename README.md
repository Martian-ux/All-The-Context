# All The Context

All The Context is a user-owned memory layer for AI tools. Your local **Core**
keeps the complete source material, provenance, review state, history,
permissions, and search index. An optional hosted **Relay** keeps only records
you approved as `always_available`, so permitted clients still have useful
context when your computer is offline.

The AI client is replaceable. Your context is not.

## Install

Normal users do not need Python, Docker, a terminal, a token, or a hand-edited
MCP configuration.

On Windows 11, download `AllTheContextSetup.exe` and double-click it. The
first-run wizard:

1. installs the application for the current user without administrator access;
2. creates the private vault in the correct per-user application-data folder;
3. stores the client credential in Windows Credential Manager when available;
4. safely adds the STDIO MCP server to the user's Codex configuration, retaining
   a timestamped backup;
5. enables per-user Core startup if selected; and
6. starts Core and opens the authenticated local dashboard.

After restarting Codex once, context retrieval and proposals happen through MCP
without repeated setup. Launching All The Context again starts Core if needed
and opens the dashboard. Core remains bound to `127.0.0.1` by default.

The locally exercised Windows engineering build is
`dist\desktop\AllTheContextSetup.exe`. Release signing is not configured yet,
so this local artifact is not presented as a production-signed download.
macOS application and Linux executable builds are authored in CI but still need
their first observed runs and release signing/package work.

## Current release target

This repository implements the first end-to-end release-candidate slice: native
Python Core, separate Relay, typed ingestion and retrieval APIs, approval and
record history, signed event replication, a STDIO/HTTP MCP bridge, a native
first-run wizard, a bundled local dashboard, generic archive import, encrypted
portable export, and a scripted offline-Relay demonstration. No vector database
is required.

## Source development

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

Open `http://127.0.0.1:7337`. This terminal-oriented path exists for contributors
and automation; it is not the intended end-user installation. Use
`.\.venv\Scripts\atc.exe doctor` in PowerShell or `./.venv/bin/atc doctor` on
macOS and Linux to verify the database.

To install the test and lint tools too, add `--dev` to the bootstrap command.
To deliberately rebuild the environment, add `--reset`. Do not run bootstrap
with the `.venv` interpreter itself.

Run the reproducible demonstration with:

Use `.\.venv\Scripts\python.exe scripts/demo.py` in PowerShell or
`./.venv/bin/python scripts/demo.py` on macOS and Linux.

Build the native artifact for the current operating system with:

```text
python -m pip install -e ".[packaging]"
python scripts/build_desktop.py
python scripts/smoke_desktop_artifact.py
python scripts/smoke_packaged_first_run.py
```

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
