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
