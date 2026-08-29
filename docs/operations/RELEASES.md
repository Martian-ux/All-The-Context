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

The **Release candidate** workflow must be dispatched from the default branch
with an exact version, channel, and full 40-character source commit. That
commit must equal the dispatch SHA and therefore the default-branch head at
candidate-creation time. The Python project/runtime/lock plus dashboard
package/lock versions must all represent the requested release. Beta tags,
asset names, and manifests keep the raw `x.y.z-beta.N` SemVer spelling even
when Python lock metadata uses its equivalent `x.y.zbN` spelling.

Before native packaging, the validate job fail-closes unless:

1. the exact eight-job hosted CI set is green on that SHA: six supported-host
   matrix slots (Python 3.12 and desktop packaging on Windows and Ubuntu, plus
   dashboard on Node 20/22), `Repository security gates`, and `Dashboard
   production asset parity`;
2. local Ruff format/lint, mypy, pytest, and docs checks rerun cleanly;
3. third-party Actions pins match `release/actions-policy.json`;
4. content-free tree/history security scans and private-key audit pass;
5. Python and dashboard dependency vulnerability gates pass;
6. dashboard `npm ci` / check / test / build / high-severity audit pass and
   committed `packages/.../web` assets match the production build byte-for-byte;
7. a component/license inventory and `NOTICES.txt` are produced from
   `uv.lock` and `apps/dashboard/package-lock.json`.

Python installs use `scripts/install_locked_python.py` so composition comes from
the reviewed `uv.lock` rather than independently resolving broad ranges.
Build backends (`setuptools`, `wheel`) must be present as hashed lock entries and
installed before `--no-build-isolation`; the installer fails closed if either is
missing. The retained macOS packaging path, when exercised locally outside
ordinary CI, sets cryptography's documented
`OPENSSL_STATIC=1` source-build mode and fails closed if its Rust extension still
links `libssl.3.dylib` or `libcrypto.3.dylib`. This keeps Intel builds, for which
cryptography 50 publishes no wheel, from colliding with Python's separately
bundled same-basename OpenSSL libraries; the install bypasses pip's wheel cache
so an older dynamically built local wheel cannot evade that policy. Those
retained Mac source/tests and historical evidence are not ordinary-CI or release
receipts and do not add a Mac target to the consumer candidate. `ensure_pinned_uv` never
network-bootstraps `uv` without digests—the
reviewed `0.11.32` binary must already be available (for example via the
SHA-pinned setup-uv action). The Python dependency vulnerability gate audits a
frozen hashed export of `uv.lock` (dev and packaging extras) with lock-installed
`pip-audit==2.10.1` and `--disable-pip`, not a fresh resolve of declared ranges.
Repository security and receipt scaffolding are documented in
[REPOSITORY_SECURITY.md](REPOSITORY_SECURITY.md).

GitHub's **immutable releases** repository setting was enabled on 2026-07-22.
GitHub's check-setting endpoint requires repository `Administration: read`,
which the automatic Actions `GITHUB_TOKEN` cannot receive. Immediately before
candidate dispatch, a repository owner therefore runs:

```text
gh api -H "X-GitHub-Api-Version: 2026-03-10" repos/OWNER/REPOSITORY/immutable-releases --jq .enabled
```

The command must return `true`. The owner then dispatches **Release candidate**
from the default branch with the exact phrase `BUILD IMMUTABLE CANDIDATE`. The
workflow never receives the owner's admin credential; it fail-closes unless
that phrase is present, the requested ref is the default branch, and
`source_commit` is 40 lowercase hex and equals the dispatch SHA. It then
checks the unused tag/release slot, version metadata, and the reviewed public
key. A failed candidate is reissued under a new version rather than uploaded
with `--clobber`.

The unpublished `v0.1.0-beta.1` four-platform draft occupies that version slot
and remains historical. The live unpublished identity is numeric release ID
`367337056`, source `563a397d3095f1f45bb5814dfd39d9d7c4fab0bc`,
release-candidate run `31285545048`, and candidate digest
`ba17eeec2e82d1ee1b0621f77024a03c78807496e8f1f07bfce38f0c42842ebe` (55
four-platform assets). An earlier episode created draft `360008392` from
source `48815077544f9defb78d0e6b9c8022319888dfed`; that episode is no longer
the live release identity. Do not retarget, delete, replace, or publish the
live draft, and do not revive or reuse the earlier episode. The first
Windows/Linux-only candidate was `0.1.0-beta.2`. That unpublished identity
remains an occupied historical draft; its evidence is not rebound, deleted,
relabeled, or reused. Source inspection of that tree found a BETA-P06
visible-focus defect, so it was rebuilt as `0.1.0-beta.3`. That unpublished
Windows/Linux draft is ID `371617909`, source
`89f3973f8408ee80a76265b88d13e6fbf5791f6e`, release-candidate run
`32010253144`, and candidate digest
`804afcd91b71ea873f86c10e8f30271cd7a63d237674af91b56aae291d77f369`.
It remains historical, unsigned, and unpublished. The prepublication
signing-key rotation produced the `0.1.0-beta.4` draft, but two live runbook
sentences still contradicted ADR-093's one-backup custody floor. That draft is
therefore also historical, unsigned, and unpublished. The `0.1.0-beta.5` draft
is ID `374697784`, source `28b46ea192af76233afe41f0d2b287edc2d59a04`,
release-candidate run `32530830948`, and candidate digest
`b0303b24164de987de9eab85caeeba4b460441fbe2c32463589269f4279462bd`.
Its source retained contradictory active-state prose, so it is also historical,
unsigned, and unpublished. The immutable `0.1.0-beta.6` prerelease remains the
current public downloadable release. Its exact post-build identity is recorded
in the external exact prepublication ledger; no occupied draft is retargeted,
deleted, reused, or published. Source metadata uses `0.1.0-beta.7` only for a
private replacement-source/candidate slot. No beta.7 candidate has been built,
executed, scanned, submitted, approved, published, tagged, uploaded, or
released. The private replacement workflow is artifact-only, exact-allowlist,
and approval-gated; it is not publication, execution, or AV evidence. Future
beta.7+ acceptance requires exact candidate-bound Microsoft closed no-malware
reassessment evidence, and none exists.

The consumer candidate matrix builds exactly Windows x86_64 and Linux x86_64.
Each job compares the actual OS, CPU, and 64-bit runtime with its label before
it builds or attests anything. Retained Mac source, tests, and packaging paths
are not part of this workflow and cannot contribute a DMG, updater ZIP,
manifest, or receipt.
For each target it produces two deliberately different deliverables:

- a direct unsigned native package (`.exe` or `.tar.gz`)
  with checksum, prominent unsigned notice, package report, and SPDX subject;
- a deterministic updater ZIP with its own checksum and an SPDX file inventory.

Both subjects receive GitHub/Sigstore SLSA provenance and SPDX attestations.
The workflow downloads and verifies the attestation bundles against the exact
source commit and exact release workflow, then writes and attests
`release-candidate-v1.json`. Only then does it create a single-use unpublished
draft containing every reviewed byte. It never signs an OTA manifest and never
publishes the draft.

In immutable-release mode, that unpublished draft is not available from
`releases/tags/<tag>` and has no Git tag ref yet. Candidate verification and
the protected publication workflow therefore enumerate authenticated release
pages, require exactly one matching `tag_name`, and bind the numeric release
ID, target commit, prerelease/draft state, and exact asset names, sizes, and
SHA-256 digests. Every draft read, download, upload, recheck, and publication
PATCH uses that numeric ID. The unused-version gate also enumerates drafts, so
a published-by-tag 404 never makes an occupied slot reusable. Only after
publication do the controls require the by-tag release, exact tag ref,
immutable state, and `gh release verify`.

Direct packages are the human install path: Windows provides one-click setup,
and Linux provides a portable archive. macOS is unsupported and has no release
asset. An updater ZIP is not automatically OTA-eligible merely because it
exists. The first public beta promotes only Windows x86_64 into the signed OTA
manifest set. The Linux ZIP manifest is withheld until real native
extraction/install, health, interruption, and rollback acceptance proves it.
No Mac ZIP or manifest exists in the candidate.

Before using a candidate, an operator must confirm all source, dashboard,
package, native diagnostic, and packaged-smoke jobs passed for the exact commit.
Native publisher signing is not a community release gate. Current artifacts
must be labeled **unsigned community builds** and must never be described as
Authenticode-signed or publisher-identified. Donated or sponsored Windows
signing can be added later as defense in depth.

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
byte-for-byte identical. The offline/key-custody checklist is in
[Release key ceremony](RELEASE_KEY_CEREMONY.md). The active beta key is
`release-2026-b`; its reviewed public-key fingerprint is
`sha256:40f95302dd6c0241dc7f639e29693c15e94c5ccae1357b927d039a7e6bf1cf8f`.
The predecessor `release-2026-a` remains tracked as revoked after its encrypted
private-key passphrase became unavailable before any release was published;
this records availability loss and does not allege compromise.
The private key has not entered the repository, GitHub, Actions, an AI
system, a shell argument, or an environment variable. One recoverable encrypted
backup, kept separate from the operator-controlled primary, must be
restore-tested before the one candidate-bound `BETA-R02` source receipt.

## Offline manifest signing and draft publication

For the first public beta, perform these steps only for the explicitly eligible Windows
x86_64 OTA ZIP:

Do not begin signing merely because the candidate exists. First complete the
one-backup custody prerequisite on
[RELEASE_KEY_CUSTODY_FORM.md](RELEASE_KEY_CUSTODY_FORM.md), emit the unique
`BETA-R02` source receipt, collect every receipt required by the explicitly
selected publication profile, and record the maintainer's bundle-level
`approve` decision. The initial `lean_public_beta_v1` profile requires exactly
`BETA-L01`, `BETA-L02`, `BETA-S06`, `BETA-R01`, `BETA-R02`, and `BETA-R03`,
plus all four lean acknowledgements set to true. The complete
`certification_v1` profile still requires the unchanged 20-gate set. The
decision is distinct from the R02 receipt and must retain
`independent_human_review_claimed=false`.

1. Download the draft ZIP, candidate inventory, checksum, SPDX document, and
   attestation bundles over authenticated HTTPS. Verify the exact workflow,
   source commit, checksum, explicit unsigned status, and packaged smoke result.
2. On the offline signing system, run the following from a clean copy of the
   reviewed source. The encrypted key path must be outside that checkout; the
   command prompts for its password without echo:

   ```text
   python scripts/release_manifest.py create --artifact all-the-context-0.1.0-beta.6-windows-x86_64.zip --version 0.1.0-beta.6 --channel beta --platform windows --architecture x86_64 --url https://github.com/OWNER/REPOSITORY/releases/download/v0.1.0-beta.6/all-the-context-0.1.0-beta.6-windows-x86_64.zip --minimum-supported-version 0.1.0-beta.6 --release-notes-url https://github.com/OWNER/REPOSITORY/releases/tag/v0.1.0-beta.6 --key-id release-2026-b --private-key <offline-path>/release-2026-b.pem --output manifest-beta-windows-x86_64-v1.json
   ```

   Add `--mandatory` only for a documented security or compatibility boundary.
3. Transfer only the signed manifest back. On a clean online machine, verify it
   against the reviewed repository keyring and the downloaded artifact:

   ```text
   python scripts/release_manifest.py verify --manifest manifest-beta-windows-x86_64-v1.json --keyring release/keys.json --artifact all-the-context-0.1.0-beta.6-windows-x86_64.zip --channel beta --current-version 0.1.0-beta.6
   ```
4. Upload that exact manifest to the draft once, without `--clobber`. Do not add
   a Linux or macOS manifest. Record the reviewed candidate-inventory SHA-256.
5. Configure the `release-promotion` environment for deliberate maintainer
   approval. This project currently has one human maintainer, so the release
   log must describe any self-approval truthfully and must not call AI review
   or an environment click independent human review. The repository owner
   repeats the admin-authenticated immutable-setting command above immediately
   before manually dispatching **Publish verified beta release** from the
   protected default branch with the exact tag, the reviewed historical
   candidate `source_commit`, candidate digest, and phrase
   `PUBLISH UNSIGNED BETA`. Protected `main` may have advanced since
   candidate creation; the later dispatch SHA is the trusted current
   release-control checkout and is not required to equal that earlier
   reviewed `source_commit`. The protected job never receives the admin
   token. It checks out `github.sha` and repeats package, checksum, SPDX,
   provenance, source, keyring, signature, URL, and supported manifest-set
   verification against the historical candidate identity before publishing.
   It also runs
   `scripts/publication_gate.py` against the reviewed candidate digest, the
   exact promotion asset inventory, a content-free acceptance receipt bundle
   containing exactly the gate set named by `publication_policy`, with an
   explicit maintainer `approve` decision, and the reviewed public-key identity.
   The gate records the selected policy and fails closed on unknown policies,
   extra/missing/duplicate gates, false lean acknowledgements, or non-pass
   receipts. `BETA-R05` and
   `BETA-O01` are postpublication gates and are rejected from this bundle. The
   private signing key, password, and backup location never enter Actions, the
   repository, an AI system, a shell argument, or an environment variable. The
   job then requires the resulting release to report immutable and verifies
   GitHub's release attestation.
6. Record tag, commit, release URL, asset digests, manifest digests, key ID,
   workflow URLs, unsigned community-build status, and approver in the release
   log. Never replace an asset underneath an already signed URL; issue a new
   version instead.

## GitHub Pages beta channel

GitHub Pages is an explicit operator gate and was enabled with **GitHub
Actions** as its publishing source on 2026-07-22. No channel content is
deployed merely by enabling the site. Before the first real promotion, an owner
configures deliberate deployment protection on the `github-pages` environment.
If the sole maintainer approves the deployment, the release receipt records
that fact without claiming separation of duties. This does not require a paid
signing identity.

The manual **Promote signed beta update channel** workflow must be dispatched
from the default branch with an exact immutable published tag, the reviewed
historical release `source_commit`, reviewed candidate digest, and the
confirmation phrase `PROMOTE SIGNED BETA`. It checks out the current
protected default-branch SHA for release-control code. That later dispatch
SHA is not required to equal the earlier reviewed `source_commit`; existing
verification still binds the published release, attestations, and signed
manifests to the historical candidate identity. It verifies GitHub's immutable
release attestation, matches every downloaded asset to that release, rechecks
the build/SBOM attestations, and accepts exactly the signed manifests identified
as OTA-eligible by the candidate inventory. It then builds a link-free Pages
artifact and pauses at the protected `github-pages` deployment environment.
There is no push-triggered or release-triggered channel promotion.

After immutable publication and protected channel promotion, collect
`BETA-R05` from fresh public downloads and channel verification. Collect
`BETA-O01` only after the public release, documentation, known-issues, support,
security-intake, and recovery URLs have remained healthy through the initial
launch watch and every received report has been triaged. These receipts close
B-206, require exact downloaded-artifact operational evidence bound to the
candidate inventory, and do not retroactively enter the selected prepublication
bundle.

Candidate creation fails closed before this sequence unless
[support](../../SUPPORT.md), [known issues](../KNOWN_ISSUES.md),
[security intake](../../SECURITY.md), and the
[recovery runbook](RUNBOOK.md) exist and remain linked from the repository
README. That is source readiness, not postpublication execution evidence.

The first-public-beta pointer is therefore only:

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

Before the first protected promotion, HTTP 404 from only that exact built-in
beta URL is the explicit `unpublished` state: no signed release exists yet, and
automatic checks remain enabled. A persisted legacy 404 error is normalized on
startup. Overrides, forks, custom endpoints, release assets, and every other
HTTP or verification failure remain errors; this empty-channel state does not
relax or replace any signing or promotion gate.

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

Linux remains direct-package/manual-required and has no first-public-beta OTA
channel manifest. The Windows evidence is an unsigned
same-version engineering transaction, not a public promotion. Community
Windows beta OTA publication requires the offline Ed25519 key ceremony,
immutable channel publication, explicit unsigned-publisher disclosure, and the
exact-candidate same-version transaction. The next-beta gate is the real signed
first-published-beta-to-successor N-1 update drill. Paid Authenticode is out of
scope. Do not enable automatic Linux cutover until equivalent journaling,
health, interruption, and rollback work is implemented and observed. macOS is
not a release target.

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
