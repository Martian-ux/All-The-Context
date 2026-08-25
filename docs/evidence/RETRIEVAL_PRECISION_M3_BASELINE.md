# Milestone 3 synthetic retrieval precision baseline

This is a privacy-safe, developer-only regression lane. It builds a temporary
fictional Core vault through `CoreStore.add_candidate`, searches through the
production `RetrievalEngine`, and emits only content-free scores. It does not
change retrieval, storage, ingestion, provider, dashboard, or release code.

The checked-in snapshot in
[`bench/baselines/retrieval_precision_m3_f5e3a2b.json`](../../bench/baselines/retrieval_precision_m3_f5e3a2b.json)
was captured at `f5e3a2b6e7f86ad65c0bf9aa78d6baa8b639456f`.

## Baseline result

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

The lane must be rerun after a production precision change. Update the
checked-in baseline only after reviewing the content-free score delta and
capturing the new production revision deliberately; the baseline test will
surface any unreviewed score change.
