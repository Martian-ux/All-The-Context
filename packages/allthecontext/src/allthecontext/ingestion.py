"""Application service for sessioned, resumable ingestion."""

from __future__ import annotations

from typing import Any

from .models import (
    Availability,
    BeginIngestionRequest,
    CandidateInput,
    ClaudeCodeCorrectionRequest,
    ClaudeCodeForgetRequest,
    ClaudeCodeRememberRequest,
    ContextErrorRequest,
    FinishIngestionRequest,
    ForgetContextRequest,
    IngestionMode,
    ObservationOut,
    SecretRefusalOut,
    Sensitivity,
    SubmitBatchRequest,
)
from .security import (
    CLAUDE_CODE_USER_WRITE_SCOPES,
    ClientPrincipal,
    principal_may_submit_claude_code_user_mutation,
    record_is_allowed,
)
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
        *,
        publish: bool = True,
    ) -> dict[str, Any]:
        return self.store.finish_ingestion(
            request.session_id,
            request.coverage,
            client=principal,
            publish=publish,
        )

    def propose(
        self,
        request: CandidateInput,
        principal: ClientPrincipal | None = None,
        *,
        route: str = "propose_memory",
    ) -> ObservationOut | SecretRefusalOut:
        refusal = self.store.refuse_direct_candidate(
            request,
            route=route,
            client=principal,
        )
        if refusal is not None:
            return refusal
        created = self.store.add_candidate(request, client=principal)
        return self.store.get_observation(created.id)

    def report_error(
        self,
        request: ContextErrorRequest,
        principal: ClientPrincipal | None = None,
        *,
        source_service: str | None = None,
        source_type: str | None = None,
        route: str = "report_context_error",
    ) -> ObservationOut | SecretRefusalOut:
        self._require_target_access(request.record_id, principal)
        refusal = self.store.refuse_direct_value(
            request.model_dump(mode="json"),
            route=route,
            operation_id=request.idempotency_key,
            client=principal,
        )
        if refusal is not None:
            return refusal
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
            source_service=source_service,
            source_type=source_type,
        )
        created = self.store.add_context_error_observation(
            candidate,
            record_id=request.record_id,
            description=request.description,
            evidence=request.evidence,
            client=principal,
        )
        return self.store.get_observation(created.id)

    def claude_code_remember(
        self,
        request: ClaudeCodeRememberRequest,
        principal: ClientPrincipal,
    ) -> ObservationOut | SecretRefusalOut:
        """Apply one explicitly user-authored Claude Code memory observation."""

        self._require_claude_code_writer(principal)
        candidate = CandidateInput(
            kind=request.kind,
            content=request.content,
            confidence=1.0,
            sensitivity=Sensitivity.NORMAL,
            availability=Availability.CORE,
            allowed_clients=[],
            denied_clients=[],
            explicit_user_statement=True,
            idempotency_key=request.idempotency_key,
            source_service="claude_code",
            source_type="direct_user_statement",
        )
        return self.propose(candidate, principal, route="claude_code_remember")

    def claude_code_correct(
        self,
        request: ClaudeCodeCorrectionRequest,
        principal: ClientPrincipal,
    ) -> ObservationOut | SecretRefusalOut:
        """Apply one explicitly user-authored correction through the error path."""

        self._require_claude_code_writer(principal)
        return self.report_error(
            ContextErrorRequest(
                record_id=request.record_id,
                description="Claude Code explicit user correction",
                suggested_correction=request.content,
                idempotency_key=request.idempotency_key,
            ),
            principal,
            source_service="claude_code",
            source_type="direct_user_statement",
            route="claude_code_correct",
        )

    def claude_code_forget(
        self,
        request: ClaudeCodeForgetRequest,
        principal: ClientPrincipal,
    ) -> ObservationOut | SecretRefusalOut:
        """Create a reversible tombstone through the existing observation path."""

        self._require_claude_code_writer(principal)
        candidate = CandidateInput(
            kind="context_forget",
            content="Explicit Claude Code user forget request",
            confidence=1.0,
            sensitivity=Sensitivity.NORMAL,
            availability=Availability.LOCAL,
            allowed_clients=[],
            denied_clients=[],
            supersedes=request.record_id,
            explicit_user_statement=True,
            idempotency_key=request.idempotency_key,
            source_service="claude_code",
            source_type="direct_user_statement",
        )
        return self.propose(candidate, principal, route="claude_code_forget")

    def _require_claude_code_writer(self, principal: ClientPrincipal) -> None:
        if not principal_may_submit_claude_code_user_mutation(
            principal
        ) or not self.store.principal_matches_registration(
            principal,
            CLAUDE_CODE_USER_WRITE_SCOPES,
        ):
            raise PermissionError(
                "Claude Code memory writes require the separate context:propose "
                "and witness:explicit_user_statement principal"
            )

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
    rebuild_generation: int | None = None,
) -> BeginIngestionRequest:
    idempotency_key: str | None = None
    if parser_version is not None:
        idempotency_key = f"archive:{source_id}:{parser_version}"
        if rebuild_generation is not None:
            idempotency_key = f"{idempotency_key}:rebuild:{rebuild_generation}"
    return BeginIngestionRequest(
        mode=IngestionMode.ARCHIVE,
        accessible_sources=[source_id],
        unavailable_sources=[],
        idempotency_key=idempotency_key,
    )
