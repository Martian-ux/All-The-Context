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
    assert "artifact ZIP serialization is transport packaging" in normalized
    assert "workflow_run_id" in normalized
    assert "workflow_artifact_name" in normalized
    assert "candidate inventory" in normalized
    assert "candidate_sha256" not in text
    assert "Microsoft reassessment is mandatory" in normalized
    assert "Clean local scans do not bypass either Microsoft stage" in normalized
    assert "there is no `submission_required=false`/`result=not_required` path" in normalized
    assert "authenticated Microsoft portal/account consent" in normalized
    assert "Those actions are not performed by repository tooling" in normalized
    assert "opaque submission ID" in normalized
    assert "result-artifact digest" in normalized
    assert "receipt, acknowledgement" in normalized
    assert "`Closed` status alone is not a final determination" in normalized
    assert "live-client or execution credit" in normalized

    stage_ids = (
        "private-build",
        "independent-verification",
        "exact-component-scan",
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
    submission = stages[3]
    assert "submission_required" not in submission
    assert submission["submission_id_present"] is False
    assert submission["submission_status"] == "unknown"
    assert submission["submitted_component_sha256"] is None
    result = stages[4]
    assert result["result"] == "unknown"
    assert result["result_component_sha256"] is None
    assert result["result_artifact_sha256"] is None
    assert stages[-1]["decision_time_present"] is False
    assert value["authorization"] == {
        "execution_authorized": False,
        "authorization_id_present": False,
        "decision_time": None,
        "content_free": True,
    }


def test_replacement_candidate_runbook_references_existing_contracts() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")

    for relative in (
        "../../DECISIONS.md",
        "../../../release/installed-component-manifest.schema.json",
        "../../../scripts/package_desktop.py",
        "../../../scripts/build_release_assets.py",
        "../../../scripts/installed_component_manifest.py",
        "../../../.github/workflows/replacement-candidate.yml",
    ):
        assert relative in text
    assert "../../../.github/workflows/release-candidate.yml" not in text
