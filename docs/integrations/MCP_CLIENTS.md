# Client setup and seamless operation

The intended user experience is one-time registration. The desktop wizard
creates separate scoped identities for Codex and Claude
Desktop, secures their credentials, and safely adds the installed STDIO adapter
to each client's normal configuration. Existing settings are preserved,
changes are parsed before replacement, and existing files receive timestamped
backups. After one client restart, normal sessions retrieve context and propose
durable changes without repeated setup, token handling, or configuration copy
and paste.

The generated entries point at the installed MCP executable, pin the actual
loopback Core URL and per-app client ID, and read credentials from the OS
credential store. If the OS store accepts but does not persist a credential,
setup detects that condition before declaring success and uses the explicit
local app-data fallback for this first slice. A user can connect or repair both
supported desktop paths from the dashboard's **Connect apps** page. Other MCP
clients use the same command, arguments, environment, and STDIO contract shown
in [`integrations/`](../../integrations/README.md).

Managed entries also carry the exact installed Core command and opt into
bounded self-healing. On each tool call the adapter cryptographically verifies
the local Core; if it is unreachable after a crash or manual stop, the adapter
starts it and waits briefly for readiness. It refuses an unknown listener on
the port and never auto-starts Core for a hosted Edge target. No repeated user
setup is required.

The desktop opens the dashboard through a short-lived, one-use loopback ticket.
Core exchanges it for an opaque session capability held only in Core memory and
the current tab's `sessionStorage`. The broad administrator credential is never
placed in a cookie, URL, or browser storage. Core also verifies a per-installation
challenge proof before the desktop sends that credential to the loopback port.

The user's approval policy remains the authority. Automatic proposals do not
bypass sensitivity, scope, provenance, or client authorization checks. The
MCP server instructions tell compatible clients to call `bootstrap_context`
automatically at the start of relevant work and to call `propose_memory` when
durable context changes. The dashboard is needed only for exceptions, review,
imports, and administration.

Example configurations and their capability-verification status live in
[`integrations/`](../../integrations/README.md). The packaged smoke test performs
a real initialize/list/call exchange and invokes the read-only `context_status`
tool through the installed executable.

ChatGPT and Claude cloud clients cannot reach the loopback Core. The dashboard
therefore prepares and pairs a personal HTTPS Edge with OAuth 2.1/PKCE, then
shows its MCP URL and provider eligibility notes. An owner link opens a short
registration window; the same recovery code can manage Edge while Core is
temporarily offline. Claude custom connectors linked on web/Desktop can be
used from Claude mobile. ChatGPT developer-mode MCP apps are currently
web-only. The engineering build
cannot offer its Render deployment button until a public release URL is
configured, so it exposes the manual blueprint/enrollment path honestly.

Capability check (official documentation reviewed 2026-07-21):

| Provider path | Eligible surfaces | Repository evidence |
|---|---|---|
| [Claude custom connector](https://support.anthropic.com/en/articles/11503834-building-custom-integrations-via-remote-mcp-servers) | Pro, Max, Team, and Enterprise; add on web/Desktop, then use the existing connector on iOS/Android | OAuth/MCP exercised with the generic SDK client; provider UI handshake not observed |
| [ChatGPT developer-mode MCP app](https://help.openai.com/en/articles/12584461-developer-mode-apps-and-full-mcp-connectors-in-chatgpt-beta) | Eligible Business, Enterprise, and Edu workspaces; web only; admin/owner policy applies | OAuth/MCP exercised with the generic SDK client; provider UI handshake not observed |

Provider pages change independently of this project. The date and unobserved
provider handshake are intentional so the dashboard never turns a generic MCP
test into a provider-specific claim.
