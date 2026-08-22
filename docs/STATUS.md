# Project status

## Current milestone

As of 2026-08-22 UTC, this document describes the local integration baseline
only: Import, Memory Truth, Retrieval, and the Context UI are composed on the
local `codex/truth-foundations-overnight` checkout. It does not assert release
publication, hosted CI results, exact artifact/client/provider acceptance, or
inspection of live/private data.

Core remains the authoritative local service and binds to loopback by default.
The supported posture is Windows and supported Linux source/package work; the
macOS source, tests, and historical preflight remain retained for portability,
but macOS is unsupported and creates no support or acceptance claim. Historical
release and CI notes lower in this file are retained as provenance only, not as
evidence for this integrated checkout.

## Adversarial boundary sweep (2026-08-22)

A focused post-integration sweep reproduced and closed four boundary defects:

- Direct-secret detector v3 normalizes Unicode compatibility forms and removes
  zero-width/combining obfuscation before applying the existing high-confidence
  credential rules. Refused content still receives only an opaque replayable
  receipt and never enters Core, Relay, SQLite/WAL/FTS, or diagnostics.
- Canonical `supersedes` writes now reject missing, cross-vault, overlong, and
  cyclic chains transactionally. Self- and two-record-cycle regressions prove a
  local correction cannot poison the temporal sidecar or make retrieval fail.
- ZIP members with names beyond the 1,000-character diagnostic/identity bound,
  or with non-printable names, close as unavailable with bounded escaped
  diagnostics. Distinct long paths can no longer collapse after truncation and
  silently discard one member.
- Continuous Capture now rejects implicit identifier/integer coercion and
  Unicode-obfuscated credential markers. An injected sink must echo the exact
  deterministic source/item lineage; a mismatched first-event receipt fails
  before `capture_items` or checkpoints can advance. Retrieval experiment
  diagnostics and selection constraints are strictly finite and bounded, and
  malformed/nonfinite lexical scores are neutralized before usefulness ranking.

This is local focused evidence only. The long full pytest matrix, hosted CI,
exact artifacts, live/private data, publication, release acceptance, and macOS
work were not run or claimed; beta.6 and the unsupported-macOS posture are
unchanged.

## Sources/Context dashboard contract reconciliation (2026-08-22)

- The beta.6 dashboard keeps source item accounting separate from terminal
  import processing, normalizes the exact seven-key closed-coverage shape, and
  preserves cancelled/failed terminal reasons without fabricating counts.
- Dashboard wire reads now construct record and selected-truth values
  field-by-field with bounded strings, arrays, enums, timestamps, hashes,
  confidence, versions, and counts. Malformed detail envelopes fail with a
  content-free API error; malformed list rows are omitted while valid siblings
  remain usable. Import IDs and displayed stats count only valid bounded
  values, including mixed legacy `candidate_ids` responses.
- Context uses the existing bounded current-only search plus typed Core reads for
  content-free coverage and selected truth. The accounting view shows real
  state counts, conflict groups, and incomplete ingestion sessions; the
  inspector renders selected truth status, provenance, evidence, and history
  without per-row truth requests. A coverage failure clears cached truth
  metrics while retaining search results, and sequence guards prevent stale
  concurrent coverage responses from changing a newer refresh.
- Focused dashboard tests cover normalization, terminal/item split, coverage
  fallback, truth races, mutation refresh, and rendered statuses. Adversarial
  normalization and coverage recovery/race checks bring the suite to 54 tests
  across 2 files. Fresh independent API/DOM review accepted the exact local
  hardening commit. Synthetic loopback browser checks remain local evidence
  only, and fresh visual Product Design acceptance is still pending because the
  required in-app browser verifier was unavailable. No release, publish, or
  live/private Core/data work is claimed here.

The integrated truth boundary is explicit: import `closed_coverage` counts
logical source items; source terminal failure/cancellation is separate; Memory
Truth coverage counts durable Core entities; and Retrieval `pack_metadata`
describes one bounded transient bootstrap selection. These dimensions are not
combined or presented as release evidence.

## Stage 4 Continuous Capture foundation (2026-08-22)

This isolated first slice adds migration 015 and provider-neutral Core contracts
for a bounded Continuous Capture ledger: source lifecycle, checkpoints,
durable staged events, source-scoped item lineage, and foreground run telemetry.
The coordinator is local-only and opt-in, requires explicit acknowledgement,
has no scheduler, and makes no adapter call for disabled, paused, or revoked
sources. Focused synthetic tests cover migration repair, lifecycle guards,
ordered multi-page upsert/update/delete, duplicate replay, idempotent sink
recovery, gaps/cursors/limits/backoff/leases, API authentication, CLI output,
and secret-marker absence.

Fresh independent security/correctness review accepted the exact cumulative
foundation after reproducing and closing schema-repair and stale-lease authority
defects. The review passed 17 capture tests, 14 migration regressions, Ruff,
mypy, diff checks, and independent temporary-database probes. The whole
`test_client_witness.py` file passes 19/19 after bounded canonical relay-reason
reconciliation. Full pytest and release acceptance are not claimed.

This is infrastructure, not a claim that a provider supports continuous
capture. No real connector, network implementation, OAuth/credential handling,
dashboard or package-startup change, live/private Core/data inspection, current
product availability, release, publication, beta acceptance, or macOS work is
claimed. The beta.6 identity and unsupported-macOS posture remain unchanged.
Full snapshot/rescan deletion is deferred until a provider-specific snapshot
contract exists.

The separately published `0.1.0-beta.6` remains the public, immutable,
current downloadable release; this local integration neither publishes nor
alters it. Its exact release identity is ID `374723649`, source
`d6d51acc5a880e611a65a206b90c6eb68118443d`, release-candidate run
`32536391309`, candidate digest
`afa9036b4df5aca85975e9f3fdf475ac85e589a4ad0937d3ca1c440287b78647`, and 34
attested assets. Occupied beta.1 through beta.5 draft/candidate identities
remain historical and are not rebound or reused.

### 2026-08-22 Memory Truth foundation

Core now has an additive canonical Memory Truth projection for authorized
clients and providers. It exposes current, tentative, superseded, conflicted,
and deleted semantics; deterministic observation evidence; source identity;
decision reason and policy metadata; effective, observed, and recorded times;
confidence and sensitivity; bounded version-history counts; and content-free
coverage/accounting. `GET /v1/context/truth/{record_id}` and
`GET /v1/context/coverage` are the smallest read contracts for authorized
clients. Admin-only list/detail endpoints include non-current records and
detached tentative observations.

Source rebuild withdrawal now records the exact rebuild source on its tombstone.
Only an untouched automatic archive record with that matching internal
source-rebuild tombstone may be re-applied under its stable identity. An
ordinary user deletion is a hard automatic reappearance barrier: matching
archive evidence is retained as ignored and linked to the deleted record, with
no replacement current record. Stable record identity includes source address
and value identity, so repeated source references with different values remain
distinct.

Migrations `010`–`014` are the Memory Truth foundation. Migration `015` is the
additive Continuous Capture contracts/ledger foundation described below.

This is a Memory Truth foundation, not a complete memory-audit product. A
replayable append-only decision event stream, configurable tentative expiry or
decay, source-content history/purge presentation, provider extraction changes,
retrieval/ranking changes, and dashboard wiring remain outside this slice.
Focused truth, storage, policy, migration, and API checks are local engineering
evidence only; this slice makes no hosted-CI or release-state claim.

### 2026-08-22 source-rebuild safety correction

Complete-source rebuild now stages its parser-versioned candidates while the
prior current context remains visible. Core publishes the replacement and
withdraws eligible old records in one SQLite transaction; parser, ingestion,
cancellation, interruption, and policy-evaluation failures leave the old
current records and their history intact. Rebuilds resume through the existing
idempotent session when source finalization is interrupted; a generation/session
publish marker written in the cutover transaction prevents a retry from
withdrawing the replacement a second time.

Only current approved records with Core origin `archive_import` and no typed
local mutation, ordinary deletion, or invalid identity are replacement targets.
Direct/local-admin records that retain the source ID remain in place; public
restore retains their truthful source provenance while the ledger blocks later
replacement.
Synthetic focused coverage covers parse failure, injected policy-ingestion
rollback, cancellation, corrected records, and local-authored records. This
integration lane reports only the scoped local checks run below; it makes no
full-pytest, hosted-CI, release, or exact-client acceptance claim.

### 2026-08-22 bounded ChatGPT attachment ingestion slice

The provider importer now inventories every ChatGPT `.dat` ZIP member without
extracting it to disk: each member is bounded, streamed, SHA-256 hashed, and
stored in source metadata with archive identity, manifest/filename/MIME
provenance, and attachment-ID conversation/message linkage where the export
metadata establishes it. Text extraction is intentionally limited to
manifest-proven `.txt`, `.json`, `.jsonl`, `.csv`, `.md`, and `.markdown` assets.
Images/audio and office/document/web/script formats remain retained raw and
explicitly unsupported; the importer does not claim that all `.dat` contents
are searchable. Synthetic tests cover identity, linkage, supported text,
unsupported binaries, traversal, member/total expansion, and text read bounds.
Real-export structural inspection was content-free; no real export bytes or
personal values enter the repository, tests, logs, or receipts.

### 2026-08-22 neutral alternate promotion correction

Auto provider detection now buffers only bounded structural-signature
observations until the complete JSON iterator succeeds. A neutral alternate
such as `messages.json` with valid ChatGPT-shaped content followed by trailing
data or a later depth, item, byte, or parser-limit failure remains generic and
incomplete, closes once as `unparsed`, and cannot enable ChatGPT attachment
inventory or links. Direct, path, and ZIP entrypoints share this boundary; a
valid named provider member remains independent of a malformed neutral sibling.
This is synthetic local engineering evidence only, does not inspect live/private
Core or exports, and grants no acceptance credit.

### 2026-08-22 Import Truth accounting closure

Provider and ZIP results now expose a dimensionally bounded
`coverage.closed_coverage` map with the item-level keys `recognized`,
`excluded`, `skipped`, `unavailable`, `duplicate`, `failed`, and `unparsed`.
The public schema rejects unknown keys and non-strict, negative, or
out-of-bound counts; `complete=true` is rejected when unavailable, duplicate,
failed, or unparsed counts are nonzero. Oversized ZIP `.txt`/`.md` members close as
`unavailable`, and malformed manifest-declared text `.dat` attachments close
as `unparsed` only. ZIP/member diagnostic names are bounded and control-escaped.
Case-insensitive duplicate ZIP members increment `duplicate`; bounded member
parser failures increment `failed` and force incomplete coverage. A fatal
source failure or cancellation never inflates those item counts: the preserved
source keeps known counts, sets `coverage_complete=false`, and records the
separate source-level `source_terminal_reason` (`failed` or `cancelled`) in
source metadata; durable import operations retain their terminal status too.
The same closed map is carried in the public ingestion coverage report and
CLI/API import result. Synthetic regressions cover duplicate members,
malformed CSV, pre-parse failure, partial member failure, and cancellation.

UI handoff contract: render `coverage.closed_coverage` as item accounting and
render `source.metadata.source_terminal_reason` or the operation `status` as a
source-level terminal state. These dimensions must not be added together.

### 2026-08-22 attachment review-boundary corrections

The ChatGPT `.dat` inventory and bounded text slice now run only for explicit
ChatGPT imports or auto/generic archives with a structurally confirmed ChatGPT
mapping graph. Explicit Claude/Grok and unconfirmed archives leave those
members raw, so malformed ChatGPT control files cannot fail unrelated provider
imports. Inventory IDs preserve unique archive-member names, links persist as
exact conversation/message pairs, and colliding stems do not produce inferred
links. MIME conflicts are explicit `ambiguous` status with no selected type or
fake provenance source. Link accumulation is capped at 10,000 pairs and
per-document scanning is bounded; truncation is reported as incomplete.

PR 63 was squash-merged into protected `main` at
`6be7e1d032714b39528fcc31d5333539406d08a6`, after PR 62 at
`080d90669dd5936206c088ae0f4fe4cca24d327e`. PR 64 then landed the ADR-088
provider-acceptance close/cleanup follow-up at
`6151e1f8850793c80ebe01d7db0b38e3ac1aff05`. That `6151e1f` SHA is the
inspected `beta.2` source tree, not a reusable exact-candidate identity.
Source inspection found that BETA-P06 cannot honestly pass on that tree: the
global visible-focus outline is overridden by `.search-input input { outline:
0; }`, and the search wrapper's static border is not a focus indicator.
ADR-089 records the source-level `:focus-within` wrapper treatment and
advanced the source version to `0.1.0-beta.3`. This correction only
makes a new exact candidate eligible for fresh Edge acceptance; it does not
pass P06. The resulting historical candidate identity is recorded above.
ADR-093 rotates the unavailable prepublication signing credential without
alleging compromise, marks `release-2026-a` revoked, activates
`release-2026-b`, and advances the replacement candidate to
`0.1.0-beta.4`. ADR-094 records the custody documentation correction and
advanced the replacement candidate to `0.1.0-beta.5`. ADR-095 records the
remaining stale active-state statements, retains beta.5 as occupied history,
and advances the active source version to `0.1.0-beta.6`. Its exact candidate,
approval, signing, and publication evidence remains in the external ledger.
ADR-096 records the post-publication GitHub CLI asset-ID compatibility repair.

PR 66 was squash-merged after both hosted CI matrices and CodeQL passed at
protected main `088485d268d11e3167e7aad2e09a22c42659607f`. It records the
post-V1 zero-friction product contract and raises the reviewed dev/build `pip`
floor past CVE-2026-13346. Neither change alters the historical beta.3
candidate identity or grants a receipt.

ADR-090 accepts the post-V1 zero-routine-friction direction and its
capability-qualified integration model. It preserves Core authority, treats
connected content as inert data, reuses the existing observation/current-record
and Retrieval V3 boundaries, and requires correction, retention, deletion, and
purge closure across derived state. This direction does not expand
`0.1.0-beta.6`, grant acceptance credit, or permit `ZF-*` implementation issues
before beta publication and tracker reconciliation.

V1 was simplified on 2026-07-22: Core is the only user-facing service. Hosted
Edge, third-party runtime deployment, offline mobile replicas, and provider
hosting setup are no longer part of the V1 beta product or acceptance gate.
The first beta is same-device only. A future mobile path would connect directly
to Core while Core is online and must pass a separate security decision.

On 2026-07-23 ADR-039 superseded the review-first memory design. The confirmed
product contract is now one-time setup plus automatic, reversible,
provenance-backed context maintenance, with no routine review queue. The
automatic-policy migration has passed the local Ruff, mypy, full pytest,
dashboard, demo, documentation, and dependency-audit gates described below,
plus the hosted Python 3.12 cross-platform/package matrix on main. The
integrated release-CI diagnostics and startup-readiness work has passed 46
focused local tests and both hosted matrices at its exact functional branch
SHA. The final frozen release identity has not yet been validated. Earlier
approval-based evidence remains historical and must not be presented as proof
of ADR-039.

Current main also raises the raw provider-import default
and ceiling to 2,000,000,000 bytes without placing a two-billion-byte value in
SQLite. Migration 007 keeps Core authoritative while moving large source
content into ordered 8 MiB-or-smaller rows; streamed copies and
source-inclusive encrypted restores validate the reconstructed size and
SHA-256 identity. Focused configuration, migration, storage, export, importer,
and CLI regressions pass locally, and the full Python suite passes 662 tests
with four host-limited symlink skips. Exact integration commit `03a266f` also
passed all nine jobs in both its hosted push and draft-PR matrices. The
two-billion-byte value is an implemented configuration boundary, not yet an
accepted beta support claim. It is now a mandatory beta boundary: publication
requires exact-boundary and boundary-plus-one behavior, disk preflight,
durable progress/cancel/retry, bounded resource use, interruption recovery,
complete source integrity, source-inclusive encrypted export, and restore.

Roadmap baseline `1d44fdd80a3dcb32c580434924bb03c1e5291ae1` passed all nine jobs in
[hosted CI run 30177362472](https://github.com/Martian-ux/All-The-Context/actions/runs/30177362472)
on 2026-07-25. The exact diagnostic/startup branch SHA `f3496df` also passed
all nine jobs in both its
[push run](https://github.com/Martian-ux/All-The-Context/actions/runs/30062427719)
and
[draft-PR run](https://github.com/Martian-ux/All-The-Context/actions/runs/30062429444).
The exact combined diagnostic/startup and 2 GB import commit `03a266f` then
passed all nine jobs in both its
[push run](https://github.com/Martian-ux/All-The-Context/actions/runs/30176660300)
and
[draft-PR run](https://github.com/Martian-ux/All-The-Context/actions/runs/30176687083).
An intermediate Windows package run reached the managed MCP adapter before the
one-file Core produced its first log line and failed at the former 10-second
deadline. The integrated hardening adds bounded retrieval, rollback-journal,
and `hdiutil` diagnostics plus one 30-second managed-Core readiness window. It
does not relax a gate, retry a failed launch, or change updater/release
behavior.

The 2026-08-08 merged-main matrix exposed a narrow mismatch between the
Windows updater's documented recovery contract and its packaged smoke. The
helper may persist `rolling_back` / `rollback_retry_required` and exit 3 when
restoration enters its bounded retryable error path; the content-free hosted
diagnostic intentionally did not retain the underlying exception. Production
then re-enters that same journal. The smoke previously required the first helper
invocation to return the terminal rollback code and therefore failed even
though the identical tree had passed twice. It now permits exactly one second
invocation only for that exact persisted state, then still requires the
terminal journal, restored binary hashes, pre-update database, healthy prior
Core, uninstall, and cleanup. Other exit codes, states, or a second failure
remain release-blocking; no updater threshold or production behavior changed.

The same merged-main matrix later exposed an evidence-boundary defect in the
100-record Retrieval V3 pytest smoke: every functional and lifecycle gate
passed, but shared-runner descheduling inflated its warm wall-clock p95. The
frozen product benchmark has always required the 1k/10k CLI run on comparable
hardware, with the 10k warm p95 below 150 ms. Pytest now treats its bounded
100-record run as functional evidence only, while deterministic unit cases
prove the production operational gate accepts 149.999 ms and rejects 150 ms,
non-finite, negative, missing, and mixed-profile evidence. The CLI clock,
sample count, profiles, threshold, and fail-closed result are unchanged.

## Superseded macOS acceptance preparation (2026-08-15)

This section records the former four-platform plan and is superseded by ADR-086.
It credits no Mac acceptance cell and creates no requirement for the current
Windows/Linux beta. The exact Mac patch was never frozen.

Source now provides a strict, content-free Mac host preflight and a
candidate-bound supporting runner. The runner verifies the complete candidate
inventory, exact clean source and Python import path, native host eligibility,
architecture-specific DMG identity, app/MCP/recovery architectures, structural
code seal, isolated Keychain/startup adapters, frozen resources, packaged
recovery, first run, MCP restart, and exact cleanup. It retains no subprocess
content and emits no gate receipt. Its tool-file digests make a newer reviewed
runner usable against an older frozen candidate without modifying that
candidate checkout.

At the time, hosted CI and the old release-candidate source also recorded the
Mac preflight, and the four-platform workflow ran the direct-package
trust/identity verifier before artifact assembly. Those unrun physical
Gatekeeper, client, login/reboot, Keychain, recovery, and 2 GB journeys are now
retired requirements, not failures or passes. The archived operator boundary is in
[`operations/MACOS_NATIVE_ACCEPTANCE.md`](operations/MACOS_NATIVE_ACCEPTANCE.md).

## Provider packaged-acceptance and Windows smoke residue (2026-07-26)

Source-level blocker fixes for V1 convergence (exact base
`a17b2ff794573750cc1a1df7735a1e3272045466`):

- ChatGPT graph nodes that are known empty system/tool shells, empty users,
  or attachment/non-text-only structures now close into excluded, skipped, or
  unavailable rather than unparsed. Genuinely unknown or malformed nodes still
  remain unparsed and keep coverage incomplete. Synthetic production-shaped
  ZIP and graph regressions prove a truthful complete aggregate for
  classifiable material and a precise reconcile-stage failure for unknown
  residuals.
- Packaged provider acceptance no longer collapses operation failure,
  non-complete operation status, and acceptance reconciliation into one
  ambiguous `import_validation_failed` code. Content-free stage codes are
  `import_operation_failed`, `import_operation_incomplete`, and
  `import_acceptance_reconcile_failed`.
- Durable import-operation `bytes_committed` / `bytes_received` are monotonic
  so later staging or member-progress domains cannot regress raw-archive
  commit telemetry.
- Successful Windows packaged first-run smoke removes the exact run-owned
  smoke startup override key after the product value is cleared, and fails
  closed if that key is nonempty or cannot be deleted. Ordinary product Run
  key behavior is unchanged.

These are source/harness fixes only. Exact downloaded-candidate real-export
provider receipts and live OTA smoke re-runs remain open.

## Packaged provider-acceptance Windows vault close (2026-08-17)

Exact main `6be7e1d032714b39528fcc31d5333539406d08a6` hosted CI exposed a
nondeterministic Windows failure in the packaged provider owned-vault removal
test. The assertion retained only exit 1, not the content-free stage report,
so the exact failing stage was not directly observed. Inspection found that
packaged `--packaged-provider-acceptance` had no explicit Core shutdown before
owned `rmtree`; on Windows, a lingering SQLite/WAL/observer handle can block
that removal. ADR-088 adds idempotent
`CoreStore.close()` (observer close, then write-locked
`PRAGMA wal_checkpoint(TRUNCATE)` on a `_ClosingConnection`) and
`CoreService` context-manager shutdown. The packaged surface binds Core as a
context manager so close always precedes owned `rmtree` on success and every
exception path. Caller-supplied data-dir deletion is unchanged. An `OSError`
from owned `rmtree` after close still yields `data_dir_cleanup_failed` and
exit 1. The release path does not sleep, retry, change `journal_mode`, hide
`rmtree` errors, or call `gc.collect`. Candidate dispatch stays blocked
pending this follow-up merge and exact-main green.

Local Python 3.12 validation for this follow-up passes repository-wide Ruff
format/check, strict mypy across 81 source files, and pytest with 1,022 passed,
four host-limited symlink skips, and three known deprecation warnings. The docs
contract, 43 third-party Action pins, current-tree and full-history security
scans, paired public keyring validation, tracked private-key audit, frozen
Python dependency audit, and `git diff --check` also pass. These local results
do not substitute for the required hosted Windows rerun on the eventual exact
protected-main merge SHA.

## Security maintenance

On 2026-08-16, privileged `workflow_dispatch` release jobs were required to
run from the default branch and to check out `github.sha` rather than
`inputs.source_commit` before executing repository code. Every
source-executing job in `release-candidate.yml` (`validate`, `native`,
`draft`), `publish-beta-release.yml` (`publish`), and
`promote-beta-channel.yml` (`build`) fail-closes unless `github.ref` is
`refs/heads/<default_branch>` and `inputs.source_commit` is exactly 40
lowercase hex. Candidate-build jobs additionally require that
`source_commit` equal the dispatch SHA, because they build one candidate
from that snapshot. Later publish and promote jobs may run after protected
`main` has advanced; their `source_commit` is the reviewed historical
candidate/release identity, is not required to equal the later
`github.sha`, and is passed as data to existing release/candidate
verification. Only the new pre-check steps bind GitHub expressions through
`env` and avoid interpolating them into the shell script. Privileged
checkouts use `ref: ${{ github.sha }}`; no `inputs.source_commit` checkout
remains in those workflows. Actions cache access (`cache`,
`cache-dependency-path`, and `actions/cache`) is removed from the three
privileged release workflows; `setup-uv` keeps `enable-cache: false`.
Ordinary CI caches are unchanged. Exact protected `main`
`6be7e1d032714b39528fcc31d5333539406d08a6` then passed hosted CodeQL run
`31991996483`: Actions, JavaScript/TypeScript, and Python analyses all reported
zero results, `main` had zero open code-scanning alerts, and alerts #3 through
#21 closed as fixed with no dismissal. This closes the ADR-087 hosted rescan;
it does not waive the separate Windows CI cleanup failure on that SHA or the
required green checks on the follow-up exact `main`.

On 2026-07-26, Core browser handoff values were removed from executable
JavaScript literals and are now passed through quoted HTML-escaped data
attributes to a constant nonce-protected script. The integrations status
endpoint also replaces local configuration exceptions with a stable repair
message, so parser errors cannot disclose paths, credentials, or personal
configuration material. Focused regressions cover both response sinks. GitHub
alerts #1 and #2 closed as fixed through the integrated `main` rescan on
2026-07-26; neither was dismissed or otherwise mutated to manufacture a clean
gate.

A later exact-candidate Windows Edge run found one same-origin, query-free
`/favicon.ico` JSON 404 during every healthy dashboard handoff. The dashboard
now declares and packages a local SVG favicon, and bundled-serving coverage
requires the icon with no external resource reference. The browser acceptance
harness functional correction reads the escaped ADR-064
`data-browser-token` attribute instead of the obsolete executable literal. A
clean committed Windows package built locally from this correction passed the
focused real Edge P06/S05 journey with zero unexpected console/page errors, no
external requests, the packaged favicon, and the accepted ticket/session
lifecycle; the repaired happy-path Python control also passed. Independent
review then closed the extractor's false-pass paths by binding its nonce to the
exact response CSP and rejecting external `src`, extra executable markup,
inert/ambiguous handoffs, and non-production targets. The integrated Python
suite passes 932 tests with four host-limited skips. A rebuilt official
downloaded release candidate is still required for release acceptance. Under
the frozen S05 contract, consumed ticket bytes in browser history files are not
a byte-erasure failure when current navigation is clean and expiry, non-replay,
referrer/cache isolation, session termination, and revocation pass.

On 2026-07-26, the live public repository enabled secret scanning and push
protection, Dependabot alerts and security updates, CodeQL default setup, and
required immutable Action SHA pins. `main` now requires an up-to-date pull
request, conversation resolution, the eleven canonical CI checks, and three
CodeQL language checks; force pushes and branch deletion are disabled.
`release-promotion` and `github-pages` both require the sole maintainer's
deliberate approval with administrator bypass disabled. The documented
administrator branch bypass and self-review capability remain explicit
sole-maintainer residuals, not independent review. Optional secret-scanning
validity checks and non-provider patterns remain disabled and are not claimed.

Release-candidate run `30200529010` on exact main
`8ec093ada6c5342417b850793aac7d81b9667810` passed validation and all four
native builds, then stopped before draft creation when the artifact scanner
reported three absolute-path findings. Content-free inspection of only the
three POSIX artifact bundles localized all findings to opaque third-party
compiled extensions inside the two macOS candidate ZIPs. ZIP scanning had
applied human-readable developer-home detection to every member, unlike the
existing top-level binary policy. The corrected scanner applies that P1 check
only to text/sidecar members while retaining private-key, credential-canary,
and raw-context-canary scans for every binary member. The 36 exact downloaded
artifact files rescan clean locally, and adversarial regressions prove both
sides of the boundary. The release workflow has not been rerun and no draft
release was created.

Release-candidate run `30202272772` on exact main
`48815077544f9defb78d0e6b9c8022319888dfed` subsequently passed validation,
all native builds, scanning, attestations, and the exact 55-file draft
inventory. GitHub created unpublished prerelease draft ID `360008392` for
`v0.1.0-beta.1`, but immutable-release mode does not expose that draft through
the published-by-tag release endpoint and does not create the tag ref until
publication. The workflow therefore failed only when its post-create
verification assumed both routes already existed. The corrected source
enumerates authenticated releases, requires exactly one matching tag, binds
the numeric release ID, target commit, state, and exact asset names,
SHA-256 digests, and sizes, and uses release-ID REST operations through
publication. The unused-version preflight now also rejects any matching draft
from the full release listing before consulting the published-only tag routes.
Post-publication verification still requires the by-tag release, exact tag
ref, immutable state, and GitHub release attestation. That `360008392` /
`48815077544f9defb78d0e6b9c8022319888dfed` episode remains recorded history
and is no longer the live `v0.1.0-beta.1` release identity. The live
unpublished draft is numeric ID `367337056`, bound to source
`563a397d3095f1f45bb5814dfd39d9d7c4fab0bc` from release-candidate run
`31285545048`, candidate digest
`ba17eeec2e82d1ee1b0621f77024a03c78807496e8f1f07bfce38f0c42842ebe`, with 55
four-platform assets. It has not been retargeted, deleted, or published.

On 2026-07-26, the packaged first-run smoke was corrected so ADR-056 fail-closed
credential safety and the Windows windowed artifact can both be meaningful:
`scripts/smoke_packaged_first_run.py` deliberately isolates non-secret smoke
credentials with the null keyring backend **and** explicit
`ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE=1`, asserts
`credential_storage` is the insecure development file, and does **not** claim
real OS credential acceptance. Real Windows Credential Manager and supported
Linux Secret Service round-trips remain the separate
`--packaged-credential-acceptance` / `smoke_platform_acceptance.py` gates. The
retained macOS Keychain adapter is unsupported source code and is not a
`0.1.0-beta.2` receipt. Headless setup writes a redacted failure
report when setup exits non-zero. On smoke failure the disposable work tree
(credentials, vault, configs, binaries) is always deleted; only a content-free
allowlisted summary is retained under a separate diagnostics directory (phase,
return code, boolean artifact presence, error class, redacted message)—never
raw setup reports, dashboard tickets, tokens, client IDs, or subprocess
streams. Production installs still never enable plaintext credential storage
silently.

On 2026-07-26, source-side exact-candidate and publication aggregation gaps
for BETA-R01/R03/R04/R05/O01 and B-107/B-108/B-201/B-206 scaffolding were
closed without claiming a frozen release or fabricating receipts (ADR-059),
then re-audited against remaining false-pass paths: hosted matrix selection
requires the exact `.github/workflows/ci.yml` path (not suffix matches), every
required job must bind `run_id`/`head_sha` with completed success and no
duplicate/shadow names, incomplete job pagination fails closed, and durable
matrix evidence stores primitive job records with recomputed `ok`. Exact
receipt `artifact_digests` must name inventory-declared files with matching
digests (arbitrary safe keys cannot pass); BETA-P04 cannot be satisfied by
source-only provider preparation; inventory/candidate checksum sidecars and
full inventory schema are required; bool-as-int schema values are refused.
Human key custody, protected publication, offline signing, and public channel
smoke remain explicit blockers. Focused release/receipt/workflow tests, Ruff,
mypy, and docs checks pass on this worktree.

On 2026-07-26, candidate convergence added one deliberately small provider
execution surface to the desktop binary:
`--packaged-provider-acceptance` creates a fresh disposable Core vault, imports
one nonempty ChatGPT/Claude/Grok export through the production durable
import-operation path, requires loopback configuration and reconciled parser,
coverage, candidate, and outcome identities, emits only a bounded content-free
report, and verifies cleanup before returning success. It does not itself
satisfy BETA-P04: all three fresh real exports still have to run inside exact
downloaded candidates and bind their receipts to inventory-declared package
digests. The same convergence pass hardened the internal Core-store boundary
so direct and Relay secret-like candidates fail before payload/hash writes,
expanded high-confidence token/credential-URI detection, and redacts
secret-like reject/delete reasons. Fifty-eight focused provider, desktop,
pre-ledger, policy, and storage tests pass locally, with touched-source Ruff
and mypy clean. Full-suite and exact-candidate validation remain pending.

On 2026-07-26, narrow exact-lock corrections closed release-install holes
without broadening release engineering: the reviewed `uv.lock` contains hashed
`packaging`, `setuptools`, and `wheel`, and the locked installer fails closed
unless the complete dependency-closed build environment is installed before
`--no-build-isolation`; this specifically prevents wheel's `packaging>=24`
dependency from escaping pip's `--require-hashes` gate. `ensure_pinned_uv`
no longer network-bootstraps unhashed `uv` and requires the already pinned
`0.11.32` binary; and `dependency_audit.py` audits a frozen hashed export of
`uv.lock` (dev and packaging) with lock-installed `pip-audit==2.10.1` and
`--disable-pip` rather than re-resolving project ranges. The export audit
required bumping the dev pin of `pytest` to `>=9.0.3,<10` (locked `9.1.1`)
for PYSEC-2026-1845.

On 2026-08-08, newly published dependency advisories made the otherwise
unchanged hosted matrix fail closed. The reviewed Python runtime range now
requires `cryptography>=50,<51` and the frozen lock contains `50.0.0`, closing
PYSEC-2026-3552, PYSEC-2026-3553, and PYSEC-2026-3554. The dashboard lock now
contains `nanoid 3.3.18`, `postcss 8.5.26`, and `undici 7.29.0`, closing the
high-severity nanoid and undici advisories plus the reported PostCSS advisory.
The frozen Python audit and `npm audit --audit-level=high` both pass locally;
the replacement exact-SHA hosted matrix remains required before integration.

On 2026-08-21, the same fail-closed audit rejected the frozen development and
packaging environment after PYSEC-2026-3721 / CVE-2026-13346 was published for
`pip 26.1.2`. The reviewed development constraint now requires `pip>=26.2,<27`
and the frozen lock selects `26.2.1`. This is a build/audit dependency repair;
it does not change the beta.3 runtime dependency set or grant acceptance
receipt credit. The hosted matrix must pass again before PR 66 can merge.

That replacement matrix exposed a deterministic macOS Intel packaging
incompatibility rather than an application regression. Cryptography 50 has no
macOS x86-64 wheel, so the locked install built its Rust extension from source
against Homebrew OpenSSL. PyInstaller then collected Python's incompatible
same-basename `libssl.3.dylib`; packaged startup failed on the missing
`_SSL_get0_group_name` symbol. Reviewed macOS packaging installs now use
cryptography's documented static-OpenSSL source mode and immediately inspect
the installed Rust extension with `otool`, failing closed if either dynamic
OpenSSL library remains. Other OSes and non-packaging installs are unchanged.
Under the then-current four-platform plan the frozen package smoke still had
to pass on both native macOS architectures. ADR-086 later removed macOS from
product support; those source packaging checks remain contributor portability
work and create no Mac package, support promise, or beta gate credit.

On 2026-07-25, experimental pre-beta Core forwarding compatibility code was
tightened so Core-approved remote Edge `context_scopes` apply to every record
returned by direct fetch, search, or bootstrap. `*` explicitly grants every
record scope; an empty grant exposes only unscoped records. Out-of-grant records
are omitted without contributing to forwarded search counts or bootstrap
character totals. That experimental module remains for residual cleanup and
security regressions only; B-103 has removed the ordinary Core product routes
that would call it.

The integrated beta-safety implementation now also closes the source-level
parts of B-101, B-102, and B-104:

- direct secret-like proposals, batches, corrections/errors, and Relay queue
  submissions are refused before payload persistence; forget reasons are
  redacted before storage;
- refusal receipts retain only opaque UUIDv4 operation identity and closed
  detector/reason metadata, never an unkeyed payload hash or fingerprint;
- startup, export, and restore migrate and repair affected live Core ledgers,
  rebuild FTS, checkpoint WAL, enable secure deletion, and compact SQLite;
  synthetic tests scan database pages, WAL/freelists, temp state, diagnostics,
  and encrypted export/restore bytes;
- normal credential setup now requires Windows Credential Manager, macOS
  Keychain, or Linux Secret Service, while plaintext development storage
  requires explicit opt-in. Only the Windows and supported Linux backends are
  `0.1.0-beta.2` release gates; the Keychain adapter is retained source code;
- failed credential or managed-client configuration revokes any new principal,
  removes the credential, and restores the prior client configuration bytes;
- A-09 / B-102 client witness: only ATC-configured same-device Codex/Claude
  principals with the closed `witness:explicit_user_statement` grant (plus
  intentional local `admin`/`*`) may force applied current context from an
  explicit-user claim; authentication and `context:propose` alone stay
  tentative; omission/default false stays tentative; exact retry is
  idempotent and exact duplicates reinforce; clients cannot self-add the
  grant or smuggle origin/disposition/force fields; authenticated archive
  batches cannot re-label `provider_archive` material as witnessed without
  the grant; policy evaluation re-binds principal scopes from durable
  registration state so forged principal shapes cannot manufacture witness
  authority; Core-controlled importers (no client principal) remain a Core
  assignment path for normalized provider user-authored archive evidence;
  generic imports and provider synthesis stay tentative; Relay claims cannot
  attest direct user statements;
- B-102 chronological conflict safety: unkeyed archive-import
  preferences/goals/projects/decisions/workflows/constraints share a lineage
  only when a derived subject-only key matches (bounded preference values
  such as answer style and dark/light, plus a literal choice-before-`for`
  placeholder, are excluded from that key), ordered by explicitness then
  `observed_at`, with synthetic fixture
  `tests/fixtures/b102_chronological_conflicts.json` and reverse-order
  coverage. Unrelated same-kind archive statements remain independent current
  records. Direct unkeyed client goals remain independent. Kind-only collapse
  is not used. Decision reason/time/policy version remain inspectable without
  persisting credentials; residual truth is an explicit local trust grant, not
  cryptographic authorship proof—an authorized malicious witness client can
  lie.

Historical external backups and device remanence are not called repaired.
Real OS credential services, the secret boundary, and exact Codex/Claude
client artifacts still require exact downloaded-artifact acceptance on the
frozen candidate. Source-level BETA-P03/BETA-S02 evidence is content-free
unit/integration coverage, not fabricated exact-client receipts.

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
`research/competitor-intake/memory-systems-intake.v1.json`. During that intake,
no third-party source was cloned, installed, executed, imported, or copied.
Wave 2 later performed the separately governed temporary Hindsight static
clone described below. Neither activity adds a runtime dependency or
production claim.

## Executable Memory Lab M0 slice

ADR-043 implements the first bounded Memory Lab comparison without changing the
V1 runtime authority boundary. `allthecontext.memory_lab` defines versioned,
provider-neutral memory-object and read-only retrieval-adapter contracts.
`bench.memory_lab` runs no-memory and deterministic token-overlap controls plus
current ATC Retrieval V3 against the same sanitized, frozen task fixture and
reports task-level sufficiency, abstention, forbidden output, disclosure,
determinism, latency, storage, and adapter-declared model/token/cost usage.

The 2026-07-24 security hardening requires `context:read` for Core search,
including Retrieval V3 `as_of` historical search, so `context:status`
credentials cannot retrieve current, expired, or superseded context content
through `/v1/context/search`. The status endpoint remains available to those
credentials without granting access to context payloads.

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
through correction, privacy, recall-to-action, and closure. The specification
itself freezes evidence requirements; the separate Wave 2 section below
records a bounded longitudinal reference adapter and six-scenario result. No
external competitor result or production memory mechanism is implemented.

The completed Wave 2 integration passes documentation validation, Ruff, mypy
over 62 source files, 35 focused Memory Lab/governance/documentation tests, and
the full Python 3.12.10 suite with 560 passes and 4 host-limited symlink skips.
The complete suite passed from the coordinator worktree without a retry.

## Memory Lab Wave 2

ADR-044 and
`research/memory-lab/wave2-manifest.json` govern five fresh visible
GPT-5.6-sol worktree cells from coordinator commit `2bc0ad6`: the simple
baseline ladder, lifecycle E01, an optional isolated Hindsight supplier cell,
a fresh primary-source horizon review, and an independent novelty/falsification
review. Workers own non-overlapping scopes, cannot merge or push, and return
committed evidence to the coordinator for review and reproduction.

The wave is complete. Its integrated report is
[`docs/research/ATC_MEMORY_LAB_WAVE2_RESULTS_2026-07-23.md`](research/ATC_MEMORY_LAB_WAVE2_RESULTS_2026-07-23.md).
The coordinator reviewed every worker diff, sent lifecycle, scope,
fixture-freeze, and evidence-boundary defects back for amendment, and
reproduced both deterministic experiments with 20 repeats. No worker used
credentials, real personal context, the operator Core, a production
dependency, Docker, or a system service.

The baseline ladder preserves the original M0 fixture hash
`5601692ea305448f6b299c32725a93c73ca83ccee66f325e22cbcbedfa0cc68f`
and freezes separate Wave 2 controls at
`6dbf75db008b1be2d3db643b8dd19fe45f1a45c88121ac1ac3af16a0a0cd3c98`.
On seven objects and five retrieval tasks, the stable current-state log is the
only condition with success `1.0`, evidence-group recall `1.0`, and zero
forbidden output. Current ATC Retrieval V3 has success `0.8`, recall `0.9`, and
zero forbidden output. The stable log advances only to mutation, poisoning,
scale, action, and CAOS fixtures; the result is not production acceptance and
may be fixture-aligned. The local file ranker is explicitly not a reproduction
of programmatic action-model log inspection.

The bounded E01 slice executes 6 of the 18 specified longitudinal scenarios.
The in-memory governed reference passes 6/6, append-only search passes 0/6, and
no-memory passes 1/6. Removing authority, currentness/invalidation,
applicability, or purge closure produces a distinct failure. Coordinator review
added authoritative control-operation checks and terminal purge behavior
before integration. The report explicitly states that the fixture and
reference were co-designed and production Core semantics were not exercised.

The Hindsight result is
`not_executed_dependency_and_egress_gate`, not a benchmark score. Its official
MIT source was temporarily cloned at
`fa69b5b73b3b50bf5dcbae5bccbc7197de03692f`, statically reviewed, and removed.
No supplier package, model, container, provider, credential, service, or
upstream script ran. The dependency-free injected-client seam and fake tests
remain; a future real cell requires loopback-only binding, immutable local
model artifacts, and externally enforced default-deny egress.

The fresh horizon and novelty reviews move lossless programmatic logs,
online/off-policy/shift evaluation, write-admission poisoning, correction and
residue repair, and secure portability ahead of framework tournaments and
learned consolidation. Generic selective reminder and barrier-first repair
novelty claims are retired. The next ATC-native experiments are the Sealed
Projection Minimal Compiler, Record-Influence Barrier Closure, and Portable
Working-State Three-Way Repair.

## Memory Lab Wave 3

Wave 3 is complete under ADR-044/ADR-046/ADR-047 and the
[integrated result](research/ATC_MEMORY_LAB_WAVE3_RESULTS_2026-07-23.md).
Six fresh visible `gpt-5.6-sol` worktree tasks remained evidence-only; the
coordinator reviewed and integrated their scoped commits and reproduced the
five deterministic cells. The focused integrated gate passed 43 tests.
On Python 3.12.10, repository-wide Ruff and mypy passed, and pytest completed
with 603 passed and four expected Windows symlink skips.

The mixed result is preserved:

- B01's restricted programmatic log scored confirmatory CAOS `0.857143`
  against stable lexical `0.428571`, and its frozen combination scored `1.0`,
  but the bounded configuration remains killed under its preregistered
  external-operation gate. Internal work was not normalized, so this does not
  falsify general programmatic memory or reproduce PRO-LONG.
- O01 is held because the static memory-policy ranking was unstable across
  off-policy, online, and shifted regimes.
- P01 holds automatic durability: the non-production governed reference
  durably retained poison in 4/5 unique attacks even though later gates reduced
  influence and protected action to zero while preserving 5/5 clean utility.
- E01b passed six narrow production Core paths and recorded six
  unsupported/not-exercised lifecycle semantics; no complete-conformance claim
  is accepted.
- M2 is narrowly retained after 1,000 paired vaults × 20 repeats produced
  CAOS, sufficiency, and one-deletion minimality `1.0`, zero full-receipt pair
  differences, and mean disclosure `38.0` versus `70.1` full-authorized.
- MPBench is metadata-qualified at pinned Apache-2.0 revision
  `6886880a7c29625e0109e0ad91d0e095029f1577`, but no payload row was opened or
  executed. The paper-linked PRO-LONG repository remained unavailable.

Wave 3 advances Evidence-Compiled Memory as a research direction: complete
versioned evidence, conservative admission, pre-relevance sealed
authorization/currentness/applicability, bounded minimal context compilation,
current-version reread, action-force ceilings, use/outcome receipts, and
dependency-complete influence closure. Only the bounded M2 compiler contract
advanced; no production implementation, external benchmark result, or claim
that ATC has solved AI memory is accepted.

## Memory Lab Wave 4

Wave 4 is complete under ADR-048/ADR-049 and the
[integrated result](research/ATC_MEMORY_LAB_WAVE4_RESULTS_2026-07-23.md).
Four fresh visible `gpt-5.6-sol` worktree tasks started from immutable
governance-only commit
`f545c37157845f0bd402215719cb8c747b7fc21d`:

- F02 independent falsification and primary-source prior-art review, medium;
- M3 dependency-complete influence closure versus a full rebuild, high;
- E02 frozen production-Core semantic-gap conformance, high; and
- M1 observable assignment/use/outcome/invalidation receipts, medium.

The independent F02 oracle was committed at
`a866ad5b9d17a72d73d2dca4de4dd8be1e71ca9e` before M3 or M1 was
dispatched. The coordinator reviewed each scoped diff in the frozen promotion
order and reproduced 49 focused tests plus all three decisive reports.

The result is deliberately mixed:

- M3 is retained as a research contract and bounded optimization. All 15
  frozen attacks pass; every hard-safety count is zero; optimized and full
  rebuild agree; and the synthetic work control evaluates 120 descendants
  instead of 12,000 nodes.
- E02 records five `UNSUPPORTED` production semantics: generic epistemic
  role, project-and-domain applicability, dependency lineage, decay or
  retirement, and procedure preconditions or transfer. Exact same-identifier
  reuse after purge is `NOT_EXERCISED`.
- M1 is retained as a research contract. All 16 frozen attacks pass; every
  hard-safety count is zero; replay and aggregate reconstruction are exact;
  and unauthorized/inapplicable paired-vault differences are zero.
- Terminal purge is explicitly destructive privacy compaction, not ordinary
  append-only event admission. All affected identifiers leave declared
  inspectable ledger surfaces.

The next research direction is Evidence-Compiled Prospective Memory: a typed
event-contingent transaction that decides when a dormant intention is due
before disclosing minimal current evidence, then caps its permitted action and
records only observable use and outcome. It must beat a simple deterministic
scheduler and retain zero-tolerance authority, stale-influence, purge,
duplicate-action, and confirmation gates.

No worker changed production, accessed the operator Core or personal context,
downloaded competitor code, called an external model or provider, merged, or
pushed. No production promotion or claim that ATC has solved AI memory is
accepted.

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
- Windows per-user installer/shortcut/startup/uninstall path, Linux portable
  package path, and three-OS source-health CI. The retained macOS unsigned
  app/DMG/LaunchAgent path is contributor portability only.
- Deterministic Retrieval V3 with policy-first authorization, rebuildable UTC
  interval sidecars, weighted candidate-scoped FTS5, conservative task
  admissibility, safe diagnostics, and deterministic marginal context-set
  selection without a vector dependency.
- Offline-signed Ed25519 update metadata, immutable candidate assets,
  checksums, SBOM/provenance, and Windows transactional update/rollback code.
- The active beta-only `release-2026-b` public key is embedded in the package
  keyring. Its SHA-256 fingerprint is
  `40f95302dd6c0241dc7f639e29693c15e94c5ccae1357b927d039a7e6bf1cf8f`;
  the encrypted private half remains operator-controlled outside the checkout.
  `release-2026-a` remains tracked as revoked and cannot sign a release.
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

## Automatic context maintenance baseline

The following implementation is integrated on current main. Its local and
hosted source/package matrices are green; beta.6 has a frozen published release
identity, while broader certification-profile browser evidence remains open:

- Core migration `005_automatic_context_policy.sql` adds per-vault
  `automatic-v1` policy, observation origin/time/disposition/decision fields,
  record policy metadata, and observation-to-record evidence links. Existing
  approved and rejected rows map to applied and ignored compatibility state.
- `AutomaticMemoryPolicy` classifies server-originated observations as
  `applied`, `reinforced`, `tentative`, or `ignored`; ingestion observations are
  `staged` until session completion. Secret-like and highly sensitive content
  is ignored, sensitive applied context is forced to `local_only`, and
  non-explicit/inferred context requires corroboration.
- The deterministic sensitivity classifier now covers personally framed
  partner/residence, health (including HIV), and mortgage/loan statements while
  leaving unframed technical/general examples at `normal`. Unit, Core policy,
  and Core-forwarding regressions prove that detected sensitivity remains
  `local_only` and cannot cross the forwarding boundary. This phrase-based
  detector is conservative but not exhaustive; explicit first-person fictional
  text may still be localized, and unrecognized personal wording is not a
  guarantee of privacy classification.
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
- Provider extraction `provider-archives-v2` keeps specific durable kinds
  (name, preference, goal, project, decision, workflow, constraint) as
  current memory, retains broad first-person fragments as tentative
  observations when they are long enough, and skips questions, task-local
  prompts, and short utterances. Sensitivity classification conservatively
  marks health, relationship, location, financial, and identifier language.
  Complete sources can be rebuilt from the preserved raw blob; uncorrected
  automatic records from that source are reversibly withdrawn and history is
  kept.
- Context catalog search returns the exact post-policy `total` with cursor
  pagination, including pages beyond the 100-record bounded evidence pool,
  with signed, request-bound cursors and optional kind, sensitivity,
  confidence, and source-id filters.
  Cursors bind the normalized query/filter/page-size criteria and authenticated
  principal; malformed, negative, oversized, or mismatched cursors fail with a
  safe 422. Direct bounded `offset` requests remain available to MCP/CLI, and
  no next cursor is issued past the inclusive 100,000 offset bound. The
  dashboard shows that total, loads further pages, and opens without
  auto-selecting a record. `bootstrap_context` retains the separate bounded
  evidence path used for context compilation.
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
- repetition of the complete evidence set on the final frozen release commit.

Tentative-observation expiry/decay is intentionally not implemented in
`automatic-v1`. It is a possible later versioned-policy extension; tentative
state is already noncurrent and creates no user queue.

## V1 Edge UI and worker removal / B-103 Core-only isolation

- Edge navigation and setup were removed from the dashboard.
- First run no longer offers or opens hosted web/mobile setup.
- Dashboard status no longer calls the Edge API.
- Newly applied context exposes only `local_only` and `core_available`.
- Core no longer starts the legacy Edge network worker.
- The GHCR Edge workflow, Render templates, and Relay container CI job were
  removed from the V1 path.
- Ordinary Core HTTP routes for Edge enroll/prepare/deploy/connect/sync,
  secure-storage migration, owner-link, remote-client approve/revoke, and the
  old forget/decommission product paths return HTTP 404 tombstones.
- Ordinary Core mutations no longer trigger Edge sync. The CLI no longer
  exposes `sync`, `serve-relay`, or other ordinary Edge/Relay operation
  commands.
- Residual cleanup is isolated under `/v1/admin/legacy-edge` and
  `atc legacy-edge {status,decommission,forget}`. Decommission refuses when no
  residual paired Edge exists so the path cannot create a second authority by
  default. Status is local-only and does not open outbound sockets.
- Negative proofs cover removed routes/CLI, package entrypoints/web assets,
  process worker absence, ordinary-mutation network isolation, and default
  cleanup refusal (`tests/unit/test_core_only_boundary.py` and updated Core
  API integration tests).
- Same-version OTA acceptance: after a verified channel check that reports
  `current`, `accept_exact_candidate` reopens that already-verified equal
  version for download/install/health/rollback smoke without network I/O and
  without weakening signature, hash, platform, channel, or key checks. The
  admin route is `POST /v1/admin/updates/accept-exact-candidate`. This supports
  exact-candidate same-version transactional proof; it is not a public
  first-beta-to-successor N-1 receipt.
- Roadmap gate `BETA-S04` / work package `B-103` is implemented for the
  supported Core product surface. Exact packaged artifact matrix proof on the
  frozen release identity, release-key ceremony, and deliberate publication
  remain separate human-controlled gates.

## V1 provider/import boundary / B-105

- ChatGPT, Claude, and Grok now have versioned parser identities, a frozen
  fictional shape manifest, and closed recognized/excluded/skipped/
  unavailable/failed/unparsed coverage accounting. Unknown material keeps
  coverage incomplete rather than silently widening a provider claim.
- Core preflights the database volume, preserves and integrity-checks the raw
  source before parsing, and parses path imports from a reconstructed
  authoritative copy. Parser failure or cancellation retains inert raw bytes
  for no-upload retry and does not partially publish current context.
- Source-record progress, cancellation, parser-versioned retry, duplicate
  suppression, and a versioned deterministic non-sparse boundary-canary
  generator are implemented. The focused importer/provider slice passes 37
  tests on Windows Python 3.14.3; Ruff and strict mypy across 72 source files
  pass for the integrated source.
- Post-beta review found that direct-address task requests such as `I want you
  to write a haiku` could be classified as durable interaction preferences.
  Provider extraction now rejects task-local and adversarial instruction
  framing before preference classification; only explicit durable markers such
  as always/never/general preference phrasing retain automatic eligibility.
  Focused parser and Core-ingestion regressions prove those instructions are
  skipped and legitimate durable preferences still apply. Imported text and
  parser warnings remain content-inert.
- Acceptance diagnosis on 2026-07-26 found two real Windows-package defects:
  synchronous parsing could leave more than five seconds between durable
  operation updates after all bytes were committed, and boundary-canary-v1
  padding produced five malformed JSONL fragments outside closed coverage.
  Production parsing now runs a serialized durable unchanged-byte heartbeat,
  and boundary-canary-v2 uses JSONL whitespace padding while generic parsing
  closes every valid noncandidate value as skipped and every malformed value
  as unparsed. Focused regressions exercise a blocked synchronous parser and
  all five scaled-canary checkpoint records without inventing byte progress or
  treating unparsed material as complete.
- Exact Linux-package diagnosis under Ubuntu 24.04 WSL2 then observed seven
  operation-row heartbeat gaps beyond five seconds despite the background
  tracker. Operation-owned reprocess had serialized a source-metadata
  transaction before every authoritative operation transaction, so source
  telemetry latency could consume the observer budget. Operation-owned
  reprocess now writes periodic progress only to the queryable operation row;
  source-only imports retain source heartbeats, and explicit processing,
  complete, cancelled, and failed source writes retain terminal metadata.
  Adversarial regressions block the source sink while proving fresh unchanged-
  byte operation heartbeats, direct source-only liveness, monotonic phases,
  cancellation, and closed failure propagation. A rebuilt exact Linux
  candidate rerun remains required.
- Independent review of that heartbeat fix then reproduced two parser-
  reclassification merge defects: operations could complete with the deleted
  provisional `source_id` while `result.source.id` pointed at the canonical
  row, and merging into an already-complete canonical source could downgrade
  it to processing and re-ingest. Source now rebinds the durable operation to
  the canonical source on success, failure, and cancellation; preserves an
  already-complete canonical source without downgrade or re-ingestion; and
  allows same-status terminal rows to rebind only `source_id` when the tracker
  sink terminalized first. Focused adversarial regressions cover initial and
  retry success, failure, and cancellation after a real merge. Exact rebuilt-
  candidate evidence is still required and is not claimed here.
- Exact candidate `4257e40` then reproduced the remaining liveness failure on a
  qualified Ubuntu 24.04 x86-64 QEMU/WHPX target with the frozen 4-vCPU,
  8-GiB, ext4-on-SSD profile: the 2,000,000,000-byte import completed with
  correct hash, coverage, memory, and storage, but 15 of 17 unchanged-byte
  parsing heartbeat intervals exceeded five seconds and the maximum was
  10.196354 seconds. The operation heartbeat still used the full lifecycle
  transaction path, whose SQLite writer may wait for the 10-second busy budget
  and requires a FULL-synchronous commit. Operation trackers now route only
  unchanged-byte liveness through a bounded telemetry writer: it updates only
  `import_operations.updated_at`, uses WAL `synchronous=NORMAL` for process-
  crash-safe noncanonical telemetry, gives lock/busy attempts 250 ms, and
  retries without advancing the heartbeat clock. Status, phase, progress JSON,
  bytes, source, result, error, and terminal transitions remain on the original
  serialized lifecycle writer; non-lock SQLite errors still fail the import.
  A rebuilt exact Linux artifact must rerun the full journey before BETA-D01.
- A content-free WSL2 discriminator then sampled scheduler wake, Python lock,
  SQLite begin/update/commit, direct WAL reads, and API reads concurrently.
  The Python store lock produced a 5.395-second direct-row gap; bypassing only
  that lock reduced the direct maximum to 3.604 seconds despite bounded SQLite
  retries. API observations still reached 8.504 seconds while direct reads
  stayed below five, isolating observer delay. Stage timing later decomposed a
  4.011-second API response into 1.136 seconds before observer-worker execution,
  2.078 seconds in the authenticated read, and 0.796 seconds in response
  delivery; direct timestamps advanced within 2.875 seconds in that window, so
  faster writes alone were not treated as the observer fix.
  Timestamp-only touches now rely on bounded SQLite arbitration directly.
  Operation GET uses an async dependency plus a dedicated single-worker,
  persistent read-only/query-only WAL observer; active registration and
  operation state come from one freshest joined statement. A worker-local,
  process-keyed HMAC cache avoids repeated PBKDF but rechecks durable
  `revoked_at IS NULL` state on every poll and is cleared with the connection on
  application shutdown. This status route does not turn every high-frequency
  poll into a durable `last_used_at` activity write; all other routes retain
  ordinary authentication activity semantics. The dedicated observer worker
  and connection are recreated for each sequential application lifespan.
  Lightweight operation touches
  run at one tenth of the five-second budget for observer margin; full
  source-only metadata heartbeats keep the original one-quarter cadence.
  Authorization still precedes missing-operation disclosure, and generic
  internal read semantics are unchanged. With the final production interval selector, the exact
  2,000,000,000-byte WSL2 diagnostic completed with a 3.590-second maximum
  direct `updated_at` interval and 4.774-second maximum API-returned
  `updated_at` interval at unchanged committed bytes (3.306/4.344 seconds by
  monotonic receipt respectively). This clears the strict source diagnostic
  but is not frozen-target or packaged-candidate acceptance.
  Qualified QEMU and rebuilt-candidate evidence remain required.
- Exact candidate `628797d` then passed the qualified 2,000,000,000-byte
  straight journey but failed the cancel/no-upload retry's authenticated
  receive-liveness gate. A 1,338-record fsynced trace
  (`75adc4e24f0a2ec09c10c9e598d57fb6e21ca75e98a9d1fc33033e35f88a8ce7`)
  measured a 5.735102-second API receipt gap while the operation's durable
  `updated_at` gap was 4.936978 seconds and direct SQLite timestamp/receipt
  gaps were 3.701321/3.731520 seconds. API requests took as long as 3.428642
  seconds, first delivery lagged direct visibility by as much as 3.986875
  seconds, response headers could take 2.699178 seconds, and the final body
  could follow headers by another 0.846243 seconds. The exact retry remained
  functionally complete with clean integrity, foreign keys, coverage, chunks,
  and candidate identity, so this is scheduling/delivery loss inside the Core
  process rather than a durable-heartbeat freeze.
  Streaming JSONL operation imports now make a one-millisecond cooperative
  scheduling handoff at their existing one-MiB progress checkpoints. The
  change is limited to trackers with the operation liveness sink; source-only
  imports, auth/revocation, the joined WAL observer, response semantics, and
  durable progress remain unchanged. A deterministic adverse-scheduler
  regression previously held the authenticated observer until a roughly
  one-second parse completed; it now starts with more than 400 ms of test
  margin and separately bounds cached auth/joined SELECT and serialization.
  This is source validation only. A new immutable candidate must rerun the
  qualified Linux cancel/retry slice, followed by the still-unrun interruption
  slice; BETA-D01 remains open.
- Replacement candidate `7ffb1a4` passed the exact Windows x86-64
  2,000,000,000-byte straight import and idempotent repeat, including separate
  top-level operation timestamp and authenticated API receipt liveness, but
  failed the frozen cancellation gate. The cancel HTTP request returned while
  the operation remained processing, and durable `cancelled` was not observed
  before the strict five-second deadline. The run stopped before retry,
  interruption, export, or restore and issued no receipt; the candidate is
  invalidated.
  A deterministic production-path HTTP reproduction separated the clocks:
  before the fix, cancel intent returned in 0.021 seconds, observer-visible
  liveness timestamps continued to advance, durable terminal state missed a
  scaled 0.75-second bound, and the worker quiesced only after the controlled
  preserved-source copy completed at 1.560 seconds. The operation advertises
  `parsing` before reconstructing the preserved blob, but that bounded-memory
  copy had no cancellation checkpoint. API delivery, the read-only observer,
  the timestamp-only writer, and SQLite/Python write-lock acquisition were
  therefore not the cause.
  Preserved-source reconstruction now calls the operation tracker's
  cancellation check after every stored chunk (at most 8 MiB in production).
  The copy helper removes its partial target if the checkpoint raises. The same
  fsynced regression now measures 0.022-second HTTP return, 0.113-second durable
  acknowledgment, and 0.135-second worker quiescence; a separate regression
  proves partial-copy cleanup. No budget or response meaning changed. A new
  immutable candidate must rerun the complete Windows journey; source tests do
  not satisfy BETA-D01.
- Candidate descriptor
  `b00297d19080d0a3252a48fe5d7ac3ad78d5395909612f86eb2ef1f2e851bc16`
  on source `905efe5631ebf2fee77fafa5d8694f77df17b8bb` completed the
  Windows straight import and functionally idempotent repeat, but the repeat
  failed closed on a 5.448395-second durable top-level `updated_at` interval.
  Both endpoints remained at `parsing`, 2,000,000,000 committed bytes, and
  99 percent; direct receipt observations were 5.447142 seconds apart. No
  receipt was emitted and later slices did not run.
  A content-free production-path regression reproduced the reconstruction
  blind spot on untouched `905efe5`: first successful liveness touch arrived
  0.964490 seconds after copy start against a scaled less-than-0.4-second gate,
  while repeat completion and candidate identity remained correct.
  Operation-owned reconstruction now yields one millisecond after each
  at-most-8-MiB cancellation checkpoint. Source-only reconstruction does not
  pause, cancellation remains checked first, and no heartbeat or acceptance
  threshold changed. A new immutable candidate must rerun the full journey.
- Exact candidate source `65612cc` passed the corrected Windows straight,
  repeat, and cancellation timing slices, but its no-upload retry exposed a
  semantic progress regression before parser work. The preserved source was
  already fully committed, yet `_run_retry` created a zero-byte tracker and
  forced a `storing` lifecycle write before advancing it to the declared size.
  A direct SQLite observer could therefore see committed bytes and percent
  fall from the preserved boundary to zero and then return to full. The
  tracker now accepts a validated initial committed-byte position, initializes
  both its monotonic value and byte-emission watermark there, and retry starts
  at the preserved source's declared size. Its first forced phase write remains
  at full committed bytes and 99 percent; fresh uploads and source-only work
  retain their zero-byte default. Deterministic regressions prove both the
  tracker invariant and the production retry constructor. The candidate is
  invalidated; a replacement exact artifact must rerun the complete journey.
- Replacement exact candidate source 7afc46b passed one Windows
  2,000,000,000-byte straight/repeat probe, but an evidence-complete fresh
  straight run then failed closed on observer-visible top-level operation
  liveness. Authenticated API timestamps were 6.325973 seconds apart and
  direct SQLite timestamps were 6.253638 seconds apart at unchanged
  2,000,000,000 committed bytes. The operation still completed with its exact
  hash, five candidates, closed coverage, clean SQLite, and zero foreign-key
  violations; no D01 receipt was emitted and later slices did not run.
  The first attempt's corresponding maximum was 1.335203 seconds, proving an
  intermittent timing failure rather than a deterministic data failure.
- Exact source inspection found that operation-owned background heartbeats
  started only when reprocess entered parsing, after raw-blob promotion.
  A bounded source-level discriminator further showed the chunk-layout scan
  holding SQLite's writer slot for 1.253978 seconds: independent liveness
  touches failed while that transaction was open and succeeded 0.013890
  seconds after commit. Raw-blob finalization now scans chunk metadata in a
  deferred WAL read transaction while the Python lifecycle lock prevents
  competing source writes; the final complete-bit update remains a short
  immediate write. The operation heartbeat starts before staging and closes
  unconditionally on every upload exit. Focused regressions prove both
  pre-parse scheduling coverage and writer-free chunk validation.
- Replacement exact candidate source `4ab235d` completed two qualified Linux
  x86-64 2,000,000,000-byte straight imports with exact hash, 239 chunks, five
  candidates, closed coverage, clean SQLite integrity, and zero foreign-key
  violations, but both attempts failed the unchanged-byte operation-liveness
  gate. Attempt one measured a 5.918573-second durable top-level timestamp
  interval; attempt two independently measured 5.332539 seconds, with API and
  direct-SQLite receipt gaps also above five seconds. Both endpoints remained
  `processing`/`parsing` at 2,000,000,000 committed bytes. No receipt was
  emitted and later D01 slices did not run.
  Twenty-millisecond multiprocessing observers remained schedulable during
  standalone full source streaming, temporary reconstruction, and the complete
  4,134,533-line parse. Source inspection localized the remaining stall to the
  full-durability operation-row phase commit immediately after preserved-source
  reconstruction: its timestamp is generated before commit, readers retain the
  prior WAL row while that commit flushes, and the timestamp-only writer cannot
  enter SQLite's single-writer slot.
  Explicit nonterminal operation progress now uses a serialized WAL
  `synchronous=NORMAL` connection with the normal ten-second arbitration
  budget. It retains the Python lifecycle lock, semantic validation, and
  monotonic fields; only its power-loss durability differs from canonical
  state. Cancellation intent, preflight changes, errors, result payloads,
  terminal status, source/blob tables, and every other Core write retain FULL
  durability. The separate timestamp-only heartbeat remains fail-fast and
  lock-bypassing. Focused regressions prove connection policy and fail-closed
  routing. A pure-Python wheel from the corrected worktree then passed one
  straight-only run in the same qualified guest: durable timestamp gaps were
  at most 0.780195 seconds and API/direct receipt gaps were at most
  0.786998/0.800204 seconds, with exact source identity and coverage. That run
  is local source-artifact evidence only and emitted no receipt. The
  five-second gate is unchanged; a rebuilt immutable Linux artifact must rerun
  the complete D01 journey.
- B-105 is not accepted yet. Durable import-operation identifiers, lifecycle
  states, and cancellable chunk heartbeats are implemented in source
  (`import_operations.py`, migration `009_import_operations.sql`, Core admin
  routes, and the combined dashboard import flow). Exact-candidate proof of
  the frozen 5-second first-progress/cancel budget, privacy-safe current
  real-export receipts for all three providers, and the exact
  2,000,000,000-byte Windows/Linux import/export/restore receipts remain
  candidate-controlled acceptance work.

## Retrieval V3 integration

- The frozen V2 comparator is pinned to `70a4808` with checked fixture hashes
  and ranking fingerprints; production V3 cannot silently move it.
- Core, MCP, and CLI accept offset-aware `as_of` search. Current and historical
  resolution is UTC-normalized, deterministic across restart, and treats
  deletion/purge as terminal across restore.
- Weighted BM25 runs only over authorized and temporally eligible candidate
  IDs. Prefix fallback, candidate count, tokens, channel results, query length,
  and result count are hard bounded; FTS5 secure-delete is feature-detected.
- Default V3 search now applies a deterministic local-usefulness rerank after
  those hard boundaries: salient query tokens and bounded intent aliases are
  combined with field coverage, recency, confidence, availability, sensitivity,
  conflict, provenance, and actionability. A 32-record bootstrap pack cap and
  optional `pack_metadata` envelope report budget, omission, provenance, and
  truncation accounting without exposing ranking diagnostics.
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
- A synthetic retrieval-usefulness harness now scores current-fact recall,
  stale/conflict exclusion, withdrawn exclusion, sensitivity, provenance,
  budget compliance, and provider-facing packaging through public
  observation/search/bootstrap/get APIs on an isolated vault. It refuses the
  live Core data directory, verifies that the checkout-local runtime is loaded,
  exercises sparse intent and truthful budget metadata cases, and grants no
  beta-acceptance credit. The checked-in baseline is
  `bench/baselines/retrieval_usefulness_v1.json` (17 cases; all scorecard gates
  pass locally).
- This integration did not rerun hosted Python/dashboard/native matrices or
  exact client acceptance. The retained macOS source and historical evidence
  remain unsupported portability material; local retrieval measurements are not
  cross-platform performance claims.

## Post-merge Sources/Context contract gaps

The backend and dashboard contracts were inspected after the ordered merge. The
next dedicated reconciliation task should address these observed mismatches:

- Core `SourceOut.import_status` includes `cancelled`, and source metadata can
  carry the seven-key `closed_coverage` map plus the separate
  `source_terminal_reason`; dashboard `SourceRecord` omits `cancelled`,
  `IngestionCoverage` omits `closed_coverage`, and the Sources view currently
  renders only selected warnings rather than the full item/terminal split.
- Core exposes `/v1/context/truth/{record_id}` and `/v1/context/coverage` with
  Memory Truth status, conflict, evidence, and durable coverage fields. The
  Context UI currently calls legacy `/v1/context/search`, maps to its narrower
  `ContextRecord` type, and hard-codes returned rows as “Current”; it does not
  consume the truth projection or render those additional states.

These are recorded as contract gaps only. No backend or UI feature is invented
or papered over in this integration.

## V1 recovery/import/release integration reconciliation

- Packaged recovery/admin code is integrated across platform implementations:
  Windows and the retained unsupported Mac path have a version-matched console
  helper; Linux exposes the same stopped-Core modes on the console-capable main
  binary. The supported candidate includes Windows and Linux only. Candidate and CI
  native jobs run fail-closed `smoke_packaged_recovery.py` against built bytes
  (`--recovery-help` / doctor plus fiction export/restore/purge). Package reports
  record `recovery_surface` / `recovery_console_helper`; Windows OTA first-run
  smoke continues to journal and verify `AllTheContextRecovery.exe`.
- Durable import operations (migration `009_import_operations.sql`, runtime,
  combined browser+import dashboard) are required by package-resource diagnose,
  frozen diagnostics, and desktop artifact smoke. Source-evidence inventory
  remains content-free (no raw exports, 2 GB canaries, or personal data).
- Acceptance receipts still use existing content-free fields; exact
  downloaded-artifact browser/client/provider/2 GB/recovery operator receipts
  remain honestly pending and are not claimed by this integration work.

## Publication sequencing and public readiness

- The protected publication decision requires exactly the gate set named by
  the bundle's publication policy plus an explicit maintainer `approve` with
  `independent_human_review_claimed=false`. The initial lean policy is exactly
  L01, L02, S06, R01, R02, and R03 and requires four true human
  acknowledgements; certification remains the unchanged 20. Offline Windows
  x86-64 signing may begin only after that approve. `BETA-R05` public-download/channel smoke and
  `BETA-O01` live public-path and triaged launch-watch proof are
  postpublication gates; either is rejected if inserted into the
  prepublication bundle. Their eventual pass receipts require exact
  downloaded-artifact operational evidence bound to the candidate inventory;
  source-only receipts remain insufficient.
- Candidate source validation now fails closed unless stable support,
  known-issues, security-intake, and recovery guidance exists and remains
  linked from the README. This source readiness does not claim either
  postpublication gate.
- `SUPPORT.md` defines content-free public intake, private security routing, and
  launch-watch triage. `docs/KNOWN_ISSUES.md` records the accepted beta-scope
  P2/P3 impacts, workarounds, owner, and post-V1 follow-up. Missing lean gates
  cannot become limitations; missing certification gates remain explicit open
  certification evidence.
- `SECURITY.md` now matches the implemented Core-only tombstones and
  fail-closed protected-credential setup. Exact platform/client/package
  receipts remain required; documentation truth is not execution evidence.

## Remaining beta work

- Merge the ADR-096 compatibility repair so published-state validation accepts
  opaque `gh release view` GraphQL asset IDs without ever treating them as the
  numeric REST IDs used for mutation endpoints.
- Run the separately protected Windows beta-channel promotion, then execute the
  exact public download/channel smoke required for `BETA-R05`. Direct GitHub
  downloads are already live; channel promotion controls OTA discovery only.
- `BETA-O01` launch-watch evidence and the certification profile's remaining
  client, provider, browser, privacy, 2 GB, and recovery cells remain open.
  They are post-beta hardening work and are not relabeled by lean publication.
- A real signed first-published-beta-to-successor Windows update and rollback
  remains a successor gate. Keep mobile and remote-computer copy out of this
  beta; Core remains bound to `127.0.0.1` by default.

## Current evidence

- A real installed Codex CLI 0.144.0 process on Windows, using an ephemeral
  session, ignored user config/rules, a read-only empty workspace, a disposable
  loopback Core, Windows Credential Manager, and only supported MCP config
  overrides, applied and then retrieved a fictional explicit-user canary. A
  second Codex process auto-started the stopped disposable Core and retrieved
  the same canary. The first attempt exposed a concrete compatibility defect:
  with global `approval_policy = "never"` and no explicit server tool policy,
  Codex returned `user cancelled`; the documented managed-server
  `default_tools_approval_mode = "approve"` made the journey succeed without
  disabling the sandbox. This is source-runtime, installed-client evidence,
  not an exact downloaded-ATC-artifact BETA-P02/P03 receipt.
- Claude Desktop MSIX 1.24012.1.0 was launched against an isolated
  `CLAUDE_USER_DATA_DIR`. Its real packaged executable read
  `claude_desktop_config.json` from that root (a malformed synthetic probe
  produced the native settings error), and
  `--force-renderer-accessibility` exposed the real Windows sign-in UI through
  UI Automation. The isolated client stopped at **Get started** without an
  authorized user sign-in, so no Claude MCP call or BETA-P02/P03 claim was
  made. This build rejects Electron remote-debugging flags unless supplied an
  Anthropic-signed, path-bound CDP authorization token.
- The first post-fix full-suite replay exposed that
  `test_open_dashboard_starts_core_and_uses_authenticated_handoff` reached the
  production launch-repair path without an isolated Codex/Claude configuration
  root. On this host it created a real ATC backup and rewrote the live managed
  Codex block. The test now pins and asserts disposable paths, and the autouse
  harness assigns every test a temporary `CODEX_HOME`, temporary Claude config,
  and null keyring. The live file was not blindly restored while Codex was
  active; its timestamped backups and semantic, secret-free differences were
  recorded for operator review.
- The 2026-07-25 status snapshot recorded GitHub private vulnerability reporting
  as enabled while branch, dependency, secret, and code-scanning controls still
  awaited their own acceptance. Current `main` protection is strict and requires
  the eight canonical CI contexts plus three CodeQL contexts; conversations are
  required and force push/deletion are off.
- Historical roadmap baseline `1d44fdd80a3dcb32c580434924bb03c1e5291ae1` passed all nine
  Windows/macOS/Linux Python, Node 20/22 dashboard, and native-package jobs in
  [hosted CI run 30177362472](https://github.com/Martian-ux/All-The-Context/actions/runs/30177362472).
  This is baseline evidence, not the still-unfrozen beta release candidate.
- Current 2 GB import integration on Windows Python 3.14.3: 47 focused
  configuration, migration, storage, encrypted export/restore, importer, and
  CLI tests pass; Ruff, strict mypy across 68 source files, and the full
  662-test suite pass with four host-limited symlink skips. Exact commit
  historical exact commit `03a266f` passed all nine jobs in both hosted matrices. The final frozen
  release identity remains pending.
- The B-105 provider/import implementation plus coordinator lifecycle
  hardening passes Ruff, strict mypy across 72 source files, and 37 focused
  raw-first recovery, cancellation, preflight, provider-shape, path-provenance,
  and ingestion tests on Windows Python 3.14.3. This is source-level evidence;
  the initial operation-progress gap and exact candidate receipts above remain
  open.
- Release-CI integration replay on Windows Python 3.14.3: 46 focused packaging,
  updater, retrieval-gate, and MCP contract tests pass. The exact functional
  source at `f3496df` passed both complete hosted CI matrices.
- Historical Wave 4 coordinator worktree on Windows Python 3.14.3: Ruff passes;
  strict mypy passes across 68 source files; 49 focused Wave 4 tests pass;
  decisive M3/M1 reports reproduce byte-for-byte and E02 reproduces
  semantically; documentation links and `git diff --check` pass; and the full
  suite passes 652 tests with four host-limited symlink skips.
- Historical Wave 2 integration worktree on Windows Python 3.12.10: Ruff passes;
  strict mypy passes across 62 source files; the full suite passes 560 tests
  with four host-limited symlink skips; 35 focused
  Memory Lab/governance/documentation tests pass; documentation links and
  `git diff --check` pass. This includes the earlier automatic-policy,
  ACL/session, migration/restore, purge, Relay, and approval-free demo
  evidence plus the governed Wave 2 research harnesses.
- Current dashboard on Node 25.6.1: 27 tests, TypeScript checking, and the
  production build pass; `npm audit --audit-level=high` reports zero
  vulnerabilities. Packaged dashboard assets match the production build
  byte-for-byte.
- The new automatic-policy browser smoke and final frozen release identity
  remain pending.
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
- The historical packaged dashboard contained no Edge setup copy or
  `/admin/edge` request path. B-103 Core product-surface isolation is now
  implemented with unit/integration negative proofs; exact frozen packaged
  artifact re-proof remains open. The active beta claim continues to exclude
  mobile and remote-computer use entirely.
- GitHub release immutability is enabled, and GitHub Pages is configured to
  deploy only from Actions. The beta.6 release is public and immutable. Until
  the separate protected channel deployment completes, the canonical beta
  metadata URL may still return HTTP 404 and the built-in client reports
  `unpublished`; direct release downloads are unaffected.
- The Python 3.12 Windows frozen application passed resource discovery and the
  isolated first-run/install, browser handoff, MCP handshake, restart, startup,
  update-recovery, shutdown, uninstall, and cleanup smoke. The unsigned Windows
  package also passed its platform trust smoke.
- Historical implementation commit `05c7638` passed both its
  [push matrix](https://github.com/Martian-ux/All-The-Context/actions/runs/29969999250)
  and
  [draft-PR matrix](https://github.com/Martian-ux/All-The-Context/actions/runs/29970013608):
  Python 3.12 on Windows, Ubuntu, and macOS; native desktop/package acceptance
  on Windows, Ubuntu, macOS ARM, and macOS Intel; and dashboard checks on Node
  20 and 22.
- Historical Retrieval V3 integration commit `67dd11c` passed its
  [push matrix](https://github.com/Martian-ux/All-The-Context/actions/runs/29976224653):
  Python 3.12 on Windows, Ubuntu, and macOS; native desktop/package acceptance
  on Windows, Ubuntu, macOS ARM, and macOS Intel; and dashboard checks on Node
  20 and 22.

## Explicitly unclaimed

- No signed beta-channel pointer is claimed until protected promotion passes.
- macOS is not supported and no Mac package belongs to the public beta.
- No secure automatic mobile endpoint currently exists.
- No paid/native Windows publisher signing is planned for the community beta.
- The live SQLite vault is not application-encrypted at rest; portable exports
  are passphrase-encrypted.

## Provider conversation-list coverage correction (2026-08-22)

- Recognized provider conversation lists now retain closed accounting for every
  malformed or unknown entry instead of filtering it out. Valid siblings still
  import, while malformed non-mapping values and unknown mapping shapes are
  counted as `unparsed` and keep coverage incomplete.
- The accounting applies to provider wrappers, nested `data`/`export`/
  `account_data` wrappers, and root conversation arrays. Structural warnings
  contain no entry IDs, titles, or imported text; all-malformed provider lists
  remain recognized but incomplete.

## Final import-truth contract hardening (2026-08-22)

- Manifest-declared JSON `.dat` attachments now validate the complete bounded
  member before publishing candidates. A valid JSON array followed by trailing
  malformed bytes is therefore one `unparsed` item with zero recognized items
  and no partial candidate; valid JSON and line-oriented JSONL attachment
  extraction remain supported.
- Ordinary bounded ZIP `.json` members now use the same atomic validation
  boundary. A generic standalone text/CSV/JSON member that yields no candidate
  closes as intentional `excluded`/`skipped` logical coverage instead of
  disappearing. Provider containers and manifest/control members remain
  structural; the separate content-free `stats.archive_member_coverage` audit
  records its raw-member denominator without adding containers to item counts.
- Malformed JSON/text is one `unparsed` logical item; malformed CSV follows
  the same rule, while JSONL remains line-oriented.
- Provider container/control members remain structural in the raw-member audit
  when malformed, while their logical `unparsed`/`failed` outcome is counted
  exactly once in `closed_coverage`. Rejected provider-memory/profile values
  close as logical `skipped` items, including content-policy, inert,
  highly-sensitive, and over-limit rejection, without exposing their text.
- Standalone text, JSON, and CSV decoding is strict UTF-8; invalid bytes never
  become replacement-decoded candidates. CSV is supported atomically through
  both public archive entrypoints.
- Ordinary JSON roots now use bounded two-pass validation and consumption, so
  no unbounded document list or raw temporary artifact is retained. Enumerated
  ZIP safety failures return content-free member closure with each rejected
  file member in exactly one `unavailable` bucket and no rejected payload read;
  an unenumerable ZIP returns `zip_enumeration_failed` without fabricated
  member closure.
- Packaged-provider acceptance now validates its dict-level coverage through
  `CoverageReport`, preserving the closed seven-key schema, strict bounded
  integer counts, and fail-closed completion semantics for unavailable,
  duplicate, failed, or unparsed items.
- Direct bytes, filesystem JSON, and ordinary ZIP JSON members now share one
  bounded incremental UTF-8/raw-decoder contract: a 512 MiB JSON byte ceiling,
  128 MiB decoded item/document ceiling, and 128-level quote/escape-aware
  nesting ceiling. Validation completes before candidate consumption, so depth,
  recursion, or trailing-data rejection cannot publish partial candidates.
  Empty ordinary `[]`/`{}` roots remain one skipped logical item across all
  three entrypoints; empty provider containers remain structural and contribute
  no invented semantic item.
- Provider raw-container classification uses the canonical
  `conversations.json`/dated `conversations-YYYY[-MM[-DD]].json` names plus the
  frozen alternate basenames `chats.json`, `history.json`, and `messages.json`.
  Alternates require an explicit provider hint or an exact provider path
  component (`chatgpt`, `openai`, `claude`, `anthropic`, `grok`, `xai`, `x.ai`);
  valid provider-shaped neutral alternates can still become structural from
  parser statistics, while malformed neutral JSON remains an ordinary item.
- `CoverageReport.closed_coverage` normalizes omitted or partial input to the
  exact seven-key zero-filled map and still rejects unknown keys, non-integer,
  negative, and overflowing counts. This preserves older partial callers while
  making model validation and serialization exact.
- Focused synthetic regressions cover the atomic malformed-JSON attachment,
  valid attachment behavior, reconciler coercion/unknown/bounds cases, and
  completion consistency, bounded direct/path/ZIP JSON, depth/escape handling,
  alternate provider names, empty roots, and exact API/model coverage maps. No
  live or private export/Core data was inspected.

## Import Truth provider-terminal follow-up (2026-08-22)

- Provider-shaped empty roots and zero-message conversations now close exactly
  one logical item. A known provider closes an empty root or conversation as
  `skipped`; an identity-free provider-shaped empty or malformed entry closes
  as `unparsed` and keeps coverage incomplete. Structural provider containers
  remain outside the raw-member terminal buckets.
- The bounded JSON iterator carries explicit root versus root-array-item context
  with each streamed value. An empty object or provider wrapper inside a
  non-empty root array is therefore one `unparsed` terminal in either sibling
  order, while a standalone known-provider root remains one `skipped` terminal.
  This context is not filename-derived and does not materialize the root;
  direct bytes, filesystem paths, and ZIP members use the same accounting.
- Auto ZIP attachment discovery scans the allowed neutral alternate basenames
  for a bounded ChatGPT content signature before link inventory. Valid
  `messages.json`, `chats.json`, `history.json`, and `conversations.json`
  content matches explicit ChatGPT attachment links; malformed neutral content
  stays generic and cannot enable provider-specific attachment parsing.
- These are bounded synthetic corrections only. No live/private Core or real
  export was inspected, and no acceptance credit is claimed.

## Repository security convergence

- Exact-candidate tree scans now read committed blobs at the bound source SHA,
  and history scans inspect each unique blob reachable from that SHA rather
  than unrelated refs or marker-only diffs.
- ZIP members and complete private-key blocks are scanned through explicit
  member, expanded-size, object-count, and per-payload ceilings. Oversized or
  unreadable inputs fail closed. The existing Windows desktop directory
  (including the 97 MB installer and 29 MB recovery helper) and 61 MB direct
  ZIP directory scan clean with bounded streaming. The fresh eight-file
  `v1-engineering-1b894dd` Windows engineering set also scans clean. It is not
  built from this scanner commit or the still-unfrozen exact candidate; Linux
  tar.gz and macOS DMG contents are not claimed as inspected by this scanner.

## Memory Truth review-fix amendment (2026-08-22)

- Core migrations 010/011/012 are restart-safe after an interrupted `ALTER TABLE`,
  including statements preceded by SQL comments; the focused probe covers an
  unrecorded partial application and a second restart. Migration 012
  downgrades legacy/unbound rebuild rows to ordinary deletion barriers.
- Observation, correction, historical-restore, and portable-restore paths now
  recompute source/value identity keys from their durable identity-bearing
  fields. A V1-to-V2 update therefore cannot bypass an ordinary deletion
  barrier on matching archive reimport.
- Source-rebuild tombstones are minted only by the validated atomic
  `publish_source_rebuild` ceremony. The public withdrawal compatibility path
  fails closed; trusted rows bind the exact finished archive session,
  generation, and content-hash marker, while reapply checks source/session
  accessibility, in-progress metadata, stable identity, tombstone hash/version,
  and user-edit absence. Imported or copied markers remain ordinary barriers.
- Manual approval recomputes the candidate and canonical record key from every
  final identity-bearing override, including content, slot, source reference,
  kind, and structured value. Delete/reimport and retry/evidence regressions
  prove no replacement current record or duplicate link is created.
- Truth list/count projections page and count at SQL level without rebuilding
  global integrity state on reads. Page-scoped set queries use SQL row limits
  for superseded IDs, conflict IDs, and evidence; a query-count regression
  stays near-constant as the database grows. Focused Memory Truth, storage,
  migration, export/restore, Core API, provider-rebuild, purge, and relay
  checks pass locally; full-suite and hosted CI evidence remain outside this
  lane.

## Memory Truth restore-boundary correction (2026-08-22)

- Core migration `013_user_mutation_boundary.sql` adds an append-only,
  constraint-backed `context_user_mutations` ledger. Public/local restore,
  correction, availability, and explicit deletion paths write typed
  `local_user` rows; source rebuild checks the ledger instead of inferring
  edits from free-form version reasons. Original `archive_import` provenance
  remains truthful.
- Internal duplicate-import recovery and source-rebuild reapply paths do not
  write user-mutation rows. The ledger survives restart, portable export/restore,
  and purge; imported rebuild tombstones remain ordinary barriers. Legacy
  databases and pre-013 exports infer one deterministic `legacy_user_edit` row
  per affected record at most, and repeated restores remain idempotent.
- Focused Memory Truth/storage/migration/export checks cover benign and
  internal-looking restore reasons, restart/portable restore, append-only and
  tamper barriers, legacy/partial migration, and stable-ID rebuild behavior.
  This is an isolated review candidate; final acceptance, full-suite, hosted
  CI, live Core, and private-export inspection remain outside this lane.

## Memory Truth ledger-authority closure (2026-08-22)

- The 013 ledger now carries typed version evidence, bounded normalized actor
  material, canonical reason codes, and deterministic intent keys. Export
  restore validates those fields against same-package record history and exact
  vault/record/source relationships; a syntactically valid forged row is
  ignored and cannot block a valid source rebuild.
- Isolated recovery carries verified active destination-local mutation barriers
  transactionally with purge tombstones before import, idempotently covering
  ordinary, missing-record, and purged-record cases. An explicit restore of an
  already-current record creates one stable version-backed barrier per retry
  contract.
- Legacy inference uses source-backed typed state/version relationships and
  excludes source-free manual records and validated automatic reapplications.
  Version/source/tombstone reasons and persisted actor fields are content-free;
  focused adversarial probes cover secret-marker absence in SQLite, exports,
  and returned durable structures. Fresh reviewer acceptance remains pending.

## Memory Truth final-authority repair (2026-08-22)

- Schema 014 adds typed canonical user-action kind/key evidence to record
  history. New portable mutation rows require that evidence and a matching
  typed digest; a generic same-package `record_restored` version cannot grant a
  forged restore barrier. Legacy generic rows remain compatibility-only
  `legacy_user_edit` facts, and old packages are handled explicitly rather than
  treated as typed action proof.
- Migration repair recreates both append-only mutation triggers on every pass,
  including databases where migration 013 is already marked applied. The same
  restart/migrate pass repairs any missing schema-014 typed-action columns and
  recreates its partial unique index idempotently when migration 014 is already
  marked applied. Restore and isolated recovery counters now report only actual
  inserts, so repeated imports are ignored rather than reported accepted.
- Focused adversarial coverage includes zero-source-ledger evidence-only
  forgery with stable-ID rebuild reuse, genuine typed mutation round-trip and
  rebuild blocking, trigger loss/restart/idempotence, schema-014 partial-column
  and index repair, all typed local action paths, and duplicate import counts.
  This remains local review evidence only; encrypted package possession is not
  an external signature over a fully rewritten and re-encrypted archive.

## Continuous Capture repair and lease-authority correction (2026-08-22)

- Marker-present migration-015 repair now reads the packaged migration SQL and
  executes its complete statements transactionally, so interrupted startup
  repair and fresh migration share the exact capture table, constraint, and
  index definitions. Focused damage/restart probes cover every capture table
  and index plus representative malformed-row rejection.
- Run-owned writes now require an opaque typed run handle with exact source and
  lease-token binding, a live strictly-future lease, and a source still in
  `reconciling`. Renewal uses the same guard. Complete event identity is
  revalidated before application commit; stale, paused, revoked, abandoned, or
  replaced runs cannot write events, items, checkpoints, or lifecycle state.
- Leaving `reconciling` abandons active runs atomically, and expiry recovery
  degrades only a source that remains reconciling. Sink-expiry replay retains
  the same idempotency key. This is local beta.6 review evidence only; fresh
  independent acceptance remains required.
