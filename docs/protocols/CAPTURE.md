# Continuous Capture foundation

This document describes the Stage 4 first slice in Core migration 015. It is a
provider-neutral contracts and ledger foundation, not evidence that any
provider supports continuous capture.

## Boundary

Capture is local-only, opt-in, and foreground-only. Core remains the sole
canonical authority. This slice contains no real provider connector, network
implementation, OAuth flow, token/credential handling, background scheduler,
dashboard/package-startup change, or macOS work. It does not change current
product availability, beta.6 identity, release state, or acceptance credit.

The supported posture remains Windows and supported Linux source/package work.
macOS source retention remains unsupported and creates no support or acceptance
claim.

## Ledger

Migration `015_continuous_capture.sql` adds five bounded SQLite tables:

- `capture_sources` stores provider identity, a content-free account label or
  fingerprint, requested scopes, local-only acknowledgement, lifecycle state,
  retry/lag telemetry, and a reserved nullable internal `credential_ref`.
  Creation is always `disabled`; `credential_ref` is never returned by Core
  admin/API/CLI projections.
- `capture_checkpoints` stores generation, an opaque bounded cursor, and the
  last committed order key/event ID. Cursors and provider IDs are internal and
  are never returned by the content-free status surfaces.
- `capture_events` stores bounded provider event/item IDs, generation/order,
  `upsert` or `delete`, normalized inert payload, SHA-256 payload hash, staged/
  applied status, attempts, canonical error code, application receipt, and a
  deterministic idempotency key.
- `capture_items` binds `(source_id, provider_item_id)` to one stable canonical
  application lineage. A provider delete is always scoped to that source/item
  pair; local corrections remain an explicit sink responsibility and cannot be
  overwritten by this coordinator.
- `capture_runs` stores lease, attempt, page, event, failure, and completion
  telemetry. Lease tokens are internal and never exposed.

The migration runner performs a restart-safe repair probe after every migration
pass. Missing migration-015 tables or indexes are recreated without advancing
an already-recorded schema marker.

## Contracts and replay

`allthecontext.capture` exposes `CaptureProviderAdapter`, `CapturePage`,
`CaptureEvent`, `CaptureApplicationSink`, `CaptureCoordinator`,
`BackoffPolicy`, and `CaptureRunResult`. `DeterministicFakeAdapter` and
`IdempotentFakeSink` are test-only deterministic fixtures. No real adapter is
registered by Core.

For an explicitly enabled, local-only-acknowledged source, one foreground run:

1. obtains a bounded lease and asks the injected adapter for ordered pages;
2. durably stages each event before calling the injected sink;
3. calls the sink with a deterministic idempotency key and source-scoped item
   lineage;
4. commits the application receipt, capture-item mapping, and checkpoint in
   one SQLite transaction; and
5. advances the page cursor only after every event in that page is applied.

If the process fails after sink application but before the commit, the staged
event is replayed with the same idempotency key. If the commit already exists,
replay is a no-op. A checkpoint never moves beyond a failed, unapplied, or
out-of-order event. Duplicate event/page replay is therefore safe and stable.
Numeric order keys enforce contiguous progression; opaque order keys enforce
strict deterministic ordering. Invalid cursors, malformed pages, generation
mismatches, gaps, oversize payloads, page/event limits, sink failures, and
expired leases produce bounded canonical error codes and retry metadata.

Full snapshot/rescan deletion is deliberately deferred. The ledger does not
invent a provider snapshot boundary or delete items merely because they are
absent from one page.

## Lifecycle

The states are `disabled`, `enabled`, `paused`, `degraded`, `revoked`, and
`reconciling`. New sources are disabled. Enable/resume/reconciliation require
the local-only acknowledgement and a non-revoked source. Disabled, paused, and
revoked sources make zero adapter calls. Revoke is terminal and clears the
reserved credential reference. There is no scheduler and only one foreground
run can hold a live lease.

## Admin API and CLI

All routes require the existing Core admin authentication and remain bound to
the existing loopback Core defaults:

- `POST/GET /v1/admin/capture/sources`
- `GET /v1/admin/capture/sources/{source_id}` and `/status`
- `POST /v1/admin/capture/sources/{source_id}/{enable,pause,resume,disable,revoke,run}`
- `GET /v1/admin/capture/status`

Responses contain source state, bounded telemetry, canonical error codes, and
generation only. They do not contain cursors, payloads, provider tokens,
credential references, or raw provider errors. If no adapter is registered,
`run` returns `capture_adapter_unavailable` without making a network call.

The contributor CLI uses `atc capture status [source_id]`, `run`, `enable`,
`pause`, `resume`, `disable`, and `revoke`. `atc capture create` accepts only
provider/account metadata and an explicit local-only acknowledgement; it has no
secret argument.
