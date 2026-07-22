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
PACKAGED_SMOKE_FLAG = "ATC_PACKAGED_SMOKE"
MACOS_LAUNCH_AGENT_LABEL = "com.allthecontext.core"
MACOS_LAUNCH_AGENT_NAME = f"{MACOS_LAUNCH_AGENT_LABEL}.plist"


@dataclass(frozen=True, slots=True)
class StartupResult:
    platform: str
    mechanism: str
    location: str


def _desktop_exec(command: tuple[str, ...]) -> str:
    def escape(value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("desktop startup arguments cannot contain line breaks")
        escaped = value.replace("\\", "\\\\")
        for character in ('"', "`", "$"):
            escaped = escaped.replace(character, f"\\{character}")
        return f'"{escaped.replace("%", "%%")}"'

    return " ".join(escape(part) for part in command)


def _windows_run_key() -> str:
    override = os.environ.get("ATC_SMOKE_STARTUP_WINDOWS_KEY")
    if override is None:
        return WINDOWS_RUN_KEY
    if os.environ.get(PACKAGED_SMOKE_FLAG) != "1" or not override.startswith(
        "Software\\AllTheContext\\Smoke\\"
    ):
        raise OSError("Refusing an unsafe startup-registry override")
    return override


def _macos_launch_agent_path() -> Path:
    override = os.environ.get("ATC_SMOKE_LAUNCH_AGENTS_DIR")
    if override is not None:
        if os.environ.get(PACKAGED_SMOKE_FLAG) != "1":
            raise OSError("Refusing an unsafe LaunchAgent directory override")
        return Path(override).expanduser().resolve() / MACOS_LAUNCH_AGENT_NAME
    return Path.home() / "Library" / "LaunchAgents" / MACOS_LAUNCH_AGENT_NAME


def install_user_startup(runtime: RuntimeCommand) -> StartupResult:
    command = runtime.core()
    system = platform.system()
    if system == "Windows":
        winreg = windows_registry()

        run_key = _windows_run_key()
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, run_key) as key:
            winreg.SetValueEx(key, STARTUP_NAME, 0, winreg.REG_SZ, subprocess.list2cmdline(command))
        return StartupResult(system, "HKCU Run", f"HKCU\\{run_key}")

    if system == "Darwin":
        path = _macos_launch_agent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "Label": MACOS_LAUNCH_AGENT_LABEL,
            "ProgramArguments": list(command),
            "RunAtLoad": True,
            "KeepAlive": False,
            "ProcessType": "Background",
            "ThrottleInterval": 10,
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
                winreg.HKEY_CURRENT_USER, _windows_run_key(), 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, STARTUP_NAME)
        except FileNotFoundError:
            return
        return
    if system == "Darwin":
        _macos_launch_agent_path().unlink(missing_ok=True)
        return
    config_root = Path(os.environ.get("XDG_CONFIG_HOME", user_config_path("AllTheContext").parent))
    (config_root / "autostart" / "all-the-context.desktop").unlink(missing_ok=True)
