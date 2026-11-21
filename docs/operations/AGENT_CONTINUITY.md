# Source-process and agent continuity smoke

Run the source harness from a clean committed checkout using the pinned product
Python environment. An authorized v2 batch uses a fresh run-owned output,
after controller-owned preflight:

```powershell
& $productPython scripts/smoke_agent_continuity.py --mode real --output "<run-owned-output>" --model gpt-6.1-sol --effort xhigh --require-complete
```

An existing output directory is refused before starting any process. A different
name does not authorize another batch, resume, per-arm rerun or selective favorable
retry. The harness leaves its report,
fictional vault, process logs, raw client JSONL, final messages, invocation
manifests, native trace bundles, tool audits, usage events and persisted actions in that output. It
does not install or configure an everyday client. Never publish these receipts
as a general memory benchmark or packaged/OS-credential acceptance result.

## Prospective action protocol v2

`agent-continuity-action-v2` exposes exactly the existing evaluator contract to
every arm through one identical rendered description in the actual continuation
prompt and MCP `write_result` tool. The only provenance fact keys are `route`,
`strategy`, `completed`, `task`, `handoff`, `reviewer`. Each citation permits only
`source_reference`, `record_id`, `provenance_ids`. Supported available facts need
exact source references; ATC also needs current record/observation provenance.
Optional record/provenance metadata in other arms must also match its evidence.
Do not cite derived `rows`, `repeat_completed`, `unknown`, `launch_window` or the
`provenance` object itself. Unavailable facts stay null, appear in `unknown` and
have no citation. There are no additional citation metadata keys and no answers
in this contract. Evaluator rules and task fixture/expected outcomes are unchanged.

`frozen-action-protocol.json` records the machine-readable contract, ID/version,
SHA256 of canonical JSON (sorted keys, compact separators, UTF-8), planned probes,
nine cells and limits before inference. Report schema 2 and base/per-session
invocations carry the same protocol identity/hash. The batch has exactly three
isolation probes followed by all three arms at current/post_forget/post_acl:
twelve fresh ephemeral main-account Sol/xhigh sessions maximum, with the same
five-minute task/three-minute probe bounds, history construction and permissions.
Pre-execution reservations are durable; even a failed attempt spends its slot.
There is no per-arm session retry. Existing within-session append-only action
retention/evaluation is unchanged. All failures and incomplete coverage survive.

Public tests use independent fictional actions and invented source identifiers
across all nine arm/phase combinations. Valid citations and derived-field,
extra-metadata and unavailable-fact negatives exercise the evaluator without
model/Core execution. They do not replay private historical actions or establish
live compatibility, held-out efficacy or general memory performance.

The native controller owns candidate-bound checks and any separately authorized
real batch; independent review remains required. A completed batch requires nine
distinct isolated, evaluated actual actions, not nine successes. A clean source
candidate or offline contract check establishes no native/live/full acceptance.

`report.json` is the entry point. By default exit zero means a report was generated; inspect
`status`, `boundary`, `errors`, `not_executed` and each action's `evaluation`.
`COMPLETED` can include unsuccessful agent actions. `BLOCKED` identifies a setup,
isolation, entitlement, tool or execution boundary and retains all earlier
evidence. It never substitutes canned actions for missing real outputs. There is
no automatic rerun of an unfavorable arm, provider fallback or retry after a
client/entitlement denial. Individual client sessions have five minutes, probes
three minutes, and the whole real run has a 77.5-minute deadline within its native
80-minute gate. Multiple useful retrievals are allowed within a session.

Native acceptance uses `--require-complete`: the report is still written, but
incomplete, BLOCKED or scripted coverage returns nonzero. `experiment_complete`
requires all nine unique actual arm/phase cells to have recorded executions,
verified native isolation and independent action evaluation, alongside the three
isolation probes, completed public journey and owned cleanup. A measured task or
provenance failure remains a completed observation; `semantic_success=false` does
not by itself fail this completion gate. It never retries a favorable answer.

## What is measured

The committed [v1 fixture](../../scripts/fixtures/agent_continuity_v1.json) freezes
the fictional task, history and machine-checkable outcome rules before model
execution. A source Core subprocess receives public lifecycle capture, proposals,
actual owner approvals and a witnessed correction. The harness stops its owned
process and starts another over the disposable vault, requiring a different PID.
Current search/bootstrap and the ordinary `codex_read` STDIO MCP adapter are
checked. Public forget removes one fact; public client revocation removes the
reader's access. Each change is followed by another actual Core restart and,
when isolation is established, fresh recorded continuation sessions.

ATC, maintained summary and no-saved-memory arms receive identical unfinished
input and artifact tools, the same model/effort and fresh ephemeral client
invocations. ATC alone receives the ordinary read-only ATC MCP integration. The
strong summary uses a deterministic query-blind maintainer: it preserves every
eligible current event, exact references, completed/failed/unfinished distinctions
and explicitly separated superseded evidence. It receives the same eligible
history used for ATC, without evaluator rules. Forget removes the fact from that
eligible history; revocation removes the principal's saved project history.
Summary construction units, source/summary characters and elapsed time are
recorded; this maintainer consumes zero model calls/tokens. Public ATC capture and
retrieval requests have separate timing/size telemetry. Client usage is retained
where observable, including cached input; unobservable attribution stays unknown.

The evaluator reads actual persisted dispatch actions rather than grading the
agent's final narrative. It checks calculation, corrected choice, completed work,
failed approach, exact source references and ATC record/observation provenance,
and warranted uncertainty. Every persisted attempt is evaluated, so a later
correct action cannot erase an earlier stale or repeated failed action. Unknown
information in the no-memory arm can be a warranted-uncertainty result, separately
from successful informed task continuation. This is one development example,
not held-out or superiority evidence.

Ordinary forget excludes current retrieval while authorized deleted history is
retained. The harness checks that distinction without exposing admin tools or
owner credentials to agents. It does not claim physical purge or crash recovery.

## Client and tool boundary

Real mode requires the installed supported included-access Codex client and
existing ChatGPT main-account authentication. It uses shared `CODEX_HOME` solely
for that authentication, with `--ignore-user-config --ignore-rules --ephemeral`
and explicit run-owned invocation configuration. It never copies, links, rotates
or prints authentication, writes main config/hooks/rules, or reads an API key.
Authentication refresh and some client logs/temp activity still belong to the
shared client home; this limitation is recorded. This is source process evidence,
not an OS credential isolation claim.

The harness inspects local help/features and the bundled/effective model catalog.
The run-owned catalog preserves the selected model identity, provider, reasoning
support and tool mode, changing only `apply_patch_tool_type` to null and
`shell_type` to disabled. `features.shell_tool=false` is also required. Disabling
`unified_exec` alone is insufficient. Unrelated apps/plugins, host skill discovery,
hooks, browser/computer tools, web, multi-agent tools and fast modes are disabled
through invocation configuration. This does not itself establish isolation.

Before task continuations, actual probe sessions must expose auditable executed
Code Mode roster source **and its runtime output** in the child's native trace.
Codex 0.167.2 deliberately reduces `exec --json`: it retains nested MCP calls but
omits the outer source/result. The harness keeps that CLI route and sets only a
fresh absolute `CODEX_ROLLOUT_TRACE_ROOT` for each actual child; it never inherits
the parent's trace setting or enables tracing on unrelated sessions. Help/model
inspections receive no trace setting. `agents.enabled=false`,
`features.multi_agent_v2=false`, `tools.update_plan.enabled=false` and
`tools.experimental_request_user_input.enabled=false` pin the intended surface.

Under `native-trace/`, require one bundle whose schema-2 manifest root thread
matches this invocation's `thread.started`. Raw `trace.jsonl` starts at seq 1
and requires every subsequent integer in recorded order, matching the pinned
writer's `next_seq: 1`; zero-based origins, gaps and duplicates are rejected. Records
must correlate the exact `ROSTER_CODE` expression (optionally followed by one
terminal LF), runtime cell,
model-visible call and turn identity with a later completed `code_cell_ended`.
Its bundle-relative payload must contain the externally tagged
`response.Result`, matching cell ID, null `error_text` and actual `input_text`
content. Yielded initial responses require the matching later terminal result.
Recording closure (`recording_completed`), an intact reference set, successful
process exit and a normally completed CLI turn are all required. Traversal,
absolute paths, symlinks/junctions, wrong schema/IDs, ambiguity and missing files
fail closed. A native reducer success alone would not establish isolation.
The same source rule applies to the resource cell. Requested-expression and raw
executed-source hashes are both retained; no whitespace stripping or executable
statement normalization is allowed.

The declared and callable **nested** Code Mode lists must equal exactly the
three artifact tools, the four read-only ATC tools for ATC, and these four native
utilities: `clock__curr_time`, `list_mcp_resources`,
`list_mcp_resource_templates`, `read_mcp_resource`. No subset-only check is used.
Clock reads UTC without filesystem/network inputs. Resource helpers resolve only
session-configured MCP connections, rejecting unknown server names; a resource
URI is sent to that server, never opened as a local path or URL by the helper.
The artifact server and `codex_read` adapter register tools without resources or
templates. A separate exact native cell must exercise empty/unsupported resource
inventories, unknown-server rejection and evaluator/answer/other-arm/fixture/file
URI denials on **every configured connection**, including an unmapped per-session
canary. Successful reads, unexpected resources or missing negatives block evidence.
No generic filesystem resource server or unbounded template is configured.

`ALL_TOOLS` is the complete nested callable list, not a complete top-level model
tool list. Every native inference request is also inspected for the direct
Code Mode `functions.exec`/`functions.wait` namespace and optional native
`tool_search`, rejecting extra direct capabilities. Thread/turn/request bindings,
names and a hash of each full tool declaration are retained in the verified
roster's content-free inventory. Incremental `response.create` requests may inherit
the full schema only through an observed completed `previous_response_id` in this
same thread and turn. Each request still pins model/effort; a redeclared schema
must match its parent. Unknown, stale or cross-turn parents and missing initial
declarations block acceptance. Scalar JSON listing strings are decoded before the
same exact empty inventory/server/template checks used for structured results.
Ambient Node/process/network globals and forbidden callables must remain absent.
These probes recur in every continuation. A final-message tool list, model
assertion or successful-call subset cannot replace them. The public source/MCP
journey can still complete without fabricated agent results when this gate blocks.

The pinned source contracts are Codex release
[`ce21072c`](https://github.com/openai/codex/tree/ce21072c90d32c40af7a55b60b2fb35c84a43a6a/codex-rs):
`rollout-trace/README.md`, `raw_event.rs`, `code_cell.rs`, `writer.rs`,
`code-mode-protocol/src/runtime.rs`/`response.rs`, and Core's
`tools/spec_plan.rs`, `handlers/current_time.rs` and `handlers/mcp_resource.rs`.
The public native-shape fixture uses independently invented correlated IDs and
parser markers. Actual native compatibility remains a required controller
observation; fixture shapes cannot certify an executable.

Artifact reads are exact dictionary lookups, with no filesystem or URL reader.
All arms share that tool surface. Results append to bounded action files in the
specific session; models supply neither a destination path nor executable code.
Actual negative calls must deny evaluator, answer, other-arm and source fixture
paths. The evaluator, raw transcripts, other sessions and outcome rules have no
artifact tool mapping. Read-only ATC has only the test principal credential,
passed in memory; owner tokens never enter a client subprocess or report.
Credential values unexpectedly emitted by a client are redacted before stream
artifacts are written, and invalidate acceptance. After reaping each child, all
files under its owned native trace root are also scanned for known synthetic
grants, redacted in place if necessary, and inventoried with hashes in
`native-trace-scan.json`. Redaction or an incomplete scan prevents acceptance.
This includes failed/timed-out children and malformed CLI streams. Trace files
contain raw fictional prompts, outputs, paths and MCP results: retain them locally
in the authorized diagnostic output, never upload them or trace personal work.
Authentication material is neither read nor copied by the harness. Processes are
bounded and cleanup uses only handles/PIDs created by this run; complete and
incomplete bundles are retained without deleting neighboring traces or old runs.

`client-event-classification.json` preserves nonfatal CLI warning/config/deprecation
items separately from fatal top-level errors, failed turns and failed MCP calls.
Failed resource reads are expected isolation negatives only when uniquely bound
to the verified exact resource cell's requested arguments, native invocation,
MCP runtime (for configured servers) and terminal denial. Client arguments and
the exact error must match that native evidence. `expected_resource_failures` in
the verified roster and execution receipt records those bindings; `tool_errors`
and raw JSONL retain every failure. Transport failures, unrelated calls and
uncorrelated errors still block acceptance.
Warnings do not suppress turn/provider errors, nonzero exits, timeouts or pinned
model reroutes. Raw JSONL and CLI `turn.completed` usage remain authoritative for
turn totals; native per-request usage overlaps and is **not** added to those totals.

## Scripted coverage and native ownership

```powershell
& $productPython scripts/smoke_agent_continuity.py --mode scripted --output "<run-owned-scripted-output>"
```

This mode starts real source Core/STDIO processes but uses explicitly canned
actions. It never calls a model or inspects main authentication. Its results prove
only exercised source orchestration and evaluator behavior. Focused tests in
`tests/integration/test_agent_continuity_harness.py` include an independently
authored positive action, intentionally broken actions, actual artifact STDIO
escape negatives, missing-action/error receipts and refusal to reuse outputs.
Invented warning/usage lifecycle events and neutral native schema shapes exercise
the parser and driver without a model call or successful agent outcome.
`tests/unit/test_agent_continuity_native_capture.py` constructs full invented
three-request chains with completed same-thread/turn parent links, inherited
schema declarations, exact source plus one optional LF and scalar empty resource
inventories. Native resource invocations, runtimes, terminal denials and client
failures have matching invented identities and arguments.

Positive and negative cases cover origins/gaps/duplicates/order, ID/payload/closure
mismatches, yielded-without-completion, stale/cross-turn/missing response parents,
changed tool schemas, unexpected direct tools, resource leaks, unrelated failures,
transport errors and failed-trace credential redaction. These are fixture-level
contracts, without private transcript, archive or recorded-fidelity claims.
Completion gate cases preserve unsuccessful measured actions as valid completed
observations while rejecting missing cells, probes, isolation, execution or cleanup.

The maintained native controller owns focused tests, the real command, independent
candidate-bound review, Ruff, mypy, full pytest, documentation and whitespace
checks. Its declared preflight must pass before any separately authorized fresh
model probe; workers run only their assigned checks. Known sandbox validation
denials retain one failure receipt and use only the declared typed native handoff
on a clean committed candidate. That handoff establishes no model/full-suite
success and waives no review or final gate. Keep exact-source results and transient
execution outcomes in external run receipts. A clean implementation handoff does
not assert those gates passed.
