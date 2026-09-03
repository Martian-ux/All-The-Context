from __future__ import annotations

from copy import deepcopy

import pytest
from allthecontext.acceptance_receipt import (
    missing_required_gates,
    recompute_receipt_artifact_bindings,
    validate_receipt,
    validate_receipt_bundle,
)
from allthecontext.release_manifest import ManifestError
from allthecontext.windows_acceptance import WINDOWS_ACCEPTANCE_CHECKS

SOURCE = "a" * 40
CANDIDATE = "b" * 64
DIRECT_NAME = "all-the-context-0.1.0-beta.7-windows-x86_64-unsigned.exe"
ARCHIVE_NAME = "all-the-context-0.1.0-beta.7-windows-x86_64-unsigned.zip"


def _windows_acceptance() -> dict[str, object]:
    return {
        "schema_version": 1,
        "platform": "windows",
        "architecture": "x86_64",
        "source_commit": SOURCE,
        "version": "0.1.0-beta.7",
        "clean_machine": True,
        "developer_tooling": False,
        "prerequisites": {
            "status": "pass",
            "os_family": "windows",
            "architecture": "x86_64",
            "developer_tooling_absent": True,
        },
        "permissions": {
            "status": "pass",
            "scope": "current_user",
            "elevation": "not_required",
        },
        "defender": {
            "status": "pass",
            "artifact_scan": "completed",
            "real_time_protection_enabled": True,
            "target_files_present": True,
            "new_detections": 0,
            "quarantine_events": 0,
            "signature_version": "1.457.1000.0",
        },
        "core": {
            "status": "pass",
            "host": "127.0.0.1",
            "loopback_only": True,
            "public_listener_count": 0,
        },
        "artifacts": {
            "direct_package": {"name": DIRECT_NAME, "sha256": "c" * 64, "size": 100},
            "installed_component_archive": {
                "name": ARCHIVE_NAME,
                "sha256": "d" * 64,
                "size": 200,
            },
            "installed_component_manifest": {
                "name": "installed-component-manifest-v1.json",
                "sha256": "e" * 64,
                "size": 300,
            },
            "installed_component_checksum": {
                "name": "installed-component-manifest-v1.json.sha256",
                "sha256": "f" * 64,
                "size": 80,
            },
            "native_build_provenance": {
                "name": "native-build-provenance-v1.json",
                "sha256": "1" * 64,
                "size": 400,
            },
            "native_build_provenance_checksum": {
                "name": "native-build-provenance-v1.json.sha256",
                "sha256": "2" * 64,
                "size": 80,
            },
        },
        "components": [
            {"role": "main", "filename": "AllTheContext.exe", "sha256": "3" * 64, "size": 100},
            {"role": "mcp", "filename": "AllTheContextMCP.exe", "sha256": "4" * 64, "size": 110},
            {
                "role": "recovery",
                "filename": "AllTheContextRecovery.exe",
                "sha256": "5" * 64,
                "size": 120,
            },
            {
                "role": "updater",
                "filename": "AllTheContextUpdater.exe",
                "sha256": "6" * 64,
                "size": 130,
            },
        ],
        "checks": {key: "pass" for key in WINDOWS_ACCEPTANCE_CHECKS},
        "leftovers": {
            "installed_binaries": 0,
            "shortcuts": 0,
            "scheduled_tasks": 0,
            "runonce_entries": 0,
            "orphaned_client_secrets": 0,
        },
    }


def _receipt(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "schema_version": 1,
        "receipt_id": "windows-clean-beta-p01",
        "gate_id": "BETA-P01",
        "evidence_kind": "exact_downloaded_artifact",
        "status": "pass",
        "source_commit": SOURCE,
        "candidate_sha256": CANDIDATE,
        "content_free": True,
        "limitations": [],
        "attempts": [{"attempt": 1, "status": "pass"}],
        "artifact_digests": {DIRECT_NAME: "c" * 64, ARCHIVE_NAME: "d" * 64},
        "windows_acceptance": _windows_acceptance(),
    }
    body.update(overrides)
    return body


def test_windows_clean_receipt_requires_complete_path_free_evidence() -> None:
    validated = validate_receipt(_receipt())
    assert validated["windows_acceptance"] == _windows_acceptance()
    missing = _receipt()
    del missing["windows_acceptance"]
    with pytest.raises(ManifestError, match="clean-machine evidence"):
        validate_receipt(missing)


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        ("windows_acceptance", "developer_tooling", True, "developer tooling"),
        ("defender", "real_time_protection_enabled", False, "real-time protection"),
        ("core", "host", "0.0.0.0", "loopback"),
        ("checks", "rollback", "not_run", "check rollback"),
        ("leftovers", "runonce_entries", 1, "leftover runonce_entries"),
    ],
)
def test_windows_clean_receipt_fails_closed_for_nonpassing_evidence(
    section: str, key: str, value: object, message: str
) -> None:
    receipt = _receipt()
    acceptance = deepcopy(receipt["windows_acceptance"])
    assert isinstance(acceptance, dict)
    target = acceptance if section == "windows_acceptance" else acceptance[section]
    assert isinstance(target, dict)
    target[key] = value
    receipt["windows_acceptance"] = acceptance
    with pytest.raises(ManifestError, match=message):
        validate_receipt(receipt)


def test_windows_clean_receipt_binds_outer_candidate_assets() -> None:
    receipt = _receipt()
    cast_artifacts = receipt["windows_acceptance"]
    assert isinstance(cast_artifacts, dict)
    artifacts = cast_artifacts["artifacts"]
    assert isinstance(artifacts, dict)
    direct = artifacts["direct_package"]
    assert isinstance(direct, dict)
    direct["sha256"] = "9" * 64
    with pytest.raises(ManifestError, match=r"direct_package.*candidate"):
        validate_receipt(receipt)


def test_windows_clean_receipt_recomputes_with_candidate_inventory() -> None:
    receipt = validate_receipt(_receipt())
    inventory = {DIRECT_NAME: "c" * 64, ARCHIVE_NAME: "d" * 64}
    recompute_receipt_artifact_bindings(
        [receipt], inventory_digests=inventory, candidate_sha256=CANDIDATE
    )
    assert missing_required_gates(
        [receipt], required_gates={"BETA-P01"}, inventory_digests=inventory
    ) == []

    wrong_inventory = dict(inventory)
    wrong_inventory[ARCHIVE_NAME] = "a" * 64
    with pytest.raises(ManifestError, match="does not match candidate inventory"):
        recompute_receipt_artifact_bindings(
            [receipt], inventory_digests=wrong_inventory, candidate_sha256=CANDIDATE
        )


def test_windows_clean_receipt_rejects_maintainer_assertions_inside_evidence() -> None:
    receipt = _receipt()
    acceptance = receipt["windows_acceptance"]
    assert isinstance(acceptance, dict)
    acceptance["maintainer_approved"] = True
    with pytest.raises(ManifestError, match="unknown fields"):
        validate_receipt(receipt)


def test_windows_clean_bundle_rebinds_evidence_to_candidate_version() -> None:
    receipt = validate_receipt(_receipt())
    bundle = {
        "schema_version": 1,
        "source_commit": SOURCE,
        "candidate_sha256": CANDIDATE,
        "version": "0.1.0-beta.6",
        "receipts": [receipt],
        "maintainer_decision": {"decision": None, "independent_human_review_claimed": False},
    }
    with pytest.raises(ManifestError, match="version does not match"):
        validate_receipt_bundle(bundle)
