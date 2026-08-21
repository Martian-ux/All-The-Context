# Beta release acceptance

This runbook defines the evidence required to call an All The Context build a
public beta. It does not authorize publication, spending, or creation of an
offline private key.

## Version identity

- Tags, update manifests, and assets use SemVer such as `0.1.0-beta.3`.
- The release candidate records one exact 40-character commit and every
  artifact digest.
- Published assets are immutable; changed bytes require a new version.

## Publication profiles

Every receipt bundle names one machine-enforced publication profile. Profiles
are exact gate sets, not labels that can reinterpret old evidence.

- `certification_v1` is the complete 20-gate prepublication contract described
  by Gates 1 through 6 and the certification matrix in `ROADMAP_TO_V1.md`.
  Existing ledgers and attempts remain under this profile.
- `lean_public_beta_v1` is the initial usable-beta contract. It requires exactly
  six passing receipts on one candidate: `BETA-L01` (Windows 11 first run),
  `BETA-L02` (Ubuntu 24.04 first run), `BETA-S06` (security and supply-chain
  scans), `BETA-R01` (green locked source), `BETA-R02` (human signing-key
  custody/recovery), and `BETA-R03` (immutable inventory/SBOM/provenance).

L01 and L02 require exact downloaded-artifact evidence for install or extract,
startup, one healthy Core listener on `127.0.0.1`, one supported local client
connection, restart persistence, and cleanup without inspecting private user
content. They do not claim that the broader client/provider/browser/2 GB/data-
recovery matrix passed. Those cells remain open certification work and must be
disclosed as a known P2 beta limitation, never marked skipped, waived, or pass.

Lean approval fails closed unless the human maintainer explicitly acknowledges
that certification is incomplete, macOS is unsupported, packages are unsigned
community builds, and known issues were reviewed. Zero P0/P1 limitations is
still mandatory. `BETA-R02` and the separate human approve decision remain
mandatory, so the lean profile does not authorize signing or publication by an
AI system.

## Gate 1: integrated source

On the exact release commit:

1. Ruff formatting/lint, strict mypy, Python tests, and dashboard checks pass.
2. The exact hosted source-health matrix passes: Python 3.12 on
   Windows/macOS/Ubuntu, dashboard on Node 20/22, and package regression jobs
   on Windows, Ubuntu, macOS ARM64, and macOS x86-64. The retained macOS jobs
   protect cross-platform source from accidental breakage; they do not create a
   support claim or a macOS release asset.
3. Dashboard production build and dependency audit pass.
4. Reachable history and artifacts contain no private key, credential, personal
   context, or developer-machine path.
5. No V1 workflow publishes an Edge image or deploys a runtime service.
6. Documentation, generated MCP instructions, UI copy, demo output, and status
   use observation/current-context terminology and do not instruct the user to
   review routine memory.

## Gate 2: installable desktop artifacts

- Windows fresh per-user install starts without a terminal, creates persistent
  shortcuts, connects selected local clients, survives restart, passes the
  same-version transactional replacement/rollback smoke, and uninstalls without
  deleting the vault.
- Linux portable package runs without Docker or root and passes the same tests.
- Initialization, startup, ingestion, automatic policy evaluation, retrieval,
  export, shutdown, and restart are observed on every supported target OS.
- Mandatory targets are Windows 11 x86-64 and Ubuntu 24.04 LTS x86-64 GNOME
  with a working Secret Service/GNOME Keyring backend. Freeze the exact Windows
  build and supported client versions; a missing certification target receipt
  leaves certification open. Under the lean profile, missing L01 or L02 leaves
  the candidate in draft. Other Linux distributions/desktops are experimental.
  macOS is excluded from the product support table before candidate freeze: no
  Mac cell is passed, waived, skipped, or represented by a release asset.
- Every artifact ships a version-matched recovery/admin helper or native mode
  that exposes installed help, stopped-Core restore, and deliberate purge
  without Python, a source checkout, or developer tooling.

## Gate 3: one-time setup and automatic context

- On each supported client/OS cell, that client connects once, preserves
  unrelated configuration, and survives Core/app restart. The supported cells
  are current stable Codex on Windows and Linux and current stable Claude
  Desktop on Windows. Linux Claude beta is excluded by the frozen stable-only
  support wording unless a stable supported client is deliberately added
  before candidate freeze.
- A user states a durable preference in a connected client; Core returns an
  `applied` disposition and another session retrieves it without an approval
  call or dashboard visit.
- An exact retry replays the same decision; an exact duplicate reinforces the
  existing record without duplicate current context.
- Model inference and provider-synthesized memory remain tentative and absent
  from current retrieval unless eligible explicit evidence corroborates them.
- Provider adapters exclude assistant, system, tool, and attachment roles;
  generic or instruction-bearing imports remain tentative; secret-like
  material is refused or irreversibly redacted before its payload enters
  durable observation history; imported text never executes as instructions.
- Refused content leaves no unkeyed hash, deterministic fingerprint, prefix, or
  other guessable verifier. Existing-data repair compacts/rebuilds the live
  store and synthetic canary scans cover SQLite pages/freelists, WAL/journal,
  FTS, temp state, diagnostics, and new exports. Historical external backups
  that cannot be repaired are explicitly retired or warned about.
- Only an ATC-configured same-device Codex or Claude principal explicitly
  granted the direct-user-statement witness class may attest that text came
  directly from the user. This accepted local trust grant is not cryptographic
  proof, authentication alone does not confer it, a client cannot silently
  claim stronger force, and contradictory unattested or unkeyed historical
  statements fail conservatively.
- An explicit correction changes current context before the successful
  operation returns and preserves the prior version.
- Reversible forget/delete and restoration work without irreversible purge.
- Deliberate purge is reachable only through the packaged administrator
  surface, requires unmistakable confirmation, and passes non-resurrection
  checks; scoped AI clients cannot invoke it.
- Every decision exposes provenance, `decision_reason`, `decided_at`, and
  `policy_version`; Activity inspection is optional and has no pending inbox.
- Scoped clients cannot select a disposition, write current records directly,
  restore deleted records, or invoke purge.
- The long-lived Core credential never enters browser URLs, storage, console,
  or referrers. The one-use handoff ticket expires and cannot replay. Any
  accepted short-lived browser capability is scoped, revocable, session-only,
  and cleared when the session terminates.

## Gate 4: archive import and lifecycle integrity

- Archive imports are bounded, inert, idempotent, and resumable.
- ChatGPT, Claude, and Grok exports acquired after parser freeze and within 30
  days of acceptance each pass a privacy-safe receipt. Every receipt is
  nonempty, records a content-free structural fingerprint, exercises the
  frozen fictional canary shapes, and reconciles all input to recognized,
  excluded, skipped, unavailable, or failed counts with closed reasons.
  Unknown/unparsed material is a visible coverage warning, not success.
- The frozen scale profile is 4 logical cores, 8 GiB RAM, local SSD, and
  16 GiB initially free. Core plus import-worker peak RSS is at most 1 GiB;
  incremental import storage is at most four times raw size plus 1 GiB.
  Preflight requires the greater of that bound or measured high-water plus 25
  percent. Progress starts within 5 seconds and advances at least every 5
  seconds or 64 MiB, remains within one 8 MiB committed chunk, and reaches 100
  percent only after integrity/atomic publication. Cancel is acknowledged
  within 5 seconds and quiesces safely within 30 seconds. Interrupted retry
  needs no re-upload and creates no duplicates. Import, source-inclusive
  export, and isolated restore each finish within 60 minutes. Candidate
  results cannot relax these budgets.
- A deterministic physically allocated/non-sparse fixture with a known
  generator, SHA-256, chunk count, nonzero parse/publication counts, and
  interruption checkpoints succeeds at exactly `2,000,000,000` bytes on the
  Windows x86-64 and Linux x86-64 candidate artifacts. A
  `2,000,000,001`-byte source is refused
  deterministically. Each receipt meets the predeclared budgets and covers
  complete source integrity, atomic publication, packaged source-inclusive
  encrypted export, stopped-Core restore, and retrieval/integrity.
- Observations remain staged and absent from retrieval until successful
  `finish_ingestion`.
- Completed imports automatically report applied, reinforced, tentative,
  ignored, skipped, and coverage counts without a review step.
- A failed or interrupted import changes no current context and can retry from
  the preserved raw source without duplicate observations or decisions.
- Correction, replacement, tombstones, permissions, validity, FTS retrieval,
  export, restore, automatic-policy migration, and policy-version replay pass
  their integration/security suites.
- Newly applied records use only `local_only` and `core_available`; the product
  does not advertise the legacy `always_available`/Edge path.

## Gate 5: no hosted, mobile, remote, or Edge runtime

- First run and the dashboard never ask for a hosting account, provider bill,
  deployment URL, Edge credential, or cloud replica.
- Ordinary Core startup and mutation cannot construct or start an Edge network
  worker.
- Supported artifacts expose no callable Edge enrollment, deployment,
  connect/sync, remote-client-management, or routine Relay operation path.
  Legacy decommissioning, if retained, is isolated from normal Core/API/CLI
  operation and cannot create a second authority.
- Core binds to `127.0.0.1` unless the operator explicitly chooses otherwise.
- Relay, if explicitly exercised for compatibility, queues observations and
  accepts signed Core projections only; it never creates current context.
- Every beta artifact, setup path, and support document says that the supported
  journey is same-device only and makes no phone or remote-computer access
  claim.
- Future direct-Core pairing, authentication, encrypted transport, revocation,
  restart persistence, and offline failure behavior are post-V1 work and
  cannot be credited toward this beta gate.

## Gate 6: release supply chain and OTA

- GitHub Release assets are produced from the frozen commit with SHA-256,
  SPDX SBOM, and provenance.
- Native packages are labeled **unsigned community build**.
- OTA metadata is signed outside the repository with an operator-controlled
  Ed25519 key; only reviewed public keys enter source control.
- First-public-beta publication requires the reviewed update client, two
  restore-tested release-key backups in distinct failure domains, one
  candidate-bound `BETA-R02` source receipt after those restore tests, the
  remaining unique receipts selected by the bundle's publication profile, an explicit maintainer
  `approve` with `independent_human_review_claimed=false`, protected immutable
  publication, and a signed Windows x86-64 channel. The private key, password,
  and backup location never enter the repository, Actions, an AI system, a
  shell argument, or an environment variable. A real update from the first
  published beta to its successor then proves success, interruption recovery,
  failed-health rollback, and vault preservation before a later beta or stable
  graduation. A same-version engineering smoke is required for the first
  published beta but is not N-1. Linux remains a direct human-install package
  until equivalent native rollback is observed; macOS has no beta release
  package or channel.

## Human release decision

Before publication, record the commit, CI and draft-release URLs, asset and
manifest digests, public-key fingerprint, real-platform results, unsigned
warning acknowledgement, and explicit release approve/reject decision against
exactly the unique pass receipts selected by the named publication profile. The
initial lean beta therefore enumerates exactly six; certification enumerates
exactly 20. The decision must enumerate every selected receipt ID exactly once and no postpublication
receipt, and it must retain `independent_human_review_claimed=false`. Offline
Windows x86-64 signing may begin only after that approve. This is a
software-release decision, not a context-review queue. The sole human
maintainer may use AI-assisted review but must not call it independent human
approval.

GitHub private vulnerability reporting must remain enabled, the public security
policy must route sensitive reports to it, and source validation must require
linked support, known-issues, security-intake, and recovery guidance before a
candidate is built. Those source prerequisites do not satisfy `BETA-O01`.
Missing gates or acknowledgements from the selected profile leave the release
as a draft. Missing certification gates remain visible open certification work
even when a lean beta is approved.

`BETA-R05` public download/channel smoke and `BETA-O01` live public-path and
triaged launch-watch proof are recorded only after the immutable release exists.
Both require exact downloaded-artifact operational evidence bound to the
candidate inventory; source-only receipts cannot satisfy them. The
prepublication gate rejects either receipt if it is inserted into the approval
bundle.
