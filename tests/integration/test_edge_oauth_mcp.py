from __future__ import annotations

import base64
import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from allthecontext.edge_setup import hash_recovery_code
from allthecontext.relay.app import create_app
from allthecontext.relay.oauth import EdgeOAuthProvider, EdgeOAuthStore
from allthecontext.relay.service import (
    ClientIdentity,
    EdgeDecommissionedError,
    RelayService,
    SQLiteRelayStore,
)
from allthecontext.replication import EventType, build_event, sign_event
from fastapi.testclient import TestClient

PUBLIC_URL = "https://edge.example.test"
VAULT_ID = "vault-edge-test"
REPLICATION_SECRET = b"edge-oauth-test-secret-at-least-32-bytes"
REPLICATION_TOKEN = "edge-oauth-replication-token-at-least-32-chars"
RECOVERY_CODE = "ABCD-EFGH-IJKL-MNOP-QRST-UVWX-YZ23-4567"


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _approved_event(
    sequence: int = 1,
    *,
    vault_id: str = VAULT_ID,
    record_id: str = "record-edge-1",
    kind: str = "interaction_preference",
    content: str = "Prefer concise, evidence-backed completion reports",
) -> dict[str, object]:
    payload = {
        "id": record_id,
        "kind": kind,
        "content": content,
        "scope": ["general"],
        "provenance": {"source_record_id": "source-1"},
        "source_service": "bootstrap",
        "confidence": 1.0,
        "sensitivity": "normal",
        "availability": "always_available",
        "allowed_clients": [],
        "denied_clients": [],
        "valid_from": "2026-01-01T00:00:00+00:00",
        "valid_until": None,
        "version": 1,
        "supersedes": None,
        "approval_status": "approved",
        "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        "updated_at": "2026-07-21T00:00:00+00:00",
    }
    return sign_event(
        build_event(
            vault_id=vault_id,
            sequence=sequence,
            event_type=EventType.RECORD_UPSERTED,
            record_id=record_id,
            payload=payload,  # type: ignore[arg-type]
            event_id=f"event-edge-{vault_id}-{sequence}",
            created_at="2026-07-21T00:00:00+00:00",
        ),
        REPLICATION_SECRET,
    ).wire_mapping()  # type: ignore[return-value]


def test_legacy_edge_with_tokens_cannot_be_adopted_by_new_enrollment(tmp_path: Path) -> None:
    database = tmp_path / "legacy-edge.sqlite3"
    initialized = SQLiteRelayStore(database)
    initialized.close()
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE edge_identity_state")
        connection.execute(
            "DELETE FROM relay_schema_migrations WHERE name='0005_edge_identity_binding.sql'"
        )
        connection.execute(
            "INSERT INTO edge_oauth_clients"
            "(client_id,client_json,registered_at,last_authorized_at) VALUES(?,?,?,?)",
            ("legacy-client", "{}", "2026-07-20T00:00:00+00:00", "2026-07-20T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO edge_oauth_access_tokens"
            "(token_hash,family_id,client_id,logical_client_id,scopes_json,resource,subject,"
            "expires_at,revoked_at) VALUES(?,?,?,?,?,?,?,?,NULL)",
            (
                "legacy-token-hash",
                "legacy-family",
                "legacy-client",
                "edge:legacy-client",
                '["context:read"]',
                f"{PUBLIC_URL}/mcp",
                "all-the-context-owner",
                int(time.time()) + 3600,
            ),
        )

    migrated_service = RelayService(SQLiteRelayStore(database), REPLICATION_SECRET)
    migrated_oauth = EdgeOAuthStore(database)
    migrated_provider = EdgeOAuthProvider(migrated_oauth, PUBLIC_URL)
    try:
        with pytest.raises(RuntimeError, match="legacy Edge database contains authority data"):
            create_app(
                migrated_service,
                replication_bearer_token="replacement-token",
                client_tokens={},
                edge_provider=migrated_provider,
                edge_pairing_secret=b"replacement-edge-secret-at-least-32-bytes",
                owner_secret_hash=hash_recovery_code(RECOVERY_CODE),
                vault_id="different-vault",
                close_service_on_shutdown=False,
            )
    finally:
        migrated_oauth.close()
        migrated_service.close()


def test_writer_that_passed_request_checks_cannot_commit_after_decommission(
    tmp_path: Path,
) -> None:
    database = tmp_path / "edge-race.sqlite3"
    relay_store = SQLiteRelayStore(database)
    service = RelayService(relay_store, REPLICATION_SECRET)
    oauth_store = EdgeOAuthStore(database)
    oauth_store.bind_instance(vault_id=VAULT_ID, binding_fingerprint="a" * 64)
    writer_ready = threading.Event()
    release_writer = threading.Event()
    failures: list[Exception] = []

    def delayed_writer() -> None:
        # This represents a request that passed the HTTP guard before the
        # decommission transaction committed, then resumed afterward.
        writer_ready.set()
        release_writer.wait(timeout=5)
        try:
            service.apply(_approved_event())
        except Exception as exc:
            failures.append(exc)

    writer = threading.Thread(target=delayed_writer)
    writer.start()
    assert writer_ready.wait(timeout=5)
    oauth_store.decommission()
    assert relay_store.purge_all() == 0
    release_writer.set()
    writer.join(timeout=5)

    assert not writer.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], EdgeDecommissionedError)
    assert relay_store.checkpoint(VAULT_ID) == 0
    with (
        sqlite3.connect(database) as connection,
        pytest.raises(sqlite3.IntegrityError, match="Edge is decommissioned"),
    ):
        connection.execute(
            "INSERT INTO edge_owner_sessions"
            "(session_hash,expires_at,created_at,revoked_at) VALUES(?,?,?,NULL)",
            ("post-terminal-session", time.time() + 60, "2026-07-21T00:00:00+00:00"),
        )

    oauth_store.close()
    service.close()


def test_terminal_edge_can_restart_and_finish_an_interrupted_purge(tmp_path: Path) -> None:
    database = tmp_path / "edge-interrupted-decommission.sqlite3"
    service = RelayService(SQLiteRelayStore(database), REPLICATION_SECRET)
    oauth_store = EdgeOAuthStore(database)
    oauth_store.bind_instance(vault_id=VAULT_ID, binding_fingerprint="b" * 64)
    service.apply(_approved_event())
    writer = ClientIdentity(
        client_id="edge:proposal-writer",
        vault_id=VAULT_ID,
        permissions=frozenset({"proposal:write"}),
        context_scopes=frozenset({"*"}),
    )
    service.propose(
        writer,
        idempotency_key="interrupted-purge-proposal",
        proposal={"kind": "preference", "content": "Keep this encrypted until purge"},
    )
    assert service.store.checkpoint(VAULT_ID) == 1
    assert len(service.queued_proposals(VAULT_ID)) == 1

    # Simulate a process loss after the durable terminal flag commits but
    # before the HTTP handler reaches RelayService.purge_all().
    oauth_store.decommission()
    assert oauth_store.is_decommissioned() is True
    oauth_store.close()
    service.close()

    reopened = RelayService(SQLiteRelayStore(database), REPLICATION_SECRET)
    reopened_oauth = EdgeOAuthStore(database)
    try:
        assert reopened_oauth.is_decommissioned() is True
        assert reopened.store.checkpoint(VAULT_ID) == 1
        assert reopened.purge_all() == 0
        assert reopened.store.checkpoint(VAULT_ID) == 0
        assert reopened.queued_proposals(VAULT_ID) == []
        reopened_oauth.decommission()
    finally:
        reopened_oauth.close()
        reopened.close()


def test_decommission_compacts_plaintext_from_live_sqlite_and_wal(tmp_path: Path) -> None:
    database = tmp_path / "decommission-hygiene.sqlite3"
    marker = "FORENSICALLY-DISTINCT-APPROVED-CONTEXT-MARKER"
    service = RelayService(SQLiteRelayStore(database), REPLICATION_SECRET)
    oauth_store = EdgeOAuthStore(database)
    provider = EdgeOAuthProvider(oauth_store, PUBLIC_URL)
    app = create_app(
        service,
        replication_bearer_token=REPLICATION_TOKEN,
        client_tokens={},
        edge_provider=provider,
        edge_pairing_secret=REPLICATION_SECRET,
        owner_secret_hash=hash_recovery_code(RECOVERY_CODE),
        vault_id=VAULT_ID,
        close_service_on_shutdown=False,
    )
    headers = {"Authorization": f"Bearer {REPLICATION_TOKEN}"}
    try:
        with TestClient(app, base_url=PUBLIC_URL) as client:
            replicated = client.post(
                "/v1/replication/events",
                headers=headers,
                json=_approved_event(content=marker),
            )
            assert replicated.status_code == 200
            live_files = list(tmp_path.glob(f"{database.name}*"))
            assert any(marker.encode() in path.read_bytes() for path in live_files)

            decommissioned = client.post("/v1/edge/decommission", headers=headers)
            assert decommissioned.status_code == 200
            assert decommissioned.json()["live_storage_compacted"] is True
    finally:
        oauth_store.close()
        service.close()

    assert all(
        marker.encode() not in path.read_bytes() for path in tmp_path.glob(f"{database.name}*")
    )


def test_edge_oauth_pkce_refresh_and_remote_mcp(tmp_path: Path) -> None:
    database = tmp_path / "edge.sqlite3"
    relay_store = SQLiteRelayStore(database)
    service = RelayService(relay_store, REPLICATION_SECRET)
    oauth_store = EdgeOAuthStore(database)
    provider = EdgeOAuthProvider(oauth_store, PUBLIC_URL)
    app = create_app(
        service,
        replication_bearer_token=REPLICATION_TOKEN,
        client_tokens={},
        edge_provider=provider,
        edge_pairing_secret=REPLICATION_SECRET,
        owner_secret_hash=hash_recovery_code(RECOVERY_CODE),
        vault_id=VAULT_ID,
        close_service_on_shutdown=False,
    )
    replication_headers = {"Authorization": f"Bearer {REPLICATION_TOKEN}"}

    try:
        with TestClient(app, base_url=PUBLIC_URL) as client:
            protected = client.get("/.well-known/oauth-protected-resource/mcp")
            assert protected.status_code == 200, protected.text
            assert protected.json()["resource"] == f"{PUBLIC_URL}/mcp"

            metadata = client.get("/.well-known/oauth-authorization-server")
            assert metadata.status_code == 200, metadata.text
            assert "S256" in metadata.json()["code_challenge_methods_supported"]

            ticket = client.post("/v1/edge/owner-ticket", headers=replication_headers)
            assert ticket.status_code == 200

            registered = client.post(
                "/register",
                json={
                    "client_name": "Claude",
                    "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": "context:read context:propose",
                },
            )
            assert registered.status_code == 201, registered.text
            client_id = registered.json()["client_id"]

            verifier = secrets.token_urlsafe(48)
            authorization = client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                    "scope": "context:read context:propose",
                    "state": "state-123",
                    "code_challenge": _pkce_challenge(verifier),
                    "code_challenge_method": "S256",
                    "resource": f"{PUBLIC_URL}/mcp",
                },
                follow_redirects=False,
            )
            assert authorization.status_code in {302, 307}, authorization.text
            consent_url = authorization.headers["location"]
            assert consent_url.startswith(f"{PUBLIC_URL}/oauth/consent?")

            owner = client.get(ticket.json()["connect_url"], follow_redirects=True)
            assert owner.status_code == 200
            assert "Edge is ready" in owner.text

            consent = client.get(consent_url)
            assert consent.status_code == 200
            request_id = parse_qs(urlsplit(consent_url).query)["request_id"][0]
            allowed = client.post(
                "/oauth/consent",
                data={"request_id": request_id, "decision": "allow"},
                follow_redirects=False,
            )
            assert allowed.status_code == 303, allowed.text
            callback = urlsplit(allowed.headers["location"])
            callback_query = parse_qs(callback.query)
            assert callback_query["state"] == ["state-123"]
            code = callback_query["code"][0]

            token = client.post(
                "/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": client_id,
                    "code": code,
                    "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                    "code_verifier": verifier,
                    "resource": f"{PUBLIC_URL}/mcp",
                },
            )
            assert token.status_code == 200, token.text
            access_token = token.json()["access_token"]
            refresh_token = token.json()["refresh_token"]

            # Existing access/refresh tokens must never become credentials for
            # a different authority reusing the same persistent Edge disk.
            for rebound_vault, rebound_secret in (
                ("different-vault", REPLICATION_SECRET),
                (VAULT_ID, b"replacement-edge-secret-at-least-32-bytes"),
            ):
                rebound_service = RelayService(SQLiteRelayStore(database), rebound_secret)
                rebound_oauth = EdgeOAuthStore(database)
                rebound_provider = EdgeOAuthProvider(rebound_oauth, PUBLIC_URL)
                try:
                    with pytest.raises(RuntimeError, match="already bound"):
                        create_app(
                            rebound_service,
                            replication_bearer_token="replacement-token",
                            client_tokens={},
                            edge_provider=rebound_provider,
                            edge_pairing_secret=rebound_secret,
                            owner_secret_hash=hash_recovery_code(RECOVERY_CODE),
                            vault_id=rebound_vault,
                            close_service_on_shutdown=False,
                        )
                finally:
                    rebound_oauth.close()
                    rebound_service.close()

            replicated = client.post(
                "/v1/replication/events",
                headers=replication_headers,
                json=_approved_event(),
            )
            assert replicated.status_code == 200, replicated.text

            mcp_headers = {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            }
            initialized = client.post(
                "/mcp",
                headers=mcp_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "edge-test", "version": "1"},
                    },
                },
            )
            assert initialized.status_code == 200, initialized.text
            assert initialized.json()["result"]["serverInfo"]["name"] == "All The Context Edge"

            searched = client.post(
                "/mcp",
                headers=mcp_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "search_context",
                        "arguments": {"query": "evidence-backed"},
                    },
                },
            )
            assert searched.status_code == 200, searched.text
            structured = searched.json()["result"]["structuredContent"]
            assert structured["items"][0]["id"] == "record-edge-1"

            for sequence in range(2, 123):
                bulk = client.post(
                    "/v1/replication/events",
                    headers=replication_headers,
                    json=_approved_event(
                        sequence,
                        record_id=f"record-edge-{sequence}",
                        kind="rare_kind" if sequence == 122 else "fact",
                        content=(
                            "pagination marker"
                            if sequence == 122
                            else f"Approved context item {sequence}"
                        ),
                    ),
                )
                assert bulk.status_code == 200, bulk.text

            paged = client.post(
                "/mcp",
                headers=mcp_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "search_context",
                        "arguments": {"query": "", "cursor": 100, "limit": 20},
                    },
                },
            )
            assert paged.status_code == 200, paged.text
            assert len(paged.json()["result"]["structuredContent"]["items"]) == 20

            rare = client.post(
                "/mcp",
                headers=mcp_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "search_context",
                        "arguments": {"query": "", "kinds": ["rare_kind"]},
                    },
                },
            )
            assert rare.status_code == 200, rare.text
            assert rare.json()["result"]["structuredContent"]["items"][0]["id"] == (
                "record-edge-122"
            )

            bootstrapped = client.post(
                "/mcp",
                headers=mcp_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 5,
                    "method": "tools/call",
                    "params": {
                        "name": "bootstrap_context",
                        "arguments": {"task_description": "unrelated mobile task"},
                    },
                },
            )
            assert bootstrapped.status_code == 200, bootstrapped.text
            assert bootstrapped.json()["result"]["structuredContent"]["items"][0]["id"] == (
                "record-edge-1"
            )

            context_status = client.post(
                "/mcp",
                headers=mcp_headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 6,
                    "method": "tools/call",
                    "params": {"name": "context_status", "arguments": {}},
                },
            )
            assert context_status.status_code == 200
            assert context_status.json()["result"]["structuredContent"]["available_records"] == 122

            refreshed = client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "resource": f"{PUBLIC_URL}/mcp",
                },
            )
            assert refreshed.status_code == 200, refreshed.text
            assert refreshed.json()["refresh_token"] != refresh_token
            assert refreshed.json()["access_token"] != access_token
            rotated_refresh = refreshed.json()["refresh_token"]

            authorized_clients = client.get("/v1/edge/clients", headers=replication_headers)
            assert authorized_clients.status_code == 200
            assert authorized_clients.json()["items"][0]["id"] == "edge:claude"
            revoked_client = client.delete(
                "/v1/edge/clients/edge%3Aclaude", headers=replication_headers
            )
            assert revoked_client.status_code == 200
            assert client.get("/v1/edge/clients", headers=replication_headers).json()["items"] == []

            replayed = client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "resource": f"{PUBLIC_URL}/mcp",
                },
            )
            assert replayed.status_code == 400

            revoked_family = client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": client_id,
                    "refresh_token": rotated_refresh,
                    "resource": f"{PUBLIC_URL}/mcp",
                },
            )
            assert revoked_family.status_code == 400

            read_only = client.post(
                "/register",
                json={
                    "client_name": "Read-only test client",
                    "redirect_uris": ["http://127.0.0.1:9284/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": "context:read",
                },
            )
            assert read_only.status_code == 201
            read_only_authorization = client.get(
                "/authorize",
                params={
                    "response_type": "code",
                    "client_id": read_only.json()["client_id"],
                    "redirect_uri": "http://127.0.0.1:9284/callback",
                    "state": "read-only-state",
                    "code_challenge": _pkce_challenge(verifier),
                    "code_challenge_method": "S256",
                    "resource": f"{PUBLIC_URL}/mcp",
                },
                follow_redirects=False,
            )
            assert read_only_authorization.status_code in {302, 307}
            read_only_request_id = parse_qs(
                urlsplit(read_only_authorization.headers["location"]).query
            )["request_id"][0]
            pending = oauth_store.pending_authorization(read_only_request_id)
            assert pending is not None
            assert pending.scopes == ("context:read",)

            logged_out = client.post("/owner/logout", follow_redirects=False)
            assert logged_out.status_code == 303
            assert client.get("/owner/ready").status_code == 401

            foreign_event = _approved_event(
                vault_id="vault-from-obsolete-build",
                record_id="foreign-record",
                content="must be erased across every vault",
            )
            rejected_foreign = client.post(
                "/v1/replication/events",
                headers=replication_headers,
                json=foreign_event,
            )
            assert rejected_foreign.status_code == 422
            assert service.owner_get("vault-from-obsolete-build", "foreign-record") is None
            # Simulate data written by an older build before vault binding existed.
            service.apply(foreign_event)
            assert service.owner_get("vault-from-obsolete-build", "foreign-record") is not None

            stale_ticket = client.post("/v1/edge/owner-ticket", headers=replication_headers).json()[
                "connect_url"
            ]

            decommissioned = client.post("/v1/edge/decommission", headers=replication_headers)
            assert decommissioned.status_code == 200
            assert decommissioned.json() == {
                "status": "decommissioned",
                "records_remaining": 0,
                "terminal": True,
                "live_storage_compacted": True,
            }
            assert service.store.checkpoint(VAULT_ID) == 0
            assert service.owner_get(VAULT_ID, "record-edge-1") is None
            assert service.owner_get("vault-from-obsolete-build", "foreign-record") is None
            assert client.get(stale_ticket).status_code == 410
            assert (
                client.post(
                    "/v1/replication/events",
                    headers=replication_headers,
                    json=_approved_event(),
                ).status_code
                == 410
            )
            assert (
                client.post(
                    "/register",
                    json={
                        "client_name": "Cannot revive Edge",
                        "redirect_uris": ["http://127.0.0.1:9284/callback"],
                    },
                ).status_code
                == 410
            )
            decommissioned_again = client.post("/v1/edge/decommission", headers=replication_headers)
            assert decommissioned_again.status_code == 200
            assert decommissioned_again.json()["terminal"] is True
            health = client.get(
                "/healthz",
                params={"challenge": "terminal-check-value", "vault_id": VAULT_ID},
            )
            assert health.status_code == 200
            assert health.json()["status"] == "decommissioned"
            assert (
                client.get(
                    "/healthz", params={"challenge": "short", "vault_id": VAULT_ID}
                ).status_code
                == 422
            )
    finally:
        oauth_store.close()
        service.close()


def test_owner_recovery_opens_persistent_bounded_registration_window(
    tmp_path: Path,
) -> None:
    database = tmp_path / "edge-recovery.sqlite3"
    service = RelayService(SQLiteRelayStore(database), REPLICATION_SECRET)
    oauth_store = EdgeOAuthStore(database)
    provider = EdgeOAuthProvider(oauth_store, PUBLIC_URL)
    app = create_app(
        service,
        replication_bearer_token=REPLICATION_TOKEN,
        client_tokens={},
        edge_provider=provider,
        edge_pairing_secret=REPLICATION_SECRET,
        owner_secret_hash=hash_recovery_code(RECOVERY_CODE),
        vault_id=VAULT_ID,
        close_service_on_shutdown=False,
    )
    try:
        with TestClient(app, base_url=PUBLIC_URL) as client:
            oversized_form = client.post(
                "/owner/recover",
                content=b"recovery_code=" + (b"x" * (257 * 1024)),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            assert oversized_form.status_code == 413
            chunked_form = client.post(
                "/owner/recover",
                content=iter([b"recovery_code=", b"x" * (257 * 1024)]),
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Transfer-Encoding": "chunked",
                },
            )
            assert chunked_form.status_code == 413
            assert client.get("/about?" + ("x" * (17 * 1024))).status_code == 414

            closed = client.post(
                "/register",
                json={
                    "client_name": "Closed window",
                    "redirect_uris": ["http://127.0.0.1:9284/callback"],
                },
            )
            assert closed.status_code == 403
            assert client.get("/owner/recover").status_code == 200
            assert (
                client.post(
                    "/owner/recover",
                    data={"recovery_code": "AAAA-BBBB-CCCC-DDDD-EEEE-FFFF-GGGG-HHHH"},
                ).status_code
                == 401
            )
            recovered = client.post(
                "/owner/recover",
                data={"recovery_code": RECOVERY_CODE},
                follow_redirects=False,
            )
            assert recovered.status_code == 303
            assert recovered.headers["location"] == "/owner/ready"
            assert client.get("/owner/ready").status_code == 200

            second_store = EdgeOAuthStore(database)
            try:
                assert second_store.registration_open() is True
            finally:
                second_store.close()

            oversized_name = client.post(
                "/register",
                json={
                    "client_name": "x" * 121,
                    "redirect_uris": ["http://127.0.0.1:9284/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": "context:read",
                },
            )
            assert oversized_name.status_code == 400

            registered = client.post(
                "/register",
                json={
                    "client_name": "Local recovery client",
                    "redirect_uris": ["http://127.0.0.1:9284/callback"],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    "scope": "context:read",
                },
            )
            assert registered.status_code == 201, registered.text
    finally:
        oauth_store.close()
        service.close()
