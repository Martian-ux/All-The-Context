"""Build platform-native desktop and embedded STDIO MCP executables."""

from __future__ import annotations

import argparse
import os
import platform
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "packages" / "allthecontext" / "src"
BUILD_ROOT = ROOT / "build" / "desktop"
DIST_ROOT = ROOT / "dist" / "desktop"


def executable_name(name: str, system: str) -> str:
    return f"{name}.exe" if system == "Windows" else name


def common_arguments() -> list[str]:
    return [
        "--noconfirm",
        "--clean",
        "--paths",
        str(SOURCE_ROOT),
        "--collect-data",
        "allthecontext",
        "--collect-submodules",
        "mcp.server",
        "--copy-metadata",
        "mcp",
    ]


def helper_arguments(system: str) -> list[str]:
    name = "AllTheContextMCP" if system == "Windows" else "all-the-context-mcp"
    return [
        *common_arguments(),
        "--onefile",
        "--console",
        "--name",
        name,
        "--distpath",
        str(BUILD_ROOT / "helper-dist"),
        "--workpath",
        str(BUILD_ROOT / "helper-work"),
        "--specpath",
        str(BUILD_ROOT / "spec"),
        str(ROOT / "scripts" / "mcp_entry.py"),
    ]


def update_helper_arguments(system: str) -> list[str]:
    name = "AllTheContextUpdater" if system == "Windows" else "all-the-context-updater"
    subsystem = ["--windowed"] if system == "Windows" else ["--console"]
    return [
        "--noconfirm",
        "--clean",
        "--paths",
        str(SOURCE_ROOT),
        "--onefile",
        *subsystem,
        "--name",
        name,
        "--distpath",
        str(BUILD_ROOT / "update-helper-dist"),
        "--workpath",
        str(BUILD_ROOT / "update-helper-work"),
        "--specpath",
        str(BUILD_ROOT / "spec"),
        str(ROOT / "scripts" / "update_helper_entry.py"),
    ]


def desktop_arguments(
    system: str, helper: Path | None, update_helper: Path | None = None
) -> list[str]:
    name = {
        "Windows": "AllTheContextSetup",
        "Darwin": "AllTheContext",
    }.get(system, "all-the-context")
    bundle_mode = "--onedir" if system == "Darwin" else "--onefile"
    subsystem = ["--windowed"] if system in {"Windows", "Darwin"} else []
    helper_arguments = ["--add-binary", f"{helper}{os.pathsep}."] if helper else []
    update_arguments = ["--add-binary", f"{update_helper}{os.pathsep}."] if update_helper else []
    return [
        *common_arguments(),
        bundle_mode,
        *subsystem,
        "--name",
        name,
        *helper_arguments,
        *update_arguments,
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(BUILD_ROOT / "app-work"),
        "--specpath",
        str(BUILD_ROOT / "spec"),
        str(ROOT / "scripts" / "desktop_entry.py"),
    ]


def build(*, system: str | None = None) -> Path:
    try:
        import PyInstaller.__main__
    except ImportError as exc:
        raise SystemExit(
            'PyInstaller is not installed. Run: python -m pip install -e ".[packaging]"'
        ) from exc

    active_system = system or platform.system()
    (BUILD_ROOT / "spec").mkdir(parents=True, exist_ok=True)
    DIST_ROOT.mkdir(parents=True, exist_ok=True)
    helper: Path | None = None
    update_helper: Path | None = None
    if active_system in {"Windows", "Darwin"}:
        helper_stem = "AllTheContextMCP" if active_system == "Windows" else "all-the-context-mcp"
        helper = BUILD_ROOT / "helper-dist" / executable_name(helper_stem, active_system)
        PyInstaller.__main__.run(helper_arguments(active_system))
        if not helper.is_file():
            raise RuntimeError(f"MCP helper was not produced at {helper}")
    if active_system == "Windows":
        update_helper = BUILD_ROOT / "update-helper-dist" / "AllTheContextUpdater.exe"
        PyInstaller.__main__.run(update_helper_arguments(active_system))
        if not update_helper.is_file():
            raise RuntimeError(f"Update helper was not produced at {update_helper}")
    PyInstaller.__main__.run(desktop_arguments(active_system, helper, update_helper))

    app_stem = {
        "Windows": "AllTheContextSetup",
        "Darwin": "AllTheContext.app",
    }.get(active_system, "all-the-context")
    artifact = DIST_ROOT / executable_name(app_stem, active_system)
    if not artifact.exists():
        raise RuntimeError(f"Desktop artifact was not produced at {artifact}")
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    artifact = build()
    print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
