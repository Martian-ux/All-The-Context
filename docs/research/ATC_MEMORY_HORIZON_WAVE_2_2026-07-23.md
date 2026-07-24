# ATC Memory Horizon Report — Wave 2

## Fresh-horizon challenge to the July 2026 architecture and experiment order

| Field | Value |
|---|---|
| Report date | July 23, 2026 |
| Evidence cutoff | July 23, 2026, 23:59 UTC |
| Repository baseline | `2bc0ad6` |
| Documents challenged | [Memory Reliability Architecture](ATC_MEMORY_RELIABILITY_ARCHITECTURE.md) v0.2, [Memory Evaluation Program](ATC_MEMORY_EVALUATION_PROGRAM.md) v0.1, [competitor intake](../../research/competitor-intake/DECISION.md), and [Wave 1 horizon report](ATC_MEMORY_HORIZON_REPORT_2026-07-23.md) |
| Scope | Primary papers, official repositories, official benchmark artifacts, and author-maintained result corrections current through July 2026 |
| Exclusions | No third-party code or data was downloaded, installed, imported, or executed; no vendor or paper score was reproduced by ATC |
| Decision | Retain the reliability-control-plane direction, but move lossless programmatic logs, online/shift testing, poisoning admission tests, and repair/transfer tests ahead of framework adapters and most representation work |

## Executive judgment

Wave 1 made the right large corrections: simple baselines before frameworks,
graphs must earn their place, memory use is hazardous, procedural learning must
wait for outcome evidence, and derived-state closure belongs in the foundation.
Wave 2 does not reverse those decisions. It makes them more concrete and changes
the first experiments.

Five findings are most likely to change ATC:

1. **The strongest overlooked baseline is not “append log plus lexical search”;
   it is a lossless structured event log plus programmatic inspection.**
   [PRO-LONG](https://arxiv.org/abs/2607.20064), released July 22, reports an
   average 18-point improvement over the same coding agents without its log on
   the public ARC-AGI-3 games, while using 4.2–5.8 times fewer billed tokens
   than stronger specialized harnesses in matched comparisons. Its ablation
   improves from read-only, to regex, to Python analysis, showing that the
   reader's ability to interrogate the log is part of the baseline. This is an
   author result on a public set, not an ATC reproduction. The paper-linked
   repository returned HTTP 404 at cutoff, so neither code nor logs nor a code
   license are inspectable today. The mechanism still moves programmatic-log
   evaluation to the first tranche, but every number remains author-reported.
2. **Static/off-policy averages can select the wrong memory policy.**
   [AMemGym](https://openreview.net/forum?id=sfrVLzsmlf) makes memory writes part
   of the evaluated interaction and diagnoses write, read, and utilization
   separately. [ShiftBench](https://openreview.net/forum?id=CCSztIjmOy) reports
   rank reversals after controlled session-boundary shifts. The baseline ladder
   therefore needs online formation and post-shift recovery before any winner is
   frozen.
3. **Poisoning admission is earlier than sycophancy mitigation.**
   [MPBench](https://arxiv.org/abs/2606.04329) reports 50.46% average attack
   success and 41.05% retrieval success across two agents, with more aggressive
   read/write policies more exploitable and prompt-injection defenses providing
   incomplete coverage. [MEMFLOW](https://arxiv.org/abs/2603.15125) reports that
   memory can steer tool control flow against current instructions. Harm testing
   must begin at write admission and persist through later action, not begin
   only after an authorized record reaches retrieval.
4. **Raw fidelity and localized maintenance deserve priority over global
   consolidation.** A 12-system, 11-dataset study,
   [Are We Ready For An Agent-Native Memory System?](https://arxiv.org/abs/2606.24775),
   finds no dominant architecture, reports that raw long context remains
   strongest on time-dependent questions, and shows abstraction layers
   progressively discard information. Conservative/local maintenance beats
   coarse summaries and delayed global reorganization. Its
   [official repository](https://github.com/OpenDataBox/MemoryData) is useful for
   inspection but currently exposes no repository license and vendors multiple
   method runtimes, so it is evidence to reproduce minimally, not code to run or
   reuse.
5. **Portability is an integrity boundary, not only a continuity feature.**
   [Autonomous LLM Agent Worms](https://arxiv.org/abs/2605.02812) reports
   zero-click, three-hop cross-platform propagation through persistent files and
   summaries in three anonymized production frameworks.
   [Environment-injected memory poisoning](https://arxiv.org/abs/2604.02623)
   reports cross-session and cross-site compromise without direct memory access.
   Cross-agent export/import should remain blocked until typed promotion,
   provenance attenuation, dependency closure, and re-entry tests pass.

The revised near-term order is:

> lossless-log baseline and online/shift protocol → write-admission poisoning
> and applicability → correction/residue plus repair → working portability →
> action grounding → representation and maintenance ablations → external
> frameworks → procedures → neural or in-weight memory.

Overall confidence in this order is **high** for the first six steps and
**medium** for the placement of external frameworks versus specialized
procedural experiments. Confidence in any reported 2026 leaderboard result is
**low to medium** until reproduced under ATC's fixed reader, action, disclosure,
and lifecycle boundary.

---

## 1. Evidence discipline

### 1.1 What “reproduced” means here

**ATC-reproduced third-party results: none.** Wave 2 did not execute external
code or data. All numerical claims in this report remain author or vendor
evidence.

The following are stronger than ordinary single-system claims but are not ATC
reproductions:

- the MemoryData paper compares 12 systems and performs module ablations under
  one author-controlled harness;
- PRO-LONG rescored released competitor runs under a common public-game scoring
  procedure and reports repeated runs for some conditions;
- the author-maintained
  [agentmemory repository](https://github.com/JordanMcCann/agentmemory)
  publicly invalidates an earlier approximately 98% LongMemEval result after
  discovering that the evaluator supplied the full transcript, then reports
  lower legitimate-retrieval baselines before further tuning; and
- ShiftBench re-analyzes established datasets under a post-shift protocol and
  reports policy-rank inversions.

These are valuable negative or replication-style signals. They still require
ATC reproduction.

### 1.2 Evidence classes

| Class | Meaning in this report |
|---|---|
| R | Reproduced by ATC under the frozen Memory Lab boundary |
| A | Peer-reviewed/accepted primary study with inspectable method and artifact |
| B | Primary preprint or workshop study with inspectable method or official artifact |
| C | Author/vendor claim, self-reported correction, qualitative case study, or artifact without an adequate reuse license |
| H | Hypothesis or architectural proposal without decisive comparative evidence |

No item in this Wave 2 report is class R.

### 1.3 Evidence that should not drive architecture

- a single score on LoCoMo, LongMemEval, or a public ARC-AGI-3 set;
- best-of-\(k\) without pass@1, run counts, selection rules, and total cost;
- “world record” claims optimized on the full evaluation set;
- a repository badge without a license covering code and data;
- a claim that deletion or unlearning works without adversarial extraction,
  sequential requests, retained-utility, and derived-state tests;
- a portable plain-text skill or memory bundle treated as safe because it is
  readable; or
- a learned manager's aggregate gain without online shift, wrong-feedback,
  false-transfer, and ordinary-case regression results.

---

## 2. Findings by research question

### 2.1 Long context, observation logs, and programmatic memory

Wave 1 correctly placed long context, stable observations, and file search
before frameworks. Wave 2 strengthens and sharpens that decision.

#### Strong comparative evidence

- The [MemoryData study](https://arxiv.org/abs/2606.24775) reports that raw
  long-context retrieval outperforms most memory-backed approaches on
  time-dependent questions, while summarization and fine-grained extraction can
  lose chronology and multi-hop bindings. In its LightMem ablation, verbatim
  user content beats abstractive summaries across the reported LoCoMo and
  LongMemEval measures.
- [PRO-LONG](https://arxiv.org/abs/2607.20064) keeps a complete structured
   interaction log and lets coding agents search and analyze it. The paper's
   tool ladder improves as the agent gains regex and Python access, while
   persistent self-authored notes add little after full-log access is present.
   Its linked repository was unavailable at cutoff, so this is paper-only
   evidence rather than an inspectable artifact.
- [Multi-Head Recurrent Memory](https://arxiv.org/abs/2607.01523) diagnoses
  retention, not initial capture, as the dominant failure of recurrent textual
  memory and reports that partitioning a fixed memory window protects retained
  information at 100K–1M tokens.

#### Negative and replication signals

- The agentmemory author invalidated an approximately 98% LongMemEval run
  because `USE_DIRECT_CONTEXT=True` provided oracle transcript access. The same
  repository documents retrieval-index nondeterminism from insertion order and
  `PYTHONHASHSEED`. This is not an independent scientific replication, but it is
  a concrete warning that benchmark harness leakage and nondeterminism can
  dominate the result.
- [Do Coding Agents Need Executable World Models?](https://arxiv.org/abs/2607.15439)
  reports that model and reasoning-effort effects were more robust than several
  harness-component effects on the public ARC-AGI-3 games, and explicitly warns
  that public-set saturation is not held-out evidence.

#### ATC consequence

Split the current `simple_append_log_search` idea into four controls:

1. append-all structured log plus exact/lexical search;
2. the same log plus read-only programmatic queries;
3. the same log plus sandboxed analysis over symbolic fixtures; and
4. stable observation summaries with exact source pointers.

The comparison must equalize reader model, reasoning effort, tool calls, action
budget, and total billed tokens. Programmatic search is not free retrieval; its
tools, code execution, and latency are part of the memory condition.

**Confidence: high** that this belongs before framework adapters; **medium**
that it will transfer from game trajectories to personal-context tasks.

### 2.2 Memory-induced harm, sycophancy, and applicability

PersistBench and MemSyco already justified an applicability gate. Wave 2 shows
that the gate must cover formation and later action, not only retrieval.

- [MPBench](https://arxiv.org/abs/2606.04329) defines explicit instruction,
  system-prompt, compaction, and experience-to-procedure write channels. It
  reports that eager writing and retrieval improve exploitability and that
  current prompt-injection defenses do not cover the full memory-poisoning
  surface.
- [GhostWriter](https://arxiv.org/abs/2607.06595) reports approximately 98%
  injection and 60% average later activation against the evaluated agents. Its
  proposed admission and retrieval screens are author claims, not a general
  defense proof.
- [Environment-injected trajectory poisoning](https://arxiv.org/abs/2604.02623)
  reports activation on later sites without direct store access and a large
  susceptibility increase under environment frustration.
- [MEMFLOW](https://arxiv.org/abs/2603.15125) reports memory-driven tool-control
  flow overriding explicit user instructions in more than 90% of tested trials
  across named frontier models and two agent frameworks.
- A preliminary structured-memory study,
  [Mitigating Unintended Memory Usage](https://openreview.net/pdf/70599ac9dad9e0dbf8f3c62262a500164dac2f31.pdf),
  reports that domain partitioning reduces cross-domain leakage but does not
  establish that representation alone solves memory-induced sycophancy.

#### ATC consequence

The current three gates should become four:

1. **admission and witness** — may this input become durable at all?
2. **authority and role** — what can it establish?
3. **task applicability and disclosure** — may it influence this task, target,
   provider, and tool argument?
4. **relevance and sufficient-set compilation** — should it be selected?

Add a “write succeeded but activation was blocked” metric. A safe system should
also minimize poisoned durable writes, but a missed admission check must not
become a later action.

**Confidence: high.**

### 2.3 Action-grounded and on-policy evaluation

The current evaluation program centers CAOS and already includes action-bearing
tasks. Wave 2 changes the protocol around those tasks.

- [AMemGym](https://openreview.net/forum?id=sfrVLzsmlf) is interactive and
  on-policy: the assistant's own responses affect the subsequent trajectory.
  It separates write, read, and utilization failures and reports that
  off-policy rankings do not reliably predict interactive performance.
- [Task2Quiz](https://openreview.net/forum?id=LeeM4dTIf3) reports that task
   success can be a poor proxy for grounded environment understanding and that
   tested memory mechanisms did not reliably create a transferable world model.
- [Remember When It Matters](https://arxiv.org/abs/2607.08716) treats memory as
   selective proactive intervention. The authors report that a separate memory
   agent deciding whether to inject a grounded reminder beats passive bank
   exposure, always-on injection, advisor-only guidance, and general retrieval,
   with reported gains of 8.3 points on Terminal-Bench 2.0 and 6.8 points on
   tau2-Bench. These are author-reported results; no official code or benchmark
   artifact was located from the paper surface at cutoff.
- [PRO-LONG](https://arxiv.org/abs/2607.20064) supplies an action-bearing
  continual environment in which the current state does not always reveal the
  governing dynamics.
- [AgentMemoryBench](https://openreview.net/pdf?id=MSXbrNExax) includes online,
  replay, transfer, and repair modes across code, embodied, web, and dialogue
  tasks, preventing an offline QA average from standing in for continual
  learning.

#### ATC consequence

Every baseline winner must survive three regimes:

- frozen off-policy replay for comparability;
- on-policy memory formation and use; and
- a controlled shift or interruption followed by Recovery@T and correct next
  action.

A condition cannot be promoted if it wins only because another condition's
trajectory supplied cleaner evidence.

**Confidence: high.**

### 2.4 Correction, unlearning, and derived-state residue

The current architecture correctly distinguishes canonical correction, derived
closure, and opaque provider/model state. Wave 2 reinforces that separation.

- [Agentic Unlearning](https://arxiv.org/abs/2602.17692) names
  parameter-memory backflow: a supposedly forgotten fact can re-enter through
  either parametric state or external memory. Its synchronized method reports
  results on medical QA, but no official reusable implementation or broad agent
  benchmark was located.
- [Secure Forgetting](https://arxiv.org/abs/2604.00430) proposes state,
  trajectory, and environment unlearning plus an inference adversary. It is an
  early author claim, not evidence that natural-language unlearning prompts
  provide physical erasure.
- [AgentMemoryBench](https://github.com/solomoon313/AgentMemoryBench) makes
  wrong-feedback contamination and later repair explicit. Its repair cycle is
  closer to ATC's procedural risk than ordinary record deletion.
- The [MUSE unlearning benchmark](https://arxiv.org/abs/2407.06460), although
  aimed at model weights rather than agent stores, remains a useful negative
  control: most tested approximate unlearning algorithms either leak privacy,
  harm retained utility, or fail under sequential removals.

#### ATC consequence

Do not broaden ATC's product claim to model unlearning. Add a dual-path audit:

- enumerate and purge every ATC-owned record and derived artifact;
- rerun an adversarial extraction suite before and after purge;
- run the same probes against the unchanged reader model to quantify
  pretraining or provider-state residue; and
- report provider/model residue as outside the declared erasure boundary, not
  as an ATC purge failure or success.

Add sequential corrections and repeated purges; a one-shot clean result is
insufficient.

**Confidence: high** for the boundary; **low-medium** for current agentic
unlearning methods.

### 2.5 Working continuity and cross-agent portability

Working continuity remains necessary, but exact state transfer is only half the
problem.

- [ShiftBench](https://openreview.net/forum?id=CCSztIjmOy) reports that
  clean-stream retrieval ranking can invert after session-boundary interruption
  or context pollution.
- [AgentMemoryBench](https://github.com/solomoon313/AgentMemoryBench) provides
  transfer and repair modes, but its full artifact requires Docker, hosted
  services for some baselines, and an approximately 50 GB Freebase download for
  one task. The MIT code is inspectable; individual datasets and services retain
  separate terms.
- [Memory Transplants](https://openreview.net/forum?id=AIJsjIqfsp) reports that
  architecture transfer, content transfer, and solver capability interact;
  weaker solvers may benefit more, while static content transfer is limited.
- [Memp](https://openreview.net/forum?id=aaij11qBCl) claims that procedural
  memory distilled by a stronger model can benefit a weaker one, but this does
  not establish semantic equivalence, safety, or correction closure.

#### ATC consequence

Define portability as a semantic conformance test over a logical checkpoint,
not successful deserialization. Test:

- same model/restart;
- stronger-to-weaker and weaker-to-stronger model;
- different host tool schema;
- correction between export and import;
- missing capability and honest downgrade;
- poisoned or instruction-shaped imported checkpoint; and
- no imported target-specific prose acquiring canonical authority.

**Confidence: high** that these tests are required; **medium-low** that a single
portable working format will preserve behavior across heterogeneous agents.

### 2.6 Causal/event memory versus graphs

Wave 2 retains the graph demotion but adds a more precise challenger: align
memory units with the stage or decision boundary, not merely with entities.

- The [MemoryData study](https://arxiv.org/abs/2606.24775) reports graph
  strengths on some fact/update tasks but weakness on temporal reasoning and
  orders-of-magnitude construction/query overhead without proportional global
  gains.
- [Structurally Aligned Subtask-Level Memory](https://arxiv.org/abs/2602.21611)
  reports a 4.7-point mean SWE-bench Verified improvement over vanilla agents
  across tested backbones and attributes instance-level failures to granularity
  mismatch between whole tasks and local reasoning stages.
- [E-mem](https://arxiv.org/abs/2601.21714) argues that preprocessing into
  embeddings or graphs can destroy sequential context and reports gains from
  reconstructing coherent episodes. It uses multiple memory agents and remains
  expensive and LoCoMo-centered.
- [UMEM](https://openreview.net/forum?id=BoiXvrwtdi) acknowledges in its own
  limitations that transfer is not monotonic under distribution shift and that
  weakly related retrieved memories can interfere with execution.

#### ATC consequence

The next representation ablation should compare:

1. raw versioned event log;
2. event log partitioned by checkpoint/task stage;
3. typed temporal and prerequisite links;
4. task-specific causal links; and
5. a graph competitor.

Only the fourth condition may claim causal memory, and only where causal labels
come from an environment transition or deterministic dependency—not an
unverified model explanation.

**Confidence: high** on stage/event-first; **medium** on the value of causal
links.

### 2.7 Test-time, KV, recurrent, and neural memory

Wave 1 said “track, do not adopt.” Wave 2 strengthens that decision.

- [Tool Use Is Provably More Scalable Than In-Weight Memory](https://openreview.net/forum?id=s7IRNX6FUs)
  gives a theoretical construction and controlled experiments favoring
  external tools for factual capacity and preservation of general
  capabilities. Its [official code](https://github.com/ambroiseodt/itl) is
  CC BY-NC 4.0 and training-heavy, so it is not a product dependency candidate.
- [Persistent Q4 KV cache](https://arxiv.org/abs/2603.04428) reports major
  time-to-first-token gains on three local architectures. This is a resumable
  inference cache, not a governed knowledge store.
- [Multi-Head Recurrent Memory](https://arxiv.org/abs/2607.01523) is
  training-free and improves retention, but its state is still compiled text
  without ATC record identity or lifecycle closure.
- Agentic-unlearning work makes the risk clearer: once private information is
  placed in weights or opaque neural state, correction requires a second,
  materially harder erasure program.

#### ATC consequence

Keep KV and recurrent mechanisms as target-level performance conditions, never
as Core authority. The first neural experiment should be an attribution and
purge falsification test, not a quality tournament.

**Confidence: high.**

### 2.8 Procedural learning and wrong-feedback repair

Wave 2 identifies useful substructure without justifying immediate procedural
promotion.

- [AgentMemoryBench](https://openreview.net/pdf?id=MSXbrNExax) explicitly
  contaminates memory with incorrect rewards, applies corrective feedback, and
  measures net recovery.
- [Structurally Aligned Subtask-Level Memory](https://arxiv.org/abs/2602.21611)
  suggests that reusable experience should be indexed by functional stage and
  local intent, not whole-task similarity.
- [UMEM](https://arxiv.org/abs/2602.10652) optimizes memory across semantic
  neighborhoods and reports gains, but its own limitations acknowledge
  negative interference under distribution shift.
- [MUSE-Autoskill](https://arxiv.org/abs/2605.27366) proposes unit-tested,
  per-skill experience and claims cross-agent transfer. No official reusable
  implementation repository was located from the primary paper page.
- [A-MemGuard](https://openreview.net/forum?id=udqe7UZUZ6) argues that poisoned
  experience can form self-reinforcing cycles and proposes consensus plus a
  separate lesson store. It is accepted ICML 2026 author evidence, not proof
  that consensus detects coordinated or shared-source poison.

#### ATC consequence

Before generating a `ProcedureRecord`, require:

- executable or deterministic outcome evidence;
- task-stage and precondition labels;
- at least one mismatched near-neighbor;
- deliberately wrong feedback followed by repair;
- a poison-seeding attempt;
- transfer to a second target with semantic parity checks;
- ordinary/easy-task noninferiority; and
- exact source dependency plus purge.

Unit tests are evidence about a procedure, not authority to install or execute
it.

**Confidence: high** for the gates; **medium-low** that current learned managers
will pass them.

### 2.9 Privacy and cross-agent portability

Local ownership remains valuable but does not contain all privacy or integrity
risk.

- [Autonomous LLM Agent Worms](https://arxiv.org/abs/2605.02812) shows that
  persistent files, scheduled reload, summarization, and cross-agent messaging
  can form a multi-hop propagation path. The affected frameworks are
  anonymized, limiting independent verification.
- [MPBench](https://arxiv.org/abs/2606.04329) covers memory write and later
  activation as separate attack phases.
- [Environment-injected poisoning](https://arxiv.org/abs/2604.02623) bypasses
  store permissions because the agent itself writes the observed content.
- [Swarm Skills](https://arxiv.org/abs/2605.10052) claims zero-adapter
  portability through a plain-text skill specification, but supplies only
  compatibility analysis and a qualitative case study; automatic trajectory
  distillation without human review increases rather than resolves ATC's
  authority and propagation concerns.

#### ATC consequence

No cross-agent memory or skill import should autoload into model context.
Imported artifacts are inert, typed, provenance-bearing evidence. Portability
promotion requires:

- content/authority separation;
- capabilities that can only attenuate across hops;
- source and transitive dependency manifests;
- no write-before-exposed-read re-entry;
- quarantine of executable content and tool instructions;
- correction and revocation propagation; and
- multi-hop canary and poison tests.

**Confidence: high.**

---

## 3. Evidence classification

### 3.1 Strongest current evidence

| Finding | Evidence | Classification | Confidence |
|---|---|---:|---:|
| No architecture dominates; workload/module fit matters | 12 systems, two baselines, 11 datasets in MemoryData paper | B, comparative author study | Medium-high |
| Raw fidelity and conservative maintenance often beat abstraction | MemoryData module ablations | B | Medium-high |
| Programmatic inspection of a lossless log can be a strong action baseline | PRO-LONG matched-agent and tool-ladder ablations | B; public-set author result | Medium |
| Selective proactive intervention can outperform passive or always-on memory | Remember When It Matters ablations on Terminal-Bench 2.0 and tau2-Bench | B; author result, no located artifact | Medium |
| Static averages can hide shift recovery failures | ShiftBench dataset re-analysis | B; tiny workshop paper | Medium |
| Interactive/on-policy formation changes what is measured | AMemGym ICLR paper and MIT artifact | A | High |
| Aggressive memory write/read increases poisoning exposure | MPBench two-agent evaluation | B | Medium-high |
| Persistent memory can steer later tool control flow | MEMFLOW, eTAMP, GhostWriter, worm studies | B; mutually reinforcing author studies | Medium-high |
| Wrong-feedback repair is a separable competency | AgentMemoryBench repair protocol | B | Medium |
| External tools are preferable to in-weight storage for scalable factual recall | Theory plus controlled ITL experiments | B; workshop artifact | Medium-high |
| Whole-episode similarity can misroute procedural experience | ICML 2026 subtask-level memory study | A | Medium-high |

### 3.2 Promising hypotheses to test

| Hypothesis | Why promising | Principal falsifier |
|---|---|---|
| Lossless programmatic log is the strongest general baseline | Full evidence plus flexible inspection wins on long-horizon games | Static profile, lexical log, or bounded history is noninferior on ATC tasks at lower cost |
| Stage-aligned event memory beats both whole episodes and graphs | Reduces granularity mismatch without global topology | No CAOS gain after equalizing tokens and tool calls |
| Admission conservatism improves both safety and quality | Eager memory increases poison and noise | Missed useful memories reduce CAOS more than poisoning falls |
| Online/shift recovery changes winner selection | AMemGym and ShiftBench expose offline mismatch | Ranking remains stable across preregistered on/off-policy and shift regimes |
| Localized maintenance is the right default | Preserves fidelity and limits invalidation fan-out | Global consolidation wins future tasks without residue or cost regression |
| Portable procedures can transfer if typed and tested | Memp and MUSE-Autoskill report cross-target benefit | Semantic drift, false transfer, or poison increases on the second target |
| Partitioned recurrent text can improve working continuity | MHM protects retained items structurally | Structured checkpoint or raw log is noninferior and more correctable |

### 3.3 Hype and premature claims

Treat the following as hype until independently reproduced:

- “complete memory OS” based on one conversational leaderboard;
- “world record” after repeated tuning on the full public evaluation set;
- “zero-adapter portability” established by format compatibility and a
  qualitative case study;
- “agentic unlearning” that reports only task accuracy and one adversary but no
  sequential deletion, provider-state, backup, cache, or derived-residue audit;
- “secure memory” because data is local, readable, or domain-partitioned;
- “self-evolution” driven by self-judged success or consensus among memories
  derived from the same poisoned source;
- “causal memory” whose edges are model-authored explanations rather than
  observed interventions or deterministic dependencies; and
- KV, recurrent state, or parameter updates described as personal memory
  without enumeration and purge.

---

## 4. Delta against the current ATC architecture and evaluation program

| Current position | Wave 2 delta | Required experiment/program change | Confidence |
|---|---|---|---:|
| Baseline ladder includes append-log search and file search | Make lossless structured log plus programmatic analysis a named first-class baseline | Add tool-ladder ablation and equalize reasoning/tool budget | High |
| E01 state/authority precedes E02 continuity | Add poisoning admission and persistent activation to E01 | Test four write channels before any automatic consolidator or procedure adapter | High |
| Offline deterministic fixtures then model-backed suites | Add on-policy formation and post-shift recovery before freezing a winner | Pair off-policy, on-policy, and Recovery@T regimes | High |
| Applicability is between authorization and relevance | Add admission/witness as a separate earlier gate and tool-control-flow outcome later | Measure poisoned write, poisoned retrieval, and poisoned action separately | High |
| Derived-state inventory and rebuild are foundation work | Add localized-versus-global maintenance and sequential purge | Prefer conservative/local invalidation until global consolidation earns promotion | High |
| Working continuity tests restart/model/client change | Add distribution shift, host schema mismatch, and capability attenuation | Treat portability as semantic and security conformance | High |
| Event stream before typed graph projections | Add task-stage partitioning as the first structured projection | Compare raw event, stage/event, typed relations, causal links, graph | Medium-high |
| E03 episodic/procedural learning is third | Keep experience capture early but move procedure generation after action and repair gates | Separate experience-ledger test from procedure-promotion test | High |
| E04 combines relational, temporal, and recall-to-action | Split action grounding from representation choice | Run action-use gap before graph/causal tournament | High |
| Intent and Consequence Plane activates at decision checkpoints | Keep selective, consequence-aware intervention, but retire generic checkpoint activation as an ATC novelty claim | Compare silent, passive, always-on, advisor-only, general retrieval, and selective intervention under matched budgets | High |
| Neural/KV memory is tracked, not adopted | Make attribution/erasure the first neural test | Kill before quality testing if private influence cannot be enumerated and removed | High |
| External adapters follow simple baselines | Also require baseline stability across online and shift regimes | Do not pin a framework winner from static QA | High |
| Cross-agent portability is a continuity capability | Treat every import/export hop as an untrusted memory boundary | Add multi-hop poison, re-entry, and transitive revocation fixtures | High |

The two-plane architecture remains defensible. The Memory Plane should,
however, be described operationally as a governed family of evidence-preserving
views and readers, not a progression toward ever richer representations. The
Intent and Consequence Plane becomes more important because persistent memory
can steer tool control flow even when a current user instruction disagrees.

---

## 5. Ranked Wave 2 experiment backlog

Each experiment compares against the immediately simpler eligible condition.
Universal authorization, purge, disclosure, contamination, and budget gates
still apply.

### 1. Lossless programmatic-log baseline

**Question:** Does a complete structured event/action/outcome log plus
programmatic inspection beat lexical search, bounded history, stable
observations, and self-authored notes?

**Conditions:** no memory; bounded long context; append log + exact/BM25; log +
regex; log + read-only symbolic query; log + sandboxed analysis; stable
observation log.

**Primary measures:** CAOS, correct next action, evidence sufficiency, total
tool calls, reasoning tokens, latency, disclosure, and correction replay.

**Kill criterion:** Kill programmatic analysis if it improves CAOS by less than
5 points over lexical log search on both action-bearing families, exceeds the
25% cost premium, or cannot deterministically exclude unauthorized records
before analysis.

### 2. Online/off-policy/shift triangulation

**Question:** Does memory-policy ranking survive when the evaluated agent forms
its own history and encounters an interruption or distribution shift?

**Conditions:** identical policy under frozen replay, on-policy simulation, and
controlled shift/pollution.

**Primary measures:** rank correlation, Recovery@T, write accuracy, utilization,
CAOS, and post-shift stale activation.

**Kill criterion:** Do not freeze a baseline winner if its rank changes by more
than two positions, Spearman correlation falls below 0.7, or post-shift CAOS is
more than 5 points worse than the strongest simpler policy.

### 3. Memory admission and delayed-activation poisoning

**Question:** Can Core prevent untrusted conversation, tool output, compaction,
and experience-to-procedure content from becoming later behavioral directives?

**Conditions:** four write channels, benign twins, direct and semantically
obfuscated payloads, with and without current prompt-injection defenses.

**Primary measures:** poisoned durable-write rate, later retrieval rate, later
action rate, benign false-positive rate, and source/role preservation.

**Kill criterion:** Kill automatic application for any source class with one
observed hard-force escalation or poisoned protected action; narrow automatic
durability if the upper confidence bound on poisoned writes exceeds the frozen
threshold.

### 4. Correction, sequential purge, and dual-path residue

**Question:** Does correction/purge converge across canonical, summary, index,
cache, checkpoint, export, backup, and reader-observable state over repeated
requests?

**Conditions:** raw-only delete, tombstone, local rebuild, full declared-boundary
purge, repeated correct/delete cycles, unchanged reader model.

**Primary measures:** dependency completeness, reachable residue, extraction
success, retained utility, rebuild determinism, and provider/pretraining
residual.

**Kill criterion:** Stop promotion of any derived tier after one reachable
attributable residue or missing dependency; reject any product-level model
unlearning claim unless the model path is independently controllable and
auditable.

### 5. Wrong-feedback contamination and repair

**Question:** Can an experience store resist one wrong outcome label and return
to the clean-learning result after correction?

**Conditions:** clean learning; wrong reward; wrong then corrected reward; wrong
reward plus similar distractors; poisoned “successful” procedure.

**Primary measures:** error robustness, repair gain, net recovery, false
transfer, and residue after correction.

**Kill criterion:** Kill procedure promotion if net recovery is below 95% of
clean learning, any poisoned procedure survives correction, or ordinary-task
CAOS regresses by more than 1 point.

### 6. Working-checkpoint semantic portability

**Question:** Does a logical checkpoint preserve open commitments and correct
next action across model, client, tool-schema, and capability changes?

**Conditions:** same target; restart; stronger/weaker model swap; client swap;
renamed/reduced tool schema; correction between export/import; poisoned import.

**Primary measures:** exact logical resume, next-action success, semantic parity,
honest downgrade, stale dependency use, and unauthorized influence.

**Kill criterion:** Kill a portable format if exact resume is below 95%, stale
resume reaches 1%, capability loss is silently ignored, or imported prose gains
canonical authority.

### 7. Recall-to-action utilization gap

**Question:** Which failures remain after sufficient current memory is present?

**Conditions:** no memory; passive memory bank; always-on injection;
advisor-only guidance; general retrieval; selective checkpoint intervention;
oracle IDs; oracle rendered evidence; typed constraint; deterministic action
validator.

**Primary measures:** retrieval sufficiency, reader utilization, exact tool
choice and arguments, hallucinated defaults, and CAOS.

**Kill criterion:** Kill retrieval enhancements that close less than 10% of the
retrieval-to-action gap or whose gains disappear with oracle retrieval.

### 8. Raw event versus stage-aligned versus graph memory

**Question:** What is the smallest structure that improves temporal,
prerequisite, causal, and multi-hop tasks?

**Conditions:** raw events; stage-partitioned events; typed temporal/prerequisite
links; observed causal links; graph competitor.

**Primary measures:** CAOS, set sufficiency, temporal accuracy, false relation
use, construction/query cost, invalidation fan-out, and purge.

**Kill criterion:** Kill any relation family with less than an 8-point
sufficient-set gain on its target cases, no 5-point CAOS gain, ordinary recall
regression above 1 point, or unacceptable invalidation fan-out.

### 9. Localized versus global maintenance

**Question:** Does conservative record-local consolidation preserve more future
utility than periodic global summarization?

**Conditions:** no consolidation; local merge with sources; delayed flush;
global summary; summary plus raw fallback.

**Primary measures:** factual fidelity, chronology, multi-hop answerability,
write and rebuild cost, stale survival, and derived residue.

**Kill criterion:** Kill global consolidation if it fails to improve CAOS by 5
points, loses any required identifier/structured value, or costs more than twice
local maintenance without a preregistered capability win.

### 10. External framework adapters under the stable winner protocol

**Question:** Do Hindsight, Mem0, Graphiti, Letta, or LangMem beat the winning
simple conditions after online/shift and safety gates?

**Conditions:** each individual adapter without ATC feature wrapping; best
simple baseline; frozen non-ATC hybrid; governed hybrid.

**Primary measures:** CAOS and stage diagnostics, lifecycle support, egress,
cost, latency, determinism, and residue.

**Kill criterion:** Do not continue an adapter after a hard-boundary failure,
static-only gain, noninferiority of the simpler baseline, or inability to reset
and inventory state.

### 11. Stage-aligned procedural memory

**Question:** Does indexing verified tactics by functional stage and
precondition reduce repeated failures without negative transfer?

**Conditions:** raw trajectory; whole-episode summary; task-stage experience;
distilled procedure; unit-tested procedure; mismatched near-neighbor.

**Primary measures:** repeated-failure reduction, false transfer, repair, target
portability, step count, and purge.

**Kill criterion:** Kill procedure generation if repeated failures fall by less
than 20%, false transfer rises by more than 1 point, or benefit does not survive
a second target.

### 12. Cross-agent multi-hop re-entry and capability attenuation

**Question:** Can an exported memory/checkpoint/skill cross two agent boundaries
without instruction re-entry, authority inflation, or capability escalation?

**Conditions:** benign artifact; instruction-shaped content; summarized poison;
revoked source after first hop; weaker second host; delayed scheduled reload.

**Primary measures:** propagation, activation, capability attenuation,
revocation convergence, transitive provenance, and unauthorized action.

**Kill criterion:** Block cross-agent autoload after any zero-click propagation,
authority increase, missed transitive revocation, or protected action caused by
imported untrusted content.

### 13. Recurrent/KV/neural attribution and erasure falsification

**Question:** Can any below-prompt state enumerate which private records
influenced it and remove one record without full opaque retraining?

**Conditions:** persistent KV; partitioned recurrent text; record-owned
projection; parameter update, only where a controlled local model permits.

**Primary measures:** provenance recall, targeted removal, residual extraction,
retained utility, cross-session isolation, and rebuild cost.

**Kill criterion:** Kill the ATC integration path before quality evaluation if
dependencies cannot be enumerated, one record cannot be removed, or a purge
requires untracked shared retraining.

---

## 6. Benchmark and artifact intake

Availability is not permission to run. Every artifact still needs a pinned
revision, transitive license review, data-flow review, and isolated execution
approval.

| Artifact | Useful capability | Official artifact | License/artifact status | ATC intake |
|---|---|---|---|---|
| [AMemGym](https://github.com/AGI-Eval-Official/amemgym) | On-policy formation; write/read/use diagnostics | Code, configs, sample data, HF dataset | MIT repository; upstream Nemotron personas and model APIs need separate review | **Adopt protocol; later isolated benchmark** |
| [AgentMemoryBench](https://github.com/solomoon313/AgentMemoryBench) | Online, replay, transfer, repair across six tasks | Code and task data adapters | MIT repository; Docker, hosted APIs, task datasets, and ~50 GB Freebase asset have separate burdens | **Adopt repair/transfer protocol; reproduce small fixtures first** |
| [MemoryData](https://github.com/OpenDataBox/MemoryData) | 22 presets and module/system comparisons | Code and configs; datasets not bundled | No repository license found; multiple vendored runtimes create provenance risk | **Observe results; do not execute or reuse pending license/provenance review** |
| [PRO-LONG paper](https://arxiv.org/abs/2607.20064) | Lossless programmatic event-log baseline | Paper says code and logs are linked, but the linked repository returned HTTP 404 at cutoff | Code/log availability and code license are unverified; paper results are author-reported; ARC-AGI-3 and model/provider terms separate | **Reproduce mechanism natively on symbolic fixtures; do not reuse unavailable code** |
| [Remember When It Matters](https://arxiv.org/abs/2607.08716) | Selective proactive memory intervention | Paper and source are available; no official code or benchmark artifact located from the paper surface | Paper is CC BY 4.0; implementation and evaluation artifact availability unverified | **Adapt the ablation protocol; do not treat generic checkpoint activation as novel** |
| [ShiftBench](https://openreview.net/forum?id=CCSztIjmOy) | Post-shift Recovery@T | Tiny-paper protocol and formulas | Paper is CC BY 4.0; no official code repository located | **Adapt protocol** |
| [ITL](https://github.com/ambroiseodt/itl) | In-tool versus in-weight factual capacity | Training/evaluation code | CC BY-NC 4.0; PyTorch/training/model burden | **Observe; no product reuse** |
| [MPBench paper](https://arxiv.org/abs/2606.04329) | Persistent poisoning write/retrieval phases | Dataset described in paper | No official reusable benchmark repository located at cutoff | **Recreate safe symbolic classes; watch for artifact** |
| [Agentic Unlearning](https://arxiv.org/abs/2602.17692) | Parameter-memory backflow | Paper | No official code or reusable benchmark located | **Observe; use as boundary challenge** |
| [MUSE-Autoskill](https://arxiv.org/abs/2605.27366) | Unit-tested skill lifecycle and claimed transfer | Paper | No official reusable implementation located | **Observe; adapt only the unit-test/lineage hypothesis** |
| [agentmemory](https://github.com/JordanMcCann/agentmemory) | Benchmark leakage and nondeterminism case study | Code and author result logs/notes | MIT repository; full optimization methodology not published; heavily tuned public benchmark | **Use only as negative methodology evidence** |

No external artifact is approved for execution by this report.

---

## 7. 30/60/90-day watch and decision items

### Next 30 days

1. Freeze the four lossless-log controls and the tool/reasoning budget that
   makes their comparison fair.
2. Add online/off-policy/shift fields to the preregistration before selecting a
   baseline winner.
3. Extend E01 with symbolic MPBench-style write channels and delayed activation
   through a protected action.
4. Add wrong-feedback repair and sequential purge scenarios to the deterministic
   fixtures.
5. Define semantic checkpoint portability and capability attenuation independently
   of any Letta/LangGraph serialization.
6. Watch for the PRO-LONG repository to become available, official licenses or
   revisions for PRO-LONG and MemoryData, a Remember When It Matters
   implementation, an official MPBench artifact, and corrections to their
   reported results.
7. Watch for independent reproduction of AMemGym ranking changes and PRO-LONG's
   public-game gains.

### Next 60 days

1. Run the same-backbone small portfolio in this order: programmatic log,
   online/shift, admission poisoning, correction/residue, repair, portability,
   then action grounding.
2. Publish all baseline leakage, nondeterminism, timeout, and invalid-run
   findings, including negative results.
3. Compare raw event, task-stage event, and typed prerequisite projections
   before any graph service.
4. Evaluate local versus global maintenance under exact source-fidelity and
   sequential-purge tests.
5. Decide whether a reduced, license-clean AMemGym or AgentMemoryBench slice is
   worth an isolated third-party intake.
6. Watch accepted/final ICML 2026 artifacts for subtask-level memory, UMEM,
   E-mem, and A-MemGuard; compare final claims with submitted versions.
7. Watch security disclosures behind the anonymized multi-agent worm study and
   any independent MEMFLOW/eTAMP/GhostWriter replication.

### Next 90 days

1. Freeze the winning simple baseline only if its ranking is stable across
   offline, online, and shift regimes.
2. Admit the first external adapter only after the simple winner and poisoning
   suite are stable.
3. Decide whether working-checkpoint portability is a product capability or a
   research-only ABI based on semantic parity and multi-hop poison results.
4. Decide whether task-stage structure earns a production-neutral projection;
   do not start a graph phase unless stage/event memory fails a specific
   preregistered capability.
5. Start procedure distillation only if wrong-feedback repair, poison
   resistance, second-target transfer, and purge all pass.
6. Keep neural/KV memory outside the product path unless attribution and erasure
   evidence changes materially.
7. Watch for private-state unlearning that survives sequential removals,
   adversarial extraction, retained-utility checks, and memory/parameter
   backflow.
8. Publish a Wave 2 replication ledger labeling every result `reproduced`,
   `failed`, `unsupported`, `contaminated`, `license-blocked`, or
   `not-attempted`.

---

## 8. Primary-source ledger

### Comparative systems and baseline evidence

1. Zhou et al. [Are We Ready For An Agent-Native Memory System?](https://arxiv.org/abs/2606.24775). 2026 preprint; [official repository](https://github.com/OpenDataBox/MemoryData). Evidence B/C due missing repository license and vendored runtimes.
2. Fox et al. [PRO-LONG: Programmatic Memory Enables Long-Horizon Reasoning](https://arxiv.org/abs/2607.20064). July 2026 preprint. The paper-linked [repository](https://github.com/alexisfox7/PRO-LONG) returned HTTP 404 at cutoff, so code, logs, and code license are unavailable for inspection. Evidence C pending artifact availability.
3. Li, Yeh, and Li. [Multi-Head Recurrent Memory Agents](https://arxiv.org/abs/2607.01523). July 2026 preprint. Evidence B.
4. Rodionov. [Do Coding Agents Need Executable World Models, Simplification, and Verification to Solve ARC-AGI-3?](https://arxiv.org/abs/2607.15439). July 2026 preprint. Evidence B.
5. McCann. [agentmemory official repository and benchmark correction](https://github.com/JordanMcCann/agentmemory). Author-maintained implementation and self-correction. Evidence C.

### Online, shift, repair, and action evaluation

6. Jiayang et al. [AMemGym: Interactive Memory Benchmarking for Assistants in Long-Horizon Conversations](https://openreview.net/forum?id=sfrVLzsmlf). ICLR 2026; [official repository](https://github.com/AGI-Eval-Official/amemgym). Evidence A.
7. Zhang. [ShiftBench: Measuring Recovery of Agent Memory Under Distribution Shift](https://openreview.net/forum?id=CCSztIjmOy). ICLR 2026 MemAgents tiny paper. Evidence B.
8. AgentMemoryBench authors. [A Unified Benchmark for Continual Agent Memory](https://openreview.net/pdf?id=MSXbrNExax). ICLR 2026 Lifelong Agent workshop; [official repository](https://github.com/solomoon313/AgentMemoryBench). Evidence B.
9. Task2Quiz authors. [What Do LLM Agents Know About Their World?](https://openreview.net/forum?id=LeeM4dTIf3). 2026 submission. Evidence B.
10. Wu et al. [Remember When It Matters: Proactive Memory Agent for Long-Horizon Agents](https://arxiv.org/abs/2607.08716). July 2026 preprint, CC BY 4.0 paper; no official code or benchmark artifact located at cutoff. Evidence B/C.

### Poisoning, privacy, and portability

11. Dash et al. [From Untrusted Input to Trusted Memory: A Systematic Study of Memory Poisoning Attacks in LLM Agents](https://arxiv.org/abs/2606.04329). 2026 preprint. Evidence B.
12. Xu et al. [From Storage to Steering: Memory Control Flow Attacks on LLM Agents](https://arxiv.org/abs/2603.15125). 2026 preprint. Evidence B.
13. Zou et al. [Poison Once, Exploit Forever: Environment-Injected Memory Poisoning Attacks on Web Agents](https://arxiv.org/abs/2604.02623). 2026 preprint. Evidence B.
14. Torres, Shrestha, and Misra. [When Agents Remember Too Much: Memory Poisoning Attacks on Large Language Model Agents](https://arxiv.org/abs/2607.06595). July 2026 preprint. Evidence B.
15. Zha and Wang. [Autonomous LLM Agent Worms: Cross-Platform Propagation, Automated Discovery and Temporal Re-Entry Defense](https://arxiv.org/abs/2605.02812). 2026 preprint. Evidence B.
16. Anonymous. [Mitigating Unintended Memory Usage in LLMs via Structured Memory](https://openreview.net/pdf/70599ac9dad9e0dbf8f3c62262a500164dac2f31.pdf). Preliminary ICML submission. Evidence C.
17. Zhang et al. [Swarm Skills: A Portable, Self-Evolving Multi-Agent System Specification](https://arxiv.org/abs/2605.10052). 2026 preprint. Evidence H/C.

### Correction and unlearning

18. Wang et al. [Agentic Unlearning: When LLM Agent Meets Machine Unlearning](https://arxiv.org/abs/2602.17692). 2026 preprint. Evidence B.
19. Ye et al. [Secure Forgetting: A Framework for Privacy-Driven Unlearning in LLM-Based Agents](https://arxiv.org/abs/2604.00430). 2026 preprint. Evidence B.
20. Shi et al. [MUSE: Machine Unlearning Six-Way Evaluation for Language Models](https://arxiv.org/abs/2407.06460). 2024 benchmark paper and artifact. Evidence A/B; model-weight scope only.

### Representation, procedures, and neural memory

21. Shen et al. [Structurally Aligned Subtask-Level Memory for Software Engineering Agents](https://openreview.net/forum?id=2CoRS45Ucj). ICML 2026. Evidence A.
22. Wang et al. [E-mem: Multi-Agent Based Episodic Context Reconstruction for LLM Agent Memory](https://openreview.net/forum?id=FAjA0snAYq). ICML 2026. Evidence A.
23. Ye et al. [UMEM: Unified Memory Extraction and Management Framework for Generalizable Memory](https://openreview.net/forum?id=BoiXvrwtdi). ICML 2026. Evidence A.
24. Lin et al. [MUSE-Autoskill: Self-Evolving Agents via Skill Creation, Memory, Management, and Evaluation](https://arxiv.org/abs/2605.27366). 2026 preprint. Evidence B/H.
25. Wei et al. [A-MemGuard: A Proactive Defense Framework for LLM-Based Agent Memory](https://openreview.net/forum?id=udqe7UZUZ6). ICML 2026. Evidence A.
26. Houliston et al. [Tool Use Is Provably More Scalable Than In-Weight Memory for Large Language Models](https://openreview.net/forum?id=s7IRNX6FUs). ICLR 2026 MemAgents workshop; [official repository](https://github.com/ambroiseodt/itl). Evidence B.
27. Shkolnikov. [Agent Memory Below the Prompt: Persistent Q4 KV Cache for Multi-Agent LLM Inference on Edge Devices](https://arxiv.org/abs/2603.04428). 2026 preprint and code. Evidence B.
28. Feng, Yao, and Lewis. [Memory Transplants for LLM Agents](https://openreview.net/forum?id=AIJsjIqfsp). ICLR 2026 MemAgents workshop. Evidence B.
29. Memp authors. [Memp: Exploring Agent Procedural Memory](https://openreview.net/forum?id=aaij11qBCl). 2026 submission. Evidence B.

---

## 9. Known limits

- No third-party result was reproduced by ATC.
- Several July 2026 sources are new preprints, preliminary submissions, or
  workshop papers without independent replication.
- PRO-LONG and related ARC-AGI-3 results use a public game set and powerful
  frontier models; transfer to private personal memory is unknown. PRO-LONG's
  linked repository was unavailable at cutoff, preventing artifact inspection.
- The MemoryData study is broad but author-controlled. Its current repository
  lacks an obvious reuse license and includes vendored runtimes, preventing a
  clean execution recommendation.
- AMemGym uses simulated users and generated latent-state scenarios; it does
  not prove real-user value or privacy.
- Security papers use different threat models, agents, and success metrics.
  Their convergence raises confidence in the attack surface, not in any one
  percentage.
- Cross-agent portability evidence is particularly weak: current work often
  demonstrates format compatibility or task gain, not semantic parity,
  authority preservation, revocation, or privacy.
- Search coverage is broad, not exhaustive. Private systems, inaccessible
  artifacts, unpublished failures, and newly posted sources may be missing.

The operational response is not to add more components. It is to make the
simple baselines stronger, the trajectories more causal, the safety tests
earlier, and the kill decisions easier.
