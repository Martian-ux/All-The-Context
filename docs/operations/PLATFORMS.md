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

The native wizard initializes SQLite and migrations, configures Codex, installs
per-user startup when selected, starts Core, and opens the dashboard. Subsequent
launches recover the desktop credential, start Core if needed, and open the
dashboard directly. The packaged smoke verifies frozen resources, first-run
initialization, a stable installed MCP command, a real MCP handshake and Core
retrieval, authenticated shutdown, and release of files before cleanup.

The current artifacts are unsigned engineering builds. Windows signing,
notarized macOS distribution, and native Linux package metadata remain release
engineering work.

## Source development installation

PowerShell on Windows, without relying on script activation policy:

```text
py -3.12 scripts/bootstrap.py
.\.venv\Scripts\python.exe -m allthecontext.cli init
.\.venv\Scripts\python.exe -m allthecontext.core.app
```

`python scripts/bootstrap.py` is equivalent when `python` is version 3.12 or
newer. The bootstrap validates the existing environment's interpreter and
compiled modules before reusing it; a stale cross-version environment is
cleared and rebuilt.

macOS and Linux shells:

```text
python3 scripts/bootstrap.py
./.venv/bin/python -m allthecontext.cli init
./.venv/bin/python -m allthecontext.core.app
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

- **Windows:** sign the existing self-installing artifact, add publisher
  identity and a standard uninstaller, then evaluate a Windows service only if
  per-user startup proves insufficient.
- **macOS:** sign and notarize the application bundle and its per-user
  LaunchAgent.
- **Linux:** wrap the executable in native packages and add a user-service
  integration behind the existing abstraction.

Service installation is isolated behind a platform adapter. None of the shared
Core lifecycle assumes systemd, LaunchAgents, or the Windows Service Manager.
Packaging work must verify initialization, restart, locking, import, export,
and clean shutdown on each target OS before release.
