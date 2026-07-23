# Architecture decisions

## ADR-001: Core is the sole authority

Relay stores a projection and proposal queue only. Application events, not
database files, cross the boundary.

## ADR-002: SQLite-first storage

Core uses SQLite/FTS5. The first Relay slice also supports SQLite for a complete
local/integration path; its storage boundary permits PostgreSQL in hosted
deployment without changing event contracts.

## ADR-003: Review-first approval with policy hook

Extracted/model-inferred candidates require review by default. The schema and
client scope model reserve deterministic auto-approval for explicit low-risk
statements, but it is off until enabled by the user.

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

## ADR-012: Edge proposals are encrypted transport, not canonical context

An OAuth client with proposal scope may enqueue a bounded AES-GCM transport
envelope while Core is unavailable. The queue is capped by count and bytes,
expires after 30 days, and is scrubbed after Core acknowledges import or
rejection. This is an explicit transport exception to the readable Edge
projection: it never becomes approved context at Edge, and it is not
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

Phase 1 keeps the Core as the only canonical authority and decomposes retrieval
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

Entity and attribute keys are optional candidate metadata, normalized only for
deterministic grouping. They do not create canonical facts. Explicit approval
copies or edits the pair, after which Core derives duplicate groups for matching
normalized values and conflict groups for materially different values in the
same current slot. Both can coexist. Groups are review aids, never automatic
merge or winner authority.

Deletion and purge are distinct. Delete preserves the canonical row, versions,
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
user-approved Core-local remote-client mapping, and re-authorizes canonical
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
setups. New UI approvals offer only `local_only` and `core_available`; Core does
not start the Edge network worker; deployment workflows/templates are removed
from V1. Deleting dormant protocol code is a later cleanup after compatibility
and migration requirements are known.

## ADR-033: Provider history is preserved completely but promoted selectively

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

Raw completeness and canonical memory are separate promises. Every recognized
message contributes to aggregate coverage, but only user-authored durable
statements and dedicated provider memory/profile fields can create candidates.
Assistant, system, tool, and attachment content remains inert raw evidence.
Provider-synthesized memory is lower-confidence and is not marked as an
explicit user statement. No imported candidate bypasses review.

Each source records provider, format, parser version, statistics, warnings, and
`processing`/`failed`/`complete` status. The source ID and parser version key the
ingestion session; source hash, parser version, and batch ordinal key candidate
batches. A retry reopens the preserved BLOB through a bounded temporary file
and replays completed batches exactly, allowing one-click crash recovery
without another upload or duplicate candidates. A future learned extractor can
use a new parser version against the same raw source without changing this
authority boundary.

## ADR-034: Packaged beta updates have a trust-gated default channel

**Status:** accepted 2026-07-22.

A frozen Windows x86_64 prerelease whose embedded keyring contains an active
beta key uses the canonical project Pages manifest endpoint and selects beta on
first run. A legacy persisted stable selection moves to beta only when stable
has no configured endpoint and beta does. Source runs, unsupported targets, and
packages without an active beta key infer no endpoint. Environment variables
remain explicit overrides for forks and acceptance environments.

GitHub's immutable versioned release download URL responds with a temporary CDN
redirect. Artifact download may therefore follow exactly one HTTPS redirect,
and only from a structurally versioned `github.com` release-asset path to the
exact `release-assets.githubusercontent.com/github-production-release-asset/`
origin and path prefix with a signed query. Manifest fetches, different
origins/paths, and further redirects remain refused. Redirect acceptance does
not confer trust on bytes: the already verified Ed25519 manifest's exact size
and SHA-256 are still required before staging succeeds.
