"""Build platform-native desktop and embedded STDIO MCP executables."""

from __future__ import annotations

import argparse
import json
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
from allthecontext.build_identity import (  # noqa: E402
    PRODUCT_NAME,
    BuildIdentity,
    BuildIdentityError,
    make_build_identity,
    normalized_architecture,
    normalized_platform,
)
from allthecontext.release_manifest import ReleaseVersion  # noqa: E402


def executable_name(name: str, system: str) -> str:
    return f"{name}.exe" if system == "Windows" else name


def _identity_data_argument(identity_path: Path | None) -> list[str]:
    if identity_path is None:
        return []
    return ["--add-data", f"{identity_path}{os.pathsep}allthecontext"]


def _version_file_argument(version_file: Path | None) -> list[str]:
    return ["--version-file", str(version_file)] if version_file is not None else []


def common_arguments(
    *, source_root: Path = SOURCE_ROOT, identity_path: Path | None = None
) -> list[str]:
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
        *_identity_data_argument(identity_path),
    ]


def helper_arguments(
    system: str,
    *,
    source_root: Path = SOURCE_ROOT,
    build_root: Path = BUILD_ROOT,
    identity_path: Path | None = None,
    version_file: Path | None = None,
) -> list[str]:
    name = "AllTheContextMCP" if system == "Windows" else "all-the-context-mcp"
    return [
        *common_arguments(source_root=source_root, identity_path=identity_path),
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
        *_version_file_argument(version_file),
        str(ROOT / "scripts" / "mcp_entry.py"),
    ]


def recovery_helper_arguments(
    system: str,
    *,
    source_root: Path = SOURCE_ROOT,
    build_root: Path = BUILD_ROOT,
    identity_path: Path | None = None,
    version_file: Path | None = None,
) -> list[str]:
    """Console recovery/admin helper for windowed Windows/macOS desktop builds."""

    name = "AllTheContextRecovery" if system == "Windows" else "all-the-context-recovery"
    return [
        *common_arguments(source_root=source_root, identity_path=identity_path),
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
        *_version_file_argument(version_file),
        str(ROOT / "scripts" / "recovery_entry.py"),
    ]


def update_helper_arguments(
    system: str,
    *,
    source_root: Path = SOURCE_ROOT,
    build_root: Path = BUILD_ROOT,
    identity_path: Path | None = None,
    version_file: Path | None = None,
) -> list[str]:
    name = "AllTheContextUpdater" if system == "Windows" else "all-the-context-updater"
    subsystem = ["--windowed"] if system == "Windows" else ["--console"]
    return [
        *common_arguments(source_root=source_root, identity_path=identity_path),
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
        *_version_file_argument(version_file),
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
    identity_path: Path | None = None,
    version_file: Path | None = None,
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
        *common_arguments(source_root=source_root, identity_path=identity_path),
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
        *_version_file_argument(version_file),
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


def finalize_macos_bundle(
    bundle: Path, *, version: str, build_identity: BuildIdentity | None = None
) -> None:
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
    if build_identity is not None:
        payload.update(
            {
                "ATCBuildIdentity": build_identity.as_dict(),
                "ATCBuildIdentitySha256": build_identity.sha256,
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


def _write_build_identity(
    *, build_root: Path, version: str, source_commit: str, system: str, architecture: str
) -> tuple[Path, BuildIdentity]:
    try:
        platform_name = normalized_platform(system)
        identity = make_build_identity(
            version=version,
            source_commit=source_commit,
            platform_name=platform_name,
            architecture=architecture,
        )
    except BuildIdentityError as exc:
        raise RuntimeError(f"native build identity is invalid: {exc}") from exc
    path = build_root / "identity" / "build-identity-v1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(identity.as_dict(), sort_keys=True, separators=(",", ":")) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != rendered:
        raise RuntimeError("refusing to replace a conflicting native build identity")
    path.write_text(rendered, encoding="utf-8", newline="\n")
    return path, identity


def _windows_version_file(*, build_root: Path, identity: BuildIdentity, component: str) -> Path:
    parsed = ReleaseVersion.parse(identity.version)
    version_tuple = (parsed.major, parsed.minor, parsed.patch, parsed.prerelease)
    digest = identity.sha256
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={version_tuple}, prodvers={version_tuple}, mask=0x3f,
                   flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', '{PRODUCT_NAME}'),
    StringStruct('FileDescription', '{PRODUCT_NAME} {component} ({identity.channel})'),
    StringStruct('FileVersion', '{identity.version}'),
    StringStruct('InternalName', '{component}'),
    StringStruct('OriginalFilename', '{component}.exe'),
    StringStruct('ProductName', '{PRODUCT_NAME}'),
    StringStruct('ProductVersion', '{identity.version}'),
    StringStruct('PrivateBuild', '{identity.source_commit}'),
    StringStruct('SpecialBuild', '{identity.channel}'),
    StringStruct('Comments', 'ATC build identity sha256:{digest}'),
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
"""
    path = build_root / "identity" / f"{component}-version.txt"
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise RuntimeError("refusing to replace a conflicting Windows version resource")
    path.write_text(content, encoding="utf-8", newline="\n")
    return path


def build(
    *,
    system: str | None = None,
    source_root: Path = SOURCE_ROOT,
    build_root: Path = BUILD_ROOT,
    dist_root: Path = DIST_ROOT,
    version: str = __version__,
    source_commit: str | None = None,
    architecture: str | None = None,
) -> Path:
    try:
        import PyInstaller.__main__
    except ImportError as exc:
        raise SystemExit(
            'PyInstaller is not installed. Run: python -m pip install -e ".[packaging]"'
        ) from exc

    active_system = system or platform.system()
    if version != __version__:
        raise RuntimeError("native build version does not match the checked-out runtime")
    if source_commit is None:
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        )
        source_commit = completed.stdout.strip()
    if source_commit is None or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        raise RuntimeError("native build source commit must be a full lowercase SHA")
    active_architecture = architecture or normalized_architecture(platform.machine())
    source_root = source_root.resolve(strict=True)
    build_root = build_root.expanduser().resolve()
    dist_root = dist_root.expanduser().resolve()
    identity_path, identity = _write_build_identity(
        build_root=build_root,
        version=version,
        source_commit=source_commit,
        system=active_system,
        architecture=active_architecture,
    )
    (build_root / "spec").mkdir(parents=True, exist_ok=True)
    dist_root.mkdir(parents=True, exist_ok=True)
    helper: Path | None = None
    update_helper: Path | None = None
    recovery_helper: Path | None = None
    if active_system in {"Windows", "Darwin"}:
        helper_stem = "AllTheContextMCP" if active_system == "Windows" else "all-the-context-mcp"
        helper = build_root / "helper-dist" / executable_name(helper_stem, active_system)
        PyInstaller.__main__.run(
            helper_arguments(
                active_system,
                source_root=source_root,
                build_root=build_root,
                identity_path=identity_path,
                version_file=(
                    _windows_version_file(
                        build_root=build_root, identity=identity, component="AllTheContextMCP"
                    )
                    if active_system == "Windows"
                    else None
                ),
            )
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
            recovery_helper_arguments(
                active_system,
                source_root=source_root,
                build_root=build_root,
                identity_path=identity_path,
                version_file=(
                    _windows_version_file(
                        build_root=build_root,
                        identity=identity,
                        component="AllTheContextRecovery",
                    )
                    if active_system == "Windows"
                    else None
                ),
            )
        )
        if not recovery_helper.is_file():
            raise RuntimeError(f"Recovery helper was not produced at {recovery_helper}")
    if active_system == "Windows":
        update_helper = build_root / "update-helper-dist" / "AllTheContextUpdater.exe"
        PyInstaller.__main__.run(
            update_helper_arguments(
                active_system,
                source_root=source_root,
                build_root=build_root,
                identity_path=identity_path,
                version_file=_windows_version_file(
                    build_root=build_root, identity=identity, component="AllTheContextUpdater"
                ),
            )
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
            identity_path=identity_path,
            version_file=(
                _windows_version_file(
                    build_root=build_root, identity=identity, component="AllTheContextSetup"
                )
                if active_system == "Windows"
                else None
            ),
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
        finalize_macos_bundle(artifact, version=__version__, build_identity=identity)
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
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--source-commit")
    parser.add_argument("--architecture", choices=("x86_64", "arm64"))
    arguments = parser.parse_args()
    artifact = build(
        system=arguments.system,
        source_root=arguments.source_root,
        build_root=arguments.build_root,
        dist_root=arguments.dist_root,
        version=arguments.version,
        source_commit=arguments.source_commit,
        architecture=arguments.architecture,
    )
    print(artifact)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
