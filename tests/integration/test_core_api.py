from __future__ import annotations

from pathlib import Path

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
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
        assert client.get("/v1/context/status").json()["counts"]["approved_records"] == 1


def test_setup_auth_and_client_revocation(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with TestClient(create_app(config)) as client:
        assert client.get("/v1/context/status").status_code == 401
        setup = client.post("/v1/setup", json={"name": "Dashboard", "scopes": []})
        assert setup.status_code == 200
        token = setup.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/v1/context/status", headers=headers).status_code == 200
        assert client.post("/v1/setup", json={"name": "Other", "scopes": []}).status_code == 409


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
