from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.service import CoreService
from allthecontext.edge_claim import generate_claim
from allthecontext.edge_connection import EdgeConnectionStore, EdgeSyncManager
from allthecontext.models import ApprovalRequest, Availability, CandidateInput, Sensitivity
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
        "edge:allowed",
        name="Approved remote client",
        scopes=["context:read"],
        context_scopes=["*"],
    )

    sensitive_observation = core.store.add_candidate(
        CandidateInput(
            kind="personal_context",
            content="My mortgage is with a bank.",
            availability=Availability.CORE,
            explicit_user_statement=True,
            idempotency_key="sensitive-mortgage-forwarding-boundary",
        )
    )
    assert sensitive_observation.record_id is not None
    sensitive_record = core.store.get_record(sensitive_observation.record_id)
    assert sensitive_record.sensitivity == Sensitivity.SENSITIVE
    assert sensitive_record.availability == Availability.LOCAL

    def envelope(
        client_id: str,
        *,
        operation: str = "search_context",
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        broker.enqueue(
            client_id=client_id,
            # Deliberately attacker-controlled; Core ignores this assertion.
            client_scopes=["*", "admin", "context:read"],
            operation=operation,
            payload=payload or {"query": "atlas", "limit": 20},
        )
        return broker.claim()[0]

    def execute(
        client_id: str,
        *,
        operation: str = "search_context",
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        claimed = envelope(client_id, operation=operation, payload=payload)
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
    wildcard_probe = execute(
        "edge:allowed",
        payload={"query": "atlas", "scopes": ["*"], "limit": 20},
    )
    assert [item["id"] for item in wildcard_probe["items"]] == [records[Availability.CORE].id]
    fetched = execute(
        "edge:allowed",
        operation="get_context_item",
        payload={"record_id": records[Availability.CORE].id},
    )
    assert fetched["found"] is True
    assert fetched["item"]["id"] == records[Availability.CORE].id
    all_context = execute(
        "edge:allowed",
        payload={"query": "", "limit": 20},
    )
    assert sensitive_record.id not in {item["id"] for item in all_context["items"]}
    sensitive_fetch = execute(
        "edge:allowed",
        operation="get_context_item",
        payload={"record_id": sensitive_record.id},
    )
    assert sensitive_fetch == {"state": "available", "found": False}

    scoped_atlas_candidate = core.store.add_candidate(
        CandidateInput(
            kind="project",
            content="core scoped atlas",
            scopes=["project:atlas"],
            availability=Availability.CORE,
            allowed_clients=["edge:scoped", "edge:empty"],
            idempotency_key="private-retry-scoped-atlas",
        )
    )
    scoped_atlas = core.store.approve_candidate(
        scoped_atlas_candidate.id, ApprovalRequest(), actor="test"
    )
    secret_candidate = core.store.add_candidate(
        CandidateInput(
            kind="project",
            content="core secret",
            scopes=["project:secret"],
            availability=Availability.CORE,
            allowed_clients=["edge:scoped"],
            idempotency_key="private-retry-secret",
        )
    )
    secret = core.store.approve_candidate(secret_candidate.id, ApprovalRequest(), actor="test")
    core.store.approve_remote_edge_client(
        "edge:scoped",
        name="Scoped remote client",
        scopes=["context:read"],
        context_scopes=["project:atlas"],
    )
    scoped_search = execute(
        "edge:scoped",
        payload={"query": "core", "limit": 20},
    )
    assert [item["id"] for item in scoped_search["items"]] == [scoped_atlas.id]
    assert scoped_search["total"] == 1
    denied_scope_search = execute(
        "edge:scoped",
        payload={"query": "core", "scopes": ["project:secret"], "limit": 20},
    )
    assert denied_scope_search == {"state": "available", "items": [], "total": 0}
    scoped_bootstrap = execute(
        "edge:scoped",
        operation="bootstrap_context",
        payload={
            "task_description": "core",
            "requested_scopes": [],
            "character_budget": 12_000,
        },
    )
    scoped_bootstrap_ids = [item["id"] for item in scoped_bootstrap["items"]]
    assert scoped_atlas.id in scoped_bootstrap_ids
    assert secret.id not in scoped_bootstrap_ids
    assert scoped_bootstrap["used_chars"] == sum(
        len(item["content"]) + 64 for item in scoped_bootstrap["items"]
    )
    denied_scope_bootstrap = execute(
        "edge:scoped",
        operation="bootstrap_context",
        payload={
            "task_description": "core",
            "requested_scopes": ["project:secret"],
            "character_budget": 12_000,
        },
    )
    assert denied_scope_bootstrap == {
        "state": "available",
        "items": [],
        "context_mode": "core_via_edge",
        "omitted_scopes": ["project:secret"],
        "used_chars": 0,
    }
    scoped_fetch_allowed = execute(
        "edge:scoped",
        operation="get_context_item",
        payload={"record_id": scoped_atlas.id},
    )
    assert scoped_fetch_allowed["found"] is True
    assert scoped_fetch_allowed["item"]["id"] == scoped_atlas.id
    scoped_fetch = execute(
        "edge:scoped",
        operation="get_context_item",
        payload={"record_id": secret.id},
    )
    assert scoped_fetch == {"state": "available", "found": False}

    unscoped_candidate = core.store.add_candidate(
        CandidateInput(
            kind="project",
            content="core unscoped",
            scopes=[],
            availability=Availability.CORE,
            allowed_clients=["edge:empty"],
            idempotency_key="private-retry-unscoped",
        )
    )
    unscoped = core.store.approve_candidate(unscoped_candidate.id, ApprovalRequest(), actor="test")
    core.store.approve_remote_edge_client(
        "edge:empty",
        name="Empty-scope remote client",
        scopes=["context:read"],
        context_scopes=[],
    )
    empty_scope_search = execute(
        "edge:empty",
        payload={"query": "core", "limit": 20},
    )
    assert [item["id"] for item in empty_scope_search["items"]] == [unscoped.id]
    assert empty_scope_search["total"] == 1
    empty_scope_fetch_denied = execute(
        "edge:empty",
        operation="get_context_item",
        payload={"record_id": scoped_atlas.id},
    )
    assert empty_scope_fetch_denied == {"state": "available", "found": False}
    empty_scope_fetch_unscoped = execute(
        "edge:empty",
        operation="get_context_item",
        payload={"record_id": unscoped.id},
    )
    assert empty_scope_fetch_unscoped["found"] is True
    assert empty_scope_fetch_unscoped["item"]["id"] == unscoped.id

    core.store.revoke_remote_edge_client("edge:allowed")
    assert execute("edge:allowed") == {"state": "unavailable"}


@pytest.mark.parametrize(
    ("record", "approved", "expected"),
    [
        ({"scopes": ["project:atlas"]}, frozenset(), False),
        ({"scopes": ["project:atlas"]}, frozenset({"*"}), True),
        ({"scopes": ["project:atlas"]}, frozenset({"project:atlas"}), True),
        ({"scopes": ["project:secret"]}, frozenset({"project:atlas"}), False),
        ({"scopes": []}, frozenset(), True),
        ({"scopes": "project:atlas"}, frozenset({"project:atlas"}), False),
    ],
)
def test_forwarding_record_scope_matching_matches_relay_semantics(
    record: dict[str, object],
    approved: frozenset[str],
    expected: bool,
) -> None:
    assert EdgeSyncManager._record_matches_approved_context_scopes(record, approved) is expected


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
