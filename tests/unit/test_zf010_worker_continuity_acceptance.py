"""Tests-only worker-backed ZF-010 continuity acceptance.

The focused proof covers one sanitized local source, one real scheduler worker,
one L2 direct-user formation host, and continuity across a Core restart.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from allthecontext.client_runtime import ClientLifecycleEnvelope, DirectUserTurnPayload
from allthecontext.core.service import CoreService
from allthecontext.experimental_reference_host import (
    ControlledReferenceHostV0,
    RuntimeCheckpoint,
)
from allthecontext.experimental_reference_host_formation import (
    form_direct_user_turn,
)
from allthecontext.experimental_reference_host_lifecycle import (
    compile_authorized_pack,
    core_retrieval_compiler,
    pack_contents,
)
from allthecontext.models import BootstrapResponse, ClientCreate, MemoryTruthStatus
from allthecontext.security import WITNESS_EXPLICIT_USER_STATEMENT, ClientPrincipal

from tests.fixtures.local_git_workspace import create_sanitized_workspace
from tests.fixtures.scheduled_packet_f import (
    SCOPE,
    MutableClock,
    assert_status_content_free,
    authorize_and_enable_scheduled_workspace,
    config,
    current_truth,
    open_process_gate,
)
from tests.unit.test_packet_g_worker_acceptance import _wait_for_capture

PREFERENCE = "Prefer concise scheduled vault answers."
CORRECTED = "Prefer bounded scheduled vault answers."
FORGET_REASON = "The user explicitly requested deletion."
FROZEN_OBSERVED_AT = datetime(2026, 8, 24, 21, 0, 0, tzinfo=UTC)


def _compile(
    host: ControlledReferenceHostV0,
    service: CoreService,
    principal: ClientPrincipal,
    *,
    generation_id: str,
) -> BootstrapResponse:
    compiled, delivery, generation = compile_authorized_pack(
        host,
        core_retrieval_compiler(service.retrieval),
        principal,
        generation_id=generation_id,
        requested_scopes=(SCOPE,),
        budget_chars=4_000,
        query="workspace item",
    )
    assert delivery.delivered_before_generation is True
    assert generation.pre_generation_delivery is True
    return compiled


def _observe(
    host: ControlledReferenceHostV0,
    reference: str,
    content: str,
) -> ClientLifecycleEnvelope:
    envelope = host.observe_direct_user_content(reference=reference, content=content)
    assert isinstance(envelope, ClientLifecycleEnvelope)
    assert type(envelope.payload) is DirectUserTurnPayload
    encoded = content.encode("utf-8")
    turn_ref = envelope.payload.turn_ref
    assert turn_ref.size_bytes == len(encoded)
    assert turn_ref.sha256 == hashlib.sha256(encoded).hexdigest()
    assert content not in json.dumps(envelope.as_dict(), sort_keys=True)
    return envelope


def _current_source_ids(service: CoreService) -> set[str]:
    return {item.record.id for item in current_truth(service).items}


def _assert_source_truth_current(service: CoreService, source_ids: set[str]) -> None:
    current_ids = _current_source_ids(service)
    assert source_ids <= current_ids
    for source_id in source_ids:
        assert service.store.get_memory_truth(source_id).status is MemoryTruthStatus.CURRENT


def test_worker_backed_zf010_preference_continuity_across_core_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_process_gate(monkeypatch)
    core_config = config(tmp_path)
    workspace = create_sanitized_workspace(tmp_path / "workspace")
    clock = MutableClock()
    dashboard_calls: list[str] = []
    checkpoints: list[tuple[RuntimeCheckpoint, str]] = []

    def unexpected_dashboard_call(url: str) -> bool:
        dashboard_calls.append(url)
        raise AssertionError("scheduled context delivery must not open the dashboard")

    def checkpoint_sink(snapshot: RuntimeCheckpoint, key: str) -> None:
        checkpoints.append((snapshot, key))

    monkeypatch.setattr("allthecontext.desktop_setup.open_dashboard", unexpected_dashboard_call)

    reader_token: str
    witness_token: str
    reader_id: str
    witness_id: str
    source_ids: set[str]
    preference_id: str
    checkpoint: RuntimeCheckpoint
    formation_session = "zf010-worker-formation-session"

    with CoreService(core_config, clock=clock) as service:
        source_id, _enabled = authorize_and_enable_scheduled_workspace(
            service,
            core_config,
            workspace,
        )
        scheduler_status = service.capture_scheduler.enable()
        assert scheduler_status["running"] is True
        _wait_for_capture(service, source_id, clock, current_items=4)

        source_ids = _current_source_ids(service)
        assert len(source_ids) == 4

        reader, reader_token = service.store.create_client(
            ClientCreate(name="ZF-010 worker continuity reader", scopes=["context:read"])
        )
        witness, witness_token = service.store.create_client(
            ClientCreate(
                name="ZF-010 worker continuity witness",
                scopes=["context:read", "context:propose", WITNESS_EXPLICIT_USER_STATEMENT],
            )
        )
        reader_id = reader.id
        witness_id = witness.id

        reader_host = ControlledReferenceHostV0.for_level(
            "L2",
            client_id=reader.id,
            session_id="zf010-worker-reader-session",
        )
        initial = _compile(
            reader_host,
            service,
            reader,
            generation_id="zf010-worker-generation-initial",
        )
        initial_ids = {item.id for item in initial.items}
        assert initial_ids
        assert initial_ids <= source_ids

        formation_host = ControlledReferenceHostV0.for_level(
            "L2",
            client_id=witness.id,
            session_id=formation_session,
            checkpoint_sink=checkpoint_sink,
        )
        assert formation_host.negotiation.accepted_level == "L2"
        preference_envelope = _observe(formation_host, "turn-preference", PREFERENCE)
        preference = form_direct_user_turn(
            service.store,
            formation_host,
            preference_envelope,
            principal=witness,
            content=PREFERENCE,
            kind="interaction_preference",
            observed_at=FROZEN_OBSERVED_AT,
        )
        assert preference.status == "formed"
        assert preference.candidate is not None
        assert preference.candidate.record_id is not None
        preference_id = preference.candidate.record_id
        assert preference.candidate.kind == "interaction_preference"
        assert _current_source_ids(service) == source_ids | {preference_id}

        checkpoint = formation_host.checkpoint()
        assert checkpoint is not None
        assert checkpoints == [(checkpoint, checkpoint.idempotency_key)]
        assert_status_content_free(
            service.capture_scheduler.status(),
            workspace,
            core_config.data_dir,
        )

    with CoreService(core_config, clock=clock) as restarted:
        resumed_reader = restarted.store.authenticate(reader_token)
        resumed_witness = restarted.store.authenticate(witness_token)
        assert resumed_reader is not None
        assert resumed_witness is not None
        assert resumed_reader.id == reader_id
        assert resumed_witness.id == witness_id
        assert resumed_reader.scopes == reader.scopes
        assert resumed_witness.scopes == witness.scopes

        restored_host = ControlledReferenceHostV0.from_checkpoint(
            checkpoint,
            current_session_id=formation_session,
            requested_level="L2",
            client_id=resumed_witness.id,
            checkpoint_sink=checkpoint_sink,
        )
        assert restored_host.negotiation.accepted_level == "L2"
        assert restored_host.events == checkpoint.events
        assert restored_host.trace == checkpoint.trace

        restarted.capture_scheduler.start()
        assert restarted.capture_scheduler.status()["running"] is True

        resumed_reader_host = ControlledReferenceHostV0.for_level(
            "L2",
            client_id=resumed_reader.id,
            session_id="zf010-worker-reader-session",
        )
        after_restart = _compile(
            resumed_reader_host,
            restarted,
            resumed_reader,
            generation_id="zf010-worker-generation-after-restart",
        )
        after_restart_contents = pack_contents(after_restart)
        after_restart_ids = {item.id for item in after_restart.items}
        assert PREFERENCE in after_restart_contents
        assert after_restart_ids <= source_ids | {preference_id}
        assert after_restart_ids & source_ids
        _assert_source_truth_current(restarted, source_ids)

        correction_envelope = _observe(restored_host, "turn-correction", CORRECTED)
        correction = form_direct_user_turn(
            restarted.store,
            restored_host,
            correction_envelope,
            principal=resumed_witness,
            content=CORRECTED,
            kind="correction",
            supersedes=preference_id,
            observed_at=FROZEN_OBSERVED_AT,
        )
        assert correction.status == "formed"
        assert correction.candidate is not None
        assert correction.candidate.record_id == preference_id

        after_correction = _compile(
            resumed_reader_host,
            restarted,
            resumed_reader,
            generation_id="zf010-worker-generation-corrected",
        )
        corrected_contents = pack_contents(after_correction)
        assert CORRECTED in corrected_contents
        assert PREFERENCE not in corrected_contents
        corrected_ids = {item.id for item in after_correction.items}
        assert corrected_ids <= source_ids | {preference_id}
        assert corrected_ids & source_ids
        _assert_source_truth_current(restarted, source_ids)

        forget_envelope = _observe(restored_host, "turn-forget", FORGET_REASON)
        forgotten = form_direct_user_turn(
            restarted.store,
            restored_host,
            forget_envelope,
            principal=resumed_witness,
            content=FORGET_REASON,
            kind="context_forget",
            supersedes=preference_id,
            observed_at=FROZEN_OBSERVED_AT,
        )
        assert forgotten.status == "formed"
        assert forgotten.candidate is not None
        assert forgotten.candidate.record_id == preference_id
        forgotten_truth = restarted.store.get_memory_truth(
            preference_id,
            include_deleted=True,
        )
        assert forgotten_truth.status is MemoryTruthStatus.DELETED

        after_forget = _compile(
            resumed_reader_host,
            restarted,
            resumed_reader,
            generation_id="zf010-worker-generation-forgotten",
        )
        forgotten_contents = pack_contents(after_forget)
        assert PREFERENCE not in forgotten_contents
        assert CORRECTED not in forgotten_contents
        forgotten_ids = {item.id for item in after_forget.items}
        assert forgotten_ids <= source_ids
        assert forgotten_ids & source_ids
        _assert_source_truth_current(restarted, source_ids)

        resumed_checkpoint = restored_host.checkpoint()
        assert resumed_checkpoint is not None
        assert resumed_checkpoint.sequence > checkpoint.sequence
        assert checkpoints[-1] == (
            resumed_checkpoint,
            resumed_checkpoint.idempotency_key,
        )

    assert dashboard_calls == []
