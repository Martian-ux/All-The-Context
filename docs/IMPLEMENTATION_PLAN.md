# Implementation plan

## Completed vertical slice

1. Define Core-authoritative architecture, schemas, ingestion/MCP protocols,
   threat model, and non-goals.
2. Implement the typed cross-platform Python Core with SQLite migrations,
   structured policy, and FTS retrieval.
3. Implement source, candidate, approval, correction, supersession, tombstone,
   permission, history, and audit lifecycles.
4. Implement idempotent model-assisted ingestion, generic archive import, and
   local ChatGPT/Claude/Grok history adapters with raw-source recovery.
5. Implement required MCP tools, HTTP transport, and STDIO adapter.
6. Implement desktop setup, local client connections, dashboard, encrypted
   export/restore path, updates, and cross-platform packages/tests.

## Current beta work

1. Keep V1 single-Core: remove hosted Edge from product/release surfaces and
   prevent its dormant worker from starting.
2. Rebuild and smoke Windows/macOS/Linux artifacts on one frozen commit.
3. Complete the offline public-key ceremony and immutable beta release flow.
4. Exercise a real signed beta1-to-beta2 Windows update and rollback.
5. Design secure direct-Core mobile pairing and encrypted transport; do not
   expose Core automatically or claim mobile completion before acceptance.

Embeddings and any new synchronization service remain deferred until the
install, update, and direct-Core security boundaries are accepted.
