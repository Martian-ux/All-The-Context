# Requirements traceability

“Implemented” means exercised locally; authored CI is not called observed until
the hosted jobs pass on the exact release commit.

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Cross-platform Core | `config.py`, `lifecycle.py`, `platform_compat.py`; platform and package smoke tests | Implemented locally; exact-commit OS matrix pending |
| Correct per-user data paths | `platformdirs` configuration and setup/package tests | Implemented |
| Loopback-only default | `CoreConfig`, server CLI, dashboard copy, security tests | Implemented |
| Source/candidate/approved lifecycle | models, migrations, storage/service APIs; unit/integration/demo tests | Implemented |
| Approval before canonical memory | ingestion/service/storage and MCP contract tests | Implemented |
| Provenance, confidence, sensitivity, validity, version, supersession, hashes, client permissions | typed models/migrations/API round trips | Implemented |
| Idempotent/resumable ingestion with coverage | `ingestion.py`; retry/resume/coverage tests | Implemented |
| Generic JSON/JSONL/Markdown import | `importers.py`; importer/security tests | Implemented |
| Structured filtering and FTS5 | retrieval engine; policy-before-ranking and integration tests | Implemented |
| Future embedding boundary | retrieval interface/selector separation | Defined; embeddings intentionally absent |
| Required MCP tools | `mcp_adapter.py`; schema and real STDIO handshake/restart tests | Implemented |
| One-time local app connection | `client_config.py`, setup wizard, dashboard; Codex/Claude config tests | Implemented locally |
| Minimal administration UI | `apps/dashboard`; component, type, build, and browser-serving tests | Implemented |
| Portable export/restore | encrypted export/dashboard download and CLI restore tests | Implemented; dashboard restore deferred |
| Locking, shutdown, restart | lifecycle locks, managed adapter self-heal, packaged first-run smoke | Implemented locally |
| OS credential abstraction | credential store/keyring abstraction and platform acceptance script | Windows observed locally; macOS/Linux hosted observation pending |
| Windows/macOS/Linux CI | `.github/workflows/ci.yml` source, dashboard, and native-package matrices | Authored |
| One-click desktop packaging | Windows installer, macOS app/DMG, Linux portable archive | Implemented; final real-platform acceptance pending |
| Signed community updates | Ed25519 manifests/keyring, checksums, SBOM/provenance, Windows recovery helper | Implemented locally; real N-1 drill pending |
| No third-party V1 runtime | no Edge UI/onboarding/status call/background worker; Edge publication workflow and Render templates removed | Implemented |
| Direct-Core mobile model | integration API/dashboard/architecture state Core-online requirement | Product contract implemented; secure pairing/transport not yet implemented |
| No automatic public exposure | loopback default; dashboard warning; acceptance gate | Implemented |
| Legacy `always_available` compatibility | schema and old records retained; UI maps new review choices to `core_available` and labels old records legacy | Implemented |
| Legacy Edge cleanup without normal operation | dormant manager/admin cleanup APIs; no automatic worker | Compatibility only; not a V1 feature |

## Deferred by the V1 boundary

- hosted Edge/Relay deployment and offline mobile replicas;
- third-party hosting/provider setup;
- multi-master synchronization, CRDTs, family accounts, and multi-tenant SaaS;
- live location, heart rate, wearables, and emergency response;
- vector embeddings; and
- automatic secure remote-Core exposure until device pairing and encrypted
  transport are designed and accepted.
