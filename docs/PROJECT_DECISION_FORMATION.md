# Bounded project decision formation

This lane is based on runtime `27a855f8ae3321c602ab8390bdfeb78677a5c15f`.
It owns a pure formation policy and its direct regressions. Shared storage and
capture integration remain the parent's responsibility. This is not an enabled
runtime path or final integrated acceptance.

## Meaning and authority

Accept one complete affirmative choice of a technology for a bounded technical
role, explicitly referring to the current project. The vocabulary covers
database/storage, implementation language, frontend framework, API format,
test runner and package manager. Values belong to their role, not to fixture
labels or evaluator cases. Equivalent wording and documented aliases identify
the same slot/value. Unsupported choices abstain rather than invent meaning.

Only a direct user turn under an existing transaction-resolved
`ResolvedProjectContext` may produce a plan. The binding says where the evidence
was captured. The statement supplies the user's decision; neither establishes
observed deployment, an outcome, team consensus, universal applicability or
general project authority. No names, query terms, working directories or client
metadata select an identity. The entity is
`project-decision:<canonical_project_id>` with exactly the existing project scope.
Personal facets use their existing separate entity and extraction path.

Accept explicit present choices and present-perfect decision declarations,
including `For this project, use SQLite for storage.` and
`I have decided to use PostgreSQL for this project's database.`. A correction
must name both the new choice and the old value in that same role. Negation,
uncertainty, attribution, questions, history, outcomes, hypothetical conditions,
other-project references and mixed turns abstain in full. Do not split a turn,
discard a qualification, or promote a substring. Raw capture remains evidence.

## Parent integration contract

1. Resolve the existing binding inside `record_client_capture`'s write
   transaction. Re-derive the decision from the exact committed user content;
   caller-authored kind, scopes, slots and structured fields are not authority.
   Invoke this policy before any broad legacy live-user fallback for project
   decision-like text; otherwise a rejected historical/tooling turn could still
   enter `workflow`. Do not change binding registration or authenticated grants.
2. Preserve the raw capture candidate. Build a separate formed candidate using
   the existing `client_capture_formation` source tuple, capture event link,
   binding hash and `:formed` idempotency convention. Content retains the entire
   original turn. Structured fields express a user's decision, never an outcome.
   Keep explicitness false; Core alone chooses the live-evidence origin.
3. Select a unique authorized current predecessor of the same project, kind,
   entity, attribute and exact scope under the same write lock. Respect typed
   tombstones, conflicts, future validity, expiry, deleted evidence and existing
   ACLs. Retrieve its exact committed capture binding using the record's
   source reference and capture-observation link; never accept a payload receipt
   or a receipt for an unrelated event. Both source bindings must name the same
   canonical project; their client/binding IDs may differ. The pure predecessor
   check verifies this receipt and formed source tuple but cannot fetch it.
4. Equivalent ordinary turns may reinforce their slot. Conflicting ordinary
   decisions require the existing chronology/conflict rules. A correction needs
   an exact old-value match and transaction-derived record ID/version, then the
   existing compare-and-swap semantics. Stale/replayed corrections must not
   overwrite a newer value, including SQLite to PostgreSQL to SQLite (ABA).
   The pure guard requires a strictly newer capture `observed_at` for corrections
   and rejects equal, absent, naive or future chronology. Supply actual committed
   event time, never current wall time, and retain the runtime's event idempotency.
   Preserve each observation, old content/source history
   and current-source attribution. Do not let arbitrary keyed/admin/archive
   records become decision predecessors.
5. The existing storage admission allowlist lacks `project_decision`, and keyed
   lookup/CAS guards currently specialize personal preferences. Coordinate these
   changes with the parent; this lane does not overwrite `storage.py` or enable
   broader authority to bypass those gates. If implementation requires new
   witnessed authority or a stronger project-identity schema, stop there.

## Evidence boundaries

Direct synthetic regressions cover paraphrases, alias equivalence, role
separation, negative/uncertain/attributed/mixed statements, unbound/non-user
abstention, cross-project predecessor rejection, source receipt mismatch,
historical/deleted/conflicted states, replay and exact-old-value corrections.
They exercise the pure policy, not an integrated capture/correction lifecycle.
Authenticated project/personal regressions and broad checks protect the
unchanged runtime. Frozen v1 fixtures, scores and sealed holdouts are unchanged.
Storage integration, exact reviewed base integration and final independent
runtime acceptance remain explicit parent gates. The reviewed runtime's broad
result is 3,685 passes, two inherited mixed-turn delivery-budget failures and
21 skips. Both failures are preserved; this pure policy does not alter those
fixtures or the long-turn extractor.
