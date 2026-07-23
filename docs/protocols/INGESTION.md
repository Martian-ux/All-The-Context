# Ingestion protocol

## Model-assisted session

1. `begin_ingestion` creates a session with mode, accessible sources, and
   explicitly unavailable sources.
2. `submit_context_batch` accepts bounded candidates and an idempotency key.
   Reuse with identical content returns the original result; reuse with changed
   content fails.
3. `finish_ingestion` stores a coverage report and counts accepted candidates.
4. `propose_memory` creates one candidate during normal client activity.
5. `report_context_error` records a correction signal as a candidate and audit
   event; it never mutates canonical context directly.

Raw imports travel directly to Core. ZIP, JSON, JSONL, Markdown, and text
importers store a content-addressed source locally, then pass normalized data
to a deterministic extractor. ChatGPT conversation graphs, Claude
`chat_messages`, flexible Grok conversation envelopes, provider memory/profile
fields, and Grok-style Markdown transcripts have explicit adapters. Imported
instructions remain inert evidence.

Provider imports use a versioned archive session keyed by source ID and parser
version. Batches use the source hash, parser version, and stable batch ordinal
as idempotency material. A replay of an interrupted batch returns its original
candidate IDs; changed content under the same key fails closed. Source status
is `processing`, `failed`, or `complete`. Failed/processing sources can be
reprocessed from the preserved raw blob, so retry does not require another
provider download or create duplicate candidates.

Every provider coverage report includes detected provider/format, file and
conversation counts, user/assistant/other message counts, memory item and
candidate counts, skipped/unsupported material, warnings, and explicit
limitations. Assistant, system, tool, and attachment content can be retained in
the raw archive but cannot produce a candidate. Dedicated provider memory
summaries can produce lower-confidence, non-explicit candidates. User-authored
durable statements retain a conversation/message source reference.

Candidates default to review. A future policy hook may auto-approve exact,
explicit, low-sensitivity statements from a client granted `auto_approve`;
model inferences remain review-only. Sensitive candidates require explicit
availability confirmation before replication.

Candidates may propose an `entity_key`/`attribute_key` pair with their existing
source reference and evidence. The pair is optional but atomic: supplying only
one is invalid. Core normalizes it for deterministic comparison, exposes it in
candidate review, and creates no canonical slot until an administrator approves
the candidate. Approval and correction may edit the pair. Duplicate/conflict
groups are review signals only; approval never silently merges, supersedes, or
chooses a record.
