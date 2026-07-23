"""Provider-neutral authenticated portable export and restore."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import IO, Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"ATCEXP1\x00"
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
MAX_RESTORE_ENTRY_BYTES = 512 * 1024 * 1024
EXCLUDED_TABLES = {
    "schema_migrations",
    "context_fts",
    "context_fts_data",
    "context_fts_idx",
    "context_fts_docsize",
    "context_fts_config",
    "integrity_groups",
    "integrity_group_members",
}


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    if len(passphrase) < 10:
        raise ValueError("export passphrase must contain at least 10 characters")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def _table_names(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(
        name
        for row in rows
        if (name := str(row[0])) not in EXCLUDED_TABLES and not name.startswith("context_fts_")
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    return value


def _source_schema_version(connection: sqlite3.Connection) -> int:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "schema_migrations" in tables:
        row = connection.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
        ).fetchone()
        return int(row[0]) if row is not None else 0
    if "vaults" in tables:
        columns = {str(row[1]) for row in connection.execute('PRAGMA table_info("vaults")')}
        if "schema_version" in columns:
            row = connection.execute(
                "SELECT COALESCE(MAX(schema_version),0) FROM vaults"
            ).fetchone()
            return int(row[0]) if row is not None else 0
    return 0


def _without_source_reference(
    table: str,
    document: dict[str, Any],
) -> dict[str, Any]:
    if table in {"context_candidates", "context_records"} and "source_id" in document:
        document["source_id"] = None
    if table == "context_record_versions":
        raw_snapshot = document.get("snapshot_json")
        if isinstance(raw_snapshot, str):
            try:
                snapshot = json.loads(raw_snapshot)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(snapshot, dict):
                    snapshot["source_id"] = None
                    document["snapshot_json"] = json.dumps(
                        snapshot,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
    return document


def _database_to_zip(
    database_path: Path,
    zip_path: Path,
    *,
    include_sources: bool,
    include_audit: bool,
) -> dict[str, Any]:
    hashes_by_file: dict[str, str] = {}
    counts: dict[str, int] = {}
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        schema_version = _source_schema_version(connection)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for table in _table_names(connection):
                lowered = table.casefold()
                if not include_sources and ("source" in lowered or "blob" in lowered):
                    continue
                if not include_audit and "audit" in lowered:
                    continue
                columns = [
                    str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
                ]
                digest = hashlib.sha256()
                count = 0
                with archive.open(f"tables/{table}.jsonl", "w") as output:
                    for row in connection.execute(f'SELECT * FROM "{table}"'):
                        document = {column: _json_value(row[column]) for column in columns}
                        if not include_sources:
                            document = _without_source_reference(table, document)
                        encoded = (
                            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
                        ).encode("utf-8")
                        output.write(encoded)
                        digest.update(encoded)
                        count += 1
                hashes_by_file[f"tables/{table}.jsonl"] = digest.hexdigest()
                counts[table] = count
            manifest = {
                "format": "all-the-context",
                "format_version": 1,
                "schema_version": schema_version,
                "include_sources": include_sources,
                "include_audit": include_audit,
                "tables": counts,
                "sha256": hashes_by_file,
            }
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            )
    finally:
        connection.close()
    return manifest


def _encrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    key = _derive_key(passphrase, salt)
    encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    with source.open("rb") as incoming, destination.open("wb") as outgoing:
        outgoing.write(MAGIC + salt + nonce)
        for chunk in iter(lambda: incoming.read(CHUNK_SIZE), b""):
            outgoing.write(encryptor.update(chunk))
        outgoing.write(encryptor.finalize())
        outgoing.write(encryptor.tag)


def create_export(
    database_path: Path,
    destination: Path,
    passphrase: str,
    *,
    include_sources: bool = False,
    include_audit: bool = False,
) -> dict[str, Any]:
    """Create an encrypted portable package without placing plaintext beside it."""
    database_path = database_path.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="atc-export-") as temporary:
        archive_path = Path(temporary) / "payload.zip"
        manifest = _database_to_zip(
            database_path,
            archive_path,
            include_sources=include_sources,
            include_audit=include_audit,
        )
        _encrypt_file(archive_path, destination, passphrase)
    return manifest


def _decrypt_file(source: Path, destination: Path, passphrase: str) -> None:
    total = source.stat().st_size
    header_size = len(MAGIC) + SALT_SIZE + NONCE_SIZE
    if total <= header_size + TAG_SIZE:
        raise ValueError("invalid or truncated export")
    with source.open("rb") as incoming:
        if incoming.read(len(MAGIC)) != MAGIC:
            raise ValueError("not an All The Context encrypted export")
        salt = incoming.read(SALT_SIZE)
        nonce = incoming.read(NONCE_SIZE)
        incoming.seek(-TAG_SIZE, os.SEEK_END)
        tag = incoming.read(TAG_SIZE)
        incoming.seek(header_size)
        remaining = total - header_size - TAG_SIZE
        decryptor = Cipher(
            algorithms.AES(_derive_key(passphrase, salt)), modes.GCM(nonce, tag)
        ).decryptor()
        with destination.open("wb") as outgoing:
            while remaining:
                chunk = incoming.read(min(CHUNK_SIZE, remaining))
                if not chunk:
                    raise ValueError("truncated encrypted payload")
                remaining -= len(chunk)
                outgoing.write(decryptor.update(chunk))
            outgoing.write(decryptor.finalize())


def _decode_value(value: Any) -> Any:
    if isinstance(value, dict) and set(value) == {"$bytes"}:
        return base64.b64decode(value["$bytes"], validate=True)
    return value


def _iter_jsonl(stream: IO[bytes]) -> Iterable[dict[str, Any]]:
    for line in stream:
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("portable table row must be a JSON object")
        yield {key: _decode_value(item) for key, item in value.items()}


def _normalize_candidate_row(
    row: dict[str, Any],
    *,
    source_schema_version: int,
) -> None:
    if source_schema_version >= 5 and "disposition" in row:
        return
    status = str(row.get("approval_status", "pending"))
    disposition = {
        "approved": "applied",
        "rejected": "ignored",
    }.get(status, "staged")
    row.setdefault("observed_at", row.get("created_at"))
    row.setdefault("observation_origin", "legacy_migration")
    row.setdefault("disposition", disposition)
    row.setdefault("record_id", None)
    if status in {"approved", "rejected"}:
        fallback = (
            "approved before automatic policy"
            if status == "approved"
            else "rejected before automatic policy"
        )
        row.setdefault("decision_reason", row.get("review_reason") or fallback)
        row.setdefault("decided_at", row.get("reviewed_at"))
        row.setdefault("policy_version", "legacy-review-v1")
    else:
        row.setdefault("decision_reason", None)
        row.setdefault("decided_at", None)
        row.setdefault("policy_version", None)


def _normalize_record_row(
    row: dict[str, Any],
    *,
    source_schema_version: int,
) -> None:
    if source_schema_version >= 5 and "observation_origin" in row:
        return
    row.setdefault(
        "observed_at",
        row.get("valid_from") or row.get("created_at"),
    )
    row.setdefault("observation_origin", "legacy_migration")
    row.setdefault("policy_version", "legacy-review-v1")


def _rebuild_context_fts(connection: sqlite3.Connection, tables: set[str]) -> None:
    if "context_fts" not in tables or "context_records" not in tables:
        return
    connection.execute("DELETE FROM context_fts")
    rows = connection.execute(
        "SELECT id,content,kind,tags_json,scopes_json FROM context_records "
        "WHERE approval_status='approved' AND deleted_at IS NULL"
    ).fetchall()
    for record_id, content, kind, tags_json, scopes_json in rows:
        tags = json.loads(str(tags_json))
        scopes = json.loads(str(scopes_json))
        connection.execute(
            "INSERT INTO context_fts(record_id,content,kind,tags,scopes) VALUES(?,?,?,?,?)",
            (
                record_id,
                content,
                kind,
                " ".join(str(value) for value in tags),
                " ".join(str(value) for value in scopes),
            ),
        )


def _post_restore_upgrade(
    connection: sqlite3.Connection,
    tables: set[str],
    columns_by_table: dict[str, set[str]],
) -> None:
    destination_schema = 0
    if "schema_migrations" in tables:
        row = connection.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations"
        ).fetchone()
        destination_schema = int(row[0]) if row is not None else 0

    if "context_candidates" in tables:
        candidate_columns = columns_by_table["context_candidates"]
        if {
            "record_id",
            "disposition",
            "approval_status",
        }.issubset(candidate_columns) and "context_records" in tables:
            connection.execute(
                "UPDATE context_candidates SET record_id=("
                "SELECT r.id FROM context_records r "
                "WHERE r.candidate_id=context_candidates.id LIMIT 1"
                ") WHERE disposition IN ('applied','reinforced') AND record_id IS NULL"
            )
        if "context_observation_links" in tables and {"record_id", "disposition"}.issubset(
            candidate_columns
        ):
            connection.execute(
                "INSERT OR IGNORE INTO context_observation_links"
                "(observation_id,record_id,relationship,created_at) "
                "SELECT id,record_id,'applied',COALESCE(decided_at,created_at) "
                "FROM context_candidates WHERE record_id IS NOT NULL "
                "AND disposition IN ('applied','reinforced')"
            )

    if "context_records" in tables:
        record_columns = columns_by_table["context_records"]
        assignments: list[str] = []
        if "observed_at" in record_columns:
            assignments.append("observed_at=COALESCE(observed_at,valid_from,created_at)")
        if "observation_origin" in record_columns:
            assignments.append("observation_origin=COALESCE(observation_origin,'legacy_migration')")
        if "policy_version" in record_columns:
            assignments.append("policy_version=COALESCE(policy_version,'legacy-review-v1')")
        if assignments:
            connection.execute(f"UPDATE context_records SET {','.join(assignments)}")

    if "vaults" in tables and destination_schema:
        connection.execute(
            "UPDATE vaults SET schema_version=? WHERE schema_version<?",
            (destination_schema, destination_schema),
        )
    if "memory_policies" in tables and "vaults" in tables:
        connection.execute(
            "INSERT OR IGNORE INTO memory_policies"
            "(vault_id,mode,sensitive_mode,inference_mode,policy_version,"
            "created_at,updated_at) "
            "SELECT id,'automatic','local_only','corroborate','automatic-v1',"
            "strftime('%Y-%m-%dT%H:%M:%fZ','now'),"
            "strftime('%Y-%m-%dT%H:%M:%fZ','now') FROM vaults"
        )
    _rebuild_context_fts(connection, tables)


def restore_export(
    source: Path,
    database_path: Path,
    passphrase: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Validate and duplicate-safely restore known rows into a migrated database."""
    source = source.resolve()
    database_path = database_path.resolve()
    with tempfile.TemporaryDirectory(prefix="atc-restore-") as temporary:
        archive_path = Path(temporary) / "payload.zip"
        _decrypt_file(source, archive_path, passphrase)
        with zipfile.ZipFile(archive_path) as archive:
            infos = archive.infolist()
            if any(
                info.file_size > MAX_RESTORE_ENTRY_BYTES
                or Path(info.filename).is_absolute()
                or ".." in Path(info.filename).parts
                for info in infos
            ):
                raise ValueError("unsafe or oversized export entry")
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != "all-the-context" or manifest.get("format_version") != 1:
                raise ValueError("unsupported export format")
            for name, expected in manifest.get("sha256", {}).items():
                actual = hashlib.sha256(archive.read(name)).hexdigest()
                if actual != expected:
                    raise ValueError(f"integrity check failed for {name}")
            if dry_run:
                return {"valid": True, "dry_run": True, "manifest": manifest}
            connection = sqlite3.connect(database_path)
            try:
                all_tables = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    ).fetchall()
                }
                existing = set(_table_names(connection))
                columns_by_table = {
                    table: {
                        str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')
                    }
                    for table in existing
                }
                manifest_tables = manifest.get("tables", {})
                if not isinstance(manifest_tables, dict):
                    raise ValueError("export manifest tables must be an object")
                try:
                    source_schema_version = int(manifest.get("schema_version", 0))
                except (TypeError, ValueError) as error:
                    raise ValueError("export schema version is invalid") from error
                include_sources = bool(manifest.get("include_sources", False))
                blocked_records: set[str] = set()
                blocked_sources: set[str] = set()
                if "purge_tombstones" in existing:
                    for stable_id, target_type in connection.execute(
                        "SELECT stable_id,target_type FROM purge_tombstones"
                    ):
                        (blocked_records if target_type == "record" else blocked_sources).add(
                            str(stable_id)
                        )
                if "purge_tombstones" in manifest_tables:
                    with archive.open("tables/purge_tombstones.jsonl") as stream:
                        for row in _iter_jsonl(stream):
                            target = (
                                blocked_records
                                if row.get("target_type") == "record"
                                else blocked_sources
                            )
                            target.add(str(row["stable_id"]))
                blocked_candidates: set[str] = set()
                if (blocked_records or blocked_sources) and "context_records" in manifest_tables:
                    with archive.open("tables/context_records.jsonl") as stream:
                        for row in _iter_jsonl(stream):
                            record_id = str(row.get("id"))
                            if (
                                record_id in blocked_records
                                or str(row.get("source_id")) in blocked_sources
                            ):
                                blocked_records.add(record_id)
                                if row.get("candidate_id"):
                                    blocked_candidates.add(str(row["candidate_id"]))
                if (blocked_records or blocked_sources) and "context_candidates" in manifest_tables:
                    with archive.open("tables/context_candidates.jsonl") as stream:
                        for row in _iter_jsonl(stream):
                            if (
                                str(row.get("source_id")) in blocked_sources
                                or str(row.get("supersedes")) in blocked_records
                                or str(row.get("record_id")) in blocked_records
                            ):
                                blocked_candidates.add(str(row["id"]))
                if blocked_records and "context_observation_links" in manifest_tables:
                    with archive.open("tables/context_observation_links.jsonl") as stream:
                        for row in _iter_jsonl(stream):
                            if str(row.get("record_id")) in blocked_records:
                                blocked_candidates.add(str(row["observation_id"]))
                if blocked_records and "context_errors" in manifest_tables:
                    with archive.open("tables/context_errors.jsonl") as stream:
                        for row in _iter_jsonl(stream):
                            if str(row.get("record_id")) in blocked_records and row.get(
                                "candidate_id"
                            ):
                                blocked_candidates.add(str(row["candidate_id"]))
                blocked_source_hashes: set[str] = set()
                if blocked_sources and "source_records" in manifest_tables:
                    with archive.open("tables/source_records.jsonl") as stream:
                        for row in _iter_jsonl(stream):
                            if str(row.get("id")) in blocked_sources:
                                blocked_source_hashes.add(str(row.get("content_hash")))
                with connection:
                    for table in manifest_tables:
                        if table not in existing:
                            continue
                        name = f"tables/{table}.jsonl"
                        with archive.open(name) as stream:
                            for row in _iter_jsonl(stream):
                                if table == "context_records" and (
                                    str(row.get("id")) in blocked_records
                                    or str(row.get("source_id")) in blocked_sources
                                ):
                                    continue
                                if table == "context_candidates" and (
                                    str(row.get("id")) in blocked_candidates
                                    or str(row.get("source_id")) in blocked_sources
                                ):
                                    continue
                                if (
                                    table == "context_record_versions"
                                    and str(row.get("record_id")) in blocked_records
                                ):
                                    continue
                                if table == "context_observation_links" and (
                                    str(row.get("record_id")) in blocked_records
                                    or str(row.get("observation_id")) in blocked_candidates
                                ):
                                    continue
                                if table == "context_errors" and (
                                    str(row.get("record_id")) in blocked_records
                                    or str(row.get("candidate_id")) in blocked_candidates
                                ):
                                    continue
                                if (
                                    table == "edge_proposal_receipts"
                                    and str(row.get("candidate_id")) in blocked_candidates
                                ):
                                    continue
                                if (
                                    table == "deletion_tombstones"
                                    and str(row.get("record_id")) in blocked_records
                                ):
                                    continue
                                if (
                                    table == "replication_events"
                                    and str(row.get("record_id")) in blocked_records
                                ):
                                    continue
                                if (
                                    table == "source_records"
                                    and str(row.get("id")) in blocked_sources
                                ):
                                    continue
                                if (
                                    table == "source_blobs"
                                    and str(row.get("content_hash")) in blocked_source_hashes
                                ):
                                    continue
                                if table == "ingestion_batches" and blocked_candidates.intersection(
                                    json.loads(str(row.get("candidate_ids_json", "[]")))
                                ):
                                    # The batch hash covers the purged proposal payload.
                                    row["candidate_ids_json"] = "[]"
                                    row["request_hash"] = secrets.token_hex(16)
                                if not include_sources:
                                    row = _without_source_reference(table, row)
                                if table == "context_candidates":
                                    _normalize_candidate_row(
                                        row,
                                        source_schema_version=source_schema_version,
                                    )
                                elif table == "context_records":
                                    _normalize_record_row(
                                        row,
                                        source_schema_version=source_schema_version,
                                    )
                                row = {
                                    column: value
                                    for column, value in row.items()
                                    if column in columns_by_table[table]
                                }
                                if not row:
                                    continue
                                columns = list(row)
                                quoted = ",".join(f'"{column}"' for column in columns)
                                placeholders = ",".join("?" for _ in columns)
                                connection.execute(
                                    (
                                        f'INSERT OR IGNORE INTO "{table}" ({quoted}) '
                                        f"VALUES ({placeholders})"
                                    ),
                                    [row[column] for column in columns],
                                )
                    _post_restore_upgrade(connection, all_tables, columns_by_table)
                    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise ValueError(
                            "restored export contains unresolved foreign-key references"
                        )
            finally:
                connection.close()
    return {"valid": True, "dry_run": False, "manifest": manifest}
