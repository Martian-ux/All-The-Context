from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.credentials import (
    DEVELOPMENT_FALLBACK_ENV,
    DevelopmentFileCredentialStore,
    development_file_credentials_enabled,
    require_development_file_credentials,
    verify_isolated_os_credential_round_trip,
)


def test_explicit_development_credential_store_round_trip(tmp_path: Path) -> None:
    store = DevelopmentFileCredentialStore(tmp_path / "secrets" / "credentials.json")
    assert "INSECURE" in store.warning
    assert store.get("client") is None
    store.set("client", "secret")
    assert store.get("client") == "secret"
    store.delete("client")
    assert store.get("client") is None


def test_development_credential_file_requires_deliberate_opt_in(monkeypatch) -> None:
    monkeypatch.delenv(DEVELOPMENT_FALLBACK_ENV, raising=False)

    assert development_file_credentials_enabled() is False
    with pytest.raises(RuntimeError, match="plaintext credential storage is disabled"):
        require_development_file_credentials()

    monkeypatch.setenv(DEVELOPMENT_FALLBACK_ENV, "yes")
    require_development_file_credentials()


def test_isolated_os_credential_acceptance_removes_unique_value(monkeypatch) -> None:
    values: dict[tuple[str, str], str] = {}
    monkeypatch.setattr(
        "allthecontext.credentials.keyring.set_password",
        lambda service, name, value: values.__setitem__((service, name), value),
    )
    monkeypatch.setattr(
        "allthecontext.credentials.keyring.get_password",
        lambda service, name: values.get((service, name)),
    )
    monkeypatch.setattr(
        "allthecontext.credentials.keyring.delete_password",
        lambda service, name: values.pop((service, name)),
    )

    verify_isolated_os_credential_round_trip()

    assert values == {}


def test_isolated_os_credential_acceptance_cleans_up_after_failed_read(monkeypatch) -> None:
    deleted: list[tuple[str, str]] = []
    monkeypatch.setattr("allthecontext.credentials.keyring.set_password", lambda *_args: None)
    monkeypatch.setattr("allthecontext.credentials.keyring.get_password", lambda *_args: "wrong")
    monkeypatch.setattr(
        "allthecontext.credentials.keyring.delete_password",
        lambda service, name: deleted.append((service, name)),
    )

    with pytest.raises(RuntimeError, match="did not round trip"):
        verify_isolated_os_credential_round_trip()

    assert len(deleted) == 1
