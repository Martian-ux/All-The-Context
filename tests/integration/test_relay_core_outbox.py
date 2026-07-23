from __future__ import annotations

import hashlib
from pathlib import Path

from allthecontext.models import ApprovalRequest, Availability, CandidateInput
from allthecontext.relay.app import create_app
from allthecontext.relay.service import ClientIdentity, RelayService, SQLiteRelayStore
from allthecontext.replication import (
    MAX_EDGE_REPLICATION_REQUEST_BYTES,
    HttpRelayTransport,
    ReplicationDispatcher,
    ReplicationEvent,
    calculate_payload_hash,
    canonical_json,
    sign_event,
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
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
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


def test_core_delete_restore_resumes_relay_projection_stream(tmp_path: Path) -> None:
    core = CoreStore(tmp_path / "restore-core.sqlite3")
    vault_id = core.initialize_vault()
    observation = core.add_candidate(
        CandidateInput(
            kind="preference",
            content="Use concise explanations.",
            scopes=["personal"],
            explicit_user_statement=True,
        )
    )
    assert observation.record_id is not None
    record_id = observation.record_id
    core.change_availability(record_id, Availability.ALWAYS)

    relay = RelayService(SQLiteRelayStore(tmp_path / "restore-relay.sqlite3"), SECRET)
    identity = ClientIdentity(
        "client-a",
        vault_id,
        frozenset({"context:read"}),
        frozenset({"personal"}),
    )
    app = create_app(
        relay,
        replication_bearer_token="replication-token",
        client_tokens={"client-token": identity},
        close_service_on_shutdown=False,
    )
    try:
        with TestClient(app) as http_client:
            dispatcher = ReplicationDispatcher(
                core,
                HttpRelayTransport(
                    "http://127.0.0.1",
                    "replication-token",
                    client=http_client,
                ),
                SECRET,
            )
            assert dispatcher.dispatch_pending().delivered == 1
            assert relay.get(identity, record_id) is not None

            core.delete_record(record_id, reason="temporary removal")
            assert dispatcher.dispatch_pending().delivered == 1
            assert relay.get(identity, record_id) is None

            restored = core.restore_record(record_id, reason="undo temporary removal")
            assert restored.version == 4
            assert dispatcher.dispatch_pending().delivered == 1
            assert relay.get(identity, record_id) is not None

            later = core.add_candidate(
                CandidateInput(
                    kind="preference",
                    content="Use descriptive variable names.",
                    scopes=["personal"],
                    explicit_user_statement=True,
                )
            )
            assert later.record_id is not None
            core.change_availability(later.record_id, Availability.ALWAYS)
            assert dispatcher.dispatch_pending().delivered == 1
            assert relay.get(identity, later.record_id) is not None
    finally:
        relay.close()


def test_largest_legal_record_fits_bounded_edge_replication_request(tmp_path: Path) -> None:
    core = CoreStore(tmp_path / "large-core.sqlite3")
    vault_id = core.initialize_vault()
    item = "😀" * 200
    candidate = core.add_candidate(
        CandidateInput(
            kind="project_decision",
            content="😀" * 64_000,
            structured_value={"data": "😀" * 16_380},
            scopes=[item] * 64,
            tags=[item] * 128,
            source_reference="😀" * 2_000,
            source_service="s" * 128,
            source_type="t" * 128,
            allowed_clients=[item] * 256,
            denied_clients=[item] * 256,
            valid_from="2026-07-21T00:00:00+00:00",
            expires_at="2027-07-21T00:00:00+00:00",
            idempotency_key="retry-key-that-must-not-replicate",
        )
    )
    record = core.approve_candidate(
        candidate.id,
        ApprovalRequest(availability=Availability.ALWAYS),
    )
    pending = core.pending_replication_events()
    assert len(pending) == 1
    event = pending[0]
    wire_bytes = canonical_json(
        sign_event(ReplicationEvent.from_mapping(event), SECRET).wire_mapping()
    ).encode("utf-8")
    assert len(wire_bytes) < MAX_EDGE_REPLICATION_REQUEST_BYTES
    assert "retry-key-that-must-not-replicate" not in str(event["payload_json"])

    relay = RelayService(SQLiteRelayStore(tmp_path / "large-edge.sqlite3"), SECRET)
    app = create_app(
        relay,
        replication_bearer_token="replication-token",
        client_tokens={},
        close_service_on_shutdown=False,
    )
    try:
        with TestClient(app) as http_client:
            transport = HttpRelayTransport(
                "http://127.0.0.1",
                "replication-token",
                client=http_client,
            )
            dispatcher = ReplicationDispatcher(core, transport, SECRET)
            assert dispatcher.dispatch_pending().delivered == 1
            assert relay.owner_get(vault_id, record.id) is not None
    finally:
        relay.close()
