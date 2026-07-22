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
  bootstrap; demonstration and automated tests; Retrieval V2 Phase 0 with an
  offline deterministic 1k/10k benchmark, frozen V1 baseline, policy-before-
  ranking invariant, bounded opt-in 50k profile, and executable V2 gates.
- Local evidence (Windows 11, Python 3.12): 138 Python tests and 10 dashboard
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

- Phase 0 is implemented. The synthetic frozen V1 baseline measures retrieval
  quality, policy/temporal behavior, context compilation, latency, index size,
  indexing throughput, and mutation/reindex cost.
- A ranker seam now makes policy-before-ranking executable without changing V1
  BM25/recency ranking. A failing spy test proves denied, allowlisted-away,
  deleted, expired, and superseded records cannot enter relevance scoring.
- Production Retrieval V2 ranking is intentionally not implemented. V2 has not
  been evaluated against, and is not claimed to meet, the acceptance gates.

## Explicitly unexercised or deferred

- Retrieval V2 ranking, semantic retrieval, typo/paraphrase recovery, and
  near-duplicate suppression remain deferred beyond the frozen Phase 0
  baseline. The benchmark is synthetic and its timing evidence is local, not a
  production workload or cross-platform performance claim.

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
