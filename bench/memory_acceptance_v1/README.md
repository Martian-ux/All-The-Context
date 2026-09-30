# Frozen memory acceptance v1

This small executable slice extends the existing Memory Lab with ordinary-language
capture and multiple lifecycle checkpoints. It reuses the lab's
`FailureAttribution` stages and the reader bridge's `ReaderUsage` validation.
It does not replace the broader evaluation program or claim best-in-class memory.

The freeze is based on merged main `1ceec65f0b356bf3f7b2be9cfce6e990dbbde637`.
The [manifest](manifest.json) binds canonical JSON hashes for the two partitions.
Regression tests also pin those hashes independently. Change expectations only
by creating a new version with a documented reason; preserve v1 and its results.

## Development and holdout

The [development fixture](development/scenarios.json) contains six scenarios and
fourteen checkpoints. It covers personal preferences, mixed preference/project
turns, decisions, observed outcomes, corrections, partial recall and unknowns,
current provenance, project isolation, restart, rebuild, deletion and retry.
All content is synthetic. Nothing reads an existing user vault or private session.

The [holdout fixture](holdout/scenarios.json) is separate and is not executed by
default or during tests. Its integrity is checked without grading its cases.
Do not tune against it. After freezing adapter code/configuration and a candidate
identity, a later evaluation owner may explicitly enable it once and preserve
the receipt. Public repository fixtures are not blinded or secret; this is a
development/execution separation, not a claim that an author never saw them.

## Execute the development acceptance

From the repository root in an existing healthy Python 3.12+ development environment:

```powershell
python -m bench.memory_acceptance --candidate-identity <exact-source-and-config-identity> --output result.json
python -m pytest tests/unit/test_memory_acceptance.py --basetemp .test-runs/acceptance-tests-<unique-run>
```

The runner prints a summary and returns **1 when acceptance fails**, including
unsupported operations. A green grader test suite does not mean ATC passed the
memory acceptance. The CLI uses unique disposable vaults beneath
`.test-runs/memory-acceptance`; `--scratch-root` selects another run-owned parent.
On Windows, the Core's private-vault ACL can require execution outside an
AppContainer sandbox. An access failure is environment evidence, not a memory score.

No package installation, HTTP listener, model call, installed harness, personal
vault, or competitor engine is required by the shipped ATC adapter. Hindsight
integration remains unimplemented and unrun.

## Stable comparison contract

The [contract and runner](../memory_acceptance.py) expose
`atc.memory-acceptance.adapter.v1`. Supply a trusted implementation through
`--adapter module:factory`. Use the same fixture, surfaces, 4,000-character request
budgets, maximum 32 chunks per surface and grader for every candidate.

An adapter implements `reset`, `capture(Event)`, `control`, `checkpoint(Request)`,
`usage`, and `close`. It receives input events and natural questions, never facts,
source expectations, forbidden patterns, unknown labels, or other oracle fields.
It must not seed canonical memories or approve candidates using evaluator labels.
One-time connection/consent and an explicit forget operation are allowed; routine
approval, manual memory curation and repairing fixtures for a particular engine
are outside this acceptance workflow.

`Observation` separates retrieved chunks from delivered chunks. Each chunk may
carry source-event citations and verbatim supporting quotations. A matching fact
must cite its expected current source, with a quote supporting that fact. Stale,
invented, cross-project and missing references fail separately from missing text.
Required unknown facets need an explicit signal from the system; an empty result
does not establish that a reader understood uncertainty. This contract does not
run an answer model or prove that a reader will use memory correctly.

`unknowns` are normalized semantic facet names emitted by an adapter from its
actual result, such as `rollback_procedure`. They must never be copied from the
oracle. If a system exposes no such signal, report that limitation and retain
the resulting failures. Unknown signal support can be evaluated separately from
the absence of fabricated facts.

Operation receipts distinguish successful capture from the optional observed
count of formed records. Unknown counts stay null. Unsupported lifecycle operations
are failures, not successful no-ops. Usage includes only observed fields; missing
token, timing and cost measurements are not zero-cost evidence.

The [ATC adapter](../memory_acceptance_atc.py) uses the public capture, bootstrap,
and forget APIs over in-process TestClient. It resolves exposed Core record/source
references against the synthetic captured events. It applies the actual Codex and
Claude Code hook formatting functions; it does not manufacture citations stripped
by those functions. Project capsule sections are included in retrieved API context.
The `rebuild` control invokes the existing integrity projection rebuild; lexical
FTS is rebuilt per query. It does not claim a complete archival reimport or a new
engine's index rebuild. `forget_event` targets formed memory records using the
existing reversible forget API, not physical purge of capture evidence.

Every report labels installed clients and the reader model **UNRUN**. Hook
formatting and synthetic principal tests are not live cross-app validation.
Before making a consumer usability claim, separately exercise connection, actual
automatic capture, delivery, correction, restart and forgetting in installed apps.

## Baseline evidence and limits

[Merged-main baseline](baseline-main.json): **2/14 checkpoints pass**, all input
capture receipts and lifecycle operations succeed. The preference-only API
checkpoints pass. Failures include project text conflated into a personal
preference, omitted decisions/outcomes, a correction leaving the old preference
active, and hook text without current provenance. After explicit forgetting,
restart/rebuild/replay returns no forgotten text; those checkpoints fail because
the current contract emits no explicit unknown facet.

No held-out score, live installed-client result, Hindsight result, answer quality,
latency, token cost, physical purge, or complete archive-reimport claim follows.
Phrase-pattern checks are deterministic surface evidence, not a semantic judge;
unanticipated equivalent paraphrases may fail. Any broader grading revision needs
a separately versioned preregistered contract rather than tuning v1 to an engine.
Baseline report identity refers to unchanged production code at the merged main
revision; the new fixture/runner code is identified by its delivery commit.

Validation on the isolated Windows checkout: 12 focused tests pass; the full
`python -m pytest -n 4 --basetemp .test-runs/acceptance-full-20260930` run passes
3,453 tests with 21 capability/input skips and 3 existing deprecation warnings
in 302.17 seconds. Repository Ruff, production mypy (113 source files), strict
mypy of the two new benchmark modules, documentation links and Git whitespace
checks pass. No skipped installed-client test is counted as live evidence.
