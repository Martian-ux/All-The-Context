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
40-character source commit. The commit must be the current default-branch head,
and the Python project/runtime/lock plus dashboard package/lock versions must
all represent the requested release. Beta tags, asset names, and manifests keep
the raw `x.y.z-beta.N` SemVer spelling even when Python lock metadata uses its
equivalent `x.y.zbN` spelling.

GitHub's **immutable releases** repository setting was enabled on 2026-07-22.
GitHub's check-setting endpoint requires repository `Administration: read`,
which the automatic Actions `GITHUB_TOKEN` cannot receive. Immediately before
candidate dispatch, a repository owner therefore runs:

```text
gh api -H "X-GitHub-Api-Version: 2026-03-10" repos/OWNER/REPOSITORY/immutable-releases --jq .enabled
```

The command must return `true`. The owner then dispatches **Release candidate**
with the exact phrase `BUILD IMMUTABLE CANDIDATE`. The workflow never receives
the owner's admin credential; it checks that deliberate phrase, the exact
default-branch head, the unused tag/release slot, version metadata, and the
reviewed public key. A failed candidate is reissued under a new version rather
than uploaded with `--clobber`.

The native matrix builds Windows x86_64, Linux x86_64, macOS arm64 on
`macos-26`, and macOS x86_64 on `macos-26-intel`. Each job compares the actual
OS, CPU, and 64-bit runtime with its label before it builds or attests anything.
For each target it produces two deliberately different deliverables:

- a direct unsigned native package (`.exe`, `.dmg`, or `.tar.gz`)
  with checksum, prominent unsigned notice, package report, and SPDX subject;
- a deterministic updater ZIP with its own checksum and an SPDX file inventory.

Both subjects receive GitHub/Sigstore SLSA provenance and SPDX attestations.
The workflow downloads and verifies the attestation bundles against the exact
source commit and exact release workflow, then writes and attests
`release-candidate-v1.json`. Only then does it create a single-use unpublished
draft containing every reviewed byte. It never signs an OTA manifest and never
publishes the draft.

Direct packages are the human install path: Windows provides one-click setup,
macOS provides an open-and-launch DMG, and Linux provides a portable archive.
An updater ZIP is not automatically
OTA-eligible merely because it exists. Beta 1 promotes only Windows x86_64 into
the signed OTA manifest set. macOS and Linux ZIP manifests are withheld until
real native extraction/install, health, interruption, and rollback acceptance
proves them. In particular, macOS application bundles contain internal symlinks
covered by the application seal. The archive writer preserves safe relative
symlinks and rejects escaping links, but that is not a substitute for a native
extraction and seal-verification test.

Before using a candidate, an operator must confirm all source, dashboard,
package, native diagnostic, and packaged-smoke jobs passed for the exact commit.
Native publisher signing is not a community release gate. Current artifacts
must be labeled **unsigned community builds** and must never be described as
Authenticode-signed, Apple-notarized, or publisher-identified. Donated or
sponsored native signing can be added later as defense in depth.

## Offline public-key ceremony

The release signing private key is generated and retained on an offline or
operator-controlled system outside GitHub and outside this repository. Do not
put it in Actions secrets, repository files, fixtures, logs, shell history, or
cloud build inputs. The signing command requires an encrypted PKCS8 PEM key and
reads its password from an interactive no-echo prompt. It has no password
argument or environment-variable path and rejects a key located inside the
checkout.

Only the Ed25519 public key is imported. `scripts/release_keyring.py` accepts a
PEM or OpenSSH public-key container, rejects private-key containers and
ambiguous bare 32-byte values, and requires the operator to supply the exact
independently reviewed `sha256:<hex>` fingerprint. An import adds the base64url
public key and fingerprint to both `release/keys.json` and packaged
`allthecontext/update_keys.json`; validation requires the tracked files to be
byte-for-byte identical. The full two-person/offline checklist is in
[Release key ceremony](RELEASE_KEY_CEREMONY.md). The operator generated the
encrypted `release-2026-a` private key outside the checkout on 2026-07-22 and
imported only its beta-authorized public half. The reviewed public-key
fingerprint is
`sha256:fe05a2bd52db97f808650fb0e832c49bd704abd62a813af4dedca4994f98e0d4`.
The private key has not entered the repository or GitHub; two recoverable
encrypted backups must be verified before its first production signature.

## Offline manifest signing and draft publication

For Beta 1, perform these steps only for the explicitly eligible Windows
x86_64 OTA ZIP:

1. Download the draft ZIP, candidate inventory, checksum, SPDX document, and
   attestation bundles over authenticated HTTPS. Verify the exact workflow,
   source commit, checksum, explicit unsigned status, and packaged smoke result.
2. On the offline signing system, run the following from a clean copy of the
   reviewed source. The encrypted key path must be outside that checkout; the
   command prompts for its password without echo:

   ```text
   python scripts/release_manifest.py create --artifact all-the-context-0.1.0-beta.1-windows-x86_64.zip --version 0.1.0-beta.1 --channel beta --platform windows --architecture x86_64 --url https://github.com/OWNER/REPOSITORY/releases/download/v0.1.0-beta.1/all-the-context-0.1.0-beta.1-windows-x86_64.zip --minimum-supported-version 0.1.0-beta.1 --release-notes-url https://github.com/OWNER/REPOSITORY/releases/tag/v0.1.0-beta.1 --key-id release-2026-a --private-key <offline-path>/release-2026-a.pem --output manifest-beta-windows-x86_64-v1.json
   ```

   Add `--mandatory` only for a documented security or compatibility boundary.
3. Transfer only the signed manifest back. On a clean online machine, verify it
   against the reviewed repository keyring and the downloaded artifact:

   ```text
   python scripts/release_manifest.py verify --manifest manifest-beta-windows-x86_64-v1.json --keyring release/keys.json --artifact all-the-context-0.1.0-beta.1-windows-x86_64.zip --channel beta --current-version 0.1.0-beta.1
   ```
4. Upload that exact manifest to the draft once, without `--clobber`. Do not add
   macOS or Linux manifests. Record the reviewed candidate-inventory SHA-256.
5. Configure required reviewers on the `release-promotion` environment. The
   repository owner repeats the admin-authenticated immutable-setting command
   above immediately before manually dispatching **Publish verified beta
   release** with the exact tag, source commit, candidate digest, and phrase
   `PUBLISH UNSIGNED BETA`. The protected job never receives the admin token. It
   repeats package, checksum, SPDX, provenance, source, keyring, signature, URL,
   and supported manifest-set verification before publishing, then requires
   the resulting release to report immutable and verifies GitHub's release
   attestation.
6. Record tag, commit, release URL, asset digests, manifest digests, key ID,
   workflow URLs, unsigned community-build status, and approver in the release
   log. Never replace an asset underneath an already signed URL; issue a new
   version instead.

## GitHub Pages beta channel

GitHub Pages is an explicit operator gate and was enabled with **GitHub
Actions** as its publishing source on 2026-07-22. No channel content is
deployed merely by enabling the site. Before the first real promotion, an owner
adds required reviewers to the `github-pages` environment. This can be done on
GitHub Free for a public repository and does not require a paid signing
identity.

The manual **Promote signed beta update channel** workflow accepts only an exact
immutable published tag, source commit, reviewed candidate digest, and the
confirmation phrase `PROMOTE SIGNED BETA`. It verifies GitHub's immutable
release attestation, matches every downloaded asset to that release, rechecks
the build/SBOM attestations, and accepts exactly the signed manifests identified
as OTA-eligible by the candidate inventory. It then builds a link-free Pages
artifact and pauses at the protected `github-pages` deployment environment.
There is no push-triggered or release-triggered channel promotion.

The Beta 1 pointer is therefore only:

```text
https://OWNER.github.io/REPOSITORY/beta/windows/x86_64/manifest-v1.json
```

The human-readable `beta/index-v1.json` is diagnostic only. Clients trust the
Ed25519 signature inside `manifest-v1.json`, not the mutability of the Pages
pointer. Re-running promotion replaces the whole Pages artifact atomically; it
cannot change a versioned GitHub Release asset.

## Client updater operation

Production packages embed the reviewed public `update_keys.json`; private keys
never enter a package. A frozen Windows x86_64 package whose embedded keyring
contains an active beta key automatically uses the canonical project endpoint:

```text
https://martian-ux.github.io/All-The-Context/beta/windows/x86_64/manifest-v1.json
```

Fresh prerelease packages select beta, and an older persisted stable default
migrates to beta only when no stable endpoint exists and the reviewed beta
endpoint does. Source runs, unsupported targets, and packages without an active
beta trust key still configure no inferred endpoint and do not make background
update requests.

`ATC_UPDATE_STABLE_URL` and `ATC_UPDATE_BETA_URL` remain explicit overrides for
forks and acceptance environments. Each value must be an exact HTTPS manifest
endpoint. The release ceremony must import the reviewed public key before a
package can gain the built-in beta endpoint.

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
recovery-capable native handoff. GitHub's versioned release download URL
returns a temporary CDN redirect, so the artifact transport accepts exactly
one HTTPS redirect from a `github.com/<owner>/<repository>/releases/download/`
path to `release-assets.githubusercontent.com/github-production-release-asset/`.
Metadata redirects, other hosts or paths, missing signed CDN queries, and
additional redirects remain refused. The signed artifact length and SHA-256
remain authoritative after transport.

Partial files are deleted after cancellation or failure. A replacement is
complete only after its version and bounded loopback `/health` response pass.
Preserve the backup and state files until recovery finishes.

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

macOS and Linux remain direct-package/manual-required and have no Beta 1 OTA
channel manifest. The Windows evidence is an unsigned
same-version engineering transaction, not a public promotion. Community
Windows OTA requires the offline Ed25519 key ceremony, immutable channel
publication, explicit unsigned-publisher disclosure, and a real signed N-1
update drill. Paid Authenticode and Apple notarization are out of scope. Do not
enable automatic macOS or Linux cutover until equivalent journaling, health,
interruption, and rollback work is implemented and observed on those systems.

Unknown operating systems, unknown CPU identifiers, and 32-bit application
runtimes fail closed. Repeated checks and channel changes remove a bounded
number of orphan staging entries; startup also safely resets corrupt persisted
state and bounded stale response copies.

## Verification and downgrade policy

The updater verifies in this order: schema and exact fields; selected
channel/platform/architecture; active
and channel-authorized key ID; Ed25519 signature; version policy; HTTPS
immutable URL; declared size; and downloaded SHA-256. An available native
publisher signature may be reported and checked as extra evidence, but is not
required for unsigned community releases. The updater must stage rather than
execute partially verified bytes.

Downgrades are rejected even when correctly signed. Equal versions are a no-op.
Stable installations consume only stable manifests. Beta installations consume
only beta manifests unless the user completes an explicit channel migration;
switching from beta to a numerically lower stable build is a downgrade and
requires a separate, interactive recovery procedure. `minimum_supported_version`
means older clients require a manual supported upgrade path. `mandatory` may
change deferral UI but never bypasses cryptographic, digest, or platform checks.

## Rotation, revocation, and recovery

Keyring entries have a unique `key_id`, Ed25519 public key, matching SHA-256
public-key fingerprint, allowed channels, and `active` or `revoked` status.
Normal rotation is an overlap:

1. Generate the successor offline and review only its public key into the
   application keyring.
2. Release a version trusting old and new public keys.
3. Sign subsequent manifests with the successor and observe adoption.
4. In a later application release, mark the predecessor revoked. Never reuse a
   key ID or delete revocation history merely to make an old manifest pass.

If a private key may be compromised, stop promotions, mark its public entry
revoked in a security release signed by a different already-trusted key, remove
all mutable channel pointers signed by the compromised key, and publish an
incident notice. Users with no remaining trusted key need a manual recovery
package distributed through a separately authenticated project security notice
and verified against the reviewed source, release digest, and provenance; a
compromised manifest key must not authorize its own replacement.
