from __future__ import annotations

import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest
from allthecontext.edge_claim import (
    MAX_ACTIVE_CHALLENGES,
    EdgeClaimError,
    EdgeClaimPrivate,
    EdgeClaimStore,
    decrypt_claim,
    generate_claim,
    sign_claim,
)
from allthecontext.edge_setup import hash_recovery_code
from allthecontext.relay.service import SQLiteRelayStore


def test_claim_is_public_key_bound_encrypted_replay_safe_and_restart_safe(tmp_path: Path) -> None:
    database = tmp_path / "edge.sqlite3"
    SQLiteRelayStore(database).close()
    bundle, private = generate_claim("vault-claim", hash_recovery_code("ABCD" * 8))
    store = EdgeClaimStore(database, bundle, "https://edge.example.test")
    challenge = store.challenge()
    signature = sign_claim(private, bundle, challenge, "https://edge.example.test")
    envelope = store.complete(challenge, signature)
    credentials = decrypt_claim(bundle, private, envelope)
    assert len(credentials["replication_secret"]) >= 32
    assert len(credentials["replication_token"]) >= 32
    with pytest.raises(EdgeClaimError, match="replayed"):
        store.complete(challenge, signature)

    restarted = EdgeClaimStore(database, bundle, "https://edge.example.test")
    retry_challenge = restarted.challenge()
    retry = restarted.complete(
        retry_challenge,
        sign_claim(private, bundle, retry_challenge, "https://edge.example.test"),
    )
    assert decrypt_claim(bundle, private, retry) == credentials
    restarted.acknowledge()
    with pytest.raises(EdgeClaimError, match="unavailable"):
        restarted.challenge()


def test_claim_rejects_first_claimer_probe_wrong_origin_and_abandonment(tmp_path: Path) -> None:
    database = tmp_path / "edge.sqlite3"
    SQLiteRelayStore(database).close()
    bundle, private = generate_claim("vault-claim", hash_recovery_code("ABCD" * 8))
    _, attacker = generate_claim("vault-attacker", hash_recovery_code("EFGH" * 8))
    store = EdgeClaimStore(database, bundle, "https://edge.example.test")
    challenge = store.challenge()
    with pytest.raises(EdgeClaimError, match="proof"):
        store.complete(
            challenge,
            sign_claim(attacker, bundle, challenge, "https://edge.example.test"),
        )
    challenge = store.challenge()
    with pytest.raises(EdgeClaimError, match="proof"):
        store.complete(
            challenge,
            sign_claim(private, bundle, challenge, "https://wrong.example.test"),
        )
    expired = replace(bundle, expires_at=int(time.time()) - 1)
    with pytest.raises(EdgeClaimError, match="unavailable"):
        EdgeClaimStore(database, expired, "https://edge.example.test").challenge()


def test_claim_decode_fails_closed_and_public_challenges_stay_bounded(tmp_path: Path) -> None:
    database = tmp_path / "edge.sqlite3"
    SQLiteRelayStore(database).close()
    bundle, _private = generate_claim("vault-claim", hash_recovery_code("ABCD" * 8))

    with pytest.raises(EdgeClaimError, match="expiry"):
        type(bundle).decode(replace(bundle, expires_at="tomorrow").encode())  # type: ignore[arg-type]
    with pytest.raises(EdgeClaimError, match="owner hash"):
        type(bundle).decode(replace(bundle, owner_secret_hash="z" * 64).encode())
    with pytest.raises(EdgeClaimError, match="key length"):
        EdgeClaimPrivate(signing_private_key="bad", encryption_private_key="bad")

    store = EdgeClaimStore(database, bundle, "https://edge.example.test")
    for _ in range(MAX_ACTIVE_CHALLENGES + 20):
        store.challenge()
    with sqlite3.connect(database) as connection:
        count = int(connection.execute("SELECT COUNT(*) FROM edge_claim_challenges").fetchone()[0])
    assert count == MAX_ACTIVE_CHALLENGES
