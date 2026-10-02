# Bounded record-delivery status (2026-10-02)

Isolated candidate from provenance-only `cc49f62d`. No formation, retrieval
completeness, selector, project binding, admissibility, permission, lifecycle,
API model or database schema change. Other candidates remain separate and frozen.

The existing hook `additionalContext` string retains the untrusted reference
frame and complete JSON record blocks, followed by one fixed `atc_delivery`
JSON block. That block contains only `scope: record_items`, a closed state and a
fixed message. It is a delivery notice, not canonical evidence or a citation.
Quoted status-looking text remains inside the record's escaped content field.
No query, source path, capsule content, denied IDs or exception text is copied.

| State | Established meaning |
|---|---|
| `records_returned` | Complete record blocks fit this delivery. Support beyond them remains unknown; this is not a complete-answer claim. |
| `records_empty` | A successful record response returned no items. This does not assess any separate project capsule or establish absent global memory. |
| `budget_omitted` | The renderer omitted a whole record for its output budget, or existing validated Core pack metadata explicitly reports budget omission. Retained record blocks remain complete. |
| `malformed_response` | The returned record shape, optional pack metadata or JSON encoding is invalid or contradictory. No record text from that response is delivered. |
| `retrieval_unavailable` | The hook could not obtain a usable response because client setup, connection, request, authorization or transport failed. It does not establish empty memory. |

Optional Core pack metadata is validated against the existing strict
`ContextPackMetadata` model and actual returned item count. A budget reason must
have `truncated: true` and a positive omitted count. Missing/null metadata stays
compatible with the existing optional contract. No new API status or semantic
unknown-facet field is introduced, and truncation is not repurposed as semantic
uncertainty. A separate `project_context` is not parsed, rendered or assessed by
this record notice. MCP instructions describe the existing records/capsule/error
distinction without changing tool inputs or outputs.

Every output stays within the requested character budget. Whole records and
the complete notice must fit together; this adds a fixed delivery cost and may
omit another record. If even a complete notice cannot fit, output remains empty.
Do not truncate a JSON block, source statement, negation or status to force it
into a tiny budget. Low budgets permitted by the existing hook contract can
therefore remain silent; no input-schema expansion is authorized here.

New adversarial tests cover empty/capsule, forged status, malformed/mixed/error
responses, strict/contradictory metadata, local/Core budget omission, all
character budgets, safe exception classification and actual cold new-reader
Core/capsule/permission behavior. Existing provenance and deletion/replay
assertions continue to check record evidence, with the fixed notice assessed
separately. Frozen acceptance files/runner/grader/manifests remain unchanged;
holdouts stay sealed. Installed clients and a reader model remain unrun.

## Local validation and scope

Reviewed runtime `2affdc7bd7711f1af471021c101c386700946e3c`: 171 focused
tests pass, including actual cold Core/permission and synthetic stdio checks.
The broader Python 3.14.3 run passes 3,541 tests with 21 skips and three warnings.
It explicitly excludes the sealed-holdout integrity reader and the two native
registry transaction tests that already reproduce Windows error 6801 on
unchanged base. Ruff check/format, production and strict new-test mypy,
documentation and whitespace checks pass. Scoped independent review found
no remaining issue; it did not execute tests.

The frozen development diagnostic remains 4/14 checkpoints passing, ten
failing, with zero operation failures and deliberate CLI exit 1. All fourteen
outcomes, failure details, retrieved evidence and existing delivered evidence
match provenance-only `cc49f62d`. The unchanged adapter exposes seven additional
generic notices as uncited delivered chunks; they do not become named unknown
facets or acceptance credit. No post-score tuning or grader change was made.

Initial synthetic cold tests exposed fixture trailing whitespace that existing
Core normalization strips; the fixture input was normalized, not production
behavior or evidence assertions. The first broader run exposed the existing
stdio expectation for blank unavailable output; that test now asserts the
fixed notice and retains the protocol/privacy checks. Historical failures and
exact-revision receipts are preserved in the external delivery folder.
