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
| Retrieval V2 reproducible baseline | `bench/retrieval_benchmark.py`, synthetic fixtures, frozen V1 JSON | benchmark metric/scale/gate tests; normal 1k/10k run | Phase 0 implemented locally; V2 ranking deferred |
| Policy before relevance ranking | `retrieval.py` `CandidateRanker` boundary | failing ranker-spy test covers denied, allowlisted-away, deleted, expired, and superseded records | Implemented |
| Separate Relay | `relay/` | API/restart/offline/demo | Implemented with SQLite |
| Signed event replication | `replication.py` | replay/tamper/gap/integration | Implemented |
| Required MCP tools | `mcp_adapter.py` | schema contract, real STDIO handshake, crash/shutdown/verified auto-restart | Implemented |
| Minimal administration UI | `apps/dashboard/` | component tests, production build, live browser smoke; responsive off-canvas inert/ARIA/focus/Escape regression | Implemented |
| Correction/deletion propagation | Core/Relay services | integration/demo | Implemented |
| Portable export/restore | `export.py`, Core dashboard export route, dashboard Backup page | encrypted round-trip; protected POST/passphrase redaction/temp cleanup/resource-bound backend tests; UI download test | Export implemented in CLI and dashboard; deliberate CLI restore implemented; dashboard restore explicitly deferred |
| Windows/macOS/Linux CI | `.github/workflows/ci.yml` | source tests plus native package/resource/first-run/MCP smoke jobs | Authored; remote run pending |
| Desktop client connections | `client_config.py`, dashboard **Connect apps** | reversible Codex and Claude Desktop config tests; UI interaction tests; generic packaged MCP handshake | Implemented locally; provider UI handshake pending |
| Cloud/mobile client connection | `edge_setup.py`, `edge_connection.py`, `relay/oauth.py`, `relay/mcp.py`, `relay/forwarding.py`, dashboard use-everywhere wizard | OAuth/PKCE/refresh/revocation; wizard retry/recovery; Core-offline Edge retrieval; outbound poll/forward/timeout/replay/restart/revocation/decommission tests | Provider-neutral path implemented; Claude mobile and ChatGPT web-only limits documented; real provider-hosted handshake and public deploy link pending |
| Online Core forwarding behind NAT | `relay/forwarding.py`, migration `0007`, Core migration `003`, `CoreRelaySync`, `EdgeSyncManager` | Core-local client approval and scope clamp; malicious Edge identity/admin forgery; concurrent/expired/cancelled/replayed requests; restart-to-unavailable; response bounds; no local-only result; raw DB/WAL/SHM sentinel absence after consume/timeout/restart | Implemented locally; production reverse proxy/host observation pending |
| One-time hosted Edge claim | `edge_claim.py`, migration `0008`, Core deployment-file/claim flow | inert preclaim Edge, public-key proof, origin binding, encrypted credential return, retry/restart, replay, wrong signer, expiry, post-ack revocation, durable-credential-absence regression | Implemented locally; Render confirmation/upload and real deployment pending |
| Internet-facing Edge hardening | `relay/app.py`, `relay/oauth.py`, `relay/service.py`, migrations `0003`-`0006` | global/chunked request bounds, registration limits, token replay/revocation, authority rebinding refusal, transactional/triggered terminal guards, interrupted-purge restart | Implemented locally; reverse-proxy controls remain deployment responsibility |
| Edge uninstall/recovery safety | `edge_connection.py`, `desktop.py`, `client_config.py` | verified terminal decommission, corrupt/missing Core DB, offline/prepared/orphan Edge, strict credential provenance, managed-backup scrub, concurrent reset/sync tests | Implemented locally; uninstall preserves recovery material rather than claiming unverified remote decommissioning |
| One-click desktop setup | `desktop.py`, `wizard.py`, `desktop_setup.py`, `browser_session.py`, `application_install.py` | unit tests, opaque browser handoff integration test, launcher/known-folder tests, mocked per-user startup and no-op installed-runtime upgrade tests, visual frozen wizard inspection, packaged first-run/MCP smoke | Implemented and exercised on Windows |
| Native packaging path | `scripts/build_desktop.py`, `docs/operations/PLATFORMS.md` | frozen Windows build and diagnostics | Windows engineering artifact exercised; signing and other OS observations pending |
| Immutable release artifacts | `build_release_assets.py`, release candidate workflow | deterministic archive/checksum/SPDX unit tests; native matrix authored | Implemented locally; hosted candidates pending |
| Authenticated OTA manifest | `release_manifest.py`, schema, public keyring | deterministic signature, tamper, revocation, URL, and downgrade tests | Contract/tooling implemented; desktop updater intentionally deferred |
| Offline promotion and channels | `docs/operations/RELEASES.md` | CLI verification path and policy tests | Authored; no production key or release created |
| Public Edge image supply chain | `edge-image.yml`, Relay Dockerfile, `render.yaml` | local Docker/config checks; digest/provenance/SBOM workflow authored | Public-ready configuration; GHCR push and hosting/provider validation pending |
| Repeatable source startup | `scripts/bootstrap.py`, CLI initialization | bootstrap unit tests and process start/stop/restart integration | Implemented |
