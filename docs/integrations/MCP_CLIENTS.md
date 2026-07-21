# Client setup and seamless operation

The intended user experience is one-time registration. The desktop wizard
creates the scoped client, secures its credential, detects Codex, and safely
adds the generated STDIO server to `~/.codex/config.toml`. Existing settings are
preserved, changes are parsed before replacement, and an existing file receives
a timestamped backup. The user restarts Codex once. Normal sessions then
retrieve context and propose durable changes without repeated setup, token
handling, or configuration copy and paste.

The generated entry points at the installed MCP executable, pins the actual
loopback Core URL and client ID, and reads the token from the OS credential
store. If the OS store accepts but does not persist a credential, setup detects
that condition before declaring success and uses the explicit local app-data
fallback for this first slice. Other MCP clients use the same command,
arguments, environment, and STDIO contract shown in [`integrations/`](../../integrations/README.md).

The user's approval policy remains the authority. Automatic proposals do not
bypass sensitivity, scope, provenance, or client authorization checks. The
dashboard is needed only for exceptions, review, imports, and administration.

Example configurations and their capability-verification status live in
[`integrations/`](../../integrations/README.md). The packaged smoke test performs
a real initialize/list/call exchange and invokes the read-only `context_status`
tool through the installed executable.

For cloud or mobile clients, point an HTTP-capable MCP client at the stable
Relay endpoint over HTTPS. Relay serves only approved `always_available`
records and may queue proposals; it cannot create canonical memory.
