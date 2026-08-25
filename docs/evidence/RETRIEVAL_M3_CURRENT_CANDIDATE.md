# Milestone 3 current-candidate retrieval evidence

This is a separate, privacy-safe developer evaluation lane. It builds a
fictional vault through public `CoreStore` and `RetrievalEngine` APIs, using a
fresh disposable vault for every case. The fixture and runner are
[`bench/retrieval_m3_current_candidate_fixture.json`](../../bench/retrieval_m3_current_candidate_fixture.json)
and
[`bench/retrieval_m3_current_candidate.py`](../../bench/retrieval_m3_current_candidate.py).

The lane deliberately does not modify or reinterpret the historical snapshot
in [`bench/baselines/retrieval_precision_m3_f5e3a2b.json`](../../bench/baselines/retrieval_precision_m3_f5e3a2b.json).

## Evaluation contract

- Direct search has one exact three-anchor record and a separate two-of-three
  near miss; the near miss must abstain.
- Bootstrap composition uses two relevant records, each with exactly one
  distinct genuine content anchor and no shared query token. The query keeps
  scaffolding outside the content, so neither record can pass the production
  per-record two-term lexical floor. The task is scored on the union across
  records, with exact both-target recall required.
- The same split records are run against a three-anchor task to require
  union-insufficient abstention.
- Curated alias-only, kind-only, tag-only, and scope-only matches are
  abstention cases. Metadata is never counted as a genuine content anchor.
- A 256-record deterministic metadata-only noise profile is compared against
  the corresponding quiet cases. All task terms occur only in metadata and
  none occur in noise content. The exact returned classification must remain
  unchanged, and exact expected sets reject every unjudged false positive.

The JSON and Markdown outputs contain only case labels, bounded counts,
booleans, safe reason codes, and an input fixture hash. They do not contain
fixture content, query text, record IDs, filesystem paths, or private data.

## Base observation

At base revision `6db3e8b`, the lane is deterministic and 7/10 case gates pass.
The three expected production-red nodes are:

- `bootstrap_split_union_recall`: both one-anchor split records are missing
  (`missing_relevant`, `union_coverage_shortfall`).

- `alias_only_not_full_coverage`: the `cache` alias-only match is returned for
  the two-anchor task (`unjudged_false_positive`, `abstention_violation`).

- `bootstrap_split_metadata_noise_invariance`: the 256-row metadata-only
  profile still misses both expected split records (`missing_relevant`,
  `union_coverage_shortfall`). Quiet and noisy returned classifications remain
  invariant, but neither receives recall credit.

The direct exact, strict near-miss, union-insufficient abstention, kind-only,
tag-only, scope-only, and direct metadata-noise cases pass. These are observed
candidate measurements, not changes to the historical baseline.

## Reproduce

From this checkout, use a repository-local temporary parent:

```text
python -m bench.retrieval_m3_current_candidate --output tmp/retrieval-m3-current-candidate.json --markdown tmp/retrieval-m3-current-candidate.md
```

Add `--fail-on-quality` for a CI-style return code. The focused tests are:

```text
python -m pytest tests/unit/test_retrieval_m3_current_candidate.py
```

This current-candidate scorecard is an evaluation gate, not a historical
baseline. A production-red case should be reported by its case label and safe
reason code; evaluator defects should be fixed in the lane itself.
