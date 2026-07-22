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
- **macOS:** CI produces an `AllTheContext.app` bundle with its STDIO helper.
- **Linux:** CI produces a console-capable `all-the-context` executable that
  also supports the `--mcp-stdio` mode.

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

The current artifacts are unsigned community engineering builds. Candidate CI
wraps each native output in an immutable versioned ZIP with a SHA-256 sidecar,
SPDX metadata, and provenance. Paid Authenticode and Apple notarization are not
release requirements; users must be told that their operating system may show
an unknown-publisher warning. See the [release runbook](RELEASES.md) for the
required offline Ed25519 manifest signing, stable/beta promotion, key rotation,
and downgrade rules.

The native updater verifies and stages a versioned ZIP on every platform. The
packaged Windows application also includes a separate recovery executable, so
it exposes one-click install when running from the complete per-user
installation. The helper journals each phase, registers per-user RunOnce
recovery, waits for Core to stop, refreshes the SQLite backup, verifies the
replacement and its MCP/updater helpers, runs a real loopback Core health check,
and either commits or restores all prior binaries and the database. Its frozen
smoke covers a crash after replacement and a failed-health rollback. macOS app
bundles and Linux standalone archives remain manual-required; those are
deliberate safety states, not missing success messages.

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

## Packaging roadmap

- **Windows:** complete the offline release-key ceremony, verify the immutable
  unsigned candidate and provenance, run a real Ed25519-signed N-1 transaction,
  and then evaluate a Windows service only if per-user startup proves
  insufficient. The per-user installer, transactional updater, and uninstaller
  paths are implemented.
- **macOS:** package the application bundle and its per-user LaunchAgent, keep
  installation manual, and document the normal unsigned-app approval flow.
- **Linux:** wrap the executable in native packages and add a user-service
  integration behind the existing abstraction.

Service installation is isolated behind a platform adapter. None of the shared
Core lifecycle assumes systemd, LaunchAgents, or the Windows Service Manager.
Packaging work must verify initialization, restart, locking, import, export,
and clean shutdown on each target OS before release.
