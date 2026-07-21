"""Platform-specific startup installation contract without OS assumptions."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ServiceInstallPlan:
    platform: str
    mechanism: str
    config_path: Path
    command: tuple[str, ...]
    requires_elevation: bool


class ServiceManager(Protocol):
    def plan(self, executable: Path, data_dir: Path) -> ServiceInstallPlan: ...


def service_install_plan(executable: Path, data_dir: Path) -> ServiceInstallPlan:
    """Return a transparent future installation plan; never mutates the OS."""
    system = platform.system()
    if system == "Windows":
        return ServiceInstallPlan(
            system,
            "per-user startup task; Windows Service in signed package",
            data_dir / "service" / "windows.json",
            (str(executable), "serve-core"),
            False,
        )
    if system == "Darwin":
        return ServiceInstallPlan(
            system,
            "signed application LaunchAgent",
            data_dir / "service" / "com.allthecontext.core.plist",
            (str(executable), "serve-core"),
            False,
        )
    return ServiceInstallPlan(
        system,
        "distribution package or user service",
        data_dir / "service" / "all-the-context.service",
        (str(executable), "serve-core"),
        False,
    )
