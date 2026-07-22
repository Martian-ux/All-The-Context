"""Per-user application registration behind platform-specific adapters."""

from __future__ import annotations

import os
import platform
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .platform_compat import windows_creation_flags, windows_registry

WINDOWS_UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\AllTheContext"
WINDOWS_APP_ID = "AllTheContext"
WINDOWS_USER_SHELL_FOLDERS = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
)
PACKAGED_SMOKE_FLAG = "ATC_PACKAGED_SMOKE"


@dataclass(frozen=True, slots=True)
class ApplicationRegistration:
    platform: str
    launcher: Path
    desktop_shortcut: Path | None
    uninstall_registered: bool


def _windows_known_folder(name: str, *, fallback: Path | None = None) -> Path | None:
    """Resolve a per-user Shell folder, including OneDrive/enterprise redirection."""

    winreg = windows_registry()

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, WINDOWS_USER_SHELL_FOLDERS) as key:
            raw, _value_type = winreg.QueryValueEx(key, name)
    except OSError:
        return fallback.resolve() if fallback is not None else None
    if not isinstance(raw, str) or not raw.strip():
        return fallback.resolve() if fallback is not None else None
    return Path(os.path.expandvars(raw)).expanduser().resolve()


def _windows_locations() -> tuple[Path, Path | None]:
    if os.environ.get(PACKAGED_SMOKE_FLAG) == "1":
        smoke_programs = os.environ.get("ATC_SMOKE_PROGRAMS_DIR")
        smoke_desktop = os.environ.get("ATC_SMOKE_DESKTOP_DIR")
        if not smoke_programs or not smoke_desktop:
            raise OSError("Packaged smoke Windows folders are not configured")
        return (
            Path(smoke_programs).resolve() / "All The Context",
            Path(smoke_desktop).resolve(),
        )
    app_data = os.environ.get("APPDATA")
    programs_fallback = (
        Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs" if app_data else None
    )
    programs = _windows_known_folder("Programs", fallback=programs_fallback)
    if programs is None:
        raise OSError("Windows Programs folder is unavailable")
    user_profile = os.environ.get("USERPROFILE")
    desktop_fallback = Path(user_profile) / "Desktop" if user_profile else None
    desktop = _windows_known_folder("Desktop", fallback=desktop_fallback)
    return programs / "All The Context", desktop


def _windows_uninstall_key() -> str:
    override = os.environ.get("ATC_SMOKE_UNINSTALL_KEY")
    if override is None:
        return WINDOWS_UNINSTALL_KEY
    if os.environ.get(PACKAGED_SMOKE_FLAG) != "1" or not override.startswith(
        "Software\\AllTheContext\\Smoke\\"
    ):
        raise OSError("Refusing an unsafe uninstall-registry override")
    return override


def _create_windows_shortcut(
    shortcut: Path,
    executable: Path,
    *,
    arguments: str = "",
    description: str,
) -> None:
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        {
            "ATC_SHORTCUT_PATH": str(shortcut),
            "ATC_SHORTCUT_TARGET": str(executable),
            "ATC_SHORTCUT_ARGUMENTS": arguments,
            "ATC_SHORTCUT_DESCRIPTION": description,
            "ATC_SHORTCUT_WORKDIR": str(executable.parent),
        }
    )
    script = (
        "$shell=New-Object -ComObject WScript.Shell;"
        "$link=$shell.CreateShortcut($env:ATC_SHORTCUT_PATH);"
        "$link.TargetPath=$env:ATC_SHORTCUT_TARGET;"
        "$link.Arguments=$env:ATC_SHORTCUT_ARGUMENTS;"
        "$link.Description=$env:ATC_SHORTCUT_DESCRIPTION;"
        "$link.WorkingDirectory=$env:ATC_SHORTCUT_WORKDIR;"
        "$link.IconLocation=$env:ATC_SHORTCUT_TARGET + ',0';"
        "$link.Save()"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
        creationflags=windows_creation_flags("CREATE_NO_WINDOW"),
    )
    if completed.returncode != 0 or not shortcut.is_file():
        detail = completed.stderr.strip()[:300]
        raise OSError(f"Windows could not create the app shortcut. {detail}".strip())


def install_application_entrypoints(executable: Path) -> ApplicationRegistration | None:
    """Register a discoverable launcher and per-user uninstaller."""

    if platform.system() != "Windows":
        return None
    target = executable.resolve(strict=True)
    start_menu, desktop = _windows_locations()
    launcher = start_menu / "All The Context.lnk"
    _create_windows_shortcut(
        launcher,
        target,
        description="Open your local All The Context Core",
    )
    desktop_shortcut = desktop / "All The Context.lnk" if desktop else None
    if desktop_shortcut is not None:
        _create_windows_shortcut(
            desktop_shortcut,
            target,
            description="Open your local All The Context Core",
        )
    uninstall_shortcut = start_menu / "Uninstall All The Context.lnk"
    _create_windows_shortcut(
        uninstall_shortcut,
        target,
        arguments="--uninstall",
        description="Uninstall All The Context (your context data is kept)",
    )

    winreg = windows_registry()

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _windows_uninstall_key()) as key:
        string_values = {
            "DisplayName": "All The Context",
            "DisplayVersion": __version__,
            "Publisher": "All The Context",
            "InstallLocation": str(target.parent),
            "DisplayIcon": str(target),
            "UninstallString": (subprocess.list2cmdline([str(target), "--uninstall"])),
        }
        for name, value in string_values.items():
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
        winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    return ApplicationRegistration("Windows", launcher, desktop_shortcut, True)


def remove_application_entrypoints() -> None:
    """Remove launchers and uninstall registration without deleting user data."""

    if platform.system() != "Windows":
        return
    start_menu, desktop = _windows_locations()
    (start_menu / "All The Context.lnk").unlink(missing_ok=True)
    (start_menu / "Uninstall All The Context.lnk").unlink(missing_ok=True)
    with suppress(OSError):
        start_menu.rmdir()
    if desktop is not None:
        (desktop / "All The Context.lnk").unlink(missing_ok=True)

    winreg = windows_registry()

    with suppress(FileNotFoundError):
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _windows_uninstall_key())
