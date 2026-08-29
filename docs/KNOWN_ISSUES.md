# Known issues and accepted beta limitations

This document records the source-level limitations accepted for the first
public beta scope. The exact release decision must still enumerate every
accepted P2/P3 limitation and its evidence. Under `lean_public_beta_v1`, missing
Windows `BETA-L01`, Ubuntu `BETA-L02`, security `BETA-S06`, source `BETA-R01`,
key-custody `BETA-R02`, or inventory `BETA-R03` evidence is a release blocker
and cannot be converted into a known issue. The broader 20-gate
`certification_v1` matrix remains explicitly incomplete rather than being
relabeled pass, skipped, or waived.

## Open Windows security incident — not an accepted limitation

On 2026-08-28, Microsoft Defender security intelligence `1.457.381.0`
classified the unsigned beta.6 installed helper
`%LOCALAPPDATA%\Programs\All The Context\AllTheContextMCP.exe` as
`Trojan:Win32/Sabsik.EN.A!ml` and quarantined it. The restored file is unsigned
and has SHA-256
`62fcf5436988053aa4e43efa81483cfa5b200b0c71c567c96f5dff78c90c37ba`.
The public beta.6 Windows archive still matches its published SHA-256 and its
release workflow is green, but the published package metadata does not contain
an independently comparable digest for every installed executable. Those facts
do not prove either malware or a false positive.

Do not restore, execute, or allow-list that helper solely because it came from
the beta archive. Leave it quarantined, use a trusted source checkout if needed,
or wait for a replacement whose installed executables have component-level
digests and Authenticode signatures. This incident remains open until the exact
installed helper is tied to reproducible release evidence and independently
reassessed. Microsoft’s current detection guidance is available in the
[malware encyclopedia](https://www.microsoft.com/en-us/wdsi/threats/malware-encyclopedia-description?Name=Trojan%3AWin32%2FSabsik.EN.A%21ml&threatId=-2147156305).

| Severity | Limitation and impact | Workaround | Owner | Post-V1 follow-up |
|---|---|---|---|---|
| P2 | The initial lean beta is not complete `certification_v1`: its full client/provider/browser, exact 2 GB, destructive-privacy, packaged recovery, and replacement matrix has not all passed on one candidate. Implemented behavior outside the six lean gates must not be read as a certification claim. | Use the beta only for the documented Windows 11 or Ubuntu 24.04 same-device path, keep current encrypted backups, verify immutable downloads, and consult the certification ledger before relying on an unverified boundary. | Repository maintainer (`Martian-ux`) | Complete the unchanged 20-gate certification matrix and publish its exact receipts without rebinding lean evidence. |
| P2 | Windows packages are unsigned community builds. SmartScreen may warn or require explicit operator confirmation. | Download only from the immutable project release, verify the published SHA-256, SBOM, and provenance, then follow the [platform instructions](operations/PLATFORMS.md). | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |
| P2 | Linux does not have automatic first-beta replacement or rollback. Updates remain manual. | Save a current encrypted backup, download and verify the new direct package, and follow the [release](operations/RELEASES.md) and [recovery](operations/RUNBOOK.md) procedures. | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |
| P2 | macOS is not supported and no Mac package is included in the public beta, even though portability code remains in the source tree. | Use a supported Windows 11 x86-64 or Ubuntu 24.04 LTS x86-64 system. Do not treat a contributor-built DMG as an official beta artifact. | Repository maintainer (`Martian-ux`) | A future support decision requires a new scoped ADR and native acceptance plan. |
| P3 | Linux is distributed as a portable archive rather than a one-click desktop installer. Desktop integration and startup are manual. | Use the supported Ubuntu 24.04 LTS GNOME/Secret Service environment and follow the README [extract and launch](../README.md#install) instructions. | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |
| P2 | Stopped-Core restore is a packaged command-line/helper operation, not a one-click graphical flow. | Use the version-matched packaged recovery mode and the [recovery runbook](operations/RUNBOOK.md); no Python or source checkout is required. | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |
| P3 | The beta does not generate an automated redacted support bundle. | Report only the content-free metadata listed in [SUPPORT.md](../SUPPORT.md); use private vulnerability reporting for sensitive security material. | Repository maintainer (`Martian-ux`) | [Issue #30](https://github.com/Martian-ux/All-The-Context/issues/30) |

The public support path and launch-watch triage policy are in
[SUPPORT.md](../SUPPORT.md). Security-sensitive reports use the private path in
[SECURITY.md](../SECURITY.md).
