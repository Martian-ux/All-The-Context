"""Persistent single-owner OAuth 2.1 provider for the hosted Edge MCP endpoint."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import cast
from urllib.parse import urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from allthecontext.edge_setup import normalize_edge_url

READ_SCOPE = "context:read"
PROPOSE_SCOPE = "context:propose"
EDGE_SCOPES = (READ_SCOPE, PROPOSE_SCOPE)
OWNER_SUBJECT = "all-the-context-owner"
MAX_CLIENT_METADATA_BYTES = 16 * 1024
MAX_CLIENT_ID_LENGTH = 256
MAX_CLIENT_NAME_LENGTH = 120
MAX_REDIRECT_URI_LENGTH = 2048


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_list(value: str) -> list[str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or any(not isinstance(item, str) for item in parsed):
        raise RuntimeError("stored OAuth scope set is invalid")
    return parsed


def _provider_for_redirect(value: str) -> str | None:
    parsed = urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if hostname == "claude.ai" and parsed.path == "/api/mcp/auth_callback":
        return "claude"
    if hostname == "chatgpt.com" and (
        parsed.path.startswith("/connector/oauth/")
        or parsed.path == "/connector_platform_oauth_redirect"
    ):
        return "chatgpt"
    return None


def _logical_client_id(client: OAuthClientInformationFull) -> str:
    providers = [_provider_for_redirect(str(item)) for item in (client.redirect_uris or [])]
    if providers and all(provider == "chatgpt" for provider in providers):
        return "edge:chatgpt"
    if providers and all(provider == "claude" for provider in providers):
        return "edge:claude"
    client_id = client.client_id or "unknown"
    return f"edge:client:{hashlib.sha256(client_id.encode()).hexdigest()[:16]}"


def client_display_name(client: OAuthClientInformationFull) -> str:
    logical = _logical_client_id(client)
    if logical == "edge:chatgpt":
        return "ChatGPT"
    if logical == "edge:claude":
        return "Claude"
    label = (client.client_name or "Unnamed client")[:90]
    return f"Unverified MCP client: {label}"


@dataclass(frozen=True, slots=True)
class PendingAuthorization:
    request_id: str
    client_id: str
    client_name: str
    scopes: tuple[str, ...]
    expires_at: float


class EdgeRefreshToken(RefreshToken):
    resource: str
    family_id: str
    logical_client_id: str


class _OAuthTransaction:
    """Class-based transaction manager compatible with frozen SDK exceptions."""

    def __init__(self, store: EdgeOAuthStore, *, allow_decommissioned: bool = False) -> None:
        self.store = store
        self.allow_decommissioned = allow_decommissioned

    def __enter__(self) -> sqlite3.Connection:
        self.store._lock.acquire()
        try:
            self.store._connection.execute("BEGIN IMMEDIATE")
            if not self.allow_decommissioned:
                row = self.store._connection.execute(
                    "SELECT decommissioned_at FROM edge_instance_state WHERE singleton=1"
                ).fetchone()
                if row is not None and row[0] is not None:
                    raise RuntimeError("Edge has been decommissioned")
        except BaseException:
            self.store._connection.rollback()
            self.store._lock.release()
            raise
        return self.store._connection

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object | None,
    ) -> None:
        try:
            if exc_type is None:
                self.store._connection.commit()
            else:
                self.store._connection.rollback()
        finally:
            self.store._lock.release()


class EdgeOAuthStore:
    """Hashed token persistence sharing the Edge SQLite database."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
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
        required = {
            "edge_oauth_clients",
            "edge_oauth_requests",
            "edge_oauth_codes",
            "edge_oauth_access_tokens",
            "edge_oauth_refresh_tokens",
            "edge_owner_tickets",
            "edge_owner_sessions",
            "edge_instance_state",
            "edge_registration_state",
            "edge_identity_state",
        }
        present = {
            str(row[0])
            for row in self._connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not required.issubset(present):
            raise RuntimeError("Edge OAuth tables are missing; initialize SQLiteRelayStore first")

    def transaction(self, *, allow_decommissioned: bool = False) -> _OAuthTransaction:
        return _OAuthTransaction(self, allow_decommissioned=allow_decommissioned)

    @staticmethod
    def _cleanup_expired(connection: sqlite3.Connection, now: float) -> None:
        connection.execute("DELETE FROM edge_oauth_requests WHERE expires_at < ?", (now,))
        connection.execute(
            "DELETE FROM edge_oauth_codes WHERE expires_at < ? OR consumed_at IS NOT NULL",
            (now,),
        )
        connection.execute("DELETE FROM edge_oauth_access_tokens WHERE expires_at < ?", (now,))
        connection.execute("DELETE FROM edge_oauth_refresh_tokens WHERE expires_at < ?", (now,))
        connection.execute(
            "DELETE FROM edge_owner_tickets WHERE expires_at < ? OR consumed_at IS NOT NULL",
            (now,),
        )
        connection.execute(
            "DELETE FROM edge_owner_sessions WHERE expires_at < ? OR revoked_at IS NOT NULL",
            (now,),
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def is_decommissioned(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT decommissioned_at FROM edge_instance_state WHERE singleton=1"
            ).fetchone()
        return row is not None and row[0] is not None

    def bind_instance(self, *, vault_id: str, binding_fingerprint: str) -> None:
        """Bind this persistent OAuth/token database to exactly one Edge authority."""

        if not vault_id or len(vault_id) > 200 or len(binding_fingerprint) != 64:
            raise RuntimeError("Edge identity binding is invalid")
        with self.transaction(allow_decommissioned=True) as connection:
            row = connection.execute(
                "SELECT vault_id,binding_fingerprint FROM edge_identity_state WHERE singleton=1"
            ).fetchone()
            if row is None:
                # Migration 0005 cannot reconstruct the old pairing secret or
                # origin. Never let the first new-version boot silently bless
                # an existing pre-binding database under arbitrary settings.
                authority_tables = (
                    "replication_checkpoints",
                    "applied_replication_events",
                    "relay_context_records",
                    "relay_context_fts",
                    "relay_deletion_tombstones",
                    "pending_memory_proposals",
                    "edge_oauth_clients",
                    "edge_oauth_requests",
                    "edge_oauth_codes",
                    "edge_oauth_access_tokens",
                    "edge_oauth_refresh_tokens",
                    "edge_owner_tickets",
                    "edge_owner_sessions",
                )
                legacy_state = any(
                    connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
                    for table in authority_tables
                )
                instance_state = connection.execute(
                    "SELECT decommissioned_at FROM edge_instance_state WHERE singleton=1"
                ).fetchone()
                registration_state = connection.execute(
                    "SELECT open_until FROM edge_registration_state WHERE singleton=1"
                ).fetchone()
                legacy_state = (
                    legacy_state
                    or (instance_state is not None and instance_state[0] is not None)
                    or (registration_state is not None and float(registration_state[0]) != 0)
                )
                if legacy_state:
                    raise RuntimeError(
                        "This legacy Edge database contains authority data but has no persisted "
                        "identity binding; refusing to adopt it under an unverified enrollment. "
                        "Restore the prior Edge release and decommission it, or deploy this "
                        "release with a fresh persistent disk and resynchronize from Core"
                    )
                connection.execute(
                    "INSERT INTO edge_identity_state"
                    "(singleton,vault_id,binding_fingerprint,bound_at) VALUES(1,?,?,?)",
                    (vault_id, binding_fingerprint, _utc_now()),
                )
                return
            same_vault = secrets.compare_digest(str(row[0]), vault_id)
            same_fingerprint = secrets.compare_digest(str(row[1]), binding_fingerprint)
            if not same_vault or not same_fingerprint:
                raise RuntimeError(
                    "This Edge database is already bound to a different vault or enrollment "
                    "bundle; refusing to reuse OAuth tokens across authorities"
                )

    def open_registration_window(self, *, lifetime_seconds: int = 600) -> None:
        if self.is_decommissioned():
            raise RuntimeError("Edge has been decommissioned")
        open_until = time.time() + max(60, min(lifetime_seconds, 900))
        with self.transaction() as connection:
            connection.execute(
                "UPDATE edge_registration_state SET open_until=MAX(open_until, ?) "
                "WHERE singleton=1",
                (open_until,),
            )

    def registration_open(self) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT i.decommissioned_at,r.open_until FROM edge_instance_state i "
                "JOIN edge_registration_state r ON r.singleton=i.singleton "
                "WHERE i.singleton=1"
            ).fetchone()
        return row is not None and row[0] is None and float(row[1]) >= time.time()

    def decommission(self) -> None:
        """Persistently disable public access and remove active OAuth/session metadata."""

        with self.transaction(allow_decommissioned=True) as connection:
            current = connection.execute(
                "SELECT decommissioned_at FROM edge_instance_state WHERE singleton=1"
            ).fetchone()
            if current is not None and current[0] is not None:
                return
            connection.execute("UPDATE edge_registration_state SET open_until=0 WHERE singleton=1")
            connection.execute(
                "UPDATE edge_instance_state SET decommissioned_at=COALESCE(decommissioned_at,?) "
                "WHERE singleton=1",
                (_utc_now(),),
            )
            connection.execute("DELETE FROM edge_oauth_requests")
            connection.execute("DELETE FROM edge_oauth_codes")
            connection.execute("DELETE FROM edge_owner_tickets")
            connection.execute("DELETE FROM edge_owner_sessions")
            connection.execute("DELETE FROM edge_oauth_clients")

    def client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT client_json FROM edge_oauth_clients WHERE client_id=?", (client_id,)
            ).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(str(row[0]))

    def save_client(self, client: OAuthClientInformationFull) -> None:
        if not client.client_id:
            raise RegistrationError("invalid_client_metadata", "client_id is required")
        serialized = client.model_dump_json(exclude_none=True)
        if len(serialized.encode("utf-8")) > MAX_CLIENT_METADATA_BYTES:
            raise RegistrationError(
                "invalid_client_metadata", "client metadata must be 16 KiB or smaller"
            )
        now = _utc_now()
        with self.transaction() as connection:
            self._cleanup_expired(connection, time.time())
            unauthorized_cutoff = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
            connection.execute(
                "DELETE FROM edge_oauth_clients WHERE last_authorized_at IS NULL "
                "AND registered_at<? AND NOT EXISTS ("
                "SELECT 1 FROM edge_oauth_requests r WHERE r.client_id=edge_oauth_clients.client_id"
                ") AND NOT EXISTS (SELECT 1 FROM edge_oauth_codes k "
                "WHERE k.client_id=edge_oauth_clients.client_id)",
                (unauthorized_cutoff,),
            )
            # DCR is unauthenticated. Keep bounded storage and discard only the
            # oldest client that has never completed owner authorization.
            count = int(connection.execute("SELECT COUNT(*) FROM edge_oauth_clients").fetchone()[0])
            if count >= 512:
                stale = connection.execute(
                    "SELECT c.client_id FROM edge_oauth_clients c "
                    "WHERE c.last_authorized_at IS NULL ORDER BY c.registered_at LIMIT 1"
                ).fetchone()
                if stale is None:
                    raise RegistrationError(
                        "invalid_client_metadata", "this personal Edge has reached its client limit"
                    )
                connection.execute(
                    "DELETE FROM edge_oauth_clients WHERE client_id=?", (str(stale[0]),)
                )
            connection.execute(
                "INSERT INTO edge_oauth_clients(client_id,client_json,registered_at) VALUES(?,?,?)",
                (client.client_id, serialized, now),
            )

    def save_authorization_request(
        self,
        request_id: str,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
        scopes: list[str],
        resource: str,
    ) -> None:
        if not client.client_id:
            raise AuthorizeError("invalid_request", "client_id is required")
        expires_at = time.time() + 600
        with self.transaction() as connection:
            self._cleanup_expired(connection, time.time())
            connection.execute(
                "INSERT INTO edge_oauth_requests"
                "(request_id_hash,client_id,oauth_state,scopes_json,code_challenge,redirect_uri,"
                "redirect_uri_explicit,resource,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    _digest(request_id),
                    client.client_id,
                    params.state,
                    json.dumps(scopes, separators=(",", ":")),
                    params.code_challenge,
                    str(params.redirect_uri),
                    int(params.redirect_uri_provided_explicitly),
                    resource,
                    expires_at,
                ),
            )

    def pending_authorization(self, request_id: str) -> PendingAuthorization | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT r.client_id,r.scopes_json,r.expires_at,c.client_json "
                "FROM edge_oauth_requests r JOIN edge_oauth_clients c USING(client_id) "
                "WHERE r.request_id_hash=?",
                (_digest(request_id),),
            ).fetchone()
        if row is None or float(row[2]) < time.time():
            return None
        client = OAuthClientInformationFull.model_validate_json(str(row[3]))
        return PendingAuthorization(
            request_id=request_id,
            client_id=str(row[0]),
            client_name=client_display_name(client),
            scopes=tuple(_json_list(str(row[1]))),
            expires_at=float(row[2]),
        )

    def complete_authorization(self, request_id: str) -> str:
        now = time.time()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM edge_oauth_requests WHERE request_id_hash=?",
                (_digest(request_id),),
            ).fetchone()
            if row is None or float(row["expires_at"]) < now:
                raise AuthorizeError("invalid_request", "authorization request expired")
            code = f"atc_code_{secrets.token_urlsafe(32)}"
            connection.execute(
                "INSERT INTO edge_oauth_codes"
                "(code_hash,client_id,scopes_json,code_challenge,redirect_uri,"
                "redirect_uri_explicit,resource,subject,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    _digest(code),
                    row["client_id"],
                    row["scopes_json"],
                    row["code_challenge"],
                    row["redirect_uri"],
                    row["redirect_uri_explicit"],
                    row["resource"],
                    OWNER_SUBJECT,
                    now + 300,
                ),
            )
            connection.execute(
                "DELETE FROM edge_oauth_requests WHERE request_id_hash=?", (_digest(request_id),)
            )
            connection.execute(
                "UPDATE edge_oauth_clients SET last_authorized_at=? WHERE client_id=?",
                (_utc_now(), row["client_id"]),
            )
            redirect_uri = str(row["redirect_uri"])
            state = row["oauth_state"]
        return construct_redirect_uri(redirect_uri, code=code, state=str(state) if state else None)

    def deny_authorization(self, request_id: str) -> str:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT redirect_uri,oauth_state FROM edge_oauth_requests WHERE request_id_hash=?",
                (_digest(request_id),),
            ).fetchone()
            if row is None:
                raise AuthorizeError("invalid_request", "authorization request expired")
            connection.execute(
                "DELETE FROM edge_oauth_requests WHERE request_id_hash=?", (_digest(request_id),)
            )
        return construct_redirect_uri(
            str(row[0]),
            error="access_denied",
            error_description="The owner declined access",
            state=str(row[1]) if row[1] else None,
        )

    def authorization_code(self, raw_code: str, client_id: str) -> AuthorizationCode | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM edge_oauth_codes WHERE code_hash=? AND client_id=? "
                "AND consumed_at IS NULL",
                (_digest(raw_code), client_id),
            ).fetchone()
        if row is None:
            return None
        return AuthorizationCode(
            code=raw_code,
            scopes=_json_list(str(row["scopes_json"])),
            expires_at=float(row["expires_at"]),
            client_id=str(row["client_id"]),
            code_challenge=str(row["code_challenge"]),
            redirect_uri=AnyUrl(str(row["redirect_uri"])),
            redirect_uri_provided_explicitly=bool(row["redirect_uri_explicit"]),
            resource=str(row["resource"]),
            subject=str(row["subject"]),
        )

    def exchange_code(
        self,
        raw_code: str,
        client: OAuthClientInformationFull,
        scopes: list[str],
        resource: str,
        subject: str,
    ) -> OAuthToken:
        if not client.client_id:
            raise TokenError("invalid_client", "client_id is required")
        now = int(time.time())
        family_id = secrets.token_urlsafe(24)
        access = f"atc_access_{secrets.token_urlsafe(40)}"
        refresh = f"atc_refresh_{secrets.token_urlsafe(48)}"
        logical = _logical_client_id(client)
        with self.transaction() as connection:
            self._cleanup_expired(connection, time.time())
            updated = connection.execute(
                "UPDATE edge_oauth_codes SET consumed_at=? WHERE code_hash=? "
                "AND client_id=? AND consumed_at IS NULL AND expires_at>=?",
                (_utc_now(), _digest(raw_code), client.client_id, time.time()),
            )
            if updated.rowcount != 1:
                raise TokenError("invalid_grant", "authorization code was already used or expired")
            self._insert_token_pair(
                connection,
                access=access,
                refresh=refresh,
                family_id=family_id,
                client_id=client.client_id,
                logical_client_id=logical,
                scopes=scopes,
                resource=resource,
                subject=subject,
                now=now,
            )
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(scopes),
        )

    @staticmethod
    def _insert_token_pair(
        connection: sqlite3.Connection,
        *,
        access: str,
        refresh: str,
        family_id: str,
        client_id: str,
        logical_client_id: str,
        scopes: list[str],
        resource: str,
        subject: str,
        now: int,
    ) -> None:
        values = json.dumps(scopes, separators=(",", ":"))
        connection.execute(
            "INSERT INTO edge_oauth_access_tokens"
            "(token_hash,family_id,client_id,logical_client_id,scopes_json,resource,subject,"
            "expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                _digest(access),
                family_id,
                client_id,
                logical_client_id,
                values,
                resource,
                subject,
                now + 3600,
            ),
        )
        connection.execute(
            "INSERT INTO edge_oauth_refresh_tokens"
            "(token_hash,family_id,client_id,logical_client_id,scopes_json,resource,subject,"
            "expires_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                _digest(refresh),
                family_id,
                client_id,
                logical_client_id,
                values,
                resource,
                subject,
                now + 30 * 24 * 3600,
            ),
        )

    def access_token(self, raw_token: str, expected_resource: str) -> AccessToken | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM edge_oauth_access_tokens WHERE token_hash=? AND revoked_at IS NULL",
                (_digest(raw_token),),
            ).fetchone()
        if (
            row is None
            or int(row["expires_at"]) < int(time.time())
            or str(row["resource"]) != expected_resource
        ):
            return None
        return AccessToken(
            token=raw_token,
            client_id=str(row["client_id"]),
            scopes=_json_list(str(row["scopes_json"])),
            expires_at=int(row["expires_at"]),
            resource=str(row["resource"]),
            subject=str(row["subject"]),
            claims={
                "atc_client_id": str(row["logical_client_id"]),
                "family_id": str(row["family_id"]),
            },
        )

    def refresh_token(self, raw_token: str, client_id: str) -> EdgeRefreshToken | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM edge_oauth_refresh_tokens WHERE token_hash=? AND client_id=?",
                (_digest(raw_token), client_id),
            ).fetchone()
        if row is None or int(row["expires_at"]) < int(time.time()):
            return None
        return EdgeRefreshToken(
            token=raw_token,
            client_id=client_id,
            scopes=_json_list(str(row["scopes_json"])),
            expires_at=int(row["expires_at"]),
            subject=str(row["subject"]),
            resource=str(row["resource"]),
            family_id=str(row["family_id"]),
            logical_client_id=str(row["logical_client_id"]),
        )

    def rotate_refresh(self, token: EdgeRefreshToken, scopes: list[str]) -> OAuthToken:
        now = int(time.time())
        access = f"atc_access_{secrets.token_urlsafe(40)}"
        refresh = f"atc_refresh_{secrets.token_urlsafe(48)}"
        reused = False
        with self.transaction() as connection:
            self._cleanup_expired(connection, time.time())
            updated = connection.execute(
                "UPDATE edge_oauth_refresh_tokens SET revoked_at=? WHERE token_hash=? "
                "AND family_id=? AND revoked_at IS NULL",
                (_utc_now(), _digest(token.token), token.family_id),
            )
            if updated.rowcount != 1:
                # Reuse of an already-rotated refresh token revokes the family.
                self._revoke_family(connection, token.family_id)
                reused = True
            else:
                self._insert_token_pair(
                    connection,
                    access=access,
                    refresh=refresh,
                    family_id=token.family_id,
                    client_id=token.client_id,
                    logical_client_id=token.logical_client_id,
                    scopes=scopes,
                    resource=token.resource,
                    subject=token.subject or OWNER_SUBJECT,
                    now=now,
                )
        if reused:
            raise TokenError("invalid_grant", "refresh token was already used")
        return OAuthToken(
            access_token=access,
            refresh_token=refresh,
            token_type="Bearer",
            expires_in=3600,
            scope=" ".join(scopes),
        )

    @staticmethod
    def _revoke_family(connection: sqlite3.Connection, family_id: str) -> None:
        revoked_at = _utc_now()
        connection.execute(
            "UPDATE edge_oauth_access_tokens SET revoked_at=COALESCE(revoked_at,?) "
            "WHERE family_id=?",
            (revoked_at, family_id),
        )
        connection.execute(
            "UPDATE edge_oauth_refresh_tokens SET revoked_at=COALESCE(revoked_at,?) "
            "WHERE family_id=?",
            (revoked_at, family_id),
        )

    def revoke(self, raw_token: str) -> None:
        digest = _digest(raw_token)
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT family_id FROM edge_oauth_access_tokens WHERE token_hash=? "
                "UNION ALL SELECT family_id FROM edge_oauth_refresh_tokens WHERE token_hash=? "
                "LIMIT 1",
                (digest, digest),
            ).fetchone()
            if row is not None:
                self._revoke_family(connection, str(row[0]))

    def authorized_clients(self) -> list[dict[str, object]]:
        now = time.time()
        with self.transaction() as connection:
            self._cleanup_expired(connection, now)
            rows = connection.execute(
                "SELECT r.logical_client_id,r.client_id,r.scopes_json,r.expires_at,"
                "COUNT(DISTINCT r.family_id) AS family_count,c.client_json,c.last_authorized_at "
                "FROM edge_oauth_refresh_tokens r "
                "JOIN edge_oauth_clients c ON c.client_id=r.client_id "
                "WHERE r.revoked_at IS NULL AND r.expires_at>=? "
                "GROUP BY r.logical_client_id,r.client_id,r.scopes_json,r.expires_at,"
                "c.client_json,c.last_authorized_at "
                "ORDER BY c.last_authorized_at DESC",
                (int(now),),
            ).fetchall()
        by_logical: dict[str, dict[str, object]] = {}
        for row in rows:
            logical_id = str(row["logical_client_id"])
            client = OAuthClientInformationFull.model_validate_json(str(row["client_json"]))
            current = by_logical.get(logical_id)
            active_until = int(row["expires_at"])
            if current is None:
                by_logical[logical_id] = {
                    "id": logical_id,
                    "name": client_display_name(client),
                    "scopes": _json_list(str(row["scopes_json"])),
                    "authorized_at": row["last_authorized_at"],
                    "active_until": active_until,
                    "token_families": int(row["family_count"]),
                }
            else:
                current["active_until"] = max(cast(int, current["active_until"]), active_until)
                current["token_families"] = cast(int, current["token_families"]) + int(
                    row["family_count"]
                )
                current["scopes"] = sorted(
                    set(cast(list[str], current["scopes"]))
                    | set(_json_list(str(row["scopes_json"])))
                )
        return list(by_logical.values())

    def revoke_logical_client(self, logical_client_id: str) -> bool:
        with self.transaction() as connection:
            client_rows = connection.execute(
                "SELECT DISTINCT client_id FROM edge_oauth_refresh_tokens "
                "WHERE logical_client_id=?",
                (logical_client_id,),
            ).fetchall()
            client_ids = [str(row[0]) for row in client_rows]
            revoked_at = _utc_now()
            access = connection.execute(
                "UPDATE edge_oauth_access_tokens SET revoked_at=COALESCE(revoked_at,?) "
                "WHERE logical_client_id=?",
                (revoked_at, logical_client_id),
            )
            refresh = connection.execute(
                "UPDATE edge_oauth_refresh_tokens SET revoked_at=COALESCE(revoked_at,?) "
                "WHERE logical_client_id=?",
                (revoked_at, logical_client_id),
            )
            for client_id in client_ids:
                connection.execute(
                    "DELETE FROM edge_oauth_requests WHERE client_id=?", (client_id,)
                )
                connection.execute("DELETE FROM edge_oauth_codes WHERE client_id=?", (client_id,))
            return access.rowcount > 0 or refresh.rowcount > 0

    def revoke_all_clients(self) -> None:
        with self.transaction() as connection:
            revoked_at = _utc_now()
            connection.execute(
                "UPDATE edge_oauth_access_tokens SET revoked_at=COALESCE(revoked_at,?)",
                (revoked_at,),
            )
            connection.execute(
                "UPDATE edge_oauth_refresh_tokens SET revoked_at=COALESCE(revoked_at,?)",
                (revoked_at,),
            )
            connection.execute("DELETE FROM edge_oauth_requests")
            connection.execute("DELETE FROM edge_oauth_codes")

    def issue_owner_ticket(self, *, lifetime_seconds: int = 300) -> str:
        if self.is_decommissioned():
            raise RuntimeError("Edge has been decommissioned")
        self.open_registration_window()
        ticket = secrets.token_urlsafe(40)
        with self.transaction() as connection:
            self._cleanup_expired(connection, time.time())
            connection.execute(
                "INSERT INTO edge_owner_tickets(ticket_hash,expires_at) VALUES(?,?)",
                (_digest(ticket), time.time() + lifetime_seconds),
            )
        return ticket

    def consume_owner_ticket(self, ticket: str) -> bool:
        if self.is_decommissioned():
            return False
        with self.transaction() as connection:
            updated = connection.execute(
                "UPDATE edge_owner_tickets SET consumed_at=? WHERE ticket_hash=? "
                "AND consumed_at IS NULL AND expires_at>=?",
                (_utc_now(), _digest(ticket), time.time()),
            )
        return updated.rowcount == 1

    def issue_owner_session(self, *, lifetime_seconds: int = 30 * 24 * 3600) -> str:
        if self.is_decommissioned():
            raise RuntimeError("Edge has been decommissioned")
        self.open_registration_window()
        session = secrets.token_urlsafe(48)
        now = time.time()
        with self.transaction() as connection:
            self._cleanup_expired(connection, now)
            connection.execute(
                "INSERT INTO edge_owner_sessions(session_hash,expires_at,created_at) VALUES(?,?,?)",
                (_digest(session), now + lifetime_seconds, _utc_now()),
            )
        return session

    def owner_session_valid(self, session: str | None) -> bool:
        if not session or self.is_decommissioned():
            return False
        with self._lock:
            row = self._connection.execute(
                "SELECT expires_at FROM edge_owner_sessions WHERE session_hash=? "
                "AND revoked_at IS NULL",
                (_digest(session),),
            ).fetchone()
        return row is not None and float(row[0]) >= time.time()

    def revoke_owner_session(self, session: str | None) -> None:
        if not session:
            return
        with self.transaction() as connection:
            connection.execute(
                "UPDATE edge_owner_sessions SET revoked_at=? WHERE session_hash=?",
                (_utc_now(), _digest(session)),
            )

    def revoke_all_owner_sessions(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE edge_owner_sessions SET revoked_at=COALESCE(revoked_at,?)",
                (_utc_now(),),
            )


class EdgeOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, EdgeRefreshToken, AccessToken]
):
    """OAuth provider with DCR, PKCE, audience binding, and rotating refresh tokens."""

    def __init__(
        self,
        store: EdgeOAuthStore,
        public_url: str,
        *,
        extra_redirect_origins: Iterable[str] = (),
    ) -> None:
        self.store = store
        self.public_url = normalize_edge_url(public_url)
        self.resource = f"{self.public_url}/mcp"
        self._extra_redirect_origins = frozenset(
            normalize_edge_url(origin) for origin in extra_redirect_origins
        )

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.store.client(client_id)

    def _redirect_allowed(self, value: str) -> bool:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower()
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if hostname in {"127.0.0.1", "::1", "localhost"} and parsed.scheme == "http":
            return True
        if parsed.scheme != "https":
            return False
        if origin in self._extra_redirect_origins:
            return True
        if hostname == "claude.ai" and parsed.path == "/api/mcp/auth_callback":
            return True
        return hostname == "chatgpt.com" and (
            parsed.path.startswith("/connector/oauth/")
            or parsed.path == "/connector_platform_oauth_redirect"
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not self.store.registration_open():
            raise RegistrationError(
                "invalid_client_metadata",
                "open Connect an AI app from the Core dashboard before registering",
            )
        redirects = client_info.redirect_uris or []
        if not redirects or len(redirects) > 10:
            raise RegistrationError(
                "invalid_redirect_uri", "between one and ten redirect URIs are required"
            )
        if len(client_info.client_id or "") > MAX_CLIENT_ID_LENGTH:
            raise RegistrationError("invalid_client_metadata", "client_id is too long")
        if len(client_info.client_name or "") > MAX_CLIENT_NAME_LENGTH:
            raise RegistrationError("invalid_client_metadata", "client_name is too long")
        if any(len(str(uri)) > MAX_REDIRECT_URI_LENGTH for uri in redirects):
            raise RegistrationError("invalid_redirect_uri", "redirect URI is too long")
        if any(not self._redirect_allowed(str(uri)) for uri in redirects):
            raise RegistrationError(
                "invalid_redirect_uri",
                "redirect URI is not an approved ChatGPT, Claude, or loopback callback",
            )
        serialized = client_info.model_dump_json(exclude_none=True)
        if len(serialized.encode("utf-8")) > MAX_CLIENT_METADATA_BYTES:
            raise RegistrationError(
                "invalid_client_metadata", "client metadata must be 16 KiB or smaller"
            )
        redirect_identities = {
            _provider_for_redirect(str(uri)) or "unverified" for uri in redirects
        }
        if len(redirect_identities) != 1:
            raise RegistrationError(
                "invalid_redirect_uri",
                "provider callbacks cannot be mixed with another provider or loopback callback",
            )
        if client_info.scope:
            requested = set(client_info.scope.split())
            if not requested.issubset(EDGE_SCOPES):
                raise RegistrationError("invalid_client_metadata", "unsupported OAuth scope")
        self.store.save_client(client_info)

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if params.resource != self.resource:
            raise AuthorizeError("invalid_request", "resource must match this Edge MCP endpoint")
        scopes = (
            params.scopes
            if params.scopes is not None
            else (client.scope.split() if client.scope else [READ_SCOPE])
        )
        if READ_SCOPE not in scopes or not set(scopes).issubset(EDGE_SCOPES):
            raise AuthorizeError("invalid_scope", "context:read is required")
        request_id = secrets.token_urlsafe(40)
        self.store.save_authorization_request(
            request_id,
            client,
            params,
            list(dict.fromkeys(scopes)),
            self.resource,
        )
        return f"{self.public_url}/oauth/consent?request_id={request_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        if not client.client_id:
            return None
        return self.store.authorization_code(authorization_code, client.client_id)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        if authorization_code.resource != self.resource:
            raise TokenError("invalid_grant", "authorization code has the wrong audience")
        return self.store.exchange_code(
            authorization_code.code,
            client,
            authorization_code.scopes,
            self.resource,
            authorization_code.subject or OWNER_SUBJECT,
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> EdgeRefreshToken | None:
        if not client.client_id:
            return None
        return self.store.refresh_token(refresh_token, client.client_id)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: EdgeRefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        if refresh_token.resource != self.resource:
            raise TokenError("invalid_grant", "refresh token has the wrong audience")
        return self.store.rotate_refresh(refresh_token, scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        return self.store.access_token(token, self.resource)

    async def revoke_token(self, token: AccessToken | EdgeRefreshToken) -> None:
        self.store.revoke(token.token)
