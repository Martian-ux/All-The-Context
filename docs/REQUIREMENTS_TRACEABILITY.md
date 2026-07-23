# Requirements traceability

“Implemented” means exercised locally; authored CI is not called observed until
the hosted jobs pass on the exact release commit.

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Cross-platform Core | `config.py`, `lifecycle.py`, `platform_compat.py`; platform and package smoke tests | Observed on Windows/macOS/Linux at `05c7638`; final release SHA pending |
| Correct per-user data paths | `platformdirs` configuration and setup/package tests | Implemented |
| Loopback-only default | `CoreConfig`, server CLI, dashboard copy, security tests | Implemented |
| Source/candidate/approved lifecycle | models, migrations, storage/service APIs; unit/integration/demo tests | Implemented |
| Approval before canonical memory | ingestion/service/storage and MCP contract tests | Implemented |
| Provenance, confidence, sensitivity, validity, version, supersession, hashes, client permissions | typed models/migrations/API round trips | Implemented |
| Idempotent/resumable ingestion with coverage | `ingestion.py`; retry/resume/coverage tests | Implemented |
| Generic JSON/JSONL/Markdown import | `importers.py`; importer/security tests | Implemented |
| Full local ChatGPT/Claude/Grok history ingestion | `provider_ingestion.py`, streaming ZIP/JSON adapters, dashboard provider flow, raw-source recovery; provider unit/integration/security/UI tests | Implemented locally; real personal export acceptance pending |
| Structured filtering and FTS5 | retrieval engine; policy-before-ranking and integration tests | Implemented |
| Future embedding boundary | shadow-retriever contract plus disabled, rebuild-only 384d exact-scan experiment outside package discovery | Defined; no production embedding dependency or authority |
| Required MCP tools | `mcp_adapter.py`; schema and real STDIO handshake/restart tests | Implemented |
| One-time local app connection | `client_config.py`, setup wizard, dashboard; Codex/Claude config tests | Implemented locally |
| Minimal administration UI | `apps/dashboard`; component, type, build, and browser-serving tests | Implemented |
| Portable export/restore | encrypted export/dashboard download and CLI restore tests | Implemented; dashboard restore deferred |
| Locking, shutdown, restart | lifecycle locks, managed adapter self-heal, packaged first-run smoke | Implemented locally |
| OS credential abstraction | credential store/keyring abstraction and platform acceptance script | Windows local and Windows/macOS/Linux hosted acceptance observed at `05c7638` |
| Windows/macOS/Linux CI | `.github/workflows/ci.yml` source, dashboard, and native-package matrices | Release matrices observed at `05c7638`; Retrieval V3 push matrix observed green at `67dd11c` |
| One-click desktop packaging | Windows installer, macOS app/DMG, Linux portable archive | Platform acceptance observed at `05c7638`; final release SHA pending |
| Signed community updates | Ed25519 manifests/keyring with active beta key `release-2026-a`, canonical packaged beta endpoint, pinned GitHub release-asset redirect, checksums, SBOM/provenance, Windows recovery helper | Client, public trust root, and release mechanics implemented locally; recovery backups, channel deployment, and real N-1 drill pending |
| No third-party V1 runtime | no Edge UI/onboarding/status call/background worker; Edge publication workflow and Render templates removed | Implemented |
| Direct-Core mobile model | integration API/dashboard/architecture state Core-online requirement | Product contract implemented; secure pairing/transport not yet implemented |
| No automatic public exposure | loopback default; dashboard warning; acceptance gate | Implemented |
| Legacy `always_available` compatibility | schema and old records retained; UI maps new review choices to `core_available` and labels old records legacy | Implemented |
| Legacy Edge cleanup without normal operation | dormant manager/admin cleanup APIs; no automatic worker | Compatibility only; not a V1 feature |
| Frozen Retrieval V2 comparator | `retrieval_contracts.py`, pinned fixture hashes/ranking fingerprints, foundation harness | Implemented; comparator identity `70a4808` |
| Policy before time/relevance | authorization-only selector, temporal eligibility IDs, candidate-scoped FTS, boundary tests | Implemented; zero forbidden results in bounded gate |
| Current and `as_of` retrieval | UTC interval sidecar, request/MCP/CLI fields, DST/offset/restart tests | Implemented locally; Python 3.12 three-OS suite observed green at `67dd11c` |
| Deletion/purge resurrection barrier | canonical terminal facts, purge tombstones, stale-sidecar recovery, pre-removal export restore test | Zero resurrection in local bounded gate; three-OS suite observed green at `67dd11c` |
| Weighted bounded FTS5 | `lexical_v3.py`; weighted columns, exact/OR/prefix caps, Unicode/case/tokenizer and secure-delete tests | Implemented locally |
| Task admissibility | deterministic numeric factor gate after hard policy/time, fail-open sparse evidence, shadow-only learned interface | Implemented locally; bounded precision improves without exact Recall@5 loss |
| Safe retrieval diagnostics | closed reason codes and numeric/boolean aggregates; admin-only returned-ID explanations | Implemented; content/unauthorized-ID exclusion tests |
| Retrieval V3 benchmark gate | foundation fixtures plus integrated 1k/10k quality, latency, storage, migration/restart/restore checks | Full local Windows gate passed; source/tests/packages observed on three OSes at `67dd11c` |
| Set-level marginal context selection | `set_selection.py`, `ContextCompiler` wiring, compatibility/diversity/conflict/support/mandatory/budget fixtures | Implemented locally; 11/11 standalone gates and combined semantic coverage `1.0` |
| Optional local dense shadow | disabled in-memory 384d exact-scan experiment, bounded tests, authorization-first filtering | Implemented as research only; 10k p95 `400.294955 ms` misses `150 ms`; real model/semantics unexercised |
| Source-evidence retrieval research | sanitized imported-chat fixtures; lexical passage and deterministic token-MaxSim benchmark/report | Implemented as research only; 64/256 recall and coverage `1.0`, diverse redundancy zero; neural path unexercised |

## Deferred by the V1 boundary

- hosted Edge/Relay deployment and offline mobile replicas;
- third-party hosting/provider setup;
- multi-master synchronization, CRDTs, family accounts, and multi-tenant SaaS;
- live location, heart rate, wearables, and emergency response;
- production vector embeddings; and
- automatic secure remote-Core exposure until device pairing and encrypted
  transport are designed and accepted.
