# Project status

- Current phase: first vertical slice, release/CI foundation, release-candidate
  UX/backup repair, and Retrieval V2 Phase 1 are implemented. Signed desktop
  updates and seamless Edge/mobile access are under active implementation.
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
  remote-app revocation, and terminal decommissioning; self-repairing source
  bootstrap; accessible responsive navigation; one-click complete encrypted
  dashboard backup with bounded temporary resources; demonstration and
  automated tests; Retrieval V2 Phase 1 with an
  offline deterministic 1k/10k benchmark, frozen V1 baseline, policy-before-
  ranking invariant, bounded lexical channels/RRF, improved context compiler,
  administrator diagnostics, bounded opt-in 50k profile, and passing V2 gates;
  and cross-platform release workflows with strict offline-signed OTA metadata.
- Prior local integrated evidence on Windows 11 and Python 3.12: 155 Python
  tests and 15 dashboard
  tests pass. Coverage includes forged-Core refusal, cross-Core browser-session
  isolation, terminal Edge races, bounded remote registration, permissions
  before pagination, credential/config cleanup, and real MCP initialize/list/
  call plus Core crash/restart recovery. Ruff formatting/lint, strict mypy,
  dashboard type checks/tests/build, npm audit, wheel/sdist build, Docker
  Compose parsing, a configured Linux Edge Docker container, and the eight-step
  offline Edge demonstration pass. A real Windows Credential Manager write,
  read, and delete round trip also passed. The
  rebuilt frozen artifact passes resource diagnostics and an isolated real
  install/private-browser-handoff/MCP/Core-restart/reopen/shutdown/uninstall
  smoke; uninstall preserves the vault while removing the app, shortcuts,
  registration, managed client credential, and temporary data. Signed-manifest
  tamper/revocation/downgrade tests, deterministic native-archive tests, an
  isolated wheel/sdist build with resource/private-key diagnostics, release
  JSON/workflow YAML validation, and Docker Compose parsing also pass. Docker
  Desktop was not running for a fresh local Edge image build in this release
  infrastructure validation.
- Current memory-integrity/purge worktree evidence on Windows and Python 3.14:
  167 Python tests pass, including legacy migration, export resurrection,
  locked-file, insufficient-disk, restart, API authority, physical-content, and
  replication-contract coverage. Ruff, strict mypy, docs checks, and wheel
  resource diagnostics also pass. Python 3.12 remains the project target; this
  slice did not rerun the dashboard because dashboard sources were untouched.
- CI authored: Python smoke/test and package/resource diagnostic jobs for
  Windows, macOS, and Linux; dashboard jobs for Node 20 and 22; hosted Edge
  image/config build; native desktop build, resource diagnostics, bounded
  packaged first-run/MCP smoke, deterministic versioned archives, checksums,
  and SPDX metadata. Draft-only candidate and digest-addressed GHCR workflows,
  a strict signed OTA manifest contract, offline signing/verification tooling,
  and stable/beta operator policy are present. No desktop updater is included.
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
  review is deferred to avoid collision with the active Edge wizard. Relay/Edge
  event application, Edge physical compaction, and mobile/Core forwarding
  parity are deliberately deferred to the next integration slice; no Core
  completion claim implies those copies have been compacted.

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
- Core purge cannot erase filesystem snapshots, SSD remanence, external
  backups, user-copied exports, or remote copies. macOS/Linux locked-file,
  disk-pressure, checkpoint, and VACUUM behavior is covered by portable design
  and tests but has not yet been observed on those operating systems.
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
  is required before hosted Edge setup can be one-click. A connector linked
  through the providers' current web/Desktop flows can then be used from their
  mobile apps, subject to plan and workspace-admin policy; real ChatGPT and
  Claude hosted/mobile handshakes remain unobserved.
- Edge provides the offline `always_available` projection and proposal queue.
  It does not yet forward `core_available` retrieval to an online Core.
- The local SQLite vault is not application-encrypted at rest; operators rely
  on operating-system account and disk protection. Portable exports are
  passphrase-encrypted.
- Dashboard restore is intentionally deferred: the release candidate keeps the
  existing deliberate CLI restore until a stopped-Core validation, rollback,
  verification, and explicit vault-cutover flow is designed and tested.
