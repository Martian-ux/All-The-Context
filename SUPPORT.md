# Support

All The Context `0.1.0-beta.6` is an unsigned community beta for Windows 11
x86-64 and Ubuntu 24.04 LTS x86-64, maintained on a best-effort basis. macOS is
unsupported and no Mac package is distributed. The retained Mac source paths
are for contributors, not supported users. Before reporting a problem, review
the [known issues](docs/KNOWN_ISSUES.md),
[platform guidance](docs/operations/PLATFORMS.md), and
[recovery runbook](docs/operations/RUNBOOK.md).

## Public, non-sensitive support

Use the
[public issue form](https://github.com/Martian-ux/All-The-Context/issues/new)
for sanitized product and installation reports. Search existing issues first.
Include only content-free facts:

- affected version and operating system;
- impact class or acceptance gate ID, if known;
- package, candidate, or report SHA-256;
- bounded error category and reproducible steps that contain no personal data;
- whether the documented workaround helped.

Do not attach conversation exports, databases, logs containing context, local
paths, credentials, tokens, private keys, or screenshots containing personal
information.

## Security and sensitive reports

Potential vulnerabilities, credential exposure, or reports that require
sensitive reproduction material belong in
[private vulnerability reporting](https://github.com/Martian-ux/All-The-Context/security/advisories/new),
not a public issue. Follow the repository [security policy](SECURITY.md).

## Triage and launch watch

The repository maintainer triages reports by the public-beta severity contract:
P0/P1 reports block publication or stop promotion; an accepted P2/P3 must have public
impact, workaround, owner, limitation copy, and a post-V1 issue. Reports without
enough safe information remain unconfirmed rather than being guessed closed.
The initial launch watch is tracked by
[B-206](https://github.com/Martian-ux/All-The-Context/issues/29) and closes only
after every received report is triaged and the documented critical paths remain
healthy.
