import json
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
    configure_claude_code: bool
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
    claude_code: object | None = None
    startup: object | None = None
    log_path: Path = Path("core.log")
    warnings: tuple[str, ...] = ()
    workspace_source_id: str | None = "opaque-workspace-source-id"
    continuous_capture_enabled: bool = True
    workspace_root: Path | None = None
    workspace_local_only_acknowledged: bool = False
    acknowledge_local_workspace: bool = False


@dataclass(frozen=True, slots=True)
class FakeClaudeCodeResult:
    changed: bool = True
    mcp_changed: bool = True
    settings_changed: bool = True
    mcp_path: Path = Path("C:/private/.claude.json")
    settings_path: Path = Path("C:/private/.claude/settings.json")


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeCommand(tmp_path / "AllTheContextSetup.exe")
    workspace_root = tmp_path / "private-workspace"
    captured = _stub_setup(monkeypatch, runtime, FakeSetupResult())
    report_path = tmp_path / "setup-report.json"

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
    assert options.configure_claude_code is False


def test_headless_setup_without_workspace_root_preserves_existing_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeCommand(tmp_path / "AllTheContextSetup.exe")
    captured = _stub_setup(monkeypatch, runtime, FakeSetupResult())
    report_path = tmp_path / "setup-report.json"

    assert desktop.main(["--headless-setup", str(report_path)]) == 0

    options, _installed = captured[0]
    assert options.workspace_root is None
    assert options.workspace_local_only_acknowledged is False
    assert options.configure_claude_code is False


def test_headless_setup_forwards_explicit_claude_code_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeCommand(tmp_path / "AllTheContextSetup.exe")
    captured = _stub_setup(monkeypatch, runtime, FakeSetupResult())
    report_path = tmp_path / "setup-report.json"

    assert desktop.main(["--headless-setup", str(report_path), "--claude-code"]) == 0

    options, _installed = captured[0]
    assert options.configure_claude_code is True


def test_headless_setup_forwards_explicit_hermes_choices_only_when_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeCommand(tmp_path / "AllTheContextSetup.exe")
    _stub_setup(monkeypatch, runtime, FakeSetupResult())
    supplied: list[dict[str, object]] = []

    def options_factory(**kwargs: object) -> FakeSetupOptions:
        supplied.append(kwargs)
        legacy = {
            key: value
            for key, value in kwargs.items()
            if key
            not in {
                "configure_hermes",
                "configure_hermes_continuous_capture",
                "hermes_profile",
            }
        }
        return FakeSetupOptions(**legacy)  # type: ignore[arg-type]

    monkeypatch.setattr(desktop, "SetupOptions", options_factory)
    report_path = tmp_path / "setup-report.json"

    assert (
        desktop.main(
            [
                "--headless-setup",
                str(report_path),
                "--hermes",
                "--hermes-continuous-capture",
                "--hermes-profile",
                "work",
            ]
        )
        == 0
    )
    assert supplied == [
        {
            "vault_name": "My Context",
            "timezone": desktop.local_timezone(),
            "configure_codex": True,
            "configure_claude": True,
            "configure_claude_code": False,
            "start_at_login": True,
            "workspace_root": None,
            "workspace_local_only_acknowledged": False,
            "configure_hermes": True,
            "configure_hermes_continuous_capture": True,
            "hermes_profile": "work",
        }
    ]


def test_headless_setup_projects_claude_code_result_without_user_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeCommand(tmp_path / "AllTheContextSetup.exe")
    _stub_setup(monkeypatch, runtime, FakeSetupResult(claude_code=FakeClaudeCodeResult()))
    report_path = tmp_path / "setup-report.json"

    assert desktop.main(["--headless-setup", str(report_path), "--claude-code"]) == 0

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["claude_code"] == {
        "client": "Claude Code",
        "changed": True,
        "mcp_changed": True,
        "settings_changed": True,
    }
    assert "C:/private" not in report_path.read_text(encoding="utf-8")


def test_headless_setup_success_report_keeps_workspace_fields_opaque(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = RuntimeCommand(tmp_path / "AllTheContextSetup.exe")
    workspace_root = tmp_path / "private-workspace"
    result = FakeSetupResult(
        workspace_root=workspace_root,
        workspace_local_only_acknowledged=True,
        acknowledge_local_workspace=True,
    )
    _stub_setup(monkeypatch, runtime, result)
    report_path = tmp_path / "setup-report.json"

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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = RuntimeCommand(tmp_path / "AllTheContextSetup.exe")
    workspace_root = tmp_path / "private-workspace"
    raw_exception = "raw imported text and secret=never-log-this"
    report_path = tmp_path / "setup-report.json"
    diagnostics_path = tmp_path / "desktop-error.json"

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
