# Discussion proposal: the next zero-friction vertical slice

| Field | Proposed value |
|---|---|
| Status | Discussion proposal only |
| Relationship to ADR-090 | Suggested refinement of the accepted direction, not a replacement decision |
| Relationship to PR #73 | Starts from the merged Import Truth, Memory Truth, Retrieval, and Continuous Capture foundations |
| Release effect | None |
| Implementation effect | None until separately reviewed and accepted |
| Scheduling rule | Dependencies and evidence should determine order; this proposal makes no effort or calendar estimate |

## 1. Intent

This document suggests a possible next product-development sequence after PR #73.
It is deliberately written as a proposal rather than a project decision. It does
not approve an architecture, create implementation commitments, assign issue
ownership, expand provider support, or grant release or acceptance credit.

The central suggestion is to prioritize one narrow, complete zero-friction loop
before adding broad integration coverage, production graphs, learned retrieval,
mobile access, or stable third-party SDK promises.

A useful target might be:

```text
source evidence arrives automatically
→ Core retains and evaluates it through existing authority boundaries
→ appropriate evidence becomes an observation
→ automatic policy forms or updates canonical memory
→ a lifecycle-aware client receives relevant context before generation
→ a natural correction changes future context
→ restart and replay create no duplicates
→ deletion or purge closes future ATC influence
```

The proposed product test is not whether ATC can store more information. It is
whether a user can work normally, state and correct durable information
naturally, and benefit in a later session without a save command, review inbox,
manual classification step, or routine dashboard visit.

## 2. Why this may be the right next milestone

PR #73 appears to establish most of the safety and truth foundations that a
zero-friction system would need:

- truthful and bounded import accounting;
- an observation ledger and canonical Memory Truth projection;
- correction, deletion, restoration, purge, and provenance boundaries;
- deterministic authorized retrieval and context-pack accounting; and
- a provider-neutral Continuous Capture ledger with ordered events,
  checkpoints, idempotency, leases, retry, and recovery.

The remaining uncertainty is primarily compositional. Individually correct
storage, policy, capture, and retrieval components do not yet prove that ATC can
operate as one automatic end-to-end product.

I therefore suggest proving the complete loop before increasing internal
sophistication. Project graphs, capsules, working-state continuity, outcome
learning, and integration breadth would have a stronger foundation once one real
source and one real lifecycle-aware client have exercised the whole path.

## 3. Suggested governing principles

Any accepted version of this direction would still need to preserve the
existing project constraints:

1. Core remains the sole canonical authority.
2. Connectors, clients, models, and imported text submit evidence or queries;
   they do not write current records directly.
3. Connected content remains inert untrusted data.
4. Authorization, currentness, temporal state, deletion, and purge run before
   derived work or disclosure.
5. A stronger integration capability is claimed only when the required hooks
   actually exist and have acceptance evidence.
6. Healthy operation should not require routine dashboard use.
7. Correction, retention, expiry, deletion, and purge should close all supported
   future ATC influence through dependency-bound invalidation.
8. A more complex mechanism should beat the strongest simpler accepted
   baseline before production promotion.
9. Early contracts should remain experimental until a fake harness and one real
   vertical slice have exercised them.
10. This feature track should remain separate from successor-beta migration,
    updater, compatibility, and release-control work.

## 4. Proposed milestone

A possible milestone name is:

> **Zero-friction vertical slice v0**

The milestone could be considered successful when one narrow integration pair
can complete this journey:

1. Install ATC.
2. Connect one source and one lifecycle-aware client once.
3. Complete an initial bounded backfill.
4. Begin a new interaction without opening the dashboard.
5. Receive relevant authorized context before generation.
6. State a durable preference or project decision naturally.
7. Observe the information in a later session without a save command.
8. Correct it naturally.
9. Verify that future context uses the correction rather than the displaced
   value.
10. Restart Core and the client.
11. Resume capture and delivery without duplicate events, observations,
    decisions, or records.
12. Delete or purge the information and verify that it no longer influences
    future ATC-issued context.

This would be a narrow product proof, not a claim that every provider or client
supports zero-friction operation.

## 5. Suggested implementation sequence

The PR boundaries below are illustrative. Review may find that some should be
combined or split further. The important suggestion is to preserve the evidence
order and avoid introducing several new authorities at once.

### Proposal A: reconcile the experimental v0 contracts

A small, migration-free contract PR could define how the existing PR #73
capture foundation participates in the larger zero-friction system.

Suggested scope:

- reconcile the existing capture adapter and ledger with a proposed
  `SourceConnectorV0` contract rather than creating a second event-ingestion
  mechanism;
- define a connector capability manifest covering snapshots, incremental
  events, cursor semantics, freshness, delete support, authorization,
  reauthorization, network behavior, data egress, retry, and health states;
- define `ClientRuntimeAdapterV0` and truthful L0-L3 capability levels;
- define the minimum event-to-observation formation seam;
- define projection lineage and invalidation requirements; and
- add sanitized synthetic conformance fixtures.

Suggested exclusions:

- no real provider;
- no production scheduler;
- no OAuth implementation;
- no graph or embedding system;
- no network call in conformance tests;
- no stable public SDK or ABI promise; and
- no new migration.

Possible exit evidence:

- a synthetic connector can represent complete, partial, unavailable,
  reauthorization-required, retryable-failure, disconnect, delete, restart, and
  replay states;
- a synthetic client cannot claim pre-generation or direct-turn capability
  without providing the corresponding hooks;
- event formation can produce observations only through Core's existing policy
  boundary; and
- correction, permission change, source drift, deletion, expiry, and purge have
  declared invalidation behavior for every proposed derived artifact.

### Proposal B: build a disposable zero-dashboard harness

A second PR could prove composition without touching an operator vault or live
provider.

Suggested components:

- a fake continuous connector;
- a fake lifecycle-aware client;
- a disposable retained event stream;
- an idempotent Core application sink;
- a deterministic formation baseline that feeds the existing observation
  ledger;
- automatic policy evaluation;
- pre-generation context compilation through the existing Retrieval path;
- restart and replay fixtures;
- correction, retention, deletion, and purge fixtures; and
- one end-to-end test that never opens the dashboard.

The first formation classes could remain deliberately conservative:

- explicit directly observed user claims;
- explicit corrections;
- explicit forget requests;
- deterministic source facts;
- strongly evidenced project goals, decisions, and constraints; and
- temporary working-state updates with an explicit retention class.

Summaries and inferred claims could remain tentative or disposable projections.
The harness should not create a parallel current-memory path.

Possible exit evidence:

- replay produces the same effective observations and no duplicate current
  memory;
- direct secret-like content is refused before durable event storage;
- unauthorized event content cannot reach retrieval or the client;
- correction changes future context;
- deletion and purge close future context influence;
- restart preserves cursors and idempotency; and
- the complete journey requires no memory inbox, save command, or dashboard
  action.

### Proposal C: prove one real vertical slice

Only after the disposable harness passes, a real vertical slice could add:

- Core-owned scheduling and connector health;
- one real or locally controlled source connector;
- one lifecycle-aware client or reference host; and
- conservative automatic formation over retained events.

This may be too much for one PR. It could be divided into a scheduler/health PR
and an integration-pair PR if that produces clearer review boundaries.

#### Suggested first source: a local Git/workspace connector

A local Git or workspace connector may be a good first candidate because it
could provide:

- locally controlled acquisition;
- stable repository, branch, commit, path, and workspace identities;
- straightforward incremental state;
- no scraping requirement;
- no OAuth dependency;
- sanitizable fixtures;
- strong project relevance; and
- a natural bridge into later project identity and capsule work.

This is only a suggestion. Another source may be better if it has clearer
lifecycle semantics, stronger official APIs, or a more representative user
journey. The selection should be made through an explicit comparison rather
than assumed here.

#### Suggested first client: a controlled reference host

The first client should ideally provide real hooks for:

- pre-generation context requests;
- directly observed user turns;
- tool and observable result events;
- task and compaction checkpoints;
- response emission;
- task completion or abandonment; and
- restart and session transitions.

Ordinary MCP access could remain a useful L0 compatibility path, but it should
not be described as lifecycle-aware merely because it can call a retrieval
tool. Pre-generation delivery and direct-turn capture require stronger hooks.

Possible exit evidence:

- one account/source connection produces an initial snapshot and later
  incremental evidence;
- one client receives context before generation;
- directly observed corrections reach Core without model self-attestation;
- integration capability is negotiated and reported truthfully;
- retry and restart create no duplicate publication; and
- the milestone journey in section 4 passes on the real integration pair.

## 6. Suggested project-intelligence sequence after the vertical slice

If the zero-friction loop works for one real pair, I suggest making project
intelligence the next differentiating product layer.

### 6.1 Stable project identity before a production graph

ATC would likely need:

- opaque project IDs;
- names and aliases;
- repository, workspace, source, and task bindings;
- evidence-based discovery;
- explicit `resolved`, `unresolved`, and `ambiguous` outcomes;
- project-neutral fallback retrieval;
- rename, merge, split, archive, correction, and purge behavior; and
- cross-project authorization rules.

A proposed safety rule is:

> When project assignment is materially ambiguous, ATC should provide
> project-neutral context rather than silently attaching evidence to the wrong
> project.

Without stable project identity, a graph may organize misassigned data more
convincingly without making it more correct.

### 6.2 Project Context Capsules as the primary product output

The most useful user-facing artifact may be a deterministic bounded project
briefing rather than a graph visualization.

A proposed capsule could include:

- current objective;
- constraints;
- decisions and rationale;
- components;
- dependencies and blockers;
- current working state;
- failed approaches;
- open questions;
- next actions;
- provenance;
- freshness and dependency generation; and
- integration capability level.

The capsule should extend the existing authorized Retrieval and context-pack
compiler path. It should remain a disposable dependency-bound projection rather
than a second project-memory authority.

### 6.3 Project graph in shadow evaluation

A graph could begin as an ephemeral projection over an already-authorized
snapshot. Initial relation families might be limited to structural or explicit
relations such as:

- `belongs_to`;
- `supersedes`;
- `depends_on`;
- `blocks`;
- `implements`; and
- `tested_by`.

Model-inferred causal, failure, or procedural relations could remain in shadow
until separately justified.

Suggested comparisons:

- existing Retrieval V3;
- structured project filtering;
- lexical retrieval;
- lexical seeds plus bounded one-hop expansion;
- lexical seeds plus bounded two-hop expansion; and
- relation-family ablations.

A relation family would be a production candidate only if it improves its target
journey over the strongest simpler baseline and passes cross-project isolation,
ambiguity, correction, history, deletion, purge, cycle, and high-fan-out tests.

A force-directed graph could remain optional inspection UI. Healthy operation
should not require users to curate nodes or edges.

## 7. Suggested hard-stop conditions

The following outcomes would argue against promoting the proposed mechanism,
regardless of aggregate retrieval quality:

- unauthorized content or unauthorized-derived signals reach a client;
- retry duplicates events, observations, decisions, or current records;
- direct secret-like content enters durable event storage;
- connected content changes policy as executable instructions;
- a correction does not change future ATC-issued context;
- deleted, expired, or purged information continues influencing a projection;
- a supported automatic journey requires routine dashboard use;
- a client or connector claims capabilities it does not possess;
- materially ambiguous project evidence is forced into project-specific
  context;
- optimized invalidation disagrees with a complete rebuild oracle;
- a graph, learned ranker, or external supplier fails to beat a simpler
  baseline; or
- diagnostics, fixtures, logs, or receipts expose personal context or
  credentials.

## 8. Suggested separation from the successor-beta maintenance lane

PR #73 adds meaningful schema and storage behavior. A beta.6 successor would
still need migration, forward-version refusal, backup, restore, updater,
rollback, and exact-artifact evidence.

I suggest handling that as a separate, narrow maintenance and release lane. The
zero-friction feature track should not absorb updater or publication work, and
the maintenance lane should not become the reason to delay all contract and
harness discussion.

The two tracks may proceed in parallel where safe, but they should not share one
mega-PR or one acceptance claim.

## 9. Work not suggested for the first slice

This proposal does not recommend including the following in the initial
vertical slice:

- several live providers at once;
- mobile or remote Core access;
- stable third-party connector or client SDK promises;
- embeddings, graph expansion, and learned reranking in one wave;
- project discovery and procedural learning in one wave;
- consequence enforcement;
- automatic provider-side deletion claims;
- a second canonical event, project, graph, or memory authority;
- routine graph curation; or
- a release-version or support-scope change.

## 10. Questions for review

Reviewers may want to challenge these assumptions before accepting any part of
this proposal:

1. Is a local Git/workspace connector the best first source, or would another
   locally controlled source produce a more representative product proof?
2. Which client or reference host can provide genuine pre-generation and
   direct-turn hooks without overstating capability?
3. Should the existing capture event be the shared event envelope, or should a
   separate bounded evidence event reference capture records?
4. What is the minimum retained payload needed for deterministic formation
   without duplicating raw source history?
5. Which event classes belong in durable truth formation, temporary working
   state, or disposable projections?
6. What should count as evidence that the user experienced less friction rather
   than merely that the system performed more background work?
7. Should scheduler and health work precede the first real connector, or can a
   foreground real slice produce enough evidence first?
8. How should project identity abstention be measured and exposed without
   creating routine user administration?
9. Which parts of the PR #73 Continuous Capture foundation are already
   sufficient, and which proposed v0 contracts would duplicate them?
10. What would falsify the recommendation to prioritize capsules before a
    production graph UI?

## 11. Suggested decision process

This document could be accepted, revised, or rejected without implying that any
implementation PR should begin immediately.

If the general direction is accepted, a reasonable next step may be to create
focused issues for only the contract and disposable-harness work. Real
connectors, project intelligence, and graph work could remain unscheduled until
the earlier evidence exists.

The proposal should not be converted into an ADR merely because it has a draft
PR. An accepted production decision should record the reviewed scope,
alternatives, evidence requirements, and explicit human approval separately.
