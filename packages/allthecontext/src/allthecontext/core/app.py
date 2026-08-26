"""FastAPI transport for the local authoritative Core."""

import asyncio
import base64
import binascii
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from functools import partial
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
    Query,
    Request,
    UploadFile,
)
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from .. import __version__
from ..browser_session import (
    BROWSER_AUTH_SCHEME,
    BROWSER_STORAGE_KEY,
    DASHBOARD_REQUEST_HEADER,
    LEGACY_BROWSER_COOKIE,
    BrowserSessions,
    BrowserSessionTickets,
)
from ..capture import CaptureError, CaptureRunResult
from ..capture_runtime import (
    authorize_local_workspace,
    refresh_local_workspace_adapter,
    reject_reserved_workspace_provider,
)
from ..capture_scheduler import scheduler_update_health_forced_off
from ..client_config import (
    claude_is_detected,
    codex_is_detected,
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
    configure_client_access_transactionally,
    delete_client_credential,
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
    ForgetContextRequest,
    MemoryTruthStatus,
    ObservationDisposition,
    PurgeRequest,
    RejectRequest,
    RestoreRequest,
    SearchCursor,
    SearchRequest,
    SubmitBatchRequest,
)
from ..project_runtime import (
    RUNTIME_MAX_CAPSULE_CHARS,
    RUNTIME_MAX_CAPSULE_ITEMS,
    AmbientProjectActivation,
    ProjectRuntimeError,
    activate_project_context,
    build_project_runtime,
    capsule_for_project,
    project_list_payload,
)
from ..security import ClientPrincipal
from ..storage import (
    ConflictError,
    InvalidStateError,
    NotFoundError,
    StorageError,
    durable_sqlite_footprint,
)
from ..updater import Channel, UpdateConfig, UpdateError, UpdateManager, UpdatePhase
from .service import CoreService

DashboardPage = Literal[
    "sources",
    "context",
    "connections",
    "activity",
    "backup",
    "updates",
]

_SEARCH_CURSOR_VERSION = "atc-search-v1"
_SEARCH_CURSOR_PART_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class _InvalidSearchCursor(ValueError):
    pass


def _search_request_fingerprint(request: SearchRequest) -> str:
    criteria = request.model_dump(mode="json", exclude={"cursor", "offset"})
    canonical = json.dumps(
        criteria,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _urlsafe_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _urlsafe_decode(value: str) -> bytes:
    if not value or not _SEARCH_CURSOR_PART_RE.fullmatch(value):
        raise _InvalidSearchCursor
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as error:
        raise _InvalidSearchCursor from error
    if _urlsafe_encode(decoded) != value:
        raise _InvalidSearchCursor
    return decoded


def _search_cursor_message(
    encoded_payload: str,
    principal: ClientPrincipal,
) -> bytes:
    return f"{_SEARCH_CURSOR_VERSION}.{encoded_payload}\0{principal.id}".encode()


def _encode_search_cursor(
    request: SearchRequest,
    principal: ClientPrincipal,
    instance_secret: str,
    offset: int,
) -> str:
    payload = SearchCursor(
        version=1,
        offset=offset,
        request_fingerprint=_search_request_fingerprint(request),
    ).model_dump(mode="json")
    encoded_payload = _urlsafe_encode(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    signature = hmac.new(
        instance_secret.encode("utf-8"),
        _search_cursor_message(encoded_payload, principal),
        hashlib.sha256,
    ).digest()
    return f"{_SEARCH_CURSOR_VERSION}.{encoded_payload}.{_urlsafe_encode(signature)}"


def _decode_search_cursor(
    value: str,
    request: SearchRequest,
    principal: ClientPrincipal,
    instance_secret: str,
) -> int:
    try:
        version, encoded_payload, encoded_signature = value.split(".")
        if version != _SEARCH_CURSOR_VERSION:
            raise _InvalidSearchCursor
        supplied_signature = _urlsafe_decode(encoded_signature)
        expected_signature = hmac.new(
            instance_secret.encode("utf-8"),
            _search_cursor_message(encoded_payload, principal),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise _InvalidSearchCursor
        payload = json.loads(_urlsafe_decode(encoded_payload).decode("utf-8"))
        cursor = SearchCursor.model_validate(payload)
        if not hmac.compare_digest(
            cursor.request_fingerprint,
            _search_request_fingerprint(request),
        ):
            raise _InvalidSearchCursor
        return cursor.offset
    except (
        UnicodeDecodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
        ValidationError,
    ) as error:
        raise _InvalidSearchCursor from error


class EdgeForgetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: Literal["DELETE HOSTED EDGE"]


class UpdatePreferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    channel: Channel


class CaptureCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    account_label: str
    account_fingerprint: str | None = None
    requested_scopes: list[str] = Field(default_factory=list, max_length=64)
    local_only_acknowledged: bool = False


class CaptureWorkspaceAuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str = Field(min_length=1, max_length=16_384)
    local_only_acknowledged: StrictBool


def create_app(
    config: CoreConfig | None = None,
    *,
    service: CoreService | None = None,
    shutdown_callback: Callable[[], None] | None = None,
    update_manager: UpdateManager | None = None,
) -> FastAPI:
    active_config = config or CoreConfig.default()
    core = service or CoreService(active_config)
    # Legacy Edge stores exist only for isolated cleanup of pre-V1 residual state.
    # Ordinary Core operation never starts the sync worker, enrolls, connects, or
    # triggers outbound replication.
    legacy_edge_connections = EdgeConnectionStore(active_config)
    legacy_edge_sync = EdgeSyncManager(legacy_edge_connections, core.store)
    default_update = UpdateConfig.default()
    updates = update_manager or UpdateManager(
        UpdateConfig(
            data_dir=active_config.data_dir / "updates",
            keyring_path=default_update.keyring_path,
            manifest_urls=default_update.manifest_urls,
            current_version=default_update.current_version,
            platform_name=default_update.platform_name,
            architecture=default_update.architecture,
        ),
        database_path=active_config.database_path,
    )
    operation_observer_executor: ThreadPoolExecutor | None = None
    operation_observer_executor_lock = threading.Lock()

    def get_operation_observer_executor() -> ThreadPoolExecutor:
        nonlocal operation_observer_executor
        with operation_observer_executor_lock:
            if operation_observer_executor is None:
                operation_observer_executor = ThreadPoolExecutor(
                    max_workers=1,
                    thread_name_prefix="atc-operation-observer",
                )
            return operation_observer_executor

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        nonlocal operation_observer_executor
        observer_executor = get_operation_observer_executor()
        try:
            await run_in_threadpool(core.store.resume_purge_jobs, limit=1)
            if not scheduler_update_health_forced_off():
                await run_in_threadpool(core.capture_scheduler.start)
                if (
                    updates.preferences.enabled
                    and updates.preferences.channel in updates.config.manifest_urls
                ):
                    threading.Thread(target=updates.scheduled_check, daemon=True).start()
                if updates.state.phase in {UpdatePhase.INSTALLING, UpdatePhase.RESTART_REQUIRED}:
                    recovery = threading.Timer(1.0, updates.recover_after_restart)
                    recovery.daemon = True
                    recovery.start()
            # Never start the legacy Edge network worker. Cleanup routes construct
            # outbound contacts only when an operator explicitly decommissions an
            # already-configured residual connection.
            yield
        finally:
            try:
                await run_in_threadpool(core.capture_scheduler.shutdown)
            finally:
                try:
                    await asyncio.get_running_loop().run_in_executor(
                        observer_executor,
                        core.store.close_import_operation_observer,
                    )
                finally:
                    try:
                        observer_executor.shutdown(wait=True, cancel_futures=True)
                    finally:
                        with operation_observer_executor_lock:
                            if operation_observer_executor is observer_executor:
                                operation_observer_executor = None

    app = FastAPI(
        title="All The Context Core",
        version=__version__,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[active_config.host, "localhost", "[::1]", "testserver"],
    )
    app.state.core = core
    app.state.legacy_edge_connections = legacy_edge_connections
    app.state.legacy_edge_sync = legacy_edge_sync
    app.state.updates = updates
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
        if isinstance(error, CaptureError):
            status, code = 422, error.code
        elif isinstance(error, NotFoundError):
            status, code = 404, "not_found"
        elif isinstance(error, ConflictError):
            status, code = 409, "conflict"
        elif isinstance(error, InvalidStateError):
            status, code = 422, "invalid_state"
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": code if isinstance(error, CaptureError) else str(error),
                }
            },
        )

    def _credential_from_header(
        request: Request,
        authorization: str | None,
    ) -> tuple[str, str, str] | None:
        if authorization is None and development_principal is not None:
            request.state.atc_credential = None
            return None
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
        return scheme, token, credential

    def principal_from_header(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> ClientPrincipal:
        credential_parts = _credential_from_header(request, authorization)
        if credential_parts is None:
            assert development_principal is not None
            return development_principal
        scheme, token, credential = credential_parts
        principal = core.store.authenticate(credential)
        if principal is None:
            if scheme == BROWSER_AUTH_SCHEME:
                browser_sessions.revoke(token)
            raise HTTPException(status_code=401, detail="Invalid or revoked credential")
        return principal

    async def operation_observer_from_header(
        operation_id: str,
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> tuple[ClientPrincipal, dict[str, Any] | None]:
        observer_executor = get_operation_observer_executor()
        credential_parts = _credential_from_header(request, authorization)
        if credential_parts is None:
            assert development_principal is not None
            operation = await asyncio.get_running_loop().run_in_executor(
                observer_executor,
                core.import_operations.get_operation,
                operation_id,
            )
            return development_principal, operation
        scheme, token, credential = credential_parts
        observe = partial(
            core.store.authenticate_import_operation_observer,
            credential,
            operation_id,
        )
        observation = await asyncio.get_running_loop().run_in_executor(
            observer_executor,
            observe,
        )
        if observation is None:
            if scheme == BROWSER_AUTH_SCHEME:
                browser_sessions.revoke(token)
            raise HTTPException(status_code=401, detail="Invalid or revoked credential")
        return observation

    Principal = Annotated[ClientPrincipal, Depends(principal_from_header)]
    OperationObserver = Annotated[
        tuple[ClientPrincipal, dict[str, Any] | None],
        Depends(operation_observer_from_header),
    ]

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
            f'</head><body><script nonce="{html.escape(nonce, quote=True)}"'
            f' data-storage-key="{html.escape(BROWSER_STORAGE_KEY, quote=True)}"'
            f' data-browser-token="{html.escape(browser_token, quote=True)}"'
            f' data-dashboard-target="{html.escape(dashboard_target, quote=True)}">'
            "const handoff=document.currentScript;"
            "sessionStorage.setItem(handoff.dataset.storageKey,handoff.dataset.browserToken);"
            "window.location.replace(handoff.dataset.dashboardTarget);"
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

    @app.post("/v1/browser/session/revoke")
    def revoke_browser_session(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> dict[str, Any]:
        """Revoke the short-lived browser capability for this tab session."""
        if authorization is None:
            raise HTTPException(status_code=401, detail="Credential required")
        scheme, _, token = authorization.partition(" ")
        token = token.strip()
        if scheme != BROWSER_AUTH_SCHEME or not token:
            raise HTTPException(
                status_code=400,
                detail="Browser session revocation requires Browser authorization",
            )
        if request.headers.get(DASHBOARD_REQUEST_HEADER) != "1":
            raise HTTPException(
                status_code=403,
                detail="Same-origin dashboard request required",
            )
        browser_sessions.revoke(token)
        return {"revoked": True}

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
        return core.ingestion.finish(request, principal)

    @app.post("/v1/ingestion/propose")
    def propose_memory(request: CandidateInput, principal: Principal) -> dict[str, Any]:
        require(principal, "context:propose")
        return core.ingestion.propose(request, principal).model_dump(mode="json")

    @app.post("/v1/ingestion/error")
    def report_context_error(request: ContextErrorRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:propose")
        return core.ingestion.report_error(request, principal).model_dump(mode="json")

    @app.post("/v1/ingestion/forget")
    def forget_context(request: ForgetContextRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:propose")
        return core.ingestion.forget(request, principal)

    @app.post("/v1/context/search")
    def search_context(request: SearchRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:read")
        if request.cursor is not None:
            try:
                offset = _decode_search_cursor(
                    request.cursor,
                    request,
                    principal,
                    instance_secret,
                )
                request = SearchRequest.model_validate(
                    {
                        **request.model_dump(mode="json"),
                        "offset": offset,
                        "cursor": None,
                    }
                )
            except (TypeError, ValueError, ValidationError) as error:
                raise HTTPException(
                    status_code=422,
                    detail="invalid or request-mismatched search cursor",
                ) from error
        response = core.retrieval.search(request, principal)
        result = response.model_dump(mode="json")
        next_offset = request.offset + len(response.items)
        result["next_cursor"] = (
            _encode_search_cursor(request, principal, instance_secret, next_offset)
            if response.items and next_offset <= 100_000 and next_offset < response.total
            else None
        )
        return result

    @app.post("/v1/context/bootstrap")
    def bootstrap_context(request: BootstrapRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "context:read")
        project_budget = min(
            RUNTIME_MAX_CAPSULE_CHARS,
            max(1, request.budget_chars // 2),
        )
        try:
            project_snapshot = build_project_runtime(
                core.store,
                character_budget=project_budget,
                item_budget=min(32, RUNTIME_MAX_CAPSULE_ITEMS),
                principal=principal,
            )
            project_activation = activate_project_context(
                project_snapshot,
                task_description=request.query,
                current_project=request.current_project,
                host_project_hint=request.host_project_hint,
            )
        except ProjectRuntimeError:
            # Project continuity is an optional derived projection. A bounded
            # projection failure must not take ordinary authorized retrieval
            # offline or expose the failing record.
            project_activation = AmbientProjectActivation(
                outcome="abstained",
                reason="project_projection_unavailable",
                snapshot_revision=None,
            )

        project_used_chars = (
            project_activation.capsule.used_chars if project_activation.capsule is not None else 0
        )
        retrieval_budget = max(1, request.budget_chars - project_used_chars)
        retrieval_request = request.model_copy(update={"budget_chars": retrieval_budget})
        response = core.retrieval.bootstrap(retrieval_request, principal)
        result = response.model_dump(mode="json")
        result["project_context"] = project_activation.to_dict()
        result["total_used_chars"] = response.used_chars + project_used_chars
        if project_activation.capsule is not None:
            core.store.audit_access(
                principal.id,
                "activate_project_context",
                [
                    item.record_id
                    for item in project_activation.capsule.items
                    if item.record_id is not None
                ],
                trace_id=response.audit_trace_id,
                metadata={
                    "activation_reason": project_activation.reason,
                    "item_count": len(project_activation.capsule.items),
                    "used_chars": project_used_chars,
                },
            )
        return result

    @app.get("/v1/context/status")
    def context_status(principal: Principal) -> dict[str, Any]:
        require(principal, "context:status")
        return core.store.status()

    @app.get("/v1/context/coverage")
    def context_truth_coverage(principal: Principal) -> dict[str, Any]:
        """Return content-free memory/source accounting for provider clients."""
        require(principal, "context:status")
        return core.store.memory_truth_coverage().model_dump(mode="json")

    @app.get("/v1/context/truth/{record_id}")
    def context_truth(record_id: str, principal: Principal) -> dict[str, Any]:
        """Return the authorized canonical record and its deterministic evidence."""
        require(principal, "context:read")
        if core.retrieval.get(record_id, principal) is None:
            raise HTTPException(status_code=404, detail="Context item not found")
        return core.store.get_memory_truth(
            record_id,
            include_deleted=False,
            principal=principal,
        ).model_dump(mode="json")

    @app.get("/v1/context/{record_id}")
    def get_context_item(record_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "context:read")
        record = core.retrieval.get(record_id, principal)
        if record is None:
            raise HTTPException(status_code=404, detail="Context item not found")
        return record.model_dump(mode="json")

    class StartImportOperationRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        # StrictInt rejects bool (True/False) and non-integer coercions.
        declared_byte_size: StrictInt
        filename: str | None = None
        provider: str | None = None
        source_service: str = "auto"
        media_type: str | None = None

    @app.post("/v1/admin/import-operations")
    def start_import_operation(
        body: StartImportOperationRequest,
        principal: Principal,
    ) -> dict[str, Any]:
        """Create a durable operation id after preflight; no source bytes yet."""
        require(principal, "admin")
        return core.import_operations.start_operation(
            declared_byte_size=body.declared_byte_size,
            filename=body.filename,
            source_service=body.source_service,
            provider=body.provider,
            media_type=body.media_type,
        )

    @app.get("/v1/admin/import-operations/{operation_id}")
    async def get_import_operation(
        operation_id: str,
        observation: OperationObserver,
    ) -> dict[str, Any]:
        principal, operation = observation
        require(principal, "admin")
        if operation is None:
            raise NotFoundError("import operation not found")
        return operation

    @app.put("/v1/admin/import-operations/{operation_id}/content")
    async def upload_import_operation_content(
        operation_id: str,
        http_request: Request,
        principal: Principal,
    ) -> dict[str, Any]:
        """Stream source bytes into a pre-created operation with chunk heartbeats."""
        require(principal, "admin")
        from queue import Empty, Full, Queue

        from allthecontext.import_boundary import (
            CANCEL_POLL_SECONDS,
            MAX_REQUEST_CHUNK_BYTES,
            ImportCancelledError,
            parse_content_length_header,
        )

        try:
            expected_size = parse_content_length_header(http_request.headers.get("content-length"))
        except InvalidStateError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

        # Bounded queue bridges the async request body to the sync Core worker.
        # maxsize keeps memory within the import RSS envelope.
        chunk_queue: Queue[bytes | None] = Queue(maxsize=8)
        stream_error: list[BaseException] = []
        stop_pump = threading.Event()

        def _put_bounded(item: bytes | None) -> bool:
            """Put with timeout so cancellation/disconnect never blocks forever."""
            while not stop_pump.is_set():
                try:
                    chunk_queue.put(item, timeout=CANCEL_POLL_SECONDS)
                    return True
                except Full:
                    continue
            return False

        async def _pump() -> None:
            try:
                async for chunk in http_request.stream():
                    if stop_pump.is_set():
                        return
                    # Bound/slice oversized transport chunks before queueing.
                    data = bytes(chunk)
                    step = MAX_REQUEST_CHUNK_BYTES
                    end = len(data)
                    for offset in range(0, end, step):
                        if stop_pump.is_set():
                            return
                        piece = data[offset : offset + step]
                        if not piece:
                            continue
                        ok = await asyncio.to_thread(_put_bounded, piece)
                        if not ok:
                            return
                await asyncio.to_thread(_put_bounded, None)
            except BaseException as error:
                stream_error.append(error)
                with suppress(Exception):
                    await asyncio.to_thread(_put_bounded, None)

        pump_task = asyncio.create_task(_pump())

        def run_upload() -> dict[str, Any]:
            def iterator() -> Any:
                while True:
                    try:
                        item = chunk_queue.get(timeout=CANCEL_POLL_SECONDS)
                    except Empty:
                        # Observe cancel at least every 250 ms while waiting for bytes.
                        if core.import_operations.cancel_registry.is_cancelled(operation_id):
                            raise ImportCancelledError(
                                "import cancelled by operator request"
                            ) from None
                        try:
                            op = core.import_operations.get_operation(operation_id)
                        except Exception:
                            op = None
                        if op is not None and (
                            op.get("cancel_requested") or op.get("status") == "cancelled"
                        ):
                            core.import_operations.cancel_registry.request_cancel(operation_id)
                            raise ImportCancelledError(
                                "import cancelled by operator request"
                            ) from None
                        continue
                    if item is None:
                        if stream_error:
                            raise stream_error[0]
                        return
                    yield item

            try:
                return core.import_operations.accept_upload(
                    operation_id,
                    iterator(),
                    expected_size=expected_size,
                    process_after=True,
                )
            finally:
                # Unblock any pump put waiting on a full queue.
                stop_pump.set()
                with suppress(Exception):
                    while True:
                        try:
                            chunk_queue.get_nowait()
                        except Empty:
                            break

        try:
            return await run_in_threadpool(run_upload)
        finally:
            stop_pump.set()
            if not pump_task.done():
                pump_task.cancel()
            # CancelledError is BaseException (not Exception) on supported Python.
            # Await of a cancelled pump must not mask a successful worker result
            # or surface a spurious cancellation after disconnect/cancel.
            try:
                await pump_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            # Drain so no blocked threadpool put remains after disconnect/cancel.
            with suppress(Exception):
                while True:
                    try:
                        chunk_queue.get_nowait()
                    except Empty:
                        break

    @app.post("/v1/admin/import-operations/{operation_id}/cancel")
    def cancel_import_operation(operation_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.import_operations.cancel_operation(operation_id)

    @app.post("/v1/admin/import-operations/{operation_id}/retry")
    async def retry_import_operation(operation_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return await run_in_threadpool(core.import_operations.retry_operation, operation_id)

    @app.post("/v1/admin/import")
    async def import_source(
        principal: Principal,
        file: Annotated[UploadFile, File()],
        source_service: Annotated[str, Form()] = "auto",
        provider: Annotated[str | None, Form()] = None,
    ) -> dict[str, Any]:
        """Compatibility multipart import. Prefer the import-operations API."""
        require(principal, "admin")
        safe_name = Path(file.filename or "import.txt").name
        # Multipart UploadFile is async-only; stage to a temp path with bounded reads,
        # then stream through the operation lifecycle so the same cancel/progress rules apply.
        with tempfile.TemporaryDirectory(
            prefix="atc-import-", dir=active_config.data_dir
        ) as temporary_directory:
            upload_path = Path(temporary_directory) / "source-upload"
            total = 0
            with upload_path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > active_config.max_import_bytes:
                        raise InvalidStateError("import exceeds configured size limit")
                    destination.write(chunk)
            operation = core.import_operations.start_operation(
                declared_byte_size=total,
                filename=safe_name,
                source_service=source_service,
                provider=provider,
            )

            def file_iter() -> Any:
                with upload_path.open("rb") as handle:
                    while chunk := handle.read(1024 * 1024):
                        yield chunk

            finished = await run_in_threadpool(
                core.import_operations.accept_upload,
                str(operation["operation_id"]),
                file_iter(),
                expected_size=total,
                process_after=True,
            )
            result = finished.get("result")
            if isinstance(result, dict):
                return result
            if finished.get("status") == "complete" and finished.get("source_id"):
                return await run_in_threadpool(
                    core.imports.reprocess_source, str(finished["source_id"])
                )
            raise InvalidStateError(str(finished.get("error_message") or "import operation failed"))

    @app.get("/v1/admin/candidates", deprecated=True, tags=["legacy compatibility"])
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

    @app.get("/v1/admin/observations")
    def list_observations(
        principal: Principal,
        disposition: ObservationDisposition | None = None,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        require(principal, "admin")
        items, total = core.store.list_observations(
            disposition=disposition,
            source_id=source_id,
            limit=limit,
            offset=offset,
        )
        return {"items": [item.model_dump(mode="json") for item in items], "total": total}

    @app.get("/v1/admin/memory-truth")
    def list_memory_truth(
        principal: Principal,
        status: MemoryTruthStatus | None = None,
        source_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Return the full record truth projection, including non-current records."""
        require(principal, "admin")
        return core.store.list_memory_truth(
            status=status,
            source_id=source_id,
            limit=limit,
            offset=offset,
        ).model_dump(mode="json")

    @app.get("/v1/admin/projects")
    def list_projects(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        try:
            snapshot = build_project_runtime(core.store)
        except ProjectRuntimeError as error:
            raise HTTPException(
                status_code=422,
                detail="project projection unavailable",
            ) from error
        return project_list_payload(snapshot)

    @app.get("/v1/admin/projects/{project_id}/capsule")
    def get_project_capsule(
        project_id: str,
        principal: Principal,
        character_budget: int = Query(
            default=12_000,
            ge=1,
            le=RUNTIME_MAX_CAPSULE_CHARS,
        ),
        item_budget: int = Query(
            default=32,
            ge=1,
            le=RUNTIME_MAX_CAPSULE_ITEMS,
        ),
    ) -> dict[str, object]:
        require(principal, "admin")
        try:
            snapshot = build_project_runtime(
                core.store,
                character_budget=character_budget,
                item_budget=item_budget,
            )
            capsule = capsule_for_project(snapshot, project_id)
        except KeyError as error:
            raise NotFoundError("project not found") from error
        except ProjectRuntimeError as error:
            raise HTTPException(
                status_code=422,
                detail="project projection unavailable",
            ) from error
        return capsule.to_dict()

    @app.get("/v1/admin/memory-truth/{record_id}")
    def get_memory_truth(record_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.store.get_memory_truth(record_id).model_dump(mode="json")

    @app.get("/v1/admin/sources")
    def list_sources(principal: Principal, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        require(principal, "admin")
        items, total = core.store.list_sources(limit=limit, offset=offset)
        return {"items": items, "total": total}

    @app.post("/v1/admin/capture/sources")
    def create_capture_source(
        request: CaptureCreateRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        reject_reserved_workspace_provider(request.provider)
        source = core.capture.create_source(
            provider=request.provider,
            account_label=request.account_label,
            account_fingerprint=request.account_fingerprint,
            requested_scopes=request.requested_scopes,
            local_only_acknowledged=request.local_only_acknowledged,
        )
        return source.model_dump(mode="json")

    @app.post("/v1/admin/capture/workspaces/authorize")
    def authorize_capture_workspace(
        request: CaptureWorkspaceAuthorizeRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        authorization = authorize_local_workspace(
            core.store,
            active_config,
            Path(request.root),
            local_only_acknowledged=request.local_only_acknowledged,
        )
        return {
            key: authorization[key]
            for key in ("id", "provider", "lifecycle_state", "authorized", "reconciled")
        }

    @app.get("/v1/admin/capture/sources")
    def list_capture_sources(
        principal: Principal, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        require(principal, "admin")
        items, total = core.capture.list_sources(limit=limit, offset=offset)
        return {"items": [item.model_dump(mode="json") for item in items], "total": total}

    @app.get("/v1/admin/capture/status")
    def capture_status(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        items, total = core.capture.list_sources()
        return {
            "items": [core.capture.status(item.id) for item in items],
            "scheduler": core.capture_scheduler.status(),
            "total": total,
        }

    @app.get("/v1/admin/capture/scheduler")
    def capture_scheduler_status(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture_scheduler.status()

    @app.post("/v1/admin/capture/scheduler/enable")
    def enable_capture_scheduler(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture_scheduler.enable()

    @app.post("/v1/admin/capture/scheduler/disable")
    def disable_capture_scheduler(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture_scheduler.disable()

    @app.get("/v1/admin/capture/sources/{source_id}/status")
    def capture_source_status(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture.status(source_id)

    @app.get("/v1/admin/capture/sources/{source_id}")
    def get_capture_source(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture.get_source(source_id).model_dump(mode="json")

    @app.post("/v1/admin/capture/sources/{source_id}/enable")
    def enable_capture_source(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture.enable(source_id).model_dump(mode="json")

    @app.post("/v1/admin/capture/sources/{source_id}/pause")
    def pause_capture_source(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture.pause(source_id).model_dump(mode="json")

    @app.post("/v1/admin/capture/sources/{source_id}/resume")
    def resume_capture_source(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture.resume(source_id).model_dump(mode="json")

    @app.post("/v1/admin/capture/sources/{source_id}/disable")
    def disable_capture_source(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture.disable(source_id).model_dump(mode="json")

    @app.post("/v1/admin/capture/sources/{source_id}/revoke")
    def revoke_capture_source(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.capture.revoke(source_id).model_dump(mode="json")

    @app.post("/v1/admin/capture/sources/{source_id}/run")
    async def run_capture_source(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")

        def run_now() -> CaptureRunResult:
            refresh_local_workspace_adapter(core.capture, active_config)
            return core.capture.run(source_id)

        result = await run_in_threadpool(run_now)
        return result.model_dump(mode="json")

    @app.post("/v1/admin/sources/{source_id}/reprocess")
    async def reprocess_source(
        source_id: str,
        principal: Principal,
        rebuild: bool = False,
    ) -> dict[str, Any]:
        require(principal, "admin")
        return await run_in_threadpool(
            partial(core.imports.reprocess_source, source_id, rebuild=rebuild)
        )

    @app.get("/v1/admin/sources/{source_id}/import-progress")
    def import_progress(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.imports.import_progress(source_id)

    @app.post("/v1/admin/sources/{source_id}/cancel-import")
    def cancel_import(source_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return core.imports.cancel_import(source_id)

    @app.get("/v1/admin/import-boundary")
    def import_boundary_profile(principal: Principal) -> dict[str, Any]:
        """Return the frozen scale profile and canary generator contract."""
        require(principal, "admin")
        from allthecontext.boundary_canary import (
            BOUNDARY_CANARY_GENERATOR_VERSION,
            BOUNDARY_CANARY_SIZE_BYTES,
            checkpoint_offsets,
        )
        from allthecontext.import_boundary import expected_chunk_count, scale_profile
        from allthecontext.provider_shapes import provider_claim_manifest

        profile = scale_profile()
        return {
            "scale_profile": profile,
            "boundary_canary": {
                "generator_version": BOUNDARY_CANARY_GENERATOR_VERSION,
                "size_bytes": BOUNDARY_CANARY_SIZE_BYTES,
                "expected_chunk_count": expected_chunk_count(BOUNDARY_CANARY_SIZE_BYTES),
                "checkpoint_offsets": list(checkpoint_offsets(BOUNDARY_CANARY_SIZE_BYTES)),
                "exact_artifact_acceptance": "pending",
            },
            "provider_claims": provider_claim_manifest(),
        }

    @app.post("/v1/admin/sources/{source_id}/delete")
    def delete_source(
        source_id: str, request: RejectRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        result = core.store.delete_source(
            source_id,
            reason=request.reason or "deleted by user",
            actor=principal.id,
        )
        return result

    @app.post("/v1/admin/sources/{source_id}/restore")
    def restore_source(
        source_id: str, request: RejectRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        result = core.store.restore_source(
            source_id,
            reason=request.reason or "restored by user",
            actor=principal.id,
        )
        return result

    @app.post(
        "/v1/admin/candidates/{candidate_id}/approve",
        deprecated=True,
        tags=["legacy compatibility"],
    )
    def approve_candidate(
        candidate_id: str, request: ApprovalRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        refusal = core.store.refuse_direct_value(
            request.model_dump(mode="json"),
            route="approve_candidate",
            operation_id=None,
            client=principal,
        )
        if refusal is not None:
            return refusal.model_dump(mode="json")
        result = core.store.approve_candidate(candidate_id, request, actor=principal.id)
        return result.model_dump(mode="json")

    @app.post(
        "/v1/admin/candidates/{candidate_id}/reject",
        deprecated=True,
        tags=["legacy compatibility"],
    )
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
        refusal = core.store.refuse_direct_value(
            request.model_dump(mode="json"),
            route="correct_record",
            operation_id=None,
            client=principal,
        )
        if refusal is not None:
            return refusal.model_dump(mode="json")
        result = core.store.correct_record(
            record_id,
            content=request.content,
            structured_value=request.structured_value,
            supersedes=request.supersedes,
            entity_key=request.entity_key,
            attribute_key=request.attribute_key,
            reason=request.reason,
            actor=principal.id,
        )
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
        return result.model_dump(mode="json")

    @app.post("/v1/admin/records/{record_id}/delete")
    def delete_record(
        record_id: str, request: RejectRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        result = core.store.delete_record(
            record_id, reason=request.reason or "deleted by user", actor=principal.id
        )
        return result

    @app.post("/v1/admin/records/{record_id}/restore")
    def restore_record(
        record_id: str, request: RestoreRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        result = core.store.restore_record(
            record_id,
            version=request.version,
            reason=request.reason,
            actor=principal.id,
        )
        return result.model_dump(mode="json")

    @app.get("/v1/admin/records/{record_id}/history")
    def record_history(record_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return {"items": core.store.record_history(record_id)}

    @app.get("/v1/admin/integrity-groups")
    def list_integrity_groups(
        principal: Principal,
        status: str = "open",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        require(principal, "admin")
        return core.store.list_integrity_groups(status=status, limit=limit, offset=offset)

    @app.post("/v1/admin/purge")
    def purge(request: PurgeRequest, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        result = core.store.purge(
            request.target_type,
            request.target_id,
            confirmation=request.confirmation,
            actor=principal.id,
            compact=request.compact,
        )
        return result

    @app.get("/v1/admin/purge-jobs")
    def list_purge_jobs(principal: Principal, limit: int = 100) -> dict[str, Any]:
        require(principal, "admin")
        return {"items": core.store.list_purge_jobs(limit=limit)}

    @app.post("/v1/admin/purge-jobs/resume")
    def resume_purge_jobs(principal: Principal, limit: int = 10) -> dict[str, Any]:
        require(principal, "admin")
        return {"completed": core.store.resume_purge_jobs(limit=limit)}

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

    def legacy_edge_status_payload() -> dict[str, Any]:
        """Return local residual Edge state for isolated cleanup only.

        This path never advertises enrollment, deployment, connect, sync, or
        remote-client management. It cannot create a second authority.
        """

        state_error: str | None = None
        material_error: str | None = None
        try:
            state = legacy_edge_connections.state()
        except RuntimeError:
            state = None
            state_error = "The saved Edge setup needs to be repaired."
        try:
            material = legacy_edge_connections.material()
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

        edge_url = state.edge_url if state is not None else None
        last_error = (
            state_error
            or material_error
            or mismatch_error
            or (state.last_error if state is not None else None)
        )
        return {
            "product_surface": "legacy_cleanup_only",
            "active_operation_available": False,
            "configured": edge_url is not None and material_available,
            "remote_present": edge_url is not None,
            "credential_available": material_available,
            "state": connection_state,
            "vault_id": state.vault_id if state is not None else core.store.vault_id(),
            "edge_url": edge_url,
            "prepared_at": state.prepared_at if state is not None else None,
            "connected_at": state.connected_at if state is not None else None,
            "credential_storage": (
                material.credential_storage
                if material is not None
                else (state.credential_storage if state is not None else None)
            ),
            "last_sequence": state.last_sequence if state is not None else 0,
            "last_success_at": state.last_success_at if state is not None else None,
            "last_error": last_error,
            "detail": (
                "Hosted Edge enrollment, deployment, connect, sync, and remote-client "
                "management are outside the V1 Core product boundary. Only isolated "
                "decommissioning or local forget of residual state remains."
            ),
        }

    def _removed_edge_product_surface() -> None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Hosted Edge enrollment, deployment, connect, sync, and remote-client "
                "management are outside the V1 Core product boundary. Use "
                "/v1/admin/legacy-edge only for residual cleanup."
            ),
        )

    # Explicit tombstones so removed Edge operations never fall through to the
    # dashboard static mount (which would otherwise answer POST with HTTP 405).
    for _removed_path in (
        "/v1/admin/edge",
        "/v1/admin/edge/prepare",
        "/v1/admin/edge/deployment-env",
        "/v1/admin/edge/connect",
        "/v1/admin/edge/sync",
        "/v1/admin/edge/secure-storage",
        "/v1/admin/edge/owner-link",
        "/v1/admin/edge/clients",
        "/v1/admin/edge/clients/{logical_client_id}",
        "/v1/admin/edge/clients/{logical_client_id}/approve",
        "/v1/admin/edge/decommission",
        "/v1/admin/edge/forget",
    ):
        app.api_route(
            _removed_path,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            include_in_schema=False,
        )(_removed_edge_product_surface)

    @app.get("/v1/admin/legacy-edge")
    def get_legacy_edge_status(principal: Principal) -> dict[str, Any]:
        """Read residual Edge state for cleanup. Does not enroll or connect."""

        require(principal, "admin")
        return legacy_edge_status_payload()

    @app.post("/v1/admin/legacy-edge/decommission")
    def decommission_legacy_edge(principal: Principal) -> dict[str, Any]:
        """Decommission a pre-existing residual Edge only.

        Refuses when nothing is configured so this path cannot create a new
        authority or open an arbitrary outbound connection by default.
        """

        require(principal, "admin")
        try:
            state = legacy_edge_connections.state()
            material = legacy_edge_connections.material()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if state is None or material is None or state.edge_url is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "No residual paired Edge is configured. Core will not open a new "
                    "hosted connection or create a second authority."
                ),
            )
        try:
            legacy_edge_sync.decommission()
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
            "product_surface": "legacy_cleanup_only",
        }

    @app.post("/v1/admin/legacy-edge/forget")
    def forget_legacy_edge(request: EdgeForgetRequest, principal: Principal) -> dict[str, Any]:
        """Forget local residual Edge credentials without creating a new connection."""

        require(principal, "admin")
        if request.confirmation != "DELETE HOSTED EDGE":  # pragma: no cover - Literal validates
            raise HTTPException(status_code=422, detail="confirmation phrase does not match")
        try:
            state = legacy_edge_connections.state()
            material = legacy_edge_connections.material()
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
                    "Edge is paired and manageable. Use the isolated decommission path "
                    "before forgetting its local recovery credential"
                ),
            )
        try:
            legacy_edge_sync.forget_local()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return legacy_edge_status_payload()

    @app.get("/v1/admin/integrations")
    def list_integrations(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")

        def desktop_status(integration_id: str) -> dict[str, Any]:
            if integration_id == "chatgpt_codex":
                name = CODEX_CLIENT_NAME
                read_config = read_codex_config
                detected = codex_is_detected()
                install_url = "https://openai.com/codex/"
                detail = "One local MCP connection for the Codex app, CLI, and editor extension."
            else:
                name = CLAUDE_CLIENT_NAME
                read_config = read_claude_config
                detected = claude_is_detected()
                install_url = "https://claude.ai/download"
                detail = "Direct private connection to Core through the local MCP adapter."
            base = {
                "id": integration_id,
                "name": name,
                "detected": detected,
                "install_url": install_url,
                "configured": False,
                "state": "disconnected" if detected else "not_installed",
                "reason": None,
                "mode": "local",
                "detail": detail,
            }
            try:
                configured = read_config()
            except (OSError, ValueError):
                return {
                    **base,
                    "state": "degraded",
                    "reason": "The app configuration could not be read. Choose Repair.",
                }
            if configured is None:
                return base
            if not detected:
                return {
                    **base,
                    "reason": (
                        "An All The Context configuration exists, but the app is not installed "
                        "on this computer."
                    ),
                }

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
            configured_data_dir = configured.env.get("ATC_CORE_DATA_DIR", "")
            try:
                same_data_dir = (
                    Path(configured_data_dir).expanduser().samefile(active_config.data_dir)
                )
            except (OSError, RuntimeError, ValueError):
                same_data_dir = False
            if not configured_data_dir or not same_data_dir:
                return {
                    **base,
                    "state": "degraded",
                    "reason": "The connection is not bound to this Core vault. Choose Repair.",
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
            "mobile": {
                "mode": "direct_core",
                "requires_core_online": True,
                "secure_remote_pairing_available": False,
                "detail": (
                    "Mobile devices connect directly to the authoritative Core while it is "
                    "online. This beta keeps Core on loopback by default and does not "
                    "automatically expose a network port or create a hosted copy."
                ),
            },
        }

    @app.post("/v1/admin/integrations/{integration_id}")
    def connect_integration(integration_id: str, principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        if integration_id not in {"chatgpt_codex", "claude"}:
            raise HTTPException(status_code=404, detail="Unknown desktop integration")
        detected = (
            codex_is_detected() if integration_id == "chatgpt_codex" else claude_is_detected()
        )
        if not detected:
            name = CODEX_CLIENT_NAME if integration_id == "chatgpt_codex" else CLAUDE_CLIENT_NAME
            raise HTTPException(
                status_code=409,
                detail=f"{name} is not installed on this computer.",
            )
        runtime = RuntimeCommand.current()
        target_url = f"http://{active_config.host}:{active_config.port}"
        name = CODEX_CLIENT_NAME if integration_id == "chatgpt_codex" else CLAUDE_CLIENT_NAME
        try:
            if integration_id == "chatgpt_codex":
                client_access, result = configure_client_access_transactionally(
                    core.store,
                    active_config,
                    name=name,
                    scopes=AI_CLIENT_SCOPES,
                    configure=lambda access: configure_codex(
                        runtime,
                        access.client_id,
                        token=(
                            None
                            if access.credential_storage == "operating-system credential store"
                            else access.token
                        ),
                        target_url=target_url,
                        core_data_dir=active_config.data_dir,
                    ),
                )
            else:
                client_access, result = configure_client_access_transactionally(
                    core.store,
                    active_config,
                    name=name,
                    scopes=AI_CLIENT_SCOPES,
                    configure=lambda access: configure_claude(
                        runtime,
                        access.client_id,
                        token=(
                            None
                            if access.credential_storage == "operating-system credential store"
                            else access.token
                        ),
                        target_url=target_url,
                        core_data_dir=active_config.data_dir,
                    ),
                )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
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

    @app.get("/v1/admin/updates")
    def update_status(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return updates.public_status()

    @app.put("/v1/admin/updates/preferences")
    def update_preferences(
        request: UpdatePreferencesRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        return updates.configure(enabled=request.enabled, channel=request.channel)

    def update_action(action: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        try:
            return action()
        except UpdateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/admin/updates/check")
    def check_for_updates(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return update_action(updates.check)

    @app.post("/v1/admin/updates/download")
    def download_update(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return update_action(updates.download)

    @app.post("/v1/admin/updates/accept-exact-candidate")
    def accept_exact_update_candidate(principal: Principal) -> dict[str, Any]:
        """Reopen a verified same-version offer for transactional acceptance smoke."""

        require(principal, "admin")
        return update_action(updates.accept_exact_candidate)

    @app.get("/v1/admin/updates/artifact")
    def save_verified_update(principal: Principal) -> FileResponse:
        require(principal, "admin")
        try:
            prepared = updates.prepare_artifact_export()
        except UpdateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        def cleanup_artifact() -> None:
            prepared.path.unlink(missing_ok=True)

        try:
            return FileResponse(
                prepared.path,
                media_type="application/zip",
                filename=prepared.filename,
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                },
                background=BackgroundTask(cleanup_artifact),
            )
        except Exception:
            cleanup_artifact()
            raise

    @app.post("/v1/admin/updates/install")
    def install_update(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        status = update_action(updates.install)
        if (
            status.get("phase") == UpdatePhase.RESTART_REQUIRED.value
            and status.get("automatic_install_supported") is True
            and shutdown_callback is not None
        ):
            shutdown = threading.Timer(0.25, shutdown_callback)
            shutdown.daemon = True
            shutdown.start()
        return status

    @app.post("/v1/admin/updates/defer")
    def defer_update(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return update_action(updates.defer)

    @app.post("/v1/admin/updates/cancel")
    def cancel_update(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return updates.cancel()

    @app.delete("/v1/admin/updates/error")
    def clear_update_error(principal: Principal) -> dict[str, Any]:
        require(principal, "admin")
        return updates.clear_error()

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


def run_update_health_check(report_path: Path) -> int:
    """Start the real loopback Core once, prove health, and shut down cleanly."""

    config = CoreConfig.default()
    finished = threading.Event()
    healthy = threading.Event()
    servers: list[uvicorn.Server] = []

    def probe() -> None:
        deadline = time.monotonic() + 30
        url = f"http://{config.host}:{config.port}/health"
        while not finished.is_set() and time.monotonic() < deadline:
            try:
                request = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(request, timeout=1) as response:
                    value = json.loads(response.read(4097).decode("utf-8"))
                if value == {"status": "ok", "component": "core"}:
                    healthy.set()
                    if servers:
                        servers[0].should_exit = True
                    return
            except (
                OSError,
                UnicodeError,
                ValueError,
                urllib.error.URLError,
                json.JSONDecodeError,
            ):
                time.sleep(0.1)
        if servers:
            servers[0].should_exit = True

    try:
        with CoreInstanceLock(config):
            app = create_app(config)
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
            watcher = threading.Thread(target=probe, daemon=True)
            watcher.start()
            try:
                server.run()
            finally:
                finished.set()
                watcher.join(timeout=2)
        if healthy.is_set():
            connection = sqlite3.connect(config.database_path)
            try:
                if (
                    os.environ.get("ATC_PACKAGED_SMOKE") == "1"
                    and os.environ.get("ATC_UPDATE_SMOKE_MUTATE_DB") == "1"
                ):
                    connection.execute(
                        "CREATE TABLE IF NOT EXISTS packaged_update_smoke(value TEXT NOT NULL)"
                    )
                    connection.execute("DELETE FROM packaged_update_smoke")
                    connection.execute("INSERT INTO packaged_update_smoke VALUES ('new-version')")
                    connection.commit()
                integrity = connection.execute("PRAGMA quick_check").fetchone()
            finally:
                connection.close()
            forced_failure = (
                os.environ.get("ATC_PACKAGED_SMOKE") == "1"
                and os.environ.get("ATC_UPDATE_FORCE_HEALTH_FAILURE") == "1"
            )
            success = integrity == ("ok",) and not forced_failure
        else:
            success = False
    except (OSError, sqlite3.Error, ValueError):
        success = False

    payload = (
        {"component": "core", "health": "ok", "version": __version__}
        if success
        else {"component": "core", "health": "failed", "version": __version__}
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(f"{report_path.name}.{secrets.token_hex(6)}.atc-new")
    try:
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(report_path)
    finally:
        temporary.unlink(missing_ok=True)
    return 0 if success else 1


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
