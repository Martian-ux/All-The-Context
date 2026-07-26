from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest
from keyring.backends.null import Keyring as NullKeyring


@pytest.fixture(autouse=True)
def isolated_test_host_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[None]:
    """Keep tests independent from host credentials and real AI-client settings."""

    monkeypatch.setenv("ATC_ENABLE_INSECURE_DEVELOPMENT_CREDENTIAL_FILE", "1")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv(
        "ATC_CLAUDE_CONFIG",
        str(tmp_path / "claude" / "claude_desktop_config.json"),
    )
    previous = keyring.get_keyring()
    keyring.set_keyring(NullKeyring())
    try:
        yield
    finally:
        keyring.set_keyring(previous)
