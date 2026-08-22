# Requirements traceability

"Implemented" means exercised locally; authored CI is not called observed until
the hosted jobs pass on the exact commit. Earlier-commit evidence does not
satisfy a frozen release-candidate gate, and pre-ADR-039 approval evidence does
not satisfy automatic-policy rows. The path from this integrated baseline to
the first usable public beta is governed by
[`ROADMAP_TO_V1.md`](ROADMAP_TO_V1.md).

### 2026-08-22 source-rebuild atomicity correction

The idempotent/resumable ingestion and full provider-history requirements now
include staged complete-source rebuilds. A rebuild leaves prior current context
untouched while parsing and submitting replacement candidates, then performs
old-record eligibility checks and new policy publication in one Core/SQLite
transaction, with a durable generation/session marker for post-cutover retry.
Focused synthetic regressions cover parser failure, injected
ingestion/policy rollback, cancellation, corrected records, and local-authored
records. Full Ruff and mypy checks pass, and the full Python suite passes 1,065
tests with 4 host-limited symlink skips. The existing exact-candidate
interruption and provider-export receipts remain separate release evidence
requirements.

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Frozen Python dependency audit | `pyproject.toml`; `uv.lock`; `scripts/dependency_audit.py`; ADR-091 | The dev-and-packaging export fails closed on newly published advisories. The 2026-08-21 response to PYSEC-2026-3721 / CVE-2026-13346 requires fixed `pip>=26.2,<27` while keeping pip outside runtime dependencies. Both hosted PR 66 matrices and CodeQL passed before merge at `088485d`; this source repair grants no beta acceptance credit |
| First usable V1 beta contract | `ROADMAP_TO_V1.md`; ADR-053/ADR-075/ADR-086/ADR-088/ADR-089/ADR-092/ADR-095/ADR-096; `acceptance_receipt.py`; both receipt-bundle templates; `STATUS.md`; this matrix | `0.1.0-beta.6` is published as immutable prerelease `374723649` from exact source `d6d51acc5a880e611a65a206b90c6eb68118443d` and candidate digest `afa9036b4df5aca85975e9f3fdf475ac85e589a4ad0937d3ca1c440287b78647`, with 34 GitHub-attested assets. Its exact six-gate `lean_public_beta_v1` bundle contains Windows/Ubuntu first run (`BETA-L01`/`BETA-L02`), security (`BETA-S06`), exact source (`BETA-R01`), human key custody (`BETA-R02`), and immutable inventory (`BETA-R03`), plus all four required maintainer acknowledgements. The unchanged 20-gate `certification_v1` contract remains incomplete rather than relabeled. Windows 11 x86-64 and Ubuntu 24.04 LTS x86-64 are supported; macOS is unsupported. `BETA-R05` channel/download smoke and `BETA-O01` remain postpublication work |
| Post-V1 zero-routine-friction platform direction | ADR-090; `product/ZERO_FRICTION_PLATFORM.md`; `product/ZERO_FRICTION_EXECUTION_PLAN.md`; `product/PRODUCT_REQUIREMENTS.md` | Accepted product and execution direction only. Core remains authoritative; connected text remains inert untrusted data; authorization and lifecycle resolution precede derived work; capability levels prevent L0 integrations from claiming lifecycle hooks; correction, deletion, retention, and purge dependencies close future ATC influence. Phase 0 preserves the initial Windows/Ubuntu lean-beta boundary. `ZF-*` implementation issues begin only after tracker reconciliation and beta publication, and no graph, connector, working-state, learned-retrieval, remote, or stable-SDK claim is credited to the initial beta |
| Cross-platform Core | `config.py`, `lifecycle.py`, `platform_compat.py`; platform/package smoke tests; retained `macos_acceptance_preflight.py`; ADR-086 | Public-beta floor: Windows 11 x86-64 and Ubuntu 24.04 LTS x86-64 GNOME with working Secret Service/GNOME Keyring; other Linux environments are experimental. macOS source, tests, and the historical preflight remain for portability, but macOS is unsupported and excluded from candidate assets, claims, and acceptance receipts. Exact supported-target clean-machine and final release-SHA receipts remain pending |
| Correct per-user data paths | `platformdirs` configuration and setup/package tests | Implemented |
| Loopback-only default | `CoreConfig`, server CLI, dashboard copy, security tests | Implemented |
| Research supplier provenance and isolation | `research/competitor-intake/memory-systems-intake.v1.json`; Wave 2 manifest; Hindsight provenance and skip receipts; ignored `research/vendor-cache`; adapter/intake/packaging guardrail tests | Official Hindsight source was temporarily cloned at pinned revision `fa69b5b`, statically reviewed, and removed; no supplier package, model, container, script, service, provider, credential, benchmark, copied source, or packaged dependency |
| Observation/disposition/current-context lifecycle | `models.py`, `memory_policy.py`, migration 005, storage transactions, evidence links | Implemented; full local suite and focused policy/storage/API regressions pass |
| Direct-secret pre-ledger boundary | `secret_boundary.py`, migration 008, Core/Relay pre-queue refusal, internal-store pre-write defense, opaque UUIDv4 receipts, startup/export/restore repair and compaction, adversarial byte-scan tests | Implemented locally for direct proposals, batches, corrections/errors, forget/reject/delete redaction, direct Core-store calls, Relay queues before proposal hashing, replay, diagnostics, SQLite/WAL/freelist/FTS, and encrypted export/restore. High-confidence vendor-token and credential-URI forms are covered without a broad entropy filter. Historical external backups and device remanence remain operator-retirement concerns; exact-candidate security acceptance is pending |
| Append-only policy-decision history | decision metadata on observations/current records plus audit/history surfaces | Partial: current metadata is inspectable, but every automatic policy transition is not yet represented as a replayable append-only event stream |
| One-time setup with no routine memory queue | automatic MCP submission plus dashboard Context default/Review removal | Integrated on current main; exact packaged fresh-user browser proof pending |
| Core-only `automatic-v1` authority | origin assigned by Core; applied/reinforced/tentative/ignored decisions; Relay staged queue receipts | Disposition authority and ACL/Relay tests pass. A-09/B-102 source implementation is integrated: closed `witness:explicit_user_statement` grant, default-false MCP claim, archive-smuggle reduction, and policy decision metadata; exact-client/exact-artifact receipts remain open |
| Explicit user observation becomes current automatically | `add_candidate` policy transaction, MCP response contract, and Codex managed-server approval policy | Approval-free observe-to-later-retrieve E2E and witness unit/integration tests pass locally. Installed Codex CLI 0.144.0 also completed a disposable source-runtime apply/retrieve and post-Core-restart retrieval after the managed ATC server explicitly used Codex's supported `approve` tool policy. A-09 is a documented local-client trust grant rather than cryptographic authorship proof; exact packaged Codex/Claude receipts remain open |
| Tentative/ignored/staged isolation | current-record-only retrieval, staged ingestion, policy tests | Implemented; restart, pre-v5 restore, FTS rebuild, and retrieval-isolation tests pass locally |
| Configurable tentative retention/decay | future versioned policy, deterministic replay, and noncurrent-isolation requirements | Deferred; not implemented or claimed by `automatic-v1` |
| Duplicate reinforcement and deterministic slot conflict | observation links, normalized value matching, explicitness then `observed_at` precedence | Keyed slots implemented. Unkeyed archive lineage (ADR-097) collapses only when a derived subject-only key matches for preference/goal/project/decision/workflow/constraint kinds; bounded preference values (including answer style and dark/light) and literal choice-before-`for` values are excluded from that key. Fixture `b102_chronological_conflicts.json`, reverse-order coverage, and unrelated same-kind independence tests cover the archive path. Kind-only collapse is not used. Generalized multi-slot normalization remains post-V1 |
| Provenance, decision reason/time/version, confidence, sensitivity, validity, record versions, hashes, client permissions | typed models, migration 005, policy/storage round trips | Implemented; source-inclusive/source-free and pre-v5 restore regressions pass locally |
| Optional automatic-decision inspectability | `/v1/admin/observations` exposes disposition, record ID, reason/time/version, source, evidence, and authenticated submitter; Context shows provenance/history; Activity renders the observation stream | Implemented; dashboard/API tests pass locally |
| Immediate correction with preserved history | explicit targeted correction observation and existing record-version lifecycle | Implemented; new/legacy HTTP, MCP, ACL, history, and idempotency tests pass locally |
| Reversible ordinary deletion | history-preserving delete; `restore_record` and admin endpoint restore latest deleted state or a selected historical version, rebuild FTS, version, audit, and replication state | Implemented; Core/API/UI, contiguous-history, and ordered Relay restore tests pass locally |
| Reversible imported-source deletion | migration 006, provenance-bounded source/record deletion membership, admin delete/restore endpoints, dashboard Remove/Undo, duplicate-reimport restoration | Implemented locally; storage/API/UI regressions prove independently deleted records are not resurrected |
| Idempotent/resumable ingestion with atomic policy publication and coverage | `ingestion.py`, `importers.py`, staged observations, raw-first preservation, `finish_ingestion`; retry/resume/coverage tests | Implemented locally: raw bytes are authoritative before parsing; failure/cancellation retains an inert retry source; path parsing uses a reconstructed Core copy; parser-versioned replay publishes no duplicates. Exact-candidate interruption receipts remain pending |
| Generic JSON/JSONL/Markdown import | `importers.py`; importer/security tests | Implemented |
| Raw imports through 2,000,000,000 bytes | `config.py`, `import_boundary.py`, `boundary_canary.py`, `importers.py`, migration 007/009, `import_operations.py`, `source_blobs`/`source_blob_chunks`; database-volume preflight, raw-first preservation, bounded chunks, operation-authoritative unchanged-byte reprocess heartbeats isolated from source telemetry latency, bounded timestamp-only WAL liveness commits, parser-reclassification merge rebinding of operation `source_id` on complete/fail/cancel without complete-canonical downgrade or re-ingest, cancellable preserved-source reconstruction checkpoints with partial-copy cleanup, preserved-source retry trackers initialized at the already-committed byte boundary, boundary-canary-v2 JSONL-safe alignment, closed generic skipped/unparsed accounting, direct source-only telemetry, terminal source state, retry/cancel, migration, copy-integrity, and focused adversarial regressions | A-08 makes the inclusive boundary mandatory. Source-level boundary/recovery machinery, durable import operations, and the corrected deterministic canary contract are implemented. WSL2 first exposed operation delay from serialized source-plus-operation transactions; source now isolates operation-owned heartbeat authority. Exact candidate `4257e40` then reproduced 15/17 over-budget unchanged-byte heartbeat intervals on a qualified 4-vCPU/8-GiB Ubuntu QEMU target, with a 10.196354-second maximum despite correct import data and resource use. Unchanged-byte liveness now updates only the operation timestamp through a 250-ms, WAL-NORMAL telemetry writer; semantic progress and terminal state retain the original lifecycle writer, source-only behavior is unchanged, and non-lock SQLite failures propagate. Candidate `628797d` kept durable retry liveness within 4.936978 seconds but authenticated API receipt reached 5.735102 seconds while direct SQLite stayed within 3.731520 seconds. Operation-owned streaming JSONL parsing now yields one millisecond at existing one-MiB checkpoints so the observer and ASGI loop receive a scheduling turn; auth, durable state, source-only behavior, and response semantics are unchanged. Replacement candidate `7ffb1a4` passed the exact Windows straight/repeat slices but missed the strict cancel-ack deadline because preserved-source reconstruction checked no cancellation until its complete copy; source now checks after every at-most-8-MiB chunk and cleans a partial target on cancellation. Exact candidate source `65612cc` then exposed a no-upload retry progress regression: the preserved source was fully committed, but the first forced retry phase briefly wrote zero committed bytes/percent before restoring the declared size. Retry trackers now initialize their monotonic byte state and emission watermark at the preserved declared size, so the first durable phase cannot regress. A new immutable candidate must rerun the complete Windows journey. A new immutable Linux artifact must prove the frozen five-second gate and run interruption. Allocated non-sparse exact-boundary success on Windows x86-64 and frozen-target Linux x86-64, boundary-plus-one refusal, interruption, resource budgets, SHA, packaged export, and restore evidence also remain open |
| Full local ChatGPT/Claude/Grok history ingestion | `provider_ingestion.py`, `provider_shapes.py`, streaming ZIP/JSON adapters, versioned parser identities (`provider-archives-v2`), closed coverage, staged policy publication, `outcomes`/`record_ids` import response, dashboard provider flow, raw-first recovery, complete-source rebuild from the preserved blob, packaged `--packaged-provider-acceptance` control surface; ADR-069/ADR-088/ADR-097/ADR-099/ADR-100 | All three providers are mandatory under A-11. Frozen fictional shape sets and parser identities are implemented; every import reconciles recognized, excluded, skipped, unavailable, failed, and unparsed counts. Extraction publishes specific durable kinds, requires durable preference evidence, and keeps task-local/adversarial instruction framing inert; sensitivity is classified conservatively. Complete-source rebuild withdraws uncorrected automatic records reversibly and re-extracts without destroying the raw blob or history. Known empty/tool/attachment ChatGPT graph shells now close into excluded/skipped/unavailable while unknown/malformed material remains unparsed and keeps coverage incomplete. Provider conversation wrappers, nested wrappers, and root conversation arrays account for every malformed or unknown entry as unparsed, retain valid siblings, emit content-free structural warnings, and keep `complete` false. Packaged acceptance emits content-free stage codes for operation failure, non-complete operation status, and reconcile refusal; synthetic ZIP/classifiable-graph and unknown-residual regressions cover the former ambiguous fail-closed path. ADR-088 binds `CoreService` as a context manager so `CoreStore.close()` always precedes owned-vault `rmtree` on success and exception paths; an `OSError` after close still yields `data_dir_cleanup_failed` / exit 1, and caller-supplied data-dir deletion is unchanged. Privacy-safe nonempty real exports acquired after parser freeze and within 30 days, execution of all three against exact downloaded candidates, and inventory-bound receipts remain open and block beta if any one is missing |
| Bounded ChatGPT attachment inventory and text slice | `importers.py`; synthetic ZIP regressions in `tests/unit/test_provider_ingestion.py`; ADR-105; `docs/protocols/INGESTION.md` | Implemented locally for `.dat` identity/hash/raw preservation, manifest/filename/MIME provenance, resolved attachment-ID conversation/message linkage, strict ZIP bounds, and supported text formats `.txt`, `.json`, `.jsonl`, `.csv`, `.md`, `.markdown`. Unsupported binary/document/web/script formats remain explicitly unavailable. This does not claim all `.dat` contents are searchable, does not provide office/PDF/media extraction, and has no real-export acceptance receipt; only structural real-export inspection was performed content-free |
| Structured filtering and FTS5 | retrieval engine; policy-before-ranking and integration tests | Implemented |
| Request-bound context-search pagination | `SearchRequest`, `SearchCursor`, Core `/v1/context/search`, per-installation HMAC cursor signing, `tests/integration/test_core_api.py`, ADR-103 | Implemented locally; malformed, negative, oversized, bounded, normal-page, query/filter/page-size mismatch, and cross-principal cursor cases are covered by API tests. The cursor is integrity-authenticated but not encrypted, one-time-use, expiry-bound, or snapshot-consistent |
| Future embedding boundary | shadow-retriever contract plus disabled, rebuild-only 384d exact-scan experiment outside package discovery | Defined; no production embedding dependency or authority |
| Required MCP tools | `mcp_adapter.py`; `observed_at` input, automatic disposition/record/reason/time/version output, and explicit reversible `forget_context`; STDIO contract tests | Implemented; contract, handshake, restart, correction, and queued-forget suites pass locally |
| One-time local app connection | `client_config.py`, setup wizard, dashboard; Codex/Claude classic and Windows Store/MSIX detection/config tests; autouse disposable client-config/keyring fixture | Implemented locally; this Windows Store install resolves to its package-local roaming config. Installed Codex CLI 0.144.0 accepted the managed STDIO shape and recovered a stopped disposable Core. Tests now fail away from real Codex/Claude config and credential roots; persistent-profile and Claude signed-in exact-artifact receipts remain open |
| Optional administration UI, no memory inbox | `apps/dashboard`; Review route/forms removed, Context default, Activity/provenance, durable import-operation flow, context and source delete/undo, version restore, source rebuild, Context total/pagination/kind/sensitivity/confidence filters without auto-select; search-wrapper `:focus-within` amber outline (ADR-089) | Source now has a focus-dependent search-wrapper indicator, a Python source regression that requires a nonzero non-none wrapper indicator, and a dashboard test that keeps the existing sr-only accessible name. Context search uses the API `total` and cursor pagination instead of a hidden 100-row cap. A static search border is not treated as focus. BETA-P06 has not passed; exact packaged Edge keyboard/focus/error/narrow-width acceptance remains open. Deliberate purge is available via the packaged recovery/admin helper; exact downloaded-artifact administrator receipts remain open |
| Approval-free reproducible demo | `scripts/demo.py`, `tests/e2e/test_demo.py`; automatic finish-to-retrieve, restart, correction/delete, revocation, encrypted restore | Integrated on current main and covered by its green hosted Python/package matrix; final release-SHA replay pending |
| Portable export/restore | encrypted export/dashboard download, contributor CLI restore tests, packaged recovery/admin helper/mode (`recovery_admin.py`, Windows helper, Linux console main binary); retained Mac helper code; separately hashed source-chunk entries with complete-source reconstruction checks | Existing round trips and packaging integration pass. Exact downloaded-artifact stopped-Core restore/purge receipts on supported Windows and Linux remain open (BETA-D03); macOS is outside the release scope |
| Locking, shutdown, restart | lifecycle locks, managed adapter self-heal with one bounded 30-second one-file startup window, packaged first-run smoke; idempotent `CoreStore.close()` / `CoreService` context manager (ADR-088) | Implemented; packaged provider acceptance now closes observer and WAL handles before owned-vault removal without `gc.collect`, sleep, retry, or `journal_mode` changes. Exact functional branch evidence and roadmap-baseline hosted matrices pass, with final release-SHA replay pending. ADR-088 landed at `6151e1f`; that SHA cannot be reused as `beta.2` after the P06 source finding, so the next exact-main identity is `0.1.0-beta.3` |
| OS credential abstraction | `credentials.py`, transactional desktop/client configuration, keyring acceptance script, platform fault-injection tests; packaged first-run smoke uses explicit isolated development-file credentials only | Normal setup fails closed without protected OS storage; plaintext development files require deliberate opt-in; managed configs omit bearer tokens when the OS store is used; failed storage/config writes revoke new principals, remove credentials, and restore prior config bytes. Packaged first-run smoke asserts the isolated development store and does not stand in for real OS credential acceptance; failure diagnostics are content-free and the disposable work tree is always removed. Exact-package real Windows Credential Manager and supported Linux Secret Service receipts remain pending; the Mac adapter is retained but unsupported |
| Safe Core response sinks | browser handoff data-attribute encoding, constant nonce-protected handoff script, inert acceptance parser, sanitized integration configuration failures, focused security regressions; ADR-064/ADR-070 | Product responses are implemented locally: request-derived dashboard targets and browser capabilities are data rather than executable JavaScript, and integration parser exceptions cannot disclose raw paths, credentials, or personal configuration material. The acceptance extractor binds the handoff nonce to the exact response CSP and rejects external `src`, extra executable markup, inert/ambiguous handoffs, and non-production storage/targets; focused adversarial regressions pass |
| Exact browser handoff and dashboard hygiene | packaged same-origin SVG favicon, bundled-serving regression, real browser P06/S05 receipt; ADR-009/ADR-064/ADR-070 | Source correction implemented after an exact Windows Edge handoff exposed the implicit `/favicon.ico` JSON 404. A clean committed local Windows package passed the focused real Edge P06/S05 replay with zero unexpected console/page errors, no external request, and the packaged favicon. Independent parser hardening is integrated and its exact production handoff/CSP probe passes. A rebuilt official downloaded release candidate remains required. BETA-S05 follows the frozen expiry/non-replay/referrer/cache/current-navigation/session termination/revocation boundary and does not impose forensic byte erasure on an already consumed ticket |
| Cross-platform source CI | `.github/workflows/ci.yml` source, dashboard, and supported native-package matrices; retained `macos_acceptance_preflight.py` as historical/source portability code | The hosted matrix covers only supported Windows and Ubuntu runners. The three Mac job contexts and ordinary Mac preflight were removed; retained Mac source and historical evidence create no support claim or receipt. Final release-SHA source health and supported clean-machine receipts remain pending |
| Supported desktop packaging | Windows installer and Linux portable archive; version-matched recovery helper/mode; bounded native-tool failure diagnostics and a single 30-second managed-Core startup window; packaged first-run smoke startup-key cleanup (`remove_smoke_windows_startup_key`); retained Mac packaging code under ADR-086 | Windows and Linux packaging/recovery surfaces are integrated. The official candidate matrix, inventory, release notes, and publication verifier accept only Windows x86-64 and Linux x86-64. Mac app/DMG code and historical packaging evidence remain in source but create no consumer asset or support evidence. Exact supported release artifacts and real-machine/downloaded-artifact receipts remain pending |
| Signed community updates | Ed25519 manifests/keyring with active beta key `release-2026-b` and revoked prepublication predecessor `release-2026-a`, installed/frozen package identity, trust-backed available-channel diagnostics, explicit `unpublished` state for only the empty canonical beta channel, owner-admin immutability preflight without an Actions admin token, authenticated paginated draft collision detection and numeric release-ID controls through publication (ADR-068/ADR-096), canonical packaged beta endpoint, pinned GitHub release-asset redirect, checksums, SBOM/provenance, Windows recovery helper, bounded non-sensitive rollback failure state, and one exact-state persisted rollback-smoke re-entry (ADR-082); `accept_exact_candidate` reopens a verified same-version offer for transactional acceptance smoke without network I/O; `publication_gate.py` + receipt recompute (ADR-059) refuse forged/incomplete/mixed digests | Beta.6 is public and immutable; its Windows x86-64 OTA ZIP is bound by the offline-signed `manifest-beta-windows-x86_64-v1.json` under `release-2026-b`. The separately protected beta-channel promotion remains pending, so direct downloads work while the canonical channel may still report `unpublished`. ADR-096 accepts GitHub CLI GraphQL node IDs only as nonnumeric validation metadata and preserves positive numeric REST IDs as the sole authority for REST asset endpoints. The exact channel smoke (`BETA-R05`) follows promotion. A real first-published-beta-to-successor N-1 transaction remains post-V1 |
| Stable release trust/channel path | candidate workflow accepts stable versions | Open: key selection, site builder, client endpoint, publish/promotion workflow, migration rehearsal, backup, and recovery remain beta-specific or absent |
| Exact-SHA reproducible candidate composition | `exact_source_gate.py` (canonical `.github/workflows/ci.yml` only; current eight required jobs with bound run_id/head_sha; primitive matrix-evidence recompute), `release_candidate.py` inventory schema + required checksum sidecars, authenticated release-list/numeric-ID draft resolver, receipt inventory-declared digests, dependency-closed locked install/parity scripts, checksums, provenance, SPDX SBOM, ADR-059/ADR-068/ADR-096 adversarial tests | The published beta.6 source historically passed its canonical 11-job CI and three-job CodeQL sets; the current exact-source contract has eight required CI jobs after removing the three unsupported Mac contexts. Draft operations still require numeric REST asset IDs. Published-state validation safely tolerates opaque `gh release view` GraphQL IDs without treating them as REST authority. Broader certification receipts remain separate from this closed lean publication identity |
| Repository and release security baseline | protected GitHub Actions/CodeQL checks, protected release environments, immutable releases, Pages from Actions, secret scanning/push protection, Dependabot, GitHub private vulnerability reporting, frozen Python/dashboard audits; ADR-065/ADR-080/ADR-087 | Current `main` protection requires strict pull-request/conversation resolution plus eight canonical CI and three CodeQL contexts; the three former Mac required contexts were removed before this source change. Force push/deletion are off; immutable Action SHA pins, secret scanning/push protection, Dependabot alerts/security updates, CodeQL default setup, and private reporting are enabled; both promotion environments require the sole maintainer and disallow administrator bypass. Newly reported Python and dashboard findings fail closed through the reviewed lock audits; the 2026-08-08 lock refresh selects cryptography 50.0.0, nanoid 3.3.18, PostCSS 8.5.26, and undici 7.29.0 with both local audits green. ADR-087 requires privileged release `workflow_dispatch` jobs to run from the default branch, check out `github.sha` rather than `inputs.source_commit`, and fail-close unless `source_commit` is 40 lowercase hex. Candidate-build jobs also require `source_commit` to equal that dispatch SHA; later publish and promote jobs treat `source_commit` as the reviewed historical candidate/release identity and must not require it to equal a later `github.sha`. Only the new pre-check steps bind expressions through `env`. Actions cache access is removed from the three privileged release workflows. Exact protected `main` `6be7e1d` passed hosted CodeQL run `31991996483` with zero results in all three languages, zero open `main` alerts, and alerts #3 through #21 fixed without dismissal. The separate Windows provider-acceptance CI failure on that SHA remains blocking pending the ADR-088 follow-up and a green exact-main matrix. Findings are fixed and rescanned, never dismissed. Administrator branch bypass and self-review remain explicit sole-maintainer residuals; AI-assisted governance is not independent human review |
| Public support and launch-watch sequencing | `SUPPORT.md`, `docs/KNOWN_ISSUES.md`, `SECURITY.md`, `docs/operations/RUNBOOK.md`, README links, release source validation, ADR-075/ADR-092 | Candidate creation fails closed unless support, known issues, security intake, and recovery guidance are present and linked. Beta.6 publication followed the exact six-gate lean bundle and four maintainer acknowledgements. This does not pass `BETA-R05` or `BETA-O01`: public channel/download smoke follows protected channel promotion, and live paths plus triaged launch-watch evidence close separately. The certification bundle remains the unchanged 20-gate contract |
| No third-party V1 runtime | no Edge UI/onboarding/status call/background worker; Edge publication workflow and Render templates removed; ordinary Core Edge/Relay operation routes and CLI commands removed or tombstoned; residual cleanup isolated under legacy-edge surfaces only | Implemented for the supported Core product surface (`BETA-S04`/`B-103`); exact packaged candidate matrix proof and publication remain open |
| Direct-Core mobile model | integration API/dashboard/architecture state Core-online requirement | Explicitly post-V1: the first usable beta is same-device only and has no supported pairing/transport/client acceptance claim |
| No automatic public exposure | loopback default; dashboard warning; acceptance gate | Implemented |
| Legacy `always_available` compatibility | schema and old records retained; new applied context uses `core_available`/`local_only` and labels old records legacy | Implemented |
| Legacy review-data migration | migration 005 maps approved/rejected to applied/ignored and startup reevaluates eligible staged rows under `automatic-v1` | Implemented; partial-migration restart, pre-v5 duplicate restore, and idempotency regressions pass locally |
| Remote Edge scoped forwarding authorization | experimental `edge_connection.py` compatibility path enforces Core-approved `context_scopes` on direct fetch, search, and bootstrap records and scrubs filtered aggregates; `tests/security/test_edge_forwarding.py` covers empty, wildcard, matching, and out-of-scope grants | Implemented as defense in depth on residual experimental code; ordinary Core product routes that would invoke it are removed/tombstoned by B-103 |
| Relay remains queue/projection only | Relay MCP returns staged receipts; Core evaluates dequeued observations; signed record events originate at Core | Authority tests pass; ordinary Core CLI no longer exposes `sync` or `serve-relay`; Relay modules remain for residual/compatibility tests only |
| Legacy Edge cleanup without normal operation | isolated `/v1/admin/legacy-edge` and `atc legacy-edge` status/decommission/forget; no automatic worker; decommission refuses when no residual paired Edge exists | Implemented with negative API/CLI/process/network proofs; exact packaged candidate artifact proof remains open |
| Frozen Retrieval V2 comparator | `retrieval_contracts.py`, pinned fixture hashes/ranking fingerprints, foundation harness | Implemented; comparator identity `70a4808` |
| Applied/current policy before time/relevance | authorization-only selector, current-record eligibility, temporal IDs, ranker-candidate-scoped FTS, boundary tests | Baseline and automatic-disposition migration/isolation verification are integrated; final candidate replay pending |
| Current and `as_of` retrieval | UTC interval sidecar, request/MCP/CLI fields, DST/offset/restart tests; Core catalog search requires `context:read` before returning current or historical content while non-content status remains independently gated by `context:status`; exact post-policy totals and cursor pages remain separate from bounded bootstrap evidence retrieval | Implemented locally; Python 3.12 three-OS suite observed green at `67dd11c`; synthetic >100-match exact-total/page, authorization/filter-isolation, status-only monitoring, and search-denial regressions added in current worktree |
| Deletion/purge resurrection barrier | authoritative terminal facts, purge tombstones, stale-sidecar recovery, pre-removal export restore test | Zero resurrection in local bounded gate; three-OS suite observed green at `67dd11c` |
| Weighted bounded FTS5 | `lexical_v3.py`; weighted columns, bounded evidence search, complete authorized catalog enumeration under the 50,000-candidate hard cap, exact/OR/prefix caps, Unicode/case/tokenizer and secure-delete tests | Implemented locally; catalog search is exact over the post-policy set while bootstrap/context compilation retains the 100-record evidence bound |
| Task admissibility | deterministic numeric factor gate after hard policy/time, fail-open sparse evidence, shadow-only learned interface | Implemented locally; bounded precision improves without exact Recall@5 loss |
| Safe retrieval diagnostics | closed reason codes and numeric/boolean aggregates; admin-only returned-ID explanations | Implemented; content/unauthorized-ID exclusion tests |
| Retrieval V3 benchmark gate | foundation fixtures plus integrated 1k/10k quality, latency, storage, migration/restart/restore checks; bounded failed-gate report; ADR-083 evidence boundary | Production CLI remains fail-closed at 10k warm p95 below 150 ms on comparable hardware. Shared-host 100-record pytest is functional evidence only; deterministic tests enforce the unchanged threshold and reject invalid/missing/mixed-profile latency evidence. Earlier source/tests/packages were observed on three OSes at `67dd11c`; a new comparable-hardware CLI run is required for current latency evidence |
| Set-level marginal context selection | `set_selection.py`, `ContextCompiler` wiring, compatibility/diversity/conflict/support/mandatory/budget fixtures | Implemented locally; 11/11 standalone gates and combined semantic coverage `1.0` |
| Synthetic retrieval usefulness eval | ADR-104; `bench/retrieval_usefulness.py`; sanitized fixture; isolated public-API vault; scorecard baseline; `tests/unit/test_retrieval_usefulness.py` | Developer-facing only: 15 synthetic cases cover current facts, stale/conflict/withdrawn exclusion, sensitivity, provenance, budget, and provider packaging. The harness refuses live Core data dirs and grants no beta credit. Production ranking, ingestion, schema, MCP, and dashboard Context are unchanged |
| Optional local dense shadow | disabled in-memory 384d exact-scan experiment, bounded tests, authorization-first filtering | Implemented as research only; 10k p95 `400.294955 ms` misses `150 ms`; real model/semantics unexercised |
| Source-evidence retrieval research | sanitized imported-chat fixtures; lexical passage and deterministic token-MaxSim benchmark/report | Implemented as research only; 64/256 recall and coverage `1.0`, diverse redundancy zero; neural path unexercised |
| Hybrid AI-memory reliability program | ADR-042; `docs/research/ATC_MEMORY_RELIABILITY_ARCHITECTURE.md`; external-baseline, Memory Plane, Intent/Consequence Plane, outcome-closure, and benchmark contracts | Research direction only; no external engine, new schema, working/episodic/procedural runtime, checkpoint ABI, or learned component implemented |
| Consequence-closed context | `docs/research/CONSEQUENCE_CLOSED_CONTEXT.md`; consequence contracts, capsules, target invalidation, memory-constraint tokens, and ConsequenceBench | Research only; explicitly not the complete memory product and not an enforcement or client-conformance claim |
| Memory Lab M0 adapter and task-metric ABI | `memory_lab.py`, `memory_lab_baselines.py`, `bench/memory_lab.py`, unchanged sanitized M0 fixture, separate Wave 2 control config, identifier-safe JSON/Markdown reports, and `test_memory_lab.py`; read-only authorized snapshot, v1 adapter ABI, additive task budget, report v2, abstention, sufficiency, forbidden output, budget, disclosure, determinism, latency/storage/cost, failure, and evidence-disposition contracts | Coordinator-reproduced bounded research: stable current-state log advances with `1.0` success/recall and zero forbidden output on 7 objects/5 tasks/20 repeats; current ATC is `0.8`/`0.9`/zero; retrieval-only, potentially fixture-aligned, and not implementation acceptance |
| AI-memory evaluation program | `docs/research/ATC_MEMORY_EVALUATION_PROGRAM.md`; specification/fixtures/tests; `memory_reliability_lab.py`; partial-E01 fixture/runner/report/tests | E01 specification covers 18 scenarios; coordinator-reproduced 6-scenario reference slice has governed 6/6, append-log 0/6, no-memory 1/6, and distinct four-rule ablation failures; fixture/rule co-design and no production Core/external/action execution are explicit |
| Governed external Hindsight boundary | `bench/hindsight_supplier_adapter.py`; pinned provenance and `not_executed_dependency_and_egress_gate` receipt; fake-client tests | Adapter declaration/translation/cleanup contract reproduced locally; no `L3` supplier result, benchmark score, supplier runtime, or production dependency; real execution requires a new gate |
| Governed independent Memory Lab waves | ADR-044/ADR-045; governance document; completed Wave 2 manifest; integrated Wave 2 result; machine-checked governance tests | Wave 2 complete with five visible worktree cells, coordinator-only integration, two `L2` deterministic synthetic results, two `L0` research reports, and one preserved supplier skip; no production promotion or real-user evidence |
| Wave 3 falsification program | ADR-046/ADR-047; completed Wave 3 manifest; integrated result; B01/O01/P01/E01b/M2 harnesses, fixtures, reports, and tests; metadata-only external-artifact intake | Complete with coordinator-only integration and 43 focused reproduction tests: B01 bounded configuration killed, O01 static winner held, P01 automatic durability held, E01b 6 narrow passes plus 6 unsupported/not-exercised semantics, M2 narrowly retained, and MPBench execution denied pending a separate quarantine cell; no production or external benchmark promotion |
| Evidence-Compiled Memory research contract | ADR-047/ADR-049; Wave 3 and Wave 4 integrated results; M2 sealed projection; M3 influence closure; M1 observable-use ledger; E02 production gap receipt | Research direction only: M3 and M1 retained at coordinator-reproduced `L2`; five required Core semantics remain unsupported and one is not exercised; no schema, runtime, external system, or solved-memory claim |
| Wave 4 closure and use-ledger program | ADR-048/ADR-049; completed Wave 4 manifest; integrated result; independent F02 oracle/review; M3/E02/M1 harnesses, fixtures, reports, and tests | Complete evidence-only execution: 49 focused tests; M3 15/15 and M1 16/16 frozen attacks pass with all hard-safety counts zero; E02 records five `UNSUPPORTED` and one `NOT_EXERCISED`; coordinator-only integration and no production promotion |
| Evidence-Compiled Prospective Memory hypothesis | ADR-049; Wave 4 integrated result; event-contingent transaction and frozen first-experiment design | Research proposal only: typed cue before disclosure, minimal current evidence, negative guards, dependency closure, action ceiling, and observable outcomes must beat a simple deterministic scheduler under non-compensable lifecycle and authority gates |
| Exact-candidate repository security | `repository_security.py`, exact committed-tree/history CLI binding, large ZIP/private-key and deleted-archive regressions, binary-vs-text ZIP absolute-path regressions; ADR-061/ADR-067 | Implemented with bounded history and streamed native-package/ZIP-member ceilings. P0 key/credential/raw-context scanning remains active for every ZIP member; P1 developer-home detection applies to human-readable members rather than incidental upstream roots in compiled extensions. The 36 exact POSIX artifact files from failed run `30200529010` rescan clean locally after this correction, but the release workflow has not been rerun and tar.gz/DMG content expansion is not claimed |
| Personally framed sensitivity and forwarding boundary | `memory_policy.py`; automatic context policy tests; Core Edge forwarding tests; ADR-102 | Implemented locally: partner/residence, HIV/health, and mortgage/loan statements are conservatively classified as `sensitive`, forced to `local_only`, and excluded from Core forwarding; unframed technical/general controls and highly sensitive precedence are covered. The heuristic is not an exhaustive semantic privacy detector |

## Deferred by the V1 boundary

- hosted Edge/Relay deployment and offline mobile replicas;
- third-party hosting/provider setup;
- multi-master synchronization, CRDTs, family accounts, and multi-tenant SaaS;
- live location, heart rate, wearables, and emergency response;
- production vector embeddings; and
- automatic secure remote-Core exposure until device pairing and encrypted
  transport are designed and accepted.

### 2026-07-28 import-operation liveness amendment

Queryable import-operation liveness now maps to timestamp-only WAL commits that
bypass the Python lifecycle lock and, only for lightweight operation liveness,
run at one tenth of the public five-second budget. The async status dependency
uses a dedicated single-worker with a persistent bounded read-only/query-only
WAL connection; each poll joins current non-revoked registration state and the
operation in one fresh statement. A process-keyed, worker-local HMAC cache
avoids repeated PBKDF without caching raw tokens or skipping durable revocation
checks. Only this high-frequency status observer omits per-poll durable
`last_used_at` activity writes; other routes retain ordinary authentication
activity semantics. Its worker is recreated for each sequential application
lifespan. Regressions cover cross-thread writer contention, source-only cadence,
async routing, cache mismatch/revocation/non-persistence, executor-thread
cleanup, and authorization-before-not-found ordering. A content-free WSL2
timing discriminator completed the exact 2,000,000,000-byte straight import
with maximum unchanged-byte `updated_at` intervals of 3.590 seconds by direct
SQLite observation and 4.774 seconds through the authenticated API. This
supports the source behavior only. Qualified QEMU and rebuilt exact-candidate
proof remain required.

### 2026-07-30 authenticated receive-liveness amendment

Exact candidate `628797d` produced a content-free, 1,338-record fsynced trace
for the qualified 2,000,000,000-byte cancel/no-upload retry. The authoritative
operation timestamp advanced within 4.936978 seconds and direct SQLite
timestamp/receipt gaps were 3.701321/3.731520 seconds, while authenticated API
receipt reached 5.735102 seconds. API request latency reached 3.428642 seconds
and first delivery lagged direct visibility by 3.986875 seconds. This closes
the durable-freeze hypothesis and maps the remaining B-105/BETA-D01 gap to
same-process API scheduling and delivery during CPU-heavy streaming JSONL
parsing.

`importers.py` now gives only operation-owned streaming JSONL parses a
one-millisecond scheduler handoff at the existing one-MiB progress checkpoints.
The durable operation row, timestamp-only WAL writer, authenticated joined
observer, revocation and scope checks, NotFound ordering, and response schema
are unchanged. The focused adverse-scheduler regression exercises the actual
cached authenticated observer and durable joined SELECT: before the handoff it
could not start until the roughly one-second bounded parse ended; with the
handoff it starts within 0.6 seconds and separately bounds auth/SELECT and JSON
serialization. A new immutable candidate must prove the frozen five-second
gate on the qualified Linux retry and then run the still-missing interruption
slice. No candidate acceptance is claimed from source tests.

### 2026-07-30 Windows cancellation amendment

Exact candidate `7ffb1a4` passed the Windows 2,000,000,000-byte straight import
and repeat but did not reach durable `cancelled` before the strict five-second
deadline. The cancel HTTP request returned and authenticated observation plus
timestamp-only liveness remained responsive, separating intent return and
observer delivery from worker acknowledgment. The worker was reconstructing
the preserved source under a `parsing` phase, and that full bounded-memory copy
had no cancellation checkpoint.

Reprocess now passes `ImportProgressTracker.check_cancelled` to the storage copy
helper, which invokes it after every stored chunk and removes any partial
target if the callback raises. A production-path controlled-copy regression
fsyncs HTTP-return, durable-terminal, and worker-quiescence timing before
assertions; the source fix reduces those clocks from
0.021 seconds / beyond the scaled 0.75-second acknowledgment bound /
1.560 seconds to 0.022 / 0.113 / 0.135 seconds. A second regression proves
partial-copy cleanup. The frozen five-second acknowledgment and 30-second
quiescence contracts are unchanged. Candidate `7ffb1a4` is invalidated, and a
new exact Windows artifact must rerun the entire journey; this source result is
not acceptance evidence.

### 2026-08-01 Windows repeat reconstruction-liveness amendment

Candidate descriptor
`b00297d19080d0a3252a48fe5d7ac3ad78d5395909612f86eb2ef1f2e851bc16`
on source `905efe5631ebf2fee77fafa5d8694f77df17b8bb` completed straight and
repeat data work but failed the repeat liveness gate. Consecutive durable
top-level timestamps during unchanged-byte `parsing` were 5.448395 seconds
apart; direct observations received them 5.447142 seconds apart. No receipt
was emitted.

The operation-owned repeat path exposes `parsing` before preserved-source
reconstruction and again before parser entry. A scaled, content-free
production-path regression reproduced the uncovered copy interval on
untouched `905efe5`: first successful liveness touch arrived 0.964490 seconds
after reconstruction start against a less-than-0.4-second gate, while
idempotent candidate identity remained correct.

`importers.py` now keeps the per-chunk cancellation check and adds a
one-millisecond handoff only when the tracker owns operation liveness.
`test_repeat_copy_yields_to_operation_heartbeat_under_cpu_pressure` covers the
production repeat path; the source-only negative test proves no added pause,
and existing cancellation/partial-copy tests retain fail-closed coverage.
Cadence, durable semantics, and the frozen five-second threshold are unchanged.
A new immutable candidate must rerun the complete Windows journey.

### 2026-08-02 source-blob finalization-liveness amendment

Replacement candidate source 7afc46b completed one Windows boundary
straight/repeat probe within budget, but a fresh evidence-complete straight run
failed closed. At unchanged 2,000,000,000 committed bytes, the maximum
top-level operation updated_at interval was 6.325973 seconds through the
authenticated API and 6.253638 seconds through direct SQLite. The import still
completed with exact hash, five candidates, closed coverage, clean SQLite, and
zero foreign-key violations. No receipt was emitted and the remaining journey
stopped.

The operation tracker had a liveness sink from construction, but its background
scheduler did not start until reprocess_source entered parsing. Source-blob
promotion therefore depended only on synchronous phase/chunk writes. A bounded
source-level 2 GB discriminator isolated the remaining lock behavior:
chunk-layout validation held SQLite's writer transaction for 1.253978 seconds;
independent timestamp-only touches returned busy while it was open and first
succeeded 0.013890 seconds after commit.

ImportOperationService now starts the operation-owned scheduler before staging
and closes it in a finally block across success, cancellation, failure, and
process-after-false return. CoreStore retains its Python lifecycle lock across
finalization but validates ordered chunk indexes and total bytes in a deferred
WAL read transaction. Timestamp-only liveness bypasses that Python lock and can
write concurrently; a fresh bounded immediate transaction rechecks immutable
blob fields before setting complete. Regressions prove the long validation
scan no longer owns SQLite's writer slot and that pre-parser promotion advances
and closes its background heartbeat. Integrity, phase/byte monotonicity,
thresholds, and source-only behavior remain unchanged. A replacement immutable
candidate must rerun the complete exact-artifact journey.

### 2026-08-08 nonterminal-operation durability amendment

Exact candidate source `4ab235d` twice completed the qualified Linux x86-64
2,000,000,000-byte straight import with exact source identity, 239 chunks, five
candidates, closed coverage, clean SQLite integrity, and zero foreign-key
violations. Both runs nevertheless failed BETA-D01 liveness at unchanged full
committed bytes during `processing`/`parsing`: durable top-level timestamp gaps
were 5.918573 and 5.332539 seconds, with independent authenticated API and
direct-SQLite receipt gaps also above the frozen five-second budget. No receipt
was emitted and later slices did not run.

Standalone full-source streaming, reconstruction, and complete JSONL parsing
kept a separate 20-millisecond observer schedulable. The remaining source path
generated the phase timestamp before committing it through the default FULL
WAL connection; readers retained the prior committed row while that flush held
SQLite's writer slot, so the fail-fast timestamp-only heartbeat could not
publish a newer row.

`CoreStore.update_import_operation` now routes only explicit nonterminal
progress to a serialized WAL-NORMAL transaction. The path keeps the Python
write lock, ordinary SQLite busy budget, atomic row validation, and monotonic
bytes. Preflight, cancellation intent, clear/error changes, result data,
completion, terminal states, source/blob authority, and all unrelated writes
remain FULL-durable. Focused regressions verify NORMAL/WAL configuration and
adversarial FULL routing. The five-second requirement is unchanged; a new
local source wheel passed a straight-only run in the same qualified guest with
a 0.780195-second maximum durable timestamp gap and 0.786998/0.800204-second
maximum API/direct receipt gaps. It emitted no receipt. A new immutable Linux
candidate must pass the complete D01 matrix.
