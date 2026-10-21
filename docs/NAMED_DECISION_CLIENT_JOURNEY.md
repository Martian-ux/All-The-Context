# Registered named decisions through actual source hooks

This test-only composition starts at named-decision delivery
`659d73792d10c6a067dd44af0ef944202ac92a4b`. Its production packages remain
byte-identical to that exact runtime. The harness and original test module are
copied byte-for-byte from `614c9a74aff704eadf0f286870bcec2475828bb9`.

## Preserved negative and separate realistic journey

The original `test_desired_project_decision_reaches_other_source_hook` remains
unchanged, including both strict xfails and their historical reason string.
Its helper registers `Identical synthetic display label`, not Harbor, as
`structured_value.project_name`. On this runtime, that case is a deliberate
name-condition mismatch. Its xfail no longer establishes that ordinary named
decision formation is absent. The historical source-harness document records
older-runtime findings; this document supplies the current composition's
interpretation without rewriting the preserved negative fixture.

`test_named_project_decision_client_journey.py` independently registers explicit
Harbor and Cedar project anchors through public owner APIs. Their opaque scope
and entity keys differ from both names; only the structured anchor name supports
named wording, and the canonical binding supplies authority. Distinct ephemeral
capture/read principals are registered and bound through the public API.

Both provider directions exercise complete user decisions, actual fresh source
reader subprocesses, correction and committed history/evidence, assistant-stop
non-authority, foreign-name capture rejection, foreign direct/truth reads, a
foreign query hint that cannot override binding, two cold Core process restarts,
new bound reader principals, original/correction replay, explicit owner forget,
fresh ordinary assertion after deletion, and preservation of an independent
database decision and Cedar's separate cache decision. The cache progresses
DuckDB version 1 to SQLite version 2, then tombstone version 3. The same-project
database stays PostgreSQL version 1. Each delivered ID/version resolves through
authenticated public truth with matching content, typed subject/value, canonical
partition, scopes and evidence. A formed decision is selected independently of
the raw observation; both are preserved.

Codex has stable source session/turn IDs; original-event replay preserves the
observation count and current corrected truth in that provider direction. Claude's
callback has no stable native turn ID, so its old conflicting statement is not
resubmitted before deletion or credited as original-event replay. A repeated
expected-old correction may add tentative evidence but cannot advance history.
After deletion, Claude's old assertion is deliberately fresh evidence and must
respect the tombstone. New subprocess readers and newly registered cold readers
provide source-context delivery evidence, not proof that a model used references.

## Reproduction and boundaries

Run serially with existing development dependencies and a fresh disposable path:

```powershell
python -B -m pytest tests/integration/test_synthetic_client_acceptance.py tests/integration/test_named_project_decision_client_journey.py -vv -p no:cacheprovider --basetemp <new-owned-temp-path> --junitxml <receipt-path>
```

The copied harness owns a marked new scratch vault, ephemeral IPv4 loopback Core,
null keyring, fake client homes and isolated environment without native model
credentials or inherited ATC bindings. It refuses an existing scratch directory.
Source hook code and transport remain unmodified. Optional read diagnostics
serialize only fixed stages/status, exception classes and numeric timing/error
fields; no credential, message, local variable, response body or proof is logged.
Observer scheduling overhead remains a limitation of this supporting evidence.

Installed Codex/Claude event dispatch, app hook registration, MCP negotiation,
real application grants, native-model account/allowance and reader-model use of
citations remain UNRUN. No actual application, live vault, installed service,
credential, permission, network or account setting is touched. The previous
native control-socket diagnosis is not retried. No push, merge or deployment is
performed. Frozen v1 and sealed holdouts remain unchanged and unopened.

The named runtime's existing broad evidence is 3,909 passed and 21 skipped across
3,930 selected IDs, with the three inherited exclusions explicit. Those are
base-owner receipts, not a new composition-wide run. New coherent source-hook,
focused named-public and static results are recorded separately in the local
delivery packet; expected name-mismatch failures earn no positive acceptance
credit. The older historical unavailable-read observation remains unclassified
and is not explained or repaired by a later passing run.
