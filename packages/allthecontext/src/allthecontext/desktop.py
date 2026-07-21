"""Single-entry desktop application, background Core, and packaged diagnostics."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tkinter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from .config import CoreConfig
from .desktop_runtime import RuntimeCommand, mcp_helper_name
from .desktop_setup import (
    SetupOptions,
    authenticated_dashboard_url,
    launch_core,
    open_dashboard,
    perform_setup,
    recover_desktop_access,
)

WINDOWS_APP_NAME = "AllTheContext.exe"


def windows_install_directory() -> Path:
    configured = os.environ.get("ATC_INSTALL_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data).resolve() / "Programs" / "All The Context"
    data_path = Path(user_data_path("AllTheContext", "AllTheContext", roaming=False))
    return data_path.parent / "Programs" / "All The Context"


def _same_file(source: Path, target: Path) -> bool:
    if not target.is_file() or source.stat().st_size != target.stat().st_size:
        return False
    chunk_size = 1024 * 1024
    with source.open("rb") as source_stream, target.open("rb") as target_stream:
        while True:
            source_chunk = source_stream.read(chunk_size)
            if source_chunk != target_stream.read(chunk_size):
                return False
            if not source_chunk:
                return True


def _copy_atomically(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if _same_file(source, target):
        return
    temporary = target.with_name(f"{target.name}.atc-new")
    shutil.copy2(source, temporary)
    temporary.replace(target)


def prepare_installed_runtime(
    runtime: RuntimeCommand,
    *,
    relaunch_args: tuple[str, ...] | None,
) -> tuple[RuntimeCommand, bool]:
    """Install frozen Windows binaries per-user and optionally relaunch the stable copy."""
    if not getattr(sys, "frozen", False) or platform.system() != "Windows":
        return runtime, False

    helper_source = runtime.mcp_executable
    if helper_source is None or not helper_source.is_file():
        raise RuntimeError("The packaged MCP helper is missing. Download the installer again.")

    install_dir = windows_install_directory()
    app_target = install_dir / WINDOWS_APP_NAME
    helper_target = install_dir / mcp_helper_name()
    _copy_atomically(helper_source, helper_target)
    _copy_atomically(runtime.executable, app_target)
    installed = RuntimeCommand(app_target, mcp_executable=helper_target)

    if runtime.executable != app_target and relaunch_args is not None:
        subprocess.Popen(
            (str(app_target), *relaunch_args),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        return installed, True
    return installed, False


def diagnostics() -> dict[str, Any]:
    package_root = Path(__file__).resolve().parent
    core_migrations = package_root / "migrations" / "core"
    relay_migrations = package_root / "migrations" / "relay"
    runtime = RuntimeCommand.current()
    return {
        "application": "All The Context",
        "version": "0.1.0",
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": platform.system(),
        "python": platform.python_version(),
        "tk": tkinter.TkVersion,
        "core_migrations": len(tuple(core_migrations.glob("*.sql"))),
        "relay_migrations": len(tuple(relay_migrations.glob("*.sql"))),
        "dashboard_bundled": (package_root / "web" / "index.html").is_file(),
        "mcp_helper_bundled": runtime.mcp_executable is not None,
        "mcp_stdio_available": runtime.mcp_executable is not None or platform.system() == "Linux",
        "core_data_directory": str(CoreConfig.default().data_dir),
    }


def write_diagnostics(path: Path) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".atc-new")
    temporary.write_text(json.dumps(diagnostics(), indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _headless_setup(args: argparse.Namespace, runtime: RuntimeCommand) -> int:
    installed, _ = prepare_installed_runtime(runtime, relaunch_args=None)
    result = perform_setup(
        SetupOptions(
            vault_name=args.vault_name,
            timezone=args.timezone,
            configure_codex=not args.no_codex,
            start_at_login=not args.no_startup,
        ),
        installed,
    )
    report = asdict(result)
    report["log_path"] = str(result.log_path)
    report["codex"] = asdict(result.codex) if result.codex else None
    report["startup"] = asdict(result.startup) if result.startup else None
    target = Path(args.headless_setup).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return 0


def _open_existing(runtime: RuntimeCommand) -> bool:
    config = CoreConfig.default()
    access = recover_desktop_access(config)
    if access is None:
        return False
    launch_core(runtime, config)
    open_dashboard(authenticated_dashboard_url(config, access.token))
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="All The Context")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--core", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--mcp-stdio", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--setup", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--diagnostics", type=Path, help=argparse.SUPPRESS)
    mode.add_argument("--headless-setup", metavar="REPORT_PATH", help=argparse.SUPPRESS)
    parser.add_argument("--vault-name", default="My Context", help=argparse.SUPPRESS)
    parser.add_argument("--timezone", default="UTC", help=argparse.SUPPRESS)
    parser.add_argument("--no-codex", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-startup", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.core:
        from .core.app import main as core_main

        core_main()
        return 0
    if args.mcp_stdio:
        from .mcp_adapter import main as mcp_main

        mcp_main()
        return 0
    if args.diagnostics:
        write_diagnostics(args.diagnostics)
        return 0

    runtime = RuntimeCommand.current()
    if args.headless_setup:
        return _headless_setup(args, runtime)

    installed, relaunched = prepare_installed_runtime(runtime, relaunch_args=("--setup",))
    if relaunched:
        return 0

    if not args.setup and _open_existing(installed):
        return 0
    from .wizard import run_setup_wizard

    run_setup_wizard(installed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
