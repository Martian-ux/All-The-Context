"""Focused sanitized proof for the Core-owned Packet E scheduler slice."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from allthecontext import capture_runtime, cli
from allthecontext.capture import (
    CaptureCoordinator,
    CaptureError,
    CaptureEvent,
    CapturePage,
    DeterministicFakeAdapter,
    IdempotentFakeSink,
)
from allthecontext.capture_runtime import (
    AUTHORIZATION_FILENAME,
    SCHEDULER_CONFIG_FILENAME,
    authorization_path,
    authorize_local_workspace,
    scheduler_config_path,
    write_scheduler_enabled,
)
from allthecontext.capture_scheduler import (
    CAPTURE_SCHEDULER_ENABLED_ENV,
    UPDATE_HEALTH_OPERATION_ENV,
    CoreCaptureScheduler,
    SchedulerConfig,
    _is_transient_sqlite_contention,
    scheduler_update_health_forced_off,
)
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.experimental_local_git_workspace_connector import (
    LOCAL_GIT_WORKSPACE_PROVIDER,
)
from allthecontext.models import ClientCreate
from allthecontext.storage import CoreStore
from fastapi.testclient import TestClient

from tests.fixtures.local_git_workspace import create_sanitized_workspace
from tests.unit.test_capture_scheduler import _MutableClock, _page, _source

_NO_FACT_RELATIVE_PATH = "notes/data.json"


def _workspace(tmp_path: Path) -> Path:
    root = create_sanitized_workspace(tmp_path / "workspace")
    target = root / _NO_FACT_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('{"fixture":true}\n', encoding="utf-8", newline="\n")
    return root.resolve()


def _config(tmp_path: Path) -> CoreConfig:
    return CoreConfig.in_directory(tmp_path / "core", require_auth=True)


def _path_leak_forms(path: Path) -> frozenset[str]:
    resolved = path.resolve()
    forms = {
        str(path),
        str(resolved),
        path.as_posix(),
        resolved.as_posix(),
        os.fspath(path),
        os.fspath(resolved),
    }
    escaped = {json.dumps(form)[1:-1] for form in forms}
    return frozenset(form for form in forms | escaped if form)


def _assert_no_root_leak(material: Any, *roots: Path) -> None:
    rendered = material if isinstance(material, str) else json.dumps(material, default=str)
    for root in roots:
        for form in _path_leak_forms(root):
            assert form not in rendered


def _cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    original = sys.argv
    try:
        sys.argv = argv
        cli.main()
    finally:
        sys.argv = original
    return cast(dict[str, Any], json.loads(capsys.readouterr().out))


def _event(event_id: str = "scheduler-product-event") -> CaptureEvent:
    return CaptureEvent(
        provider_event_id=event_id,
        provider_item_id="scheduler-product-item",
        order_key="1",
        payload={"fixture": "scheduler-product"},
        generation=1,
    )


def _open_process_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(CAPTURE_SCHEDULER_ENABLED_ENV, "1")
    monkeypatch.delenv(UPDATE_HEALTH_OPERATION_ENV, raising=False)


def _sqlite_operational_error(message: str, code: int) -> sqlite3.OperationalError:
    error = sqlite3.OperationalError(message)
    error.sqlite_errorcode = code
    return error


def _wait_until(predicate: Any, *, timeout: float = 5.0, interval: float = 0.01) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError("condition was not met before timeout")


def _blocking_core_scheduler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    join_timeout_seconds: float = 0.2,
    poll_interval_seconds: int = 1,
) -> tuple[CoreCaptureScheduler, threading.Event, threading.Event, CoreStore]:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    clock = _MutableClock()
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    started = threading.Event()
    release = threading.Event()

    class _BlockingAdapter(DeterministicFakeAdapter):
        def fetch_page(self, source: Any, cursor: str | None, page_order: int) -> CapturePage:
            started.set()
            assert release.wait(timeout=5)
            return super().fetch_page(source, cursor, page_order)

    coordinator.register_adapter("fake", _BlockingAdapter((_page(_event()),)))
    scheduler = CoreCaptureScheduler(
        coordinator,
        config,
        scheduler_config=SchedulerConfig(poll_interval_seconds=poll_interval_seconds),
        clock=clock,
        join_timeout_seconds=join_timeout_seconds,
    )
    write_scheduler_enabled(config.data_dir, enabled=True)
    return scheduler, started, release, store


def test_scheduler_disabled_by_default_without_env_or_sidecar(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with CoreService(config) as service:
        status = service.capture_scheduler.status()
        report = service.capture_scheduler.run_cycle()
        assert status["enabled"] is False
        assert status["durable_enabled"] is False
        assert status["dispatch_allowed"] is False
        assert status["running"] is False
        assert status["reason_code"] == "process_gate_closed"
        assert report.plan.enabled is False
        assert report.dispatched == ()
        assert service.capture_scheduler._scheduler.config.max_workers == 1


def test_explicit_enable_survives_restart_when_process_gate_stays_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    with CoreService(config) as first:
        enabled = first.capture_scheduler.enable()
        assert enabled["durable_enabled"] is True
        assert enabled["dispatch_allowed"] is True
        first.capture_scheduler.stop()
    with CoreService(config) as restarted:
        status = restarted.capture_scheduler.status()
        assert status["durable_enabled"] is True
        assert status["dispatch_allowed"] is True
        assert status["reason_code"] == "enabled"
        restarted.capture_scheduler.start()
        assert restarted.capture_scheduler.status()["running"] is True
        restarted.capture_scheduler.stop()


def test_disable_clears_durable_enablement_and_stops_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    with CoreService(config) as service:
        service.capture_scheduler.enable()
        assert service.capture_scheduler.status()["running"] is True
        disabled = service.capture_scheduler.disable()
        assert disabled["durable_enabled"] is False
        assert disabled["dispatch_allowed"] is False
        assert disabled["running"] is False
        assert disabled["reason_code"] == "disabled"


def test_due_execution_runs_enabled_workspace_source_through_shared_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    with CoreService(config) as service:
        authorized = authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        source_id = str(authorized["id"])
        service.capture.enable(source_id)
        write_scheduler_enabled(config.data_dir, enabled=True)
        report = service.capture_scheduler.run_cycle()
        assert LOCAL_GIT_WORKSPACE_PROVIDER in service.capture.adapters
        assert report.plan.enabled is True
        assert report.dispatched[0].source_id == source_id
        assert report.results[0].status == "completed"
        assert report.results[0].applied_events == 5
        with service.store.connect() as connection:
            records = connection.execute("SELECT COUNT(*) FROM context_records").fetchone()[0]
        assert records == 4
        _assert_no_root_leak(service.capture_scheduler.status(), workspace, config.data_dir)


def test_scheduler_does_not_overlap_in_flight_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    clock = _MutableClock()
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    started = threading.Event()
    release = threading.Event()

    class _BlockingAdapter(DeterministicFakeAdapter):
        def fetch_page(self, source: Any, cursor: str | None, page_order: int) -> CapturePage:
            started.set()
            assert release.wait(timeout=5)
            return super().fetch_page(source, cursor, page_order)

    coordinator.register_adapter("fake", _BlockingAdapter((_page(_event()),)))
    scheduler = CoreCaptureScheduler(
        coordinator,
        config,
        scheduler_config=SchedulerConfig(poll_interval_seconds=1),
        clock=clock,
    )
    write_scheduler_enabled(config.data_dir, enabled=True)
    worker = threading.Thread(target=scheduler.run_cycle, daemon=True)
    worker.start()
    assert started.wait(timeout=5)
    overlapping = scheduler.run_cycle()
    assert overlapping.dispatched == ()
    assert overlapping.results == ()
    release.set()
    worker.join(timeout=5)
    assert worker.is_alive() is False
    store.close()


def test_expired_reconciling_becomes_due_after_core_start_and_cycle_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    with CoreService(config) as service:
        authorized = authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        source_id = str(authorized["id"])
        service.capture.enable(source_id)
        handle, _source_row, _attempt = service.capture.ledger.begin_run(source_id)
        assert service.capture.get_source(source_id).lifecycle_state == "reconciling"
        with service.store.transaction() as connection:
            connection.execute(
                "UPDATE capture_runs SET lease_expires_at=? WHERE id=?",
                ("2000-01-01T00:00:00.000000Z", handle.run_id),
            )
    with CoreService(config) as restarted:
        assert restarted.capture.get_source(source_id).lifecycle_state == "degraded"
        write_scheduler_enabled(config.data_dir, enabled=True)
        report = restarted.capture_scheduler.run_cycle()
        assert report.dispatched[0].kind == "retry"
        assert report.results[0].status == "completed"
        assert restarted.capture.get_source(source_id).lifecycle_state == "enabled"


def test_refresh_after_authorization_matches_admin_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    with CoreService(config) as service:
        assert service.capture.adapters == {}
        authorized = authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        source_id = str(authorized["id"])
        service.capture.enable(source_id)
        assert service.capture.adapters == {}
        write_scheduler_enabled(config.data_dir, enabled=True)
        report = service.capture_scheduler.run_cycle()
        assert LOCAL_GIT_WORKSPACE_PROVIDER in service.capture.adapters
        assert report.results[0].status == "completed"
        assert report.results[0].applied_events == 5


def test_scheduler_recovers_exhausted_adapter_unavailable_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    with CoreService(config) as service:
        authorized = authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        source_id = str(authorized["id"])
        service.capture.enable(source_id)

        authorization_path(config.data_dir).write_text("{not-json", encoding="utf-8")
        assert capture_runtime.refresh_local_workspace_adapter(service.capture, config) is False
        for _ in range(3):
            service.capture._mark_unavailable(source_id)
        exhausted = service.capture.get_source(source_id)
        assert exhausted.lifecycle_state == "degraded"
        assert exhausted.retry_count == 3
        assert exhausted.last_error_code == "capture_adapter_unavailable"
        readiness = service.capture_scheduler.readiness()
        source_readiness = readiness["capture"]["sources"][0]
        assert source_readiness["retry_exhausted"] is True
        assert source_readiness["retry_reason_code"] == "capture_retry_exhausted"
        assert "capture_adapter_unavailable" in readiness["capture"]["reason_codes"]

        restored = authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        assert restored["id"] == source_id

        write_scheduler_enabled(config.data_dir, enabled=True)
        report = service.capture_scheduler.run_cycle()
        recovered = service.capture.get_source(source_id)

    assert report.results[0].status == "completed"
    assert recovered.lifecycle_state == "enabled"
    assert recovered.retry_count == 0
    assert recovered.next_retry_at is None
    assert recovered.last_error_code is None


def test_adapter_refresh_retries_transient_registration_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    with CoreService(config) as service:
        authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        original = capture_runtime._try_register_authorized_adapter
        calls = 0

        def fail_once(coordinator: Any, candidate: CoreConfig) -> bool:
            nonlocal calls
            calls += 1
            if calls == 1:
                return False
            return original(coordinator, candidate)

        monkeypatch.setattr(capture_runtime, "_try_register_authorized_adapter", fail_once)
        assert capture_runtime.refresh_local_workspace_adapter(service.capture, config) is True

    assert calls == 2


def test_authenticated_status_exposes_readiness_but_health_stays_liveness_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    with CoreService(config) as service:
        authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        _principal, token = service.store.create_client(
            ClientCreate(name="readiness-reader", scopes=["context:status"], auto_approve=False)
        )
        app = create_app(config, service=service)
        with TestClient(app) as client:
            health = client.get("/health")
            status = client.get(
                "/v1/context/status",
                headers={"Authorization": f"Bearer {token}"},
            )

    assert health.status_code == 200
    assert health.json() == {"status": "ok", "component": "core"}
    assert status.status_code == 200, status.text
    readiness = status.json()["runtime_readiness"]
    assert readiness["scheduler"]["alive"] is False
    assert readiness["scheduler"]["worker_state"] == "not_started"
    assert readiness["capture"]["state"] == "healthy"
    assert readiness["project_projection"] == {
        "available": True,
        "reason_code": None,
        "state": "available",
    }
    assert "scheduler" not in health.json()
    _assert_no_root_leak(status.json(), workspace, config.data_dir)


def test_worker_failure_is_content_free_and_restartable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, sink=IdempotentFakeSink())
    scheduler = CoreCaptureScheduler(coordinator, config, scheduler_config=SchedulerConfig())
    write_scheduler_enabled(config.data_dir, enabled=True)

    def boom() -> bool:
        raise TypeError("private raw failure must not be surfaced")

    observed: list[type[BaseException]] = []
    monkeypatch.setattr(
        threading,
        "excepthook",
        lambda args: observed.append(args.exc_type),
    )

    try:
        scheduler.start()
        scheduler.dispatch_allowed = boom  # type: ignore[method-assign]
        scheduler._wakeup.set()
        _wait_until(lambda: scheduler.status()["worker_state"] == "failed")
        failed = scheduler.status()
        assert failed["running"] is False
        assert failed["worker_failure_code"] == "worker_failed"
        assert failed["worker_restartable"] is True
        assert "private raw failure" not in json.dumps(failed)
        assert observed == [TypeError]

        monkeypatch.setattr(scheduler, "dispatch_allowed", lambda: True)
        scheduler.start()
        _wait_until(lambda: scheduler.status()["worker_state"] == "running")
        assert scheduler.status()["worker_restart_count"] == 2
    finally:
        scheduler.shutdown()
        store.close()


def test_invalid_scheduler_config_fail_closes_without_killing_core(tmp_path: Path) -> None:
    config = _config(tmp_path)
    secret = "sk-scheduler-do-not-leak"
    poison = tmp_path / "poison-root"
    poison.mkdir()
    with CoreService(config) as service:
        scheduler_config_path(config.data_dir).write_text(
            json.dumps(
                {
                    "enabled": True,
                    "path": str(poison),
                    "token": secret,
                    "version": 1,
                }
            ),
            encoding="utf-8",
        )
        status = service.capture_scheduler.status()
        report = service.capture_scheduler.run_cycle()
        assert service.store.status()["vault_id"]
        assert status["config_valid"] is False
        assert status["dispatch_allowed"] is False
        assert status["reason_code"] == "invalid_config"
        assert report.dispatched == ()
        rendered = json.dumps(status)
        assert secret not in rendered
        assert SCHEDULER_CONFIG_FILENAME not in rendered
        assert AUTHORIZATION_FILENAME not in rendered
        _assert_no_root_leak(status, poison, config.data_dir)


def test_update_health_operation_force_disables_scheduler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CAPTURE_SCHEDULER_ENABLED_ENV, "1")
    monkeypatch.setenv(UPDATE_HEALTH_OPERATION_ENV, "1")
    config = _config(tmp_path)
    with CoreService(config) as service:
        enabled = service.capture_scheduler.enable()
        assert enabled["durable_enabled"] is True
        assert enabled["update_health_forced_off"] is True
        assert enabled["dispatch_allowed"] is False
        assert enabled["running"] is False
        assert enabled["reason_code"] == "forced_off"
        service.capture_scheduler.start()
        assert service.capture_scheduler.status()["running"] is False
        app = create_app(config, service=service)
        with TestClient(app) as client:
            assert client.get("/health").json() == {"status": "ok", "component": "core"}
            assert service.capture_scheduler.status()["running"] is False


def test_prompt_stop_join_and_idempotent_close(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    with CoreService(config) as service:
        service.capture_scheduler.enable()
        assert service.capture_scheduler.status()["running"] is True
        started = time.monotonic()
        service.capture_scheduler.stop()
        elapsed = time.monotonic() - started
        assert elapsed < 1.0
        assert service.capture_scheduler.status()["running"] is False
        service.capture_scheduler.stop()
        service.close()
        service.close()


def test_health_body_unchanged_when_scheduler_is_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    with CoreService(config) as service:
        principal, token = service.store.create_client(
            ClientCreate(name="capture-admin", scopes=["admin"], auto_approve=False)
        )
        assert principal.id
        service.capture_scheduler.enable()
        app = create_app(config, service=service)
        with TestClient(app) as client:
            health = client.get("/health")
            capture_status = client.get(
                "/v1/admin/capture/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            scheduler = client.get(
                "/v1/admin/capture/scheduler",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert health.json() == {"status": "ok", "component": "core"}
        assert "scheduler" not in health.json()
        assert capture_status.status_code == 200
        body = capture_status.json()
        assert body["scheduler"]["dispatch_allowed"] is True
        assert scheduler.json()["reason_code"] == "enabled"
        _assert_no_root_leak(body, config.data_dir)


def test_authenticated_scheduler_endpoints_and_cli_status_enable_disable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    with CoreService(config) as service:
        admin, token = service.store.create_client(
            ClientCreate(name="capture-admin", scopes=["admin"], auto_approve=False)
        )
        reader, reader_token = service.store.create_client(
            ClientCreate(name="capture-reader", scopes=["context:read"], auto_approve=False)
        )
        assert admin.id and reader.id
        app = create_app(config, service=service)
        with TestClient(app) as client:
            assert client.get("/v1/admin/capture/scheduler").status_code == 401
            assert (
                client.post(
                    "/v1/admin/capture/scheduler/enable",
                    headers={"Authorization": f"Bearer {reader_token}"},
                ).status_code
                == 403
            )
            enabled = client.post(
                "/v1/admin/capture/scheduler/enable",
                headers={"Authorization": f"Bearer {token}"},
            )
            status = client.get(
                "/v1/admin/capture/scheduler",
                headers={"Authorization": f"Bearer {token}"},
            )
            capture_status = client.get(
                "/v1/admin/capture/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            disabled = client.post(
                "/v1/admin/capture/scheduler/disable",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert enabled.status_code == 200
        assert enabled.json()["durable_enabled"] is True
        assert status.json()["dispatch_allowed"] is True
        assert capture_status.json()["scheduler"]["reason_code"] == "enabled"
        assert disabled.json()["durable_enabled"] is False
        _assert_no_root_leak(enabled.json(), config.data_dir)
        _assert_no_root_leak(status.json(), config.data_dir)

    cli_status = _cli(
        ["atc", "capture", "scheduler", "status", "--data-dir", str(config.data_dir)],
        capsys,
    )
    assert cli_status["durable_enabled"] is False
    assert "running" not in cli_status
    cli_enabled = _cli(
        ["atc", "capture", "scheduler", "enable", "--data-dir", str(config.data_dir)],
        capsys,
    )
    assert cli_enabled["durable_enabled"] is True
    assert "running" not in cli_enabled
    cli_disabled = _cli(
        ["atc", "capture", "scheduler", "disable", "--data-dir", str(config.data_dir)],
        capsys,
    )
    assert cli_disabled["durable_enabled"] is False
    assert "running" not in cli_disabled
    help_text = cli.build_parser().format_help()
    assert "run_forever" not in help_text
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["capture", "scheduler", "run_forever"])
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["capture", "scheduler", "run"])


def test_status_does_not_mutate_reauth_or_rotation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    clock = _MutableClock()
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    scheduler = CoreCaptureScheduler(coordinator, config, clock=clock)
    write_scheduler_enabled(config.data_dir, enabled=True)
    inner = scheduler._scheduler
    inner.enable()
    before_offset = inner._source_rotation_offset
    before_reauth = set(inner._announced_reauthorization)
    first = scheduler.status()
    second = scheduler.status()
    assert first == second
    assert inner._source_rotation_offset == before_offset
    assert inner._announced_reauthorization == before_reauth
    store.close()


def test_bounded_join_returns_during_in_flight_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    clock = _MutableClock()
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    source_id = _source(coordinator)
    coordinator.enable(source_id)
    started = threading.Event()
    release = threading.Event()

    class _BlockingAdapter(DeterministicFakeAdapter):
        def fetch_page(self, source: Any, cursor: str | None, page_order: int) -> CapturePage:
            started.set()
            assert release.wait(timeout=5)
            return super().fetch_page(source, cursor, page_order)

    coordinator.register_adapter("fake", _BlockingAdapter((_page(_event()),)))
    scheduler = CoreCaptureScheduler(
        coordinator,
        config,
        scheduler_config=SchedulerConfig(poll_interval_seconds=1),
        clock=clock,
        join_timeout_seconds=0.2,
    )
    write_scheduler_enabled(config.data_dir, enabled=True)
    try:
        scheduler.start()
        assert started.wait(timeout=5)
        began = time.monotonic()
        scheduler.stop()
        assert time.monotonic() - began < 1.0
    finally:
        release.set()
        scheduler.shutdown()
        store.close()


def test_loop_converges_when_gate_closes_between_dispatch_and_activity_enter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    clock = _MutableClock()
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    scheduler = CoreCaptureScheduler(
        coordinator,
        config,
        scheduler_config=SchedulerConfig(poll_interval_seconds=1),
        clock=clock,
    )
    write_scheduler_enabled(config.data_dir, enabled=True)
    enter_started = threading.Event()
    allow_enter = threading.Event()
    shutdown_entered = threading.Event()
    release_shutdown = threading.Event()
    observed: list[BaseException | None] = []
    shutdown_errors: list[BaseException] = []
    original_enter = scheduler.activity_gate._enter
    calling_thread_id = threading.get_ident()

    def blocked_enter(owner: object, lease: Any = None) -> None:
        if threading.get_ident() == calling_thread_id:
            original_enter(owner, lease)
            return
        enter_started.set()
        if not allow_enter.wait(timeout=5):
            raise AssertionError("scheduler activity enter was not released")
        original_enter(owner, lease)

    monkeypatch.setattr(scheduler.activity_gate, "_enter", blocked_enter)

    def hook(args: threading.ExceptHookArgs) -> None:
        observed.append(args.exc_value)

    monkeypatch.setattr(threading, "excepthook", hook)

    async def hold_gate_shutdown() -> None:
        async with scheduler.activity_gate.shutdown_async():
            shutdown_entered.set()
            await asyncio.to_thread(release_shutdown.wait, 5.0)

    def close_gate() -> None:
        try:
            asyncio.run(hold_gate_shutdown())
        except BaseException as error:
            shutdown_errors.append(error)

    gate_closer = threading.Thread(target=close_gate)
    try:
        scheduler.start()
        assert enter_started.wait(timeout=5)
        gate_closer.start()
        assert shutdown_entered.wait(timeout=5)
        allow_enter.set()
        scheduler.shutdown()
        release_shutdown.set()
        gate_closer.join(timeout=5)
        assert gate_closer.is_alive() is False
        assert shutdown_errors == []
        assert observed == []
        assert scheduler.status()["running"] is False
    finally:
        allow_enter.set()
        release_shutdown.set()
        scheduler.shutdown()
        if gate_closer.ident is not None:
            gate_closer.join(timeout=5)
        store.close()


def test_productized_cycle_caps_one_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    clock = _MutableClock()
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    source_ids = [_source(coordinator) for _ in range(2)]
    for source_id in source_ids:
        coordinator.enable(source_id)
    coordinator.register_adapter("fake", DeterministicFakeAdapter((_page(_event()),)))
    scheduler = CoreCaptureScheduler(
        coordinator,
        config,
        scheduler_config=SchedulerConfig(enabled=True, max_workers=4),
        clock=clock,
    )
    write_scheduler_enabled(config.data_dir, enabled=True)
    report = scheduler.run_cycle()
    assert scheduler._scheduler.config.max_workers == 1
    assert len(report.dispatched) == 1
    assert len(report.deferred) == 1
    store.close()


def _subparser_choices(parser: Any, dest: str) -> dict[str, Any]:
    for action in parser._actions:
        if getattr(action, "dest", None) == dest:
            choices = getattr(action, "choices", None)
            if isinstance(choices, dict):
                return choices
    return {}


def test_cli_has_no_capture_scheduler_run_forever_entrypoint() -> None:
    parser = cli.build_parser()
    capture_parser = _subparser_choices(parser, "command").get("capture")
    assert capture_parser is not None
    capture_choices = _subparser_choices(capture_parser, "capture_action")
    assert "scheduler" in capture_choices
    assert "run_forever" not in capture_choices
    scheduler_parser = capture_choices["scheduler"]
    scheduler_choices = _subparser_choices(scheduler_parser, "scheduler_action")
    assert set(scheduler_choices) == {"status", "enable", "disable"}
    capture_help = capture_parser.format_help()
    scheduler_help = scheduler_parser.format_help()
    assert "run_forever" not in capture_help
    assert "run_forever" not in scheduler_help
    assert "never a daemon" in capture_help.lower()


def test_shutdown_waits_until_worker_is_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, started, release, store = _blocking_core_scheduler(tmp_path, monkeypatch)
    try:
        scheduler.start()
        assert started.wait(timeout=5)
        finished = threading.Event()

        def do_shutdown() -> None:
            scheduler.shutdown()
            finished.set()

        worker = threading.Thread(target=do_shutdown)
        worker.start()
        assert finished.wait(timeout=0.3) is False
        assert scheduler.status()["running"] is True
        release.set()
        assert finished.wait(timeout=5) is True
        worker.join(timeout=5)
        assert worker.is_alive() is False
        assert scheduler.status()["running"] is False
        assert scheduler._thread is None or scheduler._thread.is_alive() is False
        scheduler.shutdown()
        assert scheduler.status()["running"] is False
    finally:
        release.set()
        scheduler.shutdown()
        store.close()


def test_shutdown_fence_blocks_enable_start_from_reviving_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, started, release, store = _blocking_core_scheduler(tmp_path, monkeypatch)
    try:
        scheduler.start()
        assert started.wait(timeout=5)
        finished = threading.Event()

        def do_shutdown() -> None:
            scheduler.shutdown()
            finished.set()

        worker = threading.Thread(target=do_shutdown)
        worker.start()
        assert finished.wait(timeout=0.3) is False
        assert scheduler.status()["running"] is True
        enabled = scheduler.enable()
        assert enabled["durable_enabled"] is True
        scheduler.start()
        assert finished.wait(timeout=0.3) is False
        assert scheduler.status()["running"] is True
        assert scheduler._closing.is_set() is True
        release.set()
        assert finished.wait(timeout=5) is True
        worker.join(timeout=5)
        assert worker.is_alive() is False
        assert scheduler.status()["running"] is False
        assert scheduler._thread is None or scheduler._thread.is_alive() is False
        revived = scheduler.enable()
        scheduler.start()
        assert revived["durable_enabled"] is True
        assert scheduler.status()["running"] is False
        assert scheduler._thread is None or scheduler._thread.is_alive() is False
    finally:
        release.set()
        scheduler.shutdown()
        store.close()


def test_core_service_close_waits_until_scheduler_thread_is_dead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    service = CoreService(config)
    started = threading.Event()
    release = threading.Event()
    original = service.capture_scheduler.run_cycle

    def blocking_cycle() -> Any:
        started.set()
        assert release.wait(timeout=5)
        return original()

    try:
        service.capture_scheduler.run_cycle = blocking_cycle  # type: ignore[method-assign]
        service.capture_scheduler.enable()
        service.capture_scheduler._wakeup.set()
        assert started.wait(timeout=5)
        finished = threading.Event()

        def do_close() -> None:
            service.close()
            finished.set()

        closer = threading.Thread(target=do_close)
        closer.start()
        assert finished.wait(timeout=0.3) is False
        assert service.capture_scheduler.status()["running"] is True
        release.set()
        assert finished.wait(timeout=5) is True
        closer.join(timeout=5)
        assert closer.is_alive() is False
        assert service.capture_scheduler.status()["running"] is False
    finally:
        release.set()
        service.close()


def test_disable_during_in_flight_then_enable_eventually_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, started, release, store = _blocking_core_scheduler(tmp_path, monkeypatch)
    try:
        scheduler.start()
        assert started.wait(timeout=5)
        began = time.monotonic()
        disabled = scheduler.disable()
        assert time.monotonic() - began < 1.0
        assert disabled["durable_enabled"] is False
        enabled = scheduler.enable()
        assert enabled["durable_enabled"] is True
        assert enabled["dispatch_allowed"] is True
        _wait_until(lambda: scheduler.status()["running"] is True)
        release.set()
        _wait_until(lambda: scheduler.status()["running"] is True)
        assert scheduler.status()["reason_code"] == "enabled"
    finally:
        release.set()
        scheduler.shutdown()
        store.close()


def test_ordered_last_writer_enable_then_disable_is_coherent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, started, release, store = _blocking_core_scheduler(tmp_path, monkeypatch)
    try:
        scheduler.start()
        assert started.wait(timeout=5)
        enabled = scheduler.enable()
        assert enabled["durable_enabled"] is True
        began = time.monotonic()
        disabled = scheduler.disable()
        assert time.monotonic() - began < 1.0
        assert disabled["durable_enabled"] is False
        assert disabled["dispatch_allowed"] is False
        release.set()
        _wait_until(lambda: scheduler.status()["running"] is False)
        assert scheduler.status()["durable_enabled"] is False
        assert scheduler.status()["reason_code"] == "disabled"
    finally:
        release.set()
        scheduler.shutdown()
        store.close()


def test_concurrent_last_writer_sidecar_and_lifecycle_are_coherent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduler, started, release, store = _blocking_core_scheduler(tmp_path, monkeypatch)
    try:
        scheduler.start()
        assert started.wait(timeout=5)
        barrier = threading.Barrier(2)

        def do_enable() -> None:
            barrier.wait()
            scheduler.enable()

        def do_disable() -> None:
            barrier.wait()
            scheduler.disable()

        enable_thread = threading.Thread(target=do_enable)
        disable_thread = threading.Thread(target=do_disable)
        enable_thread.start()
        disable_thread.start()
        enable_thread.join(timeout=5)
        disable_thread.join(timeout=5)
        assert enable_thread.is_alive() is False
        assert disable_thread.is_alive() is False
        status = scheduler.status()
        if status["durable_enabled"]:
            assert status["dispatch_allowed"] is True
            _wait_until(lambda: scheduler.status()["running"] is True)
            assert scheduler.status()["reason_code"] == "enabled"
        else:
            assert status["dispatch_allowed"] is False
            assert status["reason_code"] == "disabled"
            release.set()
            _wait_until(lambda: scheduler.status()["running"] is False)
    finally:
        release.set()
        scheduler.shutdown()
        store.close()


def test_update_health_empty_string_force_disables_including_lifespan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(CAPTURE_SCHEDULER_ENABLED_ENV, "1")
    monkeypatch.delenv(UPDATE_HEALTH_OPERATION_ENV, raising=False)
    assert scheduler_update_health_forced_off() is False
    monkeypatch.setenv(UPDATE_HEALTH_OPERATION_ENV, "")
    assert scheduler_update_health_forced_off() is True
    config = _config(tmp_path)
    with CoreService(config) as service:
        enabled = service.capture_scheduler.enable()
        assert enabled["durable_enabled"] is True
        assert enabled["update_health_forced_off"] is True
        assert enabled["dispatch_allowed"] is False
        assert enabled["running"] is False
        assert enabled["reason_code"] == "forced_off"
        service.capture_scheduler.start()
        assert service.capture_scheduler.status()["running"] is False
        app = create_app(config, service=service)
        with TestClient(app) as client:
            assert client.get("/health").json() == {"status": "ok", "component": "core"}
            assert service.capture_scheduler.status()["running"] is False


def test_cli_scheduler_status_does_not_claim_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    config.prepare()
    write_scheduler_enabled(config.data_dir, enabled=True)
    payload = _cli(
        ["atc", "capture", "scheduler", "status", "--data-dir", str(config.data_dir)],
        capsys,
    )
    assert payload["durable_enabled"] is True
    assert payload["dispatch_allowed"] is True
    assert "running" not in payload
    assert payload.get("running") is not False


def test_run_cycle_expected_errors_are_content_free_not_fake_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    secret = tmp_path / "scheduler-oserror-do-not-leak"
    secret.write_text("secret-path\n", encoding="utf-8")
    with CoreService(config) as service:
        write_scheduler_enabled(config.data_dir, enabled=True)

        def raise_capture(*_args: object, **_kwargs: object) -> None:
            raise CaptureError("capture_failed")

        monkeypatch.setattr(
            "allthecontext.capture_scheduler.refresh_local_workspace_adapter",
            raise_capture,
        )
        capture_report = service.capture_scheduler.run_cycle()
        assert capture_report.dispatched == ()
        assert capture_report.results == ()
        assert capture_report.health.state == "unavailable"
        assert capture_report.health.reason_codes == ("capture_failed",)
        assert capture_report.plan.enabled is True

        def raise_oserror(*_args: object, **_kwargs: object) -> None:
            raise OSError(22, "invalid argument", str(secret))

        monkeypatch.setattr(
            "allthecontext.capture_scheduler.refresh_local_workspace_adapter",
            raise_oserror,
        )
        os_report = service.capture_scheduler.run_cycle()
        assert os_report.dispatched == ()
        assert os_report.results == ()
        assert os_report.health.state == "unavailable"
        assert os_report.health.reason_codes == ("capture_failed",)
        rendered = json.dumps(
            {
                "enabled": os_report.plan.enabled,
                "reason_codes": list(os_report.health.reason_codes),
                "state": os_report.health.state,
            }
        )
        _assert_no_root_leak(rendered, secret)
        assert str(secret) not in rendered
        status = service.capture_scheduler.status()
        _assert_no_root_leak(status, secret)

        def raise_programmer(*_args: object, **_kwargs: object) -> None:
            raise TypeError("internal-scheduler-bug")

        monkeypatch.setattr(
            "allthecontext.capture_scheduler.refresh_local_workspace_adapter",
            raise_programmer,
        )
        with pytest.raises(TypeError, match="internal-scheduler-bug"):
            service.capture_scheduler.run_cycle()


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("database is busy", sqlite3.SQLITE_BUSY),
        ("database is locked", sqlite3.SQLITE_BUSY_SNAPSHOT),
        ("database table is locked", sqlite3.SQLITE_LOCKED_SHAREDCACHE),
    ],
)
def test_sqlite_contention_uses_primary_code_for_extended_errors(message: str, code: int) -> None:
    assert _is_transient_sqlite_contention(_sqlite_operational_error(message, code)) is True


def test_sqlite_contention_rejects_unexpected_errors_even_with_locking_text() -> None:
    assert (
        _is_transient_sqlite_contention(
            _sqlite_operational_error("database table is locked", sqlite3.SQLITE_READONLY)
        )
        is False
    )
    assert _is_transient_sqlite_contention(sqlite3.OperationalError("database is locked")) is False


def test_run_cycle_contains_only_transient_sqlite_contention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    with CoreService(config) as service:
        write_scheduler_enabled(config.data_dir, enabled=True)

        def raise_locked() -> Any:
            raise _sqlite_operational_error(
                "database table is locked", sqlite3.SQLITE_LOCKED_SHAREDCACHE
            )

        monkeypatch.setattr(service.capture_scheduler._scheduler, "run_once", raise_locked)
        report = service.capture_scheduler.run_cycle()

        assert report.plan.enabled is True
        assert report.dispatched == ()
        assert report.results == ()
        assert report.health.state == "unavailable"
        assert report.health.reason_codes == ("capture_failed",)

        def raise_disk_io() -> Any:
            raise _sqlite_operational_error("disk I/O error", sqlite3.SQLITE_IOERR)

        monkeypatch.setattr(service.capture_scheduler._scheduler, "run_once", raise_disk_io)
        with pytest.raises(sqlite3.OperationalError, match="disk I/O error"):
            service.capture_scheduler.run_cycle()


def test_transient_sqlite_contention_keeps_loop_alive_for_workspace_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    workspace = _workspace(tmp_path)
    with CoreService(config) as service:
        authorized = authorize_local_workspace(
            service.store,
            config,
            workspace,
            local_only_acknowledged=True,
        )
        source_id = str(authorized["id"])
        service.capture.enable(source_id)
        write_scheduler_enabled(config.data_dir, enabled=True)

        original_list_sources = service.capture.list_sources
        attempts = 0
        first_contention = threading.Event()

        def list_sources_with_one_transient_lock(
            *, limit: int = 100, offset: int = 0
        ) -> tuple[list[Any], int]:
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                first_contention.set()
                raise _sqlite_operational_error(
                    "database table is locked", sqlite3.SQLITE_LOCKED_SHAREDCACHE
                )
            return original_list_sources(limit=limit, offset=offset)

        monkeypatch.setattr(service.capture, "list_sources", list_sources_with_one_transient_lock)
        service.capture_scheduler._scheduler.config = SchedulerConfig(
            enabled=False,
            poll_interval_seconds=1,
            incremental_interval_seconds=300,
            max_sources_per_cycle=500,
            max_source_pages_per_cycle=1,
            max_health_pages=4,
            max_workers=1,
        )
        try:
            service.capture_scheduler.start()
            _wait_until(lambda: first_contention.is_set())
            assert service.capture_scheduler.status()["running"] is True

            service.capture_scheduler._wakeup.set()
            _wait_until(
                lambda: (
                    LOCAL_GIT_WORKSPACE_PROVIDER in service.capture.adapters
                    and service.capture.status(source_id).get("last_run", {}).get("state")
                    == "completed"
                )
            )
            assert service.capture_scheduler.status()["running"] is True
            assert attempts >= 3
        finally:
            service.capture_scheduler.shutdown()


def test_loop_keeps_running_after_expected_capture_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    clock = _MutableClock()
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    scheduler = CoreCaptureScheduler(
        coordinator,
        config,
        scheduler_config=SchedulerConfig(poll_interval_seconds=1),
        clock=clock,
    )
    write_scheduler_enabled(config.data_dir, enabled=True)
    raised = threading.Event()
    original = scheduler.dispatch_allowed

    def flaky() -> bool:
        if not raised.is_set():
            raised.set()
            raise CaptureError("capture_failed")
        return original()

    try:
        scheduler.start()
        _wait_until(lambda: scheduler.status()["running"] is True)
        scheduler.dispatch_allowed = flaky  # type: ignore[method-assign]
        scheduler._wakeup.set()
        _wait_until(lambda: raised.is_set() and scheduler.status()["running"] is True)
        assert scheduler.status()["running"] is True
    finally:
        scheduler.shutdown()
        store.close()


def test_loop_does_not_swallow_programmer_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    clock = _MutableClock()
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    scheduler = CoreCaptureScheduler(
        coordinator,
        config,
        scheduler_config=SchedulerConfig(poll_interval_seconds=1),
        clock=clock,
    )
    write_scheduler_enabled(config.data_dir, enabled=True)

    def boom() -> bool:
        raise TypeError("internal-scheduler-bug")

    observed: list[BaseException | None] = []

    def hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is TypeError:
            observed.append(args.exc_value)
            return
        threading.__excepthook__(args)

    monkeypatch.setattr(threading, "excepthook", hook)
    try:
        scheduler.start()
        _wait_until(lambda: scheduler.status()["running"] is True)
        scheduler.dispatch_allowed = boom  # type: ignore[method-assign]
        scheduler._wakeup.set()
        _wait_until(lambda: scheduler.status()["running"] is False)
        assert scheduler._thread is None or scheduler._thread.is_alive() is False
        assert observed
        assert isinstance(observed[0], TypeError)
    finally:
        scheduler.shutdown()
        store.close()


def test_loop_does_not_swallow_noncontention_sqlite_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _open_process_gate(monkeypatch)
    config = _config(tmp_path)
    clock = _MutableClock()
    store = CoreStore(config.database_path)
    store.initialize_vault()
    coordinator = CaptureCoordinator(store, clock=clock, sink=IdempotentFakeSink())
    scheduler = CoreCaptureScheduler(
        coordinator,
        config,
        scheduler_config=SchedulerConfig(poll_interval_seconds=1),
        clock=clock,
    )
    write_scheduler_enabled(config.data_dir, enabled=True)

    def boom() -> bool:
        raise _sqlite_operational_error("database table is locked", sqlite3.SQLITE_READONLY)

    observed: list[BaseException | None] = []

    def hook(args: threading.ExceptHookArgs) -> None:
        if args.exc_type is sqlite3.OperationalError:
            observed.append(args.exc_value)
            return
        threading.__excepthook__(args)

    monkeypatch.setattr(threading, "excepthook", hook)
    try:
        scheduler.start()
        _wait_until(lambda: scheduler.status()["running"] is True)
        scheduler.dispatch_allowed = boom  # type: ignore[method-assign]
        scheduler._wakeup.set()
        _wait_until(lambda: scheduler.status()["running"] is False)
        assert scheduler._thread is None or scheduler._thread.is_alive() is False
        assert observed
        assert isinstance(observed[0], sqlite3.OperationalError)
    finally:
        scheduler.shutdown()
        store.close()
