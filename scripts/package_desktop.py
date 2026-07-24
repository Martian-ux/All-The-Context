"""Create direct, explicitly unsigned desktop packages for beta acceptance."""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import subprocess
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path

from allthecontext import __version__
from allthecontext.macos_bundle import validate_macos_bundle_links
from allthecontext.release_manifest import ReleaseVersion, sha256_file

ROOT = Path(__file__).resolve().parents[1]
DESKTOP_ROOT = ROOT / "dist" / "desktop"
PACKAGE_ROOT = ROOT / "dist" / "platform"
PLATFORMS = frozenset({"windows", "macos", "linux"})
ARCHITECTURES = frozenset({"x86_64", "arm64"})
UNSIGNED_NOTICE = """All The Context - unsigned community build

This package is intentionally not Authenticode-signed, Apple-notarized, or
publisher-signed. Your operating system may show an Unknown publisher,
SmartScreen, Gatekeeper, or equivalent warning.

Download only from the official GitHub Release. Before running it, verify the
adjacent SHA-256 metadata and the release's offline Ed25519 signature when the
signed channel manifest is available. No warning bypass is performed by the
application.
"""


def default_source(platform_name: str) -> Path:
    if platform_name == "windows":
        return DESKTOP_ROOT / "AllTheContextSetup.exe"
    if platform_name == "macos":
        return DESKTOP_ROOT / "AllTheContext.app"
    if platform_name == "linux":
        return DESKTOP_ROOT / "all-the-context"
    raise ValueError(f"unsupported platform: {platform_name}")


def _write_linux_tar(source: Path, output: Path) -> None:
    root = Path("AllTheContext")
    notice = UNSIGNED_NOTICE.encode("utf-8")
    with (
        output.open("wb") as raw_stream,
        gzip.GzipFile(fileobj=raw_stream, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as bundle,
    ):
        executable_info = tarfile.TarInfo((root / "all-the-context").as_posix())
        executable_info.size = source.stat().st_size
        executable_info.mode = 0o755
        executable_info.mtime = 0
        executable_info.uid = 0
        executable_info.gid = 0
        executable_info.uname = ""
        executable_info.gname = ""
        with source.open("rb") as executable:
            bundle.addfile(executable_info, executable)

        notice_info = tarfile.TarInfo((root / "IMPORTANT-UNSIGNED-COMMUNITY-BUILD.txt").as_posix())
        notice_info.size = len(notice)
        notice_info.mode = 0o644
        notice_info.mtime = 0
        notice_info.uid = 0
        notice_info.gid = 0
        notice_info.uname = ""
        notice_info.gname = ""
        bundle.addfile(notice_info, BytesIO(notice))


def _write_macos_dmg(source: Path, output: Path, *, version: str) -> None:
    validate_macos_bundle_links(source)
    hdiutil = shutil.which("hdiutil")
    if hdiutil is None:
        raise RuntimeError(
            "hdiutil is required for the macOS package; build this artifact on macOS"
        )
    with tempfile.TemporaryDirectory(prefix="atc-dmg-") as temporary:
        staging = Path(temporary)
        # Preserve the app bundle's structural code-seal representation.
        shutil.copytree(source, staging / "All The Context.app", symlinks=True)
        validate_macos_bundle_links(staging / "All The Context.app")
        (staging / "IMPORTANT - UNSIGNED COMMUNITY BUILD.txt").write_text(
            UNSIGNED_NOTICE, encoding="utf-8", newline="\n"
        )
        completed = subprocess.run(
            [
                hdiutil,
                "create",
                "-quiet",
                "-ov",
                "-format",
                "UDZO",
                "-volname",
                f"All The Context {version}",
                "-srcfolder",
                str(staging),
                str(output),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if completed.returncode != 0 or not output.is_file():
            stdout_tail = completed.stdout.strip()[-500:]
            stderr_tail = completed.stderr.strip()[-500:]
            raise RuntimeError(
                "hdiutil did not produce the package: "
                f"returncode={completed.returncode}; "
                f"output_exists={output.is_file()}; "
                f"stdout_tail={stdout_tail!r}; "
                f"stderr_tail={stderr_tail!r}"
            )


def build_platform_package(
    source: Path,
    output_dir: Path,
    *,
    version: str,
    platform_name: str,
    architecture: str,
) -> tuple[Path, Path, Path, Path]:
    ReleaseVersion.parse(version)
    if platform_name not in PLATFORMS:
        raise ValueError(f"unsupported platform: {platform_name}")
    if architecture not in ARCHITECTURES:
        raise ValueError(f"unsupported architecture: {architecture}")
    source = source.expanduser().resolve(strict=True)
    if platform_name == "macos" and not source.is_dir():
        raise ValueError("the macOS package source must be an application bundle")
    if platform_name != "macos" and not source.is_file():
        raise ValueError(f"the {platform_name} package source must be an executable file")

    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"all-the-context-{version}-{platform_name}-{architecture}-unsigned"
    if platform_name == "windows":
        package = output_dir / f"{base_name}.exe"
        shutil.copy2(source, package)
        package_format = "exe"
    elif platform_name == "macos":
        package = output_dir / f"{base_name}.dmg"
        _write_macos_dmg(source, package, version=version)
        package_format = "dmg"
    else:
        package = output_dir / f"{base_name}.tar.gz"
        _write_linux_tar(source, package)
        package_format = "tar.gz"

    notice = output_dir / f"{base_name}.IMPORTANT-UNSIGNED.txt"
    notice.write_text(UNSIGNED_NOTICE, encoding="utf-8", newline="\n")
    digest, size = sha256_file(package)
    checksum = output_dir / f"{package.name}.sha256"
    checksum.write_text(f"{digest}  {package.name}\n", encoding="utf-8", newline="\n")
    report = output_dir / f"{base_name}.package.json"
    report.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": version,
                "platform": platform_name,
                "architecture": architecture,
                "trust": "unsigned-community",
                "format": package_format,
                "package": package.name,
                "notice": notice.name,
                "source": source.name,
                "sha256": digest,
                "size": size,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return package, checksum, notice, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output-dir", type=Path, default=PACKAGE_ROOT)
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--platform", choices=sorted(PLATFORMS), required=True)
    parser.add_argument("--architecture", choices=sorted(ARCHITECTURES), required=True)
    arguments = parser.parse_args()
    source = arguments.source or default_source(arguments.platform)
    outputs = build_platform_package(
        source,
        arguments.output_dir,
        version=arguments.version,
        platform_name=arguments.platform,
        architecture=arguments.architecture,
    )
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
