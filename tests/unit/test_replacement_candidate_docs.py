from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "security" / "replacement-candidate" / "OPERATOR_EVIDENCE_LEDGER.md"
TEMPLATE = (
    ROOT / "docs" / "security" / "replacement-candidate" / "operator-evidence-ledger.template.json"
)


def test_replacement_candidate_runbook_is_noncanonical_and_ordered() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    normalized = " ".join(text.split())

    assert "Draft, noncanonical operator procedure" in normalized
    assert "beta.6 incident stays" in normalized
    assert "unresolved." in normalized
    assert "Candidate-owned evidence" in normalized
    assert "caller-authored JSON" in normalized
    assert "source Core" in normalized
    assert "Do not execute an installed beta.6 helper or a replacement candidate" in normalized
    assert "do not restore, execute, or allow-list it" in normalized
    assert "Windows x86-64 only and artifact-only" in normalized
    assert "cannot publish" in normalized
    assert "does not emit attestations" in normalized
    assert "create a draft release" in normalized
    assert "explicitly out of scope" in normalized
    assert "dist/replacement-candidate-handoff/**" in normalized
    assert "dist/release/**" not in normalized
    assert "dist/candidate-evidence/**" not in normalized
    for handoff_entry in (
        "release/all-the-context-<VERSION>-windows-x86_64.zip",
        "release/all-the-context-<VERSION>-windows-x86_64.zip.sha256",
        "release/all-the-context-<VERSION>-windows-x86_64.zip.spdx.json",
        "release/all-the-context-<VERSION>-windows-x86_64-unsigned.exe",
        "release/all-the-context-<VERSION>-windows-x86_64-unsigned.exe.sha256",
        "release/all-the-context-<VERSION>-windows-x86_64-unsigned.exe.spdx.json",
        "release/all-the-context-<VERSION>-windows-x86_64-unsigned.package.json",
        "release/all-the-context-<VERSION>-windows-x86_64-unsigned.IMPORTANT-UNSIGNED.txt",
        "components/AllTheContextSetup.exe",
        "components/AllTheContextMCP.exe",
        "components/AllTheContextRecovery.exe",
        "components/AllTheContextUpdater.exe",
        "components/installed-component-manifest-v1.json",
        "components/installed-component-manifest-v1.json.sha256",
        "source/matrix-evidence.json",
        "handoff-inventory-v1.json",
        "handoff-inventory-v1.json.sha256",
        "content-hygiene-scan-report.json",
    ):
        assert handoff_entry in text
    assert "four `components/*.exe` entries are the raw component bytes" in normalized
    assert "content-hygiene report is for secret/path hygiene only" in normalized
    assert "provides no malware, Defender, or Microsoft credit" in normalized
    assert "artifact ZIP serialization is transport packaging" in normalized
    assert "workflow_run_id" in normalized
    assert "workflow_artifact_name" in normalized
    assert "candidate inventory" in normalized
    assert "candidate_sha256" not in text
    assert "Microsoft reassessment is mandatory" in normalized
    assert "Clean local scans do not bypass either Microsoft stage" in normalized
    assert "there is no `submission_required=false`/`result=not_required` path" in normalized
    assert "authenticated Microsoft portal/account consent" in normalized
    assert "Repository tooling does not consent, authenticate, upload" in normalized
    assert "opaque submission ID" in normalized
    assert "result-artifact digest" in normalized
    assert "receipt, acknowledgement" in normalized
    assert "`Closed` status alone is not a final determination" in normalized
    assert "live-client or execution credit" in normalized
    assert "bounded outcome is `clean` or `detection`" in normalized
    assert "routing the exact detected component to submission preparation" in normalized
    assert "submission-preparation" in normalized
    assert "external-submission-authorization" in normalized
    assert "require Noah's explicit approval" in normalized
    assert "Repository tooling does not consent" in normalized
    assert "sanitized payload inventory" in normalized
    assert "workflow run attempt" in normalized
    assert "artifact ID/digest/name" in normalized
    assert "scanner/tool/run identity" in normalized
    assert "not a general ledger subsystem" in normalized
    assert (
        "Optional allow-listing and any execution remain separate Noah-authorized actions"
        in normalized
    )
    assert "No allow-listing is implied" in normalized

    stage_ids = (
        "private-build",
        "independent-verification",
        "exact-component-scan",
        "submission-preparation",
        "external-submission-authorization",
        "microsoft-submission",
        "microsoft-result",
        "execution-authorization",
    )
    positions = [text.index(f"`{stage_id}`") for stage_id in stage_ids]
    assert positions == sorted(positions)
    assert "`pass`:" in normalized
    assert "`HOLD`:" in normalized
    assert "`fail`:" in normalized
    assert "`pass` only when every stage is `pass`" in normalized


def test_replacement_candidate_ledger_template_is_content_free_and_hold() -> None:
    template_text = TEMPLATE.read_text(encoding="utf-8")
    value = json.loads(template_text)

    assert "candidate_sha256" not in template_text
    assert value["schema_version"] == 1
    assert value["ledger_type"] == "replacement-candidate-operator-evidence"
    assert value["content_free"] is True
    assert value["overall_status"] == "HOLD"
    candidate = value["candidate"]
    assert candidate["platform"] == "windows"
    assert candidate["architecture"] == "x86_64"
    assert all(
        candidate[field] is None
        for field in (
            "workflow_run_id",
            "workflow_artifact_name",
            "version",
            "source_commit",
            "windows_archive_sha256",
            "direct_installer_sha256",
            "component_manifest_sha256",
        )
    )

    assert [item["role"] for item in value["components"]] == [
        "main",
        "mcp",
        "recovery",
        "updater",
    ]
    assert all(item["sha256"] is None and item["size"] is None for item in value["components"])
    assert all(item["authenticode"] == "unknown" for item in value["components"])

    stages = value["stages"]
    assert [item["stage_id"] for item in stages] == [
        "private-build",
        "independent-verification",
        "exact-component-scan",
        "submission-preparation",
        "external-submission-authorization",
        "microsoft-submission",
        "microsoft-result",
        "execution-authorization",
    ]
    assert all(item["status"] == "HOLD" for item in stages)
    assert all(item["content_free"] is True for item in stages)
    assert all(item["closed_reason"] == "template_not_run" for item in stages)
    independent = stages[1]
    assert independent["checks"]["workflow_artifact_and_run_identity_verified"] is False
    assert "provenance_or_attestation_verified" not in independent["checks"]
    scan = stages[2]
    assert scan["checks"]["all_scan_inputs_rechecked"] is False
    assert scan["checks"]["scanning_completed"] is False
    assert scan["checks"]["outcome"] == "unknown"
    assert scan["checks"]["detection_component_role"] is None
    assert scan["checks"]["detection_component_sha256"] is None
    preparation = stages[3]
    assert preparation["component_role"] is None
    assert preparation["component_sha256"] is None
    assert preparation["sanitized_payload_inventory_present"] is False
    assert preparation["sanitized_payload_inventory_sha256"] is None
    assert preparation["isolated_custody_confirmed"] is False
    assert preparation["preparation_receipt_id_present"] is False
    assert preparation["preparation_time"] is None
    external_authorization = stages[4]
    assert external_authorization["component_role"] is None
    assert external_authorization["component_sha256"] is None
    assert external_authorization["sanitized_payload_inventory_sha256"] is None
    assert external_authorization["authorization_id_present"] is False
    assert external_authorization["authorization_time"] is None
    assert external_authorization["portal_consent_authorized"] is False
    assert external_authorization["external_upload_authorized"] is False
    submission = stages[5]
    assert "submission_required" not in submission
    assert submission["component_role"] is None
    assert submission["submission_id_present"] is False
    assert submission["submission_status"] == "unknown"
    assert submission["submitted_component_sha256"] is None
    result = stages[6]
    assert result["result"] == "unknown"
    assert result["component_role"] is None
    assert result["result_component_sha256"] is None
    assert result["result_artifact_sha256"] is None
    custody = value["custody_receipt"]
    assert custody == {
        "content_free": True,
        "isolated_custody_confirmed": False,
        "download_receipt_present": False,
        "workflow_run_attempt": None,
        "workflow_artifact_id": None,
        "workflow_artifact_digest": None,
        "workflow_artifact_name": None,
        "archive_size": None,
        "installer_size": None,
        "scanner_tool_identity": None,
        "scanner_run_identity": None,
    }
    assert stages[-1]["decision_time_present"] is False
    assert value["authorization"] == {
        "execution_authorized": False,
        "authorization_id_present": False,
        "decision_time": None,
        "content_free": True,
    }


def test_replacement_candidate_runbook_references_existing_contracts() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    relatives = (
        "../../DECISIONS.md",
        "../../../release/installed-component-manifest.schema.json",
        "../../../scripts/package_desktop.py",
        "../../../scripts/build_release_assets.py",
        "../../../scripts/installed_component_manifest.py",
    )
    for relative in relatives:
        assert relative in text
        assert (RUNBOOK.parent / relative).resolve().is_file()
    workflow_relative = "../../../.github/workflows/replacement-candidate.yml"
    assert workflow_relative in text
    workflow_target = (RUNBOOK.parent / workflow_relative).resolve()
    if workflow_target.exists():
        assert workflow_target.is_file()
    assert "../../../.github/workflows/release-candidate.yml" not in text
