"""Relay application service and SQLite persistence adapter."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from allthecontext.replication import (
    EventType,
    JsonValue,
    ReplicationEvent,
    canonical_json,
    verify_event,
)


class RelayError(RuntimeError):
    """Base class for Relay application errors."""


class EdgeDecommissionedError(RelayError):
    """A write reached the durable store after Edge became terminal."""


class EventSequenceError(RelayError):
    def __init__(self, *, vault_id: str, expected: int, received: int) -> None:
        super().__init__(
            f"replication gap for vault {vault_id!r}: expected sequence {expected}, "
            f"received {received}"
        )
        self.vault_id = vault_id
        self.expected = expected
        self.received = received


class ReplayMismatchError(RelayError):
    """A sequence or event ID was replayed with different content."""


class InvalidEventPayloadError(RelayError):
    """A validly signed event requests an invalid Relay state transition."""


class AuthorizationError(RelayError):
    """The authenticated client lacks a required permission."""


class ProposalConflictError(RelayError):
    """An idempotency key was reused for a different proposal."""


@dataclass(frozen=True, slots=True)
class ApplyResult:
    vault_id: str
    sequence: int
    event_id: str
    replayed: bool


@dataclass(frozen=True, slots=True)
class ClientIdentity:
    """Identity established by the HTTP authentication layer.

    Permissions name operations (``context:read`` and ``proposal:write``).
    ``context_scopes`` limits which record scopes that operation may access;
    ``*`` grants every record scope in the identity's vault.
    """

    client_id: str
    vault_id: str
    permissions: frozenset[str] = field(default_factory=frozenset)
    context_scopes: frozenset[str] = field(default_factory=frozenset)


class RelayStore(Protocol):
    """Persistence boundary; SQLite is the exercised v1 implementation."""

    def apply_event(self, event: ReplicationEvent) -> ApplyResult: ...

    def get_record_row(self, vault_id: str, record_id: str) -> Mapping[str, Any] | None: ...

    def search_record_rows(
        self,
        vault_id: str,
        query: str,
        candidate_limit: int | None,
        kinds: Sequence[str] | None = None,
        candidate_offset: int = 0,
    ) -> Sequence[Mapping[str, Any]]: ...

    def checkpoint(self, vault_id: str) -> int: ...

    def resume_purge_compaction(self) -> Mapping[str, Any]: ...

    def purge_compaction_status(self) -> Mapping[str, Any]: ...

    def configure_proposal_protection(self, replication_secret: bytes) -> None: ...

    def queue_proposal(
        self,
        *,
        identity: ClientIdentity,
        idempotency_key: str,
        proposal: Mapping[str, JsonValue],
    ) -> tuple[Mapping[str, Any], bool]: ...

    def list_proposals(
        self, vault_id: str, *, status: str, limit: int
    ) -> Sequence[Mapping[str, Any]]: ...

    def update_proposal_status(self, vault_id: str, proposal_id: str, status: str) -> bool: ...

    def purge_vault(self, vault_id: str) -> None: ...

    def purge_all(self) -> int: ...

    def close(self) -> None: ...


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


_PROPOSAL_KEY_DOMAIN = b"all-the-context/edge/proposal-envelope/v1"
_MAX_QUEUED_PROPOSALS = 1_000
_MAX_QUEUED_PROPOSAL_BYTES = 16 * 1024 * 1024
_PROPOSAL_RETENTION_DAYS = 30


def _proposal_key(replication_secret: bytes) -> bytes:
    return hmac.new(replication_secret, _PROPOSAL_KEY_DOMAIN, hashlib.sha256).digest()


def _proposal_aad(
    *, vault_id: str, client_id: str, idempotency_key: str, proposal_hash: str
) -> bytes:
    return canonical_json(
        {
            "client_id": client_id,
            "idempotency_key": idempotency_key,
            "proposal_hash": proposal_hash,
            "vault_id": vault_id,
        }
    ).encode("utf-8")


def _seal_proposal(payload: Mapping[str, Any], *, key: bytes, aad: bytes) -> str:
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, canonical_json(payload).encode("utf-8"), aad)
    return canonical_json(
        {
            "algorithm": "A256GCM",
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii"),
            "nonce": base64.urlsafe_b64encode(nonce).decode("ascii"),
            "version": 1,
        }
    )


def _open_proposal(envelope_json: str, *, key: bytes, aad: bytes) -> dict[str, Any]:
    try:
        envelope = json.loads(envelope_json)
        if not isinstance(envelope, dict) or set(envelope) != {
            "algorithm",
            "ciphertext",
            "nonce",
            "version",
        }:
            raise ValueError("unexpected envelope fields")
        if envelope["algorithm"] != "A256GCM" or envelope["version"] != 1:
            raise ValueError("unsupported envelope")
        nonce = base64.b64decode(str(envelope["nonce"]), altchars=b"-_", validate=True)
        ciphertext = base64.b64decode(str(envelope["ciphertext"]), altchars=b"-_", validate=True)
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, aad)
        proposal = json.loads(plaintext)
    except (InvalidTag, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise RelayError("stored Edge proposal envelope is invalid") from exc
    if not isinstance(proposal, dict):
        raise RelayError("stored Edge proposal envelope is invalid")
    return proposal


def _as_json_object(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    # Round-tripping through the canonical serializer performs strict JSON
    # validation and detaches caller-owned mutable containers.
    parsed = json.loads(canonical_json(value))
    if not isinstance(parsed, dict):  # pragma: no cover - Mapping always produces an object
        raise InvalidEventPayloadError("payload must be an object")
    return parsed


def _required_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidEventPayloadError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _required_string(value, field_name)


def _string_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if not isinstance(value, list):
        raise InvalidEventPayloadError(f"{field_name} must be a string or list of strings")
    if any(not isinstance(item, str) or not item for item in value):
        raise InvalidEventPayloadError(f"{field_name} contains an invalid value")
    # Keep wire order deterministic while removing duplicates.
    return list(dict.fromkeys(value))


def _parse_time(value: object, field_name: str) -> str | None:
    raw = _optional_string(value, field_name)
    if raw is None:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidEventPayloadError(f"{field_name} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise InvalidEventPayloadError(f"{field_name} must include a UTC offset")
    return parsed.astimezone(UTC).isoformat()


def _unwrap_record_payload(event: ReplicationEvent) -> dict[str, JsonValue]:
    payload = _as_json_object(event.payload)
    nested = payload.get("record")
    if nested is not None:
        if not isinstance(nested, dict):
            raise InvalidEventPayloadError("record must be an object")
        return nested
    return payload


def _normalize_upsert(event: ReplicationEvent) -> dict[str, Any]:
    record = _unwrap_record_payload(event)
    payload_record_id = record.get("record_id", record.get("id", event.record_id))
    if payload_record_id != event.record_id:
        raise InvalidEventPayloadError("payload record ID does not match event record ID")
    availability = _required_string(record.get("availability"), "availability")
    if availability != "always_available":
        raise InvalidEventPayloadError("Relay accepts only always_available records")
    approval_status = _required_string(record.get("approval_status"), "approval_status")
    if approval_status != "approved":
        raise InvalidEventPayloadError("Relay accepts only approved records")
    version = record.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise InvalidEventPayloadError("version must be a positive integer")
    confidence_value = record.get("confidence")
    confidence: float | None
    if confidence_value is None:
        confidence = None
    elif isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
        raise InvalidEventPayloadError("confidence must be a number between 0 and 1")
    else:
        confidence = float(confidence_value)
        if not 0 <= confidence <= 1:
            raise InvalidEventPayloadError("confidence must be a number between 0 and 1")
    content = _required_string(record.get("content"), "content")
    calculated_content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    claimed_content_hash = record.get("content_hash")
    if claimed_content_hash is None:
        content_hash = calculated_content_hash
    else:
        content_hash = _required_string(claimed_content_hash, "content_hash").lower()
        if len(content_hash) != 64:
            raise InvalidEventPayloadError("content_hash must be a SHA-256 hex digest")
        try:
            bytes.fromhex(content_hash)
        except ValueError as exc:
            raise InvalidEventPayloadError("content_hash must be hexadecimal") from exc
        if content_hash != calculated_content_hash:
            raise InvalidEventPayloadError("content_hash does not match content")
    valid_from = _parse_time(record.get("valid_from"), "valid_from")
    valid_until = _parse_time(record.get("valid_until", record.get("expires_at")), "valid_until")
    if valid_from is not None and valid_until is not None and valid_until <= valid_from:
        raise InvalidEventPayloadError("valid_until must be later than valid_from")
    provenance = record.get("provenance")
    if provenance is None:
        provenance = {
            "source_record_id": record.get("source_record_id"),
            "source_reference": record.get("source_reference"),
        }
    if not isinstance(provenance, (dict, list, str)):
        raise InvalidEventPayloadError("provenance must be JSON object, list, or string")
    supersedes = _optional_string(record.get("supersedes"), "supersedes")
    if supersedes == event.record_id:
        raise InvalidEventPayloadError("a record cannot supersede itself")
    sensitivity = _required_string(record.get("sensitivity"), "sensitivity")
    if sensitivity == "private":
        sensitivity = "sensitive"
    if sensitivity not in {"normal", "sensitive", "highly_sensitive"}:
        raise InvalidEventPayloadError("invalid sensitivity")
    return {
        "record_id": event.record_id,
        "kind": _required_string(record.get("kind"), "kind"),
        "content": content,
        "scope": _string_list(record.get("scope", record.get("scopes")), "scope"),
        "provenance": provenance,
        "source_service": _optional_string(record.get("source_service"), "source_service"),
        "confidence": confidence,
        "sensitivity": sensitivity,
        "availability": availability,
        "allowed_clients": _string_list(record.get("allowed_clients"), "allowed_clients"),
        "denied_clients": _string_list(record.get("denied_clients"), "denied_clients"),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "version": version,
        "supersedes": supersedes,
        "approval_status": approval_status,
        "content_hash": content_hash,
        "updated_at": _parse_time(record.get("updated_at"), "updated_at") or event.created_at,
        "payload": record,
    }


class SQLiteRelayStore:
    """Single-process SQLite Relay storage with transactional event apply."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self.database_path,
            timeout=30,
            check_same_thread=False,
            isolation_level=None,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.execute("PRAGMA secure_delete = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._proposal_encryption_key: bytes | None = None
        self._migrate()

    def _migrate(self) -> None:
        migration_dir = Path(__file__).parent.parent / "migrations" / "relay"
        with self._lock:
            self._connection.execute(
                "CREATE TABLE IF NOT EXISTS relay_schema_migrations "
                "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                str(row[0])
                for row in self._connection.execute("SELECT name FROM relay_schema_migrations")
            }
            for migration_path in sorted(migration_dir.glob("*.sql"), key=lambda path: path.name):
                if migration_path.name in applied:
                    continue
                sql = migration_path.read_text(encoding="utf-8")
                # Migrations run during exclusive service initialization; each
                # script is idempotent so interrupted initialization is safe.
                self._connection.executescript(sql)
                self._connection.execute(
                    "INSERT OR IGNORE INTO relay_schema_migrations(name, applied_at) VALUES (?, ?)",
                    (migration_path.name, _utc_now()),
                )

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    @staticmethod
    def _require_active_edge(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT decommissioned_at FROM edge_instance_state WHERE singleton=1"
        ).fetchone()
        if row is not None and row[0] is not None:
            raise EdgeDecommissionedError("Edge is decommissioned")

    def apply_event(self, event: ReplicationEvent) -> ApplyResult:
        fingerprint = event.fingerprint()
        applied_at = _utc_now()
        with self._transaction() as connection:
            self._require_active_edge(connection)
            checkpoint_row = connection.execute(
                "SELECT last_sequence FROM replication_checkpoints WHERE vault_id = ?",
                (event.vault_id,),
            ).fetchone()
            last_sequence = int(checkpoint_row[0]) if checkpoint_row is not None else 0
            expected = last_sequence + 1
            if event.sequence <= last_sequence:
                previous = connection.execute(
                    "SELECT event_id, event_fingerprint FROM applied_replication_events "
                    "WHERE vault_id = ? AND sequence = ?",
                    (event.vault_id, event.sequence),
                ).fetchone()
                if (
                    previous is not None
                    and str(previous[0]) == event.event_id
                    and str(previous[1]) == fingerprint
                ):
                    return ApplyResult(
                        event.vault_id, event.sequence, event.event_id, replayed=True
                    )
                raise ReplayMismatchError(
                    f"sequence {event.sequence} was already applied with different content"
                )
            if event.sequence != expected:
                raise EventSequenceError(
                    vault_id=event.vault_id,
                    expected=expected,
                    received=event.sequence,
                )
            duplicate_id = connection.execute(
                "SELECT vault_id, sequence FROM applied_replication_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if duplicate_id is not None:
                raise ReplayMismatchError("event_id was already used at another stream position")

            if event.event_type is EventType.RECORD_UPSERTED:
                self._apply_upsert(connection, event)
            elif event.event_type is EventType.RECORD_WITHDRAWN:
                self._reject_transition_after_purge(connection, event)
                self._remove_record(connection, event.vault_id, event.record_id)
            elif event.event_type is EventType.RECORD_DELETED:
                self._reject_transition_after_purge(connection, event)
                self._apply_deletion(connection, event)
            elif event.event_type is EventType.RECORD_PURGED:
                self._apply_purge(connection, event)
            else:  # pragma: no cover - EventType parsing closes this branch
                raise InvalidEventPayloadError(f"unsupported event type {event.event_type}")

            connection.execute(
                "INSERT INTO applied_replication_events "
                "(vault_id, sequence, event_id, event_type, record_id, payload_hash, "
                "event_fingerprint, applied_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    event.vault_id,
                    event.sequence,
                    event.event_id,
                    event.event_type.value,
                    event.record_id,
                    event.payload_hash,
                    fingerprint,
                    applied_at,
                ),
            )
            connection.execute(
                "INSERT INTO replication_checkpoints "
                "(vault_id, last_sequence, last_event_id, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(vault_id) DO UPDATE SET "
                "last_sequence = excluded.last_sequence, "
                "last_event_id = excluded.last_event_id, updated_at = excluded.updated_at",
                (event.vault_id, event.sequence, event.event_id, applied_at),
            )
        return ApplyResult(event.vault_id, event.sequence, event.event_id, replayed=False)

    def _apply_upsert(self, connection: sqlite3.Connection, event: ReplicationEvent) -> None:
        self._reject_transition_after_purge(connection, event)
        record = _normalize_upsert(event)
        tombstone = connection.execute(
            "SELECT 1 FROM relay_deletion_tombstones WHERE vault_id = ? AND record_id = ?",
            (event.vault_id, event.record_id),
        ).fetchone()
        if tombstone is not None:
            raise InvalidEventPayloadError("a deleted stable record ID cannot be resurrected")
        existing = connection.execute(
            "SELECT version FROM relay_context_records WHERE vault_id = ? AND record_id = ?",
            (event.vault_id, event.record_id),
        ).fetchone()
        if existing is not None and int(existing[0]) > int(record["version"]):
            raise InvalidEventPayloadError("record version cannot decrease")

        supersedes = record["supersedes"]
        if supersedes is not None:
            connection.execute(
                "UPDATE relay_context_records SET superseded_by = ? "
                "WHERE vault_id = ? AND record_id = ? AND superseded_by IS NULL",
                (event.record_id, event.vault_id, supersedes),
            )
        connection.execute(
            "DELETE FROM relay_context_fts WHERE vault_id = ? AND record_id = ?",
            (event.vault_id, event.record_id),
        )
        connection.execute(
            "INSERT INTO relay_context_records "
            "(vault_id, record_id, kind, content, scope_json, provenance_json, source_service, "
            "confidence, sensitivity, availability, allowed_clients_json, denied_clients_json, "
            "valid_from, valid_until, version, supersedes, superseded_by, approval_status, "
            "content_hash, updated_at, event_sequence, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?) "
            "ON CONFLICT(vault_id, record_id) DO UPDATE SET "
            "kind = excluded.kind, content = excluded.content, scope_json = excluded.scope_json, "
            "provenance_json = excluded.provenance_json, source_service = excluded.source_service, "
            "confidence = excluded.confidence, sensitivity = excluded.sensitivity, "
            "availability = excluded.availability, "
            "allowed_clients_json = excluded.allowed_clients_json, "
            "denied_clients_json = excluded.denied_clients_json, valid_from = excluded.valid_from, "
            "valid_until = excluded.valid_until, version = excluded.version, "
            "supersedes = excluded.supersedes, approval_status = excluded.approval_status, "
            "content_hash = excluded.content_hash, updated_at = excluded.updated_at, "
            "event_sequence = excluded.event_sequence, payload_json = excluded.payload_json",
            (
                event.vault_id,
                event.record_id,
                record["kind"],
                record["content"],
                canonical_json(record["scope"]),
                canonical_json(record["provenance"]),
                record["source_service"],
                record["confidence"],
                record["sensitivity"],
                record["availability"],
                canonical_json(record["allowed_clients"]),
                canonical_json(record["denied_clients"]),
                record["valid_from"],
                record["valid_until"],
                record["version"],
                record["supersedes"],
                record["approval_status"],
                record["content_hash"],
                record["updated_at"],
                event.sequence,
                canonical_json(record["payload"]),
            ),
        )
        connection.execute(
            "INSERT INTO relay_context_fts(vault_id, record_id, kind, content) VALUES (?, ?, ?, ?)",
            (event.vault_id, event.record_id, record["kind"], record["content"]),
        )

    @staticmethod
    def _remove_record(connection: sqlite3.Connection, vault_id: str, record_id: str) -> None:
        connection.execute(
            "DELETE FROM relay_context_fts WHERE vault_id = ? AND record_id = ?",
            (vault_id, record_id),
        )
        connection.execute(
            "DELETE FROM relay_context_records WHERE vault_id = ? AND record_id = ?",
            (vault_id, record_id),
        )

    def _apply_deletion(self, connection: sqlite3.Connection, event: ReplicationEvent) -> None:
        self._remove_record(connection, event.vault_id, event.record_id)
        payload = _as_json_object(event.payload)
        deleted_at = _parse_time(payload.get("deleted_at"), "deleted_at") or event.created_at
        version_value = payload.get("version")
        if version_value is not None and (
            isinstance(version_value, bool)
            or not isinstance(version_value, int)
            or version_value < 1
        ):
            raise InvalidEventPayloadError("deletion version must be a positive integer")
        content_hash = _optional_string(payload.get("content_hash"), "content_hash")
        connection.execute(
            "INSERT INTO relay_deletion_tombstones "
            "(vault_id, record_id, deleted_at, version, content_hash, "
            "event_sequence, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(vault_id, record_id) DO UPDATE SET "
            "deleted_at = excluded.deleted_at, version = excluded.version, "
            "content_hash = excluded.content_hash, event_sequence = excluded.event_sequence, "
            "payload_json = excluded.payload_json",
            (
                event.vault_id,
                event.record_id,
                deleted_at,
                version_value,
                content_hash,
                event.sequence,
                canonical_json(payload),
            ),
        )

    @staticmethod
    def _reject_transition_after_purge(
        connection: sqlite3.Connection, event: ReplicationEvent
    ) -> None:
        tombstone = connection.execute(
            "SELECT 1 FROM relay_purge_tombstones WHERE vault_id=? AND record_id=?",
            (event.vault_id, event.record_id),
        ).fetchone()
        if tombstone is not None:
            raise InvalidEventPayloadError("a purged stable record ID cannot transition")

    def _apply_purge(self, connection: sqlite3.Connection, event: ReplicationEvent) -> None:
        payload = _as_json_object(event.payload)
        purged_at = _parse_time(payload.get("purged_at"), "purged_at")
        if purged_at is None:  # ReplicationEvent already requires this field.
            raise InvalidEventPayloadError("purged_at is required")
        purge_scope = _required_string(payload.get("purge_scope"), "purge_scope")
        if purge_scope not in {"record", "source"} or payload.get("irreversible") is not True:
            raise InvalidEventPayloadError("invalid irreversible purge payload")

        previous = connection.execute(
            "SELECT supersedes FROM relay_context_records WHERE vault_id=? AND record_id=?",
            (event.vault_id, event.record_id),
        ).fetchone()
        self._remove_record(connection, event.vault_id, event.record_id)
        if previous is not None and previous[0] is not None:
            connection.execute(
                "UPDATE relay_context_records SET superseded_by=NULL "
                "WHERE vault_id=? AND record_id=? AND superseded_by=?",
                (event.vault_id, str(previous[0]), event.record_id),
            )
        connection.execute(
            "UPDATE relay_context_records SET supersedes=NULL WHERE vault_id=? AND supersedes=?",
            (event.vault_id, event.record_id),
        )
        connection.execute(
            "DELETE FROM relay_deletion_tombstones WHERE vault_id=? AND record_id=?",
            (event.vault_id, event.record_id),
        )
        # Historical event fingerprints and payload hashes can remain useful
        # guess or correlation oracles after the content itself is gone. The
        # stream checkpoint preserves ordering without retaining those rows.
        connection.execute(
            "DELETE FROM applied_replication_events WHERE vault_id=? AND record_id=?",
            (event.vault_id, event.record_id),
        )
        connection.execute(
            "INSERT INTO relay_purge_tombstones"
            "(vault_id,record_id,purge_scope,purged_at,event_sequence,event_id) "
            "VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(vault_id,record_id) DO UPDATE SET "
            "purge_scope=excluded.purge_scope,purged_at=excluded.purged_at,"
            "event_sequence=excluded.event_sequence,event_id=excluded.event_id",
            (
                event.vault_id,
                event.record_id,
                purge_scope,
                purged_at,
                event.sequence,
                event.event_id,
            ),
        )
        connection.execute(
            "UPDATE relay_purge_compaction_state SET pending=1,requested_at=?,"
            "last_error_code=NULL WHERE singleton=1",
            (purged_at,),
        )

    def get_record_row(self, vault_id: str, record_id: str) -> Mapping[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM relay_context_records "
                "WHERE vault_id = ? AND record_id = ? AND superseded_by IS NULL",
                (vault_id, record_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def search_record_rows(
        self,
        vault_id: str,
        query: str,
        candidate_limit: int | None,
        kinds: Sequence[str] | None = None,
        candidate_offset: int = 0,
    ) -> Sequence[Mapping[str, Any]]:
        kind_filter = tuple(dict.fromkeys(kinds or ()))
        kind_sql = ""
        kind_parameters: list[Any] = []
        if kind_filter:
            kind_sql = f" AND records.kind IN ({','.join('?' for _ in kind_filter)})"
            kind_parameters.extend(kind_filter)
        limit_sql = " LIMIT ? OFFSET ?" if candidate_limit is not None else ""
        limit_parameters = (
            [candidate_limit, max(0, candidate_offset)] if candidate_limit is not None else []
        )
        with self._lock:
            if query.strip():
                terms = re.findall(r"\w+", query, flags=re.UNICODE)
                if not terms:
                    return []
                match_query = " OR ".join(
                    f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms
                )
                rows = self._connection.execute(
                    "SELECT records.*, bm25(relay_context_fts) AS search_rank "
                    "FROM relay_context_fts "
                    "JOIN relay_context_records AS records "
                    "ON records.vault_id = relay_context_fts.vault_id "
                    "AND records.record_id = relay_context_fts.record_id "
                    "WHERE relay_context_fts MATCH ? AND records.vault_id = ? "
                    "AND records.superseded_by IS NULL " + kind_sql + " "
                    "ORDER BY search_rank, records.event_sequence DESC" + limit_sql,
                    (match_query, vault_id, *kind_parameters, *limit_parameters),
                ).fetchall()
            else:
                plain_kind_sql = kind_sql.replace("records.kind", "kind")
                rows = self._connection.execute(
                    "SELECT *, 0.0 AS search_rank FROM relay_context_records "
                    "WHERE vault_id = ? AND superseded_by IS NULL " + plain_kind_sql + " "
                    "ORDER BY event_sequence DESC" + limit_sql,
                    (vault_id, *kind_parameters, *limit_parameters),
                ).fetchall()
        return [dict(row) for row in rows]

    def checkpoint(self, vault_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT last_sequence FROM replication_checkpoints WHERE vault_id = ?",
                (vault_id,),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def purge_compaction_status(self) -> dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                "SELECT pending,requested_at,completed_at,last_error_code "
                "FROM relay_purge_compaction_state WHERE singleton=1"
            ).fetchone()
        if row is None:
            return {
                "pending": False,
                "requested_at": None,
                "completed_at": None,
                "last_error_code": None,
            }
        return {
            "pending": bool(row[0]),
            "requested_at": row[1],
            "completed_at": row[2],
            "last_error_code": row[3],
        }

    def _compact_after_purge(self) -> None:
        checkpoint = self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is not None and int(checkpoint[0]) != 0:
            raise sqlite3.OperationalError("database is busy")
        self._connection.execute("VACUUM")
        checkpoint = self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is not None and int(checkpoint[0]) != 0:
            raise sqlite3.OperationalError("database is busy")

    def resume_purge_compaction(self) -> dict[str, Any]:
        """Compact all logically purged rows once, safely retrying after crashes."""

        with self._lock:
            row = self._connection.execute(
                "SELECT pending FROM relay_purge_compaction_state WHERE singleton=1"
            ).fetchone()
            if row is None or not bool(row[0]):
                return self.purge_compaction_status()
            try:
                self._compact_after_purge()
                self._connection.execute(
                    "UPDATE relay_purge_compaction_state SET pending=0,completed_at=?,"
                    "last_error_code=NULL WHERE singleton=1",
                    (_utc_now(),),
                )
            except sqlite3.Error as exc:
                detail = str(exc).casefold()
                error_code = (
                    "database_busy"
                    if "busy" in detail or "locked" in detail
                    else "compaction_failed"
                )
                with suppress(sqlite3.Error):
                    self._connection.execute(
                        "UPDATE relay_purge_compaction_state SET last_error_code=? "
                        "WHERE singleton=1",
                        (error_code,),
                    )
            return self.purge_compaction_status()

    def configure_proposal_protection(self, replication_secret: bytes) -> None:
        """Encrypt legacy queued proposals and scrub completed proposal payloads."""

        if len(replication_secret) < 32:
            raise ValueError("replication_secret must contain at least 32 bytes")
        key = _proposal_key(replication_secret)
        with self._transaction() as connection:
            terminal = connection.execute(
                "SELECT decommissioned_at FROM edge_instance_state WHERE singleton=1"
            ).fetchone()
            if terminal is not None and terminal[0] is not None:
                self._proposal_encryption_key = key
                return
            self._purge_expired_proposals(connection)
            rows = connection.execute("SELECT * FROM pending_memory_proposals").fetchall()
            for row in rows:
                if str(row["status"]) != "queued":
                    self._redact_proposal_row(connection, str(row["proposal_id"]))
                    continue
                payload_json = str(row["payload_json"])
                try:
                    stored = json.loads(payload_json)
                except json.JSONDecodeError as exc:
                    raise RelayError("stored Edge proposal payload is invalid") from exc
                if isinstance(stored, dict) and stored.get("algorithm") == "A256GCM":
                    continue
                if not isinstance(stored, dict):
                    raise RelayError("stored Edge proposal payload is invalid")
                aad = _proposal_aad(
                    vault_id=str(row["vault_id"]),
                    client_id=str(row["client_id"]),
                    idempotency_key=str(row["idempotency_key"]),
                    proposal_hash=str(row["proposal_hash"]),
                )
                connection.execute(
                    "UPDATE pending_memory_proposals SET kind='__sealed__', content='', "
                    "scope_json='[]', confidence=NULL, sensitivity='sealed', "
                    "requested_availability='sealed', source_service=NULL, payload_json=? "
                    "WHERE proposal_id=?",
                    (
                        _seal_proposal(stored, key=key, aad=aad),
                        str(row["proposal_id"]),
                    ),
                )
        self._proposal_encryption_key = key

    @staticmethod
    def _purge_expired_proposals(connection: sqlite3.Connection) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=_PROPOSAL_RETENTION_DAYS)).isoformat()
        connection.execute(
            "DELETE FROM pending_memory_proposals WHERE status='queued' AND created_at<?",
            (cutoff,),
        )

    @staticmethod
    def _redact_proposal_row(connection: sqlite3.Connection, proposal_id: str) -> None:
        connection.execute(
            "UPDATE pending_memory_proposals SET kind='__redacted__', content='', "
            "scope_json='[]', confidence=NULL, sensitivity='redacted', "
            "requested_availability='redacted', source_service=NULL, payload_json='{}' "
            "WHERE proposal_id=?",
            (proposal_id,),
        )

    def queue_proposal(
        self,
        *,
        identity: ClientIdentity,
        idempotency_key: str,
        proposal: Mapping[str, JsonValue],
    ) -> tuple[Mapping[str, Any], bool]:
        key = self._proposal_encryption_key
        if key is None:
            raise RuntimeError("Edge proposal protection is not configured")
        normalized = _normalize_proposal(proposal)
        proposal_hash = hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()
        now = _utc_now()
        with self._transaction() as connection:
            self._require_active_edge(connection)
            self._purge_expired_proposals(connection)
            previous = connection.execute(
                "SELECT * FROM pending_memory_proposals "
                "WHERE vault_id = ? AND client_id = ? AND idempotency_key = ?",
                (identity.vault_id, identity.client_id, idempotency_key),
            ).fetchone()
            if previous is not None:
                if str(previous["proposal_hash"]) != proposal_hash:
                    raise ProposalConflictError(
                        "idempotency key was already used for a different proposal"
                    )
                return self._proposal_row(previous), True
            queue_usage = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(payload_json)), 0) "
                "FROM pending_memory_proposals WHERE vault_id=? AND status='queued'",
                (identity.vault_id,),
            ).fetchone()
            if queue_usage is None:  # pragma: no cover - aggregate always returns one row
                raise RelayError("proposal queue accounting failed")
            proposal_id = str(uuid4())
            aad = _proposal_aad(
                vault_id=identity.vault_id,
                client_id=identity.client_id,
                idempotency_key=idempotency_key,
                proposal_hash=proposal_hash,
            )
            envelope = _seal_proposal(normalized, key=key, aad=aad)
            if int(queue_usage[0]) >= _MAX_QUEUED_PROPOSALS or (
                int(queue_usage[1]) + len(envelope.encode("utf-8")) > _MAX_QUEUED_PROPOSAL_BYTES
            ):
                raise RelayError(
                    "the encrypted Edge proposal queue is full; start Core to import it"
                )
            connection.execute(
                "INSERT INTO pending_memory_proposals "
                "(proposal_id, vault_id, client_id, idempotency_key, proposal_hash, kind, content, "
                "scope_json, confidence, sensitivity, requested_availability, "
                "source_service, status, "
                "created_at, updated_at, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
                (
                    proposal_id,
                    identity.vault_id,
                    identity.client_id,
                    idempotency_key,
                    proposal_hash,
                    "__sealed__",
                    "",
                    "[]",
                    None,
                    "sealed",
                    "sealed",
                    None,
                    now,
                    now,
                    envelope,
                ),
            )
            row = connection.execute(
                "SELECT * FROM pending_memory_proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
            if row is None:  # pragma: no cover - same-transaction insert invariant
                raise RelayError("proposal insert failed")
            return self._proposal_row(row), False

    def list_proposals(
        self, vault_id: str, *, status: str = "queued", limit: int = 100
    ) -> Sequence[Mapping[str, Any]]:
        if status not in {"queued", "imported", "rejected"}:
            raise ValueError("invalid proposal status")
        with self._transaction() as connection:
            self._purge_expired_proposals(connection)
            rows = connection.execute(
                "SELECT * FROM pending_memory_proposals "
                "WHERE vault_id = ? AND status = ? ORDER BY created_at, proposal_id LIMIT ?",
                (vault_id, status, limit),
            ).fetchall()
        return [self._proposal_row(row) for row in rows]

    def _proposal_row(self, row: sqlite3.Row) -> dict[str, Any]:
        proposal: dict[str, Any] | None = None
        if str(row["status"]) == "queued":
            key = self._proposal_encryption_key
            if key is None:
                raise RuntimeError("Edge proposal protection is not configured")
            proposal = _open_proposal(
                str(row["payload_json"]),
                key=key,
                aad=_proposal_aad(
                    vault_id=str(row["vault_id"]),
                    client_id=str(row["client_id"]),
                    idempotency_key=str(row["idempotency_key"]),
                    proposal_hash=str(row["proposal_hash"]),
                ),
            )
        return {
            "proposal_id": str(row["proposal_id"]),
            "vault_id": str(row["vault_id"]),
            "client_id": str(row["client_id"]),
            "idempotency_key": str(row["idempotency_key"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "proposal": proposal,
        }

    def update_proposal_status(self, vault_id: str, proposal_id: str, status: str) -> bool:
        if status not in {"imported", "rejected"}:
            raise ValueError("proposal status must be imported or rejected")
        with self._transaction() as connection:
            self._require_active_edge(connection)
            existing = connection.execute(
                "SELECT status FROM pending_memory_proposals "
                "WHERE vault_id = ? AND proposal_id = ?",
                (vault_id, proposal_id),
            ).fetchone()
            if existing is not None and str(existing["status"]) == status:
                return True
            cursor = connection.execute(
                "UPDATE pending_memory_proposals SET status = ?, updated_at = ? "
                "WHERE vault_id = ? AND proposal_id = ? AND status = 'queued'",
                (status, _utc_now(), vault_id, proposal_id),
            )
            if cursor.rowcount == 1:
                self._redact_proposal_row(connection, proposal_id)
            return cursor.rowcount == 1

    def purge_vault(self, vault_id: str) -> None:
        """Remove every replicated or queued artifact for one decommissioned vault."""

        with self._transaction() as connection:
            connection.execute("DELETE FROM relay_context_fts WHERE vault_id=?", (vault_id,))
            connection.execute("DELETE FROM relay_context_records WHERE vault_id=?", (vault_id,))
            connection.execute(
                "DELETE FROM relay_deletion_tombstones WHERE vault_id=?", (vault_id,)
            )
            connection.execute("DELETE FROM relay_purge_tombstones WHERE vault_id=?", (vault_id,))
            connection.execute(
                "DELETE FROM applied_replication_events WHERE vault_id=?", (vault_id,)
            )
            connection.execute("DELETE FROM pending_memory_proposals WHERE vault_id=?", (vault_id,))
            connection.execute("DELETE FROM replication_checkpoints WHERE vault_id=?", (vault_id,))

    def purge_all(self) -> int:
        """Remove active Edge artifacts and compact the live SQLite/WAL files."""

        tables = (
            "relay_context_fts",
            "relay_context_records",
            "relay_deletion_tombstones",
            "relay_purge_tombstones",
            "relay_purge_compaction_state",
            "applied_replication_events",
            "pending_memory_proposals",
            "replication_checkpoints",
        )
        with self._transaction() as connection:
            for table in tables:
                connection.execute(f"DELETE FROM {table}")
            remaining = sum(
                int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in tables
            )
        if remaining == 0:
            # secure_delete overwrites freed cells. Checkpointing, VACUUM, and
            # a final truncate minimize remnants in the live DB/WAL; provider
            # snapshots remain governed by the hosting provider's retention.
            with self._lock:
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self._connection.execute("VACUUM")
                self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return remaining

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def _normalize_proposal(proposal: Mapping[str, JsonValue]) -> dict[str, Any]:
    value = _as_json_object(proposal)
    confidence_value = value.get("confidence")
    confidence: float | None = None
    if confidence_value is not None:
        if isinstance(confidence_value, bool) or not isinstance(confidence_value, (int, float)):
            raise InvalidEventPayloadError("confidence must be a number between 0 and 1")
        confidence = float(confidence_value)
        if not 0 <= confidence <= 1:
            raise InvalidEventPayloadError("confidence must be a number between 0 and 1")
    availability = _required_string(
        value.get("availability", value.get("requested_availability", "core_available")),
        "availability",
    )
    if availability not in {"always_available", "core_available", "local_only"}:
        raise InvalidEventPayloadError("invalid requested availability")
    sensitivity = _required_string(value.get("sensitivity", "sensitive"), "sensitivity")
    if sensitivity == "private":
        sensitivity = "sensitive"
    if sensitivity not in {"normal", "sensitive", "highly_sensitive"}:
        raise InvalidEventPayloadError("invalid sensitivity")
    return {
        "kind": _required_string(value.get("kind"), "kind"),
        "content": _required_string(value.get("content"), "content"),
        "scope": _string_list(value.get("scope", value.get("scopes")), "scope"),
        "confidence": confidence,
        "sensitivity": sensitivity,
        "requested_availability": availability,
        "source_service": _optional_string(value.get("source_service"), "source_service"),
        "provenance": value.get("provenance"),
    }


def _record_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["record_id"]),
        "vault_id": str(row["vault_id"]),
        "kind": str(row["kind"]),
        "content": str(row["content"]),
        "scope": json.loads(str(row["scope_json"])),
        "provenance": json.loads(str(row["provenance_json"])),
        "source_service": row["source_service"],
        "confidence": row["confidence"],
        "sensitivity": str(row["sensitivity"]),
        "availability": str(row["availability"]),
        "allowed_clients": json.loads(str(row["allowed_clients_json"])),
        "denied_clients": json.loads(str(row["denied_clients_json"])),
        "valid_from": row["valid_from"],
        "valid_until": row["valid_until"],
        "version": int(row["version"]),
        "supersedes": row["supersedes"],
        "approval_status": str(row["approval_status"]),
        "content_hash": str(row["content_hash"]),
        "updated_at": str(row["updated_at"]),
        "score": -float(row.get("search_rank", 0.0)),
    }


def _record_visible(
    row: Mapping[str, Any],
    identity: ClientIdentity,
    requested_scopes: frozenset[str] | None,
    now: datetime,
) -> bool:
    denied = set(json.loads(str(row["denied_clients_json"])))
    if identity.client_id in denied:
        return False
    allowed = set(json.loads(str(row["allowed_clients_json"])))
    if allowed and identity.client_id not in allowed:
        return False
    record_scopes = set(json.loads(str(row["scope_json"])))
    effective_scopes = requested_scopes if requested_scopes is not None else identity.context_scopes
    if record_scopes and "*" not in effective_scopes and record_scopes.isdisjoint(effective_scopes):
        return False
    valid_from_raw = row["valid_from"]
    if valid_from_raw is not None and datetime.fromisoformat(str(valid_from_raw)) > now:
        return False
    valid_until_raw = row["valid_until"]
    return not (valid_until_raw is not None and datetime.fromisoformat(str(valid_until_raw)) <= now)


class RelayService:
    """Verifies Core events and enforces client retrieval policy."""

    def __init__(self, store: RelayStore, replication_secret: bytes) -> None:
        if len(replication_secret) < 32:
            raise ValueError("replication_secret must contain at least 32 bytes")
        self.store = store
        self._replication_secret = bytes(replication_secret)
        self.store.configure_proposal_protection(self._replication_secret)

    def rotate_replication_secret(self, replication_secret: bytes) -> None:
        if len(replication_secret) < 32:
            raise ValueError("replication_secret must contain at least 32 bytes")
        self._replication_secret = bytes(replication_secret)
        self.store.configure_proposal_protection(self._replication_secret)

    def apply(self, event: ReplicationEvent | Mapping[str, Any]) -> ApplyResult:
        parsed = (
            event if isinstance(event, ReplicationEvent) else ReplicationEvent.from_mapping(event)
        )
        verify_event(parsed, self._replication_secret)
        return self.store.apply_event(parsed)

    def resume_purge_compaction(self) -> Mapping[str, Any]:
        return self.store.resume_purge_compaction()

    @staticmethod
    def _authorize(identity: ClientIdentity, permission: str) -> None:
        if permission not in identity.permissions:
            raise AuthorizationError(f"client lacks {permission} permission")

    @staticmethod
    def _requested_scopes(
        identity: ClientIdentity, requested_scopes: Sequence[str] | None
    ) -> frozenset[str] | None:
        if requested_scopes is None:
            return None
        requested = frozenset(requested_scopes)
        if not requested:
            return None
        if len(requested) > 64 or any(len(scope) > 200 for scope in requested):
            raise ValueError("requested scopes must contain at most 64 bounded values")
        if "*" not in identity.context_scopes and not requested.issubset(identity.context_scopes):
            raise AuthorizationError("requested context scope exceeds client grant")
        return requested

    def search(
        self,
        identity: ClientIdentity,
        *,
        query: str = "",
        scopes: Sequence[str] | None = None,
        kinds: Sequence[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        self._authorize(identity, "context:read")
        if len(query) > 4_000:
            raise ValueError("query must contain at most 4000 characters")
        if not 1 <= limit <= 101:
            raise ValueError("limit must be between 1 and 101")
        if not 0 <= offset <= 10_000:
            raise ValueError("offset must be between 0 and 10000")
        if kinds is not None and (
            len(kinds) > 64 or any(not kind or len(kind) > 128 for kind in kinds)
        ):
            raise ValueError("kinds must contain at most 64 bounded values")
        requested = self._requested_scopes(identity, scopes)
        visible = self._iter_visible_records(
            identity,
            query=query,
            kinds=kinds,
            requested_scopes=requested,
        )
        return list(islice(visible, offset, offset + limit))

    def _iter_visible_records(
        self,
        identity: ClientIdentity,
        *,
        query: str,
        kinds: Sequence[str] | None,
        requested_scopes: frozenset[str] | None,
    ) -> Iterator[dict[str, Any]]:
        """Filter deterministic SQLite pages without loading the whole Edge vault."""

        raw_offset = 0
        page_size = 256
        now = datetime.now(UTC)
        while True:
            rows = self.store.search_record_rows(
                identity.vault_id,
                query,
                page_size,
                kinds=kinds,
                candidate_offset=raw_offset,
            )
            if not rows:
                return
            for row in rows:
                if _record_visible(row, identity, requested_scopes, now):
                    yield _record_from_row(row)
            raw_offset += len(rows)
            if len(rows) < page_size:
                return

    def count(self, identity: ClientIdentity) -> int:
        self._authorize(identity, "context:read")
        return sum(
            1
            for _record in self._iter_visible_records(
                identity,
                query="",
                kinds=None,
                requested_scopes=None,
            )
        )

    def get(self, identity: ClientIdentity, record_id: str) -> dict[str, Any] | None:
        self._authorize(identity, "context:read")
        row = self.store.get_record_row(identity.vault_id, record_id)
        if row is None or not _record_visible(row, identity, None, datetime.now(UTC)):
            # Deliberately conceal whether an inaccessible stable ID exists.
            return None
        return _record_from_row(row)

    def owner_get(self, vault_id: str, record_id: str) -> dict[str, Any] | None:
        """Return a record to the separately authenticated single owner.

        Provider client allow/deny lists do not restrict the person who owns the
        Edge. The HTTP caller must establish an Edge owner session before using
        this method.
        """

        row = self.store.get_record_row(vault_id, record_id)
        if row is None:
            return None
        now = datetime.now(UTC)
        valid_from_raw = row["valid_from"]
        valid_until_raw = row["valid_until"]
        if valid_from_raw is not None and datetime.fromisoformat(str(valid_from_raw)) > now:
            return None
        if valid_until_raw is not None and datetime.fromisoformat(str(valid_until_raw)) <= now:
            return None
        return _record_from_row(row)

    def status(self, identity: ClientIdentity) -> dict[str, Any]:
        self._authorize(identity, "context:read")
        return {
            "vault_id": identity.vault_id,
            "last_applied_sequence": self.store.checkpoint(identity.vault_id),
            "available_records": self.count(identity),
            "authority": "core",
            "relay_writable": False,
            "purge_compaction": dict(self.store.purge_compaction_status()),
        }

    def propose(
        self,
        identity: ClientIdentity,
        *,
        idempotency_key: str,
        proposal: Mapping[str, JsonValue],
    ) -> tuple[Mapping[str, Any], bool]:
        self._authorize(identity, "proposal:write")
        if not idempotency_key or len(idempotency_key) > 200:
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        if len(canonical_json(proposal).encode("utf-8")) > 64 * 1024:
            raise ValueError("proposal exceeds the 64 KB encrypted queue limit")
        return self.store.queue_proposal(
            identity=identity,
            idempotency_key=idempotency_key,
            proposal=proposal,
        )

    def queued_proposals(self, vault_id: str, *, limit: int = 100) -> Sequence[Mapping[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return self.store.list_proposals(vault_id, status="queued", limit=limit)

    def acknowledge_proposal(self, vault_id: str, proposal_id: str, status: str) -> bool:
        return self.store.update_proposal_status(vault_id, proposal_id, status)

    def purge_vault(self, vault_id: str) -> None:
        self.store.purge_vault(vault_id)

    def purge_all(self) -> int:
        return self.store.purge_all()

    def close(self) -> None:
        self.store.close()
