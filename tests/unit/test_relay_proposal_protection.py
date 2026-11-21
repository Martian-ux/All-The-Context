from __future__ import annotations

import threading
from pathlib import Path

import pytest
from allthecontext.relay.service import ClientIdentity, RelayError, RelayService, SQLiteRelayStore

OLD_SECRET = b"synthetic-old-replication-secret-with-at-least-32-bytes"
NEW_SECRET = b"synthetic-new-replication-secret-with-at-least-32-bytes"
IDENTITY = ClientIdentity(
    client_id="synthetic-client",
    vault_id="synthetic-vault",
    permissions=frozenset({"proposal:write"}),
)
PROPOSAL = {
    "kind": "preference",
    "content": "Keep the synthetic proposal available across recovery.",
    "scope": ["general"],
    "sensitivity": "normal",
    "confidence": None,
    "source_service": None,
    "requested_availability": "core_available",
    "provenance": None,
}


def test_empty_proposal_queue_rotates_and_restarts_with_new_key(tmp_path: Path) -> None:
    database = tmp_path / "empty-relay.sqlite3"
    store = SQLiteRelayStore(database)
    service = RelayService(store, OLD_SECRET)
    service.rotate_replication_secret(NEW_SECRET)
    assert service.queued_proposals(IDENTITY.vault_id) == []
    service.close()

    restarted_store = SQLiteRelayStore(database)
    restarted = RelayService(restarted_store, NEW_SECRET)
    queued, _replayed = restarted.propose(
        IDENTITY,
        idempotency_key="after-empty-rotation",
        proposal=PROPOSAL,
    )
    assert restarted.queued_proposals(IDENTITY.vault_id)[0]["proposal_id"] == queued["proposal_id"]
    restarted.close()


def test_rotation_rejects_queued_proposals_and_restart_keeps_old_key(tmp_path: Path) -> None:
    database = tmp_path / "relay.sqlite3"
    store = SQLiteRelayStore(database)
    service = RelayService(store, OLD_SECRET)
    queued, _replayed = service.propose(
        IDENTITY,
        idempotency_key="synthetic-proposal",
        proposal=PROPOSAL,
    )

    with pytest.raises(RelayError, match="queued proposals exist"):
        service.rotate_replication_secret(NEW_SECRET)

    assert service._replication_secret == OLD_SECRET
    assert service.queued_proposals(IDENTITY.vault_id)[0]["proposal_id"] == queued["proposal_id"]
    service.close()

    wrong_key_store = SQLiteRelayStore(database)
    with pytest.raises(RelayError, match="envelope is invalid"):
        RelayService(wrong_key_store, NEW_SECRET)
    wrong_key_store.close()

    restarted_store = SQLiteRelayStore(database)
    restarted = RelayService(restarted_store, OLD_SECRET)
    proposals = restarted.queued_proposals(IDENTITY.vault_id)
    assert len(proposals) == 1
    assert proposals[0]["proposal"] == PROPOSAL
    restarted.close()


def test_failed_protection_rotation_preserves_database_and_selected_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SQLiteRelayStore(tmp_path / "failed-rotation.sqlite3")
    service = RelayService(store, OLD_SECRET)
    service.propose(IDENTITY, idempotency_key="retained", proposal=PROPOSAL)
    before = list(store._connection.execute("SELECT * FROM pending_memory_proposals"))

    def fail_after_cleanup(connection) -> None:  # type: ignore[no-untyped-def]
        connection.execute("DELETE FROM pending_memory_proposals")
        raise OSError("injected cleanup interruption")

    with monkeypatch.context() as patch:
        patch.setattr(store, "_purge_expired_proposals", fail_after_cleanup)
        with pytest.raises(OSError, match="injected cleanup interruption"):
            service.rotate_replication_secret(NEW_SECRET)

    assert list(store._connection.execute("SELECT * FROM pending_memory_proposals")) == before
    assert service._replication_secret == OLD_SECRET
    assert service.queued_proposals(IDENTITY.vault_id)[0]["proposal"] == PROPOSAL
    service.close()


def test_concurrent_proposal_and_rotation_preserve_one_valid_protection_key(
    tmp_path: Path,
) -> None:
    store = SQLiteRelayStore(tmp_path / "concurrent-relay.sqlite3")
    service = RelayService(store, OLD_SECRET)
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []
    results: list[object] = []

    def queue() -> None:
        try:
            barrier.wait(timeout=3)
            results.append(
                service.propose(
                    IDENTITY,
                    idempotency_key="concurrent-proposal",
                    proposal=PROPOSAL,
                )
            )
        except BaseException as error:
            errors.append(error)

    def rotate() -> None:
        try:
            barrier.wait(timeout=3)
            service.rotate_replication_secret(NEW_SECRET)
        except BaseException as error:
            errors.append(error)

    queue_thread = threading.Thread(target=queue)
    rotate_thread = threading.Thread(target=rotate)
    queue_thread.start()
    rotate_thread.start()
    barrier.wait(timeout=3)
    queue_thread.join(timeout=3)
    rotate_thread.join(timeout=3)

    assert not queue_thread.is_alive() and not rotate_thread.is_alive()
    assert len(results) == 1
    proposals = service.queued_proposals(IDENTITY.vault_id)
    assert len(proposals) == 1
    assert proposals[0]["proposal"] == PROPOSAL
    if service._replication_secret == OLD_SECRET:
        assert len(errors) == 1 and isinstance(errors[0], RelayError)
    else:
        assert service._replication_secret == NEW_SECRET
        assert errors == []
    service.close()
