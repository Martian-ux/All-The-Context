from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.service import CoreService
from allthecontext.edge_claim import generate_claim
from allthecontext.edge_connection import EdgeConnectionStore, EdgeSyncManager
from allthecontext.models import ApprovalRequest, Availability, CandidateInput
from allthecontext.relay.forwarding import EdgeForwardingBroker, ForwardingError
from allthecontext.relay.service import SQLiteRelayStore


def _broker(path: Path) -> tuple[EdgeForwardingBroker, str]:
    store = SQLiteRelayStore(path)
    store.close()
    claim, private = generate_claim("vault-test", "0" * 64)
    return EdgeForwardingBroker(path, claim.encryption_public_key), private.encryption_private_key


def _footprint(path: Path) -> bytes:
    return b"".join(
        candidate.read_bytes()
        for candidate in (
            path,
            path.with_name(f"{path.name}-wal"),
            path.with_name(f"{path.name}-shm"),
        )
        if candidate.exists()
    )


def test_forwarding_claims_are_bounded_one_time_cancelable_and_restart_safe(
    tmp_path: Path,
) -> None:
    database = tmp_path / "edge.sqlite3"
    broker, _ = _broker(database)
    query_sentinel = "QUERY-SENTINEL-8f63c7f0"
    response_sentinel = "RESPONSE-SENTINEL-3d99e40a"
    request_id = broker.enqueue(
        client_id="edge:claude",
        client_scopes=["context:read"],
        operation="search_context",
        payload={"query": query_sentinel},
    )
    claim = broker.claim()[0]
    assert claim["request_id"] == request_id
    assert broker.claim() == []

    # A different process can claim, but private results remain in the broker
    # process serving the waiting MCP request and are never written to SQLite.
    broker.answer(
        request_id,
        str(claim["claim_token"]),
        {"items": [{"content": response_sentinel}]},
    )
    with pytest.raises(ForwardingError):
        broker.answer(request_id, str(claim["claim_token"]), {"items": []})
    assert query_sentinel.encode() not in _footprint(database)
    assert response_sentinel.encode() not in _footprint(database)
    result = broker.wait(request_id)
    assert result.state == "available"
    assert result.response is not None
    assert result.response["items"][0]["content"] == response_sentinel
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM edge_forward_requests").fetchone()[0] == 0
    assert query_sentinel.encode() not in _footprint(database)
    assert response_sentinel.encode() not in _footprint(database)

    cancelled = broker.enqueue(
        client_id="edge:claude",
        client_scopes=["context:read"],
        operation="get_context_item",
        payload={"record_id": "missing"},
    )
    cancelled_claim = broker.claim()[0]
    assert broker.cancel(cancelled)
    with pytest.raises(ForwardingError):
        broker.answer(cancelled, str(cancelled_claim["claim_token"]), {"found": False})


def test_forwarding_broker_releases_sqlite_handles_after_each_operation(tmp_path: Path) -> None:
    database = tmp_path / "edge.sqlite3"
    broker, _ = _broker(database)
    assert broker.status() == {"queued": 0, "claimed": 0}

    moved = tmp_path / "moved.sqlite3"
    database.replace(moved)
    moved.replace(database)


def test_forwarding_expiry_rate_concurrency_and_decommission_cleanup(tmp_path: Path) -> None:
    broker, _ = _broker(tmp_path / "edge.sqlite3")
    first = broker.enqueue(
        client_id="edge:chatgpt",
        client_scopes=["context:read"],
        operation="search_context",
        payload={},
        ttl_seconds=2,
    )
    broker.enqueue(
        client_id="edge:chatgpt",
        client_scopes=["context:read"],
        operation="search_context",
        payload={},
    )
    with pytest.raises(ForwardingError, match="busy"):
        broker.enqueue(
            client_id="edge:chatgpt",
            client_scopes=["context:read"],
            operation="search_context",
            payload={},
        )
    assert broker.cancel(first)
    broker.purge()
    assert broker.status() == {"queued": 0, "claimed": 0}


def test_core_executes_only_authorized_core_available_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    core = CoreService(CoreConfig.in_directory(tmp_path / "core"))
    connections = EdgeConnectionStore(core.config)
    material = connections.prepare(core.store.vault_id())
    assert material.forwarding_public_key is not None
    broker = EdgeForwardingBroker(tmp_path / "forward.sqlite3", material.forwarding_public_key)
    relay = SQLiteRelayStore(tmp_path / "forward.sqlite3")
    relay.close()
    manager = EdgeSyncManager(connections, core.store)
    records = {}
    for availability in (Availability.ALWAYS, Availability.CORE, Availability.LOCAL):
        candidate = core.store.add_candidate(
            CandidateInput(
                kind="project",
                content=f"{availability.value} atlas",
                scopes=["project:atlas"],
                availability=availability,
                allowed_clients=["edge:allowed"],
                denied_clients=["edge:denied"],
                evidence="private extraction evidence must not leave Core",
                idempotency_key=f"private-retry-{availability.value}",
            )
        )
        records[availability] = core.store.approve_candidate(
            candidate.id, ApprovalRequest(), actor="test"
        )

    core.store.approve_remote_edge_client(
        "edge:allowed", name="Approved remote client", scopes=["context:read"]
    )

    def envelope(client_id: str, *, payload: dict[str, object] | None = None) -> dict[str, object]:
        broker.enqueue(
            client_id=client_id,
            # Deliberately attacker-controlled; Core ignores this assertion.
            client_scopes=["*", "admin", "context:read"],
            operation="search_context",
            payload=payload or {"query": "atlas", "limit": 20},
        )
        return broker.claim()[0]

    def execute(client_id: str, *, payload: dict[str, object] | None = None) -> dict[str, object]:
        claimed = envelope(client_id, payload=payload)
        result = manager._execute_forward_request(claimed)
        broker.cancel(str(claimed["request_id"]))
        return result

    allowed = execute("edge:allowed")
    assert [item["id"] for item in allowed["items"]] == [records[Availability.CORE].id]
    forwarded = allowed["items"][0]
    assert forwarded["scope"] == ["project:atlas"]
    assert {
        "allowed_clients",
        "denied_clients",
        "evidence",
        "idempotency_key",
        "source_id",
        "structured_value",
        "entity_key",
        "attribute_key",
    }.isdisjoint(forwarded)
    assert "private extraction evidence" not in repr(forwarded)
    forged = execute("edge:forged-admin")
    assert forged == {"state": "unavailable"}
    local_probe = execute(
        "edge:allowed",
        payload={
            "query": "atlas",
            "limit": 20,
            "availability": ["local_only", "core_available"],
        },
    )
    assert [item["id"] for item in local_probe["items"]] == [records[Availability.CORE].id]
    core.store.revoke_remote_edge_client("edge:allowed")
    assert execute("edge:allowed") == {"state": "unavailable"}


def test_wait_timeout_cancels_request(tmp_path: Path) -> None:
    database = tmp_path / "edge.sqlite3"
    broker, _ = _broker(database)
    sentinel = "TIMEOUT-QUERY-SENTINEL-e3440f"
    request_id = broker.enqueue(
        client_id="edge:claude",
        client_scopes=["context:read"],
        operation="search_context",
        payload={"query": sentinel},
    )
    assert broker.wait(request_id, timeout_seconds=0.1).state == "timeout"
    assert broker.claim() == []
    restarted = EdgeForwardingBroker(database, broker.request_encryption_public_key)
    assert restarted.wait(request_id, timeout_seconds=0.1).state == "unavailable"
    assert sentinel.encode() not in _footprint(database)


def test_response_is_memory_only_and_safe_edge_restart_becomes_unavailable(tmp_path: Path) -> None:
    database = tmp_path / "edge.sqlite3"
    broker, _ = _broker(database)
    query = "RESTART-QUERY-SENTINEL-fb27b9"
    response = "RESTART-RESPONSE-SENTINEL-0246ce"
    request_id = broker.enqueue(
        client_id="edge:claude",
        client_scopes=["context:read"],
        operation="search_context",
        payload={"query": query},
    )
    claim = broker.claim()[0]
    broker.answer(request_id, str(claim["claim_token"]), {"items": [{"content": response}]})
    assert query.encode() not in _footprint(database)
    assert response.encode() not in _footprint(database)

    restarted = EdgeForwardingBroker(database, broker.request_encryption_public_key)
    assert restarted.wait(request_id, timeout_seconds=0.1).state == "unavailable"
    assert query.encode() not in _footprint(database)
    assert response.encode() not in _footprint(database)


def test_forwarding_response_bound_is_enforced_before_memory_handoff(tmp_path: Path) -> None:
    broker, _ = _broker(tmp_path / "edge.sqlite3")
    request_id = broker.enqueue(
        client_id="edge:claude",
        client_scopes=["context:read"],
        operation="search_context",
        payload={"query": "bounded"},
    )
    claim = broker.claim()[0]
    with pytest.raises(ForwardingError, match="too large"):
        broker.answer(
            request_id,
            str(claim["claim_token"]),
            {"items": [{"content": "x" * (65 * 1024)}]},
        )
