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
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import IO, Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .config import MAX_IMPORT_BYTES
from .memory_policy import UNKEYED_CONFLICT_KINDS
from .storage import (
    _ARCHIVE_PURGE_BARRIER_TABLE,
    _ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE,
    SOURCE_BLOB_CHUNK_BYTES,
    CoreStore,
    _archive_purge_barrier_digest,
    _archive_source_less_purge_barrier_digest,
    _mutation_evidence_hash,
    _normalize_actor,
    _stable_record_key_from_row,
    _user_action_evidence_hash,
)

MAGIC = b"ATCEXP1\x00"
EXPORT_FORMAT_VERSION = 1
# Version 0 is the pre-Core/minimal export shape.  Versions 1 through 19 are
# the current and retained legacy Core schemas represented by this checkout.
SUPPORTED_SCHEMA_VERSIONS = frozenset(range(20))
SALT_SIZE = 16
NONCE_SIZE = 12
TAG_SIZE = 16
CHUNK_SIZE = 1024 * 1024
MAX_RESTORE_ENTRY_BYTES = 512 * 1024 * 1024
# These tables contain machine-local principals, credentials, grants, or
# in-flight session state.  No existing product decision makes that security
# state portable; keep this list explicit so a new table cannot silently join
# the ordinary data export policy.
NON_PORTABLE_SECURITY_TABLES = frozenset(
    {
        "client_registrations",
        "permission_grants",
        "remote_edge_clients",
        "ingestion_sessions",
        "ingestion_batches",
        "import_operations",
        "secret_refusal_receipts",
    }
)
EXCLUDED_TABLES = {
    "schema_migrations",
    "context_fts",
    "context_fts_data",
    "context_fts_idx",
    "context_fts_docsize",
    "context_fts_config",
    "integrity_groups",
    "integrity_group_members",
    "source_blob_chunks",
    # Rebuilt from current and historical record lineage on each destination;
    # never treat this derived lookup as portable authority.
    "context_record_archive_identities",
} | NON_PORTABLE_SECURITY_TABLES
CAPTURE_RUNTIME_TABLES = frozenset(
    {
        "capture_sources",
        "capture_events",
        "capture_items",
        "capture_checkpoints",
        "capture_runs",
    }
)

# This is deliberately an explicit inventory.  Restore is a security boundary:
# a newly added portable table must be added here and to the reference inventory
# below before it can silently introduce a destination-backed edge.
PORTABLE_TABLE_INVENTORY = frozenset(
    {
        "archive_purge_barriers",
        "archive_source_less_purge_barriers",
        "audit_events",
        "client_registrations",
        "context_candidates",
        "context_errors",
        "context_observation_links",
        "context_record_versions",
        "context_records",
        "context_user_mutations",
        "deletion_tombstones",
        "edge_proposal_receipts",
        "import_operations",
        "ingestion_batches",
        "ingestion_sessions",
        "memory_policies",
        "permission_grants",
        "purge_jobs",
        "purge_tombstones",
        "remote_edge_clients",
        "replication_checkpoints",
        "replication_events",
        "secret_refusal_receipts",
        "source_blobs",
        "source_deletion_members",
        "source_records",
        "vaults",
    }
)

# (table, column, target table).  This includes SQLite foreign keys and the
# intentional semantic edges that SQLite cannot express because purge removes
# the target or because old schemas predate the column.
PORTABLE_RELATIONAL_REFERENCE_INVENTORY = frozenset(
    {
        ("archive_purge_barriers", "vault_id", "vaults"),
        ("archive_purge_barriers", "source_id", "source_records"),
        ("archive_source_less_purge_barriers", "vault_id", "vaults"),
        ("audit_events", "vault_id", "vaults"),
        ("client_registrations", "vault_id", "vaults"),
        ("context_candidates", "vault_id", "vaults"),
        ("context_candidates", "session_id", "ingestion_sessions"),
        ("context_candidates", "source_id", "source_records"),
        ("context_candidates", "submitted_by_client_id", "client_registrations"),
        ("context_candidates", "record_id", "context_records"),
        ("context_candidates", "supersedes", "context_records"),
        ("context_candidates", "capture_source_id", "capture_sources"),
        ("context_candidates", "capture_event_id", "capture_events"),
        ("context_errors", "vault_id", "vaults"),
        ("context_errors", "candidate_id", "context_candidates"),
        ("context_errors", "record_id", "context_records"),
        ("context_observation_links", "observation_id", "context_candidates"),
        ("context_observation_links", "record_id", "context_records"),
        ("context_record_versions", "record_id", "context_records"),
        ("context_records", "vault_id", "vaults"),
        ("context_records", "candidate_id", "context_candidates"),
        ("context_records", "source_id", "source_records"),
        ("context_records", "supersedes", "context_records"),
        ("context_user_mutations", "vault_id", "vaults"),
        ("context_user_mutations", "record_id", "context_records"),
        ("context_user_mutations", "evidence_id", "context_record_versions"),
        ("deletion_tombstones", "vault_id", "vaults"),
        ("deletion_tombstones", "record_id", "context_records"),
        ("deletion_tombstones", "deletion_source_id", "source_records"),
        ("deletion_tombstones", "rebuild_session_id", "ingestion_sessions"),
        ("edge_proposal_receipts", "vault_id", "vaults"),
        ("edge_proposal_receipts", "candidate_id", "context_candidates"),
        ("import_operations", "vault_id", "vaults"),
        ("import_operations", "source_id", "source_records"),
        ("import_operations", "content_hash", "source_blobs"),
        ("ingestion_batches", "session_id", "ingestion_sessions"),
        ("ingestion_sessions", "vault_id", "vaults"),
        ("ingestion_sessions", "client_id", "client_registrations"),
        ("memory_policies", "vault_id", "vaults"),
        ("permission_grants", "client_id", "client_registrations"),
        ("purge_jobs", "vault_id", "vaults"),
        ("purge_jobs", "target_id", "purge_target"),
        ("purge_tombstones", "vault_id", "vaults"),
        ("purge_tombstones", "replication_event_id", "replication_events"),
        ("replication_checkpoints", "vault_id", "vaults"),
        ("replication_events", "vault_id", "vaults"),
        ("replication_events", "record_id", "context_records"),
        ("secret_refusal_receipts", "vault_id", "vaults"),
        ("source_deletion_members", "source_id", "source_records"),
        ("source_deletion_members", "record_id", "context_records"),
        ("source_records", "vault_id", "vaults"),
        ("source_records", "content_hash", "source_blobs"),
    }
)

# (table, JSON column, JSON member, target type).  Lists are intentionally
# enumerated rather than recursively treating arbitrary provider metadata as an
# ID graph: imported text and structured values are data, not authority.
PORTABLE_JSON_REFERENCE_INVENTORY = frozenset(
    {
        ("ingestion_sessions", "accessible_sources_json", "*", "source_records"),
        ("ingestion_sessions", "unavailable_sources_json", "*", "source_records"),
        ("ingestion_batches", "candidate_ids_json", "*", "context_candidates"),
        ("context_candidates", "allowed_clients_json", "*", "client_principals"),
        ("context_candidates", "denied_clients_json", "*", "client_principals"),
        ("context_records", "allowed_clients_json", "*", "client_principals"),
        ("context_records", "denied_clients_json", "*", "client_principals"),
        ("context_record_versions", "snapshot_json", "source_id", "source_records"),
        ("context_record_versions", "snapshot_json", "candidate_id", "context_candidates"),
        ("context_record_versions", "snapshot_json", "supersedes", "context_records"),
        ("context_record_versions", "snapshot_json", "allowed_clients", "client_principals"),
        ("context_record_versions", "snapshot_json", "denied_clients", "client_principals"),
        ("replication_events", "payload_json", "record_id", "context_records"),
        ("replication_events", "payload_json", "source_id", "source_records"),
        ("replication_events", "payload_json", "candidate_id", "context_candidates"),
        ("replication_events", "payload_json", "allowed_clients", "client_principals"),
        ("replication_events", "payload_json", "denied_clients", "client_principals"),
        ("audit_events", "record_ids_json", "*", "context_records"),
        ("audit_events", "denied_record_ids_json", "*", "context_records"),
        ("source_records", "metadata_json", "import_operation_id", "import_operations"),
        (
            "source_records",
            "metadata_json",
            "rebuild_published_session_id",
            "ingestion_sessions",
        ),
        ("import_operations", "result_json", "source.id", "source_records"),
        ("import_operations", "result_json", "source.content_hash", "source_blobs"),
        ("import_operations", "result_json", "session.session_id", "ingestion_sessions"),
        ("import_operations", "result_json", "candidate_ids", "context_candidates"),
        ("import_operations", "result_json", "record_ids", "context_records"),
        ("import_operations", "result_json", "withdrawn_record_ids", "context_records"),
    }
)

_PORTABLE_ROW_KEYS: dict[str, tuple[str, ...]] = {
    "archive_purge_barriers": ("vault_id", "source_id", "source_kind", "barrier_digest"),
    "archive_source_less_purge_barriers": ("vault_id", "source_kind", "barrier_digest"),
    "audit_events": ("id",),
    "client_registrations": ("id",),
    "context_candidates": ("id",),
    "context_errors": ("id",),
    "context_observation_links": ("observation_id", "record_id"),
    "context_record_versions": ("id",),
    "context_records": ("id",),
    "context_user_mutations": ("id",),
    "deletion_tombstones": ("record_id",),
    "edge_proposal_receipts": ("vault_id", "proposal_id"),
    "import_operations": ("id",),
    "ingestion_batches": ("id",),
    "ingestion_sessions": ("id",),
    "memory_policies": ("vault_id",),
    "permission_grants": ("id",),
    "purge_jobs": ("id",),
    "purge_tombstones": ("stable_id",),
    "remote_edge_clients": ("id",),
    "replication_checkpoints": ("relay_id",),
    "replication_events": ("id",),
    "secret_refusal_receipts": ("id",),
    "source_blobs": ("content_hash",),
    "source_deletion_members": ("source_id", "record_id"),
    "source_records": ("id",),
    "vaults": ("id",),
}

_PORTABLE_ID_TYPES: dict[str, tuple[str, str]] = {
    "audit_events": ("id", "audit_event"),
    "client_registrations": ("id", "client"),
    "context_candidates": ("id", "candidate"),
    "context_errors": ("id", "context_error"),
    "context_record_versions": ("id", "record_version"),
    "context_records": ("id", "record"),
    "context_user_mutations": ("id", "user_mutation"),
    "edge_proposal_receipts": ("proposal_id", "edge_proposal"),
    "import_operations": ("id", "import_operation"),
    "ingestion_batches": ("id", "ingestion_batch"),
    "ingestion_sessions": ("id", "ingestion_session"),
    "permission_grants": ("id", "permission_grant"),
    "purge_jobs": ("id", "purge_job"),
    "remote_edge_clients": ("id", "remote_edge_client"),
    "replication_checkpoints": ("relay_id", "replication_checkpoint"),
    "replication_events": ("id", "replication_event"),
    "secret_refusal_receipts": ("id", "secret_refusal_receipt"),
    "source_blobs": ("content_hash", "source_blob"),
    "source_records": ("id", "source"),
    "vaults": ("id", "vault"),
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


def _has_secret_boundary(database_path: Path) -> bool:
    connection = sqlite3.connect(database_path)
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        connection.close()
    return {"vaults", "context_candidates", "secret_refusal_receipts"}.issubset(tables)


def _repair_secret_boundary(database_path: Path) -> None:
    """Repair a migrated Core ledger before moving its durable bytes."""

    if not _has_secret_boundary(database_path):
        return
    store = CoreStore(database_path)
    # Export is also a restart boundary for databases that already recorded
    # migration 013 before evidence columns existed.  Repair those rows before
    # they become portable material.
    store.migrate()
    store.repair_preledger_secrets()


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
    if table == "ingestion_sessions":
        # A source-less package cannot carry provider/source identities that
        # would later be mistaken for destination rows.
        for field in ("accessible_sources_json", "unavailable_sources_json"):
            if field in document:
                document[field] = "[]"
    if table == "import_operations":
        # Import-operation source pointers are observer state, not portable
        # source authority.  Keep the operation row but detach source bytes.
        document["source_id"] = None
        document["content_hash"] = None
        raw_result = document.get("result_json")
        if isinstance(raw_result, str):
            try:
                result = json.loads(raw_result)
            except (TypeError, ValueError):
                result = None
            if isinstance(result, dict):
                result.pop("source", None)
                session = result.get("session")
                if isinstance(session, dict):
                    session["accessible_sources"] = []
                    session["unavailable_sources"] = []
                document["result_json"] = json.dumps(
                    result,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
    if table == "deletion_tombstones":
        document["deletion_source_id"] = None
    return document


def _without_machine_local_references(table: str, document: dict[str, Any]) -> dict[str, Any]:
    """Keep ordinary facts portable without dangling machine-local FKs."""

    if "capture_source_id" in document:
        document["capture_source_id"] = None
    if "capture_event_id" in document:
        document["capture_event_id"] = None
    if table == "context_candidates":
        # Client registrations and ingestion sessions are deliberately not
        # portable.  Preserve the candidate itself while removing references
        # that could bind it to a destination's unrelated security state.
        if "session_id" in document:
            document["session_id"] = None
        if "submitted_by_client_id" in document:
            document["submitted_by_client_id"] = None
    return document


def _write_source_chunks(
    connection: sqlite3.Connection,
    archive: zipfile.ZipFile,
    hashes_by_file: dict[str, str],
) -> list[dict[str, Any]]:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "source_blob_chunks" not in tables:
        return []
    descriptors: list[dict[str, Any]] = []
    for content_hash, chunk_index, content, byte_size in connection.execute(
        "SELECT content_hash,chunk_index,content,byte_size "
        "FROM source_blob_chunks ORDER BY content_hash,chunk_index"
    ):
        chunk = bytes(content)
        index = int(chunk_index)
        expected_size = int(byte_size)
        if len(chunk) != expected_size or len(chunk) > SOURCE_BLOB_CHUNK_BYTES or index < 0:
            raise ValueError("stored source chunk is invalid")
        entry = f"source-chunks/{content_hash}/{index:08d}.bin"
        with archive.open(entry, "w") as output:
            output.write(chunk)
        digest = hashlib.sha256(chunk).hexdigest()
        hashes_by_file[entry] = digest
        descriptors.append(
            {
                "byte_size": len(chunk),
                "chunk_index": index,
                "content_hash": str(content_hash),
                "path": entry,
            }
        )
    return descriptors


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
        connection.execute("BEGIN")
        schema_version = _source_schema_version(connection)
        source_tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        source_columns = {
            table: {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
            for table in source_tables
        }
        if include_sources:
            _validate_source_blob_storage(connection, source_tables, source_columns)
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for table in _table_names(connection):
                if table in CAPTURE_RUNTIME_TABLES:
                    continue
                if table in NON_PORTABLE_SECURITY_TABLES:
                    continue
                lowered = table.casefold()
                if (
                    not include_sources
                    and table != _ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE
                    and ("source" in lowered or "blob" in lowered)
                ):
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
                        if table == _ARCHIVE_PURGE_BARRIER_TABLE:
                            _archive_purge_barrier_key(document)
                        elif table == _ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE:
                            _archive_source_less_purge_barrier_key(document)
                        if not include_sources:
                            document = _without_source_reference(table, document)
                        document = _without_machine_local_references(table, document)
                        encoded = (
                            json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
                        ).encode("utf-8")
                        output.write(encoded)
                        digest.update(encoded)
                        count += 1
                hashes_by_file[f"tables/{table}.jsonl"] = digest.hexdigest()
                counts[table] = count
            source_chunks = (
                _write_source_chunks(connection, archive, hashes_by_file) if include_sources else []
            )
            manifest = {
                "format": "all-the-context",
                "format_version": EXPORT_FORMAT_VERSION,
                "schema_version": schema_version,
                "include_sources": include_sources,
                "include_audit": include_audit,
                "tables": counts,
                "sha256": hashes_by_file,
                "source_chunks": source_chunks,
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
    _repair_secret_boundary(database_path)
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


def _json_object_from_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Decode JSON objects without silently shadowing a repeated key."""

    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("portable JSON object contains duplicate keys")
        value[key] = item
    return value


def _iter_jsonl(stream: IO[bytes]) -> Iterable[dict[str, Any]]:
    for line in stream:
        if not line.strip():
            continue
        value = json.loads(line, object_pairs_hook=_json_object_from_pairs)
        if not isinstance(value, dict):
            raise ValueError("portable table row must be a JSON object")
        yield {key: _decode_value(item) for key, item in value.items()}


def _archive_purge_barrier_key(row: dict[str, Any] | sqlite3.Row) -> tuple[str, str, str, str]:
    """Validate and return the content-free key of one archive purge barrier."""

    vault_id = row["vault_id"]
    source_id = row["source_id"]
    source_kind = row["source_kind"]
    barrier_digest = row["barrier_digest"]
    purged_at = row["purged_at"]
    if (
        set(row.keys()) != {"vault_id", "source_id", "source_kind", "barrier_digest", "purged_at"}
        or not isinstance(vault_id, str)
        or not vault_id.strip()
        or not isinstance(source_id, str)
        or not source_id.strip()
        or not isinstance(source_kind, str)
        or not source_kind.strip()
        or source_kind != source_kind.casefold()
        or not isinstance(barrier_digest, str)
        or len(barrier_digest) != 64
        or any(character not in "0123456789abcdef" for character in barrier_digest)
        or not isinstance(purged_at, str)
        or not purged_at.strip()
    ):
        raise ValueError("archive purge barrier row is invalid")
    return vault_id, source_id, source_kind, barrier_digest


def _archive_purge_barrier_key_for_content(
    row: dict[str, Any],
) -> tuple[str, str, str, str] | None:
    try:
        digest = _archive_purge_barrier_digest(row)
    except KeyError:
        # Pre-policy portable rows have no observation_origin column and
        # cannot safely prove archive lineage during this pre-scan.
        return None
    source_id = row.get("source_id")
    kind = row.get("kind")
    vault_id = row.get("vault_id")
    if (
        digest is None
        or not isinstance(vault_id, str)
        or not isinstance(source_id, str)
        or not isinstance(kind, str)
    ):
        return None
    return vault_id, source_id, kind.casefold(), digest


def _archive_source_less_purge_barrier_key(
    row: dict[str, Any] | sqlite3.Row,
) -> tuple[str, str, str]:
    """Validate and return one opaque source-less archive purge key."""

    vault_id = row["vault_id"]
    source_kind = row["source_kind"]
    barrier_digest = row["barrier_digest"]
    purged_at = row["purged_at"]
    if (
        set(row.keys()) != {"vault_id", "source_kind", "barrier_digest", "purged_at"}
        or not isinstance(vault_id, str)
        or not vault_id.strip()
        or not isinstance(source_kind, str)
        or source_kind != source_kind.casefold()
        or source_kind not in UNKEYED_CONFLICT_KINDS
        or not isinstance(barrier_digest, str)
        or len(barrier_digest) != 64
        or any(character not in "0123456789abcdef" for character in barrier_digest)
        or not isinstance(purged_at, str)
        or not purged_at.strip()
    ):
        raise ValueError("source-less archive purge barrier row is invalid")
    return vault_id, source_kind, barrier_digest


def _archive_source_less_purge_barrier_key_for_content(
    row: dict[str, Any],
) -> tuple[str, str, str] | None:
    try:
        digest = _archive_source_less_purge_barrier_digest(row)
    except (KeyError, TypeError, ValueError):
        return None
    vault_id = row.get("vault_id")
    kind = row.get("kind")
    if (
        digest is None
        or not isinstance(vault_id, str)
        or not isinstance(kind, str)
        or kind.casefold() not in UNKEYED_CONFLICT_KINDS
    ):
        return None
    return vault_id, kind.casefold(), digest


def _portable_vault_ids(
    archive: zipfile.ZipFile,
    manifest_tables: dict[str, Any],
) -> set[str]:
    """Return the package-authenticated vault identities.

    These identities are the only package-to-destination mapping available to
    the format.  A destination row is not a mapping declaration: allowing one
    to extend this set would let an imported barrier be reassigned to an
    unrelated preexisting vault.
    """

    if "vaults" not in manifest_tables:
        return set()
    vault_ids: set[str] = set()
    folded_vault_ids: set[str] = set()
    with archive.open("tables/vaults.jsonl") as stream:
        for row in _iter_jsonl(stream):
            vault_id = row.get("id")
            if not isinstance(vault_id, str) or not vault_id.strip():
                raise ValueError("export vault row has an invalid id")
            if vault_id in vault_ids:
                raise ValueError("export vault rows are duplicated")
            folded_vault_id = vault_id.casefold()
            if folded_vault_id in folded_vault_ids:
                raise ValueError("export vault rows have ambiguous identities")
            vault_ids.add(vault_id)
            folded_vault_ids.add(folded_vault_id)
    return vault_ids


def _manifest_schema_version(manifest: dict[str, Any]) -> int:
    """Validate the explicit integer schema versions retained by this format."""

    raw_version = manifest.get("schema_version", 0)
    if type(raw_version) is not int or raw_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError("export schema version must be an integer from 0 through 19")
    return raw_version


def _validate_manifest_versions(manifest: dict[str, Any]) -> int:
    """Validate format and schema versions without coercing JSON values."""

    if (
        manifest.get("format") != "all-the-context"
        or type(manifest.get("format_version")) is not int
        or manifest.get("format_version") != EXPORT_FORMAT_VERSION
    ):
        raise ValueError("unsupported export format")
    return _manifest_schema_version(manifest)


def _validate_package_vault_binding(
    archive: zipfile.ZipFile,
    manifest_tables: dict[str, Any],
    *,
    include_sources: bool,
) -> set[str]:
    """Validate one package-wide vault identity before touching the destination.

    A portable Core database is single-vault.  The declaration in ``vaults``
    is the only package-side identity/remap authority; every serialized
    authoritative row that carries ``vault_id`` must use that exact identity.
    Destination rows are deliberately not consulted here, so an existing
    destination vault cannot authorize a cross-vault import.  Pre-Core minimal
    exports without a vault table remain valid only when they contain no vault
    references.
    """

    package_vault_ids = _portable_vault_ids(archive, manifest_tables)
    if "vaults" in manifest_tables:
        if len(package_vault_ids) != 1:
            raise ValueError("portable export must declare exactly one package vault")
        package_vault_id = next(iter(package_vault_ids))
    else:
        package_vault_id = None

    package_source_ids: set[str] = set()
    for table in manifest_tables:
        if (
            table in CAPTURE_RUNTIME_TABLES
            or table in NON_PORTABLE_SECURITY_TABLES
            or table == "vaults"
        ):
            continue
        with archive.open(f"tables/{table}.jsonl") as stream:
            for row in _iter_jsonl(stream):
                if "vault_id" in row:
                    vault_id = row["vault_id"]
                    if (
                        type(vault_id) is not str
                        or not vault_id.strip()
                        or package_vault_id is None
                        or vault_id != package_vault_id
                    ):
                        raise ValueError(
                            f"export table {table} row is not bound to the package vault"
                        )
                if table == "source_records":
                    source_id = row.get("id")
                    if isinstance(source_id, str) and source_id:
                        package_source_ids.add(source_id)

    if include_sources:
        # Source-inclusive packages must not turn a source_id into an implicit
        # lookup into the destination database.  Source-free exports clear
        # these references during restore, so this check is intentionally
        # scoped to the format that preserves them.
        for table in ("context_candidates", "context_records"):
            if table not in manifest_tables:
                continue
            with archive.open(f"tables/{table}.jsonl") as stream:
                for row in _iter_jsonl(stream):
                    source_id = row.get("source_id")
                    if source_id is not None and (
                        type(source_id) is not str or source_id not in package_source_ids
                    ):
                        raise ValueError(
                            f"export {table} source reference is not bound to a package source"
                        )
    if "context_record_versions" in manifest_tables:
        with archive.open("tables/context_record_versions.jsonl") as stream:
            for row in _iter_jsonl(stream):
                snapshot_json = row.get("snapshot_json")
                if not isinstance(snapshot_json, str):
                    continue
                try:
                    snapshot = json.loads(
                        snapshot_json,
                        object_pairs_hook=_json_object_from_pairs,
                    )
                except (TypeError, ValueError):
                    continue
                if not isinstance(snapshot, dict):
                    continue
                if "vault_id" in snapshot:
                    vault_id = snapshot["vault_id"]
                    if (
                        type(vault_id) is not str
                        or not vault_id.strip()
                        or package_vault_id is None
                        or vault_id != package_vault_id
                    ):
                        raise ValueError(
                            "export record version snapshot is not bound to the package vault"
                        )
                if include_sources:
                    source_id = snapshot.get("source_id")
                    if source_id is not None and (
                        type(source_id) is not str or source_id not in package_source_ids
                    ):
                        raise ValueError(
                            "export record version source reference is not bound to a "
                            "package source"
                        )
    if include_sources and "source_deletion_members" in manifest_tables:
        with archive.open("tables/source_deletion_members.jsonl") as stream:
            for row in _iter_jsonl(stream):
                source_id = row.get("source_id")
                if type(source_id) is not str or source_id not in package_source_ids:
                    raise ValueError(
                        "export source deletion reference is not bound to a package source"
                    )

    return package_vault_ids


def _iter_portable_table_rows(
    archive: zipfile.ZipFile,
    table: str,
) -> Iterator[dict[str, Any]]:
    with archive.open(f"tables/{table}.jsonl") as stream:
        yield from _iter_jsonl(stream)


def _package_row_key(table: str, row: dict[str, Any]) -> tuple[str, ...]:
    columns = _PORTABLE_ROW_KEYS[table]
    values: list[str] = []
    for column in columns:
        value = row.get(column)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"portable {table} row has an invalid {column}")
        values.append(value)
    return tuple(values)


def _decode_portable_json(
    table: str,
    row: dict[str, Any],
    column: str,
    *,
    required: bool = False,
) -> Any:
    raw = row.get(column)
    if raw is None:
        if required:
            raise ValueError(f"portable {table}.{column} is missing")
        return None
    if not isinstance(raw, str):
        raise ValueError(f"portable {table}.{column} is not JSON text")
    try:
        return json.loads(raw, object_pairs_hook=_json_object_from_pairs)
    except (TypeError, ValueError):
        raise ValueError(f"portable {table}.{column} is invalid JSON") from None


def _require_portable_reference(
    *,
    table: str,
    column: str,
    value: Any,
    target: str,
    target_ids: dict[str, set[str]],
    allow_none: bool = True,
) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"portable {table}.{column} is not a valid {target} reference")
    if value not in target_ids.get(target, set()):
        raise ValueError(f"portable {table}.{column} does not resolve inside the package")


def _require_portable_reference_list(
    *,
    table: str,
    column: str,
    value: Any,
    target: str,
    target_ids: dict[str, set[str]],
    allow_wildcard: bool = False,
    maximum: int = 512,
) -> None:
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f"portable {table}.{column} must be a bounded JSON string list")
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip() or item in seen:
            raise ValueError(f"portable {table}.{column} contains an invalid or duplicate ID")
        if not (allow_wildcard and item == "*") and item not in target_ids.get(target, set()):
            raise ValueError(f"portable {table}.{column} contains an unresolved package ID")
        seen.add(item)


def _validate_portable_snapshot(
    row: dict[str, Any],
    *,
    target_ids: dict[str, set[str]],
    include_sources: bool,
) -> None:
    snapshot = _decode_portable_json(
        "context_record_versions",
        row,
        "snapshot_json",
        required=True,
    )
    if not isinstance(snapshot, dict):
        raise ValueError("portable context record version snapshot is not an object")
    record_id = row.get("record_id")
    if snapshot.get("id") != record_id:
        raise ValueError("portable context record version snapshot has the wrong record ID")
    if "vault_id" in snapshot and snapshot["vault_id"] not in target_ids.get(
        "package_vault", set()
    ):
        raise ValueError("portable context record version snapshot has the wrong vault")
    source_id = snapshot.get("source_id")
    if not include_sources and source_id is not None:
        raise ValueError("source-less portable record version contains a source reference")
    _require_portable_reference(
        table="context_record_versions",
        column="snapshot_json.source_id",
        value=source_id,
        target="sources",
        target_ids=target_ids,
    )
    for field, target in (("candidate_id", "candidates"), ("supersedes", "records")):
        _require_portable_reference(
            table="context_record_versions",
            column=f"snapshot_json.{field}",
            value=snapshot.get(field),
            target=target,
            target_ids=target_ids,
        )
    for field in ("allowed_clients", "denied_clients"):
        if field in snapshot:
            _require_portable_reference_list(
                table="context_record_versions",
                column=f"snapshot_json.{field}",
                value=snapshot[field],
                target="client_principals",
                target_ids=target_ids,
                allow_wildcard=True,
                maximum=256,
            )


def _validate_portable_replication_payload(
    row: dict[str, Any],
    *,
    target_ids: dict[str, set[str]],
    include_sources: bool,
) -> None:
    event_type = row.get("event_type")
    if event_type not in {"record_upserted", "record_withdrawn", "record_deleted", "record_purged"}:
        raise ValueError("portable replication event has an unsupported event type")
    payload = _decode_portable_json("replication_events", row, "payload_json", required=True)
    if not isinstance(payload, dict):
        raise ValueError("portable replication event payload is not an object")
    record_id = row.get("record_id")
    record_target = "records" if event_type == "record_upserted" else "record_references"
    _require_portable_reference(
        table="replication_events",
        column="record_id",
        value=record_id,
        target=record_target,
        target_ids=target_ids,
        allow_none=False,
    )
    if event_type == "record_upserted" and payload.get("id") != record_id:
        raise ValueError("portable replication payload ID does not match its event")
    if "record_id" in payload and payload["record_id"] != record_id:
        raise ValueError("portable replication payload record ID does not match its event")
    for field, target in (("source_id", "sources"), ("candidate_id", "candidates")):
        if field in payload:
            if not include_sources and field == "source_id" and payload[field] is not None:
                raise ValueError("source-less portable replication payload contains a source")
            _require_portable_reference(
                table="replication_events",
                column=f"payload_json.{field}",
                value=payload[field],
                target=target,
                target_ids=target_ids,
            )
    for field in ("allowed_clients", "denied_clients"):
        if field in payload:
            _require_portable_reference_list(
                table="replication_events",
                column=f"payload_json.{field}",
                value=payload[field],
                target="client_principals",
                target_ids=target_ids,
                allow_wildcard=True,
                maximum=256,
            )


def _validate_portable_import_result(
    row: dict[str, Any],
    *,
    target_ids: dict[str, set[str]],
    include_sources: bool,
) -> None:
    result = _decode_portable_json("import_operations", row, "result_json")
    if result is None:
        return
    if not isinstance(result, dict):
        raise ValueError("portable import operation result is not an object")
    source = result.get("source")
    if source is not None:
        if not include_sources:
            raise ValueError("source-less portable import result contains a source")
        if not isinstance(source, dict):
            raise ValueError("portable import result source is not an object")
        _require_portable_reference(
            table="import_operations",
            column="result_json.source.id",
            value=source.get("id"),
            target="sources",
            target_ids=target_ids,
            allow_none=False,
        )
        _require_portable_reference(
            table="import_operations",
            column="result_json.source.content_hash",
            value=source.get("content_hash"),
            target="source_blobs",
            target_ids=target_ids,
            allow_none=False,
        )
    session = result.get("session")
    if session is not None:
        if not isinstance(session, dict):
            raise ValueError("portable import result session is not an object")
        _require_portable_reference(
            table="import_operations",
            column="result_json.session.session_id",
            value=session.get("session_id"),
            target="sessions",
            target_ids=target_ids,
            allow_none=False,
        )
        for field in ("accessible_sources", "unavailable_sources"):
            if field in session:
                _require_portable_reference_list(
                    table="import_operations",
                    column=f"result_json.session.{field}",
                    value=session[field],
                    target="sources",
                    target_ids=target_ids,
                )
    for field, target in (
        ("candidate_ids", "candidates"),
        ("record_ids", "record_references"),
        ("withdrawn_record_ids", "record_references"),
    ):
        if field in result:
            _require_portable_reference_list(
                table="import_operations",
                column=f"result_json.{field}",
                value=result[field],
                target=target,
                target_ids=target_ids,
            )


def _validate_portable_graph(
    archive: zipfile.ZipFile,
    manifest_tables: dict[str, Any],
    source_chunks: list[dict[str, Any]],
    *,
    include_sources: bool,
    package_vault_ids: set[str],
) -> None:
    """Validate the complete package graph without opening the destination."""

    unknown_tables = set(manifest_tables).difference(PORTABLE_TABLE_INVENTORY)
    unknown_tables.difference_update(CAPTURE_RUNTIME_TABLES)
    if unknown_tables:
        raise ValueError("portable export contains a table outside the supported inventory")
    source_tables = {
        "source_records",
        "source_blobs",
    }
    if not include_sources and source_tables.intersection(manifest_tables):
        raise ValueError("source-less portable export contains source material")

    row_keys: dict[str, set[tuple[str, ...]]] = {}
    ids_by_type: dict[str, set[str]] = {}
    id_types: dict[str, set[str]] = {}
    source_blob_info: dict[str, tuple[int, str, bytes]] = {}
    record_supersedes: dict[str, str | None] = {}
    purged_record_ids: set[str] = set()
    purged_source_ids: set[str] = set()

    for table in manifest_tables:
        if table in CAPTURE_RUNTIME_TABLES:
            continue
        if table not in _PORTABLE_ROW_KEYS:
            raise ValueError("portable table has no identity inventory")
        keys: set[tuple[str, ...]] = set()
        count = 0
        for row in _iter_portable_table_rows(archive, table):
            count += 1
            key = _package_row_key(table, row)
            if key in keys:
                if table == _ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE:
                    raise ValueError("source-less archive purge barrier entries are duplicated")
                raise ValueError(f"portable {table} rows are duplicated")
            keys.add(key)
            if table == "source_blobs":
                content_hash = key[0]
                if (
                    len(content_hash) != 64
                    or any(character not in "0123456789abcdef" for character in content_hash)
                ):
                    raise ValueError("portable source blob hash is not canonical")
                content = row.get("content")
                if not isinstance(content, bytes):
                    raise ValueError("portable source blob content is not binary")
                storage_kind = row.get("storage_kind", "inline")
                if storage_kind not in {"inline", "chunked"}:
                    raise ValueError("portable source blob storage kind is invalid")
                byte_size = row.get("byte_size")
                if (
                    isinstance(byte_size, bool)
                    or not isinstance(byte_size, int)
                    or not 0 <= byte_size <= MAX_IMPORT_BYTES
                ):
                    raise ValueError("portable source blob size is invalid")
                source_blob_info[content_hash] = (byte_size, storage_kind, content)
            if table == "context_records":
                record_supersedes[key[0]] = row.get("supersedes")
            if table == "purge_tombstones":
                target_type = row.get("target_type")
                if target_type not in {"record", "source"}:
                    raise ValueError("portable purge tombstone target type is invalid")
                stable_id = key[0]
                if target_type == "record":
                    purged_record_ids.add(stable_id)
                else:
                    purged_source_ids.add(stable_id)
        row_keys[table] = keys
        expected_count = manifest_tables[table]
        if count != expected_count:
            raise ValueError("portable table row count does not match its manifest")

    for table, (column, id_type) in _PORTABLE_ID_TYPES.items():
        if table not in manifest_tables:
            continue
        for key in row_keys[table]:
            # edge_proposal_receipts has a scoped proposal identity; the first
            # key element is the package vault and the second is the ID.
            index = _PORTABLE_ROW_KEYS[table].index(column)
            value = key[index]
            ids_by_type.setdefault(id_type, set()).add(value)
            id_types.setdefault(value, set()).add(id_type)
    for value, types in id_types.items():
        del value
        if len(types) > 1:
            raise ValueError("portable package reuses one ID across incompatible types")

    if "purge_tombstones" in manifest_tables:
        for row in _iter_portable_table_rows(archive, "purge_tombstones"):
            stable_id = row["stable_id"]
            target_type = row["target_type"]
            id_type = "record" if target_type == "record" else "source"
            existing_types = id_types.setdefault(stable_id, set())
            if existing_types.difference({id_type}):
                raise ValueError("portable purge tombstone has a cross-type target ID")
            existing_types.add(id_type)

    target_ids: dict[str, set[str]] = {
        "vaults": set(ids_by_type.get("vault", set())),
        "source_blobs": set(ids_by_type.get("source_blob", set())),
        "sources": set(ids_by_type.get("source", set())) | purged_source_ids,
        "clients": set(ids_by_type.get("client", set())),
        "remote_clients": set(ids_by_type.get("remote_edge_client", set())),
        "client_principals": set(ids_by_type.get("client", set()))
        | set(ids_by_type.get("remote_edge_client", set()))
        | {"*"},
        "sessions": set(ids_by_type.get("ingestion_session", set())),
        "batches": set(ids_by_type.get("ingestion_batch", set())),
        "candidates": set(ids_by_type.get("candidate", set())),
        "records": set(ids_by_type.get("record", set())),
        "record_references": set(ids_by_type.get("record", set())) | purged_record_ids,
        "record_versions": set(ids_by_type.get("record_version", set())),
        "events": set(ids_by_type.get("replication_event", set())),
        "operations": set(ids_by_type.get("import_operation", set())),
    }
    # The package vault is a declaration, not a destination lookup.  The
    # existing binding check already enforces a single value; keep an explicit
    # copy for snapshot checks without mutating the index.
    if package_vault_ids:
        target_ids["package_vault"] = set(package_vault_ids)
    else:
        target_ids["package_vault"] = set()

    for table in manifest_tables:
        if table in CAPTURE_RUNTIME_TABLES:
            continue
        for row in _iter_portable_table_rows(archive, table):
            if "vault_id" in row:
                _require_portable_reference(
                    table=table,
                    column="vault_id",
                    value=row["vault_id"],
                    target="vaults",
                    target_ids=target_ids,
                    allow_none=False,
                )
                if row["vault_id"] not in package_vault_ids:
                    raise ValueError(f"portable {table} row is outside the package vault")

            for ref_table, column, target in PORTABLE_RELATIONAL_REFERENCE_INVENTORY:
                if ref_table != table or column not in row:
                    continue
                if (
                    table == "replication_events"
                    or table in {"context_errors", "context_user_mutations"}
                    or table == "deletion_tombstones"
                ) and column == "record_id":
                    allowed_target = "record_references"
                elif (
                    table in {"archive_purge_barriers", "import_operations"}
                    or table == "deletion_tombstones"
                ) and column in {"source_id", "deletion_source_id"}:
                    allowed_target = "sources"
                elif table == "purge_jobs" and column == "target_id":
                    target_type = row.get("target_type")
                    if target_type not in {"record", "source"}:
                        raise ValueError("portable purge job target type is invalid")
                    allowed_target = (
                        "record_references" if target_type == "record" else "sources"
                    )
                else:
                    allowed_target = {
                        "vaults": "vaults",
                        "source_records": "sources",
                        "context_candidates": "candidates",
                        "context_records": "records",
                        "ingestion_sessions": "sessions",
                        "context_record_versions": "record_versions",
                        "client_registrations": "clients",
                        "permission_grants": "clients",
                        "edge_proposal_receipts": "candidates",
                        "ingestion_batches": "sessions",
                        "deletion_tombstones": "records",
                        "replication_events": "events",
                        "replication_checkpoints": "vaults",
                        "memory_policies": "vaults",
                        "secret_refusal_receipts": "vaults",
                        "import_operations": "vaults",
                        "archive_purge_barriers": "sources",
                        "purge_tombstones": "vaults",
                        "source_blobs": "source_blobs",
                    }.get(target, target)
                _require_portable_reference(
                    table=table,
                    column=column,
                    value=row[column],
                    target=allowed_target,
                    target_ids=target_ids,
                )

            if table == "context_candidates":
                for field in ("allowed_clients_json", "denied_clients_json"):
                    if field in row:
                        _require_portable_reference_list(
                            table=table,
                            column=field,
                            value=_decode_portable_json(table, row, field, required=True),
                            target="client_principals",
                            target_ids=target_ids,
                            allow_wildcard=True,
                            maximum=256,
                        )
                if not include_sources and row.get("source_id") is not None:
                    raise ValueError("source-less portable candidate contains a source reference")
            elif table == "context_records":
                for field in ("allowed_clients_json", "denied_clients_json"):
                    if field in row:
                        _require_portable_reference_list(
                            table=table,
                            column=field,
                            value=_decode_portable_json(table, row, field, required=True),
                            target="client_principals",
                            target_ids=target_ids,
                            allow_wildcard=True,
                            maximum=256,
                        )
                if not include_sources and row.get("source_id") is not None:
                    raise ValueError("source-less portable record contains a source reference")
            elif table == "context_record_versions":
                _validate_portable_snapshot(
                    row,
                    target_ids=target_ids,
                    include_sources=include_sources,
                )
                if not include_sources and row.get("record_id") is None:
                    raise ValueError("portable record version has no record")
            elif table == "ingestion_sessions":
                for field in ("accessible_sources_json", "unavailable_sources_json"):
                    if field in row:
                        values = _decode_portable_json(table, row, field, required=True)
                        _require_portable_reference_list(
                            table=table,
                            column=field,
                            value=values,
                            target="sources",
                            target_ids=target_ids,
                        )
                accessible = _decode_portable_json(
                    table, row, "accessible_sources_json", required=False
                )
                unavailable = _decode_portable_json(
                    table, row, "unavailable_sources_json", required=False
                )
                if (
                    isinstance(accessible, list)
                    and isinstance(unavailable, list)
                    and set(accessible).intersection(unavailable)
                ):
                    raise ValueError("portable ingestion session overlaps source lists")
            elif table == "ingestion_batches":
                _require_portable_reference_list(
                    table=table,
                    column="candidate_ids_json",
                    value=_decode_portable_json(table, row, "candidate_ids_json", required=True),
                    target="candidates",
                    target_ids=target_ids,
                    maximum=512,
                )
            elif table == "audit_events":
                for field in ("record_ids_json", "denied_record_ids_json"):
                    if field in row:
                        _require_portable_reference_list(
                            table=table,
                            column=field,
                            value=_decode_portable_json(table, row, field, required=True),
                            target="record_references",
                            target_ids=target_ids,
                            maximum=512,
                        )
            elif table == "replication_events":
                _validate_portable_replication_payload(
                    row,
                    target_ids=target_ids,
                    include_sources=include_sources,
                )
            elif table == "source_records":
                metadata = _decode_portable_json(table, row, "metadata_json", required=True)
                if not isinstance(metadata, dict):
                    raise ValueError("portable source metadata is not an object")
                for field, target in (
                    ("import_operation_id", "operations"),
                    ("rebuild_published_session_id", "sessions"),
                ):
                    if field in metadata:
                        _require_portable_reference(
                            table=table,
                            column=f"metadata_json.{field}",
                            value=metadata[field],
                            target=target,
                            target_ids=target_ids,
                        )
            elif table == "import_operations":
                if not include_sources and (
                    row.get("source_id") is not None or row.get("content_hash") is not None
                ):
                    raise ValueError("source-less portable import operation contains source state")
                _validate_portable_import_result(
                    row,
                    target_ids=target_ids,
                    include_sources=include_sources,
                )

    for record_id in record_supersedes:
        seen: set[str] = set()
        current = record_supersedes[record_id]
        while current is not None:
            if current in seen or current == record_id:
                raise ValueError("portable record supersession contains a cycle")
            seen.add(current)
            current = record_supersedes.get(current)

    if include_sources:
        chunk_descriptors_by_hash: dict[str, list[dict[str, Any]]] = {}
        for descriptor in source_chunks:
            content_hash = str(descriptor["content_hash"])
            if content_hash not in source_blob_info:
                raise ValueError("portable source chunk has no package source blob")
            chunk_descriptors_by_hash.setdefault(content_hash, []).append(descriptor)
            content = archive.read(str(descriptor["path"]))
            if len(content) != int(descriptor["byte_size"]):
                raise ValueError("portable source chunk has an invalid size")
        for content_hash, (byte_size, storage_kind, inline_content) in source_blob_info.items():
            descriptors = chunk_descriptors_by_hash.get(content_hash, [])
            if storage_kind == "inline":
                if descriptors:
                    raise ValueError("portable inline source blob has source chunks")
                if (
                    len(inline_content) != byte_size
                    or len(inline_content) > SOURCE_BLOB_CHUNK_BYTES
                    or hashlib.sha256(inline_content).hexdigest() != content_hash
                ):
                    raise ValueError("portable inline source blob failed its integrity check")
                continue
            if not descriptors or byte_size <= 0 or inline_content:
                raise ValueError("portable chunked source blob has invalid package storage")
            total = 0
            digest = hashlib.sha256()
            for expected_index, descriptor in enumerate(descriptors):
                if int(descriptor["chunk_index"]) != expected_index:
                    raise ValueError("portable source blob chunks are not contiguous")
                content = archive.read(str(descriptor["path"]))
                digest.update(content)
                total += len(content)
            if total != byte_size or digest.hexdigest() != content_hash:
                raise ValueError("portable source blob chunks failed their integrity check")
        if set(chunk_descriptors_by_hash).difference(source_blob_info):
            raise ValueError("portable source chunks are not package-contained")
    elif source_chunks:
        raise ValueError("source chunks require source-inclusive package material")


def _source_chunk_descriptors(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw_descriptors = manifest.get("source_chunks", [])
    if not isinstance(raw_descriptors, list):
        raise ValueError("export source_chunks must be an array")
    descriptors: list[dict[str, Any]] = []
    identities: set[tuple[str, int]] = set()
    paths: set[str] = set()
    sizes_by_hash: dict[str, list[tuple[int, int]]] = {}
    for raw in raw_descriptors:
        if not isinstance(raw, dict):
            raise ValueError("export source chunk descriptor must be an object")
        content_hash = raw.get("content_hash")
        chunk_index = raw.get("chunk_index")
        byte_size = raw.get("byte_size")
        path = raw.get("path")
        if (
            not isinstance(content_hash, str)
            or len(content_hash) != 64
            or any(character not in "0123456789abcdef" for character in content_hash)
            or isinstance(chunk_index, bool)
            or not isinstance(chunk_index, int)
            or chunk_index < 0
            or isinstance(byte_size, bool)
            or not isinstance(byte_size, int)
            or not 1 <= byte_size <= SOURCE_BLOB_CHUNK_BYTES
            or not isinstance(path, str)
            or path != f"source-chunks/{content_hash}/{chunk_index:08d}.bin"
        ):
            raise ValueError("export source chunk descriptor is invalid")
        identity = (content_hash, chunk_index)
        if identity in identities or path in paths:
            raise ValueError("export source chunk descriptor is duplicated")
        identities.add(identity)
        paths.add(path)
        sizes_by_hash.setdefault(content_hash, []).append((chunk_index, byte_size))
        descriptors.append(
            {
                "byte_size": byte_size,
                "chunk_index": chunk_index,
                "content_hash": content_hash,
                "path": path,
            }
        )
    for chunks in sizes_by_hash.values():
        ordered = sorted(chunks)
        if [index for index, _size in ordered] != list(range(len(ordered))):
            raise ValueError("export source chunk sequence is incomplete")
        if sum(size for _index, size in ordered) > MAX_IMPORT_BYTES:
            raise ValueError("export source exceeds the supported size limit")
    return descriptors


def _manifest_table_members(manifest_tables: dict[str, Any]) -> set[str]:
    """Return the canonical archive member for every serialized table."""

    members: set[str] = set()
    folded_members: set[str] = set()
    for table, count in manifest_tables.items():
        if (
            not isinstance(table, str)
            or not table
            or table != table.strip()
            or table != table.casefold()
            or "/" in table
            or "\\" in table
            or ":" in table
            or table in {".", ".."}
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 0
        ):
            raise ValueError("export manifest table entry is invalid")
        member = f"tables/{table}.jsonl"
        folded_member = member.casefold()
        if folded_member in folded_members:
            raise ValueError("export manifest table members are ambiguous")
        members.add(member)
        folded_members.add(folded_member)
    return members


def _validate_manifest_archive(
    archive: zipfile.ZipFile,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate archive topology and all member hashes before destination access."""

    manifest_tables = manifest.get("tables")
    if not isinstance(manifest_tables, dict):
        raise ValueError("export manifest tables must be an object")
    table_members = _manifest_table_members(manifest_tables)
    source_chunks = _source_chunk_descriptors(manifest)
    include_sources = manifest.get("include_sources", False)
    if not isinstance(include_sources, bool):
        raise ValueError("export include_sources flag is invalid")
    if source_chunks and not include_sources:
        raise ValueError("source chunks require a source-inclusive export")

    expected_members = table_members | {str(item["path"]) for item in source_chunks}
    if len(expected_members) != len(table_members) + len(source_chunks):
        raise ValueError("export members have duplicate or shadowed paths")

    infos = archive.infolist()
    archive_names = [info.filename for info in infos]
    if len(archive_names) != len(set(archive_names)):
        raise ValueError("export archive contains duplicate entries")
    folded_archive_names: dict[str, str] = {}
    for name in archive_names:
        if (
            not name
            or "\\" in name
            or name.startswith("/")
            or any(part in {"", ".", ".."} for part in name.split("/"))
        ):
            raise ValueError("unsafe or non-canonical export entry")
        folded_name = name.casefold()
        previous = folded_archive_names.setdefault(folded_name, name)
        if previous != name:
            raise ValueError("export archive contains shadowed paths")
    if "manifest.json" not in archive_names:
        raise ValueError("export manifest is missing")
    expected_archive_names = {"manifest.json"} | expected_members
    actual_archive_names = set(archive_names)
    if actual_archive_names != expected_archive_names:
        missing = sorted(expected_archive_names - actual_archive_names)
        extra = sorted(actual_archive_names - expected_archive_names)
        raise ValueError(
            f"export archive members do not match manifest (missing={missing!r}, extra={extra!r})"
        )

    hashes_by_file = manifest.get("sha256")
    if not isinstance(hashes_by_file, dict) or any(
        not isinstance(name, str) or not isinstance(expected, str)
        for name, expected in hashes_by_file.items()
    ):
        raise ValueError("export sha256 manifest must be a string mapping")
    hash_names = set(hashes_by_file)
    if hash_names != expected_members:
        missing = sorted(expected_members - hash_names)
        extra = sorted(hash_names - expected_members)
        raise ValueError(
            "export sha256 manifest does not exactly cover archive members"
            f" (missing={missing!r}, extra={extra!r})"
        )
    for name, expected in hashes_by_file.items():
        if (
            len(expected) != 64
            or expected != expected.casefold()
            or any(character not in "0123456789abcdef" for character in expected)
        ):
            raise ValueError(f"export digest is not canonical for {name}")
        actual = hashlib.sha256(archive.read(name)).hexdigest()
        if actual != expected:
            raise ValueError(f"integrity check failed for {name}")
    return manifest_tables, source_chunks


def _restore_source_chunks(
    connection: sqlite3.Connection,
    archive: zipfile.ZipFile,
    descriptors: list[dict[str, Any]],
    blocked_source_hashes: set[str],
    tables: set[str],
) -> None:
    if not descriptors:
        return
    if "source_blob_chunks" not in tables or "source_blobs" not in tables:
        raise ValueError("destination does not support chunked source blobs")
    for descriptor in descriptors:
        content_hash = str(descriptor["content_hash"])
        if content_hash in blocked_source_hashes:
            continue
        parent = connection.execute(
            "SELECT storage_kind FROM source_blobs WHERE content_hash=?",
            (content_hash,),
        ).fetchone()
        if parent is None:
            raise ValueError("restored source chunk has no source blob")
        if str(parent[0]) == "inline":
            continue
        if str(parent[0]) != "chunked":
            raise ValueError("restored source blob has an invalid storage kind")
        content = archive.read(str(descriptor["path"]))
        if len(content) != int(descriptor["byte_size"]):
            raise ValueError("restored source chunk has an invalid size")
        connection.execute(
            "INSERT OR IGNORE INTO source_blob_chunks"
            "(content_hash,chunk_index,content,byte_size) VALUES(?,?,?,?)",
            (
                content_hash,
                int(descriptor["chunk_index"]),
                content,
                len(content),
            ),
        )


def _validate_source_blob_storage(
    connection: sqlite3.Connection,
    tables: set[str],
    columns_by_table: dict[str, set[str]],
) -> None:
    if "source_blobs" not in tables or "storage_kind" not in columns_by_table.get(
        "source_blobs", set()
    ):
        return
    for content_hash, byte_size, storage_kind, inline_content in connection.execute(
        "SELECT content_hash,byte_size,storage_kind,content FROM source_blobs"
    ):
        expected_size = int(byte_size)
        expected_hash = str(content_hash)
        if not 0 <= expected_size <= MAX_IMPORT_BYTES:
            raise ValueError("source blob exceeds the supported size limit")
        if storage_kind == "inline":
            content = bytes(inline_content)
            if (
                len(content) != expected_size
                or len(content) > SOURCE_BLOB_CHUNK_BYTES
                or hashlib.sha256(content).hexdigest() != expected_hash
            ):
                raise ValueError("source blob has invalid inline storage")
            continue
        if storage_kind != "chunked" or expected_size == 0 or bytes(inline_content):
            raise ValueError("source blob has an invalid storage kind")
        rows = connection.execute(
            "SELECT chunk_index,byte_size,content FROM source_blob_chunks "
            "WHERE content_hash=? ORDER BY chunk_index",
            (expected_hash,),
        )
        total = 0
        digest = hashlib.sha256()
        for expected_index, row in enumerate(rows):
            chunk_index = int(row[0])
            declared_size = int(row[1])
            chunk = bytes(row[2])
            actual_size = len(chunk)
            if (
                chunk_index != expected_index
                or declared_size != actual_size
                or actual_size <= 0
                or actual_size > SOURCE_BLOB_CHUNK_BYTES
            ):
                raise ValueError("source blob chunks are invalid")
            digest.update(chunk)
            total += actual_size
        if total != expected_size or digest.hexdigest() != expected_hash:
            raise ValueError("source blob chunks failed their integrity check")


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


def _normalize_deletion_tombstone_row(row: dict[str, Any]) -> None:
    """Never import source-rebuild authority from an untrusted archive.

    A portable export authenticates the archive as a whole, but its JSONL rows
    are still input data at restore time.  Rebuild provenance is an internal
    Core capability, so an imported marker is deliberately downgraded to the
    ordinary, non-resurrecting barrier.  This preserves restore compatibility
    while making row tampering fail closed.
    """

    if str(row.get("deletion_origin", "ordinary")) != "ordinary":
        row["deletion_origin"] = "ordinary"
    row["deletion_source_id"] = None
    # Rebuild session/generation markers are process-local authority.  An
    # export row, including one from a valid source database, must reopen only
    # after a fresh in-process publish ceremony on the destination.
    row["rebuild_session_id"] = None
    row["rebuild_generation"] = None
    row["rebuild_source_marker"] = None


def _backfill_legacy_user_mutations(
    connection: sqlite3.Connection,
    tables: set[str],
    columns_by_table: dict[str, set[str]],
) -> None:
    """Recover pre-013 user-edit evidence while keeping new writes explicit."""

    required_records = {"id", "vault_id", "observation_origin", "updated_at", "created_at"}
    if (
        not {
            "context_user_mutations",
            "context_records",
            "context_record_versions",
            "deletion_tombstones",
        }.issubset(tables)
        or not required_records.issubset(columns_by_table.get("context_records", set()))
        or "deletion_origin" not in columns_by_table.get("deletion_tombstones", set())
    ):
        return
    connection.execute(
        "INSERT OR IGNORE INTO context_user_mutations"
        "(id,vault_id,record_id,mutation_kind,mutation_origin,actor,created_at) "
        "SELECT 'legacy-013:' || r.id,r.vault_id,r.id,'legacy_user_edit',"
        "'local_user','migration-013',COALESCE(r.updated_at,r.created_at) "
        "FROM context_records r JOIN source_records s ON s.id=r.source_id "
        "AND s.vault_id=r.vault_id WHERE r.source_id IS NOT NULL AND ("
        "r.observation_origin='local_admin' OR "
        "EXISTS (SELECT 1 FROM context_candidates c WHERE c.supersedes=r.id "
        "AND lower(c.kind)='correction' AND c.source_id IS NOT NULL "
        "AND c.observation_origin='local_admin') OR "
        "EXISTS (SELECT 1 FROM context_record_versions v WHERE v.record_id=r.id "
        "AND json_valid(v.snapshot_json) AND json_type(v.snapshot_json,'$.source_id')='text' "
        "AND (json_type(v.snapshot_json,'$.deleted_at')='text' OR "
        "json_extract(v.snapshot_json,'$.observation_origin')='local_admin' OR "
        "lower(v.reason) IN ('availability changed','availability_changed'))) OR "
        "EXISTS (SELECT 1 FROM source_deletion_members m WHERE m.source_id=r.source_id "
        "AND m.record_id=r.id)) AND NOT EXISTS (SELECT 1 FROM deletion_tombstones t "
        "WHERE t.record_id=r.id AND t.deletion_origin='source_rebuild') AND NOT EXISTS ("
        "SELECT 1 FROM context_observation_links l WHERE l.record_id=r.id "
        "AND l.observation_id=r.candidate_id AND l.relationship='reapplied')"
    )


def _validate_imported_user_mutation(
    connection: sqlite3.Connection,
    row: dict[str, Any],
    columns: set[str],
    version_columns: set[str],
) -> bool:
    """Accept only typed same-package action evidence or legacy compatibility facts."""

    required = {
        "id",
        "vault_id",
        "record_id",
        "mutation_kind",
        "mutation_origin",
        "actor",
        "created_at",
        "evidence_kind",
        "evidence_id",
        "evidence_version",
        "evidence_hash",
        "intent_key",
    }
    if not required.issubset(columns) or not required.issubset(row):
        return False
    mutation_kind = str(row.get("mutation_kind"))
    if (
        mutation_kind
        not in {
            "restore",
            "correction",
            "availability_change",
            "delete",
            "source_delete",
            "legacy_user_edit",
        }
        or str(row.get("mutation_origin")) != "local_user"
    ):
        return False
    actor = str(row.get("actor") or "")
    if not actor or actor != _normalize_actor(actor):
        return False
    vault_id = str(row.get("vault_id"))
    record_id = str(row.get("record_id"))
    vault = connection.execute("SELECT id FROM vaults WHERE id=?", (vault_id,)).fetchone()
    record = connection.execute(
        "SELECT id,vault_id FROM context_records WHERE id=?", (record_id,)
    ).fetchone()
    if vault is None or record is None or str(record["vault_id"]) != vault_id:
        return False
    evidence_kind = str(row.get("evidence_kind"))
    if evidence_kind not in {"record_version", "user_action"}:
        return False
    evidence_id = str(row.get("evidence_id") or "")
    try:
        evidence_version = int(str(row.get("evidence_version")))
    except (TypeError, ValueError):
        return False
    if evidence_version < 1 or not evidence_id:
        return False
    evidence = connection.execute(
        "SELECT id,record_id,version,snapshot_json,reason,user_action_kind,user_action_key "
        "FROM context_record_versions "
        "WHERE id=? AND record_id=? AND version=?",
        (evidence_id, record_id, evidence_version),
    ).fetchone()
    if evidence is None:
        return False
    intent = str(row.get("intent_key") or "")
    if evidence_kind == "user_action":
        if not {"user_action_kind", "user_action_key"}.issubset(version_columns):
            return False
        user_action_kind = str(evidence["user_action_kind"] or "")
        user_action_key = str(evidence["user_action_key"] or "")
        if user_action_kind != mutation_kind or not user_action_key:
            return False
        if intent != user_action_key:
            return False
        expected_hash = _user_action_evidence_hash(
            mutation_kind=mutation_kind,
            user_action_key=user_action_key,
            vault_id=vault_id,
            record_id=record_id,
            evidence_id=evidence_id,
            evidence_version=evidence_version,
            snapshot_json=str(evidence["snapshot_json"]),
        )
        if str(row.get("evidence_hash")) != expected_hash:
            return False
    else:
        # The pre-014 generic coordinate is retained only for the explicitly
        # compatibility-scoped legacy inference fact.  It is never authority
        # for a typed correction, restore, availability, or delete action.
        if mutation_kind != "legacy_user_edit":
            return False
        expected_hash = _mutation_evidence_hash(
            mutation_kind=mutation_kind,
            evidence_kind="record_version",
            vault_id=vault_id,
            record_id=record_id,
            evidence_id=evidence_id,
            evidence_version=evidence_version,
            snapshot_json=str(evidence["snapshot_json"]),
        )
        if str(row.get("evidence_hash")) != expected_hash:
            return False
        if intent != f"legacy-row:{row['id']}" and intent != (
            f"{mutation_kind}:{record_id}:{evidence_id}"
        ):
            return False

    try:
        snapshot = json.loads(str(evidence["snapshot_json"]))
    except (TypeError, ValueError):
        return False
    if not isinstance(snapshot, dict) or str(snapshot.get("id")) != record_id:
        return False
    source_id = snapshot.get("source_id")
    if source_id is not None:
        source = connection.execute(
            "SELECT id,vault_id FROM source_records WHERE id=?", (str(source_id),)
        ).fetchone()
        if source is None or str(source["vault_id"]) != vault_id:
            return False
    expected_reasons = {
        "restore": "record_restored",
        "correction": "record_corrected",
        "availability_change": "availability_changed",
        "delete": "record_deleted",
        "source_delete": "source_deleted",
    }
    if (
        mutation_kind != "legacy_user_edit"
        and str(evidence["reason"]) != expected_reasons[mutation_kind]
    ):
        return False
    if mutation_kind in {"delete", "source_delete"} and not isinstance(
        snapshot.get("deleted_at"), str
    ):
        return False
    if mutation_kind == "source_delete" and source_id is None:
        return False
    if mutation_kind == "legacy_user_edit":
        if source_id is None:
            return False
        local_origin = snapshot.get("observation_origin") == "local_admin"
        deleted_snapshot = isinstance(snapshot.get("deleted_at"), str)
        if not (local_origin or deleted_snapshot):
            return False
    return True


def _recompute_record_keys(
    connection: sqlite3.Connection,
    tables: set[str],
    columns_by_table: dict[str, set[str]],
) -> None:
    """Recompute identity keys from imported fields instead of trusting JSON."""

    for table in ("context_candidates", "context_records"):
        if table not in tables or "record_key" not in columns_by_table.get(table, set()):
            continue
        rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
        for row in rows:
            connection.execute(
                f'UPDATE "{table}" SET record_key=? WHERE id=?',
                (_stable_record_key_from_row(row), row["id"]),
            )


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

    _backfill_legacy_user_mutations(connection, tables, columns_by_table)
    _recompute_record_keys(connection, tables, columns_by_table)

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
            if any(info.file_size > MAX_RESTORE_ENTRY_BYTES for info in infos):
                raise ValueError("unsafe or oversized export entry")
            archive_names = [info.filename for info in infos]
            if len(archive_names) != len(set(archive_names)):
                raise ValueError("export archive contains duplicate entries")
            if "manifest.json" not in archive_names:
                raise ValueError("export manifest is missing")
            manifest = json.loads(
                archive.read("manifest.json"), object_pairs_hook=_json_object_from_pairs
            )
            if not isinstance(manifest, dict):
                raise ValueError("export manifest must be an object")
            source_schema_version = _validate_manifest_versions(manifest)
            manifest_tables, source_chunks = _validate_manifest_archive(archive, manifest)
            include_sources = manifest.get("include_sources", False)
            if type(include_sources) is not bool:
                raise ValueError("export include_sources flag is invalid")
            package_vault_ids = _validate_package_vault_binding(
                archive,
                manifest_tables,
                include_sources=include_sources,
            )
            _validate_portable_graph(
                archive,
                manifest_tables,
                source_chunks,
                include_sources=include_sources,
                package_vault_ids=package_vault_ids,
            )
            if dry_run:
                return {"valid": True, "dry_run": True, "manifest": manifest}
            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            try:
                connection.execute("BEGIN IMMEDIATE")
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
                blocked_records: set[str] = set()
                blocked_sources: set[str] = set()
                imported_user_mutations: list[dict[str, Any]] = []
                accepted_user_mutations = 0
                ignored_user_mutations = 0
                archive_purge_barriers: set[tuple[str, str, str, str]] = set()
                incoming_archive_purge_barriers: set[tuple[str, str, str, str]] = set()
                if _ARCHIVE_PURGE_BARRIER_TABLE in existing:
                    for barrier in connection.execute(
                        f"SELECT vault_id,source_id,source_kind,barrier_digest,purged_at "
                        f"FROM {_ARCHIVE_PURGE_BARRIER_TABLE}"
                    ):
                        key = _archive_purge_barrier_key(barrier)
                        archive_purge_barriers.add(key)
                if _ARCHIVE_PURGE_BARRIER_TABLE in manifest_tables:
                    if _ARCHIVE_PURGE_BARRIER_TABLE not in existing:
                        raise ValueError("destination does not support archive purge barriers")
                    with archive.open(f"tables/{_ARCHIVE_PURGE_BARRIER_TABLE}.jsonl") as stream:
                        for row in _iter_jsonl(stream):
                            key = _archive_purge_barrier_key(row)
                            if key[0] not in package_vault_ids:
                                raise ValueError(
                                    "export archive purge barrier is not bound to a package vault"
                                )
                            if key in incoming_archive_purge_barriers:
                                raise ValueError("export archive purge barriers are duplicated")
                            incoming_archive_purge_barriers.add(key)
                            archive_purge_barriers.add(key)
                archive_source_less_purge_barriers: set[tuple[str, str, str]] = set()
                incoming_archive_source_less_purge_barriers: set[tuple[str, str, str]] = set()
                if _ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE in existing:
                    for barrier in connection.execute(
                        f"SELECT vault_id,source_kind,barrier_digest,purged_at "
                        f"FROM {_ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE}"
                    ):
                        source_less_key = _archive_source_less_purge_barrier_key(barrier)
                        archive_source_less_purge_barriers.add(source_less_key)
                if _ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE in manifest_tables:
                    with archive.open(
                        f"tables/{_ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE}.jsonl"
                    ) as stream:
                        for row in _iter_jsonl(stream):
                            source_less_key = _archive_source_less_purge_barrier_key(row)
                            if source_less_key[0] not in package_vault_ids:
                                raise ValueError(
                                    "export source-less archive purge barrier is not bound to a "
                                    "package vault"
                                )
                            if source_less_key in incoming_archive_source_less_purge_barriers:
                                raise ValueError(
                                    "export source-less archive purge barriers are duplicated"
                                )
                            incoming_archive_source_less_purge_barriers.add(source_less_key)
                            archive_source_less_purge_barriers.add(source_less_key)
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
                if (
                    blocked_records
                    or blocked_sources
                    or archive_purge_barriers
                    or archive_source_less_purge_barriers
                ) and "context_records" in manifest_tables:
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
                            if (
                                _archive_purge_barrier_key_for_content(row)
                                in archive_purge_barriers
                            ):
                                blocked_records.add(record_id)
                                if row.get("candidate_id"):
                                    blocked_candidates.add(str(row["candidate_id"]))
                            if (
                                _archive_source_less_purge_barrier_key_for_content(row)
                                in archive_source_less_purge_barriers
                            ):
                                blocked_records.add(record_id)
                                if row.get("candidate_id"):
                                    blocked_candidates.add(str(row["candidate_id"]))
                if (
                    blocked_records
                    or blocked_sources
                    or archive_purge_barriers
                    or archive_source_less_purge_barriers
                ) and "context_candidates" in manifest_tables:
                    with archive.open("tables/context_candidates.jsonl") as stream:
                        for row in _iter_jsonl(stream):
                            if (
                                str(row.get("source_id")) in blocked_sources
                                or str(row.get("supersedes")) in blocked_records
                                or str(row.get("record_id")) in blocked_records
                            ):
                                blocked_candidates.add(str(row["id"]))
                            if (
                                _archive_purge_barrier_key_for_content(row)
                                in archive_purge_barriers
                            ):
                                blocked_candidates.add(str(row["id"]))
                            if (
                                _archive_source_less_purge_barrier_key_for_content(row)
                                in archive_source_less_purge_barriers
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
                CoreStore._ensure_archive_source_less_purge_barriers_tx(connection)
                all_tables.add(_ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE)
                existing.add(_ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE)
                columns_by_table.setdefault(
                    _ARCHIVE_SOURCE_LESS_PURGE_BARRIER_TABLE,
                    {"vault_id", "source_kind", "barrier_digest", "purged_at"},
                )
                with connection:
                    for table in manifest_tables:
                        if table in CAPTURE_RUNTIME_TABLES or table in NON_PORTABLE_SECURITY_TABLES:
                            # Portable archives never rehydrate machine-local
                            # capture/security state, including legacy archives
                            # that predate this explicit exclusion.
                            continue
                        if table not in existing:
                            continue
                        name = f"tables/{table}.jsonl"
                        with archive.open(name) as stream:
                            for row in _iter_jsonl(stream):
                                if table == "context_user_mutations":
                                    # Ledger rows are not ordinary portable
                                    # data.  Hold them until every referenced
                                    # record/version has been restored and
                                    # validate the typed evidence below.
                                    imported_user_mutations.append(row)
                                    continue
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
                                row = _without_machine_local_references(table, row)
                                if table == "deletion_tombstones":
                                    _normalize_deletion_tombstone_row(row)
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
                    _restore_source_chunks(
                        connection,
                        archive,
                        source_chunks,
                        blocked_source_hashes,
                        all_tables,
                    )
                    _post_restore_upgrade(connection, all_tables, columns_by_table)
                    # The archive identity ledger is derived state and is
                    # intentionally excluded from the package. Rebuild it
                    # after records and versions have been restored so
                    # deletion/rebuild barriers work immediately, before the
                    # destination's next startup.
                    CoreStore._ensure_archive_identity_index_tx(connection)
                    CoreStore._ensure_archive_source_less_purge_barriers_tx(connection)
                    if "context_user_mutations" in all_tables:
                        # The post-restore legacy inference may have created
                        # rows.  Repair evidence and canonical actor fields
                        # before considering package rows.
                        CoreStore._ensure_user_mutation_boundary_tx(connection)
                        mutation_columns = columns_by_table["context_user_mutations"]
                        for row in imported_user_mutations:
                            if not _validate_imported_user_mutation(
                                connection,
                                row,
                                mutation_columns,
                                columns_by_table.get("context_record_versions", set()),
                            ):
                                ignored_user_mutations += 1
                                continue
                            inserted = connection.execute(
                                "INSERT OR IGNORE INTO context_user_mutations"
                                "(id,vault_id,record_id,mutation_kind,mutation_origin,actor,"
                                "created_at,evidence_kind,evidence_id,evidence_version,"
                                "evidence_hash,intent_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    str(row["id"]),
                                    str(row["vault_id"]),
                                    str(row["record_id"]),
                                    str(row["mutation_kind"]),
                                    "local_user",
                                    str(row["actor"]),
                                    str(row["created_at"]),
                                    str(row["evidence_kind"]),
                                    str(row["evidence_id"]),
                                    int(row["evidence_version"]),
                                    str(row["evidence_hash"]),
                                    str(row["intent_key"]),
                                ),
                            )
                            if inserted.rowcount == 1:
                                accepted_user_mutations += 1
                            else:
                                ignored_user_mutations += 1
                    _validate_source_blob_storage(
                        connection,
                        all_tables,
                        columns_by_table,
                    )
                    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                    if violations:
                        raise ValueError(
                            "restored export contains unresolved foreign-key references"
                        )
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()
    _repair_secret_boundary(database_path)
    return {
        "valid": True,
        "dry_run": False,
        "manifest": manifest,
        "user_mutations": {
            "accepted": accepted_user_mutations,
            "ignored": ignored_user_mutations,
        },
    }
