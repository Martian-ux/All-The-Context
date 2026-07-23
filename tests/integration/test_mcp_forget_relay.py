from __future__ import annotations

from pathlib import Path

import pytest
from allthecontext.config import CoreConfig
from allthecontext.core.service import CoreService
from allthecontext.models import CandidateInput, ObservationDisposition
from allthecontext.storage import NotFoundError


def test_queued_mcp_forget_becomes_one_reversible_core_deletion(tmp_path: Path) -> None:
    core = CoreService(CoreConfig.in_directory(tmp_path))
    created = core.store.add_candidate(
        CandidateInput(
            kind="preference",
            content="The user prefers direct answers.",
            scopes=["general"],
            entity_key="user",
            attribute_key="answer_style",
            explicit_user_statement=True,
        )
    )
    assert created.disposition == ObservationDisposition.APPLIED
    assert created.record_id is not None

    queued_forget = CandidateInput(
        kind="context_forget",
        content="The user explicitly asked to forget this preference.",
        source_reference="edge-proposal:forget-1",
        source_service="edge-client",
        source_type="queued_proposal",
        supersedes=created.record_id,
        explicit_user_statement=True,
        idempotency_key="edge-proposal:forget-1",
    )
    forgotten, replayed = core.store.add_edge_candidate(
        "forget-1",
        queued_forget,
        client_id="relay-client",
    )

    assert replayed is False
    assert forgotten.disposition == ObservationDisposition.APPLIED
    assert forgotten.record_id == created.record_id
    with pytest.raises(NotFoundError, match="context record not found"):
        core.store.get_record(created.record_id)

    repeated, replayed = core.store.add_edge_candidate(
        "forget-1",
        queued_forget,
        client_id="relay-client",
    )
    assert replayed is True
    assert repeated.id == forgotten.id
    assert repeated.record_id == created.record_id

    restored = core.store.restore_record(
        created.record_id,
        reason="The user explicitly asked to restore it.",
    )
    assert restored.content == "The user prefers direct answers."
    assert core.store.get_record(created.record_id).id == created.record_id
