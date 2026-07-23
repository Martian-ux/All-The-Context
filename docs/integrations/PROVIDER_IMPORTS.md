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
   exports.
4. Let extraction complete. The dashboard reports truthful source coverage and
   the total observations processed. Core's import response also returns
   per-disposition `outcomes` and affected `record_ids`; richer outcome
   presentation in the dashboard remains pending. There is no extracted-memory
   review queue.

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
  upload and SQLite source write use bounded chunks rather than loading the
  complete archive into memory.
- The default raw-source limit is 512 MiB. An operator can lower or raise it up
  to SQLite's safe 900,000,000-byte boundary with `ATC_MAX_IMPORT_BYTES`.
- Observation batches use a versioned session and deterministic idempotency
  keys. If extraction is interrupted, the source is marked failed and the
  dashboard can retry directly from the preserved raw blob without another
  upload or duplicate decisions.
- Raw source text and credentials are never logged.

## Contributor CLI

After the development bootstrap, the same command shape works in PowerShell,
macOS shells, and Linux shells:

```text
atc import "path/to/provider-export.zip" --provider auto
```

Use `--provider chatgpt`, `--provider claude`, or `--provider grok` only when
auto-detection needs a hint. The CLI returns provider, format, conversation and
message counts, warnings, and the complete coverage report. Its import result
includes:

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
