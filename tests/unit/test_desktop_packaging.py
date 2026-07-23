from __future__ import annotations

import json
import os
import plistlib
import subprocess
from pathlib import Path

import pytest
from allthecontext.release_manifest import sha256_file
from allthecontext.wizard import community_build_notice

from scripts.build_desktop import (
    common_arguments,
    desktop_arguments,
    finalize_macos_bundle,
    helper_arguments,
    macos_bundle_version,
    reseal_macos_bundle,
    update_helper_arguments,
)
from scripts.check_runner_architecture import normalized_architecture, verify_runner_architecture
from scripts.evaluate_appimage import evaluate_appimage
from scripts.package_desktop import _write_macos_dmg, build_platform_package
from scripts.smoke_platform_package import (
    macos_has_publisher_identity,
    verify_package,
    windows_has_authenticode_certificate_table,
)


def test_windows_packaging_embeds_console_mcp_helper() -> None:
    helper = Path("build") / "AllTheContextMCP.exe"
    updater = Path("build") / "AllTheContextUpdater.exe"
    helper_args = helper_arguments("Windows")
    updater_args = update_helper_arguments("Windows")
    desktop_args = desktop_arguments("Windows", helper, updater)

    assert "--console" in helper_args
    assert "--onefile" in helper_args
    assert "AllTheContextMCP" in helper_args
    assert "--windowed" in updater_args
    assert "AllTheContextUpdater" in updater_args
    assert "--windowed" in desktop_args
    assert "--onefile" in desktop_args
    assert f"{helper}{os.pathsep}." in desktop_args
    assert f"{updater}{os.pathsep}." in desktop_args
    assert "AllTheContextSetup" in desktop_args
    assert "keyring.backends" in common_arguments()
    assert "keyring" in common_arguments()


def test_macos_packaging_produces_an_application_bundle() -> None:
    args = desktop_arguments("Darwin", Path("all-the-context-mcp"))
    assert "--windowed" in args
    assert "--onedir" in args
    assert "--osx-bundle-identifier" in args
    assert "com.allthecontext.desktop" in args
    assert "AllTheContext" in args


def test_native_runner_architecture_labels_fail_closed() -> None:
    assert normalized_architecture("AMD64") == "x86_64"
    assert normalized_architecture("aarch64") == "arm64"
    assert verify_runner_architecture("arm64", machine="ARM64") == "arm64"
    with pytest.raises(RuntimeError, match="architecture mismatch"):
        verify_runner_architecture("x86_64", machine="arm64")
    with pytest.raises(RuntimeError, match="unsupported"):
        normalized_architecture("mips64")


def test_macos_bundle_metadata_discloses_unsigned_distribution(tmp_path: Path) -> None:
    bundle = tmp_path / "AllTheContext.app"
    info = bundle / "Contents" / "Info.plist"
    info.parent.mkdir(parents=True)
    with info.open("wb") as stream:
        plistlib.dump({"CFBundleExecutable": "AllTheContext"}, stream)

    finalize_macos_bundle(bundle, version="0.1.0-beta.1")

    with info.open("rb") as stream:
        payload = plistlib.load(stream)
    assert payload["CFBundleIdentifier"] == "com.allthecontext.desktop"
    assert payload["CFBundleDisplayName"] == "All The Context"
    assert payload["CFBundleShortVersionString"] == "0.1.0"
    assert payload["ATCReleaseVersion"] == "0.1.0-beta.1"
    assert payload["ATCDistributionTrust"] == "unsigned-community"
    assert macos_bundle_version("12.3.4") == "12.3.4"


def test_macos_bundle_is_resealed_ad_hoc_after_metadata_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = tmp_path / "All The Context.app"
    bundle.mkdir()
    calls: list[list[str]] = []

    monkeypatch.setattr("scripts.build_desktop.shutil.which", lambda _name: "/usr/bin/codesign")

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.build_desktop.subprocess.run", run)

    reseal_macos_bundle(bundle)

    assert calls == [
        [
            "/usr/bin/codesign",
            "--force",
            "--sign",
            "-",
            "--timestamp=none",
            str(bundle),
        ],
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
    ]


def test_macos_dmg_refuses_unvalidated_bundle_before_native_tool(
    tmp_path: Path, monkeypatch
) -> None:
    bundle = tmp_path / "AllTheContext.app"
    bundle.mkdir()
    monkeypatch.setattr(
        "scripts.package_desktop.validate_macos_bundle_links",
        lambda _bundle: (_ for _ in ()).throw(RuntimeError("unsafe app link")),
    )

    with pytest.raises(RuntimeError, match="unsafe app link"):
        _write_macos_dmg(bundle, tmp_path / "output.dmg", version="0.1.0")


def test_linux_portable_package_is_reproducible_and_self_describing(tmp_path: Path) -> None:
    executable = tmp_path / "build" / "all-the-context"
    executable.parent.mkdir()
    executable.write_bytes(b"frozen-linux-executable")
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    first, checksum, notice, report = build_platform_package(
        executable,
        first_dir,
        version="0.1.0-beta.1",
        platform_name="linux",
        architecture="x86_64",
    )
    second, *_rest = build_platform_package(
        executable,
        second_dir,
        version="0.1.0-beta.1",
        platform_name="linux",
        architecture="x86_64",
    )

    assert sha256_file(first) == sha256_file(second)
    assert checksum.name.endswith(".tar.gz.sha256")
    assert "unsigned community build" in notice.read_text(encoding="utf-8").casefold()
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["source"] == "all-the-context"
    assert payload["trust"] == "unsigned-community"
    assert str(tmp_path) not in report.read_text(encoding="utf-8")
    assert verify_package(first_dir, platform_name="linux")["format"] == "tar.gz"


def test_windows_direct_package_preserves_self_installer(tmp_path: Path) -> None:
    executable = tmp_path / "AllTheContextSetup.exe"
    executable.write_bytes(b"windows-self-installer")

    package, checksum, notice, report = build_platform_package(
        executable,
        tmp_path / "output",
        version="0.1.0",
        platform_name="windows",
        architecture="x86_64",
    )

    assert package.read_bytes() == executable.read_bytes()
    assert package.name.endswith("-unsigned.exe")
    assert checksum.name == f"{package.name}.sha256"
    assert notice.name.endswith(".IMPORTANT-UNSIGNED.txt")
    assert json.loads(report.read_text(encoding="utf-8"))["format"] == "exe"


def test_windows_trust_parser_reads_pe_certificate_table_without_powershell(
    tmp_path: Path,
) -> None:
    def pe_image(*, certificate_offset: int = 0, certificate_size: int = 0) -> bytes:
        pe_offset = 128
        optional_size = 240
        image = bytearray(pe_offset + 24 + optional_size)
        image[:2] = b"MZ"
        image[60:64] = pe_offset.to_bytes(4, "little")
        image[pe_offset : pe_offset + 4] = b"PE\0\0"
        image[pe_offset + 20 : pe_offset + 22] = optional_size.to_bytes(2, "little")
        optional_offset = pe_offset + 24
        image[optional_offset : optional_offset + 2] = (0x20B).to_bytes(2, "little")
        image[optional_offset + 108 : optional_offset + 112] = (16).to_bytes(4, "little")
        certificate_entry = optional_offset + 112 + (4 * 8)
        image[certificate_entry : certificate_entry + 4] = certificate_offset.to_bytes(4, "little")
        image[certificate_entry + 4 : certificate_entry + 8] = certificate_size.to_bytes(
            4, "little"
        )
        return bytes(image)

    unsigned = tmp_path / "unsigned.exe"
    unsigned.write_bytes(pe_image())
    signed = tmp_path / "signed.exe"
    signed.write_bytes(pe_image(certificate_offset=392, certificate_size=128))

    assert windows_has_authenticode_certificate_table(unsigned) is False
    assert windows_has_authenticode_certificate_table(signed) is True

    malformed = tmp_path / "malformed.exe"
    malformed.write_bytes(b"not a PE image")
    with pytest.raises(RuntimeError, match="valid PE"):
        windows_has_authenticode_certificate_table(malformed)


def test_appimage_spike_selects_standard_library_fallback(monkeypatch) -> None:
    monkeypatch.setattr("scripts.evaluate_appimage.shutil.which", lambda _name: None)

    report = evaluate_appimage()

    assert report["decision"] == "portable-tar-gzip-fallback"
    assert report["appimage_status"] == "not-installed"
    assert report["fallback_properties"]["native_build_dependency"] is False
    assert report["fallback_properties"]["core_security_depends_on_posix_modes"] is False


def test_packaged_wizard_discloses_platform_unsigned_warnings() -> None:
    assert community_build_notice(system="Windows", frozen=False) is None
    assert "SmartScreen" in (community_build_notice(system="Windows", frozen=True) or "")
    assert "not notarized" in (community_build_notice(system="Darwin", frozen=True) or "")
    assert "checksum" in (community_build_notice(system="Linux", frozen=True) or "")


def test_macos_trust_parser_accepts_absent_or_ad_hoc_and_rejects_developer_id() -> None:
    assert macos_has_publisher_identity(1, "code object is not signed at all") is False
    assert (
        macos_has_publisher_identity(
            0,
            "Identifier=com.allthecontext.desktop\nSignature=adhoc\nTeamIdentifier=not set\n",
        )
        is False
    )
    assert (
        macos_has_publisher_identity(
            0,
            "Authority=Developer ID Application: Example (TEAM123)\nTeamIdentifier=TEAM123\n",
        )
        is True
    )
