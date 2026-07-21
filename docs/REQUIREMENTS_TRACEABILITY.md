# Requirements traceability

This table is updated from executable evidence; "planned" is not a completion
claim. "Implemented" means the behavior passed locally on Windows 11/Python
3.12. The authored cross-platform matrix is not marked as observed CI evidence.

| Requirement | Implementation | Tests | Status |
|---|---|---|---|
| Cross-platform Core | `config.py`, `lifecycle.py`, Core CLI | platform abstraction/smoke tests; CI authored | Implemented locally; OS matrix pending |
| Source/candidate/record lifecycle | `storage.py`, `core/service.py` | unit/integration/demo | Implemented |
| Ingestion sessions and idempotency | `ingestion.py` | ingestion/unit/demo | Implemented |
| Generic import | `importers.py` | importer/security/demo | Implemented |
| Structured and FTS retrieval | `retrieval.py` | unit/integration/security | Implemented |
| Separate Relay | `relay/` | API/restart/offline/demo | Implemented with SQLite |
| Signed event replication | `replication.py` | replay/tamper/gap/integration | Implemented |
| Required MCP tools | `mcp_adapter.py` | schema contract plus real STDIO handshake | Implemented |
| Minimal administration UI | `apps/dashboard/` | component tests, production build, live browser smoke | Implemented |
| Correction/deletion propagation | Core/Relay services | integration/demo | Implemented |
| Portable export/restore | `export.py` | encrypted round-trip/security/demo | Implemented |
| Windows/macOS/Linux CI | `.github/workflows/ci.yml` | workflow syntax and local equivalents | Authored; remote run pending |
| Provider integrations | `integrations/` | config/documentation checks | Examples only; provider handshakes pending |
| Native packaging path | `docs/operations/PLATFORMS.md` | Python wheel/sdist build | Path documented; native packages deferred |
| Repeatable source startup | `scripts/bootstrap.py`, CLI initialization | bootstrap unit tests and process start/stop/restart integration | Implemented |
