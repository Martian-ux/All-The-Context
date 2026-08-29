# Replacement-candidate operator evidence ledger

> Draft, noncanonical operator procedure. This file is not a release receipt,
> a publication approval, an antivirus exclusion, or permission to restore or
> execute the immutable `0.1.0-beta.6` files. The beta.6 incident stays
> unresolved. The companion
> [`operator-evidence-ledger.template.json`](operator-evidence-ledger.template.json)
> is a content-free worksheet and starts in `HOLD`.

This ledger defines the evidence boundary for one future Windows x86-64
replacement candidate. It does not create a candidate, submit anything to
Microsoft, or claim that a candidate exists. It is intentionally separate from
the public release receipts and from the canonical decisions in
[`ADR-167`](../../DECISIONS.md#adr-167-candidate-owned-evidence-precedes-windows-reassessment-and-live-client-credit)
and [`ADR-060`](../../DECISIONS.md#adr-060-candidate-convergence-accepts-executable-product-paths-not-acceptance-shaped-volume).

## Safety boundary

The operator must keep the exact candidate, its component bytes, and any
vendor reports in a controlled disposable workspace. The committed or shared
ledger may contain only bounded metadata:

- candidate version, full source commit, target, artifact names, SHA-256
  digests, byte sizes, manifest/checksum identity, closed reason codes, and
  Boolean or enumerated outcomes;
- the four component roles: `main`, `mcp`, `recovery`, and `updater`;
- scanner/tool version, run identity, and a Boolean result, without raw output;
- whether a Microsoft submission was required, accepted, pending, or resolved,
  plus a Boolean that a bounded case reference is held by the operator.

Do not put raw scan output, detection text, portal URLs, case text, credentials,
tokens, private context, user statements, exports, hostnames, usernames,
absolute paths, or environment dumps in the ledger. Do not put a deterministic
hash of refused or private content in it. Candidate-owned evidence means every
positive claim is bound to the candidate identity and component manifest; an
unsigned caller-authored JSON file is not evidence of a shipped candidate.

The ledger identity is the tuple below. Every stage repeats or resolves to this
same tuple; a changed value starts a new ledger and invalidates all later
stages. A filename, tag, branch, mutable URL, or outer archive digest alone is
not sufficient.

```text
(version, source_commit, candidate_sha256,
 windows_archive_sha256, direct_installer_sha256,
 component_manifest_sha256)
```

The component manifest is the canonical
[`installed-component-manifest-v1.json`](../../../release/installed-component-manifest.schema.json)
plus its `.sha256` sidecar. Its Authenticode field may say only
`not-present` or `present-unverified`; `present-unverified` is not a valid,
trusted, or publisher identity.

## Status rules

Each stage has exactly one of these statuses:

- `pass`: every predicate for the stage is true for the bound candidate and
  the required content-free evidence is present.
- `HOLD`: evidence is missing, pending, ambiguous, unavailable, or not yet
  independently checked. `HOLD` is not credit and cannot authorize execution.
- `fail`: a required predicate is false, an identity mismatch is found, a
  positive malicious determination is returned, or the evidence boundary was
  breached. Stop using that candidate; do not repair a failed record by editing
  its status.

The overall ledger is `fail` if any stage is `fail`, otherwise `HOLD` if any
stage is `HOLD`, and `pass` only when every stage is `pass`. A later result may
resolve a `HOLD` for the same immutable candidate. A new build, repackaging,
manifest change, or component-byte change may not inherit any earlier pass.

## Ordered evidence stages

The stage order is a dependency, not a checklist that may be rearranged.

| Stage | Evidence owner | `pass` requires | `HOLD` requires | `fail` / action |
|---|---|---|---|---|
| `private-build` | Candidate builder | Exact source/version/target are bound; reviewed locked source gates pass; the direct installer, archive package, archive, checksum sidecars, candidate inventory, and component manifest are created without mutation; the manifest binds the four executable roles and the main bytes equal the installer bytes | Build or metadata is incomplete, the source-health identity is unavailable, or a required sidecar/attestation is not yet available | Any source, version, target, package, manifest, checksum, or byte mismatch. Retire the candidate identity and build a new one if correction is needed |
| `independent-verification` | A verifier who did not produce the candidate | On a fresh controlled environment, the verifier recomputes the candidate, archive, installer, manifest, checksum, and four component identities; verifies canonical JSON, archive membership, expected header, provenance/attestation where required, and no links, path escape, duplicate identity, or mutation | The verifier cannot obtain the exact handoff bytes or complete a required verification; never substitute a rebuild or source checkout for the handoff | Any mismatch, unsafe archive/member, changed byte, unverifiable attestation, or rebinding attempt. Do not proceed to scanning |
| `exact-component-scan` | Security scanner plus verifier | The exact archive, direct installer, and each manifest-bound `main`, `mcp`, `recovery`, and `updater` byte set are scanned; the identity is rechecked around the scan; all required scans are clean and the ledger stores only bounded results | A scan is pending, unavailable, incomplete, or produces a detection that has not been classified by Microsoft | A scanner or Microsoft-classified result identifies the exact component as malicious, or the scanned bytes cannot be bound. Keep the candidate quarantined and do not restore, execute, or allow-list it |
| `microsoft-submission` | Maintainer, only when required | If every exact scan is clean, record `submission_required=false` and pass. If a persistent detection exists, submit only the exact independently verified component through the approved Microsoft channel and record bounded submission metadata | Submission is required but not yet accepted, cannot be made, or the vendor has not acknowledged the exact component identity | The exact sample cannot be reproduced/bound, the submission boundary would disclose private data, or the submission is rejected for an identity reason. Keep the candidate blocked |
| `microsoft-result` | Microsoft result reconciled by verifier | When no detection exists, record `result=not_required` and pass. When submission was required, pass only for a clear result tied to the exact component digest that removes the detection; a submission acknowledgement alone is not a result | Result is pending, unavailable, ambiguous, or tied to a different digest/version | Microsoft confirms malicious content, or the result cannot be tied to the manifest-bound component. Keep the candidate blocked and do not infer a false positive |
| `execution-authorization` | Separate explicit operator decision | All earlier stages are `pass`; candidate identity is unchanged; a human operator records an explicit authorization against the exact candidate and four-component manifest before any installed-candidate/client journey | Any earlier stage is not `pass`, authorization is absent, or the identity changed since the last verification | Authorization was attempted despite a failed prerequisite, or the authorized bytes no longer match. Revoke the pending execution and start a new ledger |

### Private build

The builder uses the existing source and packaging contracts; the procedure is
static/build evidence only until the final authorization stage. The relevant
implementation surfaces are the
[`release-candidate` workflow](../../../.github/workflows/release-candidate.yml),
[`package_desktop.py`](../../../scripts/package_desktop.py),
[`build_release_assets.py`](../../../scripts/build_release_assets.py), and
[`installed_component_manifest.py`](../../../scripts/installed_component_manifest.py).

For a future operator run, the build sequence is conceptually:

```text
python scripts/exact_source_gate.py local-quality
python scripts/build_desktop.py
python scripts/package_desktop.py --platform windows --architecture x86_64 --version <VERSION> --source-commit <SOURCE_COMMIT> --installed-component-output-dir dist/installed-component-package --output-dir dist/release
python scripts/build_release_assets.py --source dist/installed-component-package --output-dir dist/release --version <VERSION> --platform windows --architecture x86_64
python scripts/installed_component_manifest.py verify-archive --archive <ARCHIVE> --direct-package <DIRECT_INSTALLER> --main <MAIN> --mcp <MCP> --recovery <RECOVERY> --updater <UPDATER> --source-root <CONTROLLED_BUILD_ROOT> --version <VERSION> --source-commit <SOURCE_COMMIT> --platform windows --architecture x86_64
```

The angle-bracket values are operator inputs, not evidence. The candidate
workflow remains the source of truth for the complete Windows/Linux inventory,
candidate inventory, attestations, and draft handling. The builder must hand
the verifier the exact archive, direct installer, manifest and checksum, and
the four exact component byte sets. The verifier must not rebuild those bytes
and call the rebuild equivalent.

Do not execute an installed beta.6 helper or a replacement candidate while
producing this ledger. Build output, archive inspection, hashing, JSON
canonicality, and signature/attestation verification do not constitute client
acceptance. Packaged client execution starts only after the separate final
stage is `pass`.

### Independent verification and exact-component scanning

The verifier records one bounded row for each of the following identities:

```text
archive, direct-installer, main, mcp, recovery, updater
```

The row contains the expected and observed SHA-256/size equality as a Boolean,
not the scanner's raw report. Verification must use the exact bytes handed off
by the builder and must repeat the
[`verify-archive`](../../../scripts/installed_component_manifest.py) check with
the expected version, source commit, Windows platform, and x86-64 architecture.
The archive must contain the canonical package, manifest, and checksum members;
the manifest alone is not a substitute for scanning each installed executable.

The security scan is a separate stage from provenance verification. A clean
outer archive does not make an unscanned MCP, recovery, updater, or main
executable clean. Conversely, a scan result for a renamed, rebuilt, or
unbound file does not clear the candidate. If bytes change between hashing and
scanning, the result is not clean: record `HOLD` or `fail` according to whether
the identity can be re-established, and do not continue silently.

### Microsoft submission and result

Microsoft reassessment is conditional, but the conditional branch is explicit:

1. If all exact-component scans are clean and there is no persistent endpoint
   detection, set `submission_required=false` and `result=not_required`; this
   is a pass for both Microsoft stages and is not a claim of Microsoft review.
2. If a persistent detection remains, preserve the exact independently
   verified component and submit that component through the approved Microsoft
   channel. Submission must not contain private context, credentials, or raw
   user data. Record only candidate/component digests, bounded status, and the
   presence of an operator-held case reference.
3. Keep both stages `HOLD` until Microsoft returns a result for that exact
   component identity. Do not treat submission, a portal receipt, elapsed time,
   a clean second scanner, or an operator belief as a clear result.
4. A result tied to another version, archive, component, or digest does not
   resolve this candidate. A malicious determination is `fail`; an ambiguous
   result remains `HOLD`.

### Later execution authorization

Execution authorization is a new decision after the security chain. It is not
inferred from green source CI, an outer archive checksum, a signed attestation,
an unsigned caller-authored manifest, or a client harness. The authorizing
operator records the candidate identity, component-manifest digest, decision
time, and a bounded authorization identifier in the ledger, with no raw
context. The authorization covers only the exact candidate-owned runtime.

After authorization, a separate candidate-owned packaged journey may collect
the ordinary-use client evidence required by the product plan. A source Core,
synthetic lifecycle primitive, or model-visible mock may be useful for
development, but it can report only `HOLD` for this gate. Any later client
receipt must repeat the exact candidate and component identities and must not
be backfilled from this worksheet.

## Content-free ledger fields

The JSON worksheet deliberately contains no real candidate values. A completed
operator record may fill only these categories:

```text
identity: version, source_commit, candidate/archive/installer/manifest SHA-256,
          byte sizes, target, and component role
stage:    pass | HOLD | fail, owner class, closed reason, required booleans,
          bounded tool/run identity, and exact-component result counts
vendor:   submission_required, bounded submission/result status,
          case_reference_present
decision: explicit authorization boolean, bounded authorization identifier,
          decision time, and unchanged-identity boolean
```

Use the template as a new file for each candidate identity. Never overwrite a
prior candidate ledger or turn a `HOLD`/`fail` into `pass` by deleting a field.
