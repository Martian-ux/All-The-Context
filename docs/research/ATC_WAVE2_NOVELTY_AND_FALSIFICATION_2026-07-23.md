# ATC Wave 2 Novelty and Falsification Report

## Authority-preserving mechanisms beyond ordinary retrieval

| Field | Value |
|---|---|
| Date | July 23, 2026 |
| Repository baseline | `2bc0ad66c019511e9e0320daeec1a45aedc280b6` |
| Scope | Independent research specification only |
| Production behavior | None implemented or accepted |
| Third-party code | Not downloaded, installed, or executed |
| Evidence | Current ATC research, CAOS specification, competitor intake, primary papers, and first-party documentation |

## Executive judgment

ATC should not claim novelty for memory lineage, counterfactual memory selection,
barrier-first derived-state repair, outcome-labeled experience, working
checkpoints, or cross-run repair by themselves. Primary work published by July
23, 2026 already occupies each of those areas.

The defensible Wave 2 research claim is narrower:

> ATC may be able to join a user-owned authoritative record lifecycle to
> minimum authorized-and-applicable context, intervention-ready use receipts,
> controlled outcome attribution, repairable cross-agent working state, and
> purge-complete derived influence without allowing learned or agent-authored
> state to acquire truth or permission authority.

That is a composition hypothesis, not a priority claim. It becomes meaningful
only if the composition beats simpler controls on Current Authorized Outcome
Success (CAOS), prevents forbidden influence, and remains cheaper to correct
and purge than to discard and rebuild indiscriminately.

The most important prior-art correction is **MEMOREPAIR**. Its barrier-first
cascade-repair contract already withdraws invalid descendants before repair and
restricts republication to validated predecessor-closed successors. ATC must
not present "derived-state closure" or "barrier-first repair" alone as new.
ATC's remaining distinction is the authority- and purge-aware boundary:
canonical correction, reversible deletion, irreversible purge, authorization
change, issued context, outcome statistics, and cross-agent working state share
one Core-owned influence contract, while record-specific lineage may be erased
after purge without allowing stale clients to validate old state.

An equally important activation correction comes from
[Remember When It Matters](https://arxiv.org/abs/2607.08716). It explicitly
frames memory as selective active intervention, names behavioral state decay,
and reports selective reminders outperforming passive bank exposure, always-on
injection, advisor-only guidance, and general retrieval on Terminal-Bench 2.0
and tau2-Bench. ATC must not claim novelty for intervening selectively when
memory matters, for checkpoint-proximate reminders, or for showing that
selective intervention can beat passive retrieval. ATC's possible contribution
begins only when an intervention is constrained by Core authority, temporal
state, epistemic role, task applicability, and force ceiling; bound to current
source and target dependencies; measured by privacy-minimal receipts; and
invalidated or purged through the same authoritative lifecycle.

The six mechanisms below are implementable as isolated Memory Lab experiments.
None requires production migration, private user data, a graph database, shared
model training, or third-party execution.

## 1. Evidence discipline and novelty rules

### 1.1 Materials reviewed

The repository review covered every current document in `docs/research`:

- [ATC Memory Evaluation Program](ATC_MEMORY_EVALUATION_PROGRAM.md);
- [ATC Memory Horizon Report](ATC_MEMORY_HORIZON_REPORT_2026-07-23.md);
- [ATC Memory Reliability Architecture](ATC_MEMORY_RELIABILITY_ARCHITECTURE.md);
- [Consequence-Closed Context](CONSEQUENCE_CLOSED_CONTEXT.md); and
- [From Recall to Reaction](FROM_RECALL_TO_REACTION.md).

It also covered the machine-readable CAOS specification and fixtures in
`bench/memory_reliability_spec.json` and
`bench/memory_reliability_fixtures.json`, plus the competitor decision and full
intake in `research/competitor-intake`.

The external scan used primary papers, official repositories referenced by
those papers, and first-party product documentation. No result was reproduced.
Author-reported measurements remain hypotheses.

### 1.2 Claim classes

Every mechanism receives one of four novelty-confidence classes:

| Class | Meaning |
|---|---|
| High | The exact protocol boundary was not identified in reviewed primary work; neighboring primitives exist |
| Medium | The composition appears differentiated, but one or more close systems occupy most primitives |
| Low | The mechanism is useful ATC engineering but prior work already occupies the central claim |
| None | ATC should adopt or test the mechanism without a novelty claim |

Novelty confidence is not evidence of product value. A low-novelty mechanism
may be the most valuable thing to ship.

### 1.3 Claims this report rejects

ATC should not claim:

- the first causal or counterfactual memory selector;
- the first memory-use or derivation ledger;
- the first dependency-aware agent-memory repair system;
- the first barrier-first cascade repair;
- the first selective active memory intervention or checkpoint-proximate
  reminder;
- the first account of behavioral state decay;
- the first evidence that selective reminders can beat passive or always-on
  memory injection;
- the first minimum or budgeted context compiler;
- the first outcome-labeled or procedural memory;
- the first working checkpoint or portable memory protocol;
- causal influence from prompt inclusion, client self-report, or an
  observational outcome receipt;
- purge closure for opaque provider state or shared model weights; or
- general historical priority from this targeted scan.

### 1.4 Activation novelty boundary

Generic activation is occupied prior art. The following are not ATC research
claims:

```text
memory should sometimes intervene
memory should intervene near a relevant action
always-on memory can distract
a separate memory controller can choose remind-or-silent
selective reminders can improve long-horizon task success
```

The bounded ATC hypothesis is:

> Given a current Core-authorized candidate set, can ATC admit only memories
> whose epistemic role and task applicability permit intervention, cap the
> intervention's force by its witness and confirmation state, bind its issued
> representation to source and target dependencies, record only observable and
> privacy-minimal receipts, and remove its future local or conforming-host
> influence after correction or purge?

This hypothesis can fail even when a proactive reminder agent improves task
success. A selective reminder that uses an inapplicable preference as factual
evidence, exceeds its force ceiling, survives correction, or cannot be purged
fails ATC's claim.

## 2. Ranked portfolio

Scores use `1` (low) through `5` (high). Host dependence is reversed:
`1` means Core-only or harness-only, while `5` requires deep host cooperation.

| Rank | Mechanism | Product value | Novelty confidence | Tractability | Host dependence | Immediate decision |
|---:|---|---:|---:|---:|---:|---|
| 1 | M2 — Sealed Projection Minimal Compiler | 5 | 3 | 5 | 1 | Test now |
| 2 | M3 — Record-Influence Barrier Closure | 5 | 2 | 4 | 1 local / 3 issued state | Test now; do not claim barrier-first novelty |
| 3 | M6 — Portable Working-State Three-Way Repair | 5 | 4 | 4 | 2 | Test now |
| 4 | M5 — Outcome-Labeled Experience Quarantine | 4 | 3 | 4 | 2 | Test after M1 receipts exist |
| 5 | M1 — Authorized Memory-Use Ledger | 4 | 3 | 5 | 2 | Instrument every Wave 2 experiment |
| 6 | M4 — Budgeted Randomized Context Attribution | 4 | 3 | 3 | 3 | Run only on reversible synthetic tasks |

The ranking deliberately separates scientific novelty from the build order.
M1 is enabling infrastructure even though its standalone product value is less
visible. M3 ranks highly because closure is a safety prerequisite, despite low
novelty confidence after MEMOREPAIR.

## 3. M1 — Authorized Memory-Use Ledger

### 3.1 Exact new claim

> A Core-owned, privacy-minimized ledger can distinguish authorized
> eligibility, selection, disclosure, acknowledgement, host-observed artifact
> dependence, verified outcome, and experimentally estimated effect without
> upgrading any observational event into causal evidence or truth authority.

The claimed contribution is not tracing. It is an **intervention-ready,
authority-preserving use contract** in which every causal grade has an explicit
evidence threshold, every private dependency is purge-addressable, and
unauthorized candidates cannot enter selection, diagnostics, timing classes, or
learning updates.

Novelty confidence: **medium**.

### 3.2 Closest prior art

- [MemTrace](https://arxiv.org/abs/2605.28732) turns memory pipelines into
  executable evolution graphs and traces failures through operation subgraphs.
- [MemLineage](https://arxiv.org/abs/2605.14421) combines cryptographic
  provenance with a weighted derivation DAG and gates sensitive actions based
  on untrusted ancestry.
- [Fine-Mem](https://aclanthology.org/2026.acl-long.900/) anchors reward
  attribution to memory items used as evidence and supplies step-level rewards.
- [OpenTelemetry tracing](https://opentelemetry.io/docs/concepts/signals/traces/)
  supplies the conventional span/event vocabulary that an implementation
  should reuse where it fits.

These systems occupy tracing, derivation lineage, and evidence-anchored credit.
They do not establish the complete ATC-specific lifecycle distinction among
authorized candidate, disclosed memory, host-observed use, verified outcome,
controlled intervention, correction, and purge.

### 3.3 What ATC uniquely contributes

1. Core applies authorization, time, lifecycle, and applicability before a
   record can obtain a use edge.
2. The ledger records IDs, versions, role codes, keyed commitments, and bounded
   metrics by default, not prompts, personal payloads, outputs, or hidden
   reasoning.
3. Causal language is mechanically limited to randomized or explicitly modeled
   intervention receipts.
4. Correction and purge can invalidate or remove contribution rows and rebuild
   every statistic derived from them.
5. CAOS makes forbidden influence, currentness, prerequisites, cost, and stale
   checkpoint crossing part of the outcome, not optional audit fields.

### 3.4 State machine

```text
PLANNED
  -> ELIGIBILITY_RESOLVED
  -> COMPILED
  -> ISSUED
  -> ACKNOWLEDGED
  -> ARTIFACT_OBSERVED
  -> OUTCOME_VERIFIED
  -> CLOSED

any nonterminal state -> INVALIDATED
OUTCOME_VERIFIED -> INTERVENTION_LINKED
any retained state -> REDACTED | PURGED
```

`ARTIFACT_OBSERVED` means a registered host can bind selected memory IDs to an
observable artifact or action envelope. It does not mean the model internally
used them. `INTERVENTION_LINKED` requires an M4 experiment identifier and a
predeclared estimator.

### 3.5 Data contracts

```text
MemoryUseEnvelope
  use_id
  principal_view_generation
  canonical_snapshot_id
  request_commitment
  task_class
  target_fingerprint
  authorized_candidate_id_versions[]
  applicable_role_by_id[]
  selected_id_versions[]
  compiler_and_policy_versions
  rendered_artifact_commitment
  disclosure_fields_and_tokens
  working_checkpoint_id?
  issued_at

MemoryUseEvidence
  use_id
  evidence_grade:
    supplied
    acknowledged
    host_observed_dependency
    verified_outcome_association
    randomized_effect_estimate
  host_artifact_commitment?
  outcome_receipt_id?
  experiment_assignment_id?
  bounded_reason_codes[]

OutcomeReceipt
  outcome_id
  use_id
  oracle_version
  task_success
  currentness_pass
  forbidden_influence_pass
  prerequisite_pass
  budget_pass
  stale_checkpoint_pass
  caos
  observed_at
```

### 3.6 Threat and failure model

- A client falsely reports that memory was used.
- A host binds the wrong artifact or observes only part of a tool action.
- Correct outcomes occur without memory because of pretraining or task leakage.
- Memory changes the path but not the final outcome.
- Outcome labels arrive late, are wrong, or are selected after seeing results.
- Ledger fields reveal private candidate counts, source activity, or task
  identity.
- Aggregate utility retains influence after a source correction or purge.
- High logging volume becomes the product's largest private-data surface.

### 3.7 Cheapest decisive experiment

Use a deterministic synthetic tool agent with 40 paired episodes:

- ten where one applicable record is required;
- ten where an authorized but inapplicable record is a distractor;
- ten where the selected record is redundant with task input; and
- ten where a memory is harmful.

The host exposes exact artifact and action commitments. Run no-memory, supplied,
acknowledged-only, and controlled omission arms. The decisive endpoint is exact
grade classification plus CAOS agreement with the intervention oracle. No LLM
is required for the contract test; a small stochastic reader may be added only
after the exact suite passes.

### 3.8 Ablations

- collapse all evidence grades into `used`;
- remove target and artifact commitments;
- omit the authorized candidate-set commitment;
- record only successful outcomes;
- retain aggregate utility without source-version dependencies; and
- allow client self-report to create causal credit.

### 3.9 Falsifier

The mechanism is falsified if an evaluator cannot distinguish supplied,
host-observed, and intervention-estimated influence from ledger data, or if an
unauthorized/inapplicable canary changes any ledger-visible candidate count,
reason code, timing class, or learned statistic.

### 3.10 Kill rule

Kill the standalone causal-ledger claim if controlled trials still require raw
prompts or hidden reasoning to classify effect, or if the ledger cannot be
purged and rebuilt deterministically. Retain a smaller operational audit log if
it remains useful for debugging.

## 4. M2 — Sealed Projection Minimal Compiler

### 4.1 Exact new claim

> For a finite declared candidate and obligation set, ATC can compile a
> 1-deletion-minimal context from only the current authorized-and-applicable
> projection, while producing identical semantic output, closed reason codes,
> cursor behavior, timing class, and learning inputs for vaults that differ
> only outside that sealed projection.

This combines two independently testable properties:

1. **projection non-interference** before relevance; and
2. **local deletion minimality** after deterministic obligation closure.

It does not claim the globally smallest prompt or universally optimal
disclosure.

Novelty confidence: **medium** for the full non-interference and minimality
contract; **none** for context compilation alone.

### 4.2 Closest prior art

- [RAMPART](https://arxiv.org/abs/2606.04628) compiles context from a
  permissioned block registry under ordering, inclusion, gating, eviction, and
  rollback policy.
- [MemGate](https://arxiv.org/abs/2606.06054) treats memory search as a trust
  boundary and applies task-conditioned admission to semantically retrieved
  candidates.
- [Causal Memory Intervention](https://arxiv.org/abs/2605.17641) selects
  memories by controlled usefulness rather than similarity.
- [Decision-Aware Memory Cards](https://arxiv.org/abs/2606.08151) scores
  evidence by action shift, outcome uplift, necessity, and negative-transfer
  risk before budgeted packing.
- ATC's existing Retrieval V3 and Consequence-Closed Context already specify
  authorization-first selection, deterministic closure, and bounded local
  deletion.

The new work is therefore a sharper conformance property and experiment, not a
new compiler metaphor.

### 4.3 What ATC uniquely contributes

1. A sealed Core projection is produced before lexical, dense, graph, learned,
   or diagnostic work.
2. Applicability is typed by role: evidence, current claim, constraint,
   hypothesis, procedure, warning, working dependency, or inapplicable.
3. Core rereads selected current versions before rendering.
4. Mandatory exceptions, prerequisites, higher-authority corrections, and
   conflicts cannot be removed by minimization.
5. A minimality receipt records every tested deletion without exposing hidden
   unauthorized candidates.
6. A paired-vault oracle tests non-interference beyond returned text.

### 4.4 State machine

```text
REQUESTED
  -> AUTHORIZED_TEMPORAL_PROJECTION_SEALED
  -> ROLE_AND_APPLICABILITY_GATED
  -> DETERMINISTIC_CLOSURE
  -> BUDGET_FEASIBLE
  -> DELETE_AND_RECOMPILE
  -> CURRENT_VERSION_REREAD
  -> ISSUED

any state -> ABSTAIN
any state after sealing -> RETRY_ON_GENERATION_CHANGE
```

### 4.5 Data contracts

```text
ContextNeed
  principal
  task_class
  domain
  purpose_provenance
  requested_scopes[]
  required_obligation_ids[]
  target_capabilities
  temporal_instant
  character_or_token_budget

AdmittedContextItem
  record_id
  version
  role
  applicability_basis_code
  required_coverage_ids[]
  allowed_fields[]
  dependency_ids[]
  sensitivity_class

CompilationReceipt
  sealed_projection_generation
  policy_and_applicability_versions
  selected_id_versions[]
  mandatory_closure_edges[]
  tested_deletions[]
  rejected_deletion_reason_codes[]
  disclosure_fields_and_tokens
  semantic_output_digest
  timing_class
```

### 4.6 Threat and failure model

- Applicability becomes a learned authorization bypass.
- A preference is treated as evidence about the world.
- A procedure from one domain is admitted into another.
- Candidate counts, timing, reason codes, cursors, or cache behavior reveal an
  unauthorized record.
- Deletion minimization removes an exception or prerequisite whose need becomes
  visible only jointly.
- Current-state reread races with a correction.
- A context set is minimal for the compiler's proxy but insufficient for the
  actual task.
- Minimality search causes unacceptable latency.

### 4.7 Cheapest decisive experiment

Generate 1,000 pairs of symbolic vaults. Each pair has identical authorized and
applicable records; one member adds unauthorized, deleted, purged,
out-of-scope, or authorized-but-inapplicable canaries. Enumerate finite context
sets of at most 12 items and obligations of at most 4 items.

Pass only if:

- semantic output, reason codes, cursor transition, timing class, and learning
  input are identical within each pair;
- every issued set passes exact obligation coverage;
- removing any discretionary selected item and recompiling makes the result
  infeasible or lexicographically worse; and
- the compiler agrees with exhaustive optimum on small cases or reports its
  exact gap.

### 4.8 Ablations

- authorization after retrieval;
- authorization before retrieval but applicability after ranking;
- no epistemic role;
- top-\(k\) packing without set closure;
- no current-version reread;
- no delete-and-recompile pass; and
- per-request disclosure only, without cumulative disclosure state.

### 4.9 Falsifier

One paired-vault canary that changes any protected observable falsifies the
non-interference claim. One removable selected item in an exhaustive finite
case falsifies the local minimality claim.

### 4.10 Kill rule

Kill generalized minimal compilation if it improves tokens but not CAOS or
minimum disclosure against Retrieval V3 and a strong long-context baseline.
Retain the sealed authorization/applicability projection even if minimization
is killed.

## 5. M3 — Record-Influence Barrier Closure

### 5.1 Exact new claim

> After a correction, deletion, purge, policy restriction, or source
> invalidation, Core can publish a principal-scoped barrier before any affected
> local descendant is used again; it can then withdraw, rebuild, validate, and
> republish only support-closed successors, and after purge retain only a
> content-free generation barrier that prevents stale revalidation.

This is **not** a claim to have invented barrier-first cascade repair.

Novelty confidence: **low**. The mechanism is still a foundational product
requirement.

### 5.2 Closest prior art

- [MEMOREPAIR](https://arxiv.org/abs/2605.07242) is the closest work. It
  withdraws invalid descendants before repair and restricts republication to
  validated predecessor-closed successors.
- [MemLineage](https://arxiv.org/abs/2605.14421) propagates untrusted ancestry
  through a weighted derivation DAG for action enforcement.
- [Deployment-Time Memorization](https://arxiv.org/abs/2606.10062) measures
  forgetting residue and reports that raw-only deletion can leave derived
  summaries recoverable.
- [PLACEMEM](https://arxiv.org/abs/2607.04089) uses versioned capsules and
  cascading invalidation over live serving backends.
- Conventional data lineage, incremental view maintenance, and build systems
  already establish dependency-directed invalidation and rebuild.

### 5.3 What ATC uniquely contributes

The proposed ATC distinction is a lifecycle composition:

1. correction, reversible deletion, source deletion, permission restriction,
   and purge remain distinct causes with distinct postconditions;
2. canonical truth remains in Core, while every descendant is discardable;
3. the closure boundary includes summaries, relations, embeddings, indexes,
   caches, checkpoints, issued capsules, procedure bonds, outcome statistics,
   and experiment ledgers;
4. unauthorized state never enters repair selection;
5. purge may erase record-specific edges after closure while a content-free
   principal generation prevents old disconnected artifacts from validating;
6. live host state receives repair obligations only up to its declared
   capability level.

### 5.4 State machine

```text
ACTIVE
  -> BARRIER_COMMITTED
  -> AFFECTED_DESCENDANTS_WITHDRAWN
  -> REPAIR_PLANNED
  -> REBUILDING
  -> VALIDATED
  -> REPUBLISHED

BARRIER_COMMITTED -> TOMBSTONED
BARRIER_COMMITTED -> PURGE_ERASING
PURGE_ERASING -> PURGE_VERIFIED
any repair state -> FAILED_CLOSED
```

No affected artifact may transition to `REPUBLISHED` unless all retained
private predecessors are active, current, authorized for the artifact, and
validated under the current compiler/interface.

### 5.5 Data contracts

```text
InfluenceEdge
  predecessor_kind_and_id
  predecessor_version
  successor_kind_and_id
  successor_version
  derivation_operator
  compiler_or_evaluator_version
  influence_class:
    content
    selection
    negative_decision
    statistic
    working_state
    issued_intervention

InfluenceBarrier
  barrier_id
  principal_view_generation
  cause_kind
  cause_commitment
  committed_at
  minimum_permitted_generation

ClosureReceipt
  barrier_id
  inventoried_artifact_classes[]
  withdrawn_artifact_ids[]
  rebuilt_artifact_ids[]
  retained_support_roots[]
  orphan_scan_result
  storage_boundary_digest
  backup_or_key_erasure_status
  residue_count
  verifier_version
```

### 5.6 Threat and failure model

- Missing influence edges omit a derived artifact.
- Negative-decision or aggregate-statistic influence is not recorded.
- Repair republishes a descendant before all predecessors are valid.
- An adapter cache or working checkpoint is outside the inventory.
- Correction storms cause excessive fan-out or indefinite fail-closed state.
- Purge destroys record edges too early, making closure unverifiable.
- Retaining lineage after purge itself retains sensitive information.
- Backups, journals, temporary files, or orphaned pages remain decryptable.
- A disconnected client presents a pre-purge artifact after record-specific
  lineage has been erased.

### 5.7 Cheapest decisive experiment

Build a symbolic DAG generator with 10 artifact classes, mixed positive and
negative dependencies, shared aggregates, disconnected working checkpoints,
and injected missing-edge faults. For each source event:

1. commit a barrier;
2. assert that all oracle descendants become unavailable before repair;
3. rebuild from retained sources in randomized topological schedules;
4. verify identical successor semantics across schedules;
5. purge source identity and payload;
6. attempt stale revalidation using the old principal generation.

The decisive result is zero exposure between barrier and validated
republication and zero reachable attributable artifact after purge, against the
complete generator oracle.

### 5.8 Ablations

- repair before withdrawal;
- direct-edge traversal without transitive closure;
- content edges only, excluding negative decisions and statistics;
- rebuild-all without a repair planner;
- record-specific tombstone without a principal generation;
- principal generation without artifact inventory; and
- purge raw records only.

### 5.9 Falsifier

Any oracle descendant that remains usable after barrier commit, any successor
published with an invalid predecessor, or any decryptable attributable residue
inside the declared boundary falsifies closure.

### 5.10 Kill rule

Do not kill the safety requirement. Kill ATC-specific repair optimization if
full deterministic rebuild is noninferior on availability, CAOS, and cost at
the intended scale. Adopt the simpler rebuild path while retaining barriers,
inventory, and purge verification.

## 6. M4 — Budgeted Randomized Context Attribution

### 6.1 Exact new claim

> On reversible synthetic or replayable tasks, ATC can estimate the causal
> contribution of a current authorized-and-applicable memory item or small
> coalition to CAOS by randomized omission, substitution, or timing
> intervention under paired task, model, seed, tool, context, and cost budgets,
> without randomizing hard obligations or treating observational receipts as
> causal.

The estimand concerns the effect of **supplying a memory intervention** on an
observable outcome. It does not identify hidden model reasoning or open-world
causes.

Novelty confidence: **medium-low** for counterfactual replay; **medium** for the
authority-, lifecycle-, and CAOS-constrained protocol.

### 6.2 Closest prior art

- [Causal Memory Intervention](https://arxiv.org/abs/2605.17641) performs
  controlled memory interventions to select useful and suppress harmful
  memories.
- [Remember When It Matters](https://arxiv.org/abs/2607.08716) treats memory
  as selective active intervention, models behavioral state decay, and compares
  remind-or-silent behavior against passive, always-on, advisor, and retrieval
  controls.
- [Causal Agent Replay](https://arxiv.org/abs/2606.08275) models runs as a
  structural causal model, re-executes after step interventions, and estimates
  single-step and Shapley effects.
- [Decision-Aware Memory Cards](https://arxiv.org/abs/2606.08151) uses
  counterfactual-inspired action shift, outcome uplift, necessity, and
  negative-transfer scores.
- [Controllable Memory Usage](https://aclanthology.org/2026.acl-long.670/)
  measures and controls behavioral memory dependence.
- [Fine-Mem](https://aclanthology.org/2026.acl-long.900/) attributes global
  rewards to evidence-anchored memory operations.

### 6.3 What ATC uniquely contributes

1. Experimental arms are drawn only after sealed authorization and
   applicability projection.
2. Mandatory safety constraints are fixed across arms and never omitted.
3. Treatment identity binds exact record versions, compiler, target, and
   working checkpoint.
4. CAOS penalizes stale, unauthorized, over-budget, and prerequisite-violating
   outcomes even when task accuracy improves.
5. Harm, redundancy, synergy, disclosure, and cost are first-class effects.
6. Trial rows and aggregates participate in correction and purge closure.

### 6.4 State machine

```text
PREREGISTERED
  -> ELIGIBLE_SET_FROZEN
  -> ASSIGNED
  -> ISSUED
  -> OUTCOME_OBSERVED
  -> ORACLE_VERIFIED
  -> ESTIMATED
  -> CLOSED

any state -> CENSORED_WITH_REASON
any state before issue -> CANCELLED_BY_CORRECTION
ESTIMATED -> INVALIDATED_BY_SOURCE_CHANGE
```

### 6.5 Data contracts

```text
MemoryInterventionAssignment
  experiment_id
  assignment_id
  episode_id
  eligible_set_digest
  fixed_mandatory_ids[]
  treatment:
    include
    omit
    substitute_current
    substitute_stale_negative_control
    delay_until_checkpoint
  treated_id_versions[]
  paired_seed
  target_and_budget_fingerprint
  preregistration_digest

MemoryEffectEstimate
  estimand
  treatment_and_control_counts
  caos_risk_difference
  task_success_difference
  forbidden_influence_difference
  disclosure_difference
  cost_difference
  uncertainty_interval
  estimator_version
  interference_and_missingness_notes
```

### 6.6 Threat and failure model

- Random omission removes a mandatory safety fact.
- Model nondeterminism overwhelms the effect.
- Tool or environment state differs across paired runs.
- Treatment leaks through prompt length, position, cache, or timing.
- One memory interacts with another, invalidating single-item attribution.
- Post-treatment correction changes the source state.
- Selective failed calls bias the result.
- Replaying a real irreversible action causes harm.
- A Shapley-style estimator becomes too expensive or rhetorically overprecise.

### 6.7 Cheapest decisive experiment

Use 60 deterministic or seeded symbolic episodes with planted memory effects:
helpful, harmful, redundant, and synergistic pairs. Freeze mandatory constraints
and randomize only discretionary items. First validate estimator recovery on a
deterministic policy. Then use one local or fixed hosted reader with paired
seeds and synthetic tool outcomes.

Pass if the estimator:

- recovers the sign of every planted main effect;
- identifies pair synergy within its declared tolerance;
- never assigns benefit to an unauthorized or inapplicable canary;
- counts every failed/retried run; and
- reports uncertainty wide enough to contain the planted effect at the
  preregistered rate.

### 6.8 Ablations

- observational outcome association only;
- unpaired seeds;
- accuracy instead of CAOS;
- randomized hard obligations;
- no disclosure or cost effect;
- single-item leave-one-out without interaction tests; and
- model-judge labels instead of programmatic outcomes.

### 6.9 Falsifier

Failure to recover planted effect signs or repeated sign reversals across paired
seeds falsifies the estimator for that task class. Any treatment assignment
that omits a mandatory constraint or exposes an unauthorized item falsifies the
protocol.

### 6.10 Kill rule

Kill online or product-facing attribution if stable estimates require more
calls than their expected product value, if interference cannot be bounded, or
if host/environment replay cannot be made safe. Retain offline randomized
Memory Lab attribution for synthetic tasks.

## 7. M5 — Outcome-Labeled Experience Quarantine

### 7.1 Exact new claim

> Keeping observable experiences in quarantine until a promotion bond survives
> recurrence, counterexample, wrong-feedback repair, false-transfer, easy-task,
> and purge challenges will reduce repeated failures without increasing false
> procedure transfer relative to raw-trajectory retrieval and immediate
> procedure distillation.

A **promotion bond** is an inspectable dependency and evidence package, not a
confidence score. It can fail, expire, or be revoked.

Novelty confidence: **medium** for the complete gated promotion and repair
contract; **none** for outcome-labeled experience or procedure memory alone.

### 7.2 Closest prior art

- [How Memory Management Impacts LLM Agents](https://aclanthology.org/2026.acl-long.27/)
  documents experience-following, error propagation, misaligned replay, and the
  value of later task evaluations as experience-quality labels.
- ReasoningBank distills reusable strategies from successful and failed
  trajectories.
- [Fine-Mem](https://aclanthology.org/2026.acl-long.900/) supplies fine-grained
  memory-operation feedback.
- [Memory as a Controlled Process](https://arxiv.org/abs/2607.13591) learns
  when and how much memory to retrieve, inject, consolidate, or forget from
  task feedback.
- [AgentTether](https://arxiv.org/abs/2607.06273) carries fixed and unresolved
  repair state across reruns and applies guarded intervention.

### 7.3 What ATC uniquely contributes

1. Experience truth is limited to observable action, environment, result, and
   evaluator envelopes.
2. Agent self-rating and hidden reasoning cannot promote a procedure.
3. A promotion bond must contain supporting outcomes, counterexamples,
   explicit preconditions, evaluator versions, repair evidence, and exact
   dependencies.
4. Procedures remain derived and cannot alter canonical truth, permission, or
   behavioral force.
5. Correction or purge of an experience invalidates the bond and every
   procedure, rank statistic, and cache derived from it.
6. CAOS and negative-transfer tests decide promotion, not apparent success.

### 7.4 State machine

```text
CAPTURED
  -> QUARANTINED
  -> OUTCOME_VERIFIED
  -> REPLAY_ELIGIBLE
  -> PROCEDURE_PROPOSED
  -> CHALLENGE_PENDING
  -> SHADOW
  -> PROMOTED

any state -> REJECTED
SHADOW | PROMOTED -> REPAIR_REQUIRED
REPAIR_REQUIRED -> SHADOW | RETIRED
any retained state -> PURGED
```

### 7.5 Data contracts

```text
ExperienceEnvelope
  experience_id
  task_and_environment_class
  observable_initial_state
  action_summary
  tool_result_commitments[]
  observable_final_state
  external_outcome
  evaluator_and_oracle_version
  cost_and_latency
  source_dependencies[]
  self_report_quarantined: true

ProcedurePromotionBond
  procedure_candidate_id
  compact_tactic
  explicit_preconditions[]
  explicit_non_applicability[]
  supporting_experience_ids[]
  contradicting_or_counterexample_ids[]
  recurrence_result
  wrong_feedback_repair_result
  matched_task_effect
  mismatched_task_effect
  easy_task_regression
  purge_rebuild_result
  challenge_suite_digest
  bond_expiry_or_review_trigger
```

### 7.6 Threat and failure model

- A superficially successful trajectory contains unsafe or unnecessary steps.
- Outcome labels are delayed, noisy, poisoned, or self-generated.
- Task similarity masks a failed precondition.
- A procedure overfits benchmark or source-task wording.
- Counterexamples are missing because the system never explores alternatives.
- Repeated success reflects one environment or model build.
- A compact tactic retains secret data or hidden reasoning.
- Procedure ranking remains biased after a supporting experience is corrected.

### 7.7 Cheapest decisive experiment

Create 48 paired synthetic tasks in 12 families. Each family includes:

- two successful experiences with one shared reusable structure;
- one failure with the same surface form;
- one mismatched task where the tactic is harmful;
- one deliberately flipped outcome label; and
- one later correction to a supporting experience.

Compare raw trajectories, summaries, immediate procedures, quarantined
experience retrieval, and bonded procedure shadowing. Primary metrics are
repeated-failure reduction, false transfer, easy-task regression, repair after
wrong feedback, and purge residue.

### 7.8 Ablations

- self-evaluation accepted as outcome;
- one success sufficient for promotion;
- no counterexample requirement;
- similarity-only preconditions;
- no wrong-feedback repair;
- no easy-task control;
- no promotion-bond expiry; and
- procedure retained after source correction.

### 7.9 Falsifier

The claim is falsified if bonded procedures do not beat quarantined raw
experience on repeated-failure reduction, or if their false-transfer rate is
more than one point worse than the strongest simpler baseline.

### 7.10 Kill rule

Kill procedure promotion if raw outcome-labeled experience or a static task
note is noninferior, if repair after wrong feedback is unreliable, or if any
procedure cannot be deterministically retired and purged. Keep the experience
ledger even if procedures are killed.

## 8. M6 — Portable Working-State Three-Way Repair

### 8.1 Exact new claim

> A target-neutral working checkpoint can be repaired across agents by a typed
> three-way comparison of its acknowledged base, the agent's bounded local
> delta, and current Core state; the repair preserves valid open commitments,
> refreshes changed dependencies, drops unauthorized or purged state, and
> exposes conflicts instead of silently replaying stale prose.

The mechanism repairs **logical working state**, not hidden model state. It
requires no claim that two providers share internal representations.

Novelty confidence: **medium-high** within the reviewed scope.

### 8.2 Closest prior art

- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
  separates thread-scoped checkpoints from cross-thread stores and supports
  resume, time travel, and fault tolerance.
- [Portable Agent Memory](https://arxiv.org/abs/2605.11032) defines structured
  cross-model memory transfer with Merkle-DAG provenance, capability-based
  disclosure, and injection-resistant rehydration.
- [AgentTether](https://arxiv.org/abs/2607.06273) carries cross-iteration repair
  memory containing fixed and unresolved guidance.
- ATC's current consequence research already specifies patch, clean rebase, or
  stop for stale issued context.

The remaining gap is an authority-aware three-way repair contract for
cross-agent task state, not checkpoint persistence or repair guidance alone.

### 8.3 What ATC uniquely contributes

1. Working state is not canonical personal truth.
2. Every field declares its authority, source dependency, target portability,
   and repair policy.
3. Core compares:
   - acknowledged base checkpoint `B`;
   - bounded host-observed local delta `L`; and
   - current authorized canonical and derived state `C`.
4. Repair operations are typed: carry, refresh, recompute, drop, conflict,
   compensate, or stop.
5. Purged or newly unauthorized state is dropped without revealing its content
   to the target.
6. A target capability downgrade is explicit and can force recomputation or
   stop.

### 8.4 State machine

```text
CHECKPOINT_ACTIVE
  -> EXPORTED
  -> TARGET_CAPABILITIES_VALIDATED
  -> BASE_ACKNOWLEDGED
  -> LOCAL_DELTA_BOUND
  -> CURRENT_CORE_SNAPSHOT_BOUND
  -> THREE_WAY_CLASSIFIED
  -> PATCHED | CLEAN_REBASE_REQUIRED | CONFLICTED | STOPPED
PATCHED -> REHYDRATED
REHYDRATED -> NEXT_ACTION_VERIFIED
```

### 8.5 Data contracts

```text
WorkingCheckpoint
  checkpoint_id
  parent_checkpoint_id?
  principal_view_generation
  task_identity
  goal_and_success_predicate
  open_commitments[]
  satisfied_commitments[]
  known_unknowns[]
  artifact_handles_and_commitments[]
  pending_effect_handles[]
  selected_memory_id_versions[]
  negative_decision_frontiers[]
  target_specific_ephemera[]
  source_and_policy_dependencies[]
  capability_requirements[]
  lease

WorkingDelta
  base_checkpoint_id
  host_id
  observed_completed_steps[]
  new_artifact_commitments[]
  changed_open_commitments[]
  environment_observations[]
  abandoned_or_failed_steps[]
  bounded_client_assertions[]

WorkingRepairPlan
  base_checkpoint_id
  replacement_checkpoint_id
  operations[]:
    carry
    refresh_from_current
    recompute
    drop_without_disclosure
    mark_conflict
    require_compensation
    stop
  invalidated_dependency_ids[]
  target_downgrades[]
  required_next_action_constraints[]
  semantic_parity_digest
```

### 8.6 Three-way classification rules

For each logical field:

| Base `B` | Local delta `L` | Current Core `C` | Repair |
|---|---|---|---|
| unchanged | unchanged | unchanged | carry |
| present | progress observed | unchanged | apply bounded local progress |
| present | unchanged | corrected | refresh or recompute |
| present | contradictory progress | corrected | conflict; clean rebase or stop |
| present | any | purged/unauthorized | drop without content disclosure |
| absent | new client assertion | absent | keep as working proposal only |
| present | completed effect | corrected later | mark possibly affected; never pretend reversal |
| target-specific | any | target changed | lower, recompute, or explicit downgrade |

### 8.7 Threat and failure model

- The client fabricates progress or omits a completed effect.
- Provider prose is mistaken for logical state.
- A target cannot represent a source checkpoint field.
- A correction races export or import.
- A dropped private field leaks through reason codes or patch size.
- Local progress was based on a stale premise and should not be carried.
- Two agents change the same open commitment concurrently.
- The repair plan claims semantic parity despite target capability loss.
- A completed external effect cannot be reversed.

### 8.8 Cheapest decisive experiment

Use 60 symbolic task traces with 8-20 checkpoint fields. Cross:

- restart, compaction, target change, and target downgrade;
- no correction, value correction, scope narrowing, deletion, and purge;
- honest progress, conflicting progress, missing telemetry, and completed
  irreversible effect;
- exact and lossy target capabilities.

Compare raw recent turns, a static task note, replay of the old checkpoint,
two-way refresh, and three-way repair. Require exact repaired logical state,
correct next action, zero stale resume, zero disclosure of dropped fields, and
explicit downgrade when semantic parity is impossible.

### 8.9 Ablations

- checkpoint replay without current-state comparison;
- two-way `B` versus `C` repair without local delta;
- two-way `L` versus `C` repair without acknowledged base;
- untyped prose checkpoint;
- no negative-decision dependencies;
- no target capability fingerprint;
- patch-only repair with no clean-rebase/stop state; and
- client assertion treated as canonical truth.

### 8.10 Falsifier

Any stale dependency carried into the next action, any purged/unauthorized field
disclosed during repair, or any silent parity claim after a lossy target
downgrade falsifies the protocol property.

### 8.11 Kill rule

Kill general cross-agent repair if a static task note or clean restart is
noninferior on correct next action within the same budget, or if fewer than 95%
of target-neutral fields survive exact repair. Narrow to same-host restart if
cross-provider semantics are not stable.

## 9. Shared experiment architecture

### 9.1 Dependency order

```text
M2 sealed projection
  -> M1 use ledger
      -> M4 randomized attribution
      -> M5 experience quarantine and promotion bonds

M3 barrier closure applies to M1, M2, M4, M5, and M6 artifacts

M6 working-state repair consumes M2 projections and emits M1 receipts
```

M2 and M3 can begin independently. M4 must not begin before M1 can distinguish
assignment, supply, acknowledgement, outcome, and invalidation. M5 may capture
quarantined experiences before M4, but procedure promotion must wait for
reliable outcomes and closure.

### 9.2 Shared controls

Every mechanism must compare against:

- no memory;
- bounded full authorized history;
- static profile or task note;
- append-only event log plus exact/lexical search;
- current Retrieval V3;
- the strongest immediately simpler mechanism; and
- the closest prior-art mechanism as a logical baseline when executable
  reproduction is later authorized.

No competitor receives ATC lifecycle behavior and then earns credit for it.
Unsupported behavior remains `unsupported`.

### 9.3 Shared hard failures

One occurrence is a mechanism failure:

- unauthorized or purged influence on output, timing class, diagnostics,
  cursor, interruption, or learned state;
- an imported or inferred item gaining truth, permission, or hard-force
  authority;
- a stale protected checkpoint crossing after invalidation in a conforming
  trace;
- a missing dependency against the finite oracle;
- attributable residue inside the declared purge boundary;
- raw personal content or hidden reasoning in operational research logs; or
- a post-hoc change to the endpoint, exclusion, or kill rule after seeing the
  confirmatory result.

### 9.4 Shared reporting

Report CAOS and every conjunct separately. Also report:

- task success and correct next action;
- authorized-but-inapplicable admission;
- set sufficiency and deletion-minimality;
- exposure fields and tokens;
- helpful, harmful, redundant, and synergistic memory effects;
- stale resume and correction convergence;
- procedure false transfer and repair;
- dependency completeness, barrier-to-withdraw latency, rebuild cost, and
  residue;
- p50/p95/p99 local latency, storage, calls, tokens, and monetary cost; and
- exact one-sided confidence bounds for zero-observed-failure gates.

## 10. Three mechanisms for immediate testing

### 10.1 Sealed Projection Minimal Compiler

Why now: it is Core-only, cheap, directly tests the revised product claim, and
can falsify both unauthorized influence and unnecessary disclosure before any
learned or host-dependent work. Even if local minimality fails, the sealed
authorization/applicability projection remains valuable.

First decisive result: 1,000 paired-vault traces with zero protected-observable
differences and exact finite-set minimality.

### 10.2 Record-Influence Barrier Closure

Why now: every new summary, checkpoint, use statistic, procedure, or learned
artifact increases purge risk. The mechanism has low novelty confidence because
of MEMOREPAIR, but it is a prerequisite for responsibly testing the other five
mechanisms.

First decisive result: a symbolic multi-tier DAG in which every affected
descendant is unavailable after the barrier and before validated republication,
with zero residue after purge.

### 10.3 Portable Working-State Three-Way Repair

Why now: it targets a visible product gap, has the strongest differentiation
confidence in this portfolio, and needs only a logical L1-style checkpoint
harness rather than effect gating or provider modification.

First decisive result: exact resume plus correct next action across compaction,
target change, correction, deletion, and purge, beating raw turns and static
task notes without a stale or unauthorized carry.

M1 should be implemented as shared experiment instrumentation alongside these
three, but it should not delay the first exact fixtures.

## 11. Primary-source prior-art ledger

| Source | Occupied area | Consequence for ATC claim |
|---|---|---|
| [Remember When It Matters](https://arxiv.org/abs/2607.08716) | Memory as selective active intervention, behavioral state decay, and selective reminders over passive/always-on controls | Do not claim generic checkpoint activation, reminder selection, or active intervention; claim only authority/applicability/force bounds plus lifecycle closure and receipts |
| [Causal Memory Intervention](https://arxiv.org/abs/2605.17641) | Controlled intervention-based memory usefulness selection | Do not claim first causal memory selection |
| [Causal Agent Replay](https://arxiv.org/abs/2606.08275) | Counterfactual step replay and budgeted Shapley attribution | Limit M4 to authority- and CAOS-constrained memory supply effects |
| [Decision-Aware Memory Cards](https://arxiv.org/abs/2606.08151) | Action shift, outcome uplift, necessity, negative transfer, budgeted packing | Do not claim first decision-aware or counterfactual context packing |
| [Fine-Mem](https://aclanthology.org/2026.acl-long.900/) | Evidence-anchored reward attribution to memory operations | M1's distinction must be lifecycle grades and purgeability |
| [MemTrace](https://arxiv.org/abs/2605.28732) | Executable memory-evolution graphs and failure attribution | Do not claim first memory pipeline trace |
| [MemLineage](https://arxiv.org/abs/2605.14421) | Cryptographic provenance, derivation DAG, ancestry-based enforcement | Do not claim first lineage-based memory action gate |
| [MEMOREPAIR](https://arxiv.org/abs/2605.07242) | Barrier-first cascade repair and predecessor-closed republication | Do not claim barrier-first derived-state closure as new |
| [Deployment-Time Memorization](https://arxiv.org/abs/2606.10062) | Extraction, utility, and forgetting residue across derived tiers | Derived residue is an evaluation requirement, not ATC novelty |
| [PLACEMEM](https://arxiv.org/abs/2607.04089) | Versioned capsules and correction-aware cascading invalidation | Narrow live-capsule novelty to ATC authority and checkpoint contracts |
| [RAMPART](https://arxiv.org/abs/2606.04628) | Permissioned block registry and compile-time context transformation | Do not claim first permissioned context compiler |
| [MemGate](https://arxiv.org/abs/2606.06054) | Query-conditioned admission against harmful but similar memory | Applicability gating itself is occupied |
| [Controllable Memory Usage](https://aclanthology.org/2026.acl-long.670/) | Behavioral memory-dependence measurement and user control | Do not claim first explicit memory-influence metric |
| [Experience-Following study](https://aclanthology.org/2026.acl-long.27/) | Error propagation, misaligned replay, later outcomes as quality labels | Outcome labels and quarantine are motivated, not novel alone |
| [Memory as a Controlled Process](https://arxiv.org/abs/2607.13591) | Feedback-driven adaptive retrieval, plan injection, consolidation, forgetting | Do not claim first outcome-adaptive memory policy |
| [AgentTether](https://arxiv.org/abs/2607.06273) | Dependency-aware diagnosis, cross-iteration repair memory, guarded rerun intervention | Narrow M6 to cross-agent authority-aware three-way state repair |
| [Portable Agent Memory](https://arxiv.org/abs/2605.11032) | Structured provenance-bearing cross-model rehydration | Portability and rehydration alone are occupied |
| [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence) | Thread checkpoints, resume, failure recovery, time travel, cross-thread store | Working checkpoints and fault recovery are table stakes |
| [Mem2ActBench](https://aclanthology.org/2026.acl-long.370/) | Memory-grounded tool and parameter use | Outcome/action grounding is the required endpoint, not novelty |

## 12. Final falsification position

The broad Wave 2 program should be narrowed or stopped if, after fixed-budget
comparison:

- long context, append-log search, or a static task note is noninferior on CAOS;
- sealed applicability does not reduce harmful or unnecessary memory use;
- use receipts cannot support reliable controlled interventions without raw
  private traces;
- full rebuild is cheaper and equally available than targeted closure;
- quarantined experience does not reduce repeated failure without false
  transfer;
- three-way working repair does not beat clean restart or static notes; or
- any mechanism requires opaque private gradients, hidden reasoning retention,
  or a non-purgeable second authority.

A negative result should simplify ATC. The product moat is not the number of
memory mechanisms. It is whether a user-owned Core can govern useful context
and its consequences more reliably than simpler memory under correction,
privacy, cost, and purge constraints.
