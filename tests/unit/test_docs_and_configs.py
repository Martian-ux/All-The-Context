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
    image = (REPOSITORY_ROOT / ".github" / "workflows" / "edge-image.yml").read_text()
    releases = (REPOSITORY_ROOT / "docs" / "operations" / "RELEASES.md").read_text()
    keys = json.loads((REPOSITORY_ROOT / "release" / "keys.json").read_text())

    assert "source_commit" in candidate
    assert "--draft" in candidate
    assert "attest-build-provenance" in candidate
    assert "type=sha,format=long,prefix=sha-" in image
    assert "subject-digest" in image
    assert "private key" in releases
    assert "outside GitHub" in releases
    assert "unsigned community builds" in releases
    assert "not a community release gate" in releases
    assert keys == {"schema_version": 1, "keys": []}


def test_relay_container_uses_non_root_user_and_loopback_host_mapping() -> None:
    dockerfile = (REPOSITORY_ROOT / "apps" / "relay" / "Dockerfile").read_text()
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text()

    assert "USER 10001:10001" in dockerfile
    assert "127.0.0.1:${ATC_RELAY_PORT:-8743}:8743" in compose
    assert "ATC_RELAY_REPLICATION_SECRET" in compose


def test_render_blueprint_accepts_only_the_one_time_claim_handoff() -> None:
    blueprint = (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "autoDeploy: false" in blueprint
    assert "key: ATC_EDGE_BUNDLE" in blueprint
    assert "sync: false" in blueprint
    assert "ATC_RELAY_REPLICATION_SECRET" not in blueprint
    assert "ATC_RELAY_BEARER_TOKEN" not in blueprint
    assert "ATC_RELAY_CLIENTS_JSON" not in blueprint
