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

- Key ID: `release-2026-a`
- Channel: `beta`
- Expected public fingerprint:
  `sha256:fe05a2bd52db97f808650fb0e832c49bd704abd62a813af4dedca4994f98e0d4`
- Candidate version: `0.1.0-beta.2`
- Candidate source commit (fill after candidate freeze):
- Candidate inventory SHA-256 (fill after candidate freeze):

## Backup A

- Failure-domain class (generic description only):
- Restore-test UTC date:
- Restore tool and public version:
- Encrypted container opened successfully: [ ]
- Restored key remained outside checkout and synchronized storage: [ ]
- Public half derived from restored copy: [ ]
- Full public fingerprint matched the fixed value above: [ ]
- Transient restored/decrypted copy removed after the test: [ ]

## Backup B

- Failure-domain class (generic description only):
- Restore-test UTC date:
- Restore tool and public version:
- Encrypted container opened successfully: [ ]
- Restored key remained outside checkout and synchronized storage: [ ]
- Public half derived from restored copy: [ ]
- Full public fingerprint matched the fixed value above: [ ]
- Transient restored/decrypted copy removed after the test: [ ]

## Separation and content-free checks

- Backup A and Backup B are in distinct failure domains: [ ]
- Neither backup is in the checkout or a synchronized workspace: [ ]
- No private material or private locator appears in this form: [ ]
- The custodian reviewed every checked statement: [ ]
- Custody prerequisite result (`pass` only when every box above is checked):

## Later lifecycle boundaries

After a passing custody prerequisite, create exactly one canonical,
candidate-bound `BETA-R02` receipt with `evidence_kind=source` and
`content_free=true`. Do not include this form in the release asset set or treat
it as the receipt.

The maintainer decision remains null until all 20 unique prepublication pass
receipts, including R02, exist and are reviewed. An approval must enumerate all
20 receipt IDs exactly once and retain
`independent_human_review_claimed=false`. Only that explicit approval permits
offline signing, protected immutable publication, and channel promotion.
