"""Transactional SQLite storage for the authoritative local Core."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import shutil
import sqlite3
import threading
import unicodedata
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, cast

from .config import MAX_IMPORT_BYTES
from .ids import new_id, utc_now
from .memory_policy import (
    AUTOMATIC_POLICY_VERSION,
    UNKEYED_CONFLICT_KINDS,
    AutomaticMemoryPolicy,
    MemoryPolicy,
    ObservationOrigin,
    archive_lineage_key,
    normalized_observation_text,
)
from .models import (
    MAX_TRUTH_CONFLICT_GROUPS,
    MAX_TRUTH_EVIDENCE,
    MAX_TRUTH_SUPERSEDED_BY,
    ApprovalRequest,
    ApprovalStatus,
    Availability,
    CandidateInput,
    CandidateOut,
    ClientCreate,
    ContextRecordOut,
    CoverageReport,
    IngestionMode,
    MemoryTruthObservationOut,
    MemoryTruthRecordOut,
    MemoryTruthResponse,
    MemoryTruthStatus,
    ObservationDisposition,
    ObservationOut,
    SecretRefusalOut,
    Sensitivity,
    SourceOut,
    TruthConflictState,
    TruthCoverageOut,
    TruthEvidenceOut,
    TruthSourceOut,
)
from .replication import MAX_REPLICATION_PAYLOAD_BYTES
from .secret_boundary import (
    SECRET_DETECTOR_VERSION,
    SECRET_REFUSAL_REASON,
    contains_direct_secret,
    contains_secret_like_value,
    opaque_operation_id,
    redact_secret_reason,
)
from .security import (
    ClientPrincipal,
    generate_token,
    hash_token,
    record_is_allowed,
    verify_token,
)


class StorageError(RuntimeError):
    """Base error suitable for transport-layer translation."""


class NotFoundError(StorageError):
    pass


class ConflictError(StorageError):
    pass


class InvalidStateError(StorageError):
    pass


PURGE_CONFIRMATION_TEMPLATE = "PURGE {target_type} {target_id}"
SOURCE_BLOB_CHUNK_BYTES = 8 * 1024 * 1024
SOURCE_REBUILD_REASON = "replaced by source rebuild"
# Import-operation liveness must never sit on the full 10s busy budget: qualified
# Linux gaps clustered near that ceiling when BEGIN IMMEDIATE waited out a lock.
# Fail-fast and let the heartbeat scheduler retry within the public 5s allowance.
LIVENESS_BUSY_TIMEOUT_MS = 250
LIVENESS_CONNECT_TIMEOUT_SECONDS = 0.25


def durable_sqlite_footprint(database_path: Path) -> int:
    """Return bytes needed for durable SQLite state (main database plus WAL)."""
    resolved = database_path.resolve()
    paths = (resolved, resolved.with_name(f"{resolved.name}-wal"))
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except FileNotFoundError:
            continue
    return total


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _loads(value: str | None, default: Any) -> Any:
    return default if value is None else json.loads(value)


def _normalized_slot_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    if not normalized:
        raise InvalidStateError("memory slot keys must not normalize to an empty value")
    return normalized


def _stable_record_key(candidate: CandidateInput) -> str | None:
    """Derive identity from durable source addressing and value identity.

    Source references are necessary but not sufficient: a malformed or
    provider-specific export can repeat one reference for distinct memories.
    Including the canonical value fingerprint prevents those collisions from
    collapsing records during a rebuild.
    """

    return _stable_record_key_from_values(
        source_id=candidate.source_id,
        source_reference=candidate.source_reference,
        kind=candidate.kind,
        entity_key=candidate.entity_key,
        attribute_key=candidate.attribute_key,
        content=candidate.content,
        structured_value=candidate.structured_value,
    )


def _stable_record_key_from_values(
    *,
    source_id: str | None,
    source_reference: str | None,
    kind: str,
    entity_key: str | None,
    attribute_key: str | None,
    content: str,
    structured_value: Any,
) -> str | None:
    """Derive a record key from the identity-bearing record fields."""

    if source_id is None or source_reference is None:
        return None
    if structured_value is not None:
        value_material = "structured:" + _json(structured_value)
    else:
        normalized = unicodedata.normalize("NFKC", content).casefold()
        value_material = "content:" + " ".join(re.findall(r"\w+", normalized))
    return _hash_text(
        _json(
            [
                "source-reference-v1",
                source_id,
                source_reference,
                kind.casefold(),
                _normalized_slot_key(entity_key),
                _normalized_slot_key(attribute_key),
                _hash_text(value_material),
            ]
        )
    )


def _stable_record_key_from_row(row: Mapping[str, Any] | sqlite3.Row) -> str | None:
    """Recompute a key from a durable candidate/record row, ignoring its key."""

    try:
        structured_value = _loads(cast(str | None, row["structured_value_json"]), None)
    except (TypeError, ValueError):
        # A malformed imported value must not become a trusted identity.
        return None
    return _stable_record_key_from_values(
        source_id=cast(str | None, row["source_id"]),
        source_reference=cast(str | None, row["source_reference"]),
        kind=str(row["kind"]),
        entity_key=cast(str | None, row["entity_key"]),
        attribute_key=cast(str | None, row["attribute_key"]),
        content=str(row["content"]),
        structured_value=structured_value,
    )


def _value_fingerprint(row: sqlite3.Row) -> str:
    structured = _loads(cast(str | None, row["structured_value_json"]), None)
    if structured is not None:
        material = "structured:" + _json(structured)
    else:
        content = unicodedata.normalize("NFKC", str(row["content"])).casefold()
        material = "content:" + " ".join(re.findall(r"\w+", content))
    return _hash_text(material)


def _monotonic_security(
    record: sqlite3.Row,
    observation: sqlite3.Row,
    requested_availability: Availability,
) -> tuple[Sensitivity, Availability, set[str], set[str]]:
    sensitivity_order = {
        Sensitivity.NORMAL: 0,
        Sensitivity.SENSITIVE: 1,
        Sensitivity.HIGHLY_SENSITIVE: 2,
    }
    current_sensitivity = Sensitivity(str(record["sensitivity"]))
    observed_sensitivity = Sensitivity(str(observation["sensitivity"]))
    sensitivity = max(
        (current_sensitivity, observed_sensitivity),
        key=sensitivity_order.__getitem__,
    )
    availability_order = {
        Availability.ALWAYS: 0,
        Availability.CORE: 1,
        Availability.LOCAL: 2,
    }
    availability = max(
        (Availability(str(record["availability"])), requested_availability),
        key=availability_order.__getitem__,
    )
    if sensitivity != Sensitivity.NORMAL:
        availability = Availability.LOCAL

    current_allowed = set(_loads(record["allowed_clients_json"], []))
    observed_allowed = set(_loads(observation["allowed_clients_json"], []))
    if current_allowed and observed_allowed:
        # Empty means unrestricted in the persisted ACL model. If two
        # restrictive allowlists are disjoint, retain the existing boundary
        # rather than accidentally serializing an unrestricted empty list.
        allowed = (current_allowed & observed_allowed) or current_allowed
    else:
        allowed = current_allowed or observed_allowed
    denied = set(_loads(record["denied_clients_json"], [])) | set(
        _loads(observation["denied_clients_json"], [])
    )
    return sensitivity, availability, allowed, denied


def _migration_statements(source: str) -> Iterator[str]:
    """Yield complete SQLite statements without ``executescript`` auto-commits."""

    pending: list[str] = []
    for line in source.splitlines(keepends=True):
        pending.append(line)
        statement = "".join(pending)
        if sqlite3.complete_statement(statement):
            if statement.strip():
                yield statement
            pending.clear()
    if "".join(pending).strip():
        raise InvalidStateError("migration contains an incomplete SQL statement")


def _added_column(statement: str) -> tuple[str, str] | None:
    # Migration 010/011 place explanatory comments immediately before ALTER
    # statements.  When a process dies after the ALTER commits but before the
    # migration marker, the restart probe must still recognize that ALTER as
    # idempotently complete instead of replaying it.
    statement = _strip_leading_sql_comments(statement)
    match = re.match(
        r"\s*ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)"
        r"\s+ADD\s+COLUMN\s+([A-Za-z_][A-Za-z0-9_]*)",
        statement,
        flags=re.IGNORECASE,
    )
    return (match.group(1), match.group(2)) if match is not None else None


def _strip_leading_sql_comments(statement: str) -> str:
    """Remove whitespace and SQL comments before inspecting a statement."""

    remaining = statement.lstrip()
    while remaining.startswith("--") or remaining.startswith("/*"):
        if remaining.startswith("--"):
            newline = remaining.find("\n")
            if newline < 0:
                return ""
            remaining = remaining[newline + 1 :].lstrip()
            continue
        end = remaining.find("*/", 2)
        if end < 0:
            return ""
        remaining = remaining[end + 2 :].lstrip()
    return remaining


class _ClosingConnection(sqlite3.Connection):
    """sqlite3's context manager commits but does not close; that breaks Windows cleanup."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
        /,
    ) -> Literal[False]:
        try:
            super().__exit__(exc_type, exc_value, traceback)
            return False
        finally:
            self.close()


class CoreStore:
    """Small explicit repository layer; each public write is one transaction."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.RLock()
        self._operation_observer_local = threading.local()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path,
            timeout=10.0,
            isolation_level=None,
            check_same_thread=False,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _connect_import_operation_liveness(self) -> sqlite3.Connection:
        """Open the telemetry-only writer without the lifecycle fsync/busy budget."""
        connection = sqlite3.connect(
            self.database_path,
            timeout=LIVENESS_CONNECT_TIMEOUT_SECONDS,
            isolation_level=None,
            check_same_thread=False,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {LIVENESS_BUSY_TIMEOUT_MS}")
        mode = connection.execute("PRAGMA journal_mode").fetchone()
        if mode is None or str(mode[0]).casefold() != "wal":
            connection.close()
            raise InvalidStateError("import-operation liveness requires WAL mode")
        # This connection writes only an observer timestamp. NORMAL remains safe
        # from corruption and process crashes in WAL mode while avoiding a FULL
        # virtual-disk flush for every unchanged-byte heartbeat.
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _connect_import_operation_telemetry(self) -> sqlite3.Connection:
        """Open the serialized semantic-progress writer at WAL NORMAL durability.

        Nonterminal operation progress is queryable telemetry over authoritative
        source/blob state. A FULL WAL flush at a source-copy/parse boundary can
        make the already-committed row invisible beyond the public heartbeat
        budget on a qualified virtual SSD. NORMAL preserves transaction atomicity
        and process-crash durability without that per-transition disk flush.

        Unlike the timestamp-only liveness writer, semantic progress retains the
        store's Python write lock and normal ten-second SQLite arbitration budget.
        Terminal state, cancellation intent, errors, and results never use this
        connection.
        """
        connection = self.connect()
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def _import_operation_update_transaction(
        self,
        *,
        normal_durability: bool,
    ) -> Iterator[sqlite3.Connection]:
        connector = self._connect_import_operation_telemetry if normal_durability else self.connect
        with self._write_lock, connector() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self._write_lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def migrate(self) -> int:
        migration_dir = Path(__file__).parent / "migrations" / "core"
        migrations = sorted(migration_dir.glob("[0-9][0-9][0-9]_*.sql"))
        with self._write_lock, self.connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
            )
            applied = {
                int(row["version"])
                for row in connection.execute("SELECT version FROM schema_migrations")
            }
            for migration in migrations:
                version = int(migration.name.split("_", 1)[0])
                if version in applied:
                    continue
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _migration_statements(migration.read_text(encoding="utf-8")):
                        added_column = _added_column(statement)
                        if added_column is not None:
                            table, column = added_column
                            columns = {
                                str(row["name"])
                                for row in connection.execute(f'PRAGMA table_info("{table}")')
                            }
                            if column in columns:
                                continue
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                        (version, migration.name, utc_now()),
                    )
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
                    applied.add(version)
        return len(migrations)

    def initialize_vault(self, name: str = "My Context", display_timezone: str = "UTC") -> str:
        self.migrate()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM vaults ORDER BY created_at LIMIT 1"
            ).fetchone()
            if existing is not None:
                return str(existing["id"])
            vault_id = new_id()
            connection.execute(
                "INSERT INTO vaults(id,name,display_timezone,created_at) VALUES(?,?,?,?)",
                (vault_id, name, display_timezone, utc_now()),
            )
            now = utc_now()
            connection.execute(
                "INSERT INTO memory_policies"
                "(vault_id,mode,sensitive_mode,inference_mode,policy_version,"
                "created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    vault_id,
                    "automatic",
                    "local_only",
                    "corroborate",
                    AUTOMATIC_POLICY_VERSION,
                    now,
                    now,
                ),
            )
            return vault_id

    def repair_preledger_secrets(self) -> dict[str, Any]:
        """Remove legacy direct secret observations and physically rebuild SQLite.

        Imported source archives remain governed by their separate inert-source
        contract. This repair targets direct observations only and emits counts
        and closed codes, never copied content or content-derived fingerprints.
        """

        repaired_candidates = 0
        repaired_records = 0
        with self.transaction() as connection:
            affected_record_ids: list[str] = []
            for record in connection.execute(
                "SELECT r.*,"
                "(SELECT json_group_array(v.snapshot_json) "
                "FROM context_record_versions v WHERE v.record_id=r.id) AS history_material "
                "FROM context_records r WHERE r.source_id IS NULL ORDER BY r.created_at,r.id"
            ).fetchall():
                record_material = {
                    "content": record["content"],
                    "structured_value": _loads(record["structured_value_json"], None),
                    "source_reference": record["source_reference"],
                    "evidence": record["evidence"],
                    "history": _loads(record["history_material"], []),
                }
                if contains_secret_like_value(record_material):
                    affected_record_ids.append(str(record["id"]))

            rows = connection.execute(
                "SELECT c.*,s.mode AS ingestion_mode,"
                "(SELECT json_group_array(json_object('description',e.description,"
                "'evidence',e.evidence)) FROM context_errors e WHERE e.candidate_id=c.id) "
                "AS error_material "
                "FROM context_candidates c "
                "LEFT JOIN ingestion_sessions s ON s.id=c.session_id "
                "WHERE c.source_id IS NULL AND COALESCE(s.mode,'') != ? "
                "ORDER BY c.created_at,c.id",
                (IngestionMode.ARCHIVE.value,),
            ).fetchall()
            affected_ids: list[str] = []
            for row in rows:
                material = {
                    "kind": row["kind"],
                    "content": row["content"],
                    "structured_value": _loads(row["structured_value_json"], None),
                    "entity_key": row["entity_key"],
                    "attribute_key": row["attribute_key"],
                    "scopes": _loads(row["scopes_json"], []),
                    "tags": _loads(row["tags_json"], []),
                    "source_reference": row["source_reference"],
                    "source_service": row["source_service"],
                    "source_type": row["source_type"],
                    "evidence": row["evidence"],
                    "allowed_clients": _loads(row["allowed_clients_json"], []),
                    "denied_clients": _loads(row["denied_clients_json"], []),
                    "idempotency_key": row["idempotency_key"],
                    "context_errors": _loads(row["error_material"], []),
                }
                if contains_secret_like_value(material):
                    affected_ids.append(str(row["id"]))

            for record_id in affected_record_ids:
                if (
                    connection.execute(
                        "SELECT 1 FROM context_records WHERE id=?", (record_id,)
                    ).fetchone()
                    is not None
                ):
                    self._purge_record_tx(
                        connection,
                        record_id,
                        purge_scope="secret_boundary_repair",
                        purged_at=utc_now(),
                    )
                    repaired_records += 1

            for candidate_id in affected_ids:
                record_ids = {
                    str(row["id"])
                    for row in connection.execute(
                        "SELECT id FROM context_records WHERE candidate_id=? OR id=("
                        "SELECT record_id FROM context_candidates WHERE id=?)",
                        (candidate_id, candidate_id),
                    ).fetchall()
                }
                record_ids.update(
                    str(row["record_id"])
                    for row in connection.execute(
                        "SELECT record_id FROM context_observation_links WHERE observation_id=?",
                        (candidate_id,),
                    ).fetchall()
                )
                for record_id in sorted(record_ids):
                    if (
                        connection.execute(
                            "SELECT 1 FROM context_records WHERE id=?", (record_id,)
                        ).fetchone()
                        is not None
                    ):
                        self._purge_record_tx(
                            connection,
                            record_id,
                            purge_scope="secret_boundary_repair",
                            purged_at=utc_now(),
                        )
                        repaired_records += 1
                candidate = connection.execute(
                    "SELECT session_id FROM context_candidates WHERE id=?", (candidate_id,)
                ).fetchone()
                if candidate is None:
                    repaired_candidates += 1
                    continue
                self._detach_candidate_from_batches(connection, candidate_id)
                connection.execute(
                    "DELETE FROM edge_proposal_receipts WHERE candidate_id=?", (candidate_id,)
                )
                connection.execute(
                    "DELETE FROM context_errors WHERE candidate_id=?", (candidate_id,)
                )
                connection.execute(
                    "DELETE FROM context_observation_links WHERE observation_id=?",
                    (candidate_id,),
                )
                connection.execute("DELETE FROM context_candidates WHERE id=?", (candidate_id,))
                if candidate["session_id"] is not None:
                    connection.execute(
                        "UPDATE ingestion_sessions SET candidate_count=("
                        "SELECT COUNT(*) FROM context_candidates WHERE session_id=?) WHERE id=?",
                        (candidate["session_id"], candidate["session_id"]),
                    )
                repaired_candidates += 1
            if repaired_candidates or repaired_records:
                connection.execute("DELETE FROM context_fts")
                for record in connection.execute(
                    "SELECT * FROM context_records "
                    "WHERE approval_status='approved' AND deleted_at IS NULL ORDER BY id"
                ).fetchall():
                    self._replace_fts(connection, record)

        if repaired_candidates or repaired_records:
            self._compact_secret_repair()
        return {
            "reason_code": "preledger_secret_repair",
            "repaired_candidates": repaired_candidates,
            "repaired_records": repaired_records,
            "compacted": repaired_candidates > 0 or repaired_records > 0,
            "external_backup_action": "retire_or_replace_historical_backups",
        }

    def _compact_secret_repair(self) -> None:
        """Checkpoint and rebuild the affected live SQLite storage boundary."""

        database_size = self.database_path.stat().st_size
        required_free = database_size * 2 + 16_777_216
        if shutil.disk_usage(self.database_path.parent).free < required_free:
            raise InvalidStateError("insufficient disk space for secret-boundary compaction")
        with self._write_lock, self.connect() as connection:
            checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if checkpoint is not None and int(checkpoint[0]) != 0:
                raise InvalidStateError(
                    "secret-boundary compaction requires exclusive database access"
                )
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def vault_id(self) -> str:
        with self.connect() as connection:
            row = connection.execute("SELECT id FROM vaults ORDER BY created_at LIMIT 1").fetchone()
        if row is None:
            raise InvalidStateError("Core vault is not initialized")
        return str(row["id"])

    def add_source(
        self,
        content: bytes,
        *,
        source_service: str,
        source_type: str,
        filename: str | None = None,
        media_type: str = "application/octet-stream",
        metadata: Mapping[str, Any] | None = None,
        parser_warnings: Sequence[str] = (),
        import_status: Literal["processing", "complete", "failed", "cancelled"] = "complete",
    ) -> SourceOut:
        if len(content) > MAX_IMPORT_BYTES:
            raise InvalidStateError(f"source exceeds the {MAX_IMPORT_BYTES}-byte size limit")
        content_hash = hashlib.sha256(content).hexdigest()
        created_at = utc_now()
        vault_id = self.vault_id()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT sr.*, sb.byte_size, sb.media_type, "
                "(SELECT COUNT(*) FROM context_candidates cc WHERE cc.source_id=sr.id) "
                "AS candidate_count FROM source_records sr "
                "JOIN source_blobs sb ON sb.content_hash=sr.content_hash "
                "WHERE sr.vault_id=? AND sr.content_hash=? AND sr.source_service=? "
                "AND sr.source_type=?",
                (vault_id, content_hash, source_service, source_type),
            ).fetchone()
            if existing is not None:
                if existing["deleted_at"] is not None:
                    self._restore_source_tx(
                        connection,
                        str(existing["id"]),
                        reason="restored by duplicate re-import",
                        actor="local-import",
                    )
                    existing = self._source_row_tx(
                        connection, str(existing["id"]), include_deleted=False
                    )
                    assert existing is not None
                return self._source_out(existing, duplicate=True)
            storage_kind = "inline" if len(content) <= SOURCE_BLOB_CHUNK_BYTES else "chunked"
            inserted = connection.execute(
                "INSERT OR IGNORE INTO source_blobs"
                "(content_hash,content,byte_size,media_type,created_at,storage_kind,blob_complete) "
                "VALUES(?,?,?,?,?,?,1)",
                (
                    content_hash,
                    content if storage_kind == "inline" else b"",
                    len(content),
                    media_type,
                    created_at,
                    storage_kind,
                ),
            )
            if inserted.rowcount == 1 and storage_kind == "chunked":
                for chunk_index, start in enumerate(
                    range(0, len(content), SOURCE_BLOB_CHUNK_BYTES)
                ):
                    chunk = content[start : start + SOURCE_BLOB_CHUNK_BYTES]
                    connection.execute(
                        "INSERT INTO source_blob_chunks"
                        "(content_hash,chunk_index,content,byte_size) VALUES(?,?,?,?)",
                        (content_hash, chunk_index, chunk, len(chunk)),
                    )
            elif inserted.rowcount != 1:
                existing_blob = connection.execute(
                    "SELECT blob_complete,byte_size FROM source_blobs WHERE content_hash=?",
                    (content_hash,),
                ).fetchone()
                if existing_blob is None or int(existing_blob["blob_complete"]) != 1:
                    raise ConflictError(
                        "identical content is still staging under another import operation"
                    )
                if int(existing_blob["byte_size"]) != len(content):
                    raise InvalidStateError("content hash collision with different byte size")
            source_id = new_id()
            connection.execute(
                "INSERT INTO source_records"
                "(id,vault_id,content_hash,source_service,source_type,filename,metadata_json,"
                "import_status,parser_warnings_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    vault_id,
                    content_hash,
                    source_service,
                    source_type,
                    filename,
                    _json(dict(metadata or {})),
                    import_status,
                    _json(list(parser_warnings)[:512]),
                    created_at,
                ),
            )
            return SourceOut(
                id=source_id,
                content_hash=content_hash,
                source_service=source_service,
                source_type=source_type,
                filename=filename,
                media_type=media_type,
                byte_size=len(content),
                created_at=created_at,
                import_status=import_status,
                metadata=dict(metadata or {}),
                parser_warnings=list(parser_warnings)[:512],
            )

    def add_source_file(
        self,
        path: Path,
        *,
        source_service: str,
        source_type: str,
        filename: str | None = None,
        media_type: str = "application/octet-stream",
        metadata: Mapping[str, Any] | None = None,
        parser_warnings: Sequence[str] = (),
        import_status: Literal["processing", "complete", "failed", "cancelled"] = "complete",
    ) -> SourceOut:
        """Store a source from disk without materializing the complete file in memory."""
        resolved = path.expanduser().resolve()
        if resolved.stat().st_size > MAX_IMPORT_BYTES:
            raise InvalidStateError(f"source exceeds the {MAX_IMPORT_BYTES}-byte size limit")
        digest = hashlib.sha256()
        byte_size = 0
        with resolved.open("rb") as source_stream:
            while chunk := source_stream.read(1024 * 1024):
                digest.update(chunk)
                byte_size += len(chunk)
                if byte_size > MAX_IMPORT_BYTES:
                    raise InvalidStateError(
                        f"source exceeds the {MAX_IMPORT_BYTES}-byte size limit"
                    )
        content_hash = digest.hexdigest()
        created_at = utc_now()
        vault_id = self.vault_id()
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT sr.*, sb.byte_size, sb.media_type, "
                "(SELECT COUNT(*) FROM context_candidates cc WHERE cc.source_id=sr.id) "
                "AS candidate_count FROM source_records sr "
                "JOIN source_blobs sb ON sb.content_hash=sr.content_hash "
                "WHERE sr.vault_id=? AND sr.content_hash=? AND sr.source_service=? "
                "AND sr.source_type=?",
                (vault_id, content_hash, source_service, source_type),
            ).fetchone()
            if existing is not None:
                if existing["deleted_at"] is not None:
                    self._restore_source_tx(
                        connection,
                        str(existing["id"]),
                        reason="restored by duplicate re-import",
                        actor="local-import",
                    )
                    existing = self._source_row_tx(
                        connection, str(existing["id"]), include_deleted=False
                    )
                    assert existing is not None
                return self._source_out(existing, duplicate=True)

            storage_kind = "inline" if byte_size == 0 else "chunked"
            inserted = connection.execute(
                "INSERT OR IGNORE INTO source_blobs"
                "(content_hash,content,byte_size,media_type,created_at,storage_kind,blob_complete) "
                "VALUES(?,?,?,?,?,?,1)",
                (
                    content_hash,
                    b"",
                    byte_size,
                    media_type,
                    created_at,
                    storage_kind,
                ),
            )
            if inserted.rowcount == 1 and storage_kind == "chunked":
                written_digest = hashlib.sha256()
                written_size = 0
                with resolved.open("rb") as source_stream:
                    chunk_index = 0
                    while chunk := source_stream.read(SOURCE_BLOB_CHUNK_BYTES):
                        connection.execute(
                            "INSERT INTO source_blob_chunks"
                            "(content_hash,chunk_index,content,byte_size) VALUES(?,?,?,?)",
                            (content_hash, chunk_index, chunk, len(chunk)),
                        )
                        written_digest.update(chunk)
                        written_size += len(chunk)
                        chunk_index += 1
                if written_size != byte_size or written_digest.hexdigest() != content_hash:
                    raise InvalidStateError("source file changed while it was being imported")
            elif inserted.rowcount != 1:
                existing_blob = connection.execute(
                    "SELECT blob_complete,byte_size FROM source_blobs WHERE content_hash=?",
                    (content_hash,),
                ).fetchone()
                if existing_blob is None or int(existing_blob["blob_complete"]) != 1:
                    raise ConflictError(
                        "identical content is still staging under another import operation"
                    )
                if int(existing_blob["byte_size"]) != byte_size:
                    raise InvalidStateError("content hash collision with different byte size")

            source_id = new_id()
            connection.execute(
                "INSERT INTO source_records"
                "(id,vault_id,content_hash,source_service,source_type,filename,metadata_json,"
                "import_status,parser_warnings_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    vault_id,
                    content_hash,
                    source_service,
                    source_type,
                    filename,
                    _json(dict(metadata or {})),
                    import_status,
                    _json(list(parser_warnings)[:512]),
                    created_at,
                ),
            )
            return SourceOut(
                id=source_id,
                content_hash=content_hash,
                source_service=source_service,
                source_type=source_type,
                filename=filename,
                media_type=media_type,
                byte_size=byte_size,
                created_at=created_at,
                import_status=import_status,
                metadata=dict(metadata or {}),
                parser_warnings=list(parser_warnings)[:512],
            )

    def _source_out(self, row: sqlite3.Row, *, duplicate: bool = False) -> SourceOut:
        keys = set(row.keys())
        return SourceOut(
            id=str(row["id"]),
            content_hash=str(row["content_hash"]),
            source_service=str(row["source_service"]),
            source_type=str(row["source_type"]),
            filename=cast(str | None, row["filename"]),
            media_type=str(row["media_type"]),
            byte_size=int(row["byte_size"]),
            created_at=str(row["created_at"]),
            duplicate=duplicate,
            import_status=(
                cast(
                    Literal["processing", "complete", "failed", "cancelled"],
                    row["import_status"],
                )
                if "import_status" in keys
                else "complete"
            ),
            metadata=(
                cast(dict[str, Any], _loads(row["metadata_json"], {}))
                if "metadata_json" in keys
                else {}
            ),
            parser_warnings=(
                cast(list[str], _loads(row["parser_warnings_json"], []))[:512]
                if "parser_warnings_json" in keys
                else []
            ),
            candidate_count=(int(row["candidate_count"]) if "candidate_count" in keys else 0),
            deleted_at=(cast(str | None, row["deleted_at"]) if "deleted_at" in keys else None),
            deleted_reason=(
                cast(str | None, row["deleted_reason"]) if "deleted_reason" in keys else None
            ),
        )

    def _source_row_tx(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        *,
        include_deleted: bool,
    ) -> sqlite3.Row | None:
        deleted_filter = "" if include_deleted else " AND sr.deleted_at IS NULL"
        return cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT sr.*,sb.byte_size,sb.media_type,"
                "(SELECT COUNT(*) FROM context_candidates cc WHERE cc.source_id=sr.id) "
                "AS candidate_count FROM source_records sr "
                "JOIN source_blobs sb ON sb.content_hash=sr.content_hash "
                f"WHERE sr.id=?{deleted_filter}",
                (source_id,),
            ).fetchone(),
        )

    def get_source(
        self,
        source_id: str,
        *,
        duplicate: bool = False,
        include_deleted: bool = False,
    ) -> SourceOut:
        with self.connect() as connection:
            row = self._source_row_tx(connection, source_id, include_deleted=include_deleted)
        if row is None:
            raise NotFoundError("source not found")
        return self._source_out(row, duplicate=duplicate)

    def update_source_import(
        self,
        source_id: str,
        *,
        import_status: Literal["processing", "complete", "failed", "cancelled"],
        metadata: Mapping[str, Any],
        parser_warnings: Sequence[str],
    ) -> None:
        with self.transaction() as connection:
            result = connection.execute(
                "UPDATE source_records SET import_status=?,metadata_json=?,"
                "parser_warnings_json=? WHERE id=? AND deleted_at IS NULL",
                (
                    import_status,
                    _json(dict(metadata)),
                    _json(list(parser_warnings)[:512]),
                    source_id,
                ),
            )
            if result.rowcount != 1:
                raise NotFoundError("source not found")

    def reclassify_source(
        self,
        source_id: str,
        *,
        source_service: str,
        source_type: str,
    ) -> SourceOut:
        """Apply parser-derived source identity after the raw blob is durable."""

        with self.transaction() as connection:
            row = self._source_row_tx(connection, source_id, include_deleted=False)
            if row is None:
                raise NotFoundError("source not found")
            if (
                str(row["source_service"]) == source_service
                and str(row["source_type"]) == source_type
            ):
                return self._source_out(row)
            existing = connection.execute(
                "SELECT sr.*,sb.byte_size,sb.media_type,"
                "(SELECT COUNT(*) FROM context_candidates cc WHERE cc.source_id=sr.id) "
                "AS candidate_count FROM source_records sr "
                "JOIN source_blobs sb ON sb.content_hash=sr.content_hash "
                "WHERE sr.vault_id=? AND sr.content_hash=? AND sr.source_service=? "
                "AND sr.source_type=? AND sr.id<>?",
                (
                    row["vault_id"],
                    row["content_hash"],
                    source_service,
                    source_type,
                    source_id,
                ),
            ).fetchone()
            if existing is not None:
                if int(row["candidate_count"]) != 0:
                    raise InvalidStateError(
                        "cannot merge a reclassified source that already has observations"
                    )
                if existing["deleted_at"] is not None:
                    self._restore_source_tx(
                        connection,
                        str(existing["id"]),
                        reason="restored by duplicate re-import",
                        actor="local-import",
                    )
                    existing = self._source_row_tx(
                        connection,
                        str(existing["id"]),
                        include_deleted=False,
                    )
                    assert existing is not None
                connection.execute("DELETE FROM source_records WHERE id=?", (source_id,))
                return self._source_out(existing, duplicate=True)
            connection.execute(
                "UPDATE source_records SET source_service=?,source_type=? WHERE id=?",
                (source_service, source_type, source_id),
            )
            updated = self._source_row_tx(connection, source_id, include_deleted=False)
            assert updated is not None
            return self._source_out(updated)

    def update_source_progress(
        self,
        source_id: str,
        *,
        progress: Mapping[str, Any],
        import_status: Literal["processing", "complete", "failed", "cancelled"] | None = None,
    ) -> None:
        """Persist bounded import progress without rewriting parser warnings."""
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT metadata_json,import_status FROM source_records "
                "WHERE id=? AND deleted_at IS NULL",
                (source_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("source not found")
            metadata = cast(dict[str, Any], _loads(row["metadata_json"], {}))
            metadata["import_progress"] = dict(progress)
            status = import_status or cast(str, row["import_status"])
            connection.execute(
                "UPDATE source_records SET metadata_json=?,import_status=? "
                "WHERE id=? AND deleted_at IS NULL",
                (_json(metadata), status, source_id),
            )

    def request_import_cancel(self, source_id: str) -> dict[str, Any]:
        """Mark an in-flight import for cancellation and return current source state."""
        source = self.get_source(source_id, duplicate=True)
        if source.import_status in {"complete", "failed", "cancelled"}:
            return {
                "source_id": source.id,
                "import_status": source.import_status,
                "cancel_requested": False,
                "already_terminal": True,
            }
        metadata = dict(source.metadata)
        progress = dict(metadata.get("import_progress") or {})
        progress["cancel_requested"] = True
        metadata["import_progress"] = progress
        metadata["cancel_requested"] = True
        self.update_source_import(
            source.id,
            import_status="processing",
            metadata=metadata,
            parser_warnings=source.parser_warnings,
        )
        return {
            "source_id": source.id,
            "import_status": "processing",
            "cancel_requested": True,
            "already_terminal": False,
        }

    # --- Import operations (durable lifecycle before a source id exists) ---

    def create_import_operation(
        self,
        *,
        operation_id: str,
        declared_byte_size: int,
        filename: str | None,
        media_type: str,
        source_service: str,
        provider_hint: str | None,
        staging_name: str,
        preflight: Mapping[str, Any] | None,
        progress: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Persist a new import operation before any source bytes are accepted."""
        if isinstance(declared_byte_size, bool) or not isinstance(declared_byte_size, int):
            raise InvalidStateError("declared_byte_size must be a non-boolean integer")
        if declared_byte_size < 0 or declared_byte_size > MAX_IMPORT_BYTES:
            raise InvalidStateError(
                f"import exceeds the {MAX_IMPORT_BYTES}-byte size limit "
                f"(received declared size {declared_byte_size} bytes; boundary+1 is refused)"
            )
        now = utc_now()
        vault_id = self.vault_id()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO import_operations("
                "id,vault_id,status,phase,declared_byte_size,bytes_received,bytes_committed,"
                "content_hash,source_id,filename,media_type,source_service,provider_hint,"
                "cancel_requested,progress_json,preflight_json,result_json,error_message,"
                "staging_name,created_at,updated_at,completed_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    operation_id,
                    vault_id,
                    "awaiting_upload",
                    "awaiting_upload",
                    declared_byte_size,
                    0,
                    0,
                    None,
                    None,
                    filename,
                    media_type,
                    source_service,
                    provider_hint,
                    0,
                    _json(dict(progress)),
                    _json(dict(preflight or {})),
                    None,
                    None,
                    staging_name,
                    now,
                    now,
                    None,
                ),
            )
        return self.get_import_operation(operation_id)

    def get_import_operation(self, operation_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM import_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("import operation not found")
        return self._import_operation_out(row)

    def _connect_import_operation_reader(self) -> sqlite3.Connection:
        """Open a bounded read-only WAL observer for the operation status route."""
        connection = sqlite3.connect(
            f"{self.database_path.as_uri()}?mode=ro",
            uri=True,
            timeout=LIVENESS_CONNECT_TIMEOUT_SECONDS,
            isolation_level=None,
            check_same_thread=False,
            factory=_ClosingConnection,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout = {LIVENESS_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA query_only = ON")
        return connection

    def list_import_operations(
        self,
        *,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses = ["vault_id=?"]
        params: list[Any] = [self.vault_id()]
        if status is not None:
            clauses.append("status=?")
            params.append(status)
        where = " AND ".join(clauses)
        with self.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM import_operations WHERE {where}",
                    params,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM import_operations WHERE {where} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, min(max(limit, 1), 500), max(offset, 0)),
            ).fetchall()
        return [self._import_operation_out(row) for row in rows], total

    def update_import_operation(
        self,
        operation_id: str,
        *,
        status: str | None = None,
        phase: str | None = None,
        bytes_received: int | None = None,
        bytes_committed: int | None = None,
        content_hash: str | None = None,
        source_id: str | None = None,
        cancel_requested: bool | None = None,
        progress: Mapping[str, Any] | None = None,
        preflight: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        error_message: str | None = None,
        clear_error: bool = False,
        completed: bool = False,
    ) -> dict[str, Any]:
        """Update durable operation telemetry. Incomplete source blobs stay non-canonical."""
        terminal = frozenset({"complete", "failed", "cancelled"})
        # content_hash/source_id on this row are observer and restart-recovery
        # references, not source authority. Their source_blobs/source_records
        # transactions remain FULL, so an explicit nonterminal update may carry
        # those references on the NORMAL telemetry path. Everything that closes,
        # cancels, diagnoses, or clears an operation remains FULL below.
        nonterminal_telemetry = (
            status in {"awaiting_upload", "uploading", "processing"}
            and not completed
            and cancel_requested is None
            and preflight is None
            and result is None
            and error_message is None
            and not clear_error
        )
        with self._import_operation_update_transaction(
            normal_durability=nonterminal_telemetry
        ) as connection:
            row = connection.execute(
                "SELECT * FROM import_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("import operation not found")
            current_status = str(row["status"])
            next_status = status if status is not None else current_status
            # Fail closed: never reactivate a terminal operation via stale writers.
            if current_status in terminal and next_status not in terminal:
                raise InvalidStateError(f"import operation is already {current_status}")
            if current_status in terminal and next_status != current_status:
                raise InvalidStateError(f"import operation is already {current_status}")
            next_phase = phase if phase is not None else str(row["phase"])
            # Durable upload telemetry is monotonic: later phases (blob staging,
            # parse/member progress) must not regress committed or received bytes
            # after the raw archive has already been accepted.
            previous_received = int(row["bytes_received"])
            previous_committed = int(row["bytes_committed"])
            if bytes_received is not None and int(bytes_received) < 0:
                raise InvalidStateError("received progress cannot be negative")
            if bytes_committed is not None and int(bytes_committed) < 0:
                raise InvalidStateError("committed progress cannot be negative")
            next_received = (
                max(previous_received, int(bytes_received))
                if bytes_received is not None
                else previous_received
            )
            next_committed = (
                max(previous_committed, int(bytes_committed))
                if bytes_committed is not None
                else previous_committed
            )
            if next_committed > next_received:
                raise InvalidStateError("committed progress cannot exceed received bytes")
            if next_received > int(row["declared_byte_size"]):
                raise InvalidStateError("received bytes exceed declared size")
            # Keep reported committed progress within one chunk of durable writes.
            if next_committed + SOURCE_BLOB_CHUNK_BYTES < next_received:
                # Allow received to lead by at most one chunk; clamp reportable lead.
                pass
            next_hash = content_hash if content_hash is not None else row["content_hash"]
            next_source = source_id if source_id is not None else row["source_id"]
            next_cancel = (
                int(cancel_requested)
                if cancel_requested is not None
                else int(row["cancel_requested"])
            )
            next_progress = (
                _json(dict(progress)) if progress is not None else str(row["progress_json"])
            )
            next_preflight = (
                _json(dict(preflight)) if preflight is not None else str(row["preflight_json"])
            )
            next_result = _json(dict(result)) if result is not None else row["result_json"]
            if clear_error:
                next_error = None
            elif error_message is not None:
                next_error = error_message[:2_000]
            else:
                next_error = row["error_message"]
            now = utc_now()
            completed_at = now if completed else row["completed_at"]
            if next_status in terminal and completed_at is None:
                completed_at = now
                completed = True
            connection.execute(
                "UPDATE import_operations SET status=?,phase=?,bytes_received=?,"
                "bytes_committed=?,content_hash=?,source_id=?,cancel_requested=?,"
                "progress_json=?,preflight_json=?,result_json=?,error_message=?,"
                "updated_at=?,completed_at=? WHERE id=?",
                (
                    next_status,
                    next_phase,
                    next_received,
                    next_committed,
                    next_hash,
                    next_source,
                    next_cancel,
                    next_progress,
                    next_preflight,
                    next_result,
                    next_error,
                    now,
                    completed_at,
                    operation_id,
                ),
            )
        return self.get_import_operation(operation_id)

    def touch_import_operation_liveness(
        self,
        operation_id: str,
    ) -> bool:
        """Refresh observer-visible operation liveness without lifecycle rewrites.

        Periodic unchanged-byte heartbeats need only ``updated_at`` to advance.
        They must not:

        - rewrite status, phase, bytes, source, result, error, or progress JSON
        - re-enter the full lifecycle updater or re-fetch the full row
        - wait behind the store's Python lifecycle-write lock
        - turn SQLite lock latency into a multi-second observer gap

        Returns True when a non-terminal row was touched. Returns False when the
        row is terminal or SQLite is busy/locked; callers retry on the next
        heartbeat tick. Missing rows still
        raise ``NotFoundError`` (configuration/use error, not lock pressure).
        """
        connection: sqlite3.Connection | None = None
        try:
            connection = self._connect_import_operation_liveness()
            connection.execute("BEGIN IMMEDIATE")
            now = utc_now()
            try:
                cursor = connection.execute(
                    "UPDATE import_operations SET updated_at=? "
                    "WHERE id=? AND status NOT IN ('complete','failed','cancelled')",
                    (now, operation_id),
                )
                if cursor.rowcount != 1:
                    row = connection.execute(
                        "SELECT status FROM import_operations WHERE id=?",
                        (operation_id,),
                    ).fetchone()
                    connection.rollback()
                    if row is None:
                        raise NotFoundError("import operation not found")
                    return False
                connection.commit()
                return True
            except NotFoundError:
                raise
            except BaseException:
                with suppress(sqlite3.Error):
                    connection.rollback()
                raise
        except sqlite3.OperationalError as error:
            with suppress(sqlite3.Error):
                if connection is not None:
                    connection.rollback()
            code = getattr(error, "sqlite_errorcode", None)
            primary_code = code & 0xFF if isinstance(code, int) else None
            if primary_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
                return False
            raise
        finally:
            if connection is not None:
                with suppress(sqlite3.Error):
                    connection.close()

    def claim_import_operation_upload(self, operation_id: str) -> dict[str, Any]:
        """Exclusively claim an operation for upload (awaiting_upload -> uploading)."""
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM import_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("import operation not found")
            status = str(row["status"])
            if status in {"complete", "failed", "cancelled"}:
                raise InvalidStateError(f"import operation is already {status}")
            if status != "awaiting_upload":
                raise ConflictError(f"import operation upload already claimed (status={status})")
            cursor = connection.execute(
                "UPDATE import_operations SET status=?,phase=?,updated_at=? "
                "WHERE id=? AND status=?",
                ("uploading", "uploading", now, operation_id, "awaiting_upload"),
            )
            if cursor.rowcount != 1:
                raise ConflictError("import operation upload already claimed")
        return self.get_import_operation(operation_id)

    def claim_import_operation_retry(self, operation_id: str) -> dict[str, Any]:
        """Exclusively claim a failed/cancelled operation with preserved source for retry."""
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM import_operations WHERE id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("import operation not found")
            status = str(row["status"])
            if status == "complete":
                return self._import_operation_out(row)
            if row["source_id"] is None:
                raise InvalidStateError(
                    "import operation has no preserved source; re-upload is required"
                )
            if status not in {"failed", "cancelled"}:
                raise ConflictError(f"import operation is not retryable (status={status})")
            loaded = _loads(cast(str | None, row["progress_json"]), {})
            progress = dict(cast(dict[str, Any], loaded))
            progress.update(
                {
                    "phase": "parsing",
                    "message": "retry claimed for parse/ingest",
                    "cancel_requested": False,
                    "cancel_acknowledged": False,
                }
            )
            cursor = connection.execute(
                "UPDATE import_operations SET status=?,phase=?,cancel_requested=?,"
                "progress_json=?,error_message=NULL,completed_at=NULL,updated_at=? "
                "WHERE id=? AND status IN ('failed','cancelled') AND source_id IS NOT NULL",
                (
                    "processing",
                    "parsing",
                    0,
                    _json(progress),
                    now,
                    operation_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ConflictError("import operation retry already claimed")
        return self.get_import_operation(operation_id)

    def request_import_operation_cancel(self, operation_id: str) -> dict[str, Any]:
        operation = self.get_import_operation(operation_id)
        if operation["status"] in {"complete", "failed", "cancelled"}:
            return {
                "operation_id": operation_id,
                "status": operation["status"],
                "cancel_requested": False,
                "already_terminal": True,
                "source_id": operation.get("source_id"),
            }
        # No worker consumes awaiting_upload; terminalize immediately.
        if operation["status"] == "awaiting_upload":
            bytes_total = max(int(operation.get("declared_byte_size") or 1), 1)
            progress = {
                "phase": "cancelled",
                "bytes_processed": 0,
                "bytes_total": bytes_total,
                "percent": 0,
                "message": "import cancelled",
                "cancel_requested": True,
                "cancel_acknowledged": True,
            }
            updated = self.update_import_operation(
                operation_id,
                status="cancelled",
                phase="cancelled",
                cancel_requested=True,
                progress=progress,
                completed=True,
            )
            return {
                "operation_id": operation_id,
                "status": updated["status"],
                "cancel_requested": True,
                "already_terminal": False,
                "source_id": updated.get("source_id"),
                "immediate": True,
            }
        progress = dict(operation.get("progress") or {})
        progress["cancel_requested"] = True
        updated = self.update_import_operation(
            operation_id,
            cancel_requested=True,
            progress=progress,
        )
        return {
            "operation_id": operation_id,
            "status": updated["status"],
            "cancel_requested": True,
            "already_terminal": False,
            "source_id": updated.get("source_id"),
        }

    def list_nonterminal_import_operations(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM import_operations "
                "WHERE status IN ('awaiting_upload','uploading','processing') "
                "ORDER BY created_at"
            ).fetchall()
        return [self._import_operation_out(row) for row in rows]

    def begin_incomplete_source_blob(
        self,
        *,
        content_hash: str,
        byte_size: int,
        media_type: str,
    ) -> Literal["created", "complete", "in_progress"]:
        """Create or inspect a content-addressed blob before it is canonical."""
        if byte_size < 0 or byte_size > MAX_IMPORT_BYTES:
            raise InvalidStateError(f"source exceeds the {MAX_IMPORT_BYTES}-byte size limit")
        created_at = utc_now()
        storage_kind = "inline" if byte_size == 0 else "chunked"
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT content_hash,byte_size,blob_complete FROM source_blobs "
                "WHERE content_hash=?",
                (content_hash,),
            ).fetchone()
            if existing is not None:
                if int(existing["byte_size"]) != byte_size:
                    raise InvalidStateError("content hash collision with different byte size")
                if int(existing["blob_complete"]) == 1:
                    return "complete"
                return "in_progress"
            connection.execute(
                "INSERT INTO source_blobs"
                "(content_hash,content,byte_size,media_type,created_at,storage_kind,blob_complete) "
                "VALUES(?,?,?,?,?,?,0)",
                (content_hash, b"", byte_size, media_type, created_at, storage_kind),
            )
            return "created"

    def write_source_blob_chunk(
        self,
        *,
        content_hash: str,
        chunk_index: int,
        content: bytes,
    ) -> None:
        """Durably write one incomplete-blob chunk. Not a canonical source yet."""
        if not content or len(content) > SOURCE_BLOB_CHUNK_BYTES:
            raise InvalidStateError("source blob chunk size is invalid")
        with self.transaction() as connection:
            blob = connection.execute(
                "SELECT byte_size,blob_complete,storage_kind FROM source_blobs "
                "WHERE content_hash=?",
                (content_hash,),
            ).fetchone()
            if blob is None:
                raise NotFoundError("source blob not found")
            if int(blob["blob_complete"]) == 1:
                raise InvalidStateError("cannot modify a complete source blob")
            if str(blob["storage_kind"]) != "chunked":
                raise InvalidStateError("inline source blobs do not accept chunks")
            existing = connection.execute(
                "SELECT byte_size FROM source_blob_chunks WHERE content_hash=? AND chunk_index=?",
                (content_hash, chunk_index),
            ).fetchone()
            if existing is not None:
                if int(existing["byte_size"]) != len(content):
                    raise InvalidStateError("source blob chunk size mismatch on rewrite")
                return
            connection.execute(
                "INSERT INTO source_blob_chunks"
                "(content_hash,chunk_index,content,byte_size) VALUES(?,?,?,?)",
                (content_hash, chunk_index, content, len(content)),
            )

    def finalize_source_blob(
        self,
        *,
        content_hash: str,
        expected_byte_size: int,
        media_type: str,
        inline_content: bytes | None = None,
    ) -> None:
        """Mark a blob complete only after size integrity checks pass."""
        # Lifecycle writes remain serialized across validation and completion.
        # The potentially cold chunk-index scan is a WAL reader, though, so the
        # operation-only liveness writer can keep updated_at queryable while
        # validating a multi-gigabyte blob. Only the final bounded state change
        # owns SQLite's single writer slot.
        with self._write_lock:
            with self.transaction(immediate=False) as connection:
                blob = connection.execute(
                    "SELECT byte_size,blob_complete,storage_kind FROM source_blobs "
                    "WHERE content_hash=?",
                    (content_hash,),
                ).fetchone()
                if blob is None:
                    raise NotFoundError("source blob not found")
                if int(blob["byte_size"]) != expected_byte_size:
                    raise InvalidStateError("source blob size mismatch during finalize")
                if int(blob["blob_complete"]) == 1:
                    return
                storage_kind = str(blob["storage_kind"])
                inline = expected_byte_size == 0 or storage_kind == "inline"
                payload = inline_content if inline_content is not None else b""
                if inline:
                    if len(payload) != expected_byte_size:
                        raise InvalidStateError("inline source blob payload size mismatch")
                else:
                    total = 0
                    for expected_index, chunk_row in enumerate(
                        connection.execute(
                            "SELECT chunk_index,byte_size FROM source_blob_chunks "
                            "WHERE content_hash=? ORDER BY chunk_index",
                            (content_hash,),
                        )
                    ):
                        if int(chunk_row["chunk_index"]) != expected_index:
                            raise InvalidStateError("source blob chunks are not contiguous")
                        total += int(chunk_row["byte_size"])
                    if total != expected_byte_size:
                        raise InvalidStateError("source blob chunks do not match declared size")

            with self.transaction() as connection:
                current = connection.execute(
                    "SELECT byte_size,blob_complete,storage_kind FROM source_blobs "
                    "WHERE content_hash=?",
                    (content_hash,),
                ).fetchone()
                if current is None:
                    raise NotFoundError("source blob not found")
                if int(current["byte_size"]) != expected_byte_size:
                    raise InvalidStateError("source blob size mismatch during finalize")
                if int(current["blob_complete"]) == 1:
                    return
                if str(current["storage_kind"]) != storage_kind:
                    raise InvalidStateError("source blob storage kind changed during finalize")
                if inline:
                    connection.execute(
                        "UPDATE source_blobs SET content=?,media_type=?,blob_complete=1 "
                        "WHERE content_hash=?",
                        (payload, media_type, content_hash),
                    )
                else:
                    connection.execute(
                        "UPDATE source_blobs SET media_type=?,blob_complete=1 WHERE content_hash=?",
                        (media_type, content_hash),
                    )

    def create_source_record_for_blob(
        self,
        *,
        content_hash: str,
        source_service: str,
        source_type: str,
        filename: str | None,
        metadata: Mapping[str, Any] | None = None,
        parser_warnings: Sequence[str] = (),
        import_status: Literal["processing", "complete", "failed", "cancelled"] = "processing",
    ) -> SourceOut:
        """Link a complete blob as a source record. Incomplete blobs are refused."""
        created_at = utc_now()
        vault_id = self.vault_id()
        with self.transaction() as connection:
            blob = connection.execute(
                "SELECT content_hash,byte_size,media_type,blob_complete FROM source_blobs "
                "WHERE content_hash=?",
                (content_hash,),
            ).fetchone()
            if blob is None:
                raise NotFoundError("source blob not found")
            if int(blob["blob_complete"]) != 1:
                raise InvalidStateError("incomplete source blob cannot become a canonical source")
            existing = connection.execute(
                "SELECT sr.*, sb.byte_size, sb.media_type, "
                "(SELECT COUNT(*) FROM context_candidates cc WHERE cc.source_id=sr.id) "
                "AS candidate_count FROM source_records sr "
                "JOIN source_blobs sb ON sb.content_hash=sr.content_hash "
                "WHERE sr.vault_id=? AND sr.content_hash=? AND sr.source_service=? "
                "AND sr.source_type=?",
                (vault_id, content_hash, source_service, source_type),
            ).fetchone()
            if existing is not None:
                if existing["deleted_at"] is not None:
                    self._restore_source_tx(
                        connection,
                        str(existing["id"]),
                        reason="restored by duplicate re-import",
                        actor="local-import",
                    )
                    existing = self._source_row_tx(
                        connection, str(existing["id"]), include_deleted=False
                    )
                    assert existing is not None
                return self._source_out(existing, duplicate=True)
            source_id = new_id()
            connection.execute(
                "INSERT INTO source_records"
                "(id,vault_id,content_hash,source_service,source_type,filename,metadata_json,"
                "import_status,parser_warnings_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    source_id,
                    vault_id,
                    content_hash,
                    source_service,
                    source_type,
                    filename,
                    _json(dict(metadata or {})),
                    import_status,
                    _json(list(parser_warnings)[:512]),
                    created_at,
                ),
            )
            return SourceOut(
                id=source_id,
                content_hash=content_hash,
                source_service=source_service,
                source_type=source_type,
                filename=filename,
                media_type=str(blob["media_type"]),
                byte_size=int(blob["byte_size"]),
                created_at=created_at,
                import_status=import_status,
                metadata=dict(metadata or {}),
                parser_warnings=list(parser_warnings)[:512],
            )

    def delete_incomplete_source_blob(self, content_hash: str) -> None:
        """Remove a non-canonical incomplete blob and its chunks."""
        with self.transaction() as connection:
            blob = connection.execute(
                "SELECT blob_complete FROM source_blobs WHERE content_hash=?",
                (content_hash,),
            ).fetchone()
            if blob is None:
                return
            if int(blob["blob_complete"]) == 1:
                referenced = connection.execute(
                    "SELECT 1 FROM source_records WHERE content_hash=? LIMIT 1",
                    (content_hash,),
                ).fetchone()
                if referenced is not None:
                    return
            connection.execute(
                "DELETE FROM source_blob_chunks WHERE content_hash=?",
                (content_hash,),
            )
            connection.execute(
                "DELETE FROM source_blobs WHERE content_hash=? AND blob_complete=0",
                (content_hash,),
            )

    def cleanup_orphan_incomplete_blobs(self) -> int:
        """Delete incomplete blobs not referenced by any source record."""
        removed = 0
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT content_hash FROM source_blobs WHERE blob_complete=0"
            ).fetchall()
            for row in rows:
                content_hash = str(row["content_hash"])
                referenced = connection.execute(
                    "SELECT 1 FROM source_records WHERE content_hash=? LIMIT 1",
                    (content_hash,),
                ).fetchone()
                if referenced is not None:
                    continue
                connection.execute(
                    "DELETE FROM source_blob_chunks WHERE content_hash=?",
                    (content_hash,),
                )
                connection.execute(
                    "DELETE FROM source_blobs WHERE content_hash=?",
                    (content_hash,),
                )
                removed += 1
        return removed

    def _import_operation_out(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "operation_id": str(row["id"]),
            "status": str(row["status"]),
            "phase": str(row["phase"]),
            "declared_byte_size": int(row["declared_byte_size"]),
            "bytes_received": int(row["bytes_received"]),
            "bytes_committed": int(row["bytes_committed"]),
            "content_hash": cast(str | None, row["content_hash"]),
            "source_id": cast(str | None, row["source_id"]),
            "filename": cast(str | None, row["filename"]),
            "media_type": str(row["media_type"]),
            "source_service": str(row["source_service"]),
            "provider_hint": cast(str | None, row["provider_hint"]),
            "cancel_requested": bool(row["cancel_requested"]),
            "progress": cast(dict[str, Any], _loads(row["progress_json"], {})),
            "preflight": cast(dict[str, Any], _loads(row["preflight_json"], {})),
            "result": cast(dict[str, Any] | None, _loads(row["result_json"], None)),
            "error_message": cast(str | None, row["error_message"]),
            "staging_name": str(row["staging_name"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "completed_at": cast(str | None, row["completed_at"]),
        }

    def _source_content_chunks_tx(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
    ) -> Iterator[bytes]:
        expected_size = int(row["byte_size"])
        storage_kind = str(row["storage_kind"])
        if storage_kind == "inline":
            content = bytes(row["content"])
            if len(content) != expected_size:
                raise InvalidStateError("stored inline source blob has an invalid size")
            yield content
            return
        if storage_kind != "chunked" or expected_size <= 0:
            raise InvalidStateError("stored source blob has an invalid storage kind")

        written = 0
        for expected_index, chunk_row in enumerate(
            connection.execute(
                "SELECT chunk_index,content,byte_size FROM source_blob_chunks "
                "WHERE content_hash=? ORDER BY chunk_index",
                (str(row["content_hash"]),),
            )
        ):
            chunk = bytes(chunk_row["content"])
            if (
                int(chunk_row["chunk_index"]) != expected_index
                or len(chunk) != int(chunk_row["byte_size"])
                or not chunk
                or len(chunk) > SOURCE_BLOB_CHUNK_BYTES
            ):
                raise InvalidStateError("stored source blob chunks are invalid")
            yield chunk
            written += len(chunk)
        if written != expected_size:
            raise InvalidStateError("stored source blob chunks were truncated")

    def get_source_content(self, source_id: str) -> bytes:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT sb.content_hash,sb.content,sb.byte_size,sb.storage_kind "
                "FROM source_records sr JOIN source_blobs sb "
                "ON sb.content_hash=sr.content_hash "
                "WHERE sr.id=? AND sr.deleted_at IS NULL",
                (source_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("source not found")
            content = b"".join(self._source_content_chunks_tx(connection, row))
        if hashlib.sha256(content).hexdigest() != str(row["content_hash"]):
            raise InvalidStateError("stored source blob hash does not match its identity")
        return content

    def copy_source_content_to_path(
        self,
        source_id: str,
        destination: Path,
        *,
        checkpoint: Callable[[], None] | None = None,
    ) -> int:
        """Copy a raw source blob with a callback after every bounded chunk."""
        target = destination.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT sb.content_hash,sb.content,sb.byte_size,sb.storage_kind "
                "FROM source_records sr JOIN source_blobs sb ON sb.content_hash=sr.content_hash "
                "WHERE sr.id=? AND sr.deleted_at IS NULL",
                (source_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("source not found")
            written = 0
            digest = hashlib.sha256()
            try:
                with target.open("wb") as output:
                    for chunk in self._source_content_chunks_tx(connection, row):
                        output.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                        if checkpoint is not None:
                            checkpoint()
            except BaseException:
                target.unlink(missing_ok=True)
                raise
        if written != int(row["byte_size"]) or digest.hexdigest() != str(row["content_hash"]):
            target.unlink(missing_ok=True)
            raise InvalidStateError("stored source blob failed its integrity check")
        return written

    def list_sources(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        with self.connect() as connection:
            total = int(
                connection.execute(
                    "SELECT COUNT(*) FROM source_records WHERE deleted_at IS NULL"
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT sr.*,sb.byte_size,sb.media_type,"
                "(SELECT COUNT(*) FROM context_candidates cc WHERE cc.source_id=sr.id) "
                "AS candidate_count FROM source_records sr "
                "JOIN source_blobs sb ON sb.content_hash=sr.content_hash "
                "WHERE sr.deleted_at IS NULL "
                "ORDER BY sr.created_at DESC LIMIT ? OFFSET ?",
                (min(max(limit, 1), 500), max(offset, 0)),
            ).fetchall()
        return [
            {
                **self._source_out(row).model_dump(mode="json"),
            }
            for row in rows
        ], total

    def delete_source(
        self,
        source_id: str,
        *,
        reason: str = "deleted by user",
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Reversibly hide a source and current records attributable to it."""

        with self.transaction() as connection:
            source = connection.execute(
                "SELECT * FROM source_records WHERE id=?", (source_id,)
            ).fetchone()
            if source is None:
                raise NotFoundError("source not found")
            if source["deleted_at"] is not None:
                member_rows = connection.execute(
                    "SELECT record_id FROM source_deletion_members "
                    "WHERE source_id=? ORDER BY record_id",
                    (source_id,),
                ).fetchall()
                return {
                    "source_id": source_id,
                    "deleted_at": str(source["deleted_at"]),
                    "reason": str(source["deleted_reason"] or reason),
                    "deleted_record_ids": [str(row["record_id"]) for row in member_rows],
                }

            now = utc_now()
            connection.execute(
                "UPDATE source_records SET deleted_at=?,deleted_reason=?,deleted_by=? WHERE id=?",
                (now, reason, actor, source_id),
            )
            record_ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM context_records "
                    "WHERE source_id=? AND deleted_at IS NULL ORDER BY id",
                    (source_id,),
                ).fetchall()
            ]
            for record_id in record_ids:
                tombstone = self._delete_record_tx(
                    connection,
                    record_id,
                    reason=f"source deleted: {reason}",
                    actor=actor,
                    recompute_integrity=False,
                )
                connection.execute(
                    "INSERT INTO source_deletion_members"
                    "(source_id,record_id,deleted_version,created_at) VALUES(?,?,?,?)",
                    (source_id, record_id, int(tombstone["deleted_version"]), now),
                )
            self._recompute_integrity(connection)
            self._audit(
                connection,
                actor,
                "source_deleted",
                [source_id],
                metadata={"deleted_record_count": len(record_ids)},
            )
            return {
                "source_id": source_id,
                "deleted_at": now,
                "reason": reason,
                "deleted_record_ids": record_ids,
            }

    def restore_source(
        self,
        source_id: str,
        *,
        reason: str = "restored by user",
        actor: str = "local-user",
    ) -> dict[str, Any]:
        """Restore a source and only the records deleted by its source deletion."""

        with self.transaction() as connection:
            return self._restore_source_tx(
                connection,
                source_id,
                reason=reason,
                actor=actor,
            )

    def _restore_source_tx(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        *,
        reason: str,
        actor: str,
    ) -> dict[str, Any]:
        source = connection.execute(
            "SELECT * FROM source_records WHERE id=?", (source_id,)
        ).fetchone()
        if source is None:
            raise NotFoundError("source not found")

        restored_record_ids: list[str] = []
        if source["deleted_at"] is not None:
            members = connection.execute(
                "SELECT record_id,deleted_version FROM source_deletion_members "
                "WHERE source_id=? ORDER BY record_id",
                (source_id,),
            ).fetchall()
            connection.execute(
                "UPDATE source_records SET deleted_at=NULL,deleted_reason=NULL,deleted_by=NULL "
                "WHERE id=?",
                (source_id,),
            )
            for member in members:
                record_id = str(member["record_id"])
                tombstone = connection.execute(
                    "SELECT deleted_version FROM deletion_tombstones WHERE record_id=?",
                    (record_id,),
                ).fetchone()
                current = connection.execute(
                    "SELECT * FROM context_records WHERE id=?", (record_id,)
                ).fetchone()
                if (
                    tombstone is None
                    or current is None
                    or current["deleted_at"] is None
                    or int(tombstone["deleted_version"]) != int(member["deleted_version"])
                ):
                    continue
                self._restore_current_record_tx(
                    connection,
                    current,
                    reason=f"source restored: {reason}",
                    actor=actor,
                    recompute_integrity=False,
                )
                restored_record_ids.append(record_id)
            connection.execute(
                "DELETE FROM source_deletion_members WHERE source_id=?", (source_id,)
            )
            self._recompute_integrity(connection)
            self._audit(
                connection,
                actor,
                "source_restored",
                [source_id],
                metadata={"restored_record_count": len(restored_record_ids)},
            )

        restored_source = self._source_row_tx(connection, source_id, include_deleted=False)
        assert restored_source is not None
        return {
            "source": self._source_out(restored_source).model_dump(mode="json"),
            "restored_record_ids": restored_record_ids,
        }

    def candidate_ids_for_source(self, source_id: str) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM context_candidates WHERE source_id=? ORDER BY created_at",
                (source_id,),
            ).fetchall()
        return [str(row["id"]) for row in rows]

    def withdraw_automatic_source_records(
        self,
        source_id: str,
        *,
        reason: str = "replaced by source rebuild",
        actor: str = "local-core",
    ) -> list[str]:
        """Reversibly hide current auto-applied records from one source.

        User-corrected records, independently deleted records, history, and
        the preserved raw blob are left unchanged.
        """

        with self.transaction() as connection:
            withdrawn = self._withdraw_automatic_source_records_tx(
                connection,
                source_id,
                reason=reason,
                actor=actor,
            )
            if withdrawn:
                self._recompute_integrity(connection)
                self._audit(
                    connection,
                    actor,
                    "source_rebuild_withdrew_automatic_records",
                    [source_id],
                    metadata={"withdrawn_record_count": len(withdrawn)},
                )
            return withdrawn

    def _withdraw_automatic_source_records_tx(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        *,
        reason: str,
        actor: str,
    ) -> list[str]:
        """Withdraw only current records produced by the archive importer.

        The origin check is intentional: a direct/local-admin record can retain
        an imported source reference, but it is not an automatic rebuild target.
        User edits and privacy changes are checked again inside the same write
        transaction so eligibility cannot go stale between staging and publish.
        """

        rows = connection.execute(
            "SELECT id FROM context_records "
            "WHERE source_id=? AND deleted_at IS NULL AND approval_status='approved' "
            "AND observation_origin=? ORDER BY id",
            (source_id, ObservationOrigin.ARCHIVE_IMPORT.value),
        ).fetchall()
        withdrawn: list[str] = []
        for row in rows:
            record_id = str(row["id"])
            if self._record_has_user_edit_tx(connection, record_id):
                continue
            self._delete_record_for_source_rebuild_tx(
                connection,
                record_id,
                source_id=source_id,
                actor=actor,
                recompute_integrity=False,
            )
            withdrawn.append(record_id)
        return withdrawn

    def _delete_record_for_source_rebuild_tx(
        self,
        connection: sqlite3.Connection,
        record_id: str,
        *,
        source_id: str,
        actor: str,
        recompute_integrity: bool,
    ) -> dict[str, Any]:
        """Use the only Core-internal capability that can mint rebuild provenance."""

        if not source_id:
            raise InvalidStateError("source-rebuild provenance requires a source")
        record = connection.execute(
            "SELECT * FROM context_records WHERE id=? AND deleted_at IS NULL",
            (record_id,),
        ).fetchone()
        source = connection.execute(
            "SELECT id,deleted_at,import_status FROM source_records WHERE id=?",
            (source_id,),
        ).fetchone()
        if (
            record is None
            or source is None
            or source["deleted_at"] is not None
            or source["import_status"] is None
            or str(record["source_id"]) != source_id
            or str(record["observation_origin"] or "")
            != ObservationOrigin.ARCHIVE_IMPORT.value
            or self._record_has_user_edit_tx(connection, record_id)
        ):
            raise InvalidStateError("record is not a valid automatic source-rebuild target")

        # First create an ordinary tombstone.  Only after that durable write
        # succeeds do we upgrade it inside this validated internal path.  A
        # crash between the two writes therefore fails closed as ordinary.
        tombstone = self._delete_record_tx(
            connection,
            record_id,
            reason=SOURCE_REBUILD_REASON,
            actor=actor,
            recompute_integrity=recompute_integrity,
        )
        connection.execute(
            "UPDATE deletion_tombstones SET deletion_origin='source_rebuild',"
            "deletion_source_id=? WHERE record_id=?",
            (source_id, record_id),
        )
        return tombstone

    def publish_source_rebuild(
        self,
        source_id: str,
        session_id: str,
        *,
        rebuild_generation: int,
        actor: str = "local-core",
    ) -> list[str]:
        """Atomically replace one source's automatic archive records.

        Candidates must already be in a finished archive session, but remain
        staged until this transaction. If withdrawal or any policy evaluation
        fails, SQLite rolls back both the old-record withdrawals and all new
        decisions, leaving the prior current context intact.
        """

        if rebuild_generation < 1:
            raise InvalidStateError("source rebuild generation must be positive")

        with self.transaction() as connection:
            session = connection.execute(
                "SELECT * FROM ingestion_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None:
                raise NotFoundError("ingestion session not found")
            if str(session["mode"]) != IngestionMode.ARCHIVE.value:
                raise InvalidStateError("source rebuild requires an archive session")
            if session["client_id"] is not None:
                raise InvalidStateError("source rebuild cannot publish a client session")
            if str(session["status"]) != "finished":
                raise InvalidStateError("source rebuild session is not finished")
            accessible_sources = _loads(session["accessible_sources_json"], [])
            if source_id not in accessible_sources:
                raise InvalidStateError("source rebuild session does not cover the source")
            source = connection.execute(
                "SELECT id,metadata_json FROM source_records "
                "WHERE id=? AND deleted_at IS NULL AND import_status IS NOT NULL",
                (source_id,),
            ).fetchone()
            if source is None:
                raise NotFoundError("source not found")
            source_metadata = cast(dict[str, Any], _loads(source["metadata_json"], {}))
            published_generation_raw = source_metadata.get("rebuild_published_generation")
            published_session_id = source_metadata.get("rebuild_published_session_id")
            if published_generation_raw is not None:
                try:
                    published_generation = int(published_generation_raw)
                except (TypeError, ValueError) as error:
                    raise InvalidStateError("source rebuild publish marker is invalid") from error
                if published_generation > rebuild_generation:
                    return []
                if published_generation == rebuild_generation:
                    if published_session_id == session_id:
                        return []
                    raise InvalidStateError("source rebuild generation belongs to another session")
            elif not bool(source_metadata.get("rebuild_in_progress")):
                raise InvalidStateError("source rebuild publish marker is missing")

            withdrawn = self._withdraw_automatic_source_records_tx(
                connection,
                source_id,
                reason="replaced by source rebuild",
                actor=actor,
            )
            staged = connection.execute(
                "SELECT id FROM context_candidates "
                "WHERE session_id=? AND disposition='staged' ORDER BY created_at,id",
                (session_id,),
            ).fetchall()
            for item in staged:
                self._evaluate_observation_tx(
                    connection,
                    str(item["id"]),
                    origin=ObservationOrigin.ARCHIVE_IMPORT,
                    actor=actor,
                    principal=None,
                )
            if withdrawn:
                self._recompute_integrity(connection)
                self._audit(
                    connection,
                    actor,
                    "source_rebuild_withdrew_automatic_records",
                    [source_id],
                    metadata={"withdrawn_record_count": len(withdrawn)},
                )
            source_metadata["rebuild_published_generation"] = rebuild_generation
            source_metadata["rebuild_published_session_id"] = session_id
            connection.execute(
                "UPDATE source_records SET metadata_json=? WHERE id=?",
                (_json(source_metadata), source_id),
            )
            return withdrawn

    @staticmethod
    def _record_has_user_edit_tx(connection: sqlite3.Connection, record_id: str) -> bool:
        correction = connection.execute(
            "SELECT 1 FROM context_candidates WHERE supersedes=? "
            "AND lower(kind)='correction' LIMIT 1",
            (record_id,),
        ).fetchone()
        if correction is not None:
            return True
        versions = connection.execute(
            "SELECT reason FROM context_record_versions WHERE record_id=?",
            (record_id,),
        ).fetchall()
        return any(
            "availability changed" in str(row["reason"] or "").casefold()
            or "by user" in str(row["reason"] or "").casefold()
            or "explicit user correction" in str(row["reason"] or "").casefold()
            for row in versions
        )

    def create_client(self, request: ClientCreate) -> tuple[ClientPrincipal, str]:
        token = generate_token()
        client_id = new_id()
        vault_id = self.vault_id()
        created_at = utc_now()
        scopes = sorted(set(request.scopes))
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO client_registrations"
                "(id,vault_id,name,token_hash,scopes_json,auto_approve,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    client_id,
                    vault_id,
                    request.name,
                    hash_token(token),
                    _json(scopes),
                    int(request.auto_approve),
                    created_at,
                ),
            )
            for scope in scopes:
                connection.execute(
                    "INSERT INTO permission_grants(id,client_id,scope,granted_at) VALUES(?,?,?,?)",
                    (new_id(), client_id, scope, created_at),
                )
        return ClientPrincipal(
            client_id, request.name, frozenset(scopes), request.auto_approve
        ), token

    def client_count(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM client_registrations").fetchone()[0]
            )

    def ensure_local_development_principal(self) -> ClientPrincipal:
        """Create a DB-backed principal only for explicit authentication-disabled mode."""
        client_id = "local-development"
        scopes = ["*", "admin", "context:read", "context:write"]
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT name,scopes_json,auto_approve FROM client_registrations WHERE id=?",
                (client_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO client_registrations"
                    "(id,vault_id,name,token_hash,scopes_json,auto_approve,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (
                        client_id,
                        self.vault_id(),
                        "Local development administrator",
                        hash_token(generate_token()),
                        _json(scopes),
                        0,
                        utc_now(),
                    ),
                )
                return ClientPrincipal(
                    client_id, "Local development administrator", frozenset(scopes)
                )
            return ClientPrincipal(
                client_id,
                str(existing["name"]),
                frozenset(cast(list[str], _loads(existing["scopes_json"], []))),
                bool(existing["auto_approve"]),
            )

    def list_clients(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,name,scopes_json,auto_approve,revoked_at,created_at,last_used_at "
                "FROM client_registrations ORDER BY created_at"
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "scopes": _loads(row["scopes_json"], []),
                "auto_approve": bool(row["auto_approve"]),
                "revoked": row["revoked_at"] is not None,
                "created_at": str(row["created_at"]),
                "last_used_at": cast(str | None, row["last_used_at"]),
            }
            for row in rows
        ]

    def approve_remote_edge_client(
        self,
        client_id: str,
        *,
        name: str,
        scopes: Sequence[str] = ("context:read", "context:status"),
        context_scopes: Sequence[str] = (),
    ) -> ClientPrincipal:
        """Persist the Core-side half of an explicitly approved remote identity."""

        if not client_id.startswith("edge:") or len(client_id) > 200:
            raise ValueError("invalid remote Edge client ID")
        allowed = frozenset(scopes).intersection({"context:read", "context:status"})
        if "context:read" not in allowed:
            raise ValueError("remote Edge clients require context:read")
        bounded_context = sorted({str(scope) for scope in context_scopes if str(scope)})
        if len(bounded_context) > 64 or any(len(scope) > 200 for scope in bounded_context):
            raise ValueError("remote Edge context scopes are invalid")
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO remote_edge_clients"
                "(id,name,scopes_json,context_scopes_json,approved_at,revoked_at) "
                "VALUES(?,?,?,?,?,NULL) ON CONFLICT(id) DO UPDATE SET "
                "name=excluded.name,scopes_json=excluded.scopes_json,"
                "context_scopes_json=excluded.context_scopes_json,"
                "approved_at=excluded.approved_at,revoked_at=NULL",
                (client_id, name[:200], _json(sorted(allowed)), _json(bounded_context), utc_now()),
            )
        return ClientPrincipal(client_id, name[:200], allowed)

    def remote_edge_principal(self, client_id: str) -> ClientPrincipal | None:
        """Resolve only Core-stored approvals; Edge-asserted scopes are never trusted."""

        with self.connect() as connection:
            row = connection.execute(
                "SELECT name,scopes_json,context_scopes_json FROM remote_edge_clients "
                "WHERE id=? AND revoked_at IS NULL",
                (client_id,),
            ).fetchone()
        if row is None:
            return None
        scopes = frozenset(cast(list[str], _loads(row["scopes_json"], [])))
        return ClientPrincipal(client_id, str(row["name"]), scopes, False)

    def remote_edge_context_scopes(self, client_id: str) -> frozenset[str]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT context_scopes_json FROM remote_edge_clients "
                "WHERE id=? AND revoked_at IS NULL",
                (client_id,),
            ).fetchone()
        if row is None:
            return frozenset()
        return frozenset(cast(list[str], _loads(row["context_scopes_json"], [])))

    def revoke_remote_edge_client(self, client_id: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE remote_edge_clients SET revoked_at=? WHERE id=?",
                (utc_now(), client_id),
            )

    def revoke_all_remote_edge_clients(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE remote_edge_clients SET revoked_at=? WHERE revoked_at IS NULL",
                (utc_now(),),
            )

    def remote_edge_clients(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id,name,scopes_json,context_scopes_json,approved_at,revoked_at "
                "FROM remote_edge_clients ORDER BY approved_at"
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "scopes": _loads(row["scopes_json"], []),
                "context_scopes": _loads(row["context_scopes_json"], []),
                "approved_at": str(row["approved_at"]),
                "revoked": row["revoked_at"] is not None,
            }
            for row in rows
        ]

    def authenticate(self, token: str) -> ClientPrincipal | None:
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT * FROM client_registrations WHERE revoked_at IS NULL"
            ).fetchall()
            for row in rows:
                if verify_token(token, str(row["token_hash"])):
                    connection.execute(
                        "UPDATE client_registrations SET last_used_at=? WHERE id=?",
                        (utc_now(), row["id"]),
                    )
                    return ClientPrincipal(
                        id=str(row["id"]),
                        name=str(row["name"]),
                        scopes=frozenset(cast(list[str], _loads(row["scopes_json"], []))),
                        auto_approve=bool(row["auto_approve"]),
                    )
        return None

    def authenticate_import_operation_observer(
        self,
        token: str,
        operation_id: str,
    ) -> tuple[ClientPrincipal, dict[str, Any] | None] | None:
        """Authenticate and read one operation without joining the writer queue.

        The high-frequency import-operation status route must remain queryable
        while a long import owns the normal lifecycle writer lock. It therefore
        verifies current revocation state and reads the operation in one freshest
        joined statement on a bounded read-only WAL connection. It intentionally
        does not update ``last_used_at`` on every progress poll.
        """
        connection = getattr(self._operation_observer_local, "connection", None)
        if connection is None:
            connection = self._connect_import_operation_reader()
            self._operation_observer_local.connection = connection
        fingerprint_key = getattr(
            self._operation_observer_local,
            "fingerprint_key",
            None,
        )
        if fingerprint_key is None:
            fingerprint_key = secrets.token_bytes(32)
            self._operation_observer_local.fingerprint_key = fingerprint_key
        fingerprint = hmac.digest(
            fingerprint_key,
            token.encode("utf-8"),
            "sha256",
        )
        cached = getattr(self._operation_observer_local, "credential", None)
        if cached is None or not hmac.compare_digest(fingerprint, cached[0]):
            if cached is not None:
                del self._operation_observer_local.credential
            rows = connection.execute(
                "SELECT * FROM client_registrations WHERE revoked_at IS NULL"
            ).fetchall()
            registration = next(
                (row for row in rows if verify_token(token, str(row["token_hash"]))),
                None,
            )
            if registration is None:
                return None
            cached = (
                fingerprint,
                str(registration["id"]),
                str(registration["token_hash"]),
            )
            self._operation_observer_local.credential = cached
        try:
            connection.execute("BEGIN")
            joined = connection.execute(
                "SELECT operation.*, operation.id AS observer_operation_id,"
                "registration.id AS observer_client_id,"
                "registration.name AS observer_client_name,"
                "registration.scopes_json AS observer_scopes_json,"
                "registration.auto_approve AS observer_auto_approve "
                "FROM client_registrations AS registration "
                "LEFT JOIN import_operations AS operation ON operation.id=? "
                "WHERE registration.id=? AND registration.token_hash=? "
                "AND registration.revoked_at IS NULL",
                (operation_id, cached[1], cached[2]),
            ).fetchone()
            if joined is not None:
                principal = ClientPrincipal(
                    id=str(joined["observer_client_id"]),
                    name=str(joined["observer_client_name"]),
                    scopes=frozenset(cast(list[str], _loads(joined["observer_scopes_json"], []))),
                    auto_approve=bool(joined["observer_auto_approve"]),
                )
                connection.commit()
                operation = (
                    self._import_operation_out(joined)
                    if joined["observer_operation_id"] is not None
                    else None
                )
                return principal, operation
            del self._operation_observer_local.credential
            connection.rollback()
            return None
        except BaseException:
            with suppress(sqlite3.Error):
                connection.rollback()
            raise

    def close_import_operation_observer(self) -> None:
        """Close the current worker thread's persistent operation observer."""
        connection = getattr(self._operation_observer_local, "connection", None)
        if connection is not None:
            connection.close()
            del self._operation_observer_local.connection
        if hasattr(self._operation_observer_local, "credential"):
            del self._operation_observer_local.credential
        if hasattr(self._operation_observer_local, "fingerprint_key"):
            del self._operation_observer_local.fingerprint_key

    def close(self) -> None:
        """Release handles required for deterministic Windows vault removal.

        Shutdown intent: short-lived owners such as packaged provider acceptance
        must close before deleting an owned data root. Windows cannot unlink the
        SQLite database, WAL, or SHM file while this process still holds a
        handle. This method is idempotent and best-effort: it closes the
        thread-local import-operation observer, then under the existing write
        lock opens a ``_ClosingConnection``, runs ``PRAGMA wal_checkpoint(TRUNCATE)``,
        and lets that context close the connection. Only ``sqlite3.Error`` is
        swallowed on this release path. It does not change ``journal_mode``,
        hide later ``rmtree`` errors, sleep, retry, or force garbage collection.

        CoreStore does not keep a process-wide writer. After ``close()``, a
        later public method may open a new connection; reuse remains available
        under the current architecture.
        """
        with suppress(sqlite3.Error):
            self.close_import_operation_observer()
        try:
            with self._write_lock, self.connect() as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            return

    def revoke_client(self, client_id: str) -> None:
        with self.transaction() as connection:
            result = connection.execute(
                "UPDATE client_registrations SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (utc_now(), client_id),
            )
            if result.rowcount != 1:
                raise NotFoundError("client not found or already revoked")

    def begin_ingestion(
        self,
        *,
        mode: IngestionMode,
        accessible_sources: Sequence[str],
        unavailable_sources: Sequence[str],
        client_id: str | None = None,
        notes: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        session_id = new_id()
        created_at = utc_now()
        vault_id = self.vault_id()
        with self.transaction() as connection:
            if idempotency_key is not None:
                existing = connection.execute(
                    "SELECT * FROM ingestion_sessions WHERE vault_id=? AND client_id IS ? "
                    "AND mode=? AND idempotency_key=?",
                    (vault_id, client_id, mode.value, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if _loads(existing["accessible_sources_json"], []) != list(
                        accessible_sources
                    ) or _loads(existing["unavailable_sources_json"], []) != list(
                        unavailable_sources
                    ):
                        raise ConflictError(
                            "begin-ingestion idempotency key was reused with different coverage"
                        )
                    return self._session_mapping(existing, replayed=True)
            connection.execute(
                "INSERT INTO ingestion_sessions"
                "(id,vault_id,client_id,mode,status,accessible_sources_json,"
                "unavailable_sources_json,notes,idempotency_key,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    session_id,
                    vault_id,
                    client_id,
                    mode.value,
                    "open",
                    _json(list(accessible_sources)),
                    _json(list(unavailable_sources)),
                    notes,
                    idempotency_key,
                    created_at,
                ),
            )
        return {"session_id": session_id, "status": "open", "created_at": created_at}

    def submit_batch(
        self,
        session_id: str,
        idempotency_key: str,
        candidates: Sequence[CandidateInput],
        *,
        client: ClientPrincipal | None = None,
    ) -> dict[str, Any]:
        accepted_candidates = list(candidates)
        refused_count = 0
        safe_idempotency_key = idempotency_key
        with self.connect() as connection:
            session_mode = connection.execute(
                "SELECT mode FROM ingestion_sessions WHERE id=?", (session_id,)
            ).fetchone()
        if session_mode is not None and str(session_mode["mode"]) != IngestionMode.ARCHIVE.value:
            accepted_candidates = [
                candidate for candidate in candidates if not contains_direct_secret(candidate)
            ]
            refused_count = len(candidates) - len(accepted_candidates)
            if refused_count:
                opaque_idempotency_key = opaque_operation_id(idempotency_key)
                if opaque_idempotency_key is None:
                    raise InvalidStateError(
                        "A secret-like batch requires an opaque UUIDv4 idempotency key"
                    )
                safe_idempotency_key = opaque_idempotency_key
        canonical_request = _json(
            [candidate.model_dump(mode="json") for candidate in accepted_candidates]
        )
        request_hash = _hash_text(canonical_request)
        created_ids: list[str] = []
        with self.transaction() as connection:
            session = connection.execute(
                "SELECT * FROM ingestion_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None:
                raise NotFoundError("ingestion session not found")
            if cast(str | None, session["client_id"]) != (
                client.id if client is not None else None
            ):
                raise NotFoundError("ingestion session not found")
            existing = connection.execute(
                "SELECT * FROM ingestion_batches WHERE session_id=? AND idempotency_key=?",
                (session_id, safe_idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise ConflictError("idempotency key was reused with different content")
                return {
                    "batch_id": str(existing["id"]),
                    "candidate_ids": _loads(existing["candidate_ids_json"], []),
                    "refused_count": int(existing["refused_count"]),
                    "refusal_reason": (
                        SECRET_REFUSAL_REASON if int(existing["refused_count"]) else None
                    ),
                    "replayed": True,
                }
            if str(session["status"]) != "open":
                raise InvalidStateError("ingestion session is already finished")
            for candidate in accepted_candidates:
                created_ids.append(
                    self._insert_candidate(connection, candidate, session_id, client)
                )
            if refused_count:
                self._secret_refusal_tx(
                    connection,
                    route="ingestion_batch",
                    operation_id=safe_idempotency_key,
                    client=client,
                )
            batch_id = new_id()
            connection.execute(
                "INSERT INTO ingestion_batches"
                "(id,session_id,idempotency_key,request_hash,candidate_ids_json,created_at,"
                "refused_count) VALUES(?,?,?,?,?,?,?)",
                (
                    batch_id,
                    session_id,
                    safe_idempotency_key,
                    request_hash,
                    _json(created_ids),
                    utc_now(),
                    refused_count,
                ),
            )
            connection.execute(
                "UPDATE ingestion_sessions SET candidate_count=candidate_count+? WHERE id=?",
                (len(created_ids), session_id),
            )
        return {
            "batch_id": batch_id,
            "candidate_ids": created_ids,
            "refused_count": refused_count,
            "refusal_reason": SECRET_REFUSAL_REASON if refused_count else None,
            "replayed": False,
        }

    def refuse_direct_candidate(
        self,
        candidate: CandidateInput,
        *,
        route: str,
        client: ClientPrincipal | None = None,
    ) -> SecretRefusalOut | None:
        """Persist only an opaque receipt when a direct candidate contains a secret."""

        if not contains_direct_secret(candidate):
            return None
        operation_id = opaque_operation_id(candidate.idempotency_key) or new_id()
        with self.transaction() as connection:
            return self._secret_refusal_tx(
                connection,
                route=route,
                operation_id=operation_id,
                client=client,
            )

    def refuse_direct_value(
        self,
        value: object,
        *,
        route: str,
        operation_id: str | None,
        client: ClientPrincipal | None = None,
    ) -> SecretRefusalOut | None:
        """Apply the same boundary to direct request shapes other than candidates."""

        if not contains_secret_like_value(value):
            return None
        safe_operation_id = opaque_operation_id(operation_id) or new_id()
        with self.transaction() as connection:
            return self._secret_refusal_tx(
                connection,
                route=route,
                operation_id=safe_operation_id,
                client=client,
            )

    def _secret_refusal_tx(
        self,
        connection: sqlite3.Connection,
        *,
        route: str,
        operation_id: str,
        client: ClientPrincipal | None,
    ) -> SecretRefusalOut:
        principal_key = client.id if client is not None else "local-core"
        existing = connection.execute(
            "SELECT * FROM secret_refusal_receipts "
            "WHERE vault_id=? AND principal_key=? AND route=? AND operation_id=?",
            (self.vault_id(), principal_key, route, operation_id),
        ).fetchone()
        if existing is not None:
            return self._secret_refusal_out(existing, replayed=True)
        receipt_id = new_id()
        created_at = utc_now()
        connection.execute(
            "INSERT INTO secret_refusal_receipts"
            "(id,vault_id,principal_key,route,operation_id,reason_code,detector_version,"
            "created_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                receipt_id,
                self.vault_id(),
                principal_key,
                route,
                operation_id,
                SECRET_REFUSAL_REASON,
                SECRET_DETECTOR_VERSION,
                created_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM secret_refusal_receipts WHERE id=?", (receipt_id,)
        ).fetchone()
        assert row is not None
        return self._secret_refusal_out(row, replayed=False)

    @staticmethod
    def _secret_refusal_out(row: sqlite3.Row, *, replayed: bool) -> SecretRefusalOut:
        return SecretRefusalOut(
            id=str(row["id"]),
            detector_version=str(row["detector_version"]),
            created_at=str(row["created_at"]),
            replayed=replayed,
        )

    def add_candidate(
        self,
        candidate: CandidateInput,
        *,
        session_id: str | None = None,
        client: ClientPrincipal | None = None,
    ) -> CandidateOut:
        with self.transaction() as connection:
            return self._add_candidate_tx(
                connection,
                candidate,
                session_id=session_id,
                client=client,
            )

    def _add_candidate_tx(
        self,
        connection: sqlite3.Connection,
        candidate: CandidateInput,
        *,
        session_id: str | None = None,
        client: ClientPrincipal | None = None,
    ) -> CandidateOut:
        # Defense in depth for internal callers: no direct secret-like payload is
        # written before the public ingestion service can issue a refusal receipt.
        # Archive batches use submit_batch/_insert_candidate and remain inert data.
        if contains_direct_secret(candidate):
            raise InvalidStateError(
                "direct secret-like content cannot enter the observation ledger"
            )
        policy_principal = self._policy_principal_tx(connection, client)
        candidate_id = self._insert_candidate(connection, candidate, session_id, client)
        row = connection.execute(
            "SELECT disposition FROM context_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if row is not None and str(row["disposition"]) == ObservationDisposition.STAGED.value:
            self._evaluate_observation_tx(
                connection,
                candidate_id,
                origin=(
                    ObservationOrigin.ONGOING_CLIENT
                    if client is not None
                    else ObservationOrigin.LOCAL_ADMIN
                ),
                actor=client.id if client is not None else "local-core",
                principal=policy_principal,
            )
        result = connection.execute(
            "SELECT * FROM context_candidates WHERE id=?", (candidate_id,)
        ).fetchone()
        if result is None:  # pragma: no cover - protected by the insert above
            raise InvalidStateError("automatic observation insert was lost")
        return self._candidate_out(result)

    def add_context_error_observation(
        self,
        candidate: CandidateInput,
        *,
        record_id: str | None,
        description: str,
        evidence: str | None,
        client: ClientPrincipal | None = None,
    ) -> CandidateOut:
        """Atomically evaluate an error observation and retain its report provenance."""

        if contains_secret_like_value(
            {
                "candidate": candidate.model_dump(mode="json"),
                "description": description,
                "evidence": evidence,
            }
        ):
            raise InvalidStateError(
                "direct secret-like content cannot enter the observation ledger"
            )
        with self.transaction() as connection:
            created = self._add_candidate_tx(connection, candidate, client=client)
            existing = connection.execute(
                "SELECT record_id,description,evidence FROM context_errors "
                "WHERE candidate_id=? ORDER BY created_at,id LIMIT 1",
                (created.id,),
            ).fetchone()
            if existing is not None:
                if (
                    cast(str | None, existing["record_id"]) != record_id
                    or str(existing["description"]) != description
                    or cast(str | None, existing["evidence"]) != evidence
                ):
                    raise ConflictError(
                        "context-error idempotency key was reused with different content"
                    )
            else:
                connection.execute(
                    "INSERT INTO context_errors"
                    "(id,vault_id,client_id,record_id,candidate_id,description,evidence,"
                    "created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        new_id(),
                        self.vault_id(),
                        client.id if client is not None else None,
                        record_id,
                        created.id,
                        description,
                        evidence,
                        utc_now(),
                    ),
                )
            return created

    def add_edge_candidate(
        self,
        proposal_id: str,
        candidate: CandidateInput,
        *,
        client_id: str,
    ) -> tuple[CandidateOut, bool]:
        """Import one Edge proposal exactly once, including across failed remote ACKs."""

        if not proposal_id or len(proposal_id) > 256:
            raise InvalidStateError("Edge proposal ID is invalid")
        if not client_id.strip() or len(client_id) > 256:
            raise InvalidStateError("Edge client ID is invalid")
        if contains_direct_secret(candidate):
            raise InvalidStateError(
                "direct secret-like content cannot enter the observation ledger"
            )
        candidate = candidate.model_copy(
            update={
                "source_service": client_id,
                "source_type": "queued_proposal",
            }
        )
        proposal_hash = _hash_text(
            _json(
                {
                    "candidate": candidate.model_dump(mode="json"),
                    "client_id": client_id,
                }
            )
        )
        vault_id = self.vault_id()
        replayed = False
        principal = ClientPrincipal(
            id=client_id,
            name="Relay client",
            scopes=frozenset(),
        )
        with self.transaction() as connection:
            receipt = connection.execute(
                "SELECT proposal_hash,candidate_id FROM edge_proposal_receipts "
                "WHERE vault_id=? AND proposal_id=?",
                (vault_id, proposal_id),
            ).fetchone()
            if receipt is not None:
                if str(receipt["proposal_hash"]) != proposal_hash:
                    raise ConflictError("Edge proposal changed after it was imported")
                candidate_id = str(receipt["candidate_id"])
                replayed = True
            else:
                candidate_id = self._insert_candidate(connection, candidate, None, None)
                connection.execute(
                    "INSERT INTO edge_proposal_receipts"
                    "(vault_id,proposal_id,proposal_hash,candidate_id,created_at) "
                    "VALUES(?,?,?,?,?)",
                    (vault_id, proposal_id, proposal_hash, candidate_id, utc_now()),
                )
            disposition = connection.execute(
                "SELECT disposition FROM context_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if (
                disposition is not None
                and str(disposition["disposition"]) == ObservationDisposition.STAGED.value
            ):
                self._evaluate_observation_tx(
                    connection,
                    candidate_id,
                    origin=ObservationOrigin.RELAY_QUEUE,
                    actor=client_id,
                    principal=principal,
                )
            row = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if row is None:  # pragma: no cover - protected by the receipt foreign key
                raise InvalidStateError("Edge proposal receipt refers to a missing candidate")
            result = self._candidate_out(row)
        return result, replayed

    def _insert_candidate(
        self,
        connection: sqlite3.Connection,
        candidate: CandidateInput,
        session_id: str | None,
        client: ClientPrincipal | None,
    ) -> str:
        data = candidate.model_dump(mode="json")
        content_hash = _hash_text(_json(data))
        if candidate.idempotency_key is not None and client is not None:
            existing = connection.execute(
                "SELECT id,content_hash FROM context_candidates "
                "WHERE vault_id=? AND submitted_by_client_id=? AND idempotency_key=?",
                (self.vault_id(), client.id, candidate.idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["content_hash"]) != content_hash:
                    raise ConflictError(
                        "proposal idempotency key was reused with different content"
                    )
                return str(existing["id"])
        candidate_id = new_id()
        created_at = utc_now()
        connection.execute(
            "INSERT INTO context_candidates"
            "(id,vault_id,session_id,source_id,source_reference,submitted_by_client_id,kind,content,"
            "structured_value_json,entity_key,attribute_key,scopes_json,tags_json,source_service,source_type,evidence,"
            "confidence,sensitivity,availability,allowed_clients_json,denied_clients_json,"
            "valid_from,expires_at,supersedes,explicit_user_statement,idempotency_key,approval_status,"
            "content_hash,schema_version,created_at,observed_at,disposition,record_key) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate_id,
                self.vault_id(),
                session_id,
                candidate.source_id,
                candidate.source_reference,
                client.id if client else None,
                candidate.kind,
                candidate.content,
                _json(candidate.structured_value)
                if candidate.structured_value is not None
                else None,
                _normalized_slot_key(candidate.entity_key),
                _normalized_slot_key(candidate.attribute_key),
                _json(candidate.scopes),
                _json(candidate.tags),
                candidate.source_service,
                candidate.source_type,
                candidate.evidence,
                candidate.confidence,
                candidate.sensitivity.value,
                candidate.availability.value,
                _json(candidate.allowed_clients),
                _json(candidate.denied_clients),
                candidate.valid_from,
                candidate.expires_at,
                candidate.supersedes,
                int(candidate.explicit_user_statement),
                candidate.idempotency_key,
                ApprovalStatus.PENDING.value,
                content_hash,
                candidate.schema_version,
                created_at,
                candidate.observed_at or created_at,
                ObservationDisposition.STAGED.value,
                _stable_record_key(candidate),
            ),
        )
        return candidate_id

    def finish_ingestion(
        self,
        session_id: str,
        coverage: CoverageReport,
        *,
        client: ClientPrincipal | None = None,
        publish: bool = True,
    ) -> dict[str, Any]:
        finished_at = utc_now()
        replayed = False
        with self.transaction() as connection:
            session = connection.execute(
                "SELECT * FROM ingestion_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None:
                raise NotFoundError("ingestion session not found")
            if cast(str | None, session["client_id"]) != (
                client.id if client is not None else None
            ):
                raise NotFoundError("ingestion session not found")
            coverage_json = _json(coverage.model_dump(mode="json"))
            if str(session["status"]) == "finished":
                if str(session["coverage_json"]) != coverage_json:
                    raise ConflictError("finished session has a different coverage report")
                replayed = True
            else:
                connection.execute(
                    "UPDATE ingestion_sessions SET status='finished', coverage_json=?, "
                    "finished_at=? "
                    "WHERE id=?",
                    (coverage_json, finished_at, session_id),
                )
            if publish:
                origin = (
                    ObservationOrigin.ARCHIVE_IMPORT
                    if str(session["mode"]) == IngestionMode.ARCHIVE.value
                    else ObservationOrigin.ONGOING_CLIENT
                )
                staged = connection.execute(
                    "SELECT id FROM context_candidates WHERE session_id=? "
                    "AND disposition='staged' ORDER BY created_at,id",
                    (session_id,),
                ).fetchall()
                actor = cast(str | None, session["client_id"]) or "local-core"
                # Re-bind from durable registrations; never trust caller-supplied scopes
                # for witness / archive explicitness (principal-shape hardening).
                policy_principal = self._policy_principal_tx(connection, client)
                for item in staged:
                    self._evaluate_observation_tx(
                        connection,
                        str(item["id"]),
                        origin=origin,
                        actor=actor,
                        principal=policy_principal,
                    )
        result = self.get_session(session_id)
        result["replayed"] = replayed
        return result

    def _session_mapping(self, row: sqlite3.Row, *, replayed: bool = False) -> dict[str, Any]:
        return {
            "session_id": str(row["id"]),
            "mode": str(row["mode"]),
            "status": str(row["status"]),
            "accessible_sources": _loads(row["accessible_sources_json"], []),
            "unavailable_sources": _loads(row["unavailable_sources_json"], []),
            "coverage": _loads(row["coverage_json"], None),
            "candidate_count": int(row["candidate_count"]),
            "created_at": str(row["created_at"]),
            "finished_at": cast(str | None, row["finished_at"]),
            "replayed": replayed,
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM ingestion_sessions WHERE id=?", (session_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError("ingestion session not found")
        return self._session_mapping(row)

    def get_candidate(self, candidate_id: str) -> CandidateOut:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError("candidate not found")
        return self._candidate_out(row)

    def list_candidates(
        self,
        *,
        status: ApprovalStatus | None = ApprovalStatus.PENDING,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[CandidateOut], int]:
        conditions = ["vault_id=?"]
        parameters: list[Any] = [self.vault_id()]
        if status is not None:
            conditions.append("approval_status=?")
            parameters.append(status.value)
        if source_id is not None:
            conditions.append("source_id=?")
            parameters.append(source_id)
        where = " AND ".join(conditions)
        with self.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM context_candidates WHERE {where}", parameters
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM context_candidates WHERE {where} "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*parameters, min(max(limit, 1), 500), max(offset, 0)],
            ).fetchall()
        return [self._candidate_out(row) for row in rows], total

    def list_observations(
        self,
        *,
        disposition: ObservationDisposition | None = None,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[ObservationOut], int]:
        conditions = ["vault_id=?"]
        parameters: list[Any] = [self.vault_id()]
        if disposition is not None:
            conditions.append("disposition=?")
            parameters.append(disposition.value)
        if source_id is not None:
            conditions.append("source_id=?")
            parameters.append(source_id)
        where = " AND ".join(conditions)
        bounded_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM context_candidates WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT * FROM context_candidates WHERE {where} "
                "ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                [*parameters, bounded_limit, max(offset, 0)],
            ).fetchall()
        return [self._observation_out(row) for row in rows], total

    def get_observation(self, observation_id: str) -> ObservationOut:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (observation_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError("observation not found")
        return self._observation_out(row)

    def _candidate_out(self, row: sqlite3.Row) -> CandidateOut:
        return CandidateOut(
            id=str(row["id"]),
            session_id=cast(str | None, row["session_id"]),
            kind=str(row["kind"]),
            content=str(row["content"]),
            structured_value=_loads(row["structured_value_json"], None),
            entity_key=cast(str | None, row["entity_key"]),
            attribute_key=cast(str | None, row["attribute_key"]),
            scopes=_loads(row["scopes_json"], []),
            tags=_loads(row["tags_json"], []),
            source_id=cast(str | None, row["source_id"]),
            source_reference=cast(str | None, row["source_reference"]),
            source_service=cast(str | None, row["source_service"]),
            source_type=cast(str | None, row["source_type"]),
            evidence=cast(str | None, row["evidence"]),
            confidence=float(row["confidence"]),
            sensitivity=Sensitivity(str(row["sensitivity"])),
            availability=Availability(str(row["availability"])),
            allowed_clients=_loads(row["allowed_clients_json"], []),
            denied_clients=_loads(row["denied_clients_json"], []),
            observed_at=cast(str | None, row["observed_at"]),
            valid_from=cast(str | None, row["valid_from"]),
            expires_at=cast(str | None, row["expires_at"]),
            supersedes=cast(str | None, row["supersedes"]),
            explicit_user_statement=bool(row["explicit_user_statement"]),
            idempotency_key=cast(str | None, row["idempotency_key"]),
            approval_status=ApprovalStatus(str(row["approval_status"])),
            content_hash=str(row["content_hash"]),
            schema_version=int(row["schema_version"]),
            created_at=str(row["created_at"]),
            reviewed_at=cast(str | None, row["reviewed_at"]),
            review_reason=cast(str | None, row["review_reason"]),
            disposition=ObservationDisposition(str(row["disposition"])),
            record_id=cast(str | None, row["record_id"]),
            decision_reason=cast(str | None, row["decision_reason"]),
            decided_at=cast(str | None, row["decided_at"]),
            observation_origin=cast(str | None, row["observation_origin"]),
            policy_version=cast(str | None, row["policy_version"]),
        )

    def _observation_out(self, row: sqlite3.Row) -> ObservationOut:
        data = self._candidate_out(row).model_dump(
            mode="json",
            exclude={"approval_status", "reviewed_at", "review_reason"},
        )
        submitted_by = cast(str | None, row["submitted_by_client_id"])
        if submitted_by is None and str(row["source_type"] or "") == "queued_proposal":
            submitted_by = cast(str | None, row["source_service"])
        return ObservationOut(
            **data,
            submitted_by_client_id=submitted_by,
        )

    def _memory_policy_tx(self, connection: sqlite3.Connection) -> MemoryPolicy:
        row = connection.execute(
            "SELECT * FROM memory_policies WHERE vault_id=?", (self.vault_id(),)
        ).fetchone()
        if row is None:
            now = utc_now()
            connection.execute(
                "INSERT INTO memory_policies"
                "(vault_id,mode,sensitive_mode,inference_mode,policy_version,"
                "created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    self.vault_id(),
                    "automatic",
                    "local_only",
                    "corroborate",
                    AUTOMATIC_POLICY_VERSION,
                    now,
                    now,
                ),
            )
            return MemoryPolicy()
        return MemoryPolicy(
            mode=str(row["mode"]),
            sensitive_mode=str(row["sensitive_mode"]),
            inference_mode=str(row["inference_mode"]),
            policy_version=str(row["policy_version"]),
        )

    @staticmethod
    def _active_records_tx(connection: sqlite3.Connection, vault_id: str) -> list[sqlite3.Row]:
        return list(
            connection.execute(
                "SELECT r.* FROM context_records r "
                "WHERE r.vault_id=? AND r.approval_status='approved' AND r.deleted_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM context_records newer "
                "WHERE newer.supersedes=r.id AND newer.approval_status='approved' "
                "AND newer.deleted_at IS NULL)",
                (vault_id,),
            ).fetchall()
        )

    def _exact_record_tx(
        self,
        connection: sqlite3.Connection,
        observation: sqlite3.Row,
        principal: ClientPrincipal | None = None,
    ) -> sqlite3.Row | None:
        fingerprint = _value_fingerprint(observation)
        for record in self._active_records_tx(connection, str(observation["vault_id"])):
            if (
                str(record["kind"]) == str(observation["kind"])
                and record["entity_key"] == observation["entity_key"]
                and record["attribute_key"] == observation["attribute_key"]
                and _value_fingerprint(record) == fingerprint
                and self._record_is_allowed(record, principal)
            ):
                return record
        return None

    def _target_record_tx(
        self,
        connection: sqlite3.Connection,
        observation: sqlite3.Row,
        principal: ClientPrincipal | None = None,
        *,
        origin: ObservationOrigin | None = None,
    ) -> sqlite3.Row | None:
        supersedes = cast(str | None, observation["supersedes"])
        if supersedes is not None:
            record = cast(
                sqlite3.Row | None,
                connection.execute(
                    "SELECT * FROM context_records WHERE id=? AND deleted_at IS NULL",
                    (supersedes,),
                ).fetchone(),
            )
            return record if record is None or self._record_is_allowed(record, principal) else None
        entity_key = cast(str | None, observation["entity_key"])
        attribute_key = cast(str | None, observation["attribute_key"])
        if entity_key is not None and attribute_key is not None:
            rows = cast(
                list[sqlite3.Row],
                connection.execute(
                    "SELECT * FROM context_records WHERE vault_id=? AND entity_key=? "
                    "AND attribute_key=? AND approval_status='approved' AND deleted_at IS NULL "
                    "ORDER BY observed_at DESC,updated_at DESC,id",
                    (observation["vault_id"], entity_key, attribute_key),
                ).fetchall(),
            )
            return next(
                (record for record in rows if self._record_is_allowed(record, principal)),
                None,
            )
        # Unkeyed archive statements share a lineage only when they have the
        # same extracted subject. Kind-only collapse is not a slot: unrelated
        # goals, preferences, and projects remain independent current records.
        # Direct configured-client / local-admin unkeyed records stay independent.
        if origin != ObservationOrigin.ARCHIVE_IMPORT:
            return None
        kind = str(observation["kind"]).casefold()
        if kind not in UNKEYED_CONFLICT_KINDS:
            return None
        slot = archive_lineage_key(kind, str(observation["content"]))
        if slot is None:
            return None
        rows = cast(
            list[sqlite3.Row],
            connection.execute(
                "SELECT * FROM context_records WHERE vault_id=? AND lower(kind)=? "
                "AND approval_status='approved' AND deleted_at IS NULL "
                "AND observation_origin=? "
                "ORDER BY observed_at DESC,updated_at DESC,id",
                (observation["vault_id"], kind, ObservationOrigin.ARCHIVE_IMPORT.value),
            ).fetchall(),
        )
        return next(
            (
                record
                for record in rows
                if self._record_is_allowed(record, principal)
                and archive_lineage_key(str(record["kind"]), str(record["content"])) == slot
            ),
            None,
        )

    def _principal_for_client_id_tx(
        self,
        connection: sqlite3.Connection,
        client_id: str | None,
    ) -> ClientPrincipal | None:
        if client_id is None:
            return None
        row = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT id,name,scopes_json,auto_approve FROM client_registrations "
                "WHERE id=? AND revoked_at IS NULL",
                (client_id,),
            ).fetchone(),
        )
        if row is None:
            return ClientPrincipal(client_id, "Stored client", frozenset())
        return ClientPrincipal(
            id=str(row["id"]),
            name=str(row["name"]),
            scopes=frozenset(cast(list[str], _loads(row["scopes_json"], []))),
            auto_approve=bool(row["auto_approve"]),
        )

    def _policy_principal_tx(
        self,
        connection: sqlite3.Connection,
        principal: ClientPrincipal | None,
    ) -> ClientPrincipal | None:
        """Re-bind policy authority from durable registration state.

        Callers may pass a ClientPrincipal object; its scopes are never trusted
        for witness evaluation. ``None`` remains the Core importer / local-admin
        path. Unknown or revoked IDs resolve to an empty-scope principal so
        forged or stale shape cannot manufacture witness authority.
        """
        if principal is None:
            return None
        return self._principal_for_client_id_tx(connection, principal.id)

    @staticmethod
    def _record_is_allowed(
        record: sqlite3.Row,
        principal: ClientPrincipal | None,
    ) -> bool:
        return record_is_allowed(
            principal,
            set(_loads(record["scopes_json"], [])),
            set(_loads(record["allowed_clients_json"], [])),
            set(_loads(record["denied_clients_json"], [])),
        )

    @staticmethod
    def _explicit_target_is_denied(
        connection: sqlite3.Connection,
        observation: sqlite3.Row,
        principal: ClientPrincipal | None,
    ) -> bool:
        if principal is None or observation["supersedes"] is None:
            return False
        record = cast(
            sqlite3.Row | None,
            connection.execute(
                "SELECT * FROM context_records WHERE id=? AND deleted_at IS NULL",
                (observation["supersedes"],),
            ).fetchone(),
        )
        return record is not None and not CoreStore._record_is_allowed(record, principal)

    @staticmethod
    def _observation_wins(observation: sqlite3.Row, record: sqlite3.Row) -> bool:
        if str(observation["kind"]).casefold() == "correction":
            return True
        proposed_explicit = bool(observation["explicit_user_statement"])
        current_explicit = bool(record["explicit_user_statement"])
        if proposed_explicit != current_explicit:
            return proposed_explicit
        proposed_at = str(observation["observed_at"] or observation["created_at"])
        current_at = str(record["observed_at"] or record["created_at"])
        return proposed_at >= current_at

    @staticmethod
    def _link_observation_tx(
        connection: sqlite3.Connection,
        observation_id: str,
        record_id: str,
        relationship: str,
    ) -> None:
        connection.execute(
            "INSERT INTO context_observation_links"
            "(observation_id,record_id,relationship,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(observation_id,record_id) DO UPDATE SET "
            "relationship=excluded.relationship",
            (observation_id, record_id, relationship, utc_now()),
        )

    @staticmethod
    def _set_observation_decision_tx(
        connection: sqlite3.Connection,
        observation_id: str,
        *,
        disposition: ObservationDisposition,
        reason: str,
        policy_version: str,
        origin: ObservationOrigin,
        record_id: str | None = None,
        actor: str,
    ) -> None:
        decided_at = utc_now()
        approval_status = {
            ObservationDisposition.APPLIED: ApprovalStatus.APPROVED,
            ObservationDisposition.REINFORCED: ApprovalStatus.APPROVED,
            ObservationDisposition.IGNORED: ApprovalStatus.REJECTED,
            ObservationDisposition.TENTATIVE: ApprovalStatus.PENDING,
            ObservationDisposition.STAGED: ApprovalStatus.PENDING,
        }[disposition]
        connection.execute(
            "UPDATE context_candidates SET approval_status=?,disposition=?,record_id=?,"
            "decision_reason=?,decided_at=?,policy_version=?,observation_origin=?,"
            "reviewed_at=?,reviewed_by=?,review_reason=? WHERE id=?",
            (
                approval_status.value,
                disposition.value,
                record_id,
                reason,
                decided_at,
                policy_version,
                origin.value,
                decided_at,
                actor,
                reason,
                observation_id,
            ),
        )

    def _tentative_matches_tx(
        self, connection: sqlite3.Connection, observation: sqlite3.Row
    ) -> list[sqlite3.Row]:
        rows = connection.execute(
            "SELECT * FROM context_candidates WHERE vault_id=? AND kind=? "
            "AND disposition='tentative' AND id<>? ORDER BY created_at,id",
            (observation["vault_id"], observation["kind"], observation["id"]),
        ).fetchall()
        target = normalized_observation_text(str(observation["content"]))
        return [
            row
            for row in rows
            if row["entity_key"] == observation["entity_key"]
            and row["attribute_key"] == observation["attribute_key"]
            and normalized_observation_text(str(row["content"])) == target
        ]

    def _reapply_deleted_record_from_observation_tx(
        self,
        connection: sqlite3.Connection,
        observation: sqlite3.Row,
        *,
        availability: Availability,
        policy_version: str,
        origin: ObservationOrigin,
        reason: str,
        actor: str,
    ) -> sqlite3.Row | None:
        """Reopen an untouched archive record while preserving its stable ID.

        Source rebuilds soft-delete automatic records before publishing their
        replacement observations. Reusing only a deleted record with the same
        source address, and only when it has no user edit, keeps record identity
        stable without allowing parser output to overwrite a user's work.
        """

        record_key = cast(str | None, observation["record_key"])
        if origin != ObservationOrigin.ARCHIVE_IMPORT or record_key is None:
            return None
        prior = connection.execute(
            "SELECT r.*,t.deleted_version,t.reason AS tombstone_reason,"
            "t.content_hash AS tombstone_hash,t.deleted_at AS tombstone_deleted_at "
            "FROM context_records r JOIN deletion_tombstones t ON t.record_id=r.id "
            "JOIN source_records s ON s.id=t.deletion_source_id "
            "WHERE r.vault_id=? AND r.record_key=? AND r.deleted_at IS NOT NULL "
            "AND r.source_id=? AND r.observation_origin=? "
            "AND s.id=? AND s.deleted_at IS NULL AND s.import_status IS NOT NULL "
            "AND t.deletion_origin='source_rebuild' AND t.deletion_source_id=? "
            "AND t.reason=? ORDER BY r.version DESC,r.id LIMIT 1",
            (
                observation["vault_id"],
                record_key,
                observation["source_id"],
                ObservationOrigin.ARCHIVE_IMPORT.value,
                observation["source_id"],
                observation["source_id"],
                SOURCE_REBUILD_REASON,
            ),
        ).fetchone()
        if (
            prior is None
            or self._record_has_user_edit_tx(connection, str(prior["id"]))
            or str(prior["record_key"]) != record_key
            or int(prior["deleted_version"]) != int(prior["version"])
            or _hash_text(
                f"{prior['id']}:{prior['deleted_version']}:{prior['tombstone_deleted_at']}"
            )
            != str(prior["tombstone_hash"])
            or _stable_record_key_from_row(prior) != record_key
            or _stable_record_key_from_row(observation) != record_key
        ):
            return None

        record_id = str(prior["id"])
        now = utc_now()
        version = int(prior["version"]) + 1
        connection.execute(
            "UPDATE context_records SET candidate_id=?,source_id=?,source_reference=?,kind=?,"
            "content=?,structured_value_json=?,entity_key=?,attribute_key=?,scopes_json=?,"
            "tags_json=?,source_service=?,source_type=?,evidence=?,confidence=?,sensitivity=?,"
            "availability=?,allowed_clients_json=?,denied_clients_json=?,valid_from=?,"
            "expires_at=?,supersedes=?,explicit_user_statement=?,approval_status=?,version=?,"
            "content_hash=?,schema_version=?,updated_at=?,deleted_at=NULL,observed_at=?,"
            "observation_origin=?,policy_version=? WHERE id=?",
            (
                observation["id"],
                observation["source_id"],
                observation["source_reference"],
                observation["kind"],
                observation["content"],
                observation["structured_value_json"],
                observation["entity_key"],
                observation["attribute_key"],
                observation["scopes_json"],
                observation["tags_json"],
                observation["source_service"],
                observation["source_type"],
                observation["evidence"],
                observation["confidence"],
                observation["sensitivity"],
                availability.value,
                observation["allowed_clients_json"],
                observation["denied_clients_json"],
                observation["valid_from"],
                observation["expires_at"],
                observation["supersedes"],
                observation["explicit_user_statement"],
                ApprovalStatus.APPROVED.value,
                version,
                _hash_text(str(observation["content"])),
                observation["schema_version"],
                now,
                observation["observed_at"] or observation["created_at"],
                origin.value,
                policy_version,
                record_id,
            ),
        )
        connection.execute(
            "UPDATE context_records SET record_key=? WHERE id=?",
            (record_key, record_id),
        )
        connection.execute("DELETE FROM deletion_tombstones WHERE record_id=?", (record_id,))
        restored = connection.execute(
            "SELECT * FROM context_records WHERE id=?", (record_id,)
        ).fetchone()
        assert restored is not None
        self._set_observation_decision_tx(
            connection,
            str(observation["id"]),
            disposition=ObservationDisposition.APPLIED,
            reason=reason,
            policy_version=policy_version,
            origin=origin,
            record_id=record_id,
            actor=actor,
        )
        self._link_observation_tx(connection, str(observation["id"]), record_id, "reapplied")
        self._insert_version(connection, restored, reason)
        self._replace_fts(connection, restored)
        if availability == Availability.ALWAYS:
            self._emit_event(connection, restored, "record_upserted", self._relay_payload(restored))
        self._audit(connection, actor, "observation_reapplied", [record_id])
        return cast(sqlite3.Row, restored)

    @staticmethod
    def _ordinary_deletion_for_observation_tx(
        connection: sqlite3.Connection, observation: sqlite3.Row
    ) -> sqlite3.Row | None:
        """Find an explicit deletion that blocks automatic resurrection."""

        record_key = cast(str | None, observation["record_key"])
        source_id = cast(str | None, observation["source_id"])
        if record_key is None or source_id is None:
            return None
        return cast(sqlite3.Row, connection.execute(
            "SELECT r.id,t.reason FROM context_records r "
            "JOIN deletion_tombstones t ON t.record_id=r.id "
            "WHERE r.vault_id=? AND r.record_key=? AND r.source_id=? "
            "AND r.deleted_at IS NOT NULL AND t.deletion_origin='ordinary' "
            "ORDER BY t.deleted_at DESC,r.id LIMIT 1",
            (observation["vault_id"], record_key, source_id),
        ).fetchone())

    def _create_record_from_observation_tx(
        self,
        connection: sqlite3.Connection,
        observation: sqlite3.Row,
        *,
        availability: Availability,
        policy_version: str,
        origin: ObservationOrigin,
        reason: str,
        actor: str,
    ) -> sqlite3.Row:
        reused = self._reapply_deleted_record_from_observation_tx(
            connection,
            observation,
            availability=availability,
            policy_version=policy_version,
            origin=origin,
            reason=reason,
            actor=actor,
        )
        if reused is not None:
            return reused

        record_id = new_id()
        now = utc_now()
        values = (
            record_id,
            observation["vault_id"],
            observation["id"],
            observation["record_key"],
            observation["source_id"],
            observation["source_reference"],
            observation["kind"],
            observation["content"],
            observation["structured_value_json"],
            observation["entity_key"],
            observation["attribute_key"],
            observation["scopes_json"],
            observation["tags_json"],
            observation["source_service"],
            observation["source_type"],
            observation["evidence"],
            observation["confidence"],
            observation["sensitivity"],
            availability.value,
            observation["allowed_clients_json"],
            observation["denied_clients_json"],
            observation["valid_from"],
            observation["expires_at"],
            observation["supersedes"],
            observation["explicit_user_statement"],
            ApprovalStatus.APPROVED.value,
            1,
            _hash_text(str(observation["content"])),
            observation["schema_version"],
            now,
            now,
            observation["observed_at"] or observation["created_at"],
            origin.value,
            policy_version,
        )
        connection.execute(
            "INSERT INTO context_records"
            "(id,vault_id,candidate_id,record_key,source_id,source_reference,kind,content,"
            "structured_value_json,entity_key,attribute_key,scopes_json,tags_json,"
            "source_service,source_type,evidence,confidence,sensitivity,availability,"
            "allowed_clients_json,denied_clients_json,valid_from,expires_at,supersedes,"
            "explicit_user_statement,approval_status,version,content_hash,schema_version,"
            "created_at,updated_at,observed_at,observation_origin,policy_version) VALUES("
            + ",".join("?" * 34)
            + ")",
            values,
        )
        record = connection.execute(
            "SELECT * FROM context_records WHERE id=?", (record_id,)
        ).fetchone()
        assert record is not None
        self._set_observation_decision_tx(
            connection,
            str(observation["id"]),
            disposition=ObservationDisposition.APPLIED,
            reason=reason,
            policy_version=policy_version,
            origin=origin,
            record_id=record_id,
            actor=actor,
        )
        self._link_observation_tx(connection, str(observation["id"]), record_id, "applied")
        self._insert_version(connection, record, reason)
        self._replace_fts(connection, record)
        if availability == Availability.ALWAYS:
            self._emit_event(connection, record, "record_upserted", self._relay_payload(record))
        self._audit(connection, actor, "observation_applied", [record_id])
        return cast(sqlite3.Row, record)

    def _update_record_from_observation_tx(
        self,
        connection: sqlite3.Connection,
        observation: sqlite3.Row,
        record: sqlite3.Row,
        *,
        availability: Availability,
        policy_version: str,
        origin: ObservationOrigin,
        reason: str,
        actor: str,
    ) -> sqlite3.Row:
        record_id = str(record["id"])
        previous_availability = Availability(str(record["availability"]))
        version = int(record["version"]) + 1
        now = utc_now()
        is_correction = str(observation["kind"]).casefold() == "correction"

        def observed_or_existing(column: str) -> Any:
            return record[column] if is_correction else observation[column]

        structured_value = observation["structured_value_json"]
        if is_correction and structured_value is None:
            structured_value = record["structured_value_json"]
        confidence = (
            max(float(record["confidence"]), float(observation["confidence"]))
            if is_correction
            else observation["confidence"]
        )
        (
            effective_sensitivity,
            effective_availability,
            effective_allowed,
            effective_denied,
        ) = _monotonic_security(
            record,
            observation,
            availability,
        )
        source_id = cast(str | None, observed_or_existing("source_id"))
        source_reference = cast(str | None, observed_or_existing("source_reference"))
        kind = str(observed_or_existing("kind"))
        entity_key = cast(str | None, observed_or_existing("entity_key"))
        attribute_key = cast(str | None, observed_or_existing("attribute_key"))
        record_key = _stable_record_key_from_values(
            source_id=source_id,
            source_reference=source_reference,
            kind=kind,
            entity_key=entity_key,
            attribute_key=attribute_key,
            content=str(observation["content"]),
            structured_value=_loads(cast(str | None, structured_value), None),
        )
        connection.execute(
            "UPDATE context_records SET source_id=?,source_reference=?,kind=?,content=?,"
            "structured_value_json=?,entity_key=?,attribute_key=?,scopes_json=?,tags_json=?,"
            "source_service=?,source_type=?,evidence=?,confidence=?,sensitivity=?,"
            "availability=?,allowed_clients_json=?,denied_clients_json=?,valid_from=?,"
            "expires_at=?,explicit_user_statement=?,record_key=?,content_hash=?,version=?,updated_at=?,"
            "observed_at=?,observation_origin=?,policy_version=? WHERE id=?",
            (
                source_id,
                source_reference,
                kind,
                observation["content"],
                structured_value,
                observed_or_existing("entity_key"),
                observed_or_existing("attribute_key"),
                observed_or_existing("scopes_json"),
                observed_or_existing("tags_json"),
                observed_or_existing("source_service"),
                observed_or_existing("source_type"),
                observed_or_existing("evidence"),
                confidence,
                effective_sensitivity.value,
                effective_availability.value,
                _json(sorted(effective_allowed)),
                _json(sorted(effective_denied)),
                observed_or_existing("valid_from"),
                observed_or_existing("expires_at"),
                int(bool(record["explicit_user_statement"]) or is_correction)
                if is_correction
                else observation["explicit_user_statement"],
                record_key,
                _hash_text(str(observation["content"])),
                version,
                now,
                observation["observed_at"] or observation["created_at"],
                origin.value,
                policy_version,
                record_id,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM context_records WHERE id=?", (record_id,)
        ).fetchone()
        assert updated is not None
        self._set_observation_decision_tx(
            connection,
            str(observation["id"]),
            disposition=ObservationDisposition.APPLIED,
            reason=reason,
            policy_version=policy_version,
            origin=origin,
            record_id=record_id,
            actor=actor,
        )
        self._link_observation_tx(connection, str(observation["id"]), record_id, "updated")
        self._insert_version(connection, updated, reason)
        self._replace_fts(connection, updated)
        if effective_availability == Availability.ALWAYS:
            self._emit_event(connection, updated, "record_upserted", self._relay_payload(updated))
        elif previous_availability == Availability.ALWAYS:
            self._emit_event(connection, updated, "record_withdrawn", {"record_id": record_id})
        self._audit(connection, actor, "observation_updated", [record_id])
        return cast(sqlite3.Row, updated)

    def _reinforce_record_tx(
        self,
        connection: sqlite3.Connection,
        observation: sqlite3.Row,
        record: sqlite3.Row,
        *,
        availability: Availability,
        policy_version: str,
        origin: ObservationOrigin,
        reason: str,
        actor: str,
    ) -> sqlite3.Row:
        record_id = str(record["id"])
        now = utc_now()
        previous_availability = Availability(str(record["availability"]))
        (
            target_sensitivity,
            target_availability,
            target_allowed,
            target_denied,
        ) = _monotonic_security(
            record,
            observation,
            availability,
        )
        current_allowed = set(_loads(record["allowed_clients_json"], []))
        current_denied = set(_loads(record["denied_clients_json"], []))
        security_changed = (
            target_sensitivity != Sensitivity(str(record["sensitivity"]))
            or target_availability != previous_availability
            or target_allowed != current_allowed
            or target_denied != current_denied
        )
        observed_at = max(
            str(record["observed_at"] or record["created_at"]),
            str(observation["observed_at"] or observation["created_at"]),
        )
        connection.execute(
            "UPDATE context_records SET confidence=MAX(confidence,?),sensitivity=?,"
            "availability=?,allowed_clients_json=?,denied_clients_json=?,observed_at=?,"
            "updated_at=?,policy_version=?,version=? WHERE id=?",
            (
                observation["confidence"],
                target_sensitivity.value,
                target_availability.value,
                _json(sorted(target_allowed)),
                _json(sorted(target_denied)),
                observed_at,
                now,
                policy_version,
                int(record["version"]) + int(security_changed),
                record_id,
            ),
        )
        updated = connection.execute(
            "SELECT * FROM context_records WHERE id=?", (record_id,)
        ).fetchone()
        assert updated is not None
        if security_changed:
            self._insert_version(connection, updated, reason)
        self._set_observation_decision_tx(
            connection,
            str(observation["id"]),
            disposition=ObservationDisposition.REINFORCED,
            reason=reason,
            policy_version=policy_version,
            origin=origin,
            record_id=record_id,
            actor=actor,
        )
        self._link_observation_tx(connection, str(observation["id"]), record_id, "reinforced")
        if target_availability == Availability.ALWAYS:
            self._emit_event(connection, updated, "record_upserted", self._relay_payload(updated))
        elif previous_availability == Availability.ALWAYS:
            self._emit_event(connection, updated, "record_withdrawn", {"record_id": record_id})
        self._audit(connection, actor, "observation_reinforced", [record_id])
        return cast(sqlite3.Row, updated)

    def _evaluate_observation_tx(
        self,
        connection: sqlite3.Connection,
        observation_id: str,
        *,
        origin: ObservationOrigin,
        actor: str,
        principal: ClientPrincipal | None = None,
    ) -> CandidateOut:
        observation = connection.execute(
            "SELECT * FROM context_candidates WHERE id=?", (observation_id,)
        ).fetchone()
        if observation is None:
            raise NotFoundError("observation not found")
        if str(observation["disposition"]) != ObservationDisposition.STAGED.value:
            return self._candidate_out(observation)

        policy = self._memory_policy_tx(connection)
        if self._explicit_target_is_denied(connection, observation, principal):
            self._set_observation_decision_tx(
                connection,
                observation_id,
                disposition=ObservationDisposition.IGNORED,
                reason="target is not current context",
                policy_version=policy.policy_version,
                origin=origin,
                actor=actor,
            )
            self._audit(connection, actor, "observation_ignored", [])
            updated = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (observation_id,)
            ).fetchone()
            assert updated is not None
            return self._candidate_out(updated)
        decision = AutomaticMemoryPolicy(policy).evaluate(
            self._candidate_out(observation),
            origin=origin,
            principal=principal,
        )
        if str(observation["sensitivity"]) != decision.sensitivity.value:
            connection.execute(
                "UPDATE context_candidates SET sensitivity=? WHERE id=?",
                (decision.sensitivity.value, observation_id),
            )
            observation = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (observation_id,)
            ).fetchone()
            assert observation is not None
        if str(observation["kind"]).casefold() == "context_forget":
            target = self._target_record_tx(connection, observation, principal, origin=origin)
            if decision.disposition != ObservationDisposition.APPLIED or target is None:
                self._set_observation_decision_tx(
                    connection,
                    observation_id,
                    disposition=(
                        decision.disposition
                        if decision.disposition
                        in {
                            ObservationDisposition.IGNORED,
                            ObservationDisposition.TENTATIVE,
                        }
                        else ObservationDisposition.IGNORED
                    ),
                    reason=(
                        decision.reason
                        if decision.disposition != ObservationDisposition.APPLIED
                        else "forget target is not current context"
                    ),
                    policy_version=policy.policy_version,
                    origin=origin,
                    actor=actor,
                )
                self._audit(connection, actor, "observation_ignored", [])
            else:
                record_id = str(target["id"])
                self._set_observation_decision_tx(
                    connection,
                    observation_id,
                    disposition=ObservationDisposition.APPLIED,
                    reason=decision.reason,
                    policy_version=policy.policy_version,
                    origin=origin,
                    record_id=record_id,
                    actor=actor,
                )
                self._link_observation_tx(connection, observation_id, record_id, "forgotten")
                self._delete_record_tx(
                    connection,
                    record_id,
                    reason="Explicit user forget request",
                    actor=actor,
                )
        elif decision.disposition == ObservationDisposition.IGNORED:
            self._set_observation_decision_tx(
                connection,
                observation_id,
                disposition=ObservationDisposition.IGNORED,
                reason=decision.reason,
                policy_version=policy.policy_version,
                origin=origin,
                actor=actor,
            )
            self._audit(connection, actor, "observation_ignored", [])
        elif (
            origin == ObservationOrigin.ARCHIVE_IMPORT
            and decision.disposition == ObservationDisposition.APPLIED
            and (
                blocked := self._ordinary_deletion_for_observation_tx(
                    connection, observation
                )
            )
            is not None
        ):
            blocked_id = str(blocked["id"])
            reason = (
                "matching archive evidence was blocked by an explicit deletion; "
                "restore the deleted record before it can become current"
            )
            self._set_observation_decision_tx(
                connection,
                observation_id,
                disposition=ObservationDisposition.IGNORED,
                reason=reason,
                policy_version=policy.policy_version,
                origin=origin,
                record_id=blocked_id,
                actor=actor,
            )
            self._link_observation_tx(connection, observation_id, blocked_id, "blocked_by_deletion")
            self._audit(connection, actor, "observation_ignored", [blocked_id])
        else:
            exact = self._exact_record_tx(connection, observation, principal)
            if exact is not None:
                self._reinforce_record_tx(
                    connection,
                    observation,
                    exact,
                    availability=decision.availability,
                    policy_version=policy.policy_version,
                    origin=origin,
                    reason="observation reinforced matching current context",
                    actor=actor,
                )
            else:
                corroborating = (
                    self._tentative_matches_tx(connection, observation)
                    if decision.disposition == ObservationDisposition.APPLIED
                    and policy.inference_mode == "corroborate"
                    and str(observation["kind"]).casefold() != "context_error"
                    and not (
                        origin == ObservationOrigin.ARCHIVE_IMPORT
                        and str(observation["source_type"] or "") != "provider_archive"
                    )
                    else []
                )
                if decision.disposition == ObservationDisposition.TENTATIVE:
                    self._set_observation_decision_tx(
                        connection,
                        observation_id,
                        disposition=ObservationDisposition.TENTATIVE,
                        reason=decision.reason,
                        policy_version=policy.policy_version,
                        origin=origin,
                        actor=actor,
                    )
                    self._audit(connection, actor, "observation_tentative", [])
                else:
                    target = self._target_record_tx(
                        connection, observation, principal, origin=origin
                    )
                    if target is not None and not self._observation_wins(observation, target):
                        reason = (
                            "older or lower-authority observation did not replace current context"
                        )
                        self._set_observation_decision_tx(
                            connection,
                            observation_id,
                            disposition=ObservationDisposition.IGNORED,
                            reason=reason,
                            policy_version=policy.policy_version,
                            origin=origin,
                            record_id=str(target["id"]),
                            actor=actor,
                        )
                        self._link_observation_tx(
                            connection,
                            observation_id,
                            str(target["id"]),
                            "contradicted",
                        )
                        self._audit(connection, actor, "observation_ignored", [str(target["id"])])
                    elif observation["supersedes"] is not None and target is None:
                        self._set_observation_decision_tx(
                            connection,
                            observation_id,
                            disposition=ObservationDisposition.IGNORED,
                            reason="correction target is not current context",
                            policy_version=policy.policy_version,
                            origin=origin,
                            actor=actor,
                        )
                        self._audit(connection, actor, "observation_ignored", [])
                    else:
                        applied_reason = (
                            "explicit observation corroborated tentative evidence"
                            if corroborating
                            else decision.reason
                        )
                        current = (
                            self._update_record_from_observation_tx(
                                connection,
                                observation,
                                target,
                                availability=decision.availability,
                                policy_version=policy.policy_version,
                                origin=origin,
                                reason=applied_reason,
                                actor=actor,
                            )
                            if target is not None
                            else self._create_record_from_observation_tx(
                                connection,
                                observation,
                                availability=decision.availability,
                                policy_version=policy.policy_version,
                                origin=origin,
                                reason=applied_reason,
                                actor=actor,
                            )
                        )
                        for prior in corroborating:
                            self._set_observation_decision_tx(
                                connection,
                                str(prior["id"]),
                                disposition=ObservationDisposition.REINFORCED,
                                reason="observation helped corroborate current context",
                                policy_version=policy.policy_version,
                                origin=ObservationOrigin(str(prior["observation_origin"]))
                                if prior["observation_origin"]
                                else origin,
                                record_id=str(current["id"]),
                                actor=actor,
                            )
                            self._link_observation_tx(
                                connection,
                                str(prior["id"]),
                                str(current["id"]),
                                "corroborated",
                            )
        self._recompute_integrity(connection)
        updated = connection.execute(
            "SELECT * FROM context_candidates WHERE id=?", (observation_id,)
        ).fetchone()
        assert updated is not None
        return self._candidate_out(updated)

    def evaluate_staged_observations(self, *, limit: int = 10_000) -> int:
        """Apply eligible legacy/finished-session observations after an upgrade."""

        evaluated = 0
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT c.id,c.session_id,c.source_service,c.source_type,c.kind,"
                "c.submitted_by_client_id,"
                "s.mode,s.status FROM context_candidates c "
                "LEFT JOIN ingestion_sessions s ON s.id=c.session_id "
                "WHERE c.disposition='staged' AND (c.session_id IS NULL OR s.status='finished') "
                "ORDER BY c.created_at,c.id LIMIT ?",
                (min(max(limit, 1), 100_000),),
            ).fetchall()
            for row in rows:
                if str(row["source_type"] or "") == "queued_proposal":
                    origin = ObservationOrigin.RELAY_QUEUE
                elif str(row["mode"] or "") == IngestionMode.ARCHIVE.value:
                    origin = ObservationOrigin.ARCHIVE_IMPORT
                elif str(row["kind"]).casefold() == "correction":
                    origin = ObservationOrigin.CONTEXT_ERROR
                elif row["submitted_by_client_id"] is not None:
                    origin = ObservationOrigin.ONGOING_CLIENT
                else:
                    origin = ObservationOrigin.LEGACY_MIGRATION
                submitted_by = cast(str | None, row["submitted_by_client_id"])
                effective_client_id = (
                    cast(str | None, row["source_service"])
                    if origin == ObservationOrigin.RELAY_QUEUE
                    else submitted_by
                )
                principal = self._principal_for_client_id_tx(connection, effective_client_id)
                self._evaluate_observation_tx(
                    connection,
                    str(row["id"]),
                    origin=origin,
                    actor=effective_client_id or "automatic-migration",
                    principal=principal,
                )
                evaluated += 1
        return evaluated

    def approve_candidate(
        self,
        candidate_id: str,
        request: ApprovalRequest | None = None,
        *,
        actor: str = "local-user",
    ) -> ContextRecordOut:
        request = request or ApprovalRequest()
        if contains_secret_like_value(request.model_dump(mode="json")):
            raise InvalidStateError("direct secret-like approval content was refused")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("candidate not found")
            if str(row["approval_status"]) == ApprovalStatus.APPROVED.value:
                linked_record_id = cast(str | None, row["record_id"])
                record = (
                    connection.execute(
                        "SELECT * FROM context_records WHERE id=?", (linked_record_id,)
                    ).fetchone()
                    if linked_record_id is not None
                    else connection.execute(
                        "SELECT * FROM context_records WHERE candidate_id=?", (candidate_id,)
                    ).fetchone()
                )
                if record is None:
                    raise InvalidStateError("approved candidate has no canonical record")
                self._link_observation_tx(connection, candidate_id, str(record["id"]), "applied")
                return self._record_out(record)
            if str(row["approval_status"]) != ApprovalStatus.PENDING.value:
                raise InvalidStateError("only pending candidates may be approved")
            availability = request.availability or Availability(str(row["availability"]))
            sensitivity = request.sensitivity or Sensitivity(str(row["sensitivity"]))
            if (
                availability == Availability.ALWAYS
                and sensitivity != Sensitivity.NORMAL
                and not request.explicit_sensitive_replication
            ):
                raise InvalidStateError(
                    "sensitive context requires explicit confirmation before replication"
                )
            record_id = new_id()
            now = utc_now()
            content = request.content or str(row["content"])
            allowed_clients = (
                request.allowed_clients
                if request.allowed_clients is not None
                else _loads(row["allowed_clients_json"], [])
            )
            denied_clients = (
                request.denied_clients
                if request.denied_clients is not None
                else _loads(row["denied_clients_json"], [])
            )
            record_values = (
                record_id,
                row["vault_id"],
                candidate_id,
                row["record_key"],
                row["source_id"],
                row["source_reference"],
                row["kind"],
                content,
                row["structured_value_json"],
                _normalized_slot_key(request.entity_key)
                if request.entity_key is not None
                else row["entity_key"],
                _normalized_slot_key(request.attribute_key)
                if request.attribute_key is not None
                else row["attribute_key"],
                row["scopes_json"],
                row["tags_json"],
                row["source_service"],
                row["source_type"],
                row["evidence"],
                row["confidence"],
                sensitivity.value,
                availability.value,
                _json(allowed_clients),
                _json(denied_clients),
                row["valid_from"],
                row["expires_at"],
                row["supersedes"],
                row["explicit_user_statement"],
                ApprovalStatus.APPROVED.value,
                1,
                _hash_text(content),
                row["schema_version"],
                now,
                now,
            )
            connection.execute(
                "INSERT INTO context_records"
                "(id,vault_id,candidate_id,record_key,source_id,source_reference,kind,content,structured_value_json,"
                "entity_key,attribute_key,scopes_json,tags_json,source_service,source_type,evidence,confidence,sensitivity,"
                "availability,allowed_clients_json,denied_clients_json,valid_from,expires_at,"
                "supersedes,explicit_user_statement,approval_status,version,content_hash,"
                "schema_version,created_at,updated_at) VALUES(" + ",".join("?" * 31) + ")",
                record_values,
            )
            connection.execute(
                "UPDATE context_records SET observed_at=?,observation_origin=?,policy_version=? "
                "WHERE id=?",
                (
                    row["observed_at"] or row["created_at"],
                    ObservationOrigin.LOCAL_ADMIN.value,
                    AUTOMATIC_POLICY_VERSION,
                    record_id,
                ),
            )
            connection.execute(
                "UPDATE context_candidates SET approval_status=?,reviewed_at=?,reviewed_by=?,"
                "review_reason=?,disposition='applied',record_id=?,decision_reason=?,"
                "decided_at=?,policy_version=?,observation_origin=? WHERE id=?",
                (
                    ApprovalStatus.APPROVED.value,
                    now,
                    actor,
                    request.reason,
                    record_id,
                    request.reason or "manually applied through compatibility endpoint",
                    now,
                    AUTOMATIC_POLICY_VERSION,
                    ObservationOrigin.LOCAL_ADMIN.value,
                    candidate_id,
                ),
            )
            record = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record_id,)
            ).fetchone()
            assert record is not None
            self._link_observation_tx(connection, candidate_id, record_id, "applied")
            self._insert_version(connection, record, request.reason or "candidate approved")
            self._replace_fts(connection, record)
            supersedes = cast(str | None, row["supersedes"])
            if supersedes:
                old = connection.execute(
                    "SELECT * FROM context_records WHERE id=? AND deleted_at IS NULL", (supersedes,)
                ).fetchone()
                if old is not None and str(old["availability"]) == Availability.ALWAYS.value:
                    self._emit_event(connection, old, "record_withdrawn", {"record_id": supersedes})
            if availability == Availability.ALWAYS:
                self._emit_event(connection, record, "record_upserted", self._relay_payload(record))
            self._recompute_integrity(connection)
            self._audit(connection, actor, "candidate_approved", [record_id])
            return self._record_out(record)

    def reject_candidate(
        self, candidate_id: str, *, reason: str | None = None, actor: str = "local-user"
    ) -> CandidateOut:
        now = utc_now()
        safe_reason = None if reason is None else redact_secret_reason(reason)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("candidate not found")
            if str(row["approval_status"]) != ApprovalStatus.PENDING.value:
                raise InvalidStateError("only pending candidates may be rejected")
            connection.execute(
                "UPDATE context_candidates SET approval_status=?,reviewed_at=?,reviewed_by=?,"
                "review_reason=?,disposition='ignored',decision_reason=?,decided_at=?,"
                "policy_version=?,observation_origin=? WHERE id=?",
                (
                    ApprovalStatus.REJECTED.value,
                    now,
                    actor,
                    safe_reason,
                    safe_reason or "manually ignored through compatibility endpoint",
                    now,
                    AUTOMATIC_POLICY_VERSION,
                    ObservationOrigin.LOCAL_ADMIN.value,
                    candidate_id,
                ),
            )
            self._recompute_integrity(connection)
            self._audit(connection, actor, "candidate_rejected", [])
            updated = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            assert updated is not None
            return self._candidate_out(updated)

    def get_record(self, record_id: str, *, include_deleted: bool = False) -> ContextRecordOut:
        query = "SELECT * FROM context_records WHERE id=?"
        if not include_deleted:
            query += " AND deleted_at IS NULL"
        with self.connect() as connection:
            row = connection.execute(query, (record_id,)).fetchone()
        if row is None:
            raise NotFoundError("context record not found")
        return self._record_out(row)

    def _record_out(self, row: sqlite3.Row) -> ContextRecordOut:
        keys = set(row.keys())
        raw_status = row["truth_status"] if "truth_status" in keys else None
        status = (
            MemoryTruthStatus(str(raw_status))
            if raw_status is not None
            else (
                MemoryTruthStatus.DELETED
                if row["deleted_at"] is not None
                else MemoryTruthStatus.CURRENT
            )
        )
        return ContextRecordOut(
            id=str(row["id"]),
            kind=str(row["kind"]),
            content=str(row["content"]),
            structured_value=_loads(row["structured_value_json"], None),
            entity_key=cast(str | None, row["entity_key"]),
            attribute_key=cast(str | None, row["attribute_key"]),
            scopes=_loads(row["scopes_json"], []),
            tags=_loads(row["tags_json"], []),
            source_id=cast(str | None, row["source_id"]),
            source_reference=cast(str | None, row["source_reference"]),
            source_service=cast(str | None, row["source_service"]),
            source_type=cast(str | None, row["source_type"]),
            evidence=cast(str | None, row["evidence"]),
            confidence=float(row["confidence"]),
            sensitivity=Sensitivity(str(row["sensitivity"])),
            availability=Availability(str(row["availability"])),
            allowed_clients=_loads(row["allowed_clients_json"], []),
            denied_clients=_loads(row["denied_clients_json"], []),
            observed_at=cast(str | None, row["observed_at"]),
            valid_from=cast(str | None, row["valid_from"]),
            expires_at=cast(str | None, row["expires_at"]),
            supersedes=cast(str | None, row["supersedes"]),
            explicit_user_statement=bool(row["explicit_user_statement"]),
            version=int(row["version"]),
            content_hash=str(row["content_hash"]),
            schema_version=int(row["schema_version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            deleted_at=cast(str | None, row["deleted_at"]),
            status=status,
            observation_origin=cast(str | None, row["observation_origin"]),
            policy_version=cast(str | None, row["policy_version"]),
        )

    def correct_record(
        self,
        record_id: str,
        *,
        content: str,
        reason: str,
        structured_value: Mapping[str, Any] | None = None,
        supersedes: str | None = None,
        entity_key: str | None = None,
        attribute_key: str | None = None,
        actor: str = "local-user",
    ) -> ContextRecordOut:
        if (entity_key is None) != (attribute_key is None):
            raise InvalidStateError("entity_key and attribute_key must be supplied together")
        if contains_secret_like_value(
            {
                "content": content,
                "reason": reason,
                "structured_value": structured_value,
                "entity_key": entity_key,
                "attribute_key": attribute_key,
            }
        ):
            raise InvalidStateError("direct secret-like correction content was refused")
        with self.transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM context_records WHERE id=? AND deleted_at IS NULL", (record_id,)
            ).fetchone()
            if previous is None:
                raise NotFoundError("context record not found")
            version = int(previous["version"]) + 1
            now = utc_now()
            connection.execute(
                "UPDATE context_records SET content=?,structured_value_json=?,supersedes=?,"
                "entity_key=?,attribute_key=?,content_hash=?,version=?,updated_at=?,"
                "observed_at=?,observation_origin=?,policy_version=? WHERE id=?",
                (
                    content,
                    _json(dict(structured_value))
                    if structured_value is not None
                    else previous["structured_value_json"],
                    supersedes if supersedes is not None else previous["supersedes"],
                    _normalized_slot_key(entity_key)
                    if entity_key is not None
                    else previous["entity_key"],
                    _normalized_slot_key(attribute_key)
                    if attribute_key is not None
                    else previous["attribute_key"],
                    _hash_text(content),
                    version,
                    now,
                    now,
                    ObservationOrigin.LOCAL_ADMIN.value,
                    AUTOMATIC_POLICY_VERSION,
                    record_id,
                ),
            )
            updated = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record_id,)
            ).fetchone()
            assert updated is not None
            connection.execute(
                "UPDATE context_records SET record_key=? WHERE id=?",
                (_stable_record_key_from_row(updated), record_id),
            )
            updated = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record_id,)
            ).fetchone()
            assert updated is not None
            self._insert_version(connection, updated, reason)
            self._replace_fts(connection, updated)
            if str(updated["availability"]) == Availability.ALWAYS.value:
                self._emit_event(
                    connection, updated, "record_upserted", self._relay_payload(updated)
                )
            self._recompute_integrity(connection)
            self._audit(connection, actor, "record_corrected", [record_id])
            return self._record_out(updated)

    def change_availability(
        self,
        record_id: str,
        availability: Availability,
        *,
        explicit_sensitive_replication: bool = False,
        actor: str = "local-user",
    ) -> ContextRecordOut:
        with self.transaction() as connection:
            previous = connection.execute(
                "SELECT * FROM context_records WHERE id=? AND deleted_at IS NULL", (record_id,)
            ).fetchone()
            if previous is None:
                raise NotFoundError("context record not found")
            if (
                availability == Availability.ALWAYS
                and str(previous["sensitivity"]) != Sensitivity.NORMAL.value
                and not explicit_sensitive_replication
            ):
                raise InvalidStateError(
                    "sensitive context requires explicit confirmation before replication"
                )
            old_availability = Availability(str(previous["availability"]))
            if availability == old_availability:
                return self._record_out(previous)
            now = utc_now()
            version = int(previous["version"]) + 1
            connection.execute(
                "UPDATE context_records SET availability=?,version=?,updated_at=? WHERE id=?",
                (availability.value, version, now, record_id),
            )
            updated = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record_id,)
            ).fetchone()
            assert updated is not None
            self._insert_version(connection, updated, "availability changed")
            if availability == Availability.ALWAYS:
                self._emit_event(
                    connection, updated, "record_upserted", self._relay_payload(updated)
                )
            elif old_availability == Availability.ALWAYS:
                self._emit_event(connection, updated, "record_withdrawn", {"record_id": record_id})
            self._audit(connection, actor, "availability_changed", [record_id])
            return self._record_out(updated)

    def delete_record(
        self, record_id: str, *, reason: str, actor: str = "local-user"
    ) -> dict[str, Any]:
        with self.transaction() as connection:
            return self._delete_record_tx(
                connection,
                record_id,
                reason=reason,
                actor=actor,
            )

    def _delete_record_tx(
        self,
        connection: sqlite3.Connection,
        record_id: str,
        *,
        reason: str,
        actor: str,
        recompute_integrity: bool = True,
    ) -> dict[str, Any]:
        record = connection.execute(
            "SELECT * FROM context_records WHERE id=? AND deleted_at IS NULL", (record_id,)
        ).fetchone()
        if record is None:
            existing = connection.execute(
                "SELECT * FROM deletion_tombstones WHERE record_id=?", (record_id,)
            ).fetchone()
            if existing is not None:
                return dict(existing)
            raise NotFoundError("context record not found")
        safe_reason = redact_secret_reason(reason)
        now = utc_now()
        version = int(record["version"]) + 1
        tombstone_hash = _hash_text(f"{record_id}:{version}:{now}")
        connection.execute(
            "UPDATE context_records SET deleted_at=?,updated_at=?,version=? WHERE id=?",
            (now, now, version, record_id),
        )
        deleted = connection.execute(
            "SELECT * FROM context_records WHERE id=?", (record_id,)
        ).fetchone()
        assert deleted is not None
        self._insert_version(connection, deleted, safe_reason)
        connection.execute("DELETE FROM context_fts WHERE record_id=?", (record_id,))
        connection.execute(
            "INSERT INTO deletion_tombstones"
            "(record_id,vault_id,deleted_version,reason,content_hash,deleted_at,"
            "deletion_origin,deletion_source_id) VALUES(?,?,?,?,?,?,?,?)",
            (
                record_id,
                record["vault_id"],
                version,
                safe_reason,
                tombstone_hash,
                now,
                "ordinary",
                None,
            ),
        )
        self._emit_event(
            connection,
            record,
            "record_deleted",
            {"record_id": record_id, "version": version, "deleted_at": now},
        )
        if recompute_integrity:
            self._recompute_integrity(connection)
        self._audit(connection, actor, "record_deleted", [record_id])
        return {
            "record_id": record_id,
            "deleted_version": version,
            "reason": safe_reason,
            "content_hash": tombstone_hash,
            "deleted_at": now,
        }

    def restore_record(
        self,
        record_id: str,
        *,
        version: int | None = None,
        reason: str = "restored by user",
        actor: str = "local-user",
    ) -> ContextRecordOut:
        """Restore a soft-deleted record or copy a historical version into current state."""

        with self.transaction() as connection:
            current = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record_id,)
            ).fetchone()
            if current is None:
                raise NotFoundError("context record not found")
            snapshot: Mapping[str, Any]
            if version is None:
                return self._restore_current_record_tx(
                    connection,
                    current,
                    reason=reason,
                    actor=actor,
                )
            else:
                historical = connection.execute(
                    "SELECT snapshot_json FROM context_record_versions "
                    "WHERE record_id=? AND version=?",
                    (record_id, version),
                ).fetchone()
                if historical is None:
                    raise NotFoundError("context record version not found")
                loaded = _loads(str(historical["snapshot_json"]), {})
                if not isinstance(loaded, dict):
                    raise InvalidStateError("stored context version is invalid")
                snapshot = loaded

            now = utc_now()
            next_version = int(current["version"]) + 1
            previous_availability = Availability(str(current["availability"]))
            target_availability = Availability(
                str(snapshot.get("availability", current["availability"]))
            )
            structured = snapshot.get("structured_value")
            connection.execute(
                "UPDATE context_records SET source_id=?,source_reference=?,kind=?,content=?,"
                "structured_value_json=?,"
                "entity_key=?,attribute_key=?,scopes_json=?,tags_json=?,source_service=?,"
                "source_type=?,evidence=?,confidence=?,sensitivity=?,availability=?,"
                "allowed_clients_json=?,denied_clients_json=?,valid_from=?,expires_at=?,"
                "supersedes=?,explicit_user_statement=?,approval_status='approved',content_hash=?,"
                "schema_version=?,version=?,updated_at=?,deleted_at=NULL,observed_at=?,"
                "observation_origin=?,policy_version=? WHERE id=?",
                (
                    snapshot.get("source_id", current["source_id"]),
                    snapshot.get("source_reference", current["source_reference"]),
                    snapshot.get("kind", current["kind"]),
                    snapshot.get("content", current["content"]),
                    _json(structured) if structured is not None else None,
                    _normalized_slot_key(
                        cast(str | None, snapshot.get("entity_key", current["entity_key"]))
                    ),
                    _normalized_slot_key(
                        cast(
                            str | None,
                            snapshot.get("attribute_key", current["attribute_key"]),
                        )
                    ),
                    _json(snapshot.get("scopes", _loads(current["scopes_json"], []))),
                    _json(snapshot.get("tags", _loads(current["tags_json"], []))),
                    snapshot.get("source_service", current["source_service"]),
                    snapshot.get("source_type", current["source_type"]),
                    snapshot.get("evidence", current["evidence"]),
                    snapshot.get("confidence", current["confidence"]),
                    snapshot.get("sensitivity", current["sensitivity"]),
                    target_availability.value,
                    _json(
                        snapshot.get("allowed_clients", _loads(current["allowed_clients_json"], []))
                    ),
                    _json(
                        snapshot.get("denied_clients", _loads(current["denied_clients_json"], []))
                    ),
                    snapshot.get("valid_from", current["valid_from"]),
                    snapshot.get("expires_at", current["expires_at"]),
                    snapshot.get("supersedes", current["supersedes"]),
                    int(
                        bool(
                            snapshot.get(
                                "explicit_user_statement",
                                current["explicit_user_statement"],
                            )
                        )
                    ),
                    _hash_text(str(snapshot.get("content", current["content"]))),
                    snapshot.get("schema_version", current["schema_version"]),
                    next_version,
                    now,
                    snapshot.get("observed_at", current["observed_at"]) or now,
                    snapshot.get("observation_origin", current["observation_origin"])
                    or ObservationOrigin.LOCAL_ADMIN.value,
                    AUTOMATIC_POLICY_VERSION,
                    record_id,
                ),
            )
            connection.execute("DELETE FROM deletion_tombstones WHERE record_id=?", (record_id,))
            restored = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record_id,)
            ).fetchone()
            assert restored is not None
            connection.execute(
                "UPDATE context_records SET record_key=? WHERE id=?",
                (_stable_record_key_from_row(restored), record_id),
            )
            restored = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record_id,)
            ).fetchone()
            assert restored is not None
            self._insert_version(connection, restored, reason)
            self._replace_fts(connection, restored)
            if target_availability == Availability.ALWAYS:
                self._emit_event(
                    connection, restored, "record_upserted", self._relay_payload(restored)
                )
            elif previous_availability == Availability.ALWAYS:
                self._emit_event(connection, restored, "record_withdrawn", {"record_id": record_id})
            self._recompute_integrity(connection)
            self._audit(
                connection,
                actor,
                "record_restored",
                [record_id],
                metadata={"restored_version": version},
            )
            return self._record_out(restored)

    def _restore_current_record_tx(
        self,
        connection: sqlite3.Connection,
        current: sqlite3.Row,
        *,
        reason: str,
        actor: str,
        recompute_integrity: bool = True,
    ) -> ContextRecordOut:
        if current["deleted_at"] is None:
            return self._record_out(current)
        record_id = str(current["id"])
        now = utc_now()
        next_version = int(current["version"]) + 1
        availability = Availability(str(current["availability"]))
        connection.execute(
            "UPDATE context_records SET approval_status='approved',version=?,updated_at=?,"
            "deleted_at=NULL,policy_version=? WHERE id=?",
            (next_version, now, AUTOMATIC_POLICY_VERSION, record_id),
        )
        connection.execute("DELETE FROM deletion_tombstones WHERE record_id=?", (record_id,))
        restored = connection.execute(
            "SELECT * FROM context_records WHERE id=?", (record_id,)
        ).fetchone()
        assert restored is not None
        self._insert_version(connection, restored, reason)
        self._replace_fts(connection, restored)
        if availability == Availability.ALWAYS:
            self._emit_event(
                connection,
                restored,
                "record_upserted",
                self._relay_payload(restored),
            )
        if recompute_integrity:
            self._recompute_integrity(connection)
        self._audit(
            connection,
            actor,
            "record_restored",
            [record_id],
            metadata={"restored_version": None},
        )
        return self._record_out(restored)

    def purge_confirmation_phrase(self, target_type: str, target_id: str) -> str:
        return PURGE_CONFIRMATION_TEMPLATE.format(
            target_type=target_type.upper(), target_id=target_id
        )

    def purge(
        self,
        target_type: str,
        target_id: str,
        *,
        confirmation: str,
        actor: str = "local-administrator",
        compact: bool = True,
    ) -> dict[str, Any]:
        """Irreversibly remove a record or source and retain opaque replay barriers only."""

        if target_type not in {"record", "source"}:
            raise InvalidStateError("purge target_type must be record or source")
        if confirmation != self.purge_confirmation_phrase(target_type, target_id):
            raise InvalidStateError("exact purge confirmation phrase did not match")
        vault_id = self.vault_id()
        with self.transaction() as connection:
            existing_job = connection.execute(
                "SELECT * FROM purge_jobs WHERE vault_id=? AND target_type=? AND target_id=?",
                (vault_id, target_type, target_id),
            ).fetchone()
            if existing_job is None:
                job_id = new_id()
                now = utc_now()
                if target_type == "record":
                    self._purge_record_tx(
                        connection, target_id, purge_scope="record", purged_at=now
                    )
                else:
                    self._purge_source_tx(connection, target_id, purged_at=now)
                connection.execute(
                    "INSERT INTO purge_jobs"
                    "(id,vault_id,target_type,target_id,phase,created_at,updated_at) "
                    "VALUES(?,?,?,?,'compaction_pending',?,?)",
                    (job_id, vault_id, target_type, target_id, now, now),
                )
                self._audit(
                    connection,
                    actor,
                    f"{target_type}_purged",
                    [target_id],
                    metadata={"irreversible": True, "purge_job_id": job_id},
                )
            else:
                job_id = str(existing_job["id"])
        if compact:
            self.resume_purge_jobs(job_id=job_id, limit=1)
        return self.get_purge_job(job_id)

    def _purge_record_tx(
        self,
        connection: sqlite3.Connection,
        record_id: str,
        *,
        purge_scope: str,
        purged_at: str,
    ) -> None:
        existing = connection.execute(
            "SELECT * FROM purge_tombstones WHERE stable_id=?", (record_id,)
        ).fetchone()
        if existing is not None:
            return
        record = connection.execute(
            "SELECT * FROM context_records WHERE id=?", (record_id,)
        ).fetchone()
        if record is None:
            raise NotFoundError("purge target not found")
        vault_id = str(record["vault_id"])
        source_id = cast(str | None, record["source_id"])
        candidate_id = cast(str | None, record["candidate_id"])
        observation_ids = {
            str(row["observation_id"])
            for row in connection.execute(
                "SELECT observation_id FROM context_observation_links WHERE record_id=?",
                (record_id,),
            ).fetchall()
        }
        observation_ids.update(
            str(row["candidate_id"])
            for row in connection.execute(
                "SELECT candidate_id FROM context_errors "
                "WHERE record_id=? AND candidate_id IS NOT NULL",
                (record_id,),
            ).fetchall()
        )
        observation_ids.update(
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM context_candidates WHERE supersedes=? OR record_id=?",
                (record_id, record_id),
            ).fetchall()
        )
        if candidate_id is not None:
            observation_ids.add(candidate_id)
        slot = (cast(str | None, record["entity_key"]), cast(str | None, record["attribute_key"]))
        purge_payload = {
            "record_id": record_id,
            "purged_at": purged_at,
            "purge_scope": purge_scope,
            "irreversible": True,
        }
        # Historical outbox payloads can contain the full record. Preserve their
        # ordered sequence positions while replacing them with opaque withdrawals.
        opaque = _json({"record_id": record_id})
        connection.execute(
            "UPDATE replication_events SET event_type='record_withdrawn',payload_json=?,"
            "payload_hash=?,mac=NULL WHERE record_id=?",
            (opaque, _hash_text(opaque), record_id),
        )
        self._emit_event(connection, record, "record_purged", purge_payload)
        event = connection.execute(
            "SELECT id,sequence FROM replication_events WHERE record_id=? "
            "ORDER BY sequence DESC LIMIT 1",
            (record_id,),
        ).fetchone()
        assert event is not None
        connection.execute("DELETE FROM context_fts WHERE record_id=?", (record_id,))
        connection.execute("DELETE FROM context_record_versions WHERE record_id=?", (record_id,))
        connection.execute("DELETE FROM deletion_tombstones WHERE record_id=?", (record_id,))
        connection.execute(
            "UPDATE context_records SET supersedes=NULL WHERE supersedes=?", (record_id,)
        )
        connection.execute("DELETE FROM context_errors WHERE record_id=?", (record_id,))
        connection.execute("DELETE FROM context_records WHERE id=?", (record_id,))
        for observation_id in sorted(observation_ids):
            linked_elsewhere = connection.execute(
                "SELECT 1 FROM context_observation_links WHERE observation_id=? "
                "UNION ALL SELECT 1 FROM context_records WHERE candidate_id=? LIMIT 1",
                (observation_id, observation_id),
            ).fetchone()
            if linked_elsewhere is None:
                self._detach_candidate_from_batches(connection, observation_id)
                connection.execute(
                    "DELETE FROM edge_proposal_receipts WHERE candidate_id=?",
                    (observation_id,),
                )
                connection.execute(
                    "DELETE FROM context_errors WHERE candidate_id=?", (observation_id,)
                )
                connection.execute("DELETE FROM context_candidates WHERE id=?", (observation_id,))
        self._remove_related_audits(connection, record_id)
        connection.execute(
            "INSERT INTO purge_tombstones"
            "(stable_id,vault_id,target_type,purged_at,replication_sequence,replication_event_id) "
            "VALUES(?,?,?,?,?,?)",
            (record_id, vault_id, "record", purged_at, event["sequence"], event["id"]),
        )
        if slot[0] is not None and slot[1] is not None:
            connection.execute(
                "DELETE FROM integrity_groups WHERE vault_id=? "
                "AND entity_key=? AND attribute_key=?",
                (vault_id, slot[0], slot[1]),
            )
        if source_id is not None and purge_scope == "record":
            dependent = connection.execute(
                "SELECT 1 FROM context_records WHERE source_id=? UNION ALL "
                "SELECT 1 FROM context_candidates WHERE source_id=? LIMIT 1",
                (source_id, source_id),
            ).fetchone()
            if dependent is None:
                source = connection.execute(
                    "SELECT vault_id FROM source_records WHERE id=?", (source_id,)
                ).fetchone()
                self._delete_source_material_tx(connection, source_id)
                if source is not None:
                    connection.execute(
                        "INSERT OR IGNORE INTO purge_tombstones"
                        "(stable_id,vault_id,target_type,purged_at) VALUES(?,?,?,?)",
                        (source_id, source["vault_id"], "source", purged_at),
                    )
        self._recompute_integrity(connection)

    def _purge_source_tx(
        self, connection: sqlite3.Connection, source_id: str, *, purged_at: str
    ) -> None:
        if (
            connection.execute(
                "SELECT 1 FROM purge_tombstones WHERE stable_id=?", (source_id,)
            ).fetchone()
            is not None
        ):
            return
        source = connection.execute(
            "SELECT * FROM source_records WHERE id=?", (source_id,)
        ).fetchone()
        if source is None:
            raise NotFoundError("purge target not found")
        record_ids = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM context_records WHERE source_id=? ORDER BY id", (source_id,)
            ).fetchall()
        ]
        for record_id in record_ids:
            self._purge_record_tx(connection, record_id, purge_scope="source", purged_at=purged_at)
        candidate_ids = [
            str(row["id"])
            for row in connection.execute(
                "SELECT id FROM context_candidates WHERE source_id=?", (source_id,)
            ).fetchall()
        ]
        for candidate_id in candidate_ids:
            self._detach_candidate_from_batches(connection, candidate_id)
            connection.execute(
                "DELETE FROM edge_proposal_receipts WHERE candidate_id=?", (candidate_id,)
            )
            connection.execute("DELETE FROM context_errors WHERE candidate_id=?", (candidate_id,))
            connection.execute(
                "DELETE FROM context_observation_links WHERE observation_id=?",
                (candidate_id,),
            )
        connection.execute("DELETE FROM context_candidates WHERE source_id=?", (source_id,))
        self._delete_source_material_tx(connection, source_id)
        self._remove_related_audits(connection, source_id)
        connection.execute(
            "INSERT INTO purge_tombstones"
            "(stable_id,vault_id,target_type,purged_at) VALUES(?,?,?,?)",
            (source_id, source["vault_id"], "source", purged_at),
        )

    def _delete_source_material_tx(self, connection: sqlite3.Connection, source_id: str) -> None:
        source = connection.execute(
            "SELECT content_hash FROM source_records WHERE id=?", (source_id,)
        ).fetchone()
        if source is None:
            return
        content_hash = str(source["content_hash"])
        connection.execute("DELETE FROM source_records WHERE id=?", (source_id,))
        if (
            connection.execute(
                "SELECT 1 FROM source_records WHERE content_hash=?", (content_hash,)
            ).fetchone()
            is None
        ):
            connection.execute("DELETE FROM source_blobs WHERE content_hash=?", (content_hash,))

    def _detach_candidate_from_batches(
        self, connection: sqlite3.Connection, candidate_id: str
    ) -> None:
        for batch in connection.execute(
            "SELECT id,candidate_ids_json FROM ingestion_batches"
        ).fetchall():
            ids = [item for item in _loads(batch["candidate_ids_json"], []) if item != candidate_id]
            if len(ids) != len(_loads(batch["candidate_ids_json"], [])):
                connection.execute(
                    "UPDATE ingestion_batches SET candidate_ids_json=?,request_hash=? WHERE id=?",
                    (_json(ids), new_id(), batch["id"]),
                )

    def _remove_related_audits(self, connection: sqlite3.Connection, stable_id: str) -> None:
        for row in connection.execute(
            "SELECT id,record_ids_json,denied_record_ids_json FROM audit_events"
        ).fetchall():
            if stable_id in _loads(row["record_ids_json"], []) or stable_id in _loads(
                row["denied_record_ids_json"], []
            ):
                connection.execute("DELETE FROM audit_events WHERE id=?", (row["id"],))

    def get_purge_job(self, job_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM purge_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise NotFoundError("purge job not found")
        return {
            "id": str(row["id"]),
            "target_type": str(row["target_type"]),
            "target_id": str(row["target_id"]),
            "phase": str(row["phase"]),
            "last_error_code": cast(str | None, row["last_error_code"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "completed_at": cast(str | None, row["completed_at"]),
        }

    def list_purge_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            ids = [
                str(row["id"])
                for row in connection.execute(
                    "SELECT id FROM purge_jobs ORDER BY updated_at DESC LIMIT ?",
                    (min(max(limit, 1), 500),),
                ).fetchall()
            ]
        return [self.get_purge_job(job_id) for job_id in ids]

    def resume_purge_jobs(self, *, job_id: str | None = None, limit: int = 10) -> int:
        conditions = ["phase='compaction_pending'"]
        parameters: list[Any] = []
        if job_id is not None:
            conditions.append("id=?")
            parameters.append(job_id)
        with self.connect() as connection:
            ids = [
                str(row["id"])
                for row in connection.execute(
                    f"SELECT id FROM purge_jobs WHERE {' AND '.join(conditions)} "
                    "ORDER BY created_at LIMIT ?",
                    [*parameters, min(max(limit, 1), 100)],
                ).fetchall()
            ]
        completed = 0
        for pending_id in ids:
            try:
                database_size = self.database_path.stat().st_size
                if (
                    shutil.disk_usage(self.database_path.parent).free
                    < database_size * 2 + 16_777_216
                ):
                    self._mark_purge_compaction_error(pending_id, "insufficient_disk")
                    continue
                with self._write_lock, self.connect() as connection:
                    connection.execute("PRAGMA busy_timeout = 250")
                    checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if checkpoint is not None and int(checkpoint[0]) != 0:
                        raise sqlite3.OperationalError("database is locked")
                    connection.execute("VACUUM")
                    connection.execute(
                        "UPDATE purge_jobs SET phase='completed',last_error_code=NULL,"
                        "updated_at=?,completed_at=? WHERE id=? AND phase='compaction_pending'",
                        (utc_now(), utc_now(), pending_id),
                    )
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                completed += 1
            except sqlite3.OperationalError as exc:
                code = "database_locked" if "locked" in str(exc).casefold() else "compaction_failed"
                self._mark_purge_compaction_error(pending_id, code)
        return completed

    def _mark_purge_compaction_error(self, job_id: str, code: str) -> None:
        try:
            with self._write_lock, self.connect() as connection:
                connection.execute("PRAGMA busy_timeout = 250")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE purge_jobs SET last_error_code=?,updated_at=? "
                    "WHERE id=? AND phase='compaction_pending'",
                    (code, utc_now(), job_id),
                )
                connection.commit()
        except sqlite3.OperationalError:
            # An external writer may prevent even recording the bounded error.
            # The durable pending phase remains the fail-closed recovery signal.
            return

    def record_history(self, record_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM context_record_versions WHERE record_id=? ORDER BY version",
                (record_id,),
            ).fetchall()
        return [
            {
                "version_id": str(row["id"]),
                "record_id": str(row["record_id"]),
                "version": int(row["version"]),
                "snapshot": _loads(row["snapshot_json"], {}),
                "reason": str(row["reason"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    @staticmethod
    def _truth_status_case_sql(alias: str) -> str:
        """Return the canonical status precedence used by list/count queries."""

        return (
            "CASE "
            f"WHEN {alias}.deleted_at IS NOT NULL THEN 'deleted' "
            f"WHEN EXISTS (SELECT 1 FROM context_records newer "
            f"WHERE newer.supersedes={alias}.id "
            "AND newer.approval_status='approved' AND newer.deleted_at IS NULL) "
            "THEN 'superseded' "
            f"WHEN EXISTS (SELECT 1 FROM integrity_group_members gm "
            "JOIN integrity_groups ig ON ig.id=gm.group_id "
            f"WHERE gm.record_id={alias}.id AND ig.group_type='conflict' "
            "AND ig.status='open') THEN 'conflicted' "
            "ELSE 'current' END"
        )

    def _truth_status_tx(
        self, connection: sqlite3.Connection, record: sqlite3.Row
    ) -> tuple[MemoryTruthStatus, str, TruthConflictState, list[str], list[str]]:
        if record["deleted_at"] is not None:
            tombstone = connection.execute(
                "SELECT reason FROM deletion_tombstones WHERE record_id=?", (record["id"],)
            ).fetchone()
            return (
                MemoryTruthStatus.DELETED,
                str(tombstone["reason"] if tombstone is not None else "record is soft-deleted"),
                TruthConflictState.NONE,
                [],
                [],
            )

        superseded_rows = connection.execute(
            "SELECT id FROM context_records WHERE supersedes=? "
            "AND approval_status='approved' AND deleted_at IS NULL ORDER BY id LIMIT ?",
            (record["id"], MAX_TRUTH_SUPERSEDED_BY),
        ).fetchall()
        superseded_by = [str(row["id"]) for row in superseded_rows]
        if superseded_by:
            return (
                MemoryTruthStatus.SUPERSEDED,
                "replaced by a newer canonical record",
                TruthConflictState.NONE,
                [],
                superseded_by,
            )

        active_groups = connection.execute(
            "SELECT g.id FROM integrity_groups g "
            "JOIN integrity_group_members m ON m.group_id=g.id "
            "WHERE m.record_id=? AND g.group_type='conflict' AND g.status='open' "
            "ORDER BY g.id LIMIT ?",
            (record["id"], MAX_TRUTH_CONFLICT_GROUPS),
        ).fetchall()
        resolved_groups = connection.execute(
            "SELECT g.id FROM integrity_groups g "
            "JOIN integrity_group_members m ON m.group_id=g.id "
            "WHERE m.record_id=? AND g.group_type='conflict' AND g.status='resolved' "
            "ORDER BY g.id LIMIT ?",
            (record["id"], MAX_TRUTH_CONFLICT_GROUPS),
        ).fetchall()
        active_ids = [str(row["id"]) for row in active_groups]
        resolved_ids = [str(row["id"]) for row in resolved_groups]
        if active_ids:
            return (
                MemoryTruthStatus.CONFLICTED,
                "multiple current values remain for the same memory slot",
                TruthConflictState.ACTIVE,
                active_ids,
                [],
            )
        if resolved_ids:
            return (
                MemoryTruthStatus.CURRENT,
                "current value; an earlier slot conflict was resolved",
                TruthConflictState.RESOLVED,
                resolved_ids,
                [],
            )
        return (
            MemoryTruthStatus.CURRENT,
            "current applied record",
            TruthConflictState.NONE,
            [],
            [],
        )

    def _truth_source_tx(
        self, connection: sqlite3.Connection, source_id: str | None
    ) -> TruthSourceOut | None:
        if source_id is None:
            return None
        row = connection.execute(
            "SELECT sr.*,sb.byte_size,sb.media_type,"
            "(SELECT COUNT(*) FROM context_candidates cc WHERE cc.source_id=sr.id) "
            "AS candidate_count FROM source_records sr "
            "JOIN source_blobs sb ON sb.content_hash=sr.content_hash WHERE sr.id=?",
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        return TruthSourceOut(
            id=str(row["id"]),
            content_hash=str(row["content_hash"]),
            source_service=str(row["source_service"]),
            source_type=str(row["source_type"]),
            filename=cast(str | None, row["filename"]),
            media_type=str(row["media_type"]),
            created_at=str(row["created_at"]),
            import_status=cast(
                Literal["processing", "complete", "failed", "cancelled"],
                row["import_status"],
            ),
            deleted_at=cast(str | None, row["deleted_at"]),
            deleted_reason=cast(str | None, row["deleted_reason"]),
        )

    def _truth_evidence_tx(
        self, connection: sqlite3.Connection, record_id: str
    ) -> list[TruthEvidenceOut]:
        rows = connection.execute(
            "SELECT l.relationship,l.created_at AS link_created_at,c.* "
            "FROM context_observation_links l "
            "JOIN context_candidates c ON c.id=l.observation_id "
            "WHERE l.record_id=? ORDER BY c.observed_at,c.created_at,c.id LIMIT ?",
            (record_id, MAX_TRUTH_EVIDENCE),
        ).fetchall()
        return [
            TruthEvidenceOut(
                observation_id=str(row["id"]),
                record_id=record_id,
                relationship=str(row["relationship"]),
                link_created_at=str(row["link_created_at"]),
                disposition=ObservationDisposition(str(row["disposition"])),
                decision_reason=cast(str | None, row["decision_reason"]),
                decided_at=cast(str | None, row["decided_at"]),
                observation_origin=cast(str | None, row["observation_origin"]),
                policy_version=cast(str | None, row["policy_version"]),
                content=str(row["content"]),
                evidence=cast(str | None, row["evidence"]),
                confidence=float(row["confidence"]),
                sensitivity=Sensitivity(str(row["sensitivity"])),
                source_id=cast(str | None, row["source_id"]),
                source_reference=cast(str | None, row["source_reference"]),
                source_service=cast(str | None, row["source_service"]),
                source_type=cast(str | None, row["source_type"]),
                effective_at=cast(str | None, row["valid_from"]),
                observed_at=cast(str | None, row["observed_at"]),
                recorded_at=str(row["created_at"]),
                content_hash=str(row["content_hash"]),
            )
            for row in rows
        ]

    def _truth_record_tx(
        self, connection: sqlite3.Connection, record: sqlite3.Row
    ) -> MemoryTruthRecordOut:
        status, reason, conflict_state, group_ids, superseded_by = self._truth_status_tx(
            connection, record
        )
        record_out = self._record_out(record).model_copy(update={"status": status})
        history_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM context_record_versions WHERE record_id=?",
                (record["id"],),
            ).fetchone()[0]
        )
        return MemoryTruthRecordOut(
            record=record_out,
            status=status,
            status_reason=reason,
            conflict_state=conflict_state,
            conflict_group_ids=group_ids,
            superseded_by=superseded_by,
            source=self._truth_source_tx(connection, cast(str | None, record["source_id"])),
            evidence=self._truth_evidence_tx(connection, str(record["id"])),
            history_count=history_count,
        )

    def _truth_coverage_tx(self, connection: sqlite3.Connection) -> TruthCoverageOut:
        source_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM source_records WHERE deleted_at IS NULL"
            ).fetchone()[0]
        )
        deleted_source_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM source_records WHERE deleted_at IS NOT NULL"
            ).fetchone()[0]
        )
        observations_by_disposition = {
            str(row["disposition"]): int(row["count"])
            for row in connection.execute(
                "SELECT disposition,COUNT(*) AS count FROM context_candidates GROUP BY disposition"
            ).fetchall()
        }
        all_dispositions = {
            item.value: observations_by_disposition.get(item.value, 0)
            for item in ObservationDisposition
        }
        records_by_status = {item.value: 0 for item in MemoryTruthStatus}
        status_case = self._truth_status_case_sql("r")
        status_rows = connection.execute(
            f"SELECT {status_case} AS truth_status,COUNT(*) AS count "
            "FROM context_records r GROUP BY truth_status"
        ).fetchall()
        for row in status_rows:
            status_value = str(row["truth_status"])
            if status_value in records_by_status:
                records_by_status[status_value] = int(row["count"])
        session_rows = connection.execute(
            "SELECT status,coverage_json,unavailable_sources_json "
            "FROM ingestion_sessions"
        ).fetchall()
        incomplete = 0
        unavailable = 0
        for row in session_rows:
            try:
                coverage = _loads(cast(str | None, row["coverage_json"]), {})
                unavailable_sources = _loads(row["unavailable_sources_json"], [])
            except (TypeError, ValueError):
                coverage = {}
                unavailable_sources = ["invalid-coverage"]
            coverage_complete = (
                bool(coverage.get("complete", False))
                if isinstance(coverage, dict)
                else False
            )
            if str(row["status"]) != "finished" or not coverage_complete:
                incomplete += 1
            coverage_unavailable = (
                coverage.get("unavailable", []) if isinstance(coverage, dict) else []
            )
            if unavailable_sources or coverage_unavailable:
                unavailable += 1
        conflict_group_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM integrity_groups "
                "WHERE status='open' AND group_type='conflict'"
            ).fetchone()[0]
        )
        return TruthCoverageOut(
            source_count=source_count,
            deleted_source_count=deleted_source_count,
            observation_count=sum(all_dispositions.values()),
            observations_by_disposition=all_dispositions,
            record_count=sum(records_by_status.values()),
            records_by_status=records_by_status,
            conflict_group_count=conflict_group_count,
            ingestion_session_count=len(session_rows),
            incomplete_ingestion_session_count=incomplete,
            sessions_with_unavailable_sources=unavailable,
        )

    def memory_truth_coverage(self) -> TruthCoverageOut:
        self.rebuild_integrity_groups()
        with self.connect() as connection:
            return self._truth_coverage_tx(connection)

    def get_memory_truth(
        self,
        record_id: str,
        *,
        include_deleted: bool = True,
        principal: ClientPrincipal | None = None,
    ) -> MemoryTruthRecordOut:
        self.rebuild_integrity_groups()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record_id,)
            ).fetchone()
            if row is None or (not include_deleted and row["deleted_at"] is not None):
                raise NotFoundError("context record not found")
            if principal is not None and not self._record_is_allowed(row, principal):
                raise NotFoundError("context record not found")
            return self._truth_record_tx(connection, row)

    def list_memory_truth(
        self,
        *,
        status: MemoryTruthStatus | None = None,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> MemoryTruthResponse:
        self.rebuild_integrity_groups()
        bounded_limit = min(max(limit, 1), 500)
        bounded_offset = max(offset, 0)
        with self.connect() as connection:
            conditions = ["vault_id=?"]
            parameters: list[Any] = [self.vault_id()]
            if source_id is not None:
                conditions.append("source_id=?")
                parameters.append(source_id)
            status_case = self._truth_status_case_sql("context_records")
            if status is not None:
                conditions.append(f"({status_case})=?")
                parameters.append(status.value)
            where = " AND ".join(conditions)
            total_records = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM context_records WHERE {where}", parameters
                ).fetchone()[0]
            )
            rows = connection.execute(
                "SELECT * FROM context_records WHERE "
                + where
                + " ORDER BY updated_at DESC,id LIMIT ? OFFSET ?",
                [*parameters, bounded_limit, bounded_offset],
            ).fetchall()
            page = [self._truth_record_tx(connection, row) for row in rows]
            tentative_rows = []
            if status is None or status == MemoryTruthStatus.TENTATIVE:
                tentative_rows = connection.execute(
                    "SELECT * FROM context_candidates WHERE vault_id=? "
                    "AND disposition='tentative' "
                    + ("AND source_id=? " if source_id is not None else "")
                    + "ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                    [
                        self.vault_id(),
                        *([source_id] if source_id is not None else []),
                        bounded_limit,
                        bounded_offset,
                    ],
                ).fetchall()
            tentative = [
                MemoryTruthObservationOut(
                    observation=self._observation_out(row),
                    status=MemoryTruthStatus.TENTATIVE,
                    status_reason=str(row["decision_reason"] or "retained as non-current evidence"),
                )
                for row in tentative_rows
            ]
            coverage = self._truth_coverage_tx(connection)
        return MemoryTruthResponse(
            items=page,
            tentative_observations=tentative,
            total=total_records,
            counts=coverage.records_by_status,
            coverage=coverage,
        )

    def _insert_version(
        self, connection: sqlite3.Connection, row: sqlite3.Row, reason: str
    ) -> None:
        snapshot = self._record_out(row).model_dump(mode="json")
        connection.execute(
            "INSERT INTO context_record_versions"
            "(id,record_id,version,snapshot_json,reason,created_at) VALUES(?,?,?,?,?,?)",
            (new_id(), row["id"], row["version"], _json(snapshot), reason, utc_now()),
        )

    def _replace_fts(self, connection: sqlite3.Connection, row: sqlite3.Row) -> None:
        connection.execute("DELETE FROM context_fts WHERE record_id=?", (row["id"],))
        connection.execute(
            "INSERT INTO context_fts(record_id,content,kind,tags,scopes) VALUES(?,?,?,?,?)",
            (
                row["id"],
                row["content"],
                row["kind"],
                " ".join(_loads(row["tags_json"], [])),
                " ".join(_loads(row["scopes_json"], [])),
            ),
        )

    def _recompute_integrity(self, connection: sqlite3.Connection) -> None:
        """Rebuild deterministic open groups inside the caller's write transaction."""

        now = utc_now()
        connection.execute("UPDATE integrity_groups SET status='resolved',updated_at=?", (now,))
        rows = connection.execute(
            "SELECT r.* FROM context_records r WHERE r.approval_status='approved' "
            "AND r.deleted_at IS NULL AND r.entity_key IS NOT NULL AND r.attribute_key IS NOT NULL "
            "AND (r.expires_at IS NULL OR r.expires_at>?) "
            "AND NOT EXISTS (SELECT 1 FROM context_records newer WHERE newer.supersedes=r.id "
            "AND newer.approval_status='approved' AND newer.deleted_at IS NULL "
            "AND (newer.expires_at IS NULL OR newer.expires_at>?)) "
            "ORDER BY r.entity_key,r.attribute_key,r.id",
            (now, now),
        ).fetchall()
        slots: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
        for row in rows:
            key = (str(row["vault_id"]), str(row["entity_key"]), str(row["attribute_key"]))
            slots.setdefault(key, []).append(row)
        for (vault_id, entity_key, attribute_key), members in slots.items():
            if len(members) < 2:
                continue
            by_value: dict[str, list[sqlite3.Row]] = {}
            for member in members:
                by_value.setdefault(_value_fingerprint(member), []).append(member)
            if len(by_value) > 1:
                self._open_integrity_group(
                    connection,
                    vault_id,
                    entity_key,
                    attribute_key,
                    "conflict",
                    None,
                    members,
                    now,
                )
            for fingerprint, duplicate_members in sorted(by_value.items()):
                if len(duplicate_members) > 1:
                    self._open_integrity_group(
                        connection,
                        vault_id,
                        entity_key,
                        attribute_key,
                        "duplicate",
                        fingerprint,
                        duplicate_members,
                        now,
                    )

    def _open_integrity_group(
        self,
        connection: sqlite3.Connection,
        vault_id: str,
        entity_key: str,
        attribute_key: str,
        group_type: str,
        fingerprint: str | None,
        members: Sequence[sqlite3.Row],
        now: str,
    ) -> None:
        identity = _json([vault_id, entity_key, attribute_key, group_type, fingerprint])
        group_id = "integrity_" + _hash_text(identity)
        connection.execute(
            "INSERT INTO integrity_groups"
            "(id,vault_id,entity_key,attribute_key,group_type,value_fingerprint,"
            "status,created_at,updated_at) VALUES(?,?,?,?,?,?,'open',?,?) "
            "ON CONFLICT(id) DO UPDATE SET status='open',updated_at=excluded.updated_at",
            (group_id, vault_id, entity_key, attribute_key, group_type, fingerprint, now, now),
        )
        connection.execute("DELETE FROM integrity_group_members WHERE group_id=?", (group_id,))
        connection.executemany(
            "INSERT INTO integrity_group_members(group_id,record_id) VALUES(?,?)",
            [(group_id, str(member["id"])) for member in members],
        )

    def list_integrity_groups(
        self, *, status: str = "open", limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        if status not in {"open", "resolved", "all"}:
            raise InvalidStateError("integrity group status is invalid")
        self.rebuild_integrity_groups()
        conditions = ["g.vault_id=?"]
        parameters: list[Any] = [self.vault_id()]
        if status != "all":
            conditions.append("g.status=?")
            parameters.append(status)
        where = " AND ".join(conditions)
        bounded_limit = min(max(limit, 1), 500)
        with self.connect() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM integrity_groups g WHERE {where}", parameters
                ).fetchone()[0]
            )
            groups = connection.execute(
                f"SELECT g.* FROM integrity_groups g WHERE {where} "
                "ORDER BY g.updated_at DESC,g.id LIMIT ? OFFSET ?",
                [*parameters, bounded_limit, max(offset, 0)],
            ).fetchall()
            items = []
            for group in groups:
                member_rows = connection.execute(
                    "SELECT record_id FROM integrity_group_members "
                    "WHERE group_id=? ORDER BY record_id",
                    (group["id"],),
                ).fetchall()
                items.append(
                    {
                        "id": str(group["id"]),
                        "entity_key": str(group["entity_key"]),
                        "attribute_key": str(group["attribute_key"]),
                        "group_type": str(group["group_type"]),
                        "status": str(group["status"]),
                        "record_ids": [str(member["record_id"]) for member in member_rows],
                        "created_at": str(group["created_at"]),
                        "updated_at": str(group["updated_at"]),
                    }
                )
        return {"items": items, "total": total}

    def rebuild_integrity_groups(self) -> None:
        with self.transaction() as connection:
            self._recompute_integrity(connection)

    def _relay_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        record = self._record_out(row)
        # Evidence, local source identifiers, and ingestion retry keys are not
        # necessary to serve the approved projection.
        payload = record.model_dump(
            mode="json",
            exclude={"evidence", "source_id", "idempotency_key"},
        )
        if record.availability != Availability.ALWAYS:
            raise InvalidStateError("only always_available records may be replicated")
        if len(_json(payload).encode("utf-8")) > MAX_REPLICATION_PAYLOAD_BYTES:
            raise InvalidStateError("record is too large for the bounded Edge replication protocol")
        return payload

    def _emit_event(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        vault_id = str(row["vault_id"])
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence),0)+1 FROM replication_events WHERE vault_id=?",
                (vault_id,),
            ).fetchone()[0]
        )
        payload_json = _json(dict(payload))
        connection.execute(
            "INSERT INTO replication_events"
            "(id,vault_id,sequence,event_type,record_id,payload_json,payload_hash,created_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                new_id(),
                vault_id,
                sequence,
                event_type,
                row["id"],
                payload_json,
                _hash_text(payload_json),
                utc_now(),
            ),
        )

    def _audit(
        self,
        connection: sqlite3.Connection,
        client_id: str | None,
        action: str,
        record_ids: Sequence[str],
        *,
        trace_id: str | None = None,
        denied_ids: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        audit_id = new_id()
        connection.execute(
            "INSERT INTO audit_events"
            "(id,vault_id,client_id,action,trace_id,record_ids_json,denied_record_ids_json,"
            "metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                audit_id,
                self.vault_id(),
                client_id,
                action,
                trace_id or new_id(),
                _json(list(record_ids)),
                _json(list(denied_ids)),
                _json(dict(metadata or {})),
                utc_now(),
            ),
        )
        return audit_id

    def audit_access(
        self,
        client_id: str | None,
        action: str,
        record_ids: Sequence[str],
        *,
        trace_id: str,
        denied_ids: Sequence[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        with self.transaction() as connection:
            self._audit(
                connection,
                client_id,
                action,
                record_ids,
                trace_id=trace_id,
                denied_ids=denied_ids,
                metadata=metadata,
            )

    def list_audit(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY created_at DESC LIMIT ?",
                (min(max(limit, 1), 500),),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "client_id": cast(str | None, row["client_id"]),
                "action": str(row["action"]),
                "trace_id": str(row["trace_id"]),
                "record_ids": _loads(row["record_ids_json"], []),
                "denied_record_ids": _loads(row["denied_record_ids_json"], []),
                "metadata": _loads(row["metadata_json"], {}),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def status(self) -> dict[str, Any]:
        with self.connect() as connection:
            vault = connection.execute("SELECT * FROM vaults LIMIT 1").fetchone()
            if vault is None:
                raise InvalidStateError("Core vault is not initialized")
            counts = {
                "sources": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM source_records WHERE deleted_at IS NULL"
                    ).fetchone()[0]
                ),
                "pending_candidates": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_candidates WHERE disposition='staged'"
                    ).fetchone()[0]
                ),
                "observations": int(
                    connection.execute("SELECT COUNT(*) FROM context_candidates").fetchone()[0]
                ),
                "tentative_observations": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_candidates WHERE disposition='tentative'"
                    ).fetchone()[0]
                ),
                "approved_records": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_records WHERE deleted_at IS NULL"
                    ).fetchone()[0]
                ),
                "active_records": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_records r "
                        "WHERE r.approval_status='approved' AND r.deleted_at IS NULL "
                        "AND NOT EXISTS (SELECT 1 FROM context_records newer "
                        "WHERE newer.supersedes=r.id "
                        "AND newer.approval_status='approved' "
                        "AND newer.deleted_at IS NULL)"
                    ).fetchone()[0]
                ),
                "pending_replication_events": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM replication_events WHERE delivered_at IS NULL"
                    ).fetchone()[0]
                ),
                "open_duplicate_groups": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM integrity_groups "
                        "WHERE status='open' AND group_type='duplicate'"
                    ).fetchone()[0]
                ),
                "open_conflict_groups": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM integrity_groups "
                        "WHERE status='open' AND group_type='conflict'"
                    ).fetchone()[0]
                ),
                "pending_purge_jobs": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM purge_jobs WHERE phase='compaction_pending'"
                    ).fetchone()[0]
                ),
            }
        truth_coverage = self.memory_truth_coverage()
        return {
            "core_online": True,
            "vault_id": str(vault["id"]),
            "vault_name": str(vault["name"]),
            "schema_version": int(vault["schema_version"]),
            "database_size_bytes": durable_sqlite_footprint(self.database_path),
            "counts": counts,
            "truth_coverage": truth_coverage.model_dump(mode="json"),
        }

    def pending_replication_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM replication_events WHERE delivered_at IS NULL "
                "ORDER BY vault_id,sequence LIMIT ?",
                (min(max(limit, 1), 1_000),),
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "event_id": str(row["id"]),
                "vault_id": str(row["vault_id"]),
                "sequence": int(row["sequence"]),
                "event_type": str(row["event_type"]),
                "record_id": str(row["record_id"]),
                "payload_json": str(row["payload_json"]),
                "payload": _loads(row["payload_json"], {}),
                "payload_hash": str(row["payload_hash"]),
                "mac": cast(str | None, row["mac"]),
                "created_at": str(row["created_at"]),
            }
            for row in rows
        ]

    def mark_replication_delivered(self, event_id: str, delivered_at: str | None = None) -> None:
        with self.transaction() as connection:
            result = connection.execute(
                "UPDATE replication_events SET delivered_at=COALESCE(delivered_at,?) WHERE id=?",
                (delivered_at or utc_now(), event_id),
            )
            if result.rowcount != 1:
                raise NotFoundError("replication event not found")
