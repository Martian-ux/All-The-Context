"""Per-user background startup behind platform-specific adapters."""

from __future__ import annotations

import os
import platform
import plistlib
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_config_path

from .desktop_runtime import RuntimeCommand
from .platform_compat import windows_registry

STARTUP_NAME = "All The Context Core"
WINDOWS_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


@dataclass(frozen=True, slots=True)
class StartupResult:
    platform: str
    mechanism: str
    location: str


def _desktop_exec(command: tuple[str, ...]) -> str:
    def escape(value: str) -> str:
        return f'"{value.replace("%", "%%").replace(chr(34), chr(92) + chr(34))}"'

    return " ".join(escape(part) for part in command)


def install_user_startup(runtime: RuntimeCommand) -> StartupResult:
    command = runtime.core()
    system = platform.system()
    if system == "Windows":
        winreg = windows_registry()

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY) as key:
            winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        return StartupResult(system, "HKCU Run", f"HKCU\\{WINDOWS_RUN_KEY}")

    if system == "Darwin":
        path = Path.home() / "Library" / "LaunchAgents" / "com.allthecontext.core.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": "com.allthecontext.core",
            "ProgramArguments": list(command),
            "RunAtLoad": True,
            "KeepAlive": False,
        }
        temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
        try:
            with temporary.open("wb") as stream:
                plistlib.dump(payload, stream)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return StartupResult(system, "LaunchAgent", str(path))

    config_root = Path(os.environ.get("XDG_CONFIG_HOME", user_config_path("AllTheContext").parent))
    path = config_root / "autostart" / "all-the-context.desktop"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            "Name=All The Context Core",
            f"Exec={_desktop_exec(command)}",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        ]
    )
    temporary = path.with_name(f"{path.name}.{secrets.token_hex(6)}.atc-new")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return StartupResult(system, "XDG autostart", str(path))


def remove_user_startup() -> None:
    system = platform.system()
    if system == "Windows":
        winreg = windows_registry()

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, WINDOWS_RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, STARTUP_NAME)
        except FileNotFoundError:
            return
        return
    if system == "Darwin":
        (Path.home() / "Library" / "LaunchAgents" / "com.allthecontext.core.plist").unlink(
            missing_ok=True
        )
        return
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", user_config_path("AllTheContext").parent))
    (config_root / "autostart" / "all-the-context.desktop").unlink(missing_ok=True)
