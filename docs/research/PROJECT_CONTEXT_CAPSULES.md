# Project Context Capsules

## Status

Proposed feature plan for a post-beta ATC Project Context capability. This document
is intentionally non-normative for the `0.1.0-beta.2` release gate until promoted by
a later ADR or implementation PR.

## Motivation

ATC already treats Core as the sole authority for current context, provenance,
policy decisions, permissions, and retrieval. That foundation is valuable, but a
developer-facing memory product needs a more obvious daily payoff than ordinary
preference recall. A coding agent does not only need user memory. It needs the
right project slice: code structure, design decisions, active constraints, recent
work, failed attempts, test signals, and authoritative project memory.

The proposed feature is **Project Context Capsules**: task-specific bundles that
combine a rebuildable project graph projection with ATC's authoritative project
memory and working-state evidence. A supported agent asks for context for a
project and task; Core returns the smallest authorized capsule needed for that
operation.

The product pitch is:

> ATC remembers what this project is, why it is built this way, what the user has
> decided, what previous agents tried, and what the current agent is allowed to
> rely on.

## Non-goals

Project Context Capsules must not become a general graph-first rewrite of ATC.
They are not a second memory authority, not a hosted analysis service, not a
cloud code index, and not a way for untrusted repository text to issue
instructions. They do not replace `bootstrap_context`; they extend it with a
project-aware retrieval and compilation path.

V1 must not claim Graphify parity, static-analysis completeness, semantic code
understanding, cross-language call-graph perfection, or safe mobile/offline
project indexing. The first implementation should be local, deterministic,
rebuildable, and bounded.

## Core idea

A project has three separate layers:

1. **Canonical project memory**: user-approved or automatically applied ATC
   records about decisions, constraints, preferences, non-goals, architecture,
   release rules, and durable project facts.
2. **Project evidence**: repository files, docs, configuration files, test
   outputs, issue/PR summaries, branch metadata, agent transcripts, and explicit
   tool outcomes. This evidence may be imported or observed, but it is not
   automatically current truth.
3. **Project graph projections**: rebuildable indexes derived from project
   evidence, such as files, symbols, imports, call edges, config references,
   document links, test ownership, and optional external graph artifacts.

The graph layer assists retrieval. It never directly establishes current memory,
changes permissions, or overrides correction/purge semantics.

## Proposed agent interface

Existing agents already call `bootstrap_context` at the start of relevant tasks.
That should remain the default. Project Context adds a more specific capability
behind the same mental model:

```text
bootstrap_context(
  task_description="Implement project context capsules for ATC",
  requested_scopes=["project:All-The-Context"],
  character_budget=12000,
  current_project="All-The-Context"
)
```

A later implementation may expose a dedicated MCP tool if benchmarks show the
specialization is worth the tool surface:

```text
get_project_context(
  project_id="...",
  task_description="...",
  changed_files=["packages/allthecontext/src/allthecontext/mcp_adapter.py"],
  focus_symbols=["bootstrap_context"],
  mode="implementation",
  character_budget=12000
)
```

The dedicated tool should be read-only, idempotent, and closed-schema. It should
return a capsule object rather than raw search results.

## Capsule contents

A Project Context Capsule should be structured enough for agents and inspectable
enough for users:

```json
{
  "project_id": "...",
  "project_name": "All-The-Context",
  "task_description": "...",
  "mode": "implementation",
  "budget_chars": 12000,
  "used_chars": 9320,
  "sections": [
    {
      "kind": "authoritative_memory",
      "items": [
        {
          "record_id": "...",
          "role": "constraint",
          "content": "Core remains the sole authority...",
          "why_included": "applies to all project graph writes",
          "provenance": "current_context"
        }
      ]
    },
    {
      "kind": "project_graph",
      "items": [
        {
          "node": "packages/allthecontext/src/allthecontext/mcp_adapter.py",
          "relation": "defines_mcp_tool",
          "target": "bootstrap_context",
          "why_included": "agent entry point for context compilation",
          "provenance": "rebuildable_projection"
        }
      ]
    },
    {
      "kind": "working_state",
      "items": [
        {
          "source": "test_run",
          "content": "full suite passed at commit ...",
          "why_included": "do not rerun unrelated validation unless touched",
          "provenance": "tool_outcome"
        }
      ]
    }
  ],
  "omitted": [
    {
      "reason": "budget",
      "count": 14
    }
  ],
  "receipt": {
    "capsule_id": "...",
    "compiled_at": "...",
    "inputs_hash": "...",
    "projection_versions": ["..."],
    "memory_versions": ["..."],
    "policy_version": "..."
  }
}
```

The important contract is not the exact JSON shape. The important contract is
that every included item has role, authority, provenance, and reason-for-use.

## Project model

Introduce a first-class `project` concept only after the planner validates that
existing `scope` and `current_project` fields are insufficient. The minimal
schema should start with:

- `projects`: stable project id, display name, root identity, created/updated
  time, deleted state, user-controlled aliases.
- `project_roots`: local path identity, VCS remote, branch/revision metadata,
  root fingerprint, ignore policy, last scan metadata.
- `project_sources`: imported docs, repo files, issue summaries, PR summaries,
  task transcripts, test outputs, and graph artifacts, each linked to source
  provenance.
- `project_projection_runs`: scanner version, input fingerprints, started/
  completed time, warnings, counts, and deterministic result hash.
- `project_graph_nodes`: file, directory, symbol, package, config key, test,
  document section, decision reference, issue, PR, task, and external artifact
  nodes.
- `project_graph_edges`: imports, calls, defines, references, documents,
  configures, tests, depends_on, supersedes, conflicts_with, supports, blocks,
  and produced_by edges.
- `project_capsule_receipts`: task query, selected evidence ids, memory record
  versions, projection run ids, budget accounting, and omission reasons.

All graph nodes and edges are derived. Every derived row must carry source
lineage or projection-run lineage. Deleting or purging the source must hide,
invalidate, or destroy dependent projections according to the existing lifecycle
rules.

## Projection providers

The architecture should support multiple providers behind a narrow interface:

```text
ProjectProjectionProvider
  scan_project(root, options) -> ProjectionRun
  list_nodes(project_id, filters) -> Nodes
  list_edges(project_id, filters) -> Edges
  explain_path(project_id, source, target, constraints) -> Paths
  retrieve_project_evidence(project_id, query, budget) -> EvidenceSet
```

Recommended rollout:

1. **Native lexical/file baseline**: no new heavy dependency; index file paths,
   docs, config, and lightweight symbols. This establishes a cheap baseline.
2. **Tree-sitter optional parser**: parse selected languages into deterministic
   symbols/imports/calls where support is mature and packaged cleanly.
3. **Graphify artifact adapter**: import `graph.json` as an external projection
   artifact. Treat it as rebuildable derived data. Do not vendor or execute the
   external package until license, dependency, sandbox, packaging, and egress
   review pass.
4. **Graph database experiment**: only after the SQLite projection fails a frozen
   benchmark. A graph database is not justified by vibes.

This mirrors the existing ATC research rule: adopt before invent, benchmark
before promote, and never outsource authority.

## Retrieval and compilation pipeline

Project Context should not simply append project graph results to memory search.
It should compile context in stages:

1. **Resolve project identity** from `current_project`, workspace path, VCS
   remote, client metadata, and user aliases. Ambiguous identity returns a safe
   disambiguation response instead of mixing projects.
2. **Authorize first** using client scopes, project permissions, record
   availability, source sensitivity, deletion state, and purge barriers.
3. **Resolve currentness** for project memory and projection runs. A stale
   projection may be used only with an explicit stale warning and must not carry
   force-bearing constraints.
4. **Retrieve candidate evidence** from canonical memory, project source search,
   graph neighborhood expansion, recent working-state receipts, and relevant
   outcomes.
5. **Close dependencies** by pulling required supporting decisions, conflicts,
   preconditions, tests, and related files when a selected item would be
   misleading alone.
6. **Select minimally sufficient context** under budget using deterministic
   marginal utility, mandatory constraints first, conflict exclusion, and
   redundancy suppression.
7. **Emit a receipt** recording selected memory versions, projection run ids,
   source ids, task hash, budget use, and omitted categories.

The capsule compiler should make authority visible. A user decision is not the
same as a README statement; a test failure is not the same as an agent opinion;
a graph edge is not the same as a project rule.

## Safety and privacy rules

Project Context introduces code and document ingestion, so the following rules
are mandatory:

- Repository text is untrusted data. It cannot instruct ATC, change memory
  policy, expand permissions, or override user corrections.
- Secrets and secret-like payloads must be rejected or redacted before durable
  project projection storage where feasible. Secret scanning must happen before
  high-volume indexing becomes default.
- Generated summaries, graph communities, LLM labels, and agent reflections are
  derived projections or tentative observations, never current truth.
- Purge must affect derived nodes, edges, summaries, receipts, and imported
  graph artifacts linked to the purged source.
- Capsule receipts may store identifiers and accounting, but should not preserve
  unnecessary raw code snippets beyond the compiled disclosure boundary.
- External tools must be default-deny egress and loopback/local-only until a
  security review explicitly says otherwise.

## Benchmark and promotion gates

Do not promote this feature because it sounds useful. Promote it by beating
simpler baselines. The first Memory Lab extension should compare:

- no project context;
- long-context repo dump within the same token budget;
- file-search baseline;
- native lexical project index;
- native project graph projection;
- imported Graphify artifact adapter, if available;
- full Project Context Capsule compiler.

Primary endpoint:

- **Project CAOS**: correct current authorized project outcome within budget.

Secondary metrics:

- task success;
- required-evidence recall;
- forbidden/stale/purged disclosure count;
- wrong-project contamination count;
- stale-decision influence count;
- unnecessary file disclosure;
- latency p50/p95;
- storage growth per scanned file;
- deterministic receipt stability;
- rebuild parity after source deletion/restoration/purge.

Initial benchmark tasks should include:

1. answer architecture question from docs plus current memory;
2. modify an MCP tool while preserving Core authority boundaries;
3. find the file responsible for a failing test;
4. respect a user correction that contradicts an old ADR;
5. avoid leaking unrelated project memory;
6. handle a stale graph projection after a branch switch;
7. use a prior failed-agent outcome to avoid repeating a bad patch;
8. detect when the project is ambiguous and abstain.

Promotion requires beating file-search and long-context baselines under the same
model, data, and budget. If the graph layer does not beat the simpler native
index, it remains an optional experiment.

## Implementation slices

### Slice 0: design-only PR

- Land this document.
- Do not change runtime behavior.
- Decide whether this becomes an ADR, Memory Lab experiment, or implementation
  milestone after review.

### Slice 1: project identity and receipts

- Add project identity models without graph storage.
- Allow local clients to declare workspace/project metadata.
- Add capsule receipt schema for existing `bootstrap_context` responses.
- Validate no behavior changes for ordinary personal-context bootstrap.

### Slice 2: native project source index

- Index selected repo files, docs, config files, and paths as project evidence.
- Respect ignore files and hard size limits.
- Store scan warnings and deterministic input fingerprints.
- Provide project-scoped search as a read-only internal service.

### Slice 3: minimal capsule compiler

- Extend `bootstrap_context(current_project=...)` to combine current memory with
  project evidence under a budget.
- Add authority labels, reason-for-use, and omission accounting.
- Add deterministic tests for project disambiguation, wrong-project isolation,
  stale projection warnings, and budget behavior.

### Slice 4: lightweight graph projection

- Add file/symbol/import/config nodes and edges.
- Keep storage SQLite-native unless benchmarks prove otherwise.
- Add dependency closure for selected files and symbols.

### Slice 5: Graphify artifact adapter

- Accept an explicit user-supplied or locally generated `graph.json` artifact.
- Import nodes and edges as external derived projections.
- Compare against the native projection in Memory Lab.
- Do not vendor or auto-run Graphify until packaging and security review pass.

### Slice 6: working-state and outcome memory

- Store agent task receipts, changed files, test outcomes, failed assumptions,
  and repair evidence.
- Retrieve outcome memory only when the same project/task/file pattern applies.
- Prevent agent self-judgment from becoming user truth.

### Slice 7: dashboard and inspection

- Add a Project page showing roots, scans, warnings, projection runs, graph
  statistics, recent capsules, and delete/restore/purge effects.
- Avoid turning the dashboard into a mandatory review queue.

## Open questions

- Should `project` be a first-class database entity immediately, or should v1
  map project identity through scopes until the schema pressure is proven?
- What is the minimum language support needed for a useful first graph: Python,
  TypeScript/JavaScript, Markdown, TOML, YAML, JSON?
- Should ATC ever execute external project analyzers automatically, or should it
  import their artifacts only?
- How much raw code may a capsule receipt retain without expanding the privacy
  surface too much?
- Should project context be a separate MCP tool or remain an enhanced
  `bootstrap_context` path until benchmark evidence supports a new tool?
- What is the correction boundary for a stale project decision that already
  influenced an open agent task?

## Recommended decision

Build Project Context Capsules, but keep the first implementation boring:
SQLite, FTS5, deterministic file/config/symbol projections, strict source
lineage, and receipt-backed compilation. Treat Graphify as an adapter and
benchmark competitor first. Only add a graph database, learned summarizer, or
external runtime after a frozen benchmark shows that the simpler projection is
insufficient.

The feature should become ATC's developer-facing wedge: not just "memory for AI
chats," but **project memory that prevents agents from losing the plot across
sessions, clients, branches, corrections, and failed attempts**.
