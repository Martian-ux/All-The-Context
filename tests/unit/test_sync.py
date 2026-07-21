from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from allthecontext.sync import CoreRelaySync


def test_sync_rejects_cleartext_non_loopback_relay(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        CoreRelaySync(tmp_path / "core.db", "http://relay.example", b"x" * 32, "token")


def test_sync_accepts_loopback_for_local_demo(tmp_path: Path) -> None:
    database = tmp_path / "core.db"
    sqlite3.connect(database).close()
    sync = CoreRelaySync(
        database,
        "http://127.0.0.1:8743",
        b"x" * 32,
        "token",
        http_client=_NoopClient(),
    )
    sync.close()


class _NoopClient:
    def post(self, url: str, **kwargs: object) -> object:
        raise AssertionError("not called")

    def get(self, url: str, **kwargs: object) -> object:
        raise AssertionError("not called")

    def patch(self, url: str, **kwargs: object) -> object:
        raise AssertionError("not called")

    def close(self) -> None:
        return None
