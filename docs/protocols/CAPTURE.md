# Continuous Capture foundation and registered-source admission PR1

This document describes the Stage 4 first slice in Core migrations 015 and 017,
plus the bounded registered-source admission contract in migration 016. It is
a provider-neutral ledger foundation plus a locally injected Core sink
contract, not evidence that any provider supports continuous capture.

## Boundary

Capture is local-only, opt-in, and foreground-only. Core remains the sole
canonical authority. This slice contains no network implementation, OAuth flow,
token/credential handling, background scheduler, dashboard/package-startup
change, or macOS work. The experimental local adapter and the new sink are
explicitly injected only by focused tests; neither is constructed by
`CoreService` or package startup. It does not change current product
availability, beta.6 identity, release state, or acceptance credit.

The supported posture remains Windows and supported Linux source/package work.
macOS source retention remains unsupported and creates no support or acceptance
claim.

## Ledger

Migration `015_continuous_capture.sql` adds five bounded SQLite tables. Migration
`016_registered_source_admission.sql` adds only nullable `capture_source_id`,
`capture_event_id`, and bounded 64-character `capture_binding_hash` columns to
`context_candidates`, plus a partial unique index allowing at most one
candidate for each non-null capture event. It adds no capture truth table or
capture event admission columns.

`017_capture_page_recovery.sql` extends the existing `capture_checkpoints` row
with only nullable `pending_generation`, `pending_cursor`, and bounded JSON
ordered pending event IDs. A non-null pending generation marks a staged page,
including an empty or terminal page; null pending generation means no pending
page. Migration 016 remains the owner of only its three candidate columns and
its partial unique index.

The five migration-015 tables are:

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
  telemetry. Lease tokens are internal and never exposed; foreground mutators
  receive a typed `CaptureRunHandle` containing the run ID, source ID, and
  lease token.

The migration runner performs a restart-safe repair probe after every migration
pass. Missing migration-015 objects, migration-016 candidate columns/index,
and migration-017 pending columns are recreated without advancing an
already-recorded schema marker. The probe reads and executes the packaged SQL
one statement at a time inside the caller's transaction, so initial migration
and marker-present repair use the same bounds, enums, nonnegative counters/
generation, hashes, IDs, item/run states, foreign keys, unique constraints,
and indexes.

When a newer migration is pending, the repair probe is bounded through the
already-applied capture migration version inside that pending migration's
transaction, before the newer migration statements run. Successful migration
retains the full repaired state; a failed transaction does not retain a partial
repair. The architecture data model already records migration 017 as used and
018 as next.

## Contracts and replay

`allthecontext.capture` exposes `CaptureProviderAdapter`, `CapturePage`,
`CaptureEvent`, `CaptureApplicationSink`, `CaptureCoordinator`,
`BackoffPolicy`, `CaptureRunHandle`, and `CaptureRunResult`. `DeterministicFakeAdapter` and
`IdempotentFakeSink` are test-only deterministic fixtures. No real adapter is
registered by Core.

For an explicitly enabled, local-only-acknowledged source, one foreground run:

1. obtains a bounded lease capability and replays any durable pending page
   through the injected sink before asking the adapter for a page;
2. stages each newly fetched, already-validated page atomically: all event
   identity/idempotency checks, event rows, ordered durable event IDs, and the
   pending generation/cursor commit together before any sink call;
3. calls the sink with the exact durable `event_id`, still-running
   `CaptureRunHandle`, deterministic idempotency key, and source-scoped item
   lineage;
4. commits each application receipt and capture-item mapping in one SQLite
   transaction, replaying already-applied pending events idempotently; and
5. atomically advances the real page cursor and clears pending state only after
   every listed event is applied. After pending recovery, the adapter is asked
   for the recovered cursor in the same run, so a cursor-diff adapter can emit
   a real deletion. An empty page that advances generation clears stale order
   state; same-generation ordering remains strict.

Page validation rejects duplicate provider event IDs before staging. The ledger
also uniqueness-checks the durable event IDs that would be written to the
pending marker, so duplicate page IDs fail closed without partial staged or
pending state. For an already-persisted legacy marker only, recovery validates
the raw bounded list and each durable ID, then removes repeated identical IDs
in first-occurrence order; malformed, non-string, or invalid IDs still fail
closed before the marker can advance.

The sink receipt must echo the exact deterministic lineage supplied by Core.
A different first-event lineage is invalid and cannot create a capture-item
mapping. Provider IDs, generations, page orders, scopes, and receipt fields do
not accept implicit string/integer coercion. Content-free payload metadata is
compatibility-normalized for credential-marker scanning, including zero-width
and combining-form obfuscations, before it can be staged.

Every run-owned mutation transactionally requires the exact capability, a
`running` run with a strictly future lease, and a source still in
`reconciling`. Renewal uses the same checks. Pause, revoke, expiry, recovery,
or replacement invalidates the old capability; stale completion cannot change
the newer run or source state. If a sink crosses lease expiry, its result is
not committed. Replay uses the same idempotency key, so an idempotent sink can
complete safely on the replacement run.

If the process fails after sink application but before event commit, or after
event commit but before page-cursor commit, the durable pending page is replayed
with the same idempotency keys. If an event commit already exists, replay is a
no-op at the sink and ledger boundaries. A committed cursor never moves beyond
a failed, unapplied, or out-of-order event. Duplicate event/page replay is
therefore safe and stable, and an existing pending page is never overwritten.
Numeric order keys enforce contiguous progression; opaque order keys enforce
strict deterministic ordering. Invalid cursors, malformed pages, generation
mismatches, gaps, oversize payloads, page/event limits, sink failures, and
expired leases produce bounded canonical error codes and retry metadata.

Full snapshot/rescan deletion is deliberately deferred. The ledger does not
invent a provider snapshot boundary or delete items merely because they are
absent from one page.

## Registered-source admission PR1

`RegisteredSourceCaptureApplicationSink` is an internal, explicitly injected
implementation of `CaptureApplicationSink`. It validates the exact vault,
registered source, provider/account fingerprint, reconciling lifecycle, local-
only acknowledgement, the exact code-owned `workspace.structure` scope,
staged event projection, provider IDs,
operation/generation/order/payload hash/idempotency, canonical lineage, and run
lease inside one Core transaction. Its closed registry currently contains only
the `local-git-workspace` structural extractor. It derives only fixed classes
for Python, Markdown, shell/PowerShell, known manifests, and generic text files;
unknown or binary material is a deterministic no-fact.

Admitted candidates use `REGISTERED_SOURCE`, `registered_source_fact`,
`registered_capture`, Core availability, normal sensitivity, empty client ACLs,
confidence `1.0`, empty explicitness, exactly the code-owned
`workspace.structure` scope, and the durable
event received time. Their evidence and structured value contain only the
versioned safe schema/fact class and content-free binding hash. Their
`source_reference` is an opaque hash of the capture source and provider item;
the raw provider item ID remains in the machine-local capture ledger only. The
deterministic capture-lineage ID is used only by the internal Core evaluation
override, and public ingestion cannot select this origin.

The `LocalGitWorkspaceCaptureProviderAdapter` emits adapter-produced,
coordinator-path metadata-only `workspace.structure` payloads. The
provider-neutral ledger stores the bounded payload supplied by its internal
caller; it is not itself a metadata-only contract. The registered-source sink
keeps extra caller-supplied fields inert and does not project them into
candidate content or evidence. The adapter's durable upsert payloads contain
only bounded structural fields such as relative path, root ID, kind, size,
content hash, truncation state, and hash scope; source text and text excerpts
are never durably included in the adapter path, candidate content, or evidence.

Replay queries `capture_event_id` in the same write transaction and validates
the stored projection. A crash before ledger commit therefore replays one
candidate/record. User correction, availability, ordinary deletion, purge, and
broken registered-source linkage remain authoritative and consume later capture
as no-influence. A capture item delete, or a later upsert that deterministically
produces no fact for the same exact source/item, withdraws only the exact
untouched registered-source record without minting an ordinary tombstone; a
first-ever no-fact is harmless, and a later valid upsert may revive that same
ID. Record purge scrubs linked capture payload JSON and blocks later influence.
This is a local PR1 contract only: Packet H, ZF-010, provider/product support,
production wiring, and release acceptance remain outside scope.

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
