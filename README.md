# All The Context

All The Context is a user-owned memory layer for AI tools. Your local **Core**
keeps the complete source material, provenance, review state, history,
permissions, and search index. An optional hosted **Edge** keeps only records
you approved as `always_available`, so permitted clients still have useful
context when your computer is offline.

The AI client is replaceable. Your context is not.

The current integration target is the unsigned community beta
`0.1.0-beta.1`. Public beta downloads are not available until the exact-SHA
cross-platform, supply-chain, hosted Edge, privacy, and human-approval gates in
[`docs/operations/BETA_ACCEPTANCE.md`](docs/operations/BETA_ACCEPTANCE.md) pass.

## Install

Normal users do not need Python, Docker, a terminal, a token, or a hand-edited
MCP configuration.

On Windows 11, download `AllTheContextSetup.exe` and double-click it. The
first-run wizard:

1. installs the application for the current user without administrator access;
2. creates the private vault in the correct per-user application-data folder;
3. stores credentials in Windows Credential Manager when available;
4. detects installed AI desktop apps and creates separate least-privilege
   connections only for the apps the user selects, preserving existing
   configuration with timestamped backups;
5. establishes the dashboard automatically with a one-use local link and an
   opaque, tab-scoped session—no administrator token is placed in a cookie,
   URL, or browser storage;
6. enables per-user Core startup if selected;
7. starts Core and opens the authenticated local dashboard; and
8. by default, continues directly to guided Edge setup for supported web and
   mobile access. The wizard discloses the external hosting cost and provider
   limitations before this optional step.

After restarting a connected desktop client once, context retrieval and
proposals happen through MCP without repeated setup. The dashboard's **Connect
apps** page distinguishes installed apps from merely supported apps, offers the
official download page when an app is absent, and can connect or repair detected
Codex and Claude Desktop installations with one button. Every managed MCP entry
is bound to the exact vault it belongs to, so an isolated or non-default Core
can restart without being mistaken for another installation. Launching All The
Context again starts Core if needed and opens an
authenticated dashboard automatically. Core remains bound to `127.0.0.1` by
default. Running a newer installer upgrades the per-user app and opens the
existing vault directly; it does not send an established user through setup
again.

ChatGPT or Claude on the web and mobile cannot reach a private `127.0.0.1`
service. The dashboard can now prepare, cryptographically pair, synchronize,
manage, and decommission a personal hosted Edge from the same **Connect apps**
page. The user deploys the included Edge blueprint under their own hosting
account, enters the resulting HTTPS address once, and then adds the displayed
MCP address in an eligible AI provider. Claude custom connectors and ChatGPT
developer-mode apps have different surfaces: Claude connectors added on
web/Desktop can be used on mobile, while ChatGPT developer-mode MCP is currently
web-only; workspace policy may gate setup. These labels were checked on
2026-07-22 against the official
[Claude connector guide](https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp-servers)
and [ChatGPT developer-mode guide](https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt-beta).

The public source repository is
[Martian-ux/All-The-Context](https://github.com/Martian-ux/All-The-Context).
The engineering build still shows the one-click Render deployment link as
unavailable until a reviewed Edge image and `ATC_EDGE_DEPLOY_URL` are published.
Based on Render's published
[Starter pricing](https://render.com/articles/render-vs-railway) and
[persistent-disk pricing](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses),
Starter plus a 1 GB disk is estimated at $7.25/month before bandwidth; an
external hosting account and payment cannot be hidden by the local installer.

The locally exercised Windows engineering build is
`dist\desktop\AllTheContextSetup.exe`. This project will not require paid
Windows publisher certificates or Apple notarization for community releases.
Downloads are explicitly labeled unsigned and use GitHub Releases, SHA-256,
SBOM/provenance, and the application's offline Ed25519 release manifest for
integrity. Windows and macOS may therefore show their normal unknown-publisher
warning on first install. macOS and Linux artifacts still need their first
observed native runs and packaging work.

## Current release target

This repository implements the first end-to-end release-candidate slice: native
Python Core, separate Edge, typed ingestion and retrieval APIs, approval and
record history, signed event replication, a STDIO/HTTP MCP bridge, a native
first-run wizard, a bundled local dashboard, generic archive import, encrypted
portable export, and a scripted offline-Edge demonstration. No vector database
is required.

## Source development

The bootstrap script creates or repairs `.venv`, installs the application, and
checks compiled dependencies before reporting success. It safely replaces a
stale environment left behind when the selected Python version changes.

PowerShell on Windows (Python launcher recommended):

```text
py -3.12 scripts/bootstrap.py
.\.venv\Scripts\atc.exe init
.\.venv\Scripts\atc.exe open-dashboard
```

If `py` is unavailable but `python --version` reports 3.12 or newer, use
`python scripts/bootstrap.py` for the first line.

macOS or Linux:

```text
python3 scripts/bootstrap.py
./.venv/bin/atc init
./.venv/bin/atc open-dashboard
```

`open-dashboard` starts Core and opens a one-use authenticated link. Do not open
the bare loopback URL: it intentionally has no ambient administrator access.
This terminal-oriented path exists for contributors and automation; it is not
the intended end-user installation. Use
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
[security](SECURITY.md) before exposing an Edge. The exact locally exercised
boundary and deferred production work are recorded in
[project status](docs/STATUS.md).

## Privacy boundary

Core binds to `127.0.0.1` by default. Edge search necessarily decrypts the
small approved replica it stores, so this project does not claim zero-knowledge
hosting. Context returned to a cloud AI client is visible to that provider.
