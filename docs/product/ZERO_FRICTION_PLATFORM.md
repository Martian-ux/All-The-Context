# Zero-Friction Platform Contract

| Field | Value |
|---|---|
| Status | Accepted post-V1 product contract under [`ADR-090`](ADR-090_ZERO_FRICTION_PLATFORM.md) |
| Relationship to V1 | Does not expand or block `0.1.0-beta.6` |
| Authority | The user-owned Core remains the sole canonical authority |
| Normal interaction model | Install once, authorize accounts and clients once, then use AI tools normally |
| Capability rule | Guarantees are limited to the declared and accepted connector/client capability level |

## Executive decision

All The Context must behave like infrastructure rather than a memory-management
application.

After installation and initial authorization, a normal user should not have to
think about ATC, decide what should be remembered, classify information, create
projects, maintain a graph, approve extracted memories, repeatedly import
history, choose retrieval settings, or open the dashboard to keep the system
working.

The product promise is:

> Connect ATC once. Within the capabilities of each supported integration, ATC
> continuously learns from authorized sources, organizes that knowledge,
> supplies current context before supported AI work, preserves project and task
> continuity, and applies ordinary conversational corrections to future use.

This promise is capability-qualified. An ordinary L0 MCP integration remains a
best-effort compatibility path and cannot guarantee pre-generation delivery or
complete automatic capture. Stronger guarantees require lifecycle-aware
integration levels and their own acceptance evidence.

The dashboard remains an optional inspection, correction, recovery, and
administration surface. Requiring routine dashboard use is a product failure.

Zero friction means zero **routine** friction. It does not remove deliberate
security boundaries. Initial authorization, permission expansion,
reauthorization, irreversible purge, damaged-vault recovery, and confirmation
before a consequential memory-derived action may still require the user.

## 1. User experience contract

### 1.1 Normal journey

A normal user journey is:

1. install ATC;
2. connect supported AI clients;
3. connect supported accounts and project sources;
4. choose a small set of privacy and retention defaults; and
5. stop thinking about ATC.

After that, and only where a connector or client declares the required
capability, ATC automatically:

- backfills available history;
- synchronizes new activity incrementally;
- records truthful source coverage and connector health;
- extracts durable facts, preferences, decisions, constraints, and corrections;
- retains uncertain material as noncurrent evidence rather than creating a
  review queue;
- discovers people, projects, components, goals, and relationships;
- preserves temporary working state across supported sessions and model changes;
- compiles the smallest sufficient context before a lifecycle-aware AI answers;
- captures directly observed statements and observable outcomes;
- invalidates stale summaries, relations, capsules, and working state after a
  correction, permission change, deletion, purge, or relevant source drift;
- maintains indexes, projections, backups, updates, and storage health; and
- asks for attention only when a real consent, security, or recovery boundary
  requires it.

### 1.2 Tasks the user must not routinely perform

The normal product must not require the user to:

- press a “remember this” button;
- identify every durable statement manually;
- approve a memory inbox;
- create or select a project before project context can work;
- tag conversations, records, files, or graph nodes;
- draw, repair, or curate graph relationships;
- choose embeddings, rankers, graph traversal settings, or context budgets;
- repeatedly download and import the same provider history;
- tell a lifecycle-aware client when to retrieve context;
- tell a lifecycle-aware client when to store a correction;
- reconcile duplicate or conflicting memories;
- run database maintenance, rebuild indexes, rotate logs, or compact storage;
- inspect connector status during healthy operation; or
- open the dashboard merely to keep memory current.

Advanced controls may exist, but the default experience cannot depend on them.
An L0 client that lacks lifecycle hooks may expose weaker behavior; ATC must
label that limitation rather than quietly transferring the missing work to the
user while claiming a stronger level.

### 1.3 Legitimate user attention

ATC may interrupt the user only for a bounded class of events:

- initial account or client authorization;
- an account that requires reauthorization;
- a requested permission expansion;
- an ambiguity that is material to the requested answer or action and cannot be
  contained safely;
- an ambiguous destructive request;
- irreversible purge;
- recovery when the vault cannot safely repair itself;
- insufficient storage that cannot be resolved automatically;
- a security-relevant connector or client failure; or
- confirmation before a protected consequential action.

Temporary network failures, retryable synchronization errors, routine index
rebuilds, healthy backup completion, and ordinary maintenance should remain
silent.

## 2. Product invariants

### 2.1 Core remains authoritative

Accounts, clients, models, extractors, graph engines, rankers, consolidators,
and external memory systems may submit evidence, candidate observations,
relations, rankings, summaries, checkpoints, or outcomes. They cannot directly
create canonical current truth, expand permissions, assign action force, or
weaken correction and purge.

### 2.2 Imported and connected content is inert

Text from conversations, documents, repositories, issues, messages, tool
output, and external services is untrusted data. It is never executed as an
instruction merely because a connector supplied it.

### 2.3 Authorization precedes all derived work

Permission, lifecycle, validity, deletion, purge, and temporal resolution occur
before relevance ranking, graph traversal, summarization, context compilation,
or disclosure. Unauthorized content must not influence vocabulary, scores,
relations, project identity, summaries, or diagnostics visible to a client.

### 2.4 Derived state is disposable

Indexes, graph edges, entity clusters, summaries, project capsules, working
sets, learned policies, caches, and usage statistics are projections. Every
projection must declare lineage, version, invalidation, rebuild, deletion, and
purge behavior before production promotion.

### 2.5 Corrections close future ATC influence

A correction is incomplete if the canonical record changes while an old graph
edge, summary, capsule, checkpoint, procedure, or future ATC issuance continues
to treat the displaced value as current.

For supported connected adapters, ATC should invalidate, replace, or notify at
the next declared checkpoint. ATC cannot retroactively erase context already
sent to an external provider or client, undo a completed model response, or
delete another provider’s logs. No product or acceptance claim may imply those
powers.

### 2.6 Silence is not permission to hide failure

Healthy operation should be quiet. Materially incomplete source coverage,
expired authorization, unsafe repair failure, ambiguous project applicability,
and unsupported guarantees must remain visible in bounded health state and
surface one actionable notification only when user action is required.

## 3. Evolution from the existing ATC system

The post-V1 platform extends existing mechanisms. It must not create parallel
systems that compete for authority.

| Existing mechanism | Post-V1 role |
|---|---|
| `source_record` / source blobs | Continue to own retained raw source evidence and source-level deletion/purge semantics |
| Observation ledger | Remains the durable-context proposal, policy-decision, and provenance surface |
| Current records and versions | Remain the only canonical current memory selected by Core |
| Permissions and temporal resolution | Continue to run before every retrieval, projection, graph, or capsule operation |
| Retrieval V3 | Remains the production baseline and comparator; new candidate generators feed its protected downstream gates rather than bypassing them |
| `bootstrap_context` / `search_context` | Remain L0 compatibility entry points; lifecycle-aware adapters invoke the same compiler before generation |
| Existing `current_project` and project scopes | Become migration hints and compatibility inputs to stable project identities, not a second project store |
| Memory Lab M1 | Supplies the starting observable-use/outcome contract and falsification fixtures |
| Memory Lab M3 | Supplies the starting dependency-complete influence-closure contract and full-rebuild oracle |
| Earlier Project Context Capsule research | Is refined into the structured disposable capsule in this contract rather than recreated independently |

The names `SourceConnector` and `ClientRuntimeAdapter` define conceptual seams.
Their first executable forms are experimental **v0** contracts. They become
stable SDK or ABI promises only after a disposable fake harness and at least one
real connector/client vertical slice exercise restart, replay, coverage,
capability negotiation, correction, deletion, and purge.

## 4. Platform architecture

```mermaid
flowchart TD
  Accounts["Connected accounts and project sources"] --> Connectors["Source connection fabric"]
  Clients["AI client runtime integrations"] --> Runtime["ClientRuntimeAdapter v0 → stable"]

  Connectors --> Events["Core-owned evidence and experience stream"]
  Runtime --> Events

  Events --> Formation["Automatic memory formation"]
  Formation --> Core["Core-owned current memory"]

  Core --> Organization["Automatic organization projections"]
  Events --> Organization
  Organization --> Projects["Entities, projects, typed relations, summaries"]

  Runtime --> Working["Expiring working-state checkpoints"]
  Core --> Working
  Projects --> Working

  Runtime --> Compiler["Context and capsule compiler"]
  Core --> Compiler
  Projects --> Compiler
  Working --> Compiler
  Compiler --> Clients

  Clients --> Outcomes["Observable use and outcome ledger"]
  Outcomes --> Events

  Change["Correction, permission change, delete, purge, drift"] --> Closure["Dependency invalidation and rebuild"]
  Closure --> Core
  Closure --> Organization
  Closure --> Working
  Closure --> Compiler
  Closure --> Outcomes
```

### 4.1 Source connection fabric

A `SourceConnector` gathers evidence from one authorized account or source.
Examples include conversation history, repositories, files, issues, task
systems, messages, calendars, and tool-result streams.

The experimental v0 connector contract should cover:

```text
connect
authorize
initial_snapshot
incremental_sync
checkpoint
coverage
health
reauthorize
disconnect
source_delete
```

Every connector declares:

- identity and contract version;
- acquisition method;
- account or source identity;
- supported data families;
- initial-history and incremental-sync capabilities;
- cursor and replay semantics;
- permission, network, and data-egress behavior;
- expected freshness and coverage limitations;
- retry and backoff behavior;
- deletion, disconnection, and purge coordination; and
- bounded user-action-required states.

Official APIs and OAuth are preferred where they provide a truthful, stable
path. Local application integrations and export-directory observation may be
used where appropriate. Manual archive import remains an honest fallback when
a provider offers no reliable continuous path. ATC must not silently add
fragile scraping merely to claim automatic synchronization.

### 4.2 AI client runtime integration

A source connector gathers evidence. A `ClientRuntimeAdapter` supplies and
captures context at the point an AI is actually working. These are separate
responsibilities.

A lifecycle-aware adapter may expose events such as:

```text
session.start
user_turn.received
context.requested
generation.before
tool.before
tool.after
response.before_emit
response.after_emit
compaction.before
task.checkpoint
task.complete
session.end
```

The adapter supplies bounded task, workspace, repository, conversation, and
client identity. At L1 or higher, ATC compiles context before generation rather
than depending on the model to remember to call a tool.

Directly observed user turns provide stronger witness evidence than model
summaries. Tool calls and results provide observable experience without
retaining hidden reasoning.

### 4.3 Evidence and experience stream

The observation ledger remains the durable-context policy surface. A broader
Core-owned event stream records lower-level evidence and observable experience
without automatically declaring it durable truth.

Representative event families include:

```text
user_turn_observed
assistant_response_observed
document_changed
repository_commit_observed
issue_updated
tool_called
tool_result_observed
task_started
task_checkpointed
task_completed
user_correction_observed
connector_sync_completed
```

The event stream must not become a second permanent raw-history store.

- Source records and blobs continue to own retained raw source bytes.
- Events normally retain bounded envelopes, source references, or only the
  payload necessary for their declared evidence role.
- Direct secret-like payloads are refused before durable event persistence.
- Every event declares content ownership and a retention class.
- Temporary working events expire or compact under declared policy.
- Operational and health events should be content-free and bounded where
  possible.
- Events are append-only while retained; authorized expiry and destructive purge
  remain allowed.
- Purge removes attributable content-bearing events and derived state, retaining
  only the minimum opaque identity or generation barrier needed to prevent
  resurrection.

Events are source-linked, bounded, idempotent, and replayable within their
retention boundary. Background formation workers may propose durable
observations from events, but only Core decides what becomes current.

### 4.4 Automatic memory formation

Memory formation distinguishes at least:

- explicit user claims and corrections;
- source-grounded factual observations;
- preferences and interaction instructions;
- project goals, decisions, and constraints;
- episodic experiences and outcomes;
- temporary working state;
- inferred relationships and summaries; and
- candidate procedures.

These forms have different authority and lifecycle rules. A successful-looking
agent action is not automatically a reusable procedure. A user preference is
not world evidence. A provider summary is not equivalent to a directly
observed user statement.

Extraction may use deterministic rules, local models, external models, or
reviewed external adapters. Every path preserves origin, source, model or
extractor version, confidence, network/egress declarations, and reproducibility
limits.

### 4.5 Automatic organization

ATC organizes current authorized evidence without requiring user curation.
Derived organization may include:

- entity resolution and aliases;
- stable project identities;
- typed temporal and dependency relations;
- bounded summaries;
- topic and domain classification;
- duplicate and conflict diagnostics;
- project capsules; and
- retrieval indexes.

A general graph is not mandatory infrastructure. Typed relation families must
individually earn production use against simpler lexical, event, or structured
baselines.

### 4.6 Working-state continuity

Working state is not durable personal truth. It records the current objective,
completed work, active artifacts, hypotheses, blockers, open questions, and
next likely action for a task or project.

Working state must:

- use stable task and project identities;
- be versioned and expiring;
- remain separate from canonical facts and preferences;
- survive supported session compaction and client changes;
- declare source and project dependencies;
- invalidate on relevant source drift;
- distinguish completion, abandonment, expiry, and supersession; and
- never claim that hidden model state was preserved.

### 4.7 Context and capsule compiler

The compiler produces the smallest sufficient current context for a particular
client, task, project, time, permission set, and character budget.

The required order is:

```text
authorization
→ temporal and lifecycle resolution
→ epistemic role and task applicability
→ lexical/structured/graph candidate generation
→ conflict and support closure
→ budgeted set selection
→ immediate version re-read
→ issue receipt
```

Graph traversal, learned ranking, and summaries may expand or order candidates.
They cannot weaken upstream policy.

### 4.8 Observable use and outcomes

ATC should eventually measure whether supplied context improved an observable
answer, plan, decision, or action. The ledger records assignment,
acknowledgement, observable use or nonuse where available, outcome, correction,
and invalidation without storing hidden chain-of-thought.

This layer extends the accepted Memory Lab M1 contract. It must not create a
parallel causal authority or treat an agent’s self-evaluation as truth.

### 4.9 Invisible operations

The platform should automatically manage:

- startup and bounded self-recovery;
- connector scheduling and retries;
- cursor persistence and idempotent replay;
- index, graph, summary, and capsule rebuilds;
- encrypted backup scheduling and retention;
- update verification, installation, health checks, and rollback where
  supported;
- storage budgeting and bounded cleanup;
- SQLite checkpoint and compaction maintenance;
- redacted diagnostics and bounded log retention; and
- health notification deduplication.

## 5. Integration capability levels

ATC describes integration strength honestly.

| Level | Capability | Truthful guarantee |
|---|---|---|
| L0 | Ordinary MCP tools | Best-effort model-initiated retrieval and observation submission |
| L1 | Pre-turn and post-turn runtime hooks | Context is supplied before generation and direct user turns are observed |
| L2 | Tool, compaction, task, and outcome checkpoints | Working state and observable experience survive supported session boundaries |
| L3 | Synchronous pre-effect checkpoint | A conforming host can enforce a current memory-derived confirmation, prerequisite, or block before a declared transition |

A connector has a separate acquisition capability manifest. A connected source
that supports only manual snapshots cannot be described as continuously
synchronized.

L0 remains useful for broad compatibility. It must not be described as providing
the same capture, use, working-state, or consequence guarantees as
lifecycle-aware adapters.

## 6. Automatic project intelligence

Projects are a critical organization boundary, but users should not administer
them manually.

ATC should infer projects from signals such as:

- repository and workspace identity;
- package, product, and application names;
- issue and pull-request references;
- branches, commits, documents, and artifacts;
- recurring goals, components, constraints, and decisions;
- explicit phrases such as “for my red-teaming project”; and
- established links between conversations and project sources.

Core assigns a stable opaque project ID. Human-readable names are aliases, not
identities. Projects may be renamed, merged, split, archived, or corrected
without losing history.

Project assignment has at least three outcomes:

- `resolved` — evidence supports one authorized project;
- `unresolved` — evidence does not yet support a project; and
- `ambiguous` — multiple projects remain materially plausible.

Unresolved or ambiguous evidence must not silently enter a project-specific
graph or capsule. ATC may use project-neutral authorized retrieval and wait for
stronger signals. It asks the user only when project-specific context is
material to the answer or action and ambiguity cannot be contained safely. A
natural-language correction such as “that belongs to project B, not project A”
repairs future organization.

### 6.1 Project memory graph

The project graph is a typed, rebuildable projection over authorized Core
records, source snapshots, and working state.

Useful node families include:

```text
project
goal
decision
constraint
component
repository
document
issue
experiment
result
task
person
artifact
```

Initial relation families may include:

```text
belongs_to
depends_on
blocks
implements
supersedes
supports
conflicts_with
tested_by
failed_because
requires
owned_by
derived_from
```

Each relation records authority class, provenance, project identity, dependent
record versions, validity, extractor or compiler version, and projection
revision. A generic production `related_to` edge is prohibited unless a later
benchmark defines a precise useful meaning.

The graph never becomes a second authority. Model-inferred edges remain shadow
or tentative until their relation family and admission path pass dedicated
promotion gates.

### 6.2 Project Context Capsule

A Project Context Capsule is the user-facing project primitive: a structured,
disposable projection containing the smallest useful set of:

```text
project identity
current objective
active constraints
relevant preferences
major decisions and rationale
architecture and components
current working state
recently completed work
blockers
open questions
known failed approaches
next likely actions
source references
freshness and dependency revision
```

The client runtime supplies task and workspace signals. ATC resolves the project
and compiles the capsule automatically. The user does not request, curate, or
approve it.

A capsule includes a project revision, `as_of` time, compiler version,
dependency list or digest, budget use, omission counts, capability level, and
freshness state. A changed dependency invalidates the capsule.

## 7. Correction, deletion, and purge closure

Every derived artifact is dependency-bound.

When a record, event, permission, source, project assignment, connector
snapshot, policy generation, or target schema changes, ATC identifies and
invalidates affected:

- current records;
- graph edges and entity aliases;
- summaries and project capsules;
- working checkpoints;
- procedures and cues;
- indexes and caches;
- use and outcome statistics; and
- future issued state for supported connected clients.

Optimized repair must be checked against the existing M3-style full-rebuild
oracle before it becomes a production optimization. Purge remains fail-closed:
no stale descendant may remain reachable merely because incremental repair
failed.

For already-issued state, closure means stopping future issuance and using a
supported adapter’s invalidation, replacement, or checkpoint mechanism where
available. It does not mean retroactive erasure from external providers.

## 8. Privacy and security boundaries

Zero friction cannot mean silent over-collection.

The platform preserves:

- explicit account and client authorization;
- least-privilege scopes;
- visible source coverage and limitations;
- local-first authority;
- truthful network and data-egress declarations;
- inert treatment of imported instructions;
- secret refusal before durable direct-payload or event persistence;
- explicit event content ownership and retention classes;
- minimum necessary disclosure;
- bounded diagnostics without raw personal context;
- revocation and account disconnection;
- reversible ordinary deletion; and
- deliberate irreversible purge.

Connecting an account authorizes only the declared acquisition scope. It does
not authorize ATC to execute content, broaden client access, infer unlimited
cross-domain applicability, or retain duplicate raw history indefinitely.

## 9. User interface contract

The dashboard is optional during healthy operation. Its primary purposes are:

- inspect what ATC knows and why;
- view account and client health;
- correct, delete, restore, or purge information;
- inspect project state and dependencies;
- manage permissions and retention;
- create and restore backups; and
- troubleshoot bounded failures.

The default dashboard prioritizes actionable state rather than exposing
internal machinery. A graph visualization may exist as an optional inspector,
but the useful questions are:

- Why is this context present?
- What supports it?
- What conflicts with it?
- What depends on it?
- What changed?
- What would be invalidated by a correction or deletion?
- Does any account require attention?

## 10. Product success and acceptance

The principal product metric is successful current behavior with no
unauthorized, stale, or purged influence. Retrieval metrics remain stage-level
diagnostics.

A credible zero-friction end-to-end acceptance journey uses integrations whose
manifests declare the required acquisition and lifecycle capabilities:

1. install ATC on a clean supported machine;
2. connect at least two supported AI clients and multiple supported sources;
3. backfill history without manual classification;
4. synchronize new activity without repeat imports for continuous connectors;
5. begin work on an existing project in one lifecycle-aware client;
6. resolve the project or explicitly abstain when materially ambiguous;
7. supply current goals, constraints, decisions, failed approaches, and working
   state before generation;
8. capture a new directly stated project decision without a save-memory command;
9. move to another lifecycle-aware client and resume with updated project state;
10. apply a natural-language correction;
11. stop future issuance of the displaced record, graph relations, capsule, and
    working state, and deliver replacement/invalidation where the adapter
    supports it;
12. recover from restart and a simulated connector interruption without
    duplication; and
13. complete the journey without routine dashboard use.

Promotion gates include:

- zero unauthorized disclosure or influence;
- zero deleted or purged resurrection;
- deterministic correction and invalidation;
- truthful incomplete-coverage and capability handling;
- no mandatory manual classification or memory review;
- no routine dashboard dependency;
- bounded latency, storage, network, model, and monetary cost;
- measurable task or outcome improvement over simpler baselines; and
- no material regression on ordinary nonproject tasks.

## 11. Parallel successor-beta and stable hardening

The zero-friction program does not replace the separate post-V1 hardening track
represented by issue #30 or its successor. Real N-1 update evidence,
forward-version refusal, migration identity, compatibility and support policy,
recovery improvements, support bundles, stable APIs/schemas/exports, and release
channel hardening continue independently and may become prerequisites for a
specific zero-friction production slice.

Before `ZF-*` implementation issues are created, the existing beta tracker must
be reconciled to the active Windows/Ubuntu lean-beta scope without erasing
historical beta.1/macOS evidence. Source-readiness and exact-candidate
acceptance dependencies must be separated so candidate creation is not
circularly blocked by tests requiring the candidate to already exist.

## 12. Explicit non-goals

This contract does not require:

- a universal memory algorithm;
- one graph representation for every task;
- a graph database in the shared runtime;
- automatic execution of connected content;
- silent browser scraping to imitate unsupported provider APIs;
- hidden chain-of-thought collection;
- automatic procedure promotion from a single agent run;
- a dashboard-centered workflow;
- manual ontology or project administration;
- retroactive erasure of context already sent to an external provider;
- remote or mobile access before the local automatic loop works; or
- production promotion of learned or external systems without comparative
  evidence and lifecycle closure.

## 13. Relationship to V1

`0.1.0-beta.6` remains the immediate release target and the foundation for this
contract. Its same-device Core, provider imports, automatic policy, retrieval,
client configuration, backup, recovery, and release acceptance must finish
without adding post-V1 connector, graph, working-state, or outcome claims.

Post-V1 implementation follows the evidence-gated sequence in
[`ZERO_FRICTION_EXECUTION_PLAN.md`](ZERO_FRICTION_EXECUTION_PLAN.md).
