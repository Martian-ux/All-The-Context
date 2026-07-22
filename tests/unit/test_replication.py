from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path

import pytest
from allthecontext.relay.service import (
    ClientIdentity,
    EventSequenceError,
    InvalidEventPayloadError,
    RelayService,
    ReplayMismatchError,
    SQLiteRelayStore,
)
from allthecontext.replication import (
    EventType,
    HttpRelayTransport,
    PayloadHashError,
    ReplicationEvent,
    SignatureError,
    build_event,
    calculate_payload_hash,
    canonical_json,
    sign_event,
    verify_event,
)

SECRET = b"relay-test-secret-with-at-least-32-bytes"


def record_payload(
    record_id: str,
    *,
    content: str = "Prefers concise technical explanations",
    version: int = 1,
    scopes: list[str] | None = None,
    allowed: list[str] | None = None,
    denied: list[str] | None = None,
    supersedes: str | None = None,
) -> dict[str, object]:
    return {
        "id": record_id,
        "kind": "preference",
        "content": content,
        "scope": scopes or ["personal"],
        "provenance": {"source_record_id": "source-1"},
        "source_service": "bootstrap",
        "confidence": 0.9,
        "sensitivity": "private",
        "availability": "always_available",
        "allowed_clients": allowed or [],
        "denied_clients": denied or [],
        "valid_from": "2025-01-01T00:00:00+00:00",
        "valid_until": None,
        "version": version,
        "supersedes": supersedes,
        "approval_status": "approved",
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "updated_at": "2026-07-21T00:00:00+00:00",
    }


def event(
    sequence: int,
    event_type: EventType,
    record_id: str,
    payload: dict[str, object],
    *,
    event_id: str | None = None,
) -> ReplicationEvent:
    unsigned = build_event(
        vault_id="vault-1",
        sequence=sequence,
        event_type=event_type,
        record_id=record_id,
        payload=payload,  # type: ignore[arg-type]
        event_id=event_id or f"event-{sequence}",
        created_at="2026-07-21T00:00:00+00:00",
    )
    return sign_event(unsigned, SECRET)


@pytest.fixture
def relay(tmp_path: Path) -> RelayService:
    service = RelayService(SQLiteRelayStore(tmp_path / "relay.sqlite3"), SECRET)
    yield service
    service.close()


def test_canonical_json_and_signature_are_stable() -> None:
    left = {"z": ["é", 2], "a": {"value": True}}
    right = {"a": {"value": True}, "z": ["é", 2]}
    assert canonical_json(left) == canonical_json(right)
    assert calculate_payload_hash(left) == calculate_payload_hash(right)

    signed = sign_event(
        build_event(
            vault_id="vault-1",
            sequence=1,
            event_type=EventType.RECORD_WITHDRAWN,
            record_id="record-1",
            payload=left,  # type: ignore[arg-type]
            event_id="event-1",
            created_at="2026-07-21T00:00:00+00:00",
        ),
        SECRET,
    )
    verify_event(signed, SECRET)
    parsed = ReplicationEvent.from_mapping(signed.wire_mapping())
    assert parsed == signed


def test_unsigned_wrong_secret_and_tampered_payload_are_rejected() -> None:
    unsigned = build_event(
        vault_id="vault-1",
        sequence=1,
        event_type=EventType.RECORD_WITHDRAWN,
        record_id="record-1",
        payload={"reason": "privacy"},
    )
    with pytest.raises(SignatureError):
        verify_event(unsigned, SECRET)

    signed = sign_event(unsigned, SECRET)
    with pytest.raises(SignatureError):
        verify_event(signed, b"another-secret-that-is-definitely-long-enough")
    tampered = replace(signed, payload={"reason": "changed"})
    with pytest.raises(PayloadHashError):
        verify_event(tampered, SECRET)


def test_http_transport_requires_tls_except_on_loopback() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        HttpRelayTransport("http://relay.example", "token", client=object())


def test_apply_is_ordered_and_matching_replay_is_idempotent(relay: RelayService) -> None:
    first = event(1, EventType.RECORD_UPSERTED, "record-1", record_payload("record-1"))
    result = relay.apply(first)
    assert not result.replayed
    assert relay.apply(first).replayed
    assert relay.store.checkpoint("vault-1") == 1

    changed_replay = event(
        1,
        EventType.RECORD_UPSERTED,
        "record-1",
        record_payload("record-1", content="changed"),
        event_id="changed-event",
    )
    with pytest.raises(ReplayMismatchError):
        relay.apply(changed_replay)

    third = event(3, EventType.RECORD_WITHDRAWN, "record-1", {"reason": "scope changed"})
    with pytest.raises(EventSequenceError) as exc_info:
        relay.apply(third)
    assert exc_info.value.expected == 2
    assert relay.store.checkpoint("vault-1") == 1


def test_invalid_event_rolls_back_record_and_checkpoint(relay: RelayService) -> None:
    invalid = record_payload("record-1")
    invalid["availability"] = "core_available"
    signed = event(1, EventType.RECORD_UPSERTED, "record-1", invalid)
    with pytest.raises(InvalidEventPayloadError):
        relay.apply(signed)
    assert relay.store.checkpoint("vault-1") == 0

    valid = event(1, EventType.RECORD_UPSERTED, "record-1", record_payload("record-1"))
    relay.apply(valid)
    assert relay.store.checkpoint("vault-1") == 1


def test_retrieval_enforces_scopes_allow_deny_and_validity(relay: RelayService) -> None:
    payloads = [
        record_payload("record-1", content="alpha visible", scopes=["personal"]),
        record_payload(
            "record-2", content="alpha allow only", scopes=["work"], allowed=["client-a"]
        ),
        record_payload(
            "record-3",
            content="alpha denied wins",
            scopes=["personal"],
            allowed=["client-a"],
            denied=["client-a"],
        ),
    ]
    for sequence, payload in enumerate(payloads, start=1):
        record_id = str(payload["id"])
        relay.apply(event(sequence, EventType.RECORD_UPSERTED, record_id, payload))

    personal = ClientIdentity(
        "client-a", "vault-1", frozenset({"context:read"}), frozenset({"personal"})
    )
    all_scopes = ClientIdentity(
        "client-a", "vault-1", frozenset({"context:read"}), frozenset({"*"})
    )
    assert [item["id"] for item in relay.search(personal, query="alpha")] == ["record-1"]
    assert {item["id"] for item in relay.search(all_scopes, query="alpha")} == {
        "record-1",
        "record-2",
    }
    assert relay.get(personal, "record-3") is None


def test_supersession_withdrawal_and_deletion_propagate(relay: RelayService) -> None:
    identity = ClientIdentity("client-a", "vault-1", frozenset({"context:read"}), frozenset({"*"}))
    relay.apply(event(1, EventType.RECORD_UPSERTED, "old", record_payload("old")))
    relay.apply(
        event(
            2,
            EventType.RECORD_UPSERTED,
            "new",
            record_payload("new", content="Corrected preference", supersedes="old"),
        )
    )
    assert relay.get(identity, "old") is None
    assert relay.get(identity, "new") is not None

    relay.apply(event(3, EventType.RECORD_WITHDRAWN, "new", {"reason": "availability changed"}))
    assert relay.get(identity, "new") is None

    relay.apply(event(4, EventType.RECORD_DELETED, "old", {"version": 2}))
    assert relay.get(identity, "old") is None
    with pytest.raises(InvalidEventPayloadError):
        relay.apply(event(5, EventType.RECORD_UPSERTED, "old", record_payload("old", version=3)))
    assert relay.store.checkpoint("vault-1") == 4


def test_proposals_are_idempotent_and_never_enter_context(relay: RelayService) -> None:
    identity = ClientIdentity(
        "client-a",
        "vault-1",
        frozenset({"context:read", "proposal:write"}),
        frozenset({"*"}),
    )
    proposal = {
        "kind": "preference",
        "content": "Use dark mode",
        "scope": ["personal"],
        "confidence": 0.8,
        "sensitivity": "private",
        "availability": "always_available",
    }
    queued, replayed = relay.propose(identity, idempotency_key="request-1", proposal=proposal)
    assert not replayed
    assert queued["status"] == "queued"
    assert relay.search(identity, query="dark mode") == []

    same, replayed = relay.propose(identity, idempotency_key="request-1", proposal=proposal)
    assert replayed
    assert same["proposal_id"] == queued["proposal_id"]
    assert len(relay.queued_proposals("vault-1")) == 1
    assert relay.acknowledge_proposal("vault-1", str(queued["proposal_id"]), "imported")
    assert relay.queued_proposals("vault-1") == []
