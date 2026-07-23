# All The Context

All The Context is a user-owned memory layer for AI tools. A single local
**Core** is authoritative for complete source material, provenance, review
state, history, permissions, and search. AI clients connect to that Core
through MCP; models propose memories, but they never write canonical memory
directly.

The AI client is replaceable. Your context is not.

## V1 product boundary

V1 has no hosted Edge, cloud replica, hosting provider, or paid runtime
dependency. Desktop clients connect locally. A phone or another computer must
connect directly to the same Core, and Core must be online.

Core binds only to `127.0.0.1` by default. The beta does not silently open a
LAN/public port, upload context, or pretend that plain HTTP is safe remote
access. Guided secure remote pairing is a remaining acceptance item before the
project claims one-click mobile access. Until then, the dashboard states that
boundary plainly.

The repository still contains isolated experimental Relay/Edge protocol code
from an earlier design. It is not started automatically, exposed in onboarding,
included in the V1 release gate, or supported as a V1 deployment path.

## Install

Normal users should not need Python, Docker, a terminal, a token, or a
hand-edited MCP configuration.

On Windows 11, the intended path is to download `AllTheContextSetup.exe` and
double-click it. The first-run wizard:

1. installs for the current user without administrator access;
2. creates the vault in the platform-appropriate per-user application-data
   directory;
3. stores credentials through the operating-system credential abstraction;
4. detects Codex and Claude Desktop and connects only the apps the user selects;
5. enables per-user startup when selected;
6. starts Core and opens an authenticated local dashboard; and
7. finishes without asking for timezone, hosting, provider accounts, or Edge
   setup.

The public source repository is
[Martian-ux/All-The-Context](https://github.com/Martian-ux/All-The-Context).
Community packages are unsigned: the project does not require paid Windows
publisher certificates or Apple notarization. Releases must clearly disclose
normal operating-system warnings and provide SHA-256 checksums, SBOM,
provenance, and offline Ed25519 update metadata.

Public beta downloads do not exist until the exact-commit gates in
[`docs/operations/BETA_ACCEPTANCE.md`](docs/operations/BETA_ACCEPTANCE.md)
pass.

## Implemented slice

- typed Python 3.12+ Core with SQLite migrations and FTS5;
- source records, candidates, approval/rejection, correction, supersession,
  tombstones, permissions, history, and provenance;
- idempotent/resumable model-assisted ingestion and generic JSON, JSONL, and
  Markdown archive import;
- required MCP tools over local HTTP and a lightweight STDIO forwarding adapter;
- one-click local Codex and Claude Desktop configuration;
- local review/search/backup/update dashboard;
- encrypted portable export and deliberate CLI restore;
- cross-platform Windows, macOS, and Linux CI/package paths; and
- deterministic lexical retrieval with a future embedding interface.

## Source development

The bootstrap script creates or repairs `.venv`, installs the application, and
checks compiled dependencies. Docker is not required.

PowerShell on Windows:

```text
py -3.12 scripts/bootstrap.py
.\.venv\Scripts\atc.exe init
.\.venv\Scripts\atc.exe open-dashboard
```

If `py` is unavailable but `python --version` is 3.12 or newer, run
`python scripts/bootstrap.py` instead.

macOS or Linux:

```text
python3 scripts/bootstrap.py
./.venv/bin/atc init
./.venv/bin/atc open-dashboard
```

`open-dashboard` starts Core and opens a one-use authenticated link. The bare
loopback URL intentionally has no ambient administrator access. This
terminal-oriented path is for contributors and automation, not normal users.

Install development checks with `--dev`, then run:

```text
python -m ruff check .
python -m mypy packages/allthecontext/src
python -m pytest
```

Build and smoke the native package for the current operating system with:

```text
python -m pip install -e ".[packaging]"
python scripts/build_desktop.py
python scripts/smoke_desktop_artifact.py
python scripts/smoke_packaged_first_run.py
```

See [architecture](docs/architecture/ARCHITECTURE.md),
[platform support](docs/operations/PLATFORMS.md),
[project status](docs/STATUS.md), and [security](SECURITY.md).

## Privacy boundary

The live SQLite vault is readable to the user's operating-system account and
relies on account/disk protection in V1. Portable exports are
passphrase-encrypted. Context returned to any AI client is visible to that
client/provider. All The Context does not create a second hosted context store.
