# Product requirements

## Mission

All The Context lets one person keep durable preferences, facts, projects,
decisions, source material, and interaction instructions while changing AI
clients. The user configures it once; one user-owned Core then maintains context
automatically and remains the only authority.

## V1 principles

- Normal use has no memory inbox or routine review queue. The dashboard is an
  optional place to inspect provenance and activity, correct context, undo a
  change, forget something, export, or administer the vault.
- Clients and importers submit observations, never current context. Core applies
  a deterministic, versioned policy and records an `applied`, `reinforced`,
  `tentative`, or `ignored` disposition with provenance and a bounded reason.
- Explicit user statements and corrections from authenticated clients take
  effect automatically. Exact duplicates reinforce existing context. Inference
  and provider-synthesized memory remain tentative unless later evidence
  supports them. Provider adapters exclude assistant, system, tool, and
  attachment roles; generic or instruction-bearing imports remain tentative,
  and imported text is never executed as instructions.
- Current context contains only applied records. Every automatic change is
  provenance-backed, versioned, auditable, and reversible through ordinary
  deletion or restoration. Irreversible purge remains a deliberate
  administrator operation.
- No hosted Edge, cloud replica, hosting provider, paid runtime, Docker, or
  provider-specific integration package is required.
- Local desktop apps connect once through MCP and then retrieve and maintain
  context without repeated user work.
- Mobile and other computers connect directly to Core. Core must be online.
- Core remains loopback-only by default. Remote reachability is never enabled
  silently and is not claimed secure until authenticated pairing and transport
  security pass acceptance.

## Primary journeys

- Double-click one desktop artifact, initialize one protected local vault, and
  reach the dashboard without a timezone, token, command, or configuration
  file.
- Connect detected Codex and Claude Desktop installations with one selection or
  button while preserving unrelated settings. After the required client
  restart, context maintenance needs no recurring setup or approval work.
- Import complete raw ChatGPT, Claude, and Grok account-history archives
  locally, see truthful coverage and automatic applied/tentative/ignored
  counts, and recover interrupted extraction without re-upload. The import
  changes no current context until extraction completes successfully.
- Retrieve current context with deterministic permissions and validity rules.
- Optionally inspect the evidence and policy reason behind any current record or
  activity event.
- Correct, supersede, forget, restore, export, and audit context without a
  standing maintenance queue.
- Use a phone or another computer by connecting to the same online Core once a
  secure direct-Core pairing path is available.

## Success criteria

- Fresh Windows installation needs no terminal and survives restart.
- A user can connect a client once, state a durable preference, and have a later
  session retrieve it without opening the dashboard.
- An explicit correction or reversible forget request takes effect before its
  successful operation returns, while preserving history and provenance.
- Completed imports automatically evaluate eligible user-authored observations;
  failed imports, assistant output, and tool output cannot change current
  context, and imported instructions cannot execute or bypass Core policy.
- Tentative observations are not retrieved as current context. They are
  corroborated or remain unused without asking the user to clear an inbox.
- Initialization, startup, ingestion, policy evaluation, retrieval, export,
  shutdown/restart, and STDIO MCP pass on Windows, macOS, and Linux.
- Idempotent retries never create duplicate observations, decisions, batches,
  or current records.
- Unauthorized or revoked clients receive no record content.
- No normal V1 path asks for a hosting account, deploys a service, starts an
  Edge worker, or copies user context to a third-party runtime.
- Direct-Core mobile acceptance proves authentication, revocation, transport
  security, restart persistence, and safe failure while Core is offline before
  the UI calls that path complete.
