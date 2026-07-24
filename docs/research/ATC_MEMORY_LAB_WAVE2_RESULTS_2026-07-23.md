# ATC Memory Lab Wave 2 integrated results

## Simple memory won the first retrieval fixture; governed lifecycle rules won the first reliability slice

| Field | Value |
|---|---|
| Date | July 23, 2026 |
| Coordinator branch | `codex/memory-lab-wave2` |
| Immutable worker base | `2bc0ad66c019511e9e0320daeec1a45aedc280b6` |
| Worker model | Five fresh visible `gpt-5.6-sol` worktree tasks, high or medium reasoning |
| Authority | Coordinator-only integration; Core remains the sole canonical authority |
| Data | Frozen synthetic fixtures only; no operator Core or personal context |
| Status | Wave complete with two coordinator-reproduced synthetic results, two research specifications, and one honestly skipped supplier cell |

## Integrated decision

Wave 2 does not establish that ATC has solved AI memory. It establishes a
sharper and more demanding route:

> Start from a lossless, current-state-resolved event log; prove authority,
> applicability, correction, purge, and outcome behavior longitudinally; then
> add programmatic inspection, external systems, learned representations, and
> portable working state only when each layer beats the simpler control.

The strongest simple condition beat current ATC Retrieval V3 on the tiny
retrieval fixture. That is a useful challenge to the architecture, not a
production replacement decision. The strongest governed reference condition
then beat append-only memory on the first lifecycle slice. Together, the
results say that **simple storage and search can be enough for recall, while
governed state transitions are necessary for reliable memory use**.

## 1. What actually ran

| Cell | Integrated artifact | Evidence | Outcome |
|---|---|---|---|
| Baseline ladder | [Wave 2 baseline report](../../bench/reports/memory_lab_baseline_ladder_wave2.md) | `L2`, coordinator reproduced | Stable current-state log advances to harder fixtures |
| Lifecycle E01 | [E01 JSON report](../../bench/reports/memory_reliability_e01_wave2.json) | `L2`, coordinator reproduced | Governed in-memory reference passes 6/6; every rule ablation regresses |
| Hindsight supplier | [Execution receipt](../../research/memory-lab/hindsight/experiment-receipt.v1.json) | `L2` for the adapter contract and skip receipt; no `L3` supplier result | Supplier execution skipped at dependency/egress gate |
| Fresh horizon | [Wave 2 horizon report](ATC_MEMORY_HORIZON_WAVE_2_2026-07-23.md) | `L0`, primary-source research | Reorders the next experiments |
| Novelty/falsification | [Wave 2 novelty report](ATC_WAVE2_NOVELTY_AND_FALSIFICATION_2026-07-23.md) | `L0`, primary-source research | Narrows novelty and specifies six falsifiable mechanisms |

`L2` means a deterministic synthetic result reproduced by the coordinator on
the integrated branch. It is not cross-platform evidence, stochastic model
evidence, a real-user result, or product acceptance.

## 2. Baseline ladder result

The ladder used the unchanged M0 fixture:
`5601692ea305448f6b299c32725a93c73ca83ccee66f325e22cbcbedfa0cc68f`.
Wave 2 controls are separately frozen at
`6dbf75db008b1be2d3db643b8dd19fe45f1a45c88121ac1ac3af16a0a0cd3c98`.
Every condition ran 20 deterministic repeats over seven objects and five
tasks under the same 260-character context cap.

| Condition | Success | Evidence-group recall | Precision | Forbidden outputs | Disposition |
|---|---:|---:|---:|---:|---|
| No memory | 0.20 | 0.20 | 0.00 | 0 | Retain as negative control |
| Fixed-budget history | 0.40 | 0.80 | 0.40 | 1 | Not earned on this fixture |
| Static profile | 0.60 | 0.80 | 0.40 | 1 | Not earned on this fixture |
| Raw append-log search | 0.80 | 1.00 | 0.70 | 1 | Not earned on this fixture |
| Stable current-state log | **1.00** | **1.00** | **0.80** | **0** | **Advance to next fixture** |
| Bounded local file search | 0.80 | 1.00 | 0.70 | 1 | Not earned on this fixture |
| Current ATC Retrieval V3 | 0.80 | 0.90 | 0.80 | 0 | Not earned against the strongest lower rung on this fixture |

All conditions had zero model calls, provider tokens, monetary cost, context
budget violations, and adapter-contract violations. All rankings were stable
across repeats.

The result has three important limits:

1. The stable current-state condition is a small deterministic reference, not
   an accepted implementation. Its rules and fixture may be aligned.
2. The file-search condition is a bounded one-shot infrastructure control. It
   does not reproduce the action model and programmatic log inspection studied
   by [PRO-LONG](https://arxiv.org/abs/2607.20064).
3. This is retrieval-stage evidence only. It contains no answer model, online
   memory formation, distribution shift, poisoned write, protected action, or
   CAOS endpoint.

The next question is therefore not whether to replace ATC with this simple
reference. It is whether the stable-log advantage survives mutation,
poisoning, scale, action grounding, and outcome evaluation. If it does, more
complex retrieval and memory representations must show a separate benefit.

## 3. Lifecycle E01 result

E01 ran six opaque-symbol episodes twice in the worker and 20 times on the
integrated branch. The episodes cover authority, correction/currentness,
epistemic role, cross-project applicability, forgetting semantics, and purge
residue. Adapters saw ordered events and task descriptors but never oracle
labels.

| Condition | Exact successes | Rate | Forbidden influence | Harmful non-abstention | Purge residue |
|---|---:|---:|---:|---:|---:|
| No memory | 1/6 | 0.166667 | 0 | 0 | 0 |
| Append-only log | 0/6 | 0.000000 | 8 | 1 | 1 |
| Governed reference | **6/6** | **1.000000** | **0** | **0** | **0** |
| Without authority | 5/6 | 0.833333 | 1 | 0 | 0 |
| Without currentness/invalidation | 4/6 | 0.666667 | 2 | 0 | 0 |
| Without applicability | 3/6 | 0.500000 | 4 | 1 | 0 |
| Without purge closure | 5/6 | 0.833333 | 0 | 0 | 1 |

This is evidence that the four rule families are independently necessary in
the frozen slice. It is not evidence that production Core already implements
the reference semantics. The fixture, oracle, and reference rules were
co-designed; only 6 of the full specification's 18 scenarios ran; no external
system, answer model, wall-clock latency, or real action was exercised.

Coordinator review also strengthened the reference before acceptance:

- control operations now require the active principal and an authoritative
  source;
- irreversible purge is terminal against restore and same-ID recreation; and
- direct tests cover foreign/untrusted delete, restore, retirement, and purge.

The next E01 cell must execute equivalent observations against an isolated
production Core-shaped store and add write-admission poisoning. Until then the
result is a conformance target, not a claim about current ATC.

## 4. Hindsight supplier result

The supplier cell reviewed only the official
[Hindsight repository](https://github.com/vectorize-io/hindsight/tree/fa69b5b73b3b50bf5dcbae5bccbc7197de03692f)
at commit `fa69b5b73b3b50bf5dcbae5bccbc7197de03692f`. The root and nested
repository licenses reviewed were MIT. The disposable source clone was removed
after the static audit and no vendor bytes were committed.

Execution was intentionally skipped:

- the client-only closure was 19 packages but required a separately running
  service;
- reviewed Windows/Python 3.12 service closures contained 195 to 212 packages;
- exact locked versions included affected `click`, `jaraco-context`,
  `setuptools`, `torch`, and `diskcache` packages at the review cutoff;
- defaults and optional paths included a non-loopback listener, provider and
  telemetry surfaces, raw-document/trace storage, parent-directory `.env`
  loading, and unpinned model revisions; and
- the genuine no-LLM mode still needed an embedding provider, while reviewed
  local paths required separately pinned model/native artifacts.

No upstream script, package, model, container, database service, provider,
credential, Docker cell, or supplier benchmark was run. The checked-in adapter
uses an injected client and a fake in tests. It validates source/API/model
declarations, rejects reported model usage, returns ATC IDs only, and deletes
its isolated bank, but it cannot enforce transport behavior. A future real
cell requires loopback-only binding, immutable local model artifacts, and an
external default-deny egress boundary.

This is a successful governance outcome: `not_executed` remains the result,
and no fake Hindsight score appears in the comparison.

## 5. Research changes to ATC's direction

The fresh horizon and novelty reviews materially change the next build order:

1. **Lossless log before framework.** Treat the stable current-state event log
   as the strongest simple control. Add read-only programmatic inspection of a
   complete structured log as a distinct condition; do not relabel deterministic
   file ranking as programmatic memory.
2. **Online and shifted evaluation before winner selection.** Static retrieval
   can select the wrong memory policy. Measure write, read, utilization, and
   recovery after distribution or environment changes.
3. **Poisoning at admission and delayed action.** Measure poisoned durable
   writes, later retrieval, later influence, and protected action separately.
   Retrieval-time sycophancy tests are insufficient.
4. **Raw fidelity and localized maintenance before lossy consolidation.**
   Summaries, graphs, and global maintenance must beat faithful event storage
   and local repair.
5. **Portability is an integrity boundary.** Imported working state is
   untrusted, capability-attenuated data with provenance, revocation, and
   multi-hop re-entry tests.
6. **External frameworks come after the simple winner clears safety fixtures.**
   Supplier value still matters, but dependency burden must not force an unsafe
   or irreproducible run.

The research scan also narrows ATC's novelty. Selective proactive memory
intervention is prior art in
[Remember When It Matters](https://arxiv.org/abs/2607.08716), and
barrier-first repair is already occupied by MEMOREPAIR. ATC should not claim
either primitive as new.

The three mechanisms worth immediate ATC-native testing are:

- **M2 — Sealed Projection Minimal Compiler:** seal the authorized and
  applicable candidate set before relevance, compile a
  one-deletion-minimal context, and require paired-vault noninterference across
  output, reason codes, cursors, timing classes, and learning updates.
- **M3 — Record-Influence Barrier Closure:** use a single Core-owned influence
  contract across correction, reversible deletion, purge, permission changes,
  issued context, outcome statistics, and working state. This is important
  safety engineering with low standalone novelty confidence.
- **M6 — Portable Working-State Three-Way Repair:** compare acknowledged base
  `B`, local delta `L`, and current Core state `C`, then emit typed
  carry/refresh/recompute/drop/conflict/compensate/stop decisions without
  pretending hidden model state is portable.

## 6. Wave 3 preregistration order

| Order | Experiment | Decisive question | Minimum kill or hold rule |
|---:|---|---|---|
| 1 | `B01` lossless-log inspection | Does a restricted programmatic reader over the complete structured log beat stable lexical current-state search on CAOS or context cost? | Kill added machinery if it does not improve CAOS or reduce cost on held-out tasks |
| 2 | `O01` online/off-policy/shift triangulation | Does the baseline ranking survive online formation and a frozen distribution shift? | Do not select a winner if rankings change materially or recovery is unstable |
| 3 | `P01` admission and delayed-activation poisoning | Which write channels create durable poison, and can poison reach retrieval or protected action? | Disable automatic durability or the affected channel on any hard-force escalation or protected action |
| 4 | `E01b` production-semantics conformance | Does an isolated current Core satisfy authority, currentness, applicability, correction, and purge scenarios without oracle-aware rules? | Hold the architecture claim on any unauthorized, stale, inapplicable, or purged influence |
| 5 | `M2` sealed minimal projection | Can ATC reduce context while preserving sufficiency and paired-vault noninterference? | Kill generalized minimal compilation if token savings do not improve CAOS or any sealed-field channel leaks |
| 6 | `M3` influence barrier | Can correction or purge close every known derived influence without indiscriminate rebuild? | Keep the safety requirement; kill optimized repair if full rebuild is safer or cheaper |
| 7 | `M6` three-way working repair | Does typed repair outperform a static task note and clean restart across correction and capability change? | Kill general portability if simpler controls are noninferior or stale resume exceeds the frozen limit |
| 8 | External supplier cell | Can a pinned supplier run inside the same privacy, budget, authority, and lifecycle contract? | Preserve `skipped` or `failed` when provenance, isolation, dependency, or hard memory gates fail |

Each experiment must keep the no-memory, stable current-state, and current ATC
controls; use frozen inputs and budgets; emit stage-level reason codes; and
report negative, skipped, and killed cells alongside positive results.

## 7. What Wave 2 accepts and rejects

Accepted:

- visible worktree governance with one coordinator works as a repeatable
  research process;
- stable current-state event storage is the strongest simple retrieval control
  and advances;
- authority, currentness/invalidation, applicability, and purge closure each
  remain required hypotheses;
- supplier adapters are optional and must survive their execution gate; and
- M2, M3, and M6 are the next ATC-native falsification targets.

Not accepted:

- a production stable-log replacement;
- a production lifecycle implementation from the in-memory reference;
- any Hindsight quality result or dependency;
- generic selective reminder or barrier-first repair novelty;
- graph, embedding, consolidation, procedural, checkpoint, or neural-memory
  promotion; or
- a claim that ATC has solved AI memory.

Wave 2 makes the program harder to fool. That is its result.
