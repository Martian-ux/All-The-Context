# Product requirements

## Mission

All The Context lets one person keep durable preferences, facts, projects,
decisions, source material, and interaction instructions while changing AI
clients. Core must stay usable locally without Relay, Docker, or any particular
model provider.

## Primary journeys

- Initialize one protected local vault and receive a one-time administrator
  credential.
- Connect an MCP client once, bootstrap only the history it can genuinely see,
  and let it propose later durable changes.
- Import archives locally, review candidates in manageable batches, and inspect
  the evidence for every approved record.
- Search approved context with deterministic permissions and validity rules.
- Select a small `always_available` subset for Relay and retain it while Core is
  offline.
- Correct, supersede, or delete context and observe the restricted replica
  converge.
- Revoke a client, audit what it accessed, and export/restore the portable vault.

## Success criteria

- Initialization, ingestion, retrieval, shutdown/restart, export/restore, and
  STDIO MCP work in Windows, macOS, and Linux CI.
- Idempotent retries never create duplicate batches or canonical records.
- Relay cannot accept an unsigned, replayed, changed, or out-of-order event.
- A revoked or unauthorized client receives no record content.
- Core-offline Relay retrieval and clean reduced-context responses are proven by
  the reproducible demo.
