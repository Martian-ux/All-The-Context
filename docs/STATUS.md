# Project status

## Current milestone

The target remains an unsigned `0.1.0-beta.1` community release. The V1
single-Core boundary is being reviewed on `codex/core-only-v1`; provider archive
ingestion is stacked on `codex/provider-archive-ingestion`. No beta release has
been published and no production release private key has been created.

V1 was simplified on 2026-07-22: Core is the only user-facing service. Hosted
Edge, third-party runtime deployment, offline mobile replicas, and provider
hosting setup are no longer part of the V1 product or acceptance gate. Mobile
means connecting directly to Core while Core is online.

## Implemented

- Python 3.12+ cross-platform Core with per-user SQLite/FTS5 storage,
  migrations, portable locking, clean shutdown/restart, and loopback default.
- Source, candidate, approval/rejection, correction, supersession, tombstone,
  history, permission, provenance, validity, and audit lifecycles.
- Idempotent/resumable ingestion sessions, coverage reports, model proposals,
  generic documents, and local full-history adapters for ChatGPT, Claude, and
  Grok exports. Raw archives are streamed into Core, provider messages receive
  conversation-level provenance, assistant/tool text stays inert, and failed
  extraction can retry from the preserved blob.
- Required MCP tools over HTTP and a managed STDIO adapter; one-click local
  Codex and Claude Desktop configuration bound to the exact vault.
- Bundled dashboard for import, review, search, local connections, encrypted
  backup, audit, and signed-update controls.
- Windows per-user installer/shortcut/startup/uninstall path, macOS unsigned
  app/DMG/LaunchAgent path, Linux portable package path, and three-OS CI.
- Deterministic policy-first lexical retrieval and context compilation without
  a vector dependency.
- Offline-signed Ed25519 update metadata, immutable candidate assets,
  checksums, SBOM/provenance, and Windows transactional update/rollback code.
- Frozen Windows x86_64 beta packages with an active reviewed key now select
  the canonical Pages channel automatically. The artifact transport follows
  GitHub's single pinned release-CDN redirect while retaining signed size and
  SHA-256 verification; metadata and arbitrary redirects remain refused.

## V1 Edge removal

- Edge navigation and setup were removed from the dashboard.
- First run no longer offers or opens hosted web/mobile setup.
- Dashboard status no longer calls the Edge API.
- New approvals expose only `local_only` and `core_available`.
- Core no longer starts the legacy Edge network worker.
- The GHCR Edge workflow, Render templates, and Relay container CI job were
  removed from the V1 path.
- Experimental Relay/Edge modules and explicit cleanup APIs remain dormant so
  earlier engineering state can be inspected/decommissioned without data loss.
  They are not a supported V1 feature.

## Remaining beta gates

- Complete the offline public-key ceremony and publish only the reviewed public
  key.
- Add required reviewers to the release-promotion and `github-pages`
  environments; no live channel or public release exists yet.
- Freeze the final release commit after review and repeat the full hosted
  Windows/macOS/Linux and dashboard matrix on that release identity.
- Exercise a real signed beta1-to-beta2 Windows update and rollback.
- Design and test secure direct-Core mobile pairing before claiming one-click
  mobile access. Core remains `127.0.0.1` by default in the meantime.

## Current evidence

- Full Python suite: 345 passed; four Windows-host symlink tests skipped because
  this account cannot create the required links.
- The provider importer, API, and end-to-end slice also passed 36 focused tests
  on the minimum supported Python 3.12 runtime.
- Dashboard: 19 tests passed; type check, production build, and high-severity
  dependency audit passed.
- Ruff lint and formatting, strict mypy across 53 source files, documentation-link
  checks, and the seven-step single-Core demonstration passed.
- A live isolated browser smoke imported a fictional ChatGPT export through the
  bundled dashboard, reported one conversation/two candidates, retained the raw
  source, excluded the assistant claim, moved one approved item out of review,
  emitted no browser warnings/errors, and rendered correctly at desktop and
  390-pixel mobile widths.
- The packaged dashboard contains the direct-Core mobile boundary and contains
  no Edge setup copy or `/admin/edge` request path.
- GitHub release immutability is enabled, and GitHub Pages is configured to
  deploy only from Actions. No channel artifact has been deployed.
- The Python 3.12 Windows frozen application passed resource discovery and the
  isolated first-run/install, browser handoff, MCP handshake, restart, startup,
  update-recovery, shutdown, uninstall, and cleanup smoke. The unsigned Windows
  package also passed its platform trust smoke.
- Implementation commit `05c7638` passed both its
  [push matrix](https://github.com/Martian-ux/All-The-Context/actions/runs/29969999250)
  and
  [draft-PR matrix](https://github.com/Martian-ux/All-The-Context/actions/runs/29970013608):
  Python 3.12 on Windows, Ubuntu, and macOS; native desktop/package acceptance
  on Windows, Ubuntu, macOS ARM, and macOS Intel; and dashboard checks on Node
  20 and 22.

## Explicitly unclaimed

- No public beta downloads currently exist.
- No secure automatic mobile endpoint currently exists.
- No paid/native publisher signing or Apple notarization is planned for the
  community beta.
- The live SQLite vault is not application-encrypted at rest; portable exports
  are passphrase-encrypted.
