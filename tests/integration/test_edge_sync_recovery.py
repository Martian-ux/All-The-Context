from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.service import CoreService
from allthecontext.edge_connection import EdgeConnectionStore, EdgeSyncManager
from allthecontext.models import ApprovalRequest, Availability, CandidateInput
from allthecontext.relay.app import create_app as create_edge_app
from allthecontext.relay.oauth import EdgeOAuthProvider, EdgeOAuthStore
from allthecontext.relay.service import RelayService, SQLiteRelayStore
from allthecontext.sync import CoreRelaySync
from fastapi.testclient import TestClient

PUBLIC_URL = "https://edge.example.test"


@contextmanager
def _edge_host(
    database: Path,
    connections: EdgeConnectionStore,
) -> Iterator[tuple[RelayService, TestClient]]:
    material = connections.material()
    assert material is not None
    relay_store = SQLiteRelayStore(database)
    service = RelayService(
        relay_store,
        material.bundle.replication_secret.encode(),
    )
    oauth_store = EdgeOAuthStore(database)
    provider = EdgeOAuthProvider(oauth_store, PUBLIC_URL)
    app = create_edge_app(
        service,
        replication_bearer_token=material.bundle.replication_token,
        client_tokens={},
        edge_provider=provider,
        edge_pairing_secret=material.bundle.replication_secret.encode(),
        owner_secret_hash=material.bundle.owner_secret_hash,
        vault_id=material.bundle.vault_id,
        close_service_on_shutdown=False,
    )
    try:
        with TestClient(app, base_url=PUBLIC_URL) as client:
            yield service, client
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

    with _edge_host(tmp_path / "edge-original.sqlite3", connections) as (edge, client):
        connections.connect(PUBLIC_URL, client=client)
        first = manager.sync_now(http_client=client)
        assert first["state"] == "ready"
        assert first["last_sequence"] == 1
        assert edge.owner_get(material.bundle.vault_id, record.id) is not None

    # The Core outbox marks this event delivered. A replacement Edge still has
    # to receive the full retained log instead of remaining silently empty.
    assert core.store.pending_replication_events() == []
    with _edge_host(tmp_path / "edge-fresh.sqlite3", connections) as (fresh, fresh_client):
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
