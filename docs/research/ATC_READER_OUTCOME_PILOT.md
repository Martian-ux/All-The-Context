# ATC reader-outcome pilot

## Status and boundary

This is a six-cell pipeline sanity bridge, not a model result and not a
product benchmark. It connects the disposable Core capture/correction/
bootstrap journey to reader-ready packets and deterministic exact-answer
grading. The bridge does not run a provider, model, tokenizer, live Core, or
personal-state journey. Canned unit and integration outputs are labelled
`non_model_fixture` and cannot be used as model evidence.

The later reader route must be an already-authorized, no-charge route. This
package makes no provider, model, tokenizer, pricing, or spending commitment.
Reader execution remains unproven and deferred until such a route is selected
and separately authorized.

## Six fixed cells

Each of the two tasks is built three times:

| Arm | Context source | Construction/access/retrieval accounting |
| --- | --- | --- |
| `simple_no_memory` | Empty context | Zero construction, access, and retrieval |
| `maintained_context_file` | Query-blind maintained file | Construction is recorded once per arm; zero reader access and retrieval |
| `actual_atc_bootstrap` | Real `/v1/context/bootstrap` response | One Core access and one retrieval per task |

The task instructions are identical across arms:

- `State the user's current answer-style preference exactly, or reply UNKNOWN if context does not establish it.`
- `State the configured deployment region exactly, or reply UNKNOWN if context does not establish it.`

The positive task has the corrected preference as its evaluation-only answer.
The negative task's evaluation-only answer is `UNKNOWN`. The negative task may
receive the irrelevant preference in the maintained context file; that context
presence is not a failure. Repeating it in the answer is graded as
`inappropriate_irrelevant_disclosure`. An old preference used as the current
answer is separately graded as `stale_preference_use`; other wrong answers are
`unsupported_answer`. `UNKNOWN` from the no-memory positive cell is a safe
abstention, not evidence of superiority or inferiority.

The grader binds each fixed arm to its source identity, context/access/retrieval
contract, and separate construction accounting. No-memory packets must be
empty; the maintained file must be query-blind across tasks; maintained and
bootstrap context text may legitimately be identical.
Packets expose only the common task instruction and context text to a reader.
They do not expose arm identifiers, expected answers, scoring rules, canonical
truth, Core proof, or packet accounting. Packet hashes bind the task input,
context, source classification, common reader limits, prompt, and declared
construction/access/retrieval units. Results bind the packet hash, answer,
supplied reader configuration, usage fields, provenance, and result hash.
Both packet and result hashes are recomputed when grading, including for public
dataclass objects whose nested mappings may have been mutated. Unknown usage
fields are rejected.

## Reader-result contract

`bench/cross_client_reader_outcomes.py` accepts a result with:

- the packet hash and task key;
- the reader answer;
- an explicit `evidence_kind` of `external_reader` or `non_model_fixture`;
- optional configuration, usage, and provenance mappings; and
- a reproducible result hash.

Usage is sparse by design: fields absent from the route remain absent rather
than being fabricated. Honest nonzero input/output tokens, calls, provider
calls, network calls, and costs are accepted for a later external route.
Non-model fixtures may not claim any such usage. The common reader ceiling is
fixed at 2,048 total input tokens, 32 output tokens, and zero tools, with
`deferred_external_reader_config` and `deferred_external_tokenizer_config` as
model and tokenizer placeholders. Callers cannot override the ceiling. A route
that observes an overflow must fail before provider execution; the bridge uses
no padding or truncation and does not invent tokenizer counts.

## Core proof and maintained-file construction

`tests/integration/test_cross_client_memory_acceptance.py` uses a disposable
Core directory and executable API/SQLite assertions to prove the journey:

1. a user preference is captured through the public lifecycle route;
2. the canonical SQLite/API row is corrected in place;
3. the old value remains only in the truth evidence history;
4. real bootstrap returns the corrected value and not the old value; and
5. the unknown deployment-region bootstrap does not gain a fabricated answer.

The maintained file is constructed outside Core from the Core-backed candidate
and truth evidence rows, after asserting the capture-event identity, record
identity, content, explicit-user witness, and chronological ordering. It
receives no task query and uses a deterministic last-explicit-value-per-slot
rule. Its maintenance units are reported once per arm in the construction/
access/retrieval accounting and are not attached to either per-task reader
packet. Core proof is kept in the integration assertions; it is not treated as
reader output or hidden reader evidence.

## Interpretation and next work

The report contains only six raw cell outcomes, hashes, supplied usage fields,
and denominators. It explicitly disallows superiority, noninferiority, and
percentage-point claims. This pair of tasks verifies packet construction,
correction visibility, abstention, disclosure classification, and provenance
binding. It does not establish a realistic changed-preference capability.

A later proposal may define a realistic task family, an independent rubric, and
a separately generated and frozen holdout. The implementer must not access the
holdout before freezing and review. That later work is outside this pilot.

## Offline deployment handoff repair receipt (2026-09-06)

The bounded worker used the explicitly authorized frozen diagnostic inputs,
without changing their query or current-record contents. This is a retrieval
repair check, not a reader outcome or a new holdout evaluation. No model,
reader, network, paid service, delegation, or live state was used.

Checkpoint: clean HEAD `44d5da0a5c71e24a9bd0abd7d00c3df2c2852085`, tree
`1a52f7a46927cae67bdd6ba20866ccb233646b52`; Python 3.12.3 from `.venv-atc`;
`PYTHONPATH` and the imported `allthecontext` path both resolve to this checkout's
`packages/allthecontext/src`. Temporary file probes proved checkout and `.git`
write access. The unique writable pytest root was
`.pytest-worker-20260906-repair`, with separate subdirectories for each run.
The external bundle SHA-256 was
`929157cebcc435a153a97d41ebf65ee87afa7414c2bfa2017e9ad5337f8aee3c`; the
manifest SHA-256 was
`10aa6618b080b341ed3f3311d1e917156ee75d66f5f371e5e4f55a2c89608a49`.
Neither input was edited or copied into the repository.

The causal production change is confined to `retrieval.py`. Request-leading
verbs, handoff formatting, terminal punctuation, and narrow negated preference
output clauses no longer become required factual anchors. Factual negation and
positive preference terms remain. A small lexical equivalence lets “blocker”
match “blocked” or “blocking.” An explicit project no longer invents a zero kind
compatibility score when the caller did not request a record kind. Inspection
found no actual preference-intent classifier in the accepted checkout: the
reported kind mismatch was a literal query-token/storage-kind comparison.
Explicit kind filters and mandatory preference retrieval remain intact.
Numeric thresholds, authorization, temporal resolution, conflict handling, and
the pre/post-budget content-union checks were not changed.

Relevance heuristics are not authorization boundaries. Operation permissions
and per-record client permissions establish access before scoring; requested
record scopes select categories. Query normalization or a matching project
name cannot grant access. Content coverage then decides usefulness within that
eligible pool. A complete union must still survive selection and budget limits.

The external-manifest integration test is opt-in via `ATC_FROZEN_INPUT_DIR`.
It verifies both hashes and recreates the seven frozen current records in a
temporary Core, using new IDs and timestamps, rather than replaying the original
historical proposal stream. It sends the exact frozen bootstrap request through
ASGI with a registered `context:read` client. Replacing only the retrieval module
in process with the accepted HEAD version reproduces the preference-only
failure (2.35 seconds). The repaired version returns HTTP 200, `local_core`,
`explicit_project_match`, all three requested facts, seven selected records,
zero omitted, 882 pack characters and 940 total characters out of 1600.
The mandatory preference and other same-project records remain present; no
claim of perfect precision or downstream reader success is made.

Focused unit coverage includes output-negation variants, a genuine preference
request, independent facts, unknown facts, wrong requested project scope,
denied client access, corrections, deletion, open conflicts, and insufficient
budget, plus the existing admissibility/bootstrap/retrieval/bridge unit tests.
The initial conflict test used explicit statements and therefore exercised
Core's resolution policy; non-explicit competing evidence correctly exercises
an open conflict. Production work was one causal repair with a follow-up
narrowing of request-verb normalization to preserve the noun “state.”

Environment limitations: system cryptography is 41.0.7 rather than locked 50;
encryption and Edge HTTP were not exercised. The environment-only `httpx2`
alias lacks Starlette's private `_client` attribute, and the copied AnyIO thread
bridge stalls. The successful ASGI run used process-only test-runner adapters:
`sys.modules['httpx2'] = httpx` and an async `anyio.to_thread.run_sync` adapter
that executes the supplied callable inline. These adapters alter no repository
or venv files and no retrieval/auth logic, but do not validate normal threaded
HTTP execution. Windows must repeat the integration test without adapters.
Earlier attempts stopped at collection, were interrupted at the thread stall,
or hit bounded 20/30-second diagnostic timeouts; a setup attempt also rejected
an invalid project capability before being corrected to `context:read`.

`python -m ruff check .` and `python -m mypy packages/allthecontext/src` both
report missing modules offline; static checks are deferred to the Windows
manager. The full suite was deliberately not run. Shared STATUS, DECISIONS,
and REQUIREMENTS_TRACEABILITY updates are deferred to shipping integration per
the worker's explicit scope override.

Final gates: 73 focused unit tests passed in 10.42 seconds across
`test_retrieval_bootstrap_composition`, `test_admissibility`,
`test_retrieval_v3_integration`, `test_bootstrap`, `test_retrieval_v2`,
`test_retrieval_v3_combined`, `test_retrieval_contracts`, and
`test_cross_client_reader_outcomes`. The adapted frozen ASGI test passed in
1.16 seconds (one environment import-rewrite warning). Syntax compilation of
all three changed Python files passed in 0.019 seconds; `git diff --check`
passed. All pytest data was confined to the unique run-owned basetemp and
removed after validation.


## Independent repair review receipt (2026-09-06)

Under ATC-WF-2026-09-05 and ATC-LINUX-ASTRA-LOW-2026-09-06, the independent
review verified clean implementation HEAD
`b8b6301e890843dcf4c3d848eb27509bb3688717`, tree
`cf6f789e3d0fb0e8a7c143fd1db068cb5065fbc7`, and inspected its complete diff
against accepted base `44d5da0a5c71e24a9bd0abd7d00c3df2c2852085`.
Python 3.12.3 imported ATC from this checkout; a Git object write succeeded.
Both external input hashes matched the values recorded above. Test data used
unique checkout-local `.review-pytest-zgkmowrw` subdirectories, removed after
validation. No external fixture was modified.

Verdict: **REPAIR**. All 73 original focused tests passed, but eight new
behavioral cases failed on the implementation before repair:

- Six exclusions (`not preferences`, capitalized/punctuated variants, curly
  apostrophe `Don’t include preferences`, `Do not include my preferences`,
  `No preferences, please`, and `Don't include: preferences`) left positive
  content anchors that suppressed the requested project fact. The mandatory
  preference remained, recreating a preference-only pack.
- `State regulations for Aurora` lost the meaningful jurisdiction term, and
  `Aurora handoff owner` lost the task facet `handoff`. Both admitted incomplete
  records instead of abstaining.

The bounded repair recognizes those preference output clauses, restricts bare
`not/no preferences` to clause endings, and preserves following unrelated
anchors. It narrows request-leading verb removal and handoff packaging removal
so the proven noun cases retain their content requirements. Additional checks
preserve factual negation, positive preference requests, and nearby latency
requirements. This remains a conservative lexical projection, not general
natural-language negation understanding. No thresholds or project literals
were added to production code.

Inspection confirmed explicit `SearchRequest.kinds` still filters SQL candidates
and supplies kind compatibility; unspecified kinds alone use the neutral path.
Bootstrap's mandatory preference category remains independent of output wording.
Operation authorization at the API, per-client/request-scope selection before
ranking, temporal resolution, correction/deletion handling, unresolved-conflict
and stale-record gating, and pre/post-budget content unions are unchanged.
Focused tests cover these boundaries, including unrelated-project isolation.
Natural-language category wording is not an authorization or typed-kind filter.

The existing frozen regression uses the real authenticated Core bootstrap API
and current-record reconstruction, not symbolic answer substitution. Its fact
assertion is now explicitly non-vacuous and also requires the frozen preference.
Replacing only the retrieval module in process with the exact accepted-base
source made this same regression fail: all three required facts were absent and
only the irrelevant preference was present. The repaired replay selected all
three facts and the required preference/context, seven records total, HTTP 200,
`local_core`, `explicit_project_match`, 882 pack characters and 940 total
characters within 1600. This establishes retrieval repair, not reader/model
superiority or historical proposal replay.

The final focused unit run passed 82 tests in 11.28 seconds. Ruff and mypy
commands were attempted and reported missing modules; nothing was installed.
The full suite was not run, per review scope. Integration uses the same
process-only httpx2 and inline AnyIO adapters described above, with no product
or environment-file changes. Windows, normal threaded HTTP, locked dependency
validation, static checks, and shipping updates to STATUS, DECISIONS, and
REQUIREMENTS_TRACEABILITY remain deferred to the owning desktop manager.

The final standalone frozen ASGI regression passed with the strengthened fact
and preference assertions. An additional attempt to execute the pre-existing
six-cell TestClient bridge stalled in this environment and was interrupted;
that threaded bridge execution remains deferred. Its unit contract tests
passed in the focused run. Syntax compilation of the three owned Python
files and `git diff --check` passed.
