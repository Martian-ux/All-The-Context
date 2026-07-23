# Requirements traceability

"Implemented" means exercised locally; authored CI is not called observed until
the hosted jobs pass on the exact release commit. Rows marked "current worktree"
describe visible integrated code that has not yet passed the final required
suite. Pre-ADR-039 approval evidence does not satisfy automatic-policy rows.

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Cross-platform Core | `config.py`, `lifecycle.py`, `platform_compat.py`; platform and package smoke tests | Observed on Windows/macOS/Linux at `05c7638`; final release SHA pending |
| Correct per-user data paths | `platformdirs` configuration and setup/package tests | Implemented |
| Loopback-only default | `CoreConfig`, server CLI, dashboard copy, security tests | Implemented |
| Research supplier provenance and isolation | `research/competitor-intake/memory-systems-intake.v1.json`, decision record, ignored `research/vendor-cache`, manifest/packaging guardrail test | Intake evidence only; no third-party code cloned, installed, executed, imported, copied, or packaged |
| Observation/disposition/current-context lifecycle | `models.py`, `memory_policy.py`, migration 005, storage transactions, evidence links | Implemented; full local suite and focused policy/storage/API regressions pass |
| One-time setup with no routine memory queue | automatic MCP submission plus dashboard Context default/Review removal | Implemented in current worktree; end-to-end fresh-user proof pending |
| Core-only `automatic-v1` authority | origin assigned by Core; applied/reinforced/tentative/ignored decisions; Relay staged queue receipts | Implemented; adversarial ACL/Relay integration tests pass locally |
| Explicit user observation becomes current automatically | `add_candidate` policy transaction and MCP response contract | Implemented; approval-free observe-to-later-retrieve E2E passes locally |
| Tentative/ignored/staged isolation | current-record-only retrieval, staged ingestion, policy tests | Implemented; restart, pre-v5 restore, FTS rebuild, and retrieval-isolation tests pass locally |
| Configurable tentative retention/decay | future versioned policy, deterministic replay, and noncurrent-isolation requirements | Deferred; not implemented or claimed by `automatic-v1` |
| Duplicate reinforcement and deterministic slot conflict | observation links, normalized value matching, explicitness then `observed_at` precedence | Implemented; conflict, replay, and monotonic-security tests pass locally |
| Provenance, decision reason/time/version, confidence, sensitivity, validity, record versions, hashes, client permissions | typed models, migration 005, policy/storage round trips | Implemented; source-inclusive/source-free and pre-v5 restore regressions pass locally |
| Optional automatic-decision inspectability | `/v1/admin/observations` exposes disposition, record ID, reason/time/version, source, evidence, and authenticated submitter; Context shows provenance/history; Activity renders the observation stream | Implemented; dashboard/API tests pass locally |
| Immediate correction with preserved history | explicit targeted correction observation and existing record-version lifecycle | Implemented; new/legacy HTTP, MCP, ACL, history, and idempotency tests pass locally |
| Reversible ordinary deletion | history-preserving delete; `restore_record` and admin endpoint restore latest deleted state or a selected historical version, rebuild FTS, version, audit, and replication state | Implemented; Core/API/UI, contiguous-history, and ordered Relay restore tests pass locally |
| Reversible imported-source deletion | migration 006, provenance-bounded source/record deletion membership, admin delete/restore endpoints, dashboard Remove/Undo, duplicate-reimport restoration | Implemented locally; storage/API/UI regressions prove independently deleted records are not resurrected |
| Idempotent/resumable ingestion with atomic policy publication and coverage | `ingestion.py`, staged observations, `finish_ingestion`; retry/resume/coverage tests | Implemented; ownership, failure-before-finish, and replay verification pass locally |
| Generic JSON/JSONL/Markdown import | `importers.py`; importer/security tests | Implemented |
| Full local ChatGPT/Claude/Grok history ingestion | `provider_ingestion.py`, streaming ZIP/JSON adapters, staged policy publication, `outcomes`/`record_ids` import response, dashboard provider flow, raw-source recovery | Automatic import, failure isolation, and dashboard outcome receipt pass locally; real personal export acceptance pending |
| Structured filtering and FTS5 | retrieval engine; policy-before-ranking and integration tests | Implemented |
| Future embedding boundary | shadow-retriever contract plus disabled, rebuild-only 384d exact-scan experiment outside package discovery | Defined; no production embedding dependency or authority |
| Required MCP tools | `mcp_adapter.py`; `observed_at` input, automatic disposition/record/reason/time/version output, and explicit reversible `forget_context`; STDIO contract tests | Implemented; contract, handshake, restart, correction, and queued-forget suites pass locally |
| One-time local app connection | `client_config.py`, setup wizard, dashboard; Codex/Claude classic and Windows Store/MSIX detection/config tests | Implemented locally; this Windows Store install resolves to its package-local roaming config |
| Optional administration UI, no memory inbox | `apps/dashboard`; Review route/forms removed, Context default, Activity/provenance, context and source delete/undo, version restore | Local evidence: 27 tests and type check pass; production rebuild, dependency audit, and browser smoke pending for this worktree |
| Approval-free reproducible demo | `scripts/demo.py`, `tests/e2e/test_demo.py`; automatic finish-to-retrieve, restart, correction/delete, revocation, encrypted restore | Included in the passing 513-test Windows Python 3.14 suite; hosted Python 3.12 matrix pending |
| Portable export/restore | encrypted export/dashboard download and CLI restore tests | Implemented; automatic policy/link, pre-v5, source-free FK, FTS, and purge-barrier round trips pass locally |
| Locking, shutdown, restart | lifecycle locks, managed adapter self-heal, packaged first-run smoke | Implemented locally |
| OS credential abstraction | credential store/keyring abstraction and platform acceptance script | Windows local and Windows/macOS/Linux hosted acceptance observed at `05c7638` |
| Windows/macOS/Linux CI | `.github/workflows/ci.yml` source, dashboard, and native-package matrices | Release matrices observed at `05c7638`; Retrieval V3 push matrix observed green at `67dd11c` |
| One-click desktop packaging | Windows installer, macOS app/DMG, Linux portable archive | Platform acceptance observed at `05c7638`; final release SHA pending |
| Signed community updates | Ed25519 manifests/keyring with active beta key `release-2026-a`, installed/frozen package identity, trust-backed available-channel diagnostics, explicit `unpublished` state for only the empty canonical beta channel, owner-admin immutability preflight without an Actions admin token, canonical packaged beta endpoint, pinned GitHub release-asset redirect, checksums, SBOM/provenance, Windows recovery helper | Client, public trust root, release mechanics, installed-runtime regression, legacy-404 normalization, and custom-endpoint fail-closed coverage implemented locally; protected offline-signed publication, channel deployment, and real N-1 drill pending |
| No third-party V1 runtime | no Edge UI/onboarding/status call/background worker; Edge publication workflow and Render templates removed | Implemented |
| Direct-Core mobile model | integration API/dashboard/architecture state Core-online requirement | Product contract implemented; secure pairing/transport not yet implemented |
| No automatic public exposure | loopback default; dashboard warning; acceptance gate | Implemented |
| Legacy `always_available` compatibility | schema and old records retained; new applied context uses `core_available`/`local_only` and labels old records legacy | Implemented |
| Legacy review-data migration | migration 005 maps approved/rejected to applied/ignored and startup reevaluates eligible staged rows under `automatic-v1` | Implemented; partial-migration restart, pre-v5 duplicate restore, and idempotency regressions pass locally |
| Relay remains queue/projection only | Relay MCP returns staged receipts; Core evaluates dequeued observations; signed record events originate at Core | Implemented in current worktree over dormant compatibility code; no hosted runtime |
| Legacy Edge cleanup without normal operation | dormant manager/admin cleanup APIs; no automatic worker | Compatibility only; not a V1 feature |
| Frozen Retrieval V2 comparator | `retrieval_contracts.py`, pinned fixture hashes/ranking fingerprints, foundation harness | Implemented; comparator identity `70a4808` |
| Applied/current policy before time/relevance | authorization-only selector, current-record eligibility, temporal IDs, ranker-candidate-scoped FTS, boundary tests | Baseline implemented; new disposition migration/isolation verification pending |
| Current and `as_of` retrieval | UTC interval sidecar, request/MCP/CLI fields, DST/offset/restart tests | Implemented locally; Python 3.12 three-OS suite observed green at `67dd11c` |
| Deletion/purge resurrection barrier | authoritative terminal facts, purge tombstones, stale-sidecar recovery, pre-removal export restore test | Zero resurrection in local bounded gate; three-OS suite observed green at `67dd11c` |
| Weighted bounded FTS5 | `lexical_v3.py`; weighted columns, exact/OR/prefix caps, Unicode/case/tokenizer and secure-delete tests | Implemented locally |
| Task admissibility | deterministic numeric factor gate after hard policy/time, fail-open sparse evidence, shadow-only learned interface | Implemented locally; bounded precision improves without exact Recall@5 loss |
| Safe retrieval diagnostics | closed reason codes and numeric/boolean aggregates; admin-only returned-ID explanations | Implemented; content/unauthorized-ID exclusion tests |
| Retrieval V3 benchmark gate | foundation fixtures plus integrated 1k/10k quality, latency, storage, migration/restart/restore checks | Full local Windows gate passed; source/tests/packages observed on three OSes at `67dd11c` |
| Set-level marginal context selection | `set_selection.py`, `ContextCompiler` wiring, compatibility/diversity/conflict/support/mandatory/budget fixtures | Implemented locally; 11/11 standalone gates and combined semantic coverage `1.0` |
| Optional local dense shadow | disabled in-memory 384d exact-scan experiment, bounded tests, authorization-first filtering | Implemented as research only; 10k p95 `400.294955 ms` misses `150 ms`; real model/semantics unexercised |
| Source-evidence retrieval research | sanitized imported-chat fixtures; lexical passage and deterministic token-MaxSim benchmark/report | Implemented as research only; 64/256 recall and coverage `1.0`, diverse redundancy zero; neural path unexercised |
| Hybrid AI-memory reliability program | ADR-042; `docs/research/ATC_MEMORY_RELIABILITY_ARCHITECTURE.md`; external-baseline, Memory Plane, Intent/Consequence Plane, outcome-closure, and benchmark contracts | Research direction only; no external engine, new schema, working/episodic/procedural runtime, checkpoint ABI, or learned component implemented |
| Consequence-closed context | `docs/research/CONSEQUENCE_CLOSED_CONTEXT.md`; consequence contracts, capsules, target invalidation, memory-constraint tokens, and ConsequenceBench | Research only; explicitly not the complete memory product and not an enforcement or client-conformance claim |
| Memory Lab M0 adapter and task-metric ABI | `memory_lab.py`, `bench/memory_lab.py`, frozen sanitized fixture, and `test_memory_lab.py`; read-only authorized snapshot, manifest, ID-only adapter result, identifier-free report, abstention, sufficiency, disclosure, determinism, latency/storage/cost contracts | Implemented as bounded local research; no-memory and simple controls plus current ATC comparator only, with no external provider code or production authority |
| AI-memory evaluation program | `docs/research/ATC_MEMORY_EVALUATION_PROGRAM.md`; `bench/memory_reliability_spec.json`; `bench/memory_reliability_fixtures.json`; `tests/unit/test_memory_reliability_spec.py` | Specification only; 18 symbolic scenarios and 11 structural tests pass; no longitudinal adapter, external-system result, or production promotion claim |
| Governed independent Memory Lab waves | ADR-044; `docs/research/ATC_MEMORY_LAB_GOVERNANCE.md`; `research/memory-lab/wave2-manifest.json`; `tests/unit/test_memory_lab_wave_governance.py` | Wave 2 active with five visible worktree cells, one coordinator integration authority, explicit supplier gate, and machine-checked ownership/authority invariants; no Wave 2 result accepted yet |

## Deferred by the V1 boundary

- hosted Edge/Relay deployment and offline mobile replicas;
- third-party hosting/provider setup;
- multi-master synchronization, CRDTs, family accounts, and multi-tenant SaaS;
- live location, heart rate, wearables, and emergency response;
- production vector embeddings; and
- automatic secure remote-Core exposure until device pairing and encrypted
  transport are designed and accepted.
