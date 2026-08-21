# Release key custody form

This form covers only the human custody prerequisite for `BETA-R02`. It is not
the R02 receipt, a maintainer decision, a signature, or publication
authorization. Complete it outside the source checkout if the operator record
contains machine-specific metadata.

Do not record a private key, password, recovery phrase, backup filename, local
path, device serial, account name, cloud provider, removable-media identifier,
or decrypted key bytes. The only key value permitted here is the already-public
fingerprint.

## Fixed public identity

- Key ID: `release-2026-b`
- Channel: `beta`
- Expected public fingerprint:
  `sha256:40f95302dd6c0241dc7f639e29693c15e94c5ccae1357b927d039a7e6bf1cf8f`
- Candidate version: `0.1.0-beta.5`
- Candidate source commit (fill after candidate freeze):
- Candidate inventory SHA-256 (fill after candidate freeze):

## Separate encrypted backup

- Failure-domain class (generic description only):
- Restore-test UTC date:
- Restore tool and public version:
- Encrypted backup bytes matched the operator-controlled primary before the test: [ ]
- Encrypted container opened successfully: [ ]
- Restore tool reported a valid private-key structure: [ ]
- Restored key remained outside checkout and synchronized storage: [ ]
- The matching primary's public fingerprint equals the fixed value above: [ ]
- No transient restored/decrypted file was created, or it was removed: [ ]

## Separation and content-free checks

- The backup is in a failure domain separate from the operator-controlled primary: [ ]
- Neither the primary nor backup is in the checkout or a synchronized workspace: [ ]
- No private material or private locator appears in this form: [ ]
- The custodian reviewed every checked statement: [ ]
- Custody prerequisite result (`pass` only when every box above is checked):

## Later lifecycle boundaries

After a passing custody prerequisite, create exactly one canonical,
candidate-bound `BETA-R02` receipt with `evidence_kind=source` and
`content_free=true`. Do not include this form in the release asset set or treat
it as the receipt.

The maintainer decision remains null until every receipt selected by the named
publication profile, including R02, exists and is reviewed. The initial lean
profile requires six receipt IDs and four true acknowledgements; certification
requires 20. An approval must enumerate every selected receipt ID exactly once and retain
`independent_human_review_claimed=false`. Only that explicit approval permits
offline signing, protected immutable publication, and channel promotion.
