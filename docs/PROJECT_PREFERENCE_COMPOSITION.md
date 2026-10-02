# Local project-binding and preference composition (2026-10-02)

This isolated candidate starts at project-binding delivery
`42f067d834223a7039c68568d49b509a1c837544` and applies the personal preference
lane through `e05904d6a4d572a343bde7571ac8c49c0964741e`, whose common ancestor
is `cc49f62d44818dead799259a4c4fb62edf0af9a0`. Both original candidates are
preserved. This is local verification, not application provisioning or rollout.

## Reconciled contract

Capture resolves registered authority inside the existing write transaction.
For a bound principal, preference lookup, correction comparison and legacy
guards use `user@project:<canonical_project_id>` and exactly the registered
singleton project scope. An unbound legacy principal still uses `user` with no
scopes. Names, working directories, task hints and caller metadata cannot choose
either partition. Global and other-project records cannot supply predecessors
or reserve a bound facet. Same-partition records with incompatible scope, kind,
source or access remain guarded; no mismatch grants broader authority.

Explicit natural corrections retain the unique-current-record and exact-old-value
checks and derive ID/version under the same write lock. Ordinary equivalent
captures retain their semantic fingerprint for delayed cross-client reinforcement.
Typed tombstones prevent resurrection in their own partition. Generic legacy
records are classified only from their own complete statement and matching value:
known unrelated subjects permit empty typed facets; matching, unknown or inconsistent
subjects remain guarded. No existing history, evidence, legacy row or tombstone
is rewritten or migrated.

Project isolation remains enforced before relevant and mandatory record selection,
capsule construction and direct record/truth access. Personal facets may carry a
bound capture origin; this does not extract project decisions, infer global
applicability or add general semantic extraction. Schema 21, immutable binding
ownership, remote Edge legacy authorization and non-portable export exclusions
remain those of the project-binding candidate. There is no extra migration.

## Validation boundaries

`tests/integration/test_project_preference_composition.py` reuses the existing
authenticated project-binding fixture. Synthetic cases cross A/B facets and
corrections, delayed equivalent reinforcement, separate-store concurrent CAS,
legacy subject guards, incompatible scope/source/ACL/kind, tombstone/replay/cold
restart and binding failures before staging. A subprocess runs the actual older
bound runtime against a disposable vault, then opens that vault with this
candidate to check identity, replay receipts, correction history and legacy
compatibility. The older unbound-runtime tests remain unchanged.

The final focused run passes 146 tests, including all 25 combined adversarial
cases and both actual hook helpers over synthetic transport. Strict mypy passes
production, the shared helper and all four relevant test modules (120 files).
Ruff check/format, docs and whitespace checks pass. Scoped independent review
found no runtime blocker and did not run tests. Tested runtime source is
`27a855f8ae3321c602ab8390bdfeb78677a5c15f`; the final validation commit changes
documentation only. Packages, tests and benchmarks retain that exact source.

The broader applicable run covers all 3,708 selected test IDs exactly once:
39 specification checks run serially, followed by 3,669 parallel tests. Total
result is **3,685 passed, 2 failed, 21 skipped**, with three warnings. The two
failures are Codex/Claude variants of
`test_core_budget_omission_and_complete_provenance_survive_cold_new_reader` in
`tests/integration/test_hook_delivery_status_core.py`. Its fixture assumes a
long mixed preference/deployment turn forms automatically. The bounded preference
grammar leaves that compound turn raw, so `record_id` is absent before budget
or provenance checks can run. Both tests pass on unchanged binding `42f067d`
and fail on unchanged preference `e05904d6` with the byte-identical fixture
(SHA-256 `38637adff1eb9376cf9d380fe59ab77d467cc671a8e93312ec8dc7c96238b8ac`).
This compatibility gate remains **FAILING**. No existing assertion, fixture or
formation authority is changed to manufacture a passing composition.

Frozen development is **3/14**, zero operation failures and expected exit 1.
All checkpoint outcomes, failure details and retrieved observations match
preference `e05904d6`. Compared with the binding lane's 4/14, D01-project changes
from passing to missing the offline database decision at retrieve and compile.
D01-personal remains failing, with missing response style replacing forbidden
project influence. Other categories are unchanged. The composed delivery layer
adds seven existing generic notices; they receive no named unknown-facet credit.
The category table and exact report comparisons accompany the local receipts.
The frozen runner, adapter, grader and fixtures are unchanged. Only development
cases may run. The adapter does not activate opt-in bindings, so its categories
are regression diagnostics rather than a combined project-isolation acceptance
score. Failures remain failures; no score-based fixture or implementation tuning.

Installed Codex/Claude delivery and reader-model behavior are **UNRUN**. No
credentials, application bindings, real personal vaults or services are provisioned.
Held-out cases and their integrity-only reader remain sealed. Two unchanged native
Windows registry tests previously fail on base with error 6801 and remain explicit
exclusions. Release/security acceptance is independent of this local composition.

## Remaining product gates

Local identity and formed-slot reconciliation after export/restore is unsupported:
memory retains the old canonical key, while creating a new identity issues a new
key. Restore-to-new-binding correction is **UNRUN**; exports retain memory without
minting authority. Broader personal-versus-project applicability needs its own
policy and authorization. Bound readers separately granted `context:status` still
see vault-wide aggregates, and existing requests retain their read-consistency
semantics rather than linearizable revocation. These are inherited limits, not
silently solved by composing the candidates.

Useful product acceptance still requires automatic capture of natural mixed
claims and decisions, partial/unknown recall with provenance, and installed
cross-app delivery without a routine approval inbox or manual curation. This
composition addresses preference coexistence/correction and project isolation;
it does not establish that complete everyday memory loop.

Before a combined broad gate can close, reconcile delivery-only fixture setup
with the approved compound-turn abstention contract while retaining a separate
negative capture check and the frozen mixed-claim failures. That is a pending
test-contract decision, not permission to relax formation. Natural multi-claim
capture needs its own memory-engine work and acceptance evidence. Both original
candidates, the broad failure receipts and the unresolved compatibility gate
remain preserved; local composition does not authorize rollout.
