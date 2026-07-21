from __future__ import annotations

from pathlib import Path

from allthecontext.relay.app import create_app
from allthecontext.relay.service import ClientIdentity, RelayService, SQLiteRelayStore
from allthecontext.replication import EventType, build_event, calculate_payload_hash, sign_event
from fastapi.testclient import TestClient

SECRET = b"relay-test-secret-with-at-least-32-bytes"
REPLICATION_TOKEN = "replication-token"
CLIENT_TOKEN = "client-token"


def make_event(sequence: int, record_id: str = "record-1") -> dict[str, object]:
    content = "The project uses a portable local Core"
    payload = {
        "id": record_id,
        "kind": "project_decision",
        "content": content,
        "scope": ["project"],
        "provenance": {"source_record_id": "source-1"},
        "source_service": "bootstrap",
        "confidence": 1.0,
        "sensitivity": "private",
        "availability": "always_available",
        "allowed_clients": ["client-a"],
        "denied_clients": [],
        "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_until": None,
        "version": 1,
        "supersedes": None,
        "approval_status": "approved",
        "content_hash": calculate_payload_hash(content),
        "updated_at": "2026-07-21T00:00:00+00:00",
    }
    signed = sign_event(
        build_event(
            vault_id="vault-1",
            sequence=sequence,
            event_type=EventType.RECORD_UPSERTED,
            record_id=record_id,
            payload=payload,  # type: ignore[arg-type]
            event_id=f"event-{sequence}",
            created_at="2026-07-21T00:00:00+00:00",
        ),
        SECRET,
    )
    return signed.wire_mapping()  # type: ignore[return-value]


def test_relay_http_replication_retrieval_and_proposal_queue(tmp_path: Path) -> None:
    service = RelayService(SQLiteRelayStore(tmp_path / "relay.sqlite3"), SECRET)
    identity = ClientIdentity(
        client_id="client-a",
        vault_id="vault-1",
        permissions=frozenset({"context:read", "proposal:write"}),
        context_scopes=frozenset({"project"}),
    )
    app = create_app(
        service,
        replication_bearer_token=REPLICATION_TOKEN,
        client_tokens={CLIENT_TOKEN: identity},
        close_service_on_shutdown=False,
    )
    with TestClient(app) as client:
        assert client.get("/healthz").json() == {
            "status": "ok",
            "component": "relay",
            "authority": "core",
        }
        assert client.post("/v1/replication/events", json=make_event(1)).status_code == 401
        applied = client.post(
            "/v1/replication/events",
            json=make_event(1),
            headers={"Authorization": f"Bearer {REPLICATION_TOKEN}"},
        )
        assert applied.status_code == 200
        assert not applied.json()["replayed"]

        search = client.get(
            "/v1/context/search",
            params={"query": "portable Core"},
            headers={"Authorization": f"Bearer {CLIENT_TOKEN}"},
        )
        assert search.status_code == 200
        assert search.json()["items"][0]["id"] == "record-1"
        status_response = client.get(
            "/v1/context/status", headers={"Authorization": f"Bearer {CLIENT_TOKEN}"}
        )
        assert status_response.json()["last_applied_sequence"] == 1
        assert not status_response.json()["relay_writable"]

        proposed = client.post(
            "/v1/proposals",
            json={
                "idempotency_key": "proposal-request-1",
                "kind": "preference",
                "content": "Prefer examples",
                "scope": ["project"],
                "availability": "core_available",
            },
            headers={"Authorization": f"Bearer {CLIENT_TOKEN}"},
        )
        assert proposed.status_code == 202
        assert not proposed.json()["canonical"]

        queued = client.get(
            "/v1/replication/proposals",
            params={"vault_id": "vault-1"},
            headers={"Authorization": f"Bearer {REPLICATION_TOKEN}"},
        )
        assert queued.status_code == 200
        assert queued.json()["count"] == 1
        assert not queued.json()["canonical"]

        context_error = client.post(
            "/v1/ingestion/error",
            json={
                "record_id": "record-1",
                "content": "This decision is stale",
                "evidence": "Use the corrected architecture",
            },
            headers={"Authorization": f"Bearer {CLIENT_TOKEN}"},
        )
        assert context_error.status_code == 202
        assert not context_error.json()["canonical"]
        assert context_error.json()["proposal"]["proposal"]["kind"] == "context_error"
    service.close()


def test_relay_http_reports_sequence_gap_without_advancing(tmp_path: Path) -> None:
    service = RelayService(SQLiteRelayStore(tmp_path / "relay.sqlite3"), SECRET)
    app = create_app(
        service,
        replication_bearer_token=REPLICATION_TOKEN,
        client_tokens={},
        close_service_on_shutdown=False,
    )
    with TestClient(app) as client:
        response = client.post(
            "/v1/replication/events",
            json=make_event(2),
            headers={"Authorization": f"Bearer {REPLICATION_TOKEN}"},
        )
        assert response.status_code == 409
        assert response.json()["detail"]["expected_sequence"] == 1
        assert service.store.checkpoint("vault-1") == 0
    service.close()
