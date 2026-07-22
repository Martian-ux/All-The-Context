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
| Required MCP tools | `mcp_adapter.py` | schema contract, real STDIO handshake, crash/shutdown/verified auto-restart | Implemented |
| Minimal administration UI | `apps/dashboard/` | component tests, production build, live browser smoke; responsive off-canvas inert/ARIA/focus/Escape regression | Implemented |
| Correction/deletion propagation | Core/Relay services | integration/demo | Implemented |
| Portable export/restore | `export.py`, Core dashboard export route, dashboard Backup page | encrypted round-trip; protected POST/passphrase redaction/temp cleanup/resource-bound backend tests; UI download test | Export implemented in CLI and dashboard; deliberate CLI restore implemented; dashboard restore explicitly deferred |
| Windows/macOS/Linux CI | `.github/workflows/ci.yml` | source tests plus native package/resource/first-run/MCP smoke jobs | Authored; remote run pending |
| Desktop client connections | `client_config.py`, dashboard **Connect apps** | reversible Codex and Claude Desktop config tests; UI interaction tests; generic packaged MCP handshake | Implemented locally; provider UI handshake pending |
| Cloud/mobile client connection | `edge_setup.py`, `edge_connection.py`, `relay/oauth.py`, `relay/mcp.py`, dashboard Edge setup | OAuth/PKCE/refresh/revocation tests; owner recovery; prepare/pair/sync/provider UI test; Core-offline Edge retrieval | Provider-neutral path implemented; current Claude and ChatGPT web-to-mobile setup documented; real provider-hosted handshake and public deploy link pending |
| Internet-facing Edge hardening | `relay/app.py`, `relay/oauth.py`, `relay/service.py`, migrations `0003`-`0006` | global/chunked request bounds, registration limits, token replay/revocation, authority rebinding refusal, transactional/triggered terminal guards, interrupted-purge restart | Implemented locally; reverse-proxy controls remain deployment responsibility |
| Edge uninstall/recovery safety | `edge_connection.py`, `desktop.py`, `client_config.py` | verified terminal decommission, corrupt/missing Core DB, offline/prepared/orphan Edge, strict credential provenance, managed-backup scrub, concurrent reset/sync tests | Implemented locally; uninstall preserves recovery material rather than claiming unverified remote decommissioning |
| One-click desktop setup | `desktop.py`, `wizard.py`, `desktop_setup.py`, `browser_session.py`, `application_install.py` | unit tests, opaque browser handoff integration test, launcher/known-folder tests, mocked per-user startup and no-op installed-runtime upgrade tests, visual frozen wizard inspection, packaged first-run/MCP smoke | Implemented and exercised on Windows |
| Native packaging path | `scripts/build_desktop.py`, `docs/operations/PLATFORMS.md` | frozen Windows build and diagnostics | Windows engineering artifact exercised; signing and other OS observations pending |
| Repeatable source startup | `scripts/bootstrap.py`, CLI initialization | bootstrap unit tests and process start/stop/restart integration | Implemented |
