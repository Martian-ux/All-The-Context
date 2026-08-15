from __future__ import annotations

import json
import plistlib
from pathlib import Path

import pytest

from scripts.macos_acceptance_preflight import (
    HOSTED_CI_PROFILE,
    MIN_ROOT_FREE_BYTES_EXCLUSIVE,
    NATIVE_ACCEPTANCE_PROFILE,
    MacOSHostFacts,
    evaluate_host_facts,
    parse_diskutil_plist,
    write_report,
)


def _facts(**overrides: object) -> MacOSHostFacts:
    values: dict[str, object] = {
        "system": "Darwin",
        "architecture": "arm64",
        "rosetta_translated": False,
        "os_version": "26.0",
        "os_build": "25A123",
        "logical_cpus": 8,
        "memory_bytes": 16 * 1024 * 1024 * 1024,
        "root_free_bytes": 32 * 1024 * 1024 * 1024,
        "root_internal": True,
        "root_solid_state": True,
        "filesystem": "apfs",
        "executing_as_root": False,
        "missing_tools": (),
    }
    values.update(overrides)
    return MacOSHostFacts(**values)  # type: ignore[arg-type]


def _evaluate(
    facts: MacOSHostFacts,
    *,
    profile: str = NATIVE_ACCEPTANCE_PROFILE,
    expected_os_version: str | None = "26.0",
    dedicated_clean_user_attested: bool = True,
) -> dict[str, object]:
    return evaluate_host_facts(
        facts,
        profile=profile,
        expected_architecture="arm64",
        expected_major=26,
        expected_os_version=expected_os_version,
        dedicated_clean_user_attested=dedicated_clean_user_attested,
    )


def test_native_preflight_passes_without_claiming_acceptance() -> None:
    report = _evaluate(_facts())

    assert report["status"] == "pass"
    assert report["native_acceptance_eligible"] is True
    assert report["acceptance_claimed"] is False
    assert report["preparation_only"] is True
    assert report["reason_codes"] == []


def test_hosted_profile_can_pass_without_native_hardware_eligibility() -> None:
    report = _evaluate(
        _facts(
            root_free_bytes=MIN_ROOT_FREE_BYTES_EXCLUSIVE,
            root_internal=False,
            root_solid_state=None,
        ),
        profile=HOSTED_CI_PROFILE,
        expected_os_version=None,
        dedicated_clean_user_attested=False,
    )

    assert report["status"] == "pass"
    assert report["native_acceptance_eligible"] is False
    native_reasons = report["native_acceptance_reason_codes"]
    assert isinstance(native_reasons, list)
    assert "more_than_sixteen_gib_root_free_required" in native_reasons
    assert "internal_root_storage_required" in native_reasons
    assert "dedicated_clean_user_attestation_required" in native_reasons


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"architecture": "x86_64"}, "native_architecture_mismatch"),
        ({"rosetta_translated": True}, "rosetta_translation_rejected"),
        ({"os_version": "25.9"}, "macos_major_version_mismatch"),
        ({"missing_tools": ("security",)}, "native_tools_missing"),
        ({"executing_as_root": True}, "root_execution_rejected"),
    ],
)
def test_native_preflight_fails_closed(overrides: dict[str, object], reason: str) -> None:
    report = _evaluate(_facts(**overrides))

    assert report["status"] == "unavailable"
    assert reason in report["reason_codes"]
    assert report["acceptance_claimed"] is False


def test_native_preflight_requires_strictly_more_than_sixteen_gib_free() -> None:
    report = _evaluate(_facts(root_free_bytes=MIN_ROOT_FREE_BYTES_EXCLUSIVE))

    assert report["status"] == "unavailable"
    assert "more_than_sixteen_gib_root_free_required" in report["reason_codes"]


def test_native_preflight_requires_exact_frozen_os_version_and_clean_user() -> None:
    report = _evaluate(
        _facts(os_version="26.0.1"),
        expected_os_version="26.0",
        dedicated_clean_user_attested=False,
    )

    assert report["status"] == "unavailable"
    assert "exact_macos_version_mismatch" in report["reason_codes"]
    assert "dedicated_clean_user_attestation_required" in report["reason_codes"]


def test_diskutil_parser_projects_only_required_storage_facts() -> None:
    payload = plistlib.dumps(
        {
            "Internal": True,
            "SolidState": True,
            "FilesystemType": "apfs",
            "DeviceIdentifier": "disk3s1s1",
            "VolumeName": "Private machine name",
        }
    ).decode("utf-8")

    assert parse_diskutil_plist(payload) == (True, True, "apfs")
    assert parse_diskutil_plist("not a plist") == (None, None, None)


def test_report_write_is_atomic_and_refuses_replacement(tmp_path: Path) -> None:
    destination = tmp_path / "preflight.json"
    report = _evaluate(_facts())

    write_report(destination, report)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["kind"] == "macos_acceptance_preflight"
    assert payload["content_free"] is True
    with pytest.raises(FileExistsError):
        write_report(destination, report)
