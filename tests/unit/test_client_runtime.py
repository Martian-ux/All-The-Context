"""Focused synthetic tests for the experimental client runtime v0 contract."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any, cast

import pytest
from allthecontext.client_runtime import (
    ALL_LIFECYCLE_HOOKS,
    CONSEQUENCE_CHECKPOINT_KINDS,
    MAX_REFERENCE_BYTES,
    ClientLifecycleEnvelope,
    ClientRuntimeAdapterV0,
    ClientRuntimeCapabilities,
    ClientRuntimeContractError,
    ClientRuntimeReferenceHostV0,
    CompactionTaskCheckpointPayload,
    ConsequenceCheckpointPayload,
    ContextDeliveryReceipt,
    ContextRequestPayload,
    DeterministicFakeClientRuntimeHost,
    Diagnostic,
    DirectUserTurnPayload,
    EvidenceBoundaryError,
    GenerationReceipt,
    HookResult,
    ModelProviderSelfAttestation,
    OrderingViolation,
    PayloadReference,
    ReferenceKind,
    ResponseEmissionPayload,
    RestartSessionTransitionPayload,
    ToolObservableResultPayload,
    UnsupportedHookReport,
)


def reference(reference: str, kind: ReferenceKind) -> PayloadReference:
    return PayloadReference(reference=reference, kind=kind)


def test_capability_profiles_are_explicit_and_never_claim_provider_or_sdk_support() -> None:
    expected = {
        "L0": {
            "manual_context_request": "best_effort",
            "pre_generation_context_request": "unsupported",
            "direct_user_turn": "unsupported",
            "tool_observable_result": "unsupported",
            "response_emission": "unsupported",
            "compaction_task_checkpoint": "unsupported",
            "restart_session_transition": "unsupported",
            "completion_abandonment": "unsupported",
            "consequence_checkpoint": "unsupported",
        },
        "L1": {
            "manual_context_request": "best_effort",
            "pre_generation_context_request": "supported",
            "direct_user_turn": "supported",
            "tool_observable_result": "unsupported",
            "response_emission": "unsupported",
            "compaction_task_checkpoint": "unsupported",
            "restart_session_transition": "unsupported",
            "completion_abandonment": "unsupported",
            "consequence_checkpoint": "unsupported",
        },
        "L2": {
            "manual_context_request": "best_effort",
            "pre_generation_context_request": "supported",
            "direct_user_turn": "supported",
            "tool_observable_result": "supported",
            "response_emission": "supported",
            "compaction_task_checkpoint": "supported",
            "restart_session_transition": "supported",
            "completion_abandonment": "supported",
            "consequence_checkpoint": "unsupported",
        },
        "L3": {
            hook: "best_effort" if hook == "manual_context_request" else "supported"
            for hook in ALL_LIFECYCLE_HOOKS
        },
    }

    for level, statuses in expected.items():
        capabilities = ClientRuntimeCapabilities.for_level(level)  # type: ignore[arg-type]
        assert tuple(item.hook for item in capabilities.hooks) == ALL_LIFECYCLE_HOOKS
        assert {item.hook: item.status for item in capabilities.hooks} == statuses
        assert capabilities.provider_support_claim is False
        assert capabilities.stable_sdk_claim is False
        assert capabilities.as_dict()["provider_support_claim"] is False
        assert capabilities.as_dict()["stable_sdk_claim"] is False


def test_capabilities_reject_overstatement_and_non_misleading_metadata() -> None:
    l1 = ClientRuntimeCapabilities.for_level("L1")
    l1_hooks = list(l1.hooks)
    tool_index = next(
        index
        for index, capability in enumerate(l1_hooks)
        if capability.hook == "tool_observable_result"
    )
    l1_hooks[tool_index] = replace(
        l1_hooks[tool_index],
        status="supported",
        ordering="monotonic_sequence",
    )
    with pytest.raises(ClientRuntimeContractError):
        ClientRuntimeCapabilities(level="L1", hooks=tuple(l1_hooks))

    l3 = ClientRuntimeCapabilities.for_level("L3")
    l3_hooks = list(l3.hooks)
    consequence_index = next(
        index
        for index, capability in enumerate(l3_hooks)
        if capability.hook == "consequence_checkpoint"
    )
    l3_hooks[consequence_index] = replace(
        l3_hooks[consequence_index],
        status="unsupported",
        ordering="none",
        supported_consequence_kinds=(),
    )
    under_declared = ClientRuntimeCapabilities(level="L3", hooks=tuple(l3_hooks))
    assert under_declared.for_hook("consequence_checkpoint").status == "unsupported"

    with pytest.raises(ClientRuntimeContractError):
        replace(
            l3.for_hook("pre_generation_context_request"),
            minimum_level="L0",
        )
    with pytest.raises(ClientRuntimeContractError):
        replace(
            l3.for_hook("direct_user_turn"),
            ordering="before_generation",
        )
    with pytest.raises(ClientRuntimeContractError):
        replace(
            l3.for_hook("consequence_checkpoint"),
            status="unsupported",
            ordering="none",
            supported_consequence_kinds=("task_outcome_observed",),
        )
    with pytest.raises(ClientRuntimeContractError):
        replace(
            l3.for_hook("manual_context_request"),
            status="supported",
        )
    with pytest.raises(ClientRuntimeContractError):
        replace(
            l3.for_hook("consequence_checkpoint"),
            supported_consequence_kinds=(
                "task_outcome_observed",
                "task_outcome_observed",
            ),
        )


def test_l1_context_delivery_is_proven_to_precede_generation() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L1")
    request = host.request_pre_generation_context(
        generation_id="generation-1",
        requested_scopes=("project/demo",),
        budget_chars=2_000,
    )
    assert isinstance(request, ClientLifecycleEnvelope)
    assert isinstance(request.payload, ContextRequestPayload)

    with pytest.raises(OrderingViolation):
        host.begin_generation(generation_id="generation-1")

    delivery = host.deliver_context(
        request,
        (reference("context-pack-1", "context_pack"),),
    )
    generation = host.begin_generation(generation_id="generation-1")

    assert generation == GenerationReceipt(
        generation_id="generation-1",
        generation_sequence=3,
        context_request_event_id="evt-000001",
        context_delivery_sequence=2,
        pre_generation_delivery=True,
    )
    assert isinstance(delivery, ContextDeliveryReceipt)
    assert delivery.delivered_before_generation is True
    assert delivery.delivery_sequence < generation.generation_sequence
    assert [entry.action for entry in host.trace] == [
        "context_delivered",
        "generation_started",
    ]


def test_fake_host_rejects_duplicate_pending_context_requests() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L1")
    first = host.request_pre_generation_context(generation_id="generation-1")
    assert isinstance(first, ClientLifecycleEnvelope)

    with pytest.raises(OrderingViolation):
        host.request_pre_generation_context(
            generation_id="generation-1",
            requested_scopes=("project/other",),
        )
    assert host.events == (first,)


def test_session_transition_requires_valid_combinations_and_current_predecessor() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L2")
    current = host.current_session_id

    with pytest.raises(ClientRuntimeContractError):
        host.record_session_transition(transition="restart", next_session_id="session-2")
    with pytest.raises(ClientRuntimeContractError):
        host.record_session_transition(
            transition="session_transition",
            previous_session_id=current,
        )
    with pytest.raises(OrderingViolation):
        host.record_session_transition(
            transition="restart",
            previous_session_id="wrong-session",
            next_session_id="session-2",
        )
    assert host.current_session_id == current
    assert host.events == ()

    result = host.record_session_transition(
        transition="restart",
        previous_session_id=current,
        next_session_id="session-2",
    )
    assert isinstance(result, ClientLifecycleEnvelope)
    assert host.current_session_id == "session-2"
    with pytest.raises(OrderingViolation):
        host.record_session_transition(
            transition="restart",
            previous_session_id=current,
            next_session_id="session-3",
        )
    assert host.current_session_id == "session-2"


def emit_identifier_rich_hooks(
    adapter: ClientRuntimeAdapterV0,
) -> tuple[HookResult, HookResult]:
    request = adapter.request_pre_generation_context(
        generation_id="generation-identifiers",
        conversation_id="conversation-1",
        task_id="task-1",
        workspace_id="workspace-1",
        project_id="project-1",
    )
    direct = adapter.observe_direct_user_turn(
        reference("turn-identifiers", "user_turn"),
        conversation_id="conversation-1",
        task_id="task-1",
    )
    return request, direct


def test_adapter_protocol_expresses_identifier_rich_hooks() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L3")

    request, direct = emit_identifier_rich_hooks(host)

    assert isinstance(request, ClientLifecycleEnvelope)
    assert request.conversation_id == "conversation-1"
    assert request.task_id == "task-1"
    assert request.workspace_id == "workspace-1"
    assert request.project_id == "project-1"
    assert isinstance(direct, ClientLifecycleEnvelope)
    assert direct.conversation_id == "conversation-1"
    assert direct.task_id == "task-1"


def test_unsupported_hooks_are_reported_without_inference_or_event_creation() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L1")

    result = host.observe_tool_result(
        tool_name="synthetic-tool",
        result_ref=reference("tool-result-1", "tool_result"),
    )
    transition = host.record_session_transition(transition="restart")

    assert isinstance(result, UnsupportedHookReport)
    assert isinstance(transition, UnsupportedHookReport)
    assert result.status == "unsupported"
    assert result.required_level == "L2"
    assert transition.required_level == "L2"
    assert host.events == ()

    manual = host.request_manual_context(requested_scopes=("project/demo",))
    assert isinstance(manual, ClientLifecycleEnvelope)
    assert manual.hook == "manual_context_request"
    assert isinstance(manual.payload, ContextRequestPayload)
    assert manual.payload.delivery_mode == "manual"


def test_direct_user_witness_is_not_substituted_by_model_provider_self_attestation() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L1")
    direct = host.observe_direct_user_turn(reference("turn-1", "user_turn"))
    assert isinstance(direct, ClientLifecycleEnvelope)
    assert direct.witness == "direct_user"
    assert isinstance(direct.payload, DirectUserTurnPayload)
    assert direct.payload.evidence_role == "direct_user_statement"

    report = host.observe_user_turn_attestation(
        ModelProviderSelfAttestation(reference("attestation-1", "attestation"))
    )
    assert report.status == "unsupported"
    assert report.reason == "model_provider_self_attestation_is_not_direct_user_evidence"
    assert len(host.events) == 1


@pytest.mark.parametrize(
    ("constructor",),
    [
        (lambda: DirectUserTurnPayload(cast(Any, object())),),
        (lambda: ToolObservableResultPayload("tool", cast(Any, object())),),
        (lambda: ResponseEmissionPayload(cast(Any, object())),),
        (
            lambda: CompactionTaskCheckpointPayload(
                cast(Any, object()),
                "task_checkpoint",
            ),
        ),
        (
            lambda: ConsequenceCheckpointPayload(
                "task_outcome_observed",
                "observed",
                cast(Any, object()),
            ),
        ),
        (lambda: ModelProviderSelfAttestation(cast(Any, object())),),
        (
            lambda: ModelProviderSelfAttestation(
                reference("attestation-1", "attestation"),
                cast(Any, object()),
            ),
        ),
        (lambda: RestartSessionTransitionPayload("restart"),),
    ],
)
def test_public_payloads_reject_malformed_nested_objects_with_contract_errors(
    constructor: Callable[[], object],
) -> None:
    with pytest.raises(ClientRuntimeContractError):
        constructor()


def test_literal_fields_reject_string_lookalikes_and_malformed_host_inputs() -> None:
    class Lookalike(str):
        pass

    with pytest.raises(ClientRuntimeContractError):
        PayloadReference(reference="turn-1", kind=cast(Any, Lookalike("user_turn")))
    with pytest.raises(ClientRuntimeContractError):
        ResponseEmissionPayload(
            reference("response-1", "response"),
            emission_state=cast(Any, Lookalike("completed")),
        )
    with pytest.raises(ClientRuntimeContractError):
        ClientRuntimeCapabilities.for_level(cast(Any, Lookalike("L1")))
    with pytest.raises(ClientRuntimeContractError):
        DeterministicFakeClientRuntimeHost(capabilities=cast(Any, object()))
    with pytest.raises(ClientRuntimeContractError):
        ClientLifecycleEnvelope(
            event_id="event-1",
            sequence=1,
            hook="manual_context_request",
            session_id="session-1",
            client_id="client-1",
            payload=cast(Any, object()),
        )

    host = DeterministicFakeClientRuntimeHost.for_level("L1")
    with pytest.raises(ClientRuntimeContractError):
        host.request_pre_generation_context(
            generation_id="generation-1",
            requested_scopes=cast(Any, object()),
        )
    with pytest.raises(ClientRuntimeContractError):
        host.deliver_context(
            cast(Any, object()),
            (reference("context-pack-1", "context_pack"),),
        )
    request = host.request_pre_generation_context(generation_id="generation-2")
    assert isinstance(request, ClientLifecycleEnvelope)
    with pytest.raises(ClientRuntimeContractError):
        host.deliver_context(request, cast(Any, object()))
    with pytest.raises(ClientRuntimeContractError):
        host.observe_user_turn_attestation(cast(Any, object()))


def test_consequence_checkpoints_require_correlated_affirmative_evidence() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L3")

    with pytest.raises(ClientRuntimeContractError):
        host.record_consequence_checkpoint(
            checkpoint_kind="task_outcome_observed",
            status="not_observed",
            evidence_ref=reference("outcome-1", "outcome"),
        )
    with pytest.raises(ClientRuntimeContractError):
        host.record_consequence_checkpoint(
            checkpoint_kind="task_outcome_observed",
            status="observed",
        )
    with pytest.raises(ClientRuntimeContractError):
        host.record_consequence_checkpoint(
            checkpoint_kind="context_delivered",
            status="observed",
            evidence_ref=reference("outcome-1", "outcome"),
        )
    assert host.events == ()

    result = host.record_consequence_checkpoint(
        checkpoint_kind="task_outcome_observed",
        status="observed",
        evidence_ref=reference("outcome-1", "outcome"),
    )
    assert isinstance(result, ClientLifecycleEnvelope)


def test_l3_fake_host_exercises_every_required_hook_with_bounded_untrusted_refs() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L3")
    assert isinstance(host, ClientRuntimeAdapterV0)
    assert isinstance(host, ClientRuntimeReferenceHostV0)
    context_request = host.request_pre_generation_context(generation_id="generation-1")
    assert isinstance(context_request, ClientLifecycleEnvelope)
    assert isinstance(
        host.deliver_context(
            context_request,
            (reference("context-pack-1", "context_pack"),),
        ),
        object,
    )
    host.begin_generation(generation_id="generation-1")
    assert isinstance(
        host.observe_direct_user_turn(reference("turn-1", "user_turn")),
        ClientLifecycleEnvelope,
    )
    assert isinstance(
        host.observe_tool_result(
            tool_name="synthetic-tool",
            result_ref=reference("tool-result-1", "tool_result"),
            result_kind="observable_result",
        ),
        ClientLifecycleEnvelope,
    )
    assert isinstance(
        host.observe_response_emission(reference("response-1", "response")),
        ClientLifecycleEnvelope,
    )
    assert isinstance(
        host.record_checkpoint(
            checkpoint_ref=reference("checkpoint-1", "working_checkpoint"),
            checkpoint_kind="task_checkpoint",
        ),
        ClientLifecycleEnvelope,
    )
    assert isinstance(
        host.record_session_transition(
            transition="restart",
            previous_session_id=host.current_session_id,
            next_session_id="session-2",
        ),
        ClientLifecycleEnvelope,
    )
    assert isinstance(
        host.record_completion_or_abandonment(
            terminal_state="completed",
            task_id="task-1",
        ),
        ClientLifecycleEnvelope,
    )
    assert isinstance(
        host.record_consequence_checkpoint(
            checkpoint_kind="task_outcome_observed",
            status="observed",
            evidence_ref=reference("outcome-1", "outcome"),
        ),
        ClientLifecycleEnvelope,
    )

    hooks = {event.hook for event in host.events}
    assert set(CONSEQUENCE_CHECKPOINT_KINDS) == set(
        host.capabilities.for_hook("consequence_checkpoint").supported_consequence_kinds
    )
    assert {
        "pre_generation_context_request",
        "direct_user_turn",
        "tool_observable_result",
        "response_emission",
        "compaction_task_checkpoint",
        "restart_session_transition",
        "completion_abandonment",
        "consequence_checkpoint",
    } <= hooks
    assert all(event.content_ownership == "external_untrusted" for event in host.events)
    assert all("raw_text" not in event.as_dict() for event in host.events)


def test_bounds_and_hidden_reasoning_are_rejected_before_an_event_exists() -> None:
    with pytest.raises(EvidenceBoundaryError):
        reference("chain-of-thought-ref", "external_artifact")
    with pytest.raises(EvidenceBoundaryError):
        Diagnostic(code="unsafe", detail="hidden reasoning must not be retained")
    with pytest.raises(ClientRuntimeContractError):
        PayloadReference(reference="x", kind="context_pack", size_bytes=MAX_REFERENCE_BYTES + 1)

    host = DeterministicFakeClientRuntimeHost.for_level("L1")
    with pytest.raises(ClientRuntimeContractError):
        host.request_pre_generation_context(
            generation_id="generation-1",
            requested_scopes=tuple(f"scope-{index}" for index in range(33)),
        )
    assert host.events == ()


def test_fake_host_trace_is_deterministic_and_does_not_persist_or_call_a_provider() -> None:
    def run() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        host = DeterministicFakeClientRuntimeHost.for_level("L1")
        request = host.request_pre_generation_context(generation_id="generation-1")
        assert isinstance(request, ClientLifecycleEnvelope)
        host.deliver_context(request, (reference("context-pack-1", "context_pack"),))
        host.begin_generation(generation_id="generation-1")
        return [event.as_dict() for event in host.events], [
            {
                "sequence": entry.sequence,
                "action": entry.action,
                "reference_id": entry.reference_id,
            }
            for entry in host.trace
        ]

    first, first_trace = run()
    second, second_trace = run()
    assert first == second
    assert first_trace == second_trace
    assert all("provider" not in event for event in first)
    assert all("persist" not in event for event in first)
