# Project status

- Current phase: first vertical slice implemented; release/CI foundation is
  authored and undergoing local verification.
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
  bootstrap; demonstration and automated tests.
- Local evidence (Windows 11, Python 3.12): 137 Python tests and 10 dashboard
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
- CI authored: Python smoke/test and package/resource diagnostic jobs for
  Windows, macOS, and Linux; dashboard jobs for Node 20 and 22; hosted Edge
  image/config build; native desktop build, resource diagnostics, bounded
  packaged first-run/MCP smoke, deterministic versioned archives, checksums,
  and SPDX metadata. Draft-only candidate and digest-addressed GHCR workflows,
  a strict signed OTA manifest contract, offline signing/verification tooling,
  and stable/beta operator policy are present. No desktop updater is included.
- Blockers: none for evaluating the vertical slice locally.

## Explicitly unexercised or deferred

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
