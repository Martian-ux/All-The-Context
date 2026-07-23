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

The desktop wizard detects installed AI clients, creates a distinct
least-privilege identity for each selected client, verifies credential
persistence, and writes Codex and Claude Desktop STDIO entries automatically
with timestamped backups. It does not create a phantom configuration for an
absent application. The user does not copy a token or configuration block. A
typical generated Codex entry is:

```toml
[mcp_servers.all_the_context]
command = "C:\\Users\\user\\AppData\\Local\\Programs\\All The Context\\AllTheContextMCP.exe"
args = []
env = { ATC_TARGET_URL = "http://127.0.0.1:7337", ATC_CORE_DATA_DIR = "C:\\Users\\user\\AppData\\Local\\AllTheContext", ATC_CLIENT_ID = "...", ATC_CLIENT_TOKEN = "..." }
required = true
startup_timeout_sec = 20
```

The token is absent when the OS credential manager persisted it. The exact
Core data directory is always present so non-default and isolated vaults
self-start against their own identity. Claude's JSON entry carries the
equivalent command, arguments, and environment. `atc init`
and `atc config-mcp` remain contributor/headless alternatives. This local
`config.toml` path configures Codex, not ChatGPT.

V1 has no hosted MCP endpoint. A phone or another computer uses the same
provider-neutral tools by connecting directly to Core while Core is online.
Core remains loopback-only by default; secure guided device pairing and
encrypted remote transport are required before that path is advertised as
complete.

MCP instructions require automatic `bootstrap_context` for relevant tasks and
automatic `propose_memory` when durable user context changes.
Generated proposal idempotency keys hash the entire canonical proposal payload,
so exact retries replay cleanly while corrected confidence, evidence, or
sensitivity creates a distinct candidate.
