# Post-beta continuity and memory proposal

## Measurement-first product work after the replacement beta

| Field | Value |
|---|---|
| Date | August 29, 2026 |
| Status | Draft proposal for evaluation; no production or roadmap authority |
| Applies after | Replacement-beta acceptance and the immediate hardening lane |
| Primary product question | Can ATC reduce the context a user must personally carry across sessions and AI clients? |
| Primary research question | Which memory mechanisms improve current authorized outcomes over the strongest simpler control? |
| Canonical inputs | [Zero-Friction Platform Execution Plan](../product/ZERO_FRICTION_EXECUTION_PLAN.md), [Memory Evaluation Program](ATC_MEMORY_EVALUATION_PROGRAM.md), [Wave 4 Integrated Results](ATC_MEMORY_LAB_WAVE4_RESULTS_2026-07-23.md), and [Memory Lab Governance](ATC_MEMORY_LAB_GOVERNANCE.md) |

This document is a proposal under review. It does not change current release
work, accept a new production feature, assign a stable interface, authorize a
migration, claim provider support, or establish scholarly novelty. Any accepted
part must enter the normal decision, implementation, review, and evidence
process separately.

## 1. Executive proposal

All The Context has a coherent product thesis and unusually strong foundations
for authority, lifecycle, correction, permission, provenance, deletion, purge,
continuous capture, and project-scoped context. It does not yet have equally
strong evidence that this machinery improves real work.

The immediate replacement-beta and hardening path should continue unchanged.
After that path is complete, the project should not default to adding more
retrieval breadth, graph complexity, embeddings, providers, or remote access.
The next program should measure whether ATC prevents the user and the next AI
client from repeating work already performed.

The proposed order is:

```text
replacement beta and hardening
  -> observable measurement spine
  -> versioned working continuity
  -> state-conditioned applicability
  -> prospective memory
  -> verified experience and procedures
  -> adaptive memory routing
  -> integration breadth only after evidence
```

Three provisional product milestones organize the work:

1. **0.2 — Reliable Working Continuity**
   - privacy-minimized memory-use and outcome receipts;
   - versioned working checkpoints;
   - three-way working-state reconciliation;
   - repository/environment applicability;
   - continuity-debt measurement; and
   - semantic handoff checks in shadow.
2. **0.3 — Memory at the Right Moment**
   - event-contingent prospective memory;
   - typed cues, positive witnesses, negative guards, expiry, cooldown, and
     duplicate suppression;
   - dormant non-disclosure; and
   - a notification-only action ceiling.
3. **0.4 — Verified Experience**
   - outcome-bound experiences;
   - conditional failure memory;
   - externally verifiable memory warranties;
   - procedure admission, correction, retirement, and purge closure; and
   - no stronger action authority than the evidence permits.

These labels are planning handles, not release promises.

The proposal also defines four ATC-specific experiment candidates:

- **Semantic Handoff Checksum**;
- **External-State Memory Warranty**;
- **Conditional Failure Memory**; and
- **Continuity Debt Ledger**.

Each has a simple control, observable metrics, hard safety gates, and a kill
rule. None should be described as novel outside ATC without a fresh prior-art
review and independent expert challenge.

## 2. Why this reordering is needed

### 2.1 Engineering evidence is not product evidence

ATC's automated tests and synthetic evaluation establish valuable conformance.
They can prove that a correction propagates through declared surfaces, that a
purged record does not reappear in an inspected fixture, or that a package
matches a manifest. They cannot prove that a real user restates less context,
that an agent takes a better first action, or that the system prevents a
repeated dead end.

The [Memory Evaluation Program](ATC_MEMORY_EVALUATION_PROGRAM.md) already makes
the right distinction: the unit of evidence is a memory episode, and the
primary endpoint is Current Authorized Outcome Success, not retrieval accuracy.
This proposal turns that principle into the next product sequence.

### 2.2 ATC currently has a closed-loop validation risk

The operator, coding agents, benchmark authors, fixtures, and most review logic
share much of the same project context. That loop can produce a system that is
consistent with its own assumptions while missing how another user or client
actually behaves.

This does not invalidate the current work. It limits the claim. The project is
a serious and technically mature prototype, but it remains weakly validated as
a product and as a general memory system.

### 2.3 More recall is not the clearest next bottleneck

ATC already has current records, lexical retrieval, project scopes, deterministic
Project Context Capsules, and lifecycle-aware client work. The next high-value
failure is more specific:

> A new session may receive relevant project information yet still reopen a
> settled question, repeat repository discovery, misread current working state,
> or follow guidance that was valid only on an older branch or environment.

The next program should attack those failures directly.

## 3. Governing rules

Every proposed mechanism must preserve the following rules.

1. **Measurement before mechanism.** Instrument observable use and outcomes
   before adding another production memory substrate.
2. **Core remains the sole authority.** Checkpoints, warranties, procedures,
   graphs, embeddings, and metrics are projections or observations, never
   parallel truth stores.
3. **No hidden-reasoning requirement.** Working state records declared and
   observable state, not chain-of-thought.
4. **Strong simple control first.** A static task note, deterministic scheduler,
   source-version check, or exact search is a required comparison when it could
   solve the same problem.
5. **Authorization and lifecycle precede relevance.** No router, verifier,
   graph, or learned mechanism may inspect unauthorized candidates and filter
   later.
6. **Unknown is not verified.** A failed or unavailable external-state check
   cannot silently validate a memory.
7. **Hard failures do not average out.** Unauthorized influence, wrong-project
   injection, stale protected action, duplicate execution, correction failure,
   or purge residue stops promotion regardless of aggregate quality.
8. **Shadow before authority.** New classification, routing, warranty,
   procedure, and metric mechanisms begin as lab or shadow outputs.
9. **Complexity must earn its place.** Kill or reduce a mechanism when the
   strongest simpler baseline is noninferior under the same model, context,
   latency, disclosure, and tool budget.
10. **Real-user evidence remains necessary.** Synthetic and repeated stochastic
    evidence can justify a limited pilot, not a broad product claim.

## 4. Proposed measurement spine

The first post-hardening implementation should be a narrow production form of
the existing observable-use research contract. It should answer what ATC
supplied and what observable result followed without claiming access to the
model's internal reasoning.

### 4.1 Proposed memory-use transaction

A content-minimized transaction may contain:

```text
MemoryUseTransaction
  use_id
  principal_view_generation
  canonical_snapshot_id
  project_id or explicit unresolved state
  task_id or bounded task fingerprint
  source and workspace generation
  selected canonical record IDs and versions
  project-capsule version
  working-checkpoint version, if present
  compiler, policy, and applicability versions
  disclosed field and token counts
  issued_at
  acknowledgement state, if the host supports it
  observable artifact or action commitment, if available
  outcome_receipt_id, if later observed
  invalidation or purge state
```

The transaction must not store raw prompts, complete rendered context, hidden
reasoning, credentials, or a second copy of retained source history.

Evidence grades remain separate:

```text
assigned
  -> supplied
  -> acknowledged
  -> host_observed_dependency
  -> outcome_association
  -> controlled_effect_estimate
```

Acknowledgement does not prove use. Host-observed dependence does not prove a
model's internal causal path. Causal language is reserved for preregistered
interventions with a valid comparison arm.

### 4.2 Proposed observable outcome receipt

```text
ObservableOutcomeReceipt
  outcome_id
  use_id
  task oracle and version
  completion state
  tool or artifact commitments
  tests, checks, or external result state
  user correction or rejection state
  task_success
  currentness_pass
  forbidden_influence_pass
  prerequisite_pass
  budget_pass
  stale_checkpoint_pass
  CAOS
  observed_at
```

A missing outcome is reported as missing. It is not converted into failure or
success after the fact.

### 4.3 Privacy boundary

The measurement spine should default to identifiers, versions, closed reason
codes, commitments, bounded counts, and timing classes. It should support
terminal privacy compaction consistent with current Core semantics. Any report
leaving the local vault must be explicitly generated, content-minimized, and
reviewable before export.

### 4.4 Why this belongs first

Without this spine, later mechanisms can improve retrieval metrics while making
actual work worse. With it, ATC can distinguish:

- a relevant record existed;
- the record was eligible;
- the record was selected;
- the record was supplied;
- a host-observable artifact depended on the supplied transaction;
- the task succeeded or failed; and
- a controlled omission changed the result.

That distinction is required before outcome-based learning or procedural
memory.

## 5. Provisional milestone 0.2 — Reliable Working Continuity

### 5.1 Product objective

A user should be able to stop work, switch from one supported client to
another, survive compaction or restart, and resume without restating the
project or accepting stale working state.

### 5.2 Versioned working checkpoints

A checkpoint should contain only declared or observable state:

```text
WorkingCheckpoint
  checkpoint_id and version
  project identity or explicit unresolved state
  task identity
  source, repository, and environment generation
  current objective
  completed observable steps
  active artifacts and revisions
  declared current hypothesis, if any
  blockers
  open questions
  next likely action
  explicit do-not-repeat state
  expiry
  close or abandonment state
  dependency generation
  provenance and witness class for every field
```

A model-authored field remains tentative unless current policy and evidence
permit a stronger role. A checkpoint is not a compressed chain-of-thought and
must not claim that the previous model's hidden state has been preserved.

### 5.3 Three-way reconciliation

At resume, Core should compare:

1. the last accepted checkpoint;
2. current source, repository, project, and policy state; and
3. newly observed client or user state.

Every field is classified as:

- `current`;
- `displaced`;
- `uncertain`;
- `invalid`;
- `new`; or
- `unavailable`.

The reconciler should emit a bounded current handoff and explicit unresolved
items. It must not resurrect a displaced next action merely because it appeared
in the last checkpoint.

### 5.4 State-conditioned applicability

Currentness by time is insufficient for operational memory. ATC should support
Core-owned applicability warrants for state such as:

- repository identity and branch lineage;
- source revision or file digest;
- lockfile or dependency generation;
- migration head;
- operating system and architecture;
- release series or channel;
- enabled feature or configuration generation; and
- declared client capability level.

Applicability predicates must be typed and code-owned. Arbitrary shell scripts
or model-written executable conditions are out of scope. When a required check
is unavailable, Core should mark the memory unverified and apply the declared
fail behavior rather than treating it as valid.

### 5.5 Acceptance benchmark

Create a frozen multi-session coding benchmark with at least 40 confirmatory
episodes across five task families:

- bug fixes;
- refactors;
- release preparation;
- documentation or configuration changes; and
- incident investigation.

Use multiple sanitized fixture repositories, including Python, TypeScript, and
mixed-project shapes. ATC's own repository may provide observational examples,
but it must not be the sole confirmatory environment.

Every task spans at least two sessions. A subset must switch supported clients.
Controlled between-session mutations include:

- branch or source revision changes;
- corrected requirements;
- dependency changes;
- abandoned approaches;
- deleted or purged memory;
- project ambiguity;
- externally modified files; and
- a stale checkpoint that appears superficially plausible.

Compare these conditions under fixed model, effort, context, tool, and time
budgets:

1. no memory;
2. full feasible prior transcript;
3. current Project Context Capsule;
4. capsule plus working checkpoint;
5. capsule plus checkpoint and three-way reconciliation; and
6. condition 5 plus the Semantic Handoff Checksum in shadow.

Primary and secondary measures:

- CAOS;
- task completion;
- first-action correctness;
- continuity debt by category;
- user restatement tokens;
- repeated repository inspection;
- repeated failed commands;
- stale or wrong-branch actions;
- omitted constraints;
- context tokens;
- time to first useful action; and
- compile and resume latency.

### 5.6 Provisional promotion gate

The exact thresholds must be accepted and frozen before confirmatory execution.
A starting proposal is:

- zero hard authorization, currentness, wrong-project, correction, or purge
  failures;
- task success noninferior to the strongest baseline within two percentage
  points;
- at least a 20% relative paired reduction in continuity debt against the
  current Project Context Capsule condition;
- no increase in incorrect first actions;
- less context than the full-transcript control; and
- no more than 25% additional context over the capsule-only condition.

Failure to meet the threshold should narrow or kill the added mechanism rather
than trigger post-hoc metric substitution.

## 6. Provisional milestone 0.3 — Memory at the Right Moment

### 6.1 Product objective

ATC should surface an accepted intention or obligation when its typed cue
becomes true, without continuously disclosing the latent memory and without
exceeding the permitted action force.

This extends the Event-Contingent Memory Transaction proposed in
[Wave 4 Integrated Results](ATC_MEMORY_LAB_WAVE4_RESULTS_2026-07-23.md).

### 6.2 Proposed transaction

```text
ProspectiveMemoryTransaction
  exact supporting evidence IDs and versions
  principal, project, domain, and policy generations
  typed cue predicate over authorized event metadata
  positive witnesses
  negative guards
  expiry, cooldown, and rearm rules
  maximum action force
  dependency and influence inventory
  issue, action, outcome, and invalidation receipts
```

The first production ceiling should be `notify`. Later `suggest` or `draft`
levels require separate evidence. Protected execution always requires fresh
confirmation and is not part of this milestone.

### 6.3 Required controls

Compare:

1. no prospective memory;
2. an explicit task table;
3. a deterministic scheduler;
4. always-injected reminders;
5. retrieval-only memory;
6. Event-Contingent Memory Transactions;
7. the same transaction without negative guards;
8. without current-version reread;
9. without dependency closure; and
10. without an action ceiling.

Fixtures include delayed time cues, event cues, overloaded cues, negative
controls, corrections after scheduling, permission changes, policy changes,
offline catch-up, time-zone changes, recurrence, duplicate cues, conflicting
intentions, stale harmful instructions, and protected actions.

### 6.4 Measures and gate

Report prospective precision, recall, F1, CAOS, false alarms, duplicate issues,
action success, disclosure, latency, and outcome benefit separately.

Promotion requires:

- zero unauthorized, stale, deleted, purged, or wrong-domain influence;
- zero protected action without fresh confirmation;
- zero duplicate execution or duplicate issue for one-shot intentions;
- exact correction and purge closure;
- a measurable advantage over the deterministic scheduler on CAOS, false
  alarms, disclosure, or outcome benefit; and
- no result based solely on larger prompt exposure.

If the scheduler is noninferior, retain the scheduler and do not ship the more
complex compiler.

## 7. Provisional milestone 0.4 — Verified Experience

### 7.1 Product objective

ATC should reuse an experience only when the result, applicability, and current
external state support reuse. It should prevent repeated dead ends without
turning one failed attempt into a permanent prohibition.

### 7.2 Outcome-bound experiences

An experience should distinguish:

- attempted action;
- observed result;
- independently verified outcome where available;
- state under which the result occurred;
- applicability and transfer boundaries;
- correction or disconfirmation;
- expiry and retirement; and
- every source and derived dependency required for withdrawal.

A successful-looking model trace and a model self-rating are insufficient.

### 7.3 Procedure admission

A reusable procedure requires:

- explicit preconditions;
- positive evidence from observable outcomes;
- counterexamples or negative guards;
- exact environment and capability boundaries;
- a verification recipe;
- correction and repair behavior;
- expiry or retirement rules;
- source and outcome dependencies; and
- purge closure.

Initial procedures may be supplied only as reference, suggestion, or draft.
They do not gain autonomous execution authority from repeated retrieval.

### 7.4 Promotion gate

Verified experience must beat raw trajectory retrieval, static notes, and
simple error-signature deduplication on held-out tasks. It must reduce repeated
failures without materially blocking retries that become valid after a state
change.

A starting proposal is:

- at least a 20% relative reduction in repeated ineffective actions;
- no more than a two-percentage-point increase in incorrectly blocked valid
  retries;
- task success noninferior to the strongest simpler control;
- zero procedure use outside hard applicability bounds; and
- exact correction, retirement, and purge closure.

## 8. Experiment candidate 1 — Semantic Handoff Checksum

### 8.1 Mechanism

After receiving a Project Context Capsule or working checkpoint, a cooperating
client returns a bounded semantic acknowledgement:

```text
HandoffAcknowledgement
  project_id
  source and checkpoint generation
  interpreted objective
  interpreted hard constraints
  interpreted completed state
  interpreted blockers
  interpreted next action
  declared uncertainties
```

Core compares the acknowledgement with the canonical issued projection and may
supply a small corrective delta for omitted or misclassified fields.

The acknowledgement is not truth, authority, or proof of future obedience. It
is evidence that the handoff survived the first interpretation step.

### 8.2 Hypothesis

A two-phase handoff will reduce early-session constraint and state errors more
efficiently than increasing one-shot capsule size.

### 8.3 Comparison arms

1. current one-shot capsule;
2. larger one-shot capsule with matched total token budget;
3. capsule plus semantic acknowledgement, no repair;
4. capsule plus acknowledgement and corrective delta; and
5. full prior transcript.

### 8.4 Metrics

- incorrect first five actions;
- omitted hard constraints;
- source-generation misunderstanding;
- false declaration of understanding;
- user corrective turns;
- context and acknowledgement tokens;
- latency; and
- eventual task success.

### 8.5 Provisional gate

Advance only if acknowledgement plus corrective delta reduces early constraint
errors by at least 20% relative to the ordinary capsule, adds no authority or
privacy failure, and uses less total context than the full-transcript control.
The acknowledgement overhead should remain below 15% of the task's total
context budget.

### 8.6 Kill rule

Kill the mechanism if acknowledgement content does not predict later behavior,
if it becomes confident model self-report without useful correction, or if a
better one-shot capsule is noninferior.

### 8.7 Novelty boundary

Do not claim novelty for model acknowledgement, structured handoff, or
self-checking. The possible ATC-specific contribution is the comparison against
an exact canonical projection followed by a minimum corrective delta, while
preserving authority, generation, permission, and purge boundaries.

## 9. Experiment candidate 2 — External-State Memory Warranty

### 9.1 Mechanism

A high-impact memory may carry a typed, code-owned, read-only validity warranty:

```text
MemoryWarranty
  record ID and version
  applicability-warrant type
  valid_when predicates
  invalid_when predicates
  allowlisted verification recipe ID
  expected bounded result
  last verified generation and time
  verification expiry
  unavailable behavior
  invalid behavior
```

Permitted first probes may include:

- repository and branch identity;
- branch ancestry;
- exact file or lockfile digest;
- migration head;
- installed tool version;
- presence of a bounded configuration field; and
- a read-only capability query.

Arbitrary shell, arbitrary model-written code, broad filesystem access, and
unbounded network probes are prohibited. Verification failure never counts as
verification.

### 9.2 Hypothesis

Fresh use-time verification will prevent stale operational guidance better than
recency, semantic retrieval, or source-version checks alone.

### 9.3 Experiment

Construct at least 100 controlled state transitions in which a memory is:

- still valid;
- explicitly superseded;
- invalid because a dependency changed;
- valid only on another branch;
- temporarily unverifiable;
- valid again after rollback; or
- textually unchanged but behaviorally invalid.

Compare:

1. recency only;
2. source-version pinning;
3. dependency invalidation;
4. static applicability warrants; and
5. use-time warranty verification.

### 9.4 Metrics

- stale high-impact memory issued;
- valid memory incorrectly suppressed;
- unverified memory incorrectly treated as verified;
- task success;
- verification latency and failure rate;
- disclosure and filesystem surface; and
- correction and purge closure.

### 9.5 Gate and kill rule

Advance only when the warranty prevents stale high-impact issue cases that the
static controls miss, without adding unauthorized reads or a material false
invalidation regression.

Kill use-time verification when static dependency tracking is noninferior. Keep
the static applicability contract if it remains useful.

### 9.6 Novelty boundary

Do not claim novelty for executable assertions, cache validation, dependency
checks, verifier metadata, or environment fingerprints. The candidate ATC
contribution is a read-only, authority-bound warranty evaluated immediately
before high-impact memory use and withdrawn through the same lifecycle as the
record and every derived influence.

## 10. Experiment candidate 3 — Conditional Failure Memory

### 10.1 Mechanism

Failures should not become unconditional warnings. Store a bounded retry
contract:

```text
ConditionalFailureMemory
  attempted action
  observed failure class
  state fingerprint
  suspected cause and confidence
  supporting evidence
  do_not_retry_while predicates
  retry_when predicates
  expiry
  disconfirming evidence
  correction and retirement state
```

A suspected cause remains tentative unless independently established. The
system may suppress an identical retry only while the declared state remains
applicable.

### 10.2 Hypothesis

Conditional failure memory will reduce repeated dead-end actions without
permanently blocking an approach that becomes valid after a relevant state
change.

### 10.3 Comparison arms

1. raw failed-trajectory retrieval;
2. static `avoid this` note;
3. exact error-signature deduplication;
4. conditional failure memory without disconfirmation; and
5. full conditional failure memory.

### 10.4 Fixtures

Include tasks where:

- retrying is always wrong;
- retrying becomes correct after a dependency change;
- credentials or network state change;
- the first causal diagnosis is wrong;
- two similar errors have different causes;
- the same error text appears under different state; and
- a correction explicitly reopens an approach.

### 10.5 Metrics

- repeated ineffective actions;
- valid retries incorrectly blocked;
- steps and time to recovery;
- task completion;
- negative transfer between projects;
- stale suppression after correction; and
- retirement and purge closure.

### 10.6 Gate and kill rule

Advance only if the mechanism reduces repeated ineffective actions by at least
20% over raw-history retrieval and simple signature deduplication while keeping
incorrectly blocked valid retries within the frozen noninferiority margin.

Kill automatic suppression if it meaningfully blocks valid recovery or if exact
signature deduplication is noninferior. Retain the result as an advisory warning
only if that smaller form remains useful.

### 10.7 Novelty boundary

Do not claim novelty for learning from failed trajectories, mistake memory, or
negative examples. The candidate ATC contribution is a revocable state-bound
retry contract that explicitly defines when a past failure stops applying.

## 11. Experiment candidate 4 — Continuity Debt Ledger

### 11.1 Mechanism

Track observable work that a functioning memory system should have prevented.
The first event classes are:

- `USER_RESTATEMENT` — the user restates current authoritative information that
  should already have been available;
- `REPEATED_DISCOVERY` — the agent repeats repository or source inspection
  already represented in current working state;
- `REPEATED_FAILURE` — the same ineffective action is repeated under materially
  unchanged conditions;
- `REOPENED_DECISION` — the agent treats a current settled decision as unknown;
- `STALE_RECOVERY` — work is spent repairing guidance that ATC supplied as
  current but was stale;
- `WRONG_PROJECT_RECOVERY` — work is spent undoing a wrong-project handoff;
- `CAPSULE_OMISSION` — a required current field existed but was absent from the
  issued project or working projection; and
- `CHECKPOINT_DRIFT` — resume follows displaced checkpoint state.

Each event links to the memory transaction that should have prevented it or
records that no eligible memory existed.

### 11.2 Hypothesis

Continuity debt will correlate with task efficiency and user-perceived value
more strongly than retrieval precision or memory-question accuracy.

### 11.3 Metric contract

Raw categories and opportunity counts are always reported separately. An
aggregate score is permitted only with preregistered weights:

```text
continuity_debt_rate =
  weighted observed debt events
  / eligible continuity opportunities
```

Weights may not be selected after seeing which condition wins. If credible
weights cannot be frozen, the project should use a vector of category rates
instead of one score.

### 11.4 Comparison

Measure continuity debt across:

1. no memory;
2. full prior transcript;
3. Project Context Capsule;
4. working checkpoint;
5. checkpoint plus reconciliation;
6. prospective memory; and
7. verified experience.

Compare the metric with task completion, first-action correctness, user
restatements, elapsed work, context cost, and blinded usefulness ratings.

### 11.5 Gate and kill rule

Advance the ledger as a product metric only if its categories can be observed
reliably without raw private text and if lower debt predicts better task or user
outcomes across held-out episodes.

Kill the aggregate score if category movement is contradictory, weights dominate
the result, or it fails to predict meaningful outcomes. Retain independently
useful raw event categories for diagnostics.

### 11.6 Novelty boundary

Do not claim novelty for repeated-work metrics, user restatement counts, or
agent-efficiency telemetry. The candidate ATC contribution is an
memory-transaction-linked account of avoidable continuity work with explicit
currentness, authority, project, correction, and purge semantics.

## 12. Adaptive memory routing comes later

After the earlier milestones, ATC may evaluate routing among:

- exact current records;
- source-backed evidence;
- Project Context Capsules;
- working checkpoints;
- temporal history;
- conditional failure memory;
- verified procedures;
- typed graph relations; and
- raw source dereference.

The router must operate only over an already authorized and applicable
projection. It cannot become another permission or truth authority.

A single graph, dense index, or learned retriever should not be assumed to serve
all task classes. Each substrate must win its target task against the current
lexical and capsule baseline. Graph and embedding promotion remain blocked until
that evidence exists.

## 13. Evaluation design

### 13.1 Evidence ladder

Use the accepted Memory Lab ladder:

- `L0`: proposal and frozen specification;
- `L1`: deterministic synthetic worker result;
- `L2`: coordinator-reproduced integrated result;
- `L3`: isolated pinned external-supplier result, when permitted;
- `L4`: cross-platform or repeated stochastic client/model evidence; and
- `L5`: consented product evidence.

A feature may enter production shadow after accepted lower-level evidence, but
broad product claims require L5 evidence.

### 13.2 Development and confirmatory separation

- Build and tune against a public development suite.
- Freeze confirmatory fixtures, tasks, conditions, metrics, budgets, and kill
  rules before mechanism implementation sees their outcomes.
- Preserve negative, blocked, skipped, and unsupported cells.
- Use deterministic task and repository oracles wherever possible.
- A model judge may supplement but may not replace a deterministic oracle for
  currentness, permission, source state, tests, or tool outcomes.

### 13.3 Fairness controls

Every comparison should equalize, where applicable:

- model and client version;
- reasoning effort;
- task input;
- context and token budget;
- tools and permission set;
- time budget;
- source state;
- retry allowance;
- temperature and seed; and
- evaluator access.

Report when a client capability prevents exact equivalence instead of silently
emulating a stronger hook.

### 13.4 Required hard gates

No experiment may promote a mechanism that causes:

- unauthorized or unauthorized-derived influence;
- wrong-project context;
- stale, superseded, deleted, expired, or purged influence;
- correction nonconvergence;
- duplicate capture, memory, issue, or action;
- raw secret persistence;
- arbitrary executable predicates;
- model self-report promoted to truth or causal evidence;
- hidden-reasoning retention;
- protected action without current confirmation;
- diagnostics that reveal inaccessible candidates; or
- a derived surface outside correction and purge inventory.

## 14. Limited external validation

The project is not ready for broad promotion. It should still seek limited
outside use once the replacement beta is trustworthy and the 0.2 path has L4
evidence.

The proposed L5 entry is two or three technically competent design partners
using disposable or non-sensitive projects under an explicit experimental
label. The objective is to observe:

- installation and connection failures;
- project-resolution ambiguity;
- unexpected correction and forget behavior;
- distrust or overtrust of supplied memory;
- continuity debt missed by synthetic fixtures;
- client-specific capability gaps; and
- whether the user returns to the system voluntarily.

No invasive default telemetry is proposed. Evidence should use consented,
content-minimized local receipts and structured reports that the participant can
inspect before sharing.

## 15. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Overfitting to ATC's own repository | Use multiple frozen fixture repositories and holdout tasks |
| Instrumentation changes behavior | Compare instrumented controls and keep receipts outside model-visible context |
| Outcome ambiguity | Preserve evidence grades and require deterministic or independent oracles where possible |
| Client capability asymmetry | Negotiate and report capability levels; do not fabricate hooks |
| Privacy expansion from measurement | Store commitments, IDs, versions, and bounded codes rather than raw content |
| False novelty claims | Perform updated primary-source review and independent challenge before any public claim |
| Benchmark gaming | Freeze confirmatory tasks, budgets, and kill rules before mechanism tuning |
| Excess maintenance complexity | Require noninferiority and delete or simplify losing mechanisms |
| Single-user evidence | Progress from L2 to L4, then a small consented L5 design-partner pilot |
| Graph or embedding distraction | Keep both shadow-only until a task-specific win over simpler baselines |
| Procedure overreach | Start with notify, suggest, or draft ceilings and require fresh confirmation for protected action |

## 16. Proposed implementation packets after acceptance

Acceptance of this proposal should not authorize one large implementation PR.
Use narrow packets.

### Packet A — freeze the continuity benchmark

- define episode schema, task families, baselines, metrics, budgets, and hard
  gates;
- build development and confirmatory fixture repositories;
- add deterministic task and repository-state oracles; and
- make no production schema change.

### Packet B — continuity debt scorer

- implement event classification over deterministic receipts;
- report category vectors before accepting an aggregate score;
- test paired-vault noninterference; and
- remain benchmark-only.

### Packet C — measurement-spine shadow receipts

- add privacy-minimized transaction and outcome contracts;
- write shadow receipts without affecting retrieval or formation;
- prove correction, deletion, purge, and rebuild behavior; and
- do not expose receipts to ordinary clients.

### Packet D — checkpoint compiler in Memory Lab

- compile checkpoints from explicit user state, source facts, and observable
  client events;
- keep model-derived fields tentative;
- prove restart, correction, abandonment, and purge cases; and
- compare against a static task note.

### Packet E — three-way reconciliation

- compare checkpoint, current source state, and new client observations;
- produce current, displaced, uncertain, invalid, new, and unavailable classes;
- run the frozen 0.2 benchmark; and
- promote nothing unless the checkpoint baseline itself earns advancement.

### Packet F — Semantic Handoff Checksum shadow

- require no canonical write;
- compare one-shot and two-phase handoff under matched budgets;
- preserve acknowledgement as non-authoritative; and
- kill it when a better capsule is noninferior.

### Packet G — state applicability and warranties

- begin with static typed applicability;
- add code-owned read-only verification only for cases static state misses;
- keep unavailable distinct from invalid; and
- prohibit arbitrary executable predicates.

Prospective memory, conditional failure memory, and procedures begin only after
the measurement and continuity packets establish a valid outcome path.

## 17. Explicit non-goals

This proposal does not authorize:

- broad marketing or a stable release claim;
- automatic execution of remembered procedures;
- arbitrary scripts as memory validity checks;
- graph or embedding promotion by architectural preference;
- additional provider breadth as a success metric;
- hosted Core, remote access, mobile access, or multi-user authority;
- model self-ratings as procedure evidence;
- hidden chain-of-thought capture;
- a second canonical memory store;
- learned authorization, permission, or truth policy;
- provider-side retroactive deletion claims; or
- a claim that ATC has solved AI memory.

## 18. Review questions

Reviewers should answer these questions explicitly.

1. Is measurement correctly placed before new memory mechanisms?
2. Should the measurement spine and working checkpoints be one milestone or
   separate gates?
3. Are continuity-debt events observable without excessive private content or
   false attribution?
4. Are the provisional benchmark size and promotion thresholds credible?
5. Is the Semantic Handoff Checksum meaningfully different from simply improving
   the capsule?
6. Can external-state warranties remain code-owned, read-only, bounded, and
   portable enough to justify their complexity?
7. Does Conditional Failure Memory avoid turning one failed attempt into a
   permanent prohibition?
8. Is the notification-only ceiling sufficient for the first prospective
   memory product?
9. Which fields in the proposed receipts or checkpoints create unnecessary
   privacy or maintenance risk?
10. What is the smallest safe production slice after the lab results?
11. What evidence would justify inviting the first two or three design partners?
12. Which part of the proposal should be rejected now rather than deferred?

## 19. Requested decision

This draft requests evaluation of the direction, ordering, experiments,
controls, gates, and boundaries. It does not request automatic acceptance of
all four mechanisms.

Possible outcomes are:

- `ACCEPT_DIRECTION_AND_FREEZE_PACKET_A`;
- `ACCEPT_WITH_REQUIRED_CHANGES`;
- `ACCEPT_SELECTED_EXPERIMENTS_ONLY`;
- `SPLIT_PRODUCT_AND_RESEARCH_PROPOSALS`;
- `HOLD_FOR_POST_BETA_EVIDENCE`; or
- `REJECT_AND_RETAIN_CURRENT_ROADMAP_ORDER`.

The recommended first decision is:

> Accept the measurement-first direction, freeze Packet A, and evaluate
> versioned working checkpoints plus continuity debt before any new graph,
> embedding, provider, remote, or learned-memory feature.
