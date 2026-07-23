from __future__ import annotations

import json
import tomllib
from pathlib import Path

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
    assert "macos-26\n" in candidate
    assert "macos-26-intel" in candidate
    assert "validate-runner" in candidate
    assert "direct unsigned native package" in candidate
    assert "direct unsigned one-click" not in candidate
    assert "--ota-target windows:x86_64" in candidate
    assert "--clobber" not in candidate
    assert "github_release_gate.py" in candidate
    assert "environment: release-promotion" in publish
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
                "status": "active",
            }
        ],
    }


def test_v1_has_no_hosted_runtime_publication_or_provider_template() -> None:
    assert not (REPOSITORY_ROOT / ".github" / "workflows" / "edge-image.yml").exists()
    assert not (REPOSITORY_ROOT / "render.yaml").exists()
    assert not (REPOSITORY_ROOT / "deploy" / "edge" / "render.template.yaml").exists()
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    readme = (REPOSITORY_ROOT / "README.md").read_text()
    assert "relay-container:" not in workflow
    assert "no hosted Edge" in readme
