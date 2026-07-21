# Architecture decisions

## ADR-001: Core is the sole authority

Relay stores a projection and proposal queue only. Application events, not
database files, cross the boundary.

## ADR-002: SQLite-first storage

Core uses SQLite/FTS5. The first Relay slice also supports SQLite for a complete
local/integration path; its storage boundary permits PostgreSQL in hosted
deployment without changing event contracts.

## ADR-003: Review-first approval with policy hook

Extracted/model-inferred candidates require review by default. The schema and
client scope model reserve deterministic auto-approval for explicit low-risk
statements, but it is off until enabled by the user.

## ADR-004: One-time MCP forwarding setup

The adapter is transport glue, not an authority. Generated config pins a target,
client ID, and credential source so models can retrieve and propose memory
without repeated setup.

## ADR-005: Official MCP v1 during v2 transition

As of 2026-07-21 the official Python SDK documents v1 as stable and v2 as alpha,
with v2 stable targeted later in July. The dependency is constrained to
`mcp>=1.27,<2` and isolated in `mcp_adapter.py` for a controlled v2 migration.

## ADR-006: Bundle the dashboard with Core

The React dashboard is compiled to static assets included in the Python
package and served by the loopback Core. This avoids a second process after
installation while preserving an independently testable frontend source tree.

## ADR-007: Encrypt portable exports, not the live V1 database

Portable `.atc` exports use a passphrase-derived key and AES-256-GCM with
authenticated manifests and content hashes. The first live SQLite vault relies
on OS account and disk protection; application-level at-rest encryption needs
a separate key-recovery and migration design and is not implied here.

## ADR-008: Probe before reusing a source virtual environment

The repository bootstrap uses only platform-neutral Python and never requires
shell activation. It compares the environment's recorded Python major/minor
version with the invoking runtime and imports compiled dependencies before
reuse. A missing, cross-version, or internally inconsistent `.venv` is cleared
and rebuilt; a healthy environment is updated in place. This prevents compiled
extensions from an earlier Python installation surviving a later `venv` run.
