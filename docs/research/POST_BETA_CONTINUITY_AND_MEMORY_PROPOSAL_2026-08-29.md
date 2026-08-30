# Post-beta continuity and memory proposal

## Measurement-first staged program after the active frontier

| Field | Value |
|---|---|
| Date | August 29, 2026 |
| Status | Draft direction for review; no production, schema, roadmap, release, or marketing authority |
| First decision requested | Accept the whole measurement-first staged program as the post-frontier direction and freeze the corrected Packet A benchmark contract now |
| Scope | Research and evidence work that keeps every proposed mechanism active while sequencing stronger authority and effects behind evidence |
| Current boundary | The integrated capture/retrieval/setup/control/security milestone, replacement candidate, packaged ordinary-use journey, Wave 4 E–G product acceptance, and Phase 2 remain blocking product work |
| Mapped decisions | [ADR-049](../DECISIONS.md#adr-049-retain-closure-and-observable-use-contracts-fill-core-semantics-then-test-prospective-memory) and [ADR-090](../DECISIONS.md#adr-090-adopt-zero-routine-friction-as-the-post-v1-product-direction) |
| Mapped execution plan | [Zero-Friction Platform Execution Plan](../product/ZERO_FRICTION_EXECUTION_PLAN.md), especially [ZF-015](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-015-versioned-working-checkpoints) through [ZF-019](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-019-procedural-memory-gates) |
| Canonical research inputs | [Memory Evaluation Program](ATC_MEMORY_EVALUATION_PROGRAM.md), [Wave 4 Integrated Results](ATC_MEMORY_LAB_WAVE4_RESULTS_2026-07-23.md), and [Memory Lab Governance](ATC_MEMORY_LAB_GOVERNANCE.md) |

This is an evidence-bounded proposal. Current project documents record local
contracts, component work, and coordinator-reproduced research evidence; they
do not establish a general memory product, broad client support, or a causal
benefit for ordinary users. The proposal therefore keeps each idea alive as a
named workstream with a safe first stage, a falsifiable promotion gate, a
stronger next stage, and an explicit kill-or-narrow rule.

This document does not change current release work, accept a new production
feature, assign a stable interface, authorize a migration, authorize data
collection, authorize external access, claim provider support, claim scholarly
novelty, or establish that ATC has solved AI memory. Any accepted part must
enter the normal decision, implementation, review, and evidence process
separately.

## 1. Executive proposal

The post-frontier question is not whether ATC can retrieve more text. It is
whether an authority-bound, lifecycle-aware system can reduce repeated context
work and improve current authorized outcomes without increasing disclosure,
staleness, wrong-project influence, or maintenance burden.

No mechanism is removed from the program. The order is a research sequence:

~~~text
active frontier and product acceptance
  -> Packet A benchmark freeze (parallel, non-displacing)
  -> M1 measurement and outcome spine
  -> versioned working checkpoints and reconciliation
  -> typed applicability and local use-time verification
  -> semantic acknowledgement/challenge
  -> prospective memory at an inert local ceiling
  -> conditional failure memory
  -> verified experience and procedures
  -> consented design-partner evidence
  -> stronger notification, action, and adaptive routing only after their gates
~~~

The sequence is deliberately staged rather than semver-like. The research
stages and workstreams map to the accepted product direction as follows:

| Research stage or workstream | Mechanisms kept active | Product and decision mapping | Initial disposition |
|---|---|---|---|
| Active frontier | Integrated capture/retrieval/setup/control/security milestone; replacement candidate; packaged ordinary-use journey; Wave 4 E–G product acceptance; Phase 2 | [Zero-Friction Execution Plan](../product/ZERO_FRICTION_EXECUTION_PLAN.md), [ADR-090](../DECISIONS.md#adr-090-adopt-zero-routine-friction-as-the-post-v1-product-direction) | Blocking product work; not displaced by this proposal |
| Research Stage A — benchmark and measurement | Packet A, M1 transactions, outcome receipts, continuity-debt vector | [ZF-017](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-017-memory-use-and-outcome-ledger), [ADR-049](../DECISIONS.md#adr-049-retain-closure-and-observable-use-contracts-fill-core-semantics-then-test-prospective-memory) | Packet A may run in parallel; all product behavior remains gated |
| Workstream B — reliable working continuity | Versioned working checkpoints | [ZF-015](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-015-versioned-working-checkpoints), [ADR-090](../DECISIONS.md#adr-090-adopt-zero-routine-friction-as-the-post-v1-product-direction) | Lab/shadow first; no hidden-state claim |
| Workstream C — state reconciliation | Three-way reconciliation and bounded challenges | [ZF-016](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-016-working-state-reconciliation), [ADR-049](../DECISIONS.md#adr-049-retain-closure-and-observable-use-contracts-fill-core-semantics-then-test-prospective-memory) | After checkpoint contracts and measurement |
| Workstream D — applicability | Static typed applicability and local use-time warranties | [ZF-016](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-016-working-state-reconciliation), [ZF-017](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-017-memory-use-and-outcome-ledger), [ADR-049](../DECISIONS.md#adr-049-retain-closure-and-observable-use-contracts-fill-core-semantics-then-test-prospective-memory) | Local-only first; remote verification remains a later live hypothesis |
| Workstream E — memory at the right moment | Prospective memory and notification receipts | [ZF-017](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-017-memory-use-and-outcome-ledger), [ZF-018](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-018-background-consolidation-in-shadow), [ADR-049](../DECISIONS.md#adr-049-retain-closure-and-observable-use-contracts-fill-core-semantics-then-test-prospective-memory) | Inert local reference first; stronger notification/action path remains active |
| Workstream F — conditional failure | Conditional Failure Memory | [ZF-018](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-018-background-consolidation-in-shadow), [ZF-019](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-019-procedural-memory-gates) | Advisory first; later suppression requires L3 synchronous evidence |
| Workstream G — semantic handoff | Semantic acknowledgement/challenge, formerly called Semantic Handoff Checksum | [ZF-015](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-015-versioned-working-checkpoints), [ZF-016](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-016-working-state-reconciliation) | Ephemeral closed-field shadow first |
| Workstream H — verified experience | Outcome-bound experience and procedure candidates | [ZF-018](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-018-background-consolidation-in-shadow), [ZF-019](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-019-procedural-memory-gates), [ADR-049](../DECISIONS.md#adr-049-retain-closure-and-observable-use-contracts-fill-core-semantics-then-test-prospective-memory) | Advisory candidates; no autonomous effects |
| Workstream I — external validation and routing | Design partners and adaptive memory routing | [ADR-090](../DECISIONS.md#adr-090-adopt-zero-routine-friction-as-the-post-v1-product-direction), [ZF-019](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-019-procedural-memory-gates) | Active later stage; no broad claim or integration expansion |

## 2. Active frontier and prerequisite DAG

The active frontier remains the product priority. This proposal must not turn
research activity into a reason to defer or relabel it.

The blocking product work is:

1. The integrated capture/retrieval/setup/control/security milestone, including
   the exact acceptance boundary recorded by the execution plan.
2. A replacement candidate with exact installed-component identity,
   reproducible provenance, security scanning, and the required reassessment.
3. The packaged ordinary-use journey on the accepted Windows and supported
   Ubuntu artifacts: one-time opt-in, normal Claude Code and Codex use, later
   retrieval, correction and forget, restart, clean opt-out, Core outage
   tolerance, and operational-secret refusal without dashboard administration
   or per-turn ATC action.
4. Wave 4 E–G product acceptance, including the remaining product boundary
   between research contracts and live lifecycle support.
5. Phase 2, the continuous end-to-end vertical slice.

Packet A is the sole intentional parallel lane. It may freeze fixtures,
oracles, controls, and the benchmark contract while the frontier proceeds, but
it is non-displacing: it cannot change the frontier, grant product acceptance,
authorize a schema, or make a later workstream live.

The dependency and evidence graph is:

~~~text
[integrated capture/retrieval/setup/control/security milestone]
                         |
                         v
              [replacement candidate and
             packaged ordinary-use journey]
                         |
                         v
                  [Wave 4 E–G product
                       acceptance]
                         |
                         v
                     [Phase 2]
                         |
                         v
 [M1 + checkpoints + reconciliation + typed applicability evidence]
                         |
                         v
 [semantic acknowledgement/challenge + prospective + failure + experience]
                         |
                         v
      [design partners, stronger notification/action, adaptive routing]

[Packet A freeze] ------------------------------------^
       parallel and non-displacing; no product behavior
~~~

In plain terms, the active frontier is required for product progression. The
measurement and continuity contracts are required for interpreting later
benefit. Each stronger effect is required to clear the preceding authority,
lifecycle, applicability, outcome, and safety evidence. Production behavior
follows this DAG; a research label never bypasses it.

## 3. Evidence-bounded governing rules

Every workstream preserves these rules.

1. **Core is the sole authority.** Transactions, checkpoints, warranties,
   procedures, caches, reports, graphs, embeddings, and metrics are Core-owned
   artifacts or bounded observations. None is a parallel truth store.
2. **Imported text is inert untrusted data.** Imported, tool, model, provider,
   and client prose cannot become an instruction, predicate, credential,
   authority claim, or success assertion.
3. **Measurement precedes mechanism.** M1 assignment and outcome observation
   are established before a mechanism receives a stronger influence role.
4. **Checkpoints and receipts remain distinct.** A checkpoint describes the
   declared continuity state and opportunity. A receipt records what was
   supplied, acknowledged, observed, acted on, and what independently observed
   outcome followed.
5. **Unknown and abstention are first-class.** Missing, conflicting,
   unavailable, unverified, unsupported, and not-exercised states are retained.
   They are never silently converted to pass, failure, current, or success.
6. **Authorization and lifecycle precede relevance.** Unauthorized candidates
   are not inspected and filtered later. Correction, ordinary deletion,
   invalidation, retention, and terminal purge close every declared influence.
7. **Strong simple controls come first.** Each stage compares against the
   strongest feasible simpler control under matched model, client, context,
   token, tool, latency, and cost budgets.
8. **No self-attested safety or success.** Clients, models, tools, providers,
   and connectors cannot self-assert safety, currentness, causal use, task
   success, or permission. Evidence must come from Core, a deterministic
   harness, or an independently observed outcome.
9. **No raw or executable payloads.** Raw prompts, commands, imported/tool/
   model/provider prose, credentials, hidden reasoning, arbitrary scripts,
   executable predicates, and executable procedure payloads are prohibited.
10. **Project identity is fail-closed.** Unresolved project identity is
    abstention-only, short-lived, non-linkable, and cannot issue, write,
    reconcile, or join across projects.
11. **Hard failures do not average out.** Unauthorized influence, wrong-project
    context, stale protected action, duplicate execution, correction failure,
    secret persistence, or purge residue stops promotion.
12. **Shadow before authority.** A research stage may issue an advisory or
    inert artifact only at its declared ceiling. It may not silently gain
    notification, suppression, write, or action authority.
13. **Complexity must earn its place.** A losing mechanism is narrowed to the
    smallest useful form; its idea remains in the register and can return only
    through a stronger, preregistered experiment.
14. **Claims track evidence.** Supported client means an exact accepted
    artifact/capability pair. Novelty remains an unreviewed hypothesis pending
    fresh prior-art review and independent challenge.
15. **The current frontier remains binding.** Research packets do not displace
    the integrated milestone, replacement candidate, packaged journey, Wave 4
    E–G acceptance, or Phase 2.

## 4. Co-designed continuity artifacts and the retained M1 contract

### 4.1 Checkpoint state and outcome receipts

A working checkpoint and an outcome receipt solve different questions:

| Artifact | Question answered | Authority/lifecycle role |
|---|---|---|
| Working checkpoint | What declared, observable working state is the next session being offered? | A versioned, dependency-bound projection; never hidden model state or canonical truth |
| Reconciliation artifact | How did the checkpoint compare with current source/project state and new client observations? | A bounded Core decision with current, displaced, uncertain, invalid, new, or unavailable classifications |
| Memory-use transaction | What exact authorized projection was assigned and supplied? | The M1 ledger identity linking record/version/snapshot/policy/principal and dependencies |
| Outcome receipt | What observable action and outcome followed? | Evidence of observed behavior, not a client or model success assertion |

The checkpoint defines the continuity opportunity. The receipts measure what
followed. Co-design means the checkpoint version, dependency binding, and
reconciliation result are available as exact receipt references; it does not
merge their authority or lifecycle.

### 4.2 M1 exactly

The retained M1 sequence is:

~~~text
assigned -> supplied -> acknowledged -> observed_use -> action -> outcome
~~~

The sequence is not shortened or renamed. Each transition has an explicit
status and witness. A missing transition is unknown or abstention, not a
successful use.

Every M1 transaction binds:

- exact record IDs and record versions;
- exact canonical snapshot and project-scope versions;
- exact policy and principal-view generations;
- predecessor transaction or checkpoint identity where one exists;
- a complete dependency binding for selected evidence, projections, policy,
  applicability, and invalidation;
- typed issuer, source, and witness identities;
- verification strength;
- explicit unknown, abstention, unsupported, and not-exercised codes;
- idempotency key and duplicate handling;
- conflict state and resolution reference;
- invalidation, ordinary-deletion, and terminal-purge state; and
- bounded timing, token, and cost classes.

The M1 evidence classes remain distinct:

1. Core or harness assignment;
2. Core or harness supply;
3. client acknowledgement, when available;
4. host-observed use or non-use, when independently observable;
5. a bounded action envelope;
6. a Core, deterministic-harness, or independently observed outcome.

Client, model, tool, provider, and connector statements may be recorded only as
untrusted observations with a typed witness. They cannot promote themselves to
the issuer, prove safety, prove causal use, or declare success.

### 4.3 Closed transaction and receipt shapes

The shapes below are contracts, not production schema authorization.

~~~text
MemoryUseTransaction
  use_id: opaque identifier
  status: assigned | supplied | acknowledged | observed_use | action | outcome
  record_refs: exact record_id + version pairs
  canonical_snapshot: exact snapshot_id + version
  project_scope: exact project_id or unresolved-abstention code
  policy_generation: exact generation
  principal_view_generation: exact generation
  predecessor: exact checkpoint/use identifier or none
  dependency_binding: exact or conservative typed dependency digest
  issuer: Core | harness | typed source adapter
  source: Core | typed client | typed host | deterministic fixture
  witness: Core | harness | independently_observed | untrusted_client_report
  verification_strength: enum
  unknown_or_abstention: enum
  idempotency_key: opaque identifier
  conflict_state: enum + bounded conflict reference
  invalidation_state: enum + exact invalidation reference
  sensitivity_class: S0 | S1 | S2 | S3
  issued_at: bounded time class

ObservableOutcomeReceipt
  outcome_id: opaque identifier
  use_id: exact transaction identifier
  predecessor_action: exact action-envelope identifier
  oracle_id + oracle_version: exact
  outcome_source: Core | harness | independently_observed
  completion_state: enum
  external_result_state: enum
  correction_or_rejection_state: enum
  currentness_pass: true | false | unknown
  forbidden_influence_pass: true | false | unknown
  prerequisite_pass: true | false | unknown
  budget_pass: true | false | unknown
  stale_checkpoint_pass: true | false | unknown
  caos: pass | fail | unknown
  invalidation_state: enum
~~~

No shape above stores a raw prompt, complete rendered context, hidden
reasoning, command text, credential, imported prose, provider prose, or
executable payload.

### 4.4 Checkpoint and reconciliation shapes

~~~text
WorkingCheckpoint
  checkpoint_id + version: opaque identifier + monotonic version
  project_id: exact identifier or unresolved-abstention code
  task_id: bounded task identity
  source_revision: exact source/repository/environment generation
  objective_code: closed user-declared objective code
  completed_step_codes: closed codes + exact artifact references
  active_artifact_refs: exact artifact IDs + revisions
  hypothesis_code: closed tentative code or unknown
  blocker_codes: closed codes
  open_question_codes: closed codes
  next_action_code: closed code, never command text
  do_not_repeat_code: closed code + applicability reference
  expiry: bounded time class
  close_state: open | closed | abandoned | invalidated
  dependency_generation: exact generation
  field_provenance: typed issuer/source/witness for every field

ReconciliationArtifact
  reconciliation_id + version
  checkpoint_ref
  current_source_snapshot_ref
  new_client_observation_ref
  field_classifications: current | displaced | uncertain | invalid | new | unavailable
  challenge_codes
  corrective_delta_codes
  project_resolution: resolved | unresolved_abstention
  dependency_binding
  invalidation_state
  witness and verification strength
~~~

An unresolved project cannot issue a checkpoint, write a record, reconcile
state, or create a cross-project join. A short-lived unresolved observation may
exist only to explain abstention and must be non-linkable.

## 5. Artifact contract matrix

The matrix is the minimum contract for every proposed artifact. The same
artifact row is read across the two tables. All field sets are closed and
typed. Sensitivity classes are:

- S0: public specification or aggregate research result;
- S1: opaque lifecycle metadata;
- S2: authorized project/workspace metadata;
- S3: restricted security or participant metadata.

ACL means the narrowest of Core, the authorized principal, the isolated
harness, and a consented report recipient. No external copy is presumed safe
because it contains only metadata; its destination and deletion path must be
known.

### 5.1 Authority, provenance, and field contract

| Artifact | Owner | Allowed provenance | Closed field types | Sensitivity | ACL |
|---|---|---|---|---|---|
| M1 memory-use transaction | Core or isolated harness | Canonical record/snapshot/policy plus typed host or harness event | Opaque IDs, versions, generations, enums, digests, bounded counts/times | S1–S2 | Core, exact principal view, isolated harness |
| Observable outcome receipt | Core, deterministic harness, or independent observer | Oracle result, bounded action envelope, user correction code, independent observation | Opaque IDs, oracle versions, enums, pass/fail/unknown, bounded metrics | S1–S2 | Core, harness, authorized research reader |
| Working checkpoint | Core-owned projection compiler | User-declared closed codes, current source facts, typed client observation | IDs, revisions, closed codes, enums, digests, expiry, provenance tuples | S2 | Core and authorized project principal |
| Reconciliation artifact | Core | Checkpoint, current snapshot, typed client observation | IDs, field-class enums, challenge/delta codes, dependency digests | S2 | Core and exact project principal |
| Acknowledgement/challenge | Core and cooperating host | Core-issued projection and closed host coverage/uncertainty event | Ephemeral field IDs, coverage codes, uncertainty codes, turn ID, expiry | S1–S2 | Core and same-turn host; no durable client authority |
| Memory warranty | Core | Core-owned descriptor and bounded local verification | Descriptor IDs, typed predicates, digests, snapshot/generation, result enum, expiry | S2–S3 | Core and exact project principal |
| Conditional Failure Memory | Core | Core receipt plus allowlisted host-observed state | Action signature ID, failure enum, state digest, confidence enum, guard codes, expiry | S1–S2 | Core and exact project principal |
| Notification receipt | Core or accepted notification host | Due transaction, target binding, native host acknowledgement | Notification ID, target/recipient IDs, cue IDs, action-force enum, delivery/unknown codes | S1–S2 | Core, exact recipient, accepted host |
| Experience/procedure candidate | Core research compiler | Accepted outcome receipts, applicability evidence, closure evidence | Action signature IDs, precondition/guard codes, verification/rollback IDs, evidence refs | S2–S3 | Core, isolated lab, exact project principal |
| Cache or report | Core-derived disposable view | Rebuilt from canonical artifacts and approved aggregate queries | Digests, counts, category vectors, versions, timing/cost classes | S0–S2 | Core; consented report recipient only |
| Continuity-debt event | Core or blinded evaluator | Controlled omission, independent opportunity oracle, typed episode event | Category enum, opportunity ID, attribution enum, abstention code, dependency refs | S1–S2 | Core and blinded evaluator |

### 5.2 Lifecycle, recovery, and operational contract

| Artifact | Retention | Ordinary deletion | Terminal purge | Dependency inventory | Rebuild | Backup/export/restore | Replication eligibility | Logs | External copies | Cost/support metrics |
|---|---|---|---|---|---|---|---|---|---|---|
| M1 memory-use transaction | Episode-bound and policy-bounded | Withdraw future influence; retain bounded tombstone | Destructive compaction, identity-generation barrier, no inspectable residue | Records, versions, snapshot, policy, principal, predecessor, projections, actions, outcomes | Deterministic replay from retained canonical event boundary | Encrypted, consented, exact-version export; restore rechecks purge barrier | Only signed ordered Core events with destination lifecycle support; never Relay canonization | Status transitions, conflicts, invalidations; no payload | None by default; consented aggregate only | Bytes, writes, replay time, closure fan-out, support incidents |
| Observable outcome receipt | Episode and evidence-retention policy | Withdraw derived outcome influence; preserve unknown tombstone | Purge receipt and dependent reports/caches | Use, action, oracle, observer, correction, derived metrics | Recompute from action/oracle/observer inputs | Encrypted report export with redaction; restore verifies oracle and deletion state | Replicate only as signed evidence event; no provider-side deletion claim | Outcome status, oracle failures, missingness | No raw result or prose copy | Receipt latency, oracle cost, missing rate, review load |
| Working checkpoint | Until close, expiry, ordinary delete, or project policy | Invalidate and exclude from issue; retain audit tombstone | Purge checkpoint and every derived reconciliation/handoff dependency | Source revision, project, task, artifacts, field witnesses, policy, issued projections | Recompile from source facts and user-declared codes | Participant-controlled encrypted export/restore with exact digest | Only within same authorized project scope and signed event order | Version, displacement, correction, expiry | No client transcript copy | Compile size, resume latency, invalidation fan-out, support recoveries |
| Reconciliation artifact | Until the linked checkpoint/use episode closes | Invalidate classifications and corrective deltas | Purge artifact and issued handoff references | Checkpoint, current snapshot, observation, policy, challenge, issue | Rerun typed comparison from the three inputs | Export only as closed codes and refs; restore reruns dependency checks | Same-project signed event only; unresolved identity never replicates | Conflict, unknown, abstention, challenge outcome | No free-text copy | Reconciliation latency, conflict rate, unresolved rate |
| Acknowledgement/challenge | Same turn plus bounded diagnostic retention | Expire immediately; remove durable influence | Delete ephemeral IDs and coverage record | Issued projection, field IDs, turn, checkpoint, policy | Recreate only from the issued closed projection | No export by default; aggregate counts may be consented | Not eligible as canonical replication; observation may be summarized | Receipt of coverage/uncertainty only | No prose echo or provider copy | Turn overhead, tokens, latency, correction rate |
| Memory warranty | Descriptor lifetime plus verification expiry | Mark invalid and withdraw dependent issue/use | Purge descriptor, verification result, and dependent warranty reports | Record/version, descriptor, snapshot, local metadata, policy, verification | Re-evaluate from immutable descriptor and exact snapshot | Export descriptor only if local paths are redacted and digest-bound; restore marks unavailable until reverified | Static descriptor may replicate within project; local verification result is portable only as unavailable unless rechecked | Probe result enum, no file content, failure class | No remote endpoint or ambient credential copy | Probe time, bytes read, invalidation rate, support failures |
| Conditional Failure Memory | Until expiry, correction, retirement, or project policy | Retire guard and preserve correction tombstone | Purge failure, guards, suppression candidates, and reports | Failed action receipt, state fingerprint, evidence, correction, override | Rebuild advisory candidate from typed receipts; never infer missing state | Encrypted project-scoped export of codes and refs; restore is advisory until current state is observed | Signed same-project evidence only; no cross-project state join | Advisory issue, abstention, override, no-block outcome | No command/error prose copy | False warning rate, recovery time, state-observation cost |
| Notification receipt | Due episode plus delivery/unknown retention | Cancel pending delivery and close receipt | Purge target, cue, delivery, and dependent action references | Transaction, cue, recipient, target, permission, policy, host capability | Recompute due state; never replay a one-shot notification after closure | Participant-controlled export with target redaction; restore requires idempotency check | Only to an accepted same-principal host with signed ordering | Issue, deliver, suppress, unknown, duplicate prevention | No OS/provider-side copy assumed deletable | Notification count, latency, false alarms, support retries |
| Experience/procedure candidate | Lab evidence window and explicit retirement policy | Retire from issue; retain evidence tombstone | Purge candidate, procedure refs, evidence-derived cache, and action influence | Outcome receipts, applicability, guards, verification, rollback, source/policy | Rebuild only from accepted receipts and exact evidence versions | Export only typed candidate digest and evidence refs; restore remains advisory | No replication until accepted Core artifact contract and lifecycle closure | Admission, correction, retirement, purge, native confirmation | No provider/model procedure copy | Admission cost, verification time, rollback rate, support burden |
| Cache or report | Short TTL or report-retention policy | Drop and rebuild from current canonical artifacts | Delete all derived copies and report manifests | Every input generation, query, permission, and source artifact | Deterministic rebuild; cache miss is safe | Encrypted aggregate export with manifest; restore never bypasses current rebuild | Replicate only S0 aggregates or signed Core projections | Build, expiry, stale refusal, export | Consent and deletion receipt required for any copy | Bytes, hit rate, rebuild time, export/support cost |
| Continuity-debt event | Episode and preregistered study retention | Remove derived metric influence while preserving study disposition | Purge event and aggregate dependencies | Opportunity oracle, omission condition, receipt, category, attribution, abstention | Recompute from blinded event log and independent denominator | Export only de-identified vectors with manifest; restore preserves missingness | Replicate only de-identified signed research result | Attribution, abstention, missing, correction | No raw text; participant may delete report copy | Event rate, evaluator time, denominator coverage, storage |

### 5.3 Mandatory boundary conditions

Pre-staging secret refusal is mandatory. Before an artifact is assigned or
stored, the boundary rejects credentials, token-like values, private keys,
authorization headers, session cookies, raw prompts, command text, imported
prose, tool/model/provider prose, executable payloads, and unbounded content.
The refusal result is a bounded code and is not treated as a failed memory
episode.

Ordinary deletion removes future influence through the declared dependency
inventory. Terminal purge is stronger privacy compaction: affected identifiers
leave every declared inspectable surface, replay resumes from an identity and
generation barrier, and no report, cache, backup, export, replication, log, or
external copy is allowed to recreate the purged influence.

## 6. Packet A — frozen benchmark and measurement contract

Packet A is the first decision because it makes later evidence interpretable.
It is a research contract and a non-displacing parallel lane. It does not add a
production schema, telemetry, client hook, retrieval change, or product
behavior.

### 6.1 Calibration pilot

The calibration pilot has 48 paired episodes:

- six task families;
- two sanitized fixture repositories;
- two fixed client/model-build strata; and
- two deterministic repetitions per family/repository/stratum cell.

The pilot checks fixture determinism, receipt completeness, oracle behavior,
secret refusal, project isolation, lifecycle cleanup, and budget accounting.
It may repair instrumentation defects before freeze. It may not tune a
mechanism, select a favorable task, change the estimand, change the primary
contrast, or use a holdout result to set a threshold.

### 6.2 Confirmatory N and episode layout

The proposed confirmatory sample is 384 paired episodes:

- six task families: bug fix, refactor, release preparation,
  documentation/configuration, incident investigation, and cross-client
  project handoff;
- four sanitized fixture repositories, including Python, TypeScript, and
  mixed-project shapes;
- four fixed client/model-build strata; and
- four deterministic repetitions for every family/repository/stratum cell.

Every episode is evaluated under every arm with the same logical task, source
state, mutation schedule, oracle, tools, permission set, time budget, and
predeclared seed. A pair is an episode-level comparison, not a claim that
different model runs are identical.

The confirmatory N is power-simulation-derived, not chosen after observing
results. The frozen planning simulation uses 100,000 deterministic replicates,
seed 20260829, paired binary CAOS, a two-percentage-point noninferiority
margin, a target detectable ten-percentage-point paired improvement, two-sided
alpha 0.05, 90% power, Holm control across the two primary contrasts, and a
15% allowance for predeclared nonrecoverable infrastructure loss. The smallest
stratified N satisfying those parameters is 384 paired episodes. If the
reproducible simulation does not yield that result, the manifest must record
the derived N and Packet A is not frozen until the first decision is revisited.

### 6.3 Required arms

The benchmark includes all required controls:

1. no memory;
2. a static task note;
3. append-log/search;
4. current retrieval;
5. an optimized Project Context Capsule; and
6. matched hybrids combining the capsule, checkpoint, reconciliation, and
   the relevant mechanism under test.

The primary continuity contrast is the best checkpoint/reconciliation
condition against the optimized capsule under matched turns, total context,
latency, model/client build, and tool budget. Each specialized experiment
declares its own primary contrast before confirmatory execution and retains the
same controls.

### 6.4 Tasks, mutations, and metrics

Every task spans at least two sessions. A preregistered subset switches
supported clients. Controlled between-session mutations include branch or
source revision changes, corrected requirements, dependency changes, abandoned
approaches, ordinary deletion, terminal purge, project ambiguity, externally
modified files, and a stale checkpoint that appears superficially plausible.

Primary measures are:

- explicit Current Authorized Outcome Success (CAOS);
- hard-safety failure counts;
- task completion and first-action correctness;
- continuity-debt category vectors;
- user restatement and repeated discovery;
- repeated ineffective actions and valid-retry blocking;
- stale, wrong-branch, wrong-project, deleted, or purged influence;
- context tokens, total tokens, latency, and cost classes; and
- correction, invalidation, deletion, and purge closure.

Every rate reports its raw numerator and denominator. CAOS is conjunctive: the
task oracle, currentness, authority, prerequisites, budget, and correction
checkpoint must all pass. A missing outcome is missing. It is not success or
failure after the fact.

### 6.5 Failed-run, missingness, and inference rules

The manifest freezes these rules before any confirmatory result:

- a hard safety failure is a failure in the applicable safety denominator and
  stops the affected promotion decision;
- a deterministic fixture, oracle, or harness failure is retained as
  INFRASTRUCTURE_FAILURE and is excluded from the efficacy denominator only
  when the failure is independently diagnosed and the episode was not exposed
  to a mechanism-specific result;
- blocked, unsupported, not-exercised, unknown, and abstention states remain
  their own counts and are never imputed as pass;
- a participant or client drop is retained as ATTRITION with its last valid
  state; replacement episodes use only predeclared reserve IDs;
- no episode, family, model-build stratum, or failed run may be removed after
  looking at its outcome;
- Wilson bounds are used for individual proportions, exact paired or
  stratified bootstrap bounds for paired differences, and 95% confidence
  bounds are reported with the estimand;
- Holm control applies to the two primary contrasts; secondary measures are
  labeled exploratory; and
- all negative, blocked, skipped, unsupported, not-exercised, and missing cells
  remain in the report.

Zero observed failures is not zero risk. The report must include the exact
observed count, denominator, confidence bound, exposure, and unexercised
surface for every safety claim.

### 6.6 Exact freeze criterion

Packet A is frozen if and only if all of the following are true:

1. the manifest lists the 384 confirmatory episode IDs, six task families,
   four fixture repositories, four client/model-build strata, four repetitions,
   reserve policy, and deterministic seeds;
2. every arm, primary contrast, oracle, budget, permission set, and mutation
   is versioned and content-digested;
3. the power simulation is reproduced from seed 20260829 with its parameters,
   100,000 replicates, and derived N recorded;
4. CAOS, hard-safety gates, raw numerators/denominators, confidence methods,
   Holm multiplicity control, and all missing/failed-run dispositions are
   frozen;
5. the calibration pilot is labeled calibration-only and its results cannot
   alter the confirmatory estimand or holdout;
6. pre-staging secret refusal, project ambiguity abstention, idempotency,
   conflict, invalidation, ordinary deletion, terminal purge, and rebuild
   checks pass on the declared fixture set; and
7. the manifest digest is recorded before any confirmatory mechanism result is
   read.

This is the exact freeze criterion. Packet A may be frozen now as a contract;
execution remains non-displacing research and cannot grant product acceptance.

## 7. Staged mechanisms and falsifiable gates

Each mechanism remains active. “Promotion” below means advancement of the
research stage or artifact authority ceiling, not automatic production
authorization.

### 7.1 Reliable working continuity: checkpoints and reconciliation

**Stage 1 — lab/shadow continuity.** Compile a checkpoint from explicit
user-declared closed codes, current source facts, and observable client events.
Run three-way reconciliation against the last checkpoint, current
source/project/policy state, and new client observation. The output is a
bounded projection with current, displaced, uncertain, invalid, new, and
unavailable classes. It never stores hidden reasoning or command text.

**Promotion gate.** The frozen Packet A continuity contrast must have zero
hard authorization, currentness, wrong-project, correction, secret, or purge
failures; CAOS must be noninferior to the strongest simpler control within two
percentage points; continuity debt must fall by at least 20% relative to the
optimized capsule with the paired confidence bound recorded; first-action
correctness must not decrease; and context must stay below the full-transcript
control with no more than 25% extra over the capsule-only condition.

**Next stage.** A separately authorized Core-owned checkpoint projection may
cross a cooperating lifecycle-aware client boundary, with exact snapshot,
policy, principal, and dependency bindings and a challenge path for
displacement or uncertainty.

**Kill/narrow rule.** If the added checkpoint or reconciliation is noninferior
to the optimized capsule, increases context without measurable outcome value,
or causes any hard lifecycle failure, retain only the smallest useful typed
checkpoint or diagnostic vector and do not add richer state.

### 7.2 External-state applicability and memory warranties

Static applicability and use-time verification are separate artifacts.

**Stage 1A — static typed applicability.** Core compiles a typed descriptor
from authorized project, source, workspace, policy, and capability metadata.
The descriptor is immutable and binds a record/version and snapshot through
verification, issue, and use. It contains no executable predicate.

**Stage 1B — first live local verification.** Only Core-owned authorized
workspace/local metadata may be read: bounded files or digests, repository and
branch identity, branch lineage, lockfile/dependency generation, migration
head, operating system/architecture, and declared capability. There is strict
path containment, link/reparse refusal, bounded reads, no network,
subprocess, hooks, ambient credentials, or unbounded filesystem walk. An
unavailable result is portable as unavailable and never as verified.

**Promotion gate.** Use-time local verification must prevent stale high-impact
issue cases that static controls miss, with zero unauthorized reads, secret
exposure, containment failures, or purge residue and no material false
invalidation regression. Verification, issue, and use must share one immutable
descriptor/snapshot binding.

**Next stage.** Remote verification remains active as a separate research
stage. It requires a fresh threat model, endpoint allowlist, least-privilege
credentials held outside artifacts, paired local controls, explicit remote
consent, and an unavailable state on any unsupported or failed endpoint.

**Kill/narrow rule.** If static typed applicability is noninferior, retain the
static contract and do not add probes. If local probes add reads, latency,
support burden, or false invalidation without preventing stale influence,
narrow to static descriptors. Remote verification is not a reason to weaken
the local boundary.

### 7.3 Semantic acknowledgement/challenge

The former name “Semantic Handoff Checksum” is retained only as historical
provenance. The precise current name is **semantic acknowledgement/challenge**.
It is not a checksum unless the artifact actually computes and verifies a
checksum.

**Stage 1 — ephemeral closed-field acknowledgement.** After a capsule or
checkpoint is supplied, the cooperating host returns only ephemeral field IDs,
coverage codes, and uncertainty codes. It may identify an omitted, conflicting,
or uncertain field but may not echo prose, store durable authority, write
canonical state, supply action input, or assert that the model understood.
Compare with an optimized one-shot capsule under matched turns, total tokens,
latency, model/client build, and tool budget.

**Promotion gate.** The acknowledgement/challenge must reduce early
constraint/state errors by at least 20% relative to the ordinary optimized
capsule, add no authority, privacy, project, correction, or purge failure,
and keep acknowledgement overhead below 15% of total task context. Predictive
value and corrective value are reported separately; self-report alone does not
pass.

**Next stage.** A gated richer-correction stage may issue a minimum corrective
delta composed only of closed field IDs and correction codes. It must preserve
Core authority, exact versions, permissions, dependency closure, and an
explicit challenge/abstention path. A richer client acknowledgement or
durable handoff requires a separate L3 lifecycle capability and confirmation.

**Kill/narrow rule.** If acknowledgement content does not predict or correct
later behavior, becomes confident model self-report, or loses to a larger
one-shot capsule at matched total context and latency, retain the ephemeral
coverage/uncertainty diagnostic and do not add prose or durable state.

### 7.4 Prospective memory

Prospective memory remains an active path toward memory at the right moment.
Its transaction binds exact supporting evidence versions, principal/project/
domain/policy generations, typed cue, positive witnesses, negative guards,
expiry, cooldown, rearm rules, target/recipient, sensitivity, idempotency,
dependency closure, action force, and issue/action/outcome/invalidation
receipts.

**Stage 1 — inert local in-session reference.** The first stage is an inert
local in-session reference only. It has no OS, provider, tool, file, process,
network, or client side effect. It cannot send a notification or perform an
action. It requires exact recipient and target binding, idempotency, closed
sensitivity, rate limiting, offline replay, correction rules, and a
distinction between due, suppressed, unavailable, expired, and invalidated.

Compare no prospective memory, an explicit task table, a deterministic
scheduler, always-injected reminders, retrieval-only memory, the full typed
transaction, and ablations without negative guards, current-version reread,
dependency closure, or action ceiling.

**Promotion gate.** Promotion is conjunctive:

1. the primary contrast against the deterministic scheduler is preregistered;
2. recall is at least 0.80 and task-level blinded usefulness is at least 0.70,
   each with its raw numerator/denominator and confidence bound;
3. CAOS is noninferior to the strongest control within two percentage points;
4. false alarms are at most 5% of eligible non-due opportunities, with the
   bound reported;
5. unauthorized, stale, deleted, purged, wrong-domain, duplicate, or
   unconfirmed-protected influence is zero;
6. a paired scheduler benefit is present: at least a 5% relative improvement
   on the preregistered outcome-utility endpoint and a 95% paired bound
   excluding no benefit; and
7. the result is not explained only by larger prompt exposure, latency, or
   context.

**Next stage.** A separately accepted notification-only path may create a
user-visible notification receipt for an exact recipient and target. Later
suggest or draft behavior requires a new paired gate. Protected execution
requires fresh exact native confirmation and its own action/effect evidence.

**Kill/narrow rule.** If the deterministic scheduler is noninferior, retain
the scheduler and the typed transaction contract while narrowing the learned
or compiled mechanism. If false alarms, disclosure, duplicate issue, or
correction/purge closure fail, return to inert local reference. No notification
or action ceiling is silently inherited by a weaker stage.

### 7.5 Conditional Failure Memory

Conditional Failure Memory remains active but a failure never becomes an
unconditional prohibition.

**Stage 1 — advisory only.** Store a typed action signature, observed failure
class, Core-owned or host-observed allowlisted state, state fingerprint,
uncertainty/abstention, supporting receipt references, retry-when and
do-not-retry-while codes, expiry, correction, disconfirmation, and retirement.
Stage 1 may warn or expose an advisory code; it cannot block a retry, alter a
tool call, write a procedure, or trigger a protected action.

**Promotion gate.** Advisory output must reduce repeated ineffective actions by
at least 20% against raw failed-trajectory retrieval and simple error-signature
deduplication, with no negative transfer, no wrong-project influence, no
secret persistence, exact correction/purge closure, and valid-retry blocking
within the frozen two-percentage-point noninferiority margin.

**Next stage — automatic-suppression research.** Automatic suppression is
allowed only after accepted L3 synchronous pre-effect capability,
independently witnessed state, exact typed predicates, expiry and invalidation,
a fresh native override or confirmation, counterfactual controls, and zero
safety regressions. The state must be re-observed immediately before the
effect; a model assertion is not a witness.

**Kill/narrow rule.** Kill automatic suppression if it blocks a valid recovery,
survives correction, lacks independently witnessed state, or loses to exact
signature deduplication. Retain the advisory-only form if it remains useful;
otherwise retain only the typed failure evidence and its negative result.

### 7.6 Continuity Debt Ledger

Continuity Debt remains an active diagnostic and possible product metric. It
must distinguish work caused by a legitimate change from avoidable repetition.

**Stage 1 — category vector.** Report raw categories, not one opaque score:

| Category | Meaning |
|---|---|
| CORRECTION | Work caused by an authoritative correction or changed requirement; not automatically debt |
| VERIFICATION | Work required to check current state or applicability; not automatically debt |
| NEW_REQUIREMENT | Work introduced by genuinely new user or environment requirements; not debt |
| VALID_RETRY | A retry made valid by changed state or explicit correction; not debt |
| AVOIDABLE_REPETITION | Repeated discovery, failure, decision, or restatement with an eligible current memory opportunity |
| STALE_RECOVERY | Repair of guidance issued as current when it was stale |
| WRONG_PROJECT_RECOVERY | Repair of a cross-project or unresolved-identity influence |
| CHECKPOINT_DRIFT | Resume followed displaced checkpoint state |

Attribution uses deterministic, blinded controlled omission: paired episodes
receive the same logical task, and an independent opportunity oracle determines
whether an eligible memory could have prevented the event. The evaluator may
abstain. The denominator is the independently observed opportunity set, not
the number of events selected by the mechanism.

**Promotion gate for an aggregate score.** Category vectors are always
retained. An aggregate score is permitted only if weights and category
definitions are preregistered before holdout evaluation and, on a held-out
20% episode set, all of the following hold:

- out-of-sample CAOS prediction AUC is at least 0.70 with a bootstrap 95%
  lower bound of at least 0.65;
- a one-standard-deviation lower debt value predicts better CAOS with an odds
  ratio of at most 0.80 and a 95% upper bound below 1.00;
- the direction is consistent in at least five of six task families;
- the aggregate adds predictive value over the category vector and strongest
  simple control; and
- no hard authority, privacy, project, correction, or purge failure occurs.

**Next stage.** If the gate passes, use the aggregate only as a bounded
research/product metric with versioned weights, category drill-down, and
opportunity coverage. Pair it with CAOS, usefulness, cost, and latency rather
than treating it as a replacement endpoint.

**Kill/narrow rule.** Kill the aggregate if categories move in contradictory
directions, weights dominate, opportunity attribution is not reliable, or the
held-out gate fails. Retain the independently useful category vectors,
abstention counts, and correction/verification/new-requirement distinctions.

### 7.7 Verified experience and procedures

Verified experience and procedures remain active as outcome-bound candidates.
A candidate contains accepted outcome evidence, typed applicability, positive
and negative guards, exact source/project/policy generations, verification and
repair tests, correction/retirement state, rollback reference, and a complete
dependency inventory.

**Stage 1 — advisory candidate.** A procedure may be supplied only as a
reference, suggestion, or draft. It contains no executable payload, command,
credential, imported/tool/model/provider prose, arbitrary predicate, or
autonomous effect. Any effect requires exact native confirmation and a
rollback path.

**Promotion gate.** Admission requires recurrence across distinct task
identities or strong external verification, accepted observable outcomes,
exact applicability boundaries, negative guards, correction and repair
success, source/outcome/policy closure, and terminal-purge closure. It must
reduce repeated ineffective actions without materially blocking valid retries
after a state change and remain noninferior to static notes, raw trajectory
retrieval, and simple error-signature deduplication.

**Next stage.** A separately scoped procedure pilot may use verified
reference/suggest/draft delivery and exact native confirmation. It may enter
consented design-partner evaluation only with a candidate digest, local-only
default, participant-controlled export/deletion, and no broad claim. Stronger
effects remain a separate research decision.

**Kill/narrow rule.** Retire the procedure form if evidence does not transfer,
if negative transfer or stale applicability appears, if rollback is not
closed, or if the simpler control is noninferior. Preserve the accepted
outcome evidence and typed failure result; do not turn a failed procedure into
an unconditional prohibition.

### 7.8 Design-partner validation

Design-partner work remains active as the route to L5 evidence, not as a
shortcut to support or marketing claims.

**Stage 1 — consented preparation.** Use two or three technically competent
participants only after the relevant lab gates. Require explicit consent,
exact candidate digest, declared artifact/capability pair, local-only default,
participant-controlled export/deletion, attrition accounting, no raw text,
no credentials, and inspectable structured reports.

**Promotion gate.** The pilot must preregister task families, candidate
digests, supported capabilities, attrition and missingness rules, and
participant-visible deletion behavior. It must show no privacy, authority,
project, correction, purge, or effect regression and must preserve the
distinction between participant observation and product support.

**Next stage.** A separate L5 study may widen the participant set or compare
accepted clients, but only after repeating the exact digest-bound artifact and
local-only boundary. Any broader client or provider claim requires its own
acceptance evidence.

**Kill/narrow rule.** If consent, deletion, attrition, trust, or capability
evidence fails, narrow the study or return to lab evidence. Do not discard the
mechanism; retain its typed contract and record the disposition.

### 7.9 Adaptive memory routing

Adaptive routing remains active after the earlier workstreams. It may compare
exact current records, source-backed evidence, capsules, checkpoints, temporal
history, conditional failures, verified procedures, typed relations, and raw
source dereference.

**Stage 1.** Shadow-only routing over already authorized, applicable,
dependency-bound projections. No router may inspect unauthorized candidates,
become a permission authority, or create a truth record.

**Promotion gate.** A target-task router must beat the current lexical and
capsule baseline on preregistered CAOS, disclosure, latency, and maintenance
cost without hard lifecycle failures.

**Next stage.** A separate graph, embedding, or learned-router cell may run
only with an independent prior-art review, frozen task target, and exact
closure/rebuild contract.

**Kill/narrow rule.** Retain the best simpler projection when the router is
noninferior, expensive, opaque, or less correct. Graph, embedding, provider,
and remote ideas remain in the register and may return under a new bounded
experiment.

## 8. Evaluation design and non-compensable gates

### 8.1 Evidence ladder

The accepted ladder remains:

- L0: proposal and frozen specification;
- L1: deterministic synthetic worker result;
- L2: coordinator-reproduced integrated result;
- L3: isolated pinned external-supplier or synchronous pre-effect result,
  when permitted;
- L4: cross-platform or repeated stochastic client/model evidence; and
- L5: consented product evidence.

Wave 4 E–G remains research evidence, not product acceptance. Its retained M1
contract supplies the exact assignment-to-outcome sequence and its retained
closure contract supplies dependency-complete withdrawal. The production
semantic gaps, unsupported capabilities, and not-exercised purge routes remain
visible.

### 8.2 Fair comparison

Every comparison equalizes, where applicable:

- client and model build;
- reasoning effort;
- task and source state;
- context and total token budget;
- tools and permission set;
- time and retry budget;
- temperature and deterministic seed;
- oracle and evaluator access;
- disclosure and latency budget; and
- cost and storage budget.

If a client capability prevents exact equivalence, the report names the
unsupported capability. It does not silently emulate a stronger hook.

### 8.3 Required hard gates

No stage may advance with:

- unauthorized or unauthorized-derived influence;
- unresolved identity used to issue, write, reconcile, or join;
- wrong-project context;
- stale, superseded, deleted, expired, invalid, or purged influence;
- correction nonconvergence;
- duplicate capture, memory, issue, notification, or action;
- raw secret or hidden-reasoning persistence;
- arbitrary executable predicates or procedure payloads;
- client/model/provider self-report promoted to truth, safety, or causality;
- protected action without current exact confirmation;
- an inaccessible-candidate diagnostic leak;
- missing dependency or incomplete rebuild/purge inventory; or
- external copies whose deletion and restore behavior is unknown.

## 9. Research packet register

These research packets are distinct from product execution Packets E–H. Every
idea has an owner, packet, entry condition, exit condition, and disposition.
No disposition means deletion; “narrow” means the smaller safe form remains
active.

| Workstream | Owner | Packet | Entry condition | Exit condition | Disposition |
|---|---|---|---|---|---|
| Active frontier | Product/release acceptance owner | Existing execution-plan gates | Current protected-main frontier and exact candidate requirements | Integrated milestone, replacement candidate, packaged journey, Wave 4 E–G acceptance, and Phase 2 are each accepted at their own gates | Blocking; unchanged |
| Packet A benchmark | Memory Lab coordinator | Packet A | First decision accepts the frozen contract; no product dependency | Frozen digest, reproducible calibration, declared confirmatory N, or a recorded blocked result | Parallel, non-displacing |
| M1 measurement spine | Core contract owner and Memory Lab coordinator | Packet C | Packet A freeze and artifact boundary review | Exact replay, outcome association, unknown/abstention, idempotency, conflict, invalidation, deletion, purge, rebuild, and secret refusal pass | Shadow/lab; no production data collection |
| Versioned checkpoints | Continuity contract owner | Packet D | M1 shape and typed field contract | Checkpoint compiles only observable state and survives restart, correction, abandonment, and purge tests | Active; mapped to ZF-015 |
| Three-way reconciliation | Continuity contract owner | Packet E | Checkpoint plus current source snapshot plus client observation | Current/displaced/uncertain/invalid/new/unavailable classes are deterministic and no displaced action is resurrected | Active; mapped to ZF-016 |
| Typed applicability | Applicability contract owner | Packet G | Reconciliation and dependency inventory | Static descriptor and local verification pass containment, unavailable, binding, and closure gates | Active; local-only first |
| Semantic acknowledgement/challenge | Handoff research owner | Packet F | Checkpoint/capsule projection and matched benchmark | Closed-field acknowledgement improves handoff or remains a bounded diagnostic | Active; no checksum claim |
| Prospective memory | Event-memory research owner | Packet H | M1 outcome path and scheduler controls | Inert local stage and conjunctive gate pass; notification path receives separate decision | Active; stronger path sequenced |
| Conditional Failure Memory | Outcome-learning research owner | Packet I | Advisory receipts and allowlisted state | Advisory gate passes; L3 synchronous suppression research remains separate | Active; no Stage 1 blocking |
| Continuity Debt Ledger | Evaluation owner | Packet B | Independent opportunity oracle and blinded omission protocol | Category vector is reliable; aggregate score only if exact held-out gate passes | Active; vector first |
| Verified experience/procedures | Procedure contract owner | Packet J | Accepted outcome/applicability/closure evidence | Advisory candidate passes recurrence/verification, repair, rollback, and purge gates | Active; no autonomous effects |
| Design partners | External-validation owner | Packet K | Relevant L4 evidence, consent materials, exact digest | L5 evidence is consented, local-first, de-identified, and attrition-bounded | Active; no broad claims |
| Caches and reports | Privacy/lifecycle owner | Packet L | Artifact matrix and dependency inventory | Rebuild, backup/export/restore, ordinary deletion, terminal purge, and external-copy closure pass | Cross-cutting; derived only |
| Adaptive routing | Retrieval research owner | Packet M | Earlier workstreams and accepted simpler baselines | Target-task win with matched budgets and closure evidence | Active later stage; shadow first |

### 9.1 Packet coverage

Packet A freezes the benchmark. Packet B produces debt vectors. Packet C
implements M1 shadow receipts. Packet D compiles checkpoints. Packet E performs
three-way reconciliation. Packet F tests semantic acknowledgement/challenge.
Packet G owns static applicability and bounded local warranties. Packet H keeps
prospective memory and its notification path active. Packet I keeps Conditional
Failure Memory active. Packet J keeps verified experience and procedures active.
Packet K keeps design-partner evidence active. Packet L closes artifact
retention, deletion, purge, rebuild, export, restore, replication, logging,
and external-copy behavior. Packet M keeps adaptive routing active.

Each packet must report entry, exit, evidence grade, unresolved unknowns,
support/cost metrics, and disposition. A negative result narrows the stage; it
does not delete the mechanism from the program.

## 10. Limited external validation, claims, and risks

### 10.1 Design-partner boundary

The first L5 entry is two or three consented design partners using disposable
or non-sensitive projects under an explicit experimental label. Reports may
include only typed codes, exact artifact digests, bounded metrics, attrition,
and participant-approved aggregate outcomes. No raw prompts, transcripts,
commands, credentials, imported prose, or provider/model prose leave the
local boundary.

The participant controls export and deletion. A deletion receipt covers local
artifacts, reports, backups, restores, and any explicitly consented external
copy. A participant's report is evidence for the declared candidate digest and
capability, not general support evidence.

### 10.2 Evidence-bounded claims

| Claim | Permitted wording | Not permitted |
|---|---|---|
| Current research | “This proposal retains a staged hypothesis under the declared evidence boundary.” | “ATC has solved memory” or “ATC reliably improves users’ work” |
| Local contracts | “The declared harness or Core path passed the named cases at the named evidence grade.” | Broad maturity, strength, or production-readiness claims |
| Novelty | “A possible ATC-specific composition remains an unreviewed hypothesis pending fresh prior-art review and independent challenge.” | A novelty, priority, or scholarly contribution claim |
| Client support | “Client X supports exact artifact/capability Y under accepted artifact, version, and lifecycle evidence.” | General client/provider support inferred from source adapters or model prose |
| User evidence | “The consented candidate digest produced the reported result under the declared participant and task boundary.” | Broad product, market, safety, or marketing claims |

### 10.3 Risks and mitigations

| Risk | Mitigation |
|---|---|
| Active frontier is displaced by attractive research | Keep the frontier table and DAG binding; Packet A is explicitly non-displacing |
| Closed fields omit useful state | Measure abstention and false omission; add a field only through a frozen contract change |
| Instrumentation changes behavior | Keep receipts outside model-visible context and instrument matched controls |
| Zero observed failures is overstated | Report exposure, denominators, confidence bounds, and unexercised cells |
| Outcome ambiguity | Preserve M1 evidence grades and require Core, harness, or independent observers |
| Privacy expansion | Pre-stage secret refusal, closed fields, participant deletion, and no raw external copies |
| Wrong-project or stale transfer | Require exact project, snapshot, policy, principal, and dependency bindings with fail-closed unknowns |
| Client capability asymmetry | Report the exact unsupported capability rather than silently emulating it |
| False novelty | Fresh primary-source review and independent challenge before any public claim |
| Procedure overreach | Reference/suggest/draft ceiling, exact native confirmation, rollback, correction, and purge closure |
| Aggregate metric gaming | Category vectors first, blinded omission, independent denominator, locked weights, held-out gate |
| Provider or remote expansion | Keep provider/remote ideas active only behind separate lifecycle, threat, and deletion contracts |

## 11. Explicit non-authorization

This proposal authorizes none of the following:

- production schema changes;
- production data collection or invasive telemetry;
- external access, hosted Core, remote verification, or provider-side deletion
  claims;
- automatic suppression, automatic execution, or protected action;
- a client, provider, connector, SDK, or stable interface claim;
- release, replacement-candidate publication, support, or marketing;
- raw prompt, command, imported/tool/model/provider prose, credential, hidden
  reasoning, or executable-payload retention;
- unresolved-project writing, issuing, reconciliation, or cross-project joins;
- graph, embedding, learned-router, or external-memory promotion by preference;
- a procedure becoming authority through repeated retrieval; or
- a claim that a model, client, tool, or provider can self-assert safety or
  success.

The exact production and external boundaries remain those accepted separately
by the execution plan, ADR-049, ADR-090, and later packet decisions.

## 12. Review questions

Reviewers should answer these questions against the frozen contracts:

1. Does the active frontier remain visibly blocking while Packet A runs in
   parallel without displacement?
2. Does M1 retain the exact assigned → supplied → acknowledged → observed_use
   → action → outcome chain and distinct checkpoint authority?
3. Does the artifact matrix close ordinary deletion, terminal purge, rebuild,
   backup/export/restore, replication, logs, and external copies?
4. Does the Packet A power simulation and exact freeze criterion prevent
   confirmatory N or failed-run rules from being chosen after outcomes?
5. Does Stage 1 prospective memory remain inert, local, bound, idempotent,
   rate-limited, replayable, and correction-aware?
6. Does Conditional Failure Memory remain advisory until the L3 synchronous
   pre-effect and independent-witness requirements are met?
7. Is local warranty verification bounded and portable as unavailable, with
   remote verification kept behind its own threat model?
8. Does semantic acknowledgement/challenge avoid a checksum claim and avoid
   prose echo, durable authority, write, or action input?
9. Does the debt ledger distinguish correction, verification, new requirements,
   valid retry, and avoidable repetition, with independent attribution?
10. Are verified procedures and design partners active without granting
    autonomous effects, broad support, or broad product claims?
11. Are all owner, packet, entry, exit, and disposition rows present for every
    workstream?

## 13. Exact proposed first decision

> Accept the whole measurement-first staged program as the post-frontier
> direction and freeze the corrected Packet A benchmark contract now, while
> keeping every later packet active and sequenced.

Acceptance of this direction means:

- the active frontier remains the blocking product path;
- Packet A may freeze and run as non-displacing research;
- M1, checkpoints, reconciliation, applicability, semantic
  acknowledgement/challenge, prospective memory, Conditional Failure Memory,
  warranties, continuity debt, verified experience, procedures, design
  partners, and adaptive routing all remain active in the register;
- each mechanism must pass its own Stage 1, promotion, next-stage, and
  kill/narrow rules; and
- no production schema, data collection, external access, suppression,
  execution, release, support, or marketing authority is implied.

This is the proposed decision and sequencing contract, not a claim that any
later stage has already passed.
