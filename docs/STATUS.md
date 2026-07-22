# Project status

- Current phase: first vertical slice, release/CI foundation, release-candidate
  UX/backup repair, Retrieval V2 Phase 1, and Core memory integrity/purge are
  implemented. The secure Edge/mobile forwarding foundation, signed desktop
  update verification, automatic transactional Windows installation, and
  irreversible Edge purge parity are integrated.
- Completed: architecture and protocols; authoritative Core; restricted Edge;
  signed event replication; source, candidate, approval, correction,
  supersession, and tombstone lifecycle; nine MCP tools over STDIO and
  Streamable HTTP; generic import; review dashboard; encrypted export/restore;
  native first-run wizard with automatic timezone detection; automatic,
  reversible Codex and Claude Desktop configuration; separate least-privilege
  AI-client identities; validated connection status and disconnect/revocation;
  automatic upgrade repair; verified-Core challenge and one-use opaque browser
  handoff without credential copy/paste or durable browser credentials;
  managed MCP self-healing after Core stops; per-user startup; Windows Start
  Menu/Desktop launchers and uninstall registration; owner-gated hosted Edge
  enrollment, pairing, background synchronization, OAuth/PKCE MCP, recovery,
  outbound-only encrypted Core retrieval forwarding, remote-app approval and
  revocation, and terminal decommissioning; self-repairing source
  bootstrap; accessible responsive navigation; one-click complete encrypted
  dashboard backup with bounded temporary resources; demonstration and
  automated tests; Retrieval V2 Phase 1 with an
  offline deterministic 1k/10k benchmark, frozen V1 baseline, policy-before-
  ranking invariant, bounded lexical channels/RRF, improved context compiler,
  administrator diagnostics, bounded opt-in 50k profile, and passing V2 gates;
  cross-platform release workflows with strict offline-signed OTA metadata;
  and a fail-closed native updater with stable/beta preferences, bounded signed
  checks, verified staging, dashboard controls, an independent journaled
  Windows recovery helper, and binary/database rollback; and signed ordered
  Edge purge application with opaque
  replay barriers and resumable physical SQLite compaction.
- Current combined evidence on Windows 11 and Python 3.12: 251 Python and 18
  dashboard tests pass. Coverage includes forged-Core refusal, cross-Core browser-session
  isolation, terminal Edge races, bounded remote registration, permissions
  before pagination, credential/config cleanup, and real MCP initialize/list/
  call plus Core crash/restart recovery. Ruff formatting/lint, strict mypy,
  dashboard type checks/tests/build, npm audit, wheel/sdist build, Docker
  Compose parsing, a configured Linux Edge Docker container, and the eight-step
  offline Edge demonstration pass. A real Windows Credential Manager write,
  read, and delete round trip also passed. The
  rebuilt frozen artifact passes resource diagnostics and an isolated real
  install/private-browser-handoff/MCP/Core-restart/reopen/shutdown/uninstall
  smoke. The same packaged run injects a crash after binary replacement,
  resumes the journal, forces a post-migration health failure, restores the
  prior application, MCP adapter, updater helper, and SQLite database, and
  restarts Core. Uninstall preserves the vault while removing the app,
  shortcuts, registration, managed client credential, and temporary data. Signed-manifest
  tamper/revocation/downgrade tests, deterministic native-archive tests, an
  isolated wheel/sdist build with resource/private-key diagnostics, release
  JSON/workflow YAML validation, and Docker Compose parsing also pass. Docker
  Desktop was not running for a fresh local Edge image build in this release
  infrastructure validation.
- Integrated memory-integrity/purge evidence includes legacy migration, export
  resurrection, locked-file, insufficient-disk, restart, API authority,
  physical-content, replication-contract, Edge propagation, Edge lock/restart,
  resurrection rejection, and Edge DB/WAL/SHM byte-scan coverage. Ruff
  formatting/lint, strict mypy, docs checks, and the rebuilt combined dashboard
  pass.
- The integrated Edge/mobile slice additionally exercised sealed forwarding-request
  persistence, memory-only responses with DB/WAL/SHM byte scans, unknown or
  revoked identity and Edge-asserted admin-scope rejection, claim
  rotation/replay/restart, outbound-only
  polling, explicit per-operation SQLite handle release, and local
  offline/online mobile demonstrations. Real hosting and
  provider handshakes remain external gaps.
- OTA integration evidence includes updater unit/API/UI coverage, dashboard
  type/build/audit checks, wheel/sdist resource diagnostics, and documentation
  checks. The combined frozen Windows artifact includes the reviewed public-key
  resource and passes the isolated first-run/browser/MCP/Core-restart/reopen/
  shutdown/uninstall smoke, including Windows automatic-install capability and
  the packaged crash/resume/health-failure/database-rollback transaction.
- OTA hardening serializes every preference/state mutation, rejects unknown or
  32-bit architectures, sanitizes malformed transport lengths and persisted
  state, rejects cross-platform ZIP traversal and Windows alternate-data-stream
  paths, bounds orphan cleanup, and gives manual-required platforms an
  authenticated no-store package response that is re-verified without exposing
  private staging paths. On Windows, a separately packaged helper uses a strict
  per-user journal, cross-process lock, RunOnce recovery, a stopped-Core final
  backup, replacement diagnostics, a real one-shot loopback Core health check,
  and idempotent commit/rollback. macOS and Linux remain manual-required.
- CI authored: Python smoke/test and package/resource diagnostic jobs for
  Windows, macOS, and Linux; dashboard jobs for Node 20 and 22; hosted Edge
  image/config build; native desktop build, resource diagnostics, bounded
  packaged first-run/MCP smoke, deterministic versioned archives, checksums,
  and SPDX metadata. Draft-only candidate and digest-addressed GHCR workflows,
  a strict signed OTA manifest contract, offline signing/verification tooling,
  and stable/beta operator policy are present. The updater consumes only
  operator-configured HTTPS channel endpoints and a packaged reviewed public
  keyring; both production channel URLs and that keyring remain intentionally
  empty until release approval.
- Blockers: none for evaluating the vertical slice locally.

## Core memory-integrity and purge status

- Core migration 003, public models, API, CLI, import/export, and storage now
  carry optional normalized entity/attribute slot metadata. Model-inferred
  slots remain candidates until explicit approval. Current approved slot
  occupants produce deterministic, separate duplicate and conflict groups;
  no winner is selected automatically.
- Administrator-only record/source purge uses an exact target-bound phrase,
  one transactional logical scrub, opaque hash-free replay barriers, an ordered
  content-free `record_purged` event, and crash-resumable secure-delete/WAL/
  VACUUM compaction. Ordinary delete remains history-preserving.
- The bounded review/purge admin API and CLI are implemented. Dashboard group
  review is deferred to avoid collision with the Edge wizard. Relay migration
  0009 applies the opaque purge event transactionally, removes live/index/
  deletion/history-fingerprint state, and retains only an opaque replay barrier.
  It then records a pending compaction that is retried at startup and status
  checks. Core advances the replication checkpoint but reports Edge sync as
  degraded until that physical phase succeeds. Online `core_available`
  forwarding is implemented and does not change purge semantics.

## Retrieval V2 status

- Phase 1 is implemented. Hard policy selection precedes bounded phrase/AND and
  broad OR/BM25 channels; reciprocal-rank fusion uses bounded lexical and
  structured boosts with recency limited to tie-breaking. The existing MCP and
  default API response contracts are unchanged.
- A failing ranker spy and administrator-diagnostic regression prove denied,
  allowlisted-away, deleted, expired, and superseded records cannot enter
  scoring or explanations.
- Two deterministic 1k/10k runs passed all gates: exact Recall@5 `1.0` (V1
  `1.0`), MRR `0.777778` (V1 `0.666667`, +16.67%), multi-term empty rate `0.0`
  (V1 `0.5`), and zero policy violations. 10k warm p95 was `73.13693 ms` and
  `75.00416 ms`, below the `150 ms` gate.
- Context redundancy improved from `0.25` to `0.0`; frozen-gold coverage moved
  from `1.0` to `0.75` because one declared near-duplicate is intentionally
  suppressed.

## Explicitly unexercised or deferred

- Semantic retrieval, typo/general paraphrase recovery, and broader lexical
  vocabulary expansion remain deferred. Phase 1 uses a small inspectable alias
  table and has no embeddings or graph database. The benchmark is synthetic and
  timing evidence is local, not a production workload or cross-platform claim.
- Core and Edge live-database compaction cannot erase filesystem/provider
  snapshots, SSD remanence, external backups, user-copied exports, or other
  copies. macOS/Linux locked-file, disk-pressure, checkpoint, and VACUUM
  behavior is covered by portable design and tests but has not yet been observed
  on those operating systems.
- Temporal precision remains `0.5`; diversity and near-duplicate thresholds
  need evaluation on a larger sanitized judgment set before further tuning.

- The GitHub Actions matrix has not run because this local repository has no
  configured remote; macOS and Linux behavior is therefore designed and
  covered by tests, not claimed as observed on those operating systems.
- The Windows engineering artifact is unsigned. Windows publisher signing,
  macOS signing/notarization, and native Linux package metadata remain release
  work.
- No production release key has been created or configured. `release/keys.json`
  intentionally trusts no keys until an offline key ceremony and public-key
  review occur. Candidate workflows create drafts only; production promotion
  remains a human/offline operation.
- The packaged Windows engineering build exposes automatic install through its
  independent recovery helper and passed a same-version frozen transaction
  drill with injected crash and failed-health rollback. No production key,
  channel endpoint, Authenticode signature, or real signed N-1 release has been
  exercised, so this is not a public-production OTA claim. macOS and Linux
  continue to verify and stage downloads but stop in a precise
  manual-install-required state until their native cutovers are implemented and
  observed.
- The Edge uses SQLite in this slice. A PostgreSQL backend is an intentional
  hosted-deployment follow-up.
- Docker Compose parses successfully and a configured Linux Edge container was
  observed. This is not evidence for a production host or other Linux behavior.
- A real Windows Credential Manager round trip was exercised. macOS Keychain
  and Linux secret-service acceptance remain unexercised; persistence
  verification and the explicit local app-data fallback were also exercised in
  the packaged smoke.
- The Codex and Claude Desktop configuration writers were exercised locally,
  but those provider applications were not launched for an end-to-end
  handshake. Grok support also remains unimplemented.
- The provider-neutral hosted Edge OAuth/MCP path is implemented and exercised
  with SDK/TestClient integrations. No public repository/image or
  `ATC_EDGE_DEPLOY_URL` is configured, so the dashboard truthfully reports that
  deployment is unavailable in this development build. Publishing those assets
  is required before hosted Edge setup can be one-click. Claude custom
  connectors linked through its current web/Desktop flow can then be used from
  Claude mobile, subject to plan and workspace-admin policy. ChatGPT
  developer-mode MCP apps are currently web-only; real ChatGPT and Claude
  hosted handshakes remain unobserved.
- Edge provides the offline `always_available` projection and proposal queue.
  It also forwards authorized `core_available` retrieval through Core-initiated
  outbound polling, without exposing loopback Core. Real hosted/NAT observation
  remains pending; local integration tests exercise the same HTTP contracts.
  Forwarded queries are sealed to Core before Edge persistence, responses are
  memory-only, and Core requires a locally approved remote-client identity.
- New Edge deployments use an expiring public-key claim package. The Edge is
  inert before claim, generates durable replication credentials itself, returns
  them encrypted to Core, and revokes claim capability after acknowledgement.
- The local SQLite vault is not application-encrypted at rest; operators rely
  on operating-system account and disk protection. Portable exports are
  passphrase-encrypted.
- Dashboard restore is intentionally deferred: the release candidate keeps the
  existing deliberate CLI restore until a stopped-Core validation, rollback,
  verification, and explicit vault-cutover flow is designed and tested.
