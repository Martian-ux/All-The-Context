from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest
from allthecontext.memory_lab_m1 import (
    AdmissionStatus,
    CanonicalRecord,
    FailureClass,
    InvalidationReason,
    ObservableSource,
    OutcomeStatus,
    Stage,
    serialize_failure,
)

from bench.memory_lab_m1 import (
    F02_CASE_IDS,
    _append_chain,
    _event,
    _ledger,
    run_experiment,
)


def test_exact_observable_chain_replays_and_reconstructs() -> None:
    ledger = _ledger()
    _append_chain(ledger)

    replay = ledger.replay()

    assert replay.events == ledger.events
    assert replay.rebuild_aggregates() == ledger.rebuild_aggregates()
    view = replay.transaction_view("transaction-a")
    assert view.stages == (
        Stage.ASSIGNED,
        Stage.SUPPLIED,
        Stage.ACKNOWLEDGED,
        Stage.OBSERVED_USE,
        Stage.ACTION,
        Stage.OUTCOME,
    )
    assert view.evidence_grade == "outcome"


def test_acknowledgement_and_use_are_independent_observations() -> None:
    without_ack = _ledger()
    _append_chain(
        without_ack,
        through=Stage.ACTION,
        include_acknowledgement=False,
    )
    acknowledged_only = _ledger("ack-only")
    _append_chain(acknowledged_only, through=Stage.ACKNOWLEDGED)

    first = without_ack.transaction_view("transaction-a")
    second = acknowledged_only.transaction_view("transaction-a")
    assert first.acknowledgement == "not_observed"
    assert first.observed_use == "observed"
    assert second.acknowledgement == "observed"
    assert second.observed_use == "not_observed"
    assert "not_used" not in repr(first)
    assert "not_used" not in repr(second)


def test_external_receipts_use_the_frozen_f02_allowlist() -> None:
    ledger = _ledger()
    _append_chain(ledger, through=Stage.OUTCOME)

    receipt = ledger.normalized_receipts()[-1]

    assert set(receipt) == {
        "schema_version",
        "per_run_receipt_id",
        "event_id",
        "transaction_id",
        "stage",
        "canonical_record_id",
        "canonical_record_version",
        "issue_receipt_id",
        "policy_generation",
        "principal_capability_view_id",
        "causal_predecessor_event_ids",
        "event_time_bucket",
        "observable_source_type",
        "action_type_enum",
        "outcome_status_enum",
        "invalidation_reason_enum",
    }


def test_identical_retry_is_idempotent_but_conflict_fails_closed() -> None:
    ledger = _ledger()
    ledger.register_record(CanonicalRecord("record-a", 2, "lineage-record-a"))
    _append_chain(ledger, through=Stage.OBSERVED_USE)
    original = asdict(ledger.events[-1])

    identical = ledger.append(original)
    conflict_payload = dict(original)
    conflict_payload["canonical_record_version"] = 2
    conflict = ledger.append(conflict_payload)

    assert identical.status is AdmissionStatus.IDEMPOTENT
    assert conflict.status is AdmissionStatus.REJECTED
    assert conflict.failure is not None
    assert conflict.failure.failure_class_enum is FailureClass.DUPLICATE_CONFLICT
    assert ledger.rebuild_aggregates().observed_use == 1


def test_unknown_parents_and_impossible_transitions_have_no_side_effect() -> None:
    ledger = _ledger()
    assert ledger.append(_event(Stage.ASSIGNED, "assigned")).status is AdmissionStatus.ACCEPTED
    assert (
        ledger.append(_event(Stage.SUPPLIED, "supplied", parents=("assigned",))).status
        is AdmissionStatus.ACCEPTED
    )
    before = ledger.events

    attempts = (
        _event(
            Stage.OUTCOME,
            "outcome",
            parents=("supplied",),
            outcome=OutcomeStatus.SUCCEEDED,
        ),
        _event(Stage.ACTION, "action", parents=("supplied",), action="read"),
        _event(Stage.OBSERVED_USE, "use", parents=("unknown",)),
    )

    assert all(ledger.append(attempt).status is AdmissionStatus.REJECTED for attempt in attempts)
    assert ledger.events == before


def test_client_cannot_fabricate_outcome_or_causal_credit() -> None:
    ledger = _ledger()
    ids = _append_chain(ledger, through=Stage.ACTION)

    fabricated = ledger.append(
        _event(
            Stage.OUTCOME,
            "fabricated",
            parents=(ids[-1],),
            source=ObservableSource.CLIENT_TRANSPORT,
            outcome=OutcomeStatus.SUCCEEDED,
        )
    )
    independent = ledger.append(
        _event(
            Stage.OUTCOME,
            "independent",
            parents=(ids[-1],),
            outcome=OutcomeStatus.FAILED,
        )
    )

    assert fabricated.status is AdmissionStatus.REJECTED
    assert fabricated.failure is not None
    assert fabricated.failure.failure_class_enum is FailureClass.FABRICATED_OUTCOME
    assert independent.status is AdmissionStatus.ACCEPTED


@pytest.mark.parametrize(
    "reason",
    [
        InvalidationReason.CORRECTION,
        InvalidationReason.SCOPE_NARROWING,
        InvalidationReason.PERMISSION_REVOCATION,
        InvalidationReason.ORDINARY_DELETE,
        InvalidationReason.TERMINAL_PURGE,
    ],
)
def test_lifecycle_mutations_explicitly_invalidate_open_transactions(
    reason: InvalidationReason,
) -> None:
    ledger = _ledger()
    _append_chain(ledger, through=Stage.SUPPLIED)

    admissions = ledger.invalidate_record(
        record_id="record-a",
        version=1,
        reason=reason,
        event_time_bucket="logical-2",
    )

    assert len(admissions) == 1
    assert admissions[0].status is AdmissionStatus.ACCEPTED
    if reason is InvalidationReason.TERMINAL_PURGE:
        assert admissions[0].event is None
        with pytest.raises(KeyError):
            ledger.transaction_view("transaction-a")
        return
    view = ledger.transaction_view("transaction-a")
    assert view.terminal_state == "invalidated"
    assert view.invalidation_reason is reason


def test_terminal_purge_delinks_all_old_receipts_and_aggregate_influence() -> None:
    ledger = _ledger()
    record_id = "record-a"
    lineage_id = "lineage-record-a"
    transaction_ids = ("t1", "t2")
    issue_ids = ("issue-t1", "issue-t2")
    _append_chain(ledger, through=Stage.ACTION, transaction_id="t1", issue="issue-t1")
    ledger.invalidate_record(
        record_id="record-a",
        version=1,
        reason=InvalidationReason.ORDINARY_DELETE,
        event_time_bucket="logical-2",
    )
    _append_chain(ledger, through=Stage.ACTION, transaction_id="t2", issue="issue-t2")

    ledger.invalidate_record(
        record_id="record-a",
        version=1,
        reason=InvalidationReason.TERMINAL_PURGE,
        event_time_bucket="logical-3",
    )

    assert ledger.normalized_receipts() == ()
    aggregate = ledger.rebuild_aggregates()
    assert aggregate.observed_use == 0
    assert aggregate.action == 0
    assert aggregate.purge_count == 1
    inspectable = json.dumps(ledger.inspectable_state(), sort_keys=True, default=str)
    assert record_id not in inspectable
    assert lineage_id not in inspectable
    assert all(identifier not in inspectable for identifier in transaction_ids)
    assert all(identifier not in inspectable for identifier in issue_ids)
    assert "snapshot-1" not in inspectable
    assert "principal-view-1" not in inspectable
    assert "t1-assigned" not in inspectable
    with pytest.raises(ValueError, match="purged identity"):
        ledger.register_record(CanonicalRecord("record-a", 2, "lineage-record-a"))


def test_policy_generation_invalidates_and_rejects_late_events() -> None:
    ledger = _ledger()
    _append_chain(ledger, through=Stage.SUPPLIED, policy=2)

    admissions = ledger.advance_policy_generation(
        old_generation=2,
        event_time_bucket="logical-2",
    )
    late = ledger.append(
        _event(
            Stage.ACKNOWLEDGED,
            "late",
            policy=2,
            parents=("transaction-a-supplied",),
        )
    )

    assert len(admissions) == 1
    assert late.status is AdmissionStatus.REJECTED
    assert late.failure is not None
    assert late.failure.failure_class_enum is FailureClass.INVALIDATED_TRANSACTION


@pytest.mark.parametrize(
    "field",
    [
        "raw_context",
        "raw_prompt",
        "raw_supplied_context",
        "chain_of_thought",
        "hidden_reasoning",
        "stable_content_hash",
        "model_self_report",
    ],
)
def test_forbidden_fields_are_rejected_without_value_echo(field: str) -> None:
    ledger = _ledger()
    payload = _event(Stage.ASSIGNED, "assigned")
    payload[field] = "SYNTHETIC_PRIVATE_PAYLOAD"

    admission = ledger.append(payload, case_id="M1-C14")

    assert admission.status is AdmissionStatus.REJECTED
    assert admission.failure is not None
    rendered = serialize_failure(admission.failure)
    assert "SYNTHETIC_PRIVATE_PAYLOAD" not in rendered
    assert field not in rendered


def test_paired_vault_noninterference_covers_every_closed_channel() -> None:
    report = run_experiment()
    paired = report["paired_vault_noninterference"]

    assert paired == {
        "pair_count": 2,
        "candidate_count_difference_count": 0,
        "reason_code_difference_count": 0,
        "page_cursor_shape_difference_count": 0,
        "timing_class_difference_count": 0,
        "learned_aggregate_difference_count": 0,
        "full_receipt_difference_count": 0,
    }


def test_all_f02_cases_and_required_zero_metrics_pass() -> None:
    report = run_experiment()

    assert report["execution_origin"] == {
        "governance_base_sha": "f545c37157845f0bd402215719cb8c747b7fc21d",
        "import_origin_verified_to_worker_worktree": True,
        "repository_relative_imported_module_paths": [
            "packages/allthecontext/src/allthecontext/memory_lab_m1.py"
        ],
    }
    assert set(report["f02_case_verdicts"]) == set(F02_CASE_IDS)
    assert all(item["verdict"] == "PASS" for item in report["f02_case_verdicts"].values())
    assert report["f02_case_coverage_fraction"] == 1.0
    for name, value in report["decisive_metrics"].items():
        if name.endswith("_count"):
            assert value == 0
    assert report["decision"]["state"] == "RETAIN_M1_OBSERVABLE_LEDGER"


def test_paired_episodes_separate_association_from_randomized_effect() -> None:
    experiment = run_experiment()["paired_episode_experiment"]

    assert experiment["episode_count"] == 200
    assert experiment["paired_assignments"] == 40
    assert experiment["observational_claim"] == "association_only_not_causal"
    assert experiment["intervention_claim"] == ("paired_controlled_omission_assignment_only")
    assert experiment["self_upgrading_event_count"] == 0
    assert experiment["randomized_effect_sum"] == 0


def test_checked_in_report_is_private_complete_and_preserves_ablations() -> None:
    report_path = Path(__file__).parents[2] / "bench" / "reports" / "memory_lab_m1_wave4.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["schema"] == "atc.memory-lab.m1-report.v1"
    assert report["leak_scan"]["passed"] is True
    assert report["privacy"]["raw_task_or_content_fields"] == 0
    assert report["privacy"]["hidden_reasoning_fields"] == 0
    assert set(report["ablations"]) == {
        "collapse_all_grades_to_used",
        "client_self_report",
        "success_only_logging",
        "unversioned_records",
        "aggregates_without_dependencies",
    }
