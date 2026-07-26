from __future__ import annotations

from collections.abc import Iterator

import keyring
import pytest
from keyring.backends.null import Keyring as NullKeyring


@pytest.fixture(autouse=True)
def isolated_test_keyring(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep unit/integration tests independent from host credential services."""

    monkeypatch.setenv("ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE", "1")
    previous = keyring.get_keyring()
    keyring.set_keyring(NullKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)
