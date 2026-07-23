# ATC Memory Horizon Report

## Fresh-horizon review of the Memory Reliability Architecture

| Field | Value |
|---|---|
| Report date | July 23, 2026 |
| Evidence cutoff | July 23, 2026 |
| Proposal reviewed | [ATC Memory Reliability Architecture](ATC_MEMORY_RELIABILITY_ARCHITECTURE.md), version 0.1 |
| Repository baseline | `e5cb50a518aff571af46238682d8f7a082ca19f0` |
| Scope | Primary papers, official repositories, and first-party production documentation |
| Exclusions | No third-party code was downloaded or executed; vendor benchmarks were not reproduced |
| Decision | Conditionally retain the two-plane direction, but narrow ATC's claim, reorder the lab, demote graph-first work, and make harmful-memory tests promotion blockers |

## Executive judgment

The current proposal is directionally right about authority, correction, purge,
and consequence. It is too willing, however, to treat the rest of the memory
field as a menu of components that can be assembled into a comprehensive
Memory Plane.

Evidence released through July 23, 2026 points to a less comfortable
conclusion:

1. **There is no generally best memory representation.** In a standardized
   comparison of 15 methods, long-context baselines remained highly
   competitive, memory helped most when context was actually insufficient, and
   no memory form won across knowledge and execution tasks
   ([EvoMemBench](https://arxiv.org/abs/2605.18421)).
2. **Complex construction is not the center of value.** Controlled experiments
   found that implementation settings can dominate graph-versus-flat results,
   and that inappropriate graph construction or retrieval can degrade answers
   ([Does Memory Need Graphs?](https://aclanthology.org/2026.acl-long.1232/)).
   A separate ACL 2026 result reports that lightweight construction plus better
   use of retrieved fragments beat complex systems at a small fraction of
   their token and latency cost
   ([Chain-of-Memory](https://aclanthology.org/2026.acl-long.534/)).
3. **The best new baselines are not all databases.** A coding agent searching
   trajectory files reached 72.5% on LongMemEval-V2 versus 48.5% for the
   strongest RAG baseline, although at high latency
   ([LongMemEval-V2](https://arxiv.org/abs/2605.12493)). Mastra's shipped
   Observational Memory reports strong LongMemEval results using a stable,
   cacheable event log rather than per-query retrieval, but this remains a
   vendor-reported result
   ([technical report](https://mastra.ai/research/observational-memory),
   [official repository](https://github.com/mastra-ai/mastra)).
4. **Remembering can make an agent worse.** Similar experiences induce
   experience-following, propagating old errors and replaying superficially
   similar but misaligned solutions
   ([Xiong et al.](https://aclanthology.org/2026.acl-long.27/)).
   PersistBench reports median failure rates of 53% for cross-domain leakage
   and 97% for memory-induced sycophancy across 18 models
   ([PersistBench](https://arxiv.org/abs/2602.01146)). MemSyco-Bench likewise
   finds that existing memory systems often increase sycophancy
   ([MemSyco-Bench](https://arxiv.org/abs/2607.01071)).
5. **Recall is not action.** On Mem2ActBench, the best passive retriever reached
   about 30.7 F1 while oracle retrieval reached about 53.8 F1; end-to-end exact
   tool-call match remained only 14.25% to 18.25% in reported stress settings
   even when tool selection exceeded 93% in easier settings
   ([Mem2ActBench](https://aclanthology.org/2026.acl-long.370/)).

These findings do not invalidate ATC. They change what ATC should claim to own.

> ATC should not try to become the universal memory algorithm. It should become
> the user-owned reliability control plane that can govern several deliberately
> simple memory representations, prove when they help, prevent them from
> acquiring false authority, and remove their future influence when corrected
> or purged.

The proposal should be accepted only with the following amendments:

- put strong long-context, stable observation-log, raw-event, and file-search
  baselines ahead of heavyweight framework adapters;
- demote the general bitemporal graph from an expected Memory Plane component
  to a typed, derived projection that must earn each relation family;
- split retrieval policy into authorization, epistemic/applicability gating,
  and relevance, because authorized memory can still be irrelevant, private,
  sycophantic, or false evidence;
- prohibit automatic promotion of successful-looking trajectories into
  procedures without external outcome evidence, recurrence, and repair tests;
- make action grounding, obsolete-memory penalties, adversarial extraction,
  cross-domain leakage, sycophancy, and derived-tier purge residue mandatory
  gates rather than later governance metrics; and
- treat learned prompts, personas, neural/KV memory, and private parameter
  updates as experimental targets, not the product path.

Confidence in this overall judgment is **high**. Confidence in any specific
2026 system's superiority is **low to medium** until reproduced under one ATC
harness.

## 1. Method and evidence discipline

This report searched current primary sources and official repositories across:

- long-context and prompt-cache approaches;
- KV, neural, and external memory;
- experience and procedural learning;
- memory operating systems and managed memory layers;
- flat, hierarchical, temporal, causal, and multi-graph representations;
- personal-memory privacy, security, correction, deletion, and purge;
- action-grounded, environment-grounded, and evolving-memory evaluation; and
- production memory architecture and lifecycle documentation.

Evidence is classified as follows:

| Level | Meaning |
|---|---|
| A | Peer-reviewed or accepted conference paper with a public artifact, or a controlled comparative study with reproducible methods |
| B | Primary preprint, official repository, or first-party production documentation with inspectable methods or APIs |
| C | Vendor or author benchmark claim that has not been independently reproduced, position paper, narrow workshop result, or architectural claim without comparative evidence |

“Proven” in this report means demonstrated within the paper or a documented
production interface. It does **not** mean proven safe, general, or suitable for
ATC. Benchmark scores remain author evidence until the ATC Memory Lab
reproduces them.

No secondary summaries are used as evidence for recommendations. No
third-party code was downloaded or executed.

## 2. Findings most likely to overturn the current plan

### 2.1 Replace the framework tournament with a baseline ladder

**Finding:** The current M0 plan prioritizes Mem0, Graphiti, and Hindsight
adapters. That starts too high on the complexity ladder.

New evidence:

- EvoMemBench compares 15 methods under a standardized protocol and finds
  strong long-context baselines competitive across the board. Memory can hurt
  easy tasks, and forming reusable knowledge is a larger bottleneck than merely
  storing more information
  ([paper](https://arxiv.org/abs/2605.18421),
  [official repository](https://github.com/DSAIL-Memory/EvoMemBench)).
- LongMemEval-V2's strongest method stores trajectories as files and lets a
  coding agent gather evidence. It reaches 72.5% average accuracy, above the
  strongest RAG baseline at 48.5% and a vanilla coding-agent baseline at 69.3%,
  while exposing a serious latency cost
  ([paper](https://arxiv.org/abs/2605.12493),
  [official repository](https://github.com/xiaowu0162/LongMemEval-V2)).
- Mastra's Observational Memory uses dated event observations and periodic
  reflection in a stable prompt prefix. It reports 84.23% on LongMemEval with
  GPT-4o and 94.87% with GPT-5-mini, but the developer also documents small
  benchmark categories, judge sensitivity, and remaining multi-session limits
  ([first-party report](https://mastra.ai/research/observational-memory)).
- Chain-of-Memory reports that lightweight construction with more deliberate
  organization of retrieved evidence improves accuracy by 7.5% to 10.4% while
  using about 2.7% of the tokens and 6.0% of the latency of complex memory
  architectures
  ([ACL paper](https://aclanthology.org/2026.acl-long.534/),
  [official repository](https://github.com/Xiucheng-Xu/CoM)).

**Change:** M0 should implement a ladder in this order:

1. no durable memory;
2. bounded full history / long context;
3. current ATC lexical retrieval;
4. raw event chunks plus BM25/dense hybrid;
5. stable dated observation log with source pointers;
6. file-backed trajectory corpus queried by a coding/search agent;
7. lightweight event-centric consolidation;
8. only then Mem0, Hindsight, Graphiti, and learned managers.

**Why this matters:** If the simpler rungs match or beat the frameworks, ATC
avoids importing their authority models, hosted dependencies, extraction
errors, and purge surface.

**Confidence:** High for the priority change; medium for the specific order
until reproduced.

### 2.2 Do not make a general temporal graph the default derived memory

**Finding:** The proposal assumes a bitemporal derived graph is a likely
Memory Plane component. Current evidence supports typed structure where the
task demands it, not a general graph as the default.

New evidence:

- A controlled ACL 2026 study decomposes graph and flat systems into common
  stages and finds that foundational settings substantially affect results.
  Graph memory can help under some configurations, but poor construction or
  retrieval degrades performance. On its larger setting, graph retrieval took
  574 ms per query versus 240 ms for flat memory, and graph extraction took
  about 2.1 seconds per session versus 0.5 seconds
  ([Does Memory Need Graphs?](https://aclanthology.org/2026.acl-long.1232/),
  [official repository](https://github.com/AvatarMemory/UnifiedMem)).
- The same study documents graph failure modes: fragmented atomic entities lose
  narrative bindings, and additional temporal metadata can distract a smaller
  reader even when retrieval succeeds.
- StructMem argues that temporally grounded relational events can retain useful
  bindings without explicit entity resolution and symbolic traversal
  ([ACL paper](https://aclanthology.org/2026.acl-short.12/),
  [official implementation](https://github.com/zjunlp/LightMem)).
- AMA-Bench reports the opposite boundary condition: agent trajectories often
  require causal and objective information that similarity retrieval loses.
  Its causal graph plus tool-augmented retrieval reaches 57.22%, 11.16 points
  above the strongest compared memory baseline
  ([paper](https://arxiv.org/abs/2602.22769),
  [official repository](https://github.com/AMA-Bench/AMA-Bench)).

**Change:** Replace “build a bitemporal derived graph” with:

- keep evidence episodes and current claims as the durable substrate;
- add event bindings and explicit version/time fields first;
- materialize only task-justified relation families such as
  `supersedes`, `supports`, `conflicts`, `caused`, `attempted_before`,
  `failed_because`, and `requires`;
- require each relation family to beat a narrative/event baseline on its target
  task and to pass correction fan-out and purge tests; and
- never require a graph database for the shared runtime.

Graphiti, GAM, MAGMA, HippoRAG, and similar systems remain valuable research
competitors. They should not establish the default internal ontology.

**Confidence:** High that graph-first should be demoted; medium that
event-centric structure will be the winning replacement.

### 2.3 Treat memory use as a hazardous intervention, not a neutral read

**Finding:** The proposal correctly puts authorization before relevance, but
authorization is not enough. A memory may be authorized and accurately
retrieved yet still be inappropriate evidence or harmful guidance.

New evidence:

- PersistBench identifies cross-domain leakage and memory-induced sycophancy,
  with median failure rates of 53% and 97% respectively across 18 evaluated
  models
  ([paper](https://arxiv.org/abs/2602.01146)).
- MemSyco-Bench tests whether agents reject memory as factual evidence, respect
  scope, resolve memory against objective evidence, track updates, and use
  valid personalization. Its experiments find that existing memory systems
  often increase sycophancy
  ([paper](https://arxiv.org/abs/2607.01071),
  [official repository](https://github.com/XMUDeepLIT/MemSyco-Bench)).
- LoCoMo-Plus shows that useful personal constraints may have a semantic
  disconnect between the original cue and the later trigger, while ordinary
  string metrics and explicit task hints miss the problem
  ([ACL paper](https://aclanthology.org/2026.acl-long.1150/),
  [official repository](https://github.com/xjtuleeyf/Locomo-Plus)).
- MEXTRA demonstrates black-box extraction of private information from agent
  memory, making persistent memory a direct attack surface
  ([ACL 2025 paper](https://aclanthology.org/2025.acl-long.1227/)).
- A July 2026 preprint reports that forged reasoning memories can achieve up to
  100% attack success in its tested baseline conditions and bypass keyword and
  consensus defenses. The result is early and narrow, but it reinforces the
  danger of retaining agent-authored rationales as reusable memory
  ([FARMA/SENTINEL](https://arxiv.org/abs/2607.05029)).

**Change:** Every retrieval strategy should receive only records that pass
three gates:

1. **authorization** — may this target receive the content?
2. **epistemic and applicability policy** — what role may the memory play here:
   evidence, user constraint, hypothesis, procedure, warning, or not applicable?
3. **relevance and set compilation** — which permitted records form the
   smallest sufficient set?

A preference must not become evidence about the world. An opinion must not
silently become a tool parameter. A past successful action must not override a
current explicit instruction. An authorized memory from one life domain must
not be injected merely because it is semantically similar.

Add cross-domain leakage, sycophancy, memory-as-false-evidence, and adversarial
extraction to the zero-tolerance promotion suite.

**Confidence:** High.

### 2.4 Delay procedural learning until outcome quality and repair are real

**Finding:** The M4 procedural path is too optimistic about distilling tactics
from successes and failures. Similarity-driven experience reuse can propagate
mistakes, and “successful” executions may be bad demonstrations.

New evidence:

- Xiong et al. find an experience-following property: similar retrieved inputs
  lead to similar outputs. This creates error propagation and misaligned
  experience replay. They show that later task evaluations can act as free
  quality labels for stored experiences
  ([ACL 2026 paper](https://aclanthology.org/2026.acl-long.27/),
  [official repository](https://github.com/yuplin2333/agent_memory_manage)).
- EvoMemBench finds procedural memory useful for execution-oriented tasks only
  when stored experience matches reusable task structure. Memory can hurt easy
  tasks, and no one representation generalizes across settings
  ([paper](https://arxiv.org/abs/2605.18421)).
- LongMemEval-V2 includes balanced successful and failed trajectories, and many
  questions require learning from failure, not copying a successful trace
  ([paper](https://arxiv.org/abs/2605.12493)).
- Mem2ActBench shows that retrieval misses are only the first bottleneck. In
  stronger systems, failures shift toward retrieved-but-unused evidence,
  hallucinated defaults, corrupted structured values, and wrong parameter
  grounding
  ([ACL paper](https://aclanthology.org/2026.acl-long.370/)).
- PersonaAgent and Compiled Memory show that experience can be converted into
  user-specific or task-specific instructions with measurable gains, but both
  also create a difficult correction and authority surface
  ([PersonaAgent](https://aclanthology.org/2026.findings-acl.1315/),
  [Compiled Memory](https://arxiv.org/abs/2603.15666)).

**Change:** M4 should begin as an outcome-labeled experience ledger, not a
procedure generator. Promotion to `ProcedureRecord` should require:

- an observable environment outcome, not agent self-rating;
- source and result envelopes that can be replayed or inspected;
- at least one counterexample or explicit applicability boundary;
- repeated benefit or a high-confidence externally verified outcome;
- a repair test after a deliberately wrong reward or correction;
- no storage of hidden reasoning; and
- a dependency manifest that supports correction and purge.

Learned prompt or persona updates remain shadow artifacts. They may propose
task-local instructions but may not rewrite canonical user truth or create hard
behavioral force.

**Confidence:** High for the delay and gates; medium for whether procedures
will eventually justify their complexity.

### 2.5 Make derived-tier deletion residue a first-class product test now

**Finding:** Outcome closure is presented as later research. New evidence makes
part of it a near-term product requirement.

New evidence:

- Deployment-Time Memorization introduces a Forgetting Residue Score and
  reports that deleting raw records alone leaves derived summaries recoverable
  in roughly 20% of tested instances; full-pipeline purge or tombstone
  redaction drives worst-tier residue to zero in that experiment
  ([paper](https://arxiv.org/abs/2606.10062)). It also reports that key-fact
  summarization reduces canary extraction by 76% on Gemma 3 12B and 64% on
  GPT-4o-mini while preserving nearly all measured personalization recall,
  illustrating that privacy, utility, and deletion fidelity are separate axes.
- AWS AgentCore now exposes actor/session/strategy namespaces, permanent
  memory-record deletion, and real-time created/updated/deleted record streams
  for downstream lifecycle processing
  ([organization](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-organization.html),
  [record deletion](https://docs.aws.amazon.com/cli/latest/reference/bedrock-agentcore/delete-memory-record.html),
  [record streaming](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-record-streaming.html)).
  This is production evidence that memory lifecycle events are becoming table
  stakes, but the documentation does not establish full derived-artifact purge
  closure.
- Memora introduces Forgetting-Aware Memory Accuracy, which penalizes use of
  obsolete or invalid memory. Four LLMs and six memory agents frequently reused
  invalid memories, with memory agents providing only marginal improvements
  ([paper](https://arxiv.org/abs/2604.20006)).

**Change:** Split outcome closure:

- **Derived-state closure** becomes a foundation requirement for every summary,
  index, relation, embedding, checkpoint, cache, and local learned artifact.
- **Behavioral consequence closure** remains research because it depends on a
  conforming host and cannot reverse completed external effects.

Add canary extraction, raw-only deletion, tombstone redaction, rebuild, backup,
cache, disconnected-client, and orphaned-artifact tests before any new
consolidator is promoted.

**Confidence:** High.

## 3. What the horizon says about each technical direction

### 3.1 Long context, compaction, and stable observation logs

**Proven mechanism:** Long context is a mandatory baseline, not a straw man.
EvoMemBench shows it remains highly competitive. LongMemEval-V2 shows that
searching raw trajectory files with a general coding agent can beat specialized
RAG, though with latency costs. Production agent platforms also increasingly
pair raw session history with summarization or context editing rather than a
single universal memory store.

**Promising:** Stable, dated observation logs can preserve prompt caching and
avoid retrieval variance. Mastra's implementation is shipped and open source,
but its benchmark result is vendor-reported and LongMemEval is no longer
sufficient as the deciding benchmark.

**Risk:** Compaction can hide source evidence, preserve deleted content in
summaries, and turn model guesses into apparent history. A stable prefix is
also a stable attack surface if poisoned.

**ATC decision:** Adopt the pattern only as a source-linked working projection.
Benchmark raw bounded history, sliding window, bulk summary, incremental event
log, and event log plus reflection under identical models and cache accounting.

### 3.2 KV cache and neural/test-time memory

Current work spans persistent quantized KV caches, learned eviction, model-driven
cache compression, and test-time neural memory:

- persistent Q4 KV cache restoration reports large time-to-first-token gains on
  three local architectures
  ([Agent Memory Below the Prompt](https://arxiv.org/abs/2603.04428));
- learned global KV eviction argues selective forgetting can match or exceed a
  full cache by reducing attention dilution
  ([Make Each Token Count](https://arxiv.org/abs/2605.09649));
- SideQuest reports up to 65% peak-token reduction with minimal accuracy loss
  in its agentic evaluations
  ([paper](https://arxiv.org/abs/2602.22603)); and
- Titans proposes test-time neural memorization inside the sequence model
  ([paper](https://arxiv.org/abs/2501.00663)).

These are primarily inference and architecture mechanisms, not governed
personal memory. They lack ATC-compatible record identity, provenance,
correction semantics, and deletion proofs.

**ATC decision:** Track, do not adopt. A provider may use them below ATC, but
ATC must treat provider state as opaque and non-authoritative. Do not put
private user learning into neural or KV state that ATC cannot enumerate and
purge.

**Confidence:** High.

### 3.3 Memory operating systems and managed layers

MemOS, Letta, Hindsight, Mem0, LangMem, AWS AgentCore Memory, Mastra, and other
“memory layer” systems increasingly converge on:

- raw event/session state;
- extracted facts, preferences, episodes, or summaries;
- namespaces or blocks;
- asynchronous consolidation;
- hybrid or semantic retrieval; and
- lifecycle APIs.

AWS now documents configurable semantic, summarization, user-preference, and
episodic strategies, actor/session namespace isolation, metadata filters, and
record lifecycle streams
([strategies](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-strategies.html),
[metadata](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/long-term-memory-metadata.html)).
Hindsight demonstrates explicit separation of world facts, experiences,
observations, and opinions
([ACL demo paper](https://aclanthology.org/2026.acl-demo.27/),
[official repository](https://github.com/vectorize-io/hindsight)).
Mem0's April 2026 algorithm reports strong vendor benchmark gains from add-only
extraction, entity linking, hybrid retrieval, and temporal reasoning
([official repository](https://github.com/mem0ai/mem0)).

**Proven:** The separation of raw events, working/session state, and derived
long-term records is now a production pattern. Namespaces, record identity, and
lifecycle events are table stakes.

**Promising:** Portable typed memory operations such as Text2Mem may help build
an adapter contract, but the evidence is still early
([ACL Findings volume entry](https://aclanthology.org/volumes/2026.findings-acl/)).

**Hype:** Calling a system an OS does not establish authority, correct
extraction, action benefit, or purge. Broad claims of unified memory resources,
human-like cognition, or continuous self-evolution are not evidence.

**ATC decision:** Do not compete on the generic “memory layer” label. Compete on
authority, evidence, correction, minimum disclosure, purge closure, and
consequence verification. Reject Mem0's choice to treat agent-generated facts
with equal weight as user facts; agent outputs enter ATC as lower-authority
observations.

### 3.4 Temporal, hierarchical, and causal structure

2026 produced many graph and hierarchy proposals: GAM, MAGMA, H-Mem/H-MEM,
StructMem, Memora, and AMA-Agent. Their common useful insight is not “graphs
win”; it is that memory units and retrieval paths should reflect time, event
bindings, and sometimes causality.

- GAM separates an event-progression graph from a consolidated topic network
  ([ACL paper](https://aclanthology.org/2026.acl-long.1600/)).
- MAGMA separates semantic, temporal, causal, and entity graphs rather than
  mixing all relationships
  ([ACL paper](https://aclanthology.org/2026.acl-long.1709/)).
- StructMem uses event-centric hierarchical structure instead of explicit
  entity graphs
  ([ACL paper](https://aclanthology.org/2026.acl-short.12/)).
- AMA-Agent shows task-specific value from a causality graph on agent
  trajectories
  ([paper](https://arxiv.org/abs/2602.22769)).

**ATC decision:** Adopt explicit time and version semantics immediately. Adopt
typed event and causal relations only where a benchmark requires them. Reject a
single general-purpose graph ontology and mandatory graph service.

### 3.5 Learned memory management and experience

AgeMem trains a policy to manage short- and long-term memory through tool
actions; Memory-R1 trains ADD, UPDATE, DELETE, and NOOP management plus answer
selection; PersonaAgent updates a user-specific persona prompt; Compiled Memory
rewrites task instructions from verified failures and successes:

- [AgeMem](https://aclanthology.org/2026.acl-long.981/)
- [Memory-R1](https://aclanthology.org/2026.acl-long.583/)
- [PersonaAgent](https://aclanthology.org/2026.findings-acl.1315/)
- [Compiled Memory](https://arxiv.org/abs/2603.15666)

**Promising:** Learned managers can reduce heuristic brittleness, and task-level
instruction compilation may transfer across models.

**Unproven:** Reported gains are task- and benchmark-bound. These systems do not
demonstrate user-truth authority, private-data purge, correction propagation,
or safety under poisoned feedback.

**ATC decision:** Benchmark in shadow after deterministic baselines. A learned
manager may propose operations. It may not directly write canonical claims,
increase permissions, assign directive force, or authoritatively declare a
procedure successful.

### 3.6 Privacy-preserving personal memory

Privacy is not solved by local storage alone. Risk exists at capture, derived
storage, retrieval, prompt assembly, tool arguments, model-provider egress, and
deletion.

- MEXTRA demonstrates memory extraction attacks
  ([ACL paper](https://aclanthology.org/2025.acl-long.1227/)).
- Deployment-Time Memorization measures privacy, utility, and deletion residue
  as separate dimensions
  ([paper](https://arxiv.org/abs/2606.10062)).
- Privacy-R1 learns local/remote routing for privacy-conscious delegation, but
  deliberately sends some task-critical PII to remote models, so it is a
  routing result rather than a no-disclosure guarantee
  ([ACL paper](https://aclanthology.org/2026.acl-long.2130/)).
- A 2026 PrivateNLP position paper argues that runtime tool-call arguments are a
  key privacy boundary and finds that 36.9% of 2,344 studied tool
  specifications expose at least one generic free-text channel that could
  enable oversharing
  ([workshop volume](https://aclanthology.org/volumes/2026.privatenlp-main/)).

**ATC decision:** Keep local Core ownership, but add cumulative disclosure and
tool-argument disclosure to the consequence plane. “Authorized to retrieve”
must not imply “authorized to send to this tool or provider.” Provider and tool
egress are separate decisions over the exact compiled payload.

## 4. Evidence classification

### 4.1 Mechanisms with the strongest current support

| Mechanism | Evidence | Boundary | Confidence |
|---|---|---|---|
| Strong long-context baseline | Competitive across standardized EvoMemBench settings | Cost and scale still matter; not durable governance | High |
| Raw event/session state separated from derived records | Convergent published and production architecture in ATC, Hindsight, AWS, Mastra | Does not make extraction correct | High |
| Hybrid retrieval as a baseline | Repeated strong results; Mem2Act best passive retriever used hybrid retrieval | Large oracle gap remains | High |
| Explicit time/version fields | Temporal update failures recur across LongMemEval, Memora, MemSyco, graph studies | Temporal metadata can distract if over-injected | High |
| Outcome-labeled experience quality | Xiong et al. show later task evaluations can label memory utility | Labels may be delayed or confounded | Medium-high |
| Actor/session/namespace isolation and lifecycle events | Shipped AWS interfaces | Not proof of content-level authority or purge closure | High |
| Source-linked derived-state invalidation | ATC design plus deletion-residue evidence | Full remote/provider erasure remains out of scope | High |
| Action- and environment-grounded evaluation | Mem2ActBench, MemoryArena, AMA-Bench, LongMemEval-V2, EMemBench | Several datasets are synthetic or young | High |

### 4.2 Promising research to benchmark, not adopt

| Direction | Why promising | Why not product-ready | Confidence |
|---|---|---|---|
| Stable observational memory | Cacheable context, event preservation, shipped implementation | Vendor-reported score; reflection and purge risks | Medium |
| File/coding-agent retrieval | Best LME-V2 accuracy among reported methods | High latency and agent/tool cost | Medium-high |
| Event-centric hierarchy | Preserves narrative bindings without full graph | Mostly dialogue QA evidence | Medium |
| Typed causal graph | Wins on AMA-Bench's trajectory questions | Domain-specific and still only 57.22% | Medium |
| Learned memory operations | Adaptive ADD/UPDATE/DELETE/NOOP policies | Weak authority and correction story | Medium-low |
| Compiled task instructions | Converts outcomes into actionable guidance | Prompt rewriting is difficult to scope and purge | Medium-low |
| Optical context retrieval | High-density trajectory representation with verbatim transcription | Unusual modality and narrow evaluation | Low-medium |
| Learned KV eviction / persistent KV | Potential cost and latency gains | Below-record state lacks governance semantics | Medium for inference, low for ATC memory |

### 4.3 Hype indicators

Treat a claim as hype until reproduced when it has one or more of these signs:

- “memory OS,” “human-like,” “cognitive,” or “AGI” is the primary novelty;
- a single LoCoMo or LongMemEval score is the main evidence;
- the answer model, judge, prompts, context budget, or extraction cost differ
  from baselines;
- construction cost and query latency are omitted;
- agent-generated facts or reflections are treated as truth;
- deletion means only removing a top-level vector or row;
- a graph is justified by analogy to human associative memory rather than an
  ablation;
- “self-evolution” uses self-evaluation without an observable environment
  result; or
- a private learned artifact has no dependency manifest or rebuild procedure.

Examples worth tracking but not using for architectural decisions include broad
Memory-as-Asset claims
([paper](https://arxiv.org/abs/2603.14212)), biologically framed selective
forgetting with unusually strong security claims
([FSFM](https://arxiv.org/abs/2604.20300)), and predictive-world-model memory
evaluated on a small LoCoMo slice with uncontrolled cross-paper comparisons
([Nous](https://arxiv.org/abs/2606.22030)).

### 4.4 Dead ends and explicit kill rules

Abandon or block:

1. **Leaderboard optimization on LoCoMo or LongMemEval alone.** These remain
   regression suites, not product gates.
2. **A mandatory general knowledge graph.** Require a typed relation ablation
   and a simpler narrative/event competitor.
3. **Agent self-write to canonical truth.** Agent content remains evidence or
   proposal, never equal-authority fact.
4. **Similarity-only experience replay.** Require task state, outcome quality,
   and applicability boundaries.
5. **Raw-only deletion.** Derived summaries, indexes, caches, checkpoints, and
   learned artifacts must be invalidated or rebuilt.
6. **Storing hidden reasoning as reusable memory.** Store observable inputs,
   actions, tool results, outcomes, and concise tactics only.
7. **Private parameter memory or opaque learned state.** Do not use it until
   attribution and erasure are credible.
8. **Retrieval-only success claims.** The reader must use the memory correctly,
   and the resulting response, tool call, or action must improve.
9. **Memory as universal personalization evidence.** Preferences cannot settle
   objective questions or leak across unrelated domains.
10. **A heavyweight adapter before a strong simple baseline.** Complexity must
    earn its place at fixed quality, cost, latency, disclosure, and purge.

## 5. What ATC uniquely owns

The horizon narrows ATC's defensible differentiation.

### 5.1 Durable ownership

ATC already has or explicitly targets a combination not established by the
surveyed systems:

- one user-owned local authority rather than agent-owned or vendor-owned truth;
- preserved evidence distinct from observations, current claims, procedures,
  and working state;
- automatic policy that is deterministic and replayable;
- authorization and time filtering before relevance;
- set-level support, conflict, duplicate, mandatory-preference, and budget
  constraints;
- reversible correction and deletion distinct from purge;
- an inspectable dependency path from source memory to derived state;
- event-bound consequence contracts with honest host capability levels; and
- the research hypotheses of consequence closure, dual invalidation, and
  outcome closure.

Individual pieces exist elsewhere. The defensible unit is the
authority-preserving, correction-closed composition.

### 5.2 What ATC does not uniquely own

Do not claim novelty for:

- episodic/semantic/procedural/working-memory taxonomies;
- vector, BM25, hybrid, graph, or temporal retrieval;
- memory blocks, namespaces, summaries, reflection, or consolidation;
- user profiles or preference extraction;
- background memory agents;
- memory lifecycle APIs;
- experience distillation;
- prompt rewriting; or
- long-context management.

These are implementation choices and competitors.

### 5.3 Revised product claim

Use:

> ATC is a user-owned memory reliability control plane. It preserves evidence,
> governs what may become current, selects the minimum authorized and
> applicable context for a task, measures whether that context improved the
> outcome, and can invalidate or purge its future derived influence.

Avoid claiming that ATC is a cognitive architecture, memory OS, universal
agent-memory algorithm, or complete model of human memory.

## 6. Change log against the current proposal

| Current proposal | Horizon decision | Required change | Confidence |
|---|---|---|---|
| Adopt the document as the umbrella direction | Conditional accept | Retain the two planes, but recast the Memory Plane as a pluggable reliability control plane | High |
| Mem0, Graphiti, and Hindsight adapters lead M0 | Change | Put long context, raw hybrid retrieval, stable observations, and file-search ahead of framework adapters | High |
| Build a bitemporal derived graph | Narrow | Start with versioned events and typed relation projections; no general graph default | High |
| Extraction tournament over external systems | Expand downward | Include no-extraction raw chunks, event notes, and deterministic extraction baselines | High |
| Hybrid retrieval council | Simplify first | Begin with a baseline ladder and add strategies only when stage diagnostics justify them | High |
| Policy before every retrieval strategy | Strengthen | Split authorization from epistemic/applicability gating before relevance | High |
| Background consolidation | Retain with gate | Every artifact needs source pointers, invalidation, purge, and fidelity tests | High |
| Learn procedures from success and failure | Delay | Begin with outcome-labeled experiences; require recurrence, counterexamples, repair, and external outcomes before procedure promotion | High |
| Outcome closure as research phase C3 | Split | Derived-state closure moves to foundation; host behavioral closure remains C3 research | High |
| LongMemEval, MemoryAgentBench, MemoryArena portfolio | Replace center of gravity | Add LME-V2, Mem2ActBench, AMA-Bench, Memora/FAMA, PersistBench, MemSyco, and EMemBench; keep old QA sets as regressions | High |
| Consequence Plane as differentiated second plane | Retain | Add provider/tool argument disclosure and memory-role validation | High |
| Learned components in shadow | Retain and tighten | No learned canonical writes, permissions, force, or untracked prompt/persona mutations | High |
| Three foundation repairs precede high-force research | Retain | Add derived-state dependency inventory and harmful-memory tests to the same tranche | High |
| Broad external framework integration | Narrow | Use adapters only after data-flow, licensing, packaging, authority, and purge review | High |

## 7. Revised benchmark portfolio

The Memory Lab should measure a pipeline, not one score.

### 7.1 Required suites

| Suite | Why it changes the decision | ATC use |
|---|---|---|
| LongMemEval / LoCoMo | Established regression and comparability | Regression only; never promotion alone |
| [EvoMemBench](https://github.com/DSAIL-Memory/EvoMemBench) | Standardized long-context versus 15 memory methods across knowledge and execution | Representation-selection gate |
| [LongMemEval-V2](https://github.com/xiaowu0162/LongMemEval-V2) | Up to 115M tokens of multimodal environment experience, workflows, gotchas, and premise awareness | Experience retrieval and latency frontier |
| [Mem2ActBench](https://arxiv.org/abs/2601.19935) | Requires memory-grounded tool selection and parameters | Consequence and parameter-grounding gate |
| [MemoryArena](https://arxiv.org/abs/2602.16313) | Interdependent multi-session agent actions | Cross-session action benefit |
| [AMA-Bench](https://github.com/AMA-Bench/AMA-Bench) | Machine-generated agent trajectories, causality, objectives, arbitrary horizon | Causal/event-memory gate |
| [Memora/FAMA](https://arxiv.org/abs/2604.20006) | Penalizes obsolete and invalid memory | Correction and current-state gate |
| [PersistBench](https://arxiv.org/abs/2602.01146) | Cross-domain leakage and memory-induced sycophancy | Harmful-memory blocker |
| [MemSyco-Bench](https://github.com/XMUDeepLIT/MemSyco-Bench) | Tests memory role, scope, conflict, updates, and valid personalization | Epistemic/applicability blocker |
| [EMemBench](https://arxiv.org/abs/2601.16690) | Programmatic trajectory-grounded episodic tests with text and visual environments | Later multimodal gate |
| ATC purge-residue suite | Derived summaries, relations, indexes, caches, checkpoints, backups, and local learned artifacts | Zero-residue declared-boundary gate |
| ATC origin and instruction-injection suite | Spoofed user basis, imported instructions, poisoned experiences, forged rationales | Zero-authority-violation gate |

### 7.2 Required baseline controls

Every published comparison should freeze:

- answer and extraction models;
- exact model versions and reasoning settings;
- prompts and tool schemas;
- history order and clock;
- retrieval and context token budgets;
- cache eligibility and cache pricing;
- ingestion, consolidation, query, and generation latency;
- storage volume and rebuild time;
- evaluator prompts plus deterministic metrics;
- model calls and monetary cost by phase;
- source, projection, and final answer artifacts; and
- repeated runs with confidence intervals where stochastic.

### 7.3 Required stage diagnostics

Add the following to the proposal's metrics:

- memory-role classification: evidence, constraint, hypothesis, procedure,
  warning, or inapplicable;
- authorized-but-inapplicable retrieval rate;
- cross-domain memory leakage;
- sycophancy induced by memory;
- oracle-retrieval to final-action gap;
- retrieved-but-unused rate;
- hallucinated tool defaults;
- lossless retention of identifiers and structured values;
- procedure repair after wrong feedback;
- easy-task regression from unnecessary memory;
- canary extraction rate by tier;
- forgetting residue by derived tier;
- prompt/persona mutation lineage; and
- prompt-cache hit rate and stable-prefix cost.

## 8. Revised 30/60/90-day horizon

These are watch and decision items, not implementation commitments.

### Next 30 days

1. **Amend the architecture before adoption.** Apply the change log in this
   report and narrow the product claim.
2. **Specify the baseline ladder.** Freeze no-memory, long-context, ATC lexical,
   raw hybrid, stable observation-log, and file-search methods before external
   framework adapters.
3. **Add harmful-memory gates.** Port small, license-compatible slices or
   equivalent fixtures for Memora/FAMA, PersistBench, MemSyco, and
   Mem2Act-style argument grounding into the lab specification.
4. **Define memory roles.** Make epistemic/applicability policy an explicit
   interface between authorization and relevance.
5. **Inventory derived state.** Enumerate every present and planned summary,
   index, cache, export, checkpoint, backup, and learned artifact for deletion
   and purge closure.
6. **Watch:** LongMemEval-V2 leaderboard submissions, independent Mastra
   Observational Memory reproduction, and EvoMemBench result corrections.

### Next 60 days

1. **Run the first same-backbone comparison** on a small frozen portfolio:
   LongMemEval regression, Memora mutation, Mem2Act action grounding,
   PersistBench/MemSyco harmful use, and an ATC purge fixture.
2. **Decide event narrative versus graph.** Compare flat event notes, typed
   temporal links, and one causal relation projection. Do not test a full graph
   until a simpler representation fails.
3. **Test experience quarantine.** Evaluate outcome labels, wrong-reward repair,
   failure replay, and easy-task negative transfer without generating canonical
   procedures.
4. **Test stable observation logs.** Measure accuracy, source fidelity,
   correction propagation, prompt caching, and derived-tier residue.
5. **Watch:** accepted ICML 2026 versions and artifacts for AMA-Bench,
   MemoryArena, A-MemGuard, and related memory-security work; production
   lifecycle changes in AWS AgentCore, Mastra, Letta, Hindsight, and Mem0.

### Next 90 days

1. **Promote only winning mechanisms.** A component must beat the simpler rung
   at fixed model, cost, latency, disclosure, and purge.
2. **Choose whether a graph phase exists.** Continue only if typed relations
   materially improve causal, temporal, exception, or multi-hop tasks without
   unacceptable invalidation fan-out.
3. **Choose whether procedural memory exists.** Continue only if repeated
   outcomes improve and wrong-feedback repair plus purge succeed.
4. **Run Contracts Lite against memory-harm cases.** Verify that a relevant
   preference is applied to an action while an inapplicable belief is rejected,
   a correction revokes stale state, and tool arguments disclose no unrelated
   memory.
5. **Publish a negative-results ledger.** Record killed components, failed
   replications, judge sensitivity, cost regressions, and authority violations.
6. **Watch:** independent evidence for learned memory managers, prompt/persona
   compilation, KV/neural memory attribution, and any credible private-state
   unlearning or deletion guarantees.

## 9. Recommended decision

Do not reject the current proposal. Amend it before treating it as the umbrella
direction.

Keep:

- the user-owned authoritative Core;
- evidence/claim/experience/procedure/working-state separation;
- the two-plane architecture;
- deterministic authority boundaries;
- correction, deletion, and purge as distinct operations;
- source and target invalidation;
- consequence and outcome closure as the research moat;
- honest client capability levels; and
- the rule “adopt before invent, benchmark before promote, never outsource
  authority.”

Change:

- from “assemble the complete Memory Plane” to “govern a baseline ladder of
  memory projections”;
- from general graph construction to typed, benchmark-earned relations;
- from retrieval quality to safe and applicable memory intervention;
- from procedure distillation to outcome-labeled experience quarantine;
- from QA-centered evaluation to action, mutation, harmful-use, and purge
  evaluation; and
- from framework-first adapters to simple baselines first.

The most defensible roadmap is therefore:

1. repair witness, secret, transition-log, and derived-state closure
   foundations;
2. build the lab around simple baselines and harmful-memory gates;
3. add event and working-state continuity;
4. introduce typed relations only where a benchmark demonstrates need;
5. test outcome-labeled experience without automatic procedure promotion; and
6. pursue consequence contracts as the differentiated layer, with provider and
   tool disclosure treated as explicit effects.

## 10. Primary-source ledger

### Controlled studies and negative results

1. Xiong et al. [How Memory Management Impacts LLM Agents: An Empirical Study of Experience-Following Behavior](https://aclanthology.org/2026.acl-long.27/). ACL 2026. Evidence A.
2. Hu et al. [Does Memory Need Graphs? A Unified Framework and Empirical Analysis for Long-Term Dialog Memory](https://aclanthology.org/2026.acl-long.1232/). ACL 2026. Evidence A.
3. Wang et al. [EvoMemBench: Benchmarking Agent Memory from a Self-Evolving Perspective](https://arxiv.org/abs/2605.18421). 2026 preprint and official code. Evidence B.
4. Uddin et al. [From Recall to Forgetting: Benchmarking Long-Term Memory for Personalized Agents](https://arxiv.org/abs/2604.20006). 2026 preprint. Evidence B.
5. Pulipaka et al. [PersistBench: When Should Long-Term Memories Be Forgotten by LLMs?](https://arxiv.org/abs/2602.01146). 2026 preprint. Evidence B.
6. Xiang et al. [MemSyco-Bench: Benchmarking Sycophancy in Agent Memory](https://arxiv.org/abs/2607.01071). 2026 preprint and official code. Evidence B.
7. Chen et al. [Deployment-Time Memorization in Foundation-Model Agents](https://arxiv.org/abs/2606.10062). ICML MemFM 2026 workshop paper. Evidence C.
8. Wang et al. [Unveiling Privacy Risks in LLM Agent Memory](https://aclanthology.org/2025.acl-long.1227/). ACL 2025. Evidence A.
9. Karamchandani et al. [Your Agent's Memories Are Not Its Own: Forged Reasoning Attacks on LLM Agent Memory and Defenses](https://arxiv.org/abs/2607.05029). 2026 preprint. Evidence C.

### Action- and environment-grounded evaluation

10. Wu et al. [LongMemEval-V2: Evaluating Long-Term Agent Memory Toward Experienced Colleagues](https://arxiv.org/abs/2605.12493). 2026 preprint and official repository. Evidence B.
11. Shen et al. [Mem2ActBench: A Benchmark for Evaluating Long-Term Memory Utilization in Task-Oriented Autonomous Agents](https://aclanthology.org/2026.acl-long.370/). ACL 2026. Evidence A.
12. He et al. [MemoryArena: Benchmarking Agent Memory in Interdependent Multi-Session Agentic Tasks](https://arxiv.org/abs/2602.16313). ICML 2026. Evidence A.
13. Zhao et al. [AMA-Bench: Evaluating Long-Horizon Memory for Agentic Applications](https://arxiv.org/abs/2602.22769). ICML 2026 with official repository. Evidence A.
14. Li et al. [EMemBench: Interactive Benchmarking of Episodic Memory for VLM Agents](https://arxiv.org/abs/2601.16690). 2026 preprint. Evidence B.
15. Ye et al. [LoCoMo-Plus: Beyond-Factual Cognitive Memory Evaluation Framework for LLM Agents](https://aclanthology.org/2026.acl-long.1150/). ACL 2026. Evidence A.

### Representation and utilization

16. Xu et al. [Chain-of-Memory: Lightweight Memory Construction with Dynamic Evolution for LLM Agents](https://aclanthology.org/2026.acl-long.534/). ACL 2026. Evidence A.
17. Xu et al. [StructMem: Structured Memory for Long-Horizon Behavior in LLMs](https://aclanthology.org/2026.acl-short.12/). ACL 2026. Evidence A.
18. Wu et al. [GAM: Hierarchical Graph-based Agentic Memory for LLM Agents](https://aclanthology.org/2026.acl-long.1600/). ACL 2026. Evidence A.
19. Jiang et al. [MAGMA: A Multi-Graph based Agentic Memory Architecture for AI Agents](https://aclanthology.org/2026.acl-long.1709/). ACL 2026. Evidence A.
20. Li et al. [OCR-Memory: Optical Context Retrieval for Long-Horizon Agent Memory](https://aclanthology.org/2026.acl-long.474/). ACL 2026. Evidence A.
21. Barnes. [Observational Memory: 95% on LongMemEval](https://mastra.ai/research/observational-memory). Mastra first-party report, 2026. Evidence C.
22. Omri et al. [Agent Memory: Characterization and System Implications of Stateful Long-Horizon Workloads](https://arxiv.org/abs/2606.06448). 2026 preprint. Evidence B.

### Learned management and experience

23. Yu et al. [Agentic Memory: Learning Unified Long-Term and Short-Term Memory Management for Large Language Model Agents](https://aclanthology.org/2026.acl-long.981/). ACL 2026. Evidence A.
24. Yan et al. [Memory-R1: Enhancing Large Language Model Agents to Manage and Utilize Memories via Reinforcement Learning](https://aclanthology.org/2026.acl-long.583/). ACL 2026. Evidence A.
25. Zhang et al. [PersonaAgent: Bridging Memory and Action for Personalized LLM Agents](https://aclanthology.org/2026.findings-acl.1315/). Findings of ACL 2026. Evidence A.
26. Rhodes and Kang. [Compiled Memory: Not More Information, but More Precise Instructions for Language Agents](https://arxiv.org/abs/2603.15666). 2026 preprint. Evidence B.

### Production architecture and lifecycle

27. Amazon Web Services. [AgentCore Memory: How It Works](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/how-it-works.html). Current first-party documentation. Evidence B for shipped interface, not semantic quality.
28. Amazon Web Services. [Memory Organization](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-organization.html). Current first-party documentation. Evidence B.
29. Amazon Web Services. [Memory Record Streaming](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory-record-streaming.html). Current first-party documentation. Evidence B.
30. Hindsight. [Structured Agent Memory that Retains, Recalls, and Reflects](https://aclanthology.org/2026.acl-demo.27/). ACL 2026 system demonstration and official repository. Evidence A/B.
31. Mem0. [Official repository and April 2026 algorithm notes](https://github.com/mem0ai/mem0). First-party implementation and claims. Evidence C for benchmark superiority.
32. Anthropic. [Claude Sonnet 4.5 announcement: context editing and memory tool](https://www.anthropic.com/news/claude-sonnet-4-5). First-party production announcement. Evidence B for feature availability.

## 11. Known limits of this report

- July 2026 papers are new; several have no independent reproduction.
- Conference acceptance is not proof of production reliability or broad
  generalization.
- Some primary sources report only author-selected models, prompts, and tasks.
- Vendor production documentation describes interfaces, not failure rates,
  deletion closure, or semantic correctness.
- This report did not execute code, inspect runtime dependency behavior, or
  reproduce any score.
- Search coverage is broad but not mathematically exhaustive. Private systems,
  inaccessible datasets, unpublished negative results, and sources not indexed
  by the searched services may be missing.
- ATC's current proposal and related research documents were read from the
  working tree. This report does not accept their claims merely because they
  are internal documents.

The correct operational response to these limits is the proposal's best rule:
benchmark before promotion. The correction is to benchmark simpler and more
dangerous alternatives earlier.
