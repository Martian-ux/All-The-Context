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
)
from allthecontext.config import CoreConfig
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.desktop_setup import DesktopAccess, SetupOptions, perform_setup
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
    monkeypatch.setattr(desktop_setup, "_enable_running_core_scheduler", lambda *_args: True)

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


def test_scheduler_sidecar_failure_returns_false_without_contentful_diagnostics(
    setup_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(setup_harness["tmp_path"], marker="raw workspace marker")
    events: list[tuple[str, str]] = []

    def fail_write(_data_dir: Path, *, enabled: bool) -> Any:
        del enabled
        raise RuntimeError(f"write failed for {root}: raw workspace marker")

    monkeypatch.setattr(desktop_setup, "write_scheduler_enabled", fail_write)
    result = setup_harness["run"](_options(workspace_root=root, acknowledged=True), progress=events)

    rendered = _rendered_setup(result, events)
    assert result.workspace_source_id is not None
    assert result.continuous_capture_enabled is False
    assert not scheduler_config_path(setup_harness["config"].data_dir).exists()
    assert str(root) not in rendered
    assert "raw workspace marker" not in rendered
    assert "write failed" not in rendered


def test_already_running_core_is_woken_through_authenticated_scheduler_api(
    setup_harness: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _workspace(setup_harness["tmp_path"])
    observed: list[Any] = []
    real_scheduler_enable = setup_harness["real_scheduler_enable"]

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"config_valid":true,"durable_enabled":true,"enabled":true}'

    def fake_urlopen(request: Any, **_kwargs: Any) -> Response:
        observed.append(request)
        return Response()

    monkeypatch.setattr(desktop_setup, "_enable_running_core_scheduler", real_scheduler_enable)
    monkeypatch.setattr(
        desktop_setup,
        "probe_core",
        lambda _config, **_kwargs: desktop_setup.CoreProbe.VERIFIED,
    )
    monkeypatch.setattr(desktop_setup.urllib.request, "urlopen", fake_urlopen)

    result = setup_harness["run"](_options(workspace_root=root, acknowledged=True))

    assert result.continuous_capture_enabled is True
    assert len(observed) == 1
    request = observed[0]
    assert request.method == "POST"
    assert request.full_url.endswith("/v1/admin/capture/scheduler/enable")
    assert request.get_header("Authorization") == "Bearer desktop-token"
    assert request.data == b""
