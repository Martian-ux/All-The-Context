from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

import pytest
from allthecontext.edge_claim import (
    EdgeClaimError,
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
