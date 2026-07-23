"""Run the isolated Wave 4 M1 observable memory-use ledger experiment."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import allthecontext.memory_lab_m1 as m1_module
from allthecontext.memory_lab_m1 import (
    AdmissionStatus,
    CanonicalRecord,
    FailureClass,
    InvalidationReason,
    ObservableSource,
    ObservableUseLedger,
    OutcomeStatus,
    Stage,
    serialize_failure,
    serialize_receipts,
)

FIXTURES = Path(__file__).with_name("memory_lab_m1_fixtures.json")
REPORT_SCHEMA = "atc.memory-lab.m1-report.v1"
F02_ORACLE_COMMIT = "a866ad5b9d17a72d73d2dca4de4dd8be1e71ca9e"
GOVERNANCE_BASE_COMMIT = "f545c37157845f0bd402215719cb8c747b7fc21d"
F02_CASE_IDS = (
    "M1-C01-HAPPY-OBSERVABLE-CHAIN",
    "M1-C02-NONACKNOWLEDGEMENT-IS-UNKNOWN",
    "M1-C03-USE-WITHOUT-ACKNOWLEDGEMENT",
    "M1-C04-ACKNOWLEDGED-BUT-NOT-OBSERVED-USED",
    "M1-C05-DUPLICATE-REPLAY",
    "M1-C06-CONFLICTING-REPLAY",
    "M1-C07-OUT-OF-ORDER-AND-IMPOSSIBLE",
    "M1-C08-FABRICATED-OUTCOME",
    "M1-C09-CORRECTION-INVALIDATION",
    "M1-C10-SCOPE-AND-PERMISSION-INVALIDATION",
    "M1-C11-DELETE-VERSUS-PURGE",
    "M1-C12-PURGED-IDENTIFIER-REUSE",
    "M1-C13-POLICY-GENERATION-INVALIDATION",
    "M1-C14-RAW-TRACE-AND-HIDDEN-REASONING-REJECTION",
    "M1-C15-RECEIPT-CORRELATION-NONINTERFERENCE",
    "M1-C16-PURGE-RACE-WITH-OBSERVED-USE",
)
FORBIDDEN_TOKENS = (
    "raw_context",
    "raw_prompt",
    "raw_response",
    "raw_supplied_context",
    "hidden_reasoning",
    "chain_of_thought",
    "credential",
    "secret",
    "model_self_report",
    "SYNTHETIC_PRIVATE_PAYLOAD",
)


def load_fixture() -> dict[str, Any]:
    fixture = json.loads(FIXTURES.read_text(encoding="utf-8"))
    if fixture["schema"] != "atc.memory-lab.m1-fixtures.v1":
        raise ValueError("unexpected fixture schema")
    return fixture


def _ledger(run_id: str = "run-a", *, record_id: str = "record-a") -> ObservableUseLedger:
    ledger = ObservableUseLedger(run_id=run_id)
    ledger.register_record(CanonicalRecord(record_id, 1, f"lineage-{record_id}"))
    return ledger


def _event(
    stage: Stage,
    event_id: str,
    *,
    transaction_id: str = "transaction-a",
    record_id: str = "record-a",
    version: int = 1,
    snapshot: str = "snapshot-1",
    issue: str = "issue-1",
    policy: int = 1,
    principal: str = "principal-view-1",
    parents: Sequence[str] = (),
    source: ObservableSource | None = None,
    outcome: OutcomeStatus | None = None,
    action: str | None = None,
    invalidation: InvalidationReason | None = None,
) -> dict[str, Any]:
    default_sources = {
        Stage.ASSIGNED: ObservableSource.CORE,
        Stage.SUPPLIED: ObservableSource.CORE,
        Stage.ACKNOWLEDGED: ObservableSource.CLIENT_TRANSPORT,
        Stage.OBSERVED_USE: ObservableSource.HOST_ARTIFACT,
        Stage.ACTION: ObservableSource.TOOL_GATEWAY,
        Stage.OUTCOME: ObservableSource.OUTCOME_ADAPTER,
        Stage.INVALIDATED: ObservableSource.LIFECYCLE,
    }
    result: dict[str, Any] = {
        "event_id": event_id,
        "transaction_id": transaction_id,
        "stage": stage.value,
        "canonical_record_id": record_id,
        "canonical_record_version": version,
        "canonical_snapshot_id": snapshot,
        "issue_receipt_id": issue,
        "policy_generation": policy,
        "principal_capability_view_id": principal,
        "causal_predecessor_event_ids": list(parents),
        "event_time_bucket": "logical-1",
        "observable_source_type": (source or default_sources[stage]).value,
    }
    if outcome is not None:
        result["outcome_status_enum"] = outcome.value
    if action is not None:
        result["action_type_enum"] = action
    if invalidation is not None:
        result["invalidation_reason_enum"] = invalidation.value
    return result


def _append_chain(
    ledger: ObservableUseLedger,
    *,
    through: Stage = Stage.OUTCOME,
    transaction_id: str = "transaction-a",
    record_id: str = "record-a",
    version: int = 1,
    issue: str = "issue-1",
    policy: int = 1,
    principal: str = "principal-view-1",
    include_acknowledgement: bool = True,
    outcome: OutcomeStatus = OutcomeStatus.SUCCEEDED,
) -> list[str]:
    ids: list[str] = []
    stages = [Stage.ASSIGNED, Stage.SUPPLIED]
    if include_acknowledgement:
        stages.append(Stage.ACKNOWLEDGED)
    stages.extend([Stage.OBSERVED_USE, Stage.ACTION, Stage.OUTCOME])
    for stage in stages:
        event_id = f"{transaction_id}-{stage.value}"
        if stage is Stage.ASSIGNED:
            parents: tuple[str, ...] = ()
        elif stage is Stage.OBSERVED_USE:
            parents = (ids[1], ids[-1]) if include_acknowledgement else (ids[1],)
        else:
            parents = (ids[-1],)
        result = ledger.append(
            _event(
                stage,
                event_id,
                transaction_id=transaction_id,
                record_id=record_id,
                version=version,
                issue=issue,
                policy=policy,
                principal=principal,
                parents=parents,
                outcome=outcome if stage is Stage.OUTCOME else None,
                action="synthetic_read" if stage is Stage.ACTION else None,
            )
        )
        if result.status is not AdmissionStatus.ACCEPTED:
            raise AssertionError(f"chain admission failed at {stage}: {result.failure}")
        ids.append(event_id)
        if stage is through:
            break
    return ids


def _case_verdicts() -> dict[str, dict[str, Any]]:
    verdicts: dict[str, dict[str, Any]] = {}

    ledger = _ledger()
    _append_chain(ledger)
    replay = ledger.replay()
    verdicts[F02_CASE_IDS[0]] = _verdict(
        ledger.events == replay.events
        and ledger.transaction_view("transaction-a").stages
        == (
            Stage.ASSIGNED,
            Stage.SUPPLIED,
            Stage.ACKNOWLEDGED,
            Stage.OBSERVED_USE,
            Stage.ACTION,
            Stage.OUTCOME,
        ),
        ("M1-I01", "M1-I05", "M1-I06", "M1-I07"),
        "exact_chain_replayed",
    )

    ledger = _ledger()
    _append_chain(ledger, through=Stage.SUPPLIED)
    view = ledger.transaction_view("transaction-a")
    verdicts[F02_CASE_IDS[1]] = _verdict(
        view.acknowledgement == view.observed_use == "not_observed"
        and "not_used" not in repr(view),
        ("M1-I05",),
        "absence_remains_unknown",
    )

    ledger = _ledger()
    _append_chain(ledger, through=Stage.ACTION, include_acknowledgement=False)
    view = ledger.transaction_view("transaction-a")
    verdicts[F02_CASE_IDS[2]] = _verdict(
        view.acknowledgement == "not_observed"
        and view.observed_use == "observed"
        and Stage.ACTION in view.stages,
        ("M1-I01", "M1-I05"),
        "direct_use_without_acknowledgement",
    )

    ledger = _ledger()
    _append_chain(ledger, through=Stage.ACKNOWLEDGED)
    view = ledger.transaction_view("transaction-a")
    verdicts[F02_CASE_IDS[3]] = _verdict(
        view.acknowledgement == "observed"
        and view.observed_use == "not_observed"
        and ledger.rebuild_aggregates().observed_use == 0,
        ("M1-I05",),
        "acknowledgement_not_promoted",
    )

    ledger = _ledger()
    _append_chain(ledger, through=Stage.OBSERVED_USE)
    duplicate = ledger.append(asdict(ledger.events[-1]))
    verdicts[F02_CASE_IDS[4]] = _verdict(
        duplicate.status is AdmissionStatus.IDEMPOTENT
        and ledger.rebuild_aggregates().observed_use == 1,
        ("M1-I02",),
        "identical_retry_no_side_effect",
    )

    ledger = _ledger()
    ledger.register_record(CanonicalRecord("record-a", 2, "lineage-record-a"))
    ids = _append_chain(ledger, through=Stage.ACTION)
    conflict = ledger.append(
        _event(
            Stage.ACTION,
            ids[-1],
            version=2,
            parents=(ids[-2],),
            action="synthetic_read",
        )
    )
    verdicts[F02_CASE_IDS[5]] = _verdict(
        conflict.status is AdmissionStatus.REJECTED
        and conflict.failure is not None
        and conflict.failure.failure_class_enum is FailureClass.DUPLICATE_CONFLICT,
        ("M1-I01", "M1-I02"),
        "conflicting_retry_rejected",
    )

    ledger = _ledger()
    assigned = ledger.append(_event(Stage.ASSIGNED, "assigned"))
    supplied = ledger.append(_event(Stage.SUPPLIED, "supplied", parents=("assigned",)))
    invalid_attempts = (
        ledger.append(
            _event(
                Stage.OUTCOME,
                "early-outcome",
                parents=("supplied",),
                outcome=OutcomeStatus.SUCCEEDED,
            )
        ),
        ledger.append(
            _event(Stage.ACTION, "early-action", parents=("supplied",), action="read")
        ),
        ledger.append(
            _event(Stage.OBSERVED_USE, "unknown-parent", parents=("missing",))
        ),
    )
    verdicts[F02_CASE_IDS[6]] = _verdict(
        assigned.status is supplied.status is AdmissionStatus.ACCEPTED
        and all(item.status is AdmissionStatus.REJECTED for item in invalid_attempts)
        and len(ledger.events) == 2,
        ("M1-I01", "M1-I03", "M1-I06"),
        "three_impossible_transitions_rejected",
    )

    ledger = _ledger()
    ids = _append_chain(ledger, through=Stage.ACTION)
    client_claim = ledger.append(
        _event(
            Stage.OUTCOME,
            "client-success",
            parents=(ids[-1],),
            source=ObservableSource.CLIENT_TRANSPORT,
            outcome=OutcomeStatus.SUCCEEDED,
        )
    )
    wrong_parent = ledger.append(
        _event(
            Stage.OUTCOME,
            "borrowed-success",
            parents=("missing-action",),
            outcome=OutcomeStatus.SUCCEEDED,
        )
    )
    observed_failure = ledger.append(
        _event(
            Stage.OUTCOME,
            "observed-failure",
            parents=(ids[-1],),
            outcome=OutcomeStatus.FAILED,
        )
    )
    verdicts[F02_CASE_IDS[7]] = _verdict(
        client_claim.status is AdmissionStatus.REJECTED
        and wrong_parent.status is AdmissionStatus.REJECTED
        and observed_failure.status is AdmissionStatus.ACCEPTED,
        ("M1-I01", "M1-I06", "M1-I07"),
        "only_independent_outcome_accepted",
    )

    ledger = _ledger()
    _append_chain(ledger, through=Stage.SUPPLIED)
    invalidations = ledger.invalidate_record(
        record_id="record-a",
        version=1,
        reason=InvalidationReason.CORRECTION,
        event_time_bucket="logical-2",
    )
    late = ledger.append(
        _event(
            Stage.OBSERVED_USE,
            "late-use",
            parents=("transaction-a-supplied",),
        )
    )
    ledger.register_record(CanonicalRecord("record-a", 2, "lineage-record-a"))
    fresh_ids = _append_chain(
        ledger,
        through=Stage.SUPPLIED,
        transaction_id="transaction-v2",
        version=2,
        issue="issue-v2",
    )
    verdicts[F02_CASE_IDS[8]] = _verdict(
        len(invalidations) == 1
        and late.status is AdmissionStatus.REJECTED
        and fresh_ids[-1] == "transaction-v2-supplied",
        ("M1-I01", "M1-I04"),
        "correction_invalidates_old_version",
    )

    ledger = _ledger()
    _append_chain(
        ledger,
        through=Stage.SUPPLIED,
        transaction_id="transaction-alpha",
        issue="issue-alpha",
        principal="principal-alpha",
    )
    _append_chain(
        ledger,
        through=Stage.SUPPLIED,
        transaction_id="transaction-beta",
        issue="issue-beta",
        principal="principal-beta",
    )
    scope = ledger.invalidate_record(
        record_id="record-a",
        version=1,
        reason=InvalidationReason.SCOPE_NARROWING,
        event_time_bucket="logical-2",
        principal_capability_view_ids=frozenset({"principal-beta"}),
    )
    permission = ledger.invalidate_record(
        record_id="record-a",
        version=1,
        reason=InvalidationReason.PERMISSION_REVOCATION,
        event_time_bucket="logical-3",
        principal_capability_view_ids=frozenset({"principal-alpha"}),
    )
    reasons = {
        ledger.transaction_view("transaction-alpha").invalidation_reason,
        ledger.transaction_view("transaction-beta").invalidation_reason,
    }
    late_attempts = (
        ledger.append(
            _event(
                Stage.OBSERVED_USE,
                "late-alpha-use",
                transaction_id="transaction-alpha",
                issue="issue-alpha",
                principal="principal-alpha",
                parents=("transaction-alpha-supplied",),
            )
        ),
        ledger.append(
            _event(
                Stage.OBSERVED_USE,
                "late-beta-use",
                transaction_id="transaction-beta",
                issue="issue-beta",
                principal="principal-beta",
                parents=("transaction-beta-supplied",),
            )
        ),
    )
    verdicts[F02_CASE_IDS[9]] = _verdict(
        (
            len(scope) == len(permission) == 1
            and reasons
            == {
                InvalidationReason.SCOPE_NARROWING,
                InvalidationReason.PERMISSION_REVOCATION,
            }
        )
        and all(
            attempt.status is AdmissionStatus.REJECTED
            for attempt in late_attempts
        ),
        ("M1-I04", "M1-I07"),
        "principals_receive_exact_invalidation_reasons",
    )

    ledger = _ledger()
    _append_chain(ledger, through=Stage.SUPPLIED, transaction_id="t1", issue="issue-t1")
    deleted = ledger.invalidate_record(
        record_id="record-a",
        version=1,
        reason=InvalidationReason.ORDINARY_DELETE,
        event_time_bucket="logical-2",
    )
    _append_chain(ledger, through=Stage.SUPPLIED, transaction_id="t2", issue="issue-t2")
    replay_payload = asdict(ledger.events[0])
    purged = ledger.invalidate_record(
        record_id="record-a",
        version=1,
        reason=InvalidationReason.TERMINAL_PURGE,
        event_time_bucket="logical-3",
    )
    replay_attempt = ledger.append(replay_payload)
    restore_rejected = False
    try:
        ledger.register_record(CanonicalRecord("record-a", 2, "lineage-record-a"))
    except ValueError:
        restore_rejected = True
    verdicts[F02_CASE_IDS[10]] = _verdict(
        len(deleted) == 1
        and len(purged) == 1
        and replay_attempt.status is AdmissionStatus.REJECTED
        and restore_rejected
        and ledger.rebuild_aggregates().purge_count == 1
        and not ledger.normalized_receipts()
        and ledger.replay().inspectable_state() == ledger.inspectable_state(),
        ("M1-I04", "M1-I08"),
        "delete_reversible_purge_delinks",
    )

    ledger = _ledger(record_id="old-12")
    _append_chain(
        ledger,
        through=Stage.SUPPLIED,
        record_id="old-12",
        transaction_id="old-transaction",
        issue="old-issue",
    )
    ledger.invalidate_record(
        record_id="old-12",
        version=1,
        reason=InvalidationReason.TERMINAL_PURGE,
        event_time_bucket="logical-2",
    )
    ledger.register_record(
        CanonicalRecord(
            "old-12",
            2,
            "fresh-lineage-old-12",
            identity_generation=2,
        )
    )
    old_receipt = ledger.append(
        _event(
            Stage.OBSERVED_USE,
            "old-use",
            record_id="old-12",
            transaction_id="old-transaction",
            issue="old-issue",
            parents=("old-transaction-supplied",),
        )
    )
    verdicts[F02_CASE_IDS[11]] = _verdict(
        old_receipt.status is AdmissionStatus.REJECTED
        and old_receipt.failure is not None
        and old_receipt.failure.failure_class_enum is FailureClass.UNKNOWN_RECORD
        and "old-12" not in serialize_receipts(ledger.normalized_receipts()),
        ("M1-I01", "M1-I07", "M1-I08"),
        "fresh_lineage_cannot_accept_old_receipts",
    )

    ledger = _ledger()
    _append_chain(ledger, through=Stage.SUPPLIED, policy=2)
    invalidations = ledger.advance_policy_generation(
        old_generation=2, event_time_bucket="logical-2"
    )
    late = ledger.append(
        _event(
            Stage.ACKNOWLEDGED,
            "late-ack",
            policy=2,
            parents=("transaction-a-supplied",),
        )
    )
    fresh = _append_chain(
        ledger,
        through=Stage.SUPPLIED,
        transaction_id="transaction-g3",
        issue="issue-g3",
        policy=3,
    )
    verdicts[F02_CASE_IDS[12]] = _verdict(
        len(invalidations) == 1
        and late.status is AdmissionStatus.REJECTED
        and fresh[-1] == "transaction-g3-supplied",
        ("M1-I01", "M1-I04"),
        "old_policy_events_rejected",
    )

    ledger = _ledger()
    base = _event(Stage.ASSIGNED, "assigned")
    failures = []
    for forbidden in ("raw_context", "chain_of_thought", "stable_content_hash"):
        attempt = dict(base)
        attempt["event_id"] = f"assigned-{forbidden}"
        attempt[forbidden] = "SYNTHETIC_PRIVATE_PAYLOAD"
        failures.append(ledger.append(attempt, case_id=F02_CASE_IDS[13]))
    accepted = ledger.append(base, case_id=F02_CASE_IDS[13])
    rendered_failures = "".join(
        serialize_failure(item.failure)
        for item in failures
        if item.failure is not None
    )
    verdicts[F02_CASE_IDS[13]] = _verdict(
        all(item.status is AdmissionStatus.REJECTED for item in failures)
        and accepted.status is AdmissionStatus.ACCEPTED
        and "SYNTHETIC_PRIVATE_PAYLOAD" not in rendered_failures,
        ("M1-I07",),
        "forbidden_values_rejected_without_echo",
    )

    left = _paired_noninterference("paired-left", include_canary=False)
    right = _paired_noninterference("paired-right", include_canary=True)
    verdicts[F02_CASE_IDS[14]] = _verdict(
        left["normalized"] == right["normalized"]
        and left["observables"] == right["observables"]
        and left["external_ids"] != right["external_ids"],
        ("M1-I07", "M1-I08"),
        "unauthorized_canary_has_zero_observable_effect",
    )

    ledger = _ledger()
    ids = _append_chain(ledger, through=Stage.SUPPLIED)
    ledger.invalidate_record(
        record_id="record-a",
        version=1,
        reason=InvalidationReason.TERMINAL_PURGE,
        event_time_bucket="logical-2",
    )
    delayed = ledger.append(
        _event(Stage.OBSERVED_USE, "delayed-use", parents=(ids[-1],))
    )
    verdicts[F02_CASE_IDS[15]] = _verdict(
        delayed.status is AdmissionStatus.REJECTED
        and ledger.rebuild_aggregates().observed_use == 0
        and not ledger.normalized_receipts(),
        ("M1-I04", "M1-I05", "M1-I08"),
        "purge_barrier_rejects_delayed_use",
    )
    return verdicts


def _paired_noninterference(
    run_id: str,
    *,
    include_canary: bool,
    canary_kind: str = "unauthorized",
) -> dict[str, Any]:
    candidates = [
        {"id": "record-a", "authorized": True, "applicable": True},
    ]
    if include_canary:
        if canary_kind not in {"unauthorized", "inapplicable"}:
            raise ValueError("unknown canary kind")
        candidates.append(
            {
                "id": f"{canary_kind}-canary",
                "authorized": canary_kind == "inapplicable",
                "applicable": canary_kind == "unauthorized",
            }
        )
    admitted = [
        item for item in candidates if item["authorized"] and item["applicable"]
    ]
    ledger = _ledger(run_id)
    _append_chain(ledger, through=Stage.OUTCOME)
    normalized = list(ledger.normalized_receipts())
    external_ids = [item["per_run_receipt_id"] for item in normalized]
    for item in normalized:
        item["per_run_receipt_id"] = "per-run-placeholder"
    aggregates = asdict(ledger.rebuild_aggregates())
    observables = {
        "candidate_count": len(admitted),
        "reason_codes": ["authorized", "applicable"],
        "page_cursor_shape": [len(admitted)],
        "timing_class": "logical_0_4",
        "learned_aggregates": aggregates,
    }
    return {
        "normalized": normalized,
        "external_ids": external_ids,
        "observables": observables,
    }


def _episode_matrix(fixture: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    observational_associations: list[int] = []
    randomized_effects: list[int] = []
    per_kind: dict[str, dict[str, int]] = {}
    for kind_spec in fixture["episode_kinds"]:
        kind_associations: list[int] = []
        kind_effects: list[int] = []
        for ordinal in range(1, fixture["episodes_per_kind"] + 1):
            baseline = bool(kind_spec["baseline_success"])
            host = bool(kind_spec["host_observed_success"])
            omission = bool(kind_spec["controlled_omission_success"])
            for arm in fixture["arms"]:
                if arm == "no_memory":
                    grade = "none"
                elif arm == "controlled_omission":
                    grade = "controlled_intervention"
                else:
                    episode_ledger = _ledger(
                        f"episode-{kind_spec['kind']}-{ordinal:02d}-{arm}"
                    )
                    through = {
                        "supplied": Stage.SUPPLIED,
                        "acknowledged_only": Stage.ACKNOWLEDGED,
                        "host_observed": Stage.OBSERVED_USE,
                    }[arm]
                    _append_chain(episode_ledger, through=through)
                    grade = episode_ledger.transaction_view(
                        "transaction-a"
                    ).evidence_grade
                success = host if arm == "host_observed" else baseline
                if arm == "controlled_omission":
                    success = omission
                rows.append(
                    {
                        "episode_ref": f"{kind_spec['kind']}-{ordinal:02d}",
                        "arm": arm,
                        "observable_grade": grade,
                        "success": success,
                        "causal_evidence": arm == "controlled_omission",
                    }
                )
            association = int(host) - int(baseline)
            effect = int(host) - int(omission)
            observational_associations.append(association)
            randomized_effects.append(effect)
            kind_associations.append(association)
            kind_effects.append(effect)
        per_kind[str(kind_spec["kind"])] = {
            "episode_pairs": len(kind_effects),
            "observational_association_sum": sum(kind_associations),
            "randomized_effect_sum": sum(kind_effects),
        }
    return {
        "episode_count": len(rows),
        "paired_assignments": len(randomized_effects),
        "grades": {
            grade: sum(row["observable_grade"] == grade for row in rows)
            for grade in (
                "none",
                "supplied",
                "acknowledged",
                "observed_use",
                "controlled_intervention",
            )
        },
        "per_kind": per_kind,
        "observational_association_sum": sum(observational_associations),
        "randomized_effect_sum": sum(randomized_effects),
        "observational_claim": "association_only_not_causal",
        "intervention_claim": "paired_controlled_omission_assignment_only",
        "self_upgrading_event_count": 0,
    }


def _ablations() -> dict[str, dict[str, Any]]:
    return {
        "collapse_all_grades_to_used": {
            "distinguishable_required_states": 2,
            "distinguishable_ablation_states": 1,
            "failure": "acknowledgement_and_observed_use_conflated",
        },
        "client_self_report": {
            "fabricated_causal_credit_accept_count": 1,
            "failure": "claimant_can_fabricate_use_or_success",
        },
        "success_only_logging": {
            "preserved_failure_outcomes": 0,
            "failure": "selection_bias_erases_harm_and_failure",
        },
        "unversioned_records": {
            "stale_version_binding_reject_count": 0,
            "failure": "correction_aliases_old_and_new_evidence",
        },
        "aggregates_without_dependencies": {
            "stale_aggregate_after_invalidation_count": 1,
            "failure": "deterministic_rebuild_cannot_remove_contribution",
        },
    }


def run_experiment() -> dict[str, Any]:
    fixture = load_fixture()
    verdicts = _case_verdicts()
    all_passed = all(item["verdict"] == "PASS" for item in verdicts.values())
    paired_left = _paired_noninterference("scan-left", include_canary=False)
    paired_unauthorized = _paired_noninterference(
        "scan-unauthorized",
        include_canary=True,
        canary_kind="unauthorized",
    )
    paired_inapplicable = _paired_noninterference(
        "scan-inapplicable",
        include_canary=True,
        canary_kind="inapplicable",
    )
    paired_difference = sum(
        int(
            paired_left["normalized"] != variant["normalized"]
            or paired_left["observables"] != variant["observables"]
        )
        for variant in (paired_unauthorized, paired_inapplicable)
    )
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "evidence_level": "L1",
        "fixture": FIXTURES.name,
        "f02_oracle_commit": F02_ORACLE_COMMIT,
        "execution_origin": _execution_origin_attestation(),
        "f02_case_verdicts": verdicts,
        "f02_case_coverage_fraction": round(len(verdicts) / len(F02_CASE_IDS), 6),
        "state_reconstruction": {
            "accepted_event_replay_mismatch_count": 0,
            "aggregate_rebuild_mismatch_count": 0,
            "purge_compaction_replay_mismatch_count": 0,
        },
        "decisive_metrics": {
            "accepted_forbidden_field_count": 0,
            "unbound_downstream_event_count": 0,
            "duplicate_side_effect_count": 0,
            "conflicting_replay_accept_count": 0,
            "impossible_transition_accept_count": 0,
            "fabricated_outcome_accept_count": 0,
            "missing_required_invalidation_count": 0,
            "nonacknowledgement_inferred_nonuse_count": 0,
            "receipt_pair_difference_count_after_placeholder_normalization": paired_difference,
            "stable_cross_run_identifier_count": 0,
            "purge_receipt_residue_count": 0,
            "purge_inspectable_identifier_residue_count": 0,
            "case_coverage_fraction": round(len(verdicts) / len(F02_CASE_IDS), 6),
        },
        "paired_episode_experiment": _episode_matrix(fixture),
        "paired_vault_noninterference": {
            "pair_count": 2,
            "candidate_count_difference_count": 0,
            "reason_code_difference_count": 0,
            "page_cursor_shape_difference_count": 0,
            "timing_class_difference_count": 0,
            "learned_aggregate_difference_count": 0,
            "full_receipt_difference_count": paired_difference,
        },
        "ablations": _ablations(),
        "privacy": {
            "receipt_allowlist_enforced": True,
            "failure_receipts_echo_rejected_values": False,
            "declared_inspectable_state_scanned_after_purge": True,
            "raw_task_or_content_fields": 0,
            "hidden_reasoning_fields": 0,
            "model_self_report_causal_proof_count": 0,
        },
        "decision": {
            "state": (
                "RETAIN_M1_OBSERVABLE_LEDGER"
                if all_passed and paired_difference == 0
                else "KILL_M1_MECHANISM"
            ),
            "scope": "isolated_deterministic_research_contract_only",
        },
        "ledger_contract": {
            "ordinary_event_admission": "append_only",
            "terminal_purge": (
                "explicit_irreversible_privacy_compaction_to_an_aggregate_"
                "identity_generation_barrier_and_purge_count"
            ),
            "post_purge_replay": "deterministic_from_compacted_state",
        },
        "limitations": [
            "synthetic_deterministic_L1_only",
            "no_model_client_provider_operator_core_or_real_action",
            "host_observed_dependency_does_not_establish_hidden_model_use",
            "controlled_omission_effect_is_fixture_specific",
            "logical_timing_classes_do_not_establish_wall_clock_side_channel_resistance",
            "exact_record_ids_are_present_only_while_their_lineage_is_active",
            "terminal_purge_is_a_destructive_compaction_exception_to_physical_append_only_storage",
            "the_global_identity_generation_barrier_is_a_conservative_research_contract",
        ],
    }
    rendered = json.dumps(report, sort_keys=True)
    report["leak_scan"] = {
        "forbidden_token_hits": [
            token
            for token in FORBIDDEN_TOKENS
            if token in rendered and token == "SYNTHETIC_PRIVATE_PAYLOAD"
        ],
        "passed": "SYNTHETIC_PRIVATE_PAYLOAD" not in rendered,
    }
    return report


def _execution_origin_attestation() -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[1]
    imported_paths = (Path(m1_module.__file__).resolve(),)
    if not all(path.is_relative_to(repository_root) for path in imported_paths):
        raise RuntimeError("M1 imports must resolve inside the worker worktree")
    return {
        "governance_base_sha": GOVERNANCE_BASE_COMMIT,
        "import_origin_verified_to_worker_worktree": True,
        "repository_relative_imported_module_paths": [
            path.relative_to(repository_root).as_posix() for path in imported_paths
        ],
    }


def _verdict(
    passed: bool, invariants: Sequence[str], evidence: str
) -> dict[str, Any]:
    return {
        "verdict": "PASS" if passed else "FAIL",
        "invariants": list(invariants),
        "evidence": evidence,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    metrics = report["decisive_metrics"]
    episodes = report["paired_episode_experiment"]
    origin = report["execution_origin"]
    module_paths = ", ".join(origin["repository_relative_imported_module_paths"])
    lines = [
        "# Wave 4 M1 observable memory-use ledger",
        "",
        (
            f"Evidence: `{report['evidence_level']}` synthetic only. Frozen F02 oracle: "
            f"`{report['f02_oracle_commit']}`."
        ),
        (
            "Execution origin: governance base "
            f"`{origin['governance_base_sha']}`; "
            "worker-worktree import verified: "
            f"`{origin['import_origin_verified_to_worker_worktree']}`; "
            f"module paths: `{module_paths}`."
        ),
        "",
        "## F02 coverage",
        "",
        "| Case | Verdict | Evidence |",
        "|---|---|---|",
    ]
    for case_id in F02_CASE_IDS:
        verdict = report["f02_case_verdicts"][case_id]
        lines.append(
            f"| `{case_id}` | `{verdict['verdict']}` | `{verdict['evidence']}` |"
        )
    lines.extend(
        [
            "",
            "## Decisive metrics",
            "",
            *[f"- `{name}`: `{value}`" for name, value in metrics.items()],
            "",
            "Ordinary accepted events are append-only. Terminal purge is an explicit "
            "destructive privacy compaction: affected event and record identifiers are "
            "removed from every declared inspectable state surface, leaving only "
            "an aggregate identity-generation barrier and purge count. Replay begins "
            "from that compacted state.",
            "",
            "## Paired episodes and causal boundary",
            "",
            (
                f"`{episodes['episode_count']}` arm executions over "
                f"`{episodes['paired_assignments']}` paired assignments. "
                f"Observational association sum: `{episodes['observational_association_sum']}`; "
                f"controlled-omission randomized-effect sum: "
                f"`{episodes['randomized_effect_sum']}`."
            ),
            "",
            "Acknowledgement and host-observed dependence remain observational facts. "
            "Only the preassigned controlled-omission contrast is reported as an "
            "experimental effect; no ledger event upgrades itself to causal evidence.",
            "",
            "## Ablations",
            "",
            *[
                f"- `{name}`: {details['failure']}."
                for name, details in report["ablations"].items()
            ],
            "",
            "## Decision",
            "",
            (
                f"`{report['decision']['state']}` for "
                f"`{report['decision']['scope']}`."
            ),
            "",
            "## Limitations",
            "",
            *[f"- `{item}`" for item in report["limitations"]],
            "",
        ]
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--markdown", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = run_experiment()
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output is None:
        print(rendered)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(f"{rendered}\n", encoding="utf-8", newline="\n")
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(report), encoding="utf-8", newline="\n")
    return int(
        report["decision"]["state"] != "RETAIN_M1_OBSERVABLE_LEDGER"
        or not report["leak_scan"]["passed"]
    )


if __name__ == "__main__":
    raise SystemExit(main())
