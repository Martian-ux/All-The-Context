from __future__ import annotations

from pathlib import Path

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from fastapi.testclient import TestClient


def test_admin_integrity_review_and_irreversible_purge_contract(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with TestClient(create_app(config)) as client:
        setup = client.post("/v1/setup", json={"name": "Owner", "scopes": []})
        owner_headers = {"Authorization": f"Bearer {setup.json()['token']}"}
        candidate_ids: list[str] = []
        for content in ("red", "blue"):
            proposed = client.post(
                "/v1/ingestion/propose",
                headers=owner_headers,
                json={
                    "kind": "fact",
                    "content": content,
                    "entity_key": "person:1",
                    "attribute_key": "color",
                },
            )
            assert proposed.status_code == 200
            candidate_ids.append(proposed.json()["id"])
        record_ids = []
        for candidate_id in candidate_ids:
            approved = client.post(
                f"/v1/admin/candidates/{candidate_id}/approve",
                headers=owner_headers,
                json={},
            )
            assert approved.status_code == 200
            record_ids.append(approved.json()["id"])

        groups = client.get("/v1/admin/integrity-groups", headers=owner_headers)
        assert groups.status_code == 200
        assert groups.json()["items"][0]["group_type"] == "conflict"

        reader = client.post(
            "/v1/admin/clients",
            headers=owner_headers,
            json={"name": "Reader", "scopes": ["context:read"]},
        ).json()
        reader_headers = {"Authorization": f"Bearer {reader['token']}"}
        payload = {
            "target_type": "record",
            "target_id": record_ids[0],
            "confirmation": f"PURGE RECORD {record_ids[0]}",
            "compact": False,
        }
        assert (
            client.post("/v1/admin/purge", headers=reader_headers, json=payload).status_code == 403
        )
        wrong = client.post(
            "/v1/admin/purge",
            headers=owner_headers,
            json={**payload, "confirmation": "PURGE RECORD wrong"},
        )
        assert wrong.status_code == 422
        purged = client.post("/v1/admin/purge", headers=owner_headers, json=payload)
        assert purged.status_code == 200
        assert purged.json()["phase"] == "compaction_pending"
        assert client.get(f"/v1/context/{record_ids[0]}", headers=owner_headers).status_code == 404
        jobs = client.get("/v1/admin/purge-jobs", headers=owner_headers).json()["items"]
        assert jobs[0]["target_id"] == record_ids[0]
