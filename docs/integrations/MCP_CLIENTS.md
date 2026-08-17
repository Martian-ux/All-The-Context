# Client setup and seamless operation

The intended experience is one-time registration. The desktop wizard creates
separate least-privilege identities for Codex and Claude Desktop, secures their
credentials, and adds the installed STDIO adapter to each detected client.
Existing settings are preserved and backed up. After one client restart,
normal sessions retrieve context and submit durable observations without
repeated setup, token handling, configuration copy/paste, or memory review.

Managed entries pin the exact vault, loopback Core URL, installed Core command,
client ID, and credential. On a tool call the adapter verifies Core; if the
known local Core stopped, it starts that exact installation and waits for
readiness. It refuses an unknown listener and never auto-starts a remote target.

The dashboard opens through a short-lived one-use loopback ticket exchanged for
an opaque tab-scoped session. The administrator credential is never put in a
URL, cookie, or browser storage.

Core's versioned memory policy is authoritative. MCP instructions ask compatible
clients to call `bootstrap_context` for relevant work and `propose_memory` when
durable context changes. A proposal is an observation, not a current-context
write: Core derives its origin and returns an automatic `applied`,
`reinforced`, `tentative`, or `ignored` decision. The dashboard is optional for
activity inspection, provenance, correction, undo, import, backup, and
administration; it is not an inbox. Clients call `forget_context` only for an
explicit user request naming a particular memory; Core records a reversible
deletion, never a purge.

For the public beta, an ATC-configured same-device Codex or Claude principal may attest
that text was explicitly stated by the user only when Core grants that witness
class. This is an explicit local trust grant, not cryptographic authorship
proof, and authentication by itself is insufficient. Unattested model
inference remains tentative, imported history does not inherit the grant, and
an authorized malicious client that lies about user intent remains a
documented residual risk.

Other local MCP clients use the provider-neutral adapter contract and examples
in [`integrations/`](../../integrations/README.md). The packaged smoke performs
a real initialize/list/call exchange through the installed executable.

V1 beta does not configure hosted provider connectors and is same-device only.
Core remains loopback-only by default. Phone or remote-computer access requires
a separately accepted post-V1 pairing and encrypted-transport design; no Edge
or cloud copy is offered as a workaround.
