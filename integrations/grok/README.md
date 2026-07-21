# Grok configuration status

The All The Context adapter is provider-neutral, but native custom STDIO MCP
configuration for Grok has **not been capability-verified** for this release.
Product and plan capabilities can change independently of this project.

If the Grok client you use explicitly supports a custom STDIO MCP server,
configure command `atc-mcp` with these environment variables:

| Variable | Value |
|---|---|
| `ATC_TARGET_URL` | `http://127.0.0.1:7337` |
| `ATC_CLIENT_ID` | The scoped client ID created by Core |
| `ATC_CLIENT_TOKEN` | The corresponding one-time token |

Do not translate this into a provider API integration. Provider-specific
packages are intentionally outside v1.
