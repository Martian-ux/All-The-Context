# Repository security and release-control checklist

This document is the operator-facing companion to B-106, B-107, and the
repository-side scaffolding for B-002 and B-108. It does **not** authorize
enabling live repository controls, rotating release keys, publishing releases,
or changing branch protection. Those remain coordinator/operator actions.

## Private security intake

- GitHub private vulnerability reporting was enabled and verified on 2026-07-25.
- Public reporters must use
  [SECURITY.md](../../SECURITY.md) and the private advisory form.
- Keep credentials, personal context, exploit details, and raw logs in the
  private path. Public issues may use only sanitized, content-free summaries.

### Sanitized public versus private tracking

| Content | Track publicly | Track privately |
|---|---|---|
| Closed reason codes, gate IDs, severity | Yes | Yes |
| Versions, digests, workflow URLs | Yes | Yes |
| Credentials, tokens, private keys | Never | Private only |
| Raw conversations, exports, personal context | Never | Private only |
| Exploit steps that enable abuse before a fix | Never | Private only |
| User-visible limitations after fix/mitigation | Yes | Optional |

### Emergency path

1. If a P0/P1 security issue is live, stop promotion and remove mutable channel
   pointers. Immutable release assets are never replaced in place.
2. Open or update a **private** vulnerability report with full detail.
3. File a public issue only with a content-free title/body (impact class, gate
   ID, whether a fixed candidate exists). Do not paste secrets or context.
4. The sole human maintainer records the decision without claiming independent
   human review or separation of duties.

## Feasible automated defenses (operator-owned settings)

Repository-side workflows and scripts implement scans and gates. The
coordinator verified these **GitHub product controls** on 2026-07-26:

| Control | Live state | Notes |
|---|---|---|
| Secret scanning | Enabled | Required for BETA-S06 |
| Push protection | Enabled | Blocks accidental secret pushes |
| Dependabot alerts | Enabled | Complements hosted audit gates |
| Dependabot security updates | Enabled | PRs remain review-gated; no auto-merge claim |
| Code scanning | CodeQL default setup enabled | Initial findings are fixed and rescanned, never dismissed to clear a gate |
| Branch protection on `main` | Strict PR plus 11 required CI/CodeQL contexts (eight canonical CI and three CodeQL); conversations required; force push/deletion off | Administrator bypass is the recorded sole-maintainer recovery residual |
| Actions SHA pinning enforcement | `sha_pinning_required=true` | Repository policy and workflow pin verifier both apply |
| `release-promotion` environment | Sole maintainer required; administrator bypass off; protected branches only | Self-review is available and must be recorded truthfully |
| `github-pages` environment | Sole maintainer required; administrator bypass off; custom `main` policy | No auto-promote |
| Immutable releases | Enabled | Owner rechecks immediately before each candidate |

Optional secret-scanning validity checks and non-provider patterns remain
disabled and are not part of the claimed beta baseline. Reverify every live
setting and require zero unresolved release-blocking alerts on the final
candidate SHA.

## Repository-side scan and audit commands

Run from a clean checkout. Reports are content-free (class, path, coordinates
only—never matched secret bytes):

```text
python scripts/repository_security_scan.py --scope tree
python scripts/repository_security_scan.py --scope history
python scripts/verify_actions_pins.py
python scripts/dependency_audit.py --ecosystem python
# Python audit uses the frozen hashed uv.lock export (dev+packaging), not ambient resolve
python scripts/dependency_audit.py --ecosystem dashboard
python scripts/verify_dashboard_parity.py
python scripts/build_component_inventory.py --version 0.1.0-beta.6 --output-dir dist/inventory
```

Candidate creation also requires the exact eight-job hosted CI set—six supported-
host matrix slots plus `Repository security gates` and `Dashboard production
asset parity`—and local quality gates:

```text
python scripts/exact_source_gate.py hosted-matrix --repository OWNER/REPOSITORY --source-commit <40-char-sha>
python scripts/exact_source_gate.py local-quality
```

The replacement-candidate workflow is a private, approval-gated artifact
handoff with an exact allowlist. It does not publish or execute a candidate and
does not provide antivirus evidence. The immutable `0.1.0-beta.6` remains the
current downloadable public prerelease with its Defender incident unresolved;
`0.1.0-beta.7` is only the private replacement-source/candidate slot. No beta.7
candidate or exact candidate-bound Microsoft closed no-malware reassessment
evidence exists. Future acceptance must bind that evidence to the exact
candidate before any release recommendation.

## Acceptance receipts (B-002 scaffolding)

Schemas and templates live under `release/`:

- `acceptance-receipt.schema.json`
- `acceptance-receipt.template.json` (`status: not_run` — claims no evidence)
- `acceptance-receipt-bundle.schema.json`
- `acceptance-receipt-bundle.template.json`

Validate:

```text
python scripts/acceptance_receipt.py validate --receipt path/to/receipt.json
python scripts/acceptance_receipt.py validate-bundle --bundle path/to/bundle.json --require-publication-gates
```

## Protected publication preflight (B-108 scaffolding)

Publication workflows already require typed phrases and candidate digests. The
repository-side gate additionally requires the receipt bundle and public-key
identity:

```text
python scripts/publication_gate.py \
  --release-dir dist/release \
  --candidate-sha256 <hex> \
  --source-commit <40-char-sha> \
  --receipt-bundle path/to/bundle.json \
  --key-id release-2026-b \
  --expected-public-key-sha256 sha256:40f95302dd6c0241dc7f639e29693c15e94c5ccae1357b927d039a7e6bf1cf8f
```

This gate never accesses, generates, or prints a private release key. Offline
key backup and restore-test remain operator ceremony work documented in
[RELEASE_KEY_CEREMONY.md](RELEASE_KEY_CEREMONY.md) and the content-free
[RELEASE_KEY_CUSTODY_FORM.md](RELEASE_KEY_CUSTODY_FORM.md). One restore-tested
encrypted backup kept separate from the operator-controlled primary must
precede the one candidate-bound
`BETA-R02` source receipt. Offline signing waits for every receipt and
acknowledgement required by the explicitly selected publication profile and an explicit `approve` with
`independent_human_review_claimed=false`.

## Sole-maintainer residual

One human maintainer may approve releases and environment deploys. Release
receipts must name that person, may note AI-assisted implementation/review, and
must set `independent_human_review_claimed` to `false`.
