# Wave 2 source-evidence retrieval research

This report is a bounded, deterministic, offline experiment over sanitized imported-chat fixtures. It has no default runtime integration or production authority. Imported text was treated only as untrusted indexed data.

Frozen comparator: `LexicalV3` at `659c79136b5d5ba66b9cc5e38640a9d3f341cff3`.
The comparator's recall and coverage measure evidence available inside selected sources; passage variants must select the exact judged evidence messages.

## 64 sources

| Variant | Evidence recall | Facet coverage | Redundancy | Warm p95 ms | Incremental bytes | Deterministic | Policy violations |
| --- | ---: | ---: | ---: | ---: | ---: | :---: | ---: |
| `current_lexical_candidate_pool` | 1.000 | 1.000 | 0.083 | 6.276 | 0 | yes | 0 |
| `lexical_passages` | 1.000 | 1.000 | 0.050 | 9.035 | 114688 | yes | 0 |
| `deterministic_passage_maxsim` | 1.000 | 1.000 | 0.050 | 6.317 | 86168 | yes | 0 |
| `deterministic_diverse_maxsim` | 1.000 | 1.000 | 0.000 | 6.826 | 86168 | yes | 0 |

Safety result: **passed**. This requires deterministic repeats, invariant rankings when forbidden sources are removed, and zero policy violations for every variant.

## 256 sources

| Variant | Evidence recall | Facet coverage | Redundancy | Warm p95 ms | Incremental bytes | Deterministic | Policy violations |
| --- | ---: | ---: | ---: | ---: | ---: | :---: | ---: |
| `current_lexical_candidate_pool` | 1.000 | 1.000 | 0.083 | 18.650 | 0 | yes | 0 |
| `lexical_passages` | 1.000 | 1.000 | 0.050 | 22.612 | 397312 | yes | 0 |
| `deterministic_passage_maxsim` | 1.000 | 1.000 | 0.050 | 23.836 | 356136 | yes | 0 |
| `deterministic_diverse_maxsim` | 1.000 | 1.000 | 0.000 | 18.957 | 356136 | yes | 0 |

Safety result: **passed**. This requires deterministic repeats, invariant rankings when forbidden sources are removed, and zero policy violations for every variant.

## Unexercised claims

- `neural_model_late_interaction`: **not_exercised** - No optional model or model runtime is declared for this bounded offline experiment.

## Interpretation limits

The fixture is synthetic and intentionally small. Latency includes candidate pooling plus evidence selection but does not flush operating-system caches. Ephemeral storage is an implementation measurement, not a packaged-runtime commitment. No neural model, reranker service, ANN index, learned sparse retriever, hosted service, or production integration was exercised.
