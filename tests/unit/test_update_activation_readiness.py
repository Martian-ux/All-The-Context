from __future__ import annotations

import asyncio
import contextvars
import gc
import json
import sys
import threading
import time
import weakref
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
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


def test_http_put_cancellation_sends_sentinel_and_drains_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from allthecontext.import_boundary import ImportCancelledError
    from allthecontext.import_operations import ImportOperationService
    from allthecontext.security import ClientPrincipal

    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    service = CoreService(config)
    app = create_app(config, service=service)
    endpoint = next(
        route.endpoint
        for route in app.routes
        if getattr(route, "path", None) == "/v1/admin/import-operations/{operation_id}/content"
    )
    operation = service.import_operations.start_operation(
        declared_byte_size=1,
        filename="canceled-bridge.bin",
    )
    worker_started = threading.Event()

    def consume_until_sentinel(
        self: ImportOperationService,
        operation_id: str,
        source: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del self, operation_id, kwargs
        worker_started.set()
        list(source)
        raise ImportCancelledError("request canceled")

    monkeypatch.setattr(ImportOperationService, "accept_upload", consume_until_sentinel)

    async def exercise() -> None:
        stream_hold = asyncio.Event()

        class RequestStub:
            def __init__(self) -> None:
                self.headers = {"content-length": "1"}

            async def stream(self) -> Any:
                yield b"x"
                await stream_hold.wait()

        principal = ClientPrincipal(
            id="test-admin",
            name="test-admin",
            scopes=frozenset({"admin"}),
        )
        request_task = asyncio.create_task(
            endpoint(str(operation["operation_id"]), RequestStub(), principal)
        )
        assert await asyncio.to_thread(worker_started.wait, 5.0)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

        assert (
            service.import_operations.get_operation(str(operation["operation_id"]))["status"]
            == "cancelled"
        )
        assert service.activity_gate._active_count == 0
        assert not service.activity_gate._delegated_workers
        assert not service.activity_gate._active_by_owner

    try:
        asyncio.run(exercise())
    finally:
        service.close()


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


def test_no_task_callbacks_and_copied_contexts_cannot_replay_task_admission() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        child_entered: set[str] = set()
        callback_finished = [asyncio.Event(), asyncio.Event()]
        child_tasks: list[asyncio.Task[Any]] = []
        writer_started = threading.Event()
        release_writer = threading.Event()

        async def child(name: str) -> None:
            async with gate.activity_async():
                child_entered.add(name)

        def writer() -> None:
            with gate.exclusive():
                writer_started.set()
                assert release_writer.wait(timeout=5.0)

        def schedule_from_callback(index: int, name: str) -> None:
            child_tasks.append(asyncio.create_task(child(name)))
            callback_finished[index].set()

        async with gate.activity_async():
            writer_task = asyncio.create_task(asyncio.to_thread(writer))
            for _ in range(500):
                if gate._waiting_exclusive:
                    break
                await asyncio.sleep(0.001)
            else:
                pytest.fail("exclusive writer did not begin waiting")

            loop.call_soon(schedule_from_callback, 0, "call-soon")
            copied_context = contextvars.copy_context()
            loop.call_soon(copied_context.run, schedule_from_callback, 1, "copied")
            await asyncio.gather(*(event.wait() for event in callback_finished))
            await asyncio.sleep(0)
            assert child_entered == set()

        assert await asyncio.to_thread(writer_started.wait, 5.0)
        assert child_entered == set()
        release_writer.set()
        await writer_task
        await asyncio.gather(*child_tasks)
        assert child_entered == {"call-soon", "copied"}
        assert gate._active_count == 0
        assert not gate._active_by_owner

    asyncio.run(exercise())


def test_canceled_parent_keeps_worker_exclusive_owner_until_worker_exit() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        worker_started = threading.Event()
        release_worker = threading.Event()
        writer_started = threading.Event()

        def worker() -> None:
            with gate.exclusive():
                worker_started.set()
                assert release_worker.wait(timeout=5.0)

        def writer() -> None:
            with gate.exclusive():
                writer_started.set()

        dispatch = asyncio.create_task(gate.run_in_threadpool(worker))
        assert await asyncio.to_thread(worker_started.wait, 5.0)
        dispatch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await dispatch
        del dispatch
        assert gate._exclusive_owner is not None
        assert len(gate._delegated_workers) == 1

        writer_task = asyncio.create_task(asyncio.to_thread(writer))
        for _ in range(500):
            if gate._waiting_exclusive:
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail("exclusive writer did not begin waiting")
        await asyncio.sleep(0.03)
        assert not writer_started.is_set()

        release_worker.set()
        await writer_task
        assert writer_started.is_set()
        assert gate._active_count == 0
        assert not gate._active_by_owner
        assert not gate._delegated_workers

    asyncio.run(exercise())


def test_sync_activity_callback_fails_without_blocking_event_loop() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        callback_finished = asyncio.Event()
        callback_errors: list[BaseException] = []
        writer_started = threading.Event()
        release_writer = threading.Event()

        def writer() -> None:
            with gate.exclusive():
                writer_started.set()
                assert release_writer.wait(timeout=5.0)

        def callback() -> None:
            try:
                with gate.activity():
                    raise AssertionError("event-loop callback bypassed activity admission")
            except BaseException as error:
                callback_errors.append(error)
            finally:
                callback_finished.set()

        async with gate.activity_async():
            writer_task = asyncio.create_task(asyncio.to_thread(writer))
            for _ in range(500):
                if gate._waiting_exclusive:
                    break
                await asyncio.sleep(0.001)
            else:
                pytest.fail("exclusive writer did not begin waiting")
            asyncio.get_running_loop().call_soon(callback)
            await asyncio.wait_for(callback_finished.wait(), timeout=1.0)
            assert len(callback_errors) == 1
            assert isinstance(callback_errors[0], RuntimeError)
            assert not writer_started.is_set()

        assert await asyncio.to_thread(writer_started.wait, 5.0)
        release_writer.set()
        await writer_task
        assert gate._active_count == 0

    asyncio.run(exercise())


def test_shutdown_async_fences_new_work_and_drains_existing_activity() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        active_started = asyncio.Event()
        release_active = asyncio.Event()
        shutdown_entered = asyncio.Event()

        async def active() -> None:
            async with gate.activity_async():
                active_started.set()
                await release_active.wait()

        async def shutdown() -> None:
            async with gate.shutdown_async():
                shutdown_entered.set()

        active_task = asyncio.create_task(active())
        await active_started.wait()
        shutdown_task = asyncio.create_task(shutdown())
        for _ in range(500):
            if gate._closing:
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail("shutdown did not close admission")

        async def new_activity() -> None:
            async with gate.activity_async():
                pass

        with pytest.raises(RuntimeError, match="shutting down"):
            await new_activity()
        assert not shutdown_entered.is_set()
        release_active.set()
        await active_task
        await shutdown_task
        assert shutdown_entered.is_set()
        assert gate._active_count == 0
        assert not gate._active_by_owner
        assert not gate._closing

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
                assert gate._active_count == 4
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

            await gate.run_in_threadpool(sync_helper)
            assert gate._active_count == 1

        assert gate._active_count == 0
        assert not gate._active_by_owner

    asyncio.run(exercise())


def test_canceled_worker_lease_keeps_writer_out_until_worker_exits() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        worker_started = threading.Event()
        release_worker = threading.Event()
        writer_started = threading.Event()

        def worker() -> None:
            worker_started.set()
            assert release_worker.wait(timeout=5.0)

        def writer() -> None:
            with gate.exclusive():
                writer_started.set()

        dispatch = asyncio.create_task(gate.run_in_threadpool(worker))
        dispatch_ref = weakref.ref(dispatch)
        assert await asyncio.to_thread(worker_started.wait, 5.0)
        dispatch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await dispatch
        del dispatch
        assert sys.version_info >= (3, 12)
        assert len(gate._delegated_workers) == 1
        for _ in range(100):
            gc.collect()
            await asyncio.sleep(0)
            if dispatch_ref() is None:
                break
        assert dispatch_ref() is None

        writer_task = asyncio.create_task(asyncio.to_thread(writer))
        for _ in range(500):
            if gate._waiting_exclusive:
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail("exclusive writer did not begin waiting")
        await asyncio.sleep(0.03)
        assert not writer_started.is_set()

        release_worker.set()
        await writer_task
        assert writer_started.is_set()
        assert gate._active_count == 0
        assert not gate._active_by_owner

    asyncio.run(exercise())


def test_worker_lease_exclusive_upgrade_does_not_self_join() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        entered = threading.Event()

        def worker() -> None:
            with gate.exclusive():
                entered.set()

        async with gate.activity_async():
            await gate.run_in_threadpool(worker)
            assert entered.is_set()
            assert gate._active_count == 1

        assert gate._active_count == 0
        assert not gate._active_by_owner

    asyncio.run(exercise())


def test_canceled_worker_dispatch_before_start_releases_lease_safely() -> None:
    gate = CoreActivityGate()

    async def exercise() -> None:
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(executor)
        blocker_started = threading.Event()
        release_blocker = threading.Event()
        worker_ran = threading.Event()
        writer_started = threading.Event()

        def blocker() -> None:
            blocker_started.set()
            assert release_blocker.wait(timeout=5.0)

        def never_started_worker() -> None:
            worker_ran.set()

        def writer() -> None:
            with gate.exclusive():
                writer_started.set()

        blocker_task = asyncio.create_task(asyncio.to_thread(blocker))
        for _ in range(500):
            if blocker_started.is_set():
                break
            await asyncio.sleep(0.001)
        else:
            pytest.fail("default executor blocker did not begin")
        dispatch = asyncio.create_task(gate.run_in_threadpool(never_started_worker))
        await asyncio.sleep(0)
        dispatch.cancel()
        with pytest.raises(asyncio.CancelledError):
            await dispatch
        for _ in range(100):
            if gate._active_count == 0:
                break
            await asyncio.sleep(0)
        else:
            pytest.fail("canceled worker dispatch did not release its lease")
        assert not gate._active_by_owner

        release_blocker.set()
        await blocker_task
        await asyncio.to_thread(writer)
        assert writer_started.is_set()
        assert not worker_ran.is_set()

    asyncio.run(exercise())


def test_worker_exception_and_base_exception_release_lease_once() -> None:
    class SentinelBaseException(BaseException):
        pass

    gate = CoreActivityGate()

    async def exercise() -> None:
        def fail(error: BaseException) -> None:
            raise error

        for error in (RuntimeError("worker failed"), SentinelBaseException()):
            with pytest.raises(type(error)):
                await gate.run_in_threadpool(fail, error)
            assert gate._active_count == 0
            assert not gate._active_by_owner

        writer_started = threading.Event()

        def writer() -> None:
            with gate.exclusive():
                writer_started.set()

        await asyncio.to_thread(writer)
        assert writer_started.is_set()

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


def test_lifespan_core_close_waits_for_gate_drain_and_holds_exclusive_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    service = CoreService(config)
    gate = service.activity_gate
    reader_entered = threading.Event()
    release_reader = threading.Event()
    close_state: list[tuple[bool, int, bool]] = []
    original_close = service.close

    def reader() -> None:
        with gate.activity():
            reader_entered.set()
            assert release_reader.wait(timeout=5.0)

    def record_close(*, close_observer: bool = True) -> None:
        with gate._condition:
            close_state.append(
                (
                    gate._exclusive_owner is not None,
                    gate._active_count,
                    all(owner == gate._exclusive_owner for owner in gate._active_by_owner),
                )
            )
        original_close(close_observer=close_observer)

    monkeypatch.setattr(service, "close", record_close)
    app = create_app(config, service=service)
    client = TestClient(app)
    client.__enter__()
    reader_thread = threading.Thread(target=reader, daemon=True)
    shutdown_thread: threading.Thread | None = None
    rejected: list[BaseException] = []
    try:
        reader_thread.start()
        assert reader_entered.wait(timeout=5.0)
        shutdown_thread = threading.Thread(
            target=lambda: client.__exit__(None, None, None),
            daemon=True,
        )
        shutdown_thread.start()
        for _ in range(500):
            if gate._closing:
                break
            time.sleep(0.001)
        else:
            pytest.fail("lifespan shutdown did not close admission")
        assert not close_state

        def attempt_after_shutdown() -> None:
            try:
                with gate.activity():
                    pass
            except BaseException as error:
                rejected.append(error)

        rejected_thread = threading.Thread(target=attempt_after_shutdown, daemon=True)
        rejected_thread.start()
        rejected_thread.join(timeout=5.0)
        assert not rejected_thread.is_alive()
        assert len(rejected) == 1
        assert isinstance(rejected[0], RuntimeError)

        release_reader.set()
        reader_thread.join(timeout=5.0)
        shutdown_thread.join(timeout=10.0)
        assert not reader_thread.is_alive()
        assert not shutdown_thread.is_alive()
    finally:
        release_reader.set()
        reader_thread.join(timeout=5.0)
        if shutdown_thread is None:
            client.__exit__(None, None, None)
        elif shutdown_thread.is_alive():
            shutdown_thread.join(timeout=10.0)

    assert close_state
    assert close_state[0][0] is True
    assert close_state[0][2] is True


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
