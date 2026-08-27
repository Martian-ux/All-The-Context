from __future__ import annotations

from pathlib import Path

from allthecontext.config import CoreConfig
from allthecontext.core.app import create_app
from allthecontext.core.service import CoreService
from allthecontext.models import CandidateInput, ClientCreate
from fastapi.testclient import TestClient


def test_core_bootstrap_is_authenticated_read_only_and_revocation_is_content_free(
    tmp_path: Path,
) -> None:
    config = CoreConfig.in_directory(tmp_path, require_auth=True)
    with CoreService(config) as service:
        principal, token = service.store.create_client(
            ClientCreate(name="Claude Code hook test", scopes=["context:read"])
        )
        candidate = service.store.add_candidate(
            CandidateInput(kind="fact", content="Authorized hook reference")
        )
        approved = service.store.approve_candidate(candidate.id)
        before = service.store.status()

        with TestClient(create_app(config, service=service)) as client:
            headers = {
                "Authorization": f"Bearer {token}",
                "X-ATC-Client-ID": principal.id,
            }
            bootstrap = client.post(
                "/v1/context/bootstrap",
                headers=headers,
                json={"query": "hook reference", "budget_chars": 8_000},
            )
            assert bootstrap.status_code == 200
            assert [item["content"] for item in bootstrap.json()["items"]] == [approved.content]

            assert client.post("/v1/context/bootstrap", json={"query": "hook"}).status_code == 401

            service.store.revoke_client(principal.id)
            revoked = client.post(
                "/v1/context/bootstrap",
                headers=headers,
                json={"query": "hook reference", "budget_chars": 8_000},
            )
            assert revoked.status_code == 401
            assert "items" not in revoked.text
            assert "Authorized hook reference" not in revoked.text

        after = service.store.status()
        assert after["counts"] == before["counts"]
