from __future__ import annotations

from collections.abc import Iterator

import keyring
import pytest
from keyring.backends.null import Keyring as NullKeyring


@pytest.fixture(autouse=True)
def isolated_test_keyring() -> Iterator[None]:
    """Keep unit/integration tests independent from host credential services."""

    previous = keyring.get_keyring()
    keyring.set_keyring(NullKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)
