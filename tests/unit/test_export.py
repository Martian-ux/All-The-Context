from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from allthecontext.export import create_export, restore_export
from cryptography.exceptions import InvalidTag


def _database(path: Path, value: str | None = None) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE context_records (id TEXT PRIMARY KEY, content TEXT NOT NULL)"
        )
        if value is not None:
            connection.execute(
                "INSERT INTO context_records (id, content) VALUES (?, ?)", ("record-1", value)
            )
        connection.commit()
    finally:
        connection.close()


def test_encrypted_export_round_trip_and_duplicate_restore(tmp_path: Path) -> None:
    original = tmp_path / "original.db"
    restored = tmp_path / "restored.db"
    package = tmp_path / "portable.atc"
    _database(original, "Prefers concise technical explanations")
    _database(restored)

    manifest = create_export(original, package, "correct horse battery staple")
    assert manifest["tables"]["context_records"] == 1
    assert b"Prefers concise" not in package.read_bytes()

    checked = restore_export(package, restored, "correct horse battery staple", dry_run=True)
    assert checked["valid"] is True
    restore_export(package, restored, "correct horse battery staple")
    restore_export(package, restored, "correct horse battery staple")

    connection = sqlite3.connect(restored)
    try:
        assert connection.execute("SELECT content FROM context_records").fetchall() == [
            ("Prefers concise technical explanations",)
        ]
    finally:
        connection.close()


def test_wrong_export_passphrase_fails_authentication(tmp_path: Path) -> None:
    database = tmp_path / "source.db"
    destination = tmp_path / "destination.db"
    package = tmp_path / "portable.atc"
    _database(database, "private")
    _database(destination)
    create_export(database, package, "correct horse battery staple")

    with pytest.raises(InvalidTag):
        restore_export(package, destination, "incorrect password")
