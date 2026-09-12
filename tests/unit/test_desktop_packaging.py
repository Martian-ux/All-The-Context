from __future__ import annotations

import json
import os
import plistlib
import subprocess
import tarfile
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
    recovery_helper_arguments,
    reseal_macos_bundle,
    update_helper_arguments,
)
from scripts.check_runner_architecture import normalized_architecture, verify_runner_architecture
from scripts.evaluate_appimage import evaluate_appimage
from scripts.package_desktop import _write_macos_dmg, build_platform_package, unsigned_notice
from scripts.smoke_platform_package import (
    _load_report,
    macos_has_publisher_identity,
    verify_macos_app,
    verify_package,
    windows_has_authenticode_certificate_table,
)


def test_windows_packaging_embeds_console_mcp_and_recovery_helpers() -> None:
    helper = Path("build") / "AllTheContextMCP.exe"
    recovery = Path("build") / "AllTheContextRecovery.exe"
    updater = Path("build") / "AllTheContextUpdater.exe"
    helper_args = helper_arguments("Windows")
    recovery_args = recovery_helper_arguments("Windows")
    updater_args = update_helper_arguments("Windows")
    desktop_args = desktop_arguments("Windows", helper, updater, recovery)

    assert "--console" in helper_args
    assert "--onefile" in helper_args
    assert "AllTheContextMCP" in helper_args
    assert "--console" in recovery_args
    assert "AllTheContextRecovery" in recovery_args
    assert "recovery_entry.py" in "".join(recovery_args)
    assert "--windowed" in updater_args
    assert "AllTheContextUpdater" in updater_args
    assert "--windowed" in desktop_args
    assert "--onefile" in desktop_args
    assert f"{helper}{os.pathsep}." in desktop_args
    assert f"{recovery}{os.pathsep}." in desktop_args
    assert f"{updater}{os.pathsep}." in desktop_args
    assert "AllTheContextSetup" in desktop_args
    assert "keyring.backends" in common_arguments()
    assert "keyring" in common_arguments()


def test_macos_packaging_embeds_console_recovery_helper() -> None:
    helper = Path("all-the-context-mcp")
    recovery = Path("all-the-context-recovery")
    args = desktop_arguments("Darwin", helper, recovery_helper=recovery)
    recovery_args = recovery_helper_arguments("Darwin")
    assert "--windowed" in args
    assert "--onedir" in args
    assert "--osx-bundle-identifier" in args
    assert "com.allthecontext.desktop" in args
    assert "AllTheContext" in args
    assert "--console" in recovery_args
    assert "all-the-context-recovery" in recovery_args
    assert f"{recovery}{os.pathsep}." in args


def test_linux_desktop_is_console_capable_without_separate_recovery_binary() -> None:
    args = desktop_arguments("Linux", None)
    assert "--windowed" not in args
    assert "all-the-context" in args


def test_package_report_records_platform_recovery_surface(tmp_path: Path) -> None:
    executable = tmp_path / "all-the-context"
    executable.write_bytes(b"linux-binary")
    _package, _checksum, _notice, report = build_platform_package(
        executable,
        tmp_path / "out",
        version="0.1.0-beta.1",
        platform_name="linux",
        architecture="x86_64",
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["recovery_surface"] == "console-main-binary"
    assert payload["recovery_console_helper"] == "all-the-context"


def test_platform_package_report_can_be_selected_by_architecture(tmp_path: Path) -> None:
    arm = tmp_path / "all-the-context-0.1.0-beta.1-macos-arm64-unsigned.package.json"
    intel = tmp_path / "all-the-context-0.1.0-beta.1-macos-x86_64-unsigned.package.json"
    arm.write_text('{"architecture":"arm64"}', encoding="utf-8")
    intel.write_text('{"architecture":"x86_64"}', encoding="utf-8")

    path, payload = _load_report(tmp_path, "macos", "arm64")

    assert path == arm
    assert payload == {"architecture": "arm64"}


def test_windows_package_report_records_embedded_recovery_helper(tmp_path: Path) -> None:
    executable = tmp_path / "AllTheContextSetup.exe"
    executable.write_bytes(b"windows-setup")
    _package, _checksum, _notice, report = build_platform_package(
        executable,
        tmp_path / "out",
        version="0.1.0",
        platform_name="windows",
        architecture="x86_64",
    )
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["recovery_surface"] == "embedded-console-helper"
    assert payload["recovery_console_helper"] == "AllTheContextRecovery.exe"


def test_smoke_packaged_recovery_fails_closed_without_frozen_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.smoke_packaged_recovery as smoke

    monkeypatch.setattr(smoke, "DIST", tmp_path / "dist")
    monkeypatch.setattr(smoke, "BUILD", tmp_path / "build")
    with pytest.raises(SystemExit, match="recovery"):
        smoke.recovery_command("Windows")
    with pytest.raises(SystemExit, match="recovery"):
        smoke.recovery_command("Darwin")
    with pytest.raises(SystemExit, match="recovery"):
        smoke.recovery_command("Linux")


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


def test_macos_dmg_failure_reports_process_evidence(tmp_path: Path, monkeypatch) -> None:
    bundle = tmp_path / "AllTheContext.app"
    bundle.mkdir()
    output = tmp_path / "output.dmg"
    monkeypatch.setattr("scripts.package_desktop.shutil.which", lambda _name: "hdiutil")
    monkeypatch.setattr(
        "scripts.package_desktop.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            7,
            "native stdout",
            "native stderr",
        ),
    )

    with pytest.raises(RuntimeError) as captured:
        _write_macos_dmg(bundle, output, version="0.1.0")

    message = str(captured.value)
    assert "returncode=7" in message
    assert "output_exists=False" in message
    assert "stdout_tail='native stdout'" in message
    assert "stderr_tail='native stderr'" in message


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
    with pytest.raises(RuntimeError, match="found 0"):
        verify_package(first_dir, platform_name="linux", architecture="arm64")


def test_smoke_source_commit_requires_complete_native_identity(tmp_path: Path) -> None:
    executable = tmp_path / "build" / "all-the-context"
    executable.parent.mkdir()
    executable.write_bytes(b"source-bound-linux-executable")
    output = tmp_path / "output"
    source_commit = "a" * 40
    _package, _checksum, _notice, report = build_platform_package(
        executable,
        output,
        version="0.1.0-beta.1",
        platform_name="linux",
        architecture="x86_64",
        source_commit=source_commit,
    )

    assert verify_package(output, platform_name="linux", source_commit=source_commit)
    payload = json.loads(report.read_text(encoding="utf-8"))
    payload.pop("build_identity")
    report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RuntimeError, match="complete build identity"):
        verify_package(output, platform_name="linux", source_commit=source_commit)


def test_smoke_package_report_rejects_float_schema_version(tmp_path: Path) -> None:
    executable = tmp_path / "build" / "all-the-context"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"float-schema-version-package")
    output = tmp_path / "output"
    _package, _checksum, _notice, report = build_platform_package(
        executable,
        output,
        version="0.1.0-beta.1",
        platform_name="linux",
        architecture="x86_64",
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    payload["schema_version"] = 1.0
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeError, match="wrong platform"):
        verify_package(output, platform_name="linux")


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


_APPLE_OR_GATEKEEPER_MARKERS = ("apple", "notariz", "gatekeeper")


def _assert_official_notice_has_no_apple_wording(text: str) -> None:
    lowered = text.casefold()
    assert "unsigned community build" in lowered
    for marker in _APPLE_OR_GATEKEEPER_MARKERS:
        assert marker not in lowered, f"official notice must not mention {marker!r}"


def _linux_embedded_notice(package: Path) -> str:
    with tarfile.open(package, "r:gz") as bundle:
        extracted = bundle.extractfile("AllTheContext/IMPORTANT-UNSIGNED-COMMUNITY-BUILD.txt")
        assert extracted is not None
        return extracted.read().decode("utf-8")


def test_windows_official_package_notice_has_no_apple_or_gatekeeper_wording(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "AllTheContextSetup.exe"
    executable.write_bytes(b"windows-self-installer")

    _package, _checksum, notice, _report = build_platform_package(
        executable,
        tmp_path / "output",
        version="0.1.0-beta.2",
        platform_name="windows",
        architecture="x86_64",
    )

    text = notice.read_text(encoding="utf-8")
    lowered = text.casefold()
    _assert_official_notice_has_no_apple_wording(text)
    assert "smartscreen" in lowered
    assert "unknown publisher" in lowered
    assert text == unsigned_notice("windows")


def test_linux_official_package_notice_has_no_apple_or_gatekeeper_wording(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "all-the-context"
    executable.write_bytes(b"linux-binary")

    package, _checksum, notice, _report = build_platform_package(
        executable,
        tmp_path / "output",
        version="0.1.0-beta.2",
        platform_name="linux",
        architecture="x86_64",
    )

    adjacent = notice.read_text(encoding="utf-8")
    embedded = _linux_embedded_notice(package)
    for text in (adjacent, embedded):
        lowered = text.casefold()
        _assert_official_notice_has_no_apple_wording(text)
        assert "direct-install" in lowered
        assert "unsigned" in lowered
        assert "sha-256" in lowered
        assert "checksum" in lowered
        assert "provenance" in lowered
        assert "attestation" in lowered
        assert "signed channel manifest" not in lowered
        assert "ed25519" not in lowered
        assert "updater" not in lowered
        assert text == unsigned_notice("linux")


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


def _test_macos_app(tmp_path: Path) -> Path:
    app = tmp_path / "All The Context.app"
    contents = app / "Contents"
    macos = contents / "MacOS"
    frameworks = contents / "Frameworks"
    macos.mkdir(parents=True)
    frameworks.mkdir()
    with (contents / "Info.plist").open("wb") as stream:
        plistlib.dump(
            {
                "ATCDistributionTrust": "unsigned-community",
                "ATCReleaseVersion": "0.1.0-beta.1",
                "CFBundleDisplayName": "All The Context",
                "CFBundleExecutable": "AllTheContext",
                "CFBundleIdentifier": "com.allthecontext.desktop",
            },
            stream,
        )
    (macos / "AllTheContext").write_bytes(b"main")
    (frameworks / "all-the-context-mcp").write_bytes(b"mcp")
    (frameworks / "all-the-context-recovery").write_bytes(b"recovery")
    return app


def test_macos_package_verifier_binds_identity_seal_and_binary_architecture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _test_macos_app(tmp_path)
    monkeypatch.setattr(
        "scripts.smoke_platform_package.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "-archs" in command:
            return subprocess.CompletedProcess(command, 0, "arm64\n", "")
        if "--display" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                "",
                "Signature=adhoc\nTeamIdentifier=not set\n",
            )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.smoke_platform_package.subprocess.run", run)

    verify_macos_app(
        app,
        expected_architecture="arm64",
        expected_version="0.1.0-beta.1",
    )


def test_macos_package_verifier_rejects_mislabeled_helper_architecture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _test_macos_app(tmp_path)
    monkeypatch.setattr(
        "scripts.smoke_platform_package.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "-archs" in command:
            architecture = "x86_64" if command[-1].endswith("all-the-context-mcp") else "arm64"
            return subprocess.CompletedProcess(command, 0, architecture, "")
        return subprocess.CompletedProcess(
            command,
            0,
            "",
            "Signature=adhoc\nTeamIdentifier=not set\n",
        )

    monkeypatch.setattr("scripts.smoke_platform_package.subprocess.run", run)

    with pytest.raises(RuntimeError, match="does not match its label"):
        verify_macos_app(
            app,
            expected_architecture="arm64",
            expected_version="0.1.0-beta.1",
        )


def test_macos_package_verifier_rejects_invalid_structural_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _test_macos_app(tmp_path)
    monkeypatch.setattr(
        "scripts.smoke_platform_package.shutil.which",
        lambda name: f"/usr/bin/{name}",
    )

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "-archs" in command:
            return subprocess.CompletedProcess(command, 0, "arm64", "")
        if "--verify" in command:
            return subprocess.CompletedProcess(command, 1, "", "invalid seal")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.smoke_platform_package.subprocess.run", run)

    with pytest.raises(RuntimeError, match="structural code seal"):
        verify_macos_app(
            app,
            expected_architecture="arm64",
            expected_version="0.1.0-beta.1",
        )
