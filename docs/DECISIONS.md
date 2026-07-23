# Architecture decisions

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

## ADR-005: Official MCP v1 during v2 transition

As of 2026-07-21 the official Python SDK documents v1 as stable and v2 as alpha,
with v2 stable targeted later in July. The dependency is constrained to
`mcp>=1.27,<2` and isolated in `mcp_adapter.py` for a controlled v2 migration.

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
size mismatch, or digest mismatch. Windows and macOS first-install publisher
warnings are an accepted usability tradeoff and must be disclosed rather than
bypassed or described as signed.

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
ADR-030 and ADR-031.

V1 exposes one authoritative Core. Desktop, mobile, and other-computer clients
all connect directly to it, so Core must be online for access away from the
installation computer. The product does not deploy or require a hosted Edge,
cloud replica, Render account, GHCR runtime image, provider bill, or other
third-party context service.

Core continues to bind only to `127.0.0.1` by default. Removing Edge does not
authorize automatic LAN/public exposure: secure direct-Core mobile access
requires explicit device pairing, encrypted transport, revocation, discovery,
and recovery acceptance first. Until that work is complete, the UI states the
limitation rather than offering an unsafe shortcut.

The `always_available` schema value and experimental Relay modules remain
temporarily for import/history compatibility and safe cleanup of engineering
setups. Newly applied context uses only `local_only` and `core_available`; Core
does not start the Edge network worker; deployment workflows/templates are
removed from V1. Deleting dormant protocol code is a later cleanup after
compatibility and migration requirements are known.

## ADR-033: Provider history is preserved completely but evaluated selectively

**Status:** accepted 2026-07-22.

Initial memory bootstrap uses user-requested account exports, not provider API
keys, browser scraping, account credentials, or a recurring cloud connection.
Core stores the accepted ZIP/JSON/JSONL/Markdown/text source byte-for-byte in
its content-addressed local source store. HTTP uploads and SQLite BLOB writes
are chunked; ZIP entries are read in place; root conversation arrays are
decoded one conversation at a time. The default raw archive limit is 512 MiB,
with an operator-configurable ceiling below SQLite's safe BLOB limit, and
expanded text/entry/compression/conversation bounds remain mandatory.

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

**Status:** accepted for active research execution 2026-07-23; no Wave 4
result or production change is accepted.

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
