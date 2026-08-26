import json
import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from allthecontext import desktop
from allthecontext.desktop_runtime import RuntimeCommand


@dataclass(frozen=True, slots=True)
class FakeSetupOptions:
    vault_name: str
    timezone: str
    configure_codex: bool
    configure_claude: bool
    start_at_login: bool
    workspace_root: Path | None
    workspace_local_only_acknowledged: bool


@dataclass(frozen=True, slots=True)
class FakeSetupResult:
    vault_id: str = "vault-id"
    client_id: str = "client-id"
    dashboard_url: str = "http://127.0.0.1:4318/v1/browser/connect"
    core_url: str = "http://127.0.0.1:4318"
    credential_storage: str = "test"
    codex: object | None = None
    claude: object | None = None
    startup: object | None = None
    log_path: Path = Path("core.log")
    warnings: tuple[str, ...] = ()
    workspace_source_id: str | None = "opaque-workspace-source-id"
    continuous_capture_enabled: bool = True
    workspace_root: Path | None = None
    workspace_local_only_acknowledged: bool = False
    acknowledge_local_workspace: bool = False


@pytest.fixture
def setup_root() -> Iterator[Path]:
    root = Path(__file__).resolve().parent / ".headless-local-source-setup"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _stub_setup(
    monkeypatch: pytest.MonkeyPatch,
    runtime: RuntimeCommand,
    result: FakeSetupResult,
) -> list[tuple[FakeSetupOptions, RuntimeCommand]]:
    captured: list[tuple[FakeSetupOptions, RuntimeCommand]] = []
    monkeypatch.setattr(desktop.RuntimeCommand, "current", lambda: runtime)
    monkeypatch.setattr(
        desktop,
        "prepare_installed_runtime",
        lambda supplied_runtime, *, relaunch_args: (supplied_runtime, False),
    )
    monkeypatch.setattr(desktop, "SetupOptions", FakeSetupOptions)

    def fake_perform_setup(options: FakeSetupOptions, installed: RuntimeCommand) -> FakeSetupResult:
        captured.append((options, installed))
        return result

    monkeypatch.setattr(desktop, "perform_setup", fake_perform_setup)
    return captured


def test_headless_setup_forwards_packaged_workspace_options_exactly(
    setup_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeCommand(setup_root / "AllTheContextSetup.exe")
    workspace_root = setup_root / "private-workspace"
    captured = _stub_setup(monkeypatch, runtime, FakeSetupResult())
    report_path = setup_root / "setup-report.json"

    assert (
        desktop.main(
            [
                "--headless-setup",
                str(report_path),
                "--workspace-root",
                str(workspace_root),
                "--acknowledge-local-workspace",
                "--no-claude",
            ]
        )
        == 0
    )

    assert len(captured) == 1
    options, installed = captured[0]
    assert installed is runtime
    assert options.workspace_root == workspace_root
    assert options.workspace_local_only_acknowledged is True
    assert options.configure_codex is True
    assert options.configure_claude is False


def test_headless_setup_without_workspace_root_preserves_existing_defaults(
    setup_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeCommand(setup_root / "AllTheContextSetup.exe")
    captured = _stub_setup(monkeypatch, runtime, FakeSetupResult())
    report_path = setup_root / "setup-report.json"

    assert desktop.main(["--headless-setup", str(report_path)]) == 0

    options, _installed = captured[0]
    assert options.workspace_root is None
    assert options.workspace_local_only_acknowledged is False


def test_headless_setup_success_report_keeps_opaque_result_fields_only(
    setup_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeCommand(setup_root / "AllTheContextSetup.exe")
    workspace_root = setup_root / "private-workspace"
    result = FakeSetupResult(
        workspace_root=workspace_root,
        workspace_local_only_acknowledged=True,
        acknowledge_local_workspace=True,
    )
    _stub_setup(monkeypatch, runtime, result)
    report_path = setup_root / "setup-report.json"

    assert (
        desktop.main(
            [
                "--headless-setup",
                str(report_path),
                "--workspace-root",
                str(workspace_root),
                "--acknowledge-local-workspace",
            ]
        )
        == 0
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["workspace_source_id"] == "opaque-workspace-source-id"
    assert payload["continuous_capture_enabled"] is True
    assert "workspace_root" not in payload
    assert "workspace_local_only_acknowledged" not in payload
    assert "acknowledge_local_workspace" not in payload
    assert str(workspace_root) not in report_path.read_text(encoding="utf-8")


def test_headless_setup_failure_redacts_workspace_root_and_exception_text(
    setup_root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = RuntimeCommand(setup_root / "AllTheContextSetup.exe")
    workspace_root = setup_root / "private-workspace"
    raw_exception = "raw imported text and secret=never-log-this"
    report_path = setup_root / "setup-report.json"
    diagnostics_path = setup_root / "desktop-error.json"

    monkeypatch.setattr(desktop.RuntimeCommand, "current", lambda: runtime)
    monkeypatch.setattr(
        desktop,
        "prepare_installed_runtime",
        lambda supplied_runtime, *, relaunch_args: (supplied_runtime, False),
    )
    monkeypatch.setattr(desktop, "SetupOptions", FakeSetupOptions)

    def fake_perform_setup(
        _options: FakeSetupOptions, _installed: RuntimeCommand
    ) -> FakeSetupResult:
        raise RuntimeError(f"workspace root={workspace_root}; {raw_exception}")

    monkeypatch.setattr(desktop, "perform_setup", fake_perform_setup)
    monkeypatch.setattr(desktop, "_write_failure_diagnostics", lambda _error: diagnostics_path)

    assert (
        desktop.main(
            [
                "--headless-setup",
                str(report_path),
                "--workspace-root",
                str(workspace_root),
                "--acknowledge-local-workspace",
            ]
        )
        == 1
    )

    report_text = report_path.read_text(encoding="utf-8")
    captured = capsys.readouterr()
    assert json.loads(report_text)["error_code"] == "setup_failed"
    assert str(workspace_root) not in report_text
    assert raw_exception not in report_text
    assert str(workspace_root) not in captured.err
    assert raw_exception not in captured.err
    assert "setup-report.json" in captured.err
    assert str(diagnostics_path) not in captured.err
