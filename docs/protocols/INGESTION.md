# Ingestion protocol

## Observation lifecycle

1. `begin_ingestion` creates a session with mode, accessible sources, and
   explicitly unavailable sources.
2. `submit_context_batch` accepts bounded observations and an idempotency key.
   Reuse with identical content returns the original batch and observation IDs;
   reuse with changed content fails.
3. `finish_ingestion` validates and stores the coverage report, then atomically
   publishes the session's automatic policy decisions. A failed or unfinished
   session cannot change current context.
4. `propose_memory` submits one observation during normal client activity and
   returns its Core-derived disposition.
5. `report_context_error` submits an explicit correction observation. When the
   authenticated origin and payload satisfy policy, the correction changes
   current context before the call succeeds and preserves the earlier version.
6. `forget_context` acts only on an explicit user request and identified record.
   Local Core creates a reversible tombstone before returning. Relay can only
   queue the request as a staged observation for later Core evaluation.

Submission never grants authority over current context. Core derives origin
from authenticated server and session state; it does not accept a
client-asserted origin or requested disposition. The asserted
`explicit_user_statement` basis remains subject to hard policy. The versioned
policy produces:

- `applied` when an observation creates or updates current context;
- `reinforced` when it adds evidence to an existing applied record;
- `tentative` when it is retained as a noncurrent signal; or
- `ignored` when it is ineligible for context maintenance.

Tentative observations are not pending user work. They can be corroborated or
remain unused, and they are never returned as current context. `automatic-v1`
does not implement expiry/decay; a future policy version may add configurable
retention.

## Provider archive ingestion

Raw imports travel directly to Core through a durable **import operation**
owned by Core:

1. Create an opaque operation id with a declared byte size after boundary and
   Core-volume disk preflight (no source bytes yet).
2. Stream upload bytes; compute SHA-256 while streaming; commit progress on
   durable 8 MiB boundaries. Status is queryable by operation id concurrently.
3. Stage incomplete content-addressed blob rows (`blob_complete=0`) until size
   and hash integrity pass; only then create a canonical `source_records` row
   and link `operation.source_id`.
4. Parse and ingest from the preserved raw blob. Operation telemetry is not a
   second source of canonical context.

ZIP, JSON, JSONL, Markdown, and text importers pass normalized data to a
deterministic extractor. ChatGPT conversation graphs, Claude `chat_messages`,
flexible Grok conversation envelopes, provider memory/profile fields, and
Grok-style Markdown transcripts have explicit adapters. Imported text is
untrusted data and imported instructions remain inert.

Provider imports use a versioned archive session keyed by source ID and parser
version. Batches use the source hash, parser version, and stable batch ordinal
as idempotency material. Replaying an interrupted batch returns the original
observation IDs and decisions; changed content under the same key fails closed.
Source status is `processing`, `failed`, `cancelled`, or `complete`. Failed or
cancelled sources can be reprocessed from the preserved raw blob (operation
retry or source reprocess), so retry does not require another provider download
or create duplicate observations. Process restart recovers non-terminal
operations into a deterministic failed state with bounded staging cleanup.

Every provider import result includes detected provider/format, file and
conversation counts, user/assistant/other message counts, provider-memory item
and observation counts, skipped/unsupported material, warnings, and a truthful
coverage report with explicit limitations. Alongside that report, `outcomes`
counts the dispositions present and `record_ids` lists affected current records.
For a recognized provider conversation list, every non-conversation entry is
counted as `unparsed`; valid siblings still import, but any such residual keeps
the coverage report incomplete. Structural warnings never include imported
entry content.

Role and origin establish eligibility:

- explicit durable user-authored statements from a normalized
  `provider_archive` message may be applied automatically only after the source
  session finishes successfully, and only when they classify as a specific
  durable kind rather than a broad first-person fragment;
- generic JSON/JSONL/Markdown/text document observations remain tentative
  untrusted evidence even when their prose resembles a user assertion;
- dedicated provider memory/profile summaries are provider-synthesized and
  tentative by default;
- model inference is tentative unless corroborated by eligible explicit
  evidence; and
- assistant, system, tool, and attachment roles are excluded by provider
  adapters; generic or instruction-bearing imports remain tentative;
  short, task-local, transient, and question text is skipped; remaining
  first-person fragments may be retained as tentative observations;
  secret-like material is ignored; health, relationship, location, financial,
  and identifier language is classified sensitive or highly sensitive.
  All retained source text remains inert data.

User-authored observations retain conversation/message source references.
Policy decisions retain the parser and policy versions, origin class, bounded
reason, and affected current-record ID.

## Memory slots

Observations may include an `entity_key`/`attribute_key` pair with their source
reference and evidence. The pair is optional but atomic: supplying only one is
invalid. Core normalizes it for deterministic comparison.

An exact current value is reinforced. A material conflict is resolved
deterministically: an explicit targeted correction wins, then explicit user
evidence wins over inference, then `observed_at` and stable tie breakers decide.
The losing value and evidence remain in history. Slot keys are advisory
metadata, not permission to overwrite context. Unkeyed archive statements of
preference, goal, project, decision, workflow, or constraint kinds share a
lineage only when a derived subject key matches; kind-only collapse is not
used. Unusual duplicate or conflict groups remain optional integrity
diagnostics, never a user approval queue.

Failed or cancelled sources retry the existing parser session from the
preserved raw blob. A complete source may be rebuilt with the current parser
(`POST /v1/admin/sources/{id}/reprocess?rebuild=true` or
`atc reprocess-source --rebuild`). Rebuild reversibly withdraws uncorrected
automatic records from that source only in the same Core transaction that
publishes a successfully staged parser-versioned observation set. Parsing,
batch submission, cancellation, interruption, or policy-evaluation failure
leaves the prior current records in place; the staged session remains resumable
from the preserved blob. Rebuild eligibility is limited to current approved
records whose Core origin is `archive_import`; independently deleted records,
direct/local user-authored records, user corrections, and user
privacy/availability changes are excluded. The raw blob and all history remain
in place.

For this rebuild path, `finish_ingestion` stores coverage while leaving the new
candidates staged. Core's rebuild-publish transaction then withdraws eligible
old records and evaluates the staged candidates together. SQLite rollback
covers both sides if any part of that replacement fails. The same transaction
records the published rebuild generation and session ID in source metadata, so
a retry after source-finalization failure recognizes the committed cutover and
only finalizes source state; a failed transaction leaves no publish marker.
