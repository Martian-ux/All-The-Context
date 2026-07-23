# Client setup and seamless operation

The intended experience is one-time registration. The desktop wizard creates
separate least-privilege identities for Codex and Claude Desktop, secures their
credentials, and adds the installed STDIO adapter to each detected client.
Existing settings are preserved and backed up. After one client restart,
normal sessions retrieve context and propose durable changes without repeated
setup, token handling, or configuration copy/paste.

Managed entries pin the exact vault, loopback Core URL, installed Core command,
client ID, and credential. On a tool call the adapter verifies Core; if the
known local Core stopped, it starts that exact installation and waits for
readiness. It refuses an unknown listener and never auto-starts a remote target.

The dashboard opens through a short-lived one-use loopback ticket exchanged for
an opaque tab-scoped session. The administrator credential is never put in a
URL, cookie, or browser storage.

The approval policy remains authoritative. MCP instructions ask compatible
clients to call `bootstrap_context` for relevant work and `propose_memory` when
durable context changes, but proposals remain candidates. The dashboard is for
review, import, correction, backup, and administration.

Other local MCP clients use the provider-neutral adapter contract and examples
in [`integrations/`](../../integrations/README.md). The packaged smoke performs
a real initialize/list/call exchange through the installed executable.

V1 does not configure hosted provider connectors. A phone or another computer
must connect directly to Core while Core is online. Core remains loopback-only
by default, and secure guided remote pairing is pending; no Edge or cloud copy
is offered as a workaround.
