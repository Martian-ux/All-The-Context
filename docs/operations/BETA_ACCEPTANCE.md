# Beta release acceptance

This runbook defines the evidence required to call an All The Context build a
public beta. It does not authorize a release, a hosted deployment, spending, or
creation of an offline private key. Each external action remains an explicit
operator gate.

## Version identity

- Public tags, update manifests, and release assets use SemVer such as
  `0.1.0-beta.1`.
- Python package metadata may canonicalize that version to `0.1.0b1`.
- The candidate record must name the exact 40-character source commit, tag,
  normalized Python version, workflow run, and every artifact digest.
- Candidate assets and channel pointers are never replaced in place. A changed
  byte requires a new version.

## Gate 1: integrated source

The integration lead verifies, on one exact commit:

1. Ruff formatting and lint pass.
2. Strict mypy passes against Windows, macOS, and Linux targets.
3. The complete Python and dashboard suites pass.
4. Dashboard production build and dependency audit pass.
5. The hosted Windows/macOS/Linux Python, native artifact, dashboard, and Edge
   container jobs all pass.
6. Reachable Git history and candidate artifacts contain no private key,
   credential, personal context, or developer-machine path.

## Gate 2: installable desktop artifacts

- Windows: a fresh per-user install starts without a terminal, initializes the
  correct vault, creates working shortcuts, connects detected local clients,
  survives restart, and uninstalls without deleting the vault.
- macOS: the unsigned application bundle explains the operating-system warning,
  installs its per-user LaunchAgent without root, and passes initialization,
  startup, ingestion, retrieval, export, and shutdown smoke tests.
- Linux: the selected portable package and documented fallback run without
  Docker or root and pass the same lifecycle smoke tests.
- Credential and startup acceptance is observed on each real target OS. A mock
  or cross-target build is supporting evidence, not a substitute.

## Gate 3: candidate supply chain

- GitHub Release assets are created only from the frozen source commit.
- Each immutable platform archive has a SHA-256 sidecar, SPDX SBOM, and build
  provenance tied to the same commit.
- Native artifacts are labeled **unsigned community build**. They must not be
  described as Authenticode-signed, notarized, or publisher-identified.
- OTA metadata is signed outside the repository with an operator-controlled
  Ed25519 private key. Only the reviewed public key is committed.
- The beta channel is served over HTTPS from immutable, reviewed bytes and is
  re-fetched and verified before publication approval.

## Gate 4: hosted Edge and providers

- The Edge image is public by immutable digest and an anonymous pull is
  observed before its digest-pinned deployment blueprint is enabled.
- A real provider deployment completes claim, pairing, initial synchronization,
  restart, and decommission checks without giving Edge canonical write
  authority.
- Claude and ChatGPT/Codex eligibility, login, workspace policy, hosting account,
  and possible provider charges are disclosed before the operator leaves the
  local application.
- With Core stopped, an approved `always_available` record is retrieved from a
  mobile-capable provider. With Core online, authorized `core_available`
  forwarding and proposals are exercised separately.

## Gate 5: beta update drill

After beta 1 is accepted, beta 2 must exercise a real signed beta1-to-beta2
update. The evidence includes successful cutover, restart, interruption resume,
forced failed-health rollback, preserved vault integrity, and immutable channel
promotion. Same-version engineering transactions do not satisfy this gate.

## Human approval record

Before any public release, the operator records:

- source commit and hosted CI URL;
- candidate tag and release URL;
- asset, manifest, and public-key fingerprints;
- real-platform install and provider acceptance results;
- unsigned-warning acknowledgement;
- hosting cost/account acknowledgement; and
- the explicit approve/reject decision.

Failed or incomplete gates leave the release as a draft. Automation must not
convert missing human approval into publication.
