# STDIO adapter contract

`atc-mcp` is a lightweight, long-running STDIO process. It reads MCP JSON-RPC
messages from standard input, forwards validated tool calls to Core at
`ATC_TARGET_URL`, and writes protocol messages to standard output. Diagnostics
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
retrieval filtering, automatic memory-policy evaluation, and authoritative
context changes.

## Tools

The stable v1 surface is:

- `begin_ingestion`
- `submit_context_batch`
- `finish_ingestion`
- `propose_memory`
- `report_context_error`
- `forget_context`
- `bootstrap_context`
- `search_context`
- `get_context_item`
- `context_status`

Large archives are uploaded directly to local Core through the dashboard or
CLI. They are never embedded in MCP calls.

`propose_memory` remains the stable compatibility name for submitting a durable
observation. It accepts the observation kind, content, scope, confidence,
sensitivity, source reference, evidence, and idempotency key, plus these
optional policy inputs:

- `explicit_user_statement` (defaults to `true`)
- `entity_key` and `attribute_key` (supplied together for slot-based context)
- `supersedes`
- `observed_at`

Core evaluates the observation during the request and returns its disposition:
`applied`, `reinforced`, `tentative`, or `ignored` (`staged` is reserved for
work that has not reached Core policy evaluation yet). This flow does not create
a user review task.

`report_context_error` sends the error description separately from an optional
explicit correction. Core evaluates a supplied correction automatically. An
error without a replacement remains a tentative signal and does not overwrite
the referenced record.

`forget_context(record_id, reason)` is restricted to an explicit user request
to forget or delete that specific record. Clients must not infer permission to
call it from staleness, low confidence, a topic change, or a general cleanup
request. Local Core applies a reversible deletion and returns `applied`; the
record can still be restored through its retained version history.

When `ATC_TARGET_URL` is a Relay, the Relay may store an encrypted observation
with disposition `staged` until Core imports and evaluates it. Relay never
creates or changes authoritative context, and a staged response does not require
user action. This includes `forget_context`: Relay queues a `context_forget`
observation with the target record ID in its encrypted provenance and does not
claim that deletion has occurred.

## Failure behavior

Connection failures, authentication failures, validation errors, and Core
timeouts are returned as MCP tool errors. The STDIO adapter itself does not
persist or queue writes. Re-running `submit_context_batch` is safe when the
caller reuses the same idempotency key and identical payload.
