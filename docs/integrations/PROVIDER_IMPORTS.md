# Provider history import

All The Context can initialize its local context from official ChatGPT, Claude,
and Grok exports. This is a local account-history import, not an account login,
browser scraper, provider API integration, recurring cloud sync, or task the
user must continually curate.

## One-time user flow

1. Open **Sources** in the All The Context dashboard.
2. Request an account export from the provider:
   - [ChatGPT: Settings > Data controls > Export](https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data)
   - [Claude: Settings > Privacy > Export data](https://support.claude.com/en/articles/9450526-export-your-claude-data)
   - [Grok: Settings > Data Controls > Download data](https://x.ai/legal/faq)
3. Drop the downloaded ZIP into All The Context without unpacking or editing it.
   Auto-detection is the default; a provider hint is available for unusual
   exports. The dashboard uses the durable import-operation API: it creates an
   opaque operation id, streams the file, shows committed progress, and can
   cancel mid-upload or mid-extraction.
4. Let extraction complete. The dashboard reports truthful source coverage and
   the total observations processed. Core's import response also returns
   per-disposition `outcomes` and affected `record_ids`; the dashboard presents
   those disposition counts. There is no extracted-memory review queue.

The same importer accepts JSON, JSONL, Markdown, and text. A copied provider
memory summary can therefore be saved as a text or Markdown file, its provider
selected in the dashboard, and imported through the same automatic policy.
Provider-generated summaries remain tentative by default rather than being
treated as direct user statements.

## What "full import" means

- The accepted source file is stored byte-for-byte as a content-addressed raw
  source in the authoritative local Core.
- Every recognized conversation and message is counted in a coverage report.
- ChatGPT conversation graphs and numbered conversation JSON files are
  supported.
- Claude `chat_messages` exports and dedicated memory/profile fields are
  supported.
- Grok conversation JSON and Grok Build-style Markdown transcripts are
  supported through adaptive field normalization.
- Non-text attachments remain inside the preserved raw archive. They are
  counted, but are ignored for context maintenance in this slice.
- The import does not change current context until the extraction session
  finishes successfully. A failed or interrupted session retains recoverable source
  state without partially publishing decisions.

"Full" does not mean that every prompt or assistant response becomes current
context. Imported text is untrusted data, never instructions. The deterministic
extractor considers explicit user-authored durable statements. Core evaluates
those observations automatically only after successful session completion,
even when the truthful coverage report lists unavailable material.
Assistant, system, tool, and attachment roles are excluded by provider
adapters. Generic or instruction-bearing text and dedicated provider
memory/profile summaries are tentative by default, and imported text is never
executed as instructions. Tentative observations are not retrieved and do not
wait for user review.

The dashboard's optional Activity and Context views let a user inspect source
provenance, see why policy made a decision, correct a record, undo an ordinary
change, or forget something. Those are escape hatches, not required import
steps.

Grok documents the ability to download account data, but does not publish a
stable archive schema. Claude's public export documentation also does not
promise every internal JSON field. Those adapters therefore use bounded,
provider-neutral envelope/message detection and report unrecognized material
instead of silently treating it as context. Raw preservation allows a future
parser version to reprocess the source.

## Safety, scale, and recovery

- Core reads ZIP members in place and never extracts archive paths.
- Absolute/traversal paths, encrypted text entries, case-insensitive duplicate
  names, excessive entry counts, compression bombs, and excessive expanded
  text are rejected or explicitly skipped.
- JSON conversation arrays are decoded one conversation at a time. The HTTP
  upload and Core source write use bounded chunks rather than loading the
  complete archive into memory.
- The current implementation default and maximum raw-source limit is
  2,000,000,000 bytes, and an operator can lower it with
  `ATC_MAX_IMPORT_BYTES`. The lower operator setting does not reduce the
  mandatory beta maximum. The inclusive boundary is implemented and tested;
  exact multi-platform candidate receipts remain acceptance work. The frozen
  reference floor is 4 logical cores, 8 GiB RAM, local SSD, and 16 GiB free.
  Core plus import-worker RSS is capped at 1 GiB; incremental import storage is
  capped at four times raw size plus 1 GiB. The beta acceptance contract
  requires progress to start within 5 seconds and advance every 5 seconds or
  64 MiB, cancellation to be acknowledged within 5 seconds and quiesce safely
  within 30 seconds, and import, source-inclusive export, and isolated restore
  each to finish within 60 minutes.
- Disk preflight requires the greater of four-times-source-plus-1-GiB or any
  measured durable high-water plus 25 percent on the Core database volume
  before accepting a raw source. A durable opaque **import operation** id is
  created after that preflight and before any source bytes are accepted.
  Status, cancel, and progress are queryable by operation id while the upload
  and Core blob staging are still in flight and before any source id exists.
- Upload streams request bytes without materializing the archive in memory.
  Integrity (SHA-256) is computed while streaming. Committed progress is
  advanced on durable 8 MiB boundaries and stays within one chunk of bytes
  written. Heartbeats publish within the 5-second / 64 MiB budget. Incomplete
  blobs are never linked as canonical sources (`blob_complete=0` until
  finalize).
- After complete raw preservation, the operation links to the authoritative
  source id. Parser/ingest failure or cancellation retains that source for
  no-upload retry. Process restart recovers non-terminal operations into a
  deterministic failed terminal state with staging cleanup; linked sources
  remain retryable without re-upload.
- Cancel is acknowledged within the in-process cancel registry and durable
  operation row during upload, hashing, Core blob staging, parsing, ingestion,
  and verification. Current context is never partially published from an
  incomplete operation.
- The synchronous multipart `POST /v1/admin/import` endpoint remains for
  compatibility and routes through the same operation lifecycle. The V1
  dashboard journey uses:
  - `POST /v1/admin/import-operations` (declared size + preflight → operation id)
  - `PUT /v1/admin/import-operations/{id}/content` (streamed body)
  - `GET /v1/admin/import-operations/{id}` (concurrent status)
  - `POST /v1/admin/import-operations/{id}/cancel`
  - `POST /v1/admin/import-operations/{id}/retry`
- A deterministic physically allocated/non-sparse boundary canary generator
  (`boundary-canary-v2`) publishes version, SHA-256, 8 MiB chunk-count
  expectations, nonzero parse/publication material, and interruption
  checkpoints. Generate or verify with
  `python scripts/generate_boundary_canary.py`. Exact
  `2,000,000,000`-byte candidate runs on Windows x86-64, macOS ARM64, macOS
  x86-64, and Linux x86-64 remain operator-controlled acceptance; a
  `2,000,000,001`-byte source is refused deterministically before upload.
- Core stores large raw sources as ordered 8 MiB-or-smaller SQLite rows instead
  of one oversized BLOB. Reads, retries, and source-inclusive portable restores
  verify the complete source size and SHA-256 identity.
- The raw-source boundary does not raise the expanded-text, archive-entry,
  compression-ratio, or per-conversation parse limits. Imports near 2 GB also
  require enough local space for the temporary upload, SQLite transaction
  journal/WAL, database growth, and any source-inclusive export.
- Observation batches use a versioned session and deterministic idempotency
  keys. If extraction is interrupted, the source is marked failed or cancelled
  and the dashboard/CLI can retry directly from the preserved raw blob without
  another upload or duplicate decisions.
- Raw source text and credentials are never logged.

ChatGPT, Claude, and Grok are all mandatory beta provider targets. Each
provider claim has a parser identity (`chatgpt-archives-v1`,
`claude-archives-v1`, `grok-archives-v1`) under the aggregate
`provider-archives-v1` session version. Frozen fictional shapes live in the
runtime claim manifest. Each import reports closed coverage counts
(recognized, excluded, skipped, unavailable, failed, unparsed). Unknown or
unparsed material is a visible coverage warning and keeps coverage incomplete
rather than counting as parser success. Each provider must still pass a
privacy-safe nonempty real-export receipt acquired after parser freeze and
within 30 days of candidate acceptance. Missing real-export evidence keeps
beta1 in draft rather than narrowing the provider list.

## Contributor CLI

After the development bootstrap, the same command shape works in PowerShell,
macOS shells, and Linux shells:

```text
atc import "path/to/provider-export.zip" --provider auto
atc import-progress <operation-id-or-source-id>
atc cancel-import <operation-id-or-source-id>
atc reprocess-source <operation-id-or-source-id>
atc import-boundary
```

`atc import` creates a durable operation, streams the local file with committed
chunk progress, then parses and ingests. Progress/cancel/retry accept either an
operation id (preferred during upload/staging) or a source id (after raw
preservation). Use `--provider chatgpt`, `--provider claude`, or
`--provider grok` only when auto-detection needs a hint. The CLI returns
provider, format, conversation and message counts, warnings, parser identity,
closed coverage, and the complete coverage report. Admin HTTP routes expose the
operation API above plus source-scoped progress/cancel/reprocess compatibility
and the frozen boundary/provider claim profile. Its import result includes:

```json
{
  "candidate_ids": ["compatibility observation IDs"],
  "outcomes": {
    "applied": 0,
    "reinforced": 0,
    "tentative": 0,
    "ignored": 0
  },
  "record_ids": ["affected current-record IDs"]
}
```

Only dispositions present in that import need appear in `outcomes`.
`candidate_ids` is the compatibility wire name; product surfaces call them
observations.
