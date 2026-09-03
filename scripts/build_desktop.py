"""Build platform-native desktop and embedded STDIO MCP executables."""

from __future__ import annotations

import argparse
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "packages" / "allthecontext" / "src"
BUILD_ROOT = ROOT / "build" / "desktop"
DIST_ROOT = ROOT / "dist" / "desktop"

# A developer host may have another checkout installed editable. Make both the
# version metadata and PyInstaller's package-data discovery come from this
# checkout, not whichever allthecontext happens to win the ambient import path.
sys.path.insert(0, str(SOURCE_ROOT))

from allthecontext import __version__  # noqa: E402


def executable_name(name: str, system: str) -> str:
    return f"{name}.exe" if system == "Windows" else name


def common_arguments(*, source_root: Path = SOURCE_ROOT) -> list[str]:
    return [
        "--noconfirm",
        "--clean",
        "--paths",
        str(source_root),
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


def helper_arguments(
    system: str,
    *,
    source_root: Path = SOURCE_ROOT,
    build_root: Path = BUILD_ROOT,
) -> list[str]:
    name = "AllTheContextMCP" if system == "Windows" else "all-the-context-mcp"
    return [
        *common_arguments(source_root=source_root),
        "--onefile",
        "--console",
        "--name",
        name,
        "--distpath",
        str(build_root / "helper-dist"),
        "--workpath",
        str(build_root / "helper-work"),
        "--specpath",
        str(build_root / "spec"),
        str(ROOT / "scripts" / "mcp_entry.py"),
    ]


def recovery_helper_arguments(
    system: str,
    *,
    source_root: Path = SOURCE_ROOT,
    build_root: Path = BUILD_ROOT,
) -> list[str]:
    """Console recovery/admin helper for windowed Windows/macOS desktop builds."""

    name = "AllTheContextRecovery" if system == "Windows" else "all-the-context-recovery"
    return [
        *common_arguments(source_root=source_root),
        "--onefile",
        "--console",
        "--name",
        name,
        "--distpath",
        str(build_root / "recovery-helper-dist"),
        "--workpath",
        str(build_root / "recovery-helper-work"),
        "--specpath",
        str(build_root / "spec"),
        str(ROOT / "scripts" / "recovery_entry.py"),
    ]


def update_helper_arguments(
    system: str,
    *,
    source_root: Path = SOURCE_ROOT,
    build_root: Path = BUILD_ROOT,
) -> list[str]:
    name = "AllTheContextUpdater" if system == "Windows" else "all-the-context-updater"
    subsystem = ["--windowed"] if system == "Windows" else ["--console"]
    return [
        "--noconfirm",
        "--clean",
        "--paths",
        str(source_root),
        "--onefile",
        *subsystem,
        "--name",
        name,
        "--distpath",
        str(build_root / "update-helper-dist"),
        "--workpath",
        str(build_root / "update-helper-work"),
        "--specpath",
        str(build_root / "spec"),
        str(ROOT / "scripts" / "update_helper_entry.py"),
    ]


def desktop_arguments(
    system: str,
    helper: Path | None,
    update_helper: Path | None = None,
    recovery_helper: Path | None = None,
    *,
    source_root: Path = SOURCE_ROOT,
    build_root: Path = BUILD_ROOT,
    dist_root: Path = DIST_ROOT,
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
    helper_args = ["--add-binary", f"{helper}{os.pathsep}."] if helper else []
    update_arguments = ["--add-binary", f"{update_helper}{os.pathsep}."] if update_helper else []
    recovery_arguments = (
        ["--add-binary", f"{recovery_helper}{os.pathsep}."] if recovery_helper else []
    )
    return [
        *common_arguments(source_root=source_root),
        bundle_mode,
        *subsystem,
        *bundle_identity,
        "--name",
        name,
        *helper_args,
        *update_arguments,
        *recovery_arguments,
        "--distpath",
        str(dist_root),
        "--workpath",
        str(build_root / "app-work"),
        "--specpath",
        str(build_root / "spec"),
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


def component_paths(
    *,
    build_root: Path = BUILD_ROOT,
    dist_root: Path = DIST_ROOT,
) -> dict[str, Path]:
    """Return the four native Windows component outputs in canonical roles."""

    return {
        "main": dist_root / "AllTheContextSetup.exe",
        "mcp": build_root / "helper-dist" / "AllTheContextMCP.exe",
        "recovery": dist_root / "AllTheContextRecovery.exe",
        "updater": build_root / "update-helper-dist" / "AllTheContextUpdater.exe",
    }


def build(
    *,
    system: str | None = None,
    source_root: Path = SOURCE_ROOT,
    build_root: Path = BUILD_ROOT,
    dist_root: Path = DIST_ROOT,
) -> Path:
    try:
        import PyInstaller.__main__
    except ImportError as exc:
        raise SystemExit(
            'PyInstaller is not installed. Run: python -m pip install -e ".[packaging]"'
        ) from exc

    active_system = system or platform.system()
    source_root = source_root.resolve(strict=True)
    build_root = build_root.expanduser().resolve()
    dist_root = dist_root.expanduser().resolve()
    (build_root / "spec").mkdir(parents=True, exist_ok=True)
    dist_root.mkdir(parents=True, exist_ok=True)
    helper: Path | None = None
    update_helper: Path | None = None
    recovery_helper: Path | None = None
    if active_system in {"Windows", "Darwin"}:
        helper_stem = "AllTheContextMCP" if active_system == "Windows" else "all-the-context-mcp"
        helper = build_root / "helper-dist" / executable_name(helper_stem, active_system)
        PyInstaller.__main__.run(
            helper_arguments(active_system, source_root=source_root, build_root=build_root)
        )
        if not helper.is_file():
            raise RuntimeError(f"MCP helper was not produced at {helper}")
        recovery_stem = (
            "AllTheContextRecovery" if active_system == "Windows" else "all-the-context-recovery"
        )
        recovery_helper = (
            build_root / "recovery-helper-dist" / executable_name(recovery_stem, active_system)
        )
        PyInstaller.__main__.run(
            recovery_helper_arguments(active_system, source_root=source_root, build_root=build_root)
        )
        if not recovery_helper.is_file():
            raise RuntimeError(f"Recovery helper was not produced at {recovery_helper}")
    if active_system == "Windows":
        update_helper = build_root / "update-helper-dist" / "AllTheContextUpdater.exe"
        PyInstaller.__main__.run(
            update_helper_arguments(active_system, source_root=source_root, build_root=build_root)
        )
        if not update_helper.is_file():
            raise RuntimeError(f"Update helper was not produced at {update_helper}")
    PyInstaller.__main__.run(
        desktop_arguments(
            active_system,
            helper,
            update_helper,
            recovery_helper,
            source_root=source_root,
            build_root=build_root,
            dist_root=dist_root,
        )
    )

    app_stem = {
        "Windows": "AllTheContextSetup",
        "Darwin": "AllTheContext.app",
    }.get(active_system, "all-the-context")
    artifact = dist_root / executable_name(app_stem, active_system)
    if not artifact.exists():
        raise RuntimeError(f"Desktop artifact was not produced at {artifact}")
    if active_system == "Darwin":
        finalize_macos_bundle(artifact, version=__version__)
        # PyInstaller seals the bundle before this script adds the final public
        # metadata. Re-seal with the identity-free ad-hoc marker so Gatekeeper
        # still sees an unsigned/unnotarized community build, but the bundle is
        # not internally corrupt.
        reseal_macos_bundle(artifact)
    if recovery_helper is not None and active_system == "Windows":
        # Stage the console helper next to the setup binary so package smokes and
        # operator layout checks can invoke built bytes without MEIPASS extraction.
        # Install still extracts the embedded helper from the setup onefile.
        staged = dist_root / recovery_helper.name
        shutil.copy2(recovery_helper, staged)
    return artifact


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--system", choices=("Windows", "Darwin", "Linux"))
    parser.add_argument("--source-root", type=Path, default=SOURCE_ROOT)
    parser.add_argument("--build-root", type=Path, default=BUILD_ROOT)
    parser.add_argument("--dist-root", type=Path, default=DIST_ROOT)
    arguments = parser.parse_args()
    artifact = build(
        system=arguments.system,
        source_root=arguments.source_root,
        build_root=arguments.build_root,
        dist_root=arguments.dist_root,
    )
    print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
