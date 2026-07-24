# ATC Memory Lab Wave 4 post-result independent review

## Review verdict

| Field | Value |
|---|---|
| Review date | July 23, 2026 |
| Integrated commit reviewed | `f2065a8d026a4928327d73eb9b1282bade91f7a3` |
| Frozen F02 oracle | `a866ad5b9d17a72d73d2dca4de4dd8be1e71ca9e` |
| Governance base | `f545c37157845f0bd402215719cb8c747b7fc21d` |
| M3 grading | 15 `PASS`; 0 `HOLD`; 0 `FAIL` |
| M3 verdict | `RETAIN_M3_CONTRACT_AND_OPTIMIZATION` |
| M1 grading | 16 `PASS`; 0 `HOLD`; 0 `FAIL` |
| M1 verdict | `RETAIN_M1_OBSERVABLE_LEDGER` |
| Evidence | Symbolic deterministic `L2` after coordinator reproduction |
| Product status | Not production evidence or authorization |

I reviewed the integrated reports, manifest, research implementations, fixtures,
and focused tests read-only with `git show`. I did not modify the frozen oracle,
cherry-pick the integrated branch, or treat a mechanism's self-reported
decision as its grade.

The checked M3 and M1 reports describe worker-local results as `L1`. The
integrated manifest records coordinator reproduction of both result sets.
This review therefore characterizes the accepted integrated evidence as
`L2`: deterministic symbolic evidence reproduced by the coordinator. It is
not production, model, provider, external-system, cross-platform, real-client,
real-action, or user evidence.

E02 independently leaves all six production semantic gaps open: five are
`UNSUPPORTED` and exact caller-selected same-identifier reuse is
`NOT_EXERCISED`. Nothing in M3 or M1 converts those production gaps into
support or authorizes promotion.

## 1. Frozen-order and artifact integrity

The integrated manifest binds the pre-implementation oracle commit, records
15 M3 and 16 M1 cases, and states that the oracle was committed before M3 and
M1 dispatch. Automated comparison found:

- M3: 15 expected IDs, 15 reported IDs, no missing or extra ID, all 15
  reported `PASS`;
- M1: 16 expected IDs, 16 reported IDs, no missing or extra ID, all 16
  reported `PASS`;
- M3 required-zero metric sum: `0`;
- M1 required-zero metric sum: `0`;
- M3 case and six-surface coverage: `1.0`;
- M1 case coverage: `1.0`; and
- both reports bind F02 oracle
  `a866ad5b9d17a72d73d2dca4de4dd8be1e71ca9e`.

The result labels below are my independent grades against the frozen schedules
and invariants. `PASS` means the bounded symbolic implementation and its
inspected tests support the frozen expectation. It does not mean the same
behavior exists in production.

## 2. M3 per-case grading

| Frozen case | Grade | Independent basis |
|---|---|---|
| `M3-C01-CORRECTION-CHAIN` | `PASS` | Correction withdraws the complete chain, observes the barrier initially and after each randomized repair step, binds version 2, and matches the independently coded clean rebuild. |
| `M3-C02-SCOPE-NARROWING-FANOUT` | `PASS` | Alpha and beta principal views are exercised separately; the beta cascade is withdrawn across all six surfaces and both final views equal clean rebuild. |
| `M3-C03-PERMISSION-REVOCATION-FANIN` | `PASS` | Revoking one fan-in support withdraws shared descendants and permits only successors evaluated from retained eligible support; optimized state equals rebuild. |
| `M3-C04-ORDINARY-DELETE-RESTORE` | `PASS` | Ordinary delete withdraws descendants, restore succeeds without changing the record version, and repaired state equals rebuild; purge follows a different irreversible path. |
| `M3-C05-TERMINAL-PURGE-IDENTIFIER-REUSE` | `PASS` | Restore and old-generation replay fail, requested identifier reuse receives a fresh generated lineage, purged tokens and exclusive descendant IDs are absent, and each shared recipe is reconstructed only from retained roots before clean-build comparison. |
| `M3-C06-POLICY-GENERATION-CHANGE` | `PASS` | Policy generation advances before repair, the barrier hides all old-generation artifacts, every successor binds generation 8, and final state equals generation-8 rebuild. |
| `M3-C07-PARTIAL-REPAIR-BARRIER` | `PASS` | Selection and issue are staged but remain non-servable; premature finalize fails, withdrawal survives the simulated pause, and only complete repair publishes. This is a logical pause, not process-crash persistence evidence. |
| `M3-C08-STALE-WRITER` | `PASS` | An old-epoch shared descendant commit is rejected after the epoch advances, graph state is unchanged, and the final state still equals rebuild. |
| `M3-C09-CYCLE-EDGE-ATTEMPT` | `PASS` | The back edge is rejected atomically, graph and eligible state remain unchanged, and focused tests verify failure receipts omit source IDs and payload symbols. |
| `M3-C10-CROSS-SCOPE-EDGE-ATTEMPT` | `PASS` | The cross-domain edge without an authorized bridge is rejected atomically with unchanged graph/state and bounded failure diagnostics. |
| `M3-C11-DUPLICATE-AND-CONFLICTING-REPLAY` | `PASS` | Identical event replay is idempotent, conflicting body reuse fails closed, and three deliveries of one use ID increment the symbolic use count once. |
| `M3-C12-OUT-OF-ORDER-MUTATION` | `PASS` | Sequence 2 is rejected before sequence 1, the mutation barrier stays closed, ordered redelivery is accepted, and repaired state equals rebuild. |
| `M3-C13-SHARED-DESCENDANT-CORRECTION` | `PASS` | A same-payload correction still changes the version-bound commitment; the shared procedure cannot survive by content equality alone and equals clean rebuild. |
| `M3-C14-PURGE-DURING-ISSUE` | `PASS` | The outstanding issued artifact and all six exclusively dependent fan-out descendants are removed, delayed use fails, the full declared privacy boundary is scanned, and rebuilt state has no purged residue. Acknowledgement/action/save are represented by loss of their backing surfaces, not real client APIs. |
| `M3-C15-OPTIMIZATION-WORK-CONTROL` | `PASS` | One mutation among 100 independent six-surface records evaluates 120 optimized descendant nodes versus 12,000 clean-build nodes across 20 repeats, a `0.99` reduction above the frozen `0.25` threshold, with zero state mismatch. |

### M3 decisive result

All required-zero metrics are zero, including stale publication, clean-build
mismatch, purge residue, fail-open publication, illegal-edge acceptance,
conflicting replay, duplicate side effects, privacy-receipt violations,
partial-repair exposure, stale-writer acceptance, purged-lineage revival, and
delete/purge conflation.

The clean-build control copies only current records and recipes and never
reads incremental artifacts. Its evaluation code is separate from the
incremental evaluator, although both implement the same frozen artifact
semantics. That is sufficient for this symbolic oracle but still permits a
common specification error.

The work-control numerator and denominator count evaluated symbolic nodes, not
wall time, I/O, memory, contention, or end-to-end latency. The correct frozen
decision is nevertheless
`RETAIN_M3_CONTRACT_AND_OPTIMIZATION`, because the oracle explicitly chose
evaluated-node reduction and every safety gate passes.

## 3. M3 purge hardening audit

The integrated result reports:

- `140` exclusively dependent descendant identifiers checked; and
- `80` shared descendant recipes validated after reconstruction from retained
  evidence.

These are cumulative repeat counts, not unique topology size:

- `M3-C05` checks one exclusive descendant per repeat for 20 checks and four
  shared recipes per repeat for 80 validations; and
- `M3-C14` checks six exclusive fan-out descendants per repeat for 120 checks.

The seven exclusive descendant slots cover the six declared surfaces plus the
additional exclusively dependent node in the shared topology. The purge path:

1. advances the minimum accepted generation;
2. deletes the canonical record;
3. clears accepted event digests, ordered sequence state, use-event IDs, and
   failure receipts;
4. removes affected published and pending artifacts;
5. deletes the purged predecessor from every recipe;
6. recursively drops recipes left without support; and
7. clears and rebuilds the reverse dependency index from the scrubbed recipes.

For shared descendants, the runner additionally verifies that each surviving
artifact exists, neither its recipe-root closure nor its source-version set
contains the purged root, and every recipe root is a retained current record.
It then compares the result with a clean rebuild.

The serialized `privacy_boundary()` includes records, forward recipe graph,
artifacts, pending state, active barrier, accepted event digests, failure
receipts, minimum generation, and aggregate invalidation count. It does not
serialize the private reverse index itself. The pass therefore depends partly
on direct code inspection showing that purge clears and rebuilds that index,
plus the missing-inventory-edge fault that makes incomplete inventory
observable. A stronger future harness should serialize every index rather
than require code audit.

The generation-only, inventory-only, raw-record-only purge, and missing-edge
faults remain visibly failing. This is useful falsification sensitivity: a
generation barrier alone leaves purge residue, inventory without generation
guard admits a stale writer, and deleting only the root leaves seven residue
or publication failures in the one-repeat ablation.

This hardening supports a bounded symbolic closure contract. It does not prove
erasure from a production database, WAL, backups, replicas, external clients,
model state, unknown derived surfaces, or disconnected devices.

## 4. M1 per-case grading

| Frozen case | Grade | Independent basis |
|---|---|---|
| `M1-C01-HAPPY-OBSERVABLE-CHAIN` | `PASS` | The exact assigned-through-outcome sequence is version-, issue-, policy-, principal-view-, and predecessor-bound and replays deterministically without raw content. |
| `M1-C02-NONACKNOWLEDGEMENT-IS-UNKNOWN` | `PASS` | After supply and silence, acknowledgement and use both remain `not_observed`; the ledger contains no `not_used` state. |
| `M1-C03-USE-WITHOUT-ACKNOWLEDGEMENT` | `PASS` | Host-observed use and a later action are accepted directly from the supplied receipt while acknowledgement remains `not_observed`. |
| `M1-C04-ACKNOWLEDGED-BUT-NOT-OBSERVED-USED` | `PASS` | Acknowledgement is retained as its own stage and does not increment the observed-use aggregate. |
| `M1-C05-DUPLICATE-REPLAY` | `PASS` | Identical use replay returns idempotent status and produces exactly one observed-use aggregate. |
| `M1-C06-CONFLICTING-REPLAY` | `PASS` | Reusing the action event ID with a different canonical version is rejected as a duplicate conflict without replacing the original event. |
| `M1-C07-OUT-OF-ORDER-AND-IMPOSSIBLE` | `PASS` | Outcome-before-action, action-before-use, and use with an unknown parent are all rejected; only assigned and supplied remain. |
| `M1-C08-FABRICATED-OUTCOME` | `PASS` | Client-transport success and borrowed-parent success fail; only the outcome-adapter event bound to the correct action is admitted. The adapter is symbolic, not a real independently secured witness. |
| `M1-C09-CORRECTION-INVALIDATION` | `PASS` | Version-1 open work receives explicit correction invalidation, late version-1 use fails, and a fresh version-2 transaction can be supplied. |
| `M1-C10-SCOPE-AND-PERMISSION-INVALIDATION` | `PASS` | Beta and alpha transactions receive distinct scope-narrowing and permission-revocation reasons, and both late-use attempts fail. |
| `M1-C11-DELETE-VERSUS-PURGE` | `PASS` | Ordinary delete appends a reversible invalidation and allows a later fresh supply; terminal purge then de-links both transactions and leaves only the global identity-generation barrier and purge count. The ledger observes resupply and does not itself implement Core restore. |
| `M1-C12-PURGED-IDENTIFIER-REUSE` | `PASS` | Same textual record ID is accepted only in a higher identity generation with a fresh lineage; old transaction/issue parents cannot bind to it and old receipts are absent. |
| `M1-C13-POLICY-GENERATION-INVALIDATION` | `PASS` | Generation-2 supply is explicitly invalidated, late generation-2 acknowledgement fails, and generation-3 supply uses a fresh transaction and issue receipt. |
| `M1-C14-RAW-TRACE-AND-HIDDEN-REASONING-REJECTION` | `PASS` | Raw context, chain of thought, and stable content hash are rejected at schema admission without echoing their synthetic value; the allowlisted event succeeds. |
| `M1-C15-RECEIPT-CORRELATION-NONINTERFERENCE` | `PASS` | Unauthorized and inapplicable canaries have zero effect on normalized receipts, admitted candidate count, reason codes, cursor shape, logical timing class, or learned aggregates; per-run receipt IDs differ. |
| `M1-C16-PURGE-RACE-WITH-OBSERVED-USE` | `PASS` | Purge commits before delayed use, removes the bound record and parent events, rejects the delayed event, leaves use aggregate zero, and emits no old receipt. |

### M1 decisive result

All frozen required-zero metrics are zero. The integrated report adds
`purge_inspectable_identifier_residue_count=0`, and deterministic
reconstruction reports zero mismatch for ordinary accepted-event replay,
aggregate rebuild, and post-purge compacted-state replay.

The resulting decision is `RETAIN_M1_OBSERVABLE_LEDGER` for the isolated
research contract. It is not evidence that a model internally used memory.
`OBSERVED_USE` means a host artifact with an exact issue and record-version
binding. Acknowledgement remains observational. Outcome requires the symbolic
outcome-adapter source and correct action parent; it is not inferred from
success text or client self-report.

## 5. M1 terminal-purge compaction audit

Terminal purge is intentionally an exception to physical append-only history.
For every record version sharing the purged lineage, the implementation:

1. appends lifecycle invalidation while the lineage still exists;
2. advances a global minimum identity-generation barrier above every affected
   record generation;
3. removes all affected events from the event list;
4. reconstructs the event-ID index only from retained unrelated events;
5. removes all affected record versions;
6. increments only an aggregate purge count; and
7. returns admission statuses without retaining the purged invalidation event
   or its identifiers.

The declared post-purge inspectable state is active records, active events,
minimum identity generation, and purge count. Focused tests scan it for record,
lineage, transaction, issue, snapshot, principal-view, and event identifiers;
they also establish zero observed-use/action aggregates and deterministic
replay from compacted state.

The private event-ID index is not separately serialized by
`inspectable_state()`, but direct inspection confirms it is reconstructed from
the retained event list. The global barrier is deliberately conservative: a
purge of one lineage raises the minimum generation for all later
registrations. This avoids a per-identity tombstone that would itself preserve
the purged identity, at the cost of a coarse global generation contract.

This is meaningful symbolic evidence for destructive privacy compaction. It
does not establish atomic durable compaction across SQLite tables, indexes,
WAL, backups, replication logs, exports, crash recovery, external telemetry,
or client caches. A production design must specify those stores and failure
boundaries before claiming purge completeness.

## 6. Causal and privacy claim audit

The M1 paired-episode cell contains 200 arm executions over 40 paired
assignments. Its per-kind controlled-omission effects are `+10` for applicable
required memory, `-10` for harmful memory, and `0` for each distractor and
redundant-memory condition; the aggregate sum is therefore `0`. The report
preserves the per-kind signs rather than hiding them behind the aggregate.

No ledger event marks itself causal. The report calls host-observed dependence
an association and reserves `controlled_intervention` for the preassigned
synthetic omission arm. Even that contrast proves only fixture-specific
symbolic potential outcomes. It is not randomized user evidence, model
causation, or a general memory-effect estimate.

The paired-vault privacy check has two symbolic canary pairs and logical timing
classes. Zero normalized differences is useful but cannot rule out wall-clock,
traffic-volume, allocator, database-layout, or auxiliary-information channels.
Exact canonical IDs remain present while a lineage is active because the
ledger's audit purpose requires version binding; this is not an anonymous
receipt system.

## 7. E02 production boundary

E02 ran 15 boundary probes in disposable synthetic SQLite Core stores for ten
deterministic repeats. The six gap-level outcomes remain:

| Production semantic | Result |
|---|---|
| Generic epistemic role distinct from kind | `UNSUPPORTED` |
| Project **AND** domain applicability | `UNSUPPORTED` |
| Dependency lineage and invalidation | `UNSUPPORTED` |
| Eviction, decay, and procedure retirement | `UNSUPPORTED` |
| Same caller-selected identifier after terminal purge | `NOT_EXERCISED` |
| Procedure preconditions and transfer applicability | `UNSUPPORTED` |

Adjacent production behaviors—expiry eligibility, explicit scope exclusion,
fresh generated identity, and purge tombstone—do not substitute for the
missing semantics. The proposed optional Core-owned epistemic-role field is a
design receipt, not an observed capability or authorization to implement.

This result is decisive against reading M3 or M1 as production validation.
Those are isolated research modules; current production Core still lacks the
lineage and semantic contracts they model.

## 8. Prior-art and novelty boundary

Primary sources were rechecked on July 23, 2026. No external code or dataset
was downloaded or executed.

[MemoRepair](https://arxiv.org/abs/2605.07242) is the closest prior art for
M3. It already formalizes the cascade update problem across summaries, cached
outputs, procedures, and skills; withdraws affected descendants before
repair; constructs successors from retained support and repaired
predecessors; and requires predecessor-closed validated republication. Its
reported zero stale exposure assumes complete influence provenance.
Barrier-first cascade repair is therefore not an ATC novelty claim.

[Bazel Skyframe](https://bazel.build/reference/skyframe) already establishes
explicit dependency capture, reverse-transitive invalidation, change pruning,
and clean-build equivalence as the correctness reference for incrementality.
Incremental closure and work reduction are not new by themselves.

[MemOps](https://arxiv.org/abs/2607.12893) already represents memory lifecycle
operations with structured trigger, target, scope, transition, and evidence
traces. [Memora](https://arxiv.org/abs/2604.20006) already penalizes reliance
on obsolete or invalidated memory. Provenance records, ordered event replay,
idempotency, lifecycle evaluation, selective forgetting, and
memory-grounded-action evaluation likewise remain established prior art as
documented in the frozen F02 report.

Wave 4 supports only the narrower ATC research composition:

> Core-authoritative scope, permission, version, policy, delete, purge, and
> identity-generation semantics; fail-closed dependency closure across six
> declared surfaces; exact clean-rebuild equality; and a privacy-bounded
> observable assignment/use/action/outcome/invalidation ledger that excludes
> hidden reasoning and destructively de-links purged receipt history.

The experiment supports this composition as a deterministic symbolic contract.
It does not establish legal or scholarly novelty. The literature review is not
exhaustive, and most components—including the central barrier-first repair
idea—have clear prior art.

## 9. Residual risks and required next boundary

### M3

- The six declared surfaces do not prove that production has no seventh
  surface, hidden cache, export, replica, client state, or model-state copy.
- The clean rebuild is independently coded but shares the same hand-authored
  artifact semantics, leaving common-mode specification risk.
- The `140` and `80` purge numbers are repeated checks of seven and four
  topology slots, not broad or production-scale diversity.
- Reverse-index purge correctness is supported by code audit and injected
  faults but is not directly serialized in the privacy-boundary receipt.
- Symbolic SHA-256 commitments are comparison devices, not privacy-preserving
  redactions.
- Evaluated-node reduction does not establish latency, storage, or resource
  benefit.
- Pause, stale writer, and disconnected generation cases are logical models,
  not durable process, crash, or distributed-systems tests.

### M1

- Observable-source enums are trusted symbolic inputs; no authenticated host,
  tool gateway, or outcome adapter was exercised.
- The ledger proves host-observed binding, not hidden model use or causal
  reliance.
- Terminal purge compaction is in-memory; atomic durable erasure and crash
  recovery remain untested.
- The coarse global identity-generation barrier may be safe but operationally
  expensive or difficult to migrate.
- Active exact record IDs are linkable by design, and the two-pair
  noninterference check does not establish general privacy.
- Logical time buckets do not test timing side channels.
- The controlled-omission effects are hand-authored symbolic fixture effects,
  not external or user causal evidence.

### Production

- E02 records five unsupported semantics and one not-exercised semantic.
- No production schema, runtime path, Relay, export/import, Edge, protected
  action, external system, model, or provider has demonstrated the M3/M1
  contract.
- Cross-platform evidence is absent.
- A separate ADR, production design, migration and purge-boundary inventory,
  authenticated witness design, and cross-platform durable tests remain
  necessary before any product promotion.

## 10. Final decision

No frozen kill condition was observed. No frozen hold condition remains
inside the declared symbolic harness:

- every M3 and M1 case ran;
- all required surfaces and stages are observable within the harness;
- both reconstruction controls are deterministic;
- required failure receipts are inspectable and bounded;
- all required-zero metrics are zero; and
- M3 exceeds its frozen optimization threshold.

Accordingly:

- retain the M3 contract and its bounded evaluated-node optimization;
- retain the M1 observable-ledger contract;
- retain E02's negative production-gap receipts;
- do not promote either research implementation into production; and
- do not claim cascade repair, observable event ledgers, lifecycle evaluation,
  or the broader evidence-compiled-memory composition as established novelty.
