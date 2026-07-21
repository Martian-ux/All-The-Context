# Implementation plan

1. Establish versioned contracts, trust boundaries, repository checks, and a
   cross-platform runtime foundation.
2. Implement the authoritative Core domain lifecycle, SQLite migrations, FTS5,
   permissions, audit, import, and export.
3. Implement the restricted Relay and authenticated, ordered, resumable event
   replication with tombstones.
4. Expose the nine typed MCP tools over a lightweight STDIO forwarder and
   Streamable HTTP, with one-time client configuration generation.
5. Build the local operational dashboard, onboarding, review, record, client,
   Relay, audit, and backup surfaces.
6. Prove offline Relay retrieval, Core restart, correction, deletion, proposal
   synchronization, export/restore, and client revocation in tests and demo.
7. Verify Python 3.12 lint, types, unit/integration/security tests, package build,
   links, and migration behavior; record all unexercised platform claims.
8. Replace the contributor-oriented startup path with a native first-run wizard,
   reversible Codex configuration, per-user startup, frozen platform artifacts,
   and packaged first-run/MCP/shutdown smoke coverage.
