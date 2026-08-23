# Requirements traceability

"Implemented" means exercised locally. This integrated traceability matrix is
for the local Import → Memory Truth → Retrieval → Context UI baseline and the
separately bounded Wave 3 component handoffs; it does not claim hosted CI,
release publication, exact artifact/client/provider acceptance, or live/private
data inspection. Earlier evidence is retained only as historical context and
does not become evidence for this checkout.

### 2026-08-22 ZF-004 Wave 1 event reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Exact capture and lifecycle envelopes normalize independently into one bounded reference-only input | `experimental_event_reconciliation.py`; `tests/unit/test_experimental_event_reconciliation.py` | Implemented locally: capture operation and existing IDs/source generation/order/cursor/idempotency, lifecycle hook/session and exact payload/ownership/version validation, capture and payload-reference commitment/size, lifecycle `(client_id, event_id, sequence)` idempotency, context references, timestamps, retention/expiry, sensitivity, allow/deny authorization, and typed dependency withdrawals are retained without raw content; unlinked capture+lifecycle composition is rejected |
| Secret-like metadata and malformed evidence fail before a normalized input | `ReconciliationViolation`; isolated secret, exact-type, operation/generation, normalizer, payload-pairing, cursor-bound, and content-free error tests | Implemented locally with code-only failures; `as_dict()` exposes metadata and references only |
| Correction, delete, expiry, and purge dependencies withdraw safely | `DependencyWithdrawal`; delete-match and purge-action tests | Implemented locally: ordinary delete requires an authorized matching provider-item withdrawal, and terminal purge requires explicit `ERASE` |
| The slice does not create a second authority or persistence path | AST structural test; no direct storage/SQLite/network/provider SDK imports; no mutation, persistence, replay, cursor-advance, or observation/current-ID APIs | Implemented locally; Wave 2 Core/harness integration and provider capability claims remain out of scope |

Evidence is limited to deterministic synthetic unit tests and static structural
checks. Full repository pytest/mypy, hosted CI, provider access, private data,
release acceptance, and stable SDK/MCP lifecycle claims are not implied.

### 2026-08-22 ZF-006 Wave 2 Packet D zero-dashboard harness

| Requirement | Implementation/evidence | Status |
|---|---|---|
| One disposable journey composes capture, lifecycle, reconciliation, formation, Core policy, and authorized Retrieval V3 | `experimental_zero_dashboard_harness.py`; `tests/unit/test_zero_dashboard_harness.py`; sanitized `tests/fixtures/zero_dashboard_wave2.json` | Implemented locally as synthetic evidence: the existing deterministic fake adapter/ledger/coordinator and idempotent sink form five source observations through Core, an L2 fake host supplies pre-generation/direct-user/restart hooks, and Retrieval V3 compiles only authorized Core records. The projection closure check is separate content-free component evidence, not M3/Core/Retrieval integration. |
| First useful context and correction propagation are automatic | `run_zero_dashboard_journey`; `ZeroDashboardScorecard` | Implemented locally: phase-aware raw pack checks reject wrong-project, secret-like, inert-import, stale, expired, deleted, and purged facts in their applicable post-transition packs; direct evidence and a direct correction reach Core, and the next eligible compile contains the corrected value without the displaced value. Supersedes-output, query-adversarial wrong-project, unsupported-hook, and durable secret-absence checks close independently. The no-action claim is limited to the scripted fake-host trace, not operator telemetry. |
| Restart, cursor recovery, replay, authorization, retention, expiry, delete, purge, and zero future influence fail closed | retry/replay journey and scorecard gates; existing Core deletion/purge and Retrieval V3 selector/temporal boundaries | Implemented locally on a temporary SQLite database: a nonterminal checkpoint is resumed after Core close/reopen by a fresh coordinator/sink/adapter whose first call uses `cursor-1`; a later fresh completed replay directly compares equal Core counts with zero new capture events/observations/current records; the narrowed corrected record is absent for another principal; expiry is active at formation and absent from the later pack; ordinary delete and terminal purge have durable before/after proofs; post-restart time-to-first context is separately bounded. |
| Secret-like and imported material remain safe | direct-user reference resolver, formation refusal, Core opaque refusal receipt; inert imported fixture candidate | Implemented locally: the synthetic secret is held only in the test resolver, commitment-checked against the exact lifecycle `turn_ref`, refused before candidate persistence, absent from lifecycle envelope text and Core context state, and imported fixture text is retained only as tentative evidence with no current record. |

This is Wave 2 synthetic developer evidence only. It does not claim a real provider,
client/product acceptance, Memory Lab M3 integration, network/OAuth/client SDK,
scheduler, dashboard production behavior,
operator-vault access, private/live data, stable export, package/release
readiness, hosted CI, or full repository pytest/mypy acceptance.

### 2026-08-22 ZF-007 Wave 3 Packet E scheduler and health component

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Schedule capture around the existing Core coordinator with bounded retry and resource policy | `capture_scheduler.py`; existing `capture.py` capability/retry contracts; `tests/unit/test_capture_scheduler.py`; `tests/unit/test_capture_capabilities.py` | Component complete locally: disabled by default, reuses coordinator leases/checkpoints/cursors/event idempotency, honors bounded `Retry-After` or existing backoff, and applies per-connector concurrency/resource limits |
| Rotate bounded source selection and report truthful health | `CaptureScheduler._sources`; `CaptureScheduler._health_from_sources`; focused scheduler handoff tests | Component complete locally: source-page selection rotates in process, truncated health is explicitly `degraded`, and reauthorization actions are deduplicated in process; no durable scheduler or notification state |

### 2026-08-22 ZF-008 Wave 3 Packet F local Git/workspace component

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Read an explicitly authorized local root through the existing capture contract | `experimental_local_git_workspace_connector.py`; `tests/fixtures/local_git_workspace.py`; `tests/unit/test_local_git_workspace_connector.py` | Component complete locally: `fetch_page` fails closed before scanning unless the provider and `source.account_fingerprint` match `adapter.source_identity`; non-overlapping explicit roots only; deterministic snapshot/incremental events and Core coordinator replay reuse; partial coverage and network denial are declared |
| Fail closed at the local safety boundary | bounded scan/cursor constants and `CaptureScanReport`; provider/source-binding, AWS-shaped-secret, missing-root, secret-like, symlink/reparse, deletion, and over-20-file tests | Component complete locally: Git/dependency/credential paths and symlink/reparse paths are excluded, AWS `AKIA`/`ASIA`-shaped content is omitted, workspace text is inert, incomplete scans produce no partial page, and metadata cursors/samples/excerpts track at most 20 files |

### 2026-08-22 ZF-009 Wave 3 Packet G controlled reference host component

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Negotiate lifecycle capability truthfully and deliver context before generation | `experimental_reference_host.py`; `client_runtime.py`; `tests/unit/test_experimental_reference_host.py`; `tests/unit/test_client_runtime.py` | Component complete locally: in-process reference host accepts at most L2, ordinary MCP remains L0, L3 downgrades to L2, and pre-generation calls injected Core Retrieval V3 before delivery/generation; empty Core context fails closed before delivery or generation |
| Capture direct-user evidence and typed lifecycle checkpoints without overstating persistence | controlled-host fixture `tests/fixtures/reference_host_wave3.json`; typed checkpoint restore, ordering, retry-idempotency, integrity, L0, forged-session, and secret-refusal tests | Component complete locally: direct-user references are distinct from model self-attestation; typed snapshots restore events, trace, pending/delivered context, started-generation IDs, and sequencing state; the digest validates integrity only, the sink receives a stable retry idempotency key, L0/ordinary MCP resumes started IDs without fabricated context, L1+ retains request/delivery ordering, and no client-principal binding or production persistence is added |

### 2026-08-22 Packet H disposable proof stopped at source-fact admission

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Bind continuous Packet F evidence to deterministic source-fact promotion through Core | Stopped/removed Packet H disposable proof at the 2026-08-22 head; ADR-133 PR1 `registered_source_admission.py`; archive importer lifecycle remains separate | The stopped proof was open at the contract boundary. PR1 now implements only the local registered-source admission seam; Packet H composition and all product/provider acceptance remain open |
| Advance to the next narrow frontier without overstating acceptance | ADR-132 and ADR-133; focused successor-PR tests/docs | PR1 closes only the local admission contract. ZF-010, Packet H, Phase 2 acceptance, ZF-007/ZF-008/ZF-009 product acceptance, the first real source/client journey, production wiring, hosted CI for corrected head, release, and support remain open; macOS remains absent/deferred |

The original component handoff counts were E: 25 tests, F: 25 tests, and G: 27
tests. Corrected focused counts are F/capture-capability: 27 tests and
G/client-runtime: 32 tests. The integrated F/G-adjacent union at corrected head
`719bdd9030e32ac34eb12184c35e1e47cf99cc37` passed 59 tests; Ruff,
format-check, and `git diff --check` passed. The previous pushed head
`dcf5de50b633ff00638c1396ddfcfb8ba04070e6` was fully hosted-green, but the
corrected head has not yet run hosted CI; full repository pytest/mypy also
remain open. These rows do not close ZF-007/ZF-008/ZF-009 product acceptance,
the first real continuous source/client pair, ZF-010, Packet H, the Phase 2
acceptance journey, production wiring, release, or support status. macOS
remains unsupported/absent/deferred under the current project truth.

### 2026-08-23 registered-source admission PR1 contract

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Admit only a Core-issued registered-source structural fact through the existing capture sink | `registered_source_admission.py`; migration `016_registered_source_admission.sql`; `memory_policy.py`; sanitized `tests/unit/test_registered_source_admission.py` | Implemented locally for the bounded PR1 contract: exact durable event/run/source validation, closed local-workspace extractor registry, complete code-owned projection validation, opaque source/item memory references, Core availability, normal sensitivity, empty ACLs, explicit false, durable source scopes, deterministic capture-lineage record IDs, replay idempotency, source withdrawal, correction/delete/purge/no-linkage barriers, and content-free receipts. No CoreService, package-startup, scheduler, or reference-host wiring |
| Keep machine-local capture runtime out of portable archives without losing admitted Core truth | `export.py`; portable export/restore focused test | Implemented locally: all five capture runtime tables are omitted even for source-inclusive exports, registered candidate capture FKs are nulled, legacy capture table entries are ignored on restore, and same-database restart retains capture state |
| Advance ADR-132 without overstating acceptance | ADR-133; focused local tests only | PR1 closes only this local admission contract. Packet H, ZF-010, product/provider support, hosted/full-suite acceptance, stable SDK, production wiring, release readiness/publication, and macOS remain open, absent, or deferred |

### 2026-08-22 draft-PR formatting and CI-trigger reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Changed Python source satisfies the formatter gate used by hosted CI | `python -m ruff format .`; subsequent local `python -m ruff format --check .` | Implemented locally for the 23 files reported by the initial PR 73 Windows and Ubuntu jobs |
| Feature-branch validation runs one canonical CI matrix | `.github/workflows/ci.yml` limits `push` to `main` and `v*` tags and retains `pull_request`; release workflows remain manual | Implemented locally: pull requests retain the full supported Windows/Ubuntu, dashboard, security, parity, and desktop matrix; merged `main` and version tags retain push evidence; ordinary feature branches do not run push CI; no job or gate is weakened |

Hosted revalidation of this exact follow-up commit remains pending. The trigger
change does not alter the unsupported-macOS posture or the manual release,
candidate, and beta-channel ceremonies.

### 2026-08-22 complete-source coverage repair

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Sources dashboard retry repairs a source that is terminal-complete but coverage-incomplete | `ArchiveImportService.reprocess_source`; existing `publish_source_rebuild` authority; `test_complete_source_with_incomplete_coverage_repairs_from_preserved_blob`; dashboard retry contract regression | Implemented locally: non-rebuild reprocess routes only `import_status=complete` plus explicit `coverage_complete=false` into the preserved-blob rebuild path, which publishes only after complete coverage |
| Repair failure and concurrent retry remain safe | `test_incomplete_coverage_repair_failure_keeps_prior_records`; `test_concurrent_incomplete_coverage_repairs_are_idempotent`; `test_complete_healthy_source_reprocess_remains_a_noop` | Implemented locally: parser failure does not withdraw prior current records, concurrent callers converge on one rebuild generation, and healthy complete sources are not reparsed |

Evidence uses synthetic payloads and temporary local databases only. Dashboard
check, full test suite (55 tests), and production build pass locally. Full
pytest, hosted CI, release/publication, live/private data, and macOS execution
are not claimed here.

### 2026-08-22 generic bounded-failure coverage correction

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Standalone bounded CSV failures return truthful closed coverage without raising | `_parse_csv_document`; `_GenericCoverage`; `test_oversized_standalone_csv_is_unavailable_and_closed` | Implemented locally: an oversized synthetic CSV closes exactly one `unavailable` item, exposes the matching generic stat, returns no candidate, and remains incomplete |
| Every generic terminal failure reason maps to one declared counter | `_generic_failure_result`; `_combine`; `test_generic_failure_reasons_map_to_one_closed_counter` | Implemented locally for `unavailable`, `failed`, and `unparsed`; each result has exact seven-key accounting with a sum of one and no dynamic slotted-attribute assignment |

This correction is focused synthetic importer evidence only. Full pytest,
hosted checks, provider access, private data, release action, publication, and
macOS execution remain outside scope.

### 2026-08-22 hosted full-suite reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| A newly initialized vault reports the migration generation it actually applied | `CoreStore.initialize_vault`; `test_new_vault_schema_version_matches_applied_migrations`; complete recovery-admin file | Implemented locally: the insert binds `vaults.schema_version` to the latest applied migration, so later export/integrity verification does not mutate logical vault identity merely by reopening it |
| Frozen B01 expectations describe the accepted deterministic retrieval implementation | `test_b01_twenty_repeat_result_is_deterministic_identifier_safe_and_bounded` | Reconciled locally: `atc-retrieval-v3` confirmatory CAOS is 3/7 after the accepted query/ranking changes; fixture/config hashes, twenty-repeat determinism, zero external/model use, and the final kill decision remain unchanged |

The combined focused reconciliation is 24/24 green. The hosted full matrix for
the exact follow-up commit remains pending; no release or platform acceptance is
claimed from the local rerun.

### 2026-08-22 offline product-correctness maintenance

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Unicode-equivalent ZIP paths resolve to one logical member | NFKC/casefold identity in `parse_zip_bundle`; `test_unicode_equivalent_zip_member_collisions_are_deterministic` | Implemented locally: archive order selects the first member and later compatibility-equivalent names close as duplicates with bounded diagnostics |
| Capture integers fit durable storage and begun runs terminate on local failure | `MAX_CAPTURE_INTEGER`; `CaptureCoordinator.run`; focused capture range/recovery regressions | Implemented locally: generation, page order, and payload integers are signed-64-bit bounded before storage, and an unexpected local exception produces a content-free failed run instead of a nonterminal job |
| Pagination integers are strict | `SearchRequest.limit`; `SearchRequest.offset`; Core API cursor regression | Implemented locally: bounded integers remain accepted and JSON booleans receive HTTP 422 instead of becoming `1` or `0` |
| Direct record and registered-client lookup remain vault-local | `CoreStore.get_record`; `get_memory_truth`; client authentication/observer/list/count/revoke queries; focused temporary-database regressions | Implemented locally: synthetic rows assigned to a second vault are absent from authoritative record reads and cannot authenticate or appear in registered-client administration |

Evidence is limited to synthetic in-memory archives and temporary local
databases. No network/provider access, private data, full pytest matrix, hosted
checks, release action, publication, or macOS work is claimed.

### 2026-08-22 adversarial boundary sweep

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Unicode-equivalent direct credentials fail before durable state | `secret_boundary.py` detector v3; Core/Relay refusal paths; `tests/security/test_preledger_secret_boundary.py` | Implemented locally for compatibility-width, zero-width, and combining-form projections while retaining high-confidence matching and opaque content-free receipts. No raw secret, payload digest, or private fixture is retained |
| Canonical supersession remains acyclic and vault-local | `CoreStore._validate_supersedes_tx`; correction, approval, restore, automatic-create, and rebuild-reapply write paths; `tests/unit/test_memory_truth.py` | Implemented locally: missing, overlong, cross-vault, self, and bounded-chain cycles fail transactionally before canonical mutation. Self/two-node regressions keep temporal retrieval operational |
| ZIP member identity cannot collapse after diagnostic truncation | `_validate_zip_member_name`; `tests/unit/test_provider_ingestion.py` | Implemented locally: names over 1,000 characters or containing non-printable characters close unavailable with bounded escaped warnings; distinct long names are not reported as duplicates or silently preferred. Raw-first preservation remains unchanged |
| Capture sink cannot redirect canonical lineage | `CaptureCoordinator._apply`; strict capture value normalization; `tests/unit/test_capture.py` | Implemented locally: Unicode-obfuscated credential markers and implicit ID/integer coercions fail closed, and a sink receipt must return the exact deterministic source/item lineage before any item/checkpoint commit |
| Retrieval diagnostics remain finite and bounded under malformed inputs | `retrieval_contracts.py`; `DeterministicUsefulnessReranker`; `tests/unit/test_retrieval_contracts.py`; `tests/unit/test_retrieval_usefulness.py` | Implemented locally for strict diagnostic/selection primitives and neutral handling of NaN, infinity, or nonnumeric lexical scores. Authorization, temporal, and admissibility boundaries remain ahead of relevance |

The sweep is focused local engineering evidence. Full pytest, hosted CI,
exact-artifact/client/provider acceptance, live/private data, publication,
release acceptance, and macOS execution remain unclaimed.

### 2026-08-22 Sources/Context dashboard reconciliation

The accepted dashboard now maps the integrated contracts without flattening
state:

- `apps/dashboard/src/types.ts` and `api.ts` define defensive import, exact
  closed-coverage, context coverage, and selected-truth shapes. Record and
  truth values are constructed field-by-field with bounded primitives/enums;
  malformed detail envelopes fail content-free, malformed list rows are
  omitted, and unknown or missing legacy metadata is visible as unavailable
  rather than fabricated accounting. Import IDs and displayed statistics
  accept only valid bounded nonnegative integers.
- `apps/dashboard/src/App.tsx` and `styles.css` preserve the accepted desktop /
  mobile layout and existing actions while separating source item accounting
  from terminal processing, rendering real Core coverage, and showing the
  selected truth status, conflict, provenance, evidence, and history.
- `apps/dashboard/src/api.test.ts` and `App.test.tsx` provide focused evidence
  for normalization, terminal/item split, coverage failure fallback and
  recovery, malformed-wire filtering, honest count handling, no-N+1 truth
  selection, stale-response protection, mutation refresh, and rendered
  statuses. A failed coverage refresh clears cached truth metrics while
  preserving the independent search result counts. The dashboard suite is 54
  tests across 2 files.

This is a local-only beta.6 review candidate. Fresh independent API/DOM review
accepted the exact hardening commit. Synthetic loopback browser checks do not
inspect real exports or live/private Core data and do not constitute release or
fresh visual Product Design acceptance; visual acceptance remains pending.

### 2026-08-22 Continuous Capture foundation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Provider-neutral capture contracts and bounded ledger | `capture.py`; migration `015_continuous_capture.sql`; `docs/protocols/CAPTURE.md`; `tests/unit/test_capture.py` | Implemented locally for source/checkpoint/event/item/run storage, canonical marker-present repair, exact schema constraints/indexes, lifecycle transitions, typed lease capability authority, ordered page replay, stable lineage, canonical errors, lag/backoff/lease telemetry, and content-free projections. No real provider, network, OAuth, scheduler, dashboard, or package-startup behavior is implemented or claimed |
| Exact stage/apply/commit replay semantics | `CaptureCoordinator`; `CaptureApplicationSink`; deterministic fake adapter/sink; crash-before-commit replay tests | Implemented locally: durable staging precedes the injected idempotent sink; receipt, item mapping, and event checkpoint commit atomically; duplicate replay is a no-op; failed/out-of-order/gap events do not advance the checkpoint. Full snapshot/rescan deletion is deferred |
| Authenticated content-free admin API and CLI | `/v1/admin/capture/*`; `atc capture ...`; API/CLI tests | Implemented locally with existing admin authentication and loopback defaults. Cursors, payloads, credential references, provider tokens, and raw errors are not exposed. If no adapter is registered, run fails safely with `capture_adapter_unavailable` and no network call |
| Current product/release availability | beta.6 status and identity docs retained; no dashboard/package startup edits | Independent security/correctness/API review accepted the local foundation only. Real provider availability, hosted CI, release acceptance, publication, live/private data review, and macOS acceptance remain outside scope |

### 2026-08-22 Continuous Capture adapter-availability ownership correction

| Requirement | Implementation/evidence | Status |
|---|---|---|
| A coordinator without an adapter cannot invalidate another coordinator's live leased run | `CaptureCoordinator._mark_unavailable`; `tests/unit/test_capture.py::test_missing_adapter_does_not_invalidate_live_run_on_shared_database` | Implemented locally: the adapter-missing probe atomically observes a future-expiring run before degrading; it preserves `reconciling`, retry/operator state, and content-free errors, and the owning coordinator can renew and finish. No provider, network, scheduler, or release behavior is claimed |

### 2026-08-22 provider-terminal Import Truth correction

Provider-shaped empty roots, zero-message conversations, and malformed provider
siblings now close exactly one logical terminal result. Known-provider empty
roots/conversations are `skipped`; identity-free provider-shaped empties and
malformed entries are `unparsed` and keep coverage incomplete. Provider
containers remain structural in raw ZIP accounting and are not double-counted.
The bounded parser carries explicit root versus root-array-item context beside
each streamed value, so an empty object or wrapper sibling is `unparsed` exactly
once while standalone known-provider empties and zero-message conversations
remain `skipped`. No full root is materialized and terminal context is not
filename-derived; malformed-entry coverage and completion remain invariant under
permutation across direct, path, and ZIP entrypoints.
Allowed neutral alternate JSON basenames establish ChatGPT attachment scanning
only after a valid bounded content signature and a successful complete iterator;
the signature buffer retains no JSON root. A valid provider-looking prefix with
trailing data or any later parse, depth, item, or byte-limit failure stays
generic, closes one `unparsed` logical item, and cannot enable provider-specific
attachment inventory or links. A malformed neutral sibling cannot poison a
separately valid named provider member. Focused tests cover all three terminal
cases, adversarial permutations, four alternate names, direct/path/ZIP parity,
bounded failure classes, and the negative neutral case. This is synthetic
engineering evidence only and grants no acceptance credit.

### 2026-08-22 bounded ordinary-JSON and exact-coverage correction

Direct bytes, filesystem paths, and ordinary ZIP JSON now share a strict
incremental bounded reader with explicit 512 MiB byte, 128 MiB item/document,
and 128-level quote/escape-aware nesting limits. Validate-then-consume keeps
trailing-data, malformed, depth, and recursion failures atomic with no partial
candidates. Empty ordinary JSON roots close as one skipped logical item across
direct/path/ZIP entrypoints, while provider containers stay structural and
semantic-item-driven. Provider raw classification uses the canonical and dated
conversation filenames plus the exact alternate-name/provider-context rules in
ADR-109; neutral malformed alternates remain ordinary rather than being
silently promoted. `CoverageReport` normalizes omitted/partial maps to the exact
seven-key zero-filled contract and rejects unknown or invalid counts. Focused
synthetic tests cover all five acceptance blockers; this change grants no
acceptance credit and does not inspect live/private data.

### 2026-08-22 final import terminal-partition correction

The import boundary now keeps malformed provider containers structural in the
raw ZIP audit while assigning their logical failure exactly once to the closed
seven-key map. Provider-memory/profile values rejected by bounded content
policy close as `skipped` logical items rather than leaving an all-zero
denominator. Standalone decoding is strict UTF-8, standalone CSV is supported
atomically through both public archive entrypoints, and ordinary JSON roots use
bounded two-pass validation/consumption without raw temporary artifacts.
Enumerated ZIP count, size, ratio, encryption, and path/depth rejections return
content-free member closure without reading rejected payloads; an unenumerable
ZIP returns a distinct archive-level failure with no fabricated member closure.
Focused synthetic regressions cover these equations and terminal contracts.
This change grants no acceptance credit and does not inspect live/private data.

### 2026-08-22 final import-truth contract correction

Declared JSON `.dat` members are validated atomically before candidate
publication, so malformed trailing bytes cannot leave partial candidates while
also incrementing `unparsed`. Packaged-provider acceptance uses the shared
`CoverageReport` validator for closed keys, strict bounded counts, and
completion consistency. Synthetic focused tests cover the malformed trailing
JSON regression, valid JSON/JSONL attachment paths, reconciler coercion,
unknown/bounded counts, and unavailable/duplicate/failed/unparsed completion
cases. This correction changes no memory, retrieval, dashboard, capture,
platform, workflow, release, or GitHub behavior.

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

### 2026-08-22 Memory Truth foundation

This slice adds a Core-owned canonical truth projection after records exist. It
is locally exercised by focused synthetic tests and does not change provider
extraction, retrieval/ranking, dashboard behavior, MCP tools, or release state.

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Canonical memory truth with provenance, reason, time, confidence, sensitivity, status, and conflict visibility | `models.py`; `CoreStore.get_memory_truth`; `GET /v1/context/truth/{record_id}`; `GET /v1/admin/memory-truth`; `tests/unit/test_memory_truth.py` | Implemented locally for current, tentative, superseded, conflicted, and deleted record views. Evidence exposes source/record links, decision metadata, effective/observed/recorded times, confidence, sensitivity, and bounded version history. Public detail remains authorization-first; admin surfaces include non-current records. A replayable append-only decision stream remains partial/deferred |
| Content-free source/observation accounting | `TruthCoverageOut`; `CoreStore.memory_truth_coverage`; `GET /v1/context/coverage`; status projection; focused coverage regression | Implemented locally for source, observation disposition, record status, conflict-group, ingestion completion, and unavailable-source counts. It intentionally does not claim that raw source bytes or provider extraction are complete |
| Stable reprocessing identity without deletion resurrection | migrations `010_memory_truth.sql` and `011_rebuild_tombstone_provenance.sql`; source-rebuild cutover; deletion-barrier regressions | Implemented locally: matching internal source-rebuild tombstones can reapply an untouched automatic archive record under the same ID; ordinary user tombstones block matching archive evidence from becoming current under any new ID; source-reference collisions with different values remain distinct |

### 2026-08-22 privacy ACL boundary repair

| Requirement | Implementation/evidence | Status |
|---|---|---|
| A winning replacement cannot expose new disjoint-private content through the old client ACL | `storage.py::_monotonic_security`; `tests/unit/test_automatic_context_policy.py::test_disjoint_replacement_and_reinforcement_keep_acl_boundaries`; focused temporary-database regression | Implemented locally: overlapping restrictions intersect, disjoint replacement restrictions follow the replacement content, omitted restrictions retain the existing boundary, and disjoint reinforcement retains the current boundary |
| Principal-scoped Memory Truth cannot expose prior canonical projection fields or linked observation evidence outside their ACL | `storage.py::_update_record_from_observation_tx`; `storage.py::_truth_evidence_acl_filter`; `CoreStore.get_memory_truth`; `tests/unit/test_memory_truth.py::test_truth_detail_filters_disjoint_canonical_and_evidence_acl` | Implemented locally: canonical authorization is checked first; a disjoint restrictive correction takes its content-bearing projection fields from the replacement observation; linked evidence is ACL-filtered before the bounded limit. Principal-less Core/local-admin linked history remains intentionally complete; unrestricted records retain their existing behavior |

This repair was validated only with six focused pytest nodeids (6 passed),
`python -m ruff check .`, `python -m mypy packages/allthecontext/src`, and
`git diff --check`. Full pytest, hosted CI, release or publication checks,
network/provider access, live/private data, and macOS work remain outside the
evidence boundary. The focused test environment still emits one unrelated
FastAPI/httpx deprecation warning.

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Frozen Python dependency audit | `pyproject.toml`; `uv.lock`; `scripts/dependency_audit.py`; ADR-091 | The local lock/audit contract remains documented. This integration did not run hosted CI or make release/security acceptance claims. |
| First usable V1 beta contract | `ROADMAP_TO_V1.md`; ADR-053/ADR-075/ADR-086/ADR-088/ADR-089/ADR-092/ADR-095/ADR-096; `acceptance_receipt.py`; both receipt-bundle templates; `STATUS.md`; this matrix | Release publication and exact candidate/client acceptance are outside this integration. The local source retains the Windows/Linux posture and the unsupported-macOS retention decision; no beta receipt is credited here. |
| Post-V1 zero-routine-friction platform direction | ADR-090; `product/ZERO_FRICTION_PLATFORM.md`; `product/ZERO_FRICTION_EXECUTION_PLAN.md`; `product/PRODUCT_REQUIREMENTS.md`; `protocols/CAPTURE.md` | Accepted product and execution direction only. Core remains authoritative; connected text remains inert untrusted data; authorization and lifecycle resolution precede derived work; capability levels prevent L0 integrations from claiming lifecycle hooks; correction, deletion, retention, and purge dependencies close future ATC influence. The public beta.6 and merged PR #73 capture foundation satisfy Phase 0 but grant no provider or lifecycle support. The next gates are capture-contract reconciliation, a disposable zero-dashboard harness, one real local source/reference-host slice, stable project identity, deterministic capsules, then graph shadow evaluation. No graph, connector, working-state, learned-retrieval, remote, or stable-SDK claim is credited until separately implemented and accepted |
| Cross-platform Core | `config.py`, `lifecycle.py`, `platform_compat.py`; platform/package smoke tests; retained `macos_acceptance_preflight.py`; ADR-086 | Public-beta floor: Windows 11 x86-64 and Ubuntu 24.04 LTS x86-64 GNOME with working Secret Service/GNOME Keyring; other Linux environments are experimental. macOS source, tests, and the historical preflight remain for portability, but macOS is unsupported and excluded from candidate assets, claims, and acceptance receipts. Exact supported-target clean-machine and final release-SHA receipts remain pending |
| Correct per-user data paths | `platformdirs` configuration and setup/package tests | Implemented |
| Loopback-only default | `CoreConfig`, server CLI, dashboard copy, security tests | Implemented |
| Research supplier provenance and isolation | `research/competitor-intake/memory-systems-intake.v1.json`; Wave 2 manifest; Hindsight provenance and skip receipts; ignored `research/vendor-cache`; adapter/intake/packaging guardrail tests | Official Hindsight source was temporarily cloned at pinned revision `fa69b5b`, statically reviewed, and removed; no supplier package, model, container, script, service, provider, credential, benchmark, copied source, or packaged dependency |
| Observation/disposition/current-context lifecycle | `models.py`, `memory_policy.py`, migration 005, storage transactions, evidence links | Implemented; full local suite and focused policy/storage/API regressions pass |
| Direct-secret pre-ledger boundary | `secret_boundary.py`, migration 008, Core/Relay pre-queue refusal, internal-store pre-write defense, opaque UUIDv4 receipts, startup/export/restore repair and compaction, adversarial byte-scan tests | Implemented locally for direct proposals, batches, corrections/errors, forget/reject/delete redaction, direct Core-store calls, Relay queues before proposal hashing, replay, diagnostics, SQLite/WAL/freelist/FTS, and encrypted export/restore. High-confidence vendor-token and credential-URI forms are covered without a broad entropy filter. Historical external backups and device remanence remain operator-retirement concerns; exact-candidate security acceptance is pending |
| Append-only policy-decision history | decision metadata on observations/current records plus audit/history surfaces | Partial: current metadata is inspectable, but every automatic policy transition is not yet represented as a replayable append-only event stream |
| One-time setup with no routine memory queue | automatic MCP submission plus dashboard Context default/Review removal | Present in the local source; exact packaged fresh-user browser proof is outside this integration. |
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
| Idempotent/resumable ingestion with atomic policy publication and coverage | `ingestion.py`, `importers.py`, staged observations, raw-first preservation, `finish_ingestion`; retry/resume/coverage tests | Implemented locally: raw bytes are authoritative before parsing; failure/cancellation retains an inert retry source; path parsing uses a reconstructed Core copy; parser-versioned replay publishes no duplicates. Public coverage preserves logical seven-key item accounting with path-specific provider-container/control-member denominator rules, intentional closure for empty generic members, and atomic ordinary JSON validation. The separate content-free ZIP member audit proves raw-member closure without double-counting containers. Source-level `source_terminal_reason` remains separate, so terminal events do not corrupt item totals. Operation-owned cancel/restart paths preserve sanitized prior counts, and ZIP diagnostics escape control characters. Exact-candidate interruption receipts remain pending |
| Generic JSON/JSONL/Markdown import | `importers.py`; importer/security tests | Implemented |
| Raw imports through 2,000,000,000 bytes | `config.py`, `import_boundary.py`, `boundary_canary.py`, `importers.py`, migration 007/009, `import_operations.py`, `source_blobs`/`source_blob_chunks`; database-volume preflight, raw-first preservation, bounded chunks, operation-authoritative unchanged-byte reprocess heartbeats isolated from source telemetry latency, bounded timestamp-only WAL liveness commits, parser-reclassification merge rebinding of operation `source_id` on complete/fail/cancel without complete-canonical downgrade or re-ingest, cancellable preserved-source reconstruction checkpoints with partial-copy cleanup, preserved-source retry trackers initialized at the already-committed byte boundary, boundary-canary-v2 JSONL-safe alignment, closed generic skipped/unparsed accounting, direct source-only telemetry, terminal source state, retry/cancel, migration, copy-integrity, and focused adversarial regressions | A-08 makes the inclusive boundary mandatory. Source-level boundary/recovery machinery, durable import operations, and the corrected deterministic canary contract are implemented. WSL2 first exposed operation delay from serialized source-plus-operation transactions; source now isolates operation-owned heartbeat authority. Exact candidate `4257e40` then reproduced 15/17 over-budget unchanged-byte heartbeat intervals on a qualified 4-vCPU/8-GiB Ubuntu QEMU target, with a 10.196354-second maximum despite correct import data and resource use. Unchanged-byte liveness now updates only the operation timestamp through a 250-ms, WAL-NORMAL telemetry writer; semantic progress and terminal state retain the original lifecycle writer, source-only behavior is unchanged, and non-lock SQLite failures propagate. Candidate `628797d` kept durable retry liveness within 4.936978 seconds but authenticated API receipt reached 5.735102 seconds while direct SQLite stayed within 3.731520 seconds. Operation-owned streaming JSONL parsing now yields one millisecond at existing one-MiB checkpoints so the observer and ASGI loop receive a scheduling turn; auth, durable state, source-only behavior, and response semantics are unchanged. Replacement candidate `7ffb1a4` passed the exact Windows straight/repeat slices but missed the strict cancel-ack deadline because preserved-source reconstruction checked no cancellation until its complete copy; source now checks after every at-most-8-MiB chunk and cleans a partial target on cancellation. Exact candidate source `65612cc` then exposed a no-upload retry progress regression: the preserved source was fully committed, but the first forced retry phase briefly wrote zero committed bytes/percent before restoring the declared size. Retry trackers now initialize their monotonic byte state and emission watermark at the preserved declared size, so the first durable phase cannot regress. A new immutable candidate must rerun the complete Windows journey. A new immutable Linux artifact must prove the frozen five-second gate and run interruption. Allocated non-sparse exact-boundary success on Windows x86-64 and frozen-target Linux x86-64, boundary-plus-one refusal, interruption, resource budgets, SHA, packaged export, and restore evidence also remain open |
| Full local ChatGPT/Claude/Grok history ingestion | `provider_ingestion.py`, `provider_shapes.py`, streaming ZIP/JSON adapters, versioned parser identities (`provider-archives-v2`), closed coverage, staged policy publication, `outcomes`/`record_ids` import response, dashboard provider flow, raw-first recovery, complete-source rebuild from the preserved blob, packaged `--packaged-provider-acceptance` control surface; ADR-069/ADR-088/ADR-097/ADR-099/ADR-100/ADR-106 | All three providers are mandatory under A-11. Frozen fictional shape sets and parser identities are implemented; every import exposes the strict seven-key item map with recognized, excluded, skipped, unavailable, duplicate, failed, and unparsed counts, while source-level failed/cancelled terminal status is separate. Extraction publishes specific durable kinds, requires durable preference evidence, and keeps task-local/adversarial instruction framing inert; sensitivity is classified conservatively. Complete-source rebuild withdraws uncorrected automatic records reversibly and re-extracts without destroying the raw blob or history. Known empty/tool/attachment ChatGPT graph shells now close into excluded/skipped/unavailable while unknown/malformed material remains unparsed and keeps coverage incomplete. Provider conversation wrappers, nested wrappers, and root conversation arrays account for every malformed or unknown entry as unparsed, retain valid siblings, emit content-free structural warnings, and keep `complete` false. Oversized ZIP text members close unavailable and malformed declared-text `.dat` attachments close unparsed only; attacker-controlled ZIP diagnostics are bounded/control-escaped. Packaged acceptance emits content-free stage codes for operation failure, non-complete operation status, and reconcile refusal; synthetic ZIP/classifiable-graph and unknown-residual regressions cover the former ambiguous fail-closed path. ADR-088 binds `CoreService` as a context manager so `CoreStore.close()` always precedes owned-vault `rmtree` on success and exception paths; an `OSError` after close still yields `data_dir_cleanup_failed` / exit 1, and caller-supplied data-dir deletion is unchanged. Privacy-safe nonempty real exports acquired after parser freeze and within 30 days, execution of all three against exact downloaded candidates, and inventory-bound receipts remain open and block beta if any one is missing |
| Bounded ChatGPT attachment inventory and text slice | `importers.py`; synthetic ZIP regressions in `tests/unit/test_provider_ingestion.py`; ADR-105; `docs/protocols/INGESTION.md` | Implemented locally for gated `.dat` identity/hash/raw preservation, manifest/filename/MIME provenance, explicit MIME ambiguity status, unique archive-member identity, exact conversation/message link pairs, strict ZIP bounds, bounded 10,000-pair/64-level/10,000-node linkage scans, and supported text formats `.txt`, `.json`, `.jsonl`, `.csv`, `.md`, `.markdown`. Unsupported binary/document/web/script formats remain explicitly unavailable. This does not claim all `.dat` contents are searchable, does not provide office/PDF/media extraction, and has no real-export acceptance receipt; only structural real-export inspection was performed content-free |
| Structured filtering and FTS5 | retrieval engine; policy-before-ranking and integration tests | Implemented |
| Retrieval usefulness and bounded context packs | `retrieval.py` deterministic query-intent/usefulness rerank; `ContextCompiler`; additive `ContextPackMetadata`; `bench/retrieval_usefulness.py`; synthetic fixture/tests; ADR-116 | Implemented locally: default V3 reranking stays after authorization, temporal, and admissibility gates; ranking combines bounded lexical/query-field coverage with recency, confidence, availability, sensitivity, conflict, provenance, and actionability; bootstrap enforces a 32-record cap and exact character budget; provider-facing pack metadata reports omissions and truthful truncation reasons; 17-case isolated scorecard passes all local gates. No learned retrieval, live-data claim, or release/client/provider acceptance credit |
| Request-bound context-search pagination | `SearchRequest`, `SearchCursor`, Core `/v1/context/search`, per-installation HMAC cursor signing, `tests/integration/test_core_api.py`, ADR-103 | Implemented locally; malformed, negative, oversized, bounded, normal-page, query/filter/page-size mismatch, and cross-principal cursor cases are covered by API tests. The cursor is integrity-authenticated but not encrypted, one-time-use, expiry-bound, or snapshot-consistent |
| Future embedding boundary | shadow-retriever contract plus disabled, rebuild-only 384d exact-scan experiment outside package discovery | Defined; no production embedding dependency or authority |
| Required MCP tools | `mcp_adapter.py`; MCP SDK v2 `MCPServer` and public transport runners; `observed_at` input, automatic disposition/record/reason/time/version output, and explicit reversible `forget_context`; STDIO/Streamable HTTP/OAuth contract tests | Implemented locally; MCP v2 contract, actual unknown-argument rejection, legacy 2025-era handshake, managed STDIO restart, correction, queued-forget, PKCE refresh/revocation, bearer, Core-authority, and the shared 256 KiB hosted Edge request boundary pass focused tests. Ordinary MCP remains L0; lifecycle-aware L1-L3 hooks are not claimed |
| First-party HTTP transport | `httpx2>=2.12,<3`; `http_client.py`, `edge_connection.py`, `edge_acceptance.py`, `sync.py`, `replication.py`, packaged first-run smoke; HTTPX2 exception/trust-store and response-bound regressions | Implemented locally and locked at HTTPX2 2.12.0 with httpcore2/truststore. Production has no legacy `httpx` dependency; old `httpx` is development-only for Starlette `TestClient`. Packaged smoke responses are streamed under a 1 MiB ceiling with content-free refusal. The combined 74-test MCP/HTTP matrix passes locally; hosted Windows/Linux CI has not yet run for this branch |
| One-time local app connection | `client_config.py`, setup wizard, dashboard; Codex/Claude classic and Windows Store/MSIX detection/config tests; autouse disposable client-config/keyring fixture | Implemented locally; this Windows Store install resolves to its package-local roaming config. Installed Codex CLI 0.144.0 accepted the managed STDIO shape and recovered a stopped disposable Core. Tests now fail away from real Codex/Claude config and credential roots; persistent-profile and Claude signed-in exact-artifact receipts remain open |
| Optional administration UI, no memory inbox | `apps/dashboard`; Review route/forms removed, Context default, Activity/provenance, durable import-operation flow, context and source delete/undo, version restore, source rebuild, Context total/pagination/kind/sensitivity/confidence filters without auto-select; search-wrapper `:focus-within` amber outline (ADR-089) | Source now has a focus-dependent search-wrapper indicator, a Python source regression that requires a nonzero non-none wrapper indicator, and a dashboard test that keeps the existing sr-only accessible name. Context search uses the API `total` and cursor pagination instead of a hidden 100-row cap. A static search border is not treated as focus. BETA-P06 has not passed; exact packaged Edge keyboard/focus/error/narrow-width acceptance remains open. Deliberate purge is available via the packaged recovery/admin helper; exact downloaded-artifact administrator receipts remain open |
| Approval-free reproducible demo | `scripts/demo.py`, `tests/e2e/test_demo.py`; automatic finish-to-retrieve, restart, correction/delete, revocation, encrypted restore | Present in the local source; this integration did not run hosted matrices or final release-SHA replay. |
| Portable export/restore | encrypted export/dashboard download, contributor CLI restore tests, packaged recovery/admin helper/mode (`recovery_admin.py`, Windows helper, Linux console main binary); retained Mac helper code; separately hashed source-chunk entries with complete-source reconstruction checks | Existing round trips and packaging integration pass. Exact downloaded-artifact stopped-Core restore/purge receipts on supported Windows and Linux remain open (BETA-D03); macOS is outside the release scope |
| Locking, shutdown, restart | lifecycle locks, managed adapter self-heal with one bounded 30-second one-file startup window, packaged first-run smoke; idempotent `CoreStore.close()` / `CoreService` context manager (ADR-088) | Implemented in local source; focused verification is scoped to this integration and no hosted, exact-artifact, or release-SHA acceptance is claimed. |
| OS credential abstraction | `credentials.py`, transactional desktop/client configuration, keyring acceptance script, platform fault-injection tests; packaged first-run smoke uses explicit isolated development-file credentials only | Normal setup fails closed without protected OS storage; plaintext development files require deliberate opt-in; managed configs omit bearer tokens when the OS store is used; failed storage/config writes revoke new principals, remove credentials, and restore prior config bytes. Packaged first-run smoke asserts the isolated development store and does not stand in for real OS credential acceptance; failure diagnostics are content-free and the disposable work tree is always removed. Exact-package real Windows Credential Manager and supported Linux Secret Service receipts remain pending; the Mac adapter is retained but unsupported |
| Safe Core response sinks | browser handoff data-attribute encoding, constant nonce-protected handoff script, inert acceptance parser, sanitized integration configuration failures, focused security regressions; ADR-064/ADR-070 | Product responses are implemented locally: request-derived dashboard targets and browser capabilities are data rather than executable JavaScript, and integration parser exceptions cannot disclose raw paths, credentials, or personal configuration material. The acceptance extractor binds the handoff nonce to the exact response CSP and rejects external `src`, extra executable markup, inert/ambiguous handoffs, and non-production storage/targets; focused adversarial regressions pass |
| Exact browser handoff and dashboard hygiene | packaged same-origin SVG favicon, bundled-serving regression, real browser P06/S05 receipt; ADR-009/ADR-064/ADR-070 | Source correction implemented after an exact Windows Edge handoff exposed the implicit `/favicon.ico` JSON 404. A clean committed local Windows package passed the focused real Edge P06/S05 replay with zero unexpected console/page errors, no external request, and the packaged favicon. Independent parser hardening is integrated and its exact production handoff/CSP probe passes. A rebuilt official downloaded release candidate remains required. BETA-S05 follows the frozen expiry/non-replay/referrer/cache/current-navigation/session termination/revocation boundary and does not impose forensic byte erasure on an already consumed ticket |
| Cross-platform source CI | `.github/workflows/ci.yml` source, dashboard, and supported native-package matrices; retained `macos_acceptance_preflight.py` as historical/source portability code | The hosted matrix covers only supported Windows and Ubuntu runners. The three Mac job contexts and ordinary Mac preflight were removed; retained Mac source and historical evidence create no support claim or receipt. Final release-SHA source health and supported clean-machine receipts remain pending |
| Supported desktop packaging | Windows installer and Linux portable archive; version-matched recovery helper/mode; bounded native-tool failure diagnostics and a single 30-second managed-Core startup window; packaged first-run smoke startup-key cleanup (`remove_smoke_windows_startup_key`); retained Mac packaging code under ADR-086 | Windows and Linux packaging/recovery surfaces are integrated. The official candidate matrix, inventory, release notes, and publication verifier accept only Windows x86-64 and Linux x86-64. Mac app/DMG code and historical packaging evidence remain in source but create no consumer asset or support evidence. Exact supported release artifacts and real-machine/downloaded-artifact receipts remain pending |
| Signed community updates | Ed25519 manifest/keyring, update-state, rollback, and content-free verification code | Local update surfaces remain in source, but this integration did not validate publication, hosted workflows, channel promotion, or exact downloaded artifacts. |
| Stable release trust/channel path | candidate workflow accepts stable versions | Open: key selection, site builder, client endpoint, publish/promotion workflow, migration rehearsal, backup, and recovery remain beta-specific or absent |
| Exact-SHA reproducible candidate composition | `exact_source_gate.py` (canonical `.github/workflows/ci.yml` only; current eight required jobs with bound run_id/head_sha; primitive matrix-evidence recompute), `release_candidate.py` inventory schema + required checksum sidecars, authenticated release-list/numeric-ID draft resolver, receipt inventory-declared digests, dependency-closed locked install/parity scripts, checksums, provenance, SPDX SBOM, ADR-059/ADR-068/ADR-096 adversarial tests | The published beta.6 source historically passed its canonical 11-job CI and three-job CodeQL sets; the current exact-source contract has eight required CI jobs after removing the three unsupported Mac contexts. Draft operations still require numeric REST asset IDs. Published-state validation safely tolerates opaque `gh release view` GraphQL IDs without treating them as REST authority. Broader certification receipts remain separate from this closed lean publication identity |
| Repository and release security baseline | local dependency/security configuration and source-side guardrails; ADR-065/ADR-080/ADR-087 | Hosted repository settings, CI, CodeQL, release environments, and exact candidate scans were not revalidated in this integration and receive no acceptance credit here. |
| Public support and launch-watch sequencing | `SUPPORT.md`, `docs/KNOWN_ISSUES.md`, `SECURITY.md`, `docs/operations/RUNBOOK.md`, README links, ADR-075/ADR-092 | Local support and safety documentation remain in scope; publication, channel/download smoke, launch-watch, and exact-client acceptance are outside this integration. |
| No third-party V1 runtime | no Edge UI/onboarding/status call/background worker; Edge publication workflow and Render templates removed; ordinary Core Edge/Relay operation routes and CLI commands removed or tombstoned; residual cleanup isolated under legacy-edge surfaces only | Implemented for the supported Core product surface (`BETA-S04`/`B-103`); exact packaged candidate matrix proof and publication remain open |
| Direct-Core mobile model | integration API/dashboard/architecture state Core-online requirement | Explicitly post-V1: the first usable beta is same-device only and has no supported pairing/transport/client acceptance claim |
| No automatic public exposure | loopback default; dashboard warning; acceptance gate | Implemented |
| Legacy `always_available` compatibility | schema and old records retained; new applied context uses `core_available`/`local_only` and labels old records legacy | Implemented |
| Legacy review-data migration | migration 005 maps approved/rejected to applied/ignored and startup reevaluates eligible staged rows under `automatic-v1` | Implemented; partial-migration restart, pre-v5 duplicate restore, and idempotency regressions pass locally |
| Remote Edge scoped forwarding authorization | experimental `edge_connection.py` compatibility path enforces Core-approved `context_scopes` on direct fetch, search, and bootstrap records and scrubs filtered aggregates; bootstrap metadata is reconciled to final items with strict count invariants and bounded Core-selection suppression aggregates; `tests/security/test_edge_forwarding.py` covers empty, wildcard, matching, out-of-scope, filter, and envelope paths | Implemented as defense in depth on residual experimental code; ordinary Core product routes that would invoke it are removed/tombstoned by B-103; focused synthetic correctness evidence only |
| Relay remains queue/projection only | Relay MCP returns staged receipts; Core evaluates dequeued observations; signed record events originate at Core | Authority tests pass; ordinary Core CLI no longer exposes `sync` or `serve-relay`; Relay modules remain for residual/compatibility tests only |
| Legacy Edge cleanup without normal operation | isolated `/v1/admin/legacy-edge` and `atc legacy-edge` status/decommission/forget; no automatic worker; decommission refuses when no residual paired Edge exists | Implemented with negative API/CLI/process/network proofs; exact packaged candidate artifact proof remains open |
| Frozen Retrieval V2 comparator | `retrieval_contracts.py`, pinned fixture hashes/ranking fingerprints, foundation harness | Implemented; comparator identity `70a4808` |
| Applied/current policy before time/relevance | authorization-only selector, current-record eligibility, temporal IDs, ranker-candidate-scoped FTS, boundary tests | Baseline and automatic-disposition migration/isolation verification are integrated; final candidate replay pending |
| Current and `as_of` retrieval | UTC interval sidecar, request/MCP/CLI fields, DST/offset/restart tests; Core catalog search requires `context:read` before returning current or historical content while non-content status remains independently gated by `context:status`; exact post-policy totals and cursor pages remain separate from bounded bootstrap evidence retrieval | Implemented locally; focused current-worktree regressions cover synthetic >100-match exact-total/page, authorization/filter-isolation, status-only monitoring, and search denial. No historical three-OS or hosted result is reasserted here. |
| Deletion/purge resurrection barrier | authoritative terminal facts, purge tombstones, stale-sidecar recovery, pre-removal export restore test | Covered by local focused regressions; no historical three-OS or hosted result is reasserted for this checkout. |
| Weighted bounded FTS5 | `lexical_v3.py`; weighted columns, bounded evidence search, complete authorized catalog enumeration under the 50,000-candidate hard cap, exact/OR/prefix caps, Unicode/case/tokenizer and secure-delete tests | Implemented locally; catalog search is exact over the post-policy set while bootstrap/context compilation retains the 100-record evidence bound |
| Task admissibility | deterministic numeric factor gate after hard policy/time, fail-open sparse evidence, shadow-only learned interface | Implemented locally; bounded precision improves without exact Recall@5 loss |
| Safe retrieval diagnostics | closed reason codes and numeric/boolean aggregates; admin-only returned-ID explanations | Implemented; content/unauthorized-ID exclusion tests |
| Retrieval V3 benchmark gate | foundation fixtures plus integrated 1k/10k quality, latency, storage, migration/restart/restore checks; bounded failed-gate report; ADR-083 evidence boundary | Production CLI remains fail-closed at 10k warm p95 below 150 ms on comparable hardware. Shared-host 100-record pytest is functional evidence only; deterministic tests enforce the unchanged threshold and reject invalid/missing/mixed-profile latency evidence. Earlier source/tests/packages were observed on three OSes at `67dd11c`; a new comparable-hardware CLI run is required for current latency evidence |
| Set-level marginal context selection | `set_selection.py`, `ContextCompiler` wiring, compatibility/diversity/conflict/support/mandatory/budget fixtures, `tests/unit/test_retrieval_high_cardinality.py` | Implemented locally; standalone set-selection gates and combined semantic coverage remain passing; high-cardinality compiler regression preserves feasible primary results and bounded preferences across 77 preferences, 20 relevant records, ten generic queries, a 4,000-character budget, no-match, tight-budget, ACL/temporal/sensitivity, exact-accounting, caller-ranked duplicate relevant records, fixed mandatory survivor/conflict authority with exact prepass/final identity, alternate-primary evidence support, 1007/905 evidence-over-overflow boundaries, large-evidence ordering, infeasible-evidence fallback, and preference-input permutation cases |
| Synthetic retrieval usefulness eval | ADR-116; `bench/retrieval_usefulness.py`; sanitized fixture; isolated public-API vault; scorecard baseline; `tests/unit/test_retrieval_usefulness.py`; `tests/unit/test_retrieval_high_cardinality.py` | Developer-facing only: synthetic usefulness cases plus the bounded high-cardinality regression cover current facts, stale/conflict/withdrawn exclusion, sensitivity, provenance, budget, provider packaging, preference starvation, fixed-slot authority and duplicate survivors, alternate-primary overflow support, applicable evidence ordering and evidence-over-overflow boundaries, infeasible-evidence fallback, no-match reserve behavior, and exact disjoint/overlapping bounded-pool metadata unions. The harness refuses live Core data dirs and grants no release or client acceptance credit. Production ranking, ingestion, schema, MCP, and dashboard Context are unchanged |
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

| Memory Truth final review blockers | migrations 010/011/012/013/014; `storage.py`; `models.py`; `importers.py`; `export.py`; `recovery_admin.py`; Memory Truth/storage/export/recovery/Core API/provider tests; ADR-112/ADR-113/ADR-114/ADR-115 and the restore-boundary decision | Implemented locally in this integration: comment-aware restart-safe migration recovery; unbound/portable rebuild provenance downgraded to ordinary deletion barriers; public withdrawal fail-closed; trusted tombstones bound to the exact finished archive session, generation, and source marker with source/session/accessibility, metadata, stable-key, hash/version, and explicit local-mutation checks before reapply; schema-14 typed canonical user-action evidence and typed portable-ledger validation, with generic record-version rows retained only for explicit legacy compatibility; startup/migrate repair of any missing schema-014 typed-action columns and its typed-action unique index when migration 014 is already recorded; destination-local isolated-restore carry-forward; source-typed legacy inference; actual-insert restore/carry-forward counts; and already-current restore barriers; approval overrides recompute candidate and canonical keys from final identity-bearing values; and truth pagination uses SQL page/count selection plus page-scoped, row-limited set prefetch without read-time integrity rebuilds. Focused checks are local and scoped; no full-suite, hosted-CI, live-Core, private-export, or final-acceptance claim is made here. |

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
