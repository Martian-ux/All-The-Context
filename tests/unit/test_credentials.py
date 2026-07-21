from __future__ import annotations

from pathlib import Path

from allthecontext.credentials import DevelopmentFileCredentialStore


def test_explicit_development_credential_store_round_trip(tmp_path: Path) -> None:
    store = DevelopmentFileCredentialStore(tmp_path / "secrets" / "credentials.json")
    assert "INSECURE" in store.warning
    assert store.get("client") is None
    store.set("client", "secret")
    assert store.get("client") == "secret"
    store.delete("client")
    assert store.get("client") is None
