"""Contract tests for the private Windows replacement-candidate workflow."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "replacement-candidate.yml"
ACTION_USE = re.compile(r"^\s+uses:\s+([^\s]+)", re.MULTILINE)


def _read_workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_is_manual_windows_only_and_sha_bound_before_checkout() -> None:
    text = _read_workflow()

    assert "workflow_dispatch:" in text
    assert "    inputs:\n      version:" in text
    assert "      source_commit:" in text
    assert "    push:" not in text
    assert "    schedule:" not in text
    assert "    runs-on: windows-latest" in text
    assert "REQUESTED_REF: ${{ github.ref }}" in text
    assert "DEFAULT_BRANCH_REF: refs/heads/${{ github.event.repository.default_branch }}" in text
    assert "SOURCE_COMMIT: ${{ inputs.source_commit }}" in text
    assert "DISPATCH_SHA: ${{ github.sha }}" in text
    assert "source_commit must equal the triggering protected-main SHA" in text

    precheck_end = text.index("      - uses: actions/checkout@")
    precheck = text[:precheck_end]
    assert "REQUESTED_REF -cne $env:DEFAULT_BRANCH_REF" in precheck
    assert "$env:SOURCE_COMMIT -cne $env:DISPATCH_SHA" in precheck
    checkout = text[precheck_end : text.index("      - uses: actions/setup-python@", precheck_end)]
    assert "ref: ${{ github.sha }}" in checkout
    assert "inputs.source_commit" not in checkout


def test_workflow_has_read_only_repository_access_and_one_artifact_upload() -> None:
    text = _read_workflow()

    assert "permissions:\n  contents: read" in text
    assert "      artifact-metadata: write\n      contents: read" in text
    assert "contents: write" not in text
    assert "id-token:" not in text
    assert "attestations:" not in text
    assert len(re.findall(r"uses:\s+actions/upload-artifact@[0-9a-f]{40}", text)) == 1
    assert "name: replacement-candidate-windows-${{ inputs.version }}-${{ github.sha }}" in text
    assert "dist/release/**" in text
    assert "retention-days: 7" in text


def test_workflow_uses_pinned_actions_and_existing_static_packaging_provenance() -> None:
    text = _read_workflow()

    action_uses = ACTION_USE.findall(text)
    assert action_uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in action_uses)
    assert "python scripts/install_locked_python.py --extra packaging" in text
    assert "python scripts/build_desktop.py" in text
    assert "python scripts/package_desktop.py" in text
    assert "--installed-component-output-dir dist/installed-component-package" in text
    assert "python scripts/build_release_assets.py" in text
    assert "--source dist/installed-component-package" in text
    assert "python scripts/installed_component_manifest.py verify-archive" in text
    assert "python scripts/repository_security_scan.py" in text
    assert "--scope artifacts" in text


def test_workflow_never_invokes_release_publication_or_produced_binary_smokes() -> None:
    text = _read_workflow().casefold()

    assert "gh release" not in text
    assert "gh api" not in text
    assert "actions/attest@" not in text
    assert "actions/download-artifact@" not in text
    assert "scripts/smoke_" not in text
    assert "scripts/smoke-" not in text
