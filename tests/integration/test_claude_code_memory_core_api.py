"""Focused HTTP contract tests for the Core-only Claude Code memory routes."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.security import WITNESS_EXPLICIT_USER_STATEMENT
from fastapi.testclient import TestClient


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_client(
    client: TestClient,
    owner_headers: dict[str, str],
    *,
    name: str,
    scopes: list[str],
) -> tuple[str, dict[str, str]]:
    response = client.post(
        "/v1/admin/clients",
        headers=owner_headers,
        json={"name": name, "scopes": scopes},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    return str(payload["client"]["id"]), _bearer(str(payload["token"]))


def test_claude_code_memory_routes_require_separate_write_principal_and_are_core_only(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with TestClient(create_app(config)) as client:
        setup = client.post("/v1/setup", json={"name": "Owner", "scopes": []})
        assert setup.status_code == 200, setup.text
        owner_headers = _bearer(str(setup.json()["token"]))
        _read_id, read_headers = _create_client(
            client,
            owner_headers,
            name="Claude Code",
            scopes=["context:read"],
        )
        write_id, write_headers = _create_client(
            client,
            owner_headers,
            name="Claude Code user memory",
            scopes=["context:propose", WITNESS_EXPLICIT_USER_STATEMENT],
        )
        _missing_witness_id, missing_witness_headers = _create_client(
            client,
            owner_headers,
            name="Claude Code without witness",
            scopes=["context:propose"],
        )

        listed = client.get("/v1/admin/clients", headers=owner_headers)
        assert listed.status_code == 200, listed.text
        by_id = {item["id"]: item for item in listed.json()["items"]}
        assert by_id[_read_id]["scopes"] == ["context:read"]
        assert set(by_id[write_id]["scopes"]) == {
            "context:propose",
            WITNESS_EXPLICIT_USER_STATEMENT,
        }

        key = str(uuid4())
        denied = client.post(
            "/v1/claude-code/memory/remember",
            headers=read_headers,
            json={"content": "Prefer concise answers.", "idempotency_key": key},
        )
        assert denied.status_code == 403

        no_witness = client.post(
            "/v1/claude-code/memory/remember",
            headers=missing_witness_headers,
            json={"content": "Prefer concise answers.", "idempotency_key": str(uuid4())},
        )
        assert no_witness.status_code == 403

        rejected_authority = client.post(
            "/v1/claude-code/memory/remember",
            headers=write_headers,
            json={
                "content": "Prefer concise answers.",
                "idempotency_key": str(uuid4()),
                "explicit_user_statement": False,
                "sensitivity": "highly_sensitive",
                "availability": "always_available",
                "allowed_clients": [write_id],
                "disposition": "applied",
            },
        )
        assert rejected_authority.status_code == 422

        remembered = client.post(
            "/v1/claude-code/memory/remember",
            headers=write_headers,
            json={
                "kind": "interaction_preference",
                "content": "Prefer concise answers.",
                "idempotency_key": key,
            },
        )
        assert remembered.status_code == 200, remembered.text
        remembered_payload = remembered.json()
        assert remembered_payload["disposition"] == "applied"
        assert remembered_payload["observation_origin"] == "ongoing_client"
        assert remembered_payload["source_service"] == "claude_code"
        assert remembered_payload["source_type"] == "direct_user_statement"
        assert remembered_payload["allowed_clients"] == []
        assert remembered_payload["denied_clients"] == []
        record_id = remembered_payload["record_id"]

        retried = client.post(
            "/v1/claude-code/memory/remember",
            headers=write_headers,
            json={
                "kind": "interaction_preference",
                "content": "Prefer concise answers.",
                "idempotency_key": key,
            },
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["id"] == remembered_payload["id"]

        corrected = client.post(
            "/v1/claude-code/memory/correct",
            headers=write_headers,
            json={
                "record_id": record_id,
                "content": "Prefer concise answers with evidence.",
                "idempotency_key": str(uuid4()),
            },
        )
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["disposition"] == "applied"
        assert corrected.json()["record_id"] == record_id
        assert client.get(f"/v1/context/{record_id}", headers=read_headers).json()["content"] == (
            "Prefer concise answers with evidence."
        )

        forgotten = client.post(
            "/v1/claude-code/memory/forget",
            headers=write_headers,
            json={"record_id": record_id, "idempotency_key": str(uuid4())},
        )
        assert forgotten.status_code == 200, forgotten.text
        assert forgotten.json()["disposition"] == "applied"
        assert forgotten.json()["record_id"] == record_id
        assert client.get(f"/v1/context/{record_id}", headers=read_headers).status_code == 404

        acl_target = client.post(
            "/v1/ingestion/propose",
            headers=owner_headers,
            json={
                "kind": "fact",
                "content": "Only another client may access this target.",
                "allowed_clients": [_missing_witness_id],
                "explicit_user_statement": True,
            },
        )
        assert acl_target.status_code == 200, acl_target.text
        acl_record_id = acl_target.json()["record_id"]
        denied_forget = client.post(
            "/v1/claude-code/memory/forget",
            headers=write_headers,
            json={"record_id": acl_record_id, "idempotency_key": str(uuid4())},
        )
        assert denied_forget.status_code == 404
        assert client.app.state.core.store.get_record(acl_record_id).content == (
            "Only another client may access this target."
        )

        revoked = client.post(
            f"/v1/admin/clients/{write_id}/revoke",
            headers=owner_headers,
        )
        assert revoked.status_code == 200, revoked.text
        revoked_write = client.post(
            "/v1/claude-code/memory/remember",
            headers=write_headers,
            json={"content": "This must not be accepted.", "idempotency_key": str(uuid4())},
        )
        assert revoked_write.status_code == 401

        assert getattr(client.app.state.legacy_edge_sync, "_thread", None) is None
