# Native macOS observation form

Copy this form once for ARM64 and once for x86-64. It is a content-free
operator worksheet, not an acceptance receipt, maintainer decision, signature,
or publication authorization. Leave every unobserved item unchecked. A blank,
deferred, unavailable, or failed item is not pass.

Do not enter prompts, responses, context text, tokens, client IDs, vault IDs,
usernames, device names, local paths, Keychain contents, conversation titles,
or private account details. Record only public versions, hashes, counts,
booleans, durations, closed reason codes, and report hashes.

## Immutable inputs

- Architecture (`arm64` or `x86_64`):
- Source commit (40 lowercase hex):
- Candidate SHA-256 (64 lowercase hex):
- Version:
- Draft release numeric ID:
- DMG name / size / SHA-256:
- OTA ZIP name / size / SHA-256:
- Full candidate verification report SHA-256:
- Supporting tooling file-digest set SHA-256:

## Host and client freeze

- Exact macOS product version:
- Exact macOS build:
- Native architecture:
- Rosetta translated (`false` required):
- Logical CPU count:
- Physical memory bytes:
- Root free bytes before allocation:
- Root internal (`true` required):
- Root solid state (`true` required):
- Root filesystem:
- Dedicated clean local user attested (`true` required):
- Non-root execution (`true` required):
- Stable Codex version:
- Stable Claude Desktop version:

- [ ] Strict native preflight passed.
- [ ] Candidate source checkout and Python import path matched the source commit.
- [ ] Full candidate inventory, checksum sidecars, and source evidence recomputed.
- [ ] Candidate assets remained byte-identical after the run.

## Supporting suite

- Supporting report SHA-256:
- Preflight report SHA-256:

- [ ] Architecture-specific package report selected exactly once.
- [ ] DMG integrity and adjacent unsigned notice passed.
- [ ] App bundle identity and release version matched.
- [ ] Main, MCP, and recovery executables were exactly the declared architecture.
- [ ] Safe internal-link and structural ad-hoc code-seal checks passed.
- [ ] No publisher identity or notarization was claimed.
- [ ] Isolated Keychain adapter round-trip/delete passed.
- [ ] Frozen resources and packaged credential control passed.
- [ ] Packaged recovery smoke passed.
- [ ] Isolated first-run, MCP, Core restart, reopen, and shutdown smoke passed.
- [ ] Supporting report retained no raw subprocess stream or product content.

Supporting status (`pass`, `fail`, `unavailable`, or `not_run`):

Reminder: supporting `pass` still grants zero gate credit.

## Journey 1 — install, protected credentials, and clients

### P01 and S03 platform slice

- [ ] DMG came through the controlled download path with normal quarantine state.
- [ ] Ordinary Gatekeeper unsigned-community warning was observed.
- [ ] No quarantine deletion, Gatekeeper disablement, signing, or byte mutation occurred.
- [ ] App installed per user into the stable `~/Applications` bundle without root.
- [ ] Core bound only to loopback.
- [ ] LaunchAgent referenced the stable installed bundle.
- [ ] Login and reboot persistence passed without reinstall.
- [ ] Intended Keychain entries existed; managed configs contained no token.
- [ ] Credential-write failure rolled back principal, Keychain, config, and backup state.
- [ ] Config-write failure rolled back principal, Keychain, config, and backup state.
- [ ] Unrelated client configuration matched its pre-run byte/metadata snapshot.

P01/S03 report SHA-256:

### Codex P02/P03/S02 cell

- [ ] A new fictional Codex chat performed the first real MCP tool call.
- [ ] One-time setup survived Codex close, authenticated Core shutdown, and fresh relaunch.
- [ ] Direct fictional statement disposition was `applied` with one record ID.
- [ ] A later fresh chat retrieved that record without approval or dashboard work.
- [ ] Exact retry replayed the same decision and record ID.
- [ ] Exact duplicate reinforced without a second current record.
- [ ] Model-inferred conflict remained tentative and did not change the explicit record.
- [ ] Only IDs, dispositions, hashes, counts, and timings were retained.

Codex report SHA-256:

### Claude P02/P03/S02 cell

- [ ] A new fictional Claude chat performed the first real MCP tool call.
- [ ] One-time setup survived Claude close, authenticated Core shutdown, and fresh relaunch.
- [ ] Direct fictional statement disposition was `applied` with one record ID.
- [ ] A later fresh chat retrieved that record without approval or dashboard work.
- [ ] Exact retry replayed the same decision and record ID.
- [ ] Exact duplicate reinforced without a second current record.
- [ ] Model-inferred conflict remained tentative and did not change the explicit record.
- [ ] No existing chat, title, sidebar, response, or unrelated private content was captured.

Claude report SHA-256:

Journey 1 status (`pass`, `fail`, `unavailable`, or `not_run`):

## Journey 2 — S01, S04, D02, and D03

### S01 secret boundary

- [ ] Direct fictional secret-like payload was refused before durable payload/hash writes.
- [ ] Content-derived guessable verifier was refused.
- [ ] Secret correction, replay, and forget/reject/delete reasons remained content-free.
- [ ] Existing-data repair/compaction removed the injected legacy fictional fixture.
- [ ] SQLite table/page scan hits: `0`.
- [ ] Live WAL/journal scan hits: `0`.
- [ ] Freelist and FTS scan hits: `0`.
- [ ] Diagnostic scan hits: `0`.
- [ ] Encrypted export scan hits: `0`.
- [ ] `PRAGMA quick_check`: `ok`.
- [ ] `PRAGMA foreign_key_check` rows: `0`.

### S04 Core-only distribution

- [ ] Removed active Edge/remote HTTP routes returned the frozen refusal/not-found result.
- [ ] Legacy cleanup refused because no residual pairing existed.
- [ ] Remote pairing/deployment/sync was disabled.
- [ ] Non-loopback listener count: `0`.
- [ ] External runtime/deployment process count: `0`.

### D02 destructive privacy and authorization

- [ ] Scoped reader allow, deny, and revocation survived restart.
- [ ] Authorization and denial survived isolated restore.
- [ ] Ordinary record delete/restore survived restart without resurrection.
- [ ] Imported-source delete/restore survived restart without unrelated resurrection.
- [ ] Wrong purge confirmation was refused.
- [ ] Scoped client purge was refused.
- [ ] Administrator purge survived restart and restore with zero resurrection.
- [ ] Encrypted export contained source/audit state and restored with integrity verified.

### D03 shipped recovery helper

- [ ] Only the version-matched bundled console recovery helper was used.
- [ ] Help documented doctor/export/restore/cutover/rollback.
- [ ] Stopped-Core doctor passed.
- [ ] Missing input/passphrase, wrong passphrase, tampered export, and nonempty destination failed.
- [ ] Isolated restore passed integrity and non-vacuous retrieval equivalence.
- [ ] Active-Core export and restore were refused.
- [ ] Positive cutover omitted post-export state as expected.
- [ ] Failed cutover left the active vault unchanged.
- [ ] Rollback restored post-export state.
- [ ] Missing/tampered rollback failed.

S01/S04 report SHA-256:
D02 report SHA-256:
D03 report SHA-256:
Journey 2 status (`pass`, `fail`, `unavailable`, or `not_run`):

## Journey 3 — allocated 2,000,000,000-byte boundary

- [ ] Predeclared host, APFS volume, limits, inputs, and output locations matched this form.
- [ ] Canary logical size: `2000000000`.
- [ ] Canary SHA-256 matched `boundary-canary-v2`.
- [ ] Allocated block bytes were at least the logical size.
- [ ] Sparse/compressed/clone placeholder indicators were absent.
- [ ] Declared `2000000001` bytes was refused before upload.
- [ ] Straight import completed with source SHA and exactly 239 chunks.
- [ ] Straight import reconciled five candidates and closed all other input counts.
- [ ] Exact repeat preserved source and candidate IDs without duplicate current state.
- [ ] Authenticated API progress met the five-second-or-64-MiB liveness rule.
- [ ] Read-only SQLite progress met the same liveness rule.
- [ ] Core plus worker peak RSS was at most 1 GiB.
- [ ] Incremental storage was at most 9,073,741,824 bytes.
- [ ] Cancellation became durable within five seconds.
- [ ] Cancelled worker quiesced within 30 seconds.
- [ ] No-upload retry completed with the preserved source and stable IDs.
- [ ] Hard interruption killed only the run-owned package process group.
- [ ] Restart marked the interrupted operation with the frozen closed error code.
- [ ] Second no-upload retry completed with stable source/candidate identities.
- [ ] Source-inclusive encrypted export and isolated restore matched source SHA,
      chunks, schema, records, candidates, counts, and retrieval.
- [ ] Canary and every large temporary were removed after handles/processes closed.

D01 report SHA-256:
D01 trace-manifest SHA-256:
Journey 3 status (`pass`, `fail`, `unavailable`, or `not_run`):

## Final cleanup

- [ ] Authenticated Core shutdown completed where possible.
- [ ] Run-owned app/helper process count: `0`.
- [ ] Run-owned listener count: `0`.
- [ ] Run-owned Keychain item count: `0`.
- [ ] Run-owned LaunchAgent count: `0`.
- [ ] Run-owned mounts: `0`.
- [ ] Client config exact-byte/metadata comparison passed.
- [ ] Temporary vault/config/app/export/restore/log/canary roots are absent.
- [ ] Source checkout is clean at the exact candidate commit.
- [ ] No provider export was requested, downloaded, generated, or retained.
- [ ] Only content-free reports and their SHA-256 values remain.

Cleanup report SHA-256:
Architecture observation status (`pass`, `fail`, `unavailable`, or `not_run`):

## Consolidation boundary

- [ ] This form was not treated as a receipt.
- [ ] No per-architecture or per-client duplicate gate receipt entered the final bundle.
- [ ] The opposite native architecture has its own completed form.
- [ ] Both Codex and Claude cells passed on both architectures.
- [ ] Deferred/unavailable/unrun cells remain open.
- [ ] A coordinator validated every report and candidate binding before any receipt emission.

Coordinator review status (`not_reviewed`, `accepted_as_supporting_slice`, or `rejected`):
