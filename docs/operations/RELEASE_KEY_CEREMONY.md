# Release public-key ceremony

This checklist prepares one free Ed25519 OTA trust root without putting its
private half in GitHub, Actions, the repository, an application package, a
shell argument, or an environment variable. It does not authorize generating a
key now. Run it only when the release owner schedules the real ceremony.

## Roles and prerequisites

Use two people if possible: a **key custodian** operating a clean offline or
operator-controlled computer, and a **reviewer** independently comparing the
public fingerprint and repository diff. Both record the release key ID,
channels, date, tools, and fingerprint. The key ID is lowercase and immutable,
for example `release-2026-b`.

Use a no-cost Ed25519-capable tool that exports an encrypted PKCS8 PEM private
key and a standard PEM or OpenSSH public key. OpenSSL 3 is one option on
Windows, macOS, and Linux. Keep the output directory outside every source
checkout, cloud-synchronized folder, and shell history. Do not paste private
material into Codex or another model.

The signing utility intentionally provides no key-generation command. It
accepts only an encrypted PKCS8 PEM, requires an interactive terminal, and asks
for the password with no echo. Do not remove that boundary to make automation
easier.

## Public-key inspection

1. The custodian generates the key using the separately reviewed offline tool,
   with a strong unique password, and makes one recoverable encrypted backup in
   a failure domain separate from the operator-controlled primary, outside the
   checkout and synchronized workspace.
2. Export only the public key to removable media. Confirm it starts with
   `-----BEGIN PUBLIC KEY-----` or `ssh-ed25519`, never `PRIVATE KEY`.
3. On a clean online checkout, inspect the public half:

   ```text
   python scripts/release_keyring.py inspect --public-key <public-key-path>
   ```

   On macOS or Linux the path spelling changes, but the command and arguments
   are identical. The JSON output is public information.
4. The reviewer independently runs the inspection or calculates SHA-256 over
   the raw 32-byte Ed25519 public key. Compare the entire
   `sha256:<64-lowercase-hex>` value through a second channel.

## Reviewed import

After both people agree on the full fingerprint, import only the public half:

```text
python scripts/release_keyring.py import --public-key <public-key-path> --key-id release-2026-b --channel beta --expected-fingerprint sha256:<64-lowercase-hex>
```

The importer rejects private-key containers, ambiguous raw 32-byte values,
unknown/duplicate IDs, duplicate public keys, fingerprint mismatches, drift
between trust-store copies, and partial ordinary write failures. It updates:

- `release/keys.json`, used for operator verification;
- `packages/allthecontext/src/allthecontext/update_keys.json`, embedded in the
  application.

Review the complete diff. It must contain only the public entry and its public
fingerprint. Then run:

```text
python scripts/release_keyring.py validate --require-channel beta
python scripts/release_keyring.py audit
python -m pytest tests/unit/test_release_manifest.py tests/unit/test_release_keyring.py tests/unit/test_updater.py
```

Do not commit unless the two keyring files are byte-for-byte identical and the
audit finds no tracked private-key marker or private-key filename.

## Ceremony record: release-2026-a

On 2026-07-22, the release owner generated an Ed25519 key on an
operator-controlled Windows system outside the source checkout and
cloud-synchronized workspace. The private half is encrypted PKCS8 PEM. Only the
standard PEM public half was passed to the repository importer.

The repository inspection utility and an independent raw-key calculation
agreed on:

```text
key_id: release-2026-a
channels: beta
fingerprint: sha256:fe05a2bd52db97f808650fb0e832c49bd704abd62a813af4dedca4994f98e0d4
```

`release/keys.json` and the packaged `update_keys.json` originally contained
this active public entry. On 2026-08-21, before any ATC release was published,
the owner could no longer authenticate to the encrypted private copy. This is
loss of availability, not a claim of compromise. The key ID remains immutable
and its public entry is retained with `status=revoked`; it cannot authorize a
release.

## Ceremony record: release-2026-b

On 2026-08-21, the release owner generated a replacement Ed25519 key on an
operator-controlled Windows system outside the source checkout and synchronized
workspace. The private half is encrypted PKCS8 PEM. Only the standard PEM
public half was passed to the repository importer.

The repository inspection utility and raw-key fingerprint calculation agree
on:

```text
key_id: release-2026-b
channels: beta
fingerprint: sha256:40f95302dd6c0241dc7f639e29693c15e94c5ccae1357b927d039a7e6bf1cf8f
```

The two tracked keyrings contain the same public entries: `release-2026-a` is
revoked and `release-2026-b` is active. The encrypted private key remains
outside the checkout. The human custodian successfully opened and validated the
separate encrypted backup on 2026-08-21. Candidate identity is still required
before the `BETA-R02` receipt can be emitted.

## Beta 0.1.0-beta.4 custody handoff

The machine-side trust root is prepared: `release-2026-b` is active for beta in
both tracked keyrings and its reviewed public fingerprint is recorded above.
The old `0.1.0-beta.1` candidate and its receipts are bound to different source
and artifact bytes and cannot be reused. The unpublished `0.1.0-beta.2` draft
remains a historical occupied identity; its evidence is not rebound.

The remaining sequence is deliberately separated:

1. The custodian completes
   [`RELEASE_KEY_CUSTODY_FORM.md`](RELEASE_KEY_CUSTODY_FORM.md): restore-test one
   encrypted backup kept separate from the primary, verify its encrypted bytes
   matched the fingerprinted primary before the test, and validate its key
   structure with the human-entered passphrase. Record only content-free facts.
2. After every custody check passes, emit exactly one candidate-bound
   `BETA-R02` source receipt. That receipt proves key custody only; it is not a
   release decision, signature, or publication authorization.
3. After every receipt required by the explicitly selected publication profile
   passes, the maintainer reviews each receipt and records one bundle-level
   `approve` or `reject` decision. The initial lean profile has exactly six and
   requires four true acknowledgements; certification has 20. Keep
   `independent_human_review_claimed=false`.
4. Only an explicit `approve` permits the offline Windows x86-64 manifest
   signature, one-time draft upload, protected publication, and channel
   promotion. A null or rejected decision permits none of those actions.

No AI agent, GitHub workflow, or online build needs the private key, password,
backup location, removable-media identifier, or decrypted key bytes.

## Signing day

The custodian downloads and verifies the exact draft artifact set, moves only
the eligible updater ZIP and reviewed signing script to the offline machine,
and signs there. `scripts/release_manifest.py create` first rejects a private
key inside the checkout, then prompts for the encrypted key password without
echo. Transfer only the signed JSON manifest back. Wipe transient decrypted
copies and transfer media according to the recorded ceremony procedure.

For `0.1.0-beta.4`, sign only the Windows x86-64 OTA manifest. The Linux
portable package remains a direct human-install asset and its updater manifest
stays absent until platform-native update/rollback acceptance changes the
candidate's explicit OTA-supported target set. macOS is unsupported and has no
candidate asset or manifest.

## Loss, rotation, or suspected compromise

Never reuse a key ID. Normal rotation first ships a client trusting both old
and new reviewed public keys, then moves manifest signing to the successor,
then marks the predecessor revoked in a later release. If compromise is
suspected, stop release and Pages promotion immediately. A compromised key
cannot authorize its own replacement; use an already trusted independent key
or a separately authenticated manual recovery release.
