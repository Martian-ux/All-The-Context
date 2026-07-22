# Project status

- Current phase: first vertical slice implemented and locally verified.
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
  bootstrap; demonstration and automated tests; Retrieval V2 Phase 1 with an
  offline deterministic 1k/10k benchmark, frozen V1 baseline, policy-before-
  ranking invariant, bounded lexical channels/RRF, improved context compiler,
  administrator diagnostics, bounded opt-in 50k profile, and passing V2 gates.
- Local evidence (Windows 11; latest Python run on 3.14.3): 142 Python tests and 10 dashboard
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
  registration, managed client credential, and temporary data.
- CI authored: Python smoke/test jobs for Windows, macOS, and Linux; dashboard
  jobs for Node 20 and 22; hosted Edge image build; native desktop build,
  resource diagnostics, and packaged first-run/MCP smoke.
- Blockers: none for evaluating the vertical slice locally.

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
- Temporal precision remains `0.5`; diversity and near-duplicate thresholds
  need evaluation on a larger sanitized judgment set before further tuning.

- The GitHub Actions matrix has not run because this local repository has no
  configured remote; macOS and Linux behavior is therefore designed and
  covered by tests, not claimed as observed on those operating systems.
- The Windows engineering artifact is unsigned. Windows publisher signing,
  macOS signing/notarization, and native Linux package metadata remain release
  work.
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
