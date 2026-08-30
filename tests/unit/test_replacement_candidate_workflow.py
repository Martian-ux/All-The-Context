"""Contract tests for the private Windows replacement-candidate workflow."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "replacement-candidate.yml"
ACTION_USE = re.compile(r"^\s+uses:\s+([^\s]+)", re.MULTILINE)
INDEPENDENT_VERIFIER_SCRIPT = "verify_installed_component_manifest_independent.py"

CANONICAL_INDEPENDENT_VERIFIER_RUN = "\n".join(
    (
        '$ErrorActionPreference = "Stop"',
        "python scripts/verify_installed_component_manifest_independent.py verify-archive `",
        '  --archive "dist/release/all-the-context-$env:VERSION-windows-x86_64.zip" `',
        "  --direct-package "
        '"dist/release/all-the-context-$env:VERSION-windows-x86_64-unsigned.exe" `',
        "  --main dist/desktop/AllTheContextSetup.exe `",
        "  --mcp build/desktop/helper-dist/AllTheContextMCP.exe `",
        "  --recovery dist/desktop/AllTheContextRecovery.exe `",
        "  --updater build/desktop/update-helper-dist/AllTheContextUpdater.exe `",
        "  --source-root . `",
        '  --version "$env:VERSION" `',
        '  --source-commit "$env:SOURCE_COMMIT" `',
        "  --platform windows `",
        "  --architecture x86_64",
        "if ($LASTEXITCODE -ne 0) {",
        '  throw "Independent installed-component verification failed closed"',
        "}",
        "exit $LASTEXITCODE",
    )
) + "\n"


class _WorkflowYamlParser:
    """Parse the small YAML subset used by the workflow contract fixture."""

    def __init__(self, text: str) -> None:
        assert "#" not in text, "workflow YAML comments are not supported by this contract"
        self._lines = text.splitlines()
        self._reject_unsupported_yaml_syntax()

    def parse(self) -> dict[str, object]:
        index = self._next_content(0)
        assert index is not None
        value, index = self._node(index, self._indent(self._lines[index]))
        assert self._next_content(index) is None
        assert isinstance(value, dict)
        return value

    def _node(self, index: int, indent: int) -> tuple[object, int]:
        line = self._lines[index]
        assert self._indent(line) == indent
        if line[indent:].startswith("-"):
            return self._sequence(index, indent)
        return self._mapping(index, indent)

    def _mapping(
        self, index: int, indent: int, values: dict[str, object] | None = None
    ) -> tuple[dict[str, object], int]:
        result = {} if values is None else values
        while True:
            index = self._next_content(index)
            if index is None:
                return result, len(self._lines)
            line = self._lines[index]
            current_indent = self._indent(line)
            if current_indent < indent:
                return result, index
            assert current_indent == indent
            content = line[indent:]
            if content.startswith("-"):
                return result, index
            key, raw_value = self._split_mapping(content)
            assert key not in result, f"duplicate YAML mapping key: {key!r}"
            result[key] = self._value(raw_value, index + 1, indent)
            index = self._value_end(raw_value, index + 1, indent)

    def _sequence(self, index: int, indent: int) -> tuple[list[object], int]:
        result: list[object] = []
        while True:
            index = self._next_content(index)
            if index is None:
                return result, len(self._lines)
            line = self._lines[index]
            current_indent = self._indent(line)
            if current_indent < indent:
                return result, index
            assert current_indent == indent
            content = line[indent:]
            assert content.startswith("-")
            item = content[1:].lstrip()
            if not item:
                child = self._next_content(index + 1)
                if child is None or self._indent(self._lines[child]) <= indent:
                    result.append(None)
                    index = index + 1
                else:
                    value, index = self._node(child, self._indent(self._lines[child]))
                    result.append(value)
                continue

            key, raw_value = self._split_mapping(item)
            item_values: dict[str, object] = {}
            item_values[key] = self._value(raw_value, index + 1, indent + 2)
            index = self._value_end(raw_value, index + 1, indent + 2)
            child = self._next_content(index)
            if child is not None and self._indent(self._lines[child]) > indent:
                assert self._indent(self._lines[child]) == indent + 2
                item_values, index = self._mapping(child, indent + 2, item_values)
            result.append(item_values)

    def _value(self, raw_value: str, index: int, parent_indent: int) -> object:
        value = raw_value.strip()
        if value.startswith(("|", ">")):
            block, _ = self._block_scalar(value, index, parent_indent)
            return block
        if value:
            return self._scalar(value)
        child = self._next_content(index)
        if child is None or self._indent(self._lines[child]) <= parent_indent:
            return {}
        parsed, _ = self._node(child, self._indent(self._lines[child]))
        return parsed

    def _value_end(self, raw_value: str, index: int, parent_indent: int) -> int:
        value = raw_value.strip()
        if value.startswith(("|", ">")):
            _, index = self._block_scalar(value, index, parent_indent)
            return index
        if value:
            return index
        child = self._next_content(index)
        if child is None or self._indent(self._lines[child]) <= parent_indent:
            return index
        _, index = self._node(child, self._indent(self._lines[child]))
        return index

    def _block_scalar(self, style: str, index: int, parent_indent: int) -> tuple[str, int]:
        assert style in {"|", ">-"}, f"unsupported YAML block style: {style}"
        content: list[str] = []
        block_indent: int | None = None
        while index < len(self._lines):
            line = self._lines[index]
            if not line.strip():
                content.append("")
                index += 1
                continue
            current_indent = self._indent(line)
            if current_indent <= parent_indent:
                break
            if block_indent is None:
                block_indent = current_indent
            assert current_indent >= block_indent
            content.append(line[block_indent:])
            index += 1
        if style.startswith(">"):
            value = " ".join(part.strip() for part in content).strip()
        else:
            value = "\n".join(content)
        if style.endswith("-"):
            return value.rstrip("\n"), index
        return value + "\n", index

    def _reject_unsupported_yaml_syntax(self) -> None:
        """Reject YAML features whose semantics this small parser does not model."""

        block_parent_indent: int | None = None
        for line in self._lines:
            if not line.strip():
                continue
            current_indent = self._indent(line)
            if block_parent_indent is not None:
                if current_indent > block_parent_indent:
                    continue
                block_parent_indent = None

            content = line[current_indent:]
            structural = self._mask_quoted_scalars(content).strip()
            assert structural not in {"---", "..."}, "YAML document markers are unsupported"
            assert not structural.startswith("%"), "YAML directives are unsupported"
            assert not re.match(r"^\?\s", structural), "explicit YAML keys are unsupported"
            assert not re.search(r"(?:^|\s|-)<<\s*:", structural), (
                "YAML merge keys are unsupported"
            )
            assert not re.search(r"(?<![\w])[&*][A-Za-z0-9_-]+", structural), (
                "YAML anchors and aliases are unsupported"
            )
            assert not re.search(r"(?<![\w-])!{1,2}(?:[A-Za-z<])", structural), (
                "YAML tags are unsupported"
            )

            if re.search(r":\s*[|>]", structural):
                block_parent_indent = current_indent

    @staticmethod
    def _mask_quoted_scalars(value: str) -> str:
        masked: list[str] = []
        quoted: str | None = None
        escaped = False
        for character in value:
            if quoted is not None:
                if quoted == '"' and escaped:
                    escaped = False
                elif quoted == '"' and character == "\\":
                    escaped = True
                elif character == quoted:
                    quoted = None
                masked.append(" ")
            elif character in {'"', "'"}:
                quoted = character
                masked.append(" ")
            else:
                masked.append(character)
        assert quoted is None, "unterminated YAML quoted scalar"
        return "".join(masked)

    def _next_content(self, index: int) -> int | None:
        while index < len(self._lines):
            stripped = self._lines[index].strip()
            if stripped and not stripped.startswith("#"):
                return index
            index += 1
        return None

    @staticmethod
    def _indent(line: str) -> int:
        assert "\t" not in line[: len(line) - len(line.lstrip())]
        return len(line) - len(line.lstrip(" "))

    @staticmethod
    def _split_mapping(content: str) -> tuple[str, str]:
        for index, character in enumerate(content):
            if character == ":" and (index + 1 == len(content) or content[index + 1].isspace()):
                key = content[:index].strip()
                assert key
                return key, content[index + 1 :]
        raise AssertionError(f"expected YAML mapping entry: {content!r}")

    @staticmethod
    def _scalar(value: str) -> str:
        value = _strip_yaml_comment(value)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            return value[1:-1]
        return value


def _strip_yaml_comment(value: str) -> str:
    quoted: str | None = None
    for index, character in enumerate(value):
        if character in {'"', "'"}:
            if quoted == character:
                quoted = None
            elif quoted is None:
                quoted = character
        elif character == "#" and quoted is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value


def _mapping(value: object, description: str) -> dict[str, object]:
    assert isinstance(value, dict), description
    return value


def _build_windows_steps(text: str) -> list[dict[str, object]]:
    document = _WorkflowYamlParser(text).parse()
    jobs = _mapping(document.get("jobs"), "workflow jobs mapping is required")
    build_windows = _mapping(jobs.get("build-windows"), "build-windows job mapping is required")
    raw_steps = build_windows.get("steps")
    assert isinstance(raw_steps, list), "build-windows steps list is required"
    assert all(isinstance(step, dict) for step in raw_steps), "each workflow step must be a mapping"
    return [step for step in raw_steps if isinstance(step, dict)]


def _step_identity(step: dict[str, object]) -> str:
    name = step.get("name")
    uses = step.get("uses")
    assert isinstance(name, str) or isinstance(uses, str), "workflow step has no identity"
    if isinstance(name, str):
        assert name
        return f"name:{name}"
    assert isinstance(uses, str)
    return f"uses:{uses}"


def _step_by_name(steps: list[dict[str, object]], name: str) -> tuple[int, dict[str, object]]:
    matches = [(index, step) for index, step in enumerate(steps) if step.get("name") == name]
    assert len(matches) == 1, f"expected exactly one step named {name!r}"
    return matches[0]


def _required_string(mapping: dict[str, object], key: str) -> str:
    value = mapping.get(key)
    assert isinstance(value, str), f"workflow field {key!r} must be a string"
    return value


def _normalize_script_body(value: str) -> str:
    return value.replace("\r\n", "\n")


def _normalize_command(value: str) -> str:
    return " ".join(_normalize_script_body(value).split())


def _active_run_text(run: str) -> str:
    return "\n".join(line for line in run.splitlines() if not line.lstrip().startswith("#"))


def _semantic_step_text(step: dict[str, object]) -> str:
    values: list[str] = []

    def collect(value: object, *, key: str | None = None) -> None:
        if isinstance(value, str):
            values.append(_active_run_text(value) if key == "run" else value)
        elif isinstance(value, dict):
            for key, nested in value.items():
                values.append(key)
                collect(nested, key=key)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)

    collect(step)
    return "\n".join(values)


EXPECTED_SECURITY_STEP_KEYS = {
    "Build native desktop bytes without executing them": {"name", "run"},
    "Build direct unsigned package and installed-component provenance": {"name", "env", "run"},
    "Build deterministic Windows archive and metadata": {"name", "env", "run"},
    "Build direct package SPDX subject metadata": {"name", "env", "run"},
    "Verify installed-component manifest and archive statically": {"name", "env", "run"},
    "Independently verify exact Windows candidate archive and manifest": {
        "name",
        "shell",
        "env",
        "run",
    },
    "Stage exact replacement-candidate handoff without executing binaries": {
        "name",
        "shell",
        "env",
        "run",
    },
    "Run content-hygiene scan (not malware or Defender scanning)": {"name", "env", "run"},
    "Rehash every allowlisted handoff file after content-hygiene scan": {
        "name",
        "shell",
        "env",
        "run",
    },
    "Upload private Windows candidate artifact": {"name", "uses", "with"},
}

EXPECTED_PRODUCER_COMMANDS = {
    "Build native desktop bytes without executing them": "python scripts/build_desktop.py",
    "Build direct unsigned package and installed-component provenance": (
        'python scripts/package_desktop.py --platform windows --architecture x86_64 '
        '--version "$env:VERSION" --source-commit "$env:SOURCE_COMMIT" '
        "--installed-component-output-dir dist/installed-component-package "
        "--output-dir dist/release"
    ),
    "Build deterministic Windows archive and metadata": (
        "python scripts/build_release_assets.py --source dist/installed-component-package "
        '--output-dir dist/release --version "$env:VERSION" --platform windows '
        "--architecture x86_64"
    ),
    "Build direct package SPDX subject metadata": (
        "python scripts/build_release_assets.py "
        '--source "dist/release/all-the-context-$env:VERSION-'
        'windows-x86_64-unsigned.exe" --output-dir dist/release --version "$env:VERSION" '
        "--platform windows --architecture x86_64 --subject-metadata-only"
    ),
    "Verify installed-component manifest and archive statically": (
        'python scripts/installed_component_manifest.py verify-archive --archive '
        '"dist/release/all-the-context-$env:VERSION-windows-x86_64.zip" --direct-package '
        '"dist/release/all-the-context-$env:VERSION-windows-x86_64-unsigned.exe" --main '
        "dist/desktop/AllTheContextSetup.exe --mcp build/desktop/helper-dist/AllTheContextMCP.exe "
        "--recovery dist/desktop/AllTheContextRecovery.exe --updater "
        'build/desktop/update-helper-dist/AllTheContextUpdater.exe --source-root . --version '
        '"$env:VERSION" --source-commit "$env:SOURCE_COMMIT" --platform windows '
        "--architecture x86_64"
    ),
}

EXPECTED_SECURITY_STEP_ENVS = {
    "Build direct unsigned package and installed-component provenance": {
        "VERSION": "${{ inputs.version }}",
        "SOURCE_COMMIT": "${{ github.sha }}",
    },
    "Build deterministic Windows archive and metadata": {"VERSION": "${{ inputs.version }}"},
    "Build direct package SPDX subject metadata": {"VERSION": "${{ inputs.version }}"},
    "Verify installed-component manifest and archive statically": {
        "VERSION": "${{ inputs.version }}",
        "SOURCE_COMMIT": "${{ github.sha }}",
    },
    "Independently verify exact Windows candidate archive and manifest": {
        "VERSION": "${{ inputs.version }}",
        "SOURCE_COMMIT": "${{ github.sha }}",
    },
    "Stage exact replacement-candidate handoff without executing binaries": {
        "VERSION": "${{ inputs.version }}",
        "SOURCE_COMMIT": "${{ github.sha }}",
    },
    "Run content-hygiene scan (not malware or Defender scanning)": {
        "SOURCE_COMMIT": "${{ github.sha }}",
    },
    "Rehash every allowlisted handoff file after content-hygiene scan": {
        "VERSION": "${{ inputs.version }}",
        "SOURCE_COMMIT": "${{ github.sha }}",
    },
}


def _assert_independent_verifier_contract(text: str) -> None:
    steps = _build_windows_steps(text)
    identities = [_step_identity(step) for step in steps]
    assert len(identities) == len(set(identities)), "workflow step identities must be unique"

    for name, expected_keys in EXPECTED_SECURITY_STEP_KEYS.items():
        _, step = _step_by_name(steps, name)
        assert set(step) == expected_keys, f"security-critical step {name!r} has unexpected fields"

    for name, expected_env in EXPECTED_SECURITY_STEP_ENVS.items():
        _, step = _step_by_name(steps, name)
        assert _mapping(step.get("env"), f"{name} environment mapping is required") == expected_env

    verifier_name = "Independently verify exact Windows candidate archive and manifest"
    verifier_index, verifier = _step_by_name(steps, verifier_name)
    verifier_run = _normalize_script_body(_required_string(verifier, "run"))
    assert verifier.get("shell") == "pwsh"
    assert verifier_run == CANONICAL_INDEPENDENT_VERIFIER_RUN

    independent_invocations = [
        (index, run)
        for index, step in enumerate(steps)
        for run in [step.get("run")]
        if isinstance(run, str) and INDEPENDENT_VERIFIER_SCRIPT in run
    ]
    assert len(independent_invocations) == 1, (
        "independent verifier invocation must be unique and attached to its named step"
    )
    assert independent_invocations[0][0] == verifier_index

    for name in (
        "Stage exact replacement-candidate handoff without executing binaries",
        "Rehash every allowlisted handoff file after content-hygiene scan",
    ):
        _, step = _step_by_name(steps, name)
        assert step.get("shell") == "pwsh"

    for name, command in EXPECTED_PRODUCER_COMMANDS.items():
        producer_index, producer = _step_by_name(steps, name)
        assert producer_index < verifier_index
        producer_run = _required_string(producer, "run")
        assert _normalize_command(producer_run) == command

    static_verifier_index, _ = _step_by_name(
        steps, "Verify installed-component manifest and archive statically"
    )
    assert static_verifier_index < verifier_index

    consumer_requirements = {
        "Stage exact replacement-candidate handoff without executing binaries": (
            '"source/matrix-evidence.json" = "dist/source-evidence/matrix-evidence.json"',
        ),
        "Run content-hygiene scan (not malware or Defender scanning)": (
            "--artifact-dir dist/replacement-candidate-handoff",
            "--report dist/replacement-candidate-handoff/content-hygiene-scan-report.json",
        ),
        "Rehash every allowlisted handoff file after content-hygiene scan": (
            "foreach ($relative in $finalFiles)",
            "Get-FileHash -LiteralPath $Path -Algorithm SHA256",
        ),
        "Upload private Windows candidate artifact": (
            "actions/upload-artifact@",
            "dist/replacement-candidate-handoff/**",
        ),
    }
    for name, required_values in consumer_requirements.items():
        consumer_index, consumer = _step_by_name(steps, name)
        assert consumer_index > verifier_index
        consumer_text = _semantic_step_text(consumer)
        assert all(required in consumer_text for required in required_values)

    consumer_markers = (
        "dist/replacement-candidate-handoff",
        "content-hygiene-scan-report.json",
        "handoff-inventory-v1.json",
        '"source/matrix-evidence.json"',
        "actions/upload-artifact@",
    )
    for index, step in enumerate(steps):
        step_text = _semantic_step_text(step)
        if any(marker in step_text for marker in consumer_markers):
            assert index > verifier_index, "handoff/evidence consumer precedes independent verifier"
        assert "continue-on-error" not in step
        assert "if" not in step
        assert "prepare_windows_security_submission.py" not in step_text


def _read_workflow() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _replace_verifier_run(text: str, run: str) -> str:
    canonical_yaml = "\n".join(
        f"          {line}" for line in CANONICAL_INDEPENDENT_VERIFIER_RUN.splitlines()
    )
    canonical_yaml += "\n"
    assert text.count(canonical_yaml) == 1
    replacement = "\n".join(f"          {line}" for line in run.splitlines()) + "\n"
    return text.replace(canonical_yaml, replacement, 1)


def _verifier_section(text: str) -> tuple[str, str, str]:
    marker = "      - name: Independently verify exact Windows candidate archive and manifest"
    start = text.index(marker)
    end = text.index("      - name: Stage exact replacement-candidate handoff", start)
    return text[:start], text[start:end], text[end:]


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


def test_workflow_independently_verifies_exact_candidate_before_evidence_staging() -> None:
    _assert_independent_verifier_contract(_read_workflow())


def test_workflow_semantic_contract_rejects_commented_independent_verifier() -> None:
    text = _read_workflow()
    mutated = text.replace(
        "      - name: Independently verify exact Windows candidate archive and manifest",
        "      # - name: Independently verify exact Windows candidate archive and manifest",
        1,
    )

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


@pytest.mark.parametrize(
    ("feature", "injected_yaml"),
    (
        ("anchor", "        verifier_defaults: &verifier_defaults {}\n"),
        ("alias", "        verifier_defaults: *verifier_defaults\n"),
        ("merge key", "        <<: *verifier_defaults\n"),
        ("tag", "        verifier_defaults: !verifier-defaults {}\n"),
    ),
)
def test_workflow_contract_rejects_yaml_indirection(
    feature: str, injected_yaml: str
) -> None:
    del feature
    before, verifier, after = _verifier_section(_read_workflow())
    mutated_verifier = verifier.replace(
        "        shell: pwsh\n", injected_yaml + "        shell: pwsh\n", 1
    )
    mutated = before + mutated_verifier + after

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


def test_workflow_contract_rejects_commented_verifier_body() -> None:
    mutated = _replace_verifier_run(
        _read_workflow(),
        CANONICAL_INDEPENDENT_VERIFIER_RUN.replace(
            "python scripts/verify_installed_component_manifest_independent.py",
            "# verifier invocation\n"
            "python scripts/verify_installed_component_manifest_independent.py",
            1,
        ),
    )

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


def test_workflow_contract_rejects_duplicate_mapping_key() -> None:
    before, verifier, after = _verifier_section(_read_workflow())
    mutated = before + verifier.replace(
        "        shell: pwsh\n", "        shell: pwsh\n        shell: pwsh\n", 1
    ) + after

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


@pytest.mark.parametrize(
    ("field", "value"),
    (("if", "${{ always() }}"), ("continue-on-error", "true")),
)
def test_workflow_contract_rejects_verifier_bypass_fields(field: str, value: str) -> None:
    before, verifier, after = _verifier_section(_read_workflow())
    injected = f"        {field}: {value}\n"
    mutated_verifier = verifier.replace(
        "        shell: pwsh\n", injected + "        shell: pwsh\n", 1
    )
    mutated = before + mutated_verifier + after

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


def test_workflow_contract_rejects_extra_verifier_statement() -> None:
    mutated = _replace_verifier_run(
        _read_workflow(),
        CANONICAL_INDEPENDENT_VERIFIER_RUN.replace(
            'if ($LASTEXITCODE -ne 0) {',
            "Write-Output 'unexpected statement'\nif ($LASTEXITCODE -ne 0) {",
            1,
        ),
    )

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


def test_workflow_contract_rejects_duplicate_verifier_invocation() -> None:
    mutated = _replace_verifier_run(
        _read_workflow(),
        CANONICAL_INDEPENDENT_VERIFIER_RUN.replace(
            "if ($LASTEXITCODE -ne 0) {",
            "python scripts/verify_installed_component_manifest_independent.py verify-archive `\n"
            "if ($LASTEXITCODE -ne 0) {",
            1,
        ),
    )

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


def test_workflow_contract_rejects_detached_verifier_invocation() -> None:
    text = _read_workflow()
    detached = (
        "      - name: Detached independent verifier invocation\n"
        "        run: python scripts/verify_installed_component_manifest_independent.py "
        "verify-archive\n"
    )
    mutated = text.replace(
        "      - name: Stage exact replacement-candidate handoff",
        detached + "      - name: Stage exact replacement-candidate handoff",
        1,
    )

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


@pytest.mark.parametrize(
    ("scalar_style", "replacement"),
    (
        ("folded", "run: >-"),
        ("chomped literal", "run: |-"),
        ("indented literal", "run: |2"),
        ("plain multiline", "run:"),
    ),
)
def test_workflow_contract_rejects_verifier_scalar_tricks(
    scalar_style: str, replacement: str
) -> None:
    del scalar_style
    before, verifier, after = _verifier_section(_read_workflow())
    mutated = before + verifier.replace("        run: |\n", f"        {replacement}\n", 1) + after

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


def test_workflow_contract_rejects_here_string_and_last_exit_code_assignment() -> None:
    lines = CANONICAL_INDEPENDENT_VERIFIER_RUN.splitlines()
    here_string_run = "\n".join(
        (
            lines[0],
            "$verifier = @'",
            *lines[1:13],
            "'@",
            "& ([scriptblock]::Create($verifier))",
            "$LASTEXITCODE = 0",
            *lines[13:],
        )
    ) + "\n"
    mutated = _replace_verifier_run(_read_workflow(), here_string_run)

    with pytest.raises(AssertionError):
        _assert_independent_verifier_contract(mutated)


def test_workflow_never_invokes_release_publication_or_produced_binary_smokes() -> None:
    text = _read_workflow().casefold()

    assert "gh release" not in text
    assert "gh api" not in text
    assert "actions/attest@" not in text
    assert "actions/download-artifact@" not in text
    assert "scripts/smoke_" not in text
    assert "scripts/smoke-" not in text
