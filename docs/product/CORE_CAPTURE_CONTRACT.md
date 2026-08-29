# Core continuous-capture contract

This document defines the Core-side contract for continuous conversation
capture. Provider adapters may submit bounded evidence after the user has
explicitly enabled capture, but Core remains the authority for provenance,
sensitivity, access control, formation, reconciliation, and retrieval.

## Authenticated route

The canonical route is `POST /v1/lifecycle/events`. It accepts a flat,
versioned `CaptureEventRequest` with only these fields:

- `schema_version` (currently `1`)
- bounded opaque `event_id`, `session_id`, and `conversation_id`
- canonical UUIDv4 `idempotency_key`
- positive `sequence`
- `role`: `user`, `assistant`, `tool`, or `imported`
- bounded `content` (CR/LF/tab are permitted; unsafe control characters are
  rejected)
- optional offset-bearing `observed_at`

Unknown fields are rejected. Provider, authority, provenance, witness,
sensitivity, ACL, and explicitness fields are not part of this request. Core
derives them from the exact registered client principal authenticated with the
`context:capture` capability. `context:capture` is allowlisted for explicit
setup provisioning, but is not included in ordinary client or Claude defaults.
Capture never falls back to Relay for authority.

Successful responses are bounded receipts of the form
`{ "ok": true, "status": "captured" | "replayed", ... }`. A repeated
idempotency key replays the existing receipt without duplicating the event or
formed memory. Refusals are content-free and do not echo the submitted value.

## Event, formation, and reconciliation

Core retains the raw turn as bounded observation evidence and routes it through
the existing lifecycle normalization and event-observation formation seam.
Role controls the witness and evidence class:

- a live user turn is direct-user evidence;
- assistant/model output is model self-attestation and inert observed data;
- tool output is host-observed data;
- imported text is untrusted source data.

Only direct user content is eligible for the deterministic live-user claim
extractor. High-confidence first-person preferences, facts, goals, and working
state produce a Core-derived live-user candidate under the separate
`live_user_evidence` policy. For example, “I prefer tabs” forms an
`interaction_preference`, allowing existing preference bootstrap and
reconciliation behavior to apply. Equivalent claims reinforce/deduplicate;
later slot values supersede stale values while retaining history and
provenance. Irrelevant or task-local turns remain observation-only.

Live capture is not the explicit remember/correct/forget command path. A live
user prompt is conversation evidence, not an explicit durable-memory command.
Assistant, tool, model, or imported content cannot independently establish a
user fact.

## Personal and operational secrets

Normal, sensitive, and highly-sensitive personal context can be formed from
direct user evidence. Core assigns the sensitivity and local ACL; sensitive
and highly-sensitive formed records remain local and are available only to
authenticated, non-denied `context:read` principals. This intentionally lets
the separately provisioned read identity retrieve memory formed by the
capture-only identity. The raw sensitive lifecycle observation remains bound
to its capture principal and is not exposed through the ordinary read path.

Operational credential values—passwords, API/session tokens, private keys, and
cookies—are refused before durable capture and are absent from the observation
ledger, ordinary retrieval, replication, exports, logs, and model context.
Secret discussion without a credential-shaped value is not rejected merely for
mentioning a credential category.

The optional `LocalSecretReferenceVault` stores raw values only through the
OS-backed credential-store abstraction and returns an opaque UUIDv4 reference.
It rejects the plaintext development-file store and fails closed when an
OS-backed store is unavailable. This lane does not make raw credential bytes a
memory or capture payload.

## Setup boundary

The integrated setup path owns the one-time, false-by-default client opt-in and
explicitly provisions a separate capture principal. Existing generic and read
defaults do not receive `context:capture`. A successful later opt-out removes
the managed hook/server configuration and retires the omitted managed capture
principal without changing unrelated user configuration.
