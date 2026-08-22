"""Focused Wave 1 capture/lifecycle reconciliation boundary tests."""

from __future__ import annotations

import ast
import json
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from allthecontext.capture import CaptureEvent
from allthecontext.client_runtime import (
    ClientLifecycleEnvelope,
    DeterministicFakeClientRuntimeHost,
    PayloadReference,
)
from allthecontext.experimental_event_observation import AuthorizationApplicability
from allthecontext.experimental_event_reconciliation import (
    DependencyWithdrawal,
    EventOrigin,
    ReconciliationErrorCode,
    ReconciliationViolation,
    SensitivityClass,
    normalize_capture_event,
    normalize_lifecycle_event,
    reconcile_event,
)
from allthecontext.experimental_projection_contract import (
    InvalidationAction,
    InvalidationCause,
)

T0 = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _capture(*, operation: str = "upsert") -> CaptureEvent:
    return CaptureEvent(
        provider_event_id="provider-event-1",
        provider_item_id="provider-item-1",
        order_key="7",
        operation=cast(Any, operation),
        payload={} if operation == "delete" else {"text": "inert source text"},
        generation=3,
    )


def _withdrawal(
    cause: InvalidationCause = InvalidationCause.ORDINARY_DELETE,
    action: InvalidationAction = InvalidationAction.WITHDRAW_ONLY,
) -> DependencyWithdrawal:
    return DependencyWithdrawal(
        dependency_ref="provider-item-1",
        cause=cause,
        action=action,
        provider_item_id="provider-item-1",
    )


def test_capture_normalizes_existing_identity_without_copying_payload() -> None:
    result = normalize_capture_event(
        _capture(),
        source_id="capture-source-1",
        source_cursor="cursor-1",
        source_sequence=7,
        idempotency_key="capture-event-idempotency-1",
        account_ref="account-1",
        project_ref="project-1",
        event_time=T0,
        observed_time=T0,
        artifact_refs=("artifact-1",),
        authorization=AuthorizationApplicability(allowed_principals=frozenset({"alice"})),
    )

    assert result.origin is EventOrigin.CAPTURE
    assert result.event_id == "provider-event-1"
    assert result.source_generation == 3
    assert result.source_cursor == "cursor-1"
    assert result.source_sequence == 7
    assert result.payload_commitment is not None
    assert result.payload_size_bytes > 0
    assert result.provider_item_id == "provider-item-1"
    assert "provider-item-1" in result.artifact_refs
    assert "inert source text" not in json.dumps(result.as_dict())
    assert "payload" not in result.as_dict()
    with pytest.raises(FrozenInstanceError):
        result.event_id = "changed"  # type: ignore[misc]


def test_lifecycle_requires_exact_envelope_and_preserves_reference_truth() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L1", client_id="client-1")
    envelope = host.observe_direct_user_turn(PayloadReference("turn-1", "user_turn"))
    assert isinstance(envelope, ClientLifecycleEnvelope)
    result = normalize_lifecycle_event(envelope, conversation_ref="conversation-1")
    assert result.witness_class.value == "direct_user"
    assert result.client_ref == "client-1"
    assert result.conversation_ref == "conversation-1"
    assert result.artifact_refs == ("turn-1",)


def test_lifecycle_normalization_uses_exact_existing_envelope() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L1", client_id="client-1")
    envelope = host.request_pre_generation_context(
        generation_id="generation-1",
        requested_scopes=("project-1",),
    )
    assert isinstance(envelope, ClientLifecycleEnvelope)
    result = normalize_lifecycle_event(
        envelope,
        account_ref="account-1",
        event_time=T0,
        observed_time=T0,
        sensitivity=SensitivityClass.SENSITIVE,
    )
    assert result.origin is EventOrigin.CLIENT_LIFECYCLE
    assert result.event_id == envelope.event_id
    assert result.lifecycle_event_id == envelope.event_id
    assert result.client_ref == "client-1"
    assert result.project_ref is None
    assert result.witness_class.value == "host_artifact"
    assert result.content_ownership == "external_untrusted"
    assert result.lifecycle_contract_version == "client-runtime-v0"
    assert result.artifact_refs == ()

    class Lookalike:
        event_id = envelope.event_id

    with pytest.raises(ReconciliationViolation) as failure:
        normalize_lifecycle_event(cast(Any, Lookalike()))
    assert failure.value.code is ReconciliationErrorCode.INVALID_LIFECYCLE_ENVELOPE

    object.__setattr__(envelope, "hook", "direct_user_turn")
    with pytest.raises(ReconciliationViolation) as pairing:
        normalize_lifecycle_event(envelope)
    assert pairing.value.code is ReconciliationErrorCode.INVALID_LIFECYCLE_PAYLOAD


def test_composed_input_reuses_both_ids_and_client_artifact_reference() -> None:
    host = DeterministicFakeClientRuntimeHost.for_level("L1")
    lifecycle = host.observe_direct_user_turn(PayloadReference("turn-1", "user_turn"))
    assert isinstance(lifecycle, ClientLifecycleEnvelope)
    result = reconcile_event(
        capture_event=_capture(),
        lifecycle_envelope=lifecycle,
        source_id="source-1",
        source_cursor="cursor-1",
        source_sequence=7,
        idempotency_key="idempotency-1",
    )
    assert result.origin is EventOrigin.CAPTURE_AND_CLIENT_LIFECYCLE
    assert result.event_id == "provider-event-1"
    assert result.lifecycle_event_id == lifecycle.event_id
    assert result.witness_class.value == "direct_user"
    assert set(result.artifact_refs) == {"provider-item-1", "turn-1"}


def test_delete_requires_matching_authorized_withdrawal_and_purge_erases() -> None:
    with pytest.raises(ReconciliationViolation) as missing:
        normalize_capture_event(
            _capture(operation="delete"),
            source_id="source-1",
            idempotency_key="idempotency-1",
        )
    assert missing.value.code is ReconciliationErrorCode.DELETE_WITHDRAWAL_REQUIRED

    with pytest.raises(ReconciliationViolation) as mismatch:
        normalize_capture_event(
            _capture(operation="delete"),
            source_id="source-1",
            idempotency_key="idempotency-1",
            dependency_withdrawals=(
                DependencyWithdrawal(
                    "other-item",
                    InvalidationCause.ORDINARY_DELETE,
                    provider_item_id="other-item",
                ),
            ),
        )
    assert mismatch.value.code is ReconciliationErrorCode.DELETE_WITHDRAWAL_REQUIRED

    result = normalize_capture_event(
        _capture(operation="delete"),
        source_id="source-1",
        idempotency_key="idempotency-1",
        dependency_withdrawals=(_withdrawal(),),
    )
    assert result.payload_size_bytes == 2

    purge = _withdrawal(
        InvalidationCause.TERMINAL_PURGE,
        InvalidationAction.ERASE,
    )
    assert purge.action is InvalidationAction.ERASE
    with pytest.raises(ReconciliationViolation) as bad_purge:
        DependencyWithdrawal(
            "provider-item-1",
            InvalidationCause.TERMINAL_PURGE,
            InvalidationAction.WITHDRAW_ONLY,
        )
    assert bad_purge.value.code is ReconciliationErrorCode.PURGE_REQUIRES_ERASE


@pytest.mark.parametrize(
    "value",
    [
        [DependencyWithdrawal("x", InvalidationCause.CORRECTION)],
        ("not-a-withdrawal",),
    ],
)
def test_withdrawals_are_actual_tuples_and_nested_types_fail_without_typeerror(
    value: object,
) -> None:
    with pytest.raises(ReconciliationViolation) as failure:
        normalize_capture_event(
            _capture(),
            source_id="source-1",
            idempotency_key="idempotency-1",
            dependency_withdrawals=cast(Any, value),
        )
    assert failure.value.code is ReconciliationErrorCode.INVALID_WITHDRAWAL

    with pytest.raises(ReconciliationViolation) as nested:
        DependencyWithdrawal("x", cast(Any, "correction"), cast(Any, "erase"))
    assert nested.value.code is ReconciliationErrorCode.INVALID_WITHDRAWAL


def test_cursor_bound_secret_metadata_and_capture_normalizer_are_content_free(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ReconciliationViolation) as cursor:
        normalize_capture_event(
            _capture(),
            source_id="source-1",
            source_cursor="c" * 1025,
            idempotency_key="idempotency-1",
        )
    assert cursor.value.code is ReconciliationErrorCode.INVALID_FIELD

    with pytest.raises(ReconciliationViolation) as secret:
        normalize_capture_event(
            _capture(),
            source_id="token: never-retained",
            idempotency_key="idempotency-1",
        )
    assert str(secret.value) == ReconciliationErrorCode.SECRET_LIKE_METADATA.value
    assert "never-retained" not in str(secret.value)

    corrupted = object.__new__(CaptureEvent)
    object.__setattr__(corrupted, "provider_event_id", "event-1")
    object.__setattr__(corrupted, "provider_item_id", "item-1")
    object.__setattr__(corrupted, "order_key", "1")
    object.__setattr__(corrupted, "operation", "invalid")
    object.__setattr__(corrupted, "payload", {})
    object.__setattr__(corrupted, "generation", 1)
    with pytest.raises(ReconciliationViolation) as operation:
        normalize_capture_event(
            corrupted,
            source_id="source-1",
            idempotency_key="idempotency-1",
        )
    assert operation.value.code is ReconciliationErrorCode.INVALID_CAPTURE_OPERATION

    object.__setattr__(corrupted, "operation", "upsert")
    object.__setattr__(corrupted, "generation", -1)
    with pytest.raises(ReconciliationViolation) as generation:
        normalize_capture_event(
            corrupted,
            source_id="source-1",
            idempotency_key="idempotency-1",
        )
    assert generation.value.code is ReconciliationErrorCode.INVALID_CAPTURE_GENERATION

    monkeypatch.setattr(CaptureEvent, "normalized", None)
    with pytest.raises(ReconciliationViolation) as normalizer:
        normalize_capture_event(
            _capture(),
            source_id="source-1",
            idempotency_key="idempotency-1",
        )
    assert normalizer.value.code is ReconciliationErrorCode.CAPTURE_NORMALIZATION_REJECTED


def test_module_is_reference_only_and_has_no_second_authority_or_persistence_surface() -> None:
    path = Path(__file__).resolve().parents[2] / (
        "packages/allthecontext/src/allthecontext/experimental_event_reconciliation.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert not imported & {"sqlite3", "socket", "requests", "httpx", "urllib"}
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module and node.module.endswith("storage")
        for node in ast.walk(tree)
    )
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & {
        "CoreStore",
        "CaptureLedger",
        "CaptureCoordinator",
        "sqlite3",
        "advance_cursor",
        "checkpoint",
        "persist",
        "replay",
    }
