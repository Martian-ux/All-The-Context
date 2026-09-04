from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from allthecontext import export as portable_export
from allthecontext.config import MAX_IMPORT_BYTES
from allthecontext.export import create_export, restore_export
from allthecontext.models import CandidateInput, CoverageReport, IngestionMode
from allthecontext.storage import SOURCE_BLOB_CHUNK_BYTES, CoreStore
from cryptography.exceptions import InvalidTag

PASSPHRASE = "correct horse battery staple"
SOURCE_LESS_BARRIER_TABLE = "archive_source_less_purge_barriers"


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


def _source_less_store(path: Path) -> tuple[CoreStore, str]:
    store = CoreStore(path)
    store.initialize_vault()
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=["fixture-archive"],
        unavailable_sources=[],
        idempotency_key="source-less-export-session",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        "source-less-export-batch",
        [
            CandidateInput(
                kind="interaction_preference",
                content="I prefer concise answers.",
                source_service="fixture-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fixture-archive"], complete=True),
    )
    record_id = store.get_observation(str(batch["candidate_ids"][0])).record_id
    assert record_id is not None
    return store, record_id


def _rewrite_export(
    package: Path,
    destination: Path,
    mutate: Callable[[dict[str, bytes], dict[str, object]], None],
) -> Path:
    with TemporaryDirectory(prefix="atc-test-export-rewrite-") as temporary:
        payload = Path(temporary) / "payload.zip"
        portable_export._decrypt_file(package, payload, PASSPHRASE)
        with zipfile.ZipFile(payload) as incoming:
            members = {info.filename: incoming.read(info.filename) for info in incoming.infolist()}
        manifest = json.loads(members["manifest.json"])
        mutate(members, manifest)
        for name in manifest["sha256"]:
            if name not in members:
                raise AssertionError(f"rewritten export lost manifest member {name}")
            manifest["sha256"][name] = hashlib.sha256(members[name]).hexdigest()
        members["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()
        rewritten_payload = Path(temporary) / "rewritten.zip"
        with zipfile.ZipFile(rewritten_payload, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name, content in members.items():
                output.writestr(name, content)
        portable_export._encrypt_file(rewritten_payload, destination, PASSPHRASE)
    return destination


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


def test_default_export_carries_source_less_barriers_without_content(
    tmp_path: Path,
) -> None:
    database = tmp_path / "source-less.sqlite3"
    store, record_id = _source_less_store(database)
    store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    package = tmp_path / "source-less.atcexp"

    manifest = create_export(database, package, PASSPHRASE)

    barrier_name = f"tables/{SOURCE_LESS_BARRIER_TABLE}.jsonl"
    assert manifest["include_sources"] is False
    assert manifest["tables"][SOURCE_LESS_BARRIER_TABLE] == 1
    assert manifest["tables"].get("source_records", 0) == 0
    with TemporaryDirectory(prefix="atc-test-export-inspect-") as temporary:
        payload = Path(temporary) / "payload.zip"
        portable_export._decrypt_file(package, payload, PASSPHRASE)
        with zipfile.ZipFile(payload) as archive:
            rows = [json.loads(line) for line in archive.read(barrier_name).splitlines()]
    assert len(rows) == 1
    assert set(rows[0]) == {"vault_id", "source_kind", "barrier_digest", "purged_at"}
    assert "concise" not in json.dumps(rows)
    assert "source_less" not in json.dumps(rows)


def test_restore_rejects_malformed_or_ambiguous_source_less_barrier_entries(
    tmp_path: Path,
) -> None:
    database = tmp_path / "source-less.sqlite3"
    store, record_id = _source_less_store(database)
    store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    package = tmp_path / "source-less.atcexp"
    create_export(database, package, PASSPHRASE)
    barrier_name = f"tables/{SOURCE_LESS_BARRIER_TABLE}.jsonl"

    def add_unexpected_field(members: dict[str, bytes], _manifest: dict[str, object]) -> None:
        row = json.loads(members[barrier_name].splitlines()[0])
        row["content"] = "I prefer concise answers."
        members[barrier_name] = (
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()

    def duplicate_barrier(members: dict[str, bytes], _manifest: dict[str, object]) -> None:
        line = members[barrier_name].splitlines(keepends=True)[0]
        members[barrier_name] = line + line

    for index, mutate in enumerate((add_unexpected_field, duplicate_barrier)):
        tampered = _rewrite_export(package, tmp_path / f"tampered-{index}.atcexp", mutate)
        destination = tmp_path / f"destination-{index}.sqlite3"
        CoreStore(destination).migrate()
        with pytest.raises(ValueError, match="source-less archive purge barrier"):
            restore_export(tampered, destination, PASSPHRASE)


def test_restore_accepts_legacy_export_without_source_less_barrier_table(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-source.sqlite3"
    _store_instance, record_id = _source_less_store(database)
    package = tmp_path / "current.atcexp"
    create_export(database, package, PASSPHRASE)
    barrier_name = f"tables/{SOURCE_LESS_BARRIER_TABLE}.jsonl"

    def remove_new_barrier_table(members: dict[str, bytes], manifest: dict[str, object]) -> None:
        members.pop(barrier_name)
        tables = manifest["tables"]
        hashes = manifest["sha256"]
        assert isinstance(tables, dict)
        assert isinstance(hashes, dict)
        tables.pop(SOURCE_LESS_BARRIER_TABLE)
        hashes.pop(barrier_name)

    legacy = _rewrite_export(package, tmp_path / "legacy.atcexp", remove_new_barrier_table)
    destination = tmp_path / "destination.sqlite3"
    CoreStore(destination).migrate()
    restore_export(legacy, destination, PASSPHRASE)
    restore_export(legacy, destination, PASSPHRASE)

    with sqlite3.connect(destination) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM context_records WHERE id=?", (record_id,)
            ).fetchone()[0]
            == 1
        )


def test_restore_downgrades_tampered_source_rebuild_provenance_to_ordinary_barrier(
    tmp_path: Path,
) -> None:
    source_database = tmp_path / "source-truth.sqlite3"
    destination_database = tmp_path / "destination-truth.sqlite3"
    package = tmp_path / "truth.atcexp"
    source_store = CoreStore(source_database)
    source_store.initialize_vault()
    source = source_store.add_source(
        b"portable truth source",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    session = source_store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source.id],
        unavailable_sources=[],
        idempotency_key="truth-export-session",
    )
    batch = source_store.submit_batch(
        str(session["session_id"]),
        "truth-export-batch",
        [
            CandidateInput(
                kind="fact",
                content="Portable deletion barrier",
                source_id=source.id,
                source_reference="message:1",
                source_service="fixture-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    source_store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=["fixture-provider"], complete=True),
    )
    record_id = source_store.get_observation(str(batch["candidate_ids"][0])).record_id
    assert record_id is not None
    source_store.delete_record(record_id, reason="ordinary user deletion")
    create_export(source_database, package, PASSPHRASE, include_sources=True)

    with TemporaryDirectory(prefix="atc-test-export-") as temporary:
        payload = Path(temporary) / "payload.zip"
        portable_export._decrypt_file(package, payload, PASSPHRASE)
        tampered_payload = Path(temporary) / "tampered.zip"
        with zipfile.ZipFile(payload) as incoming:
            members = {info.filename: incoming.read(info.filename) for info in incoming.infolist()}
        tombstone_name = "tables/deletion_tombstones.jsonl"
        rows = []
        for line in members[tombstone_name].splitlines(keepends=True):
            row = json.loads(line)
            row["deletion_origin"] = "source_rebuild"
            row["deletion_source_id"] = source.id
            rows.append((json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode())
        members[tombstone_name] = b"".join(rows)
        manifest = json.loads(members["manifest.json"])
        manifest["sha256"][tombstone_name] = hashlib.sha256(members[tombstone_name]).hexdigest()
        members["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()
        with zipfile.ZipFile(tampered_payload, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name, content in members.items():
                output.writestr(name, content)
        tampered_package = Path(temporary) / "tampered.atcexp"
        portable_export._encrypt_file(tampered_payload, tampered_package, PASSPHRASE)

        CoreStore(destination_database).migrate()
        restore_export(tampered_package, destination_database, PASSPHRASE)

    restored_store = CoreStore(destination_database)
    with restored_store.connect() as connection:
        tombstone = connection.execute(
            "SELECT deletion_origin,deletion_source_id FROM deletion_tombstones WHERE record_id=?",
            (record_id,),
        ).fetchone()
    assert tombstone is not None
    assert tuple(tombstone) == ("ordinary", None)

    reimport = restored_store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source.id],
        unavailable_sources=[],
        idempotency_key="truth-export-reimport",
    )
    reimport_batch = restored_store.submit_batch(
        str(reimport["session_id"]),
        "truth-export-reimport-batch",
        [
            CandidateInput(
                kind="fact",
                content="Portable deletion barrier",
                source_id=source.id,
                source_reference="message:1",
                source_service="fixture-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    restored_store.finish_ingestion(
        str(reimport["session_id"]),
        CoverageReport(available=["fixture-provider"], complete=True),
    )
    observation = restored_store.get_observation(str(reimport_batch["candidate_ids"][0]))
    assert observation.disposition.value == "ignored"
    assert restored_store.status()["counts"]["active_records"] == 0


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
