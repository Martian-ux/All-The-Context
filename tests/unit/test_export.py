from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from allthecontext import export as portable_export
from allthecontext.config import MAX_IMPORT_BYTES
from allthecontext.export import create_export, restore_export
from allthecontext.storage import SOURCE_BLOB_CHUNK_BYTES, CoreStore
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
    assert manifest["source_chunks"] == []
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


def test_chunked_source_round_trips_through_encrypted_export(tmp_path: Path) -> None:
    source_database = tmp_path / "source.sqlite3"
    destination_database = tmp_path / "destination.sqlite3"
    source_store = CoreStore(source_database)
    source_store.initialize_vault()
    destination_store = CoreStore(destination_database)
    destination_store.initialize_vault()
    content = b"a" * SOURCE_BLOB_CHUNK_BYTES + b"tail"
    source_path = tmp_path / "large-source.jsonl"
    source_path.write_bytes(content)
    source = source_store.add_source_file(
        source_path,
        source_service="test",
        source_type="jsonl",
        filename=source_path.name,
    )
    package = tmp_path / "sources.atcexp"

    manifest = create_export(
        source_database,
        package,
        "correct horse battery staple",
        include_sources=True,
    )

    assert [(item["chunk_index"], item["byte_size"]) for item in manifest["source_chunks"]] == [
        (0, SOURCE_BLOB_CHUNK_BYTES),
        (1, 4),
    ]
    assert content not in package.read_bytes()
    restore_export(package, destination_database, "correct horse battery staple")
    restore_export(package, destination_database, "correct horse battery staple")
    copied = tmp_path / "restored-source.jsonl"
    assert destination_store.copy_source_content_to_path(source.id, copied) == len(content)
    assert copied.read_bytes() == content


def test_source_chunk_manifest_requires_a_bounded_contiguous_sequence() -> None:
    content_hash = "a" * 64

    with pytest.raises(ValueError, match="sequence"):
        portable_export._source_chunk_descriptors(
            {
                "source_chunks": [
                    {
                        "byte_size": 1,
                        "chunk_index": 1,
                        "content_hash": content_hash,
                        "path": f"source-chunks/{content_hash}/00000001.bin",
                    }
                ]
            }
        )

    oversized = [
        {
            "byte_size": SOURCE_BLOB_CHUNK_BYTES,
            "chunk_index": index,
            "content_hash": content_hash,
            "path": f"source-chunks/{content_hash}/{index:08d}.bin",
        }
        for index in range(MAX_IMPORT_BYTES // SOURCE_BLOB_CHUNK_BYTES + 1)
    ]
    with pytest.raises(ValueError, match="size limit"):
        portable_export._source_chunk_descriptors({"source_chunks": oversized})
