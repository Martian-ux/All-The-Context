# ATC Memory Lab worker governance

## Reproducible independent-worker experiments

| Field | Value |
|---|---|
| Version | 1 |
| Date | July 23, 2026 |
| Status | Accepted research governance; no production authority |
| Completed reference wave | `research/memory-lab/wave2-manifest.json` |
| Latest completed wave | `research/memory-lab/wave3-manifest.json` |

ATC uses independent visible Codex worker threads to reduce shared-context
anchoring and to run implementation, supplier, and research challenges in
parallel. Independence is useful only when the resulting work remains
inspectable. A worker is therefore an evidence producer, never an integration
authority.

## Authority model

The coordinator is the only thread allowed to integrate worker commits, edit
project governance for the wave, or describe an experiment as an ATC result.
Every worker starts from one immutable coordinator commit in its own git
worktree, owns a non-overlapping scope, commits its result, and does not merge
or push.

Worker output—including source summaries, generated fixtures, benchmark
reports, and code—is untrusted until the coordinator reads the diff, checks its
scope and provenance, reproduces the relevant result, and runs repository
gates. This mirrors the product rule that imported text is data, not authority.

## Evidence ladder

Results carry an explicit evidence level:

1. `L0`: specification or research claim only;
2. `L1`: deterministic synthetic worker result;
3. `L2`: coordinator-reproduced deterministic integrated result;
4. `L3`: isolated external-supplier result with pinned provenance;
5. `L4`: cross-platform or repeated stochastic evidence; and
6. `L5`: consented product evidence.

Higher levels do not erase lower-level failures. A benchmark cell that is
blocked, skipped, unsafe, or falsified remains part of the record.

## External supplier gate

External code is denied by default. A wave may name one supplier cell and must
record all of the following before execution:

- canonical official origin and immutable revision;
- license and component-notice compatibility;
- dependencies, install hooks, native components, and vulnerability findings;
- network, model-provider, telemetry, and data-egress behavior;
- a disposable local environment with no credentials or personal context;
- no production dependency, system service, or canonical Core access; and
- a read-only adapter or an honest skipped receipt.

Cloning source is not permission to install or execute it. Passing the supplier
gate is not permission to copy its implementation into ATC.

## Worker completion receipt

Each worker returns:

- commit SHA and exact changed files;
- commands, environment, and validation results;
- frozen inputs, fixtures, model/provider settings, and clock;
- raw aggregate metrics plus privacy-safe failure attribution;
- license/provenance and download details when applicable;
- limitations, unsupported operations, falsified claims, and kill criteria;
- exact suggested governance changes; and
- confirmation that it did not merge or push.

The coordinator records accepted results in the wave manifest only after local
reproduction. Rejected commits remain visible in their worker threads without
entering the integrated branch.

## Wave 2 ownership

Wave 2 has five independent cells:

| Cell | Scope | External code |
|---|---|---|
| Baseline ladder | Long context, profile, append log, stable observations, and file search | Forbidden |
| Lifecycle E01 | Authority, currentness, correction, forgetting, applicability, and harmful memory | Forbidden |
| Hindsight supplier | Provenance review and one optional isolated adapter cell | Official pinned source only after every gate |
| Fresh horizon | Primary-source challenge to the current experiment order | Forbidden |
| Novelty and falsification | ATC-native mechanisms, closest prior art, and decisive experiments | Forbidden |

The machine-readable manifest is authoritative for exact thread IDs, source
commit, worker settings, allowed actions, and final receipts. The
[integrated result](ATC_MEMORY_LAB_WAVE2_RESULTS_2026-07-23.md) records the
coordinator's reproduction, accepted limitations, research decision, and next
experiment order.

## Wave 3 ownership

Wave 3 applies the same governance to five new-file-only cells from immutable
coordinator commit `950f649d9e3cc106fb8ff4febbe38919f8e00d11`:

| Cell | Scope | Reasoning |
|---|---|---|
| B01 | Bounded programmatic inspection of a complete structured log | `gpt-5.6-sol` high |
| O01 | Online/off-policy/shift triangulation | `gpt-5.6-sol` medium |
| P01 | Write-admission through delayed-action poisoning | `gpt-5.6-sol` high |
| E01b | Isolated production Core lifecycle conformance | `gpt-5.6-sol` high |
| M2 | Sealed one-deletion-minimal projection and paired-vault noninterference | `gpt-5.6-sol` high |

All five workers are forbidden from editing production code, existing shared
harnesses, governance, or another cell's files. E01b may exercise only a
disposable synthetic Core; it may not connect to the operator Core or patch a
failure. No Wave 3 cell may use external code, providers, models, credentials,
or personal context. The coordinator alone may integrate and classify a
result.

A sixth medium-reasoning task performed metadata-only intake of the official
MPBench repository and the paper-linked PRO-LONG URL. It could inspect official
metadata, README, license, and tree shape but could not clone, open dataset
payload rows, execute third-party code, or contaminate the frozen P01/B01
cells.

Wave 3 is complete. The machine-readable manifest preserves its bounded B01
kill, O01 and P01 holds, E01b unsupported semantics, narrow M2 retain, and
execution-denied external intake. The
[integrated Wave 3 result](ATC_MEMORY_LAB_WAVE3_RESULTS_2026-07-23.md) records
coordinator reproduction and the resulting Evidence-Compiled Memory research
direction. No result grants production authority.
