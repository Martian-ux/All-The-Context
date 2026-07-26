"""Contract tests for release workflows, locked install, and publication allowlists."""

from __future__ import annotations

import re
from pathlib import Path

from allthecontext.acceptance_receipt import REQUIRED_PUBLICATION_GATES
from allthecontext.release_candidate import (
    ACCEPTANCE_RECEIPT_BUNDLE_FILE_NAME,
    COMPONENT_INVENTORY_FILE_NAME,
    DECISION_ASSET_NAMES,
    MATRIX_EVIDENCE_FILE_NAME,
    NOTICES_FILE_NAME,
    PUBLICATION_GATE_RECORD_FILE_NAME,
)

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_release_candidate_validate_job_grants_actions_read() -> None:
    text = _read(WORKFLOWS / "release-candidate.yml")
    # Job-level permissions for the hosted-matrix Actions API query.
    assert re.search(
        r"validate:\s*\n(?:.*\n)*?\s+permissions:\s*\n(?:.*\n)*?\s+actions:\s*read",
        text,
    )
    assert "exact_source_gate.py hosted-matrix" in text
    assert "GITHUB_TOKEN: ${{ github.token }}" in text
    # Hosted-matrix step lives under the validate job that has actions:read.
    validate_block = text.split("native:")[0]
    assert "actions: read" in validate_block
    assert "hosted-matrix" in validate_block


def test_workflows_pin_uv_and_do_not_bootstrap_unversioned_tools() -> None:
    install_script = _read(ROOT / "scripts" / "install_locked_python.py")
    audit_script = _read(ROOT / "scripts" / "dependency_audit.py")
    assert 'PINNED_UV_VERSION = "0.11.32"' in install_script
    assert "pip install" not in install_script or "--require-hashes" in install_script
    assert 'f"uv=={PINNED_UV_VERSION}"' not in install_script
    assert "pip\", \"install\", \"--upgrade\"" not in install_script
    assert "pinned uv==" in install_script
    assert "--no-hashes" not in install_script
    assert "--require-hashes" in install_script
    assert "--no-deps" in install_script
    assert "--no-build-isolation" in install_script
    assert "BUILD_BACKEND_PACKAGES = (\"setuptools\", \"wheel\")" in install_script
    assert "missing hashed build backends" in install_script
    assert "pip-audit>=" not in audit_script
    assert "pip install" not in audit_script
    assert "importlib.metadata.version" in audit_script
    assert "--disable-pip" in audit_script
    assert "uv export" in audit_script or '"export"' in audit_script
    assert "pip-audit==2.10.1" in _read(ROOT / "pyproject.toml")
    pyproject = _read(ROOT / "pyproject.toml")
    assert "setuptools>=75" in pyproject
    assert '"wheel"' in pyproject

    for name in (
        "ci.yml",
        "release-candidate.yml",
        "publish-beta-release.yml",
    ):
        text = _read(WORKFLOWS / name)
        assert "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b" in text
        assert 'version: "0.11.32"' in text
        assert "pip install uv" not in text
        assert "pip-audit>=" not in text


def test_required_publication_gates_appear_in_templates_not_r05() -> None:
    import json

    template_path = ROOT / "release" / "acceptance-receipt-bundle.template.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    gate_ids = {item["gate_id"] for item in template["receipts"]}
    assert gate_ids == REQUIRED_PUBLICATION_GATES
    assert "BETA-R05" not in gate_ids
    assert all(item["status"] == "not_run" for item in template["receipts"])
    assert all(item["status"] != "pass" for item in template["receipts"])
    assert template["maintainer_decision"]["independent_human_review_claimed"] is False


def test_publish_workflow_persists_decision_artifacts_before_final_recheck() -> None:
    text = _read(WORKFLOWS / "publish-beta-release.yml")
    assert "--asset-stage signed" in text
    assert "--asset-stage promotion" in text
    assert ACCEPTANCE_RECEIPT_BUNDLE_FILE_NAME in text
    assert PUBLICATION_GATE_RECORD_FILE_NAME in text
    assert "gh release upload" in text
    assert "decision_attest" in text or "Attest acceptance" in text
    # Upload happens before the pre-publish recheck.
    upload_at = text.index("gh release upload")
    recheck_at = text.index("Recheck the exact promotion asset set")
    assert upload_at < recheck_at


def test_release_candidate_binds_source_evidence_into_inventory() -> None:
    text = _read(WORKFLOWS / "release-candidate.yml")
    assert "--source-evidence-dir dist/source-evidence" in text
    assert MATRIX_EVIDENCE_FILE_NAME in text
    assert COMPONENT_INVENTORY_FILE_NAME in text
    assert NOTICES_FILE_NAME in text
    module = _read(
        ROOT / "packages" / "allthecontext" / "src" / "allthecontext" / "release_candidate.py"
    )
    assert "source_evidence" in module
    assert "DECISION_ASSET_NAMES" in module
    for name in DECISION_ASSET_NAMES:
        assert name in module


def test_component_inventory_scope_and_no_invented_license_text() -> None:
    from allthecontext.component_inventory import build_component_inventory

    inventory = build_component_inventory(
        ROOT,
        source_commit="c" * 40,
        version="0.1.0-beta.1",
    )
    scopes = {item["scope"] for item in inventory["components"]}
    assert scopes <= {"runtime", "build", "dev"}
    assert "runtime" in scopes
    assert "dev" in scopes
    python = [item for item in inventory["components"] if item["ecosystem"] == "python"]
    project = next(item for item in python if item["name"] == "all-the-context")
    assert project["license"] == "MIT"
    third_party = [item for item in python if item["name"] != "all-the-context"]
    assert all(item["license"] == "NOASSERTION" for item in third_party)
    assert any(item["name"] == "pip-audit" and item["scope"] == "dev" for item in python)
