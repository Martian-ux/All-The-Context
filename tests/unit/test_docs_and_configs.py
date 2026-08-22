from __future__ import annotations

import json
import tomllib
from pathlib import Path

from scripts.release_candidate import validate_public_readiness_docs

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_manifest_and_client_examples_are_valid() -> None:
    package = json.loads((REPOSITORY_ROOT / "apps" / "dashboard" / "package.json").read_text())
    claude = json.loads(
        (
            REPOSITORY_ROOT / "integrations" / "claude" / "claude_desktop_config.json.example"
        ).read_text()
    )
    codex = tomllib.loads(
        (REPOSITORY_ROOT / "integrations" / "codex" / "config.toml.example").read_text()
    )

    assert package["scripts"]["build"]
    assert claude["mcpServers"]["all-the-context"]["command"] == "atc-mcp"
    assert codex["mcp_servers"]["all_the_context"]["command"] == "atc-mcp"
    assert "ATC_TARGET_URL" in claude["mcpServers"]["all-the-context"]["env"]
    assert "ATC_TARGET_URL" in codex["mcp_servers"]["all_the_context"]["env"]


def test_examples_use_loopback_and_no_real_credentials() -> None:
    integration_root = REPOSITORY_ROOT / "integrations"
    example_text = "\n".join(
        path.read_text(encoding="utf-8") for path in integration_root.rglob("*") if path.is_file()
    )

    assert "http://127.0.0.1:7337" in example_text
    assert "atc-mcp" in example_text
    assert "replace-with-one-time-token" in example_text
    assert "sk-" not in example_text


def test_cross_platform_workflow_and_operations_are_present() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    platforms = (REPOSITORY_ROOT / "docs" / "operations" / "PLATFORMS.md").read_text()
    runbook = (REPOSITORY_ROOT / "docs" / "operations" / "RUNBOOK.md").read_text()

    for runner in ("windows-latest", "macos-latest", "ubuntu-latest"):
        assert runner in workflow
    assert 'python-version: "3.12"' in workflow
    assert "npm run build" in workflow
    assert "Windows Credential Manager" in platforms
    assert "macOS Keychain" in platforms
    assert "127.0.0.1" in runbook


def test_public_release_readiness_documents_are_linked_and_truthful() -> None:
    validate_public_readiness_docs(REPOSITORY_ROOT)
    security = (REPOSITORY_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "HTTP 404" in security
    assert "does not silently select a plaintext fallback" in security
    assert "still constructs Edge compatibility managers" not in security
    assert "can still select that fallback automatically" not in security


def test_release_workflows_are_immutable_and_offline_signing_is_documented() -> None:
    candidate = (REPOSITORY_ROOT / ".github" / "workflows" / "release-candidate.yml").read_text()
    publish = (REPOSITORY_ROOT / ".github" / "workflows" / "publish-beta-release.yml").read_text()
    promote = (REPOSITORY_ROOT / ".github" / "workflows" / "promote-beta-channel.yml").read_text()
    releases = (REPOSITORY_ROOT / "docs" / "operations" / "RELEASES.md").read_text()
    keys = json.loads((REPOSITORY_ROOT / "release" / "keys.json").read_text())
    packaged_keys = json.loads(
        (
            REPOSITORY_ROOT
            / "packages"
            / "allthecontext"
            / "src"
            / "allthecontext"
            / "update_keys.json"
        ).read_text()
    )

    assert "source_commit" in candidate
    assert "--draft" in candidate
    assert "actions/attest@v4" in candidate
    assert "package_desktop.py" in candidate
    assert "platform: windows" in candidate
    assert "platform: linux" in candidate
    assert "platform: macos" not in candidate
    assert "macos-26" not in candidate
    assert "macos-26-intel" not in candidate
    assert "-unsigned.dmg" not in candidate
    assert "-unsigned.dmg" not in publish
    assert "validate-runner" in candidate
    assert "direct unsigned native package" in candidate
    assert "direct unsigned one-click" not in candidate
    assert "--ota-target windows:x86_64" in candidate
    assert "--target windows:x86_64" in candidate
    assert "--target linux:x86_64" in candidate
    assert "--target macos:" not in candidate
    assert "--target macos:" not in publish
    assert "--target macos:" not in promote
    assert "-unsigned.dmg" not in promote
    assert "v0.1.0-beta.6" in publish
    assert "v0.1.0-beta.2" not in publish
    assert "v0.1.0-beta.1" not in publish
    assert "--clobber" not in candidate
    assert "github_release_gate.py" in candidate
    assert "BUILD IMMUTABLE CANDIDATE" in candidate
    assert "--operator-verified-immutability" in candidate
    assert "environment: release-promotion" in publish
    assert "immutable-releases" not in publish
    assert "PUBLISH UNSIGNED BETA" in publish
    assert "gh release verify" in publish
    assert "workflow_dispatch" in promote
    assert "actions/upload-pages-artifact@v4" in promote
    assert "actions/deploy-pages@v4" in promote
    assert "--ota-target windows:x86_64" in promote
    assert "push:" not in promote
    assert "release:" not in promote
    assert "private key" in releases
    assert "outside GitHub" in releases
    assert "unsigned community builds" in releases
    assert "not a community release gate" in releases
    assert "Pages is an explicit operator gate" in releases
    assert "Administration: read" in releases
    assert "BUILD IMMUTABLE CANDIDATE" in releases
    assert "encrypted PKCS8" in releases
    assert packaged_keys == keys
    assert keys == {
        "schema_version": 1,
        "keys": [
            {
                "algorithm": "Ed25519",
                "channels": ["beta"],
                "key_id": "release-2026-a",
                "public_key": "cl9ZWb0x-nxUHaklqdMq2rkEmayCi3nrW4CFOXZEQ5s",
                "public_key_sha256": (
                    "sha256:fe05a2bd52db97f808650fb0e832c49bd704abd62a813af4dedca4994f98e0d4"
                ),
                "status": "revoked",
            },
            {
                "algorithm": "Ed25519",
                "channels": ["beta"],
                "key_id": "release-2026-b",
                "public_key": "omaAzCobyHsqPt8WEjRu7peKvZ_qlDEknSd9NK8_trM",
                "public_key_sha256": (
                    "sha256:40f95302dd6c0241dc7f639e29693c15e94c5ccae1357b927d039a7e6bf1cf8f"
                ),
                "status": "active",
            },
        ],
    }


def test_active_release_docs_keep_published_and_historical_identities_distinct() -> None:
    status = (REPOSITORY_ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    traceability = (REPOSITORY_ROOT / "docs" / "REQUIREMENTS_TRACEABILITY.md").read_text(
        encoding="utf-8"
    )
    execution = (
        REPOSITORY_ROOT / "docs" / "product" / "ZERO_FRICTION_EXECUTION_PLAN.md"
    ).read_text(encoding="utf-8")
    decisions = (REPOSITORY_ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")

    assert "`0.1.0-beta.6` is published as immutable prerelease ID `374723649`" in status
    assert "all 34" in status and "external ledger" in status
    assert "live unpublished beta.3 identity" not in traceability
    assert "Phase 0 preserves the beta.3 boundary" not in traceability
    assert "Publish and verify beta.3" not in execution
    assert "align active acceptance work with beta.3" not in execution
    assert "ADR-095: Candidate identities stay external" in decisions
    assert "ADR-096: Opaque GitHub CLI asset IDs" in decisions


def test_v1_has_no_hosted_runtime_publication_or_provider_template() -> None:
    assert not (REPOSITORY_ROOT / ".github" / "workflows" / "edge-image.yml").exists()
    assert not (REPOSITORY_ROOT / "render.yaml").exists()
    assert not (REPOSITORY_ROOT / "deploy" / "edge" / "render.template.yaml").exists()
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    readme = (REPOSITORY_ROOT / "README.md").read_text()
    assert "relay-container:" not in workflow
    assert "no hosted Edge" in readme
