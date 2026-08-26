# Requirements traceability

"Implemented" means exercised locally. This integrated traceability matrix is
for the local Import → Memory Truth → Retrieval → Context UI baseline and the
separately bounded Wave 3 component handoffs; it does not claim hosted CI,
release publication, exact artifact/client/provider acceptance, or live/private
data inspection. Earlier evidence is retained only as historical context and
does not become evidence for this checkout.

### 2026-08-25 Milestone 5 graph foundation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| ZF-013 — bounded typed project graph | project_graph.py; tests/unit/test_project_graph.py | Implemented locally as an ephemeral single-project projection over caller-authorized temporal relation evidence. Six explicit/structural families, deterministic revision/receipts, cycle rejection, fan-out/node/edge/input caps, and bounded one-/two-hop expansion preserve direct provenance/dependency lineage. No persistence, prose parsing, model inference, runtime/API/UI wiring, provider/client, private-data, package, release, or hosted acceptance claim |
| ZF-013 — independent adversarial graph safety matrix and reusable oracle | `bench/zf013_graph_adversarial_fixtures.json`, `bench/zf013_graph_adversarial.py`, `tests/unit/test_zf013_graph_adversarial.py`, `docs/research/ATC_ZF013_GRAPH_ADVERSARIAL_ORACLE.md` | Independent sanitized contract complete locally: 14 cases and six exact observable dimensions cover authorization-first noninterference, cross-project isolation, ambiguity abstention, correction/supersession, `as_of`, delete/purge closure, stale dependencies, illegal topology, bounded expansion, deterministic rebuild, and untrusted-text inertness. The standalone oracle is not product proof; actual-implementation conformance remains required before promotion |

### 2026-08-25 integrated Milestones 1–3 (current checkout)

| Requirement | Implementation/evidence | Status |
|---|---|---|
| ZF-007 — explicit Core-owned Continuous Context scheduling and controls | `capture_scheduler.py`, Core admin scheduler routes, `desktop_setup.py`, Sources dashboard; `tests/unit/test_capture_scheduler_productization.py`, `tests/unit/test_desktop_setup.py`, dashboard API/DOM tests | Implemented locally: scheduler is disabled by default; dispatch requires the process gate, durable enablement, and no update-health override; desktop launch opens only the process gate; authenticated Sources controls expose bounded connect, enable, pause/resume, run-now, revoke, and automatic-sync state. No live provider acceptance is claimed |
| ZF-008 — stable local workspace identity and authorization | `capture_runtime.py`, `POST /v1/admin/capture/workspaces/authorize`, `tests/unit/test_capture_runtime.py` | Implemented locally: loopback Core plus authenticated admin authorization requires an absolute root and explicit local-only acknowledgement, creates one disabled identity-bound workspace source, fails closed for unsafe/mismatched roots and malformed inventory, and keeps repeated same-root authorization idempotent. Adapter refresh is deferred to the run boundary; no live provider or private-workspace acceptance is claimed |
| ZF-011 — stable project identity and discovery | `project_continuity.py`, `project_runtime.py`; `tests/unit/test_project_continuity.py`, `tests/unit/test_project_runtime.py` | Implemented locally as a bounded Core-derived projection: opaque project IDs, exact project scopes, one-anchor provider lineage, resolved/unresolved/ambiguous outcomes, cross-project isolation, and abstention on ambiguity. Graph discovery and learned assignment remain outside this milestone |
| ZF-012 — Project Context Capsule compiler | `project_continuity.py`, `project_runtime.py`; authenticated bootstrap plus optional admin routes; `tests/unit/test_project_continuity.py`, `tests/unit/test_project_runtime.py`, `tests/integration/test_mcp_stdio.py` | Implemented locally: current/lifecycle-eligible evidence is filtered before selection; items carry authority and provenance; default 12,000-character/32-item budgets report exact omissions and truncation; `optimized_rebuild` equals the full rebuild oracle after restart/reordering; an authorized client entering the sole or uniquely named resolved project receives the derived capsule automatically without opening ATC; ambiguity abstains |
| ZF-013 — project graph in Memory Lab | `bench/zf013_project_graph_contract.json`, `bench/zf013_project_graph_fixtures.json`, `bench/zf013_project_graph_benchmark.py`, `tests/unit/test_zf013_project_graph_benchmark.py`, `docs/research/ZF013_PROJECT_GRAPH_EVALUATION.md` | Lane A harness self-test only: frozen sanitized comparison of a stdlib lexical proxy, structured project filters, deterministic capsules, lexical typed one-hop, bounded two-hop, and six synthetic integration-hypothesis ablations. Eligible endpoints are normalized before ordering, accounting, traversal, timing, or receipts; illegal self/cycle edges are rejected. Local output explicitly marks production Retrieval V3 `not_exercised`, uses `harness_self_test_passed`/`harness_self_test_failed`, and validates finite explicit decisions fail-closed. No graph store, graph runtime, learned relation, production expansion, promotion evidence, or live usefulness claim is implemented or credited; ZF-013 remains open |
| ZF-014 — optional project inspection | `ProjectContinuity.tsx`, dashboard project API/types and tests | Implemented locally as a bounded read-only Project Continuity dashboard: resolved projects, current capsule sections, item/character budgets, and omission accounting are rendered; unresolved/ambiguous projects are excluded. No force-directed graph or graph acceptance is claimed |
| A-11 — platform and provider scope | `ROADMAP_TO_V1.md`; provider importer/parser tests and current Milestone 3 provider/runtime tests | Supported source/package targets for this checkout are Windows and supported Linux. macOS source, tests, and historical preflight/packaging code remain retained for portability and maintenance only; macOS is unsupported and creates no package, CI, release, provider/client, acceptance, or support claim. ChatGPT, Claude, and Grok remain parser/source targets in code and synthetic tests only; no live provider acceptance is claimed |
| 529 — applied/current policy before time/relevance | `EligibleRecordSelector`, temporal sidecar, retrieval policy/lifecycle tests | Implemented locally: authorization and current/applied lifecycle eligibility precede temporal resolution and relevance; staged, tentative, ignored, deleted, and purged content cannot become ranked current context |
| 530 — current and `as_of` retrieval | Retrieval V3 current/temporal API and focused current-worktree retrieval tests | Implemented locally: current and offset-aware `as_of` paths remain authorization-first with exact post-policy catalog totals; bounded bootstrap accounting is separate. No historical three-platform or hosted result is reasserted |
| 532 — weighted bounded FTS5 | `lexical_v3.py`, retrieval contracts, bootstrap composition tests | Implemented locally: catalog search is exact over the post-policy set within the 50,000-authorized-ID hard cap; bounded bootstrap uses its 100-record evidence pool, with content-only coverage and metadata-only exclusion |
| 533 — task admissibility | `admissibility.py`, `content_evidence.py`, `lexical_v3.py`; retrieval precision/bootstrap tests | Implemented locally: every nonempty multi-term direct request retains the strict `0.75` content floor; bootstrap uses the separate one-anchor path; aliases count only as mapped anchors and kind/tag/scope/project metadata cannot satisfy topical content coverage |
| 535 — Retrieval V3 benchmark gate | `docs/evidence/RETRIEVAL_PRECISION_M3_BASELINE.md`, `docs/evidence/RETRIEVAL_M3_CURRENT_CANDIDATE.md`, content-free evaluator tests | Provisional local evaluation only: current synthetic evaluator is 10/10 on production and content-free; historical five-case and 17-case scorecards pass. The reported aggregate is not committed evidence, a live acceptance receipt, or a release gate |
| 536 — set-level marginal context selection | `set_selection.py`, `ContextCompiler`, `tests/unit/test_retrieval_high_cardinality.py`, `tests/unit/test_retrieval_bootstrap_composition.py` | Implemented locally: bootstrap relevant records must provide a complete topical set union; incomplete relevant tiers abstain rather than return partial context, while exact budget/omission accounting remains bounded and deterministic |
| 537 — synthetic retrieval usefulness evaluation | `bench/retrieval_usefulness.py`, sanitized fixture, isolated public-API vault, `tests/unit/test_retrieval_usefulness.py` | Developer-facing local evaluation only: the 17-case scorecard remains passing across current-fact, lifecycle, sensitivity, provenance, budget, and provider-shape gates; the harness refuses live Core data and grants no provider/client/release credit |

### 2026-08-26 Milestone 4 ambient project activation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| M4-01 — healthy project use requires no ATC UI | background `RuntimeCommand.core()` startup contract; MCP bootstrap instructions; `tests/unit/test_user_startup.py`, `tests/unit/test_mcp_contract.py`, `tests/integration/test_mcp_stdio.py` | Implemented locally: routine startup remains Core-only, MCP tells the host not to ask the user to open/manage ATC, and the real STDIO journey activates and returns one project capsule without a dashboard request. Setup, inspection, correction, and recovery UI remains optional |
| M4-02 — automatic activation respects authority and ambiguity | principal-filtered `build_project_runtime`; `activate_project_context`; authenticated `/v1/context/bootstrap`; `tests/unit/test_project_runtime.py`, `tests/unit/test_mcp_contract.py` | Implemented locally: explicit opaque/label/scope signals win; a compatible MCP roots backchannel may then contribute exactly one safe display name as a weaker hint, never a URI or path; one unique task label and then the sole authorized content-bearing project provide bounded fallbacks. Unauthorized anchors are removed before resolution and every invalid, missing, or ambiguous case abstains without project content |
| M4-03 — project context and retrieval remain bounded and available | Core bootstrap budget split, content-free activation audit, bounded projection-error fallback; focused project-runtime and MCP tests | Implemented locally: project context receives at most half the caller character budget, retrieval receives the remainder, `total_used_chars` stays within the request without changing retrieval-only `used_chars`, and project projection failure cannot take ordinary authorized retrieval offline. No default scanning, capture enablement, provider lifecycle, remote Edge, package, or release claim is made |

### 2026-08-25 bounded PR #88 lab reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Current-production benchmark snapshots reflect accepted strict retrieval semantics | `tests/unit/test_lexical_v3_benchmark.py`; `tests/unit/test_memory_lab.py`; `tests/unit/test_memory_lab_b01.py`; regenerated `bench/reports/memory_lab_baseline_ladder_wave2.*` and `bench/reports/memory_lab_b01_wave3.*` | Reconciled locally: lexical current Recall@5 `0.611111` and MRR `0.666667` remain below the unchanged V1 gates; M0 ATC is `0.6` success/recall with zero forbidden output; B01 ATC confirmatory CAOS is `0.285714`, while its fixture/config, boundary, accounting, and kill decision remain unchanged |
| Historical reliability reports are not current-production acceptance baselines | `tests/unit/test_memory_reliability_lab_e01b.py`; `tests/unit/test_memory_reliability_lab_e02_wave4.py`; immutable `bench/reports/memory_reliability_e01b_wave3.json` and `bench/reports/memory_reliability_e02_wave4.json` | Reconciled locally: exact assertions validate each report's recorded frozen base and preserve E01b's six unsupported/not-exercised semantics and E02's five `UNSUPPORTED` plus one `NOT_EXERCISED`; current disposable runs remain content-free boundary checks |
| Historical M3 retrieval precision baseline remains immutable | `bench/baselines/retrieval_precision_m3_f5e3a2b.json`; `docs/evidence/RETRIEVAL_PRECISION_M3_BASELINE.md` | Preserved byte-for-byte; no current-production lab result is written into or compared as a replacement for the historical snapshot |

Evidence is limited to the focused local benchmark/lab tests and generated
content-free reports described above. Full pytest, network, private data,
production retrieval changes, push/merge/release/publish actions remain out of
scope.

Evidence is aggregate local evaluation only, over sanitized synthetic or disposable local state. Repository documentation may record aggregate scores/counts, pass/fail results, test node IDs, fixture revisions, and evidence-boundary facts. Do not commit raw exports, workspace files, personal context, credentials, database files, per-record traces, generated reports, or other evaluation artifacts. No local result implies live provider, client, platform, release, or private-data acceptance.

### 2026-08-25 Milestone 5 lane D — ZF-017 through ZF-019 shadow foundation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| ZF-017 — observable outcome receipts | `packages/allthecontext/src/allthecontext/memory_lab_outcome_shadow.py`; `tests/unit/test_memory_lab_outcome_shadow.py` | Implemented as a bounded pure in-memory contract: assignment, exact project/projection versions, acknowledgement, declared use/nonuse, bounded action/tool envelopes, completion, typed external result/user correction, invalidation dependencies, idempotency, correction invalidation, terminal purge closure, ASCII machine-token validation, and duplicate-identity rejection are covered by 33 focused tests. No storage, Core route, MCP, dashboard, capture, scheduler, or retrieval wiring is present |
| ZF-018 — background consolidation in shadow | `propose_procedure` in `memory_lab_outcome_shadow.py`; focused shadow tests | Implemented only as deterministic advisory consolidation over sanitized typed receipt facts. Matching action signatures, recurrence across distinct task IDs, duplicate/conflict rejection, strong external verification, and lifecycle filtering are evaluated without a model, network, live data, provider, or production behavior |
| ZF-019 — procedural-memory gates | `ApplicabilityBoundary`, `RepairTest`, `PurgeClosure`, `ProcedureProposal`, `LearningDecision`; focused shadow tests | Implemented as fail-closed proposal gates requiring recurrence or strong external verification, explicit applicability, negative guards, passing repair tests, influence dependencies (project, projection, memory, and source) plus outcome dependencies, and closed purge coverage. Every result remains `advisory_only`; no learned authority, automatic truth write, or promotion path exists |

This lane is local research evidence only. Full-suite, hosted, release,
packaging, client/provider, private/live-data, network, and macOS acceptance
remain outside the evidence boundary.

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
| Bind continuous Packet F evidence to deterministic source-fact promotion through Core | Stopped/removed Packet H disposable proof at the 2026-08-22 head; ADR-133 / PR #78 `registered_source_admission.py`; archive importer lifecycle remains separate | Historical 2026-08-22: the proof was open at the contract boundary and was stopped. PR #78 later merged the local registered-source admission seam. The later Packet H-D lane is a foreground disposable proof only. Later Packet E x Packet F scheduled composition evidence exists separately and still does not close ZF-007/ZF-008 product exit; continuous/scheduled Packet F acceptance remains open |
| Advance to the next narrow frontier without overstating acceptance | ADR-132 (historical stop), ADR-133 / PR #78, ADR-137, ADR-139, ADR-141, ADR-142, ADR-143, ADR-144; focused successor tests/docs | PR #78 closes the local admission contract. Packet E and Packet G remain component-complete. PRs #82 and #84 later added CoreService/startup capture-runtime wiring and the opt-in Packet E scheduler. Packet H-D is disposable foreground proof, not complete Packet H/Phase 2 acceptance. Packet E x Packet F scheduled composition evidence is a later focused local proof, not ZF-007/ZF-008 product exit or complete Packet E/H or Phase 2. PR #86 merged compilation of those admitted records through Packet G and is not ZF-009 product exit. A later stacked local slice forms one caller-declared interaction preference in that same vault and is not ZF-010 product exit. ZF-007/ZF-008/ZF-009/ZF-010 product acceptance, the first real source/client journey, release, and support remain open; macOS remains absent/deferred |

The original component handoff counts were E: 25 tests, F: 25 tests, and G: 27
tests. Corrected focused counts are F/capture-capability: 27 tests and
G/client-runtime: 32 tests. The integrated F/G-adjacent union at corrected head
`719bdd9030e32ac34eb12184c35e1e47cf99cc37` passed 59 tests; Ruff,
format-check, and `git diff --check` passed. The previous pushed head
`dcf5de50b633ff00638c1396ddfcfb8ba04070e6` was fully hosted-green, but the
corrected head has not yet run hosted CI; full repository pytest/mypy also
remain open. These historical Wave 3/H-stop rows do not close
ZF-007/ZF-008/ZF-009 product acceptance, the first real continuous
source/client pair, ZF-010, complete Packet H, the Phase 2 acceptance
journey, release, or support status. Later PRs #82 and #84 added
CoreService/startup capture-runtime wiring and the opt-in Packet E
scheduler; that later wiring is not denied here. macOS remains
unsupported/absent/deferred under the current project truth.

### 2026-08-24 Packet H-D disposable integration reconstruction

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Reconcile bounded Packet F source admission through the PR #78 contract | `bench/packet_h.py`; `tests/unit/test_packet_h.py`; module and direct-file H-A CLIs | Implemented as disposable local evidence on merged PR #78: fake temporary ownership, redirecting `Path` subclasses, mutated/mismatched/nonempty/symlink/reparse roots fail closed and only capability-owned canonical roots are returned; the lstat/reparse branch is covered by a focused synthetic-stat test without privileged symlink creation; partial coverage and partial availability, denied network, and empty egress are gated; four fact-bearing upserts plus one deterministic no-fact upsert yield four structural records; candidate/record evidence must be the exact code-owned registered-source string; overflow fails closed with zero candidate/record output; crash/restart/replay is idempotent; CoreStores close in `finally` before teardown |
| Reconcile admitted records with public Memory Truth | `bench/packet_h_truth.py`; `tests/unit/test_packet_h_truth.py`; exact CLI `python -m bench.packet_h_truth` | Implemented as disposable local evidence: local-only posture and all-applied evidence are gated; complete public state plus stable identities are compared across list/detail/replay without private capture-lineage helpers; four current registered-source structural records have the expected provenance and metadata; exact withdrawal is bound to the public source-reference identity of the deleted provider item, preserves the non-withdrawal state, leaves three current and one deleted record without a new observation or ordinary tombstone, and requires deleted listing/status; public string fields are scanned in native, resolved, POSIX, and JSON-escaped path forms |
| Reconcile public Retrieval V3 search, bootstrap, and get behavior | `bench/packet_h_retrieval.py`; `tests/unit/test_packet_h_retrieval.py`; exact CLI `python -m bench.packet_h_retrieval` | Implemented as disposable local evidence: H-C calls the shared authoritative H-A validator before retrieval; required acceptance semantics are recomputed, the identifier digest is verified, malformed object trees are cleanly rejected, and additional true boolean predicates are allowed only when digest-bound and cannot bypass required predicates; returned search/bootstrap items used for acceptance must be structural; adapter deletion refuses absolute or `..` relative paths before unlink; 4/4 structural recall, provenance packaging, exact-get consistency, 256-character bootstrap compliance, three negative-query exclusions, real adapter deletion exclusion, and deterministic repeats pass |
| Preserve the evidence boundary | ADR-137; the four Packet H CLIs and focused Packet H tests | Local proof/lab evidence only over Packet F + PR #78 admission + public Memory Truth + Retrieval V3. Packet H-D is merged to protected main by PR #79; it is not released and does not itself satisfy continuous/scheduled Packet F acceptance. Later Packet E x Packet F scheduled composition evidence exists separately and still does not close ZF-007/ZF-008 product exit or complete Packet E/H acceptance. This H-D lane itself claims no Packet G reference host, ZF-010 automatic formation, full Wave 4 E–G composition, Phase 2 journey, provider/client support, archive import, OAuth or network support, ranking/schema changes, release readiness, or support status; no baseline receipt is added; macOS remains unsupported/deferred. PRs #82 and #84 separately wired CoreService/startup capture-runtime composition and the opt-in Packet E scheduler |

Reconstruction base is protected main after PR #78
(`e735d0dde301c64500acd1d404a2bbb6aab6724a`). The three Packet H test files
passed 61 tests in 32.15 seconds. Four CLIs passed with `PYTHONPATH` removed
so the checkout-source `sys.path` guard is active: `python -m bench.packet_h`
(0.86s), `python bench/packet_h.py` (0.91s), `python -m bench.packet_h_truth`
(1.01s), and `python -m bench.packet_h_retrieval` (1.62s).
Ruff check and Ruff format `--check` passed for the six Packet H Python files.
`python -m mypy packages/allthecontext/src` passed (91 source files).
`git diff --check` and `scripts/check_docs.py` passed. Full repository pytest
was intentionally not run in that reconstruction. The exact PR #79 pre-merge
head `34a0f96` had all 12 hosted required checks green, including 1,693 tests
on Windows and 1,693 on Ubuntu, CodeQL, security, dashboards, and desktop
artifacts.

Each public H-A/H-B/H-C run path obtains its root and capability only from the
shared fresh runner-owned temporary-root context. Its lexical construction
authority is not exposed by the modules; this is a construction/ownership rule,
not a hostile in-process security boundary. The context removes its temporary
state on exit. The CLI modules insert this checkout's repository root and
`packages/allthecontext/src` at the front of `sys.path` and fail closed if
imported `allthecontext` does not resolve under that checkout source.

### 2026-08-24 Packet G + Core Retrieval V3 lifecycle visibility

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Accepted L1+ pre-generation compilation fails closed without a Core principal | `experimental_reference_host.py::MissingCorePrincipal`; `compile_before_generation`; `tests/unit/test_experimental_reference_host.py` | Implemented locally: L1+ refuses before request/retrieval/delivery/generation when no `ClientPrincipal` is supplied. Ordinary MCP/L0 still returns `UnsupportedHookReport` and does not call the compiler |
| Next compile reflects Core lifecycle through the controlled host only | `experimental_reference_host_lifecycle.py`; `tests/unit/test_reference_host_retrieval_lifecycle.py` | Implemented locally as sanitized composition evidence: `ControlledReferenceHostV0` is the only compiler; authorized current decision and preference are visible; an ACL-private record is excluded for another principal; missing principal refuses before retrieval; correction includes the replacement and excludes the displaced value; ordinary delete, expiry, and terminal purge stay absent; one authorized record survives purge; restart/checkpoint restores host ordering/integrity only and does not duplicate Core truth; imported instruction-like direct text remains untrusted; secret-like input is refused content-free. Truth is seeded only through authenticated Core candidate/lifecycle APIs |

This slice does not close ZF-010, Packet E/F, complete Packet H, Phase 2,
CoreService/startup wiring, MCP lifecycle support, provider support, ranking or
schema changes, checkpoint persistence, hosted CI, full pytest, release, or
macOS. Empty-pack refusal remains covered separately by the existing Packet G
empty-context test.

### 2026-08-24 productized Packet E capture scheduler

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Keep scheduling explicit, Core-owned, and disabled by default | `CoreCaptureScheduler`; sidecar `capture-scheduler.json`; `ATC_CAPTURE_SCHEDULER_ENABLED=1`; `tests/unit/test_capture_scheduler_productization.py` | Implemented locally: missing sidecar and unset env do not dispatch; enable writes a content-free sidecar that survives restart when the process gate stays open; disable persists off and bounded-stops the thread while in-flight work completes; any presence of `ATC_UPDATE_HEALTH_OPERATION`, including empty string, force-disables even when the sidecar is enabled; lifespan uses the same helper; the scheduler sidecar inherits the Windows `O_NONBLOCK`/`O_NOFOLLOW` residual |
| Start one interruptible non-daemon thread after Core is ready and stop it on close | FastAPI lifespan; `CoreService.close`; bounded admin `stop`/`disable`; irrevocable `shutdown` fence | Implemented locally: start is after Core ready when gates pass, and admin enable can start or revive the worker only before shutdown; disable/stop join with a bound; lifespan `finally` and `CoreService.close` set a permanent closing fence and join the captured worker until dead before store/instance-lock release, without holding control/lifecycle mutexes; later enable/start cannot clear stop or revive/spawn in that instance; durable sidecar enablement may remain for the next Core process; the worker is never daemonized and is not cancelled; overlapping cycles are refused by an in-process global cycle lock, distinct from the coordinator's cross-process per-source lease; sidecar write plus start/stop are serialized by a control mutex; disable-then-enable during an in-flight cycle eventually runs while gates stay enabled and shutdown has not begun; `max_workers` is 1 |
| Recover expired runs and refresh the local-workspace adapter before due work | `recover_expired_runs` on Core start and each enabled `run_once`; `refresh_local_workspace_adapter` before each scheduled cycle | Implemented locally: expired reconciling sources become retry-due after recovery; adapter refresh matches admin `run`; due enabled/retry paths use existing coordinator/sink/adapter contracts; expected `CaptureError`/`OSError` stay content-free; programmer failures are not converted into a fake successful-empty report |
| Preserve `/health` and keep scheduler state authenticated and content-free | `GET /health`; `GET/POST /v1/admin/capture/scheduler`; `GET /v1/admin/capture/status`; `atc capture scheduler status`, `enable`, and `disable` | Implemented locally: `/health` remains exactly `{"status":"ok","component":"core"}`; scheduler status is admin-authenticated; CLI has status/enable/disable only and no `run_forever`/daemon; CLI sidecar status omits `running` because it cannot observe the Core process; status/health reads do not consume one-shot reauthorization or mutate rotation; invalid config and public payloads stay path/secret free |

This slice is not complete Packet E product acceptance, complete Packet H,
ZF-010, provider or network support, hosted/full-suite acceptance, release, or
macOS support. Local-workspace source lifecycle remains explicit.

### 2026-08-24 Packet E x Packet F scheduled composition evidence

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Drive authorized local-workspace ingestion through the merged opt-in scheduler | `CoreService.capture_scheduler.run_cycle`; existing process/env/sidecar gates; injected `CoreService` clock; `tests/unit/test_scheduled_packet_f_composition.py` | Implemented locally as composition evidence: an isolated CoreService vault authorizes and enables one Packet F source; two focused tests call `run_cycle()`, the same method used by the background loop, without starting that thread; a due cycle admits four structural current records observed through public Memory Truth and Retrieval V3 rather than SQL row counts. This is not Packet H; the proof reuses Packet H-D truth/retrieval helpers |
| Prove incremental withdrawal, update, and restart idempotence on public surfaces | public `list_memory_truth` / `get_memory_truth` / `memory_truth_coverage`; Retrieval V3 search, bootstrap, and get; reused Packet H truth/retrieval helpers | Implemented locally: a not-yet-due cycle creates no new public records; after the injected clock advances, deleting one item and changing another yields exact source-reference withdrawal, in-place update of the same current identity with changed public `binding_hash`, no duplicate current records, and no ordinary tombstone; restart plus a third unchanged due cycle applies zero events and leaves public truth/retrieval identical |
| Keep negative scheduler gates from creating public records | `ATC_CAPTURE_SCHEDULER_ENABLED`; sidecar `enabled: false`; `ATC_UPDATE_HEALTH_OPERATION` including empty string | Implemented locally: each gate leaves Memory Truth current items empty and coverage `record_count` at zero. Existing content-free scheduler status is a public non-mutating read (`running` remains false) and does not expose captured text, paths beyond existing policy, credentials, or raw personal context |

This is Packet E x Packet F scheduled composition evidence. It is not
ZF-007/ZF-008 product exit, complete Packet E product acceptance, complete
Packet H, Phase 2, real provider or client support, hosted/full-suite
acceptance, release, or macOS support. Continuous/scheduled Packet F
acceptance remains open. This evidence is on protected main through merged
PR #85 at `15d313f8bee33717e3e59f2583599df5305ca4fd`. PR #86 later merged
Packet G compilation over those admitted records; it does not close this
Packet F evidence boundary.

### 2026-08-24 Packet G compilation over scheduled Packet E x Packet F records

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Compile only public registered-source records admitted by the scheduled Packet E x Packet F journey | `experimental_reference_host_lifecycle.py::compile_authorized_pack`; `ControlledReferenceHostV0`; `tests/unit/test_scheduled_packet_g_composition.py`; extracted helpers in `tests/fixtures/scheduled_packet_f.py` | Implemented locally as composition evidence in one disposable `CoreService` vault: the scheduler-driven initial due cycle admits four public registered-source records; L2 in-process compile feeds only those current public identities into the existing Packet G compile surface; compiled items are structural, provenance packaged, scope-qualified to `workspace.structure`, and delivered as `context_pack` references whose SHA-256 matches pack content. The 256-character bootstrap compile truncates for budget; a separate 4000-character compile omits the duplicate Markdown structural sentence through Retrieval V3 duplicate suppression without treating that omission as truncation. Those are distinct existing bounded compile behaviors, not a 1:1 Memory Truth dump |
| Keep compilation authorization-first and capability-qualified | `MissingCorePrincipal`; L0 / ordinary MCP `UnsupportedHookReport`; empty-pack `ClientRuntimeContractError` | Implemented locally: missing `ClientPrincipal` refuses before retrieval; L0 and ordinary MCP do not invoke the Core compiler; a closed scheduler process gate leaves zero public records so L1+ empty Core context fails closed before delivery/generation |
| Keep compiled context content-safe and untrusted-path refusing | focused Packet G composition leak/untrusted/secret checks | Implemented locally: compiled host material does not include workspace roots, captured source text, credentials, or raw personal context; instruction-like direct-user text remains an untrusted envelope and is absent from the pack; secret-like input is refused content-free. After the scheduled incremental delete/update cycle, the withdrawn record ID is absent from the next compile |

This is Packet G compilation/composition evidence over merged Packet E x
Packet F scheduled capture. It is not ZF-009 product exit, ZF-010 product
exit, complete Packet H, Phase 2, provider or client support,
hosted/full-suite acceptance, release, private-data evidence, or macOS
support. No Packet G, scheduler, retrieval, or formation production
behavior was added. Continuous/scheduled Packet F acceptance remains open.
This evidence is on protected main through merged PR #86 at
`f06961e7aaefc37f6f7f3b86d16d50d983cedca7`. A later stacked local slice forms
one caller-declared interaction preference in the same vault and is not
ZF-010 product exit.

### 2026-08-24 same-vault ZF-010 composition over scheduled Packet G records

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Form one caller-declared interaction preference in the same vault as scheduled Packet E x Packet F records and Packet G compile | `experimental_reference_host_formation.py::form_direct_user_turn`; `tests/unit/test_scheduled_zf010_same_vault.py`; reused `tests/fixtures/scheduled_packet_f.py` | Implemented locally as stacked composition evidence in one disposable `CoreService` vault: Packet F setup helpers authorize and enable a sanitized workspace; one due Packet E cycle admits public registered-source records without starting the scheduler thread; Packet G compile of those public IDs is setup, not a repeat of Packet G budget/refusal assertions. A durable `context:read` principal compiles through `ControlledReferenceHostV0`, `compile_authorized_pack`, and `core_retrieval_compiler`. A separate durable witness principal with `witness:explicit_user_statement` forms through an L2 host whose `client_id` matches that principal |
| Prove APPLIED current public truth, in-place correction, and forget without mutating scheduler-admitted records | public `list_memory_truth` / `get_memory_truth`; next Packet G compile contents | Implemented locally: observe-only envelopes are not Core persistence and keep content out of `turn_ref` commitments; `form_direct_user_turn` with an aware frozen timestamp yields disposition `APPLIED`, current public Memory Truth, and `source_id is None`; mapper status `formed` is not treated as current by itself. The next compile includes the preference while scheduler-admitted public IDs remain valid. A correction keeps the same record identity, puts new content in the next compile, and leaves registered-source fingerprints unchanged. A `context_forget` makes the preference non-current/deleted through public truth, removes both preference texts from the next compile, and leaves registered-source records current. Exact Packet G selected counts are not reasserted after the preference because mandatory preferences change budgets |
| Keep distinctive same-vault refusals fail-closed without targeting registered-source facts | compile-reader formation, undeclared-kind, lookalike envelope, unformed instruction-like observe, secret-like observe | Implemented locally: formation through the compile reader cannot create current truth; unsupported caller-declared `project_decision` is `undeclared_kind`; a lookalike copied envelope is refused by object-identity membership; instruction-like content is observed and never formed, so it is absent from Core truth and the Packet G compile; secret-like input is refused content-free. Kind is never inferred. No `project_decision` or working-state formation is claimed |

This is stacked local composition evidence, not ZF-010 product exit, complete
Packet H, Phase 2, provider or client support, hosted/full-suite acceptance,
release, private-data evidence, or macOS support. No Packet G, scheduler,
retrieval, or formation production behavior was added. Continuous/scheduled
Packet F acceptance remains open. This candidate remains a local checkout
stacked on merged PR #86 at protected main
`f06961e7aaefc37f6f7f3b86d16d50d983cedca7` until pushed and merged.

### 2026-08-24 productized foreground local-workspace capture runtime

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Compose CoreService and CLI through one capture runtime | `capture_runtime.py`; `core/service.py`; `cli.py`; `tests/unit/test_capture_runtime.py` | Implemented locally: both surfaces construct `CaptureCoordinator` only through `compose_capture_coordinator`; the registered-source sink is always injected; the local-workspace adapter is registered only for a valid machine-local sidecar |
| Authorize exactly one canonical local workspace root without leaking the path | `authorize_local_workspace`; `atc capture authorize-workspace`; private sidecar under `CoreConfig.data_dir` | Implemented locally: explicit absolute `--root` plus local-only acknowledgement; FileLock held across read/identity/complete inventory/reconcile/write; newly created identity-bound `local-git-workspace` source is disabled with scopes exactly `workspace.structure`, account label `local-workspace`, and a non-revoked lifecycle; reconciliation preserves the existing acceptable enabled/paused/degraded/reconciling lifecycle rather than resetting it to disabled; root stays out of account labels, public status, logs, receipts, portable export, and fixtures; changed roots are a new identity and are refused; simultaneous same-root authorize yields one source and different-root authorize yields one winner plus bounded refusal; fail-closed authorization errors suppress OSError context |
| Keep Core available when authorization is absent or invalid | CoreService composition fail-closed adapter registration | Implemented locally: missing/invalid/symlink/reparse/non-regular/oversize sidecar; descriptor-based sidecar read (`os.open` with available close-on-exec/no-inherit/nonblocking/nofollow flags, `fstat`, 1..16 KiB regular file, MAX+1 bounded complete read, post-open `lstat`/`os.path.samestat`/reparse refusal); missing/non-directory/symlink/reparse/parent-redirecting/UNC/extended-UNC/Windows-remote/implicit home/cwd roots; post-resolve `os.path.samestat` rather than `Path.samefile`; held authorization lock; incomplete inventory; unreadable/malformed capture rows including object/string `requested_scopes_json` shapes; and retargeted sidecar identity leave the vault available and capture skipped as `capture_adapter_unavailable`; authorize returns a bounded content-free `CaptureError` rather than decoder exceptions or raw row/path. Explicit Windows extended local-drive prefixes unwrap to the ordinary drive form. Windows named-pipe sidecar hang without `O_NONBLOCK` remains a residual |
| Inventory and validate every workspace source before register/reconcile | bounded `list_sources` pagination in `capture_runtime`; focused >100-source, 500-row page-boundary, and metadata tests | Implemented locally: inventory is not truncated at 100 rows and crosses the 500-row page boundary; unreadable or malformed rows fail closed without crashing Core; adapter registration and authorize reconciliation require exactly one canonical matching row and refuse malformed, duplicate, mismatched, and revoked rows without deleting ledger state |
| Keep generic create from racing the reserved workspace provider | CLI `atc capture create`; admin `POST /v1/admin/capture/sources`; `reject_reserved_workspace_provider` | Implemented locally: public generic create rejects `local-git-workspace` after the same Unicode `str.strip` normalization as `CaptureLedger`, including leading/trailing/tab whitespace, with `capture_authorize_workspace_required`, and preserves other providers; `CaptureCoordinator.create_source` remains the provider-neutral test seam |
| Produce Memory Truth / Retrieval V3 records from one foreground run | existing capture coordinator, registered-source sink, and retrieval engine through the shared runtime | Implemented locally for manual opt-in foreground capture after enable: structural facts and a deterministic no-fact, restart identity rebuild with idempotent replay, exact file-deletion withdrawal, and Core-authoritative correction/delete/purge barriers. Admin run refreshes the local-workspace adapter fail-closed immediately before execution so a sidecar authorized after Core startup can run without restart and a later invalid sidecar is unavailable. CLI run still composes a fresh coordinator. The later Packet E scheduler slice is a separate explicit Core opt-in |

The runtime slice is not complete Packet H, ZF-010, provider or network
support, hosted/full-suite acceptance, release, or macOS support. Packet E
scheduling is a later isolated Core opt-in documented above.
Revoked and pre-existing malformed workspace-source rows have no product
recovery here; durable database uniqueness remains later hardening.

### 2026-08-24 ZF-010 direct-user formation mapper (local composition evidence)

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Map only accepted in-process Packet G L1+ `direct_user_turn` envelopes into Core through existing contracts | `experimental_reference_host_formation.py`; `tests/unit/test_reference_host_formation.py`; existing Packet G compile helper remains compile-only | Implemented locally as sanitized composition evidence: L0, ordinary MCP, unsupported-hook reports, other hooks, and lookalike envelopes are refused; membership is in-memory object identity in `host.events`, including after typed checkpoint restore of those same objects; restore is not Core persistence; caller-supplied content is commitment-checked as exact UTF-8 length plus SHA-256 against `turn_ref`; envelopes never store content; durable Core `ClientPrincipal` is required with `envelope.client_id == principal.id`; Core rebinds registered scopes; `normalize_lifecycle_event` then `form_observation`; closed caller-declared kinds are `interaction_preference` with `supersedes=None`, `correction` with required nonblank `supersedes`, and `context_forget` with required nonblank `supersedes`; preference rejects any non-None supersedes before `add_candidate` and cannot mutate preference or `project_decision` targets; kind is never inferred; `CandidateInput.source_id` stays `None`; entity/attribute slots are rejected; `add_candidate(..., client=principal)` only |
| Keep formation fail-closed for authorization, secrets, retention, observation time, and replay | focused formation tests plus existing G/client/lifecycle tests | Implemented locally: missing/wrong principal and forged scopes fail closed; a different witness correction/forget against owner-private truth is `IGNORED` and does not mutate it; scopes that are `str`/`bytes` or invalid items are refused; allowed/denied overlap is `DirectUserFormationError`; commitment mismatch, missing targets, any retention class except `bounded`, and over-bound content are refused without truncation; missing/naive observation time is refused and `datetime.now` is not synthesized; when the envelope lacks a valid timestamp the caller supplies an aware observation time that is stamped deterministically; secret-like content is absent from envelopes, candidates, records, and refusals; secret-refusal retry in-process and after CoreStore reopen returns the same receipt id with `replayed=true` using a UUIDv4-shaped operation id derived from `client_id+event_id+sequence`; instruction-like imported text is not auto-formed; public versus caller-requested private ACL; idempotent retry/restart/checkpoint-restore does not duplicate; AST/import boundaries forbid `delete_record` / `purge` / `correct_record` / `IngestionService` / `LOCAL_ADMIN` / event-log scanning / `datetime.now` |

This mapper is local composition evidence, not ZF-010 product exit, Packet E/F/H,
Phase 2, CoreService/startup wiring, MCP lifecycle support, provider support,
ranking or schema changes, checkpoint persistence, hosted CI, full pytest,
release, or macOS. Packet G checkpoint restore is in-memory identity membership
only. ADR-139 is the foreground capture runtime decision.

### 2026-08-23 registered-source admission PR1 contract

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Admit only a Core-issued registered-source structural fact through the existing capture sink | `registered_source_admission.py`; migration `016_registered_source_admission.sql`; `memory_policy.py`; sanitized `tests/unit/test_registered_source_admission.py` | Implemented locally for the bounded PR1 contract: exact durable event/run/source validation, exact code-owned `workspace.structure` scope, closed local-workspace extractor registry, complete code-owned projection validation, opaque source/item memory references, Core availability, normal sensitivity, empty ACLs, explicit false, deterministic capture-lineage record IDs, replay idempotency, source withdrawal, deterministic no-fact upsert withdrawal for the same exact source/item, correction/delete/purge/no-linkage barriers, and content-free receipts. No CoreService, package-startup, scheduler, or reference-host wiring |
| Keep machine-local capture runtime out of portable archives without losing admitted Core truth | `export.py`; portable export/restore focused test | Implemented locally: all five capture runtime tables are omitted even for source-inclusive exports, registered candidate capture FKs are nulled, legacy capture table entries are ignored on restore, and same-database restart retains capture state |
| Advance ADR-132 without overstating acceptance | ADR-133; focused local tests only | PR1 / PR #78 closes only this local admission contract. Complete Packet H, ZF-010, product/provider support, hosted/full-suite acceptance, stable SDK, production wiring, release readiness/publication, and macOS remain open, absent, or deferred |

### 2026-08-23 bounded capture page-recovery correctness

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Persist and repair one bounded pending page without adding a truth table | `017_capture_page_recovery.sql`; `ensure_capture_schema`; focused capture migration/restart tests | Implemented locally: migration 017 owns only nullable pending checkpoint fields and a bounded JSON array; marker-present and missing-column repair remain restart-safe; migration 016 remains limited to its three candidate columns and partial index |
| Stage a complete page atomically before sink admission and recover it before provider fetch | `CaptureLedger.stage_page`; `CaptureCoordinator._recover_pending_page`; focused capture crash/rollback/retry tests | Implemented locally: existing event identity/idempotency/conflict rules are reused, ordered durable event IDs are persisted in the same transaction, applied pending events replay idempotently, all events must apply before cursor advance, and repeated sink failure remains bounded/retryable |
| Recover registered-source admission and real local deletion without generic absence deletion | existing registered-source sink; sanitized `tests/unit/test_registered_source_admission.py` recovery/delete test | Implemented locally: after sink admission/capture-commit interruption and fixture-file removal, same-run recovered-cursor diff emits the source-scoped delete, capture item is deleted, no ordinary tombstone is minted, and pending state clears; correction, availability, ordinary-delete, and purge barriers remain Core-authoritative |
| Keep scope truthful | ADR-134; focused local checks only | That capture-correctness work claimed no complete Packet H, production startup wiring, scheduler, provider/product support, private data, macOS support, or full-suite acceptance |

### 2026-08-23 capture admission and repair guard reconciliation

| Requirement | Implementation/evidence | Status |
|---|---|---|
| Reject duplicate provider event IDs before page staging and guard durable pending IDs for uniqueness | `CaptureCoordinator` page validation; `CaptureLedger.stage_page`; duplicate-page focused lane | Implemented locally: duplicate IDs within a page fail before staging, the pending durable ID list is uniqueness-guarded transactionally, and partial pending state cannot survive the rejection. Historical lane evidence: 58 focused tests |
| Recover legacy duplicate pending IDs without weakening new-page rejection | `CaptureLedger._pending_event_ids`; focused poisoned-marker recovery regression | Implemented locally: raw bounded marker lists and each ID remain validated, repeated identical durable IDs are replayed once in first-occurrence order, and successful recovery atomically advances/clears the marker; malformed marker data still fails closed |
| Keep local workspace `workspace.structure` events metadata-only | `LocalGitWorkspaceCaptureProviderAdapter`; registered-source projection; metadata-focused lane | Implemented locally: the adapter-produced/coordinator path emits bounded structural metadata only; the generic ledger retains internal caller-supplied payloads and the registered sink keeps extra fields inert. Source text and excerpts are not durably retained on the adapter path or in registered-source candidate/evidence projection. Historical lane evidence: 63 focused tests |
| Bound capture schema repair to already-applied capture migrations before applying a newer migration | `ensure_capture_schema`; `CoreStore.migrate`; capture migration-focused lane | Implemented locally: repair runs through the already-applied capture version inside the pending migration transaction, and successful repair retains the complete repaired state. Historical lane evidence: 8 capture migration tests. `docs/architecture/DATA_MODEL.md` already records 017 as used and 018 as next |
| Keep validation evidence bounded and accurately scoped | Historical lane reports; integration owner's subsequent combined focused run | The integration owner subsequently ran 152 combined focused tests on integrated code. Reported Ruff lint, Ruff format-check, and mypy checks passed; these local reports do not constitute full-suite acceptance |
| Preserve the project boundary | ADR-135; focused/static reports only | That capture-correctness work claimed no complete Packet H, production startup wiring, scheduler, provider/product support, private-data evidence, macOS support, or integrated full-suite acceptance |

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
| Post-V1 zero-routine-friction platform direction | ADR-090; `product/ZERO_FRICTION_PLATFORM.md`; `product/ZERO_FRICTION_EXECUTION_PLAN.md`; `product/PRODUCT_REQUIREMENTS.md`; `protocols/CAPTURE.md` | Accepted product and execution direction only. Core remains authoritative; connected text remains inert untrusted data; authorization and lifecycle resolution precede derived work; capability levels prevent L0 integrations from claiming lifecycle hooks; correction, deletion, retention, and purge dependencies close future ATC influence. The public beta.6, merged PR #73 capture foundation, and PR #78 admission contract satisfy Phase 0 / the local admission seam but grant no provider or lifecycle support. Packet E and Packet G are component-complete. PRs #82 and #84 wired CoreService/startup capture-runtime composition and the opt-in Packet E scheduler. Packet E x Packet F scheduled composition evidence exists over that scheduler but Packet E product acceptance remains open. PR #86 merged compilation of those admitted records through Packet G and is not ZF-009 product exit. A later stacked local slice forms one caller-declared interaction_preference in that same vault and is not ZF-010 product exit. Remaining work is Packet G product acceptance, complete Packet E product acceptance, ZF-010 automatic formation, complete Wave 4 E–G (complete Packet H), and Phase 2, then stable project identity, deterministic capsules, and graph shadow evaluation. Continuous/scheduled Packet F acceptance and ZF-007/ZF-008 product exit remain open. Packet H-D is merged foreground disposable proof, not that remaining work. No graph, connector, working-state, learned-retrieval, remote, or stable-SDK claim is credited until separately implemented and accepted |
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
