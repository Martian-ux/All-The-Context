"""Transactional SQLite storage for the authoritative local Core."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import threading
import unicodedata
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, cast

from .ids import new_id, utc_now
from .models import (
    ApprovalRequest,
    ApprovalStatus,
    Availability,
    CandidateInput,
    CandidateOut,
    ClientCreate,
    ContextRecordOut,
    CoverageReport,
    IngestionMode,
    Sensitivity,
    SourceOut,
)
from .replication import MAX_REPLICATION_PAYLOAD_BYTES
from .security import ClientPrincipal, generate_token, hash_token, verify_token


class StorageError(RuntimeError):
    """Base error suitable for transport-layer translation."""


class NotFoundError(StorageError):
    pass


class ConflictError(StorageError):
    pass


class InvalidStateError(StorageError):
    pass


PURGE_CONFIRMATION_TEMPLATE = "PURGE {target_type} {target_id}"


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


def _value_fingerprint(row: sqlite3.Row) -> str:
    structured = _loads(cast(str | None, row["structured_value_json"]), None)
    if structured is not None:
        material = "structured:" + _json(structured)
    else:
        content = unicodedata.normalize("NFKC", str(row["content"])).casefold()
        material = "content:" + " ".join(re.findall(r"\w+", content))
    return _hash_text(material)


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
                connection.executescript(migration.read_text(encoding="utf-8"))
                connection.execute(
                    "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                    (version, migration.name, utc_now()),
                )
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
            return vault_id

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
        import_status: Literal["processing", "complete", "failed"] = "complete",
    ) -> SourceOut:
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
                return self._source_out(existing, duplicate=True)
            connection.execute(
                "INSERT OR IGNORE INTO source_blobs"
                "(content_hash,content,byte_size,media_type,created_at) VALUES(?,?,?,?,?)",
                (content_hash, content, len(content), media_type, created_at),
            )
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
        import_status: Literal["processing", "complete", "failed"] = "complete",
    ) -> SourceOut:
        """Store a source from disk without materializing the complete file in memory."""
        resolved = path.expanduser().resolve()
        digest = hashlib.sha256()
        byte_size = 0
        with resolved.open("rb") as source_stream:
            while chunk := source_stream.read(1024 * 1024):
                digest.update(chunk)
                byte_size += len(chunk)
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
                return self._source_out(existing, duplicate=True)

            inserted = connection.execute(
                "INSERT OR IGNORE INTO source_blobs"
                "(content_hash,content,byte_size,media_type,created_at) "
                "VALUES(?,zeroblob(?),?,?,?)",
                (content_hash, byte_size, byte_size, media_type, created_at),
            )
            if inserted.rowcount == 1:
                blob_row = connection.execute(
                    "SELECT rowid FROM source_blobs WHERE content_hash=?", (content_hash,)
                ).fetchone()
                if blob_row is None:  # pragma: no cover - protected by the insert above
                    raise InvalidStateError("source blob could not be allocated")
                written_digest = hashlib.sha256()
                written_size = 0
                with (
                    resolved.open("rb") as source_stream,
                    connection.blobopen(
                        "source_blobs", "content", int(blob_row["rowid"]), readonly=False
                    ) as blob,
                ):
                    while chunk := source_stream.read(1024 * 1024):
                        blob.write(chunk)
                        written_digest.update(chunk)
                        written_size += len(chunk)
                if written_size != byte_size or written_digest.hexdigest() != content_hash:
                    raise InvalidStateError("source file changed while it was being imported")

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
                cast(Literal["processing", "complete", "failed"], row["import_status"])
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
        )

    def get_source(self, source_id: str, *, duplicate: bool = False) -> SourceOut:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT sr.*,sb.byte_size,sb.media_type,"
                "(SELECT COUNT(*) FROM context_candidates cc WHERE cc.source_id=sr.id) "
                "AS candidate_count FROM source_records sr "
                "JOIN source_blobs sb ON sb.content_hash=sr.content_hash WHERE sr.id=?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("source not found")
        return self._source_out(row, duplicate=duplicate)

    def update_source_import(
        self,
        source_id: str,
        *,
        import_status: Literal["processing", "complete", "failed"],
        metadata: Mapping[str, Any],
        parser_warnings: Sequence[str],
    ) -> None:
        with self.transaction() as connection:
            result = connection.execute(
                "UPDATE source_records SET import_status=?,metadata_json=?,"
                "parser_warnings_json=? WHERE id=?",
                (
                    import_status,
                    _json(dict(metadata)),
                    _json(list(parser_warnings)[:512]),
                    source_id,
                ),
            )
            if result.rowcount != 1:
                raise NotFoundError("source not found")

    def get_source_content(self, source_id: str) -> bytes:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT sb.content FROM source_records sr JOIN source_blobs sb "
                "ON sb.content_hash=sr.content_hash WHERE sr.id=?",
                (source_id,),
            ).fetchone()
        if row is None:
            raise NotFoundError("source not found")
        return bytes(row["content"])

    def copy_source_content_to_path(self, source_id: str, destination: Path) -> int:
        """Copy a raw source blob to a caller-owned path using bounded memory."""
        target = destination.expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT sb.rowid,sb.byte_size FROM source_records sr JOIN source_blobs sb "
                "ON sb.content_hash=sr.content_hash WHERE sr.id=?",
                (source_id,),
            ).fetchone()
            if row is None:
                raise NotFoundError("source not found")
            written = 0
            with (
                connection.blobopen(
                    "source_blobs", "content", int(row["rowid"]), readonly=True
                ) as blob,
                target.open("wb") as output,
            ):
                while chunk := blob.read(1024 * 1024):
                    output.write(chunk)
                    written += len(chunk)
        if written != int(row["byte_size"]):
            raise InvalidStateError("stored source blob was truncated")
        return written

    def list_sources(
        self, *, limit: int = 100, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int]:
        with self.connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0])
            rows = connection.execute(
                "SELECT sr.*,sb.byte_size,sb.media_type,"
                "(SELECT COUNT(*) FROM context_candidates cc WHERE cc.source_id=sr.id) "
                "AS candidate_count FROM source_records sr "
                "JOIN source_blobs sb ON sb.content_hash=sr.content_hash "
                "ORDER BY sr.created_at DESC LIMIT ? OFFSET ?",
                (min(max(limit, 1), 500), max(offset, 0)),
            ).fetchall()
        return [
            {
                **self._source_out(row).model_dump(mode="json"),
            }
            for row in rows
        ], total

    def candidate_ids_for_source(self, source_id: str) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM context_candidates WHERE source_id=? ORDER BY created_at",
                (source_id,),
            ).fetchall()
        return [str(row["id"]) for row in rows]

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
        canonical_request = _json([candidate.model_dump(mode="json") for candidate in candidates])
        request_hash = _hash_text(canonical_request)
        created_ids: list[str] = []
        with self.transaction() as connection:
            session = connection.execute(
                "SELECT * FROM ingestion_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None:
                raise NotFoundError("ingestion session not found")
            existing = connection.execute(
                "SELECT * FROM ingestion_batches WHERE session_id=? AND idempotency_key=?",
                (session_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["request_hash"]) != request_hash:
                    raise ConflictError("idempotency key was reused with different content")
                return {
                    "batch_id": str(existing["id"]),
                    "candidate_ids": _loads(existing["candidate_ids_json"], []),
                    "replayed": True,
                }
            if str(session["status"]) != "open":
                raise InvalidStateError("ingestion session is already finished")
            for candidate in candidates:
                created_ids.append(
                    self._insert_candidate(connection, candidate, session_id, client)
                )
            batch_id = new_id()
            connection.execute(
                "INSERT INTO ingestion_batches"
                "(id,session_id,idempotency_key,request_hash,candidate_ids_json,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    batch_id,
                    session_id,
                    idempotency_key,
                    request_hash,
                    _json(created_ids),
                    utc_now(),
                ),
            )
            connection.execute(
                "UPDATE ingestion_sessions SET candidate_count=candidate_count+? WHERE id=?",
                (len(created_ids), session_id),
            )
        if client is not None and client.auto_approve:
            for candidate_id, candidate in zip(created_ids, candidates, strict=True):
                if (
                    candidate.explicit_user_statement
                    and candidate.sensitivity == Sensitivity.NORMAL
                    and candidate.availability != Availability.ALWAYS
                ):
                    self.approve_candidate(
                        candidate_id,
                        ApprovalRequest(reason="client auto-approval policy"),
                        actor=client.id,
                    )
        return {"batch_id": batch_id, "candidate_ids": created_ids, "replayed": False}

    def add_candidate(
        self,
        candidate: CandidateInput,
        *,
        session_id: str | None = None,
        client: ClientPrincipal | None = None,
    ) -> CandidateOut:
        with self.transaction() as connection:
            candidate_id = self._insert_candidate(connection, candidate, session_id, client)
        if (
            client is not None
            and client.auto_approve
            and candidate.explicit_user_statement
            and candidate.sensitivity == Sensitivity.NORMAL
            and candidate.availability != Availability.ALWAYS
        ):
            self.approve_candidate(
                candidate_id,
                ApprovalRequest(reason="client auto-approval policy"),
                actor=client.id,
            )
        return self.get_candidate(candidate_id)

    def add_edge_candidate(
        self,
        proposal_id: str,
        candidate: CandidateInput,
    ) -> tuple[CandidateOut, bool]:
        """Import one Edge proposal exactly once, including across failed remote ACKs."""

        if not proposal_id or len(proposal_id) > 256:
            raise InvalidStateError("Edge proposal ID is invalid")
        proposal_hash = _hash_text(_json(candidate.model_dump(mode="json")))
        vault_id = self.vault_id()
        replayed = False
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
        connection.execute(
            "INSERT INTO context_candidates"
            "(id,vault_id,session_id,source_id,source_reference,submitted_by_client_id,kind,content,"
            "structured_value_json,entity_key,attribute_key,scopes_json,tags_json,source_service,source_type,evidence,"
            "confidence,sensitivity,availability,allowed_clients_json,denied_clients_json,"
            "valid_from,expires_at,supersedes,explicit_user_statement,idempotency_key,approval_status,"
            "content_hash,schema_version,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                utc_now(),
            ),
        )
        return candidate_id

    def finish_ingestion(self, session_id: str, coverage: CoverageReport) -> dict[str, Any]:
        finished_at = utc_now()
        with self.transaction() as connection:
            session = connection.execute(
                "SELECT * FROM ingestion_sessions WHERE id=?", (session_id,)
            ).fetchone()
            if session is None:
                raise NotFoundError("ingestion session not found")
            coverage_json = _json(coverage.model_dump(mode="json"))
            if str(session["status"]) == "finished":
                if str(session["coverage_json"]) != coverage_json:
                    raise ConflictError("finished session has a different coverage report")
                return self._session_mapping(session, replayed=True)
            connection.execute(
                "UPDATE ingestion_sessions SET status='finished', coverage_json=?, finished_at=? "
                "WHERE id=?",
                (coverage_json, finished_at, session_id),
            )
        return self.get_session(session_id)

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
        )

    def approve_candidate(
        self,
        candidate_id: str,
        request: ApprovalRequest | None = None,
        *,
        actor: str = "local-user",
    ) -> ContextRecordOut:
        request = request or ApprovalRequest()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM context_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("candidate not found")
            if str(row["approval_status"]) == ApprovalStatus.APPROVED.value:
                record = connection.execute(
                    "SELECT * FROM context_records WHERE candidate_id=?", (candidate_id,)
                ).fetchone()
                if record is None:
                    raise InvalidStateError("approved candidate has no canonical record")
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
                "(id,vault_id,candidate_id,source_id,source_reference,kind,content,structured_value_json,"
                "entity_key,attribute_key,scopes_json,tags_json,source_service,source_type,evidence,confidence,sensitivity,"
                "availability,allowed_clients_json,denied_clients_json,valid_from,expires_at,"
                "supersedes,explicit_user_statement,approval_status,version,content_hash,"
                "schema_version,created_at,updated_at) VALUES(" + ",".join("?" * 30) + ")",
                record_values,
            )
            connection.execute(
                "UPDATE context_candidates SET approval_status=?,reviewed_at=?,reviewed_by=?,"
                "review_reason=? WHERE id=?",
                (ApprovalStatus.APPROVED.value, now, actor, request.reason, candidate_id),
            )
            record = connection.execute(
                "SELECT * FROM context_records WHERE id=?", (record_id,)
            ).fetchone()
            assert record is not None
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
                "review_reason=? WHERE id=?",
                (ApprovalStatus.REJECTED.value, now, actor, reason, candidate_id),
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
            valid_from=cast(str | None, row["valid_from"]),
            expires_at=cast(str | None, row["expires_at"]),
            supersedes=cast(str | None, row["supersedes"]),
            explicit_user_statement=bool(row["explicit_user_statement"]),
            version=int(row["version"]),
            content_hash=str(row["content_hash"]),
            schema_version=int(row["schema_version"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
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
                "entity_key=?,attribute_key=?,content_hash=?,version=?,updated_at=? WHERE id=?",
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
                    record_id,
                ),
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
            now = utc_now()
            version = int(record["version"]) + 1
            tombstone_hash = _hash_text(f"{record_id}:{version}:{reason}:{now}")
            connection.execute(
                "UPDATE context_records SET deleted_at=?,updated_at=?,version=? WHERE id=?",
                (now, now, version, record_id),
            )
            connection.execute("DELETE FROM context_fts WHERE record_id=?", (record_id,))
            connection.execute(
                "INSERT INTO deletion_tombstones"
                "(record_id,vault_id,deleted_version,reason,content_hash,deleted_at) "
                "VALUES(?,?,?,?,?,?)",
                (record_id, record["vault_id"], version, reason, tombstone_hash, now),
            )
            self._emit_event(
                connection,
                record,
                "record_deleted",
                {"record_id": record_id, "version": version, "deleted_at": now},
            )
            self._recompute_integrity(connection)
            self._audit(connection, actor, "record_deleted", [record_id])
            return {
                "record_id": record_id,
                "deleted_version": version,
                "reason": reason,
                "content_hash": tombstone_hash,
                "deleted_at": now,
            }

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
        if candidate_id is not None:
            self._detach_candidate_from_batches(connection, candidate_id)
            connection.execute(
                "DELETE FROM edge_proposal_receipts WHERE candidate_id=?", (candidate_id,)
            )
        connection.execute("DELETE FROM context_records WHERE id=?", (record_id,))
        if candidate_id is not None:
            connection.execute("DELETE FROM context_candidates WHERE id=?", (candidate_id,))
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
                    connection.execute("SELECT COUNT(*) FROM source_records").fetchone()[0]
                ),
                "pending_candidates": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_candidates WHERE approval_status='pending'"
                    ).fetchone()[0]
                ),
                "approved_records": int(
                    connection.execute(
                        "SELECT COUNT(*) FROM context_records WHERE deleted_at IS NULL"
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
        return {
            "core_online": True,
            "vault_id": str(vault["id"]),
            "vault_name": str(vault["name"]),
            "schema_version": int(vault["schema_version"]),
            "database_size_bytes": durable_sqlite_footprint(self.database_path),
            "counts": counts,
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
