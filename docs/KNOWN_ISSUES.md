# Known issues and accepted beta limitations

This document records the source-level limitations accepted for the first
public beta scope. The exact release decision must still enumerate every
accepted P2/P3 limitation and its evidence. A missing mandatory platform,
client, provider, 2,000,000,000-byte, security, or recovery receipt is a release
blocker and cannot be converted into a known issue.

| Severity | Limitation and impact | Workaround | Owner | Post-V1 follow-up |
|---|---|---|---|---|
| P2 | Windows packages are unsigned community builds. SmartScreen may warn or require explicit operator confirmation. | Download only from the immutable project release, verify the published SHA-256, SBOM, and provenance, then follow the [platform instructions](operations/PLATFORMS.md). | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |
| P2 | Linux does not have automatic first-beta replacement or rollback. Updates remain manual. | Save a current encrypted backup, download and verify the new direct package, and follow the [release](operations/RELEASES.md) and [recovery](operations/RUNBOOK.md) procedures. | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |
| P2 | macOS is not supported and no Mac package is included in the public beta, even though portability code remains in the source tree. | Use a supported Windows 11 x86-64 or Ubuntu 24.04 LTS x86-64 system. Do not treat a contributor-built DMG as an official beta artifact. | Repository maintainer (`Martian-ux`) | A future support decision requires a new scoped ADR and native acceptance plan. |
| P3 | Linux is distributed as a portable archive rather than a one-click desktop installer. Desktop integration and startup are manual. | Use the supported Ubuntu 24.04 LTS GNOME/Secret Service environment and follow the README [extract and launch](../README.md#install) instructions. | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |
| P2 | Stopped-Core restore is a packaged command-line/helper operation, not a one-click graphical flow. | Use the version-matched packaged recovery mode and the [recovery runbook](operations/RUNBOOK.md); no Python or source checkout is required. | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |
| P3 | The beta does not generate an automated redacted support bundle. | Report only the content-free metadata listed in [SUPPORT.md](../SUPPORT.md); use private vulnerability reporting for sensitive security material. | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |

The public support path and launch-watch triage policy are in
[SUPPORT.md](../SUPPORT.md). Security-sensitive reports use the private path in
[SECURITY.md](../SECURITY.md).
