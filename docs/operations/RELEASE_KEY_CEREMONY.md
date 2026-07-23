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
for example `release-2026-a`.

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
   with a strong unique password, and makes two recoverable encrypted backups.
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
python scripts/release_keyring.py import --public-key <public-key-path> --key-id release-2026-a --channel beta --expected-fingerprint sha256:<64-lowercase-hex>
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

`release/keys.json` and the packaged `update_keys.json` contain the same active
public entry. The encrypted private key remains outside the checkout. Creating
and verifying two recoverable encrypted backups remains required before the
first production signature.

## Signing day

The custodian downloads and verifies the exact draft artifact set, moves only
the eligible updater ZIP and reviewed signing script to the offline machine,
and signs there. `scripts/release_manifest.py create` first rejects a private
key inside the checkout, then prompts for the encrypted key password without
echo. Transfer only the signed JSON manifest back. Wipe transient decrypted
copies and transfer media according to the recorded ceremony procedure.

For Beta 1, sign only the Windows x86_64 OTA manifest. The macOS DMGs and Linux
portable package remain direct human-install assets. Their updater manifests
stay absent until platform-native update/rollback acceptance changes the
candidate's explicit OTA-supported target set.

## Loss, rotation, or suspected compromise

Never reuse a key ID. Normal rotation first ships a client trusting both old
and new reviewed public keys, then moves manifest signing to the successor,
then marks the predecessor revoked in a later release. If compromise is
suspected, stop release and Pages promotion immediately. A compromised key
cannot authorize its own replacement; use an already trusted independent key
or a separately authenticated manual recovery release.
