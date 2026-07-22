# Cross-platform operations and packaging path

Core and the STDIO MCP adapter support Python 3.12+ on Windows 11, current
macOS, and Linux without Docker. Application data is resolved with
`platformdirs`; operators should not hard-code its location.

## Desktop installation

`scripts/build_desktop.py` uses PyInstaller to build on the operating system it
runs on; artifacts are never cross-compiled.

- **Windows:** `AllTheContextSetup.exe` is a single windowed download. It embeds
  the console-subsystem STDIO MCP helper, copies both to the current user's
  local Programs directory, and relaunches the stable copy. No administrator
  access is requested.
- **macOS:** CI puts `AllTheContext.app` and its STDIO helper in an unsigned
  disk image. Opening the app copies the complete bundle to
  `~/Applications/All The Context.app` before setup and relaunches that stable
  per-user copy. Startup therefore never points into a transient mounted disk
  image and no administrator access is requested. After final bundle metadata
  is written, the build restores an identity-free ad-hoc structural seal and
  verifies it. This costs nothing and prevents a corrupt bundle; it is not a
  Developer ID signature or notarization and does not suppress Gatekeeper.
- **Linux:** CI puts the console-capable `all-the-context` executable in a
  deterministic `tar.gz` portable package. The same executable opens the
  wizard and supports `--mcp-stdio`; it does not require Docker, Python, Bash,
  systemd, or an installer script at runtime.

`scripts/package_desktop.py` emits direct downloads named
`all-the-context-VERSION-PLATFORM-ARCHITECTURE-unsigned` with the appropriate
`.exe`, `.dmg`, or `.tar.gz` extension. Each has an adjacent SHA-256 file,
unsigned-build notice, and path-free JSON package report. These human-install
artifacts are separate from the immutable ZIP used by the OTA updater.
Every native job compares `platform.machine()` with its declared asset
architecture before building. Beta 1 uses the current standard `macos-26`
ARM64 and `macos-26-intel` x86-64 public-repository runners. They produce
separate, honestly labeled assets and run the identical clean-install,
Keychain, LaunchAgent, MCP, and package-trust matrix; neither is mislabeled as
universal.

The native wizard detects the local timezone, initializes SQLite and
migrations, configures Codex and Claude Desktop with separate
scoped identities, installs per-user startup when selected, starts Core, and
opens an authenticated dashboard without a token prompt. By default its final
action continues directly to the guided, optional hosted Edge setup for
supported web/mobile clients, after disclosing the external hosting cost and
provider limitations. Subsequent launches
recover the desktop credential, start Core if needed, mint a one-use browser
ticket, and open the dashboard directly. The packaged smoke verifies frozen resources, first-run
initialization, a stable installed MCP command, a real MCP handshake and Core
retrieval, authenticated shutdown, and release of files before cleanup.

The clean-install smoke uses a temporary Core data directory, temporary AI
client configuration, and isolated per-user startup location. On Windows it
also uses uniquely named test-only HKCU keys and verifies Apps & Features,
shortcuts, startup, update recovery, rollback, and uninstall before removing
them. It never targets an existing installation or credential name.

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
redirection) plus an Apps & Features uninstall entry. Uninstall first verifies
and terminally removes active records from a connected hosted Edge, revokes local AI-app
connections, removes launchers/startup, and keeps the local vault. If Edge is
offline or its state is corrupt, uninstall stops before claiming verified
remote decommissioning. A prepared but unpaired Edge is also preserved until
the user confirms that any possible hosted service, disk, and backups were
deleted.

Local AI connection removal is also fail-safe. Uninstall revokes readable
Core client rows, verifies authority-bearing credential deletion when a vault
is missing or corrupt, removes managed config blocks, and scrubs ATC-created
config backups that could contain a development-fallback token. If retained
SQLite cannot be read, uninstall says that its internal rows were not revoked
and warns against restoring that data until it is repaired or deleted.

The current artifacts are unsigned community engineering builds. The wizard,
filenames, embedded/adjacent notices, and package reports all disclose that
boundary. Paid Authenticode and Apple Developer ID/notarization are not release
requirements; users must expect an unknown-publisher, SmartScreen, or
Gatekeeper prompt. PyInstaller can add a structural macOS ad-hoc signature; it
has no publisher identity and is not notarization. Package smoke rejects any
unexpected publisher identity. Candidate CI also produces SHA-256 metadata,
SPDX inventory, and provenance. See the [release runbook](RELEASES.md) for the
required offline Ed25519 manifest signing, stable/beta promotion, key rotation,
and downgrade rules.

The native updater verifies and stages a versioned ZIP on every platform. The
packaged Windows application also includes a separate recovery executable, so
it exposes one-click install when running from the complete per-user
installation. The helper journals each phase, registers per-user RunOnce
recovery, waits for Core to stop, refreshes the SQLite backup, verifies the
replacement and its MCP/updater helpers, runs a real loopback Core health check,
and either commits or restores all prior binaries and the database. Its frozen
smoke covers a crash after replacement and a failed-health rollback. The macOS
app self-installs per user but OTA handoff remains manual. The Linux archive is
portable and its OTA handoff remains manual. Those are deliberate safety
states, not missing success messages.

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

The credential interface targets Windows Credential Manager, macOS Keychain,
and the system secret service on Linux through `keyring`. Setup verifies that a
write can be read back before trusting the backend. The first slice has an
explicitly reported local app-data fallback for development and systems without
a functional keyring; it is not equivalent to an OS-protected credential.

Native-package CI performs a unique random set/get/delete against the real
Windows Credential Manager and macOS Keychain and fails those platform jobs if
the round trip is unavailable. Headless Linux CI exercises and reports the
explicit fallback because it has no logged-in desktop secret service. Every
platform also performs an isolated fallback round trip and startup
install/remove check; no token value or host path is printed or uploaded.

## Linux AppImage spike decision

Beta 1 uses the deterministic `tar.gz` fallback. CI writes
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
  unsigned candidate and provenance, run a real Ed25519-signed N-1 transaction,
  and then evaluate a Windows service only if per-user startup proves
  insufficient. The per-user installer, transactional updater, and uninstaller
  paths are implemented.
- **macOS:** exercise the unsigned disk image, stable per-user app copy,
  LaunchAgent, Keychain, and manual OTA handoff on an operator-owned current
  macOS machine; a future paid-signing path remains optional.
- **Linux:** complete desktop acceptance of the portable archive and XDG
  autostart, then revisit AppImage or native packages only with pinned tooling.

Service installation is isolated behind a platform adapter. None of the shared
Core lifecycle assumes systemd, LaunchAgents, or the Windows Service Manager.
Packaging work must verify initialization, restart, locking, import, export,
and clean shutdown on each target OS before release.
