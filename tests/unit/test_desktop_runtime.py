from __future__ import annotations

from pathlib import Path

from allthecontext.desktop import prepare_installed_runtime
from allthecontext.desktop_runtime import RuntimeCommand


def test_windows_frozen_app_self_installs_with_mcp_helper(tmp_path: Path, monkeypatch) -> None:
    source_app = tmp_path / "download" / "AllTheContextSetup.exe"
    source_helper = tmp_path / "bundle" / "AllTheContextMCP.exe"
    source_app.parent.mkdir()
    source_helper.parent.mkdir()
    source_app.write_bytes(b"desktop")
    source_helper.write_bytes(b"mcp")
    install_dir = tmp_path / "installed"
    monkeypatch.setenv("ATC_INSTALL_DIR", str(install_dir))
    monkeypatch.setattr("allthecontext.desktop.platform.system", lambda: "Windows")
    monkeypatch.setattr("allthecontext.desktop.sys.frozen", True, raising=False)
    launched: list[tuple[str, ...]] = []

    class Process:
        pass

    def fake_popen(command: tuple[str, ...], **_kwargs: object) -> Process:
        launched.append(command)
        return Process()

    monkeypatch.setattr("allthecontext.desktop.subprocess.Popen", fake_popen)

    installed, relaunched = prepare_installed_runtime(
        RuntimeCommand(source_app, mcp_executable=source_helper),
        relaunch_args=("--setup",),
    )

    assert relaunched is True
    assert installed.executable == install_dir / "AllTheContext.exe"
    assert installed.executable.read_bytes() == b"desktop"
    assert installed.mcp_executable == install_dir / "AllTheContextMCP.exe"
    assert installed.mcp_executable.read_bytes() == b"mcp"
    assert launched == [(str(installed.executable), "--setup")]
