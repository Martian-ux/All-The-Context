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


def _source_inclusive_store(path: Path) -> tuple[CoreStore, str, str]:
    store = CoreStore(path)
    store.initialize_vault()
    source = store.add_source(
        b"source-inclusive export body",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    session = store.begin_ingestion(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source.id],
        unavailable_sources=[],
        idempotency_key="source-inclusive-export-session",
    )
    batch = store.submit_batch(
        str(session["session_id"]),
        "source-inclusive-export-batch",
        [
            CandidateInput(
                kind="fact",
                content="Source-inclusive export marker",
                source_id=source.id,
                source_reference="message:source-inclusive",
                source_service="fixture-provider",
                source_type="provider_archive",
                explicit_user_statement=True,
            )
        ],
    )
    store.finish_ingestion(
        str(session["session_id"]),
        CoverageReport(available=[source.id], complete=True),
    )
    record_id = store.get_observation(str(batch["candidate_ids"][0])).record_id
    assert record_id is not None
    return store, source.id, record_id


def _rewrite_export(
    package: Path,
    destination: Path,
    mutate: Callable[[dict[str, bytes], dict[str, object]], None],
    *,
    refresh_hashes: bool = True,
) -> Path:
    with TemporaryDirectory(prefix="atc-test-export-rewrite-") as temporary:
        payload = Path(temporary) / "payload.zip"
        portable_export._decrypt_file(package, payload, PASSPHRASE)
        with zipfile.ZipFile(payload) as incoming:
            members = {info.filename: incoming.read(info.filename) for info in incoming.infolist()}
        manifest = json.loads(members["manifest.json"])
        mutate(members, manifest)
        if refresh_hashes:
            for name in manifest["sha256"]:
                if name in members:
                    manifest["sha256"][name] = hashlib.sha256(members[name]).hexdigest()
        members["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()
        rewritten_payload = Path(temporary) / "rewritten.zip"
        with zipfile.ZipFile(rewritten_payload, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for name, content in members.items():
                output.writestr(name, content)
        portable_export._encrypt_file(rewritten_payload, destination, PASSPHRASE)
    return destination


def _rewrite_export_with_duplicate_entry(package: Path, destination: Path, name: str) -> Path:
    with TemporaryDirectory(prefix="atc-test-export-duplicate-") as temporary:
        payload = Path(temporary) / "payload.zip"
        portable_export._decrypt_file(package, payload, PASSPHRASE)
        with zipfile.ZipFile(payload) as incoming:
            entries = [
                (info.filename, incoming.read(info.filename)) for info in incoming.infolist()
            ]
        manifest = json.loads(dict(entries)["manifest.json"])
        entries.append((name, dict(entries)[name]))
        manifest["sha256"][name] = hashlib.sha256(dict(entries)[name]).hexdigest()
        manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2).encode()
        entries = [
            (entry_name, manifest_bytes if entry_name == "manifest.json" else content)
            for entry_name, content in entries
        ]
        rewritten_payload = Path(temporary) / "rewritten.zip"
        with zipfile.ZipFile(rewritten_payload, "w", compression=zipfile.ZIP_DEFLATED) as output:
            for entry_name, content in entries:
                output.writestr(entry_name, content)
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


@pytest.mark.parametrize("tamper", ["missing-hash", "hash-only", "extra", "shadow"])
def test_restore_requires_exact_manifest_member_and_hash_coverage(
    tmp_path: Path,
    tamper: str,
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

    def tamper_export(members: dict[str, bytes], manifest: dict[str, object]) -> None:
        hashes = manifest["sha256"]
        assert isinstance(hashes, dict)
        if tamper == "missing-hash":
            hashes.pop(barrier_name)
        elif tamper == "hash-only":
            hashes["tables/hash-only.jsonl"] = "0" * 64
        elif tamper == "extra":
            members["tables/extra.jsonl"] = b"{}\n"
        else:
            members[f"tables/{SOURCE_LESS_BARRIER_TABLE.upper()}.jsonl"] = members[barrier_name]

    tampered = _rewrite_export(package, tmp_path / f"{tamper}.atcexp", tamper_export)
    destination = tmp_path / f"destination-{tamper}.sqlite3"
    destination_store = CoreStore(destination)
    destination_store.initialize_vault()
    sentinel = destination_store.add_candidate(
        CandidateInput(kind="fact", content="destination sentinel", explicit_user_statement=True)
    )

    with pytest.raises(ValueError, match=r"coverage|members|shadowed"):
        restore_export(tampered, destination, PASSPHRASE)
    assert destination_store.get_record(sentinel.record_id).content == "destination sentinel"
    with destination_store.connect() as connection:
        assert (
            connection.execute(f"SELECT COUNT(*) FROM {SOURCE_LESS_BARRIER_TABLE}").fetchone()[0]
            == 0
        )


def test_restore_rejects_changed_member_digest_before_destination_mutation(
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

    def change_digest(_members: dict[str, bytes], manifest: dict[str, object]) -> None:
        hashes = manifest["sha256"]
        assert isinstance(hashes, dict)
        hashes[barrier_name] = "0" * 64

    tampered = _rewrite_export(
        package,
        tmp_path / "changed-digest.atcexp",
        change_digest,
        refresh_hashes=False,
    )
    destination = tmp_path / "destination.sqlite3"
    destination_store = CoreStore(destination)
    destination_store.initialize_vault()
    with pytest.raises(ValueError, match="integrity check failed"):
        restore_export(tampered, destination, PASSPHRASE)
    with destination_store.connect() as connection:
        assert (
            connection.execute(f"SELECT COUNT(*) FROM {SOURCE_LESS_BARRIER_TABLE}").fetchone()[0]
            == 0
        )


def test_restore_rejects_duplicate_archive_entries_and_duplicate_json_keys(
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

    duplicate_archive = _rewrite_export_with_duplicate_entry(
        package, tmp_path / "duplicate-archive.atcexp", barrier_name
    )
    duplicate_destination = tmp_path / "duplicate-archive.sqlite3"
    CoreStore(duplicate_destination).migrate()
    with pytest.raises(ValueError, match="duplicate"):
        restore_export(duplicate_archive, duplicate_destination, PASSPHRASE)

    def duplicate_json_key(members: dict[str, bytes], _manifest: dict[str, object]) -> None:
        row = json.loads(members[barrier_name].splitlines()[0])
        members[barrier_name] = (
            "{"
            f'"vault_id":{json.dumps(row["vault_id"])},'
            f'"vault_id":{json.dumps(row["vault_id"])},'
            f'"source_kind":{json.dumps(row["source_kind"])},'
            f'"barrier_digest":{json.dumps(row["barrier_digest"])},'
            f'"purged_at":{json.dumps(row["purged_at"])}'
            "}\n"
        ).encode()

    duplicate_json = _rewrite_export(
        package, tmp_path / "duplicate-json.atcexp", duplicate_json_key
    )
    json_destination = tmp_path / "duplicate-json.sqlite3"
    CoreStore(json_destination).migrate()
    with pytest.raises(ValueError, match="duplicate"):
        restore_export(duplicate_json, json_destination, PASSPHRASE)


def test_restore_rejects_barrier_reassigned_to_existing_destination_vault(
    tmp_path: Path,
) -> None:
    database = tmp_path / "source-less.sqlite3"
    store, record_id = _source_less_store(database)
    source_vault_id = store.vault_id()
    store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    package = tmp_path / "source-less.atcexp"
    create_export(database, package, PASSPHRASE)
    barrier_name = f"tables/{SOURCE_LESS_BARRIER_TABLE}.jsonl"
    destination = tmp_path / "destination.sqlite3"
    destination_store = CoreStore(destination)
    destination_vault_id = destination_store.initialize_vault("unrelated destination vault")
    assert destination_vault_id != source_vault_id
    sentinel = destination_store.add_candidate(
        CandidateInput(kind="fact", content="destination-only", explicit_user_statement=True)
    )

    def inject_into_destination(members: dict[str, bytes], _manifest: dict[str, object]) -> None:
        row = json.loads(members[barrier_name].splitlines()[0])
        row["vault_id"] = destination_vault_id
        members[barrier_name] = (
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()

    tampered = _rewrite_export(package, tmp_path / "cross-vault.atcexp", inject_into_destination)
    with pytest.raises(ValueError, match="package vault"):
        restore_export(tampered, destination, PASSPHRASE)
    assert destination_store.get_record(sentinel.record_id).content == "destination-only"
    with destination_store.connect() as connection:
        assert (
            connection.execute(f"SELECT COUNT(*) FROM {SOURCE_LESS_BARRIER_TABLE}").fetchone()[0]
            == 0
        )


def test_restore_rejects_active_candidate_and_record_reassigned_to_destination_vault(
    tmp_path: Path,
) -> None:
    source_database = tmp_path / "source.sqlite3"
    source_store, _record_id = _source_less_store(source_database)
    source_vault_id = source_store.vault_id()
    package = tmp_path / "source.atcexp"
    create_export(source_database, package, PASSPHRASE)

    destination = tmp_path / "destination.sqlite3"
    destination_store = CoreStore(destination)
    destination_vault_id = destination_store.initialize_vault("unrelated destination vault")
    sentinel = destination_store.add_candidate(
        CandidateInput(kind="fact", content="destination sentinel", explicit_user_statement=True)
    )

    def reassign_active_rows(members: dict[str, bytes], _manifest: dict[str, object]) -> None:
        for table in ("context_candidates", "context_records"):
            name = f"tables/{table}.jsonl"
            rewritten = []
            for line in members[name].splitlines():
                row = json.loads(line)
                if row.get("vault_id") == source_vault_id:
                    row["vault_id"] = destination_vault_id
                rewritten.append(
                    (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
                )
            members[name] = b"".join(rewritten)

    tampered = _rewrite_export(
        package,
        tmp_path / "active-cross-vault.atcexp",
        reassign_active_rows,
    )
    with pytest.raises(ValueError, match="package vault"):
        restore_export(tampered, destination, PASSPHRASE)
    assert destination_store.get_record(sentinel.record_id).content == "destination sentinel"


def test_restore_rejects_source_reference_to_destination_source(
    tmp_path: Path,
) -> None:
    source_database = tmp_path / "source.sqlite3"
    _source_store, source_id, _record_id = _source_inclusive_store(source_database)
    package = tmp_path / "source.atcexp"
    create_export(source_database, package, PASSPHRASE, include_sources=True)

    destination = tmp_path / "destination.sqlite3"
    destination_store = CoreStore(destination)
    destination_store.initialize_vault("unrelated destination vault")
    foreign_source = destination_store.add_source(
        b"destination-only source",
        source_service="fixture-provider",
        source_type="provider_archive",
    )
    sentinel = destination_store.add_candidate(
        CandidateInput(kind="fact", content="destination sentinel", explicit_user_statement=True)
    )

    def reassign_source_references(members: dict[str, bytes], _manifest: dict[str, object]) -> None:
        for table in ("context_candidates", "context_records"):
            name = f"tables/{table}.jsonl"
            rewritten = []
            for line in members[name].splitlines():
                row = json.loads(line)
                if row.get("source_id") == source_id:
                    row["source_id"] = foreign_source.id
                rewritten.append(
                    (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
                )
            members[name] = b"".join(rewritten)

    tampered = _rewrite_export(
        package,
        tmp_path / "source-reference-cross-vault.atcexp",
        reassign_source_references,
    )
    with pytest.raises(ValueError, match="source reference"):
        restore_export(tampered, destination, PASSPHRASE)
    assert destination_store.get_record(sentinel.record_id).content == "destination sentinel"


def test_restore_rejects_extra_package_vault_with_split_barrier_binding(
    tmp_path: Path,
) -> None:
    source_database = tmp_path / "source.sqlite3"
    source_store, record_id = _source_less_store(source_database)
    source_vault_id = source_store.vault_id()
    source_store.purge(
        "record",
        record_id,
        confirmation=source_store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    package = tmp_path / "source.atcexp"
    create_export(source_database, package, PASSPHRASE)
    barrier_name = f"tables/{SOURCE_LESS_BARRIER_TABLE}.jsonl"
    extra_vault_id = "extra-package-vault"

    def split_package_binding(members: dict[str, bytes], _manifest: dict[str, object]) -> None:
        vault_name = "tables/vaults.jsonl"
        vault_row = json.loads(members[vault_name].splitlines()[0])
        vault_row["id"] = extra_vault_id
        members[vault_name] += (
            json.dumps(vault_row, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        barrier = json.loads(members[barrier_name].splitlines()[0])
        barrier["vault_id"] = extra_vault_id
        members[barrier_name] = (
            json.dumps(barrier, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()

    tampered = _rewrite_export(
        package,
        tmp_path / "split-package-binding.atcexp",
        split_package_binding,
    )
    destination = tmp_path / "destination.sqlite3"
    destination_store = CoreStore(destination)
    destination_store.initialize_vault("destination vault")
    sentinel = destination_store.add_candidate(
        CandidateInput(kind="fact", content="destination sentinel", explicit_user_statement=True)
    )
    with pytest.raises(ValueError, match="exactly one package vault"):
        restore_export(tampered, destination, PASSPHRASE)
    assert destination_store.get_record(sentinel.record_id).content == "destination sentinel"
    assert source_vault_id != extra_vault_id


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("format_version", True),
        ("format_version", 1.0),
        ("format_version", "1"),
        ("schema_version", True),
        ("schema_version", 19.0),
        ("schema_version", "19"),
        ("schema_version", -1),
        ("schema_version", 20),
    ],
)
def test_restore_rejects_noncanonical_manifest_versions_before_destination_mutation(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    source_database = tmp_path / "source.sqlite3"
    source_store, record_id = _source_less_store(source_database)
    source_store.purge(
        "record",
        record_id,
        confirmation=source_store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    package = tmp_path / "source.atcexp"
    create_export(source_database, package, PASSPHRASE)

    def tamper_manifest(_members: dict[str, bytes], manifest: dict[str, object]) -> None:
        manifest[field] = value

    tampered = _rewrite_export(
        package,
        tmp_path / f"invalid-{field}-{str(value).replace('.', '-')}.atcexp",
        tamper_manifest,
    )
    destination = tmp_path / "destination.sqlite3"
    destination_store = CoreStore(destination)
    destination_store.initialize_vault("destination vault")
    sentinel = destination_store.add_candidate(
        CandidateInput(kind="fact", content="destination sentinel", explicit_user_statement=True)
    )
    message = "unsupported export format" if field == "format_version" else "schema version"
    with pytest.raises(ValueError, match=message):
        restore_export(tampered, destination, PASSPHRASE)
    assert destination_store.get_record(sentinel.record_id).content == "destination sentinel"
    with destination_store.connect() as connection:
        assert (
            connection.execute(f"SELECT COUNT(*) FROM {SOURCE_LESS_BARRIER_TABLE}").fetchone()[0]
            == 0
        )


def test_restore_accepts_explicit_package_wide_vault_remap(
    tmp_path: Path,
) -> None:
    database = tmp_path / "source-less.sqlite3"
    store, record_id = _source_less_store(database)
    source_vault_id = store.vault_id()
    store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    package = tmp_path / "source-less.atcexp"
    create_export(database, package, PASSPHRASE)
    destination = tmp_path / "destination.sqlite3"
    destination_store = CoreStore(destination)
    destination_vault_id = destination_store.initialize_vault("remapped destination vault")
    assert destination_vault_id != source_vault_id

    def remap_package(members: dict[str, bytes], _manifest: dict[str, object]) -> None:
        for name in list(members):
            if not name.startswith("tables/"):
                continue
            rows: list[bytes] = []
            for line in members[name].splitlines():
                row = json.loads(line)
                if name == "tables/vaults.jsonl" and row.get("id") == source_vault_id:
                    row["id"] = destination_vault_id
                if row.get("vault_id") == source_vault_id:
                    row["vault_id"] = destination_vault_id
                rows.append(
                    (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
                )
            members[name] = b"".join(rows)

    remapped = _rewrite_export(package, tmp_path / "remapped.atcexp", remap_package)
    restore_export(remapped, destination, PASSPHRASE)
    with destination_store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM vaults").fetchone()[0] == 1
        assert (
            connection.execute(
                f"SELECT COUNT(*) FROM {SOURCE_LESS_BARRIER_TABLE} WHERE vault_id=?",
                (destination_vault_id,),
            ).fetchone()[0]
            == 1
        )


def test_restore_rejects_ambiguous_package_vault_identity(
    tmp_path: Path,
) -> None:
    database = tmp_path / "source-less.sqlite3"
    store, record_id = _source_less_store(database)
    source_vault_id = store.vault_id()
    store.purge(
        "record",
        record_id,
        confirmation=store.purge_confirmation_phrase("record", record_id),
        compact=False,
    )
    package = tmp_path / "source-less.atcexp"
    create_export(database, package, PASSPHRASE)

    def add_casefold_collision(members: dict[str, bytes], _manifest: dict[str, object]) -> None:
        vault_name = "tables/vaults.jsonl"
        row = json.loads(members[vault_name].splitlines()[0])
        row["id"] = source_vault_id.upper()
        members[vault_name] += (
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()

    tampered = _rewrite_export(package, tmp_path / "ambiguous-vault.atcexp", add_casefold_collision)
    destination = tmp_path / "destination.sqlite3"
    CoreStore(destination).migrate()
    with pytest.raises(ValueError, match="ambiguous"):
        restore_export(tampered, destination, PASSPHRASE)


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
