from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
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
