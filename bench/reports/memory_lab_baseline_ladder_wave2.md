# ATC Memory Lab Wave 2 baseline ladder

Fixture `5601692ea305448f6b299c32725a93c73ca83ccee66f325e22cbcbedfa0cc68f`; 7 objects; 5 tasks; 20 repeats.
Baseline config `6dbf75db008b1be2d3db643b8dd19fe45f1a45c88121ac1ac3af16a0a0cd3c98`.

| Rung | Success | Recall | Precision | Forbidden | Failures | p50 ms | p95 ms | Storage B | Cost USD | Evidence disposition |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `no-memory` | 0.200 | 0.200 | 0.000 | 0 | 4 | 0.000600 | 0.001000 | 0 | 0.000000 | `retain_control` |
| `fixed-budget-long-history` | 0.400 | 0.800 | 0.400 | 1 | 3 | 0.004900 | 0.009435 | 382 | 0.000000 | `not_earned_on_this_fixture` |
| `static-profile` | 0.600 | 0.800 | 0.400 | 1 | 2 | 0.003200 | 0.005305 | 221 | 0.000000 | `not_earned_on_this_fixture` |
| `raw-append-log-search` | 0.800 | 1.000 | 0.700 | 1 | 1 | 0.027900 | 0.067785 | 382 | 0.000000 | `not_earned_on_this_fixture` |
| `stable-observation-current-state` | 1.000 | 1.000 | 0.800 | 0 | 0 | 0.024100 | 0.058995 | 382 | 0.000000 | `advance_to_next_fixture` |
| `bounded-local-file-search` | 0.800 | 1.000 | 0.700 | 1 | 1 | 1.027850 | 1.132170 | 980 | 0.000000 | `not_earned_on_this_fixture` |
| `atc-retrieval-v3` | 0.800 | 0.900 | 0.800 | 0 | 1 | 16.458650 | 17.741220 | 335872 | 0.000000 | `not_earned_on_this_fixture` |

## Failure cases

- `no-memory`: task-index-0 (required_evidence_missing); task-index-1 (required_evidence_missing); task-index-2 (required_evidence_missing); task-index-3 (required_evidence_missing).
- `fixed-budget-long-history`: task-index-0 (required_evidence_missing); task-index-1 (forbidden_output); task-index-4 (abstention_mismatch).
- `static-profile`: task-index-1 (required_evidence_missing, forbidden_output); task-index-4 (abstention_mismatch).
- `raw-append-log-search`: task-index-1 (forbidden_output).
- `stable-observation-current-state`: none.
- `bounded-local-file-search`: task-index-1 (forbidden_output).
- `atc-retrieval-v3`: task-index-0 (required_evidence_missing).

## Validity limitations

- `sanitized_deterministic_fixture`
- `retrieval_only_no_answer_model`
- `no_action_or_caos_endpoint`
- `small_nonrepresentative_corpus`
- `simple_reference_conditions_not_implementation_acceptance`
- `wall_clock_latency_is_machine_specific`
- `storage_excludes_common_source_corpus`
- The bounded local file-search rung is an infrastructure/control baseline, not a reproduction of programmatic action-model log search.
- The stable observation condition may be aligned to this small fixture. It must pass mutation, poisoning, scale, action-grounding, and CAOS tests.
- Evidence dispositions apply only to this retrieval-stage fixture; they are not implementation acceptance or production-promotion decisions.
