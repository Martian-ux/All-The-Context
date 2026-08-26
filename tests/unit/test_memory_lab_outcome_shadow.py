from __future__ import annotations

from dataclasses import replace

import pytest
from allthecontext.memory_lab_outcome_shadow import (
    MAX_REPAIR_TESTS,
    Acknowledgement,
    ActionEnvelope,
    ApplicabilityBoundary,
    CompletionStatus,
    ContextAssignment,
    CorrectionDisposition,
    DeclaredUse,
    DependencyKind,
    DependencyRef,
    EnvelopeKind,
    EvidenceSource,
    ExternalResult,
    InvalidationReason,
    OutcomeReceipt,
    OutcomeReceiptLedger,
    OutcomeStatus,
    ProposalReason,
    ProposalStatus,
    PurgeClosure,
    RepairTest,
    UserCorrection,
    VerificationStrength,
    propose_procedure,
    receipt_mapping,
    serialize_receipt,
    validate_receipt_mapping,
)


def _assignment(receipt_id: str) -> ContextAssignment:
    return ContextAssignment(
        assignment_id=f"assignment-{receipt_id}",
        project_id="project-alpha",
        project_version=3,
        projection_id="projection-alpha",
        projection_version=7,
        issue_receipt_id=f"issue-{receipt_id}",
        memory_versions=(DependencyRef(DependencyKind.MEMORY, f"memory-{receipt_id}", 2),),
        source_dependencies=(DependencyRef(DependencyKind.SOURCE, f"source-{receipt_id}", 1),),
        applicability_key="task-class-alpha",
        time_bucket="2026-08-25T22",
    )


def _receipt(
    receipt_id: str,
    *,
    verification: VerificationStrength = VerificationStrength.OBSERVED,
    action: str = "write",
    correction: UserCorrection | None = None,
) -> OutcomeReceipt:
    assignment = _assignment(receipt_id)
    return OutcomeReceipt(
        receipt_id=receipt_id,
        receipt_version=1,
        task_id=f"task-{receipt_id}",
        task_kind="task-alpha",
        assignment=assignment,
        acknowledgement=Acknowledgement.ACKNOWLEDGED,
        declared_use=DeclaredUse.USED,
        action_envelopes=(
            ActionEnvelope(1, EnvelopeKind.TOOL, "read", "bounded-record", OutcomeStatus.SUCCEEDED),
            ActionEnvelope(
                2, EnvelopeKind.ACTION, action, "bounded-record", OutcomeStatus.SUCCEEDED
            ),
        ),
        completion=CompletionStatus.COMPLETED,
        external_result=ExternalResult(
            OutcomeStatus.SUCCEEDED,
            EvidenceSource.OUTCOME_ADAPTER,
            verification,
            "task-success",
        ),
        user_correction=correction,
    )


def _closure(receipts: tuple[OutcomeReceipt, ...]) -> PurgeClosure:
    dependencies = tuple(
        dict.fromkeys(
            dependency
            for receipt in receipts
            for dependency in (*receipt.assignment.dependency_refs, receipt.outcome_dependency)
        )
    )
    return PurgeClosure(dependencies, closed=True)


def _proposal_kwargs(receipts: tuple[OutcomeReceipt, ...]) -> dict[str, object]:
    return {
        "proposal_id": "proposal-alpha",
        "applicability": ApplicabilityBoundary(
            "project-alpha", "task-alpha", ("precondition-record-current",)
        ),
        "negative_guards": ("guard-record-stale", "guard-permission-missing"),
        "repair_tests": (RepairTest("repair-delete", InvalidationReason.ORDINARY_DELETE, True),),
        "purge_closure": _closure(receipts),
    }


def test_receipt_mapping_is_allowlisted_and_deterministic() -> None:
    receipt = _receipt("one")

    mapping = receipt_mapping(receipt)
    validate_receipt_mapping(mapping)
    assert "raw_context" not in serialize_receipt(receipt)
    assert serialize_receipt(receipt) == serialize_receipt(receipt)
    assert mapping["schema_version"] == 1
    assert mapping["assignment"]["project_version"] == 3
    assert mapping["assignment"]["projection_version"] == 7
    assert mapping["declared_use"] == "used"

    forbidden = dict(mapping)
    forbidden_assignment = dict(forbidden["assignment"])
    forbidden_assignment["raw_context"] = "private"
    forbidden["assignment"] = forbidden_assignment
    with pytest.raises(ValueError, match="forbidden field"):
        validate_receipt_mapping(forbidden)


def test_receipt_ledger_is_idempotent_and_closes_correction_and_purge() -> None:
    receipt = _receipt("one")
    ledger = OutcomeReceiptLedger(run_id="test-run")

    assert ledger.append(receipt).status.value == "accepted"
    assert ledger.append(receipt).status.value == "idempotent"
    conflict = replace(receipt, task_id="different-task")
    assert ledger.append(conflict).status.value == "rejected"
    assert ledger.active_receipts == (receipt,)

    source = receipt.assignment.source_dependencies[0]
    mutation = ledger.invalidate(source, reason=InvalidationReason.CORRECTION)
    assert mutation.affected_count == 1
    assert ledger.active_receipts == ()

    purge = ledger.purge(receipt.assignment.memory_versions[0])
    assert purge.reason is InvalidationReason.TERMINAL_PURGE
    assert purge.affected_count == 1
    assert ledger.receipts == ()
    assert "memory-one" not in str(ledger.inspectable_state())

    rejected = ledger.append(receipt)
    assert rejected.failure is not None
    assert rejected.failure.failure_code.value == "purged_dependency"


def test_one_successful_looking_trace_does_not_create_a_proposal() -> None:
    receipt = _receipt("one")
    decision = propose_procedure((receipt,), **_proposal_kwargs((receipt,)))

    assert decision.status is ProposalStatus.REJECTED
    assert decision.proposal is None
    assert ProposalReason.RECURRENCE_OR_STRONG_VERIFICATION_REQUIRED in decision.reasons
    assert decision.shadow_only is True


def test_recurrence_with_same_action_signature_creates_advisory_proposal() -> None:
    receipts = (_receipt("one"), _receipt("two"))
    decision = propose_procedure(receipts, **_proposal_kwargs(receipts))

    assert decision.status is ProposalStatus.PROPOSED
    assert decision.reasons == ()
    assert decision.proposal is not None
    assert decision.proposal.supporting_receipt_ids == ("one", "two")
    assert decision.proposal.recurrence_count == 2
    assert decision.proposal.influence_dependencies
    assert not hasattr(decision.proposal, "source_dependencies")
    assert decision.proposal.advisory_only is True
    assert decision.shadow_only is True


def test_one_strong_external_result_can_satisfy_recurrence_gate() -> None:
    receipt = _receipt("one", verification=VerificationStrength.STRONG)
    decision = propose_procedure((receipt,), **_proposal_kwargs((receipt,)))

    assert decision.status is ProposalStatus.PROPOSED
    assert decision.recurrence_count == 1
    assert decision.strong_external_verification_count == 1


def test_correction_or_invalidation_cannot_support_learning() -> None:
    corrected = _receipt(
        "one",
        correction=UserCorrection(
            "correction-one",
            _assignment("one").source_dependencies[0],
            disposition=CorrectionDisposition.INVALIDATED,
            reason_code="user-correction",
        ),
    )
    good = _receipt("two")
    decision = propose_procedure(
        (corrected, good),
        **_proposal_kwargs((corrected, good)),
        invalidated_dependencies=frozenset({good.assignment.memory_versions[0]}),
    )

    assert decision.status is ProposalStatus.REJECTED
    assert decision.recurrence_count == 0
    assert ProposalReason.NO_OBSERVABLE_SUCCESS in decision.reasons


def test_action_disagreement_and_missing_purge_closure_fail_closed() -> None:
    first = _receipt("one")
    second = _receipt("two", action="delete")
    kwargs = _proposal_kwargs((first, second))
    kwargs["purge_closure"] = PurgeClosure((), closed=True)
    decision = propose_procedure((first, second), **kwargs)

    assert decision.status is ProposalStatus.REJECTED
    assert ProposalReason.ACTION_SIGNATURE_DISAGREEMENT in decision.reasons
    assert ProposalReason.PURGE_CLOSURE_REQUIRED in decision.reasons


def test_identical_duplicate_receipt_input_cannot_satisfy_recurrence() -> None:
    receipt = _receipt("one")
    decision = propose_procedure((receipt, receipt), **_proposal_kwargs((receipt,)))

    assert decision.status is ProposalStatus.REJECTED
    assert decision.recurrence_count == 0
    assert ProposalReason.DUPLICATE_RECEIPT_INPUT in decision.reasons


def test_conflicting_duplicate_receipt_ids_fail_closed_before_learning() -> None:
    receipt = _receipt("one")
    conflicting = replace(receipt, task_id="task-conflict")
    decision = propose_procedure(
        (receipt, conflicting), **_proposal_kwargs((receipt, conflicting))
    )

    assert decision.status is ProposalStatus.REJECTED
    assert decision.proposal is None
    assert ProposalReason.DUPLICATE_RECEIPT_CONFLICT in decision.reasons


def test_distinct_receipts_for_one_task_are_not_independent_recurrence() -> None:
    first = _receipt("one")
    second = replace(_receipt("two"), task_id=first.task_id)
    decision = propose_procedure(
        (first, second), **_proposal_kwargs((first, second))
    )

    assert decision.status is ProposalStatus.REJECTED
    assert decision.recurrence_count == 1
    assert ProposalReason.NON_INDEPENDENT_TASK_EVIDENCE in decision.reasons


@pytest.mark.parametrize(
    "factory",
    [
        lambda: DependencyRef("memory", "memory-one", 1),
        lambda: ActionEnvelope(1, "tool", "read", "bounded-record"),
        lambda: ActionEnvelope(
            1, EnvelopeKind.TOOL, "read", "bounded-record", "succeeded"
        ),
        lambda: ExternalResult(
            "succeeded", EvidenceSource.OUTCOME_ADAPTER, VerificationStrength.OBSERVED, "ok"
        ),
        lambda: ExternalResult(
            OutcomeStatus.SUCCEEDED, "outcome_adapter", VerificationStrength.OBSERVED, "ok"
        ),
        lambda: ExternalResult(
            OutcomeStatus.SUCCEEDED,
            EvidenceSource.OUTCOME_ADAPTER,
            "observed",
            "ok",
        ),
        lambda: UserCorrection(
            "correction-one",
            DependencyRef(DependencyKind.MEMORY, "memory-one", 1),
            "invalidated",
            "user-correction",
        ),
        lambda: replace(_receipt("one"), acknowledgement="acknowledged"),
        lambda: replace(_receipt("one"), declared_use="used"),
        lambda: replace(_receipt("one"), completion="completed"),
        lambda: RepairTest("repair-one", "ordinary_delete", True),
    ],
)
def test_raw_enum_values_are_rejected_at_runtime(factory: object) -> None:
    with pytest.raises(ValueError):
        factory()  # type: ignore[operator]


def test_non_bool_decision_fields_are_rejected_at_runtime() -> None:
    with pytest.raises(ValueError, match="repair test passed"):
        RepairTest("repair-one", InvalidationReason.ORDINARY_DELETE, 1)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="purge closure closed"):
        PurgeClosure((), closed=1)  # type: ignore[arg-type]


def test_invalid_lifecycle_enum_and_client_strong_verification_fail_closed() -> None:
    with pytest.raises(ValueError, match="invalidation reason"):
        OutcomeReceiptLedger(run_id="test-run").invalidate(
            _receipt("one").assignment.memory_versions[0], reason="correction"
        )  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="verification strength"):
        ExternalResult(
            OutcomeStatus.SUCCEEDED,
            EvidenceSource.OUTCOME_ADAPTER,
            "strong",
            "ok",
        )


def test_unknown_action_status_cannot_support_procedural_learning() -> None:
    receipt = _receipt("one", verification=VerificationStrength.STRONG)
    unknown_action = replace(
        receipt,
        action_envelopes=(ActionEnvelope(1, EnvelopeKind.ACTION, "write", "bounded-record"),),
    )
    decision = propose_procedure(
        (unknown_action,), **_proposal_kwargs((unknown_action,))
    )

    assert decision.status is ProposalStatus.REJECTED
    assert ProposalReason.NO_OBSERVABLE_SUCCESS in decision.reasons


@pytest.mark.parametrize(
    "bad_token",
    ["plain text", "café", r"C:\\private\\file.txt", "../traversal", "line\nfeed"],
)
def test_machine_token_grammar_rejects_content_unicode_paths_and_controls(
    bad_token: str,
) -> None:
    with pytest.raises(ValueError, match="ASCII machine-token grammar"):
        DependencyRef(DependencyKind.MEMORY, bad_token, 1)


def test_machine_token_grammar_preserves_timestamp_and_code_punctuation() -> None:
    assignment = replace(_assignment("one"), time_bucket="2026-08-25T22:00")
    receipt = replace(_receipt("one"), assignment=assignment)

    assert receipt.assignment.time_bucket == "2026-08-25T22:00"
    assert receipt.external_result is not None
    assert replace(receipt.external_result, result_code="result:v1.2").result_code == "result:v1.2"


def test_repair_collection_is_bounded_before_rejection_iteration() -> None:
    receipt = _receipt("one")
    kwargs = _proposal_kwargs((receipt,))
    kwargs["repair_tests"] = tuple(
        RepairTest(f"repair-{index}", InvalidationReason.ORDINARY_DELETE, True)
        for index in range(MAX_REPAIR_TESTS + 1)
    )

    with pytest.raises(ValueError, match="repair_tests exceed"):
        propose_procedure((receipt,), **kwargs)


def test_candidate_guards_and_invalidations_reject_duplicate_identity() -> None:
    receipt = _receipt("one")
    kwargs = _proposal_kwargs((receipt,))
    kwargs["negative_guards"] = ("guard-record-stale", "guard-record-stale")
    with pytest.raises(ValueError, match="negative guards must be unique"):
        propose_procedure((receipt,), **kwargs)

    with pytest.raises(ValueError, match="invalidated dependencies must be unique"):
        propose_procedure(
            (receipt,),
            **_proposal_kwargs((receipt,)),
            invalidated_dependencies=(
                receipt.assignment.memory_versions[0],
                receipt.assignment.memory_versions[0],
            ),
        )


def test_direct_proposal_requires_purge_coverage() -> None:
    first, second = _receipt("one"), _receipt("two")
    decision = propose_procedure(
        (first, second), **_proposal_kwargs((first, second))
    )
    assert decision.proposal is not None
    with pytest.raises(ValueError, match="purge closure must cover proposal dependencies"):
        replace(decision.proposal, purge_closure=PurgeClosure((), closed=True))
