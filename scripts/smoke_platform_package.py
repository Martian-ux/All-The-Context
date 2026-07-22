"""Verify a direct platform package and its explicit unsigned trust boundary."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import subprocess
import tarfile
from pathlib import Path
from typing import Any

from allthecontext.release_manifest import sha256_file


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


def _load_report(directory: Path, platform_name: str) -> tuple[Path, dict[str, Any]]:
    matches = sorted(directory.glob(f"*-{platform_name}-*.package.json"))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {platform_name} package report, found {len(matches)}")
    report_path = matches[0]
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("package report is not an object")
    return report_path, payload


def _verify_windows_unsigned(package: Path) -> None:
    environment = os.environ.copy()
    environment["ATC_PACKAGE_PATH"] = str(package)
    script = (
        "$signature=Get-AuthenticodeSignature -LiteralPath $env:ATC_PACKAGE_PATH;"
        "[Console]::Out.Write($signature.Status.ToString())"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    if completed.stdout.strip() != "NotSigned":
        raise RuntimeError("Windows artifact trust state is not the declared unsigned state")


def _verify_macos_unsigned(package: Path) -> None:
    subprocess.run(
        ["hdiutil", "verify", str(package)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    attached = subprocess.run(
        ["hdiutil", "attach", "-readonly", "-nobrowse", "-plist", str(package)],
        check=True,
        capture_output=True,
        timeout=120,
    )
    attach_payload = plistlib.loads(attached.stdout)
    mount_points = [
        Path(str(entity["mount-point"]))
        for entity in attach_payload.get("system-entities", [])
        if isinstance(entity, dict) and entity.get("mount-point")
    ]
    if len(mount_points) != 1:
        raise RuntimeError("macOS package did not mount exactly one volume")
    mount_point = mount_points[0]
    try:
        app = mount_point / "All The Context.app"
        notice = mount_point / "IMPORTANT - UNSIGNED COMMUNITY BUILD.txt"
        if (
            not app.is_dir()
            or "unsigned community build" not in notice.read_text(encoding="utf-8").casefold()
        ):
            raise RuntimeError("macOS package is missing its app or unsigned notice")
        signature = subprocess.run(
            ["codesign", "--display", "--verbose=4", str(app)],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        # PyInstaller may apply a structural ad-hoc signature on modern macOS.
        # It conveys no publisher identity and does not change the community
        # package's unnotarized/unknown-developer trust state.
        details = f"{signature.stdout}\n{signature.stderr}"
        if macos_has_publisher_identity(signature.returncode, details):
            raise RuntimeError("macOS app has a publisher identity but declares itself unsigned")
    finally:
        subprocess.run(
            ["hdiutil", "detach", str(mount_point)],
            check=True,
            capture_output=True,
            timeout=120,
        )


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


def verify_package(directory: Path, *, platform_name: str) -> dict[str, Any]:
    directory = directory.expanduser().resolve(strict=True)
    _report_path, report = _load_report(directory, platform_name)
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
    }
    if set(report) != expected_keys:
        raise RuntimeError("package report has an unexpected schema")
    if report["schema_version"] != 1 or report["platform"] != platform_name:
        raise RuntimeError("package report identifies the wrong platform")
    if report["trust"] != "unsigned-community":
        raise RuntimeError("package report does not disclose unsigned trust")
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
        _verify_macos_unsigned(package)
    elif platform_name == "linux":
        _verify_linux_portable(package)
    else:
        raise ValueError(f"unsupported platform: {platform_name}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--platform", choices=("windows", "macos", "linux"), required=True)
    arguments = parser.parse_args()
    report = verify_package(arguments.directory, platform_name=arguments.platform)
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
