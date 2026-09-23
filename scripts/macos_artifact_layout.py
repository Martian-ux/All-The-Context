"""Canonical paths for helpers emitted inside the macOS application bundle."""

from __future__ import annotations

from pathlib import Path

DARWIN_BUNDLE_FRAMEWORKS = Path("Contents") / "Frameworks"
DARWIN_RECOVERY_HELPER_NAME = "all-the-context-recovery"


def darwin_bundle_helper_path(bundle: Path, name: str) -> Path:
    """Return the exact Frameworks path for a helper in a PyInstaller bundle."""

    return bundle / DARWIN_BUNDLE_FRAMEWORKS / name


def darwin_recovery_helper_path(bundle: Path) -> Path:
    """Return the one canonical packaged Darwin recovery-helper path."""

    return darwin_bundle_helper_path(bundle, DARWIN_RECOVERY_HELPER_NAME)


__all__ = [
    "DARWIN_BUNDLE_FRAMEWORKS",
    "DARWIN_RECOVERY_HELPER_NAME",
    "darwin_bundle_helper_path",
    "darwin_recovery_helper_path",
]
