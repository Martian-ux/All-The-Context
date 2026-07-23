# Memory Lab Wave 4 M3 dependency-complete influence closure

## Result

Decision: `RETAIN_M3_CONTRACT_AND_OPTIMIZATION`.

All 15 frozen F02 M3 cases ran 20 deterministic repeats with complete case and six-surface coverage.

## Safety and equivalence

| Metric | Count |
|---|---:|
| `published_stale_descendant_count` | 0 |
| `optimized_full_rebuild_mismatch_count` | 0 |
| `terminal_purge_residue_count` | 0 |
| `fail_open_publication_count` | 0 |
| `illegal_edge_accept_count` | 0 |
| `conflicting_replay_accept_count` | 0 |
| `duplicate_side_effect_count` | 0 |
| `privacy_receipt_violation_count` | 0 |
| `partial_repair_exposure_count` | 0 |
| `stale_writer_accept_count` | 0 |
| `purged_lineage_revival_count` | 0 |
| `ordinary_delete_purge_conflation_count` | 0 |

## Work and availability

- Optimized descendants scanned: `120`.
- Optimized descendants rebuilt: `120`.
- Full-rebuild nodes evaluated: `12000`.
- Evaluated-node reduction: `0.990000` (frozen retain threshold `0.25`).
- Safe barrier observations: `1.000000`.
- Final eligible fraction of full rebuild: `1.000000`.
- Exclusive purge-descendant identifiers checked across the full inspectable boundary, including graph inventory: `140`.
- Shared descendant recipes validated after reconstruction solely from retained support: `80`.

## F02 coverage

| F02 case | Invariants | Verdict |
|---|---|---|
| `M3-C01-CORRECTION-CHAIN` | M3-I01, M3-I02, M3-I04, M3-I08 | `PASS` |
| `M3-C02-SCOPE-NARROWING-FANOUT` | M3-I01, M3-I02, M3-I04, M3-I08 | `PASS` |
| `M3-C03-PERMISSION-REVOCATION-FANIN` | M3-I01, M3-I02, M3-I04 | `PASS` |
| `M3-C04-ORDINARY-DELETE-RESTORE` | M3-I01, M3-I02, M3-I05 | `PASS` |
| `M3-C05-TERMINAL-PURGE-IDENTIFIER-REUSE` | M3-I02, M3-I03, M3-I04, M3-I05, M3-I08 | `PASS` |
| `M3-C06-POLICY-GENERATION-CHANGE` | M3-I01, M3-I02, M3-I04, M3-I07 | `PASS` |
| `M3-C07-PARTIAL-REPAIR-BARRIER` | M3-I01, M3-I02, M3-I04 | `PASS` |
| `M3-C08-STALE-WRITER` | M3-I02, M3-I04 | `PASS` |
| `M3-C09-CYCLE-EDGE-ATTEMPT` | M3-I06, M3-I08 | `PASS` |
| `M3-C10-CROSS-SCOPE-EDGE-ATTEMPT` | M3-I06, M3-I08 | `PASS` |
| `M3-C11-DUPLICATE-AND-CONFLICTING-REPLAY` | M3-I02, M3-I04 | `PASS` |
| `M3-C12-OUT-OF-ORDER-MUTATION` | M3-I02, M3-I04 | `PASS` |
| `M3-C13-SHARED-DESCENDANT-CORRECTION` | M3-I01, M3-I02 | `PASS` |
| `M3-C14-PURGE-DURING-ISSUE` | M3-I02, M3-I03, M3-I04, M3-I08 | `PASS` |
| `M3-C15-OPTIMIZATION-WORK-CONTROL` | M3-I02 | `PASS` |

## Execution origin

- Governance base: `f545c37157845f0bd402215719cb8c747b7fc21d`.
- Import origin verified to worker worktree: `true`.
- Repository-relative imported modules: `packages/allthecontext/src/allthecontext/memory_lab_m3.py`, `bench/memory_lab_m3.py`.

## Decisive faults and ablations

- `repair_before_withdrawal`: preserved failures `{"fail_open_publication_count": 6, "published_stale_descendant_count": 6}`.
- `direct_edge_only`: preserved failures `{"optimized_full_rebuild_mismatch_count": 1, "published_stale_descendant_count": 5}`.
- `content_only_lineage`: preserved failures `{"published_stale_descendant_count": 5}`.
- `generation_only`: preserved failures `{"terminal_purge_residue_count": 1}`.
- `inventory_only`: preserved failures `{"stale_writer_accept_count": 1}`.
- `raw_record_only_purge`: preserved failures `{"fail_open_publication_count": 6, "terminal_purge_residue_count": 7}`.
- Injected `missing_inventory_edge`: `{"optimized_full_rebuild_mismatch_count": 1, "published_stale_descendant_count": 6}`.
- Injected `stale_writer`: `rejected safely`.

## Limits

- `isolated_deterministic_symbolic_evidence_only`.
- `no_operator_core_personal_context_credentials_network_models_or_real_actions`.
- `six_declared_surfaces_do_not_prove_unknown_production_surfaces_absent`.
- `full_rebuild_is_independently_coded_but_shares_the_frozen_artifact_semantics`.
- `opaque_sha256_commitments_are_synthetic_semantics_not_privacy_redactions`.
- `availability_is_logical_publication_eligibility_not_wall_clock_latency`.
- `generation_barrier_models_disconnected_stale_state_without_external_clients`.

This is research-only L1 synthetic evidence. It does not change production behavior or establish hidden-state erasure, unknown-surface completeness, real-client compliance, cross-platform behavior, or product readiness.
