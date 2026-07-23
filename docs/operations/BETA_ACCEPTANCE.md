# Beta release acceptance

This runbook defines the evidence required to call an All The Context build a
public beta. It does not authorize publication, spending, or creation of an
offline private key.

## Version identity

- Tags, update manifests, and assets use SemVer such as `0.1.0-beta.1`.
- The candidate records one exact 40-character commit and every artifact digest.
- Published assets are immutable; changed bytes require a new version.

## Gate 1: integrated source

On the exact release commit:

1. Ruff formatting/lint, strict mypy, Python tests, and dashboard checks pass.
2. Windows, macOS, and Linux source/package jobs pass.
3. Dashboard production build and dependency audit pass.
4. Reachable history and artifacts contain no private key, credential, personal
   context, or developer-machine path.
5. No V1 workflow publishes an Edge image or deploys a runtime service.

## Gate 2: installable desktop artifacts

- Windows fresh per-user install starts without a terminal, creates persistent
  shortcuts, connects selected local clients, survives restart, upgrades, and
  uninstalls without deleting the vault.
- macOS unsigned app/DMG explains the OS warning, installs per-user startup
  without root, and passes lifecycle smoke tests.
- Linux portable package runs without Docker or root and passes the same tests.
- Initialization, startup, ingestion, retrieval, export, shutdown, and restart
  are observed on every target OS.

## Gate 3: local MCP and memory behavior

- Codex and Claude Desktop connect once, preserve unrelated configuration, and
  survive Core/app restart.
- All required MCP tools enforce scoped identities and never approve candidate
  memory directly.
- Archive imports are bounded, inert, idempotent, and resumable.
- Correction, supersession, tombstones, permissions, validity, FTS retrieval,
  export, and restore pass their integration/security suites.
- New UI approvals offer only `local_only` and `core_available`; the product
  does not advertise the legacy `always_available`/Edge path.

## Gate 4: no hosted runtime and direct-Core honesty

- First run and the dashboard never ask for a hosting account, provider bill,
  deployment URL, Edge credential, or cloud replica.
- Core does not start the dormant Edge network worker.
- Core binds to `127.0.0.1` unless the operator explicitly chooses otherwise.
- The product says that mobile requires Core to be online and securely
  reachable.
- One-click mobile is not claimed until direct-Core device pairing,
  authentication, encrypted transport, revocation, restart persistence, and
  offline failure behavior pass on real mobile hardware.

## Gate 5: candidate supply chain and OTA

- GitHub Release assets are produced from the frozen commit with SHA-256,
  SPDX SBOM, and provenance.
- Native packages are labeled **unsigned community build**.
- OTA metadata is signed outside the repository with an operator-controlled
  Ed25519 key; only reviewed public keys enter source control.
- A real beta1-to-beta2 Windows update proves success, interruption recovery,
  failed-health rollback, and vault preservation. macOS/Linux remain manual
  until equivalent native rollback is observed.

## Human approval record

Before publication, record the commit, CI and draft-release URLs, asset and
manifest digests, public-key fingerprint, real-platform results, unsigned
warning acknowledgement, and explicit approve/reject decision. Missing gates
leave the release as a draft.
