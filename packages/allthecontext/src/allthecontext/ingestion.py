"""Application service for sessioned, resumable ingestion."""

from __future__ import annotations

from typing import Any

from .ids import new_id, utc_now
from .models import (
    Availability,
    BeginIngestionRequest,
    CandidateInput,
    CandidateOut,
    ContextErrorRequest,
    FinishIngestionRequest,
    IngestionMode,
    Sensitivity,
    SubmitBatchRequest,
)
from .security import ClientPrincipal
from .storage import CoreStore


class IngestionService:
    def __init__(self, store: CoreStore) -> None:
        self.store = store

    def begin(
        self, request: BeginIngestionRequest, principal: ClientPrincipal | None = None
    ) -> dict[str, Any]:
        client_id = principal.id if principal is not None else request.client_id
        return self.store.begin_ingestion(
            mode=request.mode,
            accessible_sources=request.accessible_sources,
            unavailable_sources=request.unavailable_sources,
            client_id=client_id,
            notes=request.notes,
            idempotency_key=request.idempotency_key,
        )

    def submit(
        self, request: SubmitBatchRequest, principal: ClientPrincipal | None = None
    ) -> dict[str, Any]:
        return self.store.submit_batch(
            request.session_id,
            request.idempotency_key,
            request.candidates,
            client=principal,
        )

    def finish(self, request: FinishIngestionRequest) -> dict[str, Any]:
        return self.store.finish_ingestion(request.session_id, request.coverage)

    def propose(
        self, request: CandidateInput, principal: ClientPrincipal | None = None
    ) -> CandidateOut:
        return self.store.add_candidate(request, client=principal)

    def report_error(
        self, request: ContextErrorRequest, principal: ClientPrincipal | None = None
    ) -> CandidateOut:
        candidate = CandidateInput(
            kind="correction",
            content=request.content,
            evidence=request.evidence or request.description,
            supersedes=request.record_id,
            confidence=1.0,
            sensitivity=Sensitivity.NORMAL,
            availability=Availability.CORE,
            explicit_user_statement=False,
        )
        created = self.store.add_candidate(candidate, client=principal)
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO context_errors"
                "(id,vault_id,client_id,record_id,candidate_id,description,evidence,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    new_id(),
                    self.store.vault_id(),
                    principal.id if principal else None,
                    request.record_id,
                    created.id,
                    request.description or request.content,
                    request.evidence,
                    utc_now(),
                ),
            )
        return created


def archive_session_request(
    source_id: str,
    *,
    parser_version: str | None = None,
) -> BeginIngestionRequest:
    return BeginIngestionRequest(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source_id],
        unavailable_sources=[],
        idempotency_key=(
            f"archive:{source_id}:{parser_version}" if parser_version is not None else None
        ),
    )
