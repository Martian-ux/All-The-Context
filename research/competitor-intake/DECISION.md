# AI-memory competitor intake decision

**Evidence cutoff:** 2026-07-23 18:56 UTC
**Scope:** research intake only; no dependency, adapter, schema, or production
architecture is accepted by this document.

The labels below describe the next research action:

- **Adopt** means place the mechanism or external baseline on the Memory Lab
  backlog. It does not authorize importing upstream code.
- **Adapt** means reproduce a bounded design behind Core-authoritative
  interfaces only after benchmark and legal review.
- **Observe** means preserve the evidence but do not schedule integration.
- **Reject** means do not pursue the system as an ATC dependency under the
  current product and supply-chain constraints.

## Ranked intake

| Rank | Candidate | Decision | What ATC should take | Principal constraint |
|---:|---|---|---|---|
| 1 | Hindsight | Adopt | First full-system baseline; fact/experience/observation/opinion separation; retain/recall/reflect; vector + keyword + graph + time fusion | PostgreSQL/pgvector and model pipeline are heavy; no built-in PII detection or automatic redaction is documented in the ACL paper |
| 2 | Mem0 | Adopt | Additive extraction and a production-oriented extraction/retrieval baseline | Default and optional dependencies span hosted models and many vector/graph stores; Platform terms are separate from Apache-2.0 OSS |
| 3 | Graphiti/Zep | Adopt | Bitemporal episode/entity/fact projection and hybrid temporal-graph retrieval | Requires a graph backend and LLM/embedding services; the Zep hosted product is not licensed by Graphiti's Apache-2.0 license |
| 4 | LangMem/LangGraph | Adopt | Namespace stores, durable checkpoints, hot-path tools, and background consolidation | LangMem pulls in LangChain provider clients; LangGraph Platform/LangSmith are separate commercial services |
| 5 | Letta/MemGPT | Adapt | Working memory blocks, archival tiers, progressive disclosure, interrupts, and resumable agent state | The canonical Python server repository is now labeled legacy; active work moved to Letta Code/App Server and adds a Node 22.19+ surface |
| 6 | ReasoningBank | Adapt | Outcome-linked, reusable procedural tactics from successes and failures | The official repository vendors patched `mini-swe-agent` and WebArena trees without nested license/notice files; reuse needs legal provenance review |
| 7 | MIRIX | Adapt | Six explicit memory types and multimodal/resource-memory separation | Multi-agent and screen-capture surfaces create high disclosure risk; cloud terms and inherited Letta provenance need separate review |
| 8 | HippoRAG 2 | Observe | Personalized PageRank and passage/entity associative retrieval as an authorization-filtered lab channel | Pinned Torch/Transformers/OpenAI stack is heavy and it is a retriever, not a governed memory lifecycle |
| 9 | A-MEM | Observe | Zettelkasten-style contextual notes, dynamic links, and relation discovery | Agent-driven evolution rewrites derived representations; maintenance is slower and authority/correction semantics conflict with Core |
| 10 | AgeMem | Observe | Treat memory operations as explicit, attributable tools and evaluate learned memory policy in research | No official implementation repository or software license was located; three-stage RL/GRPO burden is outside ATC's near-term path |
| 11 | MemOS | Reject | Keep only the lifecycle, versioned-unit, and scheduling vocabulary as prior art | Activation/parameter memory, broad backend matrix, heavyweight ML extras, and opaque purge/rebuild semantics conflict with the local cross-platform product |

## Integration boundary

No candidate may write canonical Core tables, decide origin or disposition,
expand permission, assign behavioral force, or return authoritative personal
prose from an independent store. A future adapter must receive only
Core-authorized inputs and return IDs, scores, or source-linked proposals.
Imported text remains untrusted data.

Before any code reuse or execution, repeat license review at the selected
revision, inventory transitive dependencies and model/data licenses, produce a
software bill of materials, run the candidate in an isolated lab boundary, and
verify correction, deletion, purge, authorization, data-egress, and
cross-platform behavior. Vendor-reported benchmark results are hypotheses until
reproduced under the same model, data, token, latency, and cost budgets.

## Provenance and clone decision

Exact repositories, revisions, license files, papers, dependency observations,
and risks are recorded in
[`memory-systems-intake.v1.json`](memory-systems-intake.v1.json). Repository
heads are immutable commit pins observed through the official GitHub API; they
are not release endorsements.

No upstream repository was cloned. License confirmation preceded inspection of
official pinned README/dependency/license material, and a clone would not have
improved the intake decision. The ignored
[`vendor-cache`](../vendor-cache/README.md) exists only for a later, explicitly
approved source audit.

## Unresolved legal and security questions

1. Do ReasoningBank's vendored and patched third-party trees preserve every
   upstream copyright, license, and notice obligation?
2. Do MIRIX's Letta-derived portions require additional NOTICE attribution, and
   which portions remain in the current memory-only branch?
3. Which model weights, rerankers, datasets, database extensions, container
   images, and hosted APIs would be selected, and do their licenses and data
   terms permit local evaluation and intended distribution?
4. Do trademark policies constrain use of the Mem0, Hindsight, Zep, Letta,
   LangChain, MemOS, or MIRIX names in adapters and benchmark reports?
5. Can each candidate prove tenant isolation, deletion/purge completeness,
   prompt-injection resistance, secret/PII handling, and no unexpected
   telemetry or data egress?
6. AgeMem publishes an inspectable paper, not reusable software. Is an official
   implementation forthcoming under an acceptable license?
