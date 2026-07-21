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

The commands below work in PowerShell, zsh, Bash, and other ordinary shells:

```text
python -m venv .venv
python -m pip install -e ".[dev]"
atc init
atc serve-core
```

Open `http://127.0.0.1:7337`. The initialization command prints a one-time
client credential and ready-to-paste MCP configuration. Run `atc doctor` to
verify the database, Core, and MCP adapter.

Run the reproducible demonstration with:

```text
python scripts/demo.py
```

See [Architecture](docs/architecture/ARCHITECTURE.md),
[platform support](docs/operations/PLATFORMS.md), and
[security](SECURITY.md) before exposing a Relay. The exact locally exercised
boundary and deferred production work are recorded in
[project status](docs/STATUS.md).

## Privacy boundary

Core binds to `127.0.0.1` by default. Relay search necessarily decrypts the
small approved replica it stores, so this project does not claim zero-knowledge
hosting. Context returned to a cloud AI client is visible to that provider.
