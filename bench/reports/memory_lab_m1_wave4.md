# Wave 4 M1 observable memory-use ledger

Evidence: `L1` synthetic only. Frozen F02 oracle: `a866ad5b9d17a72d73d2dca4de4dd8be1e71ca9e`.
Execution origin: governance base `f545c37157845f0bd402215719cb8c747b7fc21d`; worker-worktree import verified: `True`; module paths: `packages/allthecontext/src/allthecontext/memory_lab_m1.py`.

## F02 coverage

| Case | Verdict | Evidence |
|---|---|---|
| `M1-C01-HAPPY-OBSERVABLE-CHAIN` | `PASS` | `exact_chain_replayed` |
| `M1-C02-NONACKNOWLEDGEMENT-IS-UNKNOWN` | `PASS` | `absence_remains_unknown` |
| `M1-C03-USE-WITHOUT-ACKNOWLEDGEMENT` | `PASS` | `direct_use_without_acknowledgement` |
| `M1-C04-ACKNOWLEDGED-BUT-NOT-OBSERVED-USED` | `PASS` | `acknowledgement_not_promoted` |
| `M1-C05-DUPLICATE-REPLAY` | `PASS` | `identical_retry_no_side_effect` |
| `M1-C06-CONFLICTING-REPLAY` | `PASS` | `conflicting_retry_rejected` |
| `M1-C07-OUT-OF-ORDER-AND-IMPOSSIBLE` | `PASS` | `three_impossible_transitions_rejected` |
| `M1-C08-FABRICATED-OUTCOME` | `PASS` | `only_independent_outcome_accepted` |
| `M1-C09-CORRECTION-INVALIDATION` | `PASS` | `correction_invalidates_old_version` |
| `M1-C10-SCOPE-AND-PERMISSION-INVALIDATION` | `PASS` | `principals_receive_exact_invalidation_reasons` |
| `M1-C11-DELETE-VERSUS-PURGE` | `PASS` | `delete_reversible_purge_delinks` |
| `M1-C12-PURGED-IDENTIFIER-REUSE` | `PASS` | `fresh_lineage_cannot_accept_old_receipts` |
| `M1-C13-POLICY-GENERATION-INVALIDATION` | `PASS` | `old_policy_events_rejected` |
| `M1-C14-RAW-TRACE-AND-HIDDEN-REASONING-REJECTION` | `PASS` | `forbidden_values_rejected_without_echo` |
| `M1-C15-RECEIPT-CORRELATION-NONINTERFERENCE` | `PASS` | `unauthorized_canary_has_zero_observable_effect` |
| `M1-C16-PURGE-RACE-WITH-OBSERVED-USE` | `PASS` | `purge_barrier_rejects_delayed_use` |

## Decisive metrics

- `accepted_forbidden_field_count`: `0`
- `unbound_downstream_event_count`: `0`
- `duplicate_side_effect_count`: `0`
- `conflicting_replay_accept_count`: `0`
- `impossible_transition_accept_count`: `0`
- `fabricated_outcome_accept_count`: `0`
- `missing_required_invalidation_count`: `0`
- `nonacknowledgement_inferred_nonuse_count`: `0`
- `receipt_pair_difference_count_after_placeholder_normalization`: `0`
- `stable_cross_run_identifier_count`: `0`
- `purge_receipt_residue_count`: `0`
- `purge_inspectable_identifier_residue_count`: `0`
- `case_coverage_fraction`: `1.0`

Ordinary accepted events are append-only. Terminal purge is an explicit destructive privacy compaction: affected event and record identifiers are removed from every declared inspectable state surface, leaving only an aggregate identity-generation barrier and purge count. Replay begins from that compacted state.

## Paired episodes and causal boundary

`200` arm executions over `40` paired assignments. Observational association sum: `0`; controlled-omission randomized-effect sum: `0`.

Acknowledgement and host-observed dependence remain observational facts. Only the preassigned controlled-omission contrast is reported as an experimental effect; no ledger event upgrades itself to causal evidence.

## Ablations

- `collapse_all_grades_to_used`: acknowledgement_and_observed_use_conflated.
- `client_self_report`: claimant_can_fabricate_use_or_success.
- `success_only_logging`: selection_bias_erases_harm_and_failure.
- `unversioned_records`: correction_aliases_old_and_new_evidence.
- `aggregates_without_dependencies`: deterministic_rebuild_cannot_remove_contribution.

## Decision

`RETAIN_M1_OBSERVABLE_LEDGER` for `isolated_deterministic_research_contract_only`.

## Limitations

- `synthetic_deterministic_L1_only`
- `no_model_client_provider_operator_core_or_real_action`
- `host_observed_dependency_does_not_establish_hidden_model_use`
- `controlled_omission_effect_is_fixture_specific`
- `logical_timing_classes_do_not_establish_wall_clock_side_channel_resistance`
- `exact_record_ids_are_present_only_while_their_lineage_is_active`
- `terminal_purge_is_a_destructive_compaction_exception_to_physical_append_only_storage`
- `the_global_identity_generation_barrier_is_a_conservative_research_contract`
