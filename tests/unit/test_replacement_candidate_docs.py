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
    value = json.loads(TEMPLATE.read_text(encoding="utf-8"))

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
            "version",
            "source_commit",
            "candidate_sha256",
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
    assert value["authorization"] == {
        "execution_authorized": False,
        "authorization_id_present": False,
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
        "../../../.github/workflows/release-candidate.yml",
    ):
        assert relative in text
