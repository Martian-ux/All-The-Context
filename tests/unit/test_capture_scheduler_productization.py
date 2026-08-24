"""Focused sanitized proof for the Core-owned Packet E scheduler slice."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest
from allthecontext import cli
from allthecontext.capture import (
    CaptureCoordinator,
    CaptureEvent,
    CapturePage,
    DeterministicFakeAdapter,
    IdempotentFakeSink,
)
from allthecontext.capture_runtime import (
    AUTHORIZATION_FILENAME,
    SCHEDULER_CONFIG_FILENAME,
    authorize_local_workspace,
    scheduler_config_path,
    write_scheduler_enabled,
)
from allthecontext.capture_scheduler import (
    CAPTURE_SCHEDULER_ENABLED_ENV,
    UPDATE_HEALTH_OPERATION_ENV,
    CoreCaptureScheduler,
    SchedulerConfig,
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
    cli_enabled = _cli(
        ["atc", "capture", "scheduler", "enable", "--data-dir", str(config.data_dir)],
        capsys,
    )
    assert cli_enabled["durable_enabled"] is True
    assert cli_enabled["running"] is False
    cli_disabled = _cli(
        ["atc", "capture", "scheduler", "disable", "--data-dir", str(config.data_dir)],
        capsys,
    )
    assert cli_disabled["durable_enabled"] is False
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
        scheduler.stop()
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
