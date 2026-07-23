# Wave 3 M2 sealed projection minimal compiler

Evidence: `L1_isolated_deterministic_synthetic`; fixture `20981c3da697f95c1bd791ab4aba9d2b1031afc586f71c94a70f0b9c359082b7`; 1000 paired vaults x 20 deterministic repeats.

| Condition | CAOS | Sufficiency | 1-deletion minimal | Mean chars | Pair leaks |
|---|---:|---:|---:|---:|---:|
| `full_authorized_context` | 0.840000 | 1.000000 | 0.000000 | 70.100000 | 39200 |
| `existing_deterministic_set_selection` | 1.000000 | 1.000000 | 0.000000 | 66.900000 | 0 |
| `sealed_non_minimal_projection` | 1.000000 | 1.000000 | 0.000000 | 66.900000 | 0 |
| `sealed_minimal_projection` | 1.000000 | 1.000000 | 1.000000 | 38.000000 | 0 |
| `ablation_authorization_after_relevance` | 0.650000 | 0.650000 | 0.461538 | 41.050000 | 112000 |
| `ablation_applicability_after_ranking` | 0.740000 | 0.740000 | 0.243243 | 46.900000 | 44800 |
| `ablation_no_epistemic_role` | 0.800000 | 0.800000 | 1.000000 | 37.100000 | 0 |
| `ablation_top_k_without_closure` | 0.500000 | 0.500000 | 1.000000 | 31.300000 | 0 |
| `ablation_no_current_version_reread` | 1.000000 | 1.000000 | 1.000000 | 38.000000 | 0 |
| `ablation_no_delete_and_recompile` | 1.000000 | 1.000000 | 0.000000 | 66.900000 | 0 |
| `ablation_no_cumulative_disclosure` | 1.000000 | 1.000000 | 1.000000 | 38.000000 | 0 |

## Decisive checks

- Protected-channel failures for sealed minimal projection: `0`.
- Exhaustive one-deletion checks: `2600`; removable items: `0`; nonzero exact-optimum gaps: `0`.
- Current-version races detected: `1000/1000`; no-reread ablation: `0`.
- Cumulative disclosure violations blocked: `1000/1000`; no-state ablation: `0`.
- Mean character reduction vs full authorized: `32.100000`; vs sealed non-minimal: `28.900000`.
- Leak scan passed: `True`.
- Receipt SHA-256 values are linkable, dictionary-attackable synthetic commitments; they are not production-safe redactions.
- Obligation IDs, coverage IDs, and accepted roles are frozen hand-authored upstream labels and a co-design assumption.
- Five named canary families collapse to three compiler-visible attestation patterns. Deletion versus purge and out-of-scope versus other inapplicability remain upstream distinctions.
- Timing noninterference covers only the post-seal logical receipt bucket. Pre-seal scan length and actual runtime are excluded.

## Decision

`narrow_retain_bounded_m2`: sealed_projection_noninterference_passed, finite_one_deletion_minimality_passed, minimum_disclosure_improved, generalized_product_claim_not_established.

This is isolated deterministic synthetic evidence only. It does not establish a generalized product or real-user claim.
