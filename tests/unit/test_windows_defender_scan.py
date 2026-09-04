"""Adversarial, content-free tests for the local Windows Defender receipt."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scripts import windows_defender_scan as scan_module
from scripts.build_release_assets import build_archive
from scripts.installed_component_manifest import create_manifest

SOURCE_COMMIT = "a" * 40
VERSION = "0.1.0-beta.7"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _pe_image(*, marker: bytes = b"", certificate_offset: int = 0) -> bytes:
    pe_offset = 128
    optional_size = 240
    header_size = pe_offset + 24 + optional_size
    image = bytearray(max(header_size, certificate_offset + 16))
    image[:2] = b"MZ"
    image[60:64] = pe_offset.to_bytes(4, "little")
    image[pe_offset : pe_offset + 4] = b"PE\0\0"
    image[pe_offset + 20 : pe_offset + 22] = optional_size.to_bytes(2, "little")
    optional_offset = pe_offset + 24
    image[optional_offset : optional_offset + 2] = (0x20B).to_bytes(2, "little")
    image[optional_offset + 108 : optional_offset + 112] = (16).to_bytes(4, "little")
    certificate_entry = optional_offset + 112 + (4 * 8)
    image[certificate_entry : certificate_entry + 4] = certificate_offset.to_bytes(4, "little")
    image[certificate_entry + 4 : certificate_entry + 8] = (
        16 if certificate_offset else 0
    ).to_bytes(4, "little")
    image.extend(marker)
    return bytes(image)


@dataclass(frozen=True)
class _Stage:
    root: Path
    package: Path
    archive_package: Path
    components: dict[str, Path]
    manifest: Path


def _stage(tmp_path: Path, *, with_manifest: bool = False) -> _Stage:
    root = tmp_path / "candidate"
    root.mkdir()
    package_dir = root / "release"
    package_dir.mkdir()
    build_dir = root / "build"
    build_dir.mkdir()
    package = package_dir / "all-the-context-0.1.0-beta.7-windows-x86_64-unsigned.exe"
    main = build_dir / "AllTheContextSetup.exe"
    main_bytes = _pe_image(marker=b"main")
    package.write_bytes(main_bytes)
    main.write_bytes(main_bytes)
    components = {
        "main": main,
        "mcp": build_dir / "AllTheContextMCP.exe",
        "recovery": build_dir / "AllTheContextRecovery.exe",
        "updater": build_dir / "AllTheContextUpdater.exe",
    }
    components["mcp"].write_bytes(_pe_image(marker=b"mcp", certificate_offset=392))
    components["recovery"].write_bytes(_pe_image(marker=b"recovery"))
    components["updater"].write_bytes(_pe_image(marker=b"updater"))
    archive_package = root / "manifest" / "AllTheContextSetup.exe"
    archive_package.parent.mkdir()
    archive_package.write_bytes(main_bytes)
    manifest_path = archive_package.parent / scan_module.MANIFEST_FILE_NAME
    if with_manifest:
        create_manifest(
            output_dir=archive_package.parent,
            package_path=archive_package,
            direct_package_path=package,
            component_paths=components,
            source_root=root,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
        )
        build_archive(
            archive_package.parent,
            root / "archive",
            version=VERSION,
            platform_name="windows",
            architecture="x86_64",
        )
    return _Stage(root, package, archive_package, components, manifest_path)


def _status(*, updated: datetime = NOW, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "engine_version": "1.1.1.1",
        "platform_version": "4.18.1.1",
        "service_version": "4.18.1.1",
        "signature_version": "1.457.381.0",
        "signature_updated_at": updated.isoformat().replace("+00:00", "Z"),
        "antivirus_enabled": True,
        "real_time_protection_enabled": True,
        "ioav_protection_enabled": True,
        "running_mode": "Normal",
    }
    value.update(overrides)
    return value


class _FakeDefender:
    def __init__(
        self,
        *,
        statuses: list[dict[str, Any]] | None = None,
        histories: list[object] | None = None,
        codes: list[int] | None = None,
        scan_effect: Any = None,
    ) -> None:
        self.statuses = list(statuses or [_status(), _status()])
        self.histories = list(histories or [[], []])
        self.codes = list(codes or [0] * 5)
        self.scan_effect = scan_effect
        self.scanned: list[Path] = []

    def status(self) -> dict[str, Any]:
        value = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if isinstance(value, BaseException):
            raise value
        return value

    def history(self) -> object:
        value = self.histories.pop(0) if len(self.histories) > 1 else self.histories[0]
        if isinstance(value, BaseException):
            raise value
        return value

    def custom_scan(self, path: Path) -> int:
        self.scanned.append(path)
        if self.scan_effect is not None:
            self.scan_effect(path)
        return self.codes.pop(0) if self.codes else 0


def _scan(stage: _Stage, defender: _FakeDefender, **kwargs: Any) -> dict[str, Any]:
    platform_name = kwargs.pop("platform_name", "Windows")
    component_paths = kwargs.pop("component_paths", stage.components)
    return scan_module.scan_candidate(
        package_path=stage.package,
        component_paths=component_paths,
        source_root=stage.root,
        version=VERSION,
        source_commit=SOURCE_COMMIT,
        defender=defender,
        platform_name=platform_name,
        now=lambda: NOW,
        **kwargs,
    )


def _history(path: Path, *, detection_id: str = "d1", action: str | None = None) -> dict[str, Any]:
    return {
        "DetectionID": detection_id,
        "ThreatID": "t1",
        "InitialDetectionTime": "2026-09-03T11:59:00Z",
        "LastThreatStatusChangeTime": "2026-09-03T11:59:00Z",
        "ActionSuccess": True,
        "Action": action,
        "Resources": [str(path)],
    }


def test_clean_receipt_binds_package_and_four_component_digests(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    fake = _FakeDefender()
    receipt = _scan(stage, fake)

    assert receipt["status"] == "pass"
    assert receipt["outcome"] == "clean"
    assert len(receipt["components"]) == 4
    assert receipt["invocation"] == {
        "completed": True,
        "result": "success",
        "scan_type": "custom",
        "settings_changed": False,
        "target_count": 5,
        "tool": "MpCmdRun.exe",
    }
    assert receipt["defender"]["status"] == "ready"
    assert receipt["defender"]["before"]["engine_version"] == "1.1.1.1"
    assert receipt["defender"]["before"]["platform_version"] == "4.18.1.1"
    assert receipt["defender"]["before"]["signature_version"] == "1.457.381.0"
    assert receipt["post_scan"] == {
        "presence": "all_present",
        "rehashed": True,
        "stable": True,
        "target_count": 5,
    }
    raw = json.dumps(receipt)
    assert str(stage.root) not in raw
    assert '"path"' not in raw
    assert len(fake.scanned) == 5


def test_manifest_binding_rejects_exactly_changed_metadata(tmp_path: Path) -> None:
    stage = _stage(tmp_path, with_manifest=True)
    receipt = _scan(
        stage,
        _FakeDefender(),
        manifest_path=stage.manifest,
        archive_package_path=stage.archive_package,
    )
    assert receipt["status"] == "pass"
    assert receipt["manifest"]["filename"] == scan_module.MANIFEST_FILE_NAME

    stage.manifest.write_bytes(stage.manifest.read_bytes() + b" ")
    with pytest.raises(scan_module.WindowsDefenderScanError):
        _scan(
            stage,
            _FakeDefender(),
            manifest_path=stage.manifest,
            archive_package_path=stage.archive_package,
        )


@pytest.mark.parametrize(
    ("kwargs", "reason", "status"),
    [
        ({"platform_name": "Linux"}, "unsupported_platform", "unavailable"),
        (
            {"defender": _FakeDefender(statuses=[_status(updated=NOW - timedelta(days=8))])},
            "signature_stale",
            "fail",
        ),
        (
            {"defender": _FakeDefender(statuses=[_status(antivirus_enabled=False)])},
            "defender_disabled",
            "fail",
        ),
        (
            {"defender": _FakeDefender(statuses=[{**_status(), "engine_version": "bad"}])},
            "defender_malformed",
            "fail",
        ),
        (
            {
                "defender": _FakeDefender(
                    statuses=[scan_module._BackendFailure("defender_unavailable", unavailable=True)]
                )
            },
            "defender_unavailable",
            "unavailable",
        ),
        ({"defender": _FakeDefender(codes=[0, 0, 0, 0, 2])}, "scan_failed", "fail"),
        (
            {"defender": _FakeDefender(statuses=[scan_module._BackendFailure("scan_timeout")])},
            "scan_timeout",
            "fail",
        ),
    ],
)
def test_unsupported_unavailable_stale_disabled_malformed_and_timeout_never_pass(
    tmp_path: Path, kwargs: dict[str, Any], reason: str, status: str
) -> None:
    stage = _stage(tmp_path)
    kwargs = dict(kwargs)
    defender = kwargs.pop("defender", _FakeDefender())
    receipt = _scan(stage, defender, **kwargs)
    assert receipt["status"] == status
    assert reason in receipt["reason_codes"]
    assert receipt["status"] != "pass"


def test_history_detection_and_quarantine_are_not_cleared_by_zero_scan_code(
    tmp_path: Path,
) -> None:
    stage = _stage(tmp_path)
    receipt = _scan(
        stage,
        _FakeDefender(histories=[[], [_history(stage.components["mcp"], action="quarantined")]]),
    )
    assert receipt["status"] == "fail"
    assert receipt["outcome"] == "detection"
    assert "defender_detection_history" in receipt["reason_codes"]
    assert receipt["history"]["quarantine_or_deletion_detection_count"] == 1

    preexisting = _scan(
        stage,
        _FakeDefender(histories=[[_history(stage.components["mcp"])], []]),
    )
    assert preexisting["status"] == "fail"
    assert "history_detected_before_scan" in preexisting["reason_codes"]


def test_malformed_or_unrelated_history_is_not_a_clean_scan(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    malformed = _scan(stage, _FakeDefender(histories=[[], [{"DetectionID": "bad"}]]))
    assert malformed["status"] == "fail"
    assert "history_malformed" in malformed["reason_codes"]

    unrelated = _scan(
        stage,
        _FakeDefender(
            histories=[[], [_history(stage.root / "unrelated.exe", detection_id="other")]]
        ),
    )
    assert unrelated["status"] == "fail"
    assert "history_changed_during_scan" in unrelated["reason_codes"]


def test_post_scan_reparse_state_is_explicitly_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stage = _stage(tmp_path)
    monkeypatch.setattr(
        scan_module, "_measure_post_scan", lambda _targets: ("reparse", False, False)
    )
    receipt = _scan(stage, _FakeDefender())
    assert receipt["status"] == "fail"
    assert receipt["post_scan"]["presence"] == "reparse"
    assert "target_reparse_after_scan" in receipt["reason_codes"]


def test_status_change_after_scan_and_stale_receipts_never_pass(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    changed = _scan(
        stage,
        _FakeDefender(statuses=[_status(), _status(updated=NOW - timedelta(days=8))]),
    )
    assert changed["status"] == "fail"
    assert "signature_stale" in changed["reason_codes"]

    receipt = _scan(stage, _FakeDefender())
    receipt_path = tmp_path / scan_module.RECEIPT_FILE_NAME
    scan_module.write_receipt(receipt_path, receipt)
    with pytest.raises(scan_module.WindowsDefenderScanError, match="stale"):
        scan_module.verify_receipt(
            receipt_path=receipt_path,
            package_path=stage.package,
            component_paths=stage.components,
            source_root=stage.root,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
            defender=_FakeDefender(statuses=[_status(updated=NOW + timedelta(days=2))]),
            now=lambda: NOW + timedelta(days=2),
        )


@pytest.mark.parametrize("effect", ["delete", "mutate"])
def test_post_scan_deletion_or_rehash_mismatch_never_passes(tmp_path: Path, effect: str) -> None:
    stage = _stage(tmp_path)

    def change(path: Path) -> None:
        if path == stage.components["main"]:
            if effect == "delete":
                path.unlink()
            else:
                path.write_bytes(path.read_bytes() + b"mutation")

    receipt = _scan(stage, _FakeDefender(scan_effect=change))
    assert receipt["status"] == "fail"
    assert receipt["post_scan"]["presence"] in {"missing", "changed"}
    assert receipt["outcome"] == "detection"
    assert any(
        reason in receipt["reason_codes"]
        for reason in ("target_missing_after_scan", "target_changed_after_scan")
    )


def test_extra_substituted_duplicate_and_reparse_inputs_fail_closed(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    with pytest.raises(scan_module.WindowsDefenderScanError):
        _scan(stage, _FakeDefender(), component_paths={**stage.components, "extra": stage.package})

    wrong = dict(stage.components)
    wrong["mcp"] = stage.components["recovery"]
    with pytest.raises(scan_module.WindowsDefenderScanError):
        _scan(stage, _FakeDefender(), component_paths=wrong)

    duplicate = dict(stage.components)
    duplicate["mcp"] = stage.components["main"]
    with pytest.raises(scan_module.WindowsDefenderScanError):
        _scan(stage, _FakeDefender(), component_paths=duplicate)

    linked = stage.root / "build" / "AllTheContextLinked.exe"
    try:
        linked.symlink_to(stage.components["mcp"])
    except (OSError, NotImplementedError):
        pytest.skip("host cannot create a test reparse/link input")
    linked_components = dict(stage.components)
    linked_components["mcp"] = linked
    with pytest.raises(scan_module.WindowsDefenderScanError):
        _scan(stage, _FakeDefender(), component_paths=linked_components)


def test_receipt_checksum_and_current_rehash_reject_forgery(tmp_path: Path) -> None:
    stage = _stage(tmp_path)
    receipt = _scan(stage, _FakeDefender())
    receipt_path = tmp_path / scan_module.RECEIPT_FILE_NAME
    scan_module.write_receipt(receipt_path, receipt)
    assert scan_module.load_receipt(receipt_path)["status"] == "pass"
    original_raw = receipt_path.read_bytes()
    original_checksum = receipt_path.with_name(scan_module.CHECKSUM_FILE_NAME).read_bytes()

    forged = dict(receipt)
    forged["status"] = "fail"
    forged["outcome"] = "inconclusive"
    forged["reason_codes"] = ["scan_failed"]
    receipt_path.write_bytes(scan_module.canonical_json(forged))
    with pytest.raises(scan_module.WindowsDefenderScanError, match="checksum"):
        scan_module.load_receipt(receipt_path)

    receipt_path.write_bytes(original_raw)
    receipt_path.with_name(scan_module.CHECKSUM_FILE_NAME).write_bytes(original_checksum)
    forged = json.loads(receipt_path.read_text(encoding="utf-8"))
    forged["components"][0]["sha256"] = "b" * 64
    receipt_path.write_bytes(scan_module.canonical_json(forged))
    checksum = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
    receipt_path.with_name(scan_module.CHECKSUM_FILE_NAME).write_text(
        f"{checksum}  {scan_module.RECEIPT_FILE_NAME}\n", encoding="ascii"
    )
    with pytest.raises(scan_module.WindowsDefenderScanError):
        scan_module.verify_receipt(
            receipt_path=receipt_path,
            package_path=stage.package,
            component_paths=stage.components,
            source_root=stage.root,
            version=VERSION,
            source_commit=SOURCE_COMMIT,
            defender=_FakeDefender(),
            now=lambda: NOW,
        )


def test_receipt_rejects_maintainer_boolean_and_private_fields() -> None:
    with pytest.raises(scan_module.WindowsDefenderScanError):
        scan_module.validate_payload({"maintainer_clean": True})
    with pytest.raises(scan_module.WindowsDefenderScanError):
        scan_module._assert_content_free({"notes": "C:\\Users\\Noah\\secret"})


def test_schema_is_strict_and_binds_windows_defender_contract() -> None:
    schema = json.loads(
        (
            Path(__file__).parents[2] / "release" / "windows-defender-scan-receipt.schema.json"
        ).read_text(encoding="utf-8")
    )
    assert schema["additionalProperties"] is False
    assert schema["properties"]["platform"]["const"] == "windows"
    assert schema["properties"]["architecture"]["const"] == "x86_64"
    assert schema["$defs"]["invocation"]["properties"]["scan_type"]["const"] == "custom"
    assert schema["$defs"]["invocation"]["properties"]["settings_changed"]["const"] is False
    assert schema["$defs"]["defenderSnapshot"]["required"][:4] == [
        "engine_version",
        "platform_version",
        "service_version",
        "signature_version",
    ]
    assert schema["$defs"]["postScan"]["properties"]["rehashed"]["type"] == "boolean"


def test_client_uses_supported_custom_scan_without_setting_changes(tmp_path: Path) -> None:
    powershell = tmp_path / "powershell.exe"
    mp_cmd_run = tmp_path / "MpCmdRun.exe"
    target = tmp_path / "AllTheContext.exe"
    powershell.write_bytes(b"powershell")
    mp_cmd_run.write_bytes(b"mpcmdrun")
    target.write_bytes(b"candidate")
    calls: list[list[str]] = []

    def runner(args: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0].casefold().endswith("powershell.exe"):
            if "Get-MpComputerStatus" in args[-1]:
                output = json.dumps(_status())
            else:
                output = json.dumps({"records": []})
            return subprocess.CompletedProcess(args, 0, output, "")
        return subprocess.CompletedProcess(args, 0, "", "")

    client = scan_module.DefenderClient(
        mp_cmd_run=mp_cmd_run,
        powershell=powershell,
        runner=runner,
    )
    assert client.status()["engine_version"] == "1.1.1.1"
    assert client.history() == []
    assert client.custom_scan(target) == 0
    assert calls[-1][1:] == ["-Scan", "-ScanType", "3", "-File", str(target)]
    assert all("Set-MpPreference" not in " ".join(call) for call in calls)
