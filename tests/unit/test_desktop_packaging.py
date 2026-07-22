from __future__ import annotations

import os
from pathlib import Path

from scripts.build_desktop import desktop_arguments, helper_arguments, update_helper_arguments


def test_windows_packaging_embeds_console_mcp_helper() -> None:
    helper = Path("build") / "AllTheContextMCP.exe"
    updater = Path("build") / "AllTheContextUpdater.exe"
    helper_args = helper_arguments("Windows")
    updater_args = update_helper_arguments("Windows")
    desktop_args = desktop_arguments("Windows", helper, updater)

    assert "--console" in helper_args
    assert "--onefile" in helper_args
    assert "AllTheContextMCP" in helper_args
    assert "--windowed" in updater_args
    assert "AllTheContextUpdater" in updater_args
    assert "--windowed" in desktop_args
    assert "--onefile" in desktop_args
    assert f"{helper}{os.pathsep}." in desktop_args
    assert f"{updater}{os.pathsep}." in desktop_args
    assert "AllTheContextSetup" in desktop_args


def test_macos_packaging_produces_an_application_bundle() -> None:
    args = desktop_arguments("Darwin", Path("all-the-context-mcp"))
    assert "--windowed" in args
    assert "--onedir" in args
    assert "AllTheContext" in args
