# Cross-platform operations and packaging path

Contributor source development currently targets Python 3.12+ on Windows,
macOS, and Linux without Docker. That portability work is not an unbounded
public-beta Python or operating-system compatibility claim: normal beta support
covers only the frozen release artifacts. Application data is resolved with
`platformdirs`; operators should not hard-code its location.

The `0.1.0-beta.3` public support floor is exactly Windows 11 x86-64 and Ubuntu
24.04 LTS x86-64 with GNOME plus a working Secret Service/GNOME Keyring
backend. Other Linux distributions/desktops are experimental. Downloaded
artifacts must pass clean-machine acceptance in both supported families; a
missing receipt leaves the beta in draft. The non-sparse exact-
2,000,000,000-byte journey and its frozen resource/progress/cancel/recovery
budgets run on both supported artifact targets.

macOS implementation, packaging helpers, and hosted regression jobs remain in
the source tree, but macOS is unsupported for this beta. The release-candidate
and publication workflows produce and accept no DMG, Mac updater ZIP, Mac
manifest, or Mac acceptance receipt. The archived Mac acceptance documents are
engineering history only and must not be run or cited as beta evidence.

## Desktop installation

`scripts/build_desktop.py` uses PyInstaller to build on the operating system it
runs on; artifacts are never cross-compiled.

- **Windows:** `AllTheContextSetup.exe` is a single windowed download. It embeds
  the console-subsystem STDIO MCP helper, copies both to the current user's
  local Programs directory, and relaunches the stable copy. No administrator
  access is requested.
- **macOS (unsupported contributor path):** the retained build helper can still
  produce `AllTheContext.app` and a DMG for source-level portability work. Those
  bytes are not consumer release assets, receive no support or acceptance
  credit, and must not be advertised as a beta download.
- **Linux:** CI puts the console-capable `all-the-context` executable in a
  deterministic `tar.gz` portable package. The same executable opens the
  wizard and supports `--mcp-stdio`; it does not require Docker, Python, Bash,
  systemd, or an installer script at runtime.

Each supported release artifact exposes a version-matched recovery/admin surface for
documented stopped-Core restore and deliberate administrator purge:
Windows embeds `AllTheContextRecovery.exe`, and Linux attaches recovery modes to
the console-capable `all-the-context` binary. Contributor-only `atc restore`
remains available for source development but is not the packaged-user gate.
Exact downloaded-artifact recovery acceptance receipts remain required before
publication.

For the public beta, `scripts/package_desktop.py` emits direct downloads named
`all-the-context-VERSION-PLATFORM-ARCHITECTURE-unsigned` with the appropriate
`.exe` or `.tar.gz` extension. Each has an adjacent SHA-256 file,
unsigned-build notice, and path-free JSON package report. These human-install
artifacts are separate from the immutable ZIP used by the OTA updater.
Every candidate job compares `platform.machine()` with its declared asset
architecture before building. The official candidate matrix contains only
`windows:x86_64` and `linux:x86_64`. Retained Mac CI jobs are source-health
regressions and their outputs are never downloaded into the release directory.

The native wizard detects the local timezone, initializes SQLite and
migrations, configures Codex and Claude Desktop with separate
scoped identities, installs per-user startup when selected, starts Core, and
opens an authenticated dashboard without a token prompt. By default its final
action opens All The Context; it does not ask for a hosting account or offer an
Edge deployment. A future mobile product would connect directly to Core and
therefore require Core to be online and securely reachable; mobile is not
present beta behavior. Subsequent desktop launches
recover the desktop credential, start Core if needed, mint a one-use browser
ticket, and open the dashboard directly. The packaged smoke verifies frozen resources, first-run
initialization, a stable installed MCP command, a real MCP handshake and Core
retrieval, authenticated shutdown, and release of files before cleanup.

The clean-install smoke uses a temporary Core data directory, temporary AI
client configuration, and isolated per-user startup location. It forces the
null keyring backend and explicitly enables the insecure development credential
file so non-secret smoke credentials never enter the host OS store; the setup
report must record that development store and must not be treated as real OS
credential acceptance. Real Windows Credential Manager and supported Linux
Secret Service round-trips remain separate packaged-credential and
platform-acceptance gates. On failure the disposable work tree is always deleted; only a
content-free allowlisted diagnostic summary is kept outside that tree, and
headless setup writes a redacted report (windowed Windows packages have no
console). On Windows it also uses uniquely named test-only HKCU keys and
verifies Apps & Features, shortcuts, startup, update recovery, rollback, and
uninstall before removing them on success. It never targets an existing
installation or credential name.

Long-lived processes spawned by a frozen one-file build are launched with
`PYINSTALLER_RESET_ENVIRONMENT=1`. This gives a relaunched app or background
Core an independent extraction lifecycle instead of keeping the completed
setup wrapper alive. The Windows uninstaller also retries removal for a bounded
period from a working directory outside the installation after its frozen
bootloader exits, because the executable can remain briefly locked after the
Python child has stopped.

The managed STDIO adapter also recovers from a later Core crash without asking
the user to reopen the app. It starts only the exact installed Core command,
only for a `127.0.0.1` target, and only after an installation-bound health proof
shows that no unknown service owns the port.

On Windows the installed application registers launchers in the user's actual
Shell-known Programs and Desktop folders (including OneDrive or enterprise
redirection) plus an Apps & Features uninstall entry. Uninstall revokes local
AI-app connections, removes launchers/startup, and keeps the local vault. An
older engineering installation that contains explicit experimental Edge state
uses the retained cleanup guard before uninstall; new V1 installations never
create that state or contact a hosted runtime.

Local AI connection removal is also fail-safe. Uninstall revokes readable
Core client rows, verifies authority-bearing credential deletion when a vault
is missing or corrupt, removes managed config blocks, and scrubs ATC-created
config backups that could contain a development-fallback token. If retained
SQLite cannot be read, uninstall says that its internal rows were not revoked
and warns against restoring that data until it is repaired or deleted.

The current Windows and Linux artifacts are unsigned community engineering builds. The wizard,
filenames, embedded/adjacent notices, and package reports all disclose that
boundary. Paid Authenticode is not a release requirement; Windows users must
expect an unknown-publisher or SmartScreen prompt. Package smoke rejects any
unexpected publisher identity. Candidate CI also produces SHA-256 metadata,
SPDX inventory, and provenance. See the [release runbook](RELEASES.md) for the
required offline Ed25519 manifest signing, stable/beta promotion, key rotation,
and downgrade rules.

The updater code can verify and stage a versioned ZIP across platforms. The
packaged Windows application also includes separate MCP, recovery
(`AllTheContextRecovery.exe`), and updater executables, so it exposes one-click
install when running from the complete per-user installation. The helper
journals each phase, registers per-user RunOnce recovery, waits for Core to
stop, refreshes the SQLite backup, verifies the replacement and its
MCP/recovery/updater helpers, runs a real loopback Core health check, and either
commits or restores all prior binaries and the database. Its frozen smoke covers
a crash after replacement and a failed-health rollback that re-verifies the
recovery helper digest. The Linux archive is portable with recovery modes on
the main console binary, and its OTA handoff remains manual. No Mac package or
OTA handoff belongs to the public beta.

## Source development installation

PowerShell on Windows, without relying on script activation policy:

```text
py -3.12 scripts/bootstrap.py
.\.venv\Scripts\atc.exe init
.\.venv\Scripts\atc.exe open-dashboard
```

`python scripts/bootstrap.py` is equivalent when `python` is version 3.12 or
newer. The bootstrap validates the existing environment's interpreter and
compiled modules before reusing it; a stale cross-version environment is
cleared and rebuilt.

macOS and Linux shells:

```text
python3 scripts/bootstrap.py
./.venv/bin/atc init
./.venv/bin/atc open-dashboard
```

Run the adapter with `atc-mcp`. Core binds only to `127.0.0.1` by default. This
source workflow is for contributors; normal users use the desktop artifact.

## Credential storage abstraction

The credential interface retains adapters for Windows Credential Manager,
macOS Keychain, and the system secret service on Linux through `keyring`. Setup verifies that a
write can be read back before trusting the backend. The first slice has an
explicitly reported local app-data fallback for development and systems without
a functional keyring; it is not equivalent to an OS-protected credential.

Native-package CI performs a unique random set/get/delete against the real
Windows Credential Manager and retains a Mac Keychain regression on Mac CI.
Headless Linux CI exercises and reports the
explicit fallback because it has no logged-in desktop secret service. Every
platform also performs an isolated fallback round trip and startup
install/remove check; no token value or host path is printed or uploaded.

## Linux AppImage spike decision

The public beta uses the deterministic `tar.gz` fallback. CI writes
`appimage-evaluation.json` from `scripts/evaluate_appimage.py` so the decision
is reviewable with the package artifacts. The clean toolchain has no
`appimagetool`; adding one would introduce an architecture-specific native
supply-chain input that is not pinned, checksummed, or provenance-covered in
this repository. Desktop integration also needs acceptance across supported
Linux environments before it can be called more seamless than the portable
archive.

The fallback is built entirely with Python's standard library and launches the
frozen executable directly, without an `AppRun` shell script. Its `0755` tar
member mode is packaging metadata so common Linux extractors preserve the
convenient executable bit. Core authorization, secrecy, locking, and
correctness do not inspect or trust POSIX ownership or mode bits, and the
package does not claim those bits are an access-control boundary. Revisit
AppImage only after a reviewed `appimagetool` digest is pinned and the resulting
AppRun, MCP, startup, update, and cleanup flows pass the supported-desktop
matrix.

The macOS adapter preserves any bundle-internal links produced by PyInstaller
because changing their representation can invalidate the app's structural code
seal. That exception is confined to the native `.app` package; vault files,
locking, credentials, paths, and Core behavior never use symlinks as identity,
authorization, or correctness boundaries. Pre- and post-copy validation rejects
absolute, dangling, cyclic, case-colliding, lexically escaping, or
filesystem-escaping bundle links. The per-user install target and every
existing parent component must be real directories, never links.

## Packaging roadmap

- **Windows:** complete the offline release-key ceremony, verify the immutable
  unsigned beta candidate and provenance, repeat the exact-candidate
  same-version rollback transaction, then run the real Ed25519-signed
  first-published-beta-to-successor N-1 transaction as the next-beta gate. Evaluate a Windows
  service only if per-user startup proves insufficient. The per-user installer,
  transactional updater, and uninstaller paths are implemented.
- **Linux:** complete desktop acceptance of the portable archive and XDG
  autostart, then revisit AppImage or native packages only with pinned tooling.
- **macOS:** retained code is unsupported and outside the beta release plan. A
  future decision to restore support would require a new scoped ADR, candidate,
  documentation pass, and native evidence; existing preparation is not credit.

Service installation is isolated behind a platform adapter. None of the shared
Core lifecycle assumes systemd, LaunchAgents, or the Windows Service Manager.
Packaging work must verify initialization, restart, locking, import, export,
and clean shutdown on each target OS before release.
