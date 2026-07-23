# Product requirements

## Mission

All The Context lets one person keep durable preferences, facts, projects,
decisions, source material, and interaction instructions while changing AI
clients. One user-owned Core remains the only authority.

## V1 principles

- No hosted Edge, cloud replica, hosting provider, paid runtime, Docker, or
  provider-specific integration package is required.
- Models submit candidates; approval policy alone creates canonical memory.
- Local desktop apps connect once through MCP and then retrieve/propose context
  without repeated user work.
- Mobile and other computers connect directly to Core. Core must be online.
- Core remains loopback-only by default. Remote reachability is never enabled
  silently and is not claimed secure until authenticated pairing and transport
  security pass acceptance.

## Primary journeys

- Double-click one desktop artifact, initialize one protected local vault, and
  reach the dashboard without a timezone, token, command, or configuration
  file.
- Connect detected Codex and Claude Desktop installations with one selection or
  button while preserving unrelated settings.
- Import archives locally, review candidates, and inspect evidence for every
  approved record.
- Search approved context with deterministic permissions and validity rules.
- Correct, supersede, delete, export, restore, and audit context.
- Use a phone or another computer by connecting to the same online Core once a
  secure direct-Core pairing path is available.

## Success criteria

- Fresh Windows installation needs no terminal and survives restart.
- Initialization, startup, ingestion, retrieval, export, shutdown/restart, and
  STDIO MCP pass on Windows, macOS, and Linux.
- Idempotent retries never create duplicate batches or canonical records.
- Unauthorized or revoked clients receive no record content.
- No normal V1 path asks for a hosting account, deploys a service, starts an
  Edge worker, or copies user context to a third-party runtime.
- Direct-Core mobile acceptance proves authentication, revocation, transport
  security, restart persistence, and safe failure while Core is offline before
  the UI calls that path complete.
