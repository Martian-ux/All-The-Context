# Project status

- Current phase: first vertical slice implemented and locally verified
- Completed: architecture and protocols; authoritative Core; restricted Relay;
  signed event replication; source, candidate, approval, correction,
  supersession, and tombstone lifecycle; nine MCP tools over STDIO and
  Streamable HTTP; generic import; review dashboard; encrypted export/restore;
  one-time client configuration; self-repairing source bootstrap;
  demonstration and automated tests
- Local evidence (Windows 11, Python 3.12): 49 Python tests passed, including a
  real MCP STDIO initialize/list/call handshake; Ruff, mypy, documentation
  checks, dashboard type checks, dashboard tests, dashboard production build,
  package build, and Docker Compose configuration validation passed
- CI authored: Python smoke/test jobs for Windows, macOS, and Linux; dashboard
  jobs for Node 20 and 22; hosted Relay image build
- Blockers: none for evaluating the vertical slice

## Explicitly unexercised or deferred

- The GitHub Actions matrix has not run because this local repository has no
  configured remote; macOS and Linux behavior is therefore designed and
  covered by tests, not claimed as observed on those operating systems.
- Native installers, service registration, signing/notarization, and a public
  Relay deployment are packaging work, not part of this slice.
- The Relay uses SQLite in this slice. A PostgreSQL backend is an intentional
  hosted-deployment follow-up.
- Docker Compose parses successfully, but the image was not built locally
  because a Docker daemon was unavailable.
- Real Windows Credential Manager, macOS Keychain, and Linux secret-service
  backends remain operating-system acceptance tests; the abstraction and
  explicit development fallback are covered.
- Codex and Claude configuration examples are supplied, but provider-hosted
  client handshakes and Grok support were not exercised.
- Relay provides the offline `always_available` projection and proposal queue.
  It does not yet forward `core_available` retrieval to an online Core.
- The local SQLite vault is not application-encrypted at rest; operators rely
  on operating-system account and disk protection. Portable exports are
  passphrase-encrypted.
