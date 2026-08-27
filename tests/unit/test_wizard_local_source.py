"""Focused tests for packaged local-workspace onboarding in the setup wizard."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path

import pytest
from allthecontext import wizard


@dataclass(frozen=True, slots=True)
class FrozenSetupOptions:
    vault_name: str
    configure_codex: bool
    configure_claude: bool
    configure_claude_code: bool
    start_at_login: bool
    workspace_root: Path | None = None
    workspace_local_only_acknowledged: bool = False


def _build_options(
    *,
    root_text: str = "",
    acknowledged: bool = False,
    claude_code: bool = False,
) -> FrozenSetupOptions:
    return wizard.build_setup_options(
        vault_name="My Context",
        configure_codex=True,
        configure_claude=False,
        configure_claude_code=claude_code,
        start_at_login=True,
        workspace_root_text=root_text,
        workspace_local_only_acknowledged=acknowledged,
    )


@pytest.fixture
def frozen_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wizard, "SetupOptions", FrozenSetupOptions)


def test_blank_optional_root_builds_existing_setup_options(frozen_options: None) -> None:
    options = _build_options()

    assert options.workspace_root is None
    assert options.workspace_local_only_acknowledged is False
    assert options.configure_claude_code is False


def test_claude_code_choice_builds_a_separate_setup_option(frozen_options: None) -> None:
    options = _build_options(claude_code=True)

    assert options.configure_claude_code is True


def test_claude_code_completion_distinguishes_failed_and_unselected() -> None:
    assert wizard.claude_code_completion_text(selected=False, connected=False) == "Not selected"
    assert wizard.claude_code_completion_text(selected=True, connected=False) == "Not connected"
    assert wizard.claude_code_completion_text(selected=True, connected=True) == (
        "UserPromptSubmit hook ready"
    )


def test_workspace_path_and_ack_build_frozen_setup_options(frozen_options: None) -> None:
    root_text = "C:/Users/Noah/workspaces/context-app"

    options = _build_options(root_text=root_text, acknowledged=True)

    assert options.workspace_root == Path(root_text)
    assert isinstance(options.workspace_root, Path)
    assert options.workspace_local_only_acknowledged is True
    with pytest.raises(FrozenInstanceError):
        options.workspace_root = Path("C:/other")  # type: ignore[misc]


def test_workspace_path_requires_acknowledgement(frozen_options: None) -> None:
    root_text = "C:/Users/Noah/private-project"

    with pytest.raises(ValueError, match="allowing local workspace capture") as raised:
        _build_options(root_text=root_text)

    assert root_text not in str(raised.value)


def test_workspace_acknowledgement_requires_path(frozen_options: None) -> None:
    with pytest.raises(ValueError, match="Select a workspace folder"):
        _build_options(acknowledged=True)


def test_browse_cancellation_preserves_existing_field(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeVariable:
        def __init__(self, value: str) -> None:
            self.value = value

        def set(self, value: str) -> None:
            self.value = value

    controller = wizard.SetupWizard.__new__(wizard.SetupWizard)
    controller.root = object()
    controller.workspace_root = FakeVariable("C:/already-selected")
    monkeypatch.setattr(wizard.filedialog, "askdirectory", lambda **_kwargs: "")

    controller._browse_workspace()

    assert controller.workspace_root.value == "C:/already-selected"


def test_progress_and_completion_copy_never_include_workspace_path() -> None:
    root_text = "C:/Users/Noah/private-project"
    root = Path(root_text)

    progress = wizard.progress_status_text("source", f"Scanning {root_text}")
    complete = wizard.completion_body(True)
    warning = wizard.redact_workspace_path(f"Could not read {root}", root)

    assert root_text not in progress
    assert "private-project" not in progress
    assert root_text not in complete
    assert "private-project" not in complete
    assert root_text not in warning
    assert "private-project" not in warning
    assert "Continuous capture is enabled" in complete


def test_claude_code_progress_copy_is_content_free() -> None:
    assert wizard.progress_status_text("claude_code", "private token and path") == (
        "Connecting the Claude Code hook"
    )


def test_skipped_claude_code_progress_row_is_not_marked_complete() -> None:
    class FakeLabel:
        def __init__(self) -> None:
            self.changes: list[dict[str, object]] = []

        def configure(self, **kwargs: object) -> None:
            self.changes.append(kwargs)

    controller = wizard.SetupWizard.__new__(wizard.SetupWizard)
    skipped_icon = FakeLabel()
    skipped_label = FakeLabel()
    controller.progress_rows = {
        "vault": (FakeLabel(), FakeLabel()),
        "claude_code": (skipped_icon, skipped_label),
        "startup": (FakeLabel(), FakeLabel()),
    }
    controller.skipped_progress_steps = {"claude_code"}
    controller.status_copy = FakeLabel()

    controller._show_progress("startup", "ignored content")

    assert skipped_icon.changes == []
    assert skipped_label.changes == []


def test_workspace_setup_error_hides_path_and_lower_layer_details() -> None:
    root_text = "C:/Users/Noah/private-project"
    error = RuntimeError(f"failed at {root_text}: raw workspace marker")

    rendered = wizard.setup_error_text(error, Path(root_text))

    assert root_text not in rendered
    assert "private-project" not in rendered
    assert "raw workspace marker" not in rendered
    assert "selected folder" in rendered


def test_setup_error_without_workspace_preserves_existing_diagnostic() -> None:
    error = RuntimeError("Core is not reachable")

    assert wizard.setup_error_text(error, None) == str(error)
