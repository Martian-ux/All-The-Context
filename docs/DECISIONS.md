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

## ADR-017: Offline-signed immutable OTA metadata

Release candidates are native, versioned artifacts built from a full commit
SHA. GitHub may build an unpublished draft and attach checksums, SPDX metadata,
and provenance, but it never receives the Ed25519 release private key. An
operator signs the strict v1 manifest offline after verifying the candidate;
only reviewed public keys live in the repository. Mutable channel pointers may
select a signed manifest, but executable URLs must be HTTPS, versioned, and
must never resolve through `main` or `latest`. Downgrades are rejected. Desktop
update download and installation are explicitly deferred to a separately
reviewed implementation.

## ADR-018: Edge images are release- or commit-addressed

The hosted Edge image is published to GHCR only from a published release or an
explicit full commit SHA. Every deployment record uses the returned OCI digest;
`latest` is not a deployment input. OCI metadata, BuildKit provenance/SBOM, and
GitHub provenance accompany the image. Making the package public and creating
paid hosting remain explicit operator actions.
