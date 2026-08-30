# Post-beta continuity and memory proposal

## Measurement-first staged program after the active frontier

| Field | Value |
|---|---|
| Date | August 29, 2026 |
| Status | Draft direction for review; no production, schema, roadmap, release, or marketing authority |
| First decision requested | Accept the whole measurement-first staged program as the post-frontier direction and freeze the corrected Packet A specification now; defer benchmark-manifest freeze and execution until reproducibility and fixture gates pass |
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

No mechanism is removed from the program. Two DAGs govern the work and must not
be conflated:

~~~text
PRODUCT DAG (canonical execution order)
active frontier
  -> ZF-015 versioned working checkpoints
  -> ZF-016 working-state reconciliation
  -> ZF-017 M1 memory-use and outcome ledger
  -> ZF-018 shadow consolidation and prospective work
  -> ZF-019 procedural-memory gates

RESEARCH/EVIDENCE DAG (parallel, non-displacing)
Packet A specification freeze || shadow M1 instrumentation || M3 closure oracle
  -> fixture, authority, lifecycle, and reproducibility gates
  -> evidence for the product DAG; never a product prerequisite or reorder
~~~

The product DAG is canonical. The research/evidence DAG may prepare Packet A,
shadow M1 instrumentation, and the distinct M3 closure oracle in parallel, but
none of those parallel lanes becomes a product prerequisite or reorders
ZF-015/016. Stronger effects remain behind their declared gates.

The sequence is deliberately staged rather than semver-like. The research
stages and workstreams map to the accepted product direction as follows:

| Research stage or workstream | Mechanisms kept active | Product and decision mapping | Initial disposition |
|---|---|---|---|
| Active frontier | Integrated capture/retrieval/setup/control/security milestone; replacement candidate; packaged ordinary-use journey; Wave 4 E–G product acceptance; Phase 2 | [Zero-Friction Execution Plan](../product/ZERO_FRICTION_EXECUTION_PLAN.md), [ADR-090](../DECISIONS.md#adr-090-adopt-zero-routine-friction-as-the-post-v1-product-direction) | Blocking product work; not displaced by this proposal |
| Research Stage A — specification and measurement | Packet A specification, M1 transactions, outcome receipts, continuity-debt vector | [ZF-017](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-017-memory-use-and-outcome-ledger), [ADR-049](../DECISIONS.md#adr-049-retain-closure-and-observable-use-contracts-fill-core-semantics-then-test-prospective-memory) | Specification and shadow work may run in parallel; no product prerequisite |
| Workstream A2 — M3 dependency-complete closure | Full-rebuild oracle, six-surface influence inventory, withdrawal-before-republication, optimized closure | [ZF-017](../product/ZERO_FRICTION_EXECUTION_PLAN.md#zf-017-memory-use-and-outcome-ledger), [ADR-049](../DECISIONS.md#adr-049-retain-closure-and-observable-use-contracts-fill-core-semantics-then-test-prospective-memory) | Active and distinct from M1; shadow-only until its own gates |
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

Packet A is the sole research work lane permitted to run in parallel with the
active product frontier. Once inside the research program, shadow M1, shadow
M3, and other explicitly declared research-DAG branches may run in their
stated parallel order; those branches remain non-displacing and cannot change
product sequencing. Packet A may freeze fixtures, oracles, controls, and the
benchmark contract while the frontier proceeds, but it cannot change the
frontier, grant product acceptance, authorize a schema, or make a later
workstream live.

### 2.1 Product DAG

The product DAG is the canonical execution-plan order and is not reordered by
research preparation:

~~~text
active frontier
  -> ZF-015 versioned working checkpoints
  -> ZF-016 working-state reconciliation
  -> ZF-017 M1 memory-use and outcome ledger
  -> ZF-018 shadow consolidation and prospective work
  -> ZF-019 procedural-memory gates
~~~

This order binds product progression. M1 is the ledger named by ZF-017. M3 is
an independent closure oracle and research workstream; it does not insert a
new product prerequisite into this DAG.

### 2.2 Research/evidence DAG

Packet A specification freeze, shadow M1 instrumentation, and the distinct M3
closure oracle may begin in parallel:

~~~text
[Packet A specification freeze] ----\
[shadow M1 instrumentation] --------+--> [fixture and reproducibility gates]
[M3 full-rebuild closure oracle] ---/                 |
                                                     v
                             [evidence for the product DAG]
~~~

These lanes are non-displacing. They do not become product prerequisites, do
not authorize production behavior, and do not reorder ZF-015 or ZF-016. A
later product stage may use their evidence only after the canonical product
DAG reaches that stage and the relevant authority/lifecycle decision is made.

In plain terms, the active frontier remains required for product progression.
The research/evidence DAG makes later decisions interpretable, but a research
label never bypasses the product DAG.

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
10. **Project identity is fail-closed.** An unresolved project exists only as
    a short-lived, non-linkable abstention observation. It is never an issued
    artifact state and cannot assign, supply, issue, use, action, reconcile,
    produce an outcome, or join across projects.
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

The sequence is not shortened or renamed. The only permitted alternate edge is
supplied -> observed_use when acknowledgement is absent and the use receipt
meets the direct-observation rule below.

Issuer authority is closed: only Core or a deterministic harness may issue or
accept an M1 transition. Source adapters, clients, models, tools, and providers
may contribute observation-only input; they are never truth authority or
witness authority.

The closed transition table is:

| Transition | Required issuer | Required source/witness | Reject |
|---|---|---|---|
| assigned -> supplied | Core or deterministic harness | Core-issued projection with exact record/version, snapshot, policy, principal, predecessor, and dependency binding; witness is core_observed or deterministic_harness | Client/model/tool/provider assertion, missing binding, or a second idempotency key |
| supplied -> acknowledged | Core or deterministic harness | A Core/harness receipt of a host acknowledgement; the host event remains untrusted observation input | A client assertion alone, prose echo, missing supplied receipt, or acknowledgement after invalidation |
| acknowledged -> observed_use | Core or deterministic harness | Core/harness observation or independently_observed use tied to the exact supplied receipt | Self-attested use, untied use, stale generation, or an invalidated transaction |
| supplied -> observed_use (acknowledgement absent) | Core or deterministic harness | Receipt-bound Core/harness or independently_observed use with acknowledgement=absent_or_unknown; no acknowledgement credit | Any client-only use claim, missing exact supply binding, or use after invalidation |
| observed_use -> action | Core or deterministic harness | Core/harness or independently_observed bounded action envelope | Model/client/tool/provider success claim, unbounded command, missing target, or stale generation |
| action -> outcome | Core or deterministic harness | Core, deterministic harness, or independently_observed oracle/outcome; user/client reports are source-only observations | Self-reported safety/success, missing oracle/witness, or outcome after invalidation |
| any nonterminal state -> invalidated | Core or deterministic harness | Core lifecycle event with exact invalidation reason and dependency reference | Unauthenticated client/provider request, partial invalidation, or missing dependency closure |
| invalidated -> any later state | None | None; invalidated is terminal | Always reject; no replay, acknowledgement, use, action, or outcome may follow |

The exact witness classes are:

~~~text
core_observed
deterministic_harness
independently_observed
untrusted_observation
~~~

Only the first three can satisfy a transition's witness requirement, and only
Core or deterministic harness can issue the transition. untrusted_observation
may explain an absent or disputed acknowledgement but can never close a
transition, establish truth, establish safety, or establish success. A missing
transition is unknown or abstention, except for the explicitly permitted
receipt-bound supplied -> observed_use edge.

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
  status: assigned | supplied | acknowledged | observed_use | action | outcome | invalidated
  record_refs: exact record_id + version pairs
  canonical_snapshot: exact snapshot_id + version
  project_scope: exact project_id
  policy_generation: exact generation
  principal_view_generation: exact generation
  predecessor: exact checkpoint/use identifier or none
  dependency_binding: exact or conservative typed dependency digest
  issuer: Core | deterministic_harness
  source: Core | typed_source_adapter | typed_client_observation | typed_host_observation | deterministic_fixture
  witness: core_observed | deterministic_harness | independently_observed | untrusted_observation
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
  project_id: exact identifier
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
  project_resolution: resolved
  dependency_binding
  invalidation_state
  witness and verification strength
~~~

An unresolved project cannot appear in a checkpoint, transaction, receipt,
reconciliation artifact, or any other issued artifact state. A short-lived
unresolved observation may exist only to explain abstention, must be
non-linkable, and cannot assign, supply, issue, use, action, reconcile, produce
an outcome, or create a cross-project join.

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
| M3 closure/rebuild report | Core closure owner and independent full-rebuild oracle | Canonical dependency events, six-surface inventory, optimized rebuild, independently coded full rebuild | Surface IDs, dependency digests, disposition enums, equality booleans, bounded node counts, failure codes | S1–S2 | Core and isolated lab |
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
| M3 closure/rebuild report | Frozen fixture and evidence-retention policy | Withdraw derived report influence; retain bounded failing-case disposition | Purge report, optimized/full outputs, inventory, and dependent summaries | All six surfaces, exact dependency edges/frontiers, lifecycle event, policy generation, rebuild inputs | Compare independent full rebuild with optimized rebuild; withdrawal precedes republication | Encrypted lab export with fixture and oracle digests; restore reruns exact-equality check | Research result may replicate only as signed de-identified evidence; never as live authority | Equality result, stale/purge/illegal-edge failures, node counts; no payload | No external copy unless consented aggregate report with deletion receipt | Evaluated nodes, rebuild time, fan-out, storage, support/review cost |
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

The provisional planning layout is 384 paired episodes:

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

The confirmatory N must be power-simulation-derived, not chosen after observing
results. The required simulation inputs are 100,000 deterministic replicates,
seed 20260829, paired binary CAOS, a two-percentage-point noninferiority
margin, a target detectable ten-percentage-point paired improvement, familywise
alpha 0.05 with Holm control across the two primary contrasts, 90% power, and
a 15% allowance for predeclared nonrecoverable infrastructure loss. N=384 is
only the provisional planning value; if the reproducible simulation does not
yield 384, the later manifest must record the derived N and the Packet A
specification remains valid without freezing or executing the provisional N.

### 6.3 Required arms

The benchmark includes the canonical strongest-baseline ladder from the
Memory Evaluation Program and the controls already required here:

| Cell | Definition | If unavailable | Promotion use |
|---|---|---|---|
| NO_MEMORY (simple_no_memory) | No ATC memory or retained prior context | Always supported | Required floor |
| STATIC_TASK_NOTE | Fixed task note with no adaptive memory | Always supported | Required simple control |
| STATIC_PROFILE (simple_static_profile) | Frozen compact user/project/task profile with no adaptive updates | Explicitly UNSUPPORTED if a matched profile cannot be constructed | Canonical simple baseline |
| APPEND_LOG_SEARCH (simple_append_log_search) | Append-only event log with exact/lexical search | Must be marked unsupported if the fixture cannot provide the same authorized log | Required retrieval control |
| CURRENT_RETRIEVAL | Current authorized-record retrieval with lifecycle filtering | Must be marked unsupported if current retrieval cannot be isolated | Existing current-retrieval control |
| SIMPLE_ATC_RETRIEVAL_V3 (simple_atc_retrieval_v3) | Current ATC authorized retrieval and set compilation | Explicitly UNSUPPORTED if the existing deterministic baseline cannot be isolated | Canonical existing deterministic baseline |
| OPTIMIZED_CAPSULE | Best feasible Project Context Capsule under the frozen disclosure and token budget | Must be marked unsupported if capsule optimization cannot be reproduced | Primary continuity baseline |
| LONG_CONTEXT_CONTROL (simple_long_context) | Full feasible authorized prior transcript or long-context control under matched model, context, latency, and cost budgets | Explicitly UNSUPPORTED; never imputed as pass or fail | Required canonical control when supported |
| BEST_NON_ATC_HYBRID (hybrid_best_non_atc) | Best non-ATC combination of the eligible simple controls under the same budget | Explicitly UNSUPPORTED when no eligible hybrid can meet the boundary | Strongest non-ATC baseline |
| COMPETITOR_MEM0 (competitor_mem0) | Individual pinned Mem0 adapter cell using only genuinely supported operations | Explicitly UNSUPPORTED with pinned revision and reason when the boundary cannot be met | No competitor cell supports promotion unless its boundary is met |
| COMPETITOR_GRAPHITI (competitor_graphiti) | Individual pinned Graphiti adapter cell using only genuinely supported operations | Explicitly UNSUPPORTED with pinned revision and reason when the boundary cannot be met | No competitor cell supports promotion unless its boundary is met |
| COMPETITOR_HINDSIGHT (competitor_hindsight) | Individual pinned Hindsight adapter cell using only genuinely supported operations | Explicitly UNSUPPORTED with pinned revision and reason when the boundary cannot be met | No competitor cell supports promotion unless its boundary is met |
| COMPETITOR_LETTA (competitor_letta) | Individual pinned Letta adapter cell using only genuinely supported operations | Explicitly UNSUPPORTED with pinned revision and reason when the boundary cannot be met | No competitor cell supports promotion unless its boundary is met |
| COMPETITOR_LANGMEM (competitor_langmem) | Individual pinned LangMem adapter cell using only genuinely supported operations | Explicitly UNSUPPORTED with pinned revision and reason when the boundary cannot be met | No competitor cell supports promotion unless its boundary is met |
| MATCHED_HYBRIDS (hybrid_atc_governed and mechanism-specific) | Capsule plus checkpoint, reconciliation, M1, M3, or the mechanism under test with matched turns, total tokens, latency, tools, and permissions | Individual missing combinations remain explicit UNSUPPORTED | Mechanism comparisons |

Required ablations are separate named cells, not silently folded into a
winner: working checkpoints; episodic outcome records; temporal and relational
projections; procedure distillation and retrieval; typed event activation;
consequence contracts and checkpoint tokens; outcome dependency closure; the
full ATC research stack; checkpoint without reconciliation; reconciliation
without the M1 binding; M1 without dependency/invalidation closure; semantic
acknowledgement/challenge versus its content-free placebo; prospective memory
without negative guards, current-version reread, dependency closure, or action
ceiling; Conditional Failure Memory without disconfirmation; static warranty
without local use-time verification; M3 optimized rebuild versus independent
full rebuild; Continuity Debt aggregate versus category vector; and procedures
without applicability, rollback, or purge closure.

Unsupported, blocked, skipped, not-exercised, and missing cells are retained
with reason, denominator disposition, and capability boundary. They receive no
promotion credit and cannot be used as an implicit favorable baseline.
No result is promotable unless all applicable simple, individual competitor,
hybrid, and ablation cells were attempted or explicitly reported as
UNSUPPORTED with the reason and pinned boundary.

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

### 6.5 Opportunity, abstention, and witness contract

Before execution, a frozen mechanism-independent opportunity manifest and
oracle must identify eligible positive and negative opportunities. It uses the
same logical episode and source state for every arm and cannot inspect a
mechanism result before assigning eligibility. The eligible-opportunity
denominator `E_w` is frozen before execution for each workstream and arm:
every eligible opportunity enters `E_w` whether the mechanism acts, abstains,
errors, or returns unsupported. A missing run or missing status remains in
`E_w` and lowers coverage; it cannot be removed after seeing the mechanism
result. If the fixture cannot decide eligibility before execution, the oracle
records `INDETERMINATE_PRE_ELIGIBILITY` outside `E_w`; it may not use that
status to exclude an eligible opportunity after the fact.

Packet A preregisters directional tests against this same denominator:
`coverage = recorded eligible-opportunity statuses / E_w`, and
`non_abstention = count(SUPPORTED response statuses) / E_w`. Each
requires its preregistered directional confidence bound or test to clear the
declared floor. The minimum eligibility floors are:

**Packet A erratum (PACKET-A-ERRATUM-2026-08-30).** This corrected formula
supersedes the earlier subtraction-based wording: every eligible opportunity
has exactly one response status in the complete frozen response-status
partition. `UNSUPPORTED`, `BLOCKED`, `SKIPPED`, `NOT_EXERCISED`, `MISSING`,
`UNKNOWN`, `ABSTENTION`, `ERROR`, `INFRASTRUCTURE_FAILURE`, and `ATTRITION`
remain visible and receive no non-abstention credit; none can be removed or
relabelled after the outcome is observed.

| Workstream | Minimum positive opportunities | Minimum negative opportunities | Coverage test against frozen `E_w` | Non-abstention test against frozen `E_w` |
|---|---:|---:|---|---|
| Prospective memory | 50 due/cue opportunities | 50 non-due/negative-control opportunities | Preregistered directional bound/test clears 90% | Preregistered directional bound/test clears 90% |
| Adaptive routing | 50 beneficial-route opportunities | 50 no-benefit or wrong-route opportunities | Preregistered directional bound/test clears 90% | Preregistered directional bound/test clears 90% |
| Continuity Debt | 100 independently adjudicated avoidable-debt opportunities | 100 independently adjudicated non-debt opportunities | Preregistered directional bound/test clears 90% | Preregistered directional bound/test clears 90% |

These are minimum evidence floors, not outcome credit. If a floor is missed,
the stage is insufficiently exercised and cannot promote. Abstentions, errors,
unsupported cells, not-exercised cells, and missing witnesses receive no
promotion credit and remain visible in the report. Usefulness and outcome
utility are witnessed only by Core, the deterministic harness, or an
independent oracle; a model, client, tool, provider, or participant assertion
cannot close them. Prospective-memory and adaptive-routing promotion gates
must reference this exact frozen-`E_w` coverage/non-abstention rule, including
its positive/negative minimums; they may not substitute a mechanism-defined
scored-event denominator.

For every numerical gate, Packet A freezes the estimand, denominator,
direction, confidence method or test, and missingness treatment before the
episode is scored. No opportunity may be counted only because a mechanism
claimed it, and no abstention may be relabeled as a correct suppression.

### 6.6 Failed-run, missingness, and inference rules

The frozen Packet A specification carries these rules; the later benchmark
manifest must reproduce them before any confirmatory result:

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

### 6.7 Exact Packet A specification freeze now

The first decision freezes the Packet A specification, not a benchmark manifest
and not an execution N. The specification is frozen if and only if:

1. the arm vocabulary, task families, mutation classes, oracle contracts,
   budgets, permissions, and hard-safety rules are written and content-bound;
2. the CAOS definition, every estimand, denominator, direction, confidence
   method or test, multiplicity rule, and missing/failed-run disposition is
   fixed before any confirmatory result;
3. the calibration pilot is explicitly calibration-only and cannot alter the
   confirmatory estimand, control set, holdout, or gate;
4. the opportunity, abstention, witness, secret-refusal, idempotency,
   conflict, invalidation, ordinary-deletion, terminal-purge, and rebuild
   contracts are fixed;
5. the power-simulation inputs and reproducibility record in Section 6.8 are
   specified, including the script path, version, digest fields, input/output
   manifest digests, seed, repetitions, joint distribution, allocation,
   estimator, test, alpha, power, and multiplicity; and
6. the specification digest is recorded without representing provisional N as
   an executable or fixture-frozen sample.

Packet A specification freeze is permitted now as a non-displacing research
decision. It does not freeze fixture IDs, a benchmark manifest, a final N, or
execution. Those require the later reproducibility and fixture gates.

### 6.8 Power-derived N and later manifest freeze

The provisional planning value is N=384 paired episodes. It remains a planning
value only. The later manifest must bind to the independently emitted derived N
from the exact reproducibility record below, whatever that N is; the
specification must not claim that N=384 is frozen or required before that run.

~~~text
power_simulation_script_path: bench/memory_reliability_power_simulation.py
power_simulation_script_version: packet-a-power-v1
power_simulation_script_digest: required SHA-256 at manifest freeze
input_manifest_paths:
  - bench/memory_reliability_spec.json
  - bench/memory_reliability_fixtures.json
  - Packet A fixture/task manifest
input_manifest_digest: required SHA-256 at manifest freeze
output_manifest_digest: required SHA-256 emitted by the simulation
simulation_seed: 20260829
simulation_repetitions: 100000
baseline_control_caos: 0.75
alternative_caos: 0.85
target_paired_effect: 0.10
paired_joint_distribution:
  control_0_alternative_0: 0.10
  control_0_alternative_1: 0.15
  control_1_alternative_0: 0.05
  control_1_alternative_1: 0.70
paired_correlation: 0.404226, derived from the frozen joint distribution
stratum_weights: equal across six families, four repositories, four strata
allocation: four repetitions per family/repository/stratum cell
estimand: stratified paired CAOS difference, alternative minus control
test_statistic: stratified paired difference with exact/randomization reference
alpha: familywise 0.05 with Holm control over two primary contrasts
directional_bound: one-sided 95% confidence bound for each promotion gate
power_target: 0.90
noninferiority_margin: -0.02 CAOS difference
missing_and_failure_policy: Section 6.6, frozen before simulation
provisional_confirmatory_N: 384 paired episodes
final_confirmatory_N: unset until script, inputs, and output digest reproduce it
~~~

The script path is a required future reproducibility artifact; this proposal
does not claim that the script has already been added or executed. The
simulation must independently emit the paired joint-distribution check,
stratum allocation, derived N, and output manifest digest. Any change to the
script, version, input manifest, joint distribution, allocation, estimator,
test, alpha, power, missingness rule, or seed creates a new specification
version and leaves N provisional.

The later benchmark-manifest freeze is permitted only when:

1. the manifest lists the reproduced final N, episode IDs, six task families,
   four fixture repositories, four client/model-build strata, four repetitions,
   reserve policy, and deterministic episode seeds;
2. every arm, baseline/control, primary contrast, oracle, budget, permission
   set, mutation, and required ablation is versioned and content-digested;
3. the exact script path, version, script digest, input-manifest digest, and
   output-manifest digest bind the independently derived N under the Section 6.8
   inputs; the derived N is not required to equal the provisional planning value;
4. calibration, fixture determinism, receipt completeness, oracle behavior,
   secret refusal, project isolation, lifecycle cleanup, and budget gates pass;
5. CAOS, hard-safety gates, raw numerators/denominators, confidence methods,
   Holm multiplicity control, opportunity floors, and all missing/failed-run
   dispositions are unchanged from the frozen specification; and
6. the benchmark-manifest digest is recorded before any confirmatory result is
   read.

This distinction is binding: freeze the Packet A specification now; freeze
and execute the benchmark manifest only after the reproducibility and fixture
gates pass. Neither step grants product acceptance.

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
failures. The preregistered one-sided 95% lower confidence bound for the CAOS
difference must be at least negative two percentage points. The one-sided 95%
lower confidence bound for the relative continuity-debt reduction, with the
independent opportunity denominator fixed in Packet A, must be at least 20%.
The one-sided 95% lower confidence bound for first-action correctness
difference must be at least zero. The one-sided 95% upper confidence bound
for context must remain below the full-transcript control and no more than
25% above the capsule-only condition. Each estimand, denominator, and
direction is frozen before the holdout is opened.

**Next stage.** A separately authorized Core-owned checkpoint projection may
cross a cooperating lifecycle-aware client boundary, with exact snapshot,
policy, principal, and dependency bindings and a challenge path for
displacement or uncertainty.

**Kill/narrow rule.** If the added checkpoint or reconciliation is noninferior
to the optimized capsule, increases context without measurable outcome value,
or causes any hard lifecycle failure, retain only the smallest useful typed
checkpoint or diagnostic vector and do not add richer state.

### 7.2 M3 dependency-complete closure

M3 is a dedicated active workstream and is distinct from M1. M1 measures the
assignment-to-outcome chain; M3 proves that correction, deletion, policy
change, and terminal purge withdraw every declared derived influence before
anything is republished. Owner: Core closure/rebuild owner with the Memory Lab
coordinator. Packet: Packet N — M3 closure oracle. Its authority ceiling is
research-only: the optimizer may choose an evaluation order or repair plan in
an isolated harness, but it may not create canonical records, issue context,
publish a repaired surface, suppress an action, or become a live authority.

**Stage 1 — isolated full-rebuild closure.** The frozen M3 fixture contains a
complete inventory of six declared derived surfaces:

1. retrieval selection;
2. issued context;
3. procedure;
4. selection cache;
5. working state; and
6. use statistics.

The independently coded full-rebuild oracle and the optimized affected-
descendant rebuild run correction, scope narrowing, permission revocation,
ordinary deletion, terminal purge, and policy-generation changes over chain,
fan-out, fan-in, shared-descendant, illegal-cycle, and illegal-cross-scope
topologies. Publication is withdrawn before republication. The fixture scans
the complete six-surface inventory, including graph or dependency inventory,
rather than checking only currently published artifacts.

**Promotion gate.** The exact-equality closure gate requires byte-for-byte
equality between optimized and full-rebuild outputs for every declared case,
every six-surface artifact, every dependency disposition, and every terminal
state. It also requires zero published stale descendants, zero terminal-purge
residue, zero stale-writer acceptance, zero illegal-edge acceptance, zero
ordinary-delete/purge conflation, zero fail-open or partial-repair publication,
and complete six-surface inventory coverage of 1.0. The optimization test is
the preregistered evaluated-node reduction in the declared synthetic control;
the retained ADR-049 threshold is at least 0.99. These are deterministic tests,
not point estimates: equality and all hard-safety counts must pass before the
optimization result is considered.

**Next stage.** After the isolated result is independently reproduced at L2,
M3 may enter shadow integration with the M1 dependency and invalidation
receipts when the canonical product DAG reaches ZF-017 and ZF-018. This is
evidence for those stages, not a new product prerequisite and not a license to
reorder ZF-015 or ZF-016. Any Core-owned live slice requires a separate
decision, complete declared-surface inventory, withdrawal-before-republication,
and the same full-rebuild equality oracle.

**Kill/narrow rule.** If exact equality or complete inventory coverage fails,
hold all optimized promotion and retain the full-rebuild oracle plus the
minimal failing case as the active falsifier. If equality passes but the 0.99
optimization threshold fails, kill only the optimization, retain the
dependency-complete M3 closure contract, and use the full rebuild. If purge,
stale-writer, illegal-edge, or fail-open behavior appears, narrow to an
unoptimized isolated surface until the defect is closed. M3 remains active in
each case; no negative result deletes the closure idea.

### 7.3 External-state applicability and memory warranties

Static applicability and use-time verification are separate artifacts.

**Stage 1A — static typed applicability, diagnostic-only.** Core compiles a
typed descriptor from authorized project, source, workspace, policy, and
capability metadata. The descriptor is immutable and binds a record/version
and snapshot through any later verification, issue, and use. It contains no
executable predicate and cannot by itself justify issue or use reliance.

**Stage 1B — first live local verification, diagnostic-only.** Only Core-owned
authorized workspace/local metadata may be read: bounded files or digests,
repository and branch identity, branch lineage, lockfile/dependency
generation, migration head, operating system/architecture, and declared
capability. There is strict path containment, link/reparse refusal, bounded
reads, no network, subprocess, hooks, ambient credentials, or unbounded
filesystem walk. An unavailable result is portable as unavailable and never
as verified. Stage 1 may annotate a lab result; it may not make any issue or
use rely on the annotation.

For any later issue or use reliance, Core must atomically bind the current
Core generation, immutable descriptor, exact record/version, and snapshot,
then recheck generation and state immediately at use. Any generation, state,
descriptor, policy, project, or dependency change fails closed and prevents
reliance. A client or source adapter cannot perform this recheck.

**Promotion gate.** The preregistered stale-issue prevention estimand is the
rate difference on eligible stale high-impact issue opportunities between
local use-time verification and static typed applicability; its one-sided 95%
lower confidence bound must be greater than zero. The false-invalidation
estimand is the rate on eligible still-valid opportunities; its one-sided
95% upper confidence bound must be at most two percentage points. There must
also be zero unauthorized reads, secret exposure, containment failures, or
purge residue, and every reliance must pass the atomic Core generation
binding/recheck.

**Next stage.** Remote verification remains active as a separate research
stage. It requires a fresh threat model, endpoint allowlist, least-privilege
credentials held outside artifacts, paired local controls, explicit remote
consent, and an unavailable state on any unsupported or failed endpoint.

**Kill/narrow rule.** If static typed applicability is noninferior, retain the
static contract and do not add probes. If local probes add reads, latency,
support burden, or false invalidation without preventing stale influence,
narrow to static descriptors. Remote verification is not a reason to weaken
the local boundary.

### 7.4 Semantic acknowledgement/challenge

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
The placebo is the content-free matched extra-feedback-turn placebo: it
receives the same number of turns, total-token
budget, latency budget, and host opportunity, but contains no semantic field
IDs, coverage codes, uncertainty codes, or prose. The placebo has no
authority or side effect.

**Promotion gate.** The acknowledgement/challenge must reduce early
constraint/state errors by at least 20% relative to the ordinary optimized
capsule, with a preregistered one-sided 95% lower confidence bound for the
relative reduction at least 20%. Its semantic-content benefit over the
content-free placebo must have a one-sided 95% lower confidence bound greater
than zero on the same eligible early-error estimand. The one-sided 95% upper
confidence bound for acknowledgement overhead must be at most 15% of total
task context. There must be no authority, privacy, project, correction, or
purge failure. Report semantic-content benefit separately from generic
feedback-turn benefit; self-report alone does not pass.

**Next stage.** A gated richer-correction stage may issue a minimum corrective
delta composed only of closed field IDs and correction codes. It must preserve
Core authority, exact versions, permissions, dependency closure, and an
explicit challenge/abstention path. A richer client acknowledgement or
durable handoff requires a separate L3 lifecycle capability and confirmation.

**Kill/narrow rule.** If acknowledgement content does not predict or correct
later behavior, becomes confident model self-report, or loses to a larger
one-shot capsule at matched total context and latency, retain the ephemeral
coverage/uncertainty diagnostic and do not add prose or durable state.

### 7.5 Prospective memory

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
   with a preregistered one-sided 95% lower confidence bound at or above each
   floor on the fixed positive-opportunity denominator;
3. the one-sided 95% lower confidence bound for the CAOS difference is at
   least negative two percentage points against the strongest control;
4. the one-sided 95% upper confidence bound for false alarms is at most 5% of
   eligible non-due opportunities;
5. unauthorized, stale, deleted, purged, wrong-domain, duplicate, or
   unconfirmed-protected influence is zero;
6. a paired scheduler benefit is present: the one-sided 95% lower confidence
   bound for relative improvement on the preregistered outcome-utility
   endpoint is at least 5%; and
7. the result is not explained only by larger prompt exposure, latency, or
   context, and the fixed positive/negative minimums plus the frozen-`E_w`
   directional coverage/non-abstention floors in Section 6.5 are met.

**Next stage.** A separately accepted notification-only path may create a
user-visible notification receipt for an exact recipient and target. Later
suggest or draft behavior requires a new paired gate. Protected execution
requires fresh exact native confirmation and its own action/effect evidence.

**Kill/narrow rule.** If the deterministic scheduler is noninferior, retain
the scheduler and the typed transaction contract while narrowing the learned
or compiled mechanism. If false alarms, disclosure, duplicate issue, or
correction/purge closure fail, return to inert local reference. No notification
or action ceiling is silently inherited by a weaker stage.

### 7.6 Conditional Failure Memory

Conditional Failure Memory remains active but a failure never becomes an
unconditional prohibition.

**Stage 1 — advisory only.** Store a typed action signature, observed failure
class, Core-owned or host-observed allowlisted state, state fingerprint,
uncertainty/abstention, supporting receipt references, retry-when and
do-not-retry-while codes, expiry, correction, disconfirmation, and retirement.
Stage 1 may warn or expose an advisory code; it cannot block a retry, alter a
tool call, write a procedure, or trigger a protected action.

**Promotion gate.** The preregistered repeated-ineffective-action estimand,
whose denominator is the independently observed eligible retry opportunities,
must fall by at least 20% against raw failed-trajectory retrieval and simple
error-signature deduplication, with a one-sided 95% lower confidence bound for
the relative reduction at least 20%. The one-sided 95% upper confidence bound
for incorrectly blocked valid retries and negative transfer must be at most
two percentage points. There must be no wrong-project influence, secret
persistence, or correction/purge failure, and all usefulness and outcome
utility must be witnessed by Core, the deterministic harness, or an
independent oracle.

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

### 7.7 Continuity Debt Ledger

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
20% episode set with the independent opportunity denominator fixed, all of the
following hold:

- out-of-sample CAOS prediction AUC is at least 0.70 with a bootstrap 95%
  lower bound of at least 0.65;
- a one-standard-deviation lower debt value predicts better CAOS with an odds
  ratio of at most 0.80 and a 95% upper bound below 1.00;
- a preregistered stratified permutation test rejects random directional
  agreement across the six task families at one-sided alpha 0.05;
- the one-sided 95% lower confidence bound for the aggregate's incremental
  held-out AUC over the category vector and strongest simple control is
  greater than zero; and
- no hard authority, privacy, project, correction, or purge failure occurs.

**Next stage.** If the gate passes, use the aggregate only as a bounded
research/product metric with versioned weights, category drill-down, and
opportunity coverage. Pair it with CAOS, usefulness, cost, and latency rather
than treating it as a replacement endpoint.

**Kill/narrow rule.** Kill the aggregate if categories move in contradictory
directions, weights dominate, opportunity attribution is not reliable, or the
held-out gate fails. Retain the independently useful category vectors,
abstention counts, and correction/verification/new-requirement distinctions.

### 7.8 Verified experience and procedures

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
success, source/outcome/policy closure, and terminal-purge closure. The
preregistered repeated-ineffective-action estimand must fall by at least 20%
against static notes, raw trajectory retrieval, and simple error-signature
deduplication, with a one-sided 95% lower confidence bound for the relative
reduction at least 20%. The one-sided 95% upper confidence bound for
incorrectly blocked valid retries must be at most two percentage points, and
the one-sided 95% lower confidence bound for the CAOS difference must be at
least negative two percentage points. All denominators and opportunity
eligibility are frozen before evaluation.

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

### 7.9 Design-partner validation

Design-partner work remains active as the route to L5 evidence, not as a
shortcut to support or marketing claims.

**Stage 1 — preparation-only.** Prepare consent materials, candidate-digest
binding, local-only storage design, participant-controlled export/deletion,
attrition rules, and inspectable structured reports. Do not recruit, collect
participant data, grant external access, or execute an L5 study from this
proposal. A separate explicit decision is required before any recruitment,
participant collection, external access, or L5 execution. If that decision is
made, the study uses two or three technically competent participants with
explicit consent, exact candidate digest, declared artifact/capability pair,
local-only default, no raw text, and no credentials.

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

### 7.10 Adaptive memory routing

Adaptive routing remains active after the earlier workstreams. It may compare
exact current records, source-backed evidence, capsules, checkpoints, temporal
history, conditional failures, verified procedures, typed relations, and raw
source dereference.

**Stage 1.** Shadow-only routing over already authorized, applicable,
dependency-bound projections. No router may inspect unauthorized candidates,
become a permission authority, or create a truth record.

**Promotion gate.** For the fixed target-task opportunity denominator, the
router must beat the current lexical and capsule baseline on the preregistered
CAOS estimand with a one-sided 95% lower confidence bound for improvement
greater than zero. The one-sided 95% upper confidence bounds for disclosure,
latency, and maintenance-cost differences must remain at or below their
predeclared noninferiority margins. The minimum positive/negative opportunity
counts and the directional coverage/non-abstention tests against the frozen
`E_w` denominator in Section 6.5 must be met; abstentions, errors, and
unsupported cells receive no credit. There must be no hard lifecycle failures.

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
| Packet A benchmark | Memory Lab coordinator | Packet A | First decision accepts the frozen specification; no product dependency | Specification digest now; later manifest digest and final N only after independent reproducibility and fixture gates, or a recorded blocked result | Parallel, non-displacing |
| M1 measurement spine | Core contract owner and Memory Lab coordinator | Packet C | Packet A freeze and artifact boundary review | Exact replay, outcome association, unknown/abstention, idempotency, conflict, invalidation, deletion, purge, rebuild, and secret refusal pass | Shadow/lab; no production data collection |
| M3 dependency-complete closure | Core closure/rebuild owner and Memory Lab coordinator | Packet N | M3 field contract, six-surface inventory, and independent full-rebuild oracle specification | Six-surface inventory, withdrawal-before-republication, exact equality, hard-safety, and optimization tests pass or a bounded retain/hold/kill disposition is recorded | Active and distinct from M1; shadow-only |
| Versioned checkpoints | Continuity contract owner | Packet D | M1 shape and typed field contract | Checkpoint compiles only observable state and survives restart, correction, abandonment, and purge tests | Active; mapped to ZF-015 |
| Three-way reconciliation | Continuity contract owner | Packet E | Checkpoint plus current source snapshot plus client observation | Current/displaced/uncertain/invalid/new/unavailable classes are deterministic and no displaced action is resurrected | Active; mapped to ZF-016 |
| Typed applicability | Applicability contract owner | Packet G | Reconciliation and dependency inventory | Static descriptor and local verification pass containment, unavailable, binding, and closure gates | Active; local-only first |
| Semantic acknowledgement/challenge | Handoff research owner | Packet F | Checkpoint/capsule projection and matched benchmark | Closed-field acknowledgement improves handoff or remains a bounded diagnostic | Active; no checksum claim |
| Prospective memory | Event-memory research owner | Packet H | M1 outcome path and scheduler controls | Inert local stage and conjunctive gate pass; notification path receives separate decision | Active; stronger path sequenced |
| Conditional Failure Memory | Outcome-learning research owner | Packet I | Advisory receipts and allowlisted state | Advisory gate passes; L3 synchronous suppression research remains separate | Active; no Stage 1 blocking |
| Continuity Debt Ledger | Evaluation owner | Packet B | Independent opportunity oracle and blinded omission protocol | Category vector is reliable; aggregate score only if exact held-out gate passes | Active; vector first |
| Verified experience/procedures | Procedure contract owner | Packet J | Accepted outcome/applicability/closure evidence | Advisory candidate passes recurrence/verification, repair, rollback, and purge gates | Active; no autonomous effects |
| Design partners | External-validation owner | Packet K | Relevant L4 evidence, consent materials, exact digest | L5 evidence is consented, local-first, de-identified, and attrition-bounded after a separate explicit decision | Active; preparation-only until separately authorized |
| Caches and reports | Privacy/lifecycle owner | Packet L | Artifact matrix and dependency inventory | Rebuild, backup/export/restore, ordinary deletion, terminal purge, and external-copy closure pass | Cross-cutting; derived only |
| Adaptive routing | Retrieval research owner | Packet M | Earlier workstreams and accepted simpler baselines | Target-task win with matched budgets and closure evidence | Active later stage; shadow first |

### 9.1 Packet coverage

Packet A freezes the specification. Packet B produces debt vectors. Packet C
implements M1 shadow receipts. Packet D compiles checkpoints. Packet E performs
three-way reconciliation. Packet F tests semantic acknowledgement/challenge.
Packet G owns static applicability and bounded local warranties. Packet H keeps
prospective memory and its notification path active. Packet I keeps Conditional
Failure Memory active. Packet J keeps verified experience and procedures active.
Packet K keeps design-partner evidence active. Packet L closes artifact
retention, deletion, purge, rebuild, export, restore, replication, logging,
and external-copy behavior. Packet M keeps adaptive routing active. Packet N
keeps M3 dependency-complete closure, its independent full-rebuild oracle,
six-surface inventory, withdrawal-before-republication, exact-equality gate,
and optimization disposition active.

Each packet must report entry, exit, evidence grade, unresolved unknowns,
support/cost metrics, and disposition. A negative result narrows the stage; it
does not delete the mechanism from the program.

## 10. Limited external validation, claims, and risks

### 10.1 Design-partner boundary

The potential first L5 entry is two or three consented design partners using
disposable or non-sensitive projects under an explicit experimental label.
That entry is preparation-only until a separate explicit decision authorizes
recruitment, participant collection, external access, and L5 execution.
Reports may include only typed codes, exact artifact digests, bounded metrics,
attrition, and participant-approved aggregate outcomes. No raw prompts,
transcripts, commands, credentials, imported prose, or provider/model prose
leave the local boundary.

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
> direction and freeze the corrected Packet A specification now, while
> explicitly deferring benchmark-manifest freeze and execution until
> independent reproducibility and fixture gates pass; keep every later packet
> active and sequenced.

Acceptance of this direction means:

- the active frontier remains the blocking product path;
- Packet A specification may freeze as non-displacing research, but its
  benchmark manifest and execution N remain provisional until the independent
  reproducibility and fixture gates pass;
- Packet A and shadow M1/M3 research may run in parallel, but none is a product
  prerequisite and neither reorders ZF-015 or ZF-016;
- M1, M3 dependency-complete closure, checkpoints, reconciliation, applicability, semantic
  acknowledgement/challenge, prospective memory, Conditional Failure Memory,
  warranties, continuity debt, verified experience, procedures, design
  partners, and adaptive routing all remain active in the register;
- each mechanism must pass its own Stage 1, promotion, next-stage, and
  kill/narrow rules; and
- no production schema, data collection, external access, suppression,
  execution, release, support, or marketing authority is implied.

This is the proposed decision and sequencing contract, not a claim that any
later stage has already passed.
