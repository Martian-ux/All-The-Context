"""FastAPI transport for the hosted Relay service."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import time
from collections import deque
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

from fastapi import Body, Depends, FastAPI, Form, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from allthecontext.edge_claim import EdgeClaimBundle, EdgeClaimError, EdgeClaimStore
from allthecontext.edge_setup import (
    EdgeEnrollmentBundle,
    EdgeSetupError,
    edge_instance_proof,
    hash_recovery_code,
    normalize_edge_url,
)
from allthecontext.relay.forwarding import EdgeForwardingBroker, ForwardingError
from allthecontext.relay.mcp import build_edge_mcp
from allthecontext.relay.oauth import EdgeOAuthProvider, EdgeOAuthStore
from allthecontext.relay.service import (
    AuthorizationError,
    ClientIdentity,
    EdgeDecommissionedError,
    EventSequenceError,
    InvalidEventPayloadError,
    ProposalConflictError,
    RelayService,
    ReplayMismatchError,
    SQLiteRelayStore,
)
from allthecontext.replication import (
    MAX_EDGE_REPLICATION_REQUEST_BYTES,
    JsonValue,
    PayloadHashError,
    ReplicationError,
    SignatureError,
    canonical_json,
)


@dataclass(frozen=True, slots=True)
class RelayApplicationConfig:
    database_path: Path
    replication_secret: bytes
    replication_bearer_token: str
    client_tokens: Mapping[str, ClientIdentity]
    public_url: str | None = None
    vault_id: str | None = None
    owner_secret_hash: str | None = None
    extra_redirect_origins: tuple[str, ...] = ()
    claim_bundle: EdgeClaimBundle | None = None


class ProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=64_000)
    scope: list[str] | str = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    sensitivity: str = Field(default="sensitive", min_length=1, max_length=100)
    availability: str = Field(default="core_available")
    source_service: str | None = Field(default=None, max_length=200)
    provenance: dict[str, Any] | list[Any] | str | None = None


class ProposalAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


class ContextErrorRequest(BaseModel):
    """Cloud-safe correction signal queued for authoritative Core review."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=64_000)
    evidence: str | None = Field(default=None, max_length=16_000)


class ForwardResponseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_token: str = Field(min_length=20, max_length=200)
    response: dict[str, Any]


class _TokenAuthenticator:
    def __init__(self, replication_token: str, clients: Mapping[str, ClientIdentity]) -> None:
        if not replication_token:
            raise ValueError("replication bearer token is required")
        self._replication_digest = self._digest(replication_token)
        self._client_by_digest = {
            self._digest(token): identity for token, identity in clients.items()
        }
        if any(not token for token in clients):
            raise ValueError("client bearer tokens cannot be empty")

    @staticmethod
    def _digest(token: str) -> bytes:
        return hashlib.sha256(token.encode("utf-8")).digest()

    @staticmethod
    def _bearer(authorization: str | None) -> str:
        if authorization is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="bearer authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid bearer authorization",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return token

    def require_replication(self, authorization: str | None) -> None:
        supplied = self._digest(self._bearer(authorization))
        if not hmac.compare_digest(supplied, self._replication_digest):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credential"
            )

    def rotate_replication(self, token: str) -> None:
        self._replication_digest = self._digest(token)

    def require_client(self, authorization: str | None) -> ClientIdentity:
        supplied = self._digest(self._bearer(authorization))
        identity = self._client_by_digest.get(supplied)
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credential"
            )
        return identity


class _SlidingWindowLimiter:
    """Small in-process guard for the intentionally public OAuth bootstrap routes."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._events: dict[str, deque[float]] = {}

    def allow(self, key: str, *, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True


class _RequestSizeLimitMiddleware:
    """Bound every public request, including chunked bodies without Content-Length."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int = 256 * 1024,
        max_replication_body_bytes: int = MAX_EDGE_REPLICATION_REQUEST_BYTES,
        max_query_bytes: int = 16 * 1024,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.max_replication_body_bytes = max_replication_body_bytes
        self.max_query_bytes = max_query_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if len(scope.get("query_string", b"")) > self.max_query_bytes:
            await self._reject(send, 414, "request query is too large")
            return
        body_limit = (
            self.max_replication_body_bytes
            if scope.get("path") == "/v1/replication/events"
            else self.max_body_bytes
        )
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                declared = int(content_length)
            except ValueError:
                declared = body_limit + 1
            if declared < 0 or declared > body_limit:
                await self._reject(send, 413, "request body is too large")
                return

        received = 0
        buffered: deque[Message] = deque()
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] != "http.request":
                break
            received += len(message.get("body", b""))
            if received > body_limit:
                await self._reject(send, 413, "request body is too large")
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> Message:
            if buffered:
                return buffered.popleft()
            return {"type": "http.disconnect"}

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(send: Send, status_code: int, detail: str) -> None:
        body = json.dumps({"detail": detail}, separators=(",", ":")).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status_code,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(
    service: RelayService,
    *,
    replication_bearer_token: str,
    client_tokens: Mapping[str, ClientIdentity],
    edge_provider: EdgeOAuthProvider | None = None,
    edge_pairing_secret: bytes | None = None,
    owner_secret_hash: str | None = None,
    vault_id: str | None = None,
    close_service_on_shutdown: bool = True,
    forwarding_broker: EdgeForwardingBroker | None = None,
    claim_store: EdgeClaimStore | None = None,
) -> FastAPI:
    """Build an app around an injected service (useful for tests and hosting)."""

    authenticator = _TokenAuthenticator(replication_bearer_token, client_tokens)
    pairing_secret = [bytes(edge_pairing_secret or b"")]
    edge_enabled = edge_provider is not None
    if edge_enabled and (
        owner_secret_hash is None or vault_id is None or edge_pairing_secret is None
    ):
        raise ValueError("OAuth Edge requires owner_secret_hash, vault_id, and pairing secret")
    if owner_secret_hash is not None:
        if len(owner_secret_hash) != 64:
            raise ValueError("owner_secret_hash must be a SHA-256 hex digest")
        try:
            bytes.fromhex(owner_secret_hash)
        except ValueError as exc:
            raise ValueError("owner_secret_hash must be hexadecimal") from exc

    if edge_provider is not None and vault_id is not None and edge_pairing_secret is not None:
        binding_key = (
            claim_store.bundle.signing_public_key.encode()
            if claim_store is not None
            else edge_pairing_secret
        )
        binding_fingerprint = hmac.new(
            binding_key,
            (f"all-the-context/edge-identity/v1\0{vault_id}\0{edge_provider.public_url}").encode(),
            hashlib.sha256,
        ).hexdigest()
        edge_provider.store.bind_instance(
            vault_id=vault_id,
            binding_fingerprint=binding_fingerprint,
        )

    edge_mcp = (
        build_edge_mcp(service, edge_provider, vault_id=vault_id, forwarding=forwarding_broker)
        if edge_provider is not None and vault_id is not None
        else None
    )
    edge_http_app = edge_mcp.streamable_http_app() if edge_mcp is not None else None

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            if edge_mcp is not None:
                async with edge_mcp.session_manager.run():
                    yield
            else:
                yield
        finally:
            if close_service_on_shutdown:
                service.close()
                if edge_provider is not None:
                    edge_provider.store.close()

    app = FastAPI(title="All The Context Relay", version="0.1.0", lifespan=lifespan)
    oauth_rate_limiter = _SlidingWindowLimiter()

    @app.middleware("http")
    async def edge_public_guards(request: Request, call_next: Any) -> Any:
        provider = edge_provider
        path = request.url.path
        if (
            claim_store is not None
            and not claim_store.acknowledged()
            and path
            not in {
                "/healthz",
                "/v1/edge/claim/challenge",
                "/v1/edge/claim",
                "/v1/edge/claim/ack",
            }
        ):
            return JSONResponse(
                status_code=423,
                content={"detail": "This Edge is awaiting its authorized Core claim"},
            )
        if (
            provider is not None
            and provider.store.is_decommissioned()
            and path
            not in {
                "/healthz",
                "/about",
                "/v1/edge/decommission",
            }
        ):
            return JSONResponse(
                status_code=410,
                content={"detail": "This All The Context Edge is decommissioned"},
            )
        if provider is not None and path == "/register":
            content_length = request.headers.get("content-length")
            if content_length is not None:
                try:
                    too_large = int(content_length) > 20 * 1024
                except ValueError:
                    too_large = True
                if too_large:
                    return JSONResponse(status_code=413, content={"detail": "request too large"})
            if not provider.store.registration_open():
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Open Connect an AI app from the Core dashboard first"},
                )
            client_host = request.client.host if request.client else "unknown"
            global_allowed = oauth_rate_limiter.allow(
                "register:global", limit=30, window_seconds=60
            )
            client_allowed = oauth_rate_limiter.allow(
                f"register:{client_host}", limit=12, window_seconds=60
            )
            if not global_allowed or not client_allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "OAuth registration rate limit exceeded"},
                    headers={"Retry-After": "60"},
                )
        return await call_next(request)

    def client_identity(
        authorization: Annotated[str | None, Header()] = None,
    ) -> ClientIdentity:
        return authenticator.require_client(authorization)

    def replication_authorized(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        authenticator.require_replication(authorization)
        if edge_provider is not None and edge_provider.store.is_decommissioned():
            raise HTTPException(status_code=410, detail="Edge is decommissioned")

    def replication_token_authorized(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        authenticator.require_replication(authorization)

    @app.get("/healthz")
    def health(
        challenge: str | None = None,
        requested_vault_id: Annotated[str | None, Query(alias="vault_id")] = None,
    ) -> dict[str, str]:
        result = {
            "status": (
                "decommissioned"
                if edge_provider is not None and edge_provider.store.is_decommissioned()
                else "awaiting_claim"
                if claim_store is not None and claim_store.credentials() is None
                else "ok"
            ),
            "component": "edge" if edge_enabled else "relay",
            "authority": "core",
        }
        if challenge is not None and (claim_store is None or claim_store.credentials() is not None):
            if edge_provider is None or vault_id is None:
                raise HTTPException(status_code=404, detail="Edge pairing is not enabled")
            if requested_vault_id != vault_id:
                raise HTTPException(status_code=404, detail="Edge vault not found")
            try:
                result["proof"] = edge_instance_proof(
                    pairing_secret[0],
                    public_url=edge_provider.public_url,
                    vault_id=vault_id,
                    challenge=challenge,
                )
            except EdgeSetupError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result

    @app.post("/v1/edge/claim/challenge")
    def edge_claim_challenge() -> dict[str, Any]:
        if claim_store is None:
            raise HTTPException(status_code=404, detail="Edge claim is not enabled")
        try:
            return {"claim_id": claim_store.bundle.claim_id, "challenge": claim_store.challenge()}
        except EdgeClaimError as exc:
            raise HTTPException(status_code=410, detail="Edge claim is unavailable") from exc

    @app.post("/v1/edge/claim")
    def complete_edge_claim(request: Annotated[dict[str, Any], Body()]) -> dict[str, str]:
        if claim_store is None:
            raise HTTPException(status_code=404, detail="Edge claim is not enabled")
        challenge = request.get("challenge")
        signature = request.get("signature")
        if not isinstance(challenge, str) or not isinstance(signature, str):
            raise HTTPException(status_code=422, detail="claim proof is invalid")
        try:
            envelope = claim_store.complete(challenge, signature)
            credentials = claim_store.credentials()
            assert credentials is not None
            new_secret, new_token = credentials
            service.rotate_replication_secret(new_secret.encode())
            authenticator.rotate_replication(new_token)
            pairing_secret[0] = new_secret.encode()
            return envelope
        except EdgeClaimError as exc:
            raise HTTPException(status_code=403, detail="claim proof was rejected") from exc

    @app.post("/v1/edge/claim/ack")
    def acknowledge_edge_claim(
        _authorized: None = Depends(replication_token_authorized),
    ) -> dict[str, bool]:
        if claim_store is None:
            raise HTTPException(status_code=404, detail="Edge claim is not enabled")
        claim_store.acknowledge()
        return {"claimed": True}

    @app.get("/v1/forward/requests")
    def claim_forward_requests(
        limit: Annotated[int, Query(ge=1, le=8)] = 8,
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, Any]:
        if forwarding_broker is None:
            raise HTTPException(status_code=404, detail="Core forwarding is not enabled")
        return {"items": forwarding_broker.claim(limit=limit)}

    @app.post("/v1/forward/requests/{request_id}/response")
    def answer_forward_request(
        request_id: str,
        request: ForwardResponseRequest,
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, bool]:
        if forwarding_broker is None:
            raise HTTPException(status_code=404, detail="Core forwarding is not enabled")
        try:
            forwarding_broker.answer(request_id, request.claim_token, request.response)
        except ForwardingError as exc:
            raise HTTPException(status_code=409, detail="request is no longer active") from exc
        return {"accepted": True}

    owner_cookie = "atc_edge_owner"

    def require_edge() -> tuple[EdgeOAuthProvider, str, str]:
        if edge_provider is None or vault_id is None or owner_secret_hash is None:
            raise HTTPException(status_code=404, detail="Hosted Edge setup is not enabled")
        return edge_provider, vault_id, owner_secret_hash

    def secured_html(content: str, *, status_code: int = 200) -> HTMLResponse:
        response = HTMLResponse(content=content, status_code=status_code)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; base-uri 'none'; frame-ancestors 'none'; "
            "form-action 'self'; style-src 'unsafe-inline'"
        )
        return response

    def page(title: str, body: str) -> str:
        return (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>"
            ":root{color-scheme:light}body{margin:0;background:#f4f7fb;color:#172033;"
            "font-family:Inter,ui-sans-serif,system-ui,sans-serif}.card{max-width:520px;"
            "margin:8vh auto;padding:42px;background:white;border:1px solid #d9e1ec;"
            "border-radius:18px;box-shadow:0 18px 60px #19345b18}h1{font-size:29px;"
            "letter-spacing:-.035em;margin:8px 0 12px}p,li{color:#59657a;line-height:1.55}"
            ".mark{color:#365ccf;font-size:12px;font-weight:800;letter-spacing:.12em}"
            "label{display:block;font-size:12px;font-weight:750;margin:24px 0 8px}"
            "input{box-sizing:border-box;width:100%;padding:13px;border:1px solid #b8c3d3;"
            "border-radius:9px;font:inherit}button,a.button{display:inline-flex;justify-content:center;"
            "box-sizing:border-box;padding:12px 18px;border:0;border-radius:9px;background:#365ccf;"
            "color:white;font-weight:750;text-decoration:none;cursor:pointer}.actions{display:flex;"
            "gap:10px;margin-top:26px}.secondary{background:#e8edf6!important;color:#26344d!important}"
            ".error{padding:12px;border-radius:8px;background:#fff0f0;color:#9a3030}code{display:block;"
            "overflow-wrap:anywhere;padding:12px;background:#f3f6fa;border-radius:8px}</style></head>"
            f"<body><main class='card'><div class='mark'>ALL THE CONTEXT &middot; EDGE</div>"
            f"{body}</main>"
            "</body></html>"
        )

    def consent_page(
        request_id: str,
        *,
        owner_authenticated: bool,
        error: str | None = None,
    ) -> HTMLResponse:
        provider, _active_vault, _owner_hash = require_edge()
        pending = provider.store.pending_authorization(request_id)
        if pending is None:
            return secured_html(
                page(
                    "Authorization expired",
                    "<h1>This connection request expired</h1>"
                    "<p>Return to your AI app and choose Connect again.</p>",
                ),
                status_code=410,
            )
        recovery = ""
        if not owner_authenticated:
            recovery = (
                "<label for='recovery'>Owner recovery code</label>"
                "<input id='recovery' name='recovery_code' autocomplete='one-time-code' required>"
                "<p>Find this in Edge setup on your Core computer. It is checked by Edge and "
                "is never sent to the AI app.</p>"
            )
        error_html = f"<p class='error'>{html.escape(error)}</p>" if error else ""
        scopes = "".join(f"<li>{html.escape(scope)}</li>" for scope in pending.scopes)
        content = (
            f"<h1>Connect {html.escape(pending.client_name)}</h1>"
            "<p>This grants access only to approved records marked "
            "<strong>always available</strong>. "
            "Raw sources, rejected candidates, and local-only context stay on Core.</p>"
            f"<ul>{scopes}</ul>{error_html}<form method='post' action='/oauth/consent'>"
            f"<input type='hidden' name='request_id' value='{html.escape(request_id, quote=True)}'>"
            f"{recovery}<div class='actions'>"
            "<button type='submit' name='decision' value='allow'>Allow connection</button>"
            "<button class='secondary' type='submit' name='decision' value='deny'>Decline</button>"
            "</div></form>"
        )
        return secured_html(page(f"Connect {pending.client_name}", content))

    @app.get("/about")
    def edge_about() -> HTMLResponse:
        provider, _active_vault, _owner_hash = require_edge()
        if provider.store.is_decommissioned():
            return secured_html(
                page(
                    "All The Context Edge",
                    "<h1>This Edge was decommissioned</h1>"
                    "<p>Remote access was revoked and active replicated records were removed. "
                    "Delete the hosted service, persistent disk, and provider backups under "
                    "the provider's retention policy.</p>",
                ),
                status_code=410,
            )
        return secured_html(
            page(
                "All The Context Edge",
                "<h1>Your approved context, when Core is away</h1>"
                "<p>This single-user Edge stores only records explicitly approved as always "
                "available as readable context. Remote proposals wait in a bounded encrypted "
                "transport queue for up to 30 days until Core imports and scrubs them. Core "
                "remains authoritative.</p>"
                f"<code>{html.escape(provider.resource)}</code>"
                "<div class='actions'><a class='button' href='/owner/recover'>"
                "Manage or connect AI apps</a></div>",
            )
        )

    @app.post("/v1/edge/owner-ticket")
    def create_owner_ticket(
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, str | int]:
        provider, _active_vault, _owner_hash = require_edge()
        ticket = provider.store.issue_owner_ticket()
        return {
            "connect_url": f"{provider.public_url}/owner/connect?ticket={ticket}",
            "expires_in": 300,
        }

    @app.get("/v1/edge/status")
    def edge_status(
        requested_vault_id: Annotated[str, Query(alias="vault_id")],
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, Any]:
        provider, active_vault, _owner_hash = require_edge()
        if requested_vault_id != active_vault:
            raise HTTPException(status_code=404, detail="Edge vault not found")
        return {
            "status": "ready",
            "authority": "core",
            "mcp_url": provider.resource,
            "last_applied_sequence": service.store.checkpoint(active_vault),
            "oauth_enabled": True,
            "proposal_queue": "encrypted_and_bounded",
            "core_forwarding": {
                "core_online": forwarding_broker.core_online() if forwarding_broker else False,
                **(
                    forwarding_broker.status() if forwarding_broker else {"queued": 0, "claimed": 0}
                ),
            },
        }

    @app.get("/v1/edge/clients")
    def edge_clients(
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, Any]:
        provider, _active_vault, _owner_hash = require_edge()
        items = provider.store.authorized_clients()
        return {"items": items, "count": len(items)}

    @app.delete("/v1/edge/clients/{logical_client_id}")
    def revoke_edge_client(
        logical_client_id: str,
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, Any]:
        provider, _active_vault, _owner_hash = require_edge()
        if not logical_client_id.startswith("edge:") or len(logical_client_id) > 200:
            raise HTTPException(status_code=422, detail="Invalid Edge client ID")
        revoked = provider.store.revoke_logical_client(logical_client_id)
        if not revoked:
            raise HTTPException(status_code=404, detail="Authorized Edge client not found")
        if forwarding_broker is not None:
            forwarding_broker.cancel_client(logical_client_id)
        return {"id": logical_client_id, "revoked": True}

    @app.post("/v1/edge/decommission")
    def decommission_edge(
        _authorized: None = Depends(replication_token_authorized),
    ) -> dict[str, Any]:
        provider, _active_vault, _owner_hash = require_edge()
        if not provider.store.is_decommissioned():
            provider.store.decommission()
        if forwarding_broker is not None:
            forwarding_broker.purge()
        # Always retry the purge. If a prior request was interrupted after the
        # terminal flag was committed, this request finishes erasing artifacts.
        records_remaining = service.purge_all()
        return {
            "status": "decommissioned",
            "records_remaining": records_remaining,
            "terminal": records_remaining == 0,
            "live_storage_compacted": records_remaining == 0,
        }

    @app.get("/owner/recover", response_model=None)
    def recover_owner(request: Request) -> HTMLResponse | RedirectResponse:
        provider, _active_vault, _owner_hash = require_edge()
        if provider.store.owner_session_valid(request.cookies.get(owner_cookie)):
            return RedirectResponse(url="/owner/ready", status_code=303)
        return secured_html(
            page(
                "Recover Edge access",
                "<h1>Manage your Edge</h1>"
                "<p>Enter the recovery code saved by Core during Edge setup. This creates an "
                "owner session on this browser and opens a ten-minute window for one of your "
                "AI apps to register.</p>"
                "<form method='post' action='/owner/recover'>"
                "<label for='recovery'>Owner recovery code</label>"
                "<input id='recovery' name='recovery_code' autocomplete='one-time-code' required>"
                "<div class='actions'><button type='submit'>Continue</button></div></form>",
            )
        )

    @app.post("/owner/recover", response_model=None)
    def complete_owner_recovery(
        request: Request,
        recovery_code: Annotated[str, Form()],
    ) -> HTMLResponse | RedirectResponse:
        provider, _active_vault, expected_owner_hash = require_edge()
        origin = request.headers.get("origin")
        if origin:
            try:
                valid_origin = normalize_edge_url(origin) == provider.public_url
            except EdgeSetupError:
                valid_origin = False
            if not valid_origin:
                raise HTTPException(status_code=403, detail="Invalid recovery form origin")
        try:
            supplied_hash = hash_recovery_code(recovery_code)
        except EdgeSetupError:
            supplied_hash = ""
        if not hmac.compare_digest(supplied_hash, expected_owner_hash):
            return secured_html(
                page(
                    "Recover Edge access",
                    "<h1>That code did not match</h1>"
                    "<p>Use the recovery code shown in Edge setup on your Core computer.</p>"
                    "<div class='actions'><a class='button' href='/owner/recover'>Try again</a>"
                    "</div>",
                ),
                status_code=401,
            )
        session = provider.store.issue_owner_session()
        response = RedirectResponse(url="/owner/ready", status_code=303)
        response.set_cookie(
            owner_cookie,
            session,
            max_age=30 * 24 * 3600,
            secure=provider.public_url.startswith("https://"),
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/owner/connect")
    def connect_owner(ticket: str) -> RedirectResponse:
        provider, _active_vault, _owner_hash = require_edge()
        if not provider.store.consume_owner_ticket(ticket):
            raise HTTPException(status_code=410, detail="Owner connection link expired")
        session = provider.store.issue_owner_session()
        response = RedirectResponse(url="/owner/ready", status_code=303)
        response.set_cookie(
            owner_cookie,
            session,
            max_age=30 * 24 * 3600,
            secure=provider.public_url.startswith("https://"),
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/owner/ready")
    def owner_ready(request: Request) -> HTMLResponse:
        provider, _active_vault, _owner_hash = require_edge()
        if not provider.store.owner_session_valid(request.cookies.get(owner_cookie)):
            return secured_html(
                page(
                    "Connect from Core",
                    "<h1>Open this from your Core dashboard</h1>"
                    "<p>Core creates a short-lived owner link without exposing its administrator "
                    "credential.</p>",
                ),
                status_code=401,
            )
        clients = provider.store.authorized_clients()
        client_rows = "".join(
            "<li><strong>"
            + html.escape(str(item["name"]))
            + "</strong><form method='post' action='/owner/apps/revoke'>"
            + "<input type='hidden' name='logical_client_id' value='"
            + html.escape(str(item["id"]), quote=True)
            + "'><button class='secondary' type='submit'>Disconnect</button></form></li>"
            for item in clients
        )
        apps = (
            "<h2>Authorized remote apps</h2><ul>" + client_rows + "</ul>"
            if client_rows
            else "<h2>Authorized remote apps</h2><p>None yet.</p>"
        )
        return secured_html(
            page(
                "Edge is ready",
                "<h1>Edge is ready for your AI apps</h1>"
                "<p>Add the MCP URL below in the provider's connector settings. OAuth will "
                "return here once so you can approve the connection.</p>"
                f"<code>{html.escape(provider.resource)}</code>"
                "<p>Once linked on the web, Claude custom connectors and ChatGPT "
                "developer-mode apps are also available in their iOS and Android apps. "
                "Workspace policy may gate setup.</p>"
                "<form method='post' action='/owner/registration-window'>"
                "<button type='submit'>Allow a new AI app for 10 minutes</button></form>"
                + apps
                + "<form method='post' action='/owner/logout'>"
                "<button class='secondary' type='submit'>Sign out of this browser</button>"
                "</form>",
            )
        )

    @app.post("/owner/registration-window", response_model=None)
    def open_owner_registration_window(
        request: Request,
    ) -> HTMLResponse | RedirectResponse:
        provider, _active_vault, _owner_hash = require_edge()
        if not provider.store.owner_session_valid(request.cookies.get(owner_cookie)):
            return secured_html(
                page("Sign in required", "<h1>Recover owner access first</h1>"),
                status_code=401,
            )
        origin = request.headers.get("origin")
        if origin:
            try:
                valid_origin = normalize_edge_url(origin) == provider.public_url
            except EdgeSetupError:
                valid_origin = False
            if not valid_origin:
                raise HTTPException(status_code=403, detail="Invalid registration form origin")
        provider.store.open_registration_window()
        return RedirectResponse(url="/owner/ready", status_code=303)

    @app.post("/owner/apps/revoke", response_model=None)
    def owner_revoke_app(
        request: Request,
        logical_client_id: Annotated[str, Form()],
    ) -> HTMLResponse | RedirectResponse:
        provider, _active_vault, _owner_hash = require_edge()
        if not provider.store.owner_session_valid(request.cookies.get(owner_cookie)):
            return secured_html(
                page("Sign in required", "<h1>Open this from Core again</h1>"),
                status_code=401,
            )
        origin = request.headers.get("origin")
        if origin:
            try:
                valid_origin = normalize_edge_url(origin) == provider.public_url
            except EdgeSetupError:
                valid_origin = False
            if not valid_origin:
                raise HTTPException(status_code=403, detail="Invalid app-management origin")
        if not provider.store.revoke_logical_client(logical_client_id):
            raise HTTPException(status_code=404, detail="Authorized Edge client not found")
        return RedirectResponse(url="/owner/ready", status_code=303)

    @app.post("/owner/logout")
    def owner_logout(request: Request) -> RedirectResponse:
        provider, _active_vault, _owner_hash = require_edge()
        origin = request.headers.get("origin")
        if origin:
            try:
                valid_origin = normalize_edge_url(origin) == provider.public_url
            except EdgeSetupError:
                valid_origin = False
            if not valid_origin:
                raise HTTPException(status_code=403, detail="Invalid logout form origin")
        provider.store.revoke_owner_session(request.cookies.get(owner_cookie))
        response = RedirectResponse(url="/about", status_code=303)
        response.delete_cookie(owner_cookie, path="/")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/oauth/consent")
    def oauth_consent(request: Request, request_id: str) -> HTMLResponse:
        provider, _active_vault, _owner_hash = require_edge()
        return consent_page(
            request_id,
            owner_authenticated=provider.store.owner_session_valid(
                request.cookies.get(owner_cookie)
            ),
        )

    @app.post("/oauth/consent", response_model=None)
    def complete_oauth_consent(
        request: Request,
        request_id: Annotated[str, Form()],
        decision: Annotated[str, Form()],
        recovery_code: Annotated[str | None, Form()] = None,
    ) -> HTMLResponse | RedirectResponse:
        provider, _active_vault, expected_owner_hash = require_edge()
        origin = request.headers.get("origin")
        if origin:
            try:
                valid_origin = normalize_edge_url(origin) == provider.public_url
            except EdgeSetupError:
                valid_origin = False
            if not valid_origin:
                raise HTTPException(status_code=403, detail="Invalid consent form origin")
        owner_authenticated = provider.store.owner_session_valid(request.cookies.get(owner_cookie))
        set_session = False
        if not owner_authenticated:
            try:
                supplied_hash = hash_recovery_code(recovery_code or "")
            except EdgeSetupError:
                supplied_hash = ""
            owner_authenticated = hmac.compare_digest(supplied_hash, expected_owner_hash)
            if not owner_authenticated:
                return consent_page(
                    request_id,
                    owner_authenticated=False,
                    error="That recovery code did not match.",
                )
            set_session = True
        if decision == "allow":
            location = provider.store.complete_authorization(request_id)
        elif decision == "deny":
            location = provider.store.deny_authorization(request_id)
        else:
            raise HTTPException(status_code=422, detail="decision must be allow or deny")
        response = RedirectResponse(url=location, status_code=303)
        if set_session:
            response.set_cookie(
                owner_cookie,
                provider.store.issue_owner_session(),
                max_age=30 * 24 * 3600,
                secure=provider.public_url.startswith("https://"),
                httponly=True,
                samesite="lax",
                path="/",
            )
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.get("/context/{record_id}")
    def owner_context_item(record_id: str, request: Request) -> HTMLResponse:
        provider, active_vault, _owner_hash = require_edge()
        if not provider.store.owner_session_valid(request.cookies.get(owner_cookie)):
            raise HTTPException(status_code=401, detail="Owner session required")
        record = service.owner_get(active_vault, record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Context item not found")
        return secured_html(
            page(
                str(record["kind"]).replace("_", " ").title(),
                f"<h1>{html.escape(str(record['kind']).replace('_', ' ').title())}</h1>"
                f"<p>{html.escape(str(record['content']))}</p>"
                f"<p>Version {int(record['version'])} &middot; approved on Core "
                "&middot; always available</p>",
            )
        )

    @app.post("/v1/replication/events")
    def apply_event(
        event: Annotated[dict[str, Any], Body()],
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, Any]:
        if edge_provider is not None and event.get("vault_id") != vault_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="replication event does not belong to this Edge vault",
            )
        try:
            result = service.apply(event)
        except (SignatureError, PayloadHashError) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        except EdgeDecommissionedError as exc:
            raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
        except ReplicationError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except EventSequenceError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": str(exc), "expected_sequence": exc.expected},
            ) from exc
        except ReplayMismatchError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except InvalidEventPayloadError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return {
            "accepted": True,
            "vault_id": result.vault_id,
            "sequence": result.sequence,
            "event_id": result.event_id,
            "replayed": result.replayed,
        }

    @app.get("/v1/context/search")
    def search_context(
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
        query: str = "",
        scope: Annotated[list[str] | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        try:
            results = service.search(identity, query=query, scopes=scope, limit=limit)
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return {"items": results, "count": len(results), "served_by": "relay"}

    @app.get("/v1/context/status")
    def context_status(
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
    ) -> dict[str, Any]:
        try:
            return service.status(identity)
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @app.get("/v1/context/{record_id}")
    def get_context_item(
        record_id: str,
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
    ) -> dict[str, Any]:
        try:
            item = service.get(identity, record_id)
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="context item not found"
            )
        return item

    @app.post("/v1/proposals", status_code=status.HTTP_202_ACCEPTED)
    def propose_memory(
        request: ProposalRequest,
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
    ) -> dict[str, Any]:
        proposal = request.model_dump(exclude={"idempotency_key"})
        try:
            queued, replayed = service.propose(
                identity,
                idempotency_key=request.idempotency_key,
                proposal=proposal,
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except EdgeDecommissionedError as exc:
            raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
        except ProposalConflictError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except (InvalidEventPayloadError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
        return {"proposal": queued, "replayed": replayed, "canonical": False}

    @app.post("/v1/ingestion/error", status_code=status.HTTP_202_ACCEPTED)
    def report_context_error(
        request: ContextErrorRequest,
        identity: ClientIdentity = Depends(client_identity),  # noqa: B008
    ) -> dict[str, Any]:
        proposal: dict[str, JsonValue] = {
            "kind": "context_error",
            "content": request.content,
            "scope": [],
            "sensitivity": "sensitive",
            "availability": "core_available",
            "source_service": "relay",
            "provenance": {
                "record_id": request.record_id,
                "suggested_correction": request.evidence,
            },
        }
        idempotency_key = hashlib.sha256(canonical_json(proposal).encode("utf-8")).hexdigest()
        try:
            queued, replayed = service.propose(
                identity,
                idempotency_key=f"context-error:{idempotency_key}",
                proposal=proposal,
            )
        except AuthorizationError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except EdgeDecommissionedError as exc:
            raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
        return {"proposal": queued, "replayed": replayed, "canonical": False}

    @app.get("/v1/replication/proposals")
    def queued_proposals(
        requested_vault_id: Annotated[str, Query(alias="vault_id")],
        _authorized: None = Depends(replication_authorized),
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    ) -> dict[str, Any]:
        if edge_provider is not None and requested_vault_id != vault_id:
            raise HTTPException(status_code=404, detail="Edge vault not found")
        items = service.queued_proposals(requested_vault_id, limit=limit)
        return {"items": items, "count": len(items), "canonical": False}

    @app.patch("/v1/replication/proposals/{proposal_id}")
    def acknowledge_proposal(
        proposal_id: str,
        requested_vault_id: Annotated[str, Query(alias="vault_id")],
        request: ProposalAckRequest,
        _authorized: None = Depends(replication_authorized),
    ) -> dict[str, Any]:
        if edge_provider is not None and requested_vault_id != vault_id:
            raise HTTPException(status_code=404, detail="Edge vault not found")
        if request.status not in {"imported", "rejected"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="status must be imported or rejected",
            )
        try:
            updated = service.acknowledge_proposal(requested_vault_id, proposal_id, request.status)
        except EdgeDecommissionedError as exc:
            raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(exc)) from exc
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="queued proposal not found"
            )
        return {"updated": True, "proposal_id": proposal_id, "status": request.status}

    # OAuth discovery, authorization, and Streamable HTTP MCP routes live in
    # the SDK application. Mount last so explicit health/replication/owner
    # routes above keep precedence.
    if edge_http_app is not None:
        app.mount("/", edge_http_app, name="edge-mcp")

    app.add_middleware(_RequestSizeLimitMiddleware)

    return app


def config_from_environment() -> RelayApplicationConfig:
    """Load deployment configuration without logging credential material."""

    try:
        from platformdirs import user_data_path
    except ImportError as exc:  # pragma: no cover - declared runtime dependency
        raise RuntimeError("platformdirs is required") from exc

    database_value = os.environ.get("ATC_RELAY_DATABASE")
    database_path = (
        Path(database_value).expanduser()
        if database_value
        else Path(user_data_path("AllTheContext", appauthor=False)) / "relay.sqlite3"
    )
    bundle_value = os.environ.get("ATC_EDGE_BUNDLE", "").strip()
    bundle: EdgeEnrollmentBundle | None = None
    claim_bundle: EdgeClaimBundle | None = None
    if bundle_value:
        try:
            if bundle_value.startswith("atc-edge-claim-v1."):
                claim_bundle = EdgeClaimBundle.decode(bundle_value)
            else:
                bundle = EdgeEnrollmentBundle.decode(bundle_value)
        except (EdgeSetupError, EdgeClaimError) as exc:
            raise RuntimeError(f"ATC_EDGE_BUNDLE is invalid: {exc}") from exc

    vault_id: str | None
    owner_secret_hash: str | None
    if claim_bundle is not None:
        secret = hashlib.sha256(claim_bundle.signing_public_key.encode()).digest()
        replication_token = hashlib.sha256(claim_bundle.claim_id.encode()).hexdigest()
        vault_id = claim_bundle.vault_id
        owner_secret_hash = claim_bundle.owner_secret_hash
    elif bundle is not None:
        secret = bundle.replication_secret.encode("utf-8")
        replication_token = bundle.replication_token
        vault_id = bundle.vault_id
        owner_secret_hash = bundle.owner_secret_hash
    else:
        secret = os.environ.get("ATC_RELAY_REPLICATION_SECRET", "").encode("utf-8")
        replication_token = os.environ.get("ATC_RELAY_BEARER_TOKEN", "")
        vault_id = os.environ.get("ATC_EDGE_VAULT_ID", "").strip() or None
        owner_secret_hash = os.environ.get("ATC_EDGE_OWNER_SECRET_HASH", "").strip() or None

    public_value = (
        os.environ.get("ATC_EDGE_PUBLIC_URL", "").strip()
        or os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    )
    public_url: str | None
    if public_value:
        try:
            public_url = normalize_edge_url(public_value)
        except EdgeSetupError as exc:
            raise RuntimeError(f"Edge public URL is invalid: {exc}") from exc
    else:
        public_url = None
    edge_values = (public_url, vault_id, owner_secret_hash)
    if any(value is not None for value in edge_values) and not all(
        value is not None for value in edge_values
    ):
        raise RuntimeError(
            "OAuth Edge requires a public URL, vault ID, and owner secret hash together"
        )

    redirects_value = os.environ.get("ATC_EDGE_EXTRA_REDIRECT_ORIGINS_JSON", "[]")
    try:
        raw_redirects = json.loads(redirects_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ATC_EDGE_EXTRA_REDIRECT_ORIGINS_JSON is not valid JSON") from exc
    if not isinstance(raw_redirects, list) or any(
        not isinstance(value, str) for value in raw_redirects
    ):
        raise RuntimeError("ATC_EDGE_EXTRA_REDIRECT_ORIGINS_JSON must be a list of origins")
    try:
        extra_redirects = tuple(normalize_edge_url(value) for value in raw_redirects)
    except EdgeSetupError as exc:
        raise RuntimeError(f"Edge redirect origin is invalid: {exc}") from exc

    clients_value = os.environ.get("ATC_RELAY_CLIENTS_JSON", "{}")
    try:
        raw_clients = json.loads(clients_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("ATC_RELAY_CLIENTS_JSON is not valid JSON") from exc
    if not isinstance(raw_clients, dict):
        raise RuntimeError("ATC_RELAY_CLIENTS_JSON must be an object keyed by bearer token")
    clients: dict[str, ClientIdentity] = {}
    for token, raw_identity in raw_clients.items():
        if not isinstance(token, str) or not isinstance(raw_identity, dict):
            raise RuntimeError("invalid Relay client configuration")
        try:
            clients[token] = ClientIdentity(
                client_id=str(raw_identity["client_id"]),
                vault_id=str(raw_identity["vault_id"]),
                permissions=frozenset(str(item) for item in raw_identity.get("permissions", [])),
                context_scopes=frozenset(
                    str(item) for item in raw_identity.get("context_scopes", [])
                ),
            )
        except (KeyError, TypeError) as exc:
            raise RuntimeError("invalid Relay client configuration") from exc
    return RelayApplicationConfig(
        database_path=database_path,
        replication_secret=secret,
        replication_bearer_token=replication_token,
        client_tokens=clients,
        public_url=public_url,
        vault_id=vault_id,
        owner_secret_hash=owner_secret_hash,
        extra_redirect_origins=extra_redirects,
        claim_bundle=claim_bundle,
    )


def build_environment_app() -> FastAPI:
    config = config_from_environment()
    relay_store = SQLiteRelayStore(config.database_path)
    claim_store = (
        EdgeClaimStore(config.database_path, config.claim_bundle, config.public_url)
        if config.claim_bundle is not None and config.public_url is not None
        else None
    )
    credentials = claim_store.credentials() if claim_store is not None else None
    runtime_secret = credentials[0].encode() if credentials else config.replication_secret
    runtime_token = credentials[1] if credentials else config.replication_bearer_token
    service = RelayService(relay_store, runtime_secret)
    forwarding = EdgeForwardingBroker(
        config.database_path,
        config.claim_bundle.encryption_public_key if config.claim_bundle is not None else None,
    )
    edge_provider: EdgeOAuthProvider | None = None
    if config.public_url is not None:
        oauth_store = EdgeOAuthStore(config.database_path)
        edge_provider = EdgeOAuthProvider(
            oauth_store,
            config.public_url,
            extra_redirect_origins=config.extra_redirect_origins,
        )
    return create_app(
        service,
        replication_bearer_token=runtime_token,
        client_tokens=config.client_tokens,
        edge_provider=edge_provider,
        edge_pairing_secret=runtime_secret if edge_provider is not None else None,
        owner_secret_hash=config.owner_secret_hash,
        vault_id=config.vault_id,
        forwarding_broker=forwarding if edge_provider is not None else None,
        claim_store=claim_store,
    )


def main() -> None:
    """Run Relay; remote deployments must put TLS in front of this process."""

    import uvicorn

    host = os.environ.get("ATC_RELAY_HOST", "127.0.0.1")
    port = int(os.environ.get("ATC_RELAY_PORT") or os.environ.get("PORT", "8743"))
    uvicorn.run(build_environment_app(), host=host, port=port, log_config=None)


if __name__ == "__main__":  # pragma: no cover
    main()
