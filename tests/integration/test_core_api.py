from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from allthecontext.browser_session import (
    BROWSER_AUTH_SCHEME,
    BROWSER_STORAGE_KEY,
    DASHBOARD_REQUEST_HEADER,
    LEGACY_BROWSER_COOKIE,
)
from allthecontext.client_config import configure_codex
from allthecontext.config import CoreConfig
from allthecontext.core import app as core_app
from allthecontext.core.app import create_app
from allthecontext.desktop_runtime import RuntimeCommand
from allthecontext.export import restore_export
from allthecontext.updater import PreparedArtifact, UpdatePhase
from fastapi.testclient import TestClient


def test_core_http_ingestion_review_and_retrieval(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        begin = client.post(
            "/v1/ingestion/begin",
            json={
                "mode": "model_assisted_bootstrap",
                "accessible_sources": ["current conversation"],
                "unavailable_sources": ["full account archive"],
                "idempotency_key": "session-1",
            },
        )
        assert begin.status_code == 200
        batch = client.post(
            "/v1/ingestion/batch",
            json={
                "session_id": begin.json()["session_id"],
                "idempotency_key": "batch-1",
                "candidates": [
                    {
                        "kind": "interaction_preference",
                        "content": "Use evidence-based completion reports",
                        "availability": "always_available",
                    }
                ],
            },
        )
        assert batch.status_code == 200
        candidate_id = batch.json()["candidate_ids"][0]
        approved = client.post(f"/v1/admin/candidates/{candidate_id}/approve", json={})
        assert approved.status_code == 200
        record_id = approved.json()["id"]

        search = client.post("/v1/context/search", json={"query": "evidence"})
        assert search.status_code == 200
        assert search.json()["items"][0]["id"] == record_id
        assert client.get(f"/v1/context/{record_id}").status_code == 200
        status = client.get("/v1/context/status").json()
        assert status["counts"]["approved_records"] == 1
        expected_size = config.database_path.stat().st_size
        wal_path = config.database_path.with_name(f"{config.database_path.name}-wal")
        if wal_path.exists():
            expected_size += wal_path.stat().st_size
        assert status["database_size_bytes"] == expected_size


def test_update_controls_are_admin_scoped_and_persist_preferences(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        status = client.get("/v1/admin/updates")
        assert status.status_code == 200
        assert (
            status.json().items()
            >= {
                "enabled": True,
                "channel": "stable",
                "configured": False,
                "current_version": "0.1.0",
            }.items()
        )
        changed = client.put(
            "/v1/admin/updates/preferences",
            json={"enabled": False, "channel": "beta"},
        )
        assert changed.status_code == 200
        assert changed.json()["phase"] == "disabled"
        assert json.loads(
            (tmp_path / "updates" / "preferences.json").read_text(encoding="utf-8")
        ) == {"channel": "beta", "deferred_version": None, "enabled": False}

        invalid = client.put(
            "/v1/admin/updates/preferences",
            json={"enabled": True, "channel": "nightly"},
        )
        assert invalid.status_code == 422


def test_verified_update_artifact_is_private_no_store_and_ephemeral(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    artifact = tmp_path / "private-export.zip"
    artifact.write_bytes(b"freshly reverified package")

    class ArtifactManager:
        preferences = SimpleNamespace(enabled=False, channel="stable")
        config = SimpleNamespace(manifest_urls={})
        state = SimpleNamespace(phase=UpdatePhase.MANUAL_REQUIRED)

        @staticmethod
        def public_status() -> dict[str, Any]:
            return {"phase": "manual_required"}

        @staticmethod
        def prepare_artifact_export() -> PreparedArtifact:
            return PreparedArtifact(
                artifact,
                "all-the-context-0.2.0-linux-x86_64.zip",
                artifact.stat().st_size,
            )

    with TestClient(create_app(config, update_manager=cast(Any, ArtifactManager()))) as client:
        response = client.get(
            "/v1/admin/updates/artifact",
            headers={DASHBOARD_REQUEST_HEADER: "1"},
        )

    assert response.status_code == 200
    assert response.content == b"freshly reverified package"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "all-the-context-0.2.0-linux-x86_64.zip" in response.headers["content-disposition"]
    assert str(artifact) not in response.text
    assert not artifact.exists()


def test_dashboard_downloads_complete_encrypted_export(tmp_path: Path, monkeypatch) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    destination = tmp_path / "download.atcexp"
    temporary_export = tmp_path / "temporary-export.atcexp"

    def isolated_mkstemp(**_kwargs: object) -> tuple[int, str]:
        descriptor = os.open(temporary_export, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        return descriptor, str(temporary_export)

    monkeypatch.setattr(core_app.tempfile, "mkstemp", isolated_mkstemp)
    with TestClient(create_app(config)) as client:
        imported = client.post(
            "/v1/admin/import",
            files={"file": ("context.txt", b"A private source record")},
        )
        assert imported.status_code == 200
        response = client.post(
            "/v1/admin/export",
            json={"passphrase": "correct horse battery staple"},
            headers={DASHBOARD_REQUEST_HEADER: "1"},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].endswith(
        'filename="all-the-context-backup.atcexp"'
    )
    assert b"private source" not in response.content.lower()
    destination.write_bytes(response.content)
    restored = restore_export(
        destination,
        tmp_path / "unused.sqlite3",
        "correct horse battery staple",
        dry_run=True,
    )
    assert restored["manifest"]["include_sources"] is True
    assert restored["manifest"]["include_audit"] is True
    assert not temporary_export.exists()


def test_dashboard_export_failure_cleans_temporary_file_and_redacts_passphrase(
    tmp_path: Path, monkeypatch
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    temporary_export = tmp_path / "failed-export.atcexp"

    def isolated_mkstemp(**_kwargs: object) -> tuple[int, str]:
        descriptor = os.open(temporary_export, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        return descriptor, str(temporary_export)

    monkeypatch.setattr(core_app.tempfile, "mkstemp", isolated_mkstemp)
    monkeypatch.setattr(
        core_app,
        "create_export",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("export failed")),
    )
    passphrase = "never return this secret"
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/v1/admin/export",
            json={"passphrase": passphrase},
            headers={DASHBOARD_REQUEST_HEADER: "1"},
        )

    assert response.status_code == 500
    assert passphrase not in response.text
    assert not temporary_export.exists()


def test_dashboard_export_refuses_vault_above_resource_bound(tmp_path: Path, monkeypatch) -> None:
    config = replace(
        CoreConfig.in_directory(tmp_path, require_auth=False),
        max_dashboard_export_bytes=0,
    )
    called: list[bool] = []
    monkeypatch.setattr(
        core_app,
        "create_export",
        lambda *_args, **_kwargs: called.append(True),
    )
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/v1/admin/export",
            json={"passphrase": "correct horse battery staple"},
            headers={DASHBOARD_REQUEST_HEADER: "1"},
        )

    assert response.status_code == 413
    assert response.json()["detail"] == (
        "The Core is too large for dashboard export; use the CLI instead"
    )
    assert not called


def test_edge_local_forget_requires_explicit_host_deletion_phrase(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    app = create_app(config)
    with TestClient(app) as client:
        prepared = client.post("/v1/admin/edge/prepare")
        assert prepared.status_code == 200
        assert (
            client.post("/v1/admin/edge/forget", json={"confirmation": "delete"}).status_code == 422
        )
        forgotten = client.post(
            "/v1/admin/edge/forget",
            json={"confirmation": "DELETE HOSTED EDGE"},
        )
        assert forgotten.status_code == 200
        assert forgotten.json()["state"] == "not_configured"
        assert app.state.edge_connections.state() is None
        assert app.state.edge_connections.material() is None


def test_edge_local_forget_refuses_a_healthy_paired_service(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    app = create_app(config)
    with TestClient(app) as client:
        assert client.post("/v1/admin/edge/prepare").status_code == 200
        state = app.state.edge_connections.state()
        assert state is not None
        app.state.edge_connections.save_state(
            replace(
                state,
                edge_url="https://personal-edge.example",
                connected_at="2026-07-21T00:00:00+00:00",
                last_success_at="2026-07-21T00:01:00+00:00",
            )
        )

        refused = client.post(
            "/v1/admin/edge/forget",
            json={"confirmation": "DELETE HOSTED EDGE"},
        )

        assert refused.status_code == 409
        assert "Remove active data and disconnect" in refused.json()["detail"]
        assert app.state.edge_connections.state() is not None
        assert app.state.edge_connections.material() is not None


def test_setup_auth_browser_handoff_and_app_connections(tmp_path: Path, monkeypatch) -> None:
    codex_home = tmp_path / "codex"
    claude_config = tmp_path / "claude" / "claude_desktop_config.json"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("ATC_CLAUDE_CONFIG", str(claude_config))
    monkeypatch.setenv("ATC_EDGE_MCP_URL", "https://relay.example.test/mcp")
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/context/status").status_code == 401
        setup = client.post("/v1/setup", json={"name": "Dashboard", "scopes": []})
        assert setup.status_code == 200
        token = setup.json()["token"]
        owner_client_id = setup.json()["client"]["id"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/v1/context/status", headers=headers).status_code == 200
        handoff = client.post("/v1/admin/browser-session", headers=headers)
        assert handoff.status_code == 200
        connect_path = handoff.json()["connect_path"]
        assert token not in connect_path
        connected = client.get(f"{connect_path}&page=connections", follow_redirects=False)
        assert connected.status_code == 200
        assert connected.headers["cache-control"] == "no-store"
        assert token not in connected.text
        assert LEGACY_BROWSER_COOKIE in connected.headers["set-cookie"]
        assert token not in connected.headers["set-cookie"]
        assert "Max-Age=0" in connected.headers["set-cookie"]
        assert 'window.location.replace("/?page=connections")' in connected.text
        session_match = re.search(
            rf'sessionStorage\.setItem\("{re.escape(BROWSER_STORAGE_KEY)}","([^"]+)"\)',
            connected.text,
        )
        assert session_match is not None
        browser_token = session_match.group(1)
        browser_auth = {"Authorization": f"{BROWSER_AUTH_SCHEME} {browser_token}"}
        dashboard_headers = {**browser_auth, DASHBOARD_REQUEST_HEADER: "1"}
        assert client.get("/v1/context/status", headers=browser_auth).status_code == 200
        edge_setup = client.get("/v1/admin/edge", headers=browser_auth)
        assert edge_setup.status_code == 200
        providers = {item["id"]: item for item in edge_setup.json()["providers"]}
        assert providers["chatgpt"]["mobile_supported"] is False
        assert "web-only" in providers["chatgpt"]["detail"]
        prepared_claim = client.post("/v1/admin/edge/prepare", headers=dashboard_headers)
        assert prepared_claim.status_code == 200
        deployment_file = client.post("/v1/admin/edge/deployment-env", headers=dashboard_headers)
        assert deployment_file.status_code == 200
        assert deployment_file.headers["cache-control"] == "no-store"
        assert 'filename="setup.env"' in deployment_file.headers["content-disposition"]
        assert "atc-edge-claim-v1." in deployment_file.text
        material = client.app.state.edge_connections.material()
        assert material is not None
        assert material.bundle.replication_secret not in deployment_file.text
        assert material.bundle.replication_token not in deployment_file.text
        assert token not in deployment_file.text
        client.app.state.edge_connections.replace_bundle(
            material,
            material.bundle,
            preserve_claim=False,
        )
        prepared_again = client.post("/v1/admin/edge/prepare", headers=dashboard_headers)
        assert prepared_again.status_code == 409
        assert material.bundle.replication_secret not in prepared_again.text
        assert material.bundle.replication_token not in prepared_again.text
        assert token not in prepared_again.text
        assert providers["chatgpt"]["setup_url"] == "https://chatgpt.com/"
        assert "eligible workspace admin" in providers["chatgpt"]["setup_steps"][0]
        assert providers["claude"]["mobile_supported"] is True
        assert "Settings → Connectors" in providers["claude"]["setup_steps"][0]
        assert client.get("/v1/context/status").status_code == 401
        assert client.get(connect_path, follow_redirects=False).status_code == 410
        integrations = client.get("/v1/admin/integrations", headers=browser_auth)
        assert integrations.status_code == 200
        assert not any(item["configured"] for item in integrations.json()["apps"])
        assert (
            client.post("/v1/admin/integrations/chatgpt_codex", headers=browser_auth).status_code
            == 403
        )
        unprotected_export = client.post(
            "/v1/admin/export",
            headers=browser_auth,
            json={"passphrase": "do not expose this passphrase"},
        )
        assert unprotected_export.status_code == 403
        assert "do not expose this passphrase" not in unprotected_export.text

        short_passphrase = client.post(
            "/v1/admin/export",
            headers=dashboard_headers,
            json={"passphrase": "short"},
        )
        assert short_passphrase.status_code == 422
        assert "short" not in short_passphrase.text

        codex_connection = client.post(
            "/v1/admin/integrations/chatgpt_codex", headers=dashboard_headers
        )
        assert codex_connection.status_code == 200
        codex = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
        codex_env = codex["mcp_servers"]["all_the_context"]["env"]
        assert codex_env.get("ATC_CLIENT_TOKEN") != token
        assert codex_env["ATC_CLIENT_ID"] == codex_connection.json()["client_id"]
        configure_codex(
            RuntimeCommand.current(),
            codex_env["ATC_CLIENT_ID"],
            token=codex_env.get("ATC_CLIENT_TOKEN"),
            path=codex_home / "config.toml",
            target_url="http://127.0.0.1:9999",
        )
        degraded = client.get("/v1/admin/integrations", headers=browser_auth).json()
        codex_status = next(item for item in degraded["apps"] if item["id"] == "chatgpt_codex")
        assert codex_status["state"] == "degraded"
        assert "different Core" in codex_status["reason"]
        assert degraded["remote"]["configured"] is False
        assert degraded["remote"]["state"] == "prepared"
        assert (
            client.post(
                "/v1/admin/integrations/chatgpt_codex", headers=dashboard_headers
            ).status_code
            == 200
        )

        claude_connection = client.post("/v1/admin/integrations/claude", headers=dashboard_headers)
        assert claude_connection.status_code == 200
        claude = json.loads(claude_config.read_text(encoding="utf-8"))
        claude_env = claude["mcpServers"]["all-the-context"]["env"]
        assert claude_env.get("ATC_CLIENT_TOKEN") != token
        assert claude_env["ATC_CLIENT_ID"] == claude_connection.json()["client_id"]
        assert claude_env["ATC_CLIENT_ID"] != codex_env["ATC_CLIENT_ID"]
        registered = {
            item["id"]: item
            for item in client.get("/v1/admin/clients", headers=browser_auth).json()["items"]
        }
        assert registered[owner_client_id]["protected"] is True
        protected_revoke = client.post(
            f"/v1/admin/clients/{owner_client_id}/revoke", headers=dashboard_headers
        )
        assert protected_revoke.status_code == 409
        assert "admin" not in registered[codex_connection.json()["client_id"]]["scopes"]
        assert "admin" not in registered[claude_connection.json()["client_id"]]["scopes"]
        assert all(
            item["configured"]
            for item in client.get("/v1/admin/integrations", headers=browser_auth).json()["apps"]
        )
        disconnected = client.delete("/v1/admin/integrations/claude", headers=dashboard_headers)
        assert disconnected.status_code == 200
        assert (
            "all-the-context"
            not in json.loads(claude_config.read_text(encoding="utf-8"))["mcpServers"]
        )
        integration_items = client.get("/v1/admin/integrations", headers=browser_auth).json()[
            "apps"
        ]
        assert next(item for item in integration_items if item["id"] == "claude")["state"] == (
            "disconnected"
        )
        assert client.post("/v1/setup", json={"name": "Other", "scopes": []}).status_code == 409


def test_browser_session_cannot_authenticate_to_another_core(tmp_path: Path) -> None:
    first_config = CoreConfig.in_directory(tmp_path / "first", require_auth=True)
    second_config = CoreConfig.in_directory(tmp_path / "second", require_auth=True)
    with (
        TestClient(create_app(first_config)) as first,
        TestClient(create_app(second_config)) as second,
    ):
        first_setup = first.post("/v1/setup", json={"name": "First", "scopes": []})
        second.post("/v1/setup", json={"name": "Second", "scopes": []})
        handoff = first.post(
            "/v1/admin/browser-session",
            headers={"Authorization": f"Bearer {first_setup.json()['token']}"},
        )
        html = first.get(handoff.json()["connect_path"]).text
        session_match = re.search(
            rf'sessionStorage\.setItem\("{re.escape(BROWSER_STORAGE_KEY)}","([^"]+)"\)',
            html,
        )
        assert session_match is not None

        response = second.get(
            "/v1/context/status",
            headers={"Authorization": f"{BROWSER_AUTH_SCHEME} {session_match.group(1)}"},
        )

        assert response.status_code == 401


def test_authenticated_shutdown_requests_graceful_server_exit(tmp_path: Path) -> None:
    requested: list[bool] = []
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config, shutdown_callback=lambda: requested.append(True))) as client:
        response = client.post("/v1/admin/shutdown")
    assert response.status_code == 200
    assert response.json() == {"shutting_down": True}
    assert requested == [True]


def test_archive_upload_creates_reviewable_candidates(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        response = client.post(
            "/v1/admin/import",
            files={"file": ("context.json", b'{"goals":["Build a portable context app"]}')},
            data={"source_service": "test-export"},
        )
        assert response.status_code == 200, response.text
        candidates = client.get("/v1/admin/candidates").json()
        assert candidates["total"] == 1
        assert candidates["items"][0]["source_service"] == "test-export"
