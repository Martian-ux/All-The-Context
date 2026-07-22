from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.service import CoreService
from allthecontext.edge_claim import EdgeClaimStore
from allthecontext.edge_connection import EdgeConnectionStore, EdgeSyncManager
from allthecontext.models import ApprovalRequest, Availability, CandidateInput
from allthecontext.relay.app import create_app as create_edge_app
from allthecontext.relay.forwarding import EdgeForwardingBroker
from allthecontext.relay.oauth import EdgeOAuthProvider, EdgeOAuthStore
from allthecontext.relay.service import RelayService, SQLiteRelayStore
from allthecontext.sync import CoreRelaySync
from fastapi.testclient import TestClient

PUBLIC_URL = "https://edge.example.test"


@contextmanager
def _edge_host(
    database: Path,
    connections: EdgeConnectionStore,
) -> Iterator[tuple[RelayService, TestClient, EdgeForwardingBroker]]:
    material = connections.material()
    assert material is not None
    relay_store = SQLiteRelayStore(database)
    claim_store = None
    if material.claim_bundle is not None:
        active_secret = hashlib.sha256(material.claim_bundle.signing_public_key.encode()).digest()
        active_token = hashlib.sha256(material.claim_bundle.claim_id.encode()).hexdigest()
        claim_store = EdgeClaimStore(database, material.claim_bundle, PUBLIC_URL)
    else:
        active_secret = material.bundle.replication_secret.encode()
        active_token = material.bundle.replication_token
    service = RelayService(relay_store, active_secret)
    oauth_store = EdgeOAuthStore(database)
    provider = EdgeOAuthProvider(oauth_store, PUBLIC_URL)
    forwarding = EdgeForwardingBroker(database, material.forwarding_public_key)
    app = create_edge_app(
        service,
        replication_bearer_token=active_token,
        client_tokens={},
        edge_provider=provider,
        edge_pairing_secret=active_secret,
        owner_secret_hash=material.bundle.owner_secret_hash,
        vault_id=material.bundle.vault_id,
        close_service_on_shutdown=False,
        forwarding_broker=forwarding,
        claim_store=claim_store,
    )
    try:
        with TestClient(app, base_url=PUBLIC_URL) as client:
            yield service, client, forwarding
    finally:
        oauth_store.close()
        service.close()


def test_fresh_and_rolled_back_edge_recover_from_core_event_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    core = CoreService(CoreConfig.in_directory(tmp_path / "core"))
    candidate = core.store.add_candidate(
        CandidateInput(
            kind="interaction_preference",
            content="Prefer evidence-backed completion reports",
            scopes=["general"],
            availability=Availability.ALWAYS,
        )
    )
    record = core.store.approve_candidate(candidate.id, ApprovalRequest(), actor="test")
    connections = EdgeConnectionStore(core.config)
    material = connections.prepare(core.store.vault_id())
    manager = EdgeSyncManager(connections, core.store)

    with _edge_host(tmp_path / "edge-original.sqlite3", connections) as (edge, client, _):
        connections.connect(PUBLIC_URL, client=client)
        material = connections.material()
        assert material is not None
        first = manager.sync_now(http_client=client)
        assert first["state"] == "ready"
        assert first["last_sequence"] == 1
        assert edge.owner_get(material.bundle.vault_id, record.id) is not None

    # The Core outbox marks this event delivered. A replacement Edge still has
    # to receive the full retained log instead of remaining silently empty.
    assert core.store.pending_replication_events() == []
    with _edge_host(tmp_path / "edge-fresh.sqlite3", connections) as (
        fresh,
        fresh_client,
        _,
    ):
        recovered = manager.sync_now(http_client=fresh_client)
        assert recovered["state"] == "ready"
        assert recovered["last_sequence"] == 1
        assert fresh.owner_get(material.bundle.vault_id, record.id) is not None

    corrected = core.store.correct_record(
        record.id,
        content="Prefer concise, evidence-backed completion reports",
        structured_value=None,
        supersedes=None,
        reason="clarified preference",
        actor="test",
    )

    # Seed a simulated restored backup only through sequence 1, then confirm
    # normal synchronization resumes from that checkpoint with sequence 2.
    with _edge_host(tmp_path / "edge-rolled-back.sqlite3", connections) as (
        rolled_back,
        rolled_client,
        _,
    ):
        with CoreRelaySync(
            core.store.database_path,
            PUBLIC_URL,
            material.bundle.replication_secret.encode(),
            material.bundle.replication_token,
            http_client=rolled_client,
        ) as partial:
            seeded = partial.push(
                limit=1,
                vault_id=material.bundle.vault_id,
                after_sequence=0,
            )
        assert seeded == {"delivered": 1, "replayed": 0, "remaining": 1}
        assert rolled_back.store.checkpoint(material.bundle.vault_id) == 1

        resumed = manager.sync_now(http_client=rolled_client)
        assert resumed["state"] == "ready"
        assert resumed["last_sequence"] == 2
        restored = rolled_back.owner_get(material.bundle.vault_id, corrected.id)
        assert restored is not None
        assert restored["content"] == "Prefer concise, evidence-backed completion reports"

        manager.decommission(client=rolled_client)
        assert connections.state() is None
        assert rolled_back.owner_get(material.bundle.vault_id, corrected.id) is None


def test_sync_state_update_cannot_resurrect_connection_after_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    connections = EdgeConnectionStore(CoreConfig.in_directory(tmp_path / "core"))
    connections.prepare("vault-race")
    save_started = threading.Event()
    release_save = threading.Event()
    original_save = connections.save_state

    def blocked_save(state):
        save_started.set()
        assert release_save.wait(timeout=5)
        original_save(state)

    monkeypatch.setattr(connections, "save_state", blocked_save)
    updater = threading.Thread(
        target=lambda: connections.update_sync(success=True, last_sequence=4)
    )
    updater.start()
    assert save_started.wait(timeout=5)

    reset_done = threading.Event()

    def reset_connection() -> None:
        connections.reset()
        reset_done.set()

    resetter = threading.Thread(target=reset_connection)
    resetter.start()
    assert not reset_done.wait(timeout=0.1)
    release_save.set()
    updater.join(timeout=5)
    resetter.join(timeout=5)

    assert not updater.is_alive()
    assert not resetter.is_alive()
    assert reset_done.is_set()
    assert connections.state() is None
    assert connections.material() is None


def test_online_core_services_forwarding_via_outbound_edge_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    core = CoreService(CoreConfig.in_directory(tmp_path / "core"))
    candidate = core.store.add_candidate(
        CandidateInput(
            kind="project",
            content="Atlas uses an outbound-only Core channel",
            scopes=["project:atlas"],
            availability=Availability.CORE,
            allowed_clients=["edge:claude"],
        )
    )
    record = core.store.approve_candidate(candidate.id, ApprovalRequest(), actor="test")
    connections = EdgeConnectionStore(core.config)
    connections.prepare(core.store.vault_id())
    manager = EdgeSyncManager(connections, core.store)
    database = tmp_path / "edge-forward.sqlite3"

    with _edge_host(database, connections) as (_edge, client, broker):
        connections.connect(PUBLIC_URL, client=client)
        core.store.approve_remote_edge_client("edge:claude", name="Claude", scopes=["context:read"])
        assert manager.sync_now(http_client=client)["state"] == "ready"
        request_id = broker.enqueue(
            client_id="edge:claude",
            client_scopes=["context:read"],
            operation="search_context",
            payload={"query": "Atlas", "limit": 20},
        )
        result: dict[str, object] = {}

        def wait_for_response() -> None:
            response = broker.wait(request_id, timeout_seconds=3)
            result["state"] = response.state
            result["response"] = response.response

        waiter = threading.Thread(target=wait_for_response)
        waiter.start()
        synchronized = manager.sync_now(http_client=client)
        waiter.join(timeout=5)

        assert synchronized["forwarded_requests"] == 1
        assert result["state"] == "available"
        response = result["response"]
        assert isinstance(response, dict)
        assert [item["id"] for item in response["items"]] == [record.id]


def test_public_key_claim_keeps_edge_inert_then_rotates_durable_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    core = CoreService(CoreConfig.in_directory(tmp_path / "core"))
    candidate = core.store.add_candidate(
        CandidateInput(
            kind="preference",
            content="Claimed Edge can serve this approved projection",
            scopes=["general"],
            availability=Availability.ALWAYS,
        )
    )
    core.store.approve_candidate(candidate.id, ApprovalRequest(), actor="test")
    connections = EdgeConnectionStore(core.config)
    prepared = connections.prepare(core.store.vault_id())
    assert prepared.claim_bundle is not None
    database = tmp_path / "claimed-edge.sqlite3"
    relay_store = SQLiteRelayStore(database)
    placeholder_secret = hashlib.sha256(prepared.claim_bundle.signing_public_key.encode()).digest()
    placeholder_token = hashlib.sha256(prepared.claim_bundle.claim_id.encode()).hexdigest()
    service = RelayService(relay_store, placeholder_secret)
    oauth_store = EdgeOAuthStore(database)
    provider = EdgeOAuthProvider(oauth_store, PUBLIC_URL)
    claim_store = EdgeClaimStore(database, prepared.claim_bundle, PUBLIC_URL)
    app = create_edge_app(
        service,
        replication_bearer_token=placeholder_token,
        client_tokens={},
        edge_provider=provider,
        edge_pairing_secret=placeholder_secret,
        owner_secret_hash=prepared.claim_bundle.owner_secret_hash,
        vault_id=prepared.claim_bundle.vault_id,
        close_service_on_shutdown=False,
        forwarding_broker=EdgeForwardingBroker(
            database, prepared.claim_bundle.encryption_public_key
        ),
        claim_store=claim_store,
    )
    try:
        with TestClient(app, base_url=PUBLIC_URL) as client:
            assert client.get("/about").status_code == 423
            probed = client.get(
                "/healthz", params={"challenge": "x" * 20, "vault_id": core.store.vault_id()}
            )
            assert "proof" not in probed.json()
            connections.connect(PUBLIC_URL, client=client)
            claimed = connections.material()
            assert claimed is not None
            assert claimed.claim_bundle is None
            assert prepared.bundle.replication_token != claimed.bundle.replication_token
            assert claim_store.acknowledged()
            manager = EdgeSyncManager(connections, core.store)
            assert manager.sync_now(http_client=client)["state"] == "ready"
            assert client.get("/about").status_code == 200
            assert client.post("/v1/edge/claim/challenge").status_code == 410
    finally:
        oauth_store.close()
        service.close()
