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

Repository-side workflows and scripts implement scans and gates. The following
**GitHub product controls** remain operator settings and were observed disabled
or incomplete at implementation time; enable them before beta publication or
record an accepted residual:

| Control | Operator action | Notes |
|---|---|---|
| Secret scanning | Enable for the public repository | Required for BETA-S06 |
| Push protection | Enable secret scanning push protection | Blocks accidental secret pushes |
| Dependabot alerts | Enable dependency alerts | Complements hosted audit gates |
| Dependabot security updates | Optional for solo maintainer | Prefer reviewed PRs over auto-merge |
| Code scanning | Enable CodeQL or equivalent when available | Free for public repos when offered |
| Branch protection on `main` | Require PR + required checks; allow sole-maintainer override only as recorded residual | Workable for one maintainer |
| Actions SHA pinning enforcement | Optional org/repo setting `sha_pinning_required` | Workflows already pin via policy |
| `release-promotion` environment | Required reviewers = maintainer | Deliberate solo approval |
| `github-pages` environment | Required reviewers = maintainer | No auto-promote |
| Immutable releases | Keep enabled (verified) | Owner rechecks before each candidate |

## Repository-side scan and audit commands

Run from a clean checkout. Reports are content-free (class, path, coordinates
only—never matched secret bytes):

```text
python scripts/repository_security_scan.py --scope tree
python scripts/repository_security_scan.py --scope history
python scripts/verify_actions_pins.py
python scripts/dependency_audit.py --ecosystem python
python scripts/dependency_audit.py --ecosystem dashboard
python scripts/verify_dashboard_parity.py
python scripts/build_component_inventory.py --version 0.1.0-beta.1 --output-dir dist/inventory
```

Candidate creation also requires the exact nine-job hosted matrix and local
quality gates:

```text
python scripts/exact_source_gate.py hosted-matrix --repository OWNER/REPOSITORY --source-commit <40-char-sha>
python scripts/exact_source_gate.py local-quality
```

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
  --key-id release-2026-a \
  --expected-public-key-sha256 sha256:fe05a2bd52db97f808650fb0e832c49bd704abd62a813af4dedca4994f98e0d4
```

This gate never accesses, generates, or prints a private release key. Offline
key backup and restore-test remain operator ceremony work documented in
[RELEASE_KEY_CEREMONY.md](RELEASE_KEY_CEREMONY.md).

## Sole-maintainer residual

One human maintainer may approve releases and environment deploys. Release
receipts must name that person, may note AI-assisted implementation/review, and
must set `independent_human_review_claimed` to `false`.
