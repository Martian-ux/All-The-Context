# ATC Memory Lab Wave 3 integrated results

## The next architecture is evidence-compiled memory, not a larger retrieval stack

| Field | Value |
|---|---|
| Date | July 23, 2026 |
| Coordinator branch | `codex/memory-lab-wave3-coordinator` |
| Immutable experiment base | `950f649d9e3cc106fb8ff4febbe38919f8e00d11` |
| Governance | Six fresh visible `gpt-5.6-sol` worktree tasks; coordinator-only integration |
| Data | Frozen synthetic fixtures and official metadata only; no personal context or operator Core |
| Coordinator reproduction | 43 focused tests passed; decisive reports reproduced on the integrated branch |
| Status | Complete with one bounded kill, two holds, one mixed conformance result, one narrow retain, and one execution-denied external intake |

## Executive judgment

Wave 3 does not establish that ATC has solved AI memory. It does eliminate
several tempting but unsafe shortcuts and identifies one bounded mechanism
worth carrying forward.

The integrated direction is:

> Preserve a complete, versioned evidence substrate; admit untrusted channels
> conservatively; resolve authority, currentness, and applicability before
> relevance; compile a sealed, minimally sufficient task projection; bind it
> to an action ceiling and an inspectable receipt; and close every later
> influence when its source changes or is purged.

This is **Evidence-Compiled Memory** as a research direction. It treats a memory
use as a revocable transaction from evidence to action, not as a search result
that becomes trusted merely because it was retrieved.

The novelty claim is deliberately narrow. Complete logs, programmatic search,
admission gates, context packing, provenance, and lifecycle invalidation all
have prior art. The candidate differentiation is their exact composition:

1. Core-owned canonical evidence and authority;
2. pre-relevance sealing of the authorized, current, applicable projection;
3. exact finite-set sufficiency and one-deletion minimality;
4. full-receipt paired-vault noninterference;
5. current-version reread immediately before issue;
6. a hard action-force ceiling;
7. outcome and use receipts; and
8. dependency-complete correction, deletion, and purge closure.

Items 2-5 received narrow supporting evidence from M2. P01 and E01b supplied
partial or negative evidence around admission, action gates, and lifecycle
closure; none established the complete composition, which remains a
hypothesis.

## 1. Integrated result matrix

| Cell | Evidence | Frozen result | Coordinator decision |
|---|---|---|---|
| B01 programmatic log | `L2` synthetic | Restricted programmatic CAOS `0.857143` vs stable lexical `0.428571`; frozen combination `1.0`; external operation premium `2.571429` | Preserve `KILL_MECHANISM` for this bounded DSL/accounting only; retain programmatic logs as a research control |
| O01 online/shift | `L2` synthetic | Tie-aware rank correlations `0.333333`, `0.816497`, `0.544331`; maximum average-rank move `1.5` | `HOLD`; a static winner is not stable enough to select |
| P01 poisoning | `L2` synthetic | Governed reference durably retained poison in `4/5` unique attacks, retrieved `1/5`, influenced `0/5`, crossed protected action `0/5`; clean utility `5/5` | `HOLD_AUTOMATIC_DURABILITY`; later gates worked, admission did not |
| E01b production conformance | `L2` for executed cases; unsupported cases remain explicit | Six production-path passes, six unsupported/not-exercised semantics, zero observed failures | Accept the six narrow conformance facts; reject any claim of complete lifecycle conformance |
| M2 sealed minimal projection | `L2` synthetic | 1,000 pairs × 20 repeats; CAOS/sufficiency/one-deletion minimality `1.0`; zero full-receipt pair differences; mean disclosure `38.0` vs `70.1` full-authorized | `narrow_retain_bounded_m2`; advance the contract, not a production implementation |
| External artifact intake | `L0` metadata | MPBench metadata-qualified at pinned revision; PRO-LONG repository still unavailable | Execution denied in Wave 3; design a separate quarantined external cell |

`L2` means deterministic evidence reproduced by the coordinator on the
integrated branch. It is not cross-platform, stochastic-model, real-user, or
production evidence.

## 2. B01: programmatic inspection helped, but this configuration did not clear its frozen gate

B01 compared five conditions over nine action-bearing symbolic tasks and 20
deterministic repeats:

| Condition | Confirmatory CAOS | Action accuracy | Evidence recall | Mean chars | Counted operations | Forbidden |
|---|---:|---:|---:|---:|---:|---:|
| No memory | 0.285714 | 0.285714 | 0.285714 | 0.00 | 0.00 | 0 |
| Stable lexical current-state | 0.428571 | 0.714286 | 0.809524 | 515.57 | 1.00 | 2 |
| Restricted programmatic log | **0.857143** | **0.857143** | **0.857143** | **331.00** | 3.57 | 0 |
| Current ATC Retrieval V3 | 0.142857 | 0.428571 | 0.523809 | 517.14 | 1.00 | 1 |
| Frozen programmatic/ATC combination | **1.000000** | **1.000000** | **1.000000** | 361.86 | 3.71 | 0 |

The programmatic condition issued bounded read-only operations over a complete
structured event set and excluded a same-selector, plausible-action,
wrong-scope decoy before analysis. Its only confirmatory miss was the
intentionally unsupported lexical strategy; the frozen combination recovered
that task.

The preregistered state remains `KILL_MECHANISM` because the counted external
operation premium exceeded the frozen 25% cap. That label is scoped to the
hand-authored B01 DSL. One DSL read was compared with one top-level lexical
adapter call while internal lexical and ATC work was not normalized. B01
therefore does **not** establish comparative compute efficiency, falsify
general programmatic memory, or reproduce
[PRO-LONG](https://arxiv.org/abs/2607.20064). PRO-LONG lets a coding agent use
grep, regex, and agent-written Python in an action environment; B01 supplied a
two-strategy descriptor grammar co-designed with its fixture.

Decision:

- do not ship or promote this restricted DSL;
- keep the complete structured log as the evidence substrate;
- retain programmatic inspection as a B02 research condition; and
- normalize wall time, tokens, tool calls, scanned bytes, and action budgets
  before making an efficiency decision.

## 3. O01: the static memory winner did not survive online and shifted evaluation

O01 separated write, later read, utilization, action success, and recovery in
three regimes. It also corrected two evaluation hazards before integration:
post-action feedback is explicitly a supervised corrective oracle, and
decisions use average ranks for exact CAOS ties.

| Condition | Off-policy CAOS | Online CAOS | Shifted CAOS |
|---|---:|---:|---:|
| Append log | 1.000000 | 0.500000 | 0.625000 |
| Stable current state | 1.000000 | 0.500000 | 0.750000 |
| Governed reference | 1.000000 | **0.833333** | **0.750000** |
| No memory | 0.500000 | 0.500000 | 0.500000 |

Tie-aware Spearman correlations were:

- off-policy to online: `0.333333`;
- off-policy to shifted: `0.816497`; and
- online to shifted: `0.544331`.

The maximum tie-aware rank movement was `1.5`, below the frozen `>2` hold
threshold. The result is nevertheless `HOLD` because two correlations fell
below `0.7`.

This does not prove an adaptive learned memory controller is safe or useful.
It does prove that ATC must not select one universal memory policy from a
static retrieval fixture. Future policy routing must run in shadow mode,
preserve the same authority and action gates, and be evaluated on online
formation, shift, and recovery.

## 4. P01: later gates stopped action, but automatic durability still failed

P01 used five unique attack scenarios and five channel-paired benign controls
across conversation, tool output, compaction summary, and
experience-to-procedure channels. Twenty deterministic repeats established
replication only; rates and the exact one-sided confidence bound use unique
scenarios, avoiding pseudoreplication.

For the conservative non-production governed reference:

| Stage | Attack count | Rate |
|---|---:|---:|
| Durable poison | 4/5 | 0.800000 |
| Later retrieval | 1/5 | 0.200000 |
| Observable influence | 0/5 | 0.000000 |
| Hard-force escalation | 0/5 | 0.000000 |
| Simulated protected action | 0/5 | 0.000000 |
| Clean utility | 5/5 | 1.000000 |
| Legitimate confirmed protected permission | 1/1 | 1.000000 |

The one-sided 95% upper bound for the durable-poison rate was `0.989794`.
Admission and persistence were not separately observable in this ABI, so the
first measured failure stage is honestly labeled `durable_state`.

The rule ablations are decisive within the fixture:

- eager append-only memory produced hard-force escalation and protected action
  in `5/5` attacks;
- removing protected-action confirmation produced both in `1/5`; and
- channel-role admission, currentness/revocation, and task applicability each
  stopped a distinct later path but did not prevent all poison from becoming
  durable.

Decision: automatic application can continue only behind later gates in this
synthetic reference, but automatic durability is held. Tool output,
instruction-shaped compaction, and unverified experience/procedure candidates
must remain quarantined proposals until a stronger source/witness and
promotion contract is tested.

The controls are channel-paired, not fully feature-matched, and the reference
and fixture were co-designed. P01 is not an external poisoning benchmark.

## 5. E01b: current Core has useful lifecycle behavior and important semantic gaps

E01b exercised current production paths in disposable synthetic stores. It did
not connect to the operator Core or change production code.

Observed conformance:

1. authority/source admission distinguishes explicit local observations from
   tentative generic archive imports;
2. correction and observed-time currentness converge on one stable record;
3. kind-constrained retrieval filters the requested kind;
4. explicit scope filtering excludes another project scope;
5. ordinary delete/restore is reversible; and
6. purge is terminal across restore, history, search, unshared source content,
   logical SQLite rows, and inspected local database side files.

Unsupported or not exercised:

1. generic epistemic-role semantics beyond kind filtering;
2. a project-and-domain hard applicability gate beyond explicit scope
   filtering;
3. derived dependency-lineage invalidation;
4. eviction, decay, and procedure retirement;
5. caller-selected same-ID recreation after purge; and
6. procedure preconditions and transfer applicability.

The twelve-case mapping and expected classifications were hand-authored
against the frozen implementation, not constructed as a blind independent
conformance suite. The six passes are real production-path observations; the
six unsupported entries are product-gap receipts, not passed tests.

## 6. M2: the sealed minimal compiler survives its first bounded falsifier

M2 generated 1,000 paired synthetic vaults across five named canary families
and ran each pair 20 times. The compiler received declared obligations and
already-classified record metadata, never the harness success oracle.

The retained condition:

- sealed authorization, temporal currentness, and applicability before
  relevance;
- selected an exact minimum-cardinality sufficient set within a 12-candidate
  cap;
- performed one-deletion sufficiency checks;
- enforced character and cumulative-disclosure budgets;
- reread selected record versions before issue; and
- emitted a canonically serialized receipt.

Results:

| Metric | Result |
|---|---:|
| Paired executions | 20,000 |
| Individual trials | 40,000 |
| CAOS | 1.000000 |
| Sufficiency | 1.000000 |
| One-deletion minimality | 1.000000 |
| Full serialized-receipt pair differences | 0 |
| Exhaustive deletion checks | 2,600 |
| Removable selected items | 0 |
| Optimum-gap cases | 0 |
| Mean disclosed characters | 38.0 |
| Full-authorized mean characters | 70.1 |
| Sealed non-minimal mean characters | 66.9 |
| Current-version changes detected | 1,000/1,000 |
| Cumulative-limit violations blocked | 1,000/1,000 |

The result is `narrow_retain_bounded_m2`, not a generalized product claim.
Five named canaries collapse to only three compiler-visible attestations:
unauthorized, not current, and not applicable. Deleted versus purged and
out-of-scope versus other inapplicability remain upstream distinctions.
Timing covers post-seal logical work only. The SHA-256 fields are linkable,
dictionary-attackable synthetic commitments, not production-safe redactions.
Obligations, coverage, roles, and semantic labels were hand-authored and
co-designed.

The mechanism advances because it establishes a useful contract under a finite
oracle, not because its prototype should be wired into production.

## 7. External horizon: MPBench is inspectable; PRO-LONG code is still unavailable

The metadata-only intake found the official
[Digital-Trust-Lab/mp-bench](https://github.com/Digital-Trust-Lab/mp-bench)
repository at commit
`6886880a7c29625e0109e0ad91d0e095029f1577`. Its complete current tree has a
root Apache-2.0 license, README, adversarial JSONL blob, and benign JSONL blob,
with no executable hooks or dependency manifests. The README describes six
attack classes, seven domains, and instruction-shaped free-text fields.

No dataset row or payload blob was opened or downloaded. Licensing and
availability do not make adversarial text safe to expose to an agent. A future
cell must acquire pinned bytes into a disposable quarantine, validate schema
without an LLM, replace every free-text value with opaque symbolic metadata,
and kill the run if raw text crosses the boundary.

The [PRO-LONG paper](https://arxiv.org/abs/2607.20064) still links
`alexisfox7/PRO-LONG`, but the repository and official API returned 404 during
the intake window. No code/log revision, license, inventory, or dependency set
is verifiable. B01 remains an ATC-native restricted-DSL experiment, not a
reproduction.

## 8. Product architecture consequence

Wave 3 changes the working architecture from “retrieve the best memories” to
“compile a memory transaction”:

```text
untrusted observations
  -> authority/witness admission
  -> versioned canonical evidence
  -> currentness + applicability resolution
  -> sealed task projection
  -> obligation-complete minimal set
  -> current-version reread + disclosure receipt
  -> action-force ceiling
  -> outcome/use receipt
  -> correction/purge influence closure
```

The Core remains the only canonical authority. A retriever, coding agent,
external framework, learned router, or memory model may propose candidates,
rankings, or analyses. It cannot:

- make imported text true;
- make a tool or summary durable automatically;
- expand scope or behavioral force;
- bypass the sealed projection;
- issue context without a version and dependency receipt; or
- retain influence after correction, deletion, or purge.

The first product-shaped contract should be a `MemoryTransaction` containing:

```text
MemoryTransaction
  task_need_and_obligations
  principal_and_capability_view
  sealed_projection_commitment
  selected_record_id_versions
  one_deletion_receipts
  disclosure_and_cumulative_budget
  action_force_ceiling
  dependency_and_policy_generation
  issue_receipt
  acknowledgement_and_use_receipt
  outcome_and_invalidation_state
```

This is a research contract. It does not authorize a production schema or
claim that hidden model state, external providers, or client behavior is
closed by Core.

## 9. What Wave 3 accepts and rejects

Accepted:

- complete versioned evidence remains the strongest substrate;
- static benchmark winners cannot define one permanent memory policy;
- automatic durability for tool, compaction, and unverified experience
  channels is unsafe in the tested reference;
- current Core already has six useful narrow lifecycle behaviors;
- current Core lacks or does not expose six required semantic capabilities;
- sealed pre-relevance projection plus exact bounded minimal compilation is
  worth further testing; and
- MPBench can enter a future supplier gate through metadata and quarantine,
  not by loading attacks into a model.

Rejected:

- promotion of the B01 restricted DSL or its frozen combination;
- a general claim that programmatic memory was falsified;
- a universal static retrieval winner;
- automatic durability because later action gates happened to block harm;
- complete production lifecycle conformance;
- production-safe privacy or timing claims from M2;
- an external MPBench result or PRO-LONG reproduction; and
- any claim that ATC has solved AI memory.

## 10. Next governed experiments

1. **M3/E02 influence closure and production gaps.** Add a finite dependency
   oracle and test correction, scope narrowing, deletion, purge, issued
   context, use statistics, procedures, caches, and working state. Run a full
   rebuild control before attempting optimized repair.
2. **M1 use ledger.** Record assignment, supply, acknowledgement, observable
   use, action, outcome, and invalidation without storing hidden reasoning or
   raw private traces.
3. **MPBench schema-only external cell.** Acquire only pinned Apache-2.0 data
   into a disposable quarantine; stream-validate and symbolically sanitize
   before any ATC-visible artifact. Raw attacks never enter an LLM.
4. **B02 genuine programmatic reader.** Use an action-bearing environment and
   a reader able to write bounded analysis code. Equalize tool calls, scanned
   bytes, wall time, tokens, and action budget. Reproduce PRO-LONG only if its
   official licensed artifact becomes inspectable.
5. **O02 shadow policy router.** Compare fixed policies with a bounded router
   under online formation and shift. The router cannot alter authority,
   applicability, force, or promotion gates.
6. **M6 portable working-state repair.** Begin only after dependency closure
   and use receipts can prove that corrected or purged state does not survive
   rehydration.

The next production change should be chosen only after M3/E02 shows whether the
missing role, applicability, lineage, and procedure-precondition semantics can
be implemented without creating a second authority or weakening purge.

## 11. Reproduction and provenance

Worker commits:

- B01: `4e706f545f901c817aa0f997a022cf638159f883`;
- O01: `cdc9bb81efe01c51fe05b4541d97ce883e7bc44e`,
  `bccf0a3f5ff0e44601b2d62377cb42877da3bb51`;
- P01: `f3717464c1740665267d208165e24c514c574cb3`;
- E01b: `e47084a414a129269cb4da449b77905d71e938ec`,
  `e3ebcdf8ca76b9b486b6647c8616a1623a9311b6`;
- M2: `b1fe56f22a46fb0bf5747659873b6e5f8c8d46f7`;
- external intake: `55574425d37dd49b8816a28f3626e4bd3e21e91c`.

Coordinator reproduction:

- focused Wave 3 and governance tests: `43 passed`;
- P01 JSON: byte-identical;
- M2 JSON and Markdown: byte-identical;
- O01 and E01b JSON: content-identical with only line-ending differences;
- B01: decision and decisive quality/safety metrics identical; wall-clock and
  machine fields excluded from identity; and
- full repository gates on Python `3.12.10`: Ruff passed, mypy passed across
  66 source files, and pytest completed with `603 passed` and four expected
  Windows symlink skips.

No worker merged or pushed. No experiment accessed personal context, the
operator Core, a provider, a model, credentials, or a real protected action.
