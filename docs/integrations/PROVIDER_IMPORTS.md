# Provider history import

All The Context can initialize its local memory from official ChatGPT, Claude,
and Grok exports. This is a local account-history import, not an account login,
browser scraper, provider API integration, or recurring cloud sync.

## One-time user flow

1. Open **Sources** in the All The Context dashboard.
2. Request an account export from the provider:
   - [ChatGPT: Settings > Data controls > Export](https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data)
   - [Claude: Settings > Privacy > Export data](https://support.claude.com/en/articles/9450526-export-your-claude-data)
   - [Grok: Settings > Data Controls > Download data](https://x.ai/legal/faq)
3. Drop the downloaded ZIP into All The Context without unpacking or editing it.
   Auto-detection is the default; a provider hint is available for unusual
   exports.
4. Review the extracted candidates. Nothing becomes canonical memory until the
   normal approval policy accepts it.

The same importer accepts JSON, JSONL, Markdown, and text. A copied provider
memory summary can therefore be saved as a text or Markdown file, its provider
selected in the dashboard, and imported through the same review boundary.

## What “full import” means

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
  counted, but do not become memory candidates in this slice.

“Full” does not mean that every prompt or assistant response is approved as
memory. The deterministic extractor considers user-authored durable statements
and dedicated provider memory/profile fields. Assistant, system, tool, and
attachment content remains untrusted evidence. Provider memory summaries are
lower-confidence candidates because they may be synthesized. Every extracted
item remains pending until review.

Grok documents the ability to download account data, but does not publish a
stable archive schema. Claude's public export documentation also does not
promise every internal JSON field. Those adapters therefore use bounded,
provider-neutral envelope/message detection and report unrecognized material
instead of silently treating it as memory. Raw preservation allows a future
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
- Candidate batches use a versioned session and deterministic idempotency keys.
  If extraction is interrupted, the source is marked failed and the dashboard
  can retry directly from the preserved raw blob without another upload.
- Raw source text and credentials are never logged.

## Contributor CLI

After the development bootstrap, the same command shape works in PowerShell,
macOS shells, and Linux shells:

```text
atc import "path/to/provider-export.zip" --provider auto
```

Use `--provider chatgpt`, `--provider claude`, or `--provider grok` only when
auto-detection needs a hint. The CLI returns provider, format, conversation and
message counts, candidate IDs, warnings, and the complete coverage report.
