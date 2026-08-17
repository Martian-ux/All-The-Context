# All The Context

All The Context is a user-owned memory layer for AI tools. A single local
**Core** is authoritative for complete source material, current context,
provenance, history, policy decisions, permissions, and search. AI clients
connect to that Core through MCP and submit observations; Core evaluates them
automatically, and clients never write current context directly.

The AI client is replaceable. Your context is not.

Set it up once and keep using your AI tools normally. There is no routine
memory-review inbox. Explicit durable statements and corrections can update
current context immediately, duplicates reinforce existing context, and
inferences remain tentative until sufficiently supported. Automatic decisions
retain their evidence and history so they can be inspected, corrected, undone,
or deleted later.

## V1 product boundary

V1 has no hosted Edge, cloud replica, hosting provider, or paid runtime
dependency. The first usable beta is same-device only: desktop clients connect
locally while Core is online. Phone and remote-computer access are post-V1.

The public beta supports Windows 11 x86-64 and Ubuntu 24.04 LTS x86-64 with
GNOME and a working Secret Service/GNOME Keyring backend. macOS implementation
code remains in the public source tree for portability and contributor use, but
macOS is not a supported beta platform and no macOS release package is shipped.

Core binds only to `127.0.0.1` by default. The beta does not silently open a
LAN/public port, upload context, or pretend that plain HTTP is safe remote
access. The beta makes no mobile or remote-access claim.

The repository still contains experimental Relay/Edge protocol code from an
earlier design for compatibility tests and cleanup of pre-V1 installations.
The supported Core surface has no enrollment, deployment, connect, sync,
remote-client-management, mutation-trigger, or routine Relay CLI path, and it
never starts the legacy network worker. Residual cleanup is isolated under
`atc legacy-edge`; Relay/Edge is not a supported V1 deployment path.

## Install

Normal users should not need Python, Docker, a hosting account, a copied
bearer token, or a hand-edited MCP configuration. Windows uses the
one-click installer with no routine terminal use. Supported Ubuntu remains
a portable `tar.gz` requiring documented manual extract and launch.

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

On supported Ubuntu, download the x86-64 portable `tar.gz`, verify its SHA-256
and provenance, extract it locally, and launch `all-the-context`. Linux desktop
integration and updates remain manual in this beta.

The public source repository is
[Martian-ux/All-The-Context](https://github.com/Martian-ux/All-The-Context).
Community packages are unsigned: the project does not require paid Windows
publisher certificates. Releases must clearly disclose normal operating-system
warnings and provide SHA-256 checksums, SBOM, provenance, and offline Ed25519
update metadata.

Public beta downloads do not exist until the exact-commit gates in
[`docs/operations/BETA_ACCEPTANCE.md`](docs/operations/BETA_ACCEPTANCE.md)
pass.

## Implemented slice

- typed Python 3.12+ Core with SQLite migrations and FTS5;
- source records, an observation ledger, automatic context-policy outcomes,
  correction, supersession, reversible deletion/restoration, permissions,
  history, and provenance;
- idempotent/resumable model-assisted ingestion plus full local raw-history
  import and automatic memory evaluation for ChatGPT, Claude, Grok, generic
  JSON/JSONL, Markdown, and text;
- required MCP tools over local HTTP and a lightweight STDIO forwarding adapter;
- one-click local Codex and Claude Desktop configuration;
- optional local context/activity/search/backup/update dashboard;
- encrypted portable export, contributor CLI restore, and a version-matched
  packaged recovery/admin helper or console mode on every supported release
  artifact;
  exact downloaded-artifact recovery receipts remain a beta acceptance blocker;
- Windows and Linux public-beta package paths, plus retained cross-platform
  macOS source/CI code that carries no beta support claim; and
- deterministic lexical retrieval with a future embedding interface.

## Source development

The source tree retains macOS implementation paths for contributor portability
checks. They are not a supported beta installation path: no Mac package,
Mac acceptance receipt, or Mac support promise belongs to `0.1.0-beta.3`.

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

macOS (contributors only) or Linux:

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

On macOS this command exercises retained source code only; its output is not a
beta artifact and must not be uploaded to or advertised from the public
release.

See [architecture](docs/architecture/ARCHITECTURE.md),
[provider history import](docs/integrations/PROVIDER_IMPORTS.md),
[platform support](docs/operations/PLATFORMS.md),
[support](SUPPORT.md),
[known issues](docs/KNOWN_ISSUES.md),
[recovery runbook](docs/operations/RUNBOOK.md),
[project status](docs/STATUS.md),
[the roadmap to the first usable V1 beta](docs/ROADMAP_TO_V1.md), and
[security](SECURITY.md).

## Privacy boundary

The live SQLite vault is readable to the user's operating-system account and
relies on account/disk protection in V1. Portable exports are
passphrase-encrypted. Context returned to any AI client is visible to that
client/provider. All The Context does not create a second hosted context store.
