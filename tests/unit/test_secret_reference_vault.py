from __future__ import annotations

from typing import ClassVar

import pytest
from allthecontext.credentials import (
    DevelopmentFileCredentialStore,
    KeyringCredentialStore,
    SecretReferenceVault,
)


class FakeOsCredentialStore(KeyringCredentialStore):
    values: ClassVar[dict[str, str]] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def test_secret_reference_vault_keeps_raw_value_outside_application_store() -> None:
    store = FakeOsCredentialStore()
    store.values = {}
    vault = SecretReferenceVault(store)
    raw = "ATC_SYNTHETIC_OPERATIONAL_SECRET"

    reference = vault.put(raw)

    assert reference.startswith("atc-secret-ref-v1:")
    assert raw not in reference
    assert vault.get(reference) == raw
    assert store.values[reference] == raw
    vault.delete(reference)
    assert vault.get(reference) is None


def test_secret_reference_vault_rejects_plaintext_fallback_and_bad_references(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="operating-system credential store"):
        SecretReferenceVault(DevelopmentFileCredentialStore(tmp_path / "credentials.json"))
    vault = SecretReferenceVault(FakeOsCredentialStore())
    with pytest.raises(ValueError, match="invalid secret reference"):
        vault.get("not-a-secret-reference")
