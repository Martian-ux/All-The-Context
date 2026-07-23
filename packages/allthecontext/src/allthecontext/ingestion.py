"""Application service for sessioned, resumable ingestion."""

from __future__ import annotations

from typing import Any

from .models import (
    Availability,
    BeginIngestionRequest,
    CandidateInput,
    ContextErrorRequest,
    FinishIngestionRequest,
    ForgetContextRequest,
    IngestionMode,
    ObservationOut,
    Sensitivity,
    SubmitBatchRequest,
)
from .security import ClientPrincipal, record_is_allowed
from .storage import CoreStore, NotFoundError


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

    def finish(
        self,
        request: FinishIngestionRequest,
        principal: ClientPrincipal | None = None,
    ) -> dict[str, Any]:
        return self.store.finish_ingestion(
            request.session_id,
            request.coverage,
            client=principal,
        )

    def propose(
        self, request: CandidateInput, principal: ClientPrincipal | None = None
    ) -> ObservationOut:
        created = self.store.add_candidate(request, client=principal)
        return self.store.get_observation(created.id)

    def report_error(
        self, request: ContextErrorRequest, principal: ClientPrincipal | None = None
    ) -> ObservationOut:
        self._require_target_access(request.record_id, principal)
        has_correction = request.suggested_correction is not None
        candidate = CandidateInput(
            kind="correction" if has_correction else "context_error",
            content=request.suggested_correction or request.description,
            evidence=request.evidence or request.description,
            supersedes=request.record_id,
            confidence=1.0,
            sensitivity=Sensitivity.NORMAL,
            availability=Availability.CORE,
            explicit_user_statement=has_correction,
            idempotency_key=request.idempotency_key,
        )
        created = self.store.add_context_error_observation(
            candidate,
            record_id=request.record_id,
            description=request.description,
            evidence=request.evidence,
            client=principal,
        )
        return self.store.get_observation(created.id)

    def forget(
        self,
        request: ForgetContextRequest,
        principal: ClientPrincipal | None = None,
    ) -> dict[str, Any]:
        self._require_target_access(request.record_id, principal, include_deleted=True)
        result = self.store.delete_record(
            request.record_id,
            reason="Explicit user forget request",
            actor=principal.id if principal is not None else "local-core",
        )
        return {
            **result,
            "disposition": "applied",
            "decision_reason": "explicit forget request applied as a reversible deletion",
            "user_action_required": False,
        }

    def _require_target_access(
        self,
        record_id: str | None,
        principal: ClientPrincipal | None,
        *,
        include_deleted: bool = False,
    ) -> None:
        if record_id is None or principal is None or "admin" in principal.scopes:
            return
        record = self.store.get_record(record_id, include_deleted=include_deleted)
        if not record_is_allowed(
            principal,
            set(record.scopes),
            set(record.allowed_clients),
            set(record.denied_clients),
        ):
            raise NotFoundError("context record not found")


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
