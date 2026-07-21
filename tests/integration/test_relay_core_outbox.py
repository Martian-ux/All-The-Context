from __future__ import annotations

from pathlib import Path

from allthecontext.relay.app import create_app
from allthecontext.relay.service import ClientIdentity, RelayService, SQLiteRelayStore
from allthecontext.replication import (
    HttpRelayTransport,
    ReplicationDispatcher,
    calculate_payload_hash,
    canonical_json,
)
from allthecontext.storage import CoreStore
from fastapi.testclient import TestClient

SECRET = b"relay-test-secret-with-at-least-32-bytes"


def test_core_outbox_dispatches_and_marks_only_after_relay_acceptance(tmp_path: Path) -> None:
    core = CoreStore(tmp_path / "core.sqlite3")
    vault_id = core.initialize_vault()
    content = "The authoritative Core owns canonical context"
    payload = {
        "id": "record-1",
        "kind": "project_decision",
        "content": content,
        "scopes": ["project"],
        "source_service": "bootstrap",
        "confidence": 1.0,
        "sensitivity": "normal",
        "availability": "always_available",
        "allowed_clients": [],
        "denied_clients": [],
        "valid_from": None,
        "expires_at": None,
        "version": 1,
        "supersedes": None,
        "approval_status": "approved",
        "content_hash": calculate_payload_hash(content),
        "updated_at": "2026-07-21T00:00:00+00:00",
    }
    payload_json = canonical_json(payload)
    with core.transaction() as connection:
        connection.execute(
            "INSERT INTO replication_events"
            "(id,vault_id,sequence,event_type,record_id,payload_json,payload_hash,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                "event-1",
                vault_id,
                1,
                "record_upserted",
                "record-1",
                payload_json,
                calculate_payload_hash(payload),
                "2026-07-21T00:00:00+00:00",
            ),
        )

    relay = RelayService(SQLiteRelayStore(tmp_path / "relay.sqlite3"), SECRET)
    identity = ClientIdentity(
        "client-a", vault_id, frozenset({"context:read"}), frozenset({"project"})
    )
    app = create_app(
        relay,
        replication_bearer_token="replication-token",
        client_tokens={"client-token": identity},
        close_service_on_shutdown=False,
    )
    with TestClient(app) as http_client:
        transport = HttpRelayTransport(
            "http://127.0.0.1",
            "replication-token",
            client=http_client,
        )
        dispatcher = ReplicationDispatcher(core, transport, SECRET)
        result = dispatcher.dispatch_pending()
        assert result.delivered == 1
        assert core.pending_replication_events() == []
        assert relay.get(identity, "record-1") is not None
        assert dispatcher.dispatch_pending().delivered == 0
    relay.close()
