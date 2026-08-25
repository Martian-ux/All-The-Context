# Milestone 3 synthetic retrieval precision evidence

This is a privacy-safe, developer-only regression lane. It builds a temporary
fictional Core vault through `CoreStore.add_candidate`, searches through the
production `RetrievalEngine`, and emits only content-free scores. It does not
change retrieval, storage, ingestion, provider, dashboard, or release code.

The checked-in snapshot in
[`bench/baselines/retrieval_precision_m3_f5e3a2b.json`](../../bench/baselines/retrieval_precision_m3_f5e3a2b.json)
was captured at `f5e3a2b6e7f86ad65c0bf9aa78d6baa8b639456f`.

## Historical baseline result

The baseline is intentionally an observation, not a fabricated quality claim.
The aggregate currently fails because the known precision and coverage-gap
cases expose current retrieval behavior. The exact per-case score, first
relevant rank, returned count, deterministic flag, and aggregate pass/fail are
stored without fixture text, queries, record IDs, or trace IDs.

| Case | Precision at 5 or returned depth | First relevant rank | Returned count | Pass |
|---|---:|---:|---:|:---:|
| `exact_project_phrase` | 0.200000 | 1 | 5 | fail |
| `platform_support_over_linux_noise` | 1.000000 | 1 | 1 | pass |
| `local_core_relay_architecture` | 0.333333 | 1 | 3 | fail |
| `mcp_provider_ingestion` | 0.333333 | 1 | 3 | fail |
| `coverage_gap_abstention` | 0.000000 | — | 1 | fail |

Aggregate precision is `0.373333` across five cases, with `1/5` cases passing,
`13` total returned items, and deterministic repeated rankings. The abstention
case returned one item, so its honest-abstention check failed.

## Current correction result

The current integrated production path uses focus query terms for hard task
coverage and counts those terms against content only. Multi-term hard coverage
rejects coverage below `0.75` when the task is sufficiently specified; therefore
2/3 coverage rejects while 3/4 coverage may pass. The measured coverage remains
in the gate factors and the hard rejection reports
`reject.low_task_query_coverage`. Candidate count and alias presence do not
change that rule. Single-term and empty-query behavior retain their existing
usefulness/fail-open paths. Scope, project, and kind evidence remain dedicated
signals and cannot satisfy task-topic coverage.
Nonempty bootstrap remains a separate broad context-assembly path so a
multi-topic request can contribute distinct authorized records; its dedicated
scope/kind/project filters still apply. The direct-search hard-floor behavior
is covered by the focused admissibility and usefulness regressions.

The unchanged five-case fixture now produces an honest quality pass:

| Case | Precision at 5 or returned depth | First relevant rank | Returned count | Abstained | Pass |
|---|---:|---:|---:|:---:|:---:|
| `exact_project_phrase` | 1.000000 | 1 | 1 | no | pass |
| `platform_support_over_linux_noise` | 1.000000 | 1 | 1 | no | pass |
| `local_core_relay_architecture` | 1.000000 | 1 | 1 | no | pass |
| `mcp_provider_ingestion` | 1.000000 | 1 | 1 | no | pass |
| `coverage_gap_abstention` | — | — | 0 | yes | pass |

Aggregate precision is `1.000000` across five cases, with `5/5` cases passing,
`4` total returned items, one honest abstention, and deterministic repeated
rankings. The current quality gate is tested independently; it does not
overwrite or compare future production output to the historical snapshot.

## Reproduce

From the repository root, run the evaluator without a live Core path:

```text
python -m bench.retrieval_precision_m3 --output tmp/retrieval-precision-m3.json --markdown tmp/retrieval-precision-m3.md
```

The command completes and reports the measured aggregate even when the quality
score is failing. Add `--fail-on-quality` when a production precision change
should make the command a CI-style gate:

```text
python -m bench.retrieval_precision_m3 --output tmp/retrieval-precision-m3-after-change.json --fail-on-quality
```

The focused harness tests are:

```text
python -m pytest tests/unit/test_retrieval_precision_m3.py
```

The lane must be rerun after a production precision change. Preserve the
historical snapshot; if a new snapshot is deliberately needed, provide its
explicit output path and revision with
`--write-baseline --baseline <new-path> --captured-revision <sha>` after
reviewing the content-free score delta. The CLI refuses the checked-in
historical path and rejects an empty revision marker.
