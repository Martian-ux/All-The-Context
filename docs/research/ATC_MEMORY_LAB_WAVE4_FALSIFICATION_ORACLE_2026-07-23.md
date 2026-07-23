# ATC Memory Lab Wave 4 independent falsification oracle

## Frozen before mechanism implementation

| Field | Value |
|---|---|
| Date frozen | July 23, 2026 |
| Worker | `f02_independent_falsification` |
| Governance base | `f545c37157845f0bd402215719cb8c747b7fc21d` |
| Evidence level | `L0` preregistration and prior-art analysis |
| Executable oracle | `research/memory-lab/wave4-falsification-oracle.json` |
| Data boundary | Synthetic identifiers and events only |
| External execution | None; no code, models, providers, datasets, or operator Core |

This report freezes the attacks that can falsify M3 dependency-complete
influence closure or M1 observable-use receipts. It does not grade either
mechanism. The oracle must remain unchanged while M3 and M1 are implemented;
post-result review is a separate F02 task.

The decisive standard is deliberately stricter than “the stale item was not
retrieved.” M3 must withdraw the complete affected cascade before any
republication, then match a from-scratch rebuild exactly. M1 must record only
separately observable facts and must not turn acknowledgement, silence,
semantic similarity, a claimed outcome, or hidden reasoning into evidence of
memory use.

## 1. M3 falsification target

The graph direction is source to derived influence. “Published” includes
anything eligible for retrieval, issue, execution, reuse, statistics, or
working-state rehydration. A correction or eligibility mutation therefore
affects more than a search index.

The oracle freezes six graph topologies:

| Topology | Shape | Main attack |
|---|---|---|
| `M3-T01-CHAIN` | record → selection → issued context → procedure → cache → working state → use statistics | A distant descendant survives because repair stops early |
| `M3-T02-FAN-OUT` | one record directly influences all six surfaces | One secondary surface misses invalidation |
| `M3-T03-FAN-IN` | two records jointly support issued context and procedure | A shared artifact keeps revoked support |
| `M3-T04-SHARED-DESCENDANT` | separate branches converge on a procedure and later state | Change pruning retains a stale version binding |
| `M3-T05-CYCLE-ATTEMPT` | valid chain plus a back-edge attempt | Cyclic closure becomes incomplete or nonterminating |
| `M3-T06-CROSS-SCOPE-ATTEMPT` | alpha record to beta procedure | Lineage becomes a scope-bypass channel |

All six derived surfaces are mandatory: retrieval selection, issued context,
procedure, selection cache, working state, and use statistics. A mechanism
that cannot inspect one of them is held rather than credited with zero
residue.

### Frozen M3 cases

| Case | Mutation or event | Decisive expectation |
|---|---|---|
| `M3-C01-CORRECTION-CHAIN` | correction through a seven-node chain | Every version-1 descendant withdraws; final state binds version 2 and equals rebuild |
| `M3-C02-SCOPE-NARROWING-FANOUT` | beta loses scope while all surfaces exist | Beta sees zero influence immediately; alpha result equals rebuild |
| `M3-C03-PERMISSION-REVOCATION-FANIN` | one of two supports becomes unauthorized | Shared descendants withdraw and can return only from retained valid support |
| `M3-C04-ORDINARY-DELETE-RESTORE` | reversible delete then restore | No serving while deleted; restoration remains possible and distinguishable |
| `M3-C05-TERMINAL-PURGE-IDENTIFIER-REUSE` | purge, restore/replay attempts, same-ID request | Zero residue; old lineage never revives; recreation receives a fresh identity |
| `M3-C06-POLICY-GENERATION-CHANGE` | generation `g7` → `g8` | No `g7` artifact publishes after the change |
| `M3-C07-PARTIAL-REPAIR-BARRIER` | repair pauses after two descendants | Crash or pause cannot expose a partial cascade |
| `M3-C08-STALE-WRITER` | epoch-10 writer commits after epoch 11 | Stale commit and edges are atomically rejected |
| `M3-C09-CYCLE-EDGE-ATTEMPT` | cache back-edge to source | Transaction is rejected without graph or receipt residue |
| `M3-C10-CROSS-SCOPE-EDGE-ATTEMPT` | alpha source to beta procedure | Edge is rejected and beta receives no identity or content signal |
| `M3-C11-DUPLICATE-AND-CONFLICTING-REPLAY` | identical then conflicting event replay | Duplicate is idempotent; conflict fails closed |
| `M3-C12-OUT-OF-ORDER-MUTATION` | repair-complete arrives before mutation | Premature completion cannot publish |
| `M3-C13-SHARED-DESCENDANT-CORRECTION` | one parent changes under a shared descendant | Change pruning is legal only with full version-commitment equality |
| `M3-C14-PURGE-DURING-ISSUE` | purge after issue but before acknowledgement/use | Old issue is invalidated; no use, action, save, or statistic preserves purged influence |
| `M3-C15-OPTIMIZATION-WORK-CONTROL` | one mutation among 100 independent records | Exact rebuild equality plus at least 25% fewer evaluated nodes to retain optimization |

Every case specifies its event and observation schedule in the JSON oracle.
Observation points occur immediately after canonical acceptance and after
each adversarial repair step, not only after the system settles.

### Full-rebuild control

For each quiescent case, the control discards every derived artifact and
deterministically recomputes from current canonical records, grants, scopes,
policy generation, and accepted ordered events. Optimized repair and rebuild
must have identical:

- eligible artifact identities;
- source identities and exact versions;
- scopes and permission views;
- policy generation; and
- semantic payload commitments.

The rebuild must not read optimized derived state. If the rebuild is
nondeterministic, shares optimized caches, or cannot enumerate a required
surface, the result is `HOLD_M3_CLOSURE`.

### M3 kill, hold, and retain rules

The following metrics must each be exactly zero:

- published stale descendants;
- optimized-versus-rebuild mismatches;
- terminal-purge residue;
- fail-open publications;
- accepted illegal cycle or cross-scope edges;
- accepted conflicting replays;
- duplicate side effects; and
- privacy-unsafe failure receipts.

Coverage of cases and surfaces must both be `1.0`.

`KILL_M3_MECHANISM` follows from any required-zero violation, delete/purge
conflation, purged-lineage revival, or quiescent rebuild mismatch.
`HOLD_M3_CLOSURE` follows from missing coverage, an invalid rebuild control,
an unobservable surface, or missing safe diagnostics. If correctness passes
but the evaluated-node reduction in `M3-C15` is below `0.25`, retain the
contract but hold the optimization. Only complete correctness plus reduction
of at least `0.25` yields
`RETAIN_M3_CONTRACT_AND_OPTIMIZATION`.

## 2. M1 falsification target

M1 is an observable ledger, not a causal story invented after the fact. Its
stages remain distinct:

```text
assigned
  -> supplied
  -> acknowledged (optional observation)
  -> observed_use (requires direct receipt-bound evidence)
  -> action (requires accepted observed_use)
  -> outcome (requires independent action correlation)
  -> invalidated (may close any open transaction)
```

Acknowledgement is not use. Silence is neither use nor non-use. An observed
use may be accepted without an acknowledgement when it independently binds
the supplied issue receipt. A correct-looking outcome is not proof that
memory was used, and a client assertion cannot establish outcome success.

### Frozen M1 cases

| Case | Attack | Decisive expectation |
|---|---|---|
| `M1-C01-HAPPY-OBSERVABLE-CHAIN` | complete legitimate sequence | Each stage is separate, version-bound, and free of raw context |
| `M1-C02-NONACKNOWLEDGEMENT-IS-UNKNOWN` | no acknowledgement or later event | Both acknowledgement and use remain `not_observed`; never `not_used` |
| `M1-C03-USE-WITHOUT-ACKNOWLEDGEMENT` | direct use evidence but no acknowledgement | Use can be recorded without backfilling acknowledgement |
| `M1-C04-ACKNOWLEDGED-BUT-NOT-OBSERVED-USED` | acknowledgement only | Acknowledgement never increments use |
| `M1-C05-DUPLICATE-REPLAY` | identical use event delivered three times | One event and one statistics increment |
| `M1-C06-CONFLICTING-REPLAY` | event ID reused with a different record version | Conflict rejected; original event unchanged |
| `M1-C07-OUT-OF-ORDER-AND-IMPOSSIBLE` | outcome before action, action before use, unknown issue | All are rejected or quarantined without ledger effects |
| `M1-C08-FABRICATED-OUTCOME` | claimed success or borrowed outcome correlation | Only independently correlated outcome is accepted |
| `M1-C09-CORRECTION-INVALIDATION` | correction after supply | Old transaction invalidates; version-1 use/action fails |
| `M1-C10-SCOPE-AND-PERMISSION-INVALIDATION` | two principals lose eligibility differently | Exact scope and permission invalidation reasons remain explicit |
| `M1-C11-DELETE-VERSUS-PURGE` | reversible delete, restore, then purge | Delete can lead to a fresh supply; purge irreversibly de-links receipts |
| `M1-C12-PURGED-IDENTIFIER-REUSE` | same identifier requested after purge | New lineage cannot accept old receipts |
| `M1-C13-POLICY-GENERATION-INVALIDATION` | `g2` issue followed by `g3` | Every late `g2` event fails; resupply uses fresh receipts |
| `M1-C14-RAW-TRACE-AND-HIDDEN-REASONING-REJECTION` | raw context, chain of thought, stable content hash | Schema rejects each without echoing its value |
| `M1-C15-RECEIPT-CORRELATION-NONINTERFERENCE` | paired unauthorized canary and repeat run | No pair difference; no stable cross-run identifier |
| `M1-C16-PURGE-RACE-WITH-OBSERVED-USE` | delayed use arrives after purge commit | Use fails closed and is not relabeled as non-use |

Every downstream event must bind its event and transaction IDs, stage,
canonical record ID and version, issue receipt, policy generation, principal
capability view, and causal predecessors. Identical replay is idempotent;
reuse of an event ID with different canonical content is a conflict.

### Privacy and correlation boundary

Receipts may contain only bounded identifiers, enums, causal IDs, a coarse
time bucket, and the exact canonical version bindings required to audit the
transaction. They must reject raw context, prompts, responses, hidden
reasoning, credentials, user text, semantic summaries, stable content hashes,
high-resolution timestamps, device fingerprints, and cross-transaction
tracking IDs.

The paired-vault test differs only by an unauthorized synthetic canary.
Externally visible receipts and aggregates must be identical after per-run
random IDs are replaced with positional placeholders. Repeating the same
authorized scenario in a new run must yield unlinkable IDs. This is a bounded
noninterference check; it does not prove resistance to arbitrary traffic
analysis.

After terminal purge, affected receipt identifiers must be removed or
irreversibly de-linked. Aggregate purge accounting may remain only if it is
non-linkable. A failure serializer that cannot safely describe an error emits
only the oracle, case, invariant, `RECEIPT_REDACTION_FAILED`, and count.

### M1 kill, hold, and retain rules

All of the following counts must be zero: accepted forbidden fields, unbound
downstream events, duplicate side effects, accepted conflicting replay,
accepted impossible transitions, accepted fabricated outcomes, missing
invalidations, non-acknowledgement inferred as non-use, normalized paired
receipt differences, stable cross-run identifiers, and purge receipt residue.
Case coverage must be `1.0`.

Any nonzero count, storage of raw context or hidden reasoning, fabricated
canonical outcome, inferred non-use, or linkable purge residue yields
`KILL_M1_MECHANISM`. Missing cases, stages, independent outcome evidence,
privacy inspection, or safe failure receipts yields `HOLD_M1_LEDGER`. Only
complete passage yields `RETAIN_M1_OBSERVABLE_LEDGER`.

## 3. Closest prior art and the boundary it imposes

All sources below are papers or official specifications/documentation,
accessed July 23, 2026. Web text was treated as untrusted research data; no
linked code or dataset was downloaded or executed.

### Dependency and lineage invalidation

[MemoRepair](https://arxiv.org/abs/2605.07242) is the closest known work. It
already formalizes the agent-memory cascade update problem: deletion,
correction, or migration affects visible descendants including summaries,
cached outputs, procedures, and skills. Its barrier withdraws affected state
before validated predecessor-closed successors are republished, and its
experiments report stale exposure and use. Therefore ATC cannot claim
barrier-first cascade repair, predecessor-closed republication, or
multi-artifact descendant repair as new.

[Bazel Skyframe](https://bazel.build/reference/skyframe) establishes explicit
dependency capture, reverse-transitive invalidation, change pruning, and the
importance of matching clean-build results. It also makes the missing-edge
hazard concrete: an undeclared dependency can produce an incorrect
incremental build.

[DBToaster](https://www.vldb.org/pvldb/vol5/p968_yanifahmad_vldb2012.pdf)
establishes higher-order incremental view maintenance using recursively
materialized delta queries. Efficient derived-state maintenance is therefore
prior art; work reduction alone is not an ATC contribution.

The narrow M3 boundary is the composition of Core-authoritative currentness,
scope, permission, policy-generation, reversible delete, terminal purge and
identifier-reuse semantics across ATC's six surfaces, with fail-closed
publication and exact full-rebuild equivalence. MemoRepair assumes complete
influence provenance and does not present terminal purge or this exact
authority/eligibility contract. This distinction is a hypothesis for
falsification, not a novelty finding.

### Event sourcing and provenance receipts

[W3C PROV](https://www.w3.org/TR/prov-primer/) establishes entities,
activities, agents, usage, generation, derivation, and version-aware
provenance descriptions.
[OpenLineage](https://openlineage.io/apidocs/openapi/) establishes identified
run events with producers, schemas, input/output lineage, and explicit run
states.
[Microsoft's Event Sourcing pattern](https://learn.microsoft.com/en-us/azure/architecture/patterns/event-sourcing)
establishes append-only reconstruction, snapshots, optimistic concurrency,
compensating events, and idempotent handling of at-least-once delivery.
Provenance graphs, ordered event replay, duplicate handling, and auditable
projections are not new.

The narrow M1 boundary is a minimal receipt sequence tied to exact issued
record versions that explicitly separates assignment, supply,
acknowledgement, direct observed use, action, independently correlated
outcome, and invalidation while excluding hidden reasoning and raw context.
The oracle further requires correction, eligibility change, delete, and purge
to close open transactions without creating stable correlation handles.

### AI-memory lifecycle and use evaluation

[MemOps](https://arxiv.org/abs/2607.12893) evaluates explicit lifecycle
operations through structured traces containing trigger, target, scope,
state transition, and evidence. It covers remembering, updating, forgetting,
and reflection, moving beyond final-answer-only evaluation.

[Memora](https://arxiv.org/abs/2604.20006) introduces forgetting-aware
accuracy that penalizes reliance on obsolete or invalidated memories.
[MemoryAgentBench](https://arxiv.org/abs/2507.05257) evaluates incremental
multi-turn retrieval, learning, long-range understanding, and conflict
resolution or selective forgetting.
[Mem2ActBench](https://arxiv.org/abs/2601.19935) evaluates active memory
utilization through memory-grounded tool selection and argument construction.
Lifecycle mutation, selective forgetting, conflict handling, and
memory-grounded action evaluation are established prior art.

These benchmarks do not by themselves establish a system-event ledger that
proves each M1 stage, rejects fabricated outcomes and impossible transitions,
and de-links receipts on terminal purge. Conversely, M1 receipts would not
prove that a model semantically relied on memory internally. They prove only
the bounded observable events in the contract.

## 4. What would remain if both mechanisms pass

A pass would support only this bounded composition:

> A Core-authoritative, scope/permission/policy-aware influence graph whose
> optimized repair equals full rebuild, fails closed during repair,
> distinguishes reversible delete from terminal purge and identity reuse,
> removes residue across six inspectable memory surfaces, and connects issued
> versions to privacy-bounded observable-use, action, outcome, and
> invalidation receipts without hidden reasoning or raw context.

It would not establish a general solution to AI memory, hidden model-state
erasure, external client compliance, complete privacy, cross-platform
behavior, production readiness, or legal novelty.

## 5. Limitations

- This is an `L0` oracle, not an experiment result.
- Synthetic graphs cannot reveal an unknown production surface unless the M3
  inventory exposes it.
- Full-rebuild equality depends on an independently implemented deterministic
  evaluator and complete observable-state enumeration.
- “Observed use” is operational and receipt-bound; it does not reveal or
  claim internal model causation.
- Receipt noninterference does not eliminate timing, volume, or
  auxiliary-information attacks.
- The `25%` work threshold is a frozen research convention, not a product
  performance promise.
- The research is not an exhaustive literature, patent, or legal novelty
  search.
