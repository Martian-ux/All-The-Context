from __future__ import annotations

import asyncio
import gc
import json
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from allthecontext.activity import CoreActivityGate
from allthecontext.capture import BackoffPolicy
from allthecontext.config import CoreConfig
from allthecontext.core import app as core_app
from allthecontext.core.app import (
    UPDATE_ACTIVATION_BUSY_REASON,
    create_app,
)
from allthecontext.core.service import CoreService
from allthecontext.updater import UpdatePhase
from fastapi.testclient import TestClient


class _UpdatesStub:
    def __init__(self, phase: UpdatePhase = UpdatePhase.IDLE) -> None:
        self.preferences = SimpleNamespace(enabled=False, channel="stable")
        self.config = SimpleNamespace(manifest_urls={})
        self.state = SimpleNamespace(phase=phase)
        self.install_calls = 0
        self.readiness_checks = 0
        self.recover_calls = 0

    def public_status(self) -> dict[str, Any]:
        return {"phase": self.state.phase.value}

    def install(self, *, readiness_check: Callable[[], None] | None = None) -> dict[str, Any]:
        self.install_calls += 1
        if readiness_check is not None:
            self.readiness_checks += 1
            readiness_check()
        return {
            "phase": UpdatePhase.RESTART_REQUIRED.value,
            "automatic_install_supported": False,
        }

    def recover_after_restart(self) -> dict[str, Any]:
        self.recover_calls += 1
        return self.public_status()


def test_activity_snapshots_are_bounded_and_content_free(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with CoreService(config) as service:
        operation = service.import_operations.start_operation(
            declared_byte_size=1,
            filename="private-name-must-not-escape.zip",
        )
        operation_id = str(operation["operation_id"])

        awaiting = service.import_operations.activity_snapshot()
        assert awaiting == {"active": True, "count": 1, "truncated": False}
        assert "private-name-must-not-escape.zip" not in json.dumps(awaiting)

        service.store.claim_import_operation_upload(operation_id)
        assert service.import_operations.activity_snapshot()["active"] is True
        service.store.update_import_operation(
            operation_id,
            status="processing",
            phase="parsing",
        )
        assert service.import_operations.activity_snapshot()["active"] is True
        service.store.update_import_operation(
            operation_id,
            status="complete",
            phase="complete",
            completed=True,
        )
        assert service.import_operations.activity_snapshot() == {
            "active": False,
            "count": 0,
            "truncated": False,
        }
        assert service.imports.activity_snapshot() == {
            "active": False,
            "count": 0,
            "truncated": False,
        }

        source = service.capture.create_source(
            provider="fake",
            account_label="private-account-label",
            local_only_acknowledged=True,
        )
        service.capture.enable(source.id)
        handle, _active_source, _attempt = service.capture.ledger.begin_run(source.id)

        lease_activity = service.capture_scheduler.activity_snapshot()
        assert lease_activity["durable_lease_active"] is True
        assert lease_activity["durable_lease_count"] == 1
        assert lease_activity["scheduled_worker_active"] is False
        assert lease_activity["scheduled_cycle_active"] is False
        assert "private-account-label" not in json.dumps(lease_activity)

        assert service.capture._run_lock.acquire(blocking=False)
        try:
            assert service.capture_scheduler.activity_snapshot()["foreground_run_active"] is True
        finally:
            service.capture._run_lock.release()

        assert service.capture_scheduler._cycle_lock.acquire(blocking=False)
        try:
            assert service.capture_scheduler.activity_snapshot()["scheduled_cycle_active"] is True
        finally:
            service.capture_scheduler._cycle_lock.release()

        service.capture.ledger.finish_run(
            handle=handle,
            status="completed",
            error_code=None,
            pages=0,
            events=0,
            applied_events=0,
            duplicate_events=0,
            failures=0,
            attempts=1,
            backoff=BackoffPolicy(),
        )
        assert service.capture_scheduler.activity_snapshot()["durable_lease_active"] is False


def test_busy_import_refuses_explicit_activation_before_update_or_shutdown(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    updates = _UpdatesStub()
    shutdowns: list[bool] = []
    app = create_app(
        config,
        update_manager=updates,  # type: ignore[arg-type]
        shutdown_callback=lambda: shutdowns.append(True),
    )
    secret_name = "private-import-name-must-not-escape.zip"
    app.state.core.import_operations.start_operation(
        declared_byte_size=1,
        filename=secret_name,
    )
    before_phase = updates.state.phase

    with TestClient(app) as client:
        response = client.post("/v1/admin/updates/install")

    assert response.status_code == 409
    assert response.json() == {"detail": UPDATE_ACTIVATION_BUSY_REASON}
    assert secret_name not in response.text
    assert updates.install_calls == 0
    assert shutdowns == []
    assert updates.state.phase is before_phase
    assert not (config.data_dir / "updates" / "backups").exists()


@pytest.mark.parametrize("activity", ["foreground_run", "scheduled_cycle", "durable_lease"])
def test_capture_activity_refuses_explicit_activation(
    tmp_path: Path,
    activity: str,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    updates = _UpdatesStub()
    app = create_app(config, update_manager=updates)  # type: ignore[arg-type]
    service = app.state.core
    held_lock: Any = None
    handle: Any = None
    if activity == "foreground_run":
        held_lock = service.capture._run_lock
    elif activity == "scheduled_cycle":
        held_lock = service.capture_scheduler._cycle_lock
    else:
        source = service.capture.create_source(
            provider="fake",
            account_label="lease-account",
            local_only_acknowledged=True,
        )
        service.capture.enable(source.id)
        handle, _active_source, _attempt = service.capture.ledger.begin_run(source.id)

    if held_lock is not None:
        assert held_lock.acquire(blocking=False)
    try:
        with TestClient(app) as client:
            response = client.post("/v1/admin/updates/install")
            if handle is not None:
                service.capture.ledger.finish_run(
                    handle=handle,
                    status="completed",
                    error_code=None,
                    pages=0,
                    events=0,
                    applied_events=0,
                    duplicate_events=0,
                    failures=0,
                    attempts=1,
                    backoff=BackoffPolicy(),
                )
    finally:
        if held_lock is not None:
            held_lock.release()

    assert response.status_code == 409
    assert response.json() == {"detail": UPDATE_ACTIVATION_BUSY_REASON}
    assert updates.install_calls == 0


def test_idle_explicit_activation_behavior_remains_unchanged(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    updates = _UpdatesStub()
    shutdowns: list[bool] = []
    app = create_app(
        config,
        update_manager=updates,  # type: ignore[arg-type]
        shutdown_callback=lambda: shutdowns.append(True),
    )

    with TestClient(app) as client:
        response = client.post("/v1/admin/updates/install")

    assert response.status_code == 200
    assert response.json()["phase"] == UpdatePhase.RESTART_REQUIRED.value
    assert updates.install_calls == 1
    assert updates.readiness_checks == 1
    assert shutdowns == []


def test_final_readiness_barrier_blocks_new_import_scheduler_and_lease_activity(
    tmp_path: Path,
) -> None:
    final_readiness = threading.Event()
    allow_mutation = threading.Event()
    mutation_started = threading.Event()

    class BlockingUpdates(_UpdatesStub):
        def install(self, *, readiness_check: Callable[[], None] | None = None) -> dict[str, Any]:
            self.install_calls += 1
            if readiness_check is not None:
                readiness_check()
            final_readiness.set()
            assert allow_mutation.wait(timeout=5.0)
            self.state.phase = UpdatePhase.INSTALLING
            mutation_started.set()
            return {
                "phase": UpdatePhase.RESTART_REQUIRED.value,
                "automatic_install_supported": False,
            }

    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    updates = BlockingUpdates()
    app = create_app(config, update_manager=updates)  # type: ignore[arg-type]
    service = app.state.core
    source = service.capture.create_source(
        provider="fake",
        account_label="lease-account",
        local_only_acknowledged=True,
    )
    service.capture.enable(source.id)

    activity_attempted = {
        "import": threading.Event(),
        "scheduler": threading.Event(),
        "lease": threading.Event(),
    }
    activity_finished = {
        "import": threading.Event(),
        "scheduler": threading.Event(),
        "lease": threading.Event(),
    }
    lease_handle: list[Any] = []

    def start_import() -> None:
        activity_attempted["import"].set()
        operation = service.import_operations.start_operation(
            declared_byte_size=0,
            filename="race.bin",
        )
        service.import_operations.cancel_operation(str(operation["operation_id"]))
        activity_finished["import"].set()

    def run_scheduler() -> None:
        activity_attempted["scheduler"].set()
        service.capture_scheduler.run_cycle()
        activity_finished["scheduler"].set()

    def begin_lease() -> None:
        activity_attempted["lease"].set()
        lease_handle.append(service.capture.ledger.begin_run(source.id)[0])
        activity_finished["lease"].set()

    request_result: list[Any] = []

    with TestClient(app) as client:
        request = threading.Thread(
            target=lambda: request_result.append(client.post("/v1/admin/updates/install")),
            daemon=True,
        )
        request.start()
        assert final_readiness.wait(timeout=5.0)

        activities = [
            threading.Thread(target=start_import, daemon=True),
            threading.Thread(target=run_scheduler, daemon=True),
            threading.Thread(target=begin_lease, daemon=True),
        ]
        for activity in activities:
            activity.start()
        for name in activity_attempted:
            assert activity_attempted[name].wait(timeout=1.0)
            assert not activity_finished[name].is_set()

        allow_mutation.set()
        assert mutation_started.wait(timeout=5.0)
        request.join(timeout=5.0)
        assert not request.is_alive()
        for activity in activities:
            activity.join(timeout=5.0)
            assert not activity.is_alive()

    assert request_result[0].status_code == 200
    assert all(event.is_set() for event in activity_finished.values())
    assert len(lease_handle) == 1
    service.capture.ledger.finish_run(
        handle=lease_handle[0],
        status="completed",
        error_code=None,
        pages=0,
        events=0,
        applied_events=0,
        duplicate_events=0,
        failures=0,
        attempts=1,
        backoff=BackoffPolicy(),
    )


def test_compat_multipart_waits_for_admission_before_receive_or_temp_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    app = create_app(config, update_manager=_UpdatesStub())  # type: ignore[arg-type]
    service = app.state.core
    form_started = threading.Event()
    temp_directory_created = threading.Event()
    original_form = core_app.Request.form
    original_temporary_directory = core_app.tempfile.TemporaryDirectory

    def tracked_form(request: Any, **kwargs: Any) -> Any:
        form_started.set()
        return original_form(request, **kwargs)

    def tracked_temporary_directory(*args: Any, **kwargs: Any) -> Any:
        temp_directory_created.set()
        return original_temporary_directory(*args, **kwargs)

    monkeypatch.setattr(core_app.Request, "form", tracked_form)
    monkeypatch.setattr(core_app.tempfile, "TemporaryDirectory", tracked_temporary_directory)

    exclusive_started = threading.Event()
    release_exclusive = threading.Event()

    def hold_exclusive() -> None:
        with service.activity_gate.exclusive():
            exclusive_started.set()
            assert release_exclusive.wait(timeout=5.0)

    holder = threading.Thread(target=hold_exclusive, daemon=True)
    request_started = threading.Event()
    response: list[Any] = []

    with TestClient(app) as client:
        holder.start()
        assert exclusive_started.wait(timeout=5.0)

        def upload() -> None:
            request_started.set()
            response.append(
                client.post(
                    "/v1/admin/import",
                    files={
                        "file": (
                            "admission.jsonl",
                            b'{"kind":"goal","content":"admitted after update"}\n',
                            "application/jsonl",
                        )
                    },
                    data={"provider": "generic"},
                )
            )

        request = threading.Thread(target=upload, daemon=True)
        request.start()
        assert request_started.wait(timeout=5.0)
        assert not form_started.wait(timeout=0.25)
        assert not temp_directory_created.is_set()
        assert service.import_operations.activity_snapshot() == {
            "active": False,
            "count": 0,
            "truncated": False,
        }

        release_exclusive.set()
        holder.join(timeout=5.0)
        request.join(timeout=10.0)
        assert not holder.is_alive()
        assert not request.is_alive()

    assert form_started.is_set()
    assert temp_directory_created.is_set()
    assert response[0].status_code == 200, response[0].text


def test_compat_multipart_drains_before_update_and_fences_new_uploads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    form_calls = 0
    form_lock = threading.Lock()
    first_form_started = threading.Event()
    second_form_started = threading.Event()
    child_activity_entered = threading.Event()
    release_first_form = threading.Event()
    gate_holder: list[CoreActivityGate] = []
    child_tasks: list[asyncio.Task[Any]] = []
    original_form = core_app.Request.form

    async def child_activity() -> None:
        async with gate_holder[0].activity_async():
            child_activity_entered.set()

    def tracked_form(request: Any, **kwargs: Any) -> Any:
        nonlocal form_calls
        with form_lock:
            form_calls += 1
            call_number = form_calls
        if call_number == 1:
            child_tasks.append(asyncio.create_task(child_activity()))
            first_form_started.set()
            assert release_first_form.wait(timeout=5.0)
        elif call_number == 2:
            second_form_started.set()
        return original_form(request, **kwargs)

    monkeypatch.setattr(core_app.Request, "form", tracked_form)

    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    app = create_app(config, update_manager=_UpdatesStub())  # type: ignore[arg-type]
    gate_holder.append(app.state.core.activity_gate)
    payload = b'{"kind":"goal","content":"first compatibility upload"}\n'
    first_response: list[Any] = []
    second_response: list[Any] = []
    exclusive_started = threading.Event()
    release_exclusive = threading.Event()

    def post_upload(target: list[Any], content: bytes) -> None:
        target.append(
            client.post(
                "/v1/admin/import",
                files={"file": ("compat.jsonl", content, "application/jsonl")},
                data={"provider": "generic"},
            )
        )

    def hold_exclusive() -> None:
        with app.state.core.activity_gate.exclusive():
            exclusive_started.set()
            assert release_exclusive.wait(timeout=5.0)

    with TestClient(app) as client:
        first = threading.Thread(target=post_upload, args=(first_response, payload), daemon=True)
        first.start()
        assert first_form_started.wait(timeout=5.0)

        update = threading.Thread(target=hold_exclusive, daemon=True)
        update.start()
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with app.state.core.activity_gate._condition:
                if app.state.core.activity_gate._waiting_exclusive:
                    break
            time.sleep(0.01)
        else:
            pytest.fail("updater did not enter the activity admission queue")

        second = threading.Thread(target=post_upload, args=(second_response, payload), daemon=True)
        second.start()
        assert not second_form_started.wait(timeout=0.25)

        release_first_form.set()
        first.join(timeout=10.0)
        assert not first.is_alive()
        assert first_response[0].status_code == 200, first_response[0].text
        assert exclusive_started.wait(timeout=5.0)
        assert not second_form_started.is_set()
        assert not child_activity_entered.is_set()

        release_exclusive.set()
        update.join(timeout=10.0)
        second.join(timeout=10.0)
        assert not update.is_alive()
        assert not second.is_alive()
        assert child_activity_entered.wait(timeout=5.0)

    assert second_response[0].status_code == 200, second_response[0].text
    assert form_calls == 2


@pytest.mark.parametrize("failure", ["malformed", "size_limit"])
def test_compat_multipart_failures_release_gate_and_temp_files(
    tmp_path: Path,
    failure: str,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    if failure == "size_limit":
        config = replace(config, max_import_bytes=1)
    updates = _UpdatesStub()
    app = create_app(config, update_manager=updates)  # type: ignore[arg-type]

    with TestClient(app) as client:
        if failure == "malformed":
            response = client.post(
                "/v1/admin/import",
                content=b"",
                headers={"Content-Type": "multipart/form-data"},
            )
            assert response.status_code == 400, response.text
        else:
            response = client.post(
                "/v1/admin/import",
                files={"file": ("too-large.txt", b"12", "text/plain")},
            )
            assert response.status_code == 422, response.text

        assert app.state.core.import_operations.activity_snapshot() == {
            "active": False,
            "count": 0,
            "truncated": False,
        }
        assert not list(config.data_dir.glob("atc-import-*"))
        update_response = client.post("/v1/admin/updates/install")
        assert update_response.status_code == 200, update_response.text


def test_async_activity_cancellation_releases_gate_reader() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        entered = asyncio.Event()

        async def hold_until_cancelled() -> None:
            async with gate.activity_async():
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(hold_until_cancelled())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        def acquire_exclusive() -> bool:
            with gate.exclusive():
                return True

        assert await asyncio.wait_for(asyncio.to_thread(acquire_exclusive), timeout=5.0)

    asyncio.run(exercise())


def test_async_child_task_cannot_bypass_waiting_exclusive_writer() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        child_started = asyncio.Event()
        child_entered = asyncio.Event()
        writer_started = threading.Event()
        release_writer = threading.Event()

        async def child() -> None:
            child_started.set()
            async with gate.activity_async():
                child_entered.set()

        def writer() -> None:
            with gate.exclusive():
                writer_started.set()
                assert release_writer.wait(timeout=5.0)

        async with gate.activity_async():
            writer_task = asyncio.create_task(asyncio.to_thread(writer))
            for _ in range(500):
                if gate._waiting_exclusive:
                    break
                await asyncio.sleep(0.001)
            else:
                pytest.fail("exclusive writer did not begin waiting")

            child_task = asyncio.create_task(child())
            await child_started.wait()
            await asyncio.sleep(0.03)
            assert not child_entered.is_set()
            assert gate._active_count == 1
            assert len(gate._active_by_owner) == 1

        assert await asyncio.to_thread(writer_started.wait, 5.0)
        assert not child_entered.is_set()
        assert gate._active_count == 0
        release_writer.set()
        await writer_task
        await child_task
        assert child_entered.is_set()
        assert gate._active_count == 0
        assert not gate._active_by_owner

    asyncio.run(exercise())


def test_unrelated_async_tasks_have_isolated_activity_owners() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        child_entered = asyncio.Event()
        release_child = asyncio.Event()

        async def child() -> None:
            async with gate.activity_async():
                child_entered.set()
                await release_child.wait()

        async with gate.activity_async():
            async with asyncio.TaskGroup() as group:
                group.create_task(child())
                await child_entered.wait()
                assert gate._active_count == 2
                assert len(gate._active_by_owner) == 2
                release_child.set()
            assert gate._active_count == 1

        assert gate._active_count == 0
        assert not gate._active_by_owner

    asyncio.run(exercise())


def test_same_task_nested_async_inline_sync_and_thread_sync_are_reentrant() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        def sync_helper() -> None:
            with gate.activity():
                assert gate._active_count == 2
                assert len(gate._active_by_owner) == 1

        async with gate.activity_async():
            assert gate._active_count == 1
            with gate.activity():
                assert gate._active_count == 2
                assert len(gate._active_by_owner) == 1
            assert gate._active_count == 1

            async with gate.activity_async():
                assert gate._active_count == 2
                assert len(gate._active_by_owner) == 1
            assert gate._active_count == 1

            await asyncio.to_thread(sync_helper)
            assert gate._active_count == 1

        assert gate._active_count == 0
        assert not gate._active_by_owner

    asyncio.run(exercise())


def test_async_activity_errors_release_ownership_exactly_once() -> None:
    class SentinelBaseException(BaseException):
        pass

    gate = CoreActivityGate()

    async def exercise() -> None:
        async def fail(error: BaseException) -> None:
            async with gate.activity_async():
                raise error

        for error in (RuntimeError("activity failed"), SentinelBaseException()):
            with pytest.raises(type(error)):
                await fail(error)
            assert gate._active_count == 0
            assert not gate._active_by_owner

        def acquire_exclusive() -> bool:
            with gate.exclusive():
                return True

        assert await asyncio.wait_for(asyncio.to_thread(acquire_exclusive), timeout=5.0)
        assert gate._active_count == 0
        assert not gate._active_by_owner

    asyncio.run(exercise())


def test_destroyed_async_activity_task_releases_owner_without_context_error() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()
        loop_errors: list[dict[str, Any]] = []

        def exception_handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            loop_errors.append(context)

        loop.set_exception_handler(exception_handler)
        try:

            async def abandoned() -> None:
                async with gate.activity_async():
                    entered.set()
                    await asyncio.Event().wait()

            task = asyncio.create_task(abandoned())
            task_ref = weakref.ref(task)
            await entered.wait()
            del task
            for _ in range(100):
                gc.collect()
                await asyncio.sleep(0)
                if task_ref() is None:
                    break

            assert task_ref() is None
            assert gate._active_count == 0
            assert not gate._active_by_owner
        finally:
            loop.set_exception_handler(None)

        assert not [context for context in loop_errors if context.get("exception") is not None]

    asyncio.run(exercise())


def test_direct_reprocess_activity_is_visible_while_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    service = CoreService(config)
    payload = b'{"kind":"fact","content":"reprocess activity"}\n'
    try:
        imported = service.imports.import_bytes("source.jsonl", payload)
        source_id = str(imported["source"]["id"])
        source = service.store.get_source(source_id, duplicate=True)
        service.store.update_source_import(
            source_id,
            import_status="failed",
            metadata=source.metadata,
            parser_warnings=source.parser_warnings,
        )
        copy_started = threading.Event()
        release_copy = threading.Event()
        original_copy = service.store.copy_source_content_to_path

        def blocked_copy(*args: Any, **kwargs: Any) -> int:
            copy_started.set()
            assert release_copy.wait(timeout=5.0)
            return original_copy(*args, **kwargs)

        monkeypatch.setattr(service.store, "copy_source_content_to_path", blocked_copy)
        result: list[Any] = []
        worker = threading.Thread(
            target=lambda: result.append(service.imports.reprocess_source(source_id)),
            daemon=True,
        )
        worker.start()
        assert copy_started.wait(timeout=5.0)
        assert service.imports.activity_snapshot() == {
            "active": True,
            "count": 1,
            "truncated": False,
        }
        release_copy.set()
        worker.join(timeout=5.0)
        assert not worker.is_alive()
        assert result[0]["source"]["id"] == source_id
        assert service.imports.activity_snapshot()["active"] is False
    finally:
        service.close()


def test_lifespan_cancels_delayed_recovery_timer(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[Any] = []

    class FakeTimer:
        def __init__(self, interval: float, function: Callable[[], object]) -> None:
            self.interval = interval
            self.function = function
            self.daemon = False
            self.started = False
            self.cancelled = False
            self.joined = False
            created.append(self)

        def start(self) -> None:
            self.started = True

        def cancel(self) -> None:
            self.cancelled = True

        def join(self) -> None:
            self.joined = True

    monkeypatch.setattr(core_app.threading, "Timer", FakeTimer)
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    updates = _UpdatesStub(UpdatePhase.INSTALLING)
    app = create_app(config, update_manager=updates)  # type: ignore[arg-type]

    with TestClient(app):
        assert len(created) == 1
        assert created[0].started is True

    assert created[0].cancelled is True
    assert created[0].joined is True
    assert updates.recover_calls == 0


def test_lifespan_drains_running_recovery_callback_before_core_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback_started = threading.Event()
    release_callback = threading.Event()
    events: list[str] = []
    created: list[Any] = []

    class BlockingUpdates(_UpdatesStub):
        def recover_after_restart(self) -> dict[str, Any]:
            events.append("recovery_started")
            callback_started.set()
            assert release_callback.wait(timeout=5.0)
            events.append("recovery_finished")
            return super().recover_after_restart()

    class FakeTimer:
        def __init__(self, interval: float, function: Callable[[], object]) -> None:
            self.interval = interval
            self.function = function
            self.daemon = False
            self.started = False
            self.cancelled = False
            self.joined = False
            self.worker: threading.Thread | None = None
            created.append(self)

        def start(self) -> None:
            self.started = True

        def run_callback(self) -> None:
            self.worker = threading.Thread(target=self.function, daemon=True)
            self.worker.start()

        def cancel(self) -> None:
            self.cancelled = True

        def join(self) -> None:
            self.joined = True
            if self.worker is not None:
                self.worker.join(timeout=5.0)

    monkeypatch.setattr(core_app.threading, "Timer", FakeTimer)
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    updates = BlockingUpdates(UpdatePhase.INSTALLING)
    service = CoreService(config)
    original_close = service.close

    def record_close(*, close_observer: bool = True) -> None:
        events.append("core_close")
        original_close(close_observer=close_observer)

    monkeypatch.setattr(service, "close", record_close)
    app = create_app(config, service=service, update_manager=updates)  # type: ignore[arg-type]
    client = TestClient(app)
    client.__enter__()
    shutdown: threading.Thread | None = None
    try:
        assert len(created) == 1
        created[0].run_callback()
        assert callback_started.wait(timeout=5.0)
        shutdown = threading.Thread(
            target=lambda: client.__exit__(None, None, None),
            daemon=True,
        )
        shutdown.start()
        assert "core_close" not in events
        release_callback.set()
        shutdown.join(timeout=5.0)
        assert not shutdown.is_alive()
    finally:
        if shutdown is not None and shutdown.is_alive():
            release_callback.set()
            shutdown.join(timeout=5.0)
        elif shutdown is None:
            client.__exit__(None, None, None)

    assert created[0].cancelled is True
    assert created[0].joined is True
    assert events.index("recovery_finished") < events.index("core_close")


def test_lifespan_closes_core_after_scheduler_shutdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    service = CoreService(config)
    events: list[str] = []
    original_scheduler_shutdown = service.capture_scheduler.shutdown
    original_close = service.close

    def record_scheduler_shutdown() -> None:
        events.append("scheduler_shutdown")
        original_scheduler_shutdown()

    def record_close(*, close_observer: bool = True) -> None:
        events.append("core_close")
        assert close_observer is False
        original_close(close_observer=close_observer)

    monkeypatch.setattr(service.capture_scheduler, "shutdown", record_scheduler_shutdown)
    monkeypatch.setattr(service, "close", record_close)
    app = create_app(config, service=service)

    with TestClient(app):
        pass

    assert events[0] == "scheduler_shutdown"
    assert "core_close" in events
    assert events.index("scheduler_shutdown") < events.index("core_close")
