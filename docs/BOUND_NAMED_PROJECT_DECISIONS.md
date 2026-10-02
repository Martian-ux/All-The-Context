# Named decisions within an existing canonical binding

This follow-up starts at integrated delivery
`0d92d8cda38af61688dffa0f9cabd2e8d7309d2e`, whose reviewed runtime is
`f04917e12777195a1052988c3f237063a8eeedd4`. It uses the integrated public
capture, source-lineage, correction and bound-reader paths.

## Actual synthetic harness diagnosis

The separate source-hook harness is authenticated and bound. Its `pair` creates
distinct `context:capture` and `context:read` principals. `SyntheticHost.principal`
registers each via the public admin client API, then binds it to the canonical
opaque ID through `/v1/admin/project-bindings`. Hook subprocesses use those
ephemeral synthetic principals against a disposable loopback Core.

The fixture calls `host.project("harbor")`, but that function registers an
explicit anchor whose **project_name is `Identical synthetic display label`**.
`harbor` appears in fixture content, entity and scope; none is a registered name
contract. The mention in `I decided Harbor should use DuckDB for its offline
cache.` therefore does not match the bound anchor's asserted name. The old parser
also lacks that ordinary syntax, DuckDB and a distinct offline-cache role.
The frozen adapter remains unbound and cannot exercise this named-bound path.

The original strict-xfail harness remains untouched and its Harbor wording
continues to abstain. Do not make it pass by treating a fixture key, scope suffix,
content substring, query or working directory as a canonical project name.

## Narrow supported contract

Core first resolves the existing canonical binding and validates its exact
current explicit anchor under the capture write lock. Only then does it read
that anchor's existing `structured_value.project_name`. A bounded exact name
match is an applicability condition on the user's decision. It does not select
a project, mint a canonical identity, attest deployment/team authority, or grant
read/capture permissions. No security-schema, binding logic, grant or migration
changes are needed. This is an existing asserted display name within the binding,
not a new globally unique canonical-name registry.

Names support up to four ASCII alphanumeric words with spaces or hyphens,
casefold/NFKC presentation equivalence and no control characters or ambiguous
conjunction/negation/uncertainty words. Missing/unsafe names abstain. No aliases,
fallback display fields, scope/content inference or other-project lookup occurs.
Equal display names retain distinct canonical IDs, slots and record visibility;
the already-selected binding supplies their context.

Complete ordinary forms include `I decided Quarry should use DuckDB for its
offline cache.`, present-perfect variants, `For Quarry, I choose DuckDB for the
offline cache.` and `I choose DuckDB for Quarry's offline cache.`. Explicit
corrections name the old value and reuse existing current-source lineage, exact
old-value, event chronology, ABA, tombstone, ACL and CAS guards. Raw original text
and source history remain intact.

Database choices now include DuckDB. Offline-cache choices are a separate role
with SQLite/DuckDB; a cache decision does not overwrite the project's database
decision. These are general technical roles and values, not fixture labels.
Past-tense `I decided` is accepted as the user's complete decision assertion;
dated history, outcomes and qualifications remain unsupported. Foreign names,
unbound/non-user turns, negation, quotes, attribution, mixed clauses and uncertainty
abstain in full. Personal facets retain their existing paths.

A changed anchor name does not reinterpret old evidence or create alias authority.
Original-event replay retains its committed receipt. A new correction that needs
to re-parse an old named predecessor after rename fails closed; stable aliases
would need a separate contract and remain unsupported.

## Public regression evidence

`test_bound_named_project_decisions.py` provisions only synthetic projects and
principals through existing public APIs. It tests capture followed by a cold Core
and newly registered bound reader, raw evidence for the exact original Harbor
fixture mismatch, paraphrases, foreign/identical labels, unbound/unsafe names,
whole-turn abstention, corrections, replay, ABA and current-source history.
Existing decision/personal/binding tests remain regression checks. Frozen v1
objects and sealed holdouts are unchanged. These are public API/source-runtime
tests, not installed-app, live-vault, model-outcome or release acceptance.
