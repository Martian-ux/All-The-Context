# Cross-platform operations and packaging path

Core and the STDIO MCP adapter support Python 3.12+ on Windows 11, current
macOS, and Linux without Docker. Application data is resolved with
`platformdirs`; operators should not hard-code its location.

## Current development installation

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

Run the adapter with `atc-mcp`. Core binds only to `127.0.0.1` by default.

## Credential storage abstraction

The credential interface targets Windows Credential Manager, macOS Keychain,
and the system secret service on Linux through `keyring`. Development may use
an explicit environment-based fallback. Plaintext fallback credentials must
never be enabled silently or used for a packaged release.

## Packaging roadmap

- **Windows:** a signed installer containing an embedded Python runtime, Core,
  dashboard assets, `atc-mcp`, and an optional per-user background service.
- **macOS:** a signed and notarized application bundle with a per-user
  LaunchAgent installed only after explicit consent.
- **Linux:** distribution-neutral package first, followed by native packages;
  optional user-service units remain outside shared runtime code.

Service installation is isolated behind a platform adapter. None of the shared
Core lifecycle assumes systemd, LaunchAgents, or the Windows Service Manager.
Packaging work must verify initialization, restart, locking, import, export,
and clean shutdown on each target OS before release.
