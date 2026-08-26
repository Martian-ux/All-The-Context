"""Worker-backed Packet G acceptance across a Core restart.

This disposable-vault proof composes the real scheduler worker, public memory
truth, Retrieval V3, and the controlled L2 reference host.  It does not claim
provider/client integration, packaged-install acceptance, or release support.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from allthecontext.capture_scheduler import SchedulerConfig
from allthecontext.core.service import CoreService
from allthecontext.experimental_reference_host import (
    ControlledReferenceHostV0,
    RuntimeCheckpoint,
)
from allthecontext.experimental_reference_host_lifecycle import (
    compile_authorized_pack,
    core_retrieval_compiler,
)
from allthecontext.models import BootstrapResponse, ClientCreate
from allthecontext.security import ClientPrincipal

from bench.packet_h_retrieval import _provenance_packaged
from tests.fixtures.local_git_workspace import create_sanitized_workspace
from tests.fixtures.scheduled_packet_f import (
    DELETE_RELATIVE_PATH,
    POST_UPDATE_FORBIDDEN,
    SCOPE,
    UPDATE_RELATIVE_PATH,
    UPDATED_SOURCE_BYTES,
    MutableClock,
    assert_leak_oracle,
    assert_status_content_free,
    authorize_and_enable_scheduled_workspace,
    binding_hash,
    config,
    current_truth,
    deleted_truth,
    item_for_event,
    open_process_gate,
    search,
    upsert_event,
    workspace_adapter,
)


def _wait_until(
    predicate: Callable[[], bool],
    *,
    timeout: float = 5.0,
    interval: float = 0.01,
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition was not met before timeout")


def _wait_for_capture(
    service: CoreService,
    source_id: str,
    clock: MutableClock,
    *,
    current_items: int,
    deleted_items: int = 0,
) -> None:
    expected_last_run_at = clock()

    def captured() -> bool:
        source = service.capture.get_source(source_id)
        return (
            source.lifecycle_state == "enabled"
            and source.next_retry_at is None
            and source.last_error_code is None
            and source.last_run_at == expected_last_run_at
            and len(current_truth(service).items) == current_items
            and len(deleted_truth(service).items) == deleted_items
            and search(service.retrieval).total == current_items
        )

    _wait_until(captured)


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
    assert {reference.reference for reference in delivery.context_refs} == {
        item.id for item in compiled.items
    }
    assert all(reference.kind == "context_pack" for reference in delivery.context_refs)
    assert all(reference.untrusted is True for reference in delivery.context_refs)
    assert all(_provenance_packaged(item) for item in compiled.items)
    return compiled


def test_worker_capture_resumes_into_pre_generation_context_without_dashboard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    open_process_gate(monkeypatch)
    core_config = config(tmp_path)
    workspace = create_sanitized_workspace(tmp_path / "workspace")
    clock = MutableClock()
    interval = SchedulerConfig().incremental_interval_seconds
    dashboard_calls: list[str] = []
    checkpoints: list[tuple[RuntimeCheckpoint, str]] = []
    session_id = "packet-g-worker-session"

    def unexpected_dashboard_call(url: str) -> bool:
        dashboard_calls.append(url)
        raise AssertionError("scheduled context delivery must not open the dashboard")

    def checkpoint_sink(snapshot: RuntimeCheckpoint, key: str) -> None:
        checkpoints.append((snapshot, key))

    monkeypatch.setattr("allthecontext.desktop_setup.open_dashboard", unexpected_dashboard_call)

    with CoreService(core_config, clock=clock) as service:
        source_id, _enabled = authorize_and_enable_scheduled_workspace(
            service,
            core_config,
            workspace,
        )
        enabled_status = service.capture_scheduler.enable()
        assert enabled_status["running"] is True
        _wait_for_capture(service, source_id, clock, current_items=4)

        initial = current_truth(service)
        initial_ids = {item.record.id for item in initial.items}
        assert len(initial_ids) == 4
        adapter = workspace_adapter(service)
        source = service.capture.get_source(source_id)
        delete_event = upsert_event(adapter, source, DELETE_RELATIVE_PATH)
        update_event = upsert_event(adapter, source, UPDATE_RELATIVE_PATH)
        deleted_before = item_for_event(initial.items, source_id, delete_event)
        updated_before = item_for_event(initial.items, source_id, update_event)

        principal, token = service.store.create_client(
            ClientCreate(name="Packet G worker reader", scopes=["context:read"])
        )
        host = ControlledReferenceHostV0.for_level(
            "L2",
            client_id=principal.id,
            session_id=session_id,
            checkpoint_sink=checkpoint_sink,
        )
        first_pack = _compile(
            host,
            service,
            principal,
            generation_id="packet-g-worker-generation-1",
        )
        first_ids = {item.id for item in first_pack.items}
        assert first_ids
        assert first_ids <= initial_ids
        assert len(first_ids) == len(first_pack.items)

        checkpoint = host.checkpoint()
        assert checkpoint is not None
        assert checkpoints == [(checkpoint, checkpoint.idempotency_key)]
        assert_status_content_free(
            service.capture_scheduler.status(),
            workspace,
            core_config.data_dir,
        )
        assert_leak_oracle(
            first_pack.model_dump(mode="json"),
            workspace,
            core_config.data_dir,
            extra_event=update_event,
        )

    with CoreService(core_config, clock=clock) as restarted:
        resumed_principal = restarted.store.authenticate(token)
        assert resumed_principal is not None
        assert resumed_principal.id == principal.id
        assert resumed_principal.scopes == principal.scopes

        resumed_host = ControlledReferenceHostV0.from_checkpoint(
            checkpoint,
            current_session_id=session_id,
            requested_level="L2",
            client_id=resumed_principal.id,
            checkpoint_sink=checkpoint_sink,
        )
        assert resumed_host.events == checkpoint.events
        assert resumed_host.trace == checkpoint.trace

        restarted.capture_scheduler.start()
        assert restarted.capture_scheduler.status()["running"] is True
        (workspace / DELETE_RELATIVE_PATH).unlink()
        (workspace / UPDATE_RELATIVE_PATH).write_text(
            UPDATED_SOURCE_BYTES,
            encoding="utf-8",
            newline="\n",
        )
        clock.advance(interval)
        restarted.capture_scheduler._wakeup.set()
        _wait_for_capture(
            restarted,
            source_id,
            clock,
            current_items=3,
            deleted_items=1,
        )

        current = current_truth(restarted)
        current_ids = {item.record.id for item in current.items}
        assert len(current_ids) == len(current.items) == 3
        assert deleted_before.record.id not in current_ids
        assert updated_before.record.id in current_ids
        updated_after = restarted.store.get_memory_truth(updated_before.record.id)
        assert binding_hash(updated_after) != binding_hash(updated_before)

        second_pack = _compile(
            resumed_host,
            restarted,
            resumed_principal,
            generation_id="packet-g-worker-generation-2",
        )
        second_ids = {item.id for item in second_pack.items}
        assert second_ids
        assert second_ids <= current_ids
        assert len(second_ids) == len(second_pack.items)
        assert deleted_before.record.id not in second_ids
        assert updated_before.record.id in second_ids
        assert restarted.retrieval.get(deleted_before.record.id) is None

        resumed_checkpoint = resumed_host.checkpoint()
        assert resumed_checkpoint is not None
        assert resumed_checkpoint.sequence > checkpoint.sequence
        assert checkpoints[-1] == (
            resumed_checkpoint,
            resumed_checkpoint.idempotency_key,
        )
        assert_status_content_free(
            restarted.capture_scheduler.status(),
            workspace,
            core_config.data_dir,
        )
        assert_leak_oracle(
            second_pack.model_dump(mode="json"),
            workspace,
            core_config.data_dir,
            extra_forbidden=POST_UPDATE_FORBIDDEN,
            extra_event=update_event,
        )

    assert dashboard_calls == []
