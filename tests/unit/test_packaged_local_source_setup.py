from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
from allthecontext import desktop_setup
from allthecontext.capture import CaptureCoordinator, CaptureError
from allthecontext.capture_runtime import (
    AUTHORIZATION_FILENAME,
    SCHEDULER_CONFIG_VERSION,
    authorization_path,
    compose_capture_coordinator,
    scheduler_config_path,
    write_scheduler_enabled,
)
from allthecontext.config import CoreConfig
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.desktop_setup import (
    SCHEDULER_SETUP_CLIENT_NAME,
    DesktopAccess,
    SetupOptions,
    perform_setup,
)
from allthecontext.storage import CoreStore


def _workspace(tmp_path: Path, marker: str = "workspace content must not escape") -> Path:
    root = (tmp_path / "workspace").resolve()
    root.mkdir()
    (root / "notes.txt").write_text(marker, encoding="utf-8")
    return root


def _options(
    *,
    workspace_root: Path | None = None,
    acknowledged: bool = False,
) -> SetupOptions:
    return SetupOptions(
        configure_codex=False,
        configure_claude=False,
        start_at_login=False,
        workspace_root=workspace_root,
        workspace_local_only_acknowledged=acknowledged,
    )


@pytest.fixture
def setup_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    config = CoreConfig.in_directory(tmp_path / "core")
    runtime = RuntimeCommand(Path("python"))
    log_path = config.data_dir / "logs" / "core.log"
    real_scheduler_enable = desktop_setup._enable_running_core_scheduler

    monkeypatch.setattr(
        desktop_setup,
        "_desktop_client",
        lambda _store, _config: DesktopAccess(
            "desktop-client-id", "desktop-token", "test credential storage"
        ),
    )
    monkeypatch.setattr(
        desktop_setup,
        "launch_core",
        lambda _runtime, _config: log_path,
    )
    monkeypatch.setattr(
        desktop_setup,
        "authenticated_dashboard_url",
        lambda active_config, _token: (
            f"http://{active_config.host}:{active_config.port}/v1/browser/connect?ticket=test"
        ),
    )

    # Ordinary tests exercise setup orchestration without requiring a live Core.
    def enable_scheduler(active_config: CoreConfig, _token: str) -> bool:
        write_scheduler_enabled(active_config.data_dir, enabled=True)
        return True

    monkeypatch.setattr(desktop_setup, "_enable_running_core_scheduler", enable_scheduler)

    def run(
        options: SetupOptions,
        *,
        progress: list[tuple[str, str]] | None = None,
    ) -> Any:
        events = progress if progress is not None else []
        return perform_setup(
            options,
            runtime,
            progress=lambda step, message: events.append((step, message)),
            config=config,
        )

    return {
        "config": config,
        "run": run,
        "real_scheduler_enable": real_scheduler_enable,
        "tmp_path": tmp_path,
    }


def _rendered_setup(result: Any, events: list[tuple[str, str]]) -> str:
    return json.dumps({"result": asdict(result), "events": events}, default=str)


def test_no_workspace_root_keeps_existing_setup_and_scheduler_sidecar_untouched(
    setup_harness: dict[str, Any],
) -> None:
    config = setup_harness["config"]
    events: list[tuple[str, str]] = []

    result = setup_harness["run"](_options(), progress=events)

    assert result.workspace_source_id is None
    assert result.continuous_capture_enabled is False
    assert not scheduler_config_path(config.data_dir).exists()
    assert all(step != "source" for step, _message in events)


@pytest.mark.parametrize(
    ("workspace_root", "acknowledged"),
    [(None, True), (Path("unacknowledged-root"), False)],
)
def test_workspace_setup_requires_a_content_free_explicit_acknowledgement(
    setup_harness: dict[str, Any],
    workspace_root: Path | None,
    acknowledged: bool,
) -> None:
    config = setup_harness["config"]
    root = workspace_root or (setup_harness["tmp_path"] / "unrequested-root")

    with pytest.raises(ValueError) as raised:
        setup_harness["run"](_options(workspace_root=workspace_root, acknowledged=acknowledged))

    assert str(root) not in str(raised.value)
    assert not config.data_dir.exists()


def test_first_workspace_setup_authorizes_enables_and_persists_scheduler(
    setup_harness: dict[str, Any],
) -> None:
    config = setup_harness["config"]
    root = _workspace(setup_harness["tmp_path"])
    events: list[tuple[str, str]] = []

    result = setup_harness["run"](_options(workspace_root=root, acknowledged=True), progress=events)

    assert isinstance(result.workspace_source_id, str)
    assert result.continuous_capture_enabled is True
    scheduler = json.loads(scheduler_config_path(config.data_dir).read_text(encoding="utf-8"))
    assert scheduler == {"enabled": True, "version": SCHEDULER_CONFIG_VERSION}
    authorization = json.loads(authorization_path(config.data_dir).read_text(encoding="utf-8"))
    assert authorization["canonical_root"] == str(root)
    coordinator = compose_capture_coordinator(CoreStore(config.database_path), config)
    sources, total = coordinator.list_sources()
    assert total == 1
    assert sources[0].id == result.workspace_source_id
    assert sources[0].lifecycle_state == "enabled"
    rendered = _rendered_setup(result, events)
    assert str(root) not in rendered
    assert "workspace content must not escape" not in rendered
    assert all(str(root) not in message for _step, message in events)
    assert AUTHORIZATION_FILENAME not in rendered
    steps = [step for step, _message in events]
    assert steps.index("core") < steps.index("source") < steps.index("complete")


@pytest.mark.parametrize("failure_stage", ["credential", "launch", "dashboard"])
def test_failures_before_final_workspace_mutation_leave_no_authorization(
    setup_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    config = setup_harness["config"]
    root = _workspace(setup_harness["tmp_path"])

    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError(f"{failure_stage} failed")

    target = {
        "credential": "_desktop_client",
        "launch": "launch_core",
        "dashboard": "authenticated_dashboard_url",
    }[failure_stage]
    monkeypatch.setattr(desktop_setup, target, fail)

    with pytest.raises(RuntimeError, match=failure_stage):
        setup_harness["run"](_options(workspace_root=root, acknowledged=True))

    assert not authorization_path(config.data_dir).exists()
    assert not scheduler_config_path(config.data_dir).exists()
    sources, total = compose_capture_coordinator(
        CoreStore(config.database_path), config
    ).list_sources()
    assert sources == []
    assert total == 0


def test_exact_workspace_repeat_is_idempotent_without_enabled_to_enabled_transition(
    setup_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(setup_harness["tmp_path"])
    first = setup_harness["run"](_options(workspace_root=root, acknowledged=True))

    def invalid_enable(_self: CaptureCoordinator, _source_id: str) -> Any:
        raise AssertionError("repeat setup attempted an enabled-to-enabled transition")

    monkeypatch.setattr(CaptureCoordinator, "enable", invalid_enable)
    repeated = setup_harness["run"](_options(workspace_root=root, acknowledged=True))

    assert repeated.workspace_source_id == first.workspace_source_id
    assert repeated.continuous_capture_enabled is True


@pytest.mark.parametrize("lifecycle", ["paused", "degraded"])
def test_repeat_preserves_paused_or_degraded_source_and_reports_disabled_capture(
    setup_harness: dict[str, Any],
    lifecycle: str,
) -> None:
    config = setup_harness["config"]
    root = _workspace(setup_harness["tmp_path"])
    first = setup_harness["run"](_options(workspace_root=root, acknowledged=True))
    coordinator = compose_capture_coordinator(CoreStore(config.database_path), config)
    if lifecycle == "paused":
        coordinator.pause(first.workspace_source_id)
    else:
        coordinator.ledger.transition(first.workspace_source_id, "degraded")

    repeated = setup_harness["run"](_options(workspace_root=root, acknowledged=True))
    source = coordinator.get_source(first.workspace_source_id)

    assert repeated.workspace_source_id == first.workspace_source_id
    assert repeated.continuous_capture_enabled is False
    assert source.lifecycle_state == lifecycle


def test_second_workspace_root_is_refused_by_existing_identity_check(
    setup_harness: dict[str, Any],
) -> None:
    first_root = _workspace(setup_harness["tmp_path"])
    second_root = (setup_harness["tmp_path"] / "other-workspace").resolve()
    second_root.mkdir()
    setup_harness["run"](_options(workspace_root=first_root, acknowledged=True))
    events: list[tuple[str, str]] = []

    with pytest.raises(CaptureError, match="capture_capability_invalid") as raised:
        setup_harness["run"](
            _options(workspace_root=second_root, acknowledged=True), progress=events
        )

    assert str(first_root) not in str(raised.value)
    assert str(second_root) not in str(raised.value)
    assert all(str(first_root) not in message for _step, message in events)
    assert all(str(second_root) not in message for _step, message in events)


def test_scheduler_activation_failure_returns_false_without_contentful_diagnostics(
    setup_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(setup_harness["tmp_path"], marker="raw workspace marker")
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(desktop_setup, "_enable_running_core_scheduler", lambda *_args: False)
    result = setup_harness["run"](_options(workspace_root=root, acknowledged=True), progress=events)

    rendered = _rendered_setup(result, events)
    assert result.workspace_source_id is not None
    assert result.continuous_capture_enabled is False
    assert not scheduler_config_path(setup_harness["config"].data_dir).exists()
    assert str(root) not in rendered
    assert "raw workspace marker" not in rendered
    assert "activated in the running Core" in rendered


def test_already_running_core_is_woken_through_authenticated_scheduler_api(
    setup_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(setup_harness["tmp_path"])
    observed: list[Any] = []
    real_scheduler_enable = setup_harness["real_scheduler_enable"]

    class Response:
        status = 200

        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.body

    def fake_urlopen(request: Any, **_kwargs: Any) -> Response:
        observed.append(request)
        if request.method == "GET":
            return Response(
                b'{"config_valid":true,"durable_enabled":false,"enabled":false,"running":false}'
            )
        return Response(
            b'{"config_valid":true,"durable_enabled":true,"enabled":true,"running":true}'
        )

    monkeypatch.setattr(desktop_setup, "_enable_running_core_scheduler", real_scheduler_enable)
    monkeypatch.setattr(
        desktop_setup,
        "probe_core",
        lambda _config, **_kwargs: desktop_setup.CoreProbe.VERIFIED,
    )
    monkeypatch.setattr(desktop_setup.urllib.request, "urlopen", fake_urlopen)

    result = setup_harness["run"](_options(workspace_root=root, acknowledged=True))

    assert result.continuous_capture_enabled is True
    assert len(observed) == 2
    request = observed[-1]
    assert request.method == "POST"
    assert request.full_url.endswith("/v1/admin/capture/scheduler/enable")
    authorization = request.get_header("Authorization")
    assert authorization is not None
    assert authorization.startswith("Bearer ")
    assert authorization != "Bearer desktop-token"
    assert {item.get_header("Authorization") for item in observed} == {authorization}
    assert request.data == b""
    one_time = [
        item
        for item in CoreStore(setup_harness["config"].database_path).list_clients()
        if item["name"] == SCHEDULER_SETUP_CLIENT_NAME
    ]
    assert len(one_time) == 1
    assert one_time[0]["scopes"] == ["admin"]
    assert one_time[0]["revoked"] is True


def test_scheduler_activation_requires_a_running_worker(
    setup_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(setup_harness["tmp_path"])
    real_scheduler_enable = setup_harness["real_scheduler_enable"]

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"config_valid":true,"durable_enabled":true,"enabled":true,"running":false}'

    monkeypatch.setattr(desktop_setup, "_enable_running_core_scheduler", real_scheduler_enable)
    monkeypatch.setattr(
        desktop_setup,
        "probe_core",
        lambda _config, **_kwargs: desktop_setup.CoreProbe.VERIFIED,
    )
    monkeypatch.setattr(
        desktop_setup.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(),
    )

    result = setup_harness["run"](_options(workspace_root=root, acknowledged=True))

    assert result.continuous_capture_enabled is False
    assert result.warnings[-1] == "Continuous capture could not be activated in the running Core."


def test_scheduler_response_loss_reconciles_running_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    observed: list[Any] = []
    status_reads = 0

    class Response:
        status = 200

        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.body

    def fake_urlopen(request: Any, **_kwargs: Any) -> Response:
        nonlocal status_reads
        observed.append(request)
        if request.method == "POST":
            raise TimeoutError("response lost")
        status_reads += 1
        if status_reads == 1:
            return Response(
                b'{"config_valid":true,"durable_enabled":false,"enabled":false,"running":false}'
            )
        return Response(
            b'{"config_valid":true,"durable_enabled":true,"enabled":true,"running":true}'
        )

    monkeypatch.setattr(
        desktop_setup,
        "probe_core",
        lambda _config, **_kwargs: desktop_setup.CoreProbe.VERIFIED,
    )
    monkeypatch.setattr(desktop_setup.urllib.request, "urlopen", fake_urlopen)

    assert desktop_setup._enable_running_core_scheduler(config, "one-time-token") is True
    assert [request.method for request in observed] == ["GET", "POST", "GET"]
    assert all(not request.full_url.endswith("/disable") for request in observed)


def test_scheduler_unresolved_activation_rolls_back_only_known_disabled_prestate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    observed: list[Any] = []

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"config_valid":true,"durable_enabled":false,"enabled":false,"running":false}'

    def fake_urlopen(request: Any, **_kwargs: Any) -> Response:
        observed.append(request)
        return Response()

    monkeypatch.setattr(
        desktop_setup,
        "probe_core",
        lambda _config, **_kwargs: desktop_setup.CoreProbe.VERIFIED,
    )
    monkeypatch.setattr(desktop_setup.urllib.request, "urlopen", fake_urlopen)

    assert desktop_setup._enable_running_core_scheduler(config, "one-time-token") is False
    assert [request.method for request in observed] == ["GET", "POST", "GET", "POST"]
    assert observed[-1].full_url.endswith("/v1/admin/capture/scheduler/disable")


def test_scheduler_does_not_roll_back_a_previously_enabled_operator_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = CoreConfig.in_directory(tmp_path / "core")
    observed: list[Any] = []

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"config_valid":true,"durable_enabled":true,"enabled":true,"running":false}'

    def fake_urlopen(request: Any, **_kwargs: Any) -> Response:
        observed.append(request)
        return Response()

    monkeypatch.setattr(
        desktop_setup,
        "probe_core",
        lambda _config, **_kwargs: desktop_setup.CoreProbe.VERIFIED,
    )
    monkeypatch.setattr(desktop_setup.urllib.request, "urlopen", fake_urlopen)

    assert desktop_setup._enable_running_core_scheduler(config, "one-time-token") is False
    assert [request.method for request in observed] == ["GET", "POST", "GET"]
    assert all(not request.full_url.endswith("/disable") for request in observed)
