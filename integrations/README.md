# MCP client examples

All The Context exposes one provider-neutral MCP tool contract. The local
adapter speaks MCP over STDIO and forwards requests to the loopback Core over
HTTP. Configure it once per client with a scoped client ID and token.

| Client | Example | Status in this repository |
|---|---|---|
| Codex app / CLI / editor extension | [`codex/config.toml.example`](codex/config.toml.example) | Desktop wizard and dashboard write/repair Codex config automatically; generic packaged MCP handshake exercised |
| Claude Desktop | [`claude/claude_desktop_config.json.example`](claude/claude_desktop_config.json.example) | Desktop wizard and dashboard write/repair the config automatically; generic packaged MCP handshake exercised |
| Grok | [`grok/README.md`](grok/README.md) | Native custom MCP capability is not verified; use only if the selected Grok client supports custom STDIO MCP servers |

These examples do not imply a provider partnership or guarantee that every
edition of a client supports custom MCP servers. The generic adapter and its
contract are the supported integration surface for v1.

## Normal one-time setup

Install All The Context, leave the two desktop-app connection choices enabled,
and restart each installed AI app once. The dashboard's **Connect apps** page
shows status and can repair either configuration. Each app receives a distinct,
revocable client identity. No token, command, or JSON/TOML editing is part of
the normal user path.

## Contributor/manual setup

1. Start Core with `python -m allthecontext.core.app`.
2. Create a scoped client with `python -m allthecontext.cli client-add NAME`.
3. Install the project so `atc-mcp` is on the client's executable path.
4. Copy the relevant example into the client configuration and replace the
   placeholder client ID and token.
5. Restart the client and call `context_status`.

Never commit a real token. Use the operating system's credential store when
the client can inject environment variables securely.
