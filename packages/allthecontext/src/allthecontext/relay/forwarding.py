"""Bounded, restart-safe broker for outbound-only Core retrieval forwarding."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from allthecontext.edge_claim import encrypt_forward_request

MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
MAX_PENDING_GLOBAL = 32
MAX_PENDING_PER_CLIENT = 2
MAX_REQUESTS_PER_MINUTE = 30
MAX_CLAIMS = 8
DEFAULT_TTL_SECONDS = 12.0
CLAIM_LEASE_SECONDS = 8.0


class ForwardingError(RuntimeError):
    """A forwarding request could not be safely accepted or completed."""


@dataclass(frozen=True, slots=True)
class ForwardResult:
    state: str
    response: dict[str, Any] | None = None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class EdgeForwardingBroker:
    """Persist only bounded in-flight envelopes; consume private responses once."""

    def __init__(
        self, database_path: Path, request_encryption_public_key: str | None = None
    ) -> None:
        self.database_path = Path(database_path)
        self.request_encryption_public_key = request_encryption_public_key
        self._lock = RLock()
        self._responses: dict[str, tuple[dict[str, Any], float]] = {}

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        connection.execute("PRAGMA secure_delete = ON")
        return connection

    @contextmanager
    def _connection_scope(self) -> Iterator[sqlite3.Connection]:
        connection = self._connection()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _cleanup(connection: sqlite3.Connection, now: float) -> None:
        connection.execute(
            "UPDATE edge_forward_requests SET state='cancelled',request_json='{}',"
            "response_json=NULL,response_bytes=NULL,claim_hash=NULL "
            "WHERE expires_at<? AND state IN ('queued','claimed')",
            (now,),
        )
        connection.execute(
            "DELETE FROM edge_forward_requests WHERE state='cancelled' OR completed_at<?",
            (now - 60.0,),
        )
        connection.execute("DELETE FROM edge_forward_rate_events WHERE created_at<?", (now - 60.0,))
        connection.execute(
            "UPDATE edge_forward_requests SET state='queued',claim_hash=NULL,claimed_at=NULL "
            "WHERE state='claimed' AND claimed_at<? AND expires_at>=?",
            (now - CLAIM_LEASE_SECONDS, now),
        )

    def _cleanup_memory(self, now: float) -> None:
        for request_id, (_response, expires_at) in tuple(self._responses.items()):
            if expires_at < now:
                self._responses.pop(request_id, None)

    def enqueue(
        self,
        *,
        client_id: str,
        client_scopes: list[str],
        operation: str,
        payload: dict[str, Any],
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
    ) -> str:
        if not client_id.startswith("edge:") or len(client_id) > 200:
            raise ForwardingError("forwarding identity is invalid")
        if operation not in {"bootstrap_context", "search_context", "get_context_item"}:
            raise ForwardingError("forwarding operation is not allowed")
        if len(client_scopes) > 16 or any(len(scope) > 100 for scope in client_scopes):
            raise ForwardingError("forwarding scopes are invalid")
        request_plaintext = _json(payload).encode("utf-8")
        if len(request_plaintext) > MAX_REQUEST_BYTES:
            raise ForwardingError("forwarding request is too large")
        if self.request_encryption_public_key is None:
            raise ForwardingError("Core forwarding encryption is unavailable")
        ttl = min(max(ttl_seconds, 2.0), 20.0)
        now = time.time()
        request_id = secrets.token_urlsafe(24)
        expires_at = now + ttl
        associated_data = self._request_aad(request_id, client_id, operation, expires_at)
        request_json = encrypt_forward_request(
            self.request_encryption_public_key, request_plaintext, associated_data
        )
        with self._lock, self._connection_scope() as connection:
            self._cleanup_memory(now)
            connection.execute("BEGIN IMMEDIATE")
            self._cleanup(connection, now)
            global_pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM edge_forward_requests "
                    "WHERE state IN ('queued','claimed','answered')"
                ).fetchone()[0]
            )
            client_pending = int(
                connection.execute(
                    "SELECT COUNT(*) FROM edge_forward_requests WHERE client_id=? "
                    "AND state IN ('queued','claimed','answered')",
                    (client_id,),
                ).fetchone()[0]
            )
            recent = int(
                connection.execute(
                    "SELECT COUNT(*) FROM edge_forward_rate_events WHERE client_id=?",
                    (client_id,),
                ).fetchone()[0]
            )
            if global_pending >= MAX_PENDING_GLOBAL or client_pending >= MAX_PENDING_PER_CLIENT:
                raise ForwardingError("Core retrieval is busy")
            if recent >= MAX_REQUESTS_PER_MINUTE:
                raise ForwardingError("Core retrieval rate limit reached")
            connection.execute(
                "INSERT INTO edge_forward_requests"
                "(request_id,client_id,client_scopes_json,operation,request_json,created_at,"
                "expires_at,state) VALUES(?,?,?,?,?,?,?,'queued')",
                (
                    request_id,
                    client_id,
                    # OAuth scopes are enforced at Edge for the MCP call, but
                    # are intentionally not relayed as authorization claims.
                    _json([]),
                    operation,
                    request_json,
                    now,
                    expires_at,
                ),
            )
            connection.execute(
                "INSERT INTO edge_forward_rate_events(client_id,created_at) VALUES(?,?)",
                (client_id, now),
            )
        return request_id

    def wait(self, request_id: str, *, timeout_seconds: float = 10.0) -> ForwardResult:
        deadline = time.monotonic() + min(max(timeout_seconds, 0.1), 15.0)
        while time.monotonic() < deadline:
            consumed: dict[str, Any] | None = None
            with self._lock, self._connection_scope() as connection:
                now = time.time()
                self._cleanup_memory(now)
                connection.execute("BEGIN IMMEDIATE")
                self._cleanup(connection, now)
                row = connection.execute(
                    "SELECT state FROM edge_forward_requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if row is None:
                    return ForwardResult("unavailable")
                if row["state"] == "answered":
                    connection.execute(
                        "DELETE FROM edge_forward_requests WHERE request_id=?", (request_id,)
                    )
                    memory_response = self._responses.pop(request_id, None)
                    if memory_response is None or memory_response[1] < now:
                        return ForwardResult("unavailable")
                    consumed = memory_response[0]
            if consumed is not None:
                return ForwardResult("available", consumed)
            time.sleep(0.05)
        self.cancel(request_id)
        return ForwardResult("timeout")

    def cancel(self, request_id: str) -> bool:
        with self._lock, self._connection_scope() as connection:
            self._responses.pop(request_id, None)
            result = connection.execute(
                "UPDATE edge_forward_requests SET state='cancelled',request_json='{}',"
                "response_json=NULL,response_bytes=NULL,claim_hash=NULL,completed_at=? "
                "WHERE request_id=? AND state IN ('queued','claimed')",
                (time.time(), request_id),
            )
        return result.rowcount == 1

    def cancel_client(self, client_id: str) -> int:
        with self._lock, self._connection_scope() as connection:
            request_ids = [
                str(row[0])
                for row in connection.execute(
                    "SELECT request_id FROM edge_forward_requests WHERE client_id=?", (client_id,)
                )
            ]
            for request_id in request_ids:
                self._responses.pop(request_id, None)
            result = connection.execute(
                "UPDATE edge_forward_requests SET state='cancelled',request_json='{}',"
                "response_json=NULL,response_bytes=NULL,claim_hash=NULL,completed_at=? "
                "WHERE client_id=? AND state IN ('queued','claimed','answered')",
                (time.time(), client_id),
            )
        return result.rowcount

    def claim(self, *, limit: int = MAX_CLAIMS) -> list[dict[str, Any]]:
        now = time.time()
        claimed: list[dict[str, Any]] = []
        with self._lock, self._connection_scope() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._cleanup_memory(now)
            self._cleanup(connection, now)
            connection.execute(
                "INSERT INTO edge_forward_core_state(singleton,last_seen_at) VALUES(1,?) "
                "ON CONFLICT(singleton) DO UPDATE SET last_seen_at=excluded.last_seen_at",
                (now,),
            )
            rows = connection.execute(
                "SELECT * FROM edge_forward_requests WHERE state='queued' AND expires_at>=? "
                "ORDER BY created_at LIMIT ?",
                (now, min(max(limit, 1), MAX_CLAIMS)),
            ).fetchall()
            for row in rows:
                claim_token = secrets.token_urlsafe(24)
                connection.execute(
                    "UPDATE edge_forward_requests SET state='claimed',claim_hash=?,claimed_at=? "
                    "WHERE request_id=? AND state='queued'",
                    (hashlib.sha256(claim_token.encode()).hexdigest(), now, row["request_id"]),
                )
                claimed.append(
                    {
                        "request_id": str(row["request_id"]),
                        "claim_token": claim_token,
                        "client_id": str(row["client_id"]),
                        "operation": str(row["operation"]),
                        "request_envelope": str(row["request_json"]),
                        "expires_at": float(row["expires_at"]),
                    }
                )
        return claimed

    def core_online(self, *, within_seconds: float = 35.0) -> bool:
        with self._lock, self._connection_scope() as connection:
            row = connection.execute(
                "SELECT last_seen_at FROM edge_forward_core_state WHERE singleton=1"
            ).fetchone()
        return row is not None and float(row[0]) >= time.time() - within_seconds

    def answer(self, request_id: str, claim_token: str, response: dict[str, Any]) -> None:
        response_bytes = len(_json(response).encode("utf-8"))
        if response_bytes > MAX_RESPONSE_BYTES:
            raise ForwardingError("Core retrieval response is too large")
        supplied = hashlib.sha256(claim_token.encode()).hexdigest()
        now = time.time()
        with self._lock, self._connection_scope() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._cleanup_memory(now)
            self._cleanup(connection, now)
            row = connection.execute(
                "SELECT state,claim_hash,expires_at FROM edge_forward_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if (
                row is None
                or row["state"] != "claimed"
                or float(row["expires_at"]) < now
                or not secrets.compare_digest(str(row["claim_hash"] or ""), supplied)
            ):
                raise ForwardingError("forwarding claim is no longer active")
            self._responses[request_id] = (response, float(row["expires_at"]))
            connection.execute(
                "UPDATE edge_forward_requests SET state='answered',response_json=NULL,"
                "response_bytes=?,completed_at=?,request_json='{}',claim_hash=NULL "
                "WHERE request_id=?",
                (response_bytes, now, request_id),
            )

    @staticmethod
    def _request_aad(request_id: str, client_id: str, operation: str, expires_at: float) -> bytes:
        return _json(
            {
                "request_id": request_id,
                "client_id": client_id,
                "operation": operation,
                "expires_at": expires_at,
            }
        ).encode("utf-8")

    def status(self) -> dict[str, int]:
        with self._lock, self._connection_scope() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = time.time()
            self._cleanup_memory(now)
            self._cleanup(connection, now)
            rows = connection.execute(
                "SELECT state,COUNT(*) AS count FROM edge_forward_requests GROUP BY state"
            ).fetchall()
        counts = {str(row["state"]): int(row["count"]) for row in rows}
        return {"queued": counts.get("queued", 0), "claimed": counts.get("claimed", 0)}

    def purge(self) -> None:
        with self._lock, self._connection_scope() as connection:
            self._responses.clear()
            connection.execute("DELETE FROM edge_forward_requests")
            connection.execute("DELETE FROM edge_forward_rate_events")
            connection.execute("DELETE FROM edge_forward_core_state")
