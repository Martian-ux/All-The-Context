"""Late-bound platform APIs with cross-platform static types."""

from __future__ import annotations

import ctypes
import importlib
import subprocess
from typing import Any


def windows_registry() -> Any:
    """Load winreg only after a runtime Windows guard has passed."""

    return importlib.import_module("winreg")


def windows_creation_flags(*names: str) -> int:
    """Resolve Windows-only subprocess flags without exposing platform stubs."""

    flags = 0
    for name in names:
        flags |= int(getattr(subprocess, name, 0))
    return flags


def windows_dll(name: str) -> Any:
    """Load a Windows DLL only after a runtime Windows guard has passed."""

    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise OSError("Windows DLL loading is unavailable on this platform")
    return loader(name, use_last_error=True)
