"""Immutable contract tests for the private Windows replacement workflow."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "replacement-candidate.yml"

# This digest is deliberately owned by the reviewed test code, not by the
# workflow. It covers the complete file as UTF-8 bytes, including comments,
# whitespace, scalar syntax, and Unicode. No YAML parser is an authority here.
EXPECTED_WORKFLOW_SHA256 = "fb74d52e116cb4bf79a63b798de13e5db1d09ec05fd9f92e9a4946df5817ce11"

INDEPENDENT_VERIFIER_SCRIPT = "verify_installed_component_manifest_independent.py"
INDEPENDENT_VERIFIER_NAME = "Independently verify exact Windows candidate archive and manifest"
STAGE_NAME = "Stage exact replacement-candidate handoff without executing binaries"
UPLOAD_NAME = "Upload private Windows candidate artifact"

EXPECTED_STEP_HEADERS = [
    "name: Require deliberate private replacement phrase",
    "name: Bind source to protected-main dispatch SHA",
    "uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
    "uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
    "uses: astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b",
    "name: Install packaging dependencies from the reviewed lock",
    "name: Require unused beta.7 release slot before build",
    "name: Verify exact green source-health matrix on this SHA",
    "name: Validate exact source metadata and Windows runner",
    "name: Validate native Windows x86-64 runner",
    "name: Build native desktop bytes without executing them",
    "name: Build direct unsigned package and installed-component provenance",
    "name: Build deterministic Windows archive and metadata",
    "name: Build direct package SPDX subject metadata",
    "name: Verify installed-component manifest and archive statically",
    f"name: {INDEPENDENT_VERIFIER_NAME}",
    f"name: {STAGE_NAME}",
    "name: Run content-hygiene scan (not malware or Defender scanning)",
    "name: Rehash every allowlisted handoff file after content-hygiene scan",
    f"name: {UPLOAD_NAME}",
]

EXPECTED_ACTION_REFS = [
    "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065",
    "astral-sh/setup-uv@08807647e7069bb48b6ef5acd8ec9567f424441b",
    "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02",
]

EXPECTED_PRODUCER_NAMES = [
    "Build native desktop bytes without executing them",
    "Build direct unsigned package and installed-component provenance",
    "Build deterministic Windows archive and metadata",
    "Build direct package SPDX subject metadata",
    "Verify installed-component manifest and archive statically",
]

EXPECTED_HANDOFF_MAPPINGS = [
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
    '"components/AllTheContextUpdater.exe" =',
    '"components/installed-component-manifest-v1.json" =',
    '"components/installed-component-manifest-v1.json.sha256" =',
    '"source/matrix-evidence.json" = "dist/source-evidence/matrix-evidence.json"',
]

CANONICAL_INDEPENDENT_VERIFIER_BODY = (
    '$ErrorActionPreference = "Stop"\n'
    "python scripts/verify_installed_component_manifest_independent.py verify-archive `\n"
    '  --archive "dist/release/all-the-context-$env:VERSION-windows-x86_64.zip" `\n'
    '  --direct-package "dist/release/all-the-context-$env:VERSION-windows-x86_64-unsigned.exe" `\n'
    "  --main dist/desktop/AllTheContextSetup.exe `\n"
    "  --mcp build/desktop/helper-dist/AllTheContextMCP.exe `\n"
    "  --recovery dist/desktop/AllTheContextRecovery.exe `\n"
    "  --updater build/desktop/update-helper-dist/AllTheContextUpdater.exe `\n"
    "  --source-root . `\n"
    '  --version "$env:VERSION" `\n'
    '  --source-commit "$env:SOURCE_COMMIT" `\n'
    "  --platform windows `\n"
    "  --architecture x86_64\n"
    "if ($LASTEXITCODE -ne 0) {\n"
    '  throw "Independent installed-component verification failed closed"\n'
    "}\n"
    "exit $LASTEXITCODE\n"
)


def _read_workflow() -> str:
    """Read the workflow without newline translation or Unicode normalization."""

    return WORKFLOW.read_bytes().decode("utf-8")


def _workflow_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _assert_workflow_digest(text: str) -> None:
    assert _workflow_sha256(text) == EXPECTED_WORKFLOW_SHA256, (
        "replacement workflow is not the reviewed UTF-8 byte-for-byte contract"
    )


def _step_blocks(text: str) -> list[str]:
    pattern = re.compile(
        r"^      - (?:name: [^\n]+|uses: [^\n]+)\n(?:.*?)(?=^      - |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    return pattern.findall(text)


def _step_block(text: str, name: str) -> str:
    marker = f"      - name: {name}\n"
    start = text.index(marker)
    following = text.find("\n      - ", start + len(marker))
    end = len(text) if following == -1 else following + 1
    return text[start:end]


def _step_headers(text: str) -> list[str]:
    return [block.split("\n", 1)[0][8:] for block in _step_blocks(text)]


def _assert_order(text: str, *values: str) -> None:
    positions = [text.index(value) for value in values]
    assert positions == sorted(positions), f"workflow ordering is not exact: {values!r}"


def _assert_workflow_semantics(text: str) -> None:
    """Readable assertions for the exact reviewed workflow semantics."""

    assert _step_headers(text) == EXPECTED_STEP_HEADERS
    action_refs = re.findall(r"^(?:      - uses:|        uses:) ([^\n]+)$", text, re.MULTILINE)
    assert action_refs == EXPECTED_ACTION_REFS
    assert all(re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action) for action in EXPECTED_ACTION_REFS)

    assert text.startswith("name: Private Windows replacement candidate\n\non:\n")
    assert "  workflow_dispatch:\n    inputs:\n" in text
    assert "      version:\n        description: Exact 0.1.0-beta.7 version" in text
    assert "      source_commit:\n        description: Full 40-character SHA" in text
    assert "      approval:\n        description: Type BUILD PRIVATE REPLACEMENT CANDIDATE" in text
    assert "        required: true\n        type: string\n" in text
    assert "    push:\n" not in text
    assert "    pull_request:\n" not in text
    assert "    schedule:\n" not in text
    assert "    runs-on: windows-latest\n" in text
    assert "    timeout-minutes: 35\n" in text

    assert "permissions:\n  actions: read\n  contents: read\n" in text
    assert "    permissions:\n      actions: read\n      contents: read\n" in text
    assert "contents: write" not in text
    assert "id-token:" not in text
    assert "attestations:" not in text
    assert re.search(r"^\s*[\"']?if[\"']?\s*:", text, re.MULTILINE) is None
    assert re.search(r"^\s*[\"']?continue-on-error[\"']?\s*:", text, re.MULTILINE) is None

    approval = _step_block(text, "Require deliberate private replacement phrase")
    assert "        shell: pwsh\n" in approval
    assert "          APPROVAL: ${{ inputs.approval }}\n" in approval
    assert "          VERSION: ${{ inputs.version }}\n" in approval
    assert '$env:APPROVAL -cne "BUILD PRIVATE REPLACEMENT CANDIDATE"' in approval
    assert '$env:VERSION -cne "0.1.0-beta.7"' in approval

    source_binding = _step_block(text, "Bind source to protected-main dispatch SHA")
    assert "          REQUESTED_REF: ${{ github.ref }}\n" in source_binding
    default_branch_line = (
        "          DEFAULT_BRANCH_REF: refs/heads/${{ github.event.repository.default_branch }}\n"
    )
    assert default_branch_line in source_binding
    assert "          SOURCE_COMMIT: ${{ inputs.source_commit }}\n" in source_binding
    assert "          DISPATCH_SHA: ${{ github.sha }}\n" in source_binding
    assert "REQUESTED_REF -cne $env:DEFAULT_BRANCH_REF" in source_binding
    assert "SOURCE_COMMIT -cnotmatch '^[0-9a-f]{40}$'" in source_binding
    assert "DISPATCH_SHA -cnotmatch '^[0-9a-f]{40}$'" in source_binding
    assert "SOURCE_COMMIT -cne $env:DISPATCH_SHA" in source_binding

    checkout_start = text.index("      - uses: actions/checkout@")
    checkout_end = text.index("      - uses: actions/setup-python@", checkout_start)
    checkout = text[checkout_start:checkout_end]
    assert "        ref: ${{ github.sha }}\n" in checkout
    assert "        fetch-depth: 0\n" in checkout
    assert "        persist-credentials: false\n" in checkout
    assert "inputs.source_commit" not in checkout
    _assert_order(
        text,
        "APPROVAL: ${{ inputs.approval }}",
        "REQUESTED_REF:",
        "uses: actions/checkout@",
    )

    release_gate = _step_block(text, "Require unused beta.7 release slot before build")
    assert "        shell: pwsh\n" in release_gate
    assert "          GITHUB_TOKEN: ${{ github.token }}\n" in release_gate
    assert "python scripts/github_release_gate.py" in release_gate
    assert "--operator-verified-immutability" in release_gate
    assert "$LASTEXITCODE -ne 0" in release_gate
    assert "GitHub release gate failed closed" in release_gate

    assert "python scripts/install_locked_python.py --extra packaging" in text
    assert "python scripts/exact_source_gate.py hosted-matrix" in text
    assert "python scripts/release_candidate.py validate-source" in text
    assert "python scripts/release_candidate.py validate-runner" in text
    assert "python scripts/installed_component_manifest.py verify-archive" in text
    assert "python scripts/repository_security_scan.py" in text
    assert "--scope artifacts" in text
    assert "not malware or Defender scanning" in text

    verifier = _step_block(text, INDEPENDENT_VERIFIER_NAME)
    assert "        shell: pwsh\n" in verifier
    assert (
        "        env:\n"
        "          VERSION: ${{ inputs.version }}\n"
        "          SOURCE_COMMIT: ${{ github.sha }}\n"
    ) in verifier
    expected_yaml_body = "".join(
        f"          {line}\n" for line in CANONICAL_INDEPENDENT_VERIFIER_BODY.split("\n")[:-1]
    )
    assert f"        run: |\n{expected_yaml_body}" in verifier
    assert text.count(INDEPENDENT_VERIFIER_SCRIPT) == 1
    assert verifier.count("python scripts/verify_installed_component_manifest_independent.py") == 1

    stage = _step_block(text, STAGE_NAME)
    assert "        shell: pwsh\n" in stage
    assert "          $expectedSources = [ordered]@{\n" in stage
    assert "          foreach ($relative in $expectedSources.Keys) {\n" in stage
    assert "            Copy-Item -LiteralPath $source -Destination $destination\n" in stage
    matrix_mapping = '            "source/matrix-evidence.json" = '
    matrix_mapping += '"dist/source-evidence/matrix-evidence.json"\n'
    assert matrix_mapping in stage
    assert "Copy-Item -Path" not in stage
    assert "dist/release/*" not in stage
    assert all(mapping in stage for mapping in EXPECTED_HANDOFF_MAPPINGS)
    inventory_guard = (
        "          if (\n"
        "            (Test-Path -LiteralPath $inventoryPath) -or\n"
        "            (Test-Path -LiteralPath $inventoryChecksumPath)\n"
        "          ) {\n"
        '            throw "refusing to replace an existing handoff inventory"\n'
        "          }\n"
    )
    assert inventory_guard in stage
    assert "Test-Path -LiteralPath $inventoryPath -or Test-Path -LiteralPath" not in stage

    rehash = _step_block(text, "Rehash every allowlisted handoff file after content-hygiene scan")
    scan = _step_block(text, "Run content-hygiene scan (not malware or Defender scanning)")
    upload = _step_block(text, UPLOAD_NAME)
    assert "--artifact-dir dist/replacement-candidate-handoff" in scan
    assert "--report dist/replacement-candidate-handoff/content-hygiene-scan-report.json" in scan
    assert "foreach ($relative in $finalFiles)" in rehash
    assert "Get-FileHash -LiteralPath $Path -Algorithm SHA256" in rehash
    assert "actualFiles" in rehash
    assert "actions/upload-artifact@" in upload
    assert "          path: |\n            dist/replacement-candidate-handoff/**\n" in upload
    assert "dist/release/**" not in upload
    assert "dist/candidate-evidence/**" not in upload
    assert "          if-no-files-found: error\n" in upload
    assert "          retention-days: 7\n" in upload
    upload_steps = re.findall(
        r"^      - name: Upload private Windows candidate artifact$", text, re.MULTILINE
    )
    assert len(upload_steps) == 1

    for producer in EXPECTED_PRODUCER_NAMES:
        assert text.index(f"      - name: {producer}\n") < text.index(
            f"      - name: {INDEPENDENT_VERIFIER_NAME}\n"
        )
    _assert_order(
        text,
        f"      - name: {INDEPENDENT_VERIFIER_NAME}\n",
        f"      - name: {STAGE_NAME}\n",
        "Run content-hygiene scan",
        "Rehash every allowlisted handoff file",
        f"      - name: {UPLOAD_NAME}\n",
    )

    lowered = text.casefold()
    assert "gh release" not in lowered
    assert "gh api" not in lowered
    assert "actions/attest@" not in lowered
    assert "actions/download-artifact@" not in lowered
    assert "scripts/smoke_" not in lowered
    assert "scripts/smoke-" not in lowered
    assert "prepare_windows_security_submission.py" not in lowered


def _assert_workflow_contract(text: str) -> None:
    _assert_workflow_digest(text)
    _assert_workflow_semantics(text)


def _replace_once(text: str, needle: str, replacement: str) -> str:
    assert text.count(needle) == 1, f"mutation fixture is not unique: {needle!r}"
    return text.replace(needle, replacement, 1)


def _mutate_step(text: str, name: str, needle: str, replacement: str) -> str:
    block = _step_block(text, name)
    mutated_block = _replace_once(block, needle, replacement)
    return _replace_once(text, block, mutated_block)


def _assert_rejected_mutation(mutated: str) -> None:
    with pytest.raises(AssertionError):
        _assert_workflow_contract(mutated)


def test_workflow_matches_code_owned_raw_byte_digest() -> None:
    text = _read_workflow()

    assert _workflow_sha256(text) == EXPECTED_WORKFLOW_SHA256
    assert len(text.encode("utf-8")) == 19716
    assert "\ufeff" not in text
    assert "\u2028" not in text
    assert "\u2029" not in text


def test_workflow_has_exact_approved_semantics() -> None:
    _assert_workflow_contract(_read_workflow())


@pytest.mark.parametrize(
    ("mutation", "step_name", "needle", "replacement"),
    (
        (
            "ordinary comment",
            None,
            "name: Private Windows replacement candidate\n",
            "# reviewed\nname: Private Windows replacement candidate\n",
        ),
        (
            "document marker",
            None,
            "name: Private Windows replacement candidate\n",
            "---\nname: Private Windows replacement candidate\n",
        ),
        (
            "quoted if control",
            "Require deliberate private replacement phrase",
            "        run: |\n",
            '        "if": ${{ false }}\n        run: |\n',
        ),
        (
            "if control",
            "Require deliberate private replacement phrase",
            "        shell: pwsh\n",
            "        if: ${{ false }}\n        shell: pwsh\n",
        ),
        (
            "quoted continue-on-error",
            "Require unused beta.7 release slot before build",
            "        shell: pwsh\n",
            '        "continue-on-error": true\n        shell: pwsh\n',
        ),
        (
            "continue-on-error",
            "Require unused beta.7 release slot before build",
            "        shell: pwsh\n",
            "        continue-on-error: true\n        shell: pwsh\n",
        ),
        (
            "alternate version input",
            "Build direct unsigned package and installed-component provenance",
            "VERSION: ${{ inputs.version }}",
            "VERSION: ${{ inputs.source_commit }}",
        ),
        (
            "mutable action ref",
            None,
            "actions/checkout@11d5960a326750d5838078e36cf38b85af677262",
            "actions/checkout@v4",
        ),
        (
            "alternate archive source",
            STAGE_NAME,
            '"release/$archiveName" = "dist/release/$archiveName"',
            '"release/$archiveName" = "dist/other/$archiveName"',
        ),
        (
            "alternate component source",
            STAGE_NAME,
            '"components/AllTheContextSetup.exe" = "dist/desktop/AllTheContextSetup.exe"',
            '"components/AllTheContextSetup.exe" = "dist/other/AllTheContextSetup.exe"',
        ),
        (
            "wildcard handoff source",
            STAGE_NAME,
            "Copy-Item -LiteralPath $source -Destination $destination",
            "Copy-Item -Path dist/release/* -Destination $destination",
        ),
        (
            "alternate verifier path",
            INDEPENDENT_VERIFIER_NAME,
            "--source-root . `",
            "--source-root alternate `",
        ),
        (
            "alternate upload path",
            UPLOAD_NAME,
            "dist/replacement-candidate-handoff/**",
            "dist/release/**",
        ),
        (
            "extra verifier statement",
            INDEPENDENT_VERIFIER_NAME,
            "if ($LASTEXITCODE -ne 0) {",
            "Write-Output 'unexpected statement'\nif ($LASTEXITCODE -ne 0) {",
        ),
        (
            "duplicate verifier invocation",
            INDEPENDENT_VERIFIER_NAME,
            "if ($LASTEXITCODE -ne 0) {",
            "python scripts/verify_installed_component_manifest_independent.py verify-archive `\n"
            "if ($LASTEXITCODE -ne 0) {",
        ),
        (
            "Unicode line separator",
            INDEPENDENT_VERIFIER_NAME,
            (
                '          $ErrorActionPreference = "Stop"\n'
                "          python scripts/verify_installed_component_manifest_independent.py"
            ),
            (
                '          $ErrorActionPreference = "Stop"\u2028'
                "          python scripts/verify_installed_component_manifest_independent.py"
            ),
        ),
        (
            "folded verifier scalar",
            INDEPENDENT_VERIFIER_NAME,
            "        run: |\n",
            "        run: >-\n",
        ),
        (
            "chomped verifier scalar",
            INDEPENDENT_VERIFIER_NAME,
            "        run: |\n",
            "        run: |-\n",
        ),
        (
            "indented verifier scalar",
            INDEPENDENT_VERIFIER_NAME,
            "        run: |\n",
            "        run: |2\n",
        ),
        (
            "plain multiline verifier scalar",
            INDEPENDENT_VERIFIER_NAME,
            "        run: |\n",
            "        run:\n",
        ),
        (
            "here-string verifier",
            INDEPENDENT_VERIFIER_NAME,
            "python scripts/verify_installed_component_manifest_independent.py verify-archive `",
            "$verifier = @'\n"
            "python scripts/verify_installed_component_manifest_independent.py verify-archive `",
        ),
        (
            "last-exit-code overwrite",
            INDEPENDENT_VERIFIER_NAME,
            "exit $LASTEXITCODE",
            "$LASTEXITCODE = 0\nexit $LASTEXITCODE",
        ),
        (
            "post-verifier mutator",
            None,
            f"      - name: {STAGE_NAME}\n",
            "      - name: Mutate staged artifact\n"
            "        shell: pwsh\n"
            "        run: Set-Content -LiteralPath "
            "dist/replacement-candidate-handoff\\tampered.txt -Value tampered\n"
            f"      - name: {STAGE_NAME}\n",
        ),
        (
            "extra trailing step",
            None,
            "          retention-days: 7\n",
            "          retention-days: 7\n"
            "      - name: Unexpected post-upload step\n"
            "        run: Write-Output unexpected\n",
        ),
        (
            "duplicate step identity",
            None,
            f"      - name: {STAGE_NAME}\n",
            f"      - name: {STAGE_NAME}\n      - name: {STAGE_NAME}\n",
        ),
    ),
)
def test_workflow_contract_rejects_tracked_mutations(
    mutation: str, step_name: str | None, needle: str, replacement: str
) -> None:
    del mutation
    text = _read_workflow()
    mutated = (
        _replace_once(text, needle, replacement)
        if step_name is None
        else _mutate_step(text, step_name, needle, replacement)
    )
    _assert_rejected_mutation(mutated)


@pytest.mark.parametrize("tag", ("!", "!0", "!_", "!verifier-defaults"))
def test_workflow_contract_rejects_yaml_tags(tag: str) -> None:
    text = _read_workflow()
    mutated = _mutate_step(
        text,
        INDEPENDENT_VERIFIER_NAME,
        "        shell: pwsh\n",
        f"        shell: {tag}\n",
    )
    _assert_rejected_mutation(mutated)


def test_workflow_contract_rejects_yaml_anchor_alias_and_merge_indirection() -> None:
    text = _read_workflow()
    mutations = (
        _mutate_step(
            text,
            INDEPENDENT_VERIFIER_NAME,
            "        shell: pwsh\n",
            "        verifier_defaults: &verifier_defaults {}\n        shell: pwsh\n",
        ),
        _mutate_step(
            text,
            INDEPENDENT_VERIFIER_NAME,
            "        shell: pwsh\n",
            "        verifier_defaults: *verifier_defaults\n        shell: pwsh\n",
        ),
        _mutate_step(
            text,
            INDEPENDENT_VERIFIER_NAME,
            "        shell: pwsh\n",
            "        <<: *verifier_defaults\n        shell: pwsh\n",
        ),
    )
    for mutated in mutations:
        _assert_rejected_mutation(mutated)


def test_workflow_contract_rejects_duplicate_yaml_key() -> None:
    text = _read_workflow()
    mutated = _mutate_step(
        text,
        INDEPENDENT_VERIFIER_NAME,
        "        shell: pwsh\n",
        "        shell: pwsh\n        shell: pwsh\n",
    )
    _assert_rejected_mutation(mutated)


def test_workflow_contract_rejects_detached_verifier_invocation() -> None:
    text = _read_workflow()
    detached = (
        "      - name: Detached independent verifier invocation\n"
        "        shell: pwsh\n"
        "        run: python scripts/verify_installed_component_manifest_independent.py "
        "verify-archive\n"
    )
    mutated = _replace_once(
        text,
        f"      - name: {STAGE_NAME}\n",
        detached + f"      - name: {STAGE_NAME}\n",
    )
    _assert_rejected_mutation(mutated)


def test_workflow_contract_rejects_post_verifier_shell_statement() -> None:
    text = _read_workflow()
    mutated = _mutate_step(
        text,
        INDEPENDENT_VERIFIER_NAME,
        "          if ($LASTEXITCODE -ne 0) {\n",
        "          Write-Output unexpected\n          if ($LASTEXITCODE -ne 0) {\n",
    )
    _assert_rejected_mutation(mutated)
