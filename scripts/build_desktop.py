"""Build platform-native desktop and embedded STDIO MCP executables."""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

from allthecontext import __version__

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
        "--collect-submodules",
        "keyring.backends",
        "--copy-metadata",
        "mcp",
        "--copy-metadata",
        "keyring",
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
    bundle_identity = (
        ["--osx-bundle-identifier", "com.allthecontext.desktop"] if system == "Darwin" else []
    )
    helper_arguments = ["--add-binary", f"{helper}{os.pathsep}."] if helper else []
    update_arguments = ["--add-binary", f"{update_helper}{os.pathsep}."] if update_helper else []
    return [
        *common_arguments(),
        bundle_mode,
        *subsystem,
        *bundle_identity,
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


def macos_bundle_version(version: str) -> str:
    match = re.fullmatch(
        r"(?P<base>(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
        r"(?:-beta\.[1-9][0-9]*)?",
        version,
    )
    if match is None:
        raise ValueError(f"invalid application version: {version!r}")
    return match.group("base")


def finalize_macos_bundle(bundle: Path, *, version: str) -> None:
    """Add explicit user-facing identity to the unsigned community app bundle."""

    info_path = bundle / "Contents" / "Info.plist"
    if not info_path.is_file():
        raise RuntimeError(f"macOS bundle metadata is missing: {info_path}")
    with info_path.open("rb") as stream:
        payload = plistlib.load(stream)
    payload.update(
        {
            "ATCDistributionTrust": "unsigned-community",
            "ATCReleaseVersion": version,
            "CFBundleDisplayName": "All The Context",
            "CFBundleIdentifier": "com.allthecontext.desktop",
            "CFBundleName": "All The Context",
            "CFBundleShortVersionString": macos_bundle_version(version),
            "CFBundleVersion": macos_bundle_version(version),
        }
    )
    temporary = info_path.with_name(f"{info_path.name}.atc-new")
    try:
        with temporary.open("wb") as stream:
            plistlib.dump(payload, stream, sort_keys=True)
        temporary.replace(info_path)
    finally:
        temporary.unlink(missing_ok=True)


def reseal_macos_bundle(bundle: Path) -> None:
    """Restore a free ad-hoc structural seal after changing Info.plist."""

    codesign = shutil.which("codesign")
    if codesign is None:
        raise RuntimeError("codesign is required to finish the macOS application bundle")
    sign = subprocess.run(
        [codesign, "--force", "--sign", "-", "--timestamp=none", str(bundle)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if sign.returncode != 0:
        detail = sign.stderr.strip()[-500:]
        raise RuntimeError(f"could not ad-hoc seal the macOS bundle. {detail}".strip())
    verify = subprocess.run(
        [codesign, "--verify", "--deep", "--strict", str(bundle)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if verify.returncode != 0:
        detail = verify.stderr.strip()[-500:]
        raise RuntimeError(f"macOS bundle structural seal is invalid. {detail}".strip())


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
    if active_system == "Darwin":
        finalize_macos_bundle(artifact, version=__version__)
        # PyInstaller seals the bundle before this script adds the final public
        # metadata. Re-seal with the identity-free ad-hoc marker so Gatekeeper
        # still sees an unsigned/unnotarized community build, but the bundle is
        # not internally corrupt.
        reseal_macos_bundle(artifact)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    artifact = build()
    print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
