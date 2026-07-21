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

Raw imports travel directly to Core. JSON, JSONL, Markdown, and text importers
store a content-addressed source locally, then pass obvious structured facts to
a deterministic extractor. Imported instructions remain inert evidence.

Candidates default to review. A future policy hook may auto-approve exact,
explicit, low-sensitivity statements from a client granted `auto_approve`;
model inferences remain review-only. Sensitive candidates require explicit
availability confirmation before replication.
