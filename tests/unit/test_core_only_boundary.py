"""Negative proofs that supported V1 surfaces stay Core-only (B-103 / BETA-S04)."""

from __future__ import annotations

import argparse
import socket
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from allthecontext import cli
from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from fastapi.testclient import TestClient

ACTIVE_EDGE_PATHS = (
    ("GET", "/v1/admin/edge"),
    ("POST", "/v1/admin/edge/prepare"),
    ("POST", "/v1/admin/edge/deployment-env"),
    ("POST", "/v1/admin/edge/connect"),
    ("POST", "/v1/admin/edge/sync"),
    ("POST", "/v1/admin/edge/secure-storage"),
    ("POST", "/v1/admin/edge/owner-link"),
    ("GET", "/v1/admin/edge/clients"),
    ("POST", "/v1/admin/edge/clients/example/approve"),
    ("DELETE", "/v1/admin/edge/clients/example"),
    ("POST", "/v1/admin/edge/decommission"),
    ("POST", "/v1/admin/edge/forget"),
)

FORBIDDEN_CLI_COMMANDS = ("sync", "serve-relay", "prepare-edge", "connect-edge")


def test_cli_parser_exposes_no_ordinary_edge_or_relay_operation() -> None:
    parser = cli.build_parser()
    help_text = parser.format_help()
    # argparse lists top-level commands as "{cmd1,cmd2,...}" in the usage line.
    usage_line = help_text.splitlines()[0]
    for command in FORBIDDEN_CLI_COMMANDS:
        assert f",{command}," not in usage_line
        assert not usage_line.startswith(f"{{ {command},")
        assert f"{{{command}," not in usage_line
        assert f",{command}}}" not in usage_line
        with pytest.raises(SystemExit):
            parser.parse_args([command])

    legacy = parser.parse_args(["legacy-edge", "status", "--data-dir", "unused"])
    assert legacy.handler is cli._cmd_legacy_edge_status
    assert "legacy-edge" in help_text
    assert "Isolated residual Edge cleanup" in help_text


def test_cli_legacy_edge_status_is_local_only(tmp_path: Path, monkeypatch: Any) -> None:
    opened: list[str] = []

    class ForbiddenClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            raise AssertionError("legacy status must not open HTTP clients")

    monkeypatch.setattr(httpx, "Client", ForbiddenClient)
    monkeypatch.setattr(
        "allthecontext.edge_connection.httpx.Client",
        ForbiddenClient,
        raising=False,
    )

    def track_create_connection(*args: Any, **kwargs: Any) -> Any:
        opened.append(str(args) + str(kwargs))
        raise AssertionError("legacy status must not open network sockets")

    monkeypatch.setattr(socket, "create_connection", track_create_connection)
    config = CoreConfig.in_directory(tmp_path)
    config.prepare()
    CoreService(config).store.migrate()
    cli._cmd_legacy_edge_status(argparse.Namespace(data_dir=str(tmp_path)))
    assert opened == []


def test_cli_legacy_edge_decommission_refuses_without_residual_state(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path)
    config.prepare()
    CoreService(config).store.migrate()
    with pytest.raises(RuntimeError, match="No residual paired Edge"):
        cli._cmd_legacy_edge_decommission(argparse.Namespace(data_dir=str(tmp_path)))


def test_package_project_scripts_and_web_assets_exclude_edge_operation_surfaces() -> None:
    root = Path(__file__).resolve().parents[2]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert 'atc = "allthecontext.cli:main"' in pyproject
    assert "atc-core" in pyproject
    assert "atc-desktop" in pyproject
    assert "atc-relay" not in pyproject
    assert "serve-relay" not in pyproject
    assert "edge_main" not in pyproject

    web_root = root / "packages" / "allthecontext" / "src" / "allthecontext" / "web"
    asset_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(web_root.rglob("*"))
        if path.is_file()
    )
    for needle in (
        "/admin/edge",
        "/v1/admin/edge",
        "edge/prepare",
        "edge/connect",
        "edge/sync",
        "Set up Edge",
        "Always available via Edge",
    ):
        assert needle not in asset_text, needle


def test_core_process_never_starts_edge_sync_worker(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    before = {thread.name for thread in threading.enumerate()}
    with TestClient(create_app(config)) as client:
        assert client.get("/health").status_code == 200
        names = {thread.name for thread in threading.enumerate()} - before
        assert "all-the-context-edge-sync" not in names
        assert not any("edge-sync" in name for name in names)
        assert getattr(client.app.state.legacy_edge_sync, "_thread", None) is None


def test_ordinary_core_mutations_do_not_open_outbound_network(
    tmp_path: Path, monkeypatch: Any
) -> None:
    network_calls: list[str] = []

    class ForbiddenClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            network_calls.append(f"client:{args}:{kwargs}")
            raise AssertionError("ordinary Core mutation must not open HTTP clients")

        def __enter__(self) -> ForbiddenClient:
            return self

        def __exit__(self, *args: Any) -> None:
            del args
            return None

    monkeypatch.setattr(httpx, "Client", ForbiddenClient)

    original_create_connection = socket.create_connection

    def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in {"127.0.0.1", "localhost", "::1"}:
            network_calls.append(f"socket:{address}")
            raise AssertionError(f"unexpected outbound socket to {address}")
        return original_create_connection(address, *args, **kwargs)

    monkeypatch.setattr(socket, "create_connection", guarded_create_connection)

    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    app = create_app(config)

    with TestClient(app) as client:
        begin = client.post(
            "/v1/ingestion/begin",
            json={
                "mode": "model_assisted_bootstrap",
                "accessible_sources": ["current conversation"],
                "unavailable_sources": [],
                "idempotency_key": "core-only-session",
            },
        )
        assert begin.status_code == 200
        batch = client.post(
            "/v1/ingestion/batch",
            json={
                "session_id": begin.json()["session_id"],
                "idempotency_key": "core-only-batch",
                "candidates": [
                    {
                        "kind": "interaction_preference",
                        "content": "Prefer short answers",
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
        corrected = client.post(
            f"/v1/admin/records/{record_id}/correct",
            json={"content": "Prefer concise answers", "reason": "clarity"},
        )
        assert corrected.status_code == 200
        deleted = client.post(f"/v1/admin/records/{record_id}/delete", json={})
        assert deleted.status_code == 200
        restored = client.post(f"/v1/admin/records/{record_id}/restore", json={})
        assert restored.status_code == 200

    assert network_calls == []
    assert getattr(app.state.legacy_edge_sync, "_thread", None) is None


def test_legacy_cleanup_cannot_create_authority_or_connect_by_default(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        status = client.get("/v1/admin/legacy-edge").json()
        assert status["configured"] is False
        assert status["active_operation_available"] is False
        refused = client.post("/v1/admin/legacy-edge/decommission")
        assert refused.status_code == 409
        assert "second authority" in refused.json()["detail"]
        assert client.post(
            "/v1/admin/legacy-edge/forget",
            json={"confirmation": "DELETE HOSTED EDGE"},
        ).status_code == 200


def test_integrations_surface_has_no_hosted_edge_remote_pairing(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=False)
    with TestClient(create_app(config)) as client:
        integrations = client.get("/v1/admin/integrations").json()
        assert integrations["mobile"]["secure_remote_pairing_available"] is False
        assert integrations["mobile"]["mode"] == "direct_core"
        assert "Edge" not in integrations["mobile"]["detail"]
        assert "remote" not in integrations
