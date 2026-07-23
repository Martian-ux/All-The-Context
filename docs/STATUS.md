# Project status

## Current milestone

The target remains an unsigned `0.1.0-beta.1` community release. No beta release
has been published. The encrypted `release-2026-a` private key exists outside
the checkout and cloud-synchronized workspace; only its reviewed public half is
tracked.

V1 was simplified on 2026-07-22: Core is the only user-facing service. Hosted
Edge, third-party runtime deployment, offline mobile replicas, and provider
hosting setup are no longer part of the V1 product or acceptance gate. Mobile
means connecting directly to Core while Core is online.

On 2026-07-23 ADR-039 superseded the review-first memory design. The confirmed
product contract is now one-time setup plus automatic, reversible,
provenance-backed context maintenance, with no routine review queue. The
automatic-policy migration is present in the current shared worktree and has
passed the local Ruff, mypy, full pytest, dashboard, demo, documentation, and
dependency-audit gates described below. The exact worktree has not yet passed
the hosted Python 3.12 cross-platform/package matrix. Earlier approval-based
evidence remains historical and must not be presented as proof of ADR-039.

## AI-memory research direction

ADR-042 establishes a post-beta, benchmark-driven research direction without
changing the V1 release boundary. The goal is end-to-end memory reliability,
not novelty or retrieval scores in isolation.

The research charter is
[`docs/research/ATC_MEMORY_RELIABILITY_ARCHITECTURE.md`](research/ATC_MEMORY_RELIABILITY_ARCHITECTURE.md).
It defines:

- a governed Memory Plane for evidence, current knowledge, experience,
  procedures, working state, consolidation, and recall;
- an optional Intent and Consequence Plane for adequately witnessed
  event-bound preferences and directives;
- an ATC Memory Lab that compares long context, simple profiles, ATC, and
  external systems under the same models, data, budgets, and scoring; and
- reversible experiential-learning and consequence-closure hypotheses that
  remain unimplemented research.

The existing
[`Consequence-Closed Context`](research/CONSEQUENCE_CLOSED_CONTEXT.md) proposal
is now explicitly scoped as the differentiated second plane, not a complete
AI-memory product. No external memory engine, graph/vector dependency, new
production schema, host checkpoint protocol, private learned model, or
behavioral enforcement claim has been accepted or implemented by this
documentation work. The beta remains the immediate product milestone.

The July 2026 fresh-horizon review amended the execution order: simple
long-context, append-log, stable-observation, and file-search baselines precede
framework adapters; typed relations must earn a graph; applicability follows
authorization before relevance; derived-state closure is foundational; and
procedural learning waits for trustworthy outcome and repair evidence. The
updated charter uses CAOS—correct current authorized outcome within budget—as
the primary endpoint rather than retrieval quality alone.

## Research supplier intake

The 2026-07-23 AI-memory competitor intake records official repositories,
immutable revisions, licenses, dependency burden, integration surfaces, and
safety risks for eleven candidates in
`research/competitor-intake/memory-systems-intake.v1.json`. No third-party
source was cloned, installed, executed, imported, or copied. The intake adds no
runtime dependency or production claim.

## Executable Memory Lab M0 slice

ADR-043 implements the first bounded Memory Lab comparison without changing the
V1 runtime authority boundary. `allthecontext.memory_lab` defines versioned,
provider-neutral memory-object and read-only retrieval-adapter contracts.
`bench.memory_lab` runs no-memory and deterministic token-overlap controls plus
current ATC Retrieval V3 against the same sanitized, frozen task fixture and
reports task-level sufficiency, abstention, forbidden output, disclosure,
determinism, latency, storage, and adapter-declared model/token/cost usage.

The adapter input is an already-authorized immutable snapshot. Results contain
aggregate counts, ordinal-derived ranking fingerprints, and accounting—not
memory content, object IDs, task names, queries, or policy decisions. The ATC
comparator uses only an isolated synthetic database; no operator Core,
external memory engine, network service, provider code, or new production
schema is involved.

The fixture is intentionally diagnostic rather than a promotion gate: the
no-memory control succeeds only on the abstention task; the simple baseline and
ATC each succeed on four of five tasks. The simple baseline retrieves one
forbidden cross-project distractor; ATC retrieves none, but ATC reaches only
`0.90` mean evidence-group recall because it misses one required item in the
multi-memory task.

## Falsifiable memory evaluation program

The Memory Lab now also has a longitudinal evaluation specification in
`docs/research/ATC_MEMORY_EVALUATION_PROGRAM.md`, a machine-readable experiment
and promotion contract, and 18 deterministic symbolic scenarios validated by
11 structural tests. They cover 13 capabilities from working continuity
through correction, privacy, recall-to-action, and closure. These artifacts
freeze future evidence requirements only; no longitudinal adapter, competitor
result, or production memory mechanism is implemented.

The integrated research branch passes documentation validation, Ruff, mypy
over 60 source files, all 17 new focused tests, and the full Python 3.12.10
suite with 530 passes and 4 platform skips. The first full run used a
OneDrive-synchronized pytest temporary directory and hit one transient Windows
database-replacement lock; the exact updater test and then the complete suite
passed from a nonsynchronized local temporary directory.

## Previously verified baseline

- Python 3.12+ cross-platform Core with per-user SQLite/FTS5 storage,
  migrations, portable locking, clean shutdown/restart, and loopback default.
- Source, legacy candidate/approval, correction, supersession, tombstone,
  history, permission, provenance, validity, and audit lifecycles. The
  automatic observation/disposition layer below replaces the user-facing
  approval lifecycle.
- Idempotent/resumable ingestion sessions, coverage reports, model proposals,
  generic documents, and local full-history adapters for ChatGPT, Claude, and
  Grok exports. Raw archives are streamed into Core, provider messages receive
  conversation-level provenance, assistant/tool text stays inert, and failed
  extraction can retry from the preserved blob.
- Required MCP tools over HTTP and a managed STDIO adapter; one-click local
  Codex and Claude Desktop configuration bound to the exact vault. Windows
  Claude discovery covers classic installers and the Microsoft Store/MSIX
  package, including its package-local roaming configuration path.
- Bundled dashboard infrastructure for import, search, local connections,
  encrypted backup, audit/activity, and signed-update controls.
- Windows per-user installer/shortcut/startup/uninstall path, macOS unsigned
  app/DMG/LaunchAgent path, Linux portable package path, and three-OS CI.
- Deterministic Retrieval V3 with policy-first authorization, rebuildable UTC
  interval sidecars, weighted candidate-scoped FTS5, conservative task
  admissibility, safe diagnostics, and deterministic marginal context-set
  selection without a vector dependency.
- Offline-signed Ed25519 update metadata, immutable candidate assets,
  checksums, SBOM/provenance, and Windows transactional update/rollback code.
- The active beta-only `release-2026-a` public key is embedded in the package
  keyring. Its SHA-256 fingerprint is
  `fe05a2bd52db97f808650fb0e832c49bd704abd62a813af4dedca4994f98e0d4`;
  the encrypted private half remains operator-controlled outside the checkout.
- Frozen Windows x86_64 beta packages with an active reviewed key now select
  the canonical Pages channel automatically. The artifact transport follows
  GitHub's single pinned release-CDN redirect while retaining signed size and
  SHA-256 verification; metadata and arbitrary redirects remain refused.
- Installed Windows packages also recover their packaged update identity from
  the exact application name plus adjacent updater helper if a frozen child
  process lacks its normal marker. Update status exposes the trust-backed
  available channels so the dashboard cannot invite selection of an
  unconfigured channel.
- Before the first protected promotion, an HTTP 404 from only the exact
  built-in beta manifest URL is represented as `unpublished` rather than a
  transport failure. The dashboard says it is waiting for the first signed
  release; custom endpoints and all other update failures still fail closed.
- Manual candidate and publish workflows keep repository-admin credentials out
  of Actions. An owner verifies the immutable-release setting locally, enters
  an exact nonsecret dispatch phrase, and Actions independently enforces the
  source head, unused release slot, artifact evidence, and final immutable
  published state.

## Automatic context maintenance in the current worktree

The following implementation is visible locally; verification is still in
progress:

- Core migration `005_automatic_context_policy.sql` adds per-vault
  `automatic-v1` policy, observation origin/time/disposition/decision fields,
  record policy metadata, and observation-to-record evidence links. Existing
  approved and rejected rows map to applied and ignored compatibility state.
- `AutomaticMemoryPolicy` classifies server-originated observations as
  `applied`, `reinforced`, `tentative`, or `ignored`; ingestion observations are
  `staged` until session completion. Secret-like and highly sensitive content
  is ignored, sensitive applied context is forced to `local_only`, and
  non-explicit/inferred context requires corroboration.
- Direct observations are evaluated in the same Core transaction. Exact matches
  reinforce current context; explicitness and `observed_at` resolve same-slot
  replacement, and explicit targeted corrections apply automatically while
  retaining record versions.
- Ordinary deletion is reversible. `restore_record` and the matching
  administrator endpoint can restore the latest soft-deleted state or a chosen
  historical version, rebuild retrieval state, add a new version/audit event,
  and preserve the separate irreversible-purge boundary.
- Core migration `006_reversible_source_deletion.sql` gives imported sources the
  same ordinary delete/Undo boundary. A deleted source is hidden from normal
  listing, counts, raw access, and reprocessing; current records canonically
  attributable to it are soft-deleted in the same transaction. Undo restores
  only records whose deletion version still matches that source operation, so
  independently deleted or purged records cannot be resurrected. Exact
  duplicate reimport restores the soft-deleted source safely.
- Finished ingestion sessions evaluate staged observations atomically.
  Unfinished sessions remain noncurrent, and startup reevaluates eligible
  staged legacy/finished-session observations idempotently.
- Import results expose `outcomes`, a count by actual observation disposition,
  plus deduplicated affected `record_ids`. The dashboard shows total
  observations, truthful coverage, and per-disposition outcome counts.
- MCP/HTTP models now carry `observed_at` and return disposition, optional
  `record_id`, `decision_reason`, `decided_at`, and `policy_version`. Relay MCP
  returns staged queue receipts and leaves final evaluation to Core.
- The administrator observations endpoint exposes disposition, affected record,
  decision reason/time/version, source reference, and evidence. Context shows
  record provenance and history. Activity is an optional read-only observation
  decision stream with origin, submitting client/service, content, and policy
  reason.
- Model-facing MCP includes a narrow `forget_context` tool: it requires an
  explicit user request, record ID, and reason; local Core creates an audited
  reversible tombstone, while Relay can only stage the request.
- The dashboard worktree removes Review navigation, pending badges, approval
  forms, and approval copy; Context is the default, Sources reports observations,
  Activity passively shows automatic decisions and provenance, and current records expose
  correction plus delete/undo/historical-version restoration controls. Sources
  now exposes a confirmed Remove action and immediate Undo for the source and
  derived current memories. Its local suite passes 27 tests, type checking, the production build, and a
  high-severity dependency audit with zero reported vulnerabilities.
- Physical `context_candidates`/`approval_status` names and legacy administrative
  endpoints remain temporarily for schema, backup, and integration
  compatibility. They are not the new product language.
- The reproducible demo and its E2E assertion now use successful ingestion
  finish to apply explicit observations and retrieve them without any approval
  call. The demo is included in the passing full local suite.

Still missing or not yet verified:

- the end-to-end browser smoke;
- cross-platform/package evidence on the integrated automatic-policy commit.

Tentative-observation expiry/decay is intentionally not implemented in
`automatic-v1`. It is a possible later versioned-policy extension; tentative
state is already noncurrent and creates no user queue.

## V1 Edge removal

- Edge navigation and setup were removed from the dashboard.
- First run no longer offers or opens hosted web/mobile setup.
- Dashboard status no longer calls the Edge API.
- Newly applied context exposes only `local_only` and `core_available`.
- Core no longer starts the legacy Edge network worker.
- The GHCR Edge workflow, Render templates, and Relay container CI job were
  removed from the V1 path.
- Experimental Relay/Edge modules and explicit cleanup APIs remain dormant so
  earlier engineering state can be inspected/decommissioned without data loss.
  They are not a supported V1 feature.

## Retrieval V3 integration

- The frozen V2 comparator is pinned to `70a4808` with checked fixture hashes
  and ranking fingerprints; production V3 cannot silently move it.
- Core, MCP, and CLI accept offset-aware `as_of` search. Current and historical
  resolution is UTC-normalized, deterministic across restart, and treats
  deletion/purge as terminal across restore.
- Weighted BM25 runs only over authorized and temporally eligible candidate
  IDs. Prefix fallback, candidate count, tokens, channel results, query length,
  and result count are hard bounded; FTS5 secure-delete is feature-detected.
- Task admissibility uses only upstream numeric factors after hard policy and
  time filtering. Sparse/underspecified evidence fails open; learned authority
  remains shadow-only.
- The integrated 1k/10k comparator gate passes locally on Windows. Both profiles
  have exact Recall@5 `1.0`, admissibility precision `1.0`, temporal precision
  `1.0`, semantic coverage `1.0`, zero redundancy, zero policy violations, and
  deterministic rankings/conflicts. After set-selection integration, the 10k
  warm p95 is `80.6885 ms`; total database-plus-sidecar growth from 1k to 10k
  is `1027.185778` bytes per added record. Lifecycle resurrection count is zero.
- `ContextCompiler` now uses metadata-only deterministic marginal utility,
  mandatory-preference priority, semantic/diversity gains, transitive duplicate
  groups, same-slot conflict exclusion, supporting-evidence relationships, and
  exact character budgets. Its standalone benchmark passes all 11 gates with
  semantic coverage `1.0`, zero set violations, and deterministic input-order
  behavior.
- The optional 384-dimensional float32 dense shadow remains disabled,
  in-memory, nonauthoritative, and outside default packaging. Synthetic exact scan
  is deterministic but misses its 10k target: `400.294955 ms` warm p95 versus
  `150 ms`, with `15,360,000` vector bytes. No real local model or semantic
  comparison was exercised, so dense ranking and ANN were not promoted.
- Research-only source-evidence selection preserves `1.0` recall and facet
  coverage with zero policy violations at 64/256 sources. Diversity-aware
  token MaxSim reduces measured redundancy from `0.083334` to zero; the final
  256-source warm p95 is `18.9572 ms`. Neural late interaction remains
  unexercised and there is no runtime integration.
- Integrated commit `67dd11c` passed the hosted Python 3.12 matrix on Windows,
  macOS, and Ubuntu, dashboard Node 20/22, and native package acceptance on
  Windows, Ubuntu, macOS ARM, and macOS Intel. Latency numbers remain local
  measurements rather than cross-platform performance claims.

## Remaining beta gates

- Run the exact ADR-039 worktree through the hosted Python 3.12
  Windows/macOS/Linux and native-package matrices, then complete the fresh-user
  browser smoke.
- Create and verify two recoverable encrypted backups of the operator-held
  release private key before its first production signature.
- Add required reviewers to the release-promotion and `github-pages`
  environments; no live channel or public release exists yet.
- Freeze the final release commit after review and repeat the full hosted
  Windows/macOS/Linux and dashboard matrix on that release identity.
- Exercise a real signed beta1-to-beta2 Windows update and rollback.
- Design and test secure direct-Core mobile pairing before claiming one-click
  mobile access. Core remains `127.0.0.1` by default in the meantime.

## Current evidence

- Current ADR-039 worktree on Windows Python 3.14.3: Ruff passes; strict mypy
  passes across 59 source files; the full suite passes 513 tests with four
  host-limited symlink skips; documentation links and `git diff --check` pass.
  This includes automatic policy, ACL/session isolation, migration restart,
  pre-v5 restore, source-free foreign-key/FTS recovery, purge resurrection
  barriers, context-error idempotency, delete/restore history, Relay queue
  identity, ordered projection restoration, and the approval-free E2E demo.
- Current dashboard on Node 25.6.1: 27 tests, TypeScript checking, and the
  production build pass; `npm audit --audit-level=high` reports zero
  vulnerabilities. Packaged dashboard assets match the production build
  byte-for-byte.
- The required Python 3.12 hosted cross-platform/package suite and new
  automatic-policy browser smoke remain pending for this exact worktree.
- Historical pre-ADR-039 full Python 3.12 suite: 461 passed; four Windows-host
  symlink tests skipped because this account cannot create the required links.
- The provider importer, API, and end-to-end slice also passed 36 focused tests
  on the minimum supported Python 3.12 runtime.
- Historical pre-ADR-039 dashboard: 19 tests passed; type check, production
  build, and high-severity dependency audit passed.
- Historical pre-ADR-039 Ruff format/lint, strict mypy across 58 source files,
  documentation-link checks, and the approval-based seven-step single-Core
  demonstration passed.
- A historical live isolated browser smoke imported a fictional ChatGPT export
  through the bundled dashboard, reported one conversation/two legacy
  candidates, retained the raw source, excluded the assistant claim, moved one
  approved item out of review, emitted no browser warnings/errors, and rendered
  correctly at desktop and 390-pixel mobile widths. It does not satisfy the
  new automatic-policy browser gate.
- The packaged dashboard contains the direct-Core mobile boundary and contains
  no Edge setup copy or `/admin/edge` request path.
- GitHub release immutability is enabled, and GitHub Pages is configured to
  deploy only from Actions. The canonical beta metadata URL currently returns
  HTTP 404 because no channel artifact or beta release has been deployed. The
  exact built-in client now reports that state as `unpublished`, but this does
  not replace the required offline-signed release and protected promotion.
- The Python 3.12 Windows frozen application passed resource discovery and the
  isolated first-run/install, browser handoff, MCP handshake, restart, startup,
  update-recovery, shutdown, uninstall, and cleanup smoke. The unsigned Windows
  package also passed its platform trust smoke.
- Implementation commit `05c7638` passed both its
  [push matrix](https://github.com/Martian-ux/All-The-Context/actions/runs/29969999250)
  and
  [draft-PR matrix](https://github.com/Martian-ux/All-The-Context/actions/runs/29970013608):
  Python 3.12 on Windows, Ubuntu, and macOS; native desktop/package acceptance
  on Windows, Ubuntu, macOS ARM, and macOS Intel; and dashboard checks on Node
  20 and 22.
- Retrieval V3 integration commit `67dd11c` passed its
  [push matrix](https://github.com/Martian-ux/All-The-Context/actions/runs/29976224653):
  Python 3.12 on Windows, Ubuntu, and macOS; native desktop/package acceptance
  on Windows, Ubuntu, macOS ARM, and macOS Intel; and dashboard checks on Node
  20 and 22.

## Explicitly unclaimed

- No public beta downloads currently exist.
- No secure automatic mobile endpoint currently exists.
- No paid/native publisher signing or Apple notarization is planned for the
  community beta.
- The live SQLite vault is not application-encrypted at rest; portable exports
  are passphrase-encrypted.
