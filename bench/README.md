# Retrieval benchmark

## Memory Lab M0

The first executable Memory Lab slice compares a no-memory control, a
deterministic token-overlap baseline, and the current ATC Retrieval V3 pipeline
through `atc.memory-lab.retrieval-adapter.v1`. The checked-in fixture is
synthetic, sanitized, deterministic, and pinned by SHA-256 in its focused
tests:

```text
python -m bench.memory_lab --output tmp/memory-lab.json
```

The shared ABI accepts an immutable, already-authorized
`atc.memory-object.v1` snapshot. Adapters declare provider, network, and data
egress behavior; cannot declare canonical-write authority; and return ranked
object IDs plus model/token/cost accounting without returning memory content.
The runner measures task success, evidence-group sufficiency, reciprocal rank,
abstention, forbidden and out-of-contract outputs, disclosure, repeat
determinism, latency, storage, and reported usage.

Reusable reports do not emit object IDs, task names, queries, or content. They
contain aggregate counts plus deterministic ranking fingerprints derived from
fixture ordinals, so unknown-ID contract violations remain measurable without
placing identifiers in report files.

The ATC adapter builds only an isolated synthetic database in the supplied
working directory. It does not connect to or modify an operator's Core. No
third-party competitor package is installed or executed; future competitors
implement the same small adapter protocol.

## Memory reliability evaluation specification

The memory reliability program is specified in
`docs/research/ATC_MEMORY_EVALUATION_PROGRAM.md`, with machine-readable
experiment and promotion contracts in `memory_reliability_spec.json` and
sanitized symbolic event fixtures in `memory_reliability_fixtures.json`.

These files intentionally do not implement a longitudinal adapter ABI or
executable longitudinal harness. The retrieval-only M0 ABI above is the first
bounded comparison surface; these files freeze the broader scientific matrix,
first five experiments, stage-level failure taxonomy, contamination controls,
statistical plan, latency/cost budgets, and deterministic oracles that its
longitudinal successor must honor. The structural tests run with:

```text
python -m pytest tests/unit/test_memory_reliability_spec.py
```

Passing those tests proves specification consistency only. It is not evidence
that ATC or any external memory system passes an experiment.

## Wave 2 source-evidence retrieval research

The Wave 2 harness is isolated research for long sanitized imported chats and
source evidence. It freezes the current candidate-scoped `LexicalV3` source
pool at coordinator commit `659c791`, then compares lexical passage selection,
deterministic exact-token MaxSim, and diversity-aware MaxSim. It does not edit
or integrate with the default retrieval runtime.

Run the bounded 64-source and 256-source profiles:

```text
python -m bench.source_evidence_retrieval --output bench/reports/source_evidence_retrieval_wave2.json --markdown bench/reports/source_evidence_retrieval_wave2.md
```

Profiles above 256 sources require `--include-large` and remain hard-capped at
1,024 sources. The report measures source-evidence recall, facet coverage,
redundancy, end-to-end cold/warm latency, ephemeral and persistent storage,
repeat determinism, ineligible-corpus score invariance, and policy violations.
The source-pool comparator counts judged evidence available within selected
sources; passage variants must select exact judged evidence messages. Raw
queries and imported text are never emitted in reports.

This experiment has no production authority, packaging changes, hosted or paid
services, native/default dependencies, ANN, learned sparse retrieval, or model
reranker. Neural model late interaction is explicitly `not_exercised` because
no optional model or model runtime is declared for this bounded experiment.

## Wave 2 set selection

The metadata-only set selector has a deterministic sanitized fixture covering
mandatory preferences, marginal semantic/diversity utility, supporting
evidence, compatibility, conflicts, duplicate suppression, upstream
attestations, and character budgets:

```text
python -m bench.set_selection_benchmark
```

The command exits nonzero unless all 11 acceptance gates pass. Candidate keys
and signal labels are opaque; emitted diagnostics contain only closed reason
codes and aggregate numeric/boolean values.

## Optional dense shadow

The dense shadow is disabled by default and emits `not_exercised` unless an
experiment mode is explicit. Measure deterministic non-semantic 384d float32
storage and exact-scan mechanics, including the opt-in 10k latency target, with:

```text
python -m bench.dense_shadow_benchmark --exact-scan-only --profiles 128 1024 10000 --include-10k --output tmp/dense-shadow.json
```

The synthetic runtime does not support semantic claims. A genuine comparison
requires `--enable-local-model --model-path <local-directory>` and a separately
installed Sentence Transformers runtime; loading is CPU-only and
`local_files_only`, so the benchmark never downloads a model. The experiment
is in-memory, noncanonical, outside default packaging, and has no production
ranking authority.

## Retrieval V3 foundation contracts

The V3 foundation harness freezes the production V2 ordering as a named
comparator and adds sanitized lifecycle scenarios plus machine-readable gates.
It does not wire or claim any future temporal, admissibility, compatible-set,
semantic, or shadow implementation.

Run the bounded 1k and 10k comparator profiles:

```text
python -m bench.retrieval_v3_foundation run --output tmp/retrieval-v3-comparator.json
```

The 50k profile remains explicitly opt-in:

```text
python -m bench.retrieval_v3_foundation run --profiles 50000 --include-50k --output tmp/retrieval-v3-50k.json
```

Evaluate a future candidate report against an explicitly supplied comparator
report. Missing metrics produce `not_exercised`, never a pass:

```text
python -m bench.retrieval_v3_foundation compare tmp/retrieval-v3-candidate.json tmp/retrieval-v3-comparator.json
```

Normal comparator runs intentionally contain definitions but no gate results;
only the `compare` command evaluates a candidate.

Run the integrated Retrieval V3 candidate and frozen comparator together:

```text
python -m bench.retrieval_v3_combined --output tmp/retrieval-v3-combined.json
```

The command exits nonzero unless both 1k and 10k profiles pass exact recall,
admissibility precision, temporal precision, semantic coverage, redundancy,
conflict determinism, policy, resurrection, `as_of`, restart/restore, and warm
latency gates. Its output embeds the comparator report so the evidence remains
self-contained. The 50k candidate remains explicitly opt-in and capped by the
same `--include-50k` rule.

## Retrieval V1/V2 benchmark

This directory freezes the Retrieval V1 evaluation corpus and measurements used
to assess Retrieval V2. All records are deterministic, synthetic, sanitized,
offline, and independent of models, embeddings, network services, and native
extensions.

Run the normal 1k and 10k profiles from the repository root with the active
project environment:

```text
python -m bench.retrieval_benchmark run --output tmp/retrieval-candidate.json
```

The default profiles are bounded for routine developer and CI use. The 50k
profile is opt-in and capped:

```text
python -m bench.retrieval_benchmark run --profiles 50000 --include-50k --output tmp/retrieval-50k.json
```

Compare a candidate report to the checked-in V1 baseline in human-readable
form. A nonzero exit means one or more enforced gates failed:

```text
python -m bench.retrieval_benchmark compare tmp/retrieval-candidate.json
```

The gates require zero unauthorized results, exact Recall@5 no worse than V1,
overall MRR at least 10% better, multi-term empty-result rate at least 50%
lower, and 10k warm p95 no greater than `max(150 ms, 1.25 × V1)`. Comparing V1
to itself must fail the two improvement gates. Retrieval V2 Phase 1 passes the
frozen gates; measured evidence and limitations are recorded in
`docs/architecture/RETRIEVAL.md`.

## Metric definitions

- Recall@1/3/5, MRR, and nDCG@5 use the checked-in graded gold judgments.
- Empty-result rate covers all queries; multi-term empty rate covers both the
  partial-match and vocabulary-gap multi-term cases.
- Unauthorized-result count includes denied-client, other-client allowlist,
  deleted, expired, and superseded IDs declared forbidden by the fixture.
- Temporal precision is relevant current results divided by all top-five
  results for the temporal query.
- Context coverage is the fraction of mandatory/relevant bootstrap gold records
  compiled under budget. Redundancy is the fraction of selected context beyond
  the first member of a declared near-duplicate group.
- Cold latency is the first measured call for each query after database build.
  Warm latency aggregates five immediate repetitions. Both include SQLite
  connection and audit-write cost. They are p50/p95 wall-clock measurements.
- Index size is the checkpointed SQLite file size. Initial indexing reports
  elapsed time and throughput for deterministic bulk fixture construction;
  mutation p50/p95 uses the production correction and FTS reindex path.

## Limitations

This is a regression benchmark, not a user-distribution simulation. Its small
gold set emphasizes known V1 failure modes and does not estimate real-world
relevance. The cold measurement does not flush the operating-system page cache,
and latency varies with hardware, filesystem, SQLite, Python, and concurrent
load. The checked-in baseline records its environment; compare performance on
similar hardware and rerun enough times to investigate borderline results.
Bulk scale setup writes the canonical/FTS tables directly so fixture generation
does not dominate the run. Policy-rejected fixtures intentionally remain in the
FTS table so the ranker-spy invariant can catch any boundary regression, while
mutation cost exercises the public storage path. The 50k profile is for local
investigation and is not part of normal CI.
