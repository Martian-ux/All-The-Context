from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import keyring
import pytest
from allthecontext.config import CoreConfig
from allthecontext.edge_connection import EdgeConnectionStore
from allthecontext.edge_setup import (
    BUNDLE_PREFIX,
    EdgeEnrollmentBundle,
    EdgeSetupError,
    edge_instance_proof,
    generate_enrollment,
    normalize_edge_url,
    proof_matches,
)
from allthecontext.relay.oauth import _logical_client_id, client_display_name
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl


@pytest.fixture(autouse=True)
def isolated_null_keyring() -> Iterator[None]:
    """Do not depend on whether another test initialized keyring first."""

    from keyring.backends.null import Keyring as NullKeyring

    previous = keyring.get_keyring()
    keyring.set_keyring(NullKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)


def test_enrollment_bundle_round_trips_without_weakening_credentials() -> None:
    bundle, recovery_code = generate_enrollment("vault-123")

    encoded = bundle.encode()
    decoded = EdgeEnrollmentBundle.decode(encoded)

    assert encoded.startswith(BUNDLE_PREFIX)
    assert decoded == bundle
    assert len(recovery_code.replace("-", "")) == 32
    assert recovery_code not in encoded


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Edge.Example.test/", "https://edge.example.test"),
        ("https://edge.example.test:443", "https://edge.example.test"),
        ("http://127.0.0.1:8743/", "http://127.0.0.1:8743"),
    ],
)
def test_edge_url_normalization(value: str, expected: str) -> None:
    assert normalize_edge_url(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://edge.example.test",
        "https://user:password@edge.example.test",
        "https://edge.example.test/a/path",
        "https://edge.example.test?secret=value",
    ],
)
def test_edge_url_rejects_unsafe_origins(value: str) -> None:
    with pytest.raises(EdgeSetupError):
        normalize_edge_url(value)


def test_pairing_proof_is_bound_to_origin_vault_and_challenge() -> None:
    secret = "x" * 43
    proof = edge_instance_proof(
        secret,
        public_url="https://edge.example.test",
        vault_id="vault-a",
        challenge="challenge-with-enough-entropy",
    )

    assert proof_matches(
        secret,
        public_url="https://edge.example.test",
        vault_id="vault-a",
        challenge="challenge-with-enough-entropy",
        supplied=proof,
    )
    assert not proof_matches(
        secret,
        public_url="https://other.example.test",
        vault_id="vault-a",
        challenge="challenge-with-enough-entropy",
        supplied=proof,
    )


def test_abandoned_expired_claim_is_rotated_idempotently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    connections = EdgeConnectionStore(CoreConfig.in_directory(tmp_path))
    first = connections.prepare("vault-abandoned")
    assert first.claim_bundle is not None
    future = first.claim_bundle.expires_at + 1
    monkeypatch.setattr("allthecontext.edge_connection.time.time", lambda: future)
    monkeypatch.setattr("allthecontext.edge_claim.time.time", lambda: future)

    rotated = connections.prepare("vault-abandoned")
    assert rotated.claim_bundle is not None
    assert rotated.claim_bundle.claim_id != first.claim_bundle.claim_id
    assert rotated.bundle == first.bundle
    assert rotated.recovery_code == first.recovery_code
    assert connections.prepare("vault-abandoned").claim_bundle == rotated.claim_bundle


def test_connect_rejects_an_already_decommissioned_edge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    connections = EdgeConnectionStore(CoreConfig.in_directory(tmp_path))
    material = connections.prepare("vault-terminal")
    material = connections.replace_bundle(material, material.bundle, preserve_claim=False)
    origin = "https://edge.example.test"

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return self.body

    class DecommissionedClient:
        def get(self, _url: str, **kwargs: Any) -> Response:
            challenge = str(kwargs["params"]["challenge"])
            response = Response()
            response.body = {
                "status": "decommissioned",
                "component": "edge",
                "authority": "core",
                "proof": edge_instance_proof(
                    material.bundle.replication_secret,
                    public_url=origin,
                    vault_id=material.bundle.vault_id,
                    challenge=challenge,
                ),
            }
            return response

    with pytest.raises(RuntimeError, match="already decommissioned"):
        connections.connect(origin, client=DecommissionedClient())  # type: ignore[arg-type]

    state = connections.state()
    assert state is not None
    assert state.edge_url is None


def test_core_edge_prepare_is_idempotent_and_uses_app_data_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    config = CoreConfig.in_directory(tmp_path)
    connections = EdgeConnectionStore(config)

    first = connections.prepare("vault-1")
    second = connections.prepare("vault-1")

    assert first == second
    assert first.credential_storage == "local app-data fallback"
    assert connections.state() is not None
    assert (tmp_path / "edge.json").is_file()


def test_prepare_preserves_paired_url_when_credential_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    connections = EdgeConnectionStore(CoreConfig.in_directory(tmp_path))
    connections.prepare("vault-1")
    state = connections.state()
    assert state is not None
    connections.save_state(
        replace(
            state,
            edge_url="https://edge.example.test",
            connected_at="2026-07-21T00:00:00+00:00",
        )
    )
    connections.fallback.delete(connections.credential_name)

    with pytest.raises(RuntimeError, match="still paired"):
        connections.prepare("vault-1")

    preserved = connections.state()
    assert preserved is not None
    assert preserved.edge_url == "https://edge.example.test"


def test_failed_secure_storage_retry_keeps_fallback_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    connections = EdgeConnectionStore(CoreConfig.in_directory(tmp_path))
    original = connections.prepare("vault-1")

    with pytest.raises(RuntimeError, match="still unavailable"):
        connections.migrate_credential_to_os_store()

    assert connections.material() == original


def test_reset_keeps_recovery_state_when_os_credential_deletion_cannot_be_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    connections = EdgeConnectionStore(CoreConfig.in_directory(tmp_path))
    original = connections.prepare("vault-1")
    monkeypatch.setattr(
        "allthecontext.edge_connection.KeyringCredentialStore.delete",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("credential manager unavailable")),
    )

    with pytest.raises(RuntimeError, match="Edge recovery state was kept"):
        connections.reset()

    assert connections.state() is not None
    assert connections.material() == original


def test_reset_verifies_and_removes_all_local_edge_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.null.Keyring")
    connections = EdgeConnectionStore(CoreConfig.in_directory(tmp_path))
    connections.prepare("vault-1")

    connections.reset()

    assert connections.state() is None
    assert connections.material() is None


def test_untrusted_client_name_cannot_impersonate_a_provider() -> None:
    client = OAuthClientInformationFull(
        client_id="attacker-controlled-client",
        client_name="Claude",
        redirect_uris=[AnyUrl("http://127.0.0.1:9234/callback")],
        token_endpoint_auth_method="none",
    )

    assert _logical_client_id(client).startswith("edge:client:")
    assert client_display_name(client) == "Unverified MCP client: Claude"
