# Retrieval benchmark

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
