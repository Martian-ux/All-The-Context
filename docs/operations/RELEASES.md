# Release and update operations

This runbook covers candidate artifacts, signed update metadata, and the native
updater's operator boundary. It does not create a signing key, configure a
production channel, publish a release, or authorize unattended promotion.

## Trust and channel layout

Releases use immutable tags and asset names. Stable versions are `x.y.z`; beta
versions are `x.y.z-beta.N`. A version is published to exactly one channel.
The logical OTA layout is:

```text
stable/<platform>/<architecture>/manifest-v1.json
beta/<platform>/<architecture>/manifest-v1.json
```

Those small channel pointers may change only by an explicit promotion. Every
manifest they contain points to a versioned HTTPS release asset whose path
contains the exact version. A manifest URL or artifact URL containing `main`
or `latest` is invalid. Clients must never download executable content from a
branch, a mutable source archive, or an unsigned channel pointer.

The v1 manifest contract is [machine-readable](../../release/ota-manifest.schema.json)
and contains `version`, `channel`, `platform`, `architecture`, immutable `url`,
`sha256`, byte `size`, `minimum_supported_version`, `mandatory`,
`release_notes_url`, `key_id`, and an Ed25519 `signature`. The signature covers
canonical UTF-8 JSON for every field except `signature` itself. The Python
implementation rejects unknown fields so a future contract needs a new schema
version rather than ambiguous interpretation.

## Candidate build

The **Release candidate** workflow requires an exact version, channel, and full
40-character source commit. It builds and smokes native Windows, macOS, and
Linux artifacts on their authored operating systems. It creates deterministic
versioned ZIP files, SHA-256 sidecars, SPDX 2.3 metadata, and GitHub build
provenance, then uploads them to an unpublished draft release. This workflow
does not sign an OTA manifest and does not publish the draft.

Before using a candidate, an operator must confirm all source, dashboard,
package, native diagnostic, and packaged-smoke jobs passed for the exact commit.
Windows Authenticode signing and macOS signing/notarization are separate gates;
the current engineering artifacts must not be described as publisher-signed.

## Offline signing and promotion

The release signing private key is generated and retained on an offline or
operator-controlled system outside GitHub and outside this repository. Do not
put it in Actions secrets, repository files, fixtures, logs, shell history, or
cloud build inputs. Only the raw Ed25519 public key is added to
`release/keys.json` in base64url form after an independently reviewed key
ceremony. The reviewed public entries in repository `release/keys.json` and
packaged `allthecontext/update_keys.json` must be byte-for-byte equivalent; the
release tests reject drift between operator verification and client trust.

For each platform and architecture:

1. Download the draft asset and its checksum over authenticated HTTPS. Verify
   the workflow provenance, checksum, expected commit, native signing status,
   and packaged-smoke result.
2. On the offline signing system, use a private-key path outside the checkout:

   ```text
   python scripts/release_manifest.py create --artifact <versioned-asset> --version 0.2.0 --channel stable --platform windows --architecture x86_64 --url https://github.com/OWNER/all-the-context/releases/download/v0.2.0/all-the-context-0.2.0-windows-x86_64.zip --minimum-supported-version 0.1.0 --release-notes-url https://github.com/OWNER/all-the-context/releases/tag/v0.2.0 --key-id release-2026-a --private-key <offline-path>/release-2026-a.pem --output manifest-windows-x86_64.json
   ```

   Add `--mandatory` only for a documented security or compatibility boundary.
3. Transfer only the signed manifest back. On a clean online machine, verify it
   against the reviewed repository keyring and the downloaded artifact:

   ```text
   python scripts/release_manifest.py verify --manifest manifest-windows-x86_64.json --keyring release/keys.json --artifact all-the-context-0.2.0-windows-x86_64.zip --channel stable --current-version 0.1.0
   ```
4. Upload the verified manifests to the draft, inspect every immutable asset
   URL, and obtain the required human approval. Publish the GitHub release only
   after that approval. Then update each channel pointer atomically to the exact
   signed bytes and verify it again from the public endpoint.
5. Record tag, commit, release URL, asset digests, manifest digests, key ID,
   workflow URLs, native publisher/notarization results, and approver in the
   release log. Never replace an asset underneath an already signed URL; issue a
   new version instead.

## Client updater operation

Production packages embed the reviewed public `update_keys.json`; private keys
never enter a package. Operators configure immutable channel metadata origins
with `ATC_UPDATE_STABLE_URL` and, only when beta is supported,
`ATC_UPDATE_BETA_URL`. Each value must be an exact HTTPS manifest endpoint.
The application ships with neither endpoint configured and with an empty
keyring until the release ceremony is complete, so development builds fail
closed rather than contacting an inferred repository.

The dashboard **Updates** page supports check now, stable/beta preference,
automatic launch/daily checks, opt-out, defer, verified download, and error
clearing. State and nonsecret preferences live under
the Core per-user app-data directory. Do not place credentials, release private
keys, personal context, or raw server response bodies there or in logs.

When a platform remains manual-required, **Save verified package** asks
Core for a new authenticated, no-store copy. Core re-verifies the stored signed
manifest and the artifact's target, length, and SHA-256 during that request and
deletes the response copy afterward. The dashboard never receives the private
staging path. Saving a package does not make its installation automatic or
assert that platform rollback has been observed.

The check/download sequence is: bounded no-redirect manifest fetch; strict
schema/key/signature/channel/platform/architecture/version verification;
stream to per-operation staging; exact signed length and SHA-256 verification;
disk preflight; and either a manual verified-package response or a
recovery-capable native handoff. Partial files are
deleted after cancellation or failure. A replacement is complete only after
its version and bounded loopback `/health` response pass. Preserve the backup
and state files until recovery finishes.

The packaged Windows application enables **Install and restart** only when its
separate recovery executable and stable installed files are present. Core takes
an initial consistent backup, writes a strict operation journal, registers
per-user RunOnce recovery, and exits. The helper waits for that process, takes a
final stopped-Core backup, applies the verified executable, validates the app,
MCP adapter, and installed updater, runs frozen diagnostics and a real one-shot
loopback Core health check, then commits and restarts Core. A failed or
interrupted post-cutover check restores the prior app, MCP adapter, updater, and
database; a pre-cutover failure leaves the current files and vault untouched.
The packaged Windows smoke injects both a crash after replacement and a failed
post-migration health check and verifies resume and rollback.

macOS and Linux remain manual-required. The Windows evidence is an unsigned
same-version engineering transaction, not a production promotion. Do not
publish or describe Windows OTA as production-ready until the offline key
ceremony, immutable channel publication, Authenticode/publisher checks, and a
real signed N-1 update drill pass. Do not enable automatic macOS or Linux
cutover until equivalent native signing, journaling, health, interruption, and
rollback work is implemented and observed on those systems.

Unknown operating systems, unknown CPU identifiers, and 32-bit application
runtimes fail closed. Repeated checks and channel changes remove a bounded
number of orphan staging entries; startup also safely resets corrupt persisted
state and bounded stale response copies.

## Verification and downgrade policy

The updater verifies in this order: schema and exact fields; selected
channel/platform/architecture; active
and channel-authorized key ID; Ed25519 signature; version policy; HTTPS
immutable URL; declared size; downloaded SHA-256; then native publisher
signature where the platform supports one. It must stage rather than execute
partially verified bytes.

Downgrades are rejected even when correctly signed. Equal versions are a no-op.
Stable installations consume only stable manifests. Beta installations consume
only beta manifests unless the user completes an explicit channel migration;
switching from beta to a numerically lower stable build is a downgrade and
requires a separate, interactive recovery procedure. `minimum_supported_version`
means older clients require a manual supported upgrade path. `mandatory` may
change deferral UI but never bypasses cryptographic, digest, platform, or native
signature checks.

## Rotation, revocation, and recovery

Keyring entries have a unique `key_id`, Ed25519 public key, allowed channels,
and `active` or `revoked` status. Normal rotation is an overlap:

1. Generate the successor offline and review only its public key into the
   application keyring.
2. Release a version trusting old and new public keys.
3. Sign subsequent manifests with the successor and observe adoption.
4. In a later application release, mark the predecessor revoked. Never reuse a
   key ID or delete revocation history merely to make an old manifest pass.

If a private key may be compromised, stop promotions, mark its public entry
revoked in a security release signed by a different already-trusted key, remove
all mutable channel pointers signed by the compromised key, and publish an
incident notice. Users with no remaining trusted key need a native-signed
manual recovery installer; a compromised manifest key must not authorize its
own replacement.

## Edge image and hosted deployment

Publishing a reviewed release triggers the **Publish Edge image** workflow. A
manual run accepts only a full commit SHA. The image is pushed to GHCR with an
immutable `sha-<40-character-commit>` tag and is recorded by digest
(`ghcr.io/OWNER/all-the-context-edge@sha256:...`). OCI source/revision/license
labels, BuildKit provenance, an SBOM attestation, and GitHub provenance are
attached. Deployment configuration must pin the digest, not `latest`.

After the first push, an owner must explicitly make the GHCR package public and
verify anonymous pull access. `render.yaml` is a public-ready example requiring
an HTTPS public URL, a generated Edge bundle, and persistent storage. Its
starter service and disk can incur charges; this repository does not create
them. Provider OAuth/mobile handshakes and production hosting remain acceptance
tests for the operator, not claims made by the authored configuration.
