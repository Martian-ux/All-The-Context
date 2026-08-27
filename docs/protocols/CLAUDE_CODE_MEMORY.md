# Claude Code explicit-user memory Core contract

This document defines the Core/API half of the Claude Code explicit-user
memory slice. It does not define Claude Code hook, settings, MCP setup, or
credential installation. Those client surfaces are owned by a separate lane.

## Authority boundary

The existing Claude Code read principal is exactly:

```text
context:read
```

Memory writes use a separate opt-in Core registration with exactly these two
scopes:

```text
context:propose
witness:explicit_user_statement
```

The write principal must be distinct from the read principal. Core checks both
the exact scope set and the active durable registration name/id/vault binding;
an HTTP caller cannot manufacture a principal by copying the expected shape.
There is no admin or Relay bypass.

## Routes and requests

All routes are authenticated Core HTTP routes under `/v1/claude-code/memory`.
Each request is bounded, rejects unknown fields, and must use a caller-
generated opaque UUIDv4 `idempotency_key`. The same key and payload should be
reused for a retry. Keys are not content hashes.

| Operation | Request fields |
|---|---|
| `POST /remember` | `kind` (optional closed kind, default `interaction_preference`), `content` (1–8,000 characters), `idempotency_key` (canonical UUIDv4) |
| `POST /correct` | `record_id` (1–200 characters), `content` (1–8,000 characters), `idempotency_key` (canonical UUIDv4) |
| `POST /forget` | `record_id` (1–200 characters), `idempotency_key` (canonical UUIDv4) |

`record_id` identifies an existing Core record for correction or forget. The
forget request intentionally has no free-form reason or content field. Core
uses a fixed audit-safe tombstone description.

The client must not send authority, origin, sensitivity, availability, ACL,
disposition, confidence, source, or witness fields. Such fields are rejected,
not ignored. Core assigns the source as `claude_code` /
`direct_user_statement`, sets the explicit-user witness, applies the
`ongoing_client` origin and policy version, classifies sensitivity, applies
the Core availability and ACL policy, and decides the disposition.

## Responses

Successful remember and correct responses use the existing `ObservationOut`
shape. Forget returns the same observation receipt shape for the generated
`context_forget` observation. The response includes the Core-assigned
observation `id`, `kind`, `content`, `content_hash`, timestamps, disposition,
optional `record_id`, decision reason/time, `observation_origin`, and
`policy_version`, together with the normal bounded candidate metadata.

If the existing direct-secret boundary classifies a remember or correction
payload as secret-like, Core returns the content-free `SecretRefusalOut`
receipt instead of persisting the payload. Forget does not accept caller
content and therefore uses the normal tombstone path.

Repeated requests with the same principal and idempotency key replay the
existing observation. Reusing a key for different content is rejected by the
existing idempotency conflict behavior. Forget is reversible through the
existing Core record lifecycle; this contract does not add purge or restore
routes.

## Evidence rules and client binding

Only a closed, explicitly user-approved operation may use these routes. Ordinary
`UserPromptSubmit` `prompt` text is a read-only query/input surface and is not
direct-user evidence. Model output, tool output, provider output, imported
text, and arbitrary MCP text are also not direct-user evidence. The client
uses native exact-payload confirmation as the authoritative user approval and
adoption step. Core itself does not infer authorship from client metadata or
elicitation; it accepts only a separately registered principal carrying the
exact witness grant and the strict route payload.

The client lane's `UserPromptExpansion` object uses the exact integration
metadata fields `command_name`, `command_args`, and `expansion_type`. The
client must bind and preserve those fields as client-side integration data;
they are not authority grants, do not prove authorship by themselves, and are
not accepted as additional fields by these Core request models. The client
must invoke a mutation route only after native confirmation has closed the
operation and must keep ordinary prompt submission on the read-only path. A
model-visible direct MCP call cannot silently write because it is subject to
the same confirmation gate, but the metadata alone is not proof that the
slash command was personally typed.

Core never falls back to Relay for these operations. Existing replication
events emitted by Core lifecycle machinery are not a Relay authorization path
and do not make Relay authoritative.

## Scope of this slice

This is a local Core/API contract integrated with the separate opt-in Claude
Code hook/config/setup boundary and focused unit/integration coverage. It does
not claim live or private client acceptance, add storage tables or migrations,
provide macOS support, or establish a release/product-exit claim. In
particular, nested elicitation from an `mcp_tool` hook still requires a real
Claude Code acceptance test and fails closed when unavailable.
