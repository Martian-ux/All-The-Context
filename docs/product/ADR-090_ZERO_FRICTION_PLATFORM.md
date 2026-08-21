# ADR-090: Adopt zero-routine-friction as the post-V1 product direction

**Status:** accepted 2026-08-20.

## Decision

After the first usable beta, All The Context will be developed as background
infrastructure rather than as a memory-management application. After initial
installation and explicit account/client authorization, healthy normal use must
not require a memory inbox, manual classification, project or graph curation,
repeat history imports, retrieval tuning, or routine dashboard administration.

The normative product contract is
[`ZERO_FRICTION_PLATFORM.md`](ZERO_FRICTION_PLATFORM.md). The dependency- and
evidence-ordered implementation plan is
[`ZERO_FRICTION_EXECUTION_PLAN.md`](ZERO_FRICTION_EXECUTION_PLAN.md).

This decision does not expand, block, or provide acceptance credit for
`0.1.0-beta.4`. Continuous connectors, lifecycle-aware client hooks, the broader
event substrate, automatic project discovery, project graphs, Project Context
Capsules, working-state continuity, outcome learning, and remote/mobile access
remain post-V1 behavior until separately implemented and accepted.

## Capability-qualified promise

Zero routine friction is guaranteed only within the capability declared and
accepted for a connector or client integration. Ordinary L0 MCP remains a
best-effort compatibility path: it cannot guarantee context delivery before
generation or complete automatic capture. Stronger pre-generation, checkpoint,
outcome, or consequence guarantees require the corresponding lifecycle-aware
adapter level and acceptance evidence.

Unsupported behavior must be reported as unsupported, incomplete, or requiring
user action; it must not be inferred from installation alone.

## Evolution, not parallel replacement

The post-V1 platform extends existing ATC mechanisms:

- source records and source blobs remain the authority for retained raw source
  evidence;
- the observation ledger remains the durable-context proposal and policy
  surface;
- current records, versions, temporal resolution, permissions, deletion, and
  purge remain Core-owned;
- Retrieval V3 remains the authorization/time-first production baseline and the
  comparator that project, graph, or learned mechanisms must beat;
- `bootstrap_context` and `search_context` remain the L0 compatibility entry
  points, while lifecycle-aware clients invoke the same compiler earlier;
- existing Memory Lab M1 observable-use work and M3 influence-closure work are
  starting contracts and test oracles, not invitations to create duplicate
  ledgers or repair systems; and
- prior Project Context Capsule research is refined into the structured,
  disposable capsule defined by the product contract rather than recreated as a
  separate authority.

The first `SourceConnector`, `ClientRuntimeAdapter`, event, and lineage
interfaces are experimental v0 contracts. They become stable SDK/ABI promises
only after a fake harness and at least one real continuous connector/client
vertical slice exercise restart, replay, coverage, correction, deletion, purge,
and capability negotiation.

## Event and retention boundary

The broader evidence/experience stream is append-only during ordinary retained
operation, but it is not a second permanent raw-history store and is not exempt
from privacy deletion.

- direct secret-like payloads are refused before durable event persistence;
- source-owned raw bytes remain in the existing source/blob boundary, while
  events normally retain bounded envelopes, references, or only the payload
  needed for their declared evidence role;
- each event carries an explicit retention class and content ownership;
- temporary working events expire or compact under their declared policy;
- operational events remain content-free and bounded where possible; and
- terminal purge may destructively remove content-bearing events and derived
  state while retaining only the minimum opaque identity/generation barrier
  needed to prevent resurrection.

Calling an event immutable means clients and workers cannot rewrite it while it
is retained. It does not prohibit authorized retention expiry or administrator
purge.

## Project ambiguity

Automatic project discovery must support an unresolved or ambiguous state.
When evidence does not justify one project assignment, ATC must abstain from
project-specific graph expansion or capsule injection and may fall back to
project-neutral authorized retrieval. It should ask the user only when the
ambiguity is material to the requested answer or action and cannot be contained
safely. Zero routine friction does not justify silently attaching another
project's context.

## Issued-state limit

Correction, permission change, deletion, and purge close future ATC influence.
ATC must stop issuing displaced state, invalidate or replace it at future
checkpoints, and notify connected conforming adapters where supported.

ATC cannot retroactively erase context already transmitted to an external
provider or client, undo a completed model response, or delete another
provider's logs. Product and acceptance language must not imply those powers.

## Parallel post-V1 tracks

The zero-friction program does not absorb successor-beta and stable hardening.
Real N-1 update evidence, forward-version refusal, migration and compatibility
policy, recovery improvements, support bundles, stable APIs/schemas/exports,
and release-channel hardening remain a separate post-V1 track represented by
issue #30 or its successor.

Before creating the `ZF-*` issue hierarchy, the current beta tracker must be
reconciled to the active beta.3 Windows/Ubuntu scope without erasing historical
beta.1/macOS evidence. Source-readiness and exact-downloaded-candidate
acceptance dependencies must also be separated so candidate creation is not
circularly blocked by tests that require the candidate to already exist.

## Consequences

- Product sophistication that adds routine user administration is a regression.
- Healthy operation is quiet, but material incompleteness and required user
  action remain visible.
- Connected content remains inert evidence and never gains instruction or truth
  authority merely through acquisition.
- Graphs, summaries, capsules, checkpoints, learned mechanisms, and statistics
  remain dependency-bound disposable projections.
- Each stronger integration or memory mechanism requires truthful capability
  claims and comparative evidence before production promotion.
