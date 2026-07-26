# Product requirements

## Mission

All The Context lets one person keep durable preferences, facts, projects,
decisions, source material, and interaction instructions while changing AI
clients. The user configures it once; one user-owned Core then maintains context
automatically and remains the only authority.

## V1 principles

The active V1 release is the first usable public beta, `0.1.0-beta.1`. It is a
same-device desktop product; stable `1.0.0`, phone access, and remote-computer
access are post-V1.

- Normal use has no memory inbox or routine review queue. The dashboard is an
  optional place to inspect provenance and activity, correct context, undo a
  change, forget something, export, or administer the vault.
- Clients and importers submit observations, never current context. Core applies
  a deterministic, versioned policy and records an `applied`, `reinforced`,
  `tentative`, or `ignored` disposition with provenance and a bounded reason.
- Explicit user statements and corrections attested by an ATC-configured
  same-device client principal with the explicit-statement witness grant take
  effect automatically. This is an accepted local trust grant, not
  cryptographic proof of human authorship; authentication alone is
  insufficient. Exact duplicates reinforce existing context. Unattested
  inference and provider-synthesized memory remain tentative unless later
  evidence supports them. Provider adapters exclude assistant, system, tool,
  and attachment roles; generic or instruction-bearing imports remain
  tentative, and imported text is never executed as instructions.
- Current context contains only applied records. Every automatic change is
  provenance-backed, versioned, auditable, and reversible through ordinary
  deletion or restoration. Irreversible purge remains a deliberate
  administrator operation.
- Refused secret-like direct content leaves neither its payload nor an unkeyed
  content fingerprint/guessing verifier in durable state. Existing-data repair
  covers live SQLite/WAL/freelist and new export bytes; historical external
  backups outside Core require explicit retirement guidance.
- No hosted Edge, cloud replica, hosting provider, paid runtime, Docker, or
  provider-specific integration package is required.
- Local desktop apps connect once through MCP and then retrieve and maintain
  context without repeated user work.
- A future mobile or remote-computer product would connect directly to Core
  while Core is online; it is not a V1 beta journey.
- Core remains loopback-only by default. Remote reachability is never enabled
  silently and is not claimed secure until authenticated pairing and transport
  security pass acceptance.

## Primary journeys

- Install the documented artifact on Windows, macOS, or Linux, initialize one
  protected local vault, and reach the dashboard without a timezone, token,
  command, or configuration file.
- Connect detected Codex and Claude Desktop installations with one selection or
  button while preserving unrelated settings. After the required client
  restart, context maintenance needs no recurring setup or approval work.
- Import complete raw ChatGPT, Claude, and Grok account-history archives
  locally, see truthful coverage and automatic applied/tentative/ignored
  counts, and recover interrupted extraction without re-upload. The import
  changes no current context until extraction completes successfully.
- On every frozen OS/architecture target, accept a deterministic physically
  allocated/non-sparse raw source of exactly `2,000,000,000` bytes within
  the frozen 4-core/8-GiB/SSD/16-GiB-free profile: at most 1 GiB peak
  Core/import-worker RSS, four-times-source-plus-1-GiB incremental storage,
  5-second first/heartbeat progress, 5-second cancel acknowledgement,
  30-second safe quiescence, and 60 minutes each for import, source-inclusive
  export, and isolated restore. Preserve retry, interruption, source-integrity,
  and atomic-publication guarantees; reject a `2,000,000,001`-byte source
  deterministically.
- Retrieve current context with deterministic permissions and validity rules.
- Optionally inspect the evidence and policy reason behind any current record or
  activity event.
- Correct, supersede, forget, restore, export, and audit context without a
  standing maintenance queue.
- Create an encrypted backup and use the version-matched packaged
  recovery/admin helper or native mode for stopped-Core restore and deliberate
  purge without installing Python or checking out source.

## Success criteria

- Fresh Windows 11 x86-64, macOS 26 ARM64/x86-64, and Ubuntu 24.04 LTS x86-64
  GNOME/Secret-Service installations need no developer tooling or routine
  terminal use and survive restart; exact Windows/macOS builds and client
  versions are frozen in the candidate receipt.
- A user can connect a client once, state a durable preference, and have a later
  session retrieve it without opening the dashboard.
- An explicit correction or reversible forget request takes effect before its
  successful operation returns, while preserving history and provenance.
- Completed imports automatically evaluate eligible user-authored observations;
  failed imports, assistant output, and tool output cannot change current
  context, and imported instructions cannot execute or bypass Core policy.
- Current real ChatGPT, Claude, and Grok exports all pass privacy-safe
  exact-candidate acceptance with a nonempty frozen canary shape set and
  complete recognized/excluded/skipped/unavailable/failed reconciliation; none
  of the three is optional for beta1.
- Tentative observations are not retrieved as current context. They are
  corroborated or remain unused without asking the user to clear an inbox.
- Initialization, startup, ingestion, policy evaluation, retrieval, export,
  shutdown/restart, and STDIO MCP pass on Windows, macOS, and Linux.
- Idempotent retries never create duplicate observations, decisions, batches,
  or current records.
- Unauthorized or revoked clients receive no record content.
- Packaged backup/restore and deliberate purge pass on Windows, macOS, and
  Linux; purge remains administrator-only and cannot resurrect after restart
  or restore.
- No normal V1 path asks for a hosting account, deploys a service, starts an
  Edge worker, or copies user context to a third-party runtime.
- V1 beta UI and documentation make no phone or remote-computer claim. Any
  later direct-Core mobile path requires authentication, revocation, transport
  security, restart persistence, and safe offline failure before it is called
  complete.
