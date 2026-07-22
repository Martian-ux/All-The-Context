# Product requirements

## Mission

All The Context lets one person keep durable preferences, facts, projects,
decisions, source material, and interaction instructions while changing AI
clients. Core must stay usable locally without Relay, Docker, or any particular
model provider.

## Primary journeys

- Double-click one desktop artifact, initialize one protected local vault, and
  let setup secure the administrator credential without exposing it to the user
  or asking for a timezone.
- Open the local dashboard already connected to Core, with no token, terminal,
  or configuration-file step.
- Connect Codex and Claude Desktop automatically during first
  run or with one button in the dashboard, bootstrap only the history each can
  genuinely see, and let it propose later durable changes.
- Import archives locally, review candidates in manageable batches, and inspect
  the evidence for every approved record.
- Search approved context with deterministic permissions and validity rules.
- Select a small `always_available` subset for Relay and retain it while Core is
  offline.
- Prepare, deploy, cryptographically pair, and manage a user-owned hosted Edge
  from the same computer through the installer's guided continuation, then
  connect eligible web/mobile AI clients through its stable OAuth-protected MCP
  address.
- Correct, supersede, or delete context and observe the restricted replica
  converge.
- Revoke a client, audit what it accessed, and export/restore the portable vault.

## Success criteria

- A normal desktop user reaches an authenticated dashboard and connected local
  AI clients without handling a timezone, credential, command, or config file.
- Initialization, ingestion, retrieval, shutdown/restart, export/restore, and
  STDIO MCP work in Windows, macOS, and Linux CI.
- Idempotent retries never create duplicate batches or canonical records.
- Relay cannot accept an unsigned, replayed, changed, or out-of-order event.
- A persistent Edge database cannot be rebound to a different vault or
  enrollment bundle, and terminal decommission rejects old credentials while
  removing active rows from every historical vault stream and compacting the
  live SQLite/WAL storage. Provider disks and backups still follow the host's
  deletion and retention controls.
- A revoked or unauthorized client receives no record content.
- Core-offline Relay retrieval and clean reduced-context responses are proven by
  the reproducible demo.
