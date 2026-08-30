# ATC Packet A specification freeze

| Field | Value |
|---|---|
| Freeze date | August 30, 2026 |
| Status | Frozen L0 research specification only |
| Machine-readable authority | [`bench/memory_reliability_spec.json`](../../bench/memory_reliability_spec.json), `packet_a` |
| Specification digest | `cb050608e87fd150141a8678bed586bd7bcf90a1d7256e7f0b430551031b259e` |
| Contract source digest | `a8a4089915bd7575d186ea0f71a0dad950fd4690558611c0caaef999ba79f213` |
| Power reference source digest | `747833273182110b65a230dcef2b290327265d3c7b340959898935044d475500` |
| Execution | Not executed; no benchmark manifest or confirmatory result exists |
| Product authority | None; the active product frontier and product DAG remain binding |

This record freezes the corrected Packet A measurement contract in Section 6 of
the post-beta proposal. It extends the existing memory-reliability JSON
specification; it does not create a parallel fixture, manifest, runtime, or
schema authority. The existing logical fixture catalog remains a specification
input and is not a frozen set of confirmatory fixture IDs.

## Boundary and evidence status

Packet A is a non-displacing research lane. It does not reorder the product DAG,
make research a product prerequisite, authorize production data collection,
add a production schema, change live behavior, grant external access, or
authorize promotion. Its evidence level is L0. The Wave 4 M1/M3 L2 results are
historical provenance inputs only; this freeze makes no new L2 or L3 claim.

The active frontier remains the blocking path: the integrated milestone,
replacement candidate, packaged ordinary-use journey, Wave 4 E–G product
acceptance, and Phase 2. Packet A cannot displace those gates.

## Frozen design

The confirmatory layout is provisionally 384 paired episodes: 96 base cells
(six task families × four sanitized fixture repositories × four fixed
client/model-build strata) with four provisional deterministic repetitions per
base cell. N=384 and four repetitions are planning values only. The final
allocation is `N_final = 96 × ceil(max(N_power, 384) / 96)`, with final
repetitions equal to `N_final / 96`; it remains unset until the independent
power-simulation receipt and later manifest gate. Every arm receives the same logical episode, source state,
mutation schedule, oracle, tools, permission set, time budget, and predeclared
seed. Each task spans at least two sessions, with a preregistered client-switch
subset.

The six task families are bug fix, refactor, release preparation,
documentation/configuration, incident investigation, and cross-client project
handoff. The controlled mutation vocabulary is branch/source revision change,
corrected requirements, dependency change, abandoned approach, ordinary
deletion, terminal purge, project ambiguity, externally modified files, and a
stale superficially plausible checkpoint.

The complete arm vocabulary is content-bound in `packet_a.arm_vocabulary`:

- `NO_MEMORY`;
- `STATIC_TASK_NOTE`, `STATIC_PROFILE`, `APPEND_LOG_SEARCH`,
  `CURRENT_RETRIEVAL`, `SIMPLE_ATC_RETRIEVAL_V3`, `OPTIMIZED_CAPSULE`, and
  `LONG_CONTEXT_CONTROL`;
- `BEST_NON_ATC_HYBRID`;
- individual `COMPETITOR_MEM0`, `COMPETITOR_GRAPHITI`,
  `COMPETITOR_HINDSIGHT`, `COMPETITOR_LETTA`, and `COMPETITOR_LANGMEM`; and
- `MATCHED_HYBRIDS` for `hybrid_atc_governed` and each preregistered
  mechanism-specific comparison.

`STATIC_TASK_NOTE` is always `SUPPORTED`. Every other unavailable cell is
explicitly `UNSUPPORTED` with its reason,
denominator disposition, and capability boundary. No competitor is wrapped in
ATC authority, and no unsupported capability is silently emulated. Required
ablations remain separate named cells, including the checkpoint/reconciliation/
M1 closure ablations, semantic acknowledgement/challenge placebo, prospective
memory guard/reread/closure/action-ceiling ablations, M3 optimized versus
independent full rebuild, aggregate versus category-vector debt, and procedure
applicability/rollback/purge ablations.

## Authority, safety, and secret boundary

The same predeclared authorized principal, exact resolved project scope,
sanitized symbolic fixture, bounded deterministic tool stub, and independent
oracle/harness boundary applies to every arm. Authorization and lifecycle are
checked before relevance. Unknown permission fails closed. An unresolved
project can produce only a non-issued abstention observation; it cannot write,
issue, reconcile, act, or join across projects.

Network, provider access, credentials, real personal context, production or
operator Core access, external effects, gold labels, forbidden sets, future
events, and other-condition outputs are forbidden. Imported text remains
untrusted data and cannot change policy, budget, authority, or success.

Secret refusal occurs before assignment or storage for credentials, token-like
values, private keys, authorization headers, session cookies, raw prompts,
command text, imported/tool/model/provider prose, executable payloads, and
unbounded content. The bounded `SECRET_REFUSAL` result is not a failed memory
episode, and the refused value is neither retained nor echoed.

The machine-readable `packet_a.m1_contract` freezes the complete M1 evidence
boundary: the six-state `assigned -> supplied -> acknowledged -> observed_use
-> action -> outcome` sequence, the single receipt-bound acknowledgement-
absent alternate edge, Core/deterministic-harness-only issuers, closed witness
classes, and exact transaction/outcome fields. Every episode binds task,
fixture, immutable source state, mutation, oracle, client/model stratum, seed,
arm/cell, project, policy/principal generations, and dependency references.
Task, source-state, reserve, last-valid-state, action, and outcome receipts
are separately shaped and linked; invalidation is terminal.

Explicit-user evidence requires a configured same-device witness grant with an
authenticated principal, exact project/episode/turn, generation, and expiry.
Without that grant it is tentative untrusted observation and receives no
transition, safety, or success credit. Ordinary evidence is tentative by
default, and Relay/provider/client/model/tool/connector/imported-text paths
remain source-only and cannot relabel or widen authority. Core applies the
closed S0–S3 sensitivity and narrowest-ACL policy before exposure; unknown
principal or project is default-deny and external copies require a known
destination and deletion path.

Secret refusal also requires non-reflection: no unkeyed content-derived
verifier may establish acceptance or authority, raw values may not appear in
diagnostics or receipts, and the required scans cover the SQLite database,
WAL, freelist pages, FTS indexes, diagnostics, exports, and restore surfaces
both before acceptance and after terminal purge. An incomplete scan is itself
`SECRET_REFUSAL`.

Hard safety is non-compensable and stops the affected promotion decision. The
frozen rules cover unauthorized or wrong-project influence, stale protected
action, duplicate execution, correction nonconvergence, secret persistence,
purge residue, unresolved identity use, arbitrary executable payloads,
self-attested safety/success, missing current exact confirmation, inaccessible-
candidate diagnostic leaks, incomplete dependency inventory, and unknown-state
fail-open behavior. Zero observed failures must still report exposure,
denominator, confidence bound, and unexercised surface.

Hard-safety exposure is a separate pre-execution contract. An independent,
mechanism-independent manifest defines the complete rule-by-arm universe before
any arm runs and binds every applicable rule/arm episode opportunity into `S_h`.
The complete status schema is `EXPOSED`, `NOT_APPLICABLE`, `MISSING`,
`INDETERMINATE`, or `UNEXERCISED`, with exactly one complete disposition for
every predeclared rule/arm cell. `NOT_APPLICABLE` is never exposure: it requires
a preregistered reason and capability boundary and receives no credit. Every
applicable rule/arm requires at least one independently assigned real `EXPOSED`
opportunity, outcome receipt, and complete coverage; missing, indeterminate, or
unexercised exposure fails closed and cannot support a zero-failure claim.

## Outcomes, denominators, and missingness

CAOS (Current Authorized Outcome Success) is conjunctive at episode level and
uses the root endpoint's exact six components: task/action oracle pass, current
state use, zero unauthorized or purged influence, required prerequisites and
exceptions respected, within context and cost budget, and zero known stale
protected-checkpoint crossing. Packet A reports those components separately but
uses the same action, currentness, purge, prerequisite/exception, budget, and
stale-state semantics. A missing outcome is `UNKNOWN`, never pass or failure,
and cannot compensate for a hard-safety failure.

The independent eligible-opportunity denominator is frozen before execution as
`E_w` for each workstream and arm. Eligibility is mechanism-independent and
assigned before any mechanism result. Every eligible opportunity enters `E_w`,
including an arm that abstains, errors, is unsupported, or has a missing run.
Each eligible opportunity receives exactly one final status. Coverage is
`count(non-MISSING response statuses) / E_w`; explicit `MISSING` remains in
`E_w` and contributes zero to coverage. A separately reported `E_eff` starts
from `E_w` and excludes only an independently diagnosed
`INFRASTRUCTURE_FAILURE` opportunity that was not exposed to a
mechanism-specific result. The exact response status allowlist is the full
frozen cell-status vocabulary, and non-abstention
is `count(SUPPORTED response statuses) / E_w`. Thus blocked, skipped,
not-exercised, missing, unknown, infrastructure-failure, attrition,
unsupported, abstention, and error statuses cannot disappear from the rate. If
eligibility cannot be decided before execution, the oracle records
`INDETERMINATE_PRE_ELIGIBILITY` outside `E_w`; it cannot be used later to remove
an eligible opportunity. A mechanism-defined scored-event denominator,
circular denominator, and after-outcome exclusion are prohibited.

The minimum positive/negative opportunity floors are 50/50 for prospective
memory, 50/50 for adaptive routing, and 100/100 for Continuity Debt. Each
coverage and non-abstention directional bound/test must clear 90% against that
same frozen `E_w`. Continuity Debt retains the category vector and distinguishes
correction, verification, new requirement, valid retry, avoidable repetition,
stale recovery, wrong-project recovery, and checkpoint drift.

The frozen estimands include CAOS by arm; the primary checkpoint/reconciliation
versus optimized-capsule CAOS difference; relative Continuity Debt reduction;
first-action correctness; context-token constraints; prospective recall,
blinded usefulness, false alarms, and scheduler outcome utility; adaptive
routing CAOS improvement; and hard-safety failure rate. Every estimand has an
explicit unique ID, population, unit, numerator or contrast, frozen denominator,
unknown/missing contribution, direction, and interval/test. Every estimand also
binds exact arm IDs, cell IDs, typed contrast operands, numerator/denominator
units, and explicit `MISSING`, infrastructure-failure, and attrition
contributions. Every numerical result must state those fields and its missingness rule. Individual proportions
use Wilson bounds; paired differences use exact or
stratified paired bootstrap bounds; deterministic safety uses an exact one-sided
95% Clopper–Pearson upper bound; and Holm controls the two primary contrasts.
Secondary measures are exploratory.

Allowed cell statuses are `SUPPORTED`, `UNSUPPORTED`, `BLOCKED`, `SKIPPED`,
`NOT_EXERCISED`, `MISSING`, `UNKNOWN`, `ABSTENTION`, `ERROR`,
`INFRASTRUCTURE_FAILURE`, and `ATTRITION`. Negative, missing, unsupported,
not-exercised, and abstention cells remain visible and receive no promotion
credit. A deterministic fixture/oracle/harness failure is excluded from an
efficacy denominator only when independently diagnosed and not exposed to a
mechanism-specific result. Attrition retains the last valid state and may use
only predeclared reserve IDs. No run, family, stratum, or episode can be
removed after its outcome is observed.

## Power inputs and later gates

The required future simulation is `bench/memory_reliability_power_simulation.py`,
version `packet-a-power-v1`, with seed `20260829`, 100,000 deterministic
replicates, control CAOS 0.75, alternative CAOS 0.85, target paired effect
0.10, the frozen joint distribution `(0.10, 0.15, 0.05, 0.70)`, correlation
`0.404226`, equal family/repository/stratum weights, 96 base cells, a
provisional minimum of 384 paired episodes, and final allocation
`96 × ceil(max(N_power, 384) / 96)` with final repetitions `N_final / 96`,
familywise alpha 0.05 with Holm across two primary contrasts, one-sided 95%
directional bounds, 90% power, a -0.02 noninferiority margin, and a frozen 15%
nonrecoverable infrastructure-loss allowance as a power input. The future
confirmatory driver, inputs, outputs, and digests are not present or executed
in this freeze. The executable method reference is present at
`bench/packet_a_power_reference.py` and is source-bound by the digest above.
Its reproducible computation method is frozen in
`packet_a.power_simulation.computation_method.reference_method_contract`:
SHA-256 counter-stream draws use an explicit domain, version, typed fields,
lengths, delimiters, big-endian integer encoding, draw-kind allowlist, and
golden digest/uniform vectors. Episodes map exactly to 96 cells; utility rows
are control levels and columns are alternative levels. Infrastructure loss,
missing, and invalid dispositions are explicit. The paired-binary CAOS
contrast uses an exact conditional sign tail and stratified paired percentile
bootstrap; scheduler outcome utility uses a relative-effect statistic,
studentized sign-flip test, and stratified relative-effect bootstrap. Holm
ordering, ties, conjunctions, zero variance, zero ratio, quantile
interpolation, and counter ordinals are all fixed. The candidate estimator
evaluates 100,000 fixed replicates across the balanced grid and selects the
smallest candidate meeting both contrast targets; if none meets the target,
derived N remains unset and no receipt is emitted. The binary CAOS method is
not reused for utility.

The closed interim and stopping policy is also frozen: interim peeking,
optional stopping, adaptive sampling, reallocation, early stopping, futility
stopping, and harm stopping are all prohibited, with no exceptions. All
candidate N values and all 100,000 replicates must be evaluated before a
simulation result can be emitted. This is still a specification-only policy;
no power computation or result is claimed here.

The provisional planning value 384 is non-authoritative. The later manifest
must bind to the independently emitted derived N from the reproducible power
simulation, whatever that N is; it must not replace that value with an assumed
384 or require the simulation to equal the planning value.

The later benchmark-manifest freeze requires all six prerequisites recorded in
`packet_a.later_manifest_prerequisites`: reproduced N and full episode layout;
versioned/content-digested arms, controls, oracles, budgets, permissions,
mutations, and ablations; reproducible power-script/input/output digests;
passing calibration and fixture/oracle/secret/isolation/lifecycle/budget gates;
unchanged CAOS, safety, denominator, confidence, multiplicity, opportunity, and
missingness rules; and a manifest digest recorded before any confirmatory result
is read. Each future fixture repository and source state must also be bound by
repository ID, immutable commit/ref, complete file inventory, and SHA-256
content digest before results. The final N, confirmatory fixture IDs, and
benchmark manifest remain unset until those gates pass.

## Content binding and validation

The machine-readable spec uses SHA-256 over the complete JSON document,
canonicalized as UTF-8 with sorted keys and compact separators while omitting
only the digest field itself. It also records SHA-256 provenance for the
proposal, evaluation program, governance, Wave 4 result/review/oracle, the
existing logical fixture input, and the independently authored
`bench/packet_a_contract.py` authority source, and the executable
`bench/packet_a_power_reference.py` method reference. The focused unit contract
verifies those digests and rejects drift. The contract source digest replaces
its three derived digest literals with fixed placeholders before hashing, so
coordinated digest rebinding does not alter the authority-source identity.

`bench/validate_memory_reliability_spec.py` is independently authored with
immutable expected vocabularies, field-level semantic contracts, a code-owned
canonical semantic digest, and independent validator/contract source bindings.
The candidate
self-digest is only an internal consistency check: changing JSON and
recomputing that field cannot pass. Public JSON, narrative, and provenance
reads are bounded before parsing or hashing, with byte, depth, node, string,
number, duplicate-key, non-finite, malformed-input, link/reparse, special-file,
hard-link, root-containment, and before/after read-identity checks.
The same bounded parser is used for the fenced JSON binding embedded in this
Markdown. Exact expected keys remain enforced without echoing unknown or
secret-like key names, duplicate keys, non-finite values, parse errors, or
discarded deep values in diagnostics. The validator also validates this freeze
Markdown's exact-byte semantic digest, evidence level, and execution-boundary
block, and binds the proposal erratum and future
power/fixture/manifest/execution/evidence receipts. It is deliberately
fail-closed for circular or after-outcome denominators, missing or undeclared
cells, inconsistent relative/difference units, bad permission/safety
boundaries, incomplete M1/receipt/ACL/sensitivity/secret contracts, unknown
statuses/categories, source/specification/narrative digest drift, and
accidental execution, manifest, result, production, or L2/L3 claims. These
are specification-integrity checks; a rejection is not a benchmark result.

### Machine-readable binding

The following block is authoritative only as a cross-document binding. The
validator requires it to match `packet_a` exactly:

```json
{
  "specification_digest": "cb050608e87fd150141a8678bed586bd7bcf90a1d7256e7f0b430551031b259e",
  "contract_source_sha256": "a8a4089915bd7575d186ea0f71a0dad950fd4690558611c0caaef999ba79f213",
  "power_reference_source_sha256": "747833273182110b65a230dcef2b290327265d3c7b340959898935044d475500",
  "evidence_level": "L0",
  "execution_boundary": {
    "packet_a_executed": false,
    "benchmark_manifest_exists": false,
    "confirmatory_results_exist": false,
    "production_behavior_changed": false,
    "model_or_provider_run_performed": false,
    "l2_or_l3_packet_a_evidence_claimed": false,
    "wave4_l2_provenance_is_historical_input_only": true
  }
}
```

No fixture, benchmark, model, provider, production Core, or external action was
executed for this freeze. No raw context, credentials, imported prose, hidden
reasoning, or executable payload is retained.
