# MCP API

All The Context uses the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
The stable SDK line at the implementation date supports STDIO and Streamable
HTTP; the adapter is pinned below the pending v2 major release.

## Retrieval tools

| Tool | Purpose | Required client scope |
|---|---|---|
| `bootstrap_context` | Compile mandatory and task-relevant current context within a character budget; return optional bounded `pack_metadata` accounting | `context:read` |
| `search_context` | Structured/FTS catalog search with exact post-policy totals and offset pagination | `context:read` |
| `get_context_item` | Retrieve one permitted current record and provenance | `context:read` |
| `context_status` | Report mode, connectivity, and freshness without private content | `context:status` |

## Ingestion tools

| Tool | Purpose | Required client scope |
|---|---|---|
| `begin_ingestion` | Declare accessible and inaccessible source coverage | `context:ingest` |
| `submit_context_batch` | Submit a bounded resumable/idempotent observation batch | `context:ingest` |
| `finish_ingestion` | Validate coverage and publish automatic decisions | `context:ingest` |
| `propose_memory` | Submit one durable observation for immediate Core evaluation | `context:propose` |
| `report_context_error` | Submit an error signal and optional explicit correction | `context:propose` |
| `forget_context` | Reversibly remove one identified current record only on an explicit user request | `context:propose` |

Inputs are closed typed schemas. Batch and content sizes are validated by Core.
Errors return `{ok:false,error:{code,message}}`. A successful direct observation
response includes `id`, optional `record_id`, `disposition`, `decision_reason`,
`decided_at`, and `policy_version`. The terminal disposition is `applied`,
`reinforced`, `tentative`, or `ignored`. An exact idempotent retry returns the
original observation and decision; batch and Relay envelopes report replay
state separately.

Clients cannot request a disposition or write a current record. Core derives
origin from the authenticated route and ingestion session, evaluates the
client-asserted basis and evidence with hard policy, then records the result
under its versioned memory policy. `propose_memory` defaults
`explicit_user_statement` to false. Setting it true is effective only when
the authenticated principal holds the closed
`witness:explicit_user_statement` grant (or intentional local `admin`/`*`);
authentication and propose scope alone reduce the claim to tentative.
Inference, summaries, and provider/import/Relay text must leave the flag
false. Payload fields cannot select origin, disposition, or force current
state. An explicit correction that is eligible under this policy updates
current context before the successful tool call returns.
`forget_context` requires a record ID and reason, creates a reversible
tombstone before returning, and is not a purge. When routed through dormant
Relay compatibility, it remains a staged observation until Core evaluates it.
Administrative permission, availability, restoration, and irreversible purge
tools remain absent from the model-facing MCP surface.

`search_context` reports the exact count after authorization, request filters,
temporal selection, and admissibility; its cursor can page through every
permitted match. `bootstrap_context` remains a separate bounded retrieval and
budgeted compilation contract. A Core bootstrap response may include the
backward-compatible `pack_metadata` envelope with candidate/selected/omitted
counts, budget usage, provenance-backed selected-item count, and explicit
candidate-pool, budget, or record-limit truncation reasons. It contains no raw
query or ranking diagnostics.

The v1 metadata contract is closed and bounded: candidate and omitted counts
are limited to 50,000, selected and provenance-backed counts to 32, and budget
and used-character counts to 100,000; duplicate- and conflict-suppression
counts are limited to 50,000. Its boolean flags are strict booleans.
`truncation_reasons` is a unique list of at most five values from exactly
`candidate_pool`, `budget`, `record_limit`, `edge_filter`, and `edge_envelope`.

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

The token is absent when the OS credential manager persisted it. The exact Core
data directory is always present so non-default and isolated vaults self-start
against their own identity. Claude's JSON entry carries the equivalent command,
arguments, and environment. `atc init` and `atc config-mcp` remain
contributor/headless alternatives. This local `config.toml` path configures
Codex, not ChatGPT.

After the selected client restarts, normal operation requires no recurring
memory setup or approval work. MCP instructions require automatic
`bootstrap_context` for relevant tasks and automatic `propose_memory` when
durable user context changes. They require `forget_context` only when the user
explicitly asks to forget or remove a particular memory. The adapter generates
a random UUIDv4 operation ID independent of proposal content. Direct
secret-like payloads are refused before the observation ledger. Their durable
receipt contains only opaque IDs, a closed reason code, detector version,
route, time, and replay identity. It contains no raw content, content-derived
hash, prefix, or other offline-guessing verifier.

V1 beta has no hosted MCP endpoint and is same-device only. Core remains
loopback-only by default. A future phone or remote-computer path would use the
same provider-neutral tools but requires separately accepted guided pairing and
encrypted transport before it is advertised.
