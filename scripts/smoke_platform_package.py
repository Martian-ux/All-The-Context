"""Verify a direct platform package and its explicit unsigned trust boundary."""

from __future__ import annotations

import argparse
import contextlib
import json
import plistlib
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from allthecontext.macos_bundle import validate_macos_bundle_links
from allthecontext.release_manifest import sha256_file

try:
    from scripts.check_runner_architecture import normalized_architecture
except ModuleNotFoundError:  # Direct ``python scripts/...`` execution.
    from check_runner_architecture import normalized_architecture


def macos_has_publisher_identity(returncode: int, details: str) -> bool:
    """Distinguish a Developer ID identity from absent/ad-hoc structural signing."""

    if returncode != 0:
        return False
    lines = [line.strip() for line in details.splitlines() if line.strip()]
    if any(line.startswith("Authority=") for line in lines):
        return True
    team_identifiers = [
        line.partition("=")[2] for line in lines if line.startswith("TeamIdentifier=")
    ]
    if any(team and team.casefold() != "not set" for team in team_identifiers):
        return True
    return not any(line == "Signature=adhoc" for line in lines)


def _load_report(
    directory: Path,
    platform_name: str,
    architecture: str | None = None,
) -> tuple[Path, dict[str, Any]]:
    architecture_pattern = f"{architecture}-" if architecture is not None else "*"
    matches = sorted(
        directory.glob(f"*-{platform_name}-{architecture_pattern}unsigned.package.json")
    )
    if len(matches) != 1:
        raise RuntimeError(f"expected one {platform_name} package report, found {len(matches)}")
    report_path = matches[0]
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("package report is not an object")
    return report_path, payload


def windows_has_authenticode_certificate_table(package: Path) -> bool:
    """Read the PE certificate-table directory without invoking a platform shell."""

    file_size = package.stat().st_size
    with package.open("rb") as stream:
        dos_header = stream.read(64)
        if len(dos_header) != 64 or dos_header[:2] != b"MZ":
            raise RuntimeError("Windows package is not a valid PE executable")
        pe_offset = int.from_bytes(dos_header[60:64], "little")
        if pe_offset < 64 or pe_offset > file_size - 24:
            raise RuntimeError("Windows package has an invalid PE header offset")
        stream.seek(pe_offset)
        pe_header = stream.read(24)
        if len(pe_header) != 24 or pe_header[:4] != b"PE\0\0":
            raise RuntimeError("Windows package is missing its PE signature")
        optional_header_size = int.from_bytes(pe_header[20:22], "little")
        optional_header = stream.read(optional_header_size)
        if len(optional_header) != optional_header_size:
            raise RuntimeError("Windows package has a truncated PE optional header")

    magic = int.from_bytes(optional_header[:2], "little")
    if magic == 0x10B:  # PE32
        directory_count_offset = 92
        directory_offset = 96
    elif magic == 0x20B:  # PE32+
        directory_count_offset = 108
        directory_offset = 112
    else:
        raise RuntimeError("Windows package has an unsupported PE optional header")
    if len(optional_header) < directory_count_offset + 4:
        raise RuntimeError("Windows package is missing its PE data-directory count")
    directory_count = int.from_bytes(
        optional_header[directory_count_offset : directory_count_offset + 4], "little"
    )
    certificate_entry = directory_offset + (4 * 8)
    if directory_count <= 4:
        return False
    if len(optional_header) < certificate_entry + 8:
        raise RuntimeError("Windows package has a truncated certificate-table directory")
    location = int.from_bytes(optional_header[certificate_entry : certificate_entry + 4], "little")
    size = int.from_bytes(optional_header[certificate_entry + 4 : certificate_entry + 8], "little")
    return location != 0 or size != 0


def _verify_windows_unsigned(package: Path) -> None:
    if windows_has_authenticode_certificate_table(package):
        raise RuntimeError("Windows artifact trust state is not the declared unsigned state")


def _macos_helper(app: Path, name: str) -> Path:
    candidates = (
        app / "Contents" / "MacOS" / name,
        app / "Contents" / "Frameworks" / name,
    )
    matches = [path for path in candidates if path.is_file()]
    if len(matches) != 1:
        raise RuntimeError(f"macOS package must contain exactly one {name} helper")
    return matches[0]


def _verify_macos_binary_architecture(binary: Path, *, expected_architecture: str) -> None:
    lipo = shutil.which("lipo")
    if lipo is None:
        raise RuntimeError("lipo is required to verify the macOS package architecture")
    completed = subprocess.run(
        [lipo, "-archs", str(binary)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"macOS package architecture is unreadable: {binary.name}")
    try:
        architectures = {
            normalized_architecture(value) for value in completed.stdout.split() if value.strip()
        }
    except RuntimeError as exc:
        raise RuntimeError(f"macOS package architecture is unsupported: {binary.name}") from exc
    if architectures != {expected_architecture}:
        observed = ",".join(sorted(architectures)) or "none"
        raise RuntimeError(
            "macOS package architecture does not match its label: "
            f"{binary.name} expected {expected_architecture}, observed {observed}"
        )


def verify_macos_app(
    app: Path,
    *,
    expected_architecture: str,
    expected_version: str,
) -> None:
    validate_macos_bundle_links(app)
    info_path = app / "Contents" / "Info.plist"
    if not info_path.is_file() or info_path.is_symlink():
        raise RuntimeError("macOS package is missing safe application metadata")
    try:
        with info_path.open("rb") as stream:
            info = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise RuntimeError("macOS package application metadata is invalid") from exc
    expected_metadata = {
        "ATCDistributionTrust": "unsigned-community",
        "ATCReleaseVersion": expected_version,
        "CFBundleDisplayName": "All The Context",
        "CFBundleExecutable": "AllTheContext",
        "CFBundleIdentifier": "com.allthecontext.desktop",
    }
    if not isinstance(info, dict) or any(
        info.get(key) != value for key, value in expected_metadata.items()
    ):
        raise RuntimeError("macOS package application identity does not match its report")

    executable = app / "Contents" / "MacOS" / "AllTheContext"
    if not executable.is_file() or executable.is_symlink():
        raise RuntimeError("macOS package is missing its main executable")
    mcp_helper = _macos_helper(app, "all-the-context-mcp")
    recovery_helper = _macos_helper(app, "all-the-context-recovery")
    for binary in (executable, mcp_helper, recovery_helper):
        _verify_macos_binary_architecture(binary, expected_architecture=expected_architecture)

    codesign = shutil.which("codesign")
    if codesign is None:
        raise RuntimeError("codesign is required to verify the macOS package seal")
    seal = subprocess.run(
        [codesign, "--verify", "--deep", "--strict", str(app)],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if seal.returncode != 0:
        raise RuntimeError("macOS package has an invalid structural code seal")
    signature = subprocess.run(
        [codesign, "--display", "--verbose=4", str(app)],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    details = f"{signature.stdout}\n{signature.stderr}"
    if signature.returncode != 0:
        raise RuntimeError("macOS package structural signature details are unreadable")
    if macos_has_publisher_identity(signature.returncode, details):
        raise RuntimeError("macOS app has a publisher identity but declares itself unsigned")


def _verify_macos_unsigned(
    package: Path,
    *,
    expected_architecture: str,
    expected_version: str,
) -> None:
    subprocess.run(
        ["hdiutil", "verify", str(package)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    mount_point = Path(tempfile.mkdtemp(prefix="atc-package-dmg-"))
    attached_ok = False
    detached = False
    try:
        attached = subprocess.run(
            [
                "hdiutil",
                "attach",
                "-readonly",
                "-nobrowse",
                "-mountpoint",
                str(mount_point),
                "-plist",
                str(package),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        attached_ok = True
        attach_payload = plistlib.loads(attached.stdout)
        mount_points = [
            Path(str(entity["mount-point"]))
            for entity in attach_payload.get("system-entities", [])
            if isinstance(entity, dict) and entity.get("mount-point")
        ]
        if len(mount_points) != 1 or mount_points[0].resolve() != mount_point.resolve():
            raise RuntimeError("macOS package did not mount exactly one requested volume")
        app = mount_point / "All The Context.app"
        notice = mount_point / "IMPORTANT - UNSIGNED COMMUNITY BUILD.txt"
        if (
            not app.is_dir()
            or app.is_symlink()
            or not notice.is_file()
            or notice.is_symlink()
            or "unsigned community build" not in notice.read_text(encoding="utf-8").casefold()
        ):
            raise RuntimeError("macOS package is missing its app or unsigned notice")
        verify_macos_app(
            app,
            expected_architecture=expected_architecture,
            expected_version=expected_version,
        )
    finally:
        detach = subprocess.run(
            ["hdiutil", "detach", str(mount_point)],
            check=False,
            capture_output=True,
            timeout=120,
        )
        detached = detach.returncode == 0
        if attached_ok and not detached:
            forced_detach = subprocess.run(
                ["hdiutil", "detach", "-force", str(mount_point)],
                check=False,
                capture_output=True,
                timeout=120,
            )
            detached = forced_detach.returncode == 0
        with contextlib.suppress(OSError):
            mount_point.rmdir()
        if attached_ok and not detached:
            raise RuntimeError("macOS package volume could not be detached")


def _verify_linux_portable(package: Path) -> None:
    with tarfile.open(package, "r:gz") as bundle:
        members = {member.name: member for member in bundle.getmembers()}
        executable = members.get("AllTheContext/all-the-context")
        notice = members.get("AllTheContext/IMPORTANT-UNSIGNED-COMMUNITY-BUILD.txt")
        if executable is None or not executable.isfile() or executable.mode != 0o755:
            raise RuntimeError("Linux package executable is missing or not portable-executable")
        if notice is None or not notice.isfile():
            raise RuntimeError("Linux package unsigned notice is missing")
        extracted_notice = bundle.extractfile(notice)
        if (
            extracted_notice is None
            or b"unsigned community build" not in extracted_notice.read().lower()
        ):
            raise RuntimeError("Linux package unsigned notice is invalid")


def verify_package(
    directory: Path,
    *,
    platform_name: str,
    architecture: str | None = None,
) -> dict[str, Any]:
    directory = directory.expanduser().resolve(strict=True)
    requested_architecture = architecture
    _report_path, report = _load_report(directory, platform_name, architecture)
    expected_keys = {
        "schema_version",
        "version",
        "platform",
        "architecture",
        "trust",
        "format",
        "package",
        "notice",
        "source",
        "sha256",
        "size",
        "recovery_surface",
        "recovery_console_helper",
    }
    expected_recovery_surface = {
        "windows": "embedded-console-helper",
        "macos": "bundled-console-helper",
        "linux": "console-main-binary",
    }
    expected_recovery_helper = {
        "windows": "AllTheContextRecovery.exe",
        "macos": "all-the-context-recovery",
        "linux": "all-the-context",
    }
    if set(report) != expected_keys:
        raise RuntimeError("package report has an unexpected schema")
    if report["schema_version"] != 1 or report["platform"] != platform_name:
        raise RuntimeError("package report identifies the wrong platform")
    reported_architecture = report.get("architecture")
    if reported_architecture not in {"arm64", "x86_64"}:
        raise RuntimeError("package report architecture is invalid")
    if requested_architecture is not None and reported_architecture != requested_architecture:
        raise RuntimeError("package report architecture does not match operator input")
    version = report.get("version")
    if not isinstance(version, str) or not version:
        raise RuntimeError("package report version is invalid")
    if report["trust"] != "unsigned-community":
        raise RuntimeError("package report does not disclose unsigned trust")
    if report.get("recovery_surface") != expected_recovery_surface.get(platform_name):
        raise RuntimeError("package report recovery surface does not match platform")
    if report.get("recovery_console_helper") != expected_recovery_helper.get(platform_name):
        raise RuntimeError("package report recovery helper name does not match platform")
    package = directory / str(report["package"])
    notice = directory / str(report["notice"])
    if not package.is_file() or not notice.is_file():
        raise RuntimeError("package or adjacent unsigned notice is missing")
    if "unsigned community build" not in notice.read_text(encoding="utf-8").casefold():
        raise RuntimeError("adjacent unsigned notice is invalid")
    digest, size = sha256_file(package)
    if digest != report["sha256"] or size != report["size"]:
        raise RuntimeError("package digest does not match its report")
    checksum = package.with_name(f"{package.name}.sha256")
    if checksum.read_text(encoding="utf-8") != f"{digest}  {package.name}\n":
        raise RuntimeError("package checksum sidecar is invalid")

    if platform_name == "windows":
        _verify_windows_unsigned(package)
    elif platform_name == "macos":
        _verify_macos_unsigned(
            package,
            expected_architecture=reported_architecture,
            expected_version=version,
        )
    elif platform_name == "linux":
        _verify_linux_portable(package)
    else:
        raise ValueError(f"unsupported platform: {platform_name}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--platform", choices=("windows", "macos", "linux"), required=True)
    parser.add_argument("--architecture", choices=("arm64", "x86_64"))
    arguments = parser.parse_args()
    report = verify_package(
        arguments.directory,
        platform_name=arguments.platform,
        architecture=arguments.architecture,
    )
    print(
        json.dumps(
            {
                "platform": report["platform"],
                "architecture": report["architecture"],
                "trust": report["trust"],
                "package": report["package"],
                "verified": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
