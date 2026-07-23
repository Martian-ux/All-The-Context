# STDIO adapter contract

`atc-mcp` is a lightweight, long-running STDIO process. It reads MCP JSON-RPC
messages from standard input, forwards validated tool calls to Core at
`ATC_CORE_URL`, and writes protocol messages to standard output. Diagnostics
go to standard error and must never contain credentials or raw personal
context.

## Configuration

| Environment variable | Required | Meaning |
|---|---:|---|
| `ATC_TARGET_URL` | No | Core endpoint; defaults to `http://127.0.0.1:7337` |
| `ATC_CLIENT_ID` | Yes | Stable registered client identity |
| `ATC_CLIENT_TOKEN` | Yes | Scoped bearer credential |

The adapter must not listen on a network interface, persist tool payloads, or
reinterpret context. Core performs authentication, authorization, validation,
retrieval filtering, and canonical writes.

## Tools

The stable v1 surface is:

- `begin_ingestion`
- `submit_context_batch`
- `finish_ingestion`
- `propose_memory`
- `report_context_error`
- `bootstrap_context`
- `search_context`
- `get_context_item`
- `context_status`

Large archives are uploaded directly to local Core through the dashboard or
CLI. They are never embedded in MCP calls.

## Failure behavior

Connection failures, authentication failures, validation errors, and Core
timeouts are returned as MCP tool errors. The adapter does not queue canonical
writes. Re-running `submit_context_batch` is safe when the caller reuses the
same idempotency key and identical payload.
