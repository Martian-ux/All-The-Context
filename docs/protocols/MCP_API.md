# MCP API

All The Context uses the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
The stable SDK line at the implementation date supports STDIO and Streamable
HTTP; the adapter is pinned below the pending v2 major release.

## Retrieval tools

| Tool | Purpose | Required client scope |
|---|---|---|
| `bootstrap_context` | Compile mandatory and task-relevant approved context within a character budget | `context:read` |
| `search_context` | Structured/FTS search with pagination | `context:read` |
| `get_context_item` | Retrieve one permitted record and provenance | `context:read` |
| `context_status` | Report mode, connectivity, and freshness without private content | `context:status` |

## Ingestion tools

| Tool | Purpose | Required client scope |
|---|---|---|
| `begin_ingestion` | Declare accessible and inaccessible source coverage | `context:ingest` |
| `submit_context_batch` | Submit a bounded resumable/idempotent candidate batch | `context:ingest` |
| `finish_ingestion` | Close the session with a coverage report | `context:ingest` |
| `propose_memory` | Suggest one durable change without canonical write access | `context:propose` |
| `report_context_error` | Flag stale/incorrect context for review | `context:propose` |

Inputs are closed typed schemas. Batch and content sizes are validated by Core.
Errors return `{ok:false,error:{code,message}}`. Administrative permission,
approval, availability, correction, and deletion tools are intentionally absent
from the model-facing MCP surface.

## One-time local configuration

`atc init` creates a client credential and immediately emits ready-to-paste
configuration. It omits the token from the block when it was stored in the OS
credential manager. The block can be regenerated with
`atc config-mcp --client-id <id>`; pass `--token` only when no OS credential is
available. A typical Codex STDIO entry is:

```toml
[mcp_servers.all_the_context]
command = "atc-mcp"
env = { ATC_TARGET_URL = "http://127.0.0.1:7337", ATC_CLIENT_ID = "...", ATC_CLIENT_TOKEN = "..." }
required = true
```

Codex reads durable MCP entries from `config.toml`; current Codex surfaces share
that configuration. Clients with direct Streamable HTTP support can instead use
a bearer-protected adapter endpoint. Provider capability claims remain in the
dated integration matrix.
