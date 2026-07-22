"""FastAPI transport for the local authoritative Core."""

import json
import os
import secrets
import tempfile
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from ..browser_session import (
    BROWSER_AUTH_SCHEME,
    BROWSER_STORAGE_KEY,
    DASHBOARD_REQUEST_HEADER,
    LEGACY_BROWSER_COOKIE,
    BrowserSessions,
    BrowserSessionTickets,
)
from ..client_config import (
    configure_claude,
    configure_codex,
    disconnect_claude,
    disconnect_codex,
    read_claude_config,
    read_codex_config,
)
from ..config import CoreConfig
from ..desktop_runtime import RuntimeCommand
from ..desktop_setup import (
    AI_CLIENT_SCOPES,
    CLAUDE_CLIENT_NAME,
    CODEX_CLIENT_NAME,
    delete_client_credential,
    ensure_client_access,
    recover_client_access,
    retire_other_named_clients,
)
from ..edge_connection import EdgeConnectionStore, EdgeSyncManager
from ..export import create_export
from ..ids import new_id
from ..instance_identity import ensure_instance_secret, instance_proof
from ..lifecycle import CoreInstanceLock
from ..models import (
    ApprovalRequest,
    ApprovalStatus,
    AvailabilityRequest,
    BeginIngestionRequest,
    BootstrapRequest,
    CandidateInput,
    ClientCreate,
    ContextErrorRequest,
    CorrectionRequest,
    FinishIngestionRequest,
    RejectRequest,
    SearchRequest,
    SubmitBatchRequest,
)
from ..security import ClientPrincipal
from ..storage import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    StorageError,
    durable_sqlite_footprint,
)
from .service import CoreService

DashboardPage = Literal[
    "sources",
    "review",
    "context",
    "connections",
    "relay",
    "audit",
    "backup",
]


class EdgeConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_url: str = Field(min_length=8, max_length=2_048)


class EdgeForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["DELETE HOSTED EDGE"]


class EdgeClientApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    context_scopes: list[str] = Field(default_factory=list, max_length=64)


def create_app(
    config: CoreConfig | None = None,
    *,
    service: CoreService | None = None,
    shutdown_callback: Callable[[], None] | None = None,
) -> FastAPI:
    active_config = config or CoreConfig.default()
    core = service or CoreService(active_config)
    edge_connections = EdgeConnectionStore(active_config)
    edge_sync = EdgeSyncManager(edge_connections, core.store)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        edge_sync.start()
        try:
            yield
        finally:
            edge_sync.stop()

    app = FastAPI(
        title="All The Context Core",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[active_config.host, "localhost", "[::1]", "testserver"],
    )
    app.state.core = core
    app.state.edge_connections = edge_connections
    app.state.edge_sync = edge_sync
    instance_secret = ensure_instance_secret(active_config)
    browser_tickets = BrowserSessionTickets()
    browser_sessions = BrowserSessions()
    dashboard_export_lock = threading.Lock()
    app.state.browser_tickets = browser_tickets
    app.state.browser_sessions = browser_sessions
    development_principal = (
        core.store.ensure_local_development_principal() if not active_config.require_auth else None
    )

    @app.exception_handler(StorageError)
    async def handle_storage_error(_request: Request, error: StorageError) -> JSONResponse:
        status = 500
        code = "storage_error"
        if isinstance(error, NotFoundError):
            status, code = 404, "not_found"
        elif isinstance(error, ConflictError):
            status, code = 409, "conflict"
        elif isinstance(error, InvalidStateError):
            status, code = 422, "invalid_state"
        return JSONResponse(
            status_code=status, content={"error": {"code": code, "message": str(error)}}
        )

    def principal_from_header(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ClientPrincipal:
        if authorization is None and development_principal is not None:
            request.state.atc_credential = None
            return development_principal
        if authorization is None:
            raise HTTPException(status_code=401, detail="Credential required")
        scheme, _, token = authorization.partition(" ")
        token = token.strip()
        credential: str | None
        if scheme == "Bearer":
            credential = token
            request.state.atc_credential = credential
        elif scheme == BROWSER_AUTH_SCHEME:
            if (
                request.method not in {"GET", "HEAD", "OPTIONS"}
                and request.headers.get(DASHBOARD_REQUEST_HEADER) != "1"
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Same-origin dashboard request required",
                )
            credential = browser_sessions.resolve(token)
            request.state.atc_credential = None
        else:
            raise HTTPException(status_code=401, detail="Unsupported authorization scheme")
        if not credential:
            raise HTTPException(status_code=401, detail="Credential expired or unavailable")
        principal = core.store.authenticate(credential)
        if principal is None:
            if scheme == BROWSER_AUTH_SCHEME:
                browser_sessions.revoke(token)
            raise HTTPException(status_code=401, detail="Invalid or revoked credential")
        return principal

    Principal = Annotated[ClientPrincipal, Depends(principal_from_header)]

    def require(principal: ClientPrincipal, scope: str) -> None:
        if (
            "*" not in principal.scopes
            and "admin" not in principal.scopes
            and scope not in principal.scopes
        ):
            raise HTTPException(status_code=403, detail=f"Missing required scope: {scope}")

    @app.get("/health")
    def health(challenge: str | None = None) -> dict[str, str]:
        result = {"status": "ok", "component": "core"}
        if challenge is not None:
            try:
                result["proof"] = instance_proof(active_config, challenge, instance_secret)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        return result

    @app.get("/v1/browser/connect")
    def connect_browser(ticket: str, page: DashboardPage | None = None) -> HTMLResponse:
        credential = browser_tickets.consume(ticket)
        if credential is None:
            raise HTTPException(status_code=410, detail="Browser connection link expired")
        if core.store.authenticate(credential) is None:
            raise HTTPException(status_code=401, detail="Browser credential is no longer valid")
        browser_token = browser_sessions.issue(credential)
        nonce = secrets.token_urlsafe(18)
        dashboard_target = f"/?page={page}" if page is not None else "/"
        content = (
            '<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="referrer" content="no-referrer"><title>Connecting…</title>'
            f'</head><body><script nonce="{nonce}">'
            f"sessionStorage.setItem({json.dumps(BROWSER_STORAGE_KEY)},"
            f"{json.dumps(browser_token)});"
            f"window.location.replace({json.dumps(dashboard_target)});"
            "</script></body></html>"
        )
        response = HTMLResponse(content=content)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
            f"script-src 'nonce-{nonce}'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Remove credentials issued by pre-0.1 builds. The replacement header
        # contains no secret and prevents the old host-wide cookie from being
        # sent to another service listening on a different loopback port.
        response.delete_cookie(LEGACY_BROWSER_COOKIE, path="/", samesite="strict")
        return response

    @app.post("/v1/setup")
    def setup(request: ClientCreate, http_request: Request) -> dict[str, Any]:
        if core.store.client_count() != 0:
            raise HTTPException(status_code=409, detail="Core setup is already complete")
        client_host = http_request.client.host if http_request.client else ""
        if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
            raise HTTPException(status_code=403, detail="Initial setup is loopback-only")
        scopes = sorted(
            {
                *request.scopes,
                "admin",
                "context:read",
                "context:status",
                "context:ingest",
                "context:propose",
            }
        )
        principal, token = core.store.create_client(request.model_copy(update={"scopes": scopes}))
        return {
            "client": {
                "id": principal.id,
                "name": principal.name,
                "scopes": sorted(principal.scopes),
            },
            "token": token,
            "token_notice": "This token is shown once. Store it in the OS credential store.",
        }

    @app.post("/v1/ingestion/begin")
    def begin_ingestion(request: BeginIngestionRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:ingest")
        return core.ingestion.begin(request, principal)

    @app.post("/v1/ingestion/batch")
    def submit_context_batch(request: SubmitBatchRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:ingest")
        return core.ingestion.submit(request, principal)

    @app.post("/v1/ingestion/finish")
    def finish_ingestion(request: FinishIngestionRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:ingest")
        return core.ingestion.finish(request)

    @app.post("/v1/ingestion/propose")
    def propose_memory(request: CandidateInput, principal: Principal) -> dict[str, Any]:
        require(principal, "context:propose")
        return core.ingestion.propose(request, principal).model_dump(mode="json")

    @app.post("/v1/ingestion/error")
    def report_context_error(request: ContextErrorRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:propose")
        return core.ingestion.report_error(request, principal).model_dump(mode="json")

    @app.post("/v1/context/search")
    def search_context(request: SearchRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:status")
        if request.cursor is not None:
            try:
                request = request.model_copy(update={"offset": int(request.cursor)})
            except ValueError as error:
                raise HTTPException(
                    status_code=422, detail="cursor must be an integer offset"
                ) from error
        response = core.retrieval.search(request, principal)
        result = response.model_dump(mode="json")
        next_offset = request.offset + len(response.items)
        result["next_cursor"] = str(next_offset) if next_offset < response.total else None
        return result

    @app.post("/v1/context/bootstrap")
    def bootstrap_context(request: BootstrapRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:read")
        return core.retrieval.bootstrap(request, principal).model_dump(mode="json")

    @app.get("/v1/context/status")
    def context_status(principal: Principal) -> dict[str, Any]:
        require(principal, "context:read")
        return core.store.status()

    @app.get("/v1/context/{record_id}")
    def get_context_item(record_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "context:read")
        record = core.retrieval.get(record_id, principal)
        if record is None:
            raise HTTPException(status_code=404, detail="Context item not found")
        return record.model_dump(mode="json")

    @app.post("/v1/admin/import")
    async def import_source(
        principal: Principal,
        file: Annotated[UploadFile, File()],
        source_service: Annotated[str, Form()] = "generic",
    ) -> dict[str, Any]:
        require(principal, "admin")
        content = await file.read(active_config.max_import_bytes + 1)
        if len(content) > active_config.max_import_bytes:
            raise InvalidStateError("import exceeds configured size limit")
        return core.imports.import_bytes(
            file.filename or "import.txt", content, source_service=source_service
        )

    @app.get("/v1/admin/candidates")
    def list_candidates(
        principal: Principal,
        status: ApprovalStatus | None = ApprovalStatus.PENDING,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        require(principal, "admin")
        items, total = core.store.list_candidates(
            status=status, source_id=source_id, limit=limit, offset=offset
        )
        return {"items": [item.model_dump(mode="json") for item in items], "total": total}

    @app.get("/v1/admin/sources")
    def list_sources(principal: Principal, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        require(principal, "admin")
        items, total = core.store.list_sources(limit=limit, offset=offset)
        return {"items": items, "total": total}

    @app.post("/v1/admin/candidates/{candidate_id}/approve")
    def approve_candidate(
        candidate_id: str, request: ApprovalRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        result = core.store.approve_candidate(candidate_id, request, actor=principal.id)
        edge_sync.trigger()
        return result.model_dump(mode="json")

    @app.post("/v1/admin/candidates/{candidate_id}/reject")
    def reject_candidate(
        candidate_id: str, request: RejectRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        return core.store.reject_candidate(
            candidate_id, reason=request.reason, actor=principal.id
        ).model_dump(mode="json")

    @app.post("/v1/admin/records/{record_id}/correct")
    def correct_record(
        record_id: str, request: CorrectionRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        result = core.store.correct_record(
            record_id,
            content=request.content,
            structured_value=request.structured_value,
            supersedes=request.supersedes,
            reason=request.reason,
            actor=principal.id,
        )
        edge_sync.trigger()
        return result.model_dump(mode="json")

    @app.post("/v1/admin/records/{record_id}/availability")
    def change_availability(
        record_id: str, request: AvailabilityRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        result = core.store.change_availability(
            record_id,
            request.availability,
            explicit_sensitive_replication=request.explicit_sensitive_replication,
            actor=principal.id,
        )
        edge_sync.trigger()
        return result.model_dump(mode="json")

    @app.post("/v1/admin/records/{record_id}/delete")
    def delete_record(
        record_id: str, request: RejectRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        result = core.store.delete_record(
            record_id, reason=request.reason or "deleted by user", actor=principal.id
        )
        edge_sync.trigger()
        return result

    @app.get("/v1/admin/records/{record_id}/history")
    def record_history(record_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return {"items": core.store.record_history(record_id)}

    @app.post("/v1/admin/clients")
    def create_client(request: ClientCreate, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        created, token = core.store.create_client(request)
        return {
            "client": {
                "id": created.id,
                "name": created.name,
                "scopes": sorted(created.scopes),
                "auto_approve": created.auto_approve,
            },
            "token": token,
            "token_notice": "This token is shown once.",
        }

    @app.get("/v1/admin/clients")
    def list_clients(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        items = core.store.list_clients()
        return {
            "items": [
                {
                    **item,
                    "protected": "admin" in item["scopes"] or "*" in item["scopes"],
                }
                for item in items
            ]
        }

    @app.post("/v1/admin/browser-session")
    def create_browser_session(http_request: Request, principal: Principal) -> dict[str, str]:
        require(principal, "admin")
        credential = getattr(http_request.state, "atc_credential", None)
        if not credential:
            raise HTTPException(status_code=409, detail="Authenticated browser handoff unavailable")
        ticket = browser_tickets.issue(credential)
        return {"connect_path": f"/v1/browser/connect?ticket={ticket}"}

    @app.post("/v1/admin/export")
    async def export_dashboard(http_request: Request, principal: Principal) -> FileResponse:
        """Create one complete encrypted export for a same-origin dashboard download."""
        require(principal, "admin")
        body = bytearray()
        async for chunk in http_request.stream():
            body.extend(chunk)
            if len(body) > 16 * 1024:
                raise HTTPException(status_code=413, detail="Export request is too large")
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Invalid export request") from exc
        if not isinstance(payload, dict) or set(payload) != {"passphrase"}:
            raise HTTPException(status_code=422, detail="Invalid export request")
        passphrase = payload.get("passphrase")
        if not isinstance(passphrase, str) or not 10 <= len(passphrase) <= 1_024:
            raise HTTPException(
                status_code=422,
                detail="Export passphrase must contain between 10 and 1024 characters",
            )
        if not dashboard_export_lock.acquire(blocking=False):
            raise HTTPException(status_code=429, detail="Another dashboard export is in progress")
        temporary_path: Path | None = None
        try:
            footprint = durable_sqlite_footprint(active_config.database_path)
            if footprint > active_config.max_dashboard_export_bytes:
                raise HTTPException(
                    status_code=413,
                    detail="The Core is too large for dashboard export; use the CLI instead",
                )
            descriptor, raw_path = tempfile.mkstemp(
                prefix="atc-dashboard-export-", suffix=".atcexp"
            )
            os.close(descriptor)
            temporary_path = Path(raw_path)
            await run_in_threadpool(
                create_export,
                active_config.database_path,
                temporary_path,
                passphrase,
                include_sources=True,
                include_audit=True,
            )

            def cleanup_export() -> None:
                try:
                    temporary_path.unlink(missing_ok=True)
                finally:
                    dashboard_export_lock.release()

            return FileResponse(
                temporary_path,
                media_type="application/octet-stream",
                filename="all-the-context-backup.atcexp",
                headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
                background=BackgroundTask(cleanup_export),
            )
        except HTTPException:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            dashboard_export_lock.release()
            raise
        except Exception as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            dashboard_export_lock.release()
            raise HTTPException(
                status_code=500, detail="Encrypted export could not be created"
            ) from exc

    def edge_status_payload() -> dict[str, Any]:
        """Return public Edge state without enrollment or replication credentials."""

        state_error: str | None = None
        material_error: str | None = None
        try:
            state = edge_connections.state()
        except RuntimeError:
            state = None
            state_error = "The saved Edge setup needs to be repaired."
        try:
            material = edge_connections.material()
        except RuntimeError:
            material = None
            material_error = "The saved Edge enrollment credential is invalid."
        material_available = material is not None

        mismatch_error: str | None = None
        if state is None and material_available:
            mismatch_error = (
                "An Edge enrollment credential remains, but its connection state is missing. "
                "Restore the state or delete the hosted service before forgetting this setup."
            )
        elif state is not None and not material_available:
            mismatch_error = (
                "The Edge connection is preserved, but its enrollment credential is missing. "
                "Core will not rotate or overwrite the existing remote service."
            )

        counts = core.store.status()["counts"]
        if state_error is not None or material_error is not None or mismatch_error is not None:
            connection_state = "degraded"
        elif state is None:
            connection_state = "not_configured"
        elif state.edge_url is None:
            connection_state = "prepared"
        elif state.last_error is not None:
            connection_state = "degraded"
        elif state.last_success_at is not None:
            connection_state = "ready"
        else:
            connection_state = "paired"

        wizard_state = (
            "recover"
            if connection_state == "degraded"
            else "preflight"
            if connection_state == "not_configured"
            else "deploy"
            if connection_state == "prepared"
            else "sync"
            if connection_state == "paired"
            else "connect"
        )

        edge_url = state.edge_url if state is not None else None
        last_error = (
            state_error
            or material_error
            or mismatch_error
            or (state.last_error if state is not None else None)
        )
        return {
            "configured": edge_url is not None and material_available,
            "remote_present": edge_url is not None,
            "credential_available": material_available,
            "state": connection_state,
            "vault_id": state.vault_id if state is not None else core.store.vault_id(),
            "edge_url": edge_url,
            "mcp_url": f"{edge_url}/mcp" if edge_url else None,
            "prepared_at": state.prepared_at if state is not None else None,
            "connected_at": state.connected_at if state is not None else None,
            "credential_storage": (
                material.credential_storage
                if material is not None
                else (state.credential_storage if state is not None else None)
            ),
            "last_sequence": state.last_sequence if state is not None else 0,
            "pending_events": int(counts["pending_replication_events"]),
            "last_success_at": state.last_success_at if state is not None else None,
            "last_error": last_error,
            "proposals_imported": state.proposals_imported if state is not None else 0,
            "wizard": {
                "state": wizard_state,
                "preflight_ok": material_available or state is None,
                "paired": edge_url is not None,
                "synchronized": connection_state == "ready",
                "ordinary_path_requires_terminal": False,
            },
            "deployment": {
                "provider": "render_blueprint",
                "deploy_url": os.environ.get("ATC_EDGE_DEPLOY_URL", "").strip() or None,
                "enrollment_environment_variable": "ATC_EDGE_BUNDLE",
                "requires_host_account": True,
                "estimated_monthly_cost_usd": 7.25,
                "cost_note": "Render Starter plus a 1 GB disk; bandwidth overages are extra.",
            },
            "providers": [
                {
                    "id": "claude",
                    "name": "Claude",
                    "web_supported": True,
                    "mobile_supported": True,
                    "setup_url": "https://claude.ai/settings/connectors",
                    "detail": (
                        "Claude remote custom connectors currently require Pro, Max, Team, or "
                        "Enterprise. Add one on claude.ai or Claude Desktop; an existing "
                        "connector can then be used on iOS and Android."
                    ),
                    "setup_steps": [
                        "Open Settings → Connectors on claude.ai or Claude Desktop.",
                        "Choose Add custom connector and enter the Remote MCP address.",
                        "Complete Edge authorization; the connector can then be used on mobile.",
                    ],
                },
                {
                    "id": "chatgpt",
                    "name": "ChatGPT",
                    "web_supported": True,
                    "mobile_supported": False,
                    "setup_url": "https://chatgpt.com/",
                    "detail": (
                        "Developer-mode MCP apps are currently a web-only beta for ChatGPT "
                        "Business, Enterprise, and Edu workspaces. Admin or owner policy applies."
                    ),
                    "setup_steps": [
                        (
                            "On ChatGPT web, an eligible workspace admin enables developer mode "
                            "under Apps or workspace permissions."
                        ),
                        ("Create or test a developer-mode app from the workspace Apps settings."),
                        (
                            "Paste the Remote MCP address, create the app, and complete Edge "
                            "authorization. Current developer-mode MCP apps are not on mobile."
                        ),
                    ],
                },
            ],
        }

    @app.get("/v1/admin/edge")
    def get_edge_status(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return edge_status_payload()

    @app.post("/v1/admin/edge/prepare")
    def prepare_edge(response: Response, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        try:
            material = edge_connections.prepare(core.store.vault_id())
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response.headers["Cache-Control"] = "no-store"
        return {
            **edge_status_payload(),
            "enrollment_bundle": (
                material.claim_bundle.encode()
                if material.claim_bundle
                else material.bundle.encode()
            ),
            "recovery_code": material.recovery_code,
            "secret_notice": (
                "The deployment claim contains only an expiring reference and Core public keys. "
                "Keep the separate recovery code private."
            ),
        }

    @app.post("/v1/admin/edge/deployment-env")
    def download_edge_claim(principal: Principal) -> Response:
        require(principal, "admin")
        material = edge_connections.material()
        if material is None or material.claim_bundle is None:
            raise HTTPException(status_code=409, detail="Prepare a new Edge claim first")
        return Response(
            content=f"ATC_EDGE_BUNDLE={material.claim_bundle.encode()}\n",
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": 'attachment; filename="setup.env"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/v1/admin/edge/connect")
    def connect_edge(request: EdgeConnectRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        try:
            edge_connections.connect(request.edge_url)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        synchronization = edge_sync.sync_now()
        edge_sync.trigger()
        return {**edge_status_payload(), "synchronization": synchronization}

    @app.post("/v1/admin/edge/sync")
    def synchronize_edge(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        result = edge_sync.sync_now()
        return {**edge_status_payload(), "synchronization": result}

    @app.post("/v1/admin/edge/secure-storage")
    def secure_edge_storage(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        try:
            edge_connections.migrate_credential_to_os_store()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return edge_status_payload()

    @app.post("/v1/admin/edge/owner-link")
    def create_edge_owner_link(principal: Principal) -> dict[str, str]:
        require(principal, "admin")
        try:
            return {"url": edge_sync.owner_link()}
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/admin/edge/clients")
    def list_edge_clients(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        try:
            items = edge_sync.authorized_clients()
        except RuntimeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        approved = {
            str(item["id"]): item
            for item in core.store.remote_edge_clients()
            if not bool(item["revoked"])
        }
        merged = [
            {
                **item,
                "core_approved": str(item.get("id")) in approved,
                "core_context_scopes": approved.get(str(item.get("id")), {}).get(
                    "context_scopes", []
                ),
            }
            for item in items
        ]
        return {"items": merged, "count": len(merged)}

    @app.post("/v1/admin/edge/clients/{logical_client_id}/approve")
    def approve_edge_client(
        logical_client_id: str,
        request: EdgeClientApprovalRequest,
        principal: Principal,
    ) -> dict[str, Any]:
        require(principal, "admin")
        try:
            approved = core.store.approve_remote_edge_client(
                logical_client_id,
                name=request.name,
                scopes=("context:read", "context:status"),
                context_scopes=request.context_scopes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        core.store.audit_access(
            principal.id,
            "edge.remote_client.approve",
            (),
            trace_id=new_id(),
            metadata={"remote_client_id": approved.id, "scopes": sorted(approved.scopes)},
        )
        return {"id": approved.id, "core_approved": True, "scopes": sorted(approved.scopes)}

    @app.delete("/v1/admin/edge/clients/{logical_client_id}")
    def revoke_edge_client(logical_client_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        core.store.revoke_remote_edge_client(logical_client_id)
        try:
            edge_sync.revoke_client(logical_client_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        core.store.audit_access(
            principal.id,
            "edge.remote_client.revoke",
            (),
            trace_id=new_id(),
            metadata={"remote_client_id": logical_client_id},
        )
        return {"id": logical_client_id, "revoked": True}

    @app.post("/v1/admin/edge/decommission")
    def decommission_edge(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        try:
            edge_sync.decommission()
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        core.store.revoke_all_remote_edge_clients()
        core.store.audit_access(
            principal.id,
            "edge.decommission",
            (),
            trace_id=new_id(),
        )
        return {
            "status": "decommissioned",
            "active_records_remaining": 0,
            "remote_access_revoked": True,
        }

    @app.post("/v1/admin/edge/forget")
    def forget_edge(request: EdgeForgetRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        if request.confirmation != "DELETE HOSTED EDGE":  # pragma: no cover - Literal validates
            raise HTTPException(status_code=422, detail="confirmation phrase does not match")
        try:
            state = edge_connections.state()
            material = edge_connections.material()
        except RuntimeError:
            state = None
            material = None
        if (
            state is not None
            and state.edge_url is not None
            and state.last_error is None
            and material is not None
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Edge is paired and manageable. Use Remove active data and disconnect "
                    "before forgetting its local recovery credential"
                ),
            )
        try:
            edge_sync.forget_local()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return edge_status_payload()

    @app.get("/v1/admin/integrations")
    def list_integrations(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        edge = edge_status_payload()

        def desktop_status(integration_id: str) -> dict[str, Any]:
            if integration_id == "chatgpt_codex":
                name = CODEX_CLIENT_NAME
                read_config = read_codex_config
                detail = "One local MCP connection for the Codex app, CLI, and editor extension."
            else:
                name = CLAUDE_CLIENT_NAME
                read_config = read_claude_config
                detail = "Direct private connection to Core through the local MCP adapter."
            base = {
                "id": integration_id,
                "name": name,
                "configured": False,
                "state": "disconnected",
                "reason": None,
                "mode": "local",
                "detail": detail,
            }
            try:
                configured = read_config()
            except (OSError, ValueError) as exc:
                return {
                    **base,
                    "state": "degraded",
                    "reason": f"The app configuration is invalid: {exc}",
                }
            if configured is None:
                return base

            runtime = RuntimeCommand.current()
            expected_command = runtime.mcp()
            actual_command = (configured.command, *configured.args)
            if actual_command != expected_command:
                return {
                    **base,
                    "state": "degraded",
                    "reason": "The MCP helper path is out of date. Choose Repair.",
                }
            target_url = f"http://{active_config.host}:{active_config.port}"
            if configured.env.get("ATC_TARGET_URL") != target_url:
                return {
                    **base,
                    "state": "degraded",
                    "reason": "The connection points at a different Core. Choose Repair.",
                }
            client_id = configured.env.get("ATC_CLIENT_ID", "")
            matching_client = next(
                (client for client in core.store.list_clients() if str(client["id"]) == client_id),
                None,
            )
            if (
                matching_client is None
                or matching_client["revoked"]
                or matching_client["name"] != name
                or set(matching_client.get("scopes", [])) != set(AI_CLIENT_SCOPES)
            ):
                return {
                    **base,
                    "state": "degraded",
                    "reason": (
                        "The connection credential is missing, revoked, or over-scoped. "
                        "Choose Repair."
                    ),
                }
            token = configured.env.get("ATC_CLIENT_TOKEN")
            if not token:
                access = recover_client_access(client_id, active_config)
                token = access.token if access else None
            authenticated = core.store.authenticate(token) if token else None
            if authenticated is None or authenticated.id != client_id:
                return {
                    **base,
                    "state": "degraded",
                    "reason": "The connection credential cannot be recovered. Choose Repair.",
                }
            return {**base, "configured": True, "state": "connected"}

        return {
            "apps": [
                desktop_status("chatgpt_codex"),
                desktop_status("claude"),
            ],
            "remote": {
                "configured": edge["configured"],
                "state": edge["state"],
                "edge_mcp_url": edge["mcp_url"],
                "detail": (
                    "Edge keeps approved always-available context reachable when Core is off."
                    if edge["configured"]
                    else "Set up Edge once for cloud clients and supported mobile apps."
                ),
            },
        }

    @app.post("/v1/admin/integrations/{integration_id}")
    def connect_integration(integration_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        if integration_id not in {"chatgpt_codex", "claude"}:
            raise HTTPException(status_code=404, detail="Unknown desktop integration")
        client_access = ensure_client_access(
            core.store,
            active_config,
            name=CODEX_CLIENT_NAME if integration_id == "chatgpt_codex" else CLAUDE_CLIENT_NAME,
            scopes=AI_CLIENT_SCOPES,
        )
        embedded_token = (
            None
            if client_access.credential_storage == "operating-system credential store"
            else client_access.token
        )
        runtime = RuntimeCommand.current()
        target_url = f"http://{active_config.host}:{active_config.port}"
        try:
            if integration_id == "chatgpt_codex":
                result = configure_codex(
                    runtime,
                    client_access.client_id,
                    token=embedded_token,
                    target_url=target_url,
                )
            else:
                result = configure_claude(
                    runtime,
                    client_access.client_id,
                    token=embedded_token,
                    target_url=target_url,
                )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        name = CODEX_CLIENT_NAME if integration_id == "chatgpt_codex" else CLAUDE_CLIENT_NAME
        retire_other_named_clients(
            core.store,
            active_config,
            name=name,
            keep_id=client_access.client_id,
        )
        return {
            "id": integration_id,
            "client_id": client_access.client_id,
            "configured": True,
            "changed": result.changed,
            "config_path": str(result.path),
            "backup_path": str(result.backup_path) if result.backup_path else None,
            "restart_required": True,
        }

    @app.delete("/v1/admin/integrations/{integration_id}")
    def disconnect_integration(integration_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        if integration_id not in {"chatgpt_codex", "claude"}:
            raise HTTPException(status_code=404, detail="Unknown desktop integration")
        name = CODEX_CLIENT_NAME if integration_id == "chatgpt_codex" else CLAUDE_CLIENT_NAME
        try:
            result = (
                disconnect_codex() if integration_id == "chatgpt_codex" else disconnect_claude()
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        revoked: list[str] = []
        for client in core.store.list_clients():
            if client["name"] != name or client["revoked"]:
                continue
            client_id = str(client["id"])
            core.store.revoke_client(client_id)
            delete_client_credential(client_id, active_config)
            revoked.append(client_id)
        return {
            "id": integration_id,
            "configured": False,
            "changed": result.changed,
            "config_path": str(result.path),
            "backup_path": str(result.backup_path) if result.backup_path else None,
            "revoked_client_ids": revoked,
            "restart_required": True,
        }

    @app.post("/v1/admin/clients/{client_id}/revoke")
    def revoke_client(client_id: str, principal: Principal) -> dict[str, bool]:
        require(principal, "admin")
        target = next(
            (item for item in core.store.list_clients() if str(item["id"]) == client_id),
            None,
        )
        if target is None or target["revoked"]:
            raise NotFoundError("client not found or already revoked")
        scopes = set(target["scopes"])
        if client_id == principal.id or "admin" in scopes or "*" in scopes:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Owner access cannot be revoked from the generic client list. "
                    "Use the desktop recovery flow to rotate it safely."
                ),
            )
        core.store.revoke_client(client_id)
        return {"revoked": True}

    @app.get("/v1/admin/audit")
    def list_audit(principal: Principal, limit: int = 100) -> dict[str, Any]:
        require(principal, "admin")
        return {"items": core.store.list_audit(limit=limit)}

    @app.post("/v1/admin/shutdown")
    def shutdown(principal: Principal) -> dict[str, bool]:
        require(principal, "admin")
        if shutdown_callback is None:
            raise HTTPException(status_code=503, detail="Shutdown is not available in this host")
        shutdown_callback()
        return {"shutting_down": True}

    dashboard_root = Path(__file__).parent.parent / "web"
    if dashboard_root.joinpath("index.html").is_file():
        app.mount("/", StaticFiles(directory=dashboard_root, html=True), name="dashboard")

    return app


def main() -> None:
    config = CoreConfig.default()
    servers: list[uvicorn.Server] = []

    def request_shutdown() -> None:
        if servers:
            servers[0].should_exit = True

    with CoreInstanceLock(config):
        app = create_app(config, shutdown_callback=request_shutdown)
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=config.host,
                port=config.port,
                log_config=None,
                timeout_graceful_shutdown=5,
            )
        )
        servers.append(server)
        server.run()


if __name__ == "__main__":
    main()
