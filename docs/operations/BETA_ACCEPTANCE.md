# Beta release acceptance

This runbook defines the evidence required to call an All The Context build a
public beta. It does not authorize publication, spending, or creation of an
offline private key.

## Version identity

- Tags, update manifests, and assets use SemVer such as `0.1.0-beta.1`.
- The release candidate records one exact 40-character commit and every
  artifact digest.
- Published assets are immutable; changed bytes require a new version.

## Gate 1: integrated source

On the exact release commit:

1. Ruff formatting/lint, strict mypy, Python tests, and dashboard checks pass.
2. Windows, macOS, and Linux source/package jobs pass.
3. Dashboard production build and dependency audit pass.
4. Reachable history and artifacts contain no private key, credential, personal
   context, or developer-machine path.
5. No V1 workflow publishes an Edge image or deploys a runtime service.
6. Documentation, generated MCP instructions, UI copy, demo output, and status
   use observation/current-context terminology and do not instruct the user to
   review routine memory.

## Gate 2: installable desktop artifacts

- Windows fresh per-user install starts without a terminal, creates persistent
  shortcuts, connects selected local clients, survives restart, upgrades, and
  uninstalls without deleting the vault.
- macOS unsigned app/DMG explains the OS warning, installs per-user startup
  without root, and passes lifecycle smoke tests.
- Linux portable package runs without Docker or root and passes the same tests.
- Initialization, startup, ingestion, automatic policy evaluation, retrieval,
  export, shutdown, and restart are observed on every target OS.

## Gate 3: one-time setup and automatic context

- Codex and Claude Desktop connect once, preserve unrelated configuration, and
  survive Core/app restart.
- A user states a durable preference in a connected client; Core returns an
  `applied` disposition and another session retrieves it without an approval
  call or dashboard visit.
- An exact retry replays the same decision; an exact duplicate reinforces the
  existing record without duplicate current context.
- Model inference and provider-synthesized memory remain tentative and absent
  from current retrieval unless eligible explicit evidence corroborates them.
- Provider adapters exclude assistant, system, tool, and attachment roles;
  generic or instruction-bearing imports remain tentative; secret-like
  material is ignored; imported text never executes as instructions.
- An explicit correction changes current context before the successful
  operation returns and preserves the prior version.
- Reversible forget/delete and restoration work without irreversible purge.
- Every decision exposes provenance, `decision_reason`, `decided_at`, and
  `policy_version`; Activity inspection is optional and has no pending inbox.
- Scoped clients cannot select a disposition, write current records directly,
  restore deleted records, or invoke purge.

## Gate 4: archive import and lifecycle integrity

- Archive imports are bounded, inert, idempotent, and resumable.
- Observations remain staged and absent from retrieval until successful
  `finish_ingestion`.
- Completed imports automatically report applied, reinforced, tentative,
  ignored, skipped, and coverage counts without a review step.
- A failed or interrupted import changes no current context and can retry from
  the preserved raw source without duplicate observations or decisions.
- Correction, replacement, tombstones, permissions, validity, FTS retrieval,
  export, restore, automatic-policy migration, and policy-version replay pass
  their integration/security suites.
- Newly applied records use only `local_only` and `core_available`; the product
  does not advertise the legacy `always_available`/Edge path.

## Gate 5: no hosted runtime and direct-Core honesty

- First run and the dashboard never ask for a hosting account, provider bill,
  deployment URL, Edge credential, or cloud replica.
- Core does not start the dormant Edge network worker.
- Core binds to `127.0.0.1` unless the operator explicitly chooses otherwise.
- Relay, if explicitly exercised for compatibility, queues observations and
  accepts signed Core projections only; it never creates current context.
- The product says that mobile requires Core to be online and securely
  reachable.
- One-click mobile is not claimed until direct-Core device pairing,
  authentication, encrypted transport, revocation, restart persistence, and
  offline failure behavior pass on real mobile hardware.

## Gate 6: release supply chain and OTA

- GitHub Release assets are produced from the frozen commit with SHA-256,
  SPDX SBOM, and provenance.
- Native packages are labeled **unsigned community build**.
- OTA metadata is signed outside the repository with an operator-controlled
  Ed25519 key; only reviewed public keys enter source control.
- A real beta1-to-beta2 Windows update proves success, interruption recovery,
  failed-health rollback, and vault preservation. macOS/Linux remain manual
  until equivalent native rollback is observed.

## Human release decision

Before publication, record the commit, CI and draft-release URLs, asset and
manifest digests, public-key fingerprint, real-platform results, unsigned
warning acknowledgement, and explicit release approve/reject decision. This is
a software-release decision, not a context-review queue. Missing gates leave
the release as a draft.
