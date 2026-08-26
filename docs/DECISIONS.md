# Architecture decisions

## ADR-153: Freeze an independent ZF-013 graph safety oracle

**Status:** frozen locally on 2026-08-25 for Milestone 5 against protected-main
base `fd1f802a67b3eb689ecdd4d85cd4440e1a57b7d2`. This is a safety contract,
not graph, provider, client, release, or private-data acceptance.

The sanitized oracle imports no ATC runtime, storage, retrieval, network,
provider, UI, or private data. It applies authorization and project isolation
before all graph-derived observables and requires exact paired equality for
content, reason codes, revisions, counts, ordering, and receipts when
unauthorized records are added. The frozen matrix covers assignment
abstention, correction/supersession, historical `as_of`, delete/purge closure,
stale dependency invalidation, cycle and self-edge rejection, duplicate-edge
idempotence, bounded high fan-out/two-hop expansion, deterministic rebuild,
and secret-like/imported-instruction inertness.

Any required-zero safety violation yields `KILL_ZF013`; missing coverage,
unsafe diagnostics, runtime coupling, an unobservable surface, or a rebuild
control that shares optimized state yields `HOLD_ZF013`. The standalone
reference implementation remains an independent oracle and is not product
proof. A separate implementation-facing suite maps every frozen case onto the
actual typed graph while keeping the oracle itself free of product imports.

## ADR-152: Milestone 5 uses an ephemeral typed project graph

**Status:** accepted locally on 2026-08-25 for the bounded lane-B component.
This is not graph-store, learned-retrieval, provider, client, private-data,
package, release, or production acceptance.

The graph builder consumes relation-shaped evidence only after the caller has
resolved authorization, one-project assignment, and temporal/lifecycle
eligibility. Its six closed families are belongs_to, supersedes, depends_on,
blocks, implements, and tested_by. Unknown or inferred relations—including
causal and failure edges—are unsupported. Invalid, ambiguous, cross-project,
duplicate, cyclic, self, and over-cap inputs are omitted with content-free
abstention receipts. Rebuilds sort all accepted material and hash a stable
revision; one- and two-hop expansion uses bounded deterministic traversal and
preserves direct provenance and dependency lineage.

The component is in-memory and read-only. It does not parse prose, create
canonical records, persist a graph, scan a workspace, capture a source, call a
model, or wire into runtime, retrieval, MCP, dashboard, or storage paths.

## ADR-155: Outcome receipts and procedural learning remain advisory shadow state

**Status:** accepted locally on 2026-08-25 for Milestone 5 lane D. This is a
pure research foundation, not production, provider, client, release, or
platform acceptance.

ZF-017 through ZF-019 begin with an isolated in-memory contract rather than a
Core schema or route. `memory_lab_outcome_shadow.py` models exact project and
projection versions, assigned memory/source dependencies, issue receipts,
client acknowledgement, declared use/nonuse, bounded action/tool envelopes,
task completion, typed external results, user corrections, and invalidation
dependencies. Receipt serialization is allowlisted and rejects raw context,
private text, hidden reasoning, secrets, credentials, provider claims, and
model self-report. Every identifier and code is an ASCII machine token matching
`[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`; whitespace, Unicode prose, controls,
slash/backslash paths, and `..` traversal shapes are rejected while timestamp
and code punctuation remains available.

The receipt ledger is bounded and idempotent. Ordinary correction or projection
mutation marks linked evidence inactive; terminal purge removes linked receipts
and retains only an opaque barrier/count surface, so old evidence cannot be
reused. `propose_procedure` derives a candidate only from matching observable
successes and requires recurrence or strong non-client external verification,
exact project/task/applicability-key matching, negative guards, passing repair tests,
influence dependencies (project, projection, memory, and source) plus outcome
dependencies, and a closed purge dependency set. Action
signatures must agree across evidence. The result is always `proposed` and
`advisory_only`; no promotion or truth-authority operation exists.

The focused synthetic tests cover allowlisting, idempotency, correction,
purge, recurrence, strong verification, invalidation, signature disagreement,
and missing purge closure. This slice does not add storage, Core/Relay/MCP,
dashboard, capture, scheduler, retrieval, model, network, live-data, provider,
packaging, release, or macOS behavior.

## ADR-156: Local continuous-capture acceptance must exercise the Core worker

**Status:** accepted locally on 2026-08-26 as the worker-backed Packet E x
Packet F developer acceptance boundary. This is not packaged installation,
live/private source evidence, provider or client support, complete
ZF-007/ZF-008 product exit, Packet G product acceptance, complete Packet H,
Phase 2, release acceptance, or macOS support.

The acceptance oracle starts the existing non-daemon `CoreCaptureScheduler`
through its public lifecycle after one explicit local-workspace authorization
and durable enablement. Capture transitions may be made deterministic by
advancing the injected clock and waking the worker, but the journey must not
call `capture_scheduler.run_cycle()` directly. It observes only durable source
state, public Memory Truth, Retrieval V3, and content-free scheduler status.

A fail-once adapter must persist a retry with zero public records. Restart at
the due retry must create the initial four-record snapshot without another user
action. A later update and deletion must yield three current records, one exact
deleted record, current retrieval visibility, stable updated identity, and no
duplicate current records. A second restart and unchanged due run must leave
public truth and retrieval identical. Any routine dashboard launch or leakage
of the authorized root, captured text, credentials, or fixture secret material
fails the journey.

No production source changes are required. Existing focused scheduler/runtime
tests remain authoritative for detailed health reports, gates, controls,
notifications, shutdown, and package-startup composition. This decision closes
only the manual-cycle gap left open by ADR-142 for the local worker-backed
developer pair; broader product and support exits remain explicit.

## ADR-151: Project activation is ambient, principal-filtered, and abstaining

**Status:** accepted locally on 2026-08-26 for the Milestone 4 ambient project
bootstrap. This is a local MCP/Core product contract, not live-provider,
remote-host, package, or release acceptance.

All The Context is background infrastructure during healthy use. Per-user
startup launches only Core; users do not need to open the ATC dashboard to
resume a project. The local MCP instructions require automatic bootstrap and
explicitly prohibit asking the user to open or manage ATC. The dashboard stays
optional for setup, inspection, correction, and recovery.

Core builds the project projection after applying the requesting principal's
record allow/deny boundary. Resolution then follows a closed order: an explicit
host signal may match a safe label, opaque project identity/reference, or the
existing project-scope compatibility value; otherwise one unique safe project
label present in the task may match; otherwise exactly one authorized
content-bearing project activates. Invalid, missing, unauthorized, ambiguous,
or multiply matching signals abstain. Imported prose cannot instruct the
resolver, and resolution never scans a workspace or turns capture on.

If a compatible MCP client advertises exactly one root, the adapter may request
the roots capability for at most one second and forward only one safe bounded
root display name as a weak host hint. It never reads or forwards a root URI or
path. Multiple roots, path-shaped names, malformed replies, missing backchannel
support, and timeouts produce no hint. Unlike explicit `current_project`, an
unmatched host hint falls through to the task label and sole-project rules.

The deterministic capsule and ordinary retrieval share the caller's bootstrap
character budget. Project context receives at most half and retrieval receives
the remainder; `total_used_chars` cannot exceed the requested budget while the
existing retrieval-only `used_chars` contract stays unchanged. A bounded
project-projection failure returns a content-free abstention
without taking ordinary authorized retrieval offline. Capsule delivery receives
a separate content-free audit action under the bootstrap trace. The real STDIO
MCP test proves automatic activation without a dashboard call, but does not
claim a provider lifecycle hook or remote Edge parity.

## ADR-150: Reconcile current lab snapshots without rewriting historical evidence

**Status:** accepted locally on 2026-08-25 for the bounded PR #88
reconciliation. This is evidence-contract maintenance, not production or
release acceptance.

The accepted strict Retrieval V3 behavior is measured honestly in the current
lexical, M0, and B01 snapshots. The lexical V1 comparator keeps its existing
recall and MRR gates and records their current failures; no threshold is
lowered. M0 and B01 regenerate only their current-production result rows,
while their sanitized fixture/config identities, privacy boundaries, and
research decisions remain fixed.

E01b and E02 were authored against frozen historical production bases, and
their reports are immutable observations rather than current conformance
expectations. Exact tests therefore validate the checked-in reports and their
recorded bases; current execution remains covered only by the separate
content-free, deterministic, disposable-boundary tests. The historical
`retrieval_precision_m3_f5e3a2b` baseline is not rewritten or used as a target
for current-production reconciliation.

## ADR-149: Provisional synthetic retrieval precision remains content-only and abstaining

**Status:** accepted locally on 2026-08-25 as the Milestone 3 retrieval
precision decision. This is a provisional developer-evaluation contract, not
committed evidence, live acceptance, beta readiness, or a release gate.

Direct nonempty multi-term retrieval keeps the deterministic `0.75` content
floor; query scaffolding cannot lower it. Bootstrap is a separate broad
context-assembly path with a content-only one-anchor candidate pool. Relevant
records must cover the complete topical anchor set union; an incomplete union
withholds the relevant tier rather than returning partial context. A curated
alias counts only as its mapped original anchor, and kind/tag/scope/project
metadata cannot satisfy content coverage or consume the content pool as a
metadata-only match.

The historical five-case precision snapshot remains immutable. The current
synthetic evaluator is 10/10 on production and content-free, and the separate
17-case usefulness scorecard remains passing. Reported aggregate authorized
local result on the final production evaluation profile is 387 current, 319
eligible, sample 96; exact self retrieval for 2, 3, and 4 tokens and
`natural-scaffold-3` are each 96/96 nonempty and self recall at 5; split
evaluation is 24 pairs, bootstrap both-target and any-target recall is 20/24,
the other four incomplete relevant tiers are withheld, and strict search
any-target is 1/24 with both-target 0/24 by design.

Evidence is limited to sanitized synthetic/disposable local evaluation and the
focused tests `test_retrieval_precision_m3.py`,
`test_retrieval_bootstrap_composition.py`,
`test_retrieval_m3_current_candidate.py`, and the content-free evaluator
reports. No raw exports, database files, per-record traces, generated reports,
or live provider/client/platform/private-data acceptance are credited.

## ADR-148: Failed archive rebuild candidates remain inert until atomic cutover

**Status:** accepted locally on 2026-08-25 for the Milestone 3 provider
rebuild boundary. This is not provider acceptance or release evidence.

Startup staged-observation evaluation excludes source-rebuild sessions. A
finished rebuild session is still staging until the source-bound atomic cutover
validates and publishes it together with withdrawal of prior automatic records.
If rebuild ingestion fails, prior current records and history remain unchanged,
the failed session remains marked for recovery, and startup cannot turn the
staged candidates into a second additive import.

The boundary is covered by `test_rebuild_ingestion_failure_rolls_back_withdrawal`
in `tests/unit/test_provider_ingestion.py`, including the startup recovery
evaluator returning zero and prior record/history remaining unchanged.

## ADR-147: Provider candidate quality uses opaque scoping and conservative filters

**Status:** accepted locally on 2026-08-25 for the Milestone 3 provider
candidate boundary. Candidate tests are synthetic/fixture-based and do not
establish live provider acceptance.

Provider candidates may inherit one recognized project anchor only when the
anchor is unambiguous; competing provider anchors abstain from scoping rather
than guessing or crossing lineages. Project references and provider lineage
remain opaque. Sensitivity classification is conservative, including
health/location-like material, and task-local, adversarial, assistant/system,
tool, secret-like, and instruction-shaped content remains inert or excluded
from current-memory promotion according to the existing importer policy.

The decision is grounded in the provider-ingestion tests
`test_one_project_anchor_scopes_all_siblings_opaquely_and_deterministically`,
`test_multiple_project_anchors_abstain_from_scoping_siblings`,
`test_health_and_location_statements_are_marked_sensitive`, and
`test_task_local_and_adversarial_preference_framing_stays_inert`, plus the
provider runtime project-lineage and lifecycle-filter tests. No provider SDK,
live export, OAuth, or client acceptance is implied.

## ADR-154: ZF-013 lane A is a frozen synthetic graph harness self-test

**Status:** accepted locally on 2026-08-25 for the Milestone 5 lane A
evaluation boundary. This decision does not promote a project graph or change
production behavior.

ZF-013 lane A freezes a sanitized, in-memory harness contract pinned to
protected-main base `fd1f802a67b3eb689ecdd4d85cd4440e1a57b7d2`. The harness
compares five named profiles: a stdlib-only lexical proxy, checkout-local
production `LexicalV3` with fixture-supplied current/project eligibility,
deterministic Project Context Capsules, lexical seeds plus directed typed
one-hop expansion, and bounded directed two-hop expansion. The full
RetrievalEngine/Core policy façade is explicitly not exercised. The corpus includes
current, stale, deleted, and purged synthetic records, two projects, all six
allowed relation families (`belongs_to`, `supersedes`, `depends_on`, `blocks`,
`implements`, `tested_by`), a cycle, a self-edge, a cross-project edge, and a
high-fan-out source.

The evaluator defines Project CAOS as the mean of query cases that contain all
required evidence, no wrong-project/stale/deleted/purged/unnecessary
disclosure, no budget violation, and no traversal-bound violation. Required
recall, Project CAOS, disclosure counts, deterministic receipt hashes, cycle
and fan-out bounds, depth, expanded-edge count, and warm p95 latency are
machine-readable gates. One-hop requires recall/CAOS `>= 0.75`, two-hop
requires `>= 0.90`/`0.85`; each must improve recall/CAOS over structured
filtering by the frozen `0.20`/`0.30` thresholds. Safety disclosures are zero;
depth is bounded at one/two hops, accepted self-edges and cycle revisits are
zero, each source has at most two neighbors, each query has at most 24
expanded edges, and warm p95 is at most 50 ms. Endpoint eligibility is applied
before ordering, accounting, traversal, timing, or receipts; rejected-edge
classes are aggregate validation evidence only.

Relation-family ablations remove exactly one family from the one-hop candidate
on one assigned case per family. A family is kept if removing it reduces
required recall or CAOS by at least `0.10` without a safety regression. It is
killed if both deltas are below `0.10`, or if enabling it causes a safety
failure that the ablation removes. Every family receives a finite, internally
consistent explicit decision or the harness self-test fails closed. The lane
fixture's synthetic integration hypotheses keep `belongs_to`, `depends_on`,
`blocks`, `implements`, and `tested_by`, and kill `supersedes`; none is
promotion evidence.

The evaluator remains outside `packages/allthecontext` but imports the actual
ephemeral `allthecontext.project_graph` candidate for one- and two-hop
profiles. It must not open Core or a workspace, scan files, enable capture, use
a provider/model, open a browser/dashboard, add MCP/storage/runtime/packaging/
release/CI behavior, or emit raw fixture content. Reports remain disposable aggregate
evidence and grant no live, private, provider, client, platform, release, or
graph-promotion credit. The deterministic Project Context Capsule foundation
and Core authority remain governed by ADR-146.

## ADR-146: Project Context Capsules are bounded Core-derived projections

**Status:** accepted locally on 2026-08-25 as the Milestone 2 Project Context
Capsule foundation. Graph and learning work remains open under ZF-013.

Project Context Capsules are deterministic, in-memory, read-only projections of
public Core Memory Truth. Core remains the sole authority; the project runtime
does not open imported sources or workspace sidecars, write capsules, or create
a second memory store. Exact `project:<ref>` scopes take precedence, otherwise
one unambiguous provider lineage anchor may assign a project. Ambiguous or
unresolved assignments abstain. Current/lifecycle-eligible content is filtered
before selection, and imported instruction-like material cannot choose or
instruct a project capsule.

Each selected item carries authority and provenance references. The capsule
reports project identity, assignment outcome, section, used characters,
character/item budgets, omissions, truncation, and a deterministic revision; the
default bounds are 12,000 characters and 32 items. `full_rebuild` is the clean
oracle and `optimized_rebuild` must produce the same snapshot after restart and
input reordering. Core exposes the bounded admin list/capsule routes, and the
Project Continuity dashboard displays only resolved projects and bounded
capsule accounting.

Evidence is the focused `test_project_continuity.py`,
`test_project_runtime.py`, and Project Continuity dashboard/API tests. This
decision does not claim a graph store, graph expansion, learned retrieval,
cross-project graph semantics, or client/provider/release acceptance.

## ADR-145: Continuous Context is an explicit local workspace runtime

**Status:** accepted locally on 2026-08-25 for Milestone 1. This decision
records a local capability, not live provider acceptance or universal continuous
capture.

Core remains loopback-bound by default and owns the explicit local workspace
runtime. The authenticated admin endpoint
`POST /v1/admin/capture/workspaces/authorize` requires an absolute root and an
explicit local-only acknowledgement, creates one identity-bound
`local-git-workspace` source disabled, and fails closed for implicit,
network-style, symlink/reparse, non-regular, malformed, or mismatched roots.
Repeated authorization of the same root is idempotent; a different root does
not silently replace the existing identity. Authorization does not refresh the
adapter: refresh occurs at the foreground/scheduled run boundary.

The Core-owned scheduler remains disabled by default. Dispatch requires the
process gate `ATC_CAPTURE_SCHEDULER_ENABLED=1`, valid durable enablement, and
the absence of `ATC_UPDATE_HEALTH_OPERATION`; desktop launch opens only the
process gate and does not create durable enablement. Authenticated Sources
controls cover connect/authorize, enable, pause/resume, run-now, revoke, and
automatic-sync on/off state. Public status remains bounded and content-free.

Supported source/package targets for this checkout are Windows and supported Linux. macOS source, tests, and historical preflight/packaging code remain retained for portability and maintenance only; macOS is unsupported and creates no package, CI, release, provider/client, acceptance, or support claim.

Evidence is the focused capture-runtime, scheduler productization, desktop
launch, and Sources-dashboard tests, including the late-authorization refresh
regressions. No real provider, network/OAuth, private workspace, release, or
client acceptance is credited.

## ADR-144: Same-vault ZF-010 composition forms one declared preference over scheduled Packet G records

**Status:** accepted locally on 2026-08-24 as stacked composition evidence on
merged PR #86 at protected main
`f06961e7aaefc37f6f7f3b86d16d50d983cedca7`; this candidate remains a local
checkout until pushed and merged. This is not ZF-010 product exit, complete
Packet H, Phase 2, provider or client support, hosted/full-suite acceptance,
release, private-data evidence, or macOS support. ADR-140 remains the
direct-user formation mapper. ADR-138 remains the Packet G compile contract.
ADR-143 remains the Packet G compilation over scheduled Packet E x Packet F
records and is not this formation work.

Same-vault ZF-010 composition reuses existing code only. One disposable
`CoreService` vault authorizes and enables a sanitized Packet F workspace,
runs one due Packet E scheduler cycle without starting the worker thread,
and compiles those scheduler-admitted public identities through the existing
Packet G path as setup. A durable `context:read` principal compiles; a
separate durable witness principal with the exact
`witness:explicit_user_statement` grant forms. The formation host
`client_id` matches that witness. Host events and checkpoints are not Core
persistence. Mapper status `formed` is not current truth by itself:
disposition and public Memory Truth are required. Direct-user content stays
out of envelopes; UTF-8 length and SHA-256 must match `turn_ref`. Kind is
never inferred. `CandidateInput.source_id` stays `None`.

The journey forms one caller-declared `interaction_preference` with an
aware frozen timestamp, proves APPLIED current public truth, includes that
preference in the next Packet G compile, then corrects it in place and
forgets it. Registered-source records remain valid and unmutated.
Distinctive refusals in this vault are the compile reader failing to mint
current truth, unsupported caller-declared kinds, and lookalike copied
envelopes refused by object identity. Instruction-like text is observed and
never formed. Secret-like input stays content-free. Packet F incremental
counts and Packet G exact selected counts are not reasserted after the
preference because mandatory preferences change budgets. No
`project_decision` or working-state formation is claimed. No Packet G,
scheduler, retrieval, or formation production behavior is added.

Evidence is the focused same-vault test in
`tests/unit/test_scheduled_zf010_same_vault.py` plus existing scheduled
Packet F/G and formation tests, Ruff check and format `--check` on touched
Python files, `scripts/check_docs.py`, and `git diff --check`. Full
repository pytest, mypy (no production source touched), hosted CI, private
data, and macOS work were not run.

## ADR-143: Packet G compiles scheduled Packet E x Packet F public references in-process

**Status:** accepted on protected main on 2026-08-24 through merged PR #86 at
`f06961e7aaefc37f6f7f3b86d16d50d983cedca7`. This is not ZF-009 product exit, ZF-010,
complete Packet H, Phase 2, provider or client support, hosted/full-suite
acceptance, release, private-data evidence, or macOS support. ADR-138 remains
the Packet G compile contract. ADR-140 remains the separate direct-user
formation mapper and is not this compile work. ADR-142 remains the Packet E x
Packet F scheduled composition evidence merged by PR #85.

Packet G compilation over scheduled capture records reuses the existing
in-process L2 host, `compile_authorized_pack`, and injected Core Retrieval V3.
The vault is populated only by the Packet E scheduler driving one authorized
Packet F local-workspace source through `capture_scheduler.run_cycle()`
without starting the worker thread. Compiled items must be current public
Memory Truth identities, structural registered-source facts, provenance
packaged, and delivered as `context_pack` references whose SHA-256 matches
pack content. L1+ compilation remains authorization-first:
`MissingCorePrincipal` before retrieval, L0/ordinary MCP
`UnsupportedHookReport` without invoking the compiler. Direct-user
instruction-like text stays an untrusted envelope and is not pack content;
secret-like input is refused content-free. The compile is bounded along two
distinct existing paths: the 256-character bootstrap budget truncates with
`budget` as a truncation reason, and a 4000-character compile omits the
duplicate Markdown structural sentence through Retrieval V3 duplicate
suppression without treating that omission as a truncation reason. Neither
path is a 1:1 Memory Truth dump. No Packet G, scheduler, retrieval, or
formation production behavior is added.

Evidence is the focused Packet G composition tests in
`tests/unit/test_scheduled_packet_g_composition.py` plus the extracted
Packet F journey helpers and existing scheduled Packet F tests, Ruff check
and format `--check` on touched Python files, `scripts/check_docs.py`, and
`git diff --check`; PR #86's hosted Linux and Windows Python suites and all
other required checks passed before merge. Mypy was not required locally
because no production source was touched. Private-data and macOS work were not
run.

## ADR-142: Packet E x Packet F scheduled composition is opt-in scheduler evidence, not Packet H

**Status:** accepted on protected main on 2026-08-24 through merged PR #85 at
`15d313f8bee33717e3e59f2583599df5305ca4fd`. This is not ZF-007/ZF-008 product exit,
complete Packet E product acceptance, complete Packet H, Phase 2, real
provider or client support, hosted/full-suite acceptance, release, or macOS
support. Continuous/scheduled Packet F acceptance remains open. ADR-139
remains the shared foreground capture runtime. ADR-141 remains the isolated
Packet E scheduler productization. ADR-137 remains the disposable Packet H-D
foreground proof and is not this scheduled composition.

The already-merged opt-in Core scheduler is the only capture loop in this
slice. An isolated `CoreService` vault authorizes and enables one Packet F
local-workspace source, then the two focused tests call
`capture_scheduler.run_cycle()`, the same method used by the background loop,
without starting that thread. Public Memory Truth (`list_memory_truth`,
`get_memory_truth`, `memory_truth_coverage`) and Retrieval V3 (search,
bootstrap, get) are the acceptance surfaces. Direct SQL row counts are not.
The proof reuses Packet H-D truth/retrieval helpers but is not Packet H.
`CoreService` accepts an optional injected clock so incremental due times are
deterministic; production continues to use `utc_now`. No new CLI, scheduler
table, health UI, or dashboard/desktop auto-enable is added. Existing
content-free scheduler status remains a public non-mutating read: status
equality holds and `running` remains false. Dedicated existing scheduler
tests own internal non-mutation of reauthorization and rotation state.

The focused synthetic journey proves: an initial due cycle admits four
structural current records; a cycle before the incremental interval is due
creates no new public records; after the injected clock advances, deleting one
source item and changing another yields exact public source-reference
withdrawal, in-place update of the same current identity with changed public
`binding_hash`, no duplicate current records, and
`status_reason == "record is soft-deleted"` rather than an ordinary tombstone;
restart from the same vault plus a third unchanged due cycle is idempotent
with zero new records. Negative gates keep public record counts at zero when
the scheduler env is absent, the sidecar is disabled, or
`ATC_UPDATE_HEALTH_OPERATION` is present, including the empty string.

Evidence is the focused scheduled composition tests in
`tests/unit/test_scheduled_packet_f_composition.py` plus the related
scheduler/runtime nodeids recorded in `STATUS.md`, Ruff check and format
`--check` on touched Python files, and mypy on package source. Full
repository pytest, hosted CI, private data, and macOS work were not run.

## ADR-140: Direct-user formation maps only declared Packet G L1+ turns

**Status:** accepted locally on 2026-08-24 as composition evidence for one
conservative ZF-010 class; this is not ZF-010 product exit, Packet E/F/H,
Phase 2, CoreService/startup, MCP lifecycle, provider, hosted/full-suite,
release, or support acceptance. ADR-138 remains the Packet G compile contract.
ADR-139 records the separately merged foreground capture runtime; ADR-141
records the separately bounded Packet E scheduler productization.

The mapper accepts only in-process Packet G L1+ `direct_user_turn` envelopes
that the controlled host actually accepted. Membership is object identity in
`host.events`, including after in-memory Packet G checkpoint restore of those
same envelope objects. Restore is identity membership only and is not Core
persistence or a second truth. L0, ordinary MCP, unsupported-hook reports,
other hooks, and lookalike envelopes fail closed. The caller supplies raw
content; the mapper recomputes exact UTF-8 byte length and SHA-256 against
`turn_ref` and never stores content in envelopes. A durable Core
`ClientPrincipal` is required, `envelope.client_id` must equal `principal.id`,
and Core rebinds registered scopes. Formation uses `normalize_lifecycle_event`
then `form_observation`. Closed caller-declared kinds are
`interaction_preference` with `supersedes=None` (any non-None supersedes,
including empty or whitespace, is rejected before `add_candidate`),
`correction` with a required nonblank `supersedes`, and `context_forget` with a
required nonblank `supersedes`. Preference cannot supersede. Kind is never
inferred. `CandidateInput.source_id` stays `None`. Entity/attribute slots are
rejected entirely. Admission is `add_candidate(..., client=principal)` only;
`client=None` and `LOCAL_ADMIN` are refused. Direct candidate/value secret
checks run before add. Secret and expiry refusals are content-free. Secret-
refusal replay uses a stable UUIDv4-shaped operation id derived from hash
bytes of `client_id+event_id+sequence` with UUID version 4 and RFC 4122
variant bits set. There are no direct current-record writes, `delete_record`,
`purge`, `correct_record`, `IngestionService.forget`, or event-log scans.
ACL is an optional caller request, not writer identity by default; scopes that
are `str`/`bytes` are rejected, items are validated, and allowed/denied
overlap is `DirectUserFormationError` rather than `ContractViolation`. Only
`bounded` retention is admitted. Observation time is the envelope timestamp
when it is a valid aware value; otherwise this composition seam requires an
explicit caller-supplied aware observation time and stamps it deterministically.
Missing or naive timestamps are rejected; `datetime.now` is not synthesized.
Over-bound content is refused and never truncated. The existing compile helper
remains compile-only.

Evidence is the focused new plus existing Packet G/client/lifecycle tests (46
passed), Ruff check and format `--check` on touched Python files, mypy on
package source (93 files), `scripts/check_docs.py`, `git diff --check`, and
`python scripts/repository_security_scan.py --scope tree` (486 files, 0
findings). Full repository pytest, hosted CI, private data, and macOS work were
not run. This is not ZF-010 product exit.

## ADR-141: Core-owned Packet E scheduler is explicit opt-in and fail-closed

**Status:** accepted locally on 2026-08-24 as the isolated Packet E
productization slice on the shared foreground capture runtime; this is not
complete Packet E product acceptance, complete Packet H, ZF-010, provider or
network support, hosted/full-suite acceptance, release, or macOS support.
ADR-138 remains the Packet G compile contract. ADR-139 remains the shared
foreground capture runtime and adapter-refresh authority. ADR-140 remains the
separate Packet G direct-user formation mapper and is not this scheduler work.

Core owns one disabled-by-default capture scheduler around the existing
`CaptureScheduler` planner and `CaptureCoordinator`. It does not fork
coordinator, sink, or adapter logic and does not add a scheduler table or
migration. Durable enablement is a content-free machine-local sidecar under
`CoreConfig.data_dir` using the same atomic bounded private-file pattern as
workspace authorization. Dispatch requires the exact process gate
`ATC_CAPTURE_SCHEDULER_ENABLED=1`, a valid sidecar `enabled: true`, and the
absence of `ATC_UPDATE_HEALTH_OPERATION`. Any presence of that update-health
environment variable, including the empty string, force-disables the thread so
installer/update probes cannot start capture. FastAPI lifespan uses the same
`scheduler_update_health_forced_off` helper. Invalid sidecars fail closed as
disabled without killing Core. The scheduler sidecar is read through the same
descriptor-based bounded private-file helper as workspace authorization, so it
inherits the Windows residual that `O_NONBLOCK` and `O_NOFOLLOW` are
unavailable and a blocking named-pipe sidecar can stall the read.

The FastAPI lifespan starts the scheduler only after Core is ready and only
when those gates pass. Authenticated admin enable can also start or revive the
worker. Admin disable and prompt `stop` set an interruptible
`threading.Event` and join with a bound; an in-flight cycle completes rather
than being cancelled. Lifespan `finally` and `CoreService.close` use a
distinct `shutdown` path that sets a permanent closing fence for that
`CoreService` instance, signals stop, and joins the captured worker until it
is dead, without holding the control or lifecycle mutexes, before store and
instance-lock release. After shutdown begins, `enable`/`start` must not clear
stop or revive or spawn a worker in that closing instance. Durable sidecar
enablement may remain for a later Core process, but the closing instance does
not run it. Admin disable-then-enable last-writer revival remains valid only
before shutdown begins. The loop thread is never daemonized, and this slice
does not claim cancellation. At most one worker runs at a time: overlapping
cycles are refused by an in-process global cycle lock. That is not the
coordinator's cross-process per-source lease. Sidecar write and the
start/stop decision are serialized by an in-process control mutex; joins never
run while holding the control or lifecycle mutexes. Disable during an
in-flight cycle followed by enable, before shutdown, revives the same loop or
otherwise leaves the worker eventually running while all gates stay enabled. Waits use `Event.wait` rather than unbounded `sleep`.
Expired and nonterminal capture runs are recovered on Core start and again at
the start of each enabled cycle before due evaluation, so an expired
reconciling source can become retry-due through the existing resume/run
contracts. Every scheduled cycle calls `refresh_local_workspace_adapter`
exactly as admin `run` does. Source lifecycle remains explicit: only due
enabled sources and recovered lease-expired retry paths execute. Expected
`CaptureError` and `OSError` stay content-free; internal programmer failures
are not converted into a fake successful-empty report.

Authenticated admin `GET/POST /v1/admin/capture/scheduler` and contributor CLI
`atc capture scheduler status|enable|disable` are the only control surfaces.
There is no CLI `run_forever` or daemon, no dashboard or desktop auto-enable,
and `/health` keeps the exact `{"status":"ok","component":"core"}` body.
Scheduler partial or disabled state is exposed only through authenticated
capture/admin status. CLI sidecar status omits `running` because it cannot
observe the Core process. Status and health reads do not consume one-shot
reauthorization actions or mutate rotation/scheduling state.

Evidence is the focused scheduler and productization tests (36 passed), Ruff
check and format `--check` on touched Python files, mypy on package source
(94 files),
`scripts/check_docs.py`, `git diff --check`, and
`python scripts/repository_security_scan.py --scope tree` (489 files, 0
findings). Full repository pytest, hosted CI, private data, and macOS work
were not run.

## ADR-138: Packet G L1+ compilation is authorization-first

**Status:** accepted locally on 2026-08-24 for the Packet G + Core Retrieval V3
lifecycle-visibility slice; this is not ZF-010, Packet E/F, complete Packet H,
Phase 2, CoreService/startup, MCP lifecycle, provider, hosted/full-suite,
release, or support acceptance.

Accepted in-process L1+ pre-generation compilation must receive a Core
`ClientPrincipal` before it may call the injected Retrieval V3 compiler.
Missing or non-principal callers fail closed with `MissingCorePrincipal` and
create no request, retrieval, delivery, or generation. Ordinary MCP remains L0:
unsupported pre-generation still returns `UnsupportedHookReport` without that
principal check. Core Retrieval V3 `bootstrap()` continues to accept an
optional principal for its existing CLI/local-admin path.

Canonical memory is seeded only through authenticated Core candidate and
lifecycle APIs. Packet G envelopes are untrusted references and are not current
memory. Host checkpoint restore remains typed ordering/integrity validation, not
client-to-principal binding or a second Core truth. Empty-pack refusal stays a
separate fail-closed path; a surviving authorized record is kept after purge so
that path is not conflated with lifecycle absence.

Evidence is the focused G/client/lifecycle tests (36 passed), Ruff check and
format `--check` on touched Python files, mypy on package source (92 files),
`scripts/check_docs.py`, and `git diff --check`. Full repository pytest, hosted
CI, private data, and macOS work were not run.

## ADR-139: Shared foreground capture runtime authorizes one local workspace

**Status:** accepted locally on 2026-08-24 as the first productized E/F
foundation slice; this is not Packet E scheduling, complete Packet H, ZF-010,
provider/product support, hosted/full-suite acceptance, release, or macOS
support.

`capture_runtime` is the single composition module for `CaptureCoordinator`.
`CoreService` and the contributor CLI must use it and must not construct
divergent coordinators. The composition always injects
`RegisteredSourceCaptureApplicationSink` and registers
`LocalGitWorkspaceCaptureProviderAdapter` only when a valid machine-local
authorization sidecar exists under `CoreConfig.data_dir`.

The sidecar stores exactly one explicitly authorized canonical workspace root
using pathlib and the existing cross-platform atomic/private-file pattern
(temporary file plus replace). The adjacent FileLock is held across the entire
authorize read/identity/source-reconcile/write critical section. Ordinary Core
composition uses a nonblocking lock attempt and fail-closes adapter
registration if the lock is busy, the sidecar is invalid, inventory cannot be
completed, or any capture row is unreadable or malformed, including invalid
`requested_scopes_json` shapes. Core still starts with the vault available and
without the adapter. Authorize on that vault returns a bounded content-free
`CaptureError` rather than a decoder exception or raw row/path.
`filelock.Timeout` and lock/write/unlink `OSError` paths map to a content-free
`CaptureError` with `__cause__` cleared. Authorization fail-closed errors also
suppress exception context (`from None`) so OSError filenames do not survive on
`__context__` or tracebacks. It does not assume POSIX permissions.

Sidecar reads use a descriptor-based bounded path: `os.open` with available
close-on-exec/no-inherit, nonblocking, and nofollow flags, `fstat` of that
descriptor, a regular-file 1..16 KiB size check, a bounded complete read, then
post-open `lstat` plus `os.path.samestat` and symlink/reparse refusal so a
Windows followed reparse still fails closed. The descriptor is always closed
and failures return `None` without content. Invalid sidecars do not prevent
Core startup. Missing, non-directory, symlink, reparse, parent or intermediate
redirecting, UNC/`//` network-style, Windows remote-volume, implicit-home
(`~`), and implicit-cwd (relative) roots fail closed. After `resolve`, the
implementation `lstat`s the original root again and compares root/resolved with
`os.path.samestat` rather than `Path.samefile`, refusing symlink, reparse, and
non-directory results. Explicit Windows extended local-drive prefixes
(`\\?\X:\...` and `//?/X:/...`) unwrap to the ordinary drive form before those
checks; UNC, extended UNC, volume, and device prefixes stay rejected. A changed
root is a new adapter identity; silent retargeting is refused. Path and raw
errors do not cross public status, receipts, logs, or portable export. The root
is never stored in `capture_sources.account_label`. Linux remote filesystems
that are not `//` UNC-style prefixes are not classified in this slice. On
Windows, `O_NONBLOCK` and `O_NOFOLLOW` are unavailable, so a sidecar path that
is already a blocking named pipe can stall composition.

`atc capture authorize-workspace --root PATH --local-only-acknowledged` is the
opt-in command. It computes the adapter identity and creates exactly one
disabled `local-git-workspace` source with scopes exactly `workspace.structure`,
constant account label `local-workspace`, local-only acknowledgement, and a
non-revoked lifecycle. Reconciliation preserves the existing acceptable
non-revoked lifecycle rather than resetting enabled, paused, degraded, or
reconciling to disabled. Workspace-source inventory walks the existing bounded
`list_sources` pages to completion rather than the first 100 rows. The adapter
is registered only when exactly one matching canonical source exists. Generic
contributor CLI `atc capture create` and admin `POST /v1/admin/capture/sources`
reject the reserved `local-git-workspace` provider with
`capture_authorize_workspace_required` after the same Unicode `str.strip`
normalization `CaptureLedger` applies, so leading, trailing, and tab whitespace
cannot become that provider. Other providers are unchanged. The
`CaptureCoordinator.create_source` test seam stays provider-neutral.

Ledger `requested_scopes_json` must decode as a JSON array of opaque scope
strings. Object, string, number, null, and non-string-item shapes fail closed
as `capture_page_malformed` without coercing to an empty tuple and without
leaking the raw JSON. Rows written by `create_source` remain arrays and are
unaffected.

Revoked or pre-existing malformed workspace-source rows are a terminal recovery
boundary in this slice: authorize refuses, the adapter is not registered, ledger
rows are not deleted, and revocation is not weakened. No migration or unique
index is added here; durable database uniqueness for this provider remains a
later hardening. Existing create/enable/run remain the lifecycle and execution
authority for non-reserved providers and for enable/run after authorization.
After enable, a foreground CLI run composes a fresh coordinator. An admin run
on a long-lived Core process removes any stale local-workspace adapter, takes
the nonblocking authorization lock, revalidates sidecar/root/inventory, and
then registers or leaves the adapter unavailable so a sidecar written after
startup can run without restart and a later invalid sidecar fail-closes.
No scheduler loop, scheduler table/config, health UI, or 20-file cap change is
introduced. Continuous scheduling remains a separate Packet E PR.

## ADR-137: Packet H-D records disposable composition evidence without acceptance

**Status:** accepted on 2026-08-24 after reconstructing the Packet H-only
proof delta onto merged PR #78 and landing PR #79 on protected main; this is
not ZF-010, complete Packet H, Phase 2, product/provider, release, or support
acceptance.

Packet H-D reconciles three runner-owned disposable proof lanes without creating
a new authority. The reconstruction base is protected main after PR #78
(`e735d0dde301c64500acd1d404a2bbb6aab6724a`). The proof as written exercises
Packet F local workspace capture, the PR #78 registered-source admission
contract, public Memory Truth, and Retrieval V3. It does not exercise Packet E
scheduler, Packet G reference host, ZF-010 automatic formation, the full Wave 4
E–G composition, the Phase 2 journey, production/CoreService/startup wiring,
provider/client support, release acceptance, private data, or macOS.

H-A fail-closes fake temporary ownership, redirecting `Path` subclasses, mutated
or mismatched roots, nonempty roots, and symlink/reparse roots, returning only
capability-owned canonical roots. The lstat/reparse branch is implemented and
covered by a focused synthetic-stat test; live privileged symlink/reparse
creation is not separately exercised. It gates partial coverage and partial
availability, denied network, and empty egress, then exercises sanitized
local-workspace source admission with four fact-bearing upserts plus one
deterministic no-fact upsert that yields no candidate or record, overflow
fail-closed behavior, and crash/restart/replay. Candidate and record evidence
must be the exact code-owned registered-source evidence for the fact class and
binding. Every CoreStore is closed in a `finally` block before temporary-root
teardown. Its module and direct-file CLIs insert this checkout's repository
root and `packages/allthecontext/src` at the front of `sys.path` and fail closed
if imported `allthecontext` does not resolve under that checkout source. H-B
observes Core through public Memory Truth rather than private capture helpers,
gates local-only posture and all-applied evidence, compares complete public
state plus stable identities across list/detail/replay, binds exact withdrawal
to the public source-reference identity derived from the source ID and target
provider item, requires deleted listing/status, scans public string fields in
native/resolved/POSIX/JSON-escaped path forms, and publishes bounded
content-free aggregate JSON. H-C calls the shared authoritative H-A validator
before retrieval. It recomputes the required acceptance semantics, verifies the
identifier digest, cleanly rejects malformed object trees, and permits
additional true boolean predicates only when digest-bound and never as a bypass
for required predicates. Returned search and bootstrap items used for
acceptance must be structural, and adapter deletion refuses absolute or `..`
relative paths before unlink. It then exercises public Retrieval V3 search,
bootstrap, and get packaging with 4/4 structural recall/provenance/exact-get
checks, a 256-character budget, negative-query exclusions, adapter deletion
exclusion, and deterministic repeats.

Local reconstruction validation: the three Packet H test files passed 61 tests
in 32.15 seconds. Four CLIs passed with `PYTHONPATH` removed so the
checkout-source `sys.path` guard is active: `python -m bench.packet_h`
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
state on exit and emits only bounded content-free aggregate JSON. The exact
commands are recorded in `bench/README.md`. No receipt is added under
`bench/baselines/`: that directory is reserved for frozen benchmark baselines,
while these outputs are disposable proof reports that remain reproducible from
the commands.

This decision does not claim production or startup wiring, a scheduler, provider
or client support, archive import, OAuth or network support, ranking or schema
changes, release state, or support status. It adds no baseline receipt under
`bench/baselines/`; Packet H-D outputs remain disposable proof reports. It does
not relabel this partial proof as complete Packet H, Phase 2, scheduled or
continuous Packet F product acceptance, provider/client support, release
acceptance, private-data evidence, or macOS support. macOS remains
unsupported/deferred.

Historical ADR-132 records the 2026-08-22 stop at the then-missing admission
seam. That blocker is closed locally by ADR-133 / PR #78; ADR-132 remains
historical provenance and is not the live frontier.

## ADR-136: Local workspace discovery caps foreground traversal

**Status:** accepted locally on 2026-08-23 after focused synthetic traversal
regression; this is not provider support, release readiness, hosted/full-suite
acceptance, or macOS support.

`MAX_DISCOVERED_FILES` is a global hard bound across the adapter's explicitly
authorized roots, not merely an item-emission limit. The deterministic sorted
walk counts authorized regular-file candidates before content inspection; when
the bound is reached, it stops before checking or reading later entries and
does not begin another authorized root. The report is marked incomplete, so
the existing `fetch_page` fail-closed behavior remains authoritative. Stable
ordering, credential/Git/dependency exclusions, symlink/reparse checks, and
pathlib-based containment remain unchanged for scans below the bound.

The focused regression uses two sanitized roots and instruments `Path.lstat`
and the adapter's content-read seam to prove that entries after the bound are
not visited or read, that the report is bounded and incomplete, and that the
small existing workspace scan is unchanged. No path or content is emitted by
the adapter diagnostics.

## ADR-132: Packet H stops pending Core source-fact admission

**Status:** accepted locally on 2026-08-22 after a stopped disposable proof;
this is not ZF-010, Packet H, Phase 2, product, hosted-CI, release, or support
acceptance.

Packet H was attempted as a disposable integration of the Wave 3 components and
was correctly stopped and removed. The blocker at that time was a missing
Core-owned registered-source-authoritative admission seam: Core policy then had
no genuine contract that bound Packet F continuous-capture evidence to
deterministic source-fact promotion. `add_candidate` entered the ongoing-client
policy, so non-witness workspace facts remained tentative; the archive importer
was a distinct lifecycle and could not be relabeled as this source path.

A hardcoded allowlist or `explicit_user_statement` relabeling was rejected as
false authority rather than a source-fact admission contract. The next narrow
frontier at that date was to design and review the Core-owned admission
contract in a successor PR. No production wiring, stable SDK, provider/client
support, release state, or macOS support followed from this stopped proof.

This decision remains historical provenance for the 2026-08-22 stop. ADR-133 /
PR #78 later closed the local admission contract; ADR-137 records a later
Packet H-D disposable proof over Packet F, that contract, public Memory Truth,
and Retrieval V3 without complete Packet H or Phase 2 acceptance.

## ADR-133: PR1 admits only closed registered-source structural facts

**Status:** accepted locally on 2026-08-23 as a bounded successor contract to
ADR-132; this is not complete Packet H, ZF-010, product/provider support,
hosted/full-suite acceptance, release readiness, or macOS support.

PR1 adds migration 016's three nullable provenance columns and one partial
unique event index to the existing `context_candidates` table. It adds the
Core-owned `REGISTERED_SOURCE` policy origin and an explicitly injected
internal sink. The sink accepts only the durable source/event/run projection
from `CaptureCoordinator`, only the exact code-owned `workspace.structure`
scope, only the code-owned `local-git-workspace` extractor registry, and only
bounded structural classes with code-owned sentences. Raw
workspace text, paths, roots, account labels, fingerprints, and provider item
IDs never become candidate content, evidence, structured memory, errors,
receipts, or logs; the projection uses an opaque source/item hash reference
while the raw item ID remains in the machine-local capture ledger.

The sink reuses CaptureLedger for ordering, staging, lease, replay, and item
lineage authority, and uses the existing Memory Truth tables for candidate and
record truth. Deterministic registered-source record IDs are supplied only by
the internal evaluation override; user correction, availability, deletion, broken
linkage, and purge barriers remain authoritative. Capture item deletion is a source
withdrawal without an ordinary tombstone, while a purge scrubs linked staged
payloads and blocks future influence. Portable exports omit all machine-local
capture runtime tables and null the new candidate foreign keys; same-database
restart retains capture state.

This PR1 does not construct the sink from `CoreService`, package startup, the
scheduler, or a reference host. It closes only the local admission contract
with focused sanitized evidence. Complete Packet H, production wiring, ZF-010,
product/provider support, hosted/full-suite acceptance, release work, and
macOS remain open or deferred. Packet H-D later records a disposable
foreground proof over this contract without composing Packet E or Packet G.

## ADR-134: Capture recovery stages one bounded page in the existing checkpoint

**Status:** accepted locally on 2026-08-23 as a bounded correctness follow-up
to ADR-133; this is not Packet H, production support, hosted/full-suite
acceptance, release readiness, or macOS support.

Migration 017 extends the existing `capture_checkpoints` row with nullable
pending generation/cursor state and a bounded JSON array of ordered durable
`capture_events.id` values. It creates no second truth table. `stage_page`
validates the live run and stages the complete already-validated page plus its
pending marker in one transaction, preserving the existing provider-event
identity, idempotency, conflict, and lineage rules. Existing pending state is
retryable and cannot be overwritten.

CaptureCoordinator recovery runs before provider fetch. It reconstructs events
only from pending durable event IDs and stored provider payloads, replays each
through the injected Core sink, commits each event idempotently, verifies all
events are applied, then atomically advances the real cursor and clears the
marker. It fetches from the recovered cursor in the same foreground run; an
empty newer generation resets stale order state while same-generation order is
preserved. Correction, availability, ordinary-delete, and purge barriers stay
with the existing Core sink authority. No production startup wiring, scheduler,
provider support, Packet H, private data, or generic absence-to-delete rule is
introduced.

## ADR-135: Capture page identity, metadata, and migration repair guards

**Status:** accepted locally on 2026-08-23 as bounded follow-up corrections;
this is not Packet H, production support, hosted/full-suite acceptance, release
readiness, or macOS support.

Capture page validation rejects duplicate provider event IDs before any page is
staged. `CaptureLedger.stage_page` also uniqueness-guards the resulting durable
pending event IDs in the same transaction, so a duplicate cannot leave partial
pending state or replace an existing pending page. When reading a legacy
pending marker, the bounded raw list and every durable ID are still validated,
then repeated identical IDs are canonicalized in first-occurrence order for
one-time recovery; malformed, non-string, or invalid IDs still fail closed.

The `LocalGitWorkspaceCaptureProviderAdapter` keeps its adapter-produced,
coordinator-path `workspace.structure` events metadata-only. This does not
make the provider-neutral ledger itself metadata-only: it stores the bounded,
internal caller-supplied payload. The registered-source sink keeps extra
caller-supplied fields inert and does not project them into Memory Truth; this
does not weaken the adapter's actual metadata-only guarantee. Source text and
text excerpts do not enter the adapter's durable capture event payload or the
registered-source projection; the bounded structural projection remains the
only admitted content.

Capture schema repair is limited to capture migration versions already recorded
as applied, and runs through that version inside the transaction that is about
to apply a newer migration. Successful repair retains the complete repaired
state. The architecture data model already records migration 017 as used and
018 as next; this decision does not renumber migrations.

The historical evidence is 58 focused tests for duplicate IDs, 63 focused tests
for metadata-only workspace events, and 8 capture migration tests for repair.
The integration owner subsequently ran 152 combined focused tests on integrated
code. Reported Ruff lint, Ruff format-check, and mypy checks passed. These are
bounded local reports only and do not establish full-suite acceptance. No
production startup wiring, scheduler, provider/product support, Packet H,
private-data evidence, or macOS support follows from these corrections.

## ADR-131: Wave 3 components remain experimental until Packet H

**Status:** accepted locally on 2026-08-22 from focused component handoffs;
this is not ZF-007/ZF-008/ZF-009 product acceptance, a real integration pair,
production wiring, hosted/full-matrix acceptance, release evidence, or a
support claim.

Wave 3 keeps the three component seams narrow until complete Packet H composes
them in a disposable Core and runs the Phase 2 journey. Packet E's
`capture_scheduler.py` is disabled by default and orchestrates the existing
`CaptureCoordinator`; it owns no cursor, event ledger, lease, checkpoint,
durable scheduler state, or durable notification state. Its focused contract
uses the existing persisted retry/backoff path, bounded `Retry-After`, source
rotation, concurrency/resource limits, truncated-health degradation, and
in-process reauthorization notification deduplication.

Packet F's `experimental_local_git_workspace_connector.py` is an explicit-root,
read-only local adapter. Before scanning, `fetch_page` requires both the local
provider identity and `source.account_fingerprint == adapter.source_identity`;
a mismatch fails closed without scanning. It executes no Git command or other
process, uses no network, declares partial coverage, fails closed on incomplete
scans, omits AWS `AKIA`/`ASIA`-shaped content, and excludes Git/dependency/
credential paths plus symlink/reparse paths. Its metadata-only cursor and
bounded samples track at most 20 files. It is not a general connector or
provider-support claim and is not wired into install or Core startup.

Packet G's `experimental_reference_host.py` is a controlled in-process host
capped at L2; ordinary MCP remains L0 and an L3 request is truthfully
downgraded. It routes pre-generation through injected Core Retrieval V3,
captures direct-user references, and sends typed lifecycle snapshots to an
injected checkpoint sink on checkpoint, session-transition, and completion
hooks. A typed restore includes events, trace, pending and delivered context,
started-generation IDs, and sequencing state. Its deterministic digest is
integrity validation only, never authentication, and the sink receives a stable
idempotency key for retries. L0/ordinary MCP resumes started IDs without
fabricating context; L1+ restores request/delivery ordering, and empty Core
context fails closed. It rejects forged resume sessions and secret-like
payloads, has no client-principal binding or production persistence, and adds
no provider integration, general persistence format, or stable SDK.

These component boundaries do not close the first real continuous source/client
pair, automatic formation ZF-010, complete Packet H, the Phase 2 acceptance
journey, production startup wiring, or any release/support state. Packet E and
Packet G remain component-complete; remaining work is their composition and
product acceptance, ZF-010, complete Wave 4 E–G, and Phase 2. Packet H-D later
records a disposable foreground proof over Packet F, the PR #78 admission
contract, public Memory Truth, and Retrieval V3; it does not compose Packet E
or Packet G and is not complete Packet H. macOS remains absent and deferred
exactly as recorded by the current project truth.

## ADR-130: Wave 1 reconciliation is an opaque metadata boundary

**Status:** accepted locally on 2026-08-22 after isolated synthetic contract
tests; this is not provider, SDK, MCP lifecycle, release, or hosted acceptance.

ZF-004 Wave 1 normalizes either the existing `CaptureEvent` or the exact
`ClientLifecycleEnvelope` into one immutable `EventReconciliationInput` for a
later formation step. The existing lifecycle envelope declares no linkage to a
capture event, so the composed call is rejected until a real link contract
exists. The slice reuses source/event IDs, capture generation and order, the
existing capture normalizer's operation, commitment, and bounded byte size,
source cursor and idempotency material, lifecycle hook/session and payload
references with optional commitment/size, lifecycle idempotency
`(client_id, event_id, sequence)`, and bounded
account/client/conversation/task/workspace/project/artifact references. Raw
source text is never copied or returned by `as_dict()`.

Dependency withdrawals are actual immutable tuples of typed Packet C
cause/action declarations. Authorized ordinary-delete withdrawals must match
the deleted provider item; terminal purge is allowed only with `ERASE`.
Secret-like direct metadata and malformed exact-contract evidence fail with
codes only. The module has no persistence, replay, cursor advancement, ID
minting, or mutation, and has no direct storage, network, or provider-SDK
imports or stable integration claim. Any formation, Core policy, storage, and
harness wiring belongs to the later integration-owned seam.

## ADR-001: Core is the sole authority

Relay stores a projection and proposal queue only. Application events, not
database files, cross the boundary.

External memory systems are research suppliers or discardable projections, not
additional authorities. Intake requires an official origin, immutable revision,
license and component caveat review, dependency/data-flow inventory, and an
isolated benchmark before any code reuse or execution.

## ADR-002: SQLite-first storage

Core uses SQLite/FTS5. The first Relay slice also supports SQLite for a complete
local/integration path; its storage boundary permits PostgreSQL in hosted
deployment without changing event contracts.

## ADR-003: Review-first approval with policy hook

**Status:** superseded by ADR-039 on 2026-07-23.

The original V1 design made extracted and model-proposed context wait for
routine user review. That boundary proved incompatible with the intended
configure-once product: it made the user administer a memory database. The
historical schema and APIs remain migration inputs, not the current product
contract.

## ADR-004: One-time MCP forwarding setup

The adapter is transport glue, not an authority. Generated config pins a target,
client ID, and credential source so models can retrieve and propose memory
without repeated setup.

## ADR-005: Official MCP v2 and HTTPX2 compatibility lane

As of 2026-08-22 the official Python SDK v2 line is stable. ATC constrains the
runtime to `mcp>=2,<3` and raises the MCP-required Pydantic/AnyIO floors. The
local and Edge adapters use the supported `MCPServer` registration surface;
STDIO uses the SDK's descriptor-isolated runner, and Streamable HTTP transport
options are supplied at app construction. Edge OAuth provider settings remain
on the server's supported auth surface, while bearer protection, Core
authority, loopback defaults, and content bounds remain ATC-owned boundaries.
The hosted Edge binds the SDK's request ceiling and ATC's outer request guard to
the same 256 KiB constant, while the loopback HTTP adapter retains the SDK's
4 MiB default.

The v2 SDK serves both modern and 2025-era MCP clients on the same Streamable
HTTP app. This is an interoperability migration only: ordinary MCP remains an
L0 integration and does not provide lifecycle-aware L1-L3 hooks or make such a
claim for ATC.

First-party production HTTP call sites use `httpx2>=2.12,<3`, including the
library's OS trust-store path; hosted Windows/Linux verification remains a CI
responsibility. Legacy `httpx` is not a runtime dependency; it remains in the
development extra only because Starlette's in-process `TestClient` still imports
that package. The packaged first-run smoke streams response bodies in bounded
chunks, refuses responses over 1 MiB, and never includes rejected response
content in the error.

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

## ADR-009: Desktop-first one-time setup

The supported user path is a native first-run wizard, not a sequence of Python
or shell commands. A frozen Windows executable self-installs per-user, embeds a
separate console-subsystem MCP helper, initializes the vault, verifies credential
persistence, updates supported client configuration reversibly, enables
per-user startup when selected, and opens the dashboard through a one-use
loopback ticket exchanged for a tab-scoped opaque session. The session maps to
the administrator credential only in Core memory; the credential is never put
in a browser cookie, URL, or browser storage. Timezone is detected from the
operating system instead of collected in the wizard. Codex and Claude Desktop
receive separate scoped identities and reversible config updates.
Session-authenticated mutations also require a dashboard-only custom header.
Source bootstrap and CLI commands remain contributor and automation interfaces.

## ADR-010: Local and cloud clients have different connection paths

Desktop clients on the Core device use the packaged STDIO adapter. Codex uses
its documented `config.toml` MCP configuration; Claude Desktop receives its own
configuration and client identity. Web and mobile clients cannot reach
`127.0.0.1`, so they use the HTTPS Edge MCP endpoint with OAuth 2.1, PKCE,
audience binding, rotating refresh tokens, and owner consent. Core creates and
keeps the Edge enrollment secret, verifies a per-instance proof before sending
its bearer credential, and remains the sole writable authority. Provider plan,
admin, and surface limitations are shown in the UI rather than hidden.

## ADR-011: Personal Edge registration is owner-gated and recoverable

Dynamic OAuth client registration is available only during a persisted,
ten-minute owner window opened from Core or with the recovery code. Registration
has per-origin/global rate limits, strict metadata and redirect bounds, and a
bounded client table. The recovery code is hashed at deployment and entered only
on the Edge owner page. Decommission persists a terminal state, revokes OAuth
material, purges every vault artifact, and rejects old tickets, tokens, and
signed replication events.

## ADR-012: Edge proposals are encrypted transport, not current context

An OAuth client with proposal scope may enqueue a bounded AES-GCM transport
envelope while Core is unavailable. The queue is capped by count and bytes,
expires after 30 days, and is scrubbed after Core acknowledges import or
rejection. This is an explicit transport exception to the readable Edge
projection: it never becomes current context at Edge, and it is not
zero-knowledge against an operator who controls the Edge process and its
replication secret.

## ADR-013: A persistent Edge disk is bound to one authority

On first OAuth-enabled startup, Edge persists a singleton identity binding for
the vault, pairing-secret fingerprint, and normalized public origin. Later
starts must match all three values before serving MCP or applying replication.
This prevents a valid old access or refresh token from becoming valid against a
different vault after an operator repoints the same SQLite disk or replaces an
enrollment secret. Edge also applies global body and query bounds before route
parsing (including chunked bodies), bounded filter cardinality and field sizes,
and iterative database paging before permission filtering. The goal is a
personal-scale service with finite work per request, not an unbounded public
search endpoint.

## ADR-014: Terminal Edge state is enforced inside write transactions

HTTP middleware is not the decommission boundary because a request can pass a
guard and pause before its database write. Every replication, proposal, and
OAuth write rechecks terminal state inside the same `BEGIN IMMEDIATE`
transaction, and SQLite triggers reject direct post-terminal inserts/updates.
Core serializes sync/decommission/forget state with both an in-process lock and
a cross-process file lock. A terminal Edge can restart after an interrupted
purge, but it cannot accept new authority or data while finishing cleanup.

## ADR-015: Managed local MCP connections self-heal Core

Generated Codex and Claude Desktop entries opt into bounded Core restart and
carry the exact installed Core command. Before each tool call, the STDIO
adapter probes the configured `127.0.0.1` endpoint with the installation-bound
challenge proof. It starts Core only when that endpoint is unreachable, never
when an unverified listener owns the port, and never for a remote/Edge target.
This turns startup-at-login into an optimization rather than a user-visible
recovery requirement.

## ADR-016: Uninstall removes connection authority before application files

Uninstall first decommissions a paired Edge, then stops Core and preflights all
managed Codex/Claude configs plus ATC-created backups. With a readable vault it
revokes every named AI principal before best-effort secret cleanup. With a
missing/corrupt vault it derives the authoritative credential store from the
managed config and verifies deletion before changing any file. Existing token-
bearing ATC backups are scrubbed without creating a new backup; exact-content
checks make concurrent edits fail retryably. A corrupt retained vault is kept
with an explicit warning that its internal rows could not be revoked.

## ADR-017: Dashboard backup is a bounded encrypted download; restore stays deliberate

The Core status contract reports `database_size_bytes` as the durable SQLite
footprint: the main database plus the write-ahead log when present, excluding
the transient shared-memory file. This is a stable, cross-platform `pathlib`
measurement and reflects durable bytes rather than one implementation file.

The dashboard may request one complete portable export at a time through an
administrator, same-origin-protected POST. Its passphrase exists only in the
JSON request body and in request-local memory; it is not logged, persisted,
placed in a URL, or repeated in an error. Core reuses the CLI's AES-GCM portable
export implementation, enforces a configured durable-footprint bound, streams
the encrypted file, disables response caching, and deletes its temporary file
after success or failure. The CLI contract is unchanged.

Dashboard restore is deferred. A safe native restore requires stopping Core,
validating into an isolated destination, transactional migration and rollback,
post-restore checks, and an explicit vault switch. Adding an upload button
without that lifecycle would create a destructive recovery path.

## ADR-018: Freeze Retrieval V1 before changing ranking

Retrieval V2 begins with a versioned, offline synthetic corpus and a checked-in
machine-readable V1 baseline at deterministic 1k and 10k scales. A bounded 50k
profile is explicit opt-in. This keeps ordinary CI finite and makes quality,
policy, temporal, context-compilation, latency, storage, and mutation tradeoffs
visible before production ranking changes.

Hard policy remains outside and before the replaceable `CandidateRanker` seam.
The V1 implementation preserves FTS5 BM25/recency ordering, while an invariant
test injects a failing spy to prove policy-rejected records never reach
relevance scoring. V2 must pass the executable comparison gates; the existence
of Phase 0 is not evidence that a future ranker passes them.

## ADR-019: Offline-signed immutable OTA metadata

Release candidates are native, versioned artifacts built from a full commit
SHA. GitHub may build an unpublished draft and attach checksums, SPDX metadata,
and provenance, but it never receives the Ed25519 release private key. An
operator signs the strict v1 manifest offline after verifying the candidate;
only reviewed public keys live in the repository. Mutable channel pointers may
select a signed manifest, but executable URLs must be HTTPS, versioned, and
must never resolve through `main` or `latest`. Downgrades are rejected. Desktop
update download and installation are explicitly deferred to a separately
reviewed implementation.

## ADR-020: Edge images are release- or commit-addressed

The hosted Edge image is published to GHCR only from a published release or an
explicit full commit SHA. Every deployment record uses the returned OCI digest;
`latest` is not a deployment input. OCI metadata, BuildKit provenance/SBOM, and
GitHub provenance accompany the image. Making the package public and creating
paid hosting remain explicit operator actions.

## ADR-021: Retrieval V2 remains lexical and policy-first

Phase 1 keeps Core as the only current-context authority and decomposes retrieval
behind the existing facade into eligible-record selection, bounded lexical
channels, reciprocal-rank fusion, context compilation, and internal ranking
explanations. Authorization and lifecycle predicates produce the eligible ID
set before any FTS/BM25 channel executes. The temporary permitted-ID table is a
derived query artifact, not authority.

Phrase, all-term AND, and broad OR channels are capped at 256 candidates each.
RRF combines their ranks with small deterministic coverage, phrase, kind, tag,
project, and explicit-preference signals; recency only breaks otherwise equal
ranking. The small lexical alias table is source-controlled and independent of
vault contents. No embeddings or graph database are introduced.

Ranking explanations are not an MCP contract. They are limited to authorized
returned IDs and exposed initially through the local administrator CLI. Context
compilation reserves preference budget, suppresses normalized exact and
conservative near duplicates, diversifies kinds/projects/sources, and orders
support after primary answers. This intentionally trades frozen-gold coverage
of duplicate records for lower compiled-context redundancy.

## ADR-022: Memory slots are advisory; purge is an irreversible Core state machine

Entity and attribute keys are optional observation metadata, normalized for
deterministic grouping and conflict policy. They do not create current context
by themselves. An exact matching value reinforces the existing applied record.
Materially different values in the same current slot are resolved by the
versioned Core policy using targeted-correction intent, explicitness,
`observed_at`, and stable tie breakers. The prior value, evidence, and decision
remain in history. Derived duplicate/conflict groups are optional integrity
diagnostics, never a user approval queue.

Deletion and purge are distinct. Delete preserves the current-context row, versions,
source provenance, and deletion tombstone for reversible history. Purge requires
administrator scope plus the exact `PURGE RECORD <id>` or `PURGE SOURCE <id>`
phrase. Its logical transaction removes attributable content, candidates,
history, indexes, provenance, batch payload fingerprints, and content-bearing
audit/outbox state. It retains only opaque stable-ID tombstones, job state, audit
coordinates, and an exact-shape ordered purge event. Content hashes are not
retained as purge proof because low-entropy secrets may be guessable.

Physical SQLite cleanup is a resumable second phase: secure deletion is enabled
on every connection, temp storage stays in memory, WAL is checkpointed, disk is
preflighted, and one bounded job runs VACUUM. A crash, lock, or insufficient
disk cannot roll back logical absence; it leaves a retryable pending job. This
boundary makes no claim about snapshots, device remanence, external backups,
or user copies.

## ADR-023: Online Core retrieval uses a bounded outbound-only broker

Edge may queue an authenticated read request only after OAuth identifies a
logical client. The request payload is sealed to Core's X25519 public key before
SQLite or its WAL sees it. Core polls Edge over the existing bearer-authenticated
HTTPS channel, ignores Edge-asserted scopes, resolves the identity from a
user-approved Core-local remote-client mapping, and re-authorizes current
records against that mapping and per-record allow/deny policy. It returns only
`core_available` records. Edge never exposes loopback Core.
Random IDs, expiries, one-use claim hashes, leases, cancellation, response
limits, and durable cleanup make retries and restarts safe. Results remain only
in bounded memory in the waiting Edge process; an Edge restart safely becomes
unavailable rather than persisting private content. `local_only` is
categorically excluded. The durable
`always_available` projection remains independently usable while Core is off.

Core-approved `context_scopes` are capability boundaries on every forwarded
record from search, bootstrap, and direct `get_context_item` requests. `*`
explicitly grants every record scope; an empty grant exposes only records with
no context scope, matching Relay visibility semantics. Out-of-grant records are
reported as not found or omitted, and they do not contribute to forwarded
search counts or bootstrap character totals.

The Render handoff carries only a 24-hour claim reference and Core public keys.
The deployed Edge stays inert until Core signs an origin-bound challenge. Edge
generates durable credentials locally and encrypts them to Core; acknowledgement
revokes the claim. Render still requires a provider-owned Blueprint approval
and environment-file upload, while AI providers require connector creation and
OAuth consent. ATC does not claim to automate or have observed those external
handshakes.

The provider terminates OAuth at Edge, so a fully compromised live Edge can
assert another already Core-approved logical client and observe response bytes
while forwarding them. Core-local approval prevents unknown, revoked, or
Edge-invented administrative authority; it is not end-to-end client attestation.
Use separate Edge deployments for mutually distrusting client domains until a
provider transport can carry a client-held proof through to Core.

## ADR-024: OTA verification and installation are separate fail-closed phases

The Core owns a serialized update transaction and stores only nonsecret
preferences and recovery state below its platformdirs-derived per-user data
directory. Stable releases default to stable; ADR-034 defines the reviewed
prerelease bootstrap behavior. Launch checks run only when a reviewed HTTPS
endpoint is configured and at most once per 24 hours. Metadata is
size/time/redirect bounded, then must pass the strict manifest schema, active
Ed25519 key, channel, platform, architecture, and version policy before its
artifact URL is used. Artifacts stream into private per-operation staging and
must match both signed byte length and SHA-256.

Installer, backup, health, transport, and rollback behavior are explicit
interfaces. Windows automatic installation is enabled only when the frozen
desktop, stable installed application, and separately packaged recovery helper
are all present. That helper owns the native cutover and rollback described in
ADR-026. macOS app bundles and Linux standalone archives still stop after
verified staging because neither has a reviewed automatic cutover. Persisted
phases make interrupted checks and downloads cleanable; a manual-required
platform can save a newly reverified package without receiving a private
staging path.

All preference/state mutations share the transaction gate and use atomic
same-directory replacement. Invalid persisted versions, phases, identifiers,
or private paths reset to an operator-visible error instead of entering
recovery with untrusted state. Unsupported and non-64-bit architectures fail
before channel selection. Manual-required packages are available only through
an authenticated, no-store Core response that re-verifies the signed manifest,
target, exact length, and SHA-256 while copying to a one-response temporary
file; private staging paths remain undisclosed.

## ADR-025: Edge applies irreversible purge before retryable physical compaction

Relay migration 0009 adds an opaque purge tombstone and singleton compaction
state. A valid next-sequence `record_purged` event transactionally removes the
live record, FTS row, ordinary deletion tombstone, supersession references, and
historical content-derived event fingerprints for that stable ID. The same
transaction advances the stream checkpoint, stores the exact purge event for
idempotent replay, creates the hash-free resurrection barrier, and marks
physical compaction pending. Later upsert, withdrawal, or deletion events for
the purged stable ID fail closed.

Logical absence is authoritative even when SQLite is locked or VACUUM is
interrupted. Edge retries WAL truncation and secure-delete VACUUM at process
startup and whenever Core requests status. The status contract exposes only a
pending flag, timestamps, and fixed error codes; Core records the advanced
sequence but reports synchronization as degraded until compaction succeeds.
Tests close and reopen the store after an injected lock, reject resurrection,
and scan the live database, WAL, and shared-memory files for both raw content
and its SHA-256. This protects the live Edge storage set, not provider snapshots,
external backups, media remanence, or user-created copies.

## ADR-026: Windows cutover belongs to a separate journaled recovery executable

The Windows desktop bundles `AllTheContextUpdater.exe` and installs a stable
copy next to the application and STDIO MCP adapter. For each update, Core makes
a verified SQLite backup, copies the current application, MCP adapter, and
updater into an operation-scoped rollback directory, copies the recovery helper
outside the binary being replaced, writes an exact-schema journal below the
per-user update directory, registers a per-user RunOnce recovery command, and
then exits. Journal paths are constrained to the expected Core data and
per-user installation roots; replacement bytes retain the digest and size from
the already verified release archive.

After the old process exits, the helper refreshes the backup from the stopped
Core database so writes completed during HTTP handoff are not lost. It applies
the replacement, verifies the installed application and helper files, runs
frozen diagnostics, starts the real migrated Core once on `127.0.0.1`, probes
its exact health response, shuts it down, and runs SQLite `quick_check`. Only
then does it commit state, remove RunOnce recovery, and relaunch Core. Ordinary
Core startup refuses to race an active journal; only the matching one-shot
health process may start during cutover.

Every phase is durable and protected by a cross-process lock. A crash can
resume from the last phase. A failure before cutover marks the attempt stopped
without overwriting the still-current application or database. A failure after
cutover restores the prior application, MCP adapter, updater helper, and final
stopped-Core database, removes WAL/shared-memory sidecars, records a terminal
rollback, and relaunches the prior Core. Recovery inputs and persisted error
codes are bounded and path-validated. The latest terminal journal is retained
until a later operation supersedes it.

This decision establishes an exercised engineering recovery boundary, not a
public release claim. Community Windows OTA still requires an offline release
key ceremony, immutable channel publication, and a real Ed25519-signed N-1
release drill. macOS and Linux remain manual-required.

## ADR-027: Managed local integrations are installation-aware and vault-bound

The Connections API reports whether each supported desktop application is
actually detected. A missing application is shown as **Not installed**, links
to the official download page, and cannot receive a generated configuration or
credential. A configuration directory by itself is not installation evidence.

Every managed STDIO MCP entry includes the absolute `ATC_CORE_DATA_DIR` for the
authoritative vault alongside its loopback URL and client identity. Launch
migration adds this value to older managed entries. Connection status compares
it with the active Core using platform path semantics and offers Repair on a
mismatch. This lets isolated/non-default instances self-start their own Core
without confusing a live Core on the same port for another vault.

## ADR-028: Community releases do not depend on paid publisher signing

All The Context is distributed as an open-source, zero-cost community project.
Paid Authenticode certificates, Apple Developer membership/notarization, and
commercial signing services are not release requirements. Native publisher
signing may be added later if it is donated or sponsored, but its absence does
not block a community release.

Unsigned artifacts must be labeled honestly. Release integrity instead relies
on the public GitHub repository and immutable GitHub Release assets, SHA-256
sidecars, SBOM/provenance, reproducible source inspection, and an offline
Ed25519 key whose reviewed public half ships with the application. The updater
continues to fail closed on a missing/invalid manifest signature, wrong target,
size mismatch, or digest mismatch. Windows and, under the historical
four-platform plan, macOS first-install publisher warnings are an accepted
usability tradeoff and must be disclosed rather than bypassed or described as
signed. ADR-086 later removed macOS from the public-beta support table; the
`0.1.0-beta.2` first-install warning surface is Windows SmartScreen only.

## ADR-029: Platform-only APIs are late-bound behind typed compatibility helpers

Runtime guards remain the authority for entering Windows-specific registry,
DLL, and process-creation paths. Those APIs are loaded only after the guard
through `platform_compat.py`; shared modules do not directly expose
platform-conditional standard-library attributes to the type checker. This
keeps normal execution native while allowing the complete shared package to be
checked against Windows, macOS, and Linux types instead of suppressing
`attr-defined` errors globally.

Dashboard download tests use transport-neutral response bodies rather than
constructing a Node `Response` from a jsdom-specific `Blob`. The production API
continues to return a browser `Blob`; only the test fixture crosses the
Node/jsdom boundary. CI retains both supported Node versions so compatibility
failures remain observable.

## ADR-030: Distribution acceptance precedes new retrieval infrastructure

**Status:** accepted as the then-current distribution milestone. Later ADRs
superseded its hosted-Edge path (ADR-032) and its `0.1.0-beta.1` / three-OS
product-scope (ADR-053, then ADR-086). The historical text is unchanged.

The next milestone is the installable `0.1.0-beta.1` community release, a real
hosted Edge/provider acceptance pass, and a signed beta1-to-beta2 update and
rollback drill. Embeddings and other backend expansion are deferred until those
distribution paths are observed end to end.

Release automation may assemble and verify drafts, but it cannot silently
enable hosting, publish a release or channel, create a production private key,
or convert incomplete evidence into approval. The integration lead freezes one
source commit; platform, supply-chain, privacy, hosted Edge, provider, and OTA
evidence attach to that identity. Operator-controlled publication, provider
accounts/costs, and the offline Ed25519 ceremony remain explicit human gates.
The complete evidence contract is maintained in
`docs/operations/BETA_ACCEPTANCE.md`.

## ADR-031: Beta 1 separates native installation, OTA eligibility, and Edge activation

**Status:** accepted as the then-current Beta 1 packaging split. ADR-032
superseded its hosted-Edge activation path. ADR-086 later removed macOS from
the public-beta support table and release composition; the Mac DMG and
manual-required Mac OTA sentences below remain historical and are not a
`0.1.0-beta.2` package, support, or execution requirement.

Beta 1 publishes direct unsigned native packages for people to install:
Windows uses a one-click `.exe`, macOS uses a `.dmg` containing the per-user
self-installing application, and Linux uses a deterministic portable `.tar.gz`.
The macOS build restores an identity-free ad-hoc structural seal after its
`Info.plist` is finalized and verifies that seal. This costs nothing and detects
bundle damage; it is not publisher identity or notarization. AppImage remains a
follow-up until its toolchain is pinned and its desktop integration is observed.

Direct packages and updater ZIPs are different release assets. Only Windows
x86_64 is eligible for automatic Beta 1 OTA because its independent journaled
helper has exercised interruption recovery and full rollback. macOS and Linux
may verify and save release packages, but installation remains manual until
equivalent native cutover safety is implemented and observed.

Hosted Edge distribution is also deliberately staged. Commit A supplies the
reviewed image source and permanent deployment template; the manual workflow
publishes and anonymously verifies A's immutable image digest. Commit B adds
the exact digest-pinned Blueprint and a one-use digest-derived deployment
branch. Commit C packages the deploy URL and A/B identities into Core only
after the activation tool proves the template, Blueprint, digest, branch, and
commits agree. No GitHub Release event automatically performs these steps, and
no provider resource is created without an operator decision.

## ADR-032: V1 is single-Core and has no hosted runtime

**Status:** accepted 2026-07-22; supersedes the hosted-Edge portions of
ADR-030 and ADR-031. Its remote-client scope was superseded by ADR-053 on
2026-07-25; the active beta is same-device only.

The decision established one authoritative Core and rejected a hosted Edge,
cloud replica, Render account, GHCR runtime image, provider bill, or other
third-party context service. It originally contemplated future direct mobile
and other-computer clients. ADR-053 narrowed the first usable beta to
same-device desktop clients; remote and mobile access are post-V1.

Core continues to bind only to `127.0.0.1` by default. Removing Edge does not
authorize automatic LAN/public exposure: secure direct-Core mobile access
requires explicit device pairing, encrypted transport, revocation, discovery,
and recovery acceptance first. Until that work is complete, the UI states the
limitation rather than offering an unsafe shortcut.

The `always_available` schema value and experimental Relay modules remain
temporarily for import/history compatibility and safe cleanup of engineering
setups. Newly applied context uses only `local_only` and `core_available`.
ADR-053 and B-103 strengthen the beta boundary: ordinary Core Edge/Relay
operation paths are removed or tombstoned from the supported product surface,
and the retained cleanup path is isolated under legacy-edge API/CLI commands
that cannot enroll, connect, sync, or create a second authority by default.

## ADR-054: Same-version OTA acceptance is explicit and fail-closed

**Status:** accepted 2026-07-25.

Ordinary channel checks report `current` when a signed offer equals the
installed version. Beta1 exact-candidate acceptance still needs that already
verified same-version candidate to exercise download, transactional
replacement, health verification, and rollback without fabricating a newer
release or weakening trust checks.

`UpdateManager.accept_exact_candidate` therefore reopens only a phase=`current`
offer whose offered version equals the installed version, re-verifies the
stored signed manifest against the keyring, platform, architecture, and
channel, performs no network I/O, and transitions the phase to `available` so
the existing download/install/recover path can run. A newer available offer is
rejected. The Core admin route is
`POST /v1/admin/updates/accept-exact-candidate`.

This is an engineering acceptance helper for exact-candidate same-version
smoke. It is not public promotion, not a substitute for a real signed
beta1-to-beta2 N-1 receipt, and not a weakening of signature, hash, platform,
channel, or key policy.

## ADR-033: Provider history is preserved completely but evaluated selectively

**Status:** accepted 2026-07-22.

Initial memory bootstrap uses user-requested account exports, not provider API
keys, browser scraping, account credentials, or a recurring cloud connection.
Core stores the accepted ZIP/JSON/JSONL/Markdown/text source byte-for-byte in
its content-addressed local source store. HTTP uploads and Core source writes
are streamed; ZIP entries are read in place; root conversation arrays are
decoded one conversation at a time. The default and maximum raw archive limit
is 2,000,000,000 bytes, which an operator may lower. Raw size acceptance does
not relax expanded-text, entry, compression-ratio, or per-conversation parse
bounds.

Provider adapters normalize documented ChatGPT conversation JSON, common
Claude `chat_messages`/memory data, flexible Grok JSON, and Grok Build-style
Markdown transcripts. ChatGPT officially documents `conversations.json` and
numbered conversation JSON files. Claude and Grok do not publish stable field
contracts for every export, so their adapters detect bounded envelopes and
must report unrecognized material rather than guessing silently.

Raw completeness and current context are separate promises. Every recognized
message contributes to aggregate coverage, but only eligible user-authored
durable statements and dedicated provider memory/profile fields can create
observations. Assistant, system, tool, attachment, and instruction-like content
remains inert raw evidence and is ignored for context maintenance.
Provider-synthesized memory is not marked as an explicit user statement and is
tentative by default. User-authored observations are evaluated automatically
only when the ingestion session finishes successfully; a failed or
unfinished session changes no current context.

Each source records provider, format, parser version, statistics, warnings, and
`processing`/`failed`/`complete` status. The source ID and parser version key the
ingestion session; source hash, parser version, and batch ordinal key
observation batches. A retry reopens the preserved BLOB through a bounded
temporary file and replays completed batches exactly, allowing one-click crash
recovery without another upload or duplicate observations or decisions. A
future learned extractor can use a new parser version against the same raw
source without changing this authority boundary.

## ADR-034: Packaged beta updates have a trust-gated default channel

**Status:** accepted 2026-07-22.

A packaged Windows x86_64 prerelease whose embedded keyring contains an active
beta key uses the canonical project Pages manifest endpoint and selects beta on
first run. Packaging is normally proved by the frozen-runtime marker. An
installed `AllTheContext.exe` may also prove it with its exact executable name
and adjacent `AllTheContextUpdater.exe`; this covers a frozen child process that
loses the marker without enabling source Python runs. A legacy persisted stable
selection moves to beta only when stable has no configured endpoint and beta
does. Source runs, unsupported targets, and packages without an active beta key
infer no endpoint. Environment variables remain explicit overrides for forks
and acceptance environments.

GitHub's immutable versioned release download URL responds with a temporary CDN
redirect. Artifact download may therefore follow exactly one HTTPS redirect,
and only from a structurally versioned `github.com` release-asset path to the
exact `release-assets.githubusercontent.com/github-production-release-asset/`
origin and path prefix with a signed query. Manifest fetches, different
origins/paths, and further redirects remain refused. Redirect acceptance does
not confer trust on bytes: the already verified Ed25519 manifest's exact size
and SHA-256 are still required before staging succeeds.

## ADR-035: The first beta OTA trust root is operator-held and free

**Status:** accepted 2026-07-22.

The first community beta update key is `release-2026-a`, an Ed25519 key
generated on an operator-controlled Windows system outside the checkout and
cloud-synchronized workspace. Its encrypted PKCS8 private half remains with
the release owner. Only the beta-authorized public half is tracked and embedded
in packages, with fingerprint
`sha256:fe05a2bd52db97f808650fb0e832c49bd704abd62a813af4dedca4994f98e0d4`.

This free manifest-signing identity authenticates OTA metadata and is separate
from paid native publisher signing, so it does not remove Windows or macOS
first-install warnings. Two recoverable encrypted private-key backups must be
verified before first use. Losing the only trusted private half requires a
separately authenticated manual recovery release; suspected compromise stops
all publication and promotion.

## ADR-036: Retrieval V3 separates authority, time, relevance, and admissibility

**Status:** accepted 2026-07-22; advances ADR-018 and ADR-021 without adding a
hosted or vector authority.

Authorization is the first retrieval boundary. The temporal resolver receives
only authorized opaque IDs; lexical ranking receives only IDs selected by that
resolver; admissibility receives only authorized, temporally eligible rows and
passes numeric factors rather than raw context to its gate. Administrator
diagnostics use closed reason codes and aggregates. Returned authorized IDs may
be explained, but rejected or unauthorized IDs and raw content are absent.

Temporal state is a separate content-free SQLite sidecar using its own schema
version. Core records and purge tombstones remain authoritative. The sidecar is
discardable, migratable, and reconciled after startup, current-context mutations, and
restore. Intervals are UTC half-open, expiry is exclusive, supersession remains
effective after a superseder expires, and deletion/purge are terminal even for
historical queries. Ordinary current records use a deterministic fast path;
`as_of` resolves the complete authorized set. An in-place correction is the
latest current-record content for its stable ID; earlier content remains available
through record history, while separate superseding records are searchable by
historical instant.

Production lexical retrieval uses weighted BM25 over a temporary candidate-only
FTS5 corpus. Exact channels precede carefully bounded OR/prefix fallback, and
FTS5 secure-delete is enabled only when the linked SQLite build accepts it.
Admissibility combines task/query coverage, project/scope fit, requested-kind
fit, confidence/explicitness, and conflict state with a conservative fail-open
rule. A learned gate can observe sanitized features in shadow but cannot reject,
reorder, or create current context.

The V2 comparator remains a named frozen pipeline, not the current production
default. The combined gate requires exact Recall@5 and semantic coverage at
least that comparator, improved temporal and admissibility precision, zero
policy violations and duplicate redundancy, deterministic rankings/conflicts,
no deletion/purge resurrection, exercised restart/restore/history paths, and a
10k warm p95 below 150 ms. Dense retrieval, late interaction, rerankers, and ANN
remain experiments until stage diagnostics meet their explicit escalation
conditions.

## ADR-037: Context assembly is set-level; dense and source evidence stay shadow-only

**Status:** accepted 2026-07-22; extends ADR-036 without granting a new
current-context or production ranking authority.

Context assembly is a deterministic set-selection problem rather than a linear
packing loop. `ContextCompiler` derives bounded opaque labels only after policy,
temporal, lexical, and task-admissibility stages have completed. The selector
uses integer utilities and exact rational benefit-per-character comparisons,
prioritizes feasible interaction preferences, and enforces character budget,
duplicate, conflict, compatibility, and supporting-evidence constraints. The
selector's diagnostics remain closed aggregate codes. Raw content, query text,
unauthorized identifiers, and arbitrary metadata are not diagnostic fields.

The high-cardinality compiler reserve is also a compiler-only policy. It first
uses the selector locally to identify feasible fixed-mandatory survivors, then
reserves at most eight interaction preferences. An overflow preference is
gated by an actually selected applicable evidence item when one follows a
selected compatible primary; the final selector's exact budget may therefore
retain evidence while omitting overflow. If no such evidence is selected,
overflow falls back to an actually selected compatible primary. This keeps
evidence ahead of optional preference fallback without changing the selector
contract or pretending its OR-based `supports` field expresses an AND chain.
Fixed mandatory candidates use original ordered base utilities and
context-independent semantic/diversity signals in both compiler passes, so
reserved preference facets cannot change the authoritative fixed survivor.
Bootstrap metadata counts the exact union of the complete bounded
policy/temporal-eligible candidate ID sets when available, while legacy or
injected diagnostics retain the bounded count fallback; those internal IDs
never enter public metadata.

Dense retrieval is not a production dependency. The checked-in 384-dimensional
CPU experiment is disabled by default, rebuild-only, nonpersistent, and outside
application package discovery. Its deterministic synthetic runtime can measure
exact-scan mechanics but cannot establish semantic value. The 10,000-candidate
measurement missed the explicit `150 ms` p95 target at `400.294955 ms`, so a
future optional ANN shadow study is latency-justified. It is not approved yet:
the real local model and semantic comparison were not exercised, and no default
native dependency, authoritative vector state, or production ANN authority is
allowed.

Long imported-chat evidence also remains research-only. Deterministic passage
MaxSim variants are benchmarked after the frozen authorized lexical source
pool; they do not alter runtime results. The diversity-aware variant preserved
the bounded fixture's `1.0` evidence recall and coverage while reducing measured
redundancy to zero. Neural late interaction, learned sparse retrieval, and
reranking remain unexercised. Promotion requires representative evidence,
cross-platform measurements, explicit packaging review, and the same
policy-first and rebuildable-state guarantees as production retrieval.

## ADR-038: Repository-admin release checks stay outside GitHub Actions

**Status:** accepted 2026-07-23.

GitHub's immutable-release settings endpoint requires repository
`Administration: read`, a permission unavailable to the automatic Actions
`GITHUB_TOKEN`. Candidate and publish workflows must not receive a personal
access token or other repository-admin credential merely to inspect that
setting.

Immediately before each candidate or publish dispatch, a repository owner uses
their existing authenticated `gh` session to verify that immutable releases are
enabled. The manual workflow requires an exact, nonsecret confirmation phrase.
Actions then independently verifies every property its least-privilege token
can observe: the source commit, default-branch head where applicable, unused
tag/release slot, draft state, artifacts, digests, attestations, signed manifest,
and final immutable published state. A missing phrase or failed observable check
stops the workflow. This boundary keeps admin credentials and the offline
Ed25519 private key out of GitHub Actions without pretending the Actions token
can perform an impossible admin API call.

## ADR-039: Context maintenance is automatic, reversible, and Core-owned

**Status:** accepted 2026-07-23; supersedes ADR-003 and refines ADR-022 and
ADR-033.

All The Context is configured once and then gets out of the user's way. Normal
operation has no memory review inbox. The dashboard is an optional history,
provenance, correction, undo, deletion, backup, and administration surface.
Removing routine review does not transfer authority to a model, importer,
client, or Relay: Core remains the only component that can create or change
current context.

Every client- or importer-supplied durable-context input is an observation.
Core derives the effective origin from authenticated client registration,
transport, parser, message role, and ingestion session. A submitter may provide
evidence, confidence, `observed_at`, and the asserted basis
`explicit_user_statement`, but it cannot choose its Core-derived origin,
policy result, or current-record ID. The initial deterministic policy version is
`automatic-v1`.

The observation ledger records one of five dispositions:

- `staged` is internal unpublished work in an unfinished ingestion session or
  queued at Relay for later Core evaluation;
- `applied` creates or updates current context;
- `reinforced` attaches corroborating evidence to an existing applied record
  without creating a duplicate;
- `tentative` retains a noncurrent signal for deterministic corroboration; and
- `ignored` records that hard or source policy rejected the observation for
  context maintenance.

Explicit durable user statements from eligible authenticated direct clients
apply immediately. Eligible explicit corrections update the current record
before the successful operation returns and preserve the earlier version.
Exact duplicates reinforce. Model inference and provider-synthesized memory are
tentative unless later eligible evidence corroborates them. Provider adapters
exclude assistant, system, tool, and attachment roles. Generic or
instruction-bearing imports remain tentative, secret-like material is ignored,
and imported text is never executed as instructions. Tentative, ignored, and
staged observations are never retrieved as current context and never create a
user task.

`automatic-v1` does not implement tentative expiry or confidence decay.
Configurable retention/decay is a future versioned-policy extension, not a beta
claim.

Provider archives remain untrusted inert data. Archive observations stay staged
until `finish_ingestion` stores truthful coverage and publishes the
automatic decisions transactionally. Failed or unfinished extraction cannot
partially change current context. The original source, parser version, policy
version, disposition, `decision_reason`, `decided_at`, and affected record ID
make every decision inspectable and replayable.

Ordinary automatic changes remain reversible. Correction, supersession,
deletion, and restoration retain version history and evidence. Irreversible
purge remains a separate administrator-only state machine. Model-facing
`forget_context` is deliberately narrow: it requires an explicit user request,
record ID, and reason; creates an audited reversible tombstone at Core; and
never grants restore or purge authority. Legacy approved
records migrate to applied current context; rejected observations migrate to
ignored; unresolved legacy candidates are reevaluated idempotently under the
versioned policy.

Relay may queue observations for later delivery and may accept signed ordered
projections produced by Core. It never evaluates the policy, changes a
disposition, or creates current context. This keeps one authority while
allowing future transport work without reintroducing review as a consistency
mechanism.

## ADR-040: Imported-source deletion is reversible and provenance-bounded

**Status:** accepted 2026-07-23.

Ordinary deletion of an imported source is a Core-owned soft deletion. The
source disappears from normal listing, status counts, raw-content access, and
reprocessing. In the same transaction, Core soft-deletes current records whose
canonical `source_id` is that source and records each resulting deletion
version. Observations and the raw BLOB remain preserved for immediate Undo,
history, and a later irreversible administrator purge.

Restoring the source restores only a member whose current deletion tombstone
still has the exact version created by that source deletion. A record deleted
before the source, restored and deleted again independently, or purged is never
resurrected by source Undo. Reimporting the exact soft-deleted source is treated
as a duplicate and restores it under the same rule. Irreversible source purge
continues to remove the source, raw BLOB when unshared, observations, derived
records, and ordinary audit material through the existing confirmed purge state
machine.

## ADR-041: An empty canonical update channel is explicit state, not a transport error

**Status:** accepted 2026-07-23; refines ADR-034 without weakening manifest
verification or release gates.

Before the first protected beta promotion, the exact built-in GitHub Pages
manifest URL legitimately returns HTTP 404 because no signed channel pointer
exists yet. A packaged beta client maps only that exact URL and status to the
`unpublished` phase, clears stale offer data, records the completed check, and
shows that it is waiting for the first signed release. The persisted legacy
`Update endpoint returned HTTP 404` state is normalized on startup so an
already-installed client does not retain a false failure.

This exception is deliberately narrow. A 404 from an environment override,
fork, custom endpoint, artifact URL, or any noncanonical channel remains an
operator-visible error. Other HTTP, transport, signature, schema, channel,
platform, architecture, version, size, and checksum failures continue to fail
closed. `unpublished` never implies that a release exists and does not replace
the offline signature, immutable GitHub Release, protected publication, or
Pages promotion required for real OTA delivery.

## ADR-042: AI-memory research is hybrid, benchmark-driven, and subordinate to Core

**Status:** accepted as a research direction 2026-07-23; does not change the V1
product boundary or accept a production implementation.

ATC's long-term objective is end-to-end AI-memory reliability. It will use
proven external implementations and conventional systems mechanisms wherever
they improve the product, while reserving new research for measured gaps.
Novelty is not a promotion criterion.

The research program has two product planes and one evaluation surface:

- a Memory Plane for governed evidence, current knowledge, experience,
  procedures, working state, consolidation, and recall;
- an optional Intent and Consequence Plane for adequately witnessed
  preferences and directives compiled at cooperating client checkpoints; and
- an ATC Memory Lab that compares simple, external, hybrid, and experimental
  systems with fixed data, model backbones, budgets, and stage-level metrics.

External extractors, graph engines, retrievers, consolidators, and learned
models may enter through lab adapters or discardable sidecars. They may propose
observations, IDs, rankings, relations, summaries, and procedures. They do not
create current context directly, choose origin or disposition, expand
permission, assign behavioral force, or weaken correction, deletion, and
purge. Core remains authoritative.

The
[`Consequence-Closed Context`](research/CONSEQUENCE_CLOSED_CONTEXT.md) protocol
is a differentiated research plane, not the whole memory product. Its protocol
must remain useful without learned joint compilation. Learned target envelopes,
relation models, record-owned packets, private residuals, or parameter memory
must beat strong deterministic and external baselines, retain exact dependency
and purge semantics, and satisfy the local cross-platform boundary before a
separate production decision.

The beta remains the immediate milestone. Research does not block release, add
a mandatory hosted service, or make unimplemented checkpoint, behavioral,
graph, vector, neural, or experiential-learning claims.

The 2026-07-23 horizon amendment narrows the execution order. The Memory Lab
must climb a simple baseline ladder before external framework adapters. A
versioned event stream is the foundation; a general graph, retrieval council,
or learned procedure must earn its complexity against a simpler rung.
Authorization is followed by a separate epistemic-role and task-applicability
gate before relevance. Derived-state lineage, invalidation, rebuild, and purge
move into the authority foundation, while host behavioral closure remains
research.

Memory research promotion uses Current Authorized Outcome Success (CAOS) and
separately reported stage metrics. Applicable evaluations compare simple
baselines, each individual competitor, frozen hybrids, and ATC ablations under
equal reader, context, cost, and clock conditions. Official benchmark metrics
remain comparability measures, not sole promotion criteria. Authorization,
correction, forgetting, harmful-memory, consequence-closure, outcome-closure,
and purge gates cannot be offset by aggregate quality.

## ADR-043: Memory Lab adapters rank authorized snapshots and never own truth

**Status:** accepted for the bounded M0 research harness 2026-07-23; not a
production adapter or external-system acceptance.

The first executable Memory Lab surface uses two versioned contracts:
`atc.memory-object.v1` for immutable memory objects and
`atc.memory-lab.retrieval-adapter.v1` for retrieval adapters. A benchmark run
supplies the same already-authorized snapshot, frozen tasks, clock, result
limit, and repeat protocol to every adapter. Adapters return ordered object IDs,
explicit abstention, and provider-neutral usage accounting. They do not return
authoritative prose, select disposition, expand permission, or write canonical
Core state.

Every adapter declares identity, version, model provider, network access, and
data egress. The contract rejects canonical-write capability and inconsistent
egress declarations. The reusable report replaces result IDs with counts and a
deterministic ranking fingerprint derived from corpus ordinals; it also omits
task names, queries, and memory content. Unknown-ID violations remain counted
but cannot place the unknown identifier in a report.

M0 includes a no-memory control, a deterministic token-overlap baseline, and an
adapter over the current ATC Retrieval V3 implementation. The latter builds an
isolated synthetic Core-shaped database so it can exercise production retrieval
without connecting to or modifying the operator's authoritative Core. No
competitor code, hosted service, new default dependency, or production schema is
added.
Future competitors implement the same protocol only after separate dependency,
license, security, data-flow, and provider review.

Task-level evidence groups define sufficiency: a task succeeds only when every
required group is represented, no forbidden or fabricated result appears, and
an abstention task returns no memory. Recall, reciprocal rank, disclosure,
latency, storage, determinism, model calls, tokens, and cost remain separate
measurements. The initial five-task fixture is a contract regression and
diagnostic comparison, not evidence of real-user quality or a promotion gate.

## ADR-044: Independent Memory Lab workers produce evidence, not integration authority

**Status:** accepted for governed research waves 2026-07-23; does not grant
workers, external systems, or research results production authority.

Each parallel Memory Lab cell runs in a fresh visible Codex thread and separate
git worktree from one immutable coordinator commit. Its prompt freezes scope,
file ownership, allowed external actions, validation duties, and completion
receipt. Workers may commit scoped results but do not merge, push, edit wave
governance, connect to the operator Core, or describe their result as an
integrated ATC result.

The coordinator is the sole integrator. Worker output is untrusted until its
diff, provenance, privacy boundary, and result are reviewed and reproduced on
the integration branch. Evidence levels distinguish specification, isolated
synthetic, coordinator-reproduced, external-supplier, cross-platform, and
consented-product results. Negative, unsafe, blocked, and skipped cells remain
visible and cannot be promoted by aggregate scores from other cells.

External code is denied by default. A wave may name a bounded supplier cell
only after recording canonical origin, immutable revision, licenses and
notices, dependencies and install hooks, vulnerabilities, network and data
flows, disposable isolation, and zero personal data or credentials. A clone is
not permission to install, execute, copy, or make the supplier a production
dependency. The machine-readable wave manifest records the exact workers,
authority boundaries, gates, commits, results, and limitations.

## ADR-045: Wave 2 advances the simple current-state log and gates complexity behind longitudinal evidence

**Status:** accepted as bounded research evidence 2026-07-23; no production
memory implementation or external supplier is accepted.

The first governed Memory Lab wave completed five independent cells and the
coordinator reproduced both executable synthetic experiments. On the unchanged
seven-object/five-task M0 retrieval fixture, a stable observation log with
deterministic current-state resolution achieved task success `1.0`, evidence
group recall `1.0`, and zero forbidden output. Current ATC Retrieval V3
achieved `0.8`, `0.9`, and zero respectively. The stable condition advances to
mutation, poisoning, scale, action, and CAOS fixtures; it is not an
implementation-acceptance or production-replacement decision.

The retrieval adapter ABI remains `atc.memory-lab.retrieval-adapter.v1`.
Optional task budgets and identifier-safe failure diagnostics are additive, and
the aggregate report is versioned separately as v2. The original M0 fixture
remains byte-for-byte frozen; Wave 2 baseline controls have their own
schema-versioned configuration and digest. Current-state resolution occurs
over the complete authorized temporal snapshot before task scope and project
applicability so a narrower or inapplicable superseder cannot resurrect an
older broad record.

The bounded E01 reference slice executed six of eighteen specified lifecycle
scenarios. The in-memory governed reference passed 6/6, append-only search
passed 0/6, and no-memory passed 1/6. Removing authority,
currentness/invalidation, applicability, or purge closure caused a distinct
regression. This accepts those four rule families as required conformance
hypotheses, not as evidence that current production Core implements the
reference. The fixture, oracle, and rules were co-designed; an isolated
production-semantics E01b cell is required.

The Hindsight supplier execution was skipped with
`not_executed_dependency_and_egress_gate`. Only its official MIT source at
`fa69b5b73b3b50bf5dcbae5bccbc7197de03692f` was temporarily cloned for static
review and then removed. No supplier package, model, container, provider,
credential, service, or benchmark ran, and no Hindsight score exists.
Checked-in code is a dependency-free injected-client boundary tested with a
fake. A future real cell requires immutable local model artifacts,
loopback-only binding, and an externally enforced default-deny egress boundary.

Wave 2 also changes the experiment order. Lossless structured-log inspection,
online/off-policy/shift testing, and admission-to-delayed-action poisoning
precede framework tournaments. Raw fidelity and localized maintenance precede
lossy consolidation. External systems remain valued competitors and suppliers,
but a skipped or failed gate is preserved rather than bypassed.

ATC does not claim generic selective reminder or barrier-first repair novelty.
Immediate ATC-native hypotheses are the Sealed Projection Minimal Compiler,
the authority/purge-aware Record-Influence Barrier Closure composition, and
Portable Working-State Three-Way Repair. Each remains specification-level
until it beats simple controls on CAOS and hard lifecycle gates.

## ADR-046: Wave 3 tests five independent falsification surfaces before further architecture promotion

**Status:** completed as governed research execution 2026-07-23; final result
classification is in ADR-047 and no production change is accepted.

Wave 3 starts five fresh visible worktree tasks from immutable coordinator
commit `950f649d9e3cc106fb8ff4febbe38919f8e00d11`: B01 programmatic
lossless-log inspection, O01 online/off-policy/shift triangulation, P01
admission-to-delayed-action poisoning, E01b isolated production-Core
conformance, and M2 sealed minimal projection. Each owns new experiment files
only. Workers do not edit shared harnesses, production behavior, governance,
or another cell's files; they never merge or push.

Core remains authoritative. E01b may exercise public or stable production paths
only through a disposable synthetic store and records unsupported or failing
semantics rather than fixing them. P01 uses opaque synthetic poison and a
simulated protected action. B01 cannot call a deterministic one-shot file
ranker "programmatic memory" and cannot claim PRO-LONG reproduction without an
equivalent action model. M2 must test paired-vault noninterference across every
declared observable channel, not output content alone.

The smaller bounded O01 protocol cell uses `gpt-5.6-sol` medium reasoning; the
four core implementation/falsification cells use high reasoning. Model effort
does not change evidence level. Every result remains `L1` until coordinator
diff review and deterministic reproduction raise it to `L2`.

No external code, model, provider, credential, real personal context, operator
Core, or production schema is allowed. Negative, unsupported, held, killed,
and not-exercised outcomes are preserved. Promotion order remains B01, O01,
P01, E01b, then M2 even though implementation proceeds in parallel.

During active execution, the authors' official page exposed an Apache-2.0
MPBench repository that the prior horizon had not located, while PRO-LONG's
paper-linked repository still returned 404. A sixth, smaller
`gpt-5.6-sol` medium task is therefore appended after the five falsification
surfaces for metadata-only provenance, license, and safe-cell design. It may
inspect official metadata, README, license, and tree shape only: cloning,
payload-row access, third-party execution, and contamination of the frozen P01
or B01 cells remain forbidden. This intake is evidence preparation, not an
external benchmark result or production promotion.

## ADR-047: Wave 3 advances evidence-compiled memory while holding automatic durability and static winner selection

**Status:** accepted as bounded research direction 2026-07-23; no production
schema, external benchmark, or claim of solved AI memory is accepted.

Wave 3 completed all six governed cells and the coordinator reproduced the
five deterministic experiments. Their mixed results remain visible:

- B01 preserves `KILL_MECHANISM` for its bounded hand-authored DSL under the
  frozen external-operation gate. Its strong synthetic quality result does not
  overcome the gate, but non-normalized internal work prevents a general
  programmatic-memory or compute-efficiency conclusion.
- O01 is held because tie-aware policy rankings were not stable across
  off-policy, online, and shifted regimes.
- P01 holds automatic durability because the governed reference durably
  retained poison in four of five unique scenarios even though applicability,
  currentness, and protected-action confirmation prevented observable
  influence and action.
- E01b accepts six narrow current-Core conformance facts and records six
  unsupported or not-exercised semantics. Kind and explicit-scope filtering do
  not establish generic epistemic roles or a project-and-domain applicability
  hard gate.
- M2 advances only as a bounded synthetic contract after exact finite-set
  sufficiency, one-deletion minimality, current-version reread, disclosure
  reduction, and full-receipt paired-vault noninterference passed.
- The MPBench artifact is metadata-qualified for a future quarantined,
  schema-only cell. No payload or external result entered Wave 3, and the
  paper-linked PRO-LONG repository remained unavailable.

The resulting research architecture is called **Evidence-Compiled Memory**.
The name describes a contract, not a new authority or accepted product brand.
A memory use is treated as a revocable transaction:

1. untrusted observations enter through authority and witness admission;
2. Core owns the complete versioned canonical evidence substrate;
3. currentness and task applicability resolve before relevance;
4. the authorized/current/applicable projection is sealed;
5. a bounded compiler selects an obligation-complete minimal working set;
6. selected record versions are reread immediately before issue;
7. the issue carries disclosure, dependency, and action-force receipts;
8. observable use and outcomes are recorded without hidden reasoning; and
9. correction, deletion, permission change, and purge close every derived
   influence before republication.

Retrievers, coding agents, learned routers, and external systems may propose
candidates or rankings through bounded adapters. They do not create canonical
truth, make untrusted content durable automatically, expand scope or force,
bypass the sealed projection, or retain influence after invalidation. Core
remains the sole canonical authority.

The next gated order is:

1. M3/E02 dependency-complete influence closure and the six exposed production
   semantic gaps;
2. M1 assignment/use/outcome/invalidation receipts;
3. a separately preregistered MPBench schema-only quarantine cell;
4. B02 with a genuine bounded code-writing reader and normalized compute,
   token, and action accounting;
5. O02 shadow policy routing under online formation and shift; and
6. M6 three-way working-state repair after closure and use receipts exist.

M3/E02 must compare optimized repair with full rebuild and preserve fail-closed
purge. A production change requires a separate ADR after integrated tests show
that role, applicability, lineage, and procedure-precondition semantics can be
implemented without creating a second authority. M2's unkeyed synthetic
commitments, logical timing classes, hand-authored obligations, and
compiler-visible attestations are not production privacy or security designs.

## ADR-048: Wave 4 tests influence closure and observable use before product promotion

**Status:** completed as governed research execution 2026-07-23; final
classifications are recorded in ADR-049 and no production change is accepted.

Wave 4 starts four fresh visible worktree tasks from one immutable
governance-only base. M3 tests incremental dependency-complete repair against
a full-rebuild oracle across correction, scope narrowing, permission
revocation, delete, purge, and policy-generation change. E02 exercises the six
semantic gaps recorded by Wave 3 against disposable synthetic instances of
the frozen production Core. M1 tests an observable assignment/use/outcome
ledger that is forbidden from storing hidden reasoning or raw context. F02
commits an independent falsification oracle before mechanism implementation
and performs the final result review.

M3 can advance only with zero published stale descendants, zero optimized
versus full-rebuild eligibility mismatches, zero purge residue in inspectable
derived state, and fail-closed behavior across partial repair and stale-writer
attempts. Correct closure without a work reduction may retain the contract
while holding the optimization. E02 must preserve `UNSUPPORTED` and
`NOT_EXERCISED` as distinct outcomes and cannot patch a failed path. M1 events
must bind canonical record identifiers and versions, reject impossible causal
transitions and conflicting replay, and distinguish non-acknowledgement from
non-use.

Core remains the sole authority. Workers may add only research-specific files,
cannot access the operator Core, cannot use personal context, credentials,
external code, models, or providers, and cannot merge, push, edit governance,
or change production behavior. A separate decision is required before any
schema or runtime promotion.

## ADR-049: Retain closure and observable-use contracts, fill Core semantics, then test prospective memory

**Status:** accepted as bounded research direction 2026-07-23; no production
schema, runtime mechanism, competitor dependency, or solved-memory claim is
accepted.

Wave 4 completed its four governed cells and the coordinator reproduced the
three executable results. M3 passes all 15 independently frozen cases with
zero hard-safety failures, exact agreement with an independently coded full
rebuild, and a `0.99` evaluated-node reduction in its synthetic work control.
M1 passes all 16 independently frozen cases with zero hard-safety failures,
exact replay and aggregate reconstruction, and zero paired-vault differences.
E02 classifies five required production semantics as `UNSUPPORTED` and exact
same-identifier recreation after terminal purge as `NOT_EXERCISED`.

Retain the M3 dependency-complete closure contract and its bounded
optimization. Retain the M1 observable-use ledger contract. Neither research
prototype enters production. Barrier-first cascade repair is not claimed as
novel because MemoRepair already establishes that mechanism class. The
candidate ATC differentiation remains the exact composition of Core-owned
authority, scope, applicability, currentness, policy generation, delete/purge
semantics, minimal sealed projection, complete influence closure, action
ceilings, and privacy-bounded observable-use and outcome receipts.

M1's storage contract has one explicit exception: ordinary accepted events
are append-only, while terminal purge is destructive privacy compaction.
Affected event, record, and index identifiers leave all declared inspectable
surfaces; replay resumes from an aggregate identity-generation barrier and
purge count. A future implementation must preserve that boundary rather than
claim both physical append-only storage and terminal erasure.

Production work, if separately authorized, proceeds in small Core-owned
slices:

1. optional explicit generic epistemic role with unknown legacy values;
2. project-and-domain applicability with fail-closed unknowns;
3. version-bound dependency and influence inventory;
4. optimized closure dual-run against a full-rebuild shadow oracle;
5. the observable ledger and privacy-compaction boundary; and
6. only then a notification-only prospective-memory kernel.

The next research hypothesis is Evidence-Compiled Prospective Memory. A
canonical event-contingent memory transaction binds exact evidence versions,
a typed cue, positive witnesses, negative guards, expiry/rearm behavior,
principal/project/domain/policy generations, a maximum action force, closure
dependencies, and observable outcome receipts. Typed cue evaluation occurs
before content disclosure. Only a due, current, authorized, applicable
transaction may compile minimal context and cross a cooperating host
checkpoint.

This proposal must first beat a simple explicit task table and deterministic
scheduler. Prospective precision, recall, false alarms, action success,
disclosure, outcome benefit, and lifecycle failures remain separate metrics.
Unauthorized, stale, deleted, purged, wrong-domain, duplicate, or
unconfirmed-protected action is a non-compensable failure.

External memory systems may enter later through dedicated supplier cells with
pinned revision, license confirmation, static inventory, isolated execution,
and an adapter that cannot canonize records or bypass Core. Wave 4's
external-code prohibition remains truthful: it downloaded or executed no
competitor system.

## ADR-050: Release acceptance failures emit bounded evidence without changing gates

**Status:** accepted 2026-07-25 after review against current main; release,
updater, retrieval, and authority semantics are unchanged.

Hosted release acceptance must preserve enough evidence to diagnose a failure
without logging credentials, raw personal context, or unbounded local state.
The integrated retrieval assertion therefore emits its bounded gate,
lifecycle, operational, and profile report when it fails. The macOS package
wrapper emits the `hdiutil` return code, output-file existence, and bounded
stdout/stderr tails. The packaged Windows rollback smoke emits only the helper
journal phase, fixed error code, and schema version; paths, operation
identifiers, and the rest of the journal remain excluded.

These diagnostics do not convert a failure to success. No threshold is relaxed.
ADR-082 later adds one exact-state re-entry to the rollback smoke so it exercises
the already-defined persisted recovery contract rather than misclassifying it.
The one-file Windows MCP adapter gets one
bounded 30-second managed-Core readiness window so native extraction and
startup are not misclassified by the earlier 10-second boundary; it still
launches once and fails hard at the deadline. A repeated failure still stops
the matrix, but the next investigation can distinguish a gate regression,
helper rollback state, native-tool error, missing native output, and a managed
Core startup timeout from runner noise.

## ADR-051: Store large raw sources as bounded authoritative SQLite chunks

**Status:** accepted 2026-07-25.

The supported raw import boundary is 2,000,000,000 bytes by default and at its
maximum; `ATC_MAX_IMPORT_BYTES` may lower it. This cannot be implemented as one
SQLite value: supported SQLite/Python runtimes may cap a single string or BLOB
well below two billion bytes. Core therefore retains the content-addressed
source identity and metadata in `source_blobs`, while nonempty path imports and
in-memory values larger than 8 MiB are stored as ordered 8 MiB-or-smaller rows
in `source_blob_chunks`.

The chunk table remains inside Core's SQLite transaction and references its
parent with cascading deletion. A path import hashes the file before the
transaction, streams it again into chunk rows, and aborts if its size or digest
changed. Reads require contiguous indices, declared and actual sizes to agree,
the reconstructed size to match the parent, and the reconstructed SHA-256 to
match the content-addressed identity. The schema migration converts legacy
inline values larger than 8 MiB without changing their source identity.

Source-inclusive portable exports write each database chunk as a separately
hashed encrypted-package entry. Restore verifies the archive digest, descriptor
shape, bounded chunk size, parent relationship, sequence, reconstructed size,
and complete source hash; duplicate restore remains idempotent. No sidecar file
or alternate source authority is introduced. Operators supporting imports near
the ceiling must provide enough local space for the upload, SQLite transaction
journal/WAL, database growth, and any source-inclusive export. Existing
expanded-text, archive-entry, compression, and per-item parser bounds remain
independent safety controls.

## ADR-052: Search result content requires read scope

**Status:** accepted 2026-07-25.

Core context search returns full context record payloads, including content, for
both current and `as_of` historical retrieval. The `/v1/context/search`
endpoint therefore requires `context:read`; `context:status` is not sufficient
for any search path, including offset-aware historical requests that can
surface expired or superseded personal context. The separate
`/v1/context/status` endpoint continues to require `context:status`, preserving
a non-content monitoring permission.

## ADR-053: Govern V1 as the first usable public beta

**Status:** accepted as the planning and release-governance baseline on
2026-07-25. ADR-086 later superseded the three-OS / `0.1.0-beta.1`
product-scope and version identity while preserving the text below as the
historical four-platform plan. Those Mac requirements were not passed,
skipped, waived, or marked unavailable. Implementation, exact-artifact
acceptance, and publication remain open for the Windows/Linux
`0.1.0-beta.2` destination in the active roadmap.

For the then-current roadmap, V1 meant the first usable public beta,
`0.1.0-beta.1`, not stable `1.0.0`. The active execution plan is
[`ROADMAP_TO_V1.md`](ROADMAP_TO_V1.md). It contains no calendar or effort
estimates. Readiness follows dependency order and exact-artifact evidence.

The beta critical path is one user-owned authoritative Core, same-device
desktop use, automatic reversible context maintenance, truthful local provider
import, deterministic retrieval, encrypted backup with a packaged
version-matched stopped-Core helper/mode, and immutable beta publication.
Phone/remote access, real beta1-to-beta2 N-1 evidence, one-click graphical
restore, stable 1.x contracts and channels, Project Context Capsules, Memory
Lab mechanisms, hosted Edge/Relay operation, and other research proposals
remain post-V1 unless a later decision expands a public beta claim.

Beta1 must support all three desktop OS families—Windows, macOS, and
Linux—and current account-history exports from all three providers—ChatGPT,
Claude, and Grok. The platform floor is Windows 11 x86-64, macOS 26 ARM64 and
x86-64, and Ubuntu 24.04 LTS x86-64 GNOME with a working Secret Service/GNOME
Keyring backend; other Linux distributions/desktops are experimental. Phase A
freezes the exact Windows/macOS build/patch, client variants, and
parser/format shapes. Source installs remain contributor-only rather than an
unbounded public `Python 3.12+` claim. Provider evidence must use nonempty exports
acquired after parser freeze and within 30 days of acceptance, exercise frozen
fictional canary shapes, and reconcile every input to a closed outcome. Missing
acceptance evidence for any mandatory target leaves the release in draft; it
does not silently narrow the beta scope.

The inclusive raw-source boundary remains `2,000,000,000` bytes. The existing
chunked SQLite representation and configuration boundary establish structural
support, not usable beta acceptance. Publication requires an exact-boundary
success on every frozen OS/architecture target, boundary-plus-one refusal, disk
preflight, durable progress and cancellation/retry, bounded resource evidence,
interruption recovery, complete source integrity, packaged source-inclusive
encrypted export, and restore. Before implementation measurements, Phase A
freezes a reference machine/disk profile, numeric budgets, and a deterministic
physically allocated/non-sparse fixture with a known digest and nonzero
publication result; candidate outcomes cannot relax them. An operator may
still configure a lower local limit without reducing the advertised and tested
beta maximum. The beta1 scale profile is 4 logical cores, 8 GiB RAM, local
SSD, and 16 GiB free; Core plus import-worker RSS is capped at 1 GiB and
incremental import storage at four times raw size plus 1 GiB. Progress begins
and heartbeats within 5 seconds, cancellation is acknowledged within 5 seconds
and quiesces safely within 30 seconds, and import, source-inclusive export, and
isolated restore each complete within 60 minutes. The full boundary journey
runs on Windows x86-64, macOS ARM64, macOS x86-64, and Linux x86-64.

The downloaded packages expose a version-matched recovery/admin helper or
native mode for stopped-Core restore and deliberate administrator purge without
Python or a source checkout. Contributor-only `atc restore` remains a
development path; beta acceptance requires exact downloaded-artifact packaged
recovery receipts rather than contributor CLI evidence alone.

An ATC-configured same-device Codex or Claude client principal may attest that
text is an explicit user statement only when Core grants that witness class.
This is an explicit local trust decision, not cryptographic proof that the
human authored each statement. Authentication alone does not grant it,
unattested inference remains tentative, imported material does not inherit it,
and an authorized malicious client remains a documented residual risk.

One human maintainer remains accountable and may use AI tools for
implementation, inspection, and adversarial review. The project must not call
AI review independent human approval or imply separation of duties that does
not exist. GitHub private vulnerability reporting was enabled and verified on
2026-07-25; detailed sensitive defects use that intake, while public tracking
is sanitized.

The release advances through claim/trust lock, beta safety fixes, release
controls and candidate freeze, exact downloaded-artifact acceptance, and
publication. A later behavior or packaging change invalidates earlier browser,
client, platform, provider, recovery, and security receipts.

Authentication/authorization bypass, secret persistence contrary to policy,
credential or context leakage, data loss, purge resurrection, unsafe update or
signature behavior, unintended Edge/network operation, an untraceable
candidate, and any open P0/P1 are non-waivable beta failures. Only P2/P3
limitations may be accepted with public disclosure and a follow-up issue.

`STATUS.md` records current evidence,
`REQUIREMENTS_TRACEABILITY.md` maps requirements to implementation and proof,
and immutable release receipts will record the exact candidate results. An
earlier green commit, synthetic result, roadmap checkbox, or elapsed date cannot
promote the beta.

## ADR-055: Refuse direct secret-like payloads before the durable ledger

**Status:** accepted 2026-07-25.

Direct observation paths inspect caller-controlled proposal, batch,
correction/error, and Relay-queue material before any payload write. Detected
secret-like content is refused or, for forget reasons, replaced with a fixed
content-free statement. A durable refusal receipt may contain only a random
UUIDv4 operation identity, route/principal identity, closed reason and detector
version, and timestamps. It may not retain an unkeyed payload hash,
deterministic fingerprint, prefix, or other offline-guessing verifier.

Migration 008 adds the refusal ledger and batch refusal count. Core startup,
new export creation, and restore repair affected direct-observation rows,
rebuild FTS, checkpoint WAL, enable secure deletion, and vacuum the live
database. Imported raw archives remain governed by the separate inert-source
contract. Synthetic byte scans cover SQLite pages/freelists, WAL, FTS, temp
state, diagnostics, and encrypted export/restore. Historical external backups,
user copies, and device remanence are outside the live-store repair claim and
require explicit retirement or replacement.

## ADR-056: Normal client setup fails closed without protected credential storage

**Status:** accepted 2026-07-25; packaged first-run isolation clarified 2026-07-26.

Normal V1 setup stores client credentials in Windows Credential Manager, macOS
Keychain, or Linux Secret Service and verifies each write by reading it back.
When protected storage is unavailable or fails, setup stops; it does not
silently write a plaintext app-data fallback. The plaintext development file
requires deliberate operator opt-in and is excluded from the normal beta
journey.

Managed Codex and Claude configurations carry the client identity and protected
credential lookup, not a bearer token, when the OS store is used. A failed
credential or configuration transaction revokes a newly created principal,
removes its credential, and restores the exact prior configuration bytes.
Backend errors are surfaced without credential values. Exact-package real
credential-service receipts on every then-mandatory OS remain a
release-acceptance gate. ADR-086 later limited that mandatory set to Windows
Credential Manager and supported Linux Secret Service; the retained macOS
Keychain adapter is unsupported source/CI code, not a `0.1.0-beta.2` receipt.

Packaged first-run smoke (`scripts/smoke_packaged_first_run.py`) is intentionally
**not** that OS-credential receipt. It isolates non-secret smoke credentials with
the null keyring backend plus explicit
`ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE=1`, asserts the setup report's
`credential_storage` is the insecure development file, and labels the result as
outside real OS acceptance. Real OS credential round-trips stay with
`--packaged-credential-acceptance` / platform acceptance. Headless setup writes
a redacted failure report when windowed packages hide stderr. On smoke failure
the entire disposable work tree is always removed; retained evidence is only a
content-free summary in a separate diagnostics directory (labels, return codes,
phases, boolean presence, error class, redacted bounded message, safe relative
filenames)—never dashboard URLs, tickets, tokens, client IDs, raw statements,
absolute developer paths, or raw stdout/stderr.

## ADR-057: Preserve imported bytes before making parser claims

**Status:** accepted 2026-07-26.

Core commits an inert raw import and verifies its content-addressed identity
before a provider parser may create observations. Path imports are parsed from
a bounded temporary reconstruction of that authoritative blob, not from the
caller-owned path after storage. A parser failure or acknowledged cancellation
marks the source terminal without publishing current context and leaves the raw
bytes available for no-upload, parser-versioned retry.

Auto-detection may initially store a provisional provider label. After parsing,
Core reclassifies that source transactionally to the versioned parser-derived
identity; a collision may reuse an existing identical source only when the
provisional source has no observations. This keeps content identity,
provenance, retry idempotency, and duplicate suppression aligned.

ChatGPT, Claude, and Grok claims carry explicit parser identities, frozen
fictional shapes, and closed coverage counts. Unknown/unparsed material is a
visible incomplete-coverage result, not implicit support. Durable import
operation identifiers and cancellable chunk heartbeats are implemented in
source; this source-level decision still does not satisfy the beta scale gate:
real-provider plus exact-boundary candidate receipts remain mandatory.

## ADR-058: Integrated packaged recovery and durable import surfaces are source-complete before exact-artifact acceptance

**Status:** accepted 2026-07-26.

Native candidates must carry a version-matched recovery/admin surface on every
OS family: Windows and macOS ship a console recovery helper
(`AllTheContextRecovery.exe` / `all-the-context-recovery`); Linux attaches
the same stopped-Core modes to the console-capable main binary. Candidate and
CI native matrices fail closed when that surface is missing and exercise
content-free `--recovery-help` / doctor from built bytes. Windows OTA
journals, diagnostics, and rollback inventory include the recovery helper.

Durable import operations (migration `009_import_operations.sql`, runtime,
and the combined browser+import dashboard) travel through ordinary package-data
and packaging collect paths. Package-resource diagnose and frozen desktop
smokes refuse wheels or artifacts that omit the migration or import dashboard
surface. Candidate source evidence stays content-free: no raw exports, 2 GB
canaries, or personal data.

This decision does not mark BETA-D03, provider, 2 GB, or browser/client gates
passed. Exact downloaded-artifact and operator receipts remain mandatory and
use the existing content-free acceptance-receipt fields without silently
widening release inventory allowlists.

## ADR-069: Classifiable provider residuals and staged packaged-acceptance failures

**Status:** accepted 2026-07-26.

Provider conversation graphs may contain structurally valid message
dictionaries that yield no normalized text. Known, explicitly classifiable
shells close into existing closed-coverage reasons without weakening fail-closed
unknown handling:

- empty or non-text assistant/system/tool/developer/function roles → excluded
- empty user turns without attachment structure → skipped
- known attachment/non-text-only shells → unavailable
- unknown roles and malformed structures → unparsed (coverage incomplete)

Packaged `--packaged-provider-acceptance` reports a small content-free stage
set for failures that previously shared one ambiguous code:

- `import_operation_failed` — operation/import raised before a complete result
- `import_operation_incomplete` — operation terminal/non-complete status
- `import_acceptance_reconcile_failed` — acceptance reconciliation refused the
  terminal result

Reports remain create-once, content-free, and free of exception text, paths,
dynamic type names, and personal/provider content. Durable import-operation
committed/received byte counters stay monotonic across raw-archive and later
staging/member progress domains. Windows packaged-smoke startup cleanup may
delete only the exact run-owned `Software\AllTheContext\Smoke\...` override key
after its value is removed; product Run-key behavior is unchanged.

This decision does not satisfy real-provider exact-candidate receipts.

## ADR-053 implementation note: A-09 / B-102 client witness (source-complete)

**Status:** source-level implementation under ADR-053 A-09; accepted residual
risks unchanged. Does not satisfy exact packaged Codex/Claude receipts.

Core enforces the closed capability `witness:explicit_user_statement` on
authenticated ongoing-client and authenticated archive-batch paths. Desktop
setup assigns that class only to ATC-configured same-device Codex and Claude
principals; authentication, `context:propose`, and `context:ingest` alone do
not. Intentional local `admin`/`*` remain local authorities. Payload schemas
forbid smuggled origin/disposition/force fields. Relay proposals cannot attest
direct user statements. Core-controlled importers with no client principal may
still assign archive explicitness for trusted parser output. Unkeyed
archive-import preference/goal/project/decision/workflow/constraint lineages
collapse conservatively by explicitness then `observed_at` so contradictory
imported history cannot all remain confident current truth. Exact duplicate
retries are idempotent; matching content reinforces rather than duplicates.
Decision reason, decided_at, and policy_version remain inspectable without
persisting credentials. Residual honesty: this is a local trust grant, not
cryptographic authorship proof, and an authorized malicious witness client can
lie.

## ADR-059: Exact candidate and publication gates recompute fail-closed source evidence

**Status:** accepted 2026-07-26; revised 2026-07-26 after independent false-pass
audit.

The release-candidate path binds one exact 40-character source commit. Hosted
preflight must observe a completed successful run of the **canonical** workflow
path `.github/workflows/ci.yml` (not suffix-matched paths such as
`evilci.yml`) for that SHA, with workflow name `CI` when the API supplies it.
Required jobs are the nine-slot matrix **plus** repository-security and
dashboard-parity jobs. Branch names, short SHAs, merge-queue mismatches,
skipped/cancelled/neutral required jobs, partial job sets, duplicate required
job names / re-run shadows, jobs missing `run_id`/`head_sha`, jobs bound to
another run or SHA, and incomplete paginated job payloads (`total_count` >
returned) never satisfy the gate.

Durable `matrix-evidence.json` stores recomputable primitives: canonical
workflow path/name, selected run ID, exact head SHA, run status/conclusion, and
one job record per required name with `name`/`run_id`/`head_sha`/`status`/
`conclusion`. The stored `ok` boolean is recomputed from those primitives and
is never trusted as authority. Forged truthy strings, name-only job lists,
bool-as-int IDs/schema values, and partial/extra shadow records are refused.

Candidate assembly and verification revalidate bound source evidence: matrix
evidence primitives, component-inventory schema matching
`build_component_inventory` (exact keys, unique components/locks, count
consistency, lowercase digests), required inventory and candidate checksum
sidecars, source/version identity, and notices that reference the exact commit.
Descriptor/file duplication is rejected rather than last-write-wins. Inventory
verification still rejects extra, missing, or substituted release files.
Cryptographic provenance/Sigstore trust and live operator GitHub controls remain
outside what source bytes alone can recompute; those stay honest human/hosted
boundaries.

The locked installer also treats the no-build-isolation environment as a
dependency-closed unit: `packaging`, `setuptools`, and `wheel` must each come
from exact `uv.lock` versions and hashes. Installing only the two declared
backend packages is insufficient because modern wheel itself depends on
`packaging`; allowing pip to resolve that edge under `--require-hashes` either
fails CI or tempts an unreviewed ambient dependency.

Publication and receipt aggregation recompute relationships from receipts and
inventories. Required package/platform gates (including **BETA-P04**) and the
postpublication `BETA-R05`/`BETA-O01` operational gates pass only with
`exact_downloaded_artifact` evidence whose every `artifact_digests` key is a
filename declared by the verified candidate inventory with an exact matching
digest. Arbitrary safe basenames (for example `foo.bin`) never satisfy an exact
gate. Source-scaffolding gates (`BETA-R01`, `BETA-R02`) cannot be labeled exact
artifact; provider source-preparation fragments cannot pass as BETA-P04.
Duplicate `gate_id`/`receipt_id` values, conflicting digests, a prepublication
bundle containing anything other than exactly the 20 required gate IDs,
non-pass statuses, open P0/P1 limitations, forged maintainer booleans,
postpublication O01/R05 before release, and overwrite of immutable evidence
files fail closed. Hosted CI still must not deploy Edge or a third-party
runtime. Human key custody, protected-environment promotion, private-key
signing, repository-control enablement, and public channel smoke remain
explicit operator blockers and are not fabricated in source.

## ADR-060: Candidate convergence accepts executable product paths, not acceptance-shaped volume

**Status:** accepted 2026-07-26.

V1 convergence freezes broad acceptance-framework growth. A change advances a
candidate only when the shipped application executes the claimed behavior or
when a focused regression protects that executable path. Schema-only
aggregators, caller-authored lifecycle primitives, and subprocess doubles that
require fictitious flags on a named client cannot satisfy exact-artifact gates,
regardless of their test volume.

The desktop binary therefore carries a narrow hidden provider-acceptance mode.
It creates a disposable loopback Core, invokes the production durable import
operation on one mandatory-provider export, reconciles parser/coverage/outcome
counts, emits no export content or path, and fails if cleanup is incomplete.
This is an execution control surface, not a BETA-P04 receipt generator. Exact
downloaded-candidate identity, three current nonempty real exports, and
inventory-bound receipt aggregation remain separate mandatory evidence.

The same rule permits compact defense-in-depth fixes discovered during
acceptance work: direct Core-store and Relay candidate paths now reject
secret-like material before payload-derived writes, and durable free-text
reject/delete reasons are redacted. Large provider/client/secret/security
harness branches are not integration dependencies; any later salvage must be
justified by a concrete failure from the built candidate.

## ADR-061: Candidate security scanning is exact-source and bounded

**Status:** accepted 2026-07-26.

The repository security gate scans committed blobs at the explicitly bound
candidate SHA, not ignored or modified working-tree files. Its history scope is
the candidate's reachable object graph only. Every reachable blob is validated
with the same complete private-key and shaped-canary rules, including content
inside ZIP members and archives later deleted or renamed. Per-blob, member,
expanded-archive, and object-count ceilings bound memory and work; exceeding a
ceiling or failing to read an object/member is a gate error, never a clean
result. Native package files and ZIP members use bounded chunk streaming so
normal release sizes do not fail solely for exceeding the in-memory history
blob ceiling. This deliberately excludes the broader multi-format scanner
redesign; tar.gz and DMG content coverage is not claimed.

## ADR-062: Managed Codex connections explicitly approve the scoped ATC tool set

**Status:** accepted 2026-07-26.

Generated Codex MCP entries set the documented
`default_tools_approval_mode = "approve"` server policy. Codex CLI 0.144.0
otherwise reports ATC calls as user-cancelled when the client runs
noninteractively with `approval_policy = "never"`, even though the managed
principal is already scoped by Core. This setting removes repeated
client-side prompts only for the named ATC server; it does not bypass the Codex
sandbox, broaden the principal's durable Core scopes, grant administrator
operations, or weaken Core's explicit-user and purge boundaries.

## ADR-063: Automated tests never inherit real AI-client configuration roots

**Status:** accepted 2026-07-26.

The pytest harness assigns every test its own temporary `CODEX_HOME` and Claude
configuration path, in addition to the existing null keyring. The
open-dashboard regression also pins and asserts its disposable client paths.
This is mandatory even for tests whose primary subject is browser handoff:
launch repair is a production side effect and otherwise rewrites a real
managed Codex or Claude entry when one exists on the developer host.

## ADR-064: Core transport responses keep dynamic values out of executable code and raw errors

**Status:** accepted 2026-07-26.

The one-time browser handoff keeps its nonce-protected inline script, but the
script is constant. Dynamic storage keys, browser capabilities, and validated
dashboard targets cross the HTML boundary only through quoted, HTML-escaped
data attributes and are consumed as DOM strings. This preserves the
session-storage handoff and loopback redirect without treating request-derived
values as JavaScript source.

Authenticated integration diagnostics expose only a stable degraded-state
repair message when local Codex or Claude configuration parsing fails. Raw
exception text is neither returned nor logged because it can contain local
paths, credentials, or personal configuration material.

## ADR-065: Public beta repository controls preserve deliberate solo-maintainer release gates

**Status:** accepted 2026-07-26.

The public repository protects `main` with a strict up-to-date pull-request
gate, conversation resolution, the eleven canonical CI contexts, and the
three CodeQL language contexts. Force pushes and branch deletion are disabled,
and repository Actions must use immutable SHA pins. Administrator enforcement
remains off as an explicit sole-maintainer recovery residual; using that
bypass must be recorded and cannot be described as independent review.

Private vulnerability reporting, secret scanning, push protection, Dependabot
alerts and security updates, and CodeQL default setup are enabled. Findings
must be fixed and rescanned rather than dismissed to manufacture a clean gate.
Optional secret-scanning validity checks and non-provider patterns remain
disabled and are not claimed.

The `release-promotion` and `github-pages` environments require the repository
owner as reviewer and disallow administrator bypass. Self-review remains
available because there is one human maintainer, and release records must state
that truthfully. The Pages environment accepts only `main`; neither environment
turns AI-assisted implementation or review into a second human approval.

## ADR-066: Import liveness and canary coverage remain closed under synchronous parsing

**Status:** accepted 2026-07-26.

Once raw bytes are durably committed, long synchronous parser work reports
liveness through a serialized background operation heartbeat. The heartbeat
persists the current phase and unchanged committed-byte count at one quarter of
the public five-second budget. It never advances bytes, and serialized sink
writes prevent an older heartbeat from overwriting a newer phase. A durable
sink failure remains an import failure rather than an invisible telemetry gap.

`boundary-canary-v2` replaces v1's short raw-hex alignment fragments with JSONL
whitespace. Its normal filler records retain deterministic high-entropy
allocation, and its five checkpoint objects remain the only expected
candidates. Generic JSON/JSONL parsing now assigns every successfully decoded
noncandidate value to `skipped` and every malformed value to `unparsed`;
unparsed input keeps coverage incomplete. The generator version changes
because changing deterministic bytes without changing their declared identity
would invalidate SHA-bound acceptance evidence.

## ADR-067: Artifact home-path detection follows payload type inside archives

**Status:** accepted 2026-07-26.

Absolute developer-home paths remain a release-blocking finding in
human-readable artifacts and ZIP members, including JSON metadata, checksum
sidecars, configuration, scripts, and documentation. Opaque compiled ZIP
members use the same policy already applied to top-level native binaries:
incidental upstream build/debug roots are not treated as developer-data
leakage.

This distinction changes only absolute-path detection. Complete private-key
blocks, credential canaries, and raw-context canaries remain scanned in every
member regardless of suffix, and archive size/count/encryption/path ceilings
remain fail-closed. The policy is type-based rather than an allowlist for the
three extension names that exposed the inconsistency, while text sidecars
cannot bypass the gate.

## ADR-068: Unpublished immutable-release drafts use numeric release identity

**Status:** accepted 2026-07-26.

GitHub immutable-release mode does not expose an unpublished draft through the
published-by-tag release route and does not create its tag ref until
publication. Candidate and publication controls therefore discover drafts
through the authenticated, paginated release listing, require exactly one
release with the requested `tag_name`, and bind its numeric release ID,
target commit, draft/prerelease state, and exact asset names, sizes, and
SHA-256 digests. All pre-publication reads, asset downloads, asset uploads,
state rechecks, and the final `draft=false` transition use release-ID REST
routes.

The unused-version gate enumerates every authenticated release page before
checking the published-by-tag release and tag ref. Any draft or published
release with the requested tag closes the slot, including when both tag routes
return 404. This preserves single-use semantics and prevents a failed
post-create check from enabling a duplicate candidate race.

After publication, the control returns to tag-addressed verification and
requires the published-by-tag release, the exact source-bound tag ref,
immutable state, and `gh release verify`. Promotion consumes only published
releases and remains tag-addressed.

## ADR-070: Browser acceptance uses a packaged favicon and the accepted handoff boundary

**Status:** accepted 2026-07-26.

The packaged dashboard declares and ships a same-origin SVG favicon. It never
uses an external icon URL. This closes the implicit browser request that
otherwise reached `/favicon.ico`, returned JSON 404, and produced a console
error during an otherwise healthy exact-candidate handoff. A production build
and bundled-serving regression must keep the icon present before a browser
receipt can claim zero unexpected console errors.

Browser acceptance reads the opaque capability only from the HTML-escaped
`data-browser-token` attribute on the nonce-protected ADR-064 script. The
acceptance parser must treat the response as inert HTML, bind the inline
script nonce to the response CSP, require the constant handoff script and
production storage/target attributes, reject external `src`, and reject
executable credential literals, ambiguous duplicate handoffs, and non-script
lookalikes. Independent review added those fail-closed checks after the initial
functional extractor correction; the exact production handoff/CSP probe and
focused adversarial regressions pass on the integrated source.

The one-use ticket contract remains the frozen Phase D boundary: the ticket
expires, cannot replay, leaks through no referrer or cache, and is absent from
current navigation after exchange. Browser history implementation files are
not required to erase every byte of an already consumed ticket. The
long-lived Core credential and short-lived browser capability retain their
separate, stricter ADR-009 storage and revocation requirements.

## ADR-071: Import-operation rows own operation-scoped reprocess liveness

**Status:** accepted 2026-07-26; amended 2026-07-27.

The durable import-operation ID is the queryable progress authority for an
operation-owned parse or retry. Its periodic unchanged-byte heartbeat must not
wait behind a source-metadata heartbeat transaction. Reprocess therefore keeps
the caller's operation sink as the tracker's sole periodic durable sink when
an operation tracker is supplied.

This authority split does not remove source lifecycle state. Direct
source-only imports and reprocess calls retain their source progress sink.
Operation-owned parsing still writes explicit source processing metadata after
parse and explicit complete, cancelled, or failed metadata at terminal
boundaries. Operation sink failures remain import failures, phase and byte
progress remain monotonic, and closed error codes continue to propagate to
both durable lifecycle views.

Parser-driven reclassification may delete a provisional source and bind the
tracker to an existing canonical source. Operation completion, failure, and
cancellation must then rebind the durable operation row to that canonical
`source_id`. When the merge lands on an already-complete canonical source,
do not downgrade it to processing or re-ingest it. If the operation sink has
already terminalized the row under the pre-merge source id, the outer terminal
handler may rebind only `source_id` without changing terminal status, phase,
result, or closed error code.

The decision follows an exact Linux-package WSL2 observation where the prior
combined sink performed source and operation transactions serially under one
tracker emission lock and the store write lock. The source transaction could
delay the authoritative operation update beyond the frozen five-second
observer budget. Adversarial tests must block the source progress sink and
still observe fresh operation-row heartbeats with unchanged bytes, while also
proving source-only liveness, terminal source state, and post-merge operation
source rebinding on success, failure, and cancellation. Exact rebuilt-candidate
evidence remains required; source tests do not satisfy BETA-D01.

## ADR-072: Unchanged-byte operation liveness uses a bounded telemetry commit

**Status:** accepted 2026-07-28.

The authoritative import-operation row must remain observer-visible within the
frozen five-second heartbeat budget even when committed bytes do not change.
Exact candidate `4257e40` completed the 2,000,000,000-byte import correctly on
a qualified Ubuntu 24.04 QEMU/WHPX target, but 15 of 17 parsing intervals
exceeded five seconds and the maximum was 10.196354 seconds. The remaining
periodic path still used the full lifecycle transaction: it could spend the
10-second SQLite busy budget acquiring a writer and required a FULL-synchronous
commit for a noncanonical timestamp.

Operation trackers therefore have a dedicated liveness sink. A liveness tick
updates only `import_operations.updated_at`; it never rewrites status, phase,
progress JSON, received or committed bytes, source identity, result, error, or
terminal state. The connection requires the existing WAL database and uses
`synchronous=NORMAL`, which remains consistent and durable across application
process crashes while avoiding a virtual-disk FULL flush for each unchanged-
byte timestamp. Store-lock acquisition, SQLite connection timeout, and SQLite
busy timeout are each bounded to 250 ms. SQLITE_BUSY and SQLITE_LOCKED return a
retry signal without advancing the tracker's successful-emit clock; missing
rows and every other SQLite error still fail closed.

The liveness sink does not wait behind the tracker's lifecycle emit lock.
Byte-advancing, phase-changing, cancellation, failure, completion, and source-
rebind writes retain the original serialized lifecycle sink and its stronger
durability. Source-only imports have no dedicated liveness sink and retain the
existing serialized source progress path. Because a timestamp-only update is
semantically neutral, concurrent liveness cannot regress progress or overwrite
a newer phase snapshot.

Focused regressions hold an external WAL writer to prove the touch returns
within the bounded wait, then prove it recovers without changing any semantic
field. They also prove terminal/missing behavior and that non-lock I/O errors
propagate. Existing operation parse, retry, cancellation/failure, and source-
only tests retain end-to-end coverage. A rebuilt exact artifact on the frozen
Linux target is still required; source tests do not satisfy BETA-D01.

**Amendment 2026-07-28.** A content-free WSL2 discriminator separated the
remaining symptom into writer and observer causes. The global Python store lock
starved timestamp-only commits even when SQLite could arbitrate independently.
The synchronous status dependency then joined the shared application
threadpool and performed an activity-writing authentication transaction before
the status read; later stage timing also showed that separate registration and
operation reads could establish a stale snapshot across a scheduler delay.

Timestamp-only operation touches therefore bypass the Python lifecycle lock and
retain bounded SQLite busy handling. Only trackers with this lightweight sink
run at one tenth of the five-second public budget (two timestamp-only attempts
per second, 2.5 times the former attempt rate); source-only trackers retain the
one-quarter cadence so full source-metadata writes do not increase.

The operation status dependency is async and delegates to one dedicated
single-worker observer. That worker owns a persistent read-only/query-only WAL
connection and performs current-revocation plus operation lookup in one
freshest joined statement and explicit read transaction. Repeated polls avoid
PBKDF through a single-entry, process-keyed HMAC fingerprint kept only in
worker memory; each poll still matches the durable client id and token-hash
identity with `revoked_at IS NULL`. Mismatch, revocation, and application
shutdown clear the cache; shutdown also closes the connection on its owner
thread. The worker is recreated for each sequential application lifespan so a
later lifespan never reuses a shut-down executor. Scope is enforced before
missing-operation disclosure, and ordinary
authentication/activity writes remain unchanged on all other routes. This
high-frequency status route authenticates and rechecks revocation without
treating every poll as durable client activity. Generic internal operation
reads, semantic progress, and lifecycle writes remain unchanged.

## ADR-073: Operation-owned streaming JSONL parsing yields at durable progress checkpoints

**Decision.** Streaming JSONL parsing with an operation-owned liveness sink
performs a one-millisecond cooperative scheduler handoff at each existing
one-MiB parse-progress checkpoint. Plain source-only parsing and parser paths
that do not own the queryable operation heartbeat do not add this handoff.

**Reason.** Qualified exact-candidate evidence separated durable liveness from
API receive liveness during a 2,000,000,000-byte cancel/no-upload retry. The
operation timestamp advanced within 4.936978 seconds and direct SQLite
observation remained within 3.731520 seconds, but authenticated API receipt
reached 5.735102 seconds. API query latency reached 3.428642 seconds and first
delivery lagged direct visibility by 3.986875 seconds. Client stage timing
placed as much as 2.699178 seconds before response headers and another
0.846243 seconds between headers and the completed body. Functional result,
source hash, chunk identity, coverage, SQLite integrity, and foreign keys all
remained correct. The remaining loss was therefore scheduling and delivery
inside the CPU-busy Core process, not a stale or frozen durable row.

The boundary canary is millions of small JSONL objects. That parser previously
kept its worker continuously runnable between automatic interpreter and
platform scheduler decisions even though it already crossed a progress
checkpoint about once per MiB. A positive one-millisecond pause at those
existing checkpoints provides a cross-platform scheduling turn to the
dedicated observer and ASGI loop. A zero-duration pause was rejected because
the deterministic Windows regression proved that it does not reliably
schedule a waiting observer.

No status data is cached or synthesized. The observer still authenticates the
credential, checks durable revocation, selects the operation from the
read-only WAL connection, preserves scope-before-NotFound behavior, and
returns only durable state. The heartbeat cadence and five-second threshold
are unchanged. At the inclusive plain-JSONL boundary, the added pauses total
less than two seconds across the operation rather than adding latency to each
API request. A new immutable candidate and qualified rerun are required; this
source decision is not acceptance evidence.

## ADR-074: Preserved-source reconstruction checks cancellation at bounded chunks

**Status:** accepted 2026-07-30.

An operation may expose `parsing` before `reprocess_source` reconstructs the
preserved raw blob into its caller-owned temporary file. That reconstruction
is part of the cancellable worker lifecycle. The storage copy helper therefore
accepts an optional checkpoint and invokes it after every bounded source
chunk. Operation-owned and source-only reprocess pass the tracker's
`check_cancelled` callback. Stored chunks are at most 8 MiB; cancellation no
longer waits for the complete source copy. If a checkpoint raises, the
existing fail-closed path removes the partial target before propagating the
exception.

The three cancellation clocks remain distinct:

1. `POST .../cancel` durably records intent and may return the current
   `processing` status with `cancel_requested=true`.
2. The worker acknowledges at its next checkpoint, and the lifecycle sink
   commits terminal `cancelled` state for the operation and processing source.
3. Quiescence is reached only when the active upload/retry request and its
   producer or parser work have unwound.

The frozen contract remains strict: durable acknowledgment must be observed in
less than five seconds and worker quiescence within 30 seconds. The response is
not relabeled as terminal before the worker acknowledges, no synthetic state
is returned, and no threshold is relaxed.

Exact candidate `7ffb1a4` exposed the missing checkpoint on Windows after its
straight 2,000,000,000-byte import and repeat passed. The cancel request
returned, authenticated GET remained responsive, and timestamp-only liveness
continued to commit, but semantic status stayed processing beyond five
seconds. A production-path controlled-copy regression reproduced the causal
ordering without personal content: before the fix, HTTP intent returned in
0.021 seconds, durable terminal state missed a scaled 0.75-second bound, and
worker quiescence arrived at 1.560 seconds only after reconstruction. With the
chunk checkpoint, the same fsynced evidence records 0.022-second HTTP return,
0.113-second durable acknowledgment, and 0.135-second quiescence. This rules
out the API observer, liveness writer, and SQLite/Python writer lock as the
observed cause. A new immutable candidate must rerun the full Windows
boundary/cancel/retry/interruption/export/restore journey; source evidence does
not satisfy BETA-D01.

## ADR-075: Public-path and launch-watch gates close only after publication

**Status:** accepted 2026-07-30.

The protected beta publication decision requires exactly 20 prepublication
receipts. `BETA-R05` and `BETA-O01` are postpublication gates and are rejected
if included in that bundle. The sole maintainer reviews every prepublication
receipt ID without claiming independent human review. Public-download/channel
smoke, live release/documentation/support/security/recovery paths, and a
triaged launch watch cannot be truthfully asserted while the release is still
an unpublished draft. Their eventual pass receipts require exact
downloaded-artifact evidence bound to the candidate inventory; source-only
preparation cannot satisfy either gate.

This sequencing does not defer documentation readiness. Existing exact-source
candidate validation fails closed unless `SUPPORT.md`,
`docs/KNOWN_ISSUES.md`, `SECURITY.md`, and the recovery runbook exist, contain
their required operational contracts, and remain linked from `README.md`.
Private vulnerability intake and Core-only/fail-closed credential guidance
must therefore be present before candidate creation. Source readiness is not
laundered into an O01 receipt; O01 closes only against live postpublication
evidence.

The earlier contract required O01 before `draft=false` even though Phase E and
B-206 require public release, channel, URL, and launch-watch evidence. That
cycle made truthful publication impossible. Moving O01 beside R05 resolves the
cycle without weakening any product, platform, provider, security, recovery,
inventory, key-custody, or maintainer-approval gate.

## ADR-076: Operation-owned reconstruction yields at bounded copy checkpoints

**Status:** accepted 2026-08-01.

Preserved-source reconstruction retains ADR-074's cancellation checkpoint and,
only for a tracker with queryable operation liveness, adds a one-millisecond
cooperative scheduling handoff after every stored source chunk. Chunks are at
most 8 MiB. Source-only reprocess retains its previous cadence.

Candidate descriptor
`b00297d19080d0a3252a48fe5d7ac3ad78d5395909612f86eb2ef1f2e851bc16`
on source `905efe5631ebf2fee77fafa5d8694f77df17b8bb` completed straight and
repeat data work but exposed a 5.448395-second durable `updated_at` interval
during unchanged-byte repeat `parsing`. The repeat path exposes `parsing`,
reconstructs its preserved blob, and exposes `parsing` again before parser
entry; parser checkpoints already yielded, but copy checkpoints only checked
cancellation.

A content-free production-path regression on untouched `905efe5` reproduced
the missing scheduling opportunity without large data: first successful
liveness touch arrived 0.964490 seconds after reconstruction began versus a
scaled less-than-0.4-second requirement, while repeat completion and candidate
IDs remained correct. The corrected checkpoint checks cancellation first and
then yields only for operation-owned work. No durable field, response meaning,
heartbeat cadence, or five-second threshold changes. Source-only negative and
existing cancellation/partial-copy regressions close the scope. Source tests
are not BETA-D01 acceptance; a new immutable candidate must rerun Windows.

## ADR-077: Preserved-source retries begin at the durable committed boundary

**Status:** accepted 2026-08-02.

A no-upload retry operates on a source whose raw bytes are already durably
stored and integrity-checked. Its operation tracker must therefore begin at the
preserved source's declared byte size rather than replaying the upload progress
domain from zero.

Exact candidate source `65612cc` passed the corrected Windows straight,
repeat, and cancellation timing slices. During the subsequent no-upload retry,
a direct SQLite observer caught a committed-byte/percent regression. The retry
claim retained the full source, but `_run_retry` constructed
`ImportProgressTracker` with its default zero position, then forced a `storing`
phase write before calling `advance_bytes` with the declared size. Those two
durable lifecycle writes made the operation briefly report zero committed
bytes and zero percent after it had already reported the complete preserved
source.

`ImportProgressTracker` now accepts a validated `initial_bytes_processed`
position. Initialization rejects negative values and values above
`bytes_total`, and sets both the monotonic byte counter and the last-emitted
byte watermark to that position. `_run_retry` supplies the preserved declared
size at construction and removes the redundant post-construction advance. Its
first forced `storing` write therefore preserves full committed bytes and the
nonterminal 99-percent ceiling. All other tracker callers retain the zero-byte
default; phase ordering, liveness cadence, cancellation, completion, and the
five-second acceptance budget are unchanged.

A tracker regression proves the validated initial position and 99-percent
reservation. The production retry regression proves that the constructor is
seeded with the preserved payload size. Source tests are not BETA-D01 evidence;
the invalidated candidate must be replaced and the complete exact-artifact
journey rerun.

## ADR-078: Blob finalization keeps operation liveness queryable

**Status:** accepted 2026-08-02.

Operation-owned durable heartbeats begin immediately after the upload claim is
published, before staging or source-blob finalization, and stop
unconditionally when the upload worker exits. A later reprocess call may ask
to start the same tracker again; tracker startup remains idempotent.

Chunked source-blob finalization continues to serialize every lifecycle writer
under CoreStore's re-entrant Python write lock. Its potentially cold
chunk-index and byte-total validation now runs in a deferred WAL read
transaction. The timestamp-only operation liveness writer deliberately
bypasses that Python lock and can therefore commit while validation reads a
stable source-blob snapshot. After validation, a fresh short immediate
transaction rechecks blob identity, size, completeness, and storage kind before
marking it complete. Inline and zero-byte integrity rules are unchanged.

Exact candidate source 7afc46b completed one Windows boundary straight/repeat
probe within the liveness budget, then an evidence-complete fresh straight run
reproduced an intermittent failure. At unchanged 2,000,000,000 committed bytes,
top-level operation timestamps were 6.325973 seconds apart through the
authenticated API and 6.253638 seconds apart through direct SQLite. Functional
import identity, closed coverage, SQLite integrity, and foreign keys remained
correct, but the frozen five-second gate failed and no receipt was emitted.

The operation scheduler previously began only inside reprocess_source, after
promotion. A content-free source discriminator also proved that the
chunk-layout scan itself occupied SQLite's writer transaction: during a
1.253978-second scan, independent liveness touches failed and the first
post-commit touch succeeded 0.013890 seconds later. Deterministic regressions
now hold that scan open and prove a liveness commit remains possible, and hold
promotion before parser entry while proving multiple durable timestamps plus
unconditional heartbeat shutdown.

## ADR-079: Nonterminal operation progress avoids FULL WAL flush stalls

**Status:** accepted 2026-08-08.

Explicit nonterminal import-operation progress uses a serialized WAL
`synchronous=NORMAL` connection. It retains CoreStore's Python lifecycle lock,
`BEGIN IMMEDIATE`, the ordinary ten-second SQLite arbitration budget, complete
row validation, monotonic byte rules, and atomic commit. Only the durability of
the latest observer-facing nonterminal transition changes: it remains safe
across a process crash but, like other WAL-NORMAL telemetry, the newest commit
may roll back after an operating-system or power failure. Authoritative source
blobs, source records, and parser outcomes remain separate canonical writes.
Startup recovery terminalizes any surviving nonterminal operation.

The NORMAL path is fail-closed and deliberately narrow. The caller must provide
an explicit `awaiting_upload`, `uploading`, or `processing` status, must not
complete the operation, and must not change cancellation intent, preflight,
error state, or result data. Cancellation requests, clear/error writes,
terminal complete/failed/cancelled transitions, and every update without an
explicit nonterminal status retain the original FULL-durability transaction.
The timestamp-only liveness writer remains a separate 250-millisecond,
Python-lock-bypassing WAL-NORMAL path; semantic progress does not inherit that
fail-fast busy budget.

Exact candidate source `4ab235d` completed two qualified Linux x86-64
2,000,000,000-byte straight imports with correct data, coverage, resources,
SQLite integrity, and foreign keys, but emitted no D01 receipt. At unchanged
2,000,000,000 committed bytes in `processing`/`parsing`, attempt one measured a
5.918573-second durable top-level timestamp interval and attempt two measured
5.332539 seconds. API and direct-SQLite receipt observations independently
exceeded five seconds in both attempts.

Bounded controls kept a separate 20-millisecond process schedulable during a
full two-billion-byte source stream, temporary reconstruction, and the complete
4,134,533-line parse. Source inspection then isolated the remaining handoff:
the lifecycle updater generates `updated_at` inside a FULL transaction before
commit, readers keep the previous WAL row while that commit flushes, and the
timestamp-only writer cannot enter SQLite's single-writer slot. A NORMAL
nonterminal commit removes that per-transition FULL flush without weakening
canonical or terminal durability. Focused tests prove the WAL mode/busy budget
and adversarial routing of preflight, cancellation, error, clear-error, result,
and terminal updates. The frozen five-second threshold is unchanged. Source
wheel `0905c7ab90de20dd7d5cf2b0f01a9fbc6a9ec8e1244ed15b34c2da4d985da974`
then passed a straight-only run in the same qualified guest with a 0.780195-
second maximum durable timestamp gap and 0.786998/0.800204-second maximum
API/direct receipt gaps. It retained exact two-billion-byte identity and closed
coverage but emitted no receipt. Source tests and local wheels are not
acceptance evidence; a rebuilt immutable Linux artifact must rerun the complete
BETA-D01 journey.

## ADR-080: Security advisories advance frozen dependency locks

**Status:** accepted 2026-08-08.

The release dependency gates intentionally audit the reviewed lock state on
every hosted matrix run. When new advisories made the exact branch fail closed,
the repository advanced only the affected reviewed packages rather than
weakening, ignoring, or dismissing the gates.

The Python runtime range now requires `cryptography>=50,<51`, and `uv.lock`
selects `50.0.0`, the first version that closes all three reported
cryptography findings (PYSEC-2026-3552, PYSEC-2026-3553, and
PYSEC-2026-3554). The dashboard retains its existing direct dependency ranges;
its lock alone advances the affected transitives to `nanoid 3.3.18`,
`postcss 8.5.26`, and `undici 7.29.0`.

No vulnerability waiver, audit threshold change, package-manager override, or
runtime feature is introduced. The frozen Python export audit and dashboard
high-severity audit must pass locally and in the complete replacement hosted
matrix, and the ordinary cross-platform/package test gates remain authoritative
for compatibility with the cryptography major-version change.

## ADR-081: macOS packaging statically links source-built cryptography OpenSSL

**Status:** accepted 2026-08-08.

Cryptography 50 publishes a macOS ARM64 wheel but no macOS x86-64 wheel. The
reviewed Intel packaging install therefore builds its Rust extension from
source. The first hosted replacement matrix linked that extension to Homebrew
OpenSSL, while PyInstaller selected Python's incompatible same-basename
`libssl.3.dylib` for the bundle. Packaged startup failed on
`_SSL_get0_group_name` before application code ran.

All macOS installs that request the packaging extra now set cryptography's
documented `OPENSSL_STATIC=1` build mode and bypass pip's wheel cache so a prior
dynamic local build cannot be reused. Immediately after the locked,
hash-enforced third-party install, the installer locates the installed Rust
extension and runs `otool -L`; either `libssl.3.dylib` or
`libcrypto.3.dylib` is a fail-closed packaging error. This avoids relying on
PyInstaller collision order or a mutable Homebrew path and keeps cryptography's
OpenSSL implementation inside its extension. Python's standard-library TLS
dependency remains independently bundled and package startup exercises both.

The rule is scoped to Darwin plus the packaging extra. macOS wheel installs
also pass the static-link check, while Windows, Linux, ordinary development,
and audit installs inherit their previous environment. The frozen dependency
range, vulnerability gate, package smoke, native architecture matrix, and
release-candidate evidence requirements are unchanged.

## ADR-082: Windows rollback smoke re-enters one persisted retry state

**Status:** accepted 2026-08-08.

The Windows update helper intentionally treats an interrupted rollback as a
recoverable journal state. An `OSError`, bounded helper error, or SQLite error
during restoration leaves the journal at `rolling_back` with
`rollback_retry_required`; the helper exits 3, and RunOnce or the ordinary
Core-start guard re-enters that exact journal. The hosted diagnostic retains
only that bounded state, so its underlying exception is not claimed. Unit
coverage already proves the next invocation can finish the restoration.

The packaged first-run smoke previously required the first forced-health-failure
invocation to return the terminal rollback code 2. An exact merged-main job
therefore failed on the designed retry state even though the byte-identical tree
had completed the same Windows smoke twice. The smoke now performs exactly one
second invocation only after verifying both the persisted phase and fixed error
code. The resumed helper must return 0, after which the existing terminal
`rolled_back` journal, restored application/MCP/recovery/updater hashes,
pre-update SQLite database, prior-Core health, uninstall, and cleanup checks all
remain mandatory. Any other first result, malformed state, or unsuccessful
second invocation still fails closed.

This is test-contract alignment, not a production retry loop. Helper behavior,
RunOnce recovery, timeouts, release gates, and update semantics are unchanged.

## ADR-083: Shared-host retrieval smoke is not latency certification

**Status:** accepted 2026-08-08.

Retrieval V3 latency acceptance remains the integrated CLI's 1k/10k
wall-clock measurement on comparable hardware, including five immediate warm
repetitions per query and a strict 10k warm p95 below 150 ms. That production
benchmark remains fail-closed and retains its clock, sample count, profiles,
and threshold.

The bounded 100-record pytest integration runs inside the complete shared-host
suite. It proves retrieval quality, authorization, time, admissibility,
determinism, storage wiring, and lifecycle behavior, but it cannot certify a
hardware-sensitive latency SLO. A hosted Windows run demonstrated the mismatch:
all functional gates passed while concurrent-host delay inflated warm p95 to
1,305 ms. Pytest therefore no longer treats its measured wall clock as
acceptance evidence. The production operational calculation is factored into
one helper with deterministic boundary regressions: 149.999 ms passes; 150 ms,
non-finite, negative, missing, and any failing required profile do not. A green
shared-host suite must never be described as a Retrieval V3 latency pass.

## ADR-085: macOS preparation is deterministic but native acceptance stays physical

**Status:** accepted 2026-08-15. ADR-086 superseded its product-scope and
acceptance requirement on 2026-08-16 while preserving this record as
engineering history. The native Mac cells below were never passed, skipped,
waived, or marked unavailable.

Beta 1 continues to require native macOS 26 ARM64 and native macOS 26 x86-64.
Rosetta, a virtual machine, a hosted package job, or success on the other Mac
architecture cannot close either target. The exact macOS patch and stable
Codex/Claude versions are frozen when the two physical hosts are selected.

Every hosted Mac package job now records a content-free host preflight. The
release-candidate job also mounts the direct DMG and fails unless the package
report, checksum, unsigned notice, bundle identity, version, safe internal
links, structural code seal, and main/MCP/recovery binary architectures all
match the declared target. A publisher identity is still rejected when the
package declares the unsigned-community trust state. This strengthens artifact
identity; it does not add Developer ID signing or notarization.

Physical-host preparation uses a strict preflight that requires the exact OS
version, native non-Rosetta architecture, four logical CPUs, 8 GiB memory,
internal solid-state root storage, more than 16 GiB free, non-root execution,
native tools, and a dedicated-clean-user attestation. A candidate-bound runner
then verifies the complete inventory and exact clean source, mounts and stages
the DMG, executes the existing isolated package smokes, removes only run-owned
state, and retains content-free reports. When the tooling is newer than an
already-frozen candidate, it must be invoked by that candidate checkout's
Python environment; the runner verifies the imported package path and records
its own tool-file digests.

The runner always records `preparation_only=true`,
`acceptance_claimed=false`, and `canonical_receipts_emitted=false`. It cannot
replace the supervised Gatekeeper path, stable Codex and Claude journeys,
login/reboot persistence, Keychain failure rollback, raw-store secret scan,
authorization/deletion/recovery matrix, or allocated two-billion-byte import
journey. ARM64, Intel, Codex, and Claude results remain separate slices until
every frozen cell passes and one unique receipt per gate is consolidated.
Deferred or unrun Mac work is never relabeled as passed, skipped, or
unavailable.

## ADR-086: First public beta supports Windows and Linux, not macOS

**Status:** accepted 2026-08-16; supersedes ADR-085's product-scope and
acceptance requirement while preserving it as engineering history.

The first public beta supports exactly Windows 11 x86-64 and Ubuntu 24.04 LTS
x86-64 with GNOME and a working Secret Service/GNOME Keyring backend. The
supported stable-client cells are Codex on Windows/Linux and Claude Desktop on
Windows; Linux Claude beta is not promoted into the stable-only claim. ChatGPT,
Claude, and Grok export claims remain mandatory and unchanged.

macOS is not a beta platform. Existing Mac runtime, packaging, Keychain,
LaunchAgent, DMG, preflight, and tests remain in the public source tree so the
cross-platform implementation is not destructively removed. The former hosted
Mac CI configuration is historical only; ordinary CI now schedules supported
Windows and Ubuntu runners, while the Mac paths remain unsupported portability
code. The consumer release workflow builds no Mac job, the candidate inventory
accepts no Mac asset, publication accepts no Mac manifest, public copy
advertises no Mac download, and no Mac receipt can close or strengthen a beta
gate. A future return to Mac support requires a new ADR, newly frozen support
table, new candidate, current documentation, and native evidence; ADR-085
preparation cannot be retroactively counted.

The 20 prepublication gate IDs remain unchanged. Gates such as BETA-D01,
BETA-D03, and BETA-X01 now quantify only over the two supported artifact
targets and the frozen supported client/provider claims. This is an explicit
pre-candidate product-scope decision, not a claim that deferred Mac cells
passed, failed, were skipped, or became unavailable.

The existing unpublished `v0.1.0-beta.1` four-platform draft remains bound to
its live identity: numeric release ID `367337056`, source
`563a397d3095f1f45bb5814dfd39d9d7c4fab0bc`, release-candidate run
`31285545048`, and candidate digest
`ba17eeec2e82d1ee1b0621f77024a03c78807496e8f1f07bfce38f0c42842ebe` (55
assets). An earlier episode created draft `360008392` from source
`48815077544f9defb78d0e6b9c8022319888dfed`; that episode is historical and is
no longer the live release identity. Immutable-version controls prohibit
retargeting, deleting, replacing, or publishing the live draft and prohibit
reviving or reusing the earlier draft episode, so the Windows/Linux-only
source version advances to `0.1.0-beta.2`.

Release signing remains offline Ed25519 signing of the Windows x86-64 OTA
manifest only. This decision originally required two encrypted backups in
distinct failure domains; ADR-093 supersedes that count with one encrypted
backup separate from the operator-controlled primary. After the applicable
restore test, one BETA-R02 source receipt may be emitted. The separate bundle
decision remains null until
all 20 unique prepublication receipts pass and the maintainer reviews them.
Only an explicit approve permits offline signing, immutable publication, and
channel promotion; private signing material never enters GitHub, Actions, the
repository, an AI system, a shell argument, or an environment variable.

## ADR-087: Privileged release workflows check out the default-branch dispatch SHA

**Status:** accepted 2026-08-16; revised 2026-08-17 after review of the
candidate versus publish/promote lifecycle and the protected-main rescan.

Privileged `workflow_dispatch` jobs that check out source and execute
repository code are release-control surfaces. They must not check out an
operator-supplied SHA that can differ from the default-branch dispatch
commit, and they must not consume GitHub Actions caches that untrusted
workflows can poison.

Every source-executing job in `release-candidate.yml` (`validate`, `native`,
`draft`), `publish-beta-release.yml` (`publish`), and
`promote-beta-channel.yml` (`build`) therefore fail-closes in inline Bash
before checkout: the requested ref must be `refs/heads/<default_branch>`,
and `inputs.source_commit` must be exactly 40 lowercase hexadecimal
characters. Checkout then uses `ref: ${{ github.sha }}` so the executed
tree is the trusted current protected default-branch dispatch snapshot,
not `inputs.source_commit`.

The two privileged families then diverge:

- Candidate-build jobs (`validate`, `native`, `draft`) construct one
  candidate from that dispatch snapshot. Their `inputs.source_commit` is
  that snapshot's identity and must equal `github.sha`.
- Later publish (`publish`) and promote (`build`) jobs may run after
  protected `main` has advanced. Their `inputs.source_commit` is the
  reviewed historical candidate/release identity and must not be required
  to equal the later `github.sha`. Those jobs still check out `github.sha`
  for current protected release-control code and pass the historical
  `source_commit` as data to the existing release/candidate verification
  steps.

In the new pre-check steps only, GitHub expressions are bound through
`env` and are not interpolated into the shell script. Later verification
steps may still pass `inputs.source_commit` and other inputs as data.
Actions cache access (`cache`, `cache-dependency-path`, and
`actions/cache`) is removed from the three privileged release workflows;
`setup-uv` keeps `enable-cache: false`. Ordinary CI caches remain.

Exact protected `main` `6be7e1d032714b39528fcc31d5333539406d08a6` passed
hosted CodeQL run `31991996483` for Actions, JavaScript/TypeScript, and Python.
Each analysis reported zero results, `main` had zero open code-scanning
alerts, and alerts #3 through #21 closed as fixed without dismissal. The
separate Windows provider-acceptance CI failure on that SHA remains blocking
under ADR-088; the CodeQL result does not waive a required CI context.

## ADR-088: Packaged provider acceptance closes Core before owned vault removal

**Status:** accepted 2026-08-17.

Exact protected `main` `6be7e1d032714b39528fcc31d5333539406d08a6` exposed a
nondeterministic Windows failure in
`test_packaged_surface_removes_its_disposable_vault`. The hosted assertion
retained only exit 1, not the content-free stage report, so the precise failing
stage was not directly observed. Inspection found a concrete lifecycle gap:
packaged `--packaged-provider-acceptance` constructed a `CoreService`, imported
through the production operation path, then called `shutil.rmtree` on its
owned data root without an explicit Core close. Windows cannot unlink SQLite,
WAL, or SHM files while this process still holds a handle, so that path can
fail closed as `data_dir_cleanup_failed` even after a truthful complete import.

`CoreStore.close()` is the explicit shutdown path. It is idempotent and
best-effort: it closes the thread-local import-operation observer, then under
the existing write lock opens a `_ClosingConnection`, runs
`PRAGMA wal_checkpoint(TRUNCATE)`, and lets that context close the connection.
Only `sqlite3.Error` is swallowed on this release path. It does not change
`journal_mode`, hide later `rmtree` errors, sleep, retry, or force garbage
collection. Because CoreStore does not keep a process-wide writer, a later
public method may open a new connection after `close()`.

`CoreService` exposes `close()`, `__enter__`, and `__exit__` that delegate to
the store. Packaged provider acceptance binds that service as a context
manager around the import operation so close always precedes owned
`data_root` cleanup on success and every exception path. Caller-supplied
data-dir deletion behavior is unchanged: only an acceptance-owned temporary
root is removed. An `OSError` from that owned `rmtree` still replaces the
payload with `data_dir_cleanup_failed` and returns exit 1.

Candidate dispatch stays blocked pending this follow-up merge and exact-main
green. This decision does not satisfy real-provider exact-candidate receipts.

## ADR-089: Unpublished beta.2 stays historical; P06 source fix advances beta.3

**Status:** accepted 2026-08-17.

`ROADMAP_TO_V1.md` requires keyboard traversal and visible focus for BETA-P06.
Source inspection of the unpublished `0.1.0-beta.2` tree at protected main
`6151e1f8850793c80ebe01d7db0b38e3ac1aff05` found that the global
`:focus-visible` amber outline is overridden by a later equal-specificity
`.search-input input { outline: 0; }` rule. The `.search-input` label wrapper
has only a static border. A static border is not a focus indicator, so that
source cannot honestly pass P06.

The unpublished `0.1.0-beta.2` draft/candidate remains an occupied historical
identity. Its evidence is not rebound, deleted, relabeled, weakened, or
reused. Exact-candidate evidence cannot be rewritten onto a later source tree.
The next candidate must be rebuilt as `0.1.0-beta.3`.

The smallest source correction keeps the input borderless and adds a
`:focus-within` outline on the existing search label wrapper, matching the
global amber outline and offset. A Python source regression fails unless that
wrapper declares a nonzero, non-none focus-dependent indicator and keeps the
input `outline: 0`. A dashboard test keeps the existing sr-only accessible
name. This source fix only makes a new exact Windows/Linux candidate eligible
for fresh Edge acceptance. It does not pass P06.

macOS remains unsupported and excluded from candidate assets, receipts, and
support claims. Retained Mac source and CI code is unchanged portability work.

## ADR-090: Adopt zero-routine-friction as the post-V1 product direction

**Status:** accepted 2026-08-20.

After the first usable public beta, ATC is developed as background local-first
infrastructure: users connect supported sources and clients once, then work
normally without a routine memory inbox, manual classification, project
curation, repeated archive imports, or dashboard administration. The normative
contract is
[`product/ZERO_FRICTION_PLATFORM.md`](product/ZERO_FRICTION_PLATFORM.md), and
its dependency- and evidence-ordered implementation sequence is
[`product/ZERO_FRICTION_EXECUTION_PLAN.md`](product/ZERO_FRICTION_EXECUTION_PLAN.md).

Core remains the sole canonical authority. Connected text is inert untrusted
data; authorization and temporal/lifecycle resolution occur before derived
work; projections are disposable and dependency-bound; and correction,
retention, deletion, and purge must close future ATC influence without claiming
retroactive erasure from an external provider. The current source, observation,
ledger, current-record, Retrieval V3, Memory Lab, and project-scope mechanisms
are extended rather than replaced by parallel authorities.

Integration claims are capability-qualified. L0 MCP is a best-effort
compatibility path. Pre-generation delivery, direct-turn capture, working
checkpoints, observable outcomes, or consequence enforcement require matching
L1-L3 lifecycle hooks and acceptance evidence. Experimental connector, runtime,
event, and lineage contracts remain v0 until exercised by a fake harness and
one real vertical slice; stable SDK promises come later.

This decision does not expand, block, or grant acceptance credit to the public
immutable `0.1.0-beta.6`. Beta publication and tracker reconciliation are now
complete, and PR #73 merged the provider-neutral capture ledger. The execution
plan therefore begins with contract reconciliation against that ledger, then a
disposable zero-dashboard harness and one real integration pair. Stable project
identity and deterministic Project Context Capsules precede graph shadow
evaluation. Successor-beta and stable hardening, including real N-1 updates and
stable contracts, remains a separate post-V1 track.

## ADR-091: Raise the reviewed pip floor after PYSEC-2026-3721

**Status:** accepted 2026-08-21.

The frozen Python dependency audit for PR 66 failed closed after
PYSEC-2026-3721 / CVE-2026-13346 was published for the locked `pip 26.1.2`.
The advisory identifies `26.2` as the first fixed release. The development
extra therefore requires `pip>=26.2,<27`, and `uv.lock` selects fixed `26.2.1`
for the exact dev-and-packaging export audited by CI.

This is a reviewed build and audit dependency repair. `pip` is not added to
the ATC runtime dependencies, the beta.3 candidate identity is unchanged, and
no acceptance gate receives credit from this source-level correction. PR 66
passed both hosted CI matrices and CodeQL before its squash merge at
`088485d268d11e3167e7aad2e09a22c42659607f`.

## ADR-092: Add an explicit lean public-beta publication profile

**Status:** accepted 2026-08-21.

The complete 20-gate prepublication set remains the `certification_v1`
contract. Its gates, existing receipts, failed attempts, and canonical ledger
are not deleted, weakened, waived, renamed, or credited to another profile.
`BETA-R05` and `BETA-O01` remain postpublication evidence under every profile.

The first usable public beta may instead use the separately named
`lean_public_beta_v1` contract. Its exact prepublication set is `BETA-L01`
(Windows 11 exact-artifact first-run readiness), `BETA-L02` (Ubuntu 24.04
exact-artifact first-run readiness), `BETA-S06`, `BETA-R01`, `BETA-R02`, and
`BETA-R03`. L01/L02 bind the downloaded candidate and cover install/extract,
startup, loopback health, one supported client connection, restart persistence,
and cleanup. They do not imply that the broader 20-gate client, provider,
browser, 2 GB, privacy, recovery, or replacement matrix passed.

A bundle names exactly one publication profile. The protected publication gate
accepts exactly that profile's gate IDs, rejects extras, duplicates,
postpublication receipts, non-pass status, stale source/candidate bindings, and
open P0/P1 limitations. Lean approval additionally requires four literal-true
human acknowledgements: the certification matrix is incomplete, macOS is
unsupported, community packages are unsigned, and known issues were reviewed.
The maintainer must still enumerate every selected receipt once, name the human
approver, and retain `independent_human_review_claimed=false`.

`BETA-R02` remains mandatory. This policy does not authorize an AI system to
handle private signing material, claim key-backup recovery, approve the release,
or publish it. It changes only the machine-enforced evidence threshold for the
initial public beta; certification remains a visible post-beta hardening track.

## ADR-093: Rotate the unavailable prepublication beta signing key

**Status:** accepted 2026-08-21.

The encrypted private credential for `release-2026-a` could no longer be
authenticated by the custodian before any ATC release was published. This is a
key-availability failure, not a claim that the private key was disclosed or
compromised. The immutable key ID is not reused: both tracked public keyrings
retain `release-2026-a` with `status=revoked`.

The replacement Ed25519 beta key is `release-2026-b`, with reviewed public
fingerprint
`sha256:40f95302dd6c0241dc7f639e29693c15e94c5ccae1357b927d039a7e6bf1cf8f`.
Only the public half enters source control. The encrypted private half and its
passphrase remain under human custody outside the checkout, GitHub, Actions,
and model context. For this beta, the operator-controlled primary plus one
encrypted backup in a separate failure domain is the accepted custody floor.
The backup must pass a human-entered restore test before the candidate-bound
`BETA-R02` receipt can pass. This supersedes only ADR-092's two-backup custody
count; it does not weaken candidate binding, signing separation, or explicit
human approval.

The unpublished `0.1.0-beta.3` draft already occupies its immutable version
slot and embeds the predecessor-only trust root. It is retained as unsigned,
unpublished history and is not retargeted, deleted, reused, or published. The
replacement source version is `0.1.0-beta.4`; a new exact candidate and fresh
candidate-bound lean receipts are required. This decision adds no feature,
macOS asset, Mac support or evidence, signature, human release approval, or
publication authorization.

## ADR-094: Retire the contradictory beta.4 draft and correct custody wording

**Status:** accepted 2026-08-21.

The unpublished `0.1.0-beta.4` draft is release ID `374681462`, source
`1084abd54d39ecb5be6e33e5dce57cd3d56d3ccb`, release-candidate run
`32527232813`, and candidate digest
`79020be27e998725664abe0f20fd27cc1b4e1735f4ea9ff1ae0171252f2dd797` (31
assets). It is unsigned and unpublished. Although ADR-093 and the custody form
correctly set the floor at one encrypted backup separate from the primary,
two live runbook sentences still said two backups. The contradiction is a
documentation defect; it is not resolved by silently reinterpreting the
frozen candidate.

The beta.4 draft remains occupied historical evidence and is not retargeted,
deleted, reused, signed, or published. All live custody instructions now say
one restore-tested encrypted backup separate from the operator-controlled
primary. The replacement version is `0.1.0-beta.5`, which requires a fresh
exact candidate and fresh candidate-bound lean receipts. No feature, support
target, signing key, macOS claim, human approval, signature, or publication
authorization changes.

## ADR-095: Candidate identities stay external; retire contradictory beta.5

**Status:** accepted 2026-08-21.

The unpublished `0.1.0-beta.5` draft is release ID `374697784`, source
`28b46ea192af76233afe41f0d2b287edc2d59a04`, release-candidate run
`32530830948`, and candidate digest
`b0303b24164de987de9eab85caeeba4b460441fbe2c32463589269f4279462bd` (31
assets). Six candidate-bound lean receipts were materialized, but no bundle,
approval, signature, or publication exists.

That candidate source still said beta.5 had not been built, named beta.3 as the
live traceability identity, and aligned the active product plan to beta.3.
Those active-state contradictions are not reinterpreted after freeze. Beta.5
remains occupied, unsigned, unpublished historical evidence and is not
retargeted, deleted, reused, signed, or published.

The active source version advances to `0.1.0-beta.6`. Source documentation now
records immutable historical drafts and the release contract, while the exact
post-build candidate ID, run, digest, and receipts are recorded in an external
exact prepublication ledger. A commit cannot truthfully self-record a candidate
created only after that commit. This decision changes no feature, support
target, signing key, custody floor, macOS claim, human approval requirement,
signature, or publication authorization.

## ADR-096: Opaque GitHub CLI asset IDs are validation-only metadata

**Status:** accepted 2026-08-22 UTC after beta.6 publication.

The protected beta.6 publisher successfully created immutable prerelease
`v0.1.0-beta.6`, then its final local verifier rejected the already-published
state because `gh release view --json assets` returned opaque GraphQL node IDs
such as `RA_...` while the normalizer accepted only positive numeric REST asset
IDs. GitHub's release attestation, exact tag, immutable state, and all 34 asset
digests independently verified; the failure was a post-publication compatibility
defect, not an asset or release-integrity failure.

Release-state validation may accept a bounded printable ASCII GraphQL asset ID
from the GitHub CLI shape, but normalizes it to no numeric ID. Operations that
address REST mutation/download endpoints continue to require a positive numeric
REST asset ID, size, and SHA-256 from authenticated REST metadata. An opaque
node ID is never parsed, converted, or interpolated into a REST URL. This keeps
published-state checks compatible without weakening the numeric-ID authority
used before publication or channel promotion.

## ADR-097: Archive memories stay independent unless they share a subject

**Status:** accepted 2026-08-21. Post-beta source correction. Does not
retarget, relabel, or grant acceptance credit to `0.1.0-beta.6`.

B-102 originally collapsed every unkeyed archive-import preference, goal,
project, decision, workflow, and constraint into one current lineage per kind.
That prevented contradictory imported history from remaining concurrent truth,
but it also merged unrelated statements into fake version histories.

Core now derives a deterministic subject-only key from kind-specific framing,
temporal/quantity modifiers, a deliberately bounded preference-value
vocabulary (answer-style terms plus dark/light), and a literal choice-before-
`for` placeholder for clear preference forms. Archive observations share a
lineage only when that subject key matches; for a recognized preference value,
one remaining subject token is sufficient (for example, `dark mode` and
`light mode` both key to `mode`), while `Python for project Alpha` and `Rust
for project Alpha` retain `project Alpha` as the subject. Unknown wording still
requires at least two subject tokens. If no subject can be extracted, the
records remain independent current memories. Exact content still reinforces.
Direct client unkeyed records stay independent. Version history means a
revision of the same memory.

Provider extraction (`provider-archives-v2`) no longer auto-publishes broad
low-confidence first-person fragments. Specific durable kinds still apply.
Short, task-local, transient, and question text is skipped. Remaining
first-person prose may be retained as tentative `personal_context` only when
it is long enough to be self-contained. Sensitivity classification is
conservative: health, relationship, location, financial, and identifier
language upgrades `normal` to `sensitive` (local-only) or
`highly_sensitive` (ignored).

Complete sources can be rebuilt from the preserved raw blob. Rebuild
reversibly withdraws uncorrected automatic records from that source, starts a
new parser-versioned session, and publishes the new extraction. User
corrections, independently deleted records, observation history, and the raw
source remain. Failed-source retry is unchanged.

Context search now exposes the real `total`, cursor pagination, and kind,
sensitivity, confidence, and source filters. The dashboard shows that total,
loads further pages, and does not auto-select a record.

## ADR-098: Catalog search totals are separate from bounded evidence retrieval

**Status:** accepted 2026-08-22.

The Core catalog-search contract must report an exact post-policy total and make
every authorized current or historical match reachable through deterministic
offset/cursor pagination. A bounded relevance pool cannot serve as that total:
it makes a large but valid catalog appear truncated and strands records after
the pool boundary.

`LexicalV3.search_catalog` and `LexicalV3CandidateRanker.catalog_rank_with_explanations`
therefore enumerate the complete match set only after the existing authorization,
request-filter, temporal, and admissibility boundaries. Enumeration remains
bounded by the existing 50,000 eligible-candidate hard cap, and the lexical
ordering retains deterministic channel, score, and record-ID tie breaks.

`RetrievalEngine.search()` uses this catalog path for Core, CLI, and MCP search;
its `total` is the exact number of records that can be returned for the request.
`RetrievalEngine.bootstrap()` calls a separate 100-record evidence path before
`ContextCompiler` budget selection. Bootstrap remains deliberately bounded and
does not define the catalog API's total. Search audits still record only the
returned authorized page IDs and the existing safe aggregate metadata; no
unauthorized IDs or raw query/context text are added.

## ADR-099: Task-local provider instructions remain inert

**Status:** accepted 2026-08-22. Post-beta source correction. Does not
retarget, relabel, or grant acceptance credit to `0.1.0-beta.6`.

Provider archives are untrusted data, not an instruction channel. Extraction
must reject task-local and adversarial instruction framing before applying
preference classification. In particular, direct-address requests such as
`I want you to write a haiku` or `I want you to ignore previous instructions`
cannot become current interaction preferences merely because they use
first-person wording. Prompt-injection language wins over any preference
marker and remains inert.

Automatic provider preference eligibility requires durable evidence: explicit
always/never or general-preference phrasing, including ordinary statements
such as `I prefer concise answers`, `I always want concise answers`, and
`Please never use emoji in responses`. Task-local paraphrases are skipped;
provider-synthesized memory fields remain tentative by their existing policy.
No imported text is executed, and parser diagnostics remain content-free.

## ADR-100: Provider conversation-list residuals remain visible

**Status:** accepted 2026-08-22. Post-beta source correction. Does not
retarget, relabel, or grant acceptance credit to `0.1.0-beta.6`.

A recognized provider conversation list is a coverage boundary. Every entry
that is not a valid conversation mapping is counted as `unparsed`, including
non-mapping values and unknown mapping shapes. Valid sibling conversations are
still normalized and imported. Nested wrappers and root conversation arrays
use the same accounting, and an all-malformed provider list remains a
recognized but incomplete provider surface.

Residual warnings contain only the safe source name, count, and structural
classification; they never interpolate entry IDs, titles, or imported text.
Unparsed residuals keep `complete` false so provider coverage cannot report
success after silently dropping malformed material. Known message-level
attachment and role classifications remain governed by the existing
excluded/skipped/unavailable rules.

## ADR-101: Source rebuilds publish through a staged atomic cutover

**Status:** accepted 2026-08-22.

Complete-source rebuild is a replacement operation over the preserved raw blob,
not a destructive pre-step. A rebuild first marks the source as resumably in
progress, parses the Core-preserved bytes, and submits a parser-versioned
archive session whose candidates remain staged after coverage is recorded. Only
then does Core run one write transaction that withdraws eligible prior records
and evaluates the staged replacement. A parser error, batch/ingestion error,
cancellation, process interruption, or policy failure therefore leaves the
prior current context unchanged; SQLite rollback covers a failure after the
cutover transaction begins, and a committed staged session can be resumed
idempotently if source-finalization is interrupted. The publish transaction
also writes the generation and session ID into the source metadata before it
commits. A retry uses that durable marker to finalize metadata without
withdrawing the already-published replacement; a pre-commit failure has no
marker and retries the full cutover.

The replacement set is deliberately narrow: current approved records must have
Core origin `archive_import` and must not have a correction, user edit,
availability/privacy change, or deletion. Direct/local-admin records that happen
to retain the source ID are not automatic rebuild targets. Raw source bytes,
candidate/session history, record versions, independently deleted records, and
local-only policy outcomes remain preserved. This supersedes ADR-097's rebuild
wording only; it does not change archive extraction eligibility or conflict
resolution.

## ADR-102: Personally framed sensitivity is fail-local

**Status:** accepted 2026-08-22. Post-beta privacy correction. Does not retarget,
relabel, or grant acceptance credit to `0.1.0-beta.6`.

The deterministic `automatic-v1` sensitivity classifier now recognizes narrowly
personally framed health, relationship, location, and financial statements that
were previously easy to miss. This includes first-person residence and health
assertions such as HIV status, possessive relationship statements such as a
partner living somewhere, and possessive mortgage/loan statements. A detected
`sensitive` value remains monotonic: Core forces the resulting record to
`local_only`, regardless of a requested `core_available` or legacy
`always_available` value, and the Core forwarding boundary admits only
`core_available` records.

The rules are intentionally phrase-based rather than a general semantic
classifier. Unframed technical or general text such as a partner function,
mortgage rates, or a medical reference is not promoted by these additions.
This is not an exhaustive privacy detector: wording without a recognized
personal frame can remain `normal`, while an explicit first-person fictional
statement can still be conservatively localized. Secret-like content and
identifier rules remain higher precedence and highly sensitive content remains
excluded from automatic current context.

## ADR-103: Bind Core context-search cursors to the request and principal

**Status:** accepted 2026-08-22.

The Core HTTP search endpoint no longer emits or accepts bare decimal cursors.
It emits a compact versioned cursor whose payload contains a bounded offset and
the SHA-256 fingerprint of the normalized search criteria. The payload is
authenticated with an HMAC derived from the per-installation Core instance
secret; the authenticated principal ID is part of the signed message. Reusing
a cursor with a different query, filter, page size, or principal therefore
fails closed with HTTP 422 instead of silently returning a page from a
different request. Malformed, negative, and over-limit cursor payloads are
rejected by the `SearchCursor` model and the reconstructed `SearchRequest`.

Direct `offset` requests remain available for the existing MCP/CLI transport
contract, with the existing inclusive `0..100000` bound. A next cursor is not
issued when advancing would exceed that bound or when the current page is
empty. The cursor is opaque and integrity/authenticated, but it is not
encrypted, one-time-use, expiry-bound, or a snapshot-consistency token; record
changes between pages retain the existing retrieval semantics. This decision
changes only the Core search request/response contract and its focused API
coverage, not ranking, totals, or retrieval selection.

## ADR-104: Isolate synthetic retrieval usefulness evaluation

**Status:** accepted 2026-08-21.

Provider-facing retrieval usefulness is evaluated with a synthetic, sanitized
corpus and public Core observation/retrieval APIs. The harness may measure
current-fact recall, stale/conflict and withdrawn exclusion, sensitivity and
provenance packaging, character-budget compliance, and the JSON shape returned
to search, bootstrap, and get callers.

The eval has no production authority. It must not change ingestion, storage
schema, memory identity, sensitivity classification, dashboard Context,
retrieval ranking, MCP production behavior, release state, or live user data.
It must refuse the operator Core data directory and any existing `core.sqlite3`,
and it must not log raw personal context. Results are developer evidence only
and grant no beta-acceptance credit. Core-correctness work remains the
authority for observation/policy behavior; this harness should follow those
public APIs rather than inserting fixture rows into canonical tables.

## ADR-105: Bound ChatGPT attachment ingestion at the preserved-source edge

**Status:** accepted 2026-08-22. Post-beta source correction. Does not
retarget, relabel, or grant acceptance credit to `0.1.0-beta.6`.

ChatGPT export `.dat` members are attachment assets, not a generic searchable
document format. The importer preserves the raw ZIP as Core already requires,
then records one content-addressed inventory item per `.dat` member: archive
identity, uncompressed byte size, explicit `content_sha256`, bounded original
filename and MIME metadata, provenance sources, and attachment-ID
conversation/message links.
The inventory is source metadata and contains no attachment payload.

Text extraction is permitted only when both the export manifest and filename
metadata establish a supported text format: `.txt`, `.json`, `.jsonl`, `.csv`,
`.md`, or `.markdown`. It uses bounded in-memory reads and existing
deterministic provider extraction; no member is rendered, macro-enabled, or
executed. Images/audio, PDF, DOCX, PPTX, XLSX, RTF, HTML, scripts, unknown
extensions, and over-limit text remain raw and are counted as
unsupported/unavailable. Malformed JSON or text is one `unparsed` logical item;
it is never also counted as unavailable. Attachment inventory presence, hashing, or raw
preservation must never be reported as searchable coverage. Linkage is
retained only when a conversation attachment ID resolves to the `.dat` asset
identity; unresolved assets remain inventoried without an invented link.

The default ZIP safety limits are 10,000 entries, 512 MiB per member, 2 GiB
total declared uncompressed expansion, 500:1 compression ratio, 1 MiB stream
reads, 128 MiB JSON item parsing, and 8 MiB attachment-text reads. These limits
are independent of the 2,000,000,000-byte raw import boundary. Path traversal,
absolute, drive-relative, or drive-qualified paths, encrypted entries, and
malformed bounded reads
fail closed. Synthetic ZIP fixtures are the only attachment test inputs.

The post-review boundary is explicit: attachment inventory and `.dat` text
extraction run only for an explicit ChatGPT hint or an auto/generic archive
with a structurally confirmed ChatGPT mapping graph. Explicit Claude/Grok and
unconfirmed auto/generic imports retain those members raw without invoking
ChatGPT control-file parsing. Each inventory `asset_id` preserves the unique
safe archive member identity. Linkage is stored as exact conversation/message
pairs; a colliding stem produces no inferred link. MIME conflicts persist as
`mime_type_status=ambiguous` with no selected MIME value or synthetic provenance
source. Total link accumulation is bounded at 10,000 pairs, with per-document
link scanning bounded to 64 levels and 10,000 nodes; either truncation is
visible and leaves coverage incomplete.

## ADR-106: Keep item coverage separate from source terminal state

**Status:** accepted 2026-08-22. Import Truth correction. Does not retarget,
relabel, or grant acceptance credit to any published artifact.

The public import contract carries a closed item-level map in
`CoverageReport.closed_coverage`. Its keys are `recognized`, `excluded`,
`skipped`, `unavailable`, `duplicate`, `failed`, and `unparsed`. Duplicate ZIP
members and bounded per-member parser failures are counted there because they
refer to source items. A parser failure makes coverage incomplete even when
other members are retained and imported.

The schema is closed to those seven keys and accepts only strict non-negative
integer counts up to 2,147,483,647. `complete=true` is inconsistent with any
unavailable, duplicate, failed, or unparsed count; excluded/skipped material is
still resolved item accounting. An oversized ZIP text member is unavailable,
while a malformed manifest-declared text `.dat` attachment is unparsed only,
never both. Attacker-controlled member names are bounded and escaped before
they enter warnings, errors, or diagnostics.

Fatal source errors and operator cancellation are lifecycle events, not items.
They preserve any already-known item counts, set `coverage_complete` false,
and store `source_terminal_reason=failed|cancelled` in source metadata while
the durable import-operation row exposes its corresponding terminal status.
They must never be added to `closed_coverage` or summed with its item counts.
The preserved raw source remains the retry authority. This keeps pre-parse
failure, partial member failure, and cancellation dimensionally honest.

The contract remains content-free: warnings and durable error messages use
bounded status codes/classes, while the raw source stays local and untrusted.
The dashboard may render the two dimensions together but must label them
separately.

## ADR-107: Packaged acceptance must validate the same import coverage contract

**Status:** accepted 2026-08-22. Post-review import-truth correction. Does not
retarget, relabel, or grant acceptance credit to any published artifact.

Declared-text JSON `.dat` members are bounded source items. The importer first
validates the complete member, including trailing-data rejection, and only then
publishes its parsed items. A malformed member cannot publish early array items
and also be counted as `unparsed`; JSONL remains line-oriented with its existing
per-line behavior.

The packaged-provider acceptance boundary constructs the shared
`CoverageReport` model for its dict-level coverage map. It therefore rejects
unknown keys, booleans/floats/strings, negative or overflowing counts, and
`complete=true` when unavailable, duplicate, failed, or unparsed items exist.
Synthetic acceptance fixtures with unavailable content remain incomplete and
cannot produce a complete packaged report.

## ADR-108: Close terminal import partitions at both archive dimensions

**Status:** accepted 2026-08-22. Import Truth correction. Does not retarget,
relabel, or grant acceptance credit to any published artifact.

The importer keeps two deliberately separate contracts. `closed_coverage` is
the logical item map; `stats.archive_member_coverage` is the content-free raw
ZIP-member audit. Provider containers and controls remain structural in the
raw audit even when bounded parsing finds malformed content. Their applicable
logical `unparsed` or `failed` result is assigned exactly once, and the raw
member is never also placed in a terminal ordinary-member bucket.

Provider-memory/profile values rejected before candidate construction by
secret-like, inert, highly-sensitive, or size policy close as logical
`skipped` items. Accepted and rejected values therefore cannot produce a
nonempty provider-memory surface with an all-zero logical denominator, and
rejected text is not copied into warnings or receipts. Sensitivity that is
allowed by the configured local-only policy remains a candidate with its
declared sensitivity; parser coverage is not confused with later policy
dispositions.

Standalone text, JSON, and CSV decoding is strict UTF-8. Invalid bytes are one
atomic `unparsed` item, never replacement-decoded. CSV is a supported generic
logical item through both public archive entrypoints, with malformed CSV
closing atomically as `unparsed`. Ordinary JSON roots use a bounded two-pass
validate-then-consume strategy, preserving trailing-data atomicity without an
unbounded document list or a retained raw temporary artifact.

When ZIP metadata can be enumerated, entry-count, declared-size, compression,
encryption, and path/depth safety failures return a content-free member audit.
Every rejected file member is placed in exactly one `unavailable` bucket, the
raw denominator closes, and no rejected payload is opened. If enumeration
fails, the result uses `archive_level_failure=zip_enumeration_failed` and
`member_coverage_available=false`; it deliberately has no invented member
closure. These are parser results and coverage contracts, not fresh acceptance
evidence.

## ADR-109: Share bounded ordinary-JSON parsing across every archive entrypoint

**Status:** accepted 2026-08-22. Import Truth acceptance-blocker correction.
Does not retarget, relabel, or grant acceptance credit to any published artifact.

Direct byte imports, filesystem JSON paths, and ordinary JSON ZIP members use the
same incremental strict-UTF-8 reader and `JSONDecoder.raw_decode` contract. The
reader enforces a 512 MiB raw-byte ceiling, a 128 MiB decoded item/document
ceiling, and a 128-level quote/escape-aware nesting ceiling before recursive
decode can raise `RecursionError`. It validates the complete source before the
builder or generic candidate list is mutated; trailing data, malformed JSON,
depth rejection, and parser failure therefore cannot leave partial candidates.
The reader yields root-array members without materializing the array. An empty
ordinary root array is represented as one logical value so direct, path, and ZIP
coverage agree; provider containers remain structural and are counted only by
their semantic contents.

Raw ZIP classification has an explicit bounded allowlist. Canonical
`conversations.json` and dated `conversations-YYYY[-MM[-DD]].json` names are
provider containers. The alternate `chats.json`, `history.json`, and
`messages.json` basenames require an explicit provider hint or an exact provider
path component from `chatgpt`, `openai`, `claude`, `anthropic`, `grok`, `xai`, or
`x.ai`. A neutral alternate with valid provider-shaped content can still be
promoted to structural by provider parser statistics; malformed neutral
alternates remain ordinary unparsed members because filename-only inference
would overclassify arbitrary generic JSON. This residual is visible in the raw
member audit and closed logical coverage.

`CoverageReport.closed_coverage` remains closed to the seven recognized keys and
strict bounded non-negative integers. Omitted or partial maps are normalized to
the exact zero-filled seven-key map for backward-compatible callers; unknown
keys and invalid counts remain validation errors. The API and serialized model
therefore never expose a missing or extra coverage key.

## ADR-110: Establish provider context before terminal accounting

**Status:** implemented as a bounded synthetic correction on 2026-08-22. Does
not retarget, relabel, or grant acceptance credit to any published artifact.

The importer observes provider evidence from every value yielded by the bounded
JSON validator in a disposable validation builder, buffering only a bounded set
of structural provider signatures. Those signatures are published to the live
builder only after the entire iterator succeeds; the consuming pass then mutates
candidates or logical counts. This two-phase boundary is intentionally
context-only: it does not retain the root array or publish any imported text. A
valid provider-looking prefix followed by trailing data or any later bounded
parse failure therefore cannot promote a neutral alternate or enable ChatGPT
attachment links. Such a ZIP member closes exactly once as `unparsed`; direct
and path entrypoints report generic incomplete coverage. A malformed neutral
sibling cannot poison a separately valid named provider member.

The bounded iterator carries an explicit context tag with each yielded value:
standalone roots use root policy, while members streamed from a non-empty root
array use root-array-item policy. An empty object or empty provider wrapper in
that item context is therefore one `unparsed` terminal even when the valid
conversation is its sibling. The distinction is part of the parser boundary;
it is not inferred from a filename and does not require materializing the root.

Provider containers and conversations have a nonzero logical denominator even
when they contain no messages. A known empty provider root or zero-message
conversation closes as one `skipped` item; an identity-free provider-shaped
empty root and malformed provider entries close as one `unparsed` item. The
container remains structural in the ZIP-member audit, so it is never counted as
both a raw member and a logical item.

Auto ZIP attachment discovery uses a bounded content signature over the allowed
conversation basenames, including neutral `messages.json`, `chats.json`, and
`history.json`. The signature scan consumes the entire bounded iterator before
publishing a member observation. Only valid ChatGPT-shaped content promotes
such a member before attachment link scanning. A malformed or over-limit
neutral alternate remains generic and does not activate ChatGPT attachment
inventory; filename alone is insufficient.
## ADR-111: Core owns the Memory Truth projection and deletion barriers

**Status:** accepted 2026-08-22.

Memory Truth is an additive Core projection over existing observations,
current-record versions, evidence links, source metadata, integrity groups, and
tombstones. It gives authorized clients a canonical record status (`current`,
`tentative`, `superseded`, `conflicted`, or `deleted`), bounded evidence and
history, source identity, decision metadata, confidence, sensitivity, and
separate effective/observed/recorded times. Content-free coverage is exposed
separately so a client can report source and decision accounting without
revealing memory text. The public record endpoint remains authorization-first;
admin list/detail endpoints are the inspection surface for deleted records and
detached tentative observations. No new model-facing MCP tool is added.

Reprocessing identity is source-scoped and value-aware. A stable key includes
the source ID and reference, kind/slot keys, and a canonical value fingerprint;
the source reference alone is never an identity. Complete-source rebuild
withdrawal marks its tombstone with the exact internal source and origin. Core
may reapply only an untouched automatic archive record whose tombstone proves
that same source-rebuild removal. Ordinary user or source deletion is not
eligible: matching archive evidence receives an explicit ignored disposition
linked to the deleted record, and cannot create a replacement current record.
An authorized restore is required before that lineage can become current again.

This slice does not add a replayable append-only decision event stream,
tentative expiration/decay, provider extraction changes, retrieval/ranking
changes, dashboard wiring, or source-content history/purge presentation.

## ADR-112: Memory Truth review corrections fail closed at storage boundaries

**Status:** accepted 2026-08-22.

Memory Truth identity is derived from the current durable source address, kind,
slot keys, and value. Every path that changes those fields recomputes the
identity key before recording the next version, so an ordinary deletion remains
a barrier after an observation update or restore. Manual approval links its
originating observation through the same unique durable link used by automatic
application; retries update no duplicate row.

Migration 010/011 statement inspection strips leading SQL comments before
idempotent `ALTER TABLE` recovery. Rebuild deletion provenance is constrained
to a Core-internal validated cutover helper, with SQLite provenance checks and
record/source/version/hash invariants before reuse. Portable restore treats all
rebuild markers as untrusted input and imports them as ordinary deletion
barriers; a valid rebuild marker is never sufficient authority merely because
it appears in an authenticated export.

Truth list pagination and status coverage use bounded SQL selection/counting.
Projection arrays use the public limits of 64 superseders, 64 conflict groups,
and 512 evidence links. This correction is additive and preserves existing
purge, relay, authorization, and source-restore contracts.

## ADR-113: Bind reopenable rebuild tombstones to one validated ceremony

**Status:** accepted 2026-08-22.

Only the atomic `publish_source_rebuild` transaction may mint a trusted
source-rebuild tombstone. Its private withdrawal capability requires a binding
containing the exact finished archive session, rebuild generation, and
content-hash-derived source marker. The storage checks also require archive
mode, finished status, client-free and source-accessible session state,
source-rebuild-in-progress metadata, stable identity, tombstone hash/version,
and no user edit. The public compatibility withdrawal method fails closed;
legacy or portable rows without the binding are ordinary deletion barriers.
Reapply verifies the same source/session/generation and marker relationships in
the transaction, preserving stable IDs only for a valid untouched lineage.

Manual approval derives the candidate and record keys from the final persisted
identity-bearing values, including content, source reference, kind, slots, and
structured value. Truth list projection counts and pages in SQL, avoids
read-time integrity rebuilding, and uses page-scoped SQL set prefetch with
per-record limits for superseders, conflict groups, and evidence. Focused
regressions cover tampered provenance, delete/reimport replacement prevention,
idempotent approval evidence, and near-constant query count as the database
grows. This is additive to the existing purge, restore, relay, export, and
provider contracts.

## ADR-114: Explicit local mutation ledger guards restore/rebuild boundaries

**Status:** implemented in the isolated 2026-08-22 review candidate; final
acceptance remains a fresh-review decision.

Source-rebuild eligibility must not infer human edits from free-form version
reasons. Migration 013 adds the append-only `context_user_mutations` ledger.
Public/local record restore, source restore, correction, availability change,
and explicit deletion paths write typed rows with `mutation_origin='local_user'`.
The original record/source observation provenance is not rewritten. Automatic
duplicate-import recovery, archive-import forget evaluation, and source-rebuild
reapply remain distinct because they do not write a local mutation row.

The ledger deliberately has no record foreign key: purge removes record content
but retains the opaque stable-ID mutation barrier. Export/restore uses
duplicate-safe inserts and never deletes destination rows. Imported
source-rebuild tombstones remain ordinary barriers. A pre-013 database or
export is upgraded by a deterministic, unique-per-record `legacy_user_edit`
row derived from durable correction or deleted-snapshot evidence; this one
compatibility fact is distinct from typed explicit actions and repeated legacy
restores are idempotent. New rows bind a canonical actor, version evidence
coordinates/digest, and deterministic intent key. Portable restore stages and
validates ledger rows only after same-package records, versions, tombstones,
and source relationships exist; forged or incomplete rows are ignored.
Recovery carries verified destination-local barriers and purge tombstones into
an isolated restore transaction, including no-record/purged targets. All
version/source/tombstone reasons are canonical codes, and restore of an already
current record is one version-backed, exact-retry-idempotent barrier. SQLite
checks and append-only triggers fail closed on invalid, copied, or tampered
ledger mutations.

## ADR-115: Require typed canonical action evidence for portable mutation authority

**Status:** implemented in the isolated 2026-08-22 review candidate; final
acceptance remains a fresh-review decision.

Migration 014 adds nullable `user_action_kind` and deterministic
`user_action_key` fields to `context_record_versions`. Core emits those fields
only in the local correction, availability-change, restore, delete, and
source-delete paths. New `context_user_mutations` rows use
`evidence_kind='user_action'`; portable restore recomputes the typed digest and
requires the history action kind/key, canonical reason, exact vault/record/
source relationship, and ledger intent to agree. A generic `record_version`
coordinate can remain a compatibility-scoped `legacy_user_edit` fact but cannot
authorize a typed action.

Migration repair drops and recreates both append-only ledger triggers on every
restart-safe repair pass, including when migration 013 is already marked
applied. The same pass probes the schema-014 history table, adds any missing
typed-action columns, and recreates the typed-action unique index idempotently
when migration 014 is already marked applied. Duplicate-safe restore and
isolated carry-forward counters increment only when SQLite actually inserts a
row. The encrypted export passphrase is
not an external author signature: a holder who rewrites and re-encrypts every
package member can rewrite both canonical history and ledger data. This
decision closes row-only forgery without claiming provenance that the package
trust model cannot establish.
## ADR-116: Retrieval usefulness reranks only after hard boundaries

**Status:** accepted 2026-08-22. This focused post-beta retrieval slice does
not change Core authority, import policy, storage schema, lifecycle semantics,
release state, or acceptance credit.

Production Retrieval V3 keeps authorization, temporal resolution, and numeric
admissibility ahead of relevance. After those boundaries, the default lexical
candidate order receives a deterministic local-usefulness rerank using bounded
query-intent features and record metadata: salient-token/field coverage,
recency, confidence, availability, sensitivity, conflict state, provenance,
and actionability. The feature projection never accepts raw unauthorized rows,
learned model output, network data, or imported instructions, and the frozen V2
comparator remains unchanged.

Bootstrap remains a separate 100-record evidence pool followed by metadata-only
set selection. The compiler caps a pack at 32 records, preserves mandatory
preferences, deduplicates and excludes same-slot conflicts, and enforces the
exact character budget. Its additive `pack_metadata` envelope reports bounded
counts, provenance-backed selected items, and explicit candidate-pool/budget/
record-limit truncation reasons so providers can distinguish omission from an
empty result. The synthetic usefulness fixture adds sparse location/latest
intent cases and budget-metadata assertions; its 17-case scorecard is
developer evidence only.

## ADR-117: Dashboard truth surfaces preserve backend accounting boundaries

**Status:** API/DOM behavior accepted locally on 2026-08-22 after independent
review of the post-review wire-safety/count/coverage hardening. Fresh visual
Product Design acceptance remains pending; this is not release acceptance.

Sources UI treats item closure and source processing as different dimensions.
The wire normalizer accepts the exact seven-key `closed_coverage` contract,
retains unknown or missing older metadata without inventing counts, and exposes
terminal `failed`/`cancelled` reasons separately from incomplete item coverage.
Retry is available for failed, cancelled, or incomplete extraction; rebuild is
available only for a complete source. Existing remove/undo behavior is retained.

Context keeps `/context/search` as a bounded, explicitly current-only list and
adds only the existing Core reads for `/context/coverage` and
`/context/truth/{record_id}`. Coverage is content-free and failure-visible, so
search results remain usable when accounting is unavailable. A selected row gets
one truth read for status, conflict, provenance, evidence, and history metadata;
sequence guards prevent stale responses from replacing a newer selection, and
mutations refresh coverage and the selected truth at bounded times. The
dashboard now builds record/truth values field-by-field, bounds all displayed
strings, arrays, enums, timestamps, hashes, confidence values, versions, and
counts, drops malformed list rows, and fails malformed detail envelopes with a
content-free error. Import IDs ignore non-string values, stats reject invalid
count shapes, and a failed coverage refresh clears cached truth metrics while
retaining the independent search window. The slice is local-only review
evidence and does not change routes, Core authority, release state, or the
beta.6 public identity.

## ADR-118: Continuous Capture begins as a provider-neutral local ledger

**Status:** accepted locally on 2026-08-22 after fresh independent
security/correctness/API review; this is not release or provider acceptance.

Continuous Capture starts with migration 015 contracts only. Core stores
content-free source metadata, bounded opaque checkpoints, normalized inert
events, source-scoped provider-item lineage, and foreground run telemetry.
Creation is disabled; enabling/resuming requires explicit local-only
acknowledgement; revocation is terminal and clears the reserved credential
reference. Disabled, paused, and revoked sources make zero adapter calls.

The coordinator durably stages an event, calls an injected idempotent sink with
a deterministic key, and atomically commits the application receipt, item
mapping, and checkpoint. Page cursors advance only after all page events apply.
Duplicate replay is a no-op, while gaps, malformed pages, invalid cursors,
bounded-limit failures, sink failures, and expired leases degrade the source
with canonical retry metadata. Provider deletes are constrained to one source
and item lineage; local corrections remain an explicit sink contract. Full
snapshot/rescan deletion is deferred rather than inferred from page absence.

No real connector, provider/network implementation, OAuth or credential flow,
background scheduler, dashboard/package-startup change, current product
availability, beta.6 identity change, release/publication/acceptance claim,
live/private Core/data work, or macOS work is included. The unsupported-macOS
posture and Core authority boundary remain unchanged.

## ADR-119: Continuous Capture repair and lease capability authority

**Status:** accepted locally on 2026-08-22 after fresh independent
security/correctness review; this is not release or provider acceptance.

Migration-015 startup repair reads the packaged authoritative migration and
executes its statements one at a time inside the existing transaction. This
keeps marker-present repair invariant-equivalent to fresh migration without a
second weakened schema definition or `executescript` auto-commit behavior.

After `begin_run`, every run-owned mutation carries a typed handle binding the
run ID, source ID, and lease token. The ledger checks that exact capability,
`running` state, strictly future expiry, and `reconciling` source state in the
mutation transaction. Renewal has the same guard. Leaving `reconciling`
atomically abandons active runs, and expiry recovery only degrades sources
that still reconcile; a sink result crossing expiry is not committed and is
safe to replay under the same idempotency key. These changes preserve the
provider-neutral, foreground-only, beta.6 and unsupported-macOS boundaries.

## ADR-120: Canonical boundaries normalize hostile representations before authority

**Status:** accepted locally on 2026-08-22 after focused adversarial
reproduction and regression checks; this is not release acceptance.

Equivalent hostile representations must not bypass a boundary or change
identity after validation. Direct-secret detector v3 therefore scans a
compatibility-normalized, zero-width/combining-free projection while retaining
the existing high-confidence credential patterns and content-free refusal
receipt. Capture applies the same representation rule to its content-free
metadata contract and rejects implicit identifier or integer coercion.

Canonical Memory Truth supersession is a database relationship, not caller
metadata. Every direct canonical write validates that the target exists in the
same vault and that following the bounded predecessor chain cannot reach the
record being written. This prevents dangling/self/cyclic state from making the
rebuildable temporal resolver fail. ZIP display names remain safely escaped and
bounded; names that cannot preserve unique identity inside that bound close as
unavailable instead of being truncated into a false duplicate.

An injected Continuous Capture sink may acknowledge only the exact
source-scoped canonical lineage supplied by Core. A different lineage is an
invalid receipt even on the first event and cannot create an item mapping.
Experimental retrieval diagnostics accept only finite bounded primitives, and
malformed/nonfinite upstream lexical scores become a neutral score before local
usefulness reranking. These rules do not add a provider connector, change Core
authority, inspect private data, publish a release, or add macOS support.

## ADR-121: Cross-platform identities and local authority fail closed

**Status:** accepted locally on 2026-08-22 through focused synthetic
regressions; this is not release or provider acceptance.

ZIP member identity is normalized with Unicode NFKC plus case folding before
duplicate selection. Members are still read in archive order and never
extracted, so the first logical path is deterministic and a compatibility-
equivalent later path closes as a duplicate. Raw-source preservation and
bounded diagnostic names are unchanged.

Continuous Capture counters and metadata integers are limited to SQLite's
signed-64-bit range before durable writes. If an unexpected local runtime or
storage exception still occurs after a run begins, the coordinator records a
content-free `capture_failed` terminal result through the ordinary retry path;
lease loss remains governed by the existing stale-result behavior. Context
pagination similarly uses strict bounded integer fields so booleans cannot
become offsets or page sizes through model coercion.

Core's existing single-authoritative-vault selection now participates in direct
record/truth lookup and registered-client lookup. Record IDs and client tokens
are not treated as sufficient authority across vault rows. Client counts,
listing, revocation, ordinary authentication, and the import-operation observer
all use the same authoritative-vault boundary. This decision changes no schema,
network behavior, provider support, release state, or macOS posture.

## ADR-122: Pull requests, default-branch pushes, and version tags are distinct CI evidence

**Status:** accepted locally on 2026-08-22 after draft PR 73 exposed duplicate
feature-branch matrices; hosted revalidation of the follow-up commit is pending.

The canonical CI workflow validates every pull request, every push to `main`,
and every push for a version tag matching `v*`, but it does not also run the
full matrix for an ordinary feature-branch push that already has a pull-request
event. This keeps pre-merge review evidence, post-merge protected-default-branch
evidence, and version-tag validation while avoiding two equivalent Windows,
Ubuntu, dashboard, security, parity, and desktop matrices for one feature
commit.

Release candidate construction, beta publication, and channel promotion remain
separate explicit `workflow_dispatch` ceremonies. This trigger routing removes
no CI job, platform, or release approval, and it does not add or imply macOS
support.

## ADR-123: New vault identity starts at the applied migration generation

**Status:** accepted locally on 2026-08-22 after the PR 73 full suite exposed a
recovery fingerprint mismatch; hosted revalidation is pending.

`CoreStore.initialize_vault()` applies migrations before creating the first
vault row. The new row must therefore be inserted with the latest applied
migration generation rather than the table's historical version-1 default.
Otherwise a later integrity/export pass changes schema identity simply by
reopening an otherwise unchanged vault, which invalidates recovery's logical
old-or-new fingerprint invariant.

The accepted deterministic retrieval integration also changes the frozen B01
`atc-retrieval-v3` confirmatory result from 1/7 to 3/7. That expected result is
updated without changing the frozen fixture/config identities, boundary flags,
operation accounting, or final `KILL_MECHANISM` decision. Neither reconciliation
changes provider availability, release state, or the unsupported-macOS posture.

## ADR-124: ACLs follow replacement content and evidence follows observation ACLs

**Status:** accepted locally on 2026-08-22 after focused synthetic privacy
regressions; this is not release acceptance.

Persisted empty `allowed_clients` means unrestricted, but an observation with
that omitted/empty allowlist must not loosen a currently restrictive canonical
record. When both the current record and observation are restrictive, an
overlap is intersected. If the restrictions are disjoint, a content-replacing
observation adopts its own allowlist because the canonical payload has changed;
retaining the old set would authorize the old client to read new private
content. A reinforcement does not replace content, so a disjoint observation
retains the current allowlist and cannot transfer existing content to the new
client. Deny sets continue to union, preserving the fail-closed result when a
client is both allowed and denied.

`CoreStore.get_memory_truth(principal=...)` is authorization-first for the
canonical record and now filters linked observation evidence with each
observation's own allow/deny ACL before applying the bounded evidence limit.
When a correction transfers a restrictive ACL to a disjoint client set, the
canonical record's content-bearing projection fields (structured value,
scopes, tags, source provenance, evidence, confidence, and validity window)
follow the replacement observation rather than the old target. Stable kind and
slot identity remain attached to the canonical record. The principal-less
Core/local-admin path intentionally retains complete linked observation
history; no admin-scope bypass is added to the principal-scoped path. This is a
storage/query repair with no schema or provider-contract change, and it uses
only synthetic temporary-database tests.

## ADR-125: Complete-but-incomplete sources use the existing rebuild authority

**Status:** accepted locally on 2026-08-22 after focused synthetic backend and
dashboard regression tests; hosted revalidation is pending.

`import_status=complete` and `coverage_complete=false` is a repairable source
state, not a successful extraction. A non-rebuild source reprocess request from
that exact state therefore enters the existing source-rebuild ceremony. The
preserved source blob is reparsed, candidates remain staged, and
`publish_source_rebuild` is allowed to cut over only when the new coverage is
complete. A parser failure leaves prior current records in place and marks the
rebuild resumable/failed through the existing terminal path.

This decision deliberately does not create a new endpoint, parser session, or
deletion primitive. Explicit healthy complete-source reprocess remains a
duplicate/no-op, and the source-rebuild authority continues to protect user
mutations, local-only records, stable identities, and ordinary deletion
barriers. Concurrent repair callers may share one idempotent rebuild generation
and session; only the validated atomic cutover withdraws eligible automatic
records.

## ADR-126: Adapter availability cannot revoke another coordinator's live run

**Status:** accepted locally on 2026-08-22 after focused shared-SQLite
correctness reproduction; this is not release or provider acceptance.

`CaptureCoordinator.run()` may be invoked by a coordinator that has no adapter
for the source provider while another coordinator or process owns a live leased
run. The adapter-missing path therefore checks the source lifecycle and
future-expiring running lease in the same serialized transaction before
degrading. A live owner leaves `reconciling`, retry metadata, and operator
pause/revoke state authoritative, while the caller still receives the bounded
`capture_adapter_unavailable` result. If no live run exists, the existing
degradation and retry behavior remains unchanged. Run-owned renewal and finish
continue to require the exact handle, lease, and `reconciling` state.

## ADR-127: Generic bounded failures map to declared closed-coverage counters

**Status:** accepted locally on 2026-08-22 after focused PR 73 review
reproduction and importer regressions; this is not release or provider
acceptance.

Generic standalone parsing has three bounded terminal reasons: `unavailable`,
`failed`, and `unparsed`. The generic coverage accumulator declares a counter
for each reason, `_generic_failure_result()` assigns them through an explicit
bounded match, and `_combine()` merges every counter into the exact seven-key
`closed_coverage` map and generic stats. This prevents a slotted-dataclass
`AttributeError` and prevents a successful return with an unreported terminal
item.

An oversized standalone CSV is one `unavailable` item; malformed CSV remains
one `unparsed` item. The focused synthetic tests assert no exception, one
closed item, exact bucket identity, and incomplete coverage. No provider,
network, private data, release state, or unsupported-macOS posture changes.

## ADR-128: Forwarded context-pack accounting is boundary-reconciled

**Status:** accepted locally on 2026-08-22 after synthetic Edge filter and
envelope-trim reproduction; this is not release or provider acceptance.

`pack_metadata` is provider-facing accounting for the pack at the boundary
where it is emitted. Core reports its bounded selection; Edge recomputes the
returned-item count, exact omission complement, character usage, and
provenance-backed returned-item count after any ACL or envelope projection.
The model rejects `selected_count > candidate_count`, an incorrect omission
complement, provenance exceeding selected items, or suppression counts
exceeding omitted items.

Duplicate/conflict suppression counts remain explicitly Core-selection-scoped,
because Edge receives no Core suppression-group identities from which to
recompute those reason-specific counts. Edge therefore preserves only bounded
claims about candidates still omitted and never presents ACL or envelope
removals as duplicate/conflict suppression. No Core authority, transport,
provider support, release state, or macOS posture changes.

## ADR-129: High-cardinality interaction preferences use a compiler-local reserve

**Status:** accepted locally on 2026-08-22 after focused sanitized retrieval and
set-selection regressions; this is not release or provider acceptance.

`ContextCompiler` preserves the existing mandatory-preference behavior through
eight eligible interaction preferences. Above that threshold, it uses the
existing deterministic selector and the same opaque semantic, diversity,
redundancy, and conflict metadata to choose a compiler-local reserve of at most
eight. The reserve is bounded against the exact character budget so a cheapest
compatible primary relevant record remains feasible whenever a preference-plus-
primary combination fits. Only the reserve is marked mandatory; overflow
preferences are optional fallback candidates gated behind a compatible primary,
so they cannot starve query-relevant records or supporting evidence. No-match
queries still return the reserve, and impossible tight-budget combinations fail
closed deterministically.

The follow-up correction keeps caller retrieval order for non-preference primary,
supporting, and fixed-mandatory candidates, while canonicalizing only preference
reserve and overflow tiers for preference-input permutation stability. Reserve
eligibility excludes every duplicate/conflict group occupied by a fixed mandatory
record. Each overflow preference supports the intersection of all compatible
primary IDs, rather than one preselected anchor, so a different selected primary
can unlock it while no-match overflow remains dormant.

Because selector support is an OR relationship, the compiler does not pretend
that one support set can require both a primary and evidence. When applicable
evidence is feasible within the fixed-plus-reserve-plus-primary budget, overflow
supports only that evidence's IDs; each evidence candidate independently supports
its compatible primary, forming the required chain. If no applicable evidence can
fit, overflow retains the compatible-primary fallback so the optional tier does
not deadlock under absent or infeasible evidence.

This is deliberately not a selector, storage, retrieval-pool, ACL, temporal,
sensitivity, API, or metadata-schema change. Core still reports exact used
characters, selected/omitted counts, duplicate/conflict aggregates, and
provenance using the existing pack contract. The regression is sanitized and
synthetic only: 77 preferences, 20 relevant records, ten generic queries, a
4,000-character budget, a no-match query, and negative ACL/temporal/sensitivity
records. It does not touch private/live data, GitHub, release state, Figma, or
macOS acceptance.

## ADR-130: Packet D remains a disposable zero-dashboard composition

**Status:** accepted locally on 2026-08-22 as Wave 2 synthetic evidence; this
is not provider, client, release, or production acceptance.

Packet D composes the existing capture ledger/coordinator and idempotent sink
with the Wave 1 lifecycle, reconciliation, formation, and dependency contracts.
Formation writes only through `CoreStore.add_candidate`, where the existing
Core policy decides whether evidence becomes current context; the harness never
creates a parallel observation, current-record, cursor, checkpoint, or
retrieval store. A delete after restart resolves its observation/record
correlation through durable public Core candidate APIs; no in-memory item map is
carried across restart.

The fixture is sanitized, deterministic, local-only, and inert. A deliberate
retry after the first capture page closes the first CoreStore, then a fresh
coordinator/sink/cursor-semantic adapter resumes from persisted `cursor-1` and
applies the correct page. A later fresh completed replay is compared directly
before/after for Core counts and creates no new capture events, observations, or
current records. Retrieval V3 is called with an authorized Core principal; the
L2 host declares every hook, exercises pre-generation, direct-user, and restart
hooks, and leaves its unsupported consequence hook explicitly unsupported.
Correction, permission narrowing, expiry, ordinary delete, and terminal purge
are checked by their existing Core/Retrieval boundaries. The bounded projection
closure result is separate content-free component evidence, not M3/Core/Retrieval
production integration; scripted host trace checks are not operator telemetry.

The receipt is developer evidence only. It does not imply a real connector,
provider integration, network/OAuth behavior, stable SDK/export contract,
dashboard independence in production, or acceptance against private/live data.
