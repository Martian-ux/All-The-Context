"""Focused Core-only Claude Code explicit-user memory contract tests."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from allthecontext.ingestion import IngestionService
from allthecontext.models import (
    ClaudeCodeCorrectionRequest,
    ClaudeCodeForgetRequest,
    ClaudeCodeRememberRequest,
    ClientCreate,
    ObservationDisposition,
    Sensitivity,
)
from allthecontext.security import (
    CLAUDE_CODE_USER_WRITE_SCOPES,
    WITNESS_EXPLICIT_USER_STATEMENT,
    ClientPrincipal,
    principal_may_submit_claude_code_user_mutation,
)
from allthecontext.storage import CoreStore, NotFoundError
from pydantic import ValidationError


def _store(tmp_path: Path) -> CoreStore:
    store = CoreStore(tmp_path / "core.db")
    store.initialize_vault()
    return store


def _writer(store: CoreStore) -> ClientPrincipal:
    principal, _token = store.create_client(
        ClientCreate(
            name="Claude Code user memory",
            scopes=sorted(CLAUDE_CODE_USER_WRITE_SCOPES),
        )
    )
    return principal


def test_write_identity_is_exactly_separate_from_claude_code_read_identity() -> None:
    assert (
        frozenset({"context:propose", WITNESS_EXPLICIT_USER_STATEMENT})
        == CLAUDE_CODE_USER_WRITE_SCOPES
    )
    assert principal_may_submit_claude_code_user_mutation(
        ClientPrincipal("writer", "writer", CLAUDE_CODE_USER_WRITE_SCOPES)
    )
    assert not principal_may_submit_claude_code_user_mutation(
        ClientPrincipal("reader", "Claude Code", frozenset({"context:read"}))
    )
    assert not principal_may_submit_claude_code_user_mutation(
        ClientPrincipal(
            "read-write",
            "Claude Code",
            frozenset({"context:read", *CLAUDE_CODE_USER_WRITE_SCOPES}),
        )
    )
    assert not principal_may_submit_claude_code_user_mutation(
        ClientPrincipal(
            "admin",
            "admin",
            frozenset({"admin", "context:propose", WITNESS_EXPLICIT_USER_STATEMENT}),
        )
    )


def test_claude_code_request_is_bounded_strict_and_opaque() -> None:
    key = str(uuid4())
    request = ClaudeCodeRememberRequest(content="Prefer concise answers.", idempotency_key=key)
    assert request.kind == "interaction_preference"
    assert request.idempotency_key == key

    with pytest.raises(ValidationError):
        ClaudeCodeRememberRequest(
            content="Prefer concise answers.",
            idempotency_key="remember-1",
        )
    with pytest.raises(ValidationError):
        ClaudeCodeRememberRequest(
            content="Prefer concise answers.",
            idempotency_key=key,
            sensitivity="highly_sensitive",  # type: ignore[call-arg]
        )
    with pytest.raises(ValidationError):
        ClaudeCodeRememberRequest(
            content="x" * 8_001,
            idempotency_key=str(uuid4()),
        )


def test_core_assigns_authority_fields_and_reuses_existing_lifecycle_machinery(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    principal = _writer(store)
    service = IngestionService(store)

    remembered = service.claude_code_remember(
        ClaudeCodeRememberRequest(
            kind="personal_context",
            content="I live in Boston.",
            idempotency_key=str(uuid4()),
        ),
        principal,
    )
    assert remembered.disposition == ObservationDisposition.APPLIED
    assert remembered.record_id is not None
    assert remembered.observation_origin == "ongoing_client"
    assert remembered.source_service == "claude_code"
    assert remembered.source_type == "direct_user_statement"
    assert remembered.sensitivity == Sensitivity.SENSITIVE
    assert remembered.availability.value == "core_available"
    assert store.get_record(remembered.record_id).availability.value == "local_only"
    assert remembered.allowed_clients == []
    assert remembered.denied_clients == []

    retry = service.claude_code_remember(
        ClaudeCodeRememberRequest(
            kind="personal_context",
            content="I live in Boston.",
            idempotency_key=remembered.idempotency_key,
        ),
        principal,
    )
    assert retry.id == remembered.id
    assert retry.record_id == remembered.record_id

    corrected = service.claude_code_correct(
        ClaudeCodeCorrectionRequest(
            record_id=remembered.record_id,
            content="I live in Philadelphia.",
            idempotency_key=str(uuid4()),
        ),
        principal,
    )
    assert corrected.disposition == ObservationDisposition.APPLIED
    assert corrected.record_id == remembered.record_id
    assert store.get_record(remembered.record_id).content == "I live in Philadelphia."

    forgotten = service.claude_code_forget(
        ClaudeCodeForgetRequest(
            record_id=remembered.record_id,
            idempotency_key=str(uuid4()),
        ),
        principal,
    )
    assert forgotten.disposition == ObservationDisposition.APPLIED
    assert forgotten.record_id == remembered.record_id
    with pytest.raises(NotFoundError):
        store.get_record(remembered.record_id)
    assert store.get_record(remembered.record_id, include_deleted=True).deleted_at is not None


def test_core_service_rejects_non_opt_in_or_forged_write_identity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    principal = _writer(store)
    plain, _token = store.create_client(
        ClientCreate(name="Plain proposal client", scopes=["context:propose"])
    )
    service = IngestionService(store)
    request = ClaudeCodeRememberRequest(
        content="Prefer short answers.", idempotency_key=str(uuid4())
    )

    for unauthorized in (
        ClientPrincipal(principal.id, "reader", frozenset({"context:read"})),
        ClientPrincipal(principal.id, "admin", frozenset({"admin", "context:propose"})),
        ClientPrincipal(
            plain.id,
            "forged",
            frozenset({"context:propose", WITNESS_EXPLICIT_USER_STATEMENT}),
        ),
    ):
        with pytest.raises(PermissionError):
            service.claude_code_remember(request, unauthorized)
