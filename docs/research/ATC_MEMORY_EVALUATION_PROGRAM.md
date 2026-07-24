# ATC Memory Evaluation Program

## A falsifiable program for distinguishing memory from retrieval

| Field | Value |
|---|---|
| Version | 0.1 |
| Date | July 23, 2026 |
| Status | Evaluation specification; no adapter, harness, product schema, or production behavior is implemented |
| Machine-readable specification | `bench/memory_reliability_spec.json` |
| Deterministic logical fixtures | `bench/memory_reliability_fixtures.json` |
| Architecture inputs | [ATC Memory Reliability Architecture](ATC_MEMORY_RELIABILITY_ARCHITECTURE.md) and [Consequence-Closed Context](CONSEQUENCE_CLOSED_CONTEXT.md) |

The claim under test is not that ATC can find relevant text. The claim is that a
user-owned system can preserve and transform authorized experience into useful
current state, carry that state across sessions and agents, cause better
observable outcomes, and remove the future influence of corrections and purges
inside a declared boundary.

The program is deliberately capable of returning a negative result. ATC has not
solved AI memory when a long-context reader, an append-only transcript plus
search, or an individual external memory system matches its end-to-end outcomes
under the same model, context, latency, disclosure, and monetary budgets.

---

## 1. Scientific question and falsifiers

### 1.1 Primary question

> Under a fixed task, model, data, clock, context, and cost envelope, does an
> ATC memory condition improve correct authorized action and longitudinal
> continuity over the strongest simpler or external condition, without
> worsening correction, forgetting, privacy, or purge behavior?

The unit of evidence is a **memory episode**, not a retrieved passage. An episode
contains a sequence of evidence and environment events, one or more later
checkpoints, observable task outcomes, and any correction, deletion, purge, or
target-change events. All conditions receive the same logical episode.

### 1.2 Primary endpoint

The primary endpoint is **Current Authorized Outcome Success (CAOS)**. A trial
passes CAOS only when all of the following are true:

1. the final answer or action satisfies the task oracle;
2. every fact or procedure used is current at the checkpoint;
3. no unauthorized or purged item affects output, timing class, interruption,
   diagnostic, cursor, or learned state;
4. every required prerequisite or exception is respected;
5. the result is achieved within the declared context and cost budget; and
6. a correction-sensitive trial crosses no protected checkpoint from known
   stale state.

CAOS is intentionally conjunctive. A condition cannot offset a privacy failure
with a better QA score. The component outcomes are always reported separately;
CAOS is not a replacement for stage diagnostics.

### 1.3 Null hypotheses

The program starts from the following nulls:

- **N0 — retrieval equivalence:** ATC does not outperform an append-only event
  log plus exact/lexical search on CAOS.
- **N1 — context equivalence:** ATC does not outperform the best feasible
  full-authorized-history or static-profile baseline.
- **N2 — external equivalence:** ATC does not outperform the best individual
  competitor adapter that satisfies the test boundary.
- **N3 — hybrid sufficiency:** ATC-specific mechanisms do not improve the best
  practical non-ATC hybrid.
- **N4 — action disconnect:** any retrieval gain disappears at the task or
  action outcome.
- **N5 — correction disconnect:** canonical correction does not converge across
  active working state, projections, issued artifacts, and later actions.
- **N6 — closure failure:** consequence or outcome closure misses a reachable
  stale dependency under the declared mutation oracle.
- **N7 — governance cost:** gains depend on excess disclosure, excess context,
  more model effort, higher cost, or an evaluator that saw the answer.

Rejecting one null does not reject the others. A system can solve temporal
state without solving procedural learning, or recall-to-action without solving
purge.

### 1.4 Decisive falsifiers

Stop or narrow the broad “memory reliability” claim if any of these persist on
the frozen confirmatory suite:

- the strongest simple baseline is noninferior to ATC on CAOS within two
  percentage points;
- ATC improves evidence recall but not CAOS, task success, repeated-failure
  reduction, or correction convergence;
- the gain vanishes when reader, model effort, context tokens, and tool budget
  are equalized;
- an individual competitor adapter wins and can meet ATC's authority,
  correction, deletion, and purge boundary;
- correction or purge leaves an attributable reachable artifact;
- a specialized mechanism regresses ordinary cases by more than its
  preregistered noninferiority margin; or
- the result depends on public benchmark labels, prompt tuning on the holdout,
  one favorable judge, or unreported failed runs.

---

## 2. What current benchmarks contribute—and what they do not

External benchmarks are imported as task families and comparability anchors,
not as ATC's definition of success.

### 2.1 LongMemEval

[LongMemEval](https://github.com/xiaowu0162/LongMemEval) provides 500 questions
covering information extraction, multi-session reasoning, knowledge updates,
temporal reasoning, and abstention. Its cleaned release is useful for
conversation-history ingestion and current-vs-stale answer tests. ATC will
retain its question-level accuracy for comparability, but will add source-span
precision, current-state correctness, unauthorized influence, disclosure,
stage attribution, and correction replay. Answer accuracy alone can credit a
reader that guessed correctly after bad retrieval.

### 2.2 LongMemEval-V2

[LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2) pairs 451
questions with up to 500 multimodal web-agent trajectories and evaluates static
state, dynamic state, workflow knowledge, environment gotchas, and premise
awareness. Its official formulation fixes a reader and asks memory systems to
return compact evidence, while reporting accuracy and query latency. That is a
strong context-gathering test, but ATC will additionally measure whether
retrieved experience changes a later action, whether a learned procedure
transfers falsely, and whether corrected source experience is removed from
derived state. The official paper also reports that coding-agent memory
controllers can trade materially more query latency for accuracy, so latency
must be treated as a co-constraint rather than a footnote
([paper](https://arxiv.org/abs/2605.12493)).

### 2.3 MemoryAgentBench

[MemoryAgentBench](https://github.com/HUST-AI-HYZ/MemoryAgentBench) uses
incremental multi-turn inputs and evaluates accurate retrieval, test-time
learning, long-range understanding, and conflict resolution. It is useful for
incremental ingestion and update experiments. ATC will not collapse those
competencies into one average: conflict and selective forgetting receive
separate current-state, abstention, and residue metrics, and procedural
learning must improve a held-out observable task rather than reproduce a
reference string ([paper](https://arxiv.org/abs/2507.05257)).

### 2.4 MemoryArena

[MemoryArena](https://memoryarena.github.io/) evaluates multi-session,
interdependent agent tasks across web navigation, preference-constrained
planning, progressive search, and formal reasoning. Its Task Progress Score
and Task Success Rate expose a central weakness of recall-only evaluation:
partial subtask success may not produce a globally consistent outcome. ATC
will retain progress and success for comparability, but add prerequisite
coverage, false transfer, current-state use, intervention ablations, and
correction/closure faults
([paper](https://arxiv.org/abs/2602.16313)).

### 2.5 Adoption rule

For every imported benchmark:

1. pin the upstream revision, data digest, license, and official evaluator;
2. preserve the official score unchanged;
3. add ATC stage receipts without modifying source data;
4. use a public development slice and a separately frozen confirmatory slice;
5. disclose any excluded examples before running outcomes;
6. compare identical reader and controller models where the benchmark permits;
7. report unsupported adapter operations instead of silently emulating them;
8. never train an extractor, compiler, or judge on confirmatory labels; and
9. keep synthetic ATC governance fixtures separate from official leaderboards.

---

## 3. Capability hypotheses

Each capability has a success claim and a disconfirming observation.

| Capability | Falsifiable claim | Disconfirming observation |
|---|---|---|
| Working | A versioned checkpoint resumes the correct open state after compaction, restart, or model/client change | Raw recent turns or a static task note is noninferior, or stale dependencies survive resume |
| Episodic | Specific attempts, environment states, feedback, and outcomes improve a later matched task | A semantic summary performs equally, or the system confuses an attempted action with a successful event |
| Semantic | Current facts and preferences are more accurate than evidence-only or transcript baselines | Facts are merely retrieved passages, unsupported inferences become truth, or updates fail |
| Procedural | Derived tactics reduce repeat failures on held-out tasks with matching preconditions | Gains are task leakage, hidden reasoning retention, or false transfer to a mismatched task |
| Relational | Explicit exception, prerequisite, conflict, and support structure improves sufficient-set use | More tokens or independent ranking matches it, or relation edges bypass current canonical state |
| Temporal | Current and `as_of` answers respect event-valid and known-at time across corrections | Latest-text heuristics are noninferior, or expired/superseded facts resurrect |
| Correction | One correction converges across canonical, working, derived, and issued state | Database state changes but any later conforming checkpoint uses the old value |
| Forgetting | Eviction, rank decay, retirement, deletion, and purge have observably distinct semantics | One “forget” path conflates operations or a deleted/purged item reappears |
| Privacy | Minimum authorized context improves outcomes without unrelated or unauthorized influence | Quality relies on full-history disclosure, timing leakage, or adapter caches crossing principals |
| Cross-agent portability | A logical checkpoint rehydrates across supported clients/models without provider-specific prose becoming authority | A target-specific hidden state is required, or semantics drift without an explicit downgrade |
| Recall-to-action | A recalled item changes the correct observable action, not merely the prompt | Retrieval succeeds but action, plan, or answer remains wrong |
| Consequence closure | A correction before token consumption prevents a later protected checkpoint from using stale memory | Any revoked, expired, replayed, mutated, or stale token crosses the declared host boundary |
| Outcome closure | Corrected or purged private inputs lose reachable influence in local derived/learned artifacts | A dependency is missing, rebuild is nondeterministic, or attributable residue remains |

The deterministic fixtures cover every capability at least twice. External
benchmark mappings then add scale and stochastic model behavior.

---

## 4. Stage model and failure attribution

Every trial emits stage outcomes. A final wrong answer without attribution is
not an actionable memory result.

```text
episode presentation
  -> capture
  -> canonicalize
  -> consolidate/project
  -> retrieve
  -> compile working context
  -> read/reason
  -> act at checkpoint
  -> verify outcome
  -> correct/delete/purge
  -> invalidate/rebuild
```

The first failing stage is the primary attribution; later consequential
failures remain secondary labels.

### 4.1 Failure taxonomy

| Code | Class | Example |
|---|---|---|
| `CAPTURE_MISS` | Capture | Durable user statement never becomes an eligible observation |
| `CAPTURE_FALSE_WRITE` | Capture | Incidental or assistant-authored text is stored as user truth |
| `WITNESS_COLLAPSE` | Authority | Imported/client-asserted text gains attested or confirmed force |
| `CANONICAL_WRONG_CURRENT` | Canonicalization | Superseded fact remains current |
| `CANONICAL_FALSE_INFERENCE` | Canonicalization | Derived summary silently becomes canonical truth |
| `EPISODE_OUTCOME_CONFUSION` | Episodic | Attempted action is remembered as completed |
| `TEMPORAL_BOUNDARY` | Temporal | Half-open interval, `as_of`, or known-at boundary is wrong |
| `RELATION_CLOSURE_MISS` | Relational | Required exception, conflict, or prerequisite is absent |
| `RETRIEVAL_MISS` | Retrieval | Eligible required evidence is not selected |
| `RETRIEVAL_STALE` | Retrieval | Stale, deleted, or superseded evidence is selected as current |
| `SET_INSUFFICIENT` | Compilation | Individually relevant items omit a necessary coalition member |
| `EXCESS_DISCLOSURE` | Privacy | Correct outcome includes unnecessary fields or records |
| `UNAUTHORIZED_INFLUENCE` | Privacy | Forbidden data changes any observable channel |
| `WORKING_STATE_DRIFT` | Working | Resume loses an open commitment or keeps a corrected one |
| `PORTABILITY_SEMANTIC_DRIFT` | Portability | Rehydration changes the logical obligation across targets |
| `READER_MISUSE` | Reader | Correct sufficient memory is present but interpreted incorrectly |
| `ACTION_NONUSE` | Recall-to-action | Correct memory is read but the action violates it |
| `PROCEDURE_FALSE_TRANSFER` | Procedural | A tactic is applied outside its preconditions |
| `SELF_REINFORCEMENT` | Procedural | Agent self-evaluation promotes a failed tactic |
| `CORRECTION_NONCONVERGENCE` | Correction | Any supported future surface retains old current state |
| `FORGETTING_SEMANTIC_COLLAPSE` | Forgetting | Eviction, decay, deletion, and purge act identically |
| `STALE_CHECKPOINT_ESCAPE` | Consequence closure | Protected transition crosses from stale capsule/token state |
| `DEPENDENCY_OMISSION` | Outcome closure | Derived artifact lacks a contributing dependency |
| `PURGE_RESIDUE` | Outcome closure/privacy | Attributable private artifact remains reachable after purge |
| `EVALUATOR_ERROR` | Evaluation | Deterministic oracle or independent labels disagree with judge |
| `BUDGET_ESCAPE` | Efficiency | Condition exceeds a fixed token, call, latency, storage, or cost cap |
| `CONTAMINATION` | Validity | Labels, canaries, holdout tasks, or prior outputs leak into a condition |

Failures are counted per opportunity and per episode. Reports include a
confusion matrix for stage attribution and preserve raw structured receipts for
audit, without raw personal text.

---

## 5. Comparison matrix

No result is promotable unless all applicable simple, individual competitor,
hybrid, and ablation conditions were attempted or explicitly reported
`unsupported`.

### 5.1 Simple baselines

| ID | Condition | Purpose |
|---|---|---|
| `simple_no_memory` | Reader/agent receives only the current task | Detect pretraining and task leakage |
| `simple_long_context` | Full authorized history within the same context cap, with deterministic truncation | Test whether selective memory is necessary |
| `simple_static_profile` | Frozen compact user/project/task profile | Test whether updates and lifecycle machinery add value |
| `simple_append_log_search` | Append-only logical event log plus exact and lexical search | Strong retrieval-without-memory-state control |
| `simple_atc_retrieval_v3` | Current ATC authorized retrieval and set compilation | Connect to the existing deterministic baseline |

`simple_long_context` is marked infeasible rather than truncated differently
when the fixed context limit cannot contain the history. The truncation policy
is frozen before evaluation and reported.

### 5.2 Individual competitor adapters

Each external system is a separate condition:

- `competitor_mem0`
- `competitor_graphiti`
- `competitor_hindsight`
- `competitor_letta`
- `competitor_langmem`

An adapter may use only the operations its system genuinely supports. ATC
governance must not be wrapped around an external system in the individual
cell, because that would obscure the competitor's correction, privacy, and
forgetting behavior. Safety boundaries still prevent real data egress and
real effects. Unsupported operations are scored as unsupported capabilities,
not silently implemented by ATC.

### 5.3 Hybrids

- `hybrid_best_non_atc` combines only mechanisms that win development
  tournaments, with no ATC-specific closure or authority mechanism.
- `hybrid_atc_governed` adds Core authorization, temporal resolution, current
  canonical reread, dependency manifests, and existing deterministic set
  closure to the best non-ATC components.

The hybrid recipe is frozen before confirmatory runs. Selecting a different
recipe for each test example is prohibited.

### 5.4 ATC research ablations

Add one mechanism at a time and then a preregistered full condition:

- working checkpoints;
- episodic outcome records;
- temporal and relational projections;
- procedure distillation and retrieval;
- typed event activation;
- consequence contracts and checkpoint tokens;
- outcome dependency closure; and
- full ATC research stack.

The decisive comparison for each mechanism is the immediately simpler winning
condition, not `simple_no_memory`.

---

## 6. Adapter-neutral experimental boundary

This work does not define or implement the future adapter ABI. It defines the
logical data that a harness must translate.

An eventual adapter must expose receipts for these conceptual phases:

```text
reset(run_identity, principal_identity, frozen_clock)
present(logical_event)
checkpoint(checkpoint_descriptor, budget) -> candidate context or action input
observe(outcome_event)
correct(logical_correction)
forget(forgetting_operation)
export_state() -> opaque state plus declared inventory
import_state(opaque state, target descriptor)
inventory_dependencies() -> artifact and dependency manifest
close()
```

The ABI may use different method names. The invariant is that the harness owns
the episode order, principal, clock, budgets, faults, and outcome oracle.
Adapters do not see gold labels, forbidden sets, promotion thresholds, future
events, or another condition's outputs.

Every adapter declaration must include:

- exact version and source revision;
- network and provider calls;
- model names and parameters;
- cache and persistence locations;
- supported logical operations;
- reset and cleanup behavior;
- data-egress classification;
- whether correction mutates, appends, or rebuilds;
- whether purge is physically testable inside the declared boundary; and
- any emulation performed by the common harness.

Common harness emulation is reported separately and cannot earn capability
credit for the adapter.

---

## 7. Deterministic local fixtures

`bench/memory_reliability_fixtures.json` is a symbolic, sanitized corpus. Values
such as `TOKEN_COLOR_COBALT` are opaque labels, not realistic personal text.
Imported instruction-shaped content is represented as a token and never
interpreted.

Each scenario contains:

- monotonically ordered logical events on a frozen clock;
- source/witness class and principal;
- one or more checkpoints;
- required and forbidden state or action labels;
- expected invalidations and residue;
- explicit capability tags; and
- deterministic faults where applicable.

The fixtures are **specification inputs**, not an executable benchmark harness.
Their tests prove structural coverage and freeze key oracles. They do not claim
that ATC or any competitor passes.

The local suite has four roles:

1. eliminate unsafe or semantically incoherent designs before paid model calls;
2. validate the future adapter translation contract;
3. provide exact oracles for correction, forgetting, tokens, and lineage; and
4. reproduce stage failures without LLM variance.

Passing the local suite is necessary and insufficient for promotion.

---

## 8. Experiment families

### E01 — State, authority, correction, and forgetting

Run every simple baseline, each individual competitor, both hybrids, and the
governed ATC condition on symbolic semantic, temporal, privacy, correction,
forgetting, and purge scenarios. Primary endpoint: exact current authorized
state and zero forbidden influence. No model is required. This experiment
quickly eliminates systems that are only append-and-search.

### E02 — Working continuity and cross-agent portability

Resume an interrupted task after compaction, process restart, model change, and
client change. Inject a correction before one resume. Primary endpoint:
checkpoint-state exact match plus correct next action. Compare raw recent
turns, static task notes, append-log search, Letta, LangMem, hybrids, and ATC
working checkpoints.

### E03 — Episodic and procedural learning

Present failed and successful attempts with observable environment feedback.
Test a held-out task with matched or mismatched preconditions. Primary endpoint:
repeated-failure reduction at no increase in false procedure transfer. Compare
raw trajectories, summaries, individual procedural-memory competitors, the
best hybrid, and ATC episodic/procedure mechanisms.

### E04 — Relational, temporal, and recall-to-action

Use prerequisite, exception, conflict, update, and semantic-disconnect cases.
Measure sufficient-set recall, CAOS, and the difference between retrieval
success and action success. Compare independent rankers, deterministic closure,
temporal/graph competitors, the best hybrid, and ATC projections. The experiment
fails if gains come only from more selected tokens.

### E05 — Consequence and outcome closure

Exhaustively enumerate local state-machine faults for corrections before and
after token preparation/consumption, target drift, disconnect/reconnect,
mutation, replay, and purge. Primary endpoints: zero stale protected-checkpoint
escapes against the mutation oracle and zero reachable attributable artifacts
after required rebuild. This is a protocol experiment, not a stochastic model
compliance experiment.

### E06 — LongMemEval cleaned

Run the pinned official evaluator plus ATC stage metrics. Primary confirmatory
contrast: `hybrid_atc_governed` against the best simple or individual competitor
condition under the same reader and context cap. Knowledge-update and
abstention subsets are reported separately.

### E07 — MemoryAgentBench incremental competencies

Preserve official per-competency results and add current-state, false-write,
false-forgetting, and stage-attribution metrics. Do not average away a conflict
resolution failure.

### E08 — LongMemEval-V2 context gathering

Use the official fixed reader and report official accuracy/latency. Add evidence
sufficiency, workflow-to-action transfer, disclosure, and cost. Coding-agent
memory controllers receive a separate tool-call and latency budget stratum
rather than being compared as if they were one vector query.

### E09 — MemoryArena agentic outcomes

Preserve official task progress and task success. Add CAOS, prerequisite
coverage, current-state use, false transfer, and intervention ablations.
Environment snapshots and random seeds are paired across conditions.

### E10 — Multi-target consequence behavior

Only after deterministic closure passes, test low-risk observable obligations
on a fixed roster of model builds and target placements. Compare identical
text, best static template, sequential compilation, and joint compilation.
Hard effects remain synthetic host predicates.

### E11 — Opt-in longitudinal pilot

Only after all safety gates pass, measure user-reported usefulness, correction
trust, repeated-restatement reduction, and burden on explicitly consented local
data. This stage estimates product value; it is not used to tune hidden
benchmark holdouts.

---

## 9. Metrics and budgets

### 9.1 Stage metrics

Report at least:

- capture precision/recall, false-write rate, witness accuracy, and source-span
  completeness;
- current-state accuracy, temporal accuracy, correction convergence, conflict
  accuracy, and abstention;
- evidence Recall@k, MRR/nDCG for comparability, set sufficiency, prerequisite
  and exception recall, stale inclusion, contradiction, and redundancy;
- working resume accuracy and portability semantic parity;
- repeated-failure reduction, false procedure transfer, and task success;
- recall-to-action conversion:
  `correct_action_given_sufficient_memory / sufficient_memory_trials`;
- consequence activation, false activation, token rejection, stale checkpoint
  escape, and rebase convergence;
- dependency completeness, rebuild determinism, purge residue, and orphaned
  artifact count;
- per-request and cumulative fields/tokens disclosed;
- model calls, tool calls, input/output tokens, storage bytes, and monetary
  cost; and
- local and end-to-end p50/p95/p99 latency.

### 9.2 Fixed local budgets

The machine-readable specification defines initial budgets. They are gates on a
declared reference profile, not universal hardware claims:

| Operation | Initial budget |
|---|---:|
| Deterministic local ingest p95 | 25 ms/event at 10,000 logical objects |
| Authorized query + compile p95 | 150 ms at 10,000 objects |
| Working checkpoint export/import p95 | 100 ms each |
| Local correction-to-invalidation p95 | 250 ms for fan-out up to 1,000 artifacts |
| Protected token consume p99 | 100 ms |
| Deterministic rebuild | 30 s for 10,000 objects and 100,000 dependency edges |
| Ordinary compiled context | 2,048 tokens maximum |
| Specialized agentic context | 8,192 tokens maximum unless an upstream benchmark fixes another limit |

Every report records CPU, memory, OS, Python, filesystem, cold/warm definition,
concurrency, and background load. Provider latency is reported separately from
local memory latency.

### 9.3 Cost rules

- identical reader/controller model and reasoning effort within a contrast;
- identical maximum calls, tool steps, and context tokens unless the experiment
  is explicitly a Pareto-frontier study;
- monetary cost calculated from a frozen price sheet and also reported as raw
  token/call counts;
- development tuning cost reported separately from evaluation cost;
- failed and retried calls count;
- cache hits and precomputation are disclosed and symmetrically available; and
- a promoted condition must either cost no more than the strongest simpler
  baseline or improve CAOS by at least five points while remaining within a
  preregistered 25% end-to-end cost premium.

No quality score can compensate for a hard safety failure.

---

## 10. Contamination and validity controls

### 10.1 Data isolation

- Freeze public development, private confirmatory, and fault-only partitions by
  content digest.
- Use opaque symbolic values and per-run canaries in local fixtures.
- Do not place answer labels, forbidden IDs, future events, or promotion gates
  in adapter-visible inputs.
- Store benchmark data outside adapter search roots except for the presented
  episode.
- Reset adapter state, caches, namespaces, files, and provider threads between
  principals and conditions.
- Test cross-principal canary absence after reset.

### 10.2 Prompt and instruction contamination

- Treat all imported strings as data.
- Include instruction-shaped imports that request rule changes, label
  disclosure, and budget changes.
- Keep policy and budget configuration outside the imported content channel.
- Record whether any imported token affects configuration or hard force.

### 10.3 Model and evaluator contamination

- Record model build, cutoff disclosure when available, prompt digest, and
  parameters.
- Use a no-memory condition to estimate pretraining/task leakage.
- Prefer programmatic or environment oracles.
- For soft judgments, use two independently prompted judges plus a blinded human
  adjudication sample.
- Validate judge precision, recall, macro-F1, Brier score, and calibration on a
  separately labeled set.
- A judge never evaluates its own generated rationale and never sees the
  condition name.

### 10.4 Researcher degrees of freedom

Pre-register:

- primary endpoint and contrasts;
- scenario and model roster;
- exclusions;
- truncation and timeout policy;
- random seeds and repetitions;
- noninferiority margins;
- multiplicity family;
- missing/failed-call treatment;
- cost sheet;
- stop/go thresholds; and
- exact commit and fixture digests.

All attempted runs, including crashes and unsafe outputs, remain in the
aggregate report.

---

## 11. Statistical plan

### 11.1 Deterministic suite

Deterministic fixtures use exact equality and exhaustive fault enumeration
where the finite state space permits it. Safety gates report opportunities,
observed failures, and exact one-sided 95% Clopper-Pearson upper bounds. Zero
observed failures is not described as proof of zero risk.

Property/fault runs use fixed seed lists checked into the eventual preregistration.
Shrunk counterexamples retain the original seed and mutation lineage.

### 11.2 Stochastic model and agent suite

- Use paired episodes, environment snapshots, and seeds across conditions.
- Treat episode, not question or turn, as the clustering unit.
- Run at least three independent generation seeds per episode for development;
  the confirmatory sample size comes from simulation using the smallest effect
  worth detecting, familywise alpha `0.05`, and at least `0.80` power (`0.90`
  for the primary CAOS contrast when feasible).
- Primary binary CAOS contrasts use paired cluster bootstrap confidence
  intervals and a paired randomization test; McNemar's test is a sensitivity
  analysis for one-run paired binary outcomes.
- Continuous cost, disclosure, latency, and progress contrasts use paired
  bootstrap intervals and report median plus tail quantiles.
- Repeated multi-session tasks use a mixed-effects model with condition fixed
  effects and episode/model random intercepts as a secondary analysis.
- Report absolute risk difference, relative change, number needed to improve,
  and raw numerator/denominator—not only p-values.
- Control the confirmatory family with Holm's method. Capability-specific
  exploratory analyses are labeled exploratory.

### 11.3 Promotion inference

A mechanism promotes only when:

1. the 95% interval clears its absolute safety/quality floor;
2. the paired interval clears the preregistered improvement or noninferiority
   margin versus the strongest simpler eligible condition;
3. the result holds on at least two task families, one of which is
   action-bearing for recall-to-action claims;
4. no model build contributes more than 70% of the total gain;
5. the cost/disclosure condition passes; and
6. the result survives removal of evaluator-disagreement cases.

Benchmark averages are never the sole promotion statistic.

---

## 12. Promotion and stop/go gates

### 12.1 Universal safety gates

Required before any live promotion:

- zero observed unauthorized influence in the declared randomized boundary;
- zero observed imported/inferred escalation to hard force;
- zero observed expired, revoked, replayed, mutated, or stale token acceptance;
- zero missed affected artifacts against the deterministic dependency oracle;
- no decryptable attributable residue after the declared purge procedure;
- complete provenance and dependency manifests;
- deterministic fallback for every learned component; and
- no raw personal context in operational logs or benchmark reports.

### 12.2 Capability gates

| Capability | Initial promotion gate |
|---|---|
| Working/portability | At least 95% exact resume and at least 10-point correct-next-action gain over the best simple baseline; stale resume below 1% |
| Semantic/temporal | At least 98% deterministic current-state accuracy and noninferior ordinary QA within 1 point |
| Episodic/procedural | At least 20% relative repeated-failure reduction; false transfer no more than 1 point above the strongest baseline |
| Relational | At least 8-point sufficient-set gain on multi-record cases; ordinary recall noninferior within 1 point |
| Recall-to-action | At least 5-point CAOS gain and at least 10% relative reduction in the retrieval-to-action gap |
| Correction | 100% deterministic convergence in local fixtures and at least 99% supported-surface convergence in stochastic tests |
| Forgetting/privacy | Zero hard-boundary failures; lower confidence bound on minimum-disclosure improvement above zero |
| Consequence closure | Zero stale protected-checkpoint escapes against the declared fault oracle |
| Outcome closure | Zero missing dependencies and zero reachable attributable artifacts after rebuild/purge |

These thresholds are starting hypotheses. They must be frozen before running a
confirmatory set and revised only for future preregistered rounds.

### 12.3 Decision states

- **GO:** universal gates pass and the capability clears both absolute and
  relative promotion gates.
- **HOLD:** safety passes but power, cross-task replication, cost, or effect
  size is insufficient. Gather the preregistered additional evidence only.
- **NARROW:** a mechanism works for a bounded capability but the broader memory
  claim fails. Ship or study only that capability.
- **ADOPT COMPETITOR:** an individual adapter wins, passes authority/lifecycle
  requirements, and can be packaged within product constraints.
- **KILL MECHANISM:** the strongest simpler baseline is noninferior, gains are
  budget-driven, or the mechanism causes a hard-boundary failure.
- **STOP PROGRAM CLAIM:** correction, privacy, consequence closure, or outcome
  closure remains unsound after two independently reviewed redesigns.

---

## 13. Experiment execution order

Wave 2 completed the retrieval-only baseline ladder and a bounded 6-of-18 E01
reference slice. It did not execute production Core semantics or an external
system. The simple stable current-state log advanced, each of the four
governance-rule ablations regressed, and the Hindsight supplier cell was
skipped at its dependency/egress gate. The exact results and validity limits
are in
[ATC Memory Lab Wave 2 Integrated Results](ATC_MEMORY_LAB_WAVE2_RESULTS_2026-07-23.md).

Do not infer the rest of E01 from that reference result. Insert the following
preregistered gates before full model-backed capability evaluation:

1. **B01 — Lossless-log inspection.** Compare the stable current-state lexical
   control with a restricted programmatic reader over a complete structured
   log, current ATC, and a frozen combination under identical context and
   action budgets.
2. **O01 — Online/off-policy/shift triangulation.** Compare rankings under
   offline retrieval, online formation/utilization, and a frozen distribution
   shift with measured recovery.
3. **P01 — Admission and delayed-activation poisoning.** Separate poisoned
   durable-write, later-retrieval, later-influence, and protected-action rates
   across each supported write channel.
4. **E01b — Production-semantics conformance.** Run authority, currentness,
   correction, epistemic role, applicability, forgetting, and purge scenarios
   against an isolated current Core-shaped implementation without exposing
   oracle labels.

After those gates:

5. **E02 — Working continuity and cross-agent portability.** Compare raw recent
   turns, static task note, append-log search, hybrids, and ATC checkpoints
   across compaction, restart, target/capability change, correction, poisoned
   import, and multi-hop re-entry. External SDK conditions enter only after
   their own supplier gates.
6. **E03 — Episodic and procedural learning.** Measure repeated-failure
   reduction, poisoned feedback, repair, quarantine, and false transfer on
   held-out matched/mismatched tasks using only observable trajectory and
   outcome evidence.
7. **E04 — Relational, temporal, and recall-to-action.** Quantify the gap between
   sufficient retrieval and correct action under prerequisites, exceptions,
   conflicts, updates, and semantic disconnect.
8. **E05 — Consequence and outcome closure.** Exhaustively fault token,
   correction, drift, disconnect, dependency, rebuild, and purge state before
   any stochastic multi-target consequence trial.

Only after these establish coherent local semantics should the program run
E06–E10 on official or paid model-backed benchmarks.

---

## 14. Deliverables and evidence policy

The next implementation phase should produce, in order:

1. a versioned adapter ABI derived from—not embedded in—this logical
   specification;
2. deterministic adapter conformance tests;
3. baseline adapters and immutable environment manifests;
4. a preregistration containing exact data/model/configuration digests;
5. raw structured per-stage receipts;
6. a reproducible aggregate report with all attempted runs; and
7. a signed promotion decision that cites the exact gates cleared.

This document, its JSON specification, and its fixture tests do not constitute
benchmark evidence. They establish what must be measured and what results would
cause ATC to stop claiming that it is solving memory rather than retrieval.
