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
    assert "      approval:" in text
    assert "description: Type BUILD PRIVATE REPLACEMENT CANDIDATE" in text
    assert "    push:" not in text
    assert "    schedule:" not in text
    assert "    runs-on: windows-latest" in text
    assert "REQUESTED_REF: ${{ github.ref }}" in text
    assert "DEFAULT_BRANCH_REF: refs/heads/${{ github.event.repository.default_branch }}" in text
    assert "SOURCE_COMMIT: ${{ inputs.source_commit }}" in text
    assert "DISPATCH_SHA: ${{ github.sha }}" in text
    assert "APPROVAL: ${{ inputs.approval }}" in text
    assert "VERSION: ${{ inputs.version }}" in text
    assert 'BUILD PRIVATE REPLACEMENT CANDIDATE' in text
    assert "0.1.0-beta.7" in text
    assert "source_commit must equal the triggering protected-main SHA" in text

    precheck_end = text.index("      - uses: actions/checkout@")
    precheck = text[:precheck_end]
    assert precheck.index("APPROVAL: ${{ inputs.approval }}") < precheck.index("REQUESTED_REF:")
    assert precheck.index("BUILD PRIVATE REPLACEMENT CANDIDATE") < precheck_end
    assert '$env:APPROVAL -cne "BUILD PRIVATE REPLACEMENT CANDIDATE"' in precheck
    assert '$env:VERSION -cne "0.1.0-beta.7"' in precheck
    assert "REQUESTED_REF -cne $env:DEFAULT_BRANCH_REF" in precheck
    assert "$env:SOURCE_COMMIT -cne $env:DISPATCH_SHA" in precheck
    checkout = text[precheck_end : text.index("      - uses: actions/setup-python@", precheck_end)]
    assert "ref: ${{ github.sha }}" in checkout
    assert "inputs.source_commit" not in checkout


def test_workflow_has_read_only_repository_access_and_one_artifact_upload() -> None:
    text = _read_workflow()

    assert "permissions:\n  actions: read\n  contents: read" in text
    assert "      actions: read\n      contents: read" in text
    assert "artifact-metadata:" not in text
    assert "contents: write" not in text
    assert "id-token:" not in text
    assert "attestations:" not in text
    assert len(re.findall(r"uses:\s+actions/upload-artifact@[0-9a-f]{40}", text)) == 1
    assert "name: replacement-candidate-windows-${{ inputs.version }}-${{ github.sha }}" in text
    upload_start = text.index("      - name: Upload private Windows candidate artifact")
    upload = text[upload_start:]
    assert "dist/replacement-candidate-handoff/**" in upload
    assert "dist/release/**" not in upload
    assert "dist/candidate-evidence/**" not in upload
    assert "retention-days: 7" in text


def test_workflow_uses_pinned_actions_and_existing_static_packaging_provenance() -> None:
    text = _read_workflow()

    action_uses = ACTION_USE.findall(text)
    assert action_uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", action) for action in action_uses)
    assert "python scripts/install_locked_python.py --extra packaging" in text
    gate_start = text.index("Require unused beta.7 release slot before build")
    gate_end = text.index("Verify exact green source-health matrix", gate_start)
    gate = text[gate_start:gate_end]
    assert "shell: pwsh" in gate
    assert "GITHUB_TOKEN: ${{ github.token }}" in gate
    assert "REPOSITORY: ${{ github.repository }}" in gate
    assert "SOURCE_COMMIT: ${{ github.sha }}" in gate
    assert "VERSION: ${{ inputs.version }}" in gate
    assert "python scripts/github_release_gate.py" in gate
    assert '--repository "$env:REPOSITORY"' in gate
    assert '--version "$env:VERSION"' in gate
    assert '--source-commit "$env:SOURCE_COMMIT"' in gate
    assert "--operator-verified-immutability" in gate
    assert '$LASTEXITCODE -ne 0' in gate
    assert "GitHub release gate failed closed" in gate
    assert "python scripts/exact_source_gate.py hosted-matrix" in text
    assert '--repository "$env:REPOSITORY"' in text
    assert '--source-commit "$env:SOURCE_COMMIT"' in text
    assert "--output dist/source-evidence/matrix-evidence.json" in text
    assert text.index("exact_source_gate.py hosted-matrix") < text.index(
        "python scripts/build_desktop.py"
    )
    assert gate_start < text.index("python scripts/build_desktop.py")
    assert text.index("python scripts/github_release_gate.py") < text.index(
        "python scripts/build_desktop.py"
    )
    assert "python scripts/build_desktop.py" in text
    assert "python scripts/package_desktop.py" in text
    assert "--installed-component-output-dir dist/installed-component-package" in text
    assert "python scripts/build_release_assets.py" in text
    assert "--source dist/installed-component-package" in text
    assert "python scripts/installed_component_manifest.py verify-archive" in text
    assert "python scripts/repository_security_scan.py" in text
    assert "--scope artifacts" in text
    assert "dist/replacement-candidate-handoff" in text
    assert "--report dist/replacement-candidate-handoff/content-hygiene-scan-report.json" in text
    assert "not malware or Defender scanning" in text


def test_workflow_stages_exact_allowlisted_handoff_files_without_wildcard_copy() -> None:
    text = _read_workflow()

    stage_start = text.index("Stage exact replacement-candidate handoff")
    scan_start = text.index("Run content-hygiene scan")
    stage = text[stage_start:scan_start]
    assert '"dist/replacement-candidate-handoff"' in stage
    for expected in (
        '"release/$archiveName" = "dist/release/$archiveName"',
        '"release/$archiveName.sha256" = "dist/release/$archiveName.sha256"',
        '"release/$archiveName.spdx.json" = "dist/release/$archiveName.spdx.json"',
        '"release/$directName" = "dist/release/$directName"',
        '"release/$directName.sha256" = "dist/release/$directName.sha256"',
        '"release/$directName.spdx.json" = "dist/release/$directName.spdx.json"',
        '"release/all-the-context-$env:VERSION-windows-x86_64-unsigned.package.json" =',
        '"release/all-the-context-$env:VERSION-windows-x86_64-unsigned.IMPORTANT-UNSIGNED.txt" =',
        '"components/AllTheContextSetup.exe" = "dist/desktop/AllTheContextSetup.exe"',
        '"components/AllTheContextMCP.exe" = "build/desktop/helper-dist/AllTheContextMCP.exe"',
        '"components/AllTheContextRecovery.exe" = "dist/desktop/AllTheContextRecovery.exe"',
        '"components/AllTheContextUpdater.exe"',
        '"components/installed-component-manifest-v1.json"',
        '"components/installed-component-manifest-v1.json.sha256"',
        '"source/matrix-evidence.json" = "dist/source-evidence/matrix-evidence.json"',
    ):
        assert expected in stage
    assert "Copy-Item -LiteralPath $source -Destination $destination" in stage
    assert "source and handoff digest/size differ" in stage
    assert "handoff-inventory-v1.json" in stage
    assert "handoff-inventory-v1.json.sha256" in stage
    assert "refusing to replace an existing handoff inventory" in stage
    assert "artifact_name = $artifactName" in stage
    assert "run_attempt = [int]$env:GITHUB_RUN_ATTEMPT" in stage
    assert "run_id = [int64]$env:GITHUB_RUN_ID" in stage
    assert "source_commit = $env:SOURCE_COMMIT" in stage
    assert 'target = "windows:x86_64"' in stage
    assert "version = $env:VERSION" in stage
    assert "Copy-Item -Path" not in stage
    assert "dist/release/*" not in stage
    assert stage_start < scan_start < text.index("Rehash every allowlisted handoff file")


def test_workflow_rehashes_full_handoff_and_binds_inventory_to_run_attempt() -> None:
    text = _read_workflow()

    scan_start = text.index("Run content-hygiene scan")
    rehash_start = text.index("Rehash every allowlisted handoff file")
    upload_start = text.index("Upload private Windows candidate artifact")
    rehash = text[rehash_start:upload_start]
    assert "--artifact-dir dist/replacement-candidate-handoff" in text[scan_start:rehash_start]
    assert "--report dist/replacement-candidate-handoff/content-hygiene-scan-report.json" in text[
        scan_start:rehash_start
    ]
    assert "GITHUB_RUN_ID" in rehash
    assert "GITHUB_RUN_ATTEMPT" in rehash
    assert "artifact_name" in text
    assert "handoff inventory identity does not match this workflow run" in rehash
    assert "foreach ($relative in $finalFiles)" in rehash
    assert "Get-FileHash -LiteralPath $Path -Algorithm SHA256" in rehash
    assert "actualFiles" in rehash
    assert "TODO(lead composition): invoke the independent verifier" in text


def test_workflow_never_invokes_release_publication_or_produced_binary_smokes() -> None:
    text = _read_workflow().casefold()

    assert "gh release" not in text
    assert "gh api" not in text
    assert "actions/attest@" not in text
    assert "actions/download-artifact@" not in text
    assert "scripts/smoke_" not in text
    assert "scripts/smoke-" not in text
