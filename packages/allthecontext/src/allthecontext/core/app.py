"""FastAPI transport for the local authoritative Core."""

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from ..config import CoreConfig
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
from ..storage import ConflictError, InvalidStateError, NotFoundError, StorageError
from .service import CoreService


def create_app(
    config: CoreConfig | None = None,
    *,
    service: CoreService | None = None,
    shutdown_callback: Callable[[], None] | None = None,
) -> FastAPI:
    active_config = config or CoreConfig.default()
    core = service or CoreService(active_config)
    app = FastAPI(
        title="All The Context Core",
        version="0.1.0",
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.core = core
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
        authorization: Annotated[str | None, Header()] = None,
    ) -> ClientPrincipal:
        if authorization is None and development_principal is not None:
            return development_principal
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Bearer credential required")
        token = authorization.removeprefix("Bearer ").strip()
        principal = core.store.authenticate(token)
        if principal is None:
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
    def health() -> dict[str, str]:
        return {"status": "ok", "component": "core"}

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
        return core.store.approve_candidate(candidate_id, request, actor=principal.id).model_dump(
            mode="json"
        )

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
        return core.store.correct_record(
            record_id,
            content=request.content,
            structured_value=request.structured_value,
            supersedes=request.supersedes,
            reason=request.reason,
            actor=principal.id,
        ).model_dump(mode="json")

    @app.post("/v1/admin/records/{record_id}/availability")
    def change_availability(
        record_id: str, request: AvailabilityRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        return core.store.change_availability(
            record_id,
            request.availability,
            explicit_sensitive_replication=request.explicit_sensitive_replication,
            actor=principal.id,
        ).model_dump(mode="json")

    @app.post("/v1/admin/records/{record_id}/delete")
    def delete_record(
        record_id: str, request: RejectRequest, principal: Principal
    ) -> dict[str, Any]:
        require(principal, "admin")
        return core.store.delete_record(
            record_id, reason=request.reason or "deleted by user", actor=principal.id
        )

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
        return {"items": core.store.list_clients()}

    @app.post("/v1/admin/clients/{client_id}/revoke")
    def revoke_client(client_id: str, principal: Principal) -> dict[str, bool]:
        require(principal, "admin")
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
            uvicorn.Config(app, host=config.host, port=config.port, log_config=None)
        )
        servers.append(server)
        server.run()


if __name__ == "__main__":
    main()
