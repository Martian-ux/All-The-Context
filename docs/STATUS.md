# Project status

- Current phase: first vertical slice implemented and locally verified
- Completed: architecture and protocols; authoritative Core; restricted Relay;
  signed event replication; source, candidate, approval, correction,
  supersession, and tombstone lifecycle; nine MCP tools over STDIO and
  Streamable HTTP; generic import; review dashboard; encrypted export/restore;
  native first-run wizard; automatic reversible Codex configuration; per-user
  startup; frozen desktop packaging; self-repairing source bootstrap;
  demonstration and automated tests
- Local evidence (Windows 11, Python 3.12): 58 Python tests and 4 dashboard
  tests passed, including real source and frozen MCP initialize/list/call
  handshakes; Ruff formatting/lint, strict mypy, documentation checks,
  dashboard type checks and production build, wheel/sdist build, frozen
  resource diagnostics, and an isolated packaged setup/MCP/retrieval/graceful-
  shutdown/cleanup smoke passed. The frozen welcome and preferences screens
  were also inspected at their real 900x590 window size.
- CI authored: Python smoke/test jobs for Windows, macOS, and Linux; dashboard
  jobs for Node 20 and 22; hosted Relay image build; native desktop build,
  resource diagnostics, and packaged first-run/MCP smoke on all three operating
  systems
- Blockers: none for evaluating the vertical slice

## Explicitly unexercised or deferred

- The GitHub Actions matrix has not run because this local repository has no
  configured remote; macOS and Linux behavior is therefore designed and
  covered by tests, not claimed as observed on those operating systems.
- The Windows engineering artifact is unsigned and does not yet register a
  standard uninstaller. Windows publisher signing, macOS signing/notarization,
  native Linux package metadata, and a public Relay deployment remain release
  work.
- The Relay uses SQLite in this slice. A PostgreSQL backend is an intentional
  hosted-deployment follow-up.
- Docker Compose parses successfully, but the image was not built locally
  because a Docker daemon was unavailable.
- Real Windows Credential Manager, macOS Keychain, and Linux secret-service
  acceptance remain unexercised; persistence verification and the explicit
  local app-data fallback were exercised in the packaged smoke.
- Codex and Claude configuration examples are supplied, but provider-hosted
  client handshakes and Grok support were not exercised.
- Relay provides the offline `always_available` projection and proposal queue.
  It does not yet forward `core_available` retrieval to an online Core.
- The local SQLite vault is not application-encrypted at rest; operators rely
  on operating-system account and disk protection. Portable exports are
  passphrase-encrypted.
